// The wiki's routes: pages, the editor, scratch notes, the
// deck page, previews, page lists.
package main

import (
	"errors"
	"html/template"
	"io"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

func pageRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /private", privateRoute)
	mux.HandleFunc("GET /page/_scratch", scratchPage)
	mux.HandleFunc("GET /page/{slug...}", viewPage)
	mux.HandleFunc("GET /api/page-preview/{slug...}", apiPagePreview)
	mux.HandleFunc("POST /api/scratch", func(w http.ResponseWriter, r *http.Request) {
		slug, err := createScratch(userOf(r), r.PostFormValue("title"), nil, "")
		if err != nil {
			http.Error(w, "Bad Request", http.StatusBadRequest)
			return
		}
		writeJSON(w, http.StatusOK, M{"slug": slug})
	})
	mux.HandleFunc("GET /edit/{slug...}", editPage)
	mux.HandleFunc("POST /edit/{slug...}", savePage)
	mux.HandleFunc("POST /api/save/{slug...}", apiSave)
	mux.HandleFunc("GET /new", func(w http.ResponseWriter, r *http.Request) {
		page(w, newReq(r), http.StatusOK, "new.html", "New Page", shell{}, nil)
	})
	mux.HandleFunc("POST /new", createPage)
	mux.HandleFunc("POST /rename/{slug...}", renamePage)
	mux.HandleFunc("POST /promote/{slug...}", promotePage)
	mux.HandleFunc("POST /append/{slug...}", appendPage)
	mux.HandleFunc("POST /delete/{slug...}", deletePage)
	mux.HandleFunc("GET /pages", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/", http.StatusFound)
	})
	mux.HandleFunc("GET /api/pages-list", apiPagesList)
	mux.HandleFunc("GET /deck", deckPage)
	mux.HandleFunc("GET /api/search", apiSearch)
	mux.HandleFunc("GET /search", searchPage)
	mux.HandleFunc("POST /api/toggle-checkbox/{slug...}", apiToggleCheckbox)
	mux.HandleFunc("POST /api/preview", apiPreview)
	mux.HandleFunc("POST /api/render-notes", apiRenderNotes)
	mux.HandleFunc("GET /api/pages", func(w http.ResponseWriter, r *http.Request) {
		rq := newReq(r)
		out := []string{}
		for _, s := range allSlugs() {
			if !hiddenPage(rq, s) {
				out = append(out, s)
			}
		}
		writeJSON(w, http.StatusOK, out)
	})
}

func badRequestPage(w http.ResponseWriter) { http.Error(w, "Bad Request", http.StatusBadRequest) }

func serverError(w http.ResponseWriter, r *http.Request, err error) {
	logf("%s %s: %v", r.Method, r.URL.Path, err)
	http.Error(w, "Internal Server Error", http.StatusInternalServerError)
}

func htmlOf(s string) template.HTML { return template.HTML(s) }

// quote is urllib's quote(s): letters, digits, _.-~ and / stay.
func quote(s string) string { return pathURL(s)[1:] }

// scratchPage: the scratch notes. Its filter runs the search over them
// (js/search.js initScratchFilter); ?q= keeps the query.
func scratchPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	page(w, rq, http.StatusOK, "scratch.html", "Scratch", shell{}, M{"notes": listScratch(rq),
		"q": r.URL.Query().Get("q"), "breadcrumb_html": buildBreadcrumb(rq, "_scratch", "Scratch", "", "")})
}

var (
	pidRE         = regexp.MustCompile(`(<h[1-6]\b[^>]*?) id="`)
	sectionHeadRE = regexp.MustCompile(`(?m)^#{2,3}` + spaceClass + `+(.+?)` + spaceClass + `*#*` + spaceClass + `*$`)
)

// apiPagePreview: a page for the search modal's preview pane, {title, html}:
// heading ids become data-pid (the page underneath has its own), deck
// embeds are left out.
func apiPagePreview(w http.ResponseWriter, r *http.Request) {
	rq, slug := newReq(r), r.PathValue("slug")
	path := safePath(slug)
	if path == "" || !exists(path) || hiddenPage(rq, slug) {
		http.NotFound(w, r)
		return
	}
	content, err := readText(path)
	if err != nil {
		serverError(w, r, err)
		return
	}
	masked, stash := maskCode(content)
	masked = deckRE.ReplaceAllString(masked, "*(deck)*")
	out := renderMd(rq, stripH1(unmaskCode(masked, stash)), RenderOpts{})
	writeJSON(w, http.StatusOK, M{"title": pageTitle(content, slug), "html": pidRE.ReplaceAllString(out, `$1 data-pid="`)})
}

func viewPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	slug := strings.TrimSuffix(r.PathValue("slug"), ".md")
	path := safePath(slug)
	if path == "" {
		badRequestPage(w)
		return
	}
	if refusePage(w, r, rq, slug) {
		return
	}
	if !exists(path) {
		// A page deleted in git history offers to come back.
		if parent, date, ok := lastDeletion(slug); ok {
			title := deletedTitle(parent, slug)
			page(w, rq, http.StatusOK, "deleted_page.html", title, shell{}, M{"slug": slug, "sha": parent,
				"date": date, "breadcrumb_html": buildBreadcrumb(rq, slug, title, "", "")})
			return
		}
		http.Redirect(w, r, pathURL("edit", slug), http.StatusFound)
		return
	}
	content, err := readText(path)
	if err != nil {
		serverError(w, r, err)
		return
	}
	title := pageTitle(content, slug)
	var backlinks []PageRef
	for _, b := range findBacklinks(slug) {
		if !hiddenPage(rq, b.Slug) {
			backlinks = append(backlinks, b)
		}
	}
	// Tasks the page shows inline already aren't repeated under Linked tasks.
	inline := pageTaskRefs(rq.user, content)
	// A section a task links to is named as the page's heading says it.
	masked, _ := maskCode(content)
	headingNames := map[string]string{}
	for _, m := range sectionHeadRE.FindAllStringSubmatch(masked, -1) {
		headingNames[sectionAnchor(m[1])] = plainHeading(m[1])
	}
	var tasks []TaskLink
	for _, t := range findTaskBacklinks(rq.user, slug) {
		if inline[t.ID] {
			continue
		}
		if name, ok := headingNames[t.Anchor]; ok && t.Anchor != "" {
			t.Section = name
		}
		tasks = append(tasks, t)
	}
	var lists []string
	for _, l := range pageDeckLists(content) {
		lists = append(lists, l.Name)
	}
	sections := linkSections(rq, backlinks, tasks, lists)
	if rel := relatedSection(rq, slug, path, backlinks); rel != nil {
		sections = append(sections, rel)
	}
	chip := macro("backlinks_chip", pageLinks(backlinks))

	// Viewing a page from the uncommitted-changes list: Commit instead of Edit.
	fromUncommitted := r.URL.Query().Get("from") == "uncommitted" && cfg.GitEnabled
	var bc, metaRow, article template.HTML
	var menu M
	if fromUncommitted {
		bc = buildBreadcrumb(rq, "uncommitted", "Uncommitted Changes", chip, "", crumb{title, ""})
		article = htmlOf(renderMd(rq, stripH1(content), RenderOpts{Page: slug}))
	} else {
		qs := ""
		if r.URL.RawQuery != "" {
			qs = "?" + r.URL.RawQuery
		}
		bc = buildBreadcrumb(rq, slug, title, chip, qs)
		q := r.URL.Query()
		menu = pageMenu(rq, slug, title, q.Get("rename_error"), q.Get("promote_error"), q.Get("append_error"))
		changed, commit := lastChanged(slug, path, title)
		meta, _ := parseFrontmatter(content)
		metaRow = macro("meta_row", M{"tags_html": htmlOf(renderTagsPillsHTML(meta.Tags)), "tldr": meta.Tldr,
			"changed": changed, "commit": commit})
		article = htmlOf(renderMd(rq, stripH1(content), RenderOpts{Page: slug, HideTags: true, HideTldr: true,
			EditSlug: slug, SectionTasks: tasks}))
	}
	_, private := pageOwner(slug)
	page(w, rq, http.StatusOK, "view.html", title, shell{wide: true}, M{"slug": slug, "from_uncommitted": fromUncommitted, "private": private,
		"task_keys":       strings.Join(taskLineKeys(content), ","),
		"breadcrumb_html": bc, "delete_dialog_html": buildDeleteDialog(slug, title),
		"rail_toc_html": railTocItems(string(article)), "meta_row_html": metaRow, "article_html": article,
		"link_sections": sections, "outlinks": outlinks(rq, slug, path), "menu": menu})
}

