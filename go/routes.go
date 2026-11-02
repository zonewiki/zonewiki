// The wiki's routes: pages, the editor, notes, the
// tasks page, previews, page lists.
package main

import (
	"errors"
	"html/template"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"zonewiki/internal/mdtext"
)

func pageRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /private", privateRoute) // pagesMu only to make the home (privateRoute)
	mux.HandleFunc("GET /notes", notesPage)
	mux.HandleFunc("GET /page/{slug...}", viewPage)
	mux.HandleFunc("GET /api/page-preview/{slug...}", apiPagePreview)
	mux.HandleFunc("POST /api/notes", writesPages(apiNewNote))
	mux.HandleFunc("GET /edit/{slug...}", editPage)
	mux.HandleFunc("POST /edit/{slug...}", writesPages(savePage))
	mux.HandleFunc("POST /api/save/{slug...}", writesPages(apiSave))
	mux.HandleFunc("GET /new", func(w http.ResponseWriter, r *http.Request) {
		page(w, newReq(r), http.StatusOK, "new.html", "New Page", shell{}, nil)
	})
	mux.HandleFunc("POST /new", writesPages(createPage))
	mux.HandleFunc("POST /rename/{slug...}", writesPages(renamePage))
	mux.HandleFunc("POST /promote/{slug...}", writesPages(promotePage))
	mux.HandleFunc("POST /append/{slug...}", writesPages(appendPage))
	mux.HandleFunc("POST /delete/{slug...}", writesPages(deletePage))
	mux.HandleFunc("GET /pages", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/", http.StatusFound)
	})
	mux.HandleFunc("GET /api/pages-list", apiPagesList)
	mux.HandleFunc("GET /tasks", tasksPage)
	mux.HandleFunc("GET /api/search", apiSearch)
	mux.HandleFunc("GET /search", searchPage)
	mux.HandleFunc("POST /api/toggle-checkbox/{slug...}", writesPages(apiToggleCheckbox))
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

// quote percent-encodes s for a URL: letters, digits, _.-~ and / stay.
func quote(s string) string { return pathURL(s)[1:] }

// apiNewNote: a new note titled by the form's title; answers its slug.
func apiNewNote(w http.ResponseWriter, r *http.Request) {
	slug, err := createQuicknote(userOf(r), r.PostFormValue("title"), nil, "")
	if err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, M{"slug": slug})
}

// notesPage: the notes. Its filter runs the search over them
// (js/search.js initNotesFilter); ?q= keeps the query.
func notesPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	page(w, rq, http.StatusOK, "notes.html", "Notes", shell{}, &notesView{Notes: listQuicknote(rq),
		Private: privateFolder(rq.user) != "", Q: r.URL.Query().Get("q"), BreadcrumbHTML: breadcrumb(rq, []crumb{{"Notes", ""}}, "/notes", true, "Notes", "")})
}

// notesView: the notes (notes.html); Private: they are in the user's folder.
type notesView struct {
	view
	Notes          []Quicknote
	Private        bool
	Q              string
	BreadcrumbHTML template.HTML
}

var (
	sectionHeadRE = regexp.MustCompile(`(?m)^#{2,3}` + mdtext.SpaceClass + `+(.+?)` + mdtext.SpaceClass + `*#*` + mdtext.SpaceClass + `*$`)
)

