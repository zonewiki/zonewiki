// The wiki's routes: pages, the editor, notes, the
// tasks page, previews, page lists.
package main

import (
	"cmp"
	"errors"
	"html/template"
	"io"
	"maps"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskmodel"
)

func pageRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /private", privateRoute) // pagesMu only to make the home (privateRoute)
	groupsRoutes(mux)
	mux.HandleFunc("GET /groups/{group}", groupRoute) // pagesMu only to make the home (groupRoute)
	mux.HandleFunc("GET /notes", notesPage)
	mux.HandleFunc("GET /page/{slug...}", viewPage)
	mux.HandleFunc("GET /api/page-preview/{slug...}", apiPagePreview)
	mux.HandleFunc("POST /api/notes", writesPages(apiNewNote))
	mux.HandleFunc("GET /edit/{slug...}", editPage)
	mux.HandleFunc("POST /edit/{slug...}", writesPages(savePage))
	mux.HandleFunc("POST /api/save/{slug...}", writesPages(apiSave))
	mux.HandleFunc("GET /new", func(w http.ResponseWriter, r *http.Request) {
		var groups []string
		if u := settings().Users[userOf(r)]; u != nil && !settings().isViewer(u.Name) {
			groups = u.In
		}
		// for: where the page starts (the New page button of a group's home, of the private home).
		who := r.URL.Query().Get("for")
		if g, ok := strings.CutPrefix(who, "group:"); who != "private" && !(ok && slices.Contains(groups, g)) {
			who = ""
		}
		page(w, newReq(r), http.StatusOK, "new.html", "New Page", shell{}, &newPageView{Groups: groups, For: who})
	})
	mux.HandleFunc("POST /new", writesPages(createPage))
	mux.HandleFunc("POST /link-new", writesPages(linkNew))
	mux.HandleFunc("POST /rename/{slug...}", writesPages(renamePage))
	mux.HandleFunc("POST /promote/{slug...}", writesPages(promotePage))
	mux.HandleFunc("POST /append/{slug...}", writesPages(appendPage))
	mux.HandleFunc("POST /delete/{slug...}", writesPages(deletePage))
	mux.HandleFunc("GET /pages", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/", http.StatusFound)
	})
	mux.HandleFunc("GET /api/pages-list", apiPagesList)
	mux.HandleFunc("GET /tasks", tasksPage)
	mux.HandleFunc("POST /tasks/rename", writesPages(renameTagRoute))
	mux.HandleFunc("POST /tasks/new-list", writesPages(newListRoute))
	mux.HandleFunc("POST /tasks/rename-list", writesPages(renameListRoute))
	mux.HandleFunc("POST /tasks/delete-list", writesPages(deleteListRoute))
	mux.HandleFunc("POST /tasks/share-list", writesPages(shareListRoute))
	mux.HandleFunc("POST /tasks/list-for", listForRoute)
	mux.HandleFunc("POST /tasks/pin-list", writesPages(pinListRoute))
	mux.HandleFunc("POST /tasks/pin-order", writesPages(pinOrderRoute))
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
	if pe, ok := refusalOf(err); ok && pe.kind == refusedFull {
		http.Error(w, pe.msg, http.StatusInsufficientStorage)
		return
	}
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
		if from := r.URL.Query().Get("from"); newFromRE.MatchString(from) {
			to += "?from=" + from // the link was on a private page, or a group's (wikilinker)
			if on := r.URL.Query().Get("on"); on != "" {
				to += "&on=" + url.QueryEscape(on)
			}
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
	headings := pageHeadings(content)
	headingNames := map[string]string{}
	for _, h := range headings {
		if h.startsSection() {
			headingNames[mdtext.SectionAnchor(h.text)] = mdtext.PlainHeading(h.text)
		}
	}
	var tasks []TaskLink
	for _, t := range findTaskBacklinks(rq.openTasks(), slug) {
		if inline[t.ID] {
			continue
		}
		if name, ok := headingNames[t.Anchor]; ok && t.Anchor != "" {
			t.Section = name
		}
		tasks = append(tasks, t)
	}
	sections := linkSections(rq, backlinks, tasks)
	if rel := relatedSection(rq, slug, path, backlinks); rel != nil {
		sections = append(sections, *rel)
	}
	chip := macro("backlinks_chip", pageLinks(backlinks))

	// Viewing a page from the uncommitted-changes list: Commit instead of Edit.
	fromUncommitted := r.URL.Query().Get("from") == "uncommitted" && cfg.GitEnabled && rq.user != visitor
	var bc, metaRow, article template.HTML
	var menu *pageMenuView
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
		menu = pageMenu(rq, slug, title, q.Get("rename_error"), q.Get("promote_error"), q.Get("append_error"), q.Get("rename_name"), q.Get("rename_ask"))
		changed, commit := lastChanged(slug, path, title)
		if rq.user == visitor {
			commit = nil // a visitor commits nothing
		}
		meta, _ := mdtext.ParseFrontmatter(content)
		metaRow = macro("meta_row", metaRowView{TagsHTML: htmlOf(renderTagsPillsHTML(meta.Tags)), Tldr: meta.Tldr,
			Changed: changed, Commit: commit})
		article = htmlOf(renderMd(rq, mdtext.StripH1(content), RenderOpts{Page: slug, HideTags: true, HideTldr: true,
			EditSlug: slug, SectionKeys: sectionKeys(content, headings), SectionTasks: tasks}))
	}
	// A place's home is where its pages are started: New page, with For at that place.
	newFor := ""
	if g := placeOf(slug).group(); g != "" && isGroupHome(slug) {
		newFor = "group:" + g
	} else if isPrivateHome(slug) && placeOf(slug).own(rq.user) {
		newFor = "private"
	}
	page(w, rq, http.StatusOK, "view.html", title, shell{wide: true}, &pageView{Title: title, Slug: slug, NewPageFor: newFor,
		FromUncommitted: fromUncommitted, Private: placeOf(slug).user() != "", Group: placeOf(slug).group(), GroupTitle: groupTitle(slug), ReadOnly: readOnly(rq.user, slug), TaskKeys: strings.Join(mdtext.TaskLineKeys(content), ","),
		BreadcrumbHTML: bc, DeleteDialogHTML: buildDeleteDialog(slug, title), RailTocHTML: railTocItems(string(article)),
		MetaRowHTML: metaRow, ArticleHTML: article, LinkSections: sections, Outlinks: outlinks(rq, slug, path), Menu: menu})
}