// intArg is Flask's args.get(name, type=int): ok false when missing or not an int.
func intArg(v string, present bool) (int, bool) {
	if !present {
		return 0, false
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	return n, err == nil
}

// formInt is Flask's form.get(name, type=int), urlencoded or multipart.
func formInt(r *http.Request, name string) (int, bool) {
	_ = r.ParseMultipartForm(32 << 20)
	_, present := r.PostForm[name]
	return intArg(r.PostForm.Get(name), present)
}

var onlyTitleRE = regexp.MustCompile(`^# [^\n]*\n*$`)

func editPage(w http.ResponseWriter, r *http.Request) {
	rq, slug := newReq(r), r.PathValue("slug")
	path := safePath(slug)
	if path == "" {
		badRequestPage(w)
		return
	}
	if refusePage(w, r, rq, slug) {
		return
	}
	var content, title string
	isNew := !exists(path)
	if isNew {
		title = slugDisplayName(slug)
		content = "# " + title + "\n\n"
	} else {
		var err error
		if content, err = readText(path); err != nil {
			serverError(w, r, err)
			return
		}
		title = pageTitle(content, slug)
	}
	if !isNew {
		keepGivenBase(content) // what a later save from this editor is merged from
	}
	q := r.URL.Query()
	section, hasSection := intArg(q.Get("section"), q.Has("section"))
	// Changes the user left here in conflict: offered on the whole page.
	var left M
	if d, ok := draftLeft(rq.user, path); ok && !isNew && d.Content == content {
		dropDraft(rq.user, path) // the page holds it now: nothing to offer
	} else if ok && !isNew {
		if hasSection {
			http.Redirect(w, r, pathURL("edit", slug), http.StatusFound)
			return
		}
		left = M{"content": d.Content, "at": d.At.Format("2006-01-02 15:04")}
		if d.Base != "" {
			if merged, _, err := mergeText(d.Base, content, d.Content, "theirs"); err == nil {
				left["conflict"] = merged
			}
		}
	}
	fileHash, start, end := "", 0, 0
	if hasSection && !isNew {
		if s, e, ok := sectionCharRange(content, section); ok {
			fileHash, start, end = contentHash(content), s, e
			content = strings.TrimRight(runeSlice(content, s, e), "\n")
		} else {
			hasSection = false // an invalid index: the whole page
		}
	}
	editable := !isNew && !hasSection // a whole existing page
	// Nothing but the title yet (a new page, a new scratch note): the cursor
	// starts after it, ready for the body.
	onlyTitle := !hasSection && onlyTitleRE.MatchString(content)
	if !isNew && !hasSection {
		fileHash = contentHash(content)
	}
	extra := crumb{"Edit", ""}
	if hasSection {
		extra = crumb{"§ " + strconv.Itoa(section), ""}
	}
	page(w, rq, http.StatusOK, "edit.html", "Edit — "+title, shell{editor: true}, M{"slug": slug,
		"page_title": title, "content": content, "is_new": isNew, "only_title": onlyTitle,
		"has_section": hasSection, "section": section, "start_char": start, "end_char": end,
		// The file's hash for conflict detection, whole page or section.
		"file_hash": fileHash, "can_rename": editable && !isProtected(slug), "can_delete": editable && !isProtected(slug),
		"git": cfg.GitEnabled, "rename_error": q.Get("rename_error"), "left_draft": left,
		"paste":           pasteHint(rq, q.Get("paste"), q.Get("copied") == "1"),
		"breadcrumb_html": buildBreadcrumb(rq, slug, title, "", "", extra),
		"deck_html":       htmlOf(buildDeckEmbed(rq.user, DeckParams{}, 0, true, ownPrivate(rq.user, slug)))})
}

// postedContent: the editor's text, the browser's CRLF line ends as LF.
func postedContent(r *http.Request) string {
	return newlines(r.PostFormValue("content"))
}

// currentText is the page's text, nil when the file isn't there.
func currentText(path string) (*string, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	s := newlines(string(b))
	return &s, nil
}

// editorSave is what the editor posts: the text, the file's hash as the
// editor loaded it, the section's range (when editing one), and the page's
// text now (nil: no file).
type editorSave struct {
	slug, path, text, fileHash string
	start, end                 int
	section                    bool
	current                    *string
}

// readEditorSave reads the editor's post, or answers it (bad slug, error).
func readEditorSave(w http.ResponseWriter, r *http.Request) (*editorSave, bool) {
	s := &editorSave{slug: r.PathValue("slug")}
	if s.path = safePath(s.slug); s.path == "" {
		badRequestPage(w)
		return nil, false
	}
	if refuseWrite(w, r, s.slug) {
		return nil, false
	}
	s.text, s.fileHash = postedContent(r), r.PostFormValue("file_hash")
	start, okS := formInt(r, "start_char")
	end, okE := formInt(r, "end_char")
	s.start, s.end, s.section = start, end, s.fileHash != "" && okS && okE
	current, err := currentText(s.path)
	if err != nil {
		serverError(w, r, err)
		return nil, false
	}
	s.current = current
	return s, true
}

// changedSince: the page changed since the editor loaded it (whole-page edits).
func (s *editorSave) changedSince() bool {
	return s.fileHash != "" && s.current != nil && contentHash(*s.current) != s.fileHash
}

// write writes content unless the file holds it already.
func (s *editorSave) write(content string) error {
	if s.current != nil && *s.current == content {
		return nil
	}
	return writeText(s.path, content)
}

func savePage(w http.ResponseWriter, r *http.Request) {
	s, ok := readEditorSave(w, r)
	if !ok {
		return
	}
	content := s.text
	conflict := pathURL("edit", s.slug) + "?conflict=1"
	var changed bool
	if s.section {
		var spliced string
		if spliced, _, _, _, ok = spliceSection(s.current, s.fileHash, s.start, s.end, content); ok {
			content = spliced
		}
		changed = !ok
	} else {
		changed = s.changedSince()
	}
	if changed {
		// Changed since the editor loaded it: merged, if it merges cleanly.
		m, ok := mergeSave(s, userOf(r))
		if !ok || !m.clean {
			http.Redirect(w, r, conflict, http.StatusFound)
			return
		}
		content = m.content
	}
	commitOthersDraft(userOf(r), s.slug, s.path)
	// Unchanged: no write, but still a commit when the file is dirty.
	if err := s.write(content); err != nil {
		serverError(w, r, err)
		return
	}
	keepBase(content)
	dropDraft(userOf(r), s.path)
	gitCommit(userOf(r), "Update "+s.slug, s.path)
	http.Redirect(w, r, pathURL("page", s.slug), http.StatusFound)
}

// apiSave: the editor's auto-save (no commit): a whole page, a section
// (guarded by the file's hash), or force_section (spliced by index). A
// save onto a file that changed since is merged when the editor takes a
// merged answer (merge=1: it puts the merged text into its textarea) and
// the lines don't clash; else 409.
func apiSave(w http.ResponseWriter, r *http.Request) {
	s, ok := readEditorSave(w, r)
	if !ok {
		return
	}
	force, okF := formInt(r, "force_section")
	isForce := okF && force >= 1
	var content, newHash string
	var newStart, newEnd int
	var merged *mergeOutcome
	// conflict: the file changed since; merged if it can be, else 409.
	conflict := func() bool {
		m, ok := mergeSave(s, userOf(r))
		if ok && m.clean && r.PostFormValue("merge") == "1" {
			merged, content = &m, m.content
			newHash, newStart, newEnd = contentHash(content), m.start, m.end
			return false
		}
		answer := M{"error": "file_changed"}
		if s.current != nil {
			answer["file_hash"] = keepGivenBase(*s.current) // Merge by hand saves from it
		}
		if ok {
			answer["by"] = m.by
			if !m.clean && !s.section {
				// The lines that clash, with git's markers, for Merge by hand.
				answer["conflict"] = m.content
			}
		}
		if r.PostFormValue("leaving") == "1" {
			keepLeftDraft(r, s)
		}
		writeJSON(w, http.StatusConflict, answer)
		return true
	}
	if r.PostFormValue("set_aside") == "1" {
		setAside(w, r, s, force)
		return
	}
	if r.PostFormValue("keep_left") == "1" {
		// Leaving the page with the banner up or markers left: kept for the
		// next visit, the page left as it is.
		keepLeftDraft(r, s)
		w.WriteHeader(http.StatusNoContent)
		return
	}
	switch {
	case isForce:
		// Spliced by its index in the file as it is now, without the hash guard.
		if s.current == nil {
			http.NotFound(w, r)
			return
		}
		start, end, ok := sectionCharRange(*s.current, force)
		if !ok {
			badRequestPage(w)
			return
		}
		content, newHash, newStart, newEnd, _ = spliceSection(s.current, contentHash(*s.current), start, end, s.text)
	case s.section:
		if content, newHash, newStart, newEnd, ok = spliceSection(s.current, s.fileHash, s.start, s.end, s.text); !ok && conflict() {
			return
		}
	default:
		// The whole page: a hash checks for a conflict.
		content = s.text
		if s.changedSince() && conflict() {
			return
		}
	}
	if isForce || r.PostFormValue("overwrite") == "1" {
		// Keep mine: whatever the page holds, but never what was never in git.
		commitBeforeOverwrite(userOf(r), s.slug, s.path)
	}
	commitOthersDraft(userOf(r), s.slug, s.path)
	if err := s.write(content); err != nil {
		serverError(w, r, err)
		return
	}
	noteDraft(s.path, userOf(r))
	dropDraft(userOf(r), s.path)
	keepBase(content)
	switch {
	case merged != nil:
		// The merged text, for the editor's textarea: the section's, as /edit
		// gives it; and the save's own, which an editor typed on meanwhile
		// saves from next.
		text := content
		answer := M{"file_hash": newHash, "merged": true, "by": merged.by, "sent_hash": merged.sentHash}
		if s.section {
			text = strings.TrimRight(runeSlice(content, newStart, newEnd), "\n")
			answer["start_char"], answer["end_char"] = newStart, newEnd
			answer["sent_start"], answer["sent_end"] = merged.sentStart, merged.sentEnd
		}
		answer["content"] = text
		writeJSON(w, http.StatusOK, answer)
	case s.section || isForce:
		writeJSON(w, http.StatusOK, M{"file_hash": newHash, "start_char": newStart, "end_char": newEnd})
	default:
		// Also for a save without a hash (a new page's first, Keep mine):
		// the editor's next save then goes from what this one wrote.
		writeJSON(w, http.StatusOK, M{"file_hash": contentHash(content)})
	}
}

// setAside: Reload theirs after a conflict, the editor's text (a whole
// page, or a section by index) committed as the user's first, then the
// page back as it is now, so what was typed is in the page's history, not
// lost. {set_aside: false}: without git there is no history to keep it in.
func setAside(w http.ResponseWriter, r *http.Request, s *editorSave, section int) {
	if s.current == nil {
		http.NotFound(w, r)
		return
	}
	theirs, mine := *s.current, s.text
	if section >= 1 {
		start, end, ok := sectionCharRange(theirs, section)
		if !ok {
			badRequestPage(w)
			return
		}
		mine, _, _, _, _ = spliceSection(&theirs, contentHash(theirs), start, end, s.text)
	}
	if !cfg.GitEnabled || mine == theirs {
		writeJSON(w, http.StatusOK, M{"set_aside": false})
		return
	}
	user := userOf(r)
	commitBeforeOverwrite(user, s.slug, s.path) // theirs, if never in git
	if err := writeText(s.path, mine); err != nil {
		serverError(w, r, err)
		return
	}
	gitCommit(user, "Set aside an edit of "+s.slug, s.path)
	if err := writeText(s.path, theirs); err != nil {
		serverError(w, r, err)
		return
	}
	gitCommit(user, "Reload "+s.slug, s.path)
	dropDraft(user, s.path)
	writeJSON(w, http.StatusOK, M{"set_aside": true})
}

// keepLeftDraft: the editor's save on leaving the page (leaving=1) was
// refused: its text, as a whole page, is kept for its user (leaveDraft).
// A section goes into the text it came from, or, that not kept, into the
// page as it is now at its index (section_idx).
func keepLeftDraft(r *http.Request, s *editorSave) {
	base, _ := baseText(s.fileHash)
	whole := s.text
	if s.section {
		var ok bool
		switch idx, hasIdx := formInt(r, "section_idx"); {
		case base != "":
			whole, _, _, _, ok = spliceSection(&base, s.fileHash, s.start, s.end, s.text)
		case hasIdx && s.current != nil:
			if start, end, valid := sectionCharRange(*s.current, idx); valid {
				whole, _, _, _, ok = spliceSection(s.current, contentHash(*s.current), start, end, s.text)
			}
		}
		if !ok {
			return
		}
	}
	leaveDraft(userOf(r), s.path, whole, base)
}

// createPage: /new, the form for a new page's name (pages are made from
// links to them; this is its form without JavaScript); private=1 puts
// the page in the user's private folder.
func createPage(w http.ResponseWriter, r *http.Request) {
	raw := r.PostFormValue("slug")
	slug := toSlug(raw)
	if own := privateFolder(userOf(r)); own != "" && slug != "" && r.PostFormValue("private") == "1" &&
		!strings.HasPrefix(slug, own+"/") {
		slug = own + "/" + slug
	}
	path := ""
	if slug != "" {
		path = safePath(slug)
	}
	if path == "" {
		badRequestPage(w)
		return
	}
	if refuseWrite(w, r, slug) {
		return
	}
	if !exists(path) {
		if err := writeText(path, "# "+titleWords(strings.TrimSpace(raw))+"\n\n"); err != nil {
			serverError(w, r, err)
			return
		}
		gitCommit(userOf(r), "Create "+slug, path)
	}
	http.Redirect(w, r, pathURL("edit", slug), http.StatusFound)
}

func renamePage(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	if isProtected(slug) {
		badRequestPage(w)
		return
	}
	oldPath := safePath(slug)
	if oldPath == "" || !exists(oldPath) || !canWrite(userOf(r), slug) {
		http.NotFound(w, r)
		return
	}
	// Back to where the rename was asked for: the editor, or the page view's
	// ⋯ menu (which reopens its dialog with the error).
	back := pathURL("edit", slug)
	if r.PostFormValue("from") == "page" {
		back = pathURL("page", slug)
	}
	// toSlug keeps underscores, so scratch timestamps survive a rename.
	newSlug := toSlug(strings.TrimSpace(r.PostFormValue("new_name")))
	if newSlug == "" || newSlug == slug {
		http.Redirect(w, r, back, http.StatusFound)
		return
	}
	newPath := safePath(newSlug)
	if newPath == "" {
		badRequestPage(w)
		return
	}
	if !canWrite(userOf(r), newSlug) {
		http.Redirect(w, r, back+"?rename_error="+quote("“"+newSlug+"”: that folder is another user's"), http.StatusFound)
		return
	}
	if exists(newPath) {
		http.Redirect(w, r, back+"?rename_error=A+page+named+%22"+newSlug+"%22+already+exists", http.StatusFound)
		return
	}
	if _, err := movePage(userOf(r), oldPath, newPath, slug, newSlug, "Rename "+slug+" to "+newSlug); err != nil {
		serverError(w, r, err)
		return
	}
	http.Redirect(w, r, pathURL("page", newSlug), http.StatusFound)
}

// scratchNoteArg: the scratch note a Move to wiki or Append route acts on
// (400 for another page, 404 for none).
func scratchNoteArg(w http.ResponseWriter, r *http.Request) (string, bool) {
	slug := r.PathValue("slug")
	if refuseWrite(w, r, slug) {
		return "", false
	}
	if !isScratch(slug) {
		badRequestPage(w)
		return "", false
	}
	if p := safePath(slug); p == "" || !exists(p) {
		http.NotFound(w, r)
		return "", false
	}
	return slug, true
}

// scratchFailed answers a failed move or append: a refusal goes back to
// the page's dialog (back, the message appended), anything else is a 500.
func scratchFailed(w http.ResponseWriter, r *http.Request, back string, err error) {
	var se *scratchError
	if errors.As(err, &se) {
		http.Redirect(w, r, back+quote(se.msg), http.StatusFound)
		return
	}
	serverError(w, r, err)
}

// promotePage moves a scratch note into the wiki (the Move to wiki dialog),
// links to it following, with its own commit message.
func promotePage(w http.ResponseWriter, r *http.Request) {
	slug, ok := scratchNoteArg(w, r)
	if !ok {
		return
	}
	back := pathURL("page", slug) + "?promote_error="
	rawLink := strings.TrimSpace(r.PostFormValue("link_from"))
	linkSlug, linkPath := "", ""
	if rawLink != "" {
		linkSlug = pageFromInput(rawLink)
		if linkSlug != "" && canWrite(userOf(r), linkSlug) {
			linkPath = safePath(linkSlug)
		}
		if linkPath == "" || !exists(linkPath) || linkSlug == slug {
			http.Redirect(w, r, back+quote("There is no page “"+rawLink+"” to link from"), http.StatusFound)
			return
		}
	}
	newSlug, err := moveScratch(userOf(r), slug, r.PostFormValue("folder"), r.PostFormValue("name"), r.PostFormValue("keep_date") != "", "")
	if err != nil {
		scratchFailed(w, r, back, err)
		return
	}
	// The page to link it from opens in the editor, the link to paste where
	// it belongs (js/pagemenu.js copied it on Move); a page linking to the
	// note already links here now: nothing to paste.
	if linkPath != "" {
		text, _ := readText(linkPath)
		if !pageLinkTargets(text, linkSlug)[newSlug] {
			copied := ""
			if r.PostFormValue("copied") == "1" {
				copied = "&copied=1"
			}
			http.Redirect(w, r, pathURL("edit", linkSlug)+"?paste="+quote(newSlug)+copied, http.StatusFound)
			return
		}
	}
	http.Redirect(w, r, pathURL("page", newSlug), http.StatusFound)
}

// appendPage appends a scratch note to a page as a section and deletes the
// note; links to the note point at that section, all in one commit.
func appendPage(w http.ResponseWriter, r *http.Request) {
	slug, ok := scratchNoteArg(w, r)
	if !ok {
		return
	}
	back := pathURL("page", slug) + "?append_error="
	raw := strings.TrimSpace(r.PostFormValue("page"))
	target := pageFromInput(raw)
	// The error names the page as typed.
	targetPath := ""
	if target != "" && canWrite(userOf(r), target) {
		targetPath = safePath(target)
	}
	if raw != "" && (targetPath == "" || !exists(targetPath)) {
		http.Redirect(w, r, back+quote("There is no page “"+raw+"”"), http.StatusFound)
		return
	}
	anchor, err := appendScratch(userOf(r), slug, target, r.PostFormValue("heading"), "")
	if err != nil {
		scratchFailed(w, r, back, err)
		return
	}
	http.Redirect(w, r, pathURL("page", target)+"#"+anchor, http.StatusFound)
}

func deletePage(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	if isProtected(slug) {
		badRequestPage(w)
		return
	}
	path := safePath(slug)
	if path == "" || !exists(path) || !canWrite(userOf(r), slug) {
		http.NotFound(w, r)
		return
	}
	commitOthersDraft(userOf(r), slug, path)
	if err := os.Remove(path); err != nil {
		serverError(w, r, err)
		return
	}
	gitCommit(userOf(r), "Delete "+slug, path)
	next := r.PostFormValue("next")
	if !isLocalPath(next) || strings.Contains(next, "/page/"+slug) || strings.Contains(next, "/edit/"+slug) {
		next = "/page/index"
	}
	http.Redirect(w, r, next, http.StatusFound)
}

// apiPagesList: every page for the search modal's list, and the built-in
// ones: Deck and Scratch are modules (typing finds them); Deleted Pages is
// pinned to the top of the empty query's list (special).
func apiPagesList(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	counts := allBacklinkCounts(rq)
	pages := []M{}
	for _, p := range iterPages() {
		if hiddenPage(rq, p.Slug) {
			continue
		}
		title, _ := pageMeta(p.Path, p.Slug)
		st, err := os.Stat(p.Path)
		if err != nil {
			continue
		}
		_, private := pageOwner(p.Slug) // not hidden above: the user's own
		pages = append(pages, M{"slug": p.Slug, "title": title, "links": counts[p.Slug], "private": private,
			"mtime": st.ModTime().Unix(), "created": fmtDate(changeTime(st)), "edited": fmtDate(st.ModTime())})
	}
	sort.SliceStable(pages, func(i, j int) bool {
		return strings.ToLower(pages[i]["title"].(string)) < strings.ToLower(pages[j]["title"].(string))
	})
	out := []M{}
	if cfg.GitEnabled {
		out = append(out, M{"slug": "_deleted", "title": "Deleted Pages", "links": 0, "mtime": 0, "builtin": true, "special": true})
	}
	out = append(out, pages...)
	out = append(out,
		M{"slug": "/deck", "title": "Deck", "href": "/deck", "links": 0, "mtime": 0, "builtin": true},
		M{"slug": "_scratch", "title": "Scratch", "links": counts["_scratch"], "mtime": 0, "builtin": true})
	writeJSON(w, http.StatusOK, out)
}

// deckPage: /deck, every task, or with ?list= that list, looking as on the
// page that declares it (that embed's params apply). Every link to "the
// deck" goes here; [[deck …]] embeds are for views on ordinary pages.
func deckPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	list := cleanList(r.URL.Query().Get("list"))
	params, title, heading := DeckParams{}, "Deck", "Deck"
	var home *DeckHome
	crumbs := []crumb{{"Deck", ""}}
	// The breadcrumb's key names the view for the navigation trail; no page has it.
	key := "/deck"
	if list != "" {
		if declared, ok := deckLists(rq.user)[list]; ok {
			params = declared.Params
			t, _ := pageMeta(safePath(declared.Slug), declared.Slug)
			home = &DeckHome{Slug: declared.Slug, Title: t}
		}
		params = params.With("list", list)
		title, heading = list+" · Deck", list
		crumbs = []crumb{{"Deck", "/deck"}, {list, ""}}
		key += "?list=" + list
	}
	// A list declared in the user's private folder makes private tasks, as there.
	private := home != nil && ownPrivate(rq.user, home.Slug)
	data := M{"heading": heading, "list_name": list, "deck_html": htmlOf(buildDeckEmbed(rq.user, params, 0, false, private)),
		"breadcrumb_html": breadcrumb(rq, crumbs, key, true, title, "")}
	if home != nil {
		data["home"] = home
	}
	page(w, rq, http.StatusOK, "deck_page.html", title, shell{}, data)
}