// apiPagePreview: a page for the search modal's preview pane, {title, html}:
// heading ids become data-pid (the page underneath has its own), tasks
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
	masked, stash := mdtext.MaskCode(content)
	masked = tasksRE.ReplaceAllString(masked, "*(tasks)*")
	out := renderMd(rq, mdtext.StripH1(mdtext.UnmaskCode(masked, stash)), RenderOpts{HeadingIDs: headingPID})
	writeJSON(w, http.StatusOK, M{"title": mdtext.PageTitle(content, slug), "html": out})
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
			page(w, rq, http.StatusOK, "deleted_page.html", title, shell{}, &deletedPageView{Slug: slug, Sha: parent,
				Date: date, BreadcrumbHTML: buildBreadcrumb(rq, slug, title, "", "")})
			return
		}
		to := pathURL("edit", slug)
		if r.URL.Query().Get("from") == "private" {
			to += "?from=private" // the link was on a private page (processWikilinks)
		}
		http.Redirect(w, r, to, http.StatusFound)
		return
	}
	content, err := readText(path)
	if err != nil {
		serverError(w, r, err)
		return
	}
	title := mdtext.PageTitle(content, slug)
	var backlinks []PageRef
	for _, b := range findBacklinksIn(rq.pageFiles(), slug) {
		if !hiddenPage(rq, b.Slug) {
			backlinks = append(backlinks, b)
		}
	}
	// Tasks the page shows inline already aren't repeated under Linked tasks.
	inline := pageTaskRefs(rq.user, content)
	// A section a task links to is named as the page's heading says it.
	masked, stash := mdtext.MaskCode(content)
	headingNames := map[string]string{}
	for _, m := range sectionHeadRE.FindAllStringSubmatch(masked, -1) {
		heading := mdtext.UnmaskCode(m[1], stash)
		headingNames[mdtext.SectionAnchor(heading)] = mdtext.PlainHeading(heading)
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
	for _, l := range pageTaskLists(content) {
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
		article = htmlOf(renderMd(rq, mdtext.StripH1(content), RenderOpts{Page: slug}))
	} else {
		qs := ""
		if r.URL.RawQuery != "" {
			qs = "?" + r.URL.RawQuery
		}
		bc = buildBreadcrumb(rq, slug, title, chip, qs)
		q := r.URL.Query()
		menu = pageMenu(rq, slug, title, q.Get("rename_error"), q.Get("promote_error"), q.Get("append_error"), q.Get("rename_name"))
		changed, commit := lastChanged(slug, path, title)
		meta, _ := mdtext.ParseFrontmatter(content)
		metaRow = macro("meta_row", M{"tags_html": htmlOf(renderTagsPillsHTML(meta.Tags)), "tldr": meta.Tldr,
			"changed": changed, "commit": commit})
		article = htmlOf(renderMd(rq, mdtext.StripH1(content), RenderOpts{Page: slug, HideTags: true, HideTldr: true,
			EditSlug: slug, SectionTasks: tasks}))
	}
	_, private := pageOwner(slug)
	page(w, rq, http.StatusOK, "view.html", title, shell{wide: true}, &pageView{Title: title, Slug: slug,
		FromUncommitted: fromUncommitted, Private: private, TaskKeys: strings.Join(mdtext.TaskLineKeys(content), ","),
		BreadcrumbHTML: bc, DeleteDialogHTML: buildDeleteDialog(slug, title), RailTocHTML: railTocItems(string(article)),
		MetaRowHTML: metaRow, ArticleHTML: article, LinkSections: sections, Outlinks: outlinks(rq, slug, path), Menu: menu})
}

// pageView: a page's view (view.html).
type pageView struct {
	view
	Title, Slug                                                             string
	FromUncommitted                                                         bool   // opened from the uncommitted list: Commit instead of Edit
	Private                                                                 bool   // in a private folder (the lock)
	TaskKeys                                                                string // a key per checkbox line (mdtext.TaskLineKeys), comma-separated
	BreadcrumbHTML, DeleteDialogHTML, RailTocHTML, MetaRowHTML, ArticleHTML template.HTML
	LinkSections                                                            []M // Backlinks, Linked tasks, Related (linkSections)
	Outlinks                                                                []M // the right column's Links to
	Menu                                                                    M   // the ⋯ menu (pageMenu); nil from the uncommitted list
}

// deletedPageView: a page deleted in git history, Sha the commit before
// (deleted_page.html).
type deletedPageView struct {
	view
	Slug, Sha, Date string
	BreadcrumbHTML  template.HTML
}

// editView: the editor (edit.html).
type editView struct {
	view
	Slug, PageTitle, Content    string
	OnlyTitle                   bool // nothing but the title yet: the cursor starts after it
	HasSection                  bool // one section (Section, its range StartChar–EndChar)
	Section, StartChar, EndChar int
	FileHash                    string // the file's hash for conflict detection, whole page or section
	CanRename, CanDelete        bool
	Git                         bool
	RenameError, RenameName     string
	PrivateOffer                string // a new shared page from a private one: the private slug to link instead
	LeftDraft                   *leftDraftView
	Paste                       M // after Move to wiki (pasteHint)
	BreadcrumbHTML, TasksHTML   template.HTML
}

// leftDraftView: changes the user left here in conflict, offered again.
type leftDraftView struct{ Content, At, Conflict string }