// pageView: a page's view (view.html).
type pageView struct {
	view
	Title, Slug                                                             string
	FromUncommitted                                                         bool   // opened from the uncommitted list: Commit instead of Edit
	Private                                                                 bool   // in a private folder (the lock)
	Group, GroupTitle                                                       string // in a group's folder: the group, and what its mark says (who sees the page)
	NewPageFor                                                              string // a place's home: what /new's For starts at for its New page button ("group:<name>", "private"), else ""
	ReadOnly                                                                bool   // a viewer's view of a shared page: no Edit, ✎ or ticks
	TaskKeys                                                                string // a key per checkbox line (mdtext.TaskLineKeys), comma-separated
	BreadcrumbHTML, DeleteDialogHTML, RailTocHTML, MetaRowHTML, ArticleHTML template.HTML
	LinkSections                                                            []linkSection // Backlinks, Linked tasks, Related (linkSections)
	Outlinks                                                                []pageLink    // the right column's Links to
	Menu                                                                    *pageMenuView // the ⋯ menu (pageMenu); nil from the uncommitted list
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
	HasSection                  bool // one section (Section, its heading line, its range StartChar–EndChar)
	Section, StartChar, EndChar int
	SectionHeading              string
	FileHash                    string // the file's hash for conflict detection, whole page or section
	CanRename, CanDelete        bool
	Git                         bool
	RenameError, RenameName     string
	RenameAsk                   string // a rename to other readers, asked first: what it would do (the form then confirms that name)
	PrivateOffer                string // a new shared page from a private one, or a group's: the slug in that folder to link instead
	OfferGroup                  string // the group whose folder PrivateOffer is in ("": the user's own)
	OfferOn                     string // the page whose link led here, when Make it … can point that link at PrivateOffer ("": the note only says how)
	LeftDraft                   *leftDraftView
	Paste                       *pasteHintView // after Move to wiki (pasteHint)
	TaskLists                   []string       // the lists there are, for the Task list dialog
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
	if refusePage(w, r, rq, slug) || refuseWrite(w, r, slug) {
		return // a viewer's editor on a shared page: 403 before anything is typed
	}
	var content, title string
	isNew := !exists(path)
	if isNew {
		title = mdtext.SlugDisplayName(slug)
		content = "# " + title + "\n\n"
		// Full: refused before anything is typed, not at its first save.
		if err := checkRoom(int64(len(content))); err != nil {
			pageFailed(w, r, err)
			return
		}
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
	fileHash, start, end, heading := "", 0, 0, ""
	if hasSection && !isNew {
		s, e, ok := 0, 0, false
		if key := q.Get("h"); key != "" {
			// A ✎ from a page view older than the page: the section it was
			// on, wherever that is by now.
			section, s, e, ok = findSection(content, section, key)
		} else {
			s, e, ok = sectionRange(content, section)
		}
		if ok {
			fileHash, start, end = mdtext.ContentHash(content), s, e
			content = strings.TrimRight(mdtext.RuneSlice(content, s, e), "\r\n")
			heading = firstLine(content)
		} else {
			hasSection = false // no such section: the whole page
		}
	}
	editable := !isNew && !hasSection // a whole existing page
	// Nothing but the title yet (a new page, a new note): the cursor
	// starts after it, ready for the body.
	onlyTitle := !hasSection && onlyTitleRE.MatchString(content)
	if !isNew && !hasSection {
		fileHash = mdtext.ContentHash(content)
	}
	// A new page from a link on a private page, or a group's: shared, unless
	// it is made in that folder.
	privateOffer, offerGroup, offerOn := "", "", ""
	if isNew && !hasSection {
		privateOffer, offerGroup = sharedNewOffer(rq.user, slug, q.Get("from"))
		// The page the link is on, when it is one of that folder they may
		// change: one click makes the new page there and points the link at it.
		if on := q.Get("on"); privateOffer != "" && on == mdtext.ToSlug(on) && newFrom(placeOf(on)) == q.Get("from") &&
			!placeOf(on).shared() && canWrite(rq.user, on) && safePath(on) != "" && exists(safePath(on)) {
			offerOn = on
		}
	}
	extra := crumb{"Edit", ""}
	if hasSection {
		extra = crumb{"§ " + strconv.Itoa(section), ""}
	}
	page(w, rq, http.StatusOK, "edit.html", "Edit — "+title, shell{editor: true}, &editView{Slug: slug,
		PageTitle: title, Content: content, OnlyTitle: onlyTitle,
		HasSection: hasSection, Section: section, SectionHeading: heading, StartChar: start, EndChar: end, FileHash: fileHash,
		// A visitor (a public wiki) edits and makes pages, and moves none.
		CanRename: editable && !isProtected(slug) && rq.user != visitor, CanDelete: editable && !isProtected(slug) && rq.user != visitor,
		Git: cfg.GitEnabled, RenameError: q.Get("rename_error"), RenameName: q.Get("rename_name"), RenameAsk: q.Get("rename_ask"), LeftDraft: left,
		PrivateOffer: privateOffer, OfferGroup: offerGroup, OfferOn: offerOn, Paste: pasteHint(rq, q.Get("paste"), q.Get("copied") == "1"),
		TaskLists:      slices.Sorted(maps.Keys(rq.taskLists())),
		BreadcrumbHTML: buildBreadcrumb(rq, slug, title, "", "", extra),
		TasksHTML:      editorTasksHTML(rq, slug)})
}

// editorTasksHTML: the editor's hidden task app, for Make task; none for a
// public wiki's visitor, who makes no tasks.
func editorTasksHTML(rq *Req, slug string) template.HTML {
	if rq.user == visitor {
		return ""
	}
	return htmlOf(buildTasksEmbed(rq, TasksParams{}, 0, true, ""))
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

// linkNew: POST /link-new: the new page slug, opened from a link on the
// page on (one of the user's private pages, or a group's), is made in on's
// folder instead of shared: the link on on points there, and the editor
// opens for the page in that folder (linkNewOp).
func linkNew(w http.ResponseWriter, r *http.Request) {
	target, err := linkNewOp(userOf(r), r.PostFormValue("on"), r.PostFormValue("slug"))
	if err != nil && !writtenAnyway(err) {
		pageFailed(w, r, err)
		return
	}
	http.Redirect(w, r, pathURL("edit", target), http.StatusSeeOther)
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
// (guarded by the file's hash), or force_section (spliced where the section
// with section_heading is now, refused if that can't be told). A
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
		// Into the file as it is now, without the hash guard: the section
		// the editor was on, found by its heading, or nothing is written.
		if s.current == nil {
			http.NotFound(w, r)
			return
		}
		_, start, end, ok := findSection(*s.current, force, headingKey(r.PostFormValue("section_heading")))
		if !ok {
			writeJSON(w, http.StatusConflict, M{"error": "section_gone", "file_hash": keepGivenBase(*s.current)})
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
		// Only for the page's history, so a section that can't be told loses
		// nothing: the text is added at the end there.
		mine, _ = spliceIntoPage(theirs, section, r.PostFormValue("section_heading"), s.text)
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
			// A section that can't be told: at the end, for the banner's choices.
			whole, _ = spliceIntoPage(*s.current, idx, r.PostFormValue("section_heading"), s.text)
			ok = true
		}
		if !ok {
			return
		}
	}
	leaveDraft(userOf(r), s.path, whole, base)
}

// newPageView: /new (new.html): the groups the user is in, whose folders a
// new page can be made in.
type newPageView struct {
	view
	Groups []string
	For    string // what For starts at: "", "private", "group:<name>"
}

// createPage: /new, the form for a new page's name (pages are made from
// links to them; this is its form without JavaScript); private=1 (or
// for=private) puts the page in the user's private folder, for=group:<name>
// in the folder of a group they are in.
func createPage(w http.ResponseWriter, r *http.Request) {
	raw := r.PostFormValue("slug")
	slug := mdtext.ToSlug(raw)
	who := r.PostFormValue("for")
	if own := privateFolder(userOf(r)); own != "" && slug != "" && (r.PostFormValue("private") == "1" || who == "private") &&
		!strings.HasPrefix(slug, own+"/") {
		slug = own + "/" + slug
	}
	if g, ok := strings.CutPrefix(who, "group:"); ok && slug != "" && newFromRE.MatchString(who) &&
		settings().memberOf(userOf(r), g) && !strings.HasPrefix(slug, groupsDir+"/"+g+"/") {
		slug = groupsDir + "/" + g + "/" + slug
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
	// confirmed: a move to other readers was asked about and answered: the
	// name the question was about (the rename dialog, asked again below), or
	// "share" from Share… and Make shared…, which say who will see the page
	// (to more people only).
	confirmed := r.PostFormValue("confirmed")
	asked := moveSure{wider: confirmed == newSlug || confirmed == "share", narrower: confirmed == newSlug}
	_, _, err := renamePageOp(userOf(r), slug, newSlug, "", asked)
	if ask := (*askError)(nil); errors.As(err, &ask) {
		http.Redirect(w, r, back+"?rename_name="+quote(newSlug)+"&rename_ask="+url.QueryEscape(ask.msg), http.StatusFound)
		return
	}
	if _, err = partly(err); err != nil {
		switch pe, ok := refusalOf(err); {
		case !ok || pe.kind == refusedNotFound || pe.kind == refusedProtected || pe.kind == refusedReadOnly:
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
	if target != "" && readOnly(userOf(r), target) {
		http.Redirect(w, r, back+quote(readOnlyWhy(userOf(r), target)), http.StatusFound)
		return
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
		private := placeOf(p.Slug).user() != "" // not hidden above: the user's own
		pages = append(pages, M{"slug": p.Slug, "title": title, "links": counts[p.Slug], "private": private,
			"group": placeOf(p.Slug).group(), // a group's page (not hidden above: one of the user's groups)
			"mtime": st.ModTime().Unix(), "created": fmtDate(changeTime(st)), "edited": fmtDate(st.ModTime())})
	}
	sort.SliceStable(pages, func(i, j int) bool {
		return strings.ToLower(pages[i]["title"].(string)) < strings.ToLower(pages[j]["title"].(string))
	})
	if rq.user == visitor {
		// A public wiki's visitor: the pages, not the views that need an account.
		writeJSON(w, http.StatusOK, pages)
		return
	}
	out := []M{}
	if cfg.GitEnabled {
		out = append(out, M{"slug": "_deleted", "title": "Deleted Pages", "links": 0, "mtime": 0, "builtin": true, "special": true})
	}
	out = append(out, pages...)
	out = append(out,
		M{"slug": "/tasks", "title": "Tasks", "href": "/tasks", "links": 0, "mtime": 0, "builtin": true},
		M{"slug": "/notes", "title": "Notes", "href": "/notes", "links": 0, "mtime": 0, "builtin": true})
	writeJSON(w, http.StatusOK, out)
}

// tasksPage: /tasks, every task; with ?list= that list (by its ref), with
// its settings, ?list=none the tasks in no list; with ?mine=1 My tasks,
// the user's home view (mineFilter); with ?tag= every task, filtered by the tag (tasks.js
// reads it). Every link to "the tasks" goes here; [[tasks: …]] embeds are
// for views on ordinary pages.
func tasksPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	q := r.URL.Query()
	ref := cleanList(q.Get("list"))
	params, title, heading := allTasksParams(), "Tasks", "All tasks"
	var list *ListInfo
	var sections []linkSection
	var chip template.HTML
	mine, inbox := false, false
	missing := ""
	crumbs := []crumb{{"Tasks", ""}}
	// The breadcrumb's key names the view for the navigation trail; no page has it.
	key, place := "/tasks", "all"
	backlinks := func(pages []PageRef) {
		if len(pages) > 0 {
			sections = []linkSection{{ID: "backlinks", Heading: "Backlinks", Links: pageLinks(pages)}}
			chip = macro("backlinks_chip", pageLinks(pages))
		}
	}
	switch {
	case ref != "":
		name := ref
		title, heading = "List: "+name, "List: "+name
		if l, ok := resolveList(ref, rq.taskLists()); ok {
			list, params, name, place = &l, l.Params, l.Name, "list:"+l.Ref
			title, heading = "List: "+name, "List: "+name // the name as the list has it
			// Backlinks: the pages that embed the list, as a page's are.
			backlinks(rq.listPages(l.Ref))
		} else if isInbox(ref, rq.taskLists()) && rq.hasInbox() {
			// ?list=inbox: the Inbox, the tasks in no list.
			params, name, place, title, heading = inboxParams(), "Inbox", inboxRef, "Inbox", "Inbox"
			inbox = true
		} else {
			missing, place = ref, "" // nothing in the switcher is current
		}
		crumbs = []crumb{{"Tasks", "/tasks"}, {name, ""}}
		key += "?list=" + ref
	case q.Get("mine") != "":
		// ?mine=1: My tasks, one task app; with no list in the wiki that is
		// every task, at /tasks.
		if len(rq.taskLists()) == 0 {
			http.Redirect(w, r, "/tasks", http.StatusFound)
			return
		}
		params, place, title, heading, key = mineParams(rq.who()), "mine", "My tasks", "My tasks", mineHref
		crumbs = []crumb{{"Tasks", "/tasks"}, {"My tasks", ""}}
		mine = true
	}
	var tasksHTML string
	if missing != "" {
		tasksHTML = noListHTML(rq, missing)
	} else {
		tasksHTML = tasksInitial(rq, paramsScope(params)) + buildTasksEmbed(rq, params, 0, false, "")
	}
	// The list switcher: the pills, this view's the current one; a view
	// that has no pill is under More, which then names it.
	more := rq.taskMore(place)
	var places []TaskPlace
	if more != nil {
		places = rq.taskPlaces()
		for i := range places {
			places[i].Current = places[i].Key == place
		}
	}
	v := &tasksPageView{Heading: heading, Missing: missing,
		Menu: !rq.sharing && !listsUnmigrated && mayWriteTasks(rq.who()) == nil, Users: privateFolder(rq.user) != "",
		Mine: mine, MineAssigns: mine && mineAssigns(rq.who()), Inbox: inbox, InboxPrivate: inbox && inboxOwner(rq.who()) != "",
		Groups: settings().groups(), People: settings().assignableUsers(), NewListName: q.Get("newlist_name"), NewListError: q.Get("newlist_error"), NewListFor: q.Get("newlist_for"),
		Places: places, More: more, TasksHTML: htmlOf(tasksHTML), LinkSections: sections,
		BreadcrumbHTML: breadcrumb(rq, crumbs, key, true, title, chip)}
	if mine {
		v.LeftOutLists = rq.leftOutLinks()
		if v.MineAssigns {
			v.Given, v.GivenHref = givenToOthers(rq.who(), rq.openTasks()), givenHref
		}
	}
	if list != nil {
		v.List = list
		v.Manage = canManageList(rq.who(), *list)
		v.ForWords, v.ForError = firstNonEmpty(q.Get("for_words"), forWords(list.For)), q.Get("for_error")
		v.Members, v.ForNow = listMembers(list.For), forWords(list.For)
		v.ForConfirm = q.Get("for_confirm") != "" && v.ForError != ""
		// The wiki's pins (Pin for everyone): what a user who has set none has.
		if !list.Private() && privateFolder(rq.user) != "" {
			v.WikiPins = true
			for _, name := range taskFiles.ReadLists("").Pinned {
				v.WikiPinned = v.WikiPinned || name == list.Name
			}
		}
		v.ListRenameName, v.ListRenameError = firstNonEmpty(q.Get("rename_name"), list.Name), q.Get("rename_error")
		v.DeleteError, v.ShareError = q.Get("delete_error"), q.Get("share_error")
		for i, p := range rq.pinnedLists() {
			if p.Ref == list.Ref {
				v.Pin = map[bool]string{true: "first", false: "1"}[i == 0]
			}
		}
		// How many open tasks it has: Delete… offers to close them; and of a
		// shared list's, with users, how many no one has: its backlog.
		assigns := settings().Users != nil && !list.Private()
		for _, t := range rq.openTasks() {
			if t.List == list.Name && t.Owner == list.Owner {
				v.OpenInList++
				if assigns && (t.Assignee == "" || assigneeGone(t)) {
					v.Unassigned++
				}
			}
		}
		v.LeftOut = list.LeftOut() && (list.Private() || !mineAssigns(rq.who()))
		// Where a deleted list's tasks go when no list is named: a private
		// list's to the Inbox, a shared one's with users to #general.
		v.DeleteToGeneral = settings().Users != nil && !list.Private() && list.Name != generalListName
		// Where its tasks can go when it is deleted: the other lists, a
		// private list's own kind first.
		for _, ref := range sortedKeys(rq.taskLists()) {
			if o := rq.taskLists()[ref]; o.Ref != list.Ref && (list.Private() || !o.Private()) &&
				!(v.DeleteToGeneral && !o.Private() && o.Name == generalListName) {
				v.MoveTo = append(v.MoveTo, o)
			}
		}
		slices.SortStableFunc(v.MoveTo, func(a, b ListInfo) int {
			return cmp.Compare(btoi(a.Private() != list.Private()), btoi(b.Private() != list.Private()))
		})
	} else if tags := taskmodel.CleanTags([]string{q.Get("tag")}); place == "all" && len(tags) == 1 && !strings.ContainsAny(tags[0], ",|") {
		// ?tag=<one tag>: its Rename… is in the menu.
		v.Tag = tags[0]
		v.RenameName, v.RenameError, v.RenameMerge = firstNonEmpty(q.Get("rename_name"), v.Tag), q.Get("rename_error"), q.Get("rename_merge") != ""
	}
	page(w, rq, http.StatusOK, "tasks_page.html", title, shell{wide: sections != nil}, v)
}

func btoi(b bool) int {
	if b {
		return 1
	}
	return 0
}

// listBack: where a list's dialog goes back to with its refusal: the
// list's view with the dialog's params (name → value).
func listBack(w http.ResponseWriter, r *http.Request, ref string, params ...string) {
	back := listHref(ref)
	for i := 0; i+1 < len(params); i += 2 {
		back += "&" + params[i] + "=" + url.QueryEscape(params[i+1])
	}
	http.Redirect(w, r, back, http.StatusFound)
}

// renameTagRoute: the Rename tag… dialog of /tasks?tag=<tag>. A refusal
// goes back to the dialog with the message; a new name in use, there with
// the offer to merge.
func renameTagRoute(w http.ResponseWriter, r *http.Request) {
	tag, name := r.PostFormValue("tag"), strings.TrimSpace(r.PostFormValue("new_name"))
	back := "/tasks?tag=" + url.QueryEscape(strings.TrimLeft(strings.TrimSpace(tag), "#"))
	_, err := renameTagOp(userOf(r), tag, name, r.PostFormValue("merge") != "", "")
	if err != nil {
		pe, ok := refusalOf(err)
		switch {
		case !ok:
			serverError(w, r, err)
		case pe.kind == refusedUnchanged:
			http.Redirect(w, r, back, http.StatusFound)
		default:
			back += "&rename_name=" + url.QueryEscape(name) + "&rename_error=" + url.QueryEscape(pe.msg)
			if pe.kind == refusedExists {
				back += "&rename_merge=1"
			}
			http.Redirect(w, r, back, http.StatusFound)
		}
		return
	}
	http.Redirect(w, r, "/tasks?tag="+url.QueryEscape(strings.TrimLeft(name, "#")), http.StatusFound)
}

// listRefused answers a list operation's refusal: one the dialog can say
// goes back to the list's view with it (param: the dialog's error), a list
// that isn't there is a 404, a viewer's a 403; anything else failed.
func listRefused(w http.ResponseWriter, r *http.Request, err error, ref, param string, more ...string) {
	pe, ok := refusalOf(err)
	switch {
	case !ok:
		serverError(w, r, err)
	case pe.kind == refusedUnchanged:
		listBack(w, r, ref)
	case pe.kind == refusedNotFound, pe.kind == refusedReadOnly:
		pageFailed(w, r, err)
	default:
		listBack(w, r, ref, append([]string{param, pe.msg}, more...)...)
	}
}

// newListRoute: the New list… dialog of /tasks (its ⋯ menu), and Create it
// under an embed of a list that isn't there. The new list is pinned for who
// made it (pin=0: not; no dialog sends that) and opens; a refusal
// that another name would mend goes back to the dialog with the message.
func newListRoute(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PostFormValue("name"))
	user := userOf(r)
	ref, err := createListOp(user, name, r.PostFormValue("private") != "", r.PostFormValue("pin") != "0", r.PostFormValue("for"), "")
	if err != nil {
		if pe, ok := refusalOf(err); ok && (pe.kind == refusedInvalid || pe.kind == refusedExists) {
			http.Redirect(w, r, "/tasks?newlist_name="+url.QueryEscape(name)+"&newlist_error="+url.QueryEscape(pe.msg), http.StatusFound)
			return
		}
		pageFailed(w, r, err)
		return
	}
	http.Redirect(w, r, listHref(ref), http.StatusFound)
}

// pinOrderRoute: the pinned lists in a new order (the row's pills, dragged:
// js/listsrow.js): every list field a ref, in the order wanted.
func pinOrderRoute(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		badRequestPage(w)
		return
	}
	if err := pinOrderOp(userOf(r), r.PostForm["list"]); err != nil {
		pageFailed(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// renameListRoute: a list's Rename… dialog (/tasks?list=<ref>).
func renameListRoute(w http.ResponseWriter, r *http.Request) {
	ref, name := cleanList(r.PostFormValue("list")), strings.TrimSpace(r.PostFormValue("new_name"))
	done, err := renameListOp(userOf(r), ref, name, "")
	if err != nil {
		listRefused(w, r, err, ref, "rename_error", "rename_name", name)
		return
	}
	http.Redirect(w, r, listHref(done.Ref), http.StatusFound)
}

// deleteListRoute: a list's Delete… dialog: its tasks go to another list
// (to) or to none; share: a private list's tasks may become shared so;
// close: its open tasks are closed as won't do.
func deleteListRoute(w http.ResponseWriter, r *http.Request) {
	ref, to := cleanList(r.PostFormValue("list")), cleanList(r.PostFormValue("to"))
	if _, err := deleteListOp(userOf(r), ref, to, r.PostFormValue("share") != "", r.PostFormValue("close") != "", ""); err != nil {
		listRefused(w, r, err, ref, "delete_error")
		return
	}
	if to != "" {
		http.Redirect(w, r, listHref(to), http.StatusFound)
		return
	}
	http.Redirect(w, r, "/tasks", http.StatusFound)
}

// listForRoute: a shared list's Who it is for… dialog (its owners').
func listForRoute(w http.ResponseWriter, r *http.Request) {
	ref := cleanList(r.PostFormValue("list"))
	err := setListForOp(userOf(r), ref, r.PostFormValue("for"), r.PostFormValue("confirm") != "")
	if ask := (*askError)(nil); errors.As(err, &ask) {
		// What it would take out of others' sight, said in the dialog: Save again goes on.
		listBack(w, r, ref, "for_error", ask.msg, "for_words", r.PostFormValue("for"), "for_confirm", "1")
		return
	}
	if err != nil {
		listRefused(w, r, err, ref, "for_error", "for_words", r.PostFormValue("for"))
		return
	}
	http.Redirect(w, r, listHref(ref), http.StatusFound)
}

// shareListRoute: a private list's Make shared… dialog.
func shareListRoute(w http.ResponseWriter, r *http.Request) {
	ref := cleanList(r.PostFormValue("list"))
	name, err := shareListOp(userOf(r), ref, "")
	if err != nil {
		listRefused(w, r, err, ref, "share_error")
		return
	}
	http.Redirect(w, r, listHref(name), http.StatusFound)
}

// pinListRoute: a list's Pin, Move first and Unpin (its ⋯ menu, the
// settings dialog's pin): pin is 1, first or 0. Back to the list.
func pinListRoute(w http.ResponseWriter, r *http.Request) {
	ref := cleanList(r.PostFormValue("list"))
	if err := pinListOp(userOf(r), ref, r.PostFormValue("pin"), r.PostFormValue("everyone") != ""); err != nil {
		pageFailed(w, r, err)
		return
	}
	http.Redirect(w, r, listHref(ref), http.StatusFound)
}

// tasksPageView: /tasks (tasks_page.html). List: the list shown, nil for
// all tasks and My tasks; Missing: the name ?list= gave when no
// list has it; Tag: the one tag ?tag= names; Places: the list switcher's
// pills and More its last one (nil: no switcher);
// Mine: the view is My tasks (MineAssigns: assignment counts in it for
// this user, mineAssigns), LeftOutLists: the lists left out of it; Inbox:
// the view is the Inbox (InboxPrivate: the user's own, with users);
// Unassigned: a shared list's open tasks that no one has (with users);
// LeftOut: the list is left out of the user's My tasks; DeleteToGeneral: a
// deleted shared list's tasks go to #general when no list is named;
// LinkSections: a list's Backlinks (with them the page has a page view's
// columns). Menu: the ⋯ menu (New list…; a list's Rename…,
// its pin, Make shared…, Delete…; a tag's Rename tag…), for who may change
// tasks; Users: with users, so New list… has its Private box. Pin: how the
// user pinned List ("", "1", "first": their first pinned list); MoveTo: where its tasks can go
// when it is deleted. The …Name and …Error fields: a dialog back from a
// refusal.
type tasksPageView struct {
	view
	Heading, Missing, Tag           string
	List                            *ListInfo
	Pin                             string
	MoveTo                          []ListInfo
	OpenInList                      int // the list's open tasks (Delete…'s "Close its open tasks")
	RenameName, RenameError         string
	RenameMerge                     bool
	ListRenameName, ListRenameError string
	DeleteError, ShareError         string
	Menu, Users                     bool
	Manage                          bool // the list is the user's to rename, delete and set (canManageList)
	// A shared list for some: whom it is for as the dialog's field says it
	// ("@dev, anna"; a refused one as it was typed), why the dialog came
	// back, and its members by name; Groups: the groups there are, to
	// complete from.
	ForWords, ForError string
	ForNow             string // whom it is for now, "@dev, anna"
	ForConfirm         bool   // the dialog is back to ask: Save again goes on (confirm)
	// WikiPins: the list can be pinned for everyone (a shared one, with
	// users), by who manages it; WikiPinned: it is. People: who a For field
	// can name, beside the groups.
	WikiPins, WikiPinned      bool
	People                    []string
	Members, Groups           []string
	Mine, MineAssigns         bool
	NewListName, NewListError string
	NewListFor                string // what New list…'s For starts with, the dialog open (?newlist_for=@dev: New list on a group's entry of Groups)
	Places                    []TaskPlace
	More                      *TasksMore
	Inbox, InboxPrivate       bool
	LeftOutLists              []listLink
	Unassigned                int
	GivenHref                 string
	Given                     int // My tasks: the open tasks the user made that someone else has (givenToOthers)
	LeftOut, DeleteToGeneral  bool
	LinkSections              []linkSection
	TasksHTML, BreadcrumbHTML template.HTML
}

func apiToggleCheckbox(w http.ResponseWriter, r *http.Request) {
	path := safePath(r.PathValue("slug"))
	if path == "" || !exists(path) || refuseWrite(w, r, r.PathValue("slug")) {
		if path == "" || !exists(path) {
			http.NotFound(w, r)
		}
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

// apiPreview renders posted markdown (the editor's live preview, a task
// chip re-rendered alone: js/page.js rerenderTaskRef).
func apiPreview(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if tooLarge(err) {
		tooLargeError(w, bodyMax)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	// ?page=: the page being edited, for what depends on where the text is
	// (a private folder, or shared); only pages the user may see count.
	rq := newReq(r)
	page := r.URL.Query().Get("page")
	if safePath(page) == "" || !canSee(rq.who(), page) {
		page = ""
	}
	_, _ = io.WriteString(w, renderMd(rq, string(body), RenderOpts{Page: page}))
}

// apiRenderNotes: task notes and comments as HTML, as pages render:
// {"notes": [text…], "comments": [text…]} → the same with HTML. A comment
// keeps its lines: a line's end is a line break.
func apiRenderNotes(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	data, err := jsonObject(r)
	if err != nil {
		badRequestPage(w)
		return
	}
	out := M{}
	for _, key := range []string{"notes", "comments"} {
		var texts []string
		if !data.Null(key) {
			var ok bool
			if texts, ok = jsonobj.As[[]string](data[key]); !ok {
				badRequestPage(w)
				return
			}
		}
		out[key] = renderNotes(rq, texts, key == "comments")
	}
	writeJSON(w, http.StatusOK, out)
}

// apiSearch: the search modal's results (?limit=, ?unlinked=1; ?notes=1:
// notes only, for the Notes page's filter).
func apiSearch(w http.ResponseWriter, r *http.Request) {
	args := r.URL.Query()
	q := strings.TrimSpace(args.Get("q"))
	if q == "" {
		writeJSON(w, http.StatusOK, []M{})
		return
	}
	quicknote := args.Get("notes") == "1"
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