func apiToggleCheckbox(w http.ResponseWriter, r *http.Request) {
	path := safePath(r.PathValue("slug"))
	if path == "" || !exists(path) || !canWrite(userOf(r), r.PathValue("slug")) {
		http.NotFound(w, r)
		return
	}
	idx, ok := formInt(r, "idx")
	if !ok || idx < 0 {
		badRequestPage(w)
		return
	}
	content, err := readText(path)
	if err != nil {
		serverError(w, r, err)
		return
	}
	// The item is the one whose line the page view had (key): at idx, or,
	// the page changed since the view loaded, wherever it is now, if it is
	// there once. Checked on every tick: a view that ticked an item found
	// elsewhere still counts its items as it did.
	if key := r.PostFormValue("key"); key != "" {
		keys := taskLineKeys(content)
		if idx >= len(keys) || keys[idx] != key {
			idx = -1
			for i, k := range keys {
				if k == key {
					if idx >= 0 {
						idx = -1
						break
					}
					idx = i
				}
			}
		}
		if idx < 0 {
			writeJSON(w, http.StatusConflict, M{"error": "page_changed"})
			return
		}
	}
	commitOthersDraft(userOf(r), r.PathValue("slug"), path)
	toggled := toggleNthCheckbox(content, idx, true)
	if err := writeText(path, toggled); err != nil {
		serverError(w, r, err)
		return
	}
	noteDraft(path, userOf(r))
	writeJSON(w, http.StatusOK, M{"file_hash": contentHash(toggled)})
}