// intArg: a query or form value as an int; ok false when missing or not an int.
func intArg(v string, present bool) (int, bool) {
	if !present {
		return 0, false
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	return n, err == nil
}

// formInt: a posted field as an int (intArg), urlencoded or multipart.
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
		title = mdtext.SlugDisplayName(slug)
		content = "# " + title + "\n\n"
	} else {
		var err error
		if content, err = readText(path); err != nil {
			serverError(w, r, err)
			return
		}
		title = mdtext.PageTitle(content, slug)
	}
	if !isNew {
		keepGivenBase(content) // what a later save from this editor is merged from
	}
	q := r.URL.Query()
	section, hasSection := intArg(q.Get("section"), q.Has("section"))
	// Changes the user left here in conflict: offered on the whole page.
	var left *leftDraftView
	if d, ok := draftLeft(rq.user, path); ok && !isNew && d.Content == content {
		dropDraft(rq.user, path) // the page holds it now: nothing to offer
	} else if ok && !isNew {
		if hasSection {
			http.Redirect(w, r, pathURL("edit", slug), http.StatusFound)
			return
		}
		left = &leftDraftView{Content: d.Content, At: d.At.Format("2006-01-02 15:04")}
		if d.Base != "" {
			if merged, _, err := mergeText(d.Base, content, d.Content, "theirs"); err == nil {
				left.Conflict = merged
			}
		}
	}
	fileHash, start, end := "", 0, 0
	if hasSection && !isNew {
		if s, e, ok := mdtext.SectionCharRange(content, section); ok {
			fileHash, start, end = mdtext.ContentHash(content), s, e
			content = strings.TrimRight(mdtext.RuneSlice(content, s, e), "\n")
		} else {
			hasSection = false // an invalid index: the whole page
		}
	}
	editable := !isNew && !hasSection // a whole existing page
	// Nothing but the title yet (a new page, a new note): the cursor
	// starts after it, ready for the body.
	onlyTitle := !hasSection && onlyTitleRE.MatchString(content)
	if !isNew && !hasSection {
		fileHash = mdtext.ContentHash(content)
	}
	// A new page from a link on a private page: shared, unless made private.
	privateOffer := ""
	if isNew && !hasSection {
		privateOffer = sharedNewOffer(rq.user, slug, q.Get("from") == "private")
	}
	extra := crumb{"Edit", ""}
	if hasSection {
		extra = crumb{"§ " + strconv.Itoa(section), ""}
	}
	page(w, rq, http.StatusOK, "edit.html", "Edit — "+title, shell{editor: true}, &editView{Slug: slug,
		PageTitle: title, Content: content, OnlyTitle: onlyTitle,
		HasSection: hasSection, Section: section, StartChar: start, EndChar: end, FileHash: fileHash,
		CanRename: editable && !isProtected(slug), CanDelete: editable && !isProtected(slug),
		Git: cfg.GitEnabled, RenameError: q.Get("rename_error"), RenameName: q.Get("rename_name"), LeftDraft: left,
		PrivateOffer: privateOffer, Paste: pasteHint(rq, q.Get("paste"), q.Get("copied") == "1"),
		BreadcrumbHTML: buildBreadcrumb(rq, slug, title, "", "", extra),
		TasksHTML:      htmlOf(buildTasksEmbed(rq, TasksParams{}, 0, true, ownPrivate(rq.user, slug)))})
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
	return s.fileHash != "" && s.current != nil && mdtext.ContentHash(*s.current) != s.fileHash
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
		if spliced, _, _, _, ok = mdtext.SpliceSection(s.current, s.fileHash, s.start, s.end, content); ok {
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
	if err := writeDraft(userOf(r), s.slug, s.path, s.current, content, true); err != nil {
		pageFailed(w, r, err)
		return
	}
	keepBase(content)
	dropDraft(userOf(r), s.path)
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
			newHash, newStart, newEnd = mdtext.ContentHash(content), m.start, m.end
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
		start, end, ok := mdtext.SectionCharRange(*s.current, force)
		if !ok {
			badRequestPage(w)
			return
		}
		content, newHash, newStart, newEnd, _ = mdtext.SpliceSection(s.current, mdtext.ContentHash(*s.current), start, end, s.text)
	case s.section:
		if content, newHash, newStart, newEnd, ok = mdtext.SpliceSection(s.current, s.fileHash, s.start, s.end, s.text); !ok && conflict() {
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
		if _, err := keepPending(s.path, s.slug); err != nil {
			serverError(w, r, err)
			return
		}
	}
	if err := writeDraft(userOf(r), s.slug, s.path, s.current, content, false); err != nil {
		pageFailed(w, r, err)
		return
	}
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
			text = strings.TrimRight(mdtext.RuneSlice(content, newStart, newEnd), "\n")
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
		writeJSON(w, http.StatusOK, M{"file_hash": mdtext.ContentHash(content)})
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
		start, end, ok := mdtext.SectionCharRange(theirs, section)
		if !ok {
			badRequestPage(w)
			return
		}
		mine, _, _, _, _ = mdtext.SpliceSection(&theirs, mdtext.ContentHash(theirs), start, end, s.text)
	}
	if !cfg.GitEnabled || mine == theirs {
		writeJSON(w, http.StatusOK, M{"set_aside": false})
		return
	}
	user := userOf(r)
	if _, err := keepPending(s.path, s.slug); err != nil { // theirs, if never in git
		serverError(w, r, err)
		return
	}
	if err := writeText(s.path, mine); err != nil {
		serverError(w, r, err)
		return
	}
	if err := gitCommit(user, "Set aside an edit of "+s.slug, s.path); err != nil {
		// Not in git: putting theirs back would lose it.
		serverError(w, r, err)
		return
	}
	if err := writeText(s.path, theirs); err != nil {
		serverError(w, r, err)
		return
	}
	if err := gitCommit(user, "Reload "+s.slug, s.path); err != nil {
		serverError(w, r, err)
		return
	}
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
			whole, _, _, _, ok = mdtext.SpliceSection(&base, s.fileHash, s.start, s.end, s.text)
		case hasIdx && s.current != nil:
			if start, end, valid := mdtext.SectionCharRange(*s.current, idx); valid {
				whole, _, _, _, ok = mdtext.SpliceSection(s.current, mdtext.ContentHash(*s.current), start, end, s.text)
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
	slug := mdtext.ToSlug(raw)
	if own := privateFolder(userOf(r)); own != "" && slug != "" && r.PostFormValue("private") == "1" &&
		!strings.HasPrefix(slug, own+"/") {
		slug = own + "/" + slug
	}
	if slug == "" {
		badRequestPage(w)
		return
	}
	// A page by that name is there already: it opens in the editor.
	if err := createPageOp(userOf(r), slug, "# "+mdtext.TitleWords(strings.TrimSpace(raw))+"\n\n", ""); err != nil {
		if pe, ok := refusalOf(err); !ok || pe.kind != refusedExists {
			pageFailed(w, r, err)
			return
		}
	}
	http.Redirect(w, r, pathURL("edit", slug), http.StatusFound)
}

func renamePage(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	// Back to where the rename was asked for: the editor, or the page view's
	// ⋯ menu (which reopens its dialog with the error).
	back := pathURL("edit", slug)
	if r.PostFormValue("from") == "page" {
		back = pathURL("page", slug)
	}
	// mdtext.ToSlug keeps underscores, so note timestamps survive a rename.
	newSlug := mdtext.ToSlug(strings.TrimSpace(r.PostFormValue("new_name")))
	if newSlug == "" {
		newSlug = slug
	}
	_, _, err := renamePageOp(userOf(r), slug, newSlug, "")
	if _, err = partly(err); err != nil {
		switch pe, ok := refusalOf(err); {
		case !ok || pe.kind == refusedNotFound || pe.kind == refusedProtected:
			pageFailed(w, r, err)
		case pe.kind == refusedUnchanged:
			http.Redirect(w, r, back, http.StatusFound)
		default:
			http.Redirect(w, r, back+"?rename_name="+quote(newSlug)+"&rename_error="+url.QueryEscape(pe.msg), http.StatusFound)
		}
		return
	}
	http.Redirect(w, r, pathURL("page", newSlug), http.StatusFound)
}

// quicknoteArg: the note a Move to wiki or Append route acts on
// (400 for another page, 404 for none).
func quicknoteArg(w http.ResponseWriter, r *http.Request) (string, bool) {
	slug := r.PathValue("slug")
	if _, err := notePath(userOf(r), slug); err != nil {
		pageFailed(w, r, err)
		return "", false
	}
	return slug, true
}