// apiPreview renders posted markdown (the editor's live preview); ?lists=
// the lists the page declares, so a chip re-rendered alone keeps linking
// to the page's list (js/page.js rerenderTaskRef).
func apiPreview(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	var lists []string
	for _, x := range strings.Split(r.URL.Query().Get("lists"), ",") {
		if n := cleanList(x); n != "" {
			lists = append(lists, n)
		}
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = io.WriteString(w, renderMd(newReq(r), string(body), RenderOpts{PreferLists: lists}))
}

// apiRenderNotes: deck task notes and comments as HTML, as pages render:
// {"notes": [text…], "comments": [text…]} → the same with HTML.
func apiRenderNotes(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	data, err := jsonObject(r)
	if err != nil {
		badRequestPage(w)
		return
	}
	out := M{}
	for _, k := range []struct {
		key    string
		inline bool
	}{{"notes", false}, {"comments", true}} {
		var texts []string
		if !data.null(k.key) {
			var ok bool
			if texts, ok = as[[]string](data[k.key]); !ok {
				badRequestPage(w)
				return
			}
		}
		out[k.key] = renderNotes(rq, texts, k.inline)
	}
	writeJSON(w, http.StatusOK, out)
}

// apiSearch: the search modal's results (?limit=, ?unlinked=1; ?scratch=1:
// scratch notes only, for the Scratch page's filter).
func apiSearch(w http.ResponseWriter, r *http.Request) {
	args := r.URL.Query()
	q := strings.TrimSpace(args.Get("q"))
	if q == "" {
		writeJSON(w, http.StatusOK, []M{})
		return
	}
	scratch := args.Get("scratch") == "1"
	limit, ok := intArg(args.Get("limit"), args.Has("limit"))
	if !ok {
		limit = searchLimit
	}
	most := searchMaxLimit
	if scratch {
		most = scratchFilterLimit
	}
	results := doSearch(newReq(r), q, args.Get("unlinked") == "1", max(1, min(limit, most)), scratch)
	// The search dialog's indicator (js/search.js) follows it: indexing → on.
	w.Header().Set("X-Leaf-Semantic", semanticStatus())
	writeJSON(w, http.StatusOK, results)
}

// searchPage: /search, up to searchPageLimit results (the modal's "All
// results").
func searchPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	q := strings.TrimSpace(r.URL.Query().Get("q"))
	unlinked := r.URL.Query().Get("unlinked") == "1"
	var results []M
	if q != "" {
		results = doSearch(rq, q, unlinked, searchPageLimit, false)
	}
	page(w, rq, http.StatusOK, "search.html", "Search: "+q, shell{}, M{"q": q, "results": results,
		"unlinked": unlinked, "limit": searchPageLimit})
}