// quicknoteFailed answers a failed move or append: a refusal goes back to
// the page's dialog (back, the message appended), anything else is a 500.
func quicknoteFailed(w http.ResponseWriter, r *http.Request, back string, err error) {
	if pe, ok := refusalOf(err); ok {
		http.Redirect(w, r, back+quote(pe.msg), http.StatusFound)
		return
	}
	serverError(w, r, err)
}

// promotePage moves a note into the wiki (the Move to wiki dialog),
// links to it following, with its own commit message.
func promotePage(w http.ResponseWriter, r *http.Request) {
	slug, ok := quicknoteArg(w, r)
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
	newSlug, _, err := moveQuicknote(userOf(r), slug, r.PostFormValue("folder"), r.PostFormValue("name"), r.PostFormValue("keep_date") != "", "")
	_, err = partly(err)
	if err != nil {
		quicknoteFailed(w, r, back, err)
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

// appendPage appends a note to a page as a section and deletes the
// note; links to the note point at that section, all in one commit.
func appendPage(w http.ResponseWriter, r *http.Request) {
	slug, ok := quicknoteArg(w, r)
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
	anchor, _, err := appendQuicknote(userOf(r), slug, target, r.PostFormValue("heading"), "")
	if _, err = partly(err); err != nil {
		quicknoteFailed(w, r, back, err)
		return
	}
	http.Redirect(w, r, pathURL("page", target)+"#"+anchor, http.StatusFound)
}

func deletePage(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	if _, err := deletePageOp(userOf(r), slug, ""); err != nil {
		pageFailed(w, r, err)
		return
	}
	next := r.PostFormValue("next")
	if !isLocalPath(next) || strings.Contains(next, "/page/"+slug) || strings.Contains(next, "/edit/"+slug) {
		next = "/page/index"
	}
	http.Redirect(w, r, next, http.StatusFound)
}

// apiPagesList: every page for the search modal's list, and the built-in
// ones: Tasks and Notes are modules (typing finds them); Deleted Pages is
// pinned to the top of the empty query's list (special).
func apiPagesList(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	counts := allBacklinkCounts(rq)
	pages := []M{}
	for _, p := range rq.pageFiles() {
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
		M{"slug": "/tasks", "title": "Tasks", "href": "/tasks", "links": 0, "mtime": 0, "builtin": true},
		M{"slug": "/notes", "title": "Notes", "href": "/notes", "links": counts["_scratch"], "mtime": 0, "builtin": true})
	writeJSON(w, http.StatusOK, out)
}

// tasksPage: /tasks, every task, or with ?list= that list, looking as on the
// page that declares it (that embed's params apply). Every link to "the
// tasks" goes here; [[tasks: …]] embeds are for views on ordinary pages.
func tasksPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	list := cleanList(r.URL.Query().Get("list"))
	params, title, heading := TasksParams{}, "Tasks", "All tasks"
	var home *TasksHome
	var sections []M
	var chip template.HTML
	crumbs := []crumb{{"Tasks", ""}}
	// The breadcrumb's key names the view for the navigation trail; no page has it.
	key := "/tasks"
	if list != "" {
		if declared, ok := rq.taskLists()[list]; ok {
			params = declared.Params
			t, _ := pageMeta(safePath(declared.Slug), declared.Slug)
			home = &TasksHome{Slug: declared.Slug, Title: t}
		}
		// A pinned list looks as its pinned embed shows it, wherever else it
		// is declared.
		if h := rq.pinnedFor(list); h != nil {
			params, home = h.Params, h
		}
		params = params.With("list", list)
		title, heading = "List: "+list, "List: "+list
		// Backlinks: the pages that embed the list, as a page's are.
		if pages := rq.listPages(list); len(pages) > 0 {
			sections = []M{{"id": "backlinks", "heading": "Backlinks", "links": pageLinks(pages)}}
			chip = macro("backlinks_chip", pageLinks(pages))
		}
		crumbs = []crumb{{"Tasks", "/tasks"}, {list, ""}}
		key += "?list=" + list
	}
	// A list declared in the user's private folder makes private tasks, as there.
	private := home != nil && ownPrivate(rq.user, home.Slug)
	tasksHTML := buildTasksEmbed(rq, params, 0, false, private)
	// The list switcher, this list (or All) current.
	place := "all"
	if list != "" {
		place = "list:" + list
	} else if r.URL.Query().Get("pinned") != "" {
		// ?pinned=1: the pinned lists as one task app; with one pin or
		// none, where the Tasks link goes.
		pinned := rq.pinnedLists()
		if len(pinned) < 2 {
			http.Redirect(w, r, rq.tasksLinkHref(), http.StatusFound)
			return
		}
		place, title, heading, crumbs, key = "pinned", "Pinned", "Pinned", []crumb{{"Tasks", "/tasks"}, {"Pinned", ""}}, "/tasks?pinned=1"
		tasksHTML = buildTasksEmbed(rq, rq.pinnedView(), 0, false, rq.defaultPrivate())
	}
	places := rq.taskPlaces()
	for i := range places {
		places[i].Current = places[i].Key == place
	}
	page(w, rq, http.StatusOK, "tasks_page.html", title, shell{wide: sections != nil}, &tasksPageView{Heading: heading, ListName: list,
		Home: home, Places: places, TasksHTML: htmlOf(tasksHTML), LinkSections: sections,
		BreadcrumbHTML: breadcrumb(rq, crumbs, key, true, title, chip)})
}

// tasksPageView: /tasks (tasks_page.html); Home: the page declaring the
// list, nil for all tasks, the pinned lists or an undeclared list; Places: the
// list switcher; LinkSections: a list's Backlinks (with them the page has a
// page view's columns).
type tasksPageView struct {
	view
	Heading, ListName         string
	Home                      *TasksHome
	Places                    []TaskPlace
	LinkSections              []M
	TasksHTML, BreadcrumbHTML template.HTML
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
		keys := mdtext.TaskLineKeys(content)
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
	toggled := mdtext.ToggleNthCheckbox(content, idx, true, now())
	if err := writeDraft(userOf(r), r.PathValue("slug"), path, &content, toggled, false); err != nil {
		pageFailed(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, M{"file_hash": mdtext.ContentHash(toggled)})
}

// apiPreview renders posted markdown (the editor's live preview); ?lists=
// the lists the page declares, so a chip re-rendered alone keeps linking
// to the page's list (js/page.js rerenderTaskRef).
func apiPreview(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if tooLarge(err) {
		tooLargeError(w, bodyMax)
		return
	}
	var lists []string
	for _, x := range strings.Split(r.URL.Query().Get("lists"), ",") {
		if n := cleanList(x); n != "" {
			lists = append(lists, n)
		}
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	// ?page=: the page being edited, for what depends on where the text is
	// (a private folder, or shared); only pages the user may see count.
	rq := newReq(r)
	page := r.URL.Query().Get("page")
	if safePath(page) == "" || !canSee(rq.who(), page) {
		page = ""
	}
	_, _ = io.WriteString(w, renderMd(rq, string(body), RenderOpts{Page: page, PreferLists: lists}))
}

// apiRenderNotes: task notes and comments as HTML, as pages render:
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

// apiSearch: the search modal's results (?limit=, ?unlinked=1; ?notes=1,
// or ?scratch=1 as it was called before: notes only, for Notes page's filter).
func apiSearch(w http.ResponseWriter, r *http.Request) {
	args := r.URL.Query()
	q := strings.TrimSpace(args.Get("q"))
	if q == "" {
		writeJSON(w, http.StatusOK, []M{})
		return
	}
	quicknote := args.Get("notes") == "1" || args.Get("scratch") == "1"
	limit, ok := intArg(args.Get("limit"), args.Has("limit"))
	if !ok {
		limit = searchLimit
	}
	most := searchMaxLimit
	if quicknote {
		most = notesFilterLimit
	}
	results := doSearch(newReq(r), q, args.Get("unlinked") == "1", max(1, min(limit, most)), quicknote)
	// The search dialog's indicator (js/search.js) follows it: indexing → on.
	w.Header().Set("X-Zone-Semantic", semanticStatus())
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
	page(w, rq, http.StatusOK, "search.html", "Search: "+q, shell{}, &searchView{Q: q, Results: results,
		Unlinked: unlinked, Limit: searchPageLimit})
}

// searchView: /search (search.html); Results as /api/search answers them.
type searchView struct {
	view
	Q        string
	Results  []M
	Unlinked bool
	Limit    int
}
