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
	mux.HandleFunc("GET /page/_scratch", scratchPage)
	mux.HandleFunc("GET /page/{slug...}", viewPage)
	mux.HandleFunc("GET /api/page-preview/{slug...}", apiPagePreview)
	mux.HandleFunc("POST /api/scratch", func(w http.ResponseWriter, r *http.Request) {
		slug, err := createScratch(r.PostFormValue("title"), nil, "")
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
	sectionHeadRE = regexp.MustCompile(`(?m)^#{2,3}` + pySpace + `+(.+?)` + pySpace + `*#*` + pySpace + `*$`)
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
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
		return
	}
	if !exists(path) {
		// A page deleted in git history offers to come back.
		if cfg.GitEnabled {
			rel := slug + ".md"
			g := runGit("log", "-1", "--format=%H\t%P\t%as", "--diff-filter=D", "--", rel)
			if out := pyStrip(g.Stdout); g.Code == 0 && out != "" {
				parts := strings.SplitN(out, "\t", 3)
				parent := ""
				if len(parts) > 1 {
					if ps := pySplit(parts[1]); len(ps) > 0 {
						parent = ps[0]
					}
				}
				if parent != "" {
					title := slugDisplayName(slug)
					if g2 := runGit("show", parent+":"+rel); g2.Code == 0 {
						title = pageTitle(g2.Stdout, slug)
					}
					date := ""
					if len(parts) > 2 {
						date = parts[2]
					}
					page(w, rq, http.StatusOK, "deleted_page.html", title, shell{}, M{"slug": slug, "sha": parent,
						"date": date, "breadcrumb_html": buildBreadcrumb(rq, slug, title, "", "")})
					return
				}
			}
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
	inline := pageTaskRefs(content)
	// A section a task links to is named as the page's heading says it.
	masked, _ := maskCode(content)
	headingNames := map[string]string{}
	for _, m := range sectionHeadRE.FindAllStringSubmatch(masked, -1) {
		headingNames[sectionAnchor(m[1])] = plainHeading(m[1])
	}
	var tasks []TaskLink
	for _, t := range findTaskBacklinks(slug) {
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
	sections := linkSections(backlinks, tasks, lists)
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
		article = htmlOf(renderMd(rq, stripH1(content), RenderOpts{}))
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
		article = htmlOf(renderMd(rq, stripH1(content), RenderOpts{HideTags: true, HideTldr: true,
			EditSlug: slug, SectionTasks: tasks}))
	}
	page(w, rq, http.StatusOK, "view.html", title, shell{wide: true}, M{"slug": slug, "from_uncommitted": fromUncommitted,
		"breadcrumb_html": bc, "delete_dialog_html": buildDeleteDialog(slug, title),
		"rail_toc_html": railTocItems(string(article)), "meta_row_html": metaRow, "article_html": article,
		"link_sections": sections, "outlinks": outlinks(rq, slug, path), "menu": menu})
}

// intArg is Flask's args.get(name, type=int): ok false when missing or not an int.
func intArg(v string, present bool) (int, bool) {
	if !present {
		return 0, false
	}
	n, err := strconv.Atoi(pyStrip(v))
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
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
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
	q := r.URL.Query()
	section, hasSection := intArg(q.Get("section"), q.Has("section"))
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
		"git": cfg.GitEnabled, "rename_error": q.Get("rename_error"),
		"paste":           pasteHint(rq, q.Get("paste"), q.Get("copied") == "1"),
		"breadcrumb_html": buildBreadcrumb(rq, slug, title, "", "", extra),
		"deck_html":       htmlOf(buildDeckEmbed(DeckParams{}, 0, true))})
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

func savePage(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	path := safePath(slug)
	if path == "" {
		badRequestPage(w)
		return
	}
	content := postedContent(r)
	fileHash := r.PostFormValue("file_hash")
	start, okS := formInt(r, "start_char")
	end, okE := formInt(r, "end_char")
	current, err := currentText(path)
	if err != nil {
		serverError(w, r, err)
		return
	}
	conflict := pathURL("edit", slug) + "?conflict=1"
	if fileHash != "" && okS && okE {
		merged, _, _, _, ok := spliceSection(current, fileHash, start, end, content)
		if !ok {
			http.Redirect(w, r, conflict, http.StatusFound)
			return
		}
		content = merged
	} else if fileHash != "" && current != nil && contentHash(*current) != fileHash {
		http.Redirect(w, r, conflict, http.StatusFound)
		return
	}
	// Unchanged: no write, but still a commit when the file is dirty.
	if current == nil || *current != content {
		if err := writeText(path, content); err != nil {
			serverError(w, r, err)
			return
		}
	}
	gitCommit("Update "+slug, path)
	http.Redirect(w, r, pathURL("page", slug), http.StatusFound)
}

// apiSave: the editor's auto-save (no commit): a whole page, a section
// (guarded by the file's hash), or force_section (spliced by index).
func apiSave(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	path := safePath(slug)
	if path == "" {
		badRequestPage(w)
		return
	}
	raw := postedContent(r)
	fileHash := r.PostFormValue("file_hash")
	start, okS := formInt(r, "start_char")
	end, okE := formInt(r, "end_char")
	force, okF := formInt(r, "force_section")
	isSection := fileHash != "" && okS && okE
	isForce := okF && force >= 1
	current, err := currentText(path)
	if err != nil {
		serverError(w, r, err)
		return
	}
	var content, newHash string
	var newStart, newEnd int
	switch {
	case isForce:
		// Spliced by its index in the file as it is now, without the hash guard.
		if current == nil {
			http.NotFound(w, r)
			return
		}
		s, e, ok := sectionCharRange(*current, force)
		if !ok {
			badRequestPage(w)
			return
		}
		content, newHash, newStart, newEnd, _ = spliceSection(current, contentHash(*current), s, e, raw)
	case isSection:
		var ok bool
		if content, newHash, newStart, newEnd, ok = spliceSection(current, fileHash, start, end, raw); !ok {
			writeJSON(w, http.StatusConflict, M{"error": "file_changed"})
			return
		}
	default:
		// The whole page: a hash checks for a conflict.
		if fileHash != "" && current != nil && contentHash(*current) != fileHash {
			writeJSON(w, http.StatusConflict, M{"error": "file_changed", "file_hash": contentHash(*current)})
			return
		}
		content = raw
	}
	if current == nil || *current != content {
		if err := writeText(path, content); err != nil {
			serverError(w, r, err)
			return
		}
	}
	switch {
	case isSection || isForce:
		writeJSON(w, http.StatusOK, M{"file_hash": newHash, "start_char": newStart, "end_char": newEnd})
	case fileHash != "":
		writeJSON(w, http.StatusOK, M{"file_hash": contentHash(content)})
	default:
		w.WriteHeader(http.StatusNoContent)
	}
}

func createPage(w http.ResponseWriter, r *http.Request) {
	raw := r.PostFormValue("slug")
	slug := toSlug(raw)
	path := ""
	if slug != "" {
		path = safePath(slug)
	}
	if path == "" {
		badRequestPage(w)
		return
	}
	if !exists(path) {
		if err := writeText(path, "# "+pyTitle(pyStrip(raw))+"\n\n"); err != nil {
			serverError(w, r, err)
			return
		}
		gitCommit("Create "+slug, path)
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
	if oldPath == "" || !exists(oldPath) {
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
	newSlug := toSlug(pyStrip(r.PostFormValue("new_name")))
	if newSlug == "" || newSlug == slug {
		http.Redirect(w, r, back, http.StatusFound)
		return
	}
	newPath := safePath(newSlug)
	if newPath == "" {
		badRequestPage(w)
		return
	}
	if exists(newPath) {
		http.Redirect(w, r, back+"?rename_error=A+page+named+%22"+newSlug+"%22+already+exists", http.StatusFound)
		return
	}
	if _, err := movePage(oldPath, newPath, slug, newSlug, "Rename "+slug+" to "+newSlug); err != nil {
		serverError(w, r, err)
		return
	}
	http.Redirect(w, r, pathURL("page", newSlug), http.StatusFound)
}

// promotePage moves a scratch note into the wiki (the Move to wiki dialog),
// links to it following, with its own commit message.
func promotePage(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	if !isScratch(slug) {
		badRequestPage(w)
		return
	}
	oldPath := safePath(slug)
	if oldPath == "" || !exists(oldPath) {
		http.NotFound(w, r)
		return
	}
	back := pathURL("page", slug) + "?promote_error="
	rawLink := pyStrip(r.PostFormValue("link_from"))
	linkSlug, linkPath := "", ""
	if rawLink != "" {
		linkSlug = pageFromInput(rawLink)
		if linkSlug != "" {
			linkPath = safePath(linkSlug)
		}
		if linkPath == "" || !exists(linkPath) || linkSlug == slug {
			http.Redirect(w, r, back+quote("There is no page “"+rawLink+"” to link from"), http.StatusFound)
			return
		}
	}
	newSlug, err := moveScratch(slug, r.PostFormValue("folder"), r.PostFormValue("name"), r.PostFormValue("keep_date") != "", "")
	var se *scratchError
	if errors.As(err, &se) {
		http.Redirect(w, r, back+quote(se.msg), http.StatusFound)
		return
	} else if err != nil {
		serverError(w, r, err)
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
	slug := r.PathValue("slug")
	if !isScratch(slug) {
		badRequestPage(w)
		return
	}
	oldPath := safePath(slug)
	if oldPath == "" || !exists(oldPath) {
		http.NotFound(w, r)
		return
	}
	back := pathURL("page", slug) + "?append_error="
	raw := pyStrip(r.PostFormValue("page"))
	target := pageFromInput(raw)
	// The error names the page as typed.
	targetPath := ""
	if target != "" {
		targetPath = safePath(target)
	}
	if raw != "" && (targetPath == "" || !exists(targetPath)) {
		http.Redirect(w, r, back+quote("There is no page “"+raw+"”"), http.StatusFound)
		return
	}
	anchor, err := appendScratch(slug, target, r.PostFormValue("heading"), "")
	var se *scratchError
	if errors.As(err, &se) {
		http.Redirect(w, r, back+quote(se.msg), http.StatusFound)
		return
	} else if err != nil {
		serverError(w, r, err)
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
	if path == "" || !exists(path) {
		http.NotFound(w, r)
		return
	}
	if err := os.Remove(path); err != nil {
		serverError(w, r, err)
		return
	}
	gitCommit("Delete "+slug, path)
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
	counts := allBacklinkCounts()
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
		pages = append(pages, M{"slug": p.Slug, "title": title, "links": counts[p.Slug],
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
	params, title := DeckParams{}, "Deck"
	var home *DeckHome
	crumbs := []crumb{{"Deck", ""}}
	if list != "" {
		if declared, ok := deckLists()[list]; ok {
			params = declared.Params
			t, _ := pageMeta(safePath(declared.Slug), declared.Slug)
			home = &DeckHome{Slug: declared.Slug, Title: t}
		}
		params = params.With("list", list)
		title = list + " · Deck"
		crumbs = []crumb{{"Deck", "/deck"}, {list, ""}}
	}
	// The breadcrumb's key names the view for the navigation trail; no page has it.
	key := "/deck"
	if list != "" {
		key += "?list=" + list
	}
	heading := "Deck"
	if list != "" {
		heading = list
	}
	data := M{"heading": heading, "list_name": list, "deck_html": htmlOf(buildDeckEmbed(params, 0, false)),
		"breadcrumb_html": breadcrumb(rq, crumbs, key, true, title, "")}
	if home != nil {
		data["home"] = home
	}
	page(w, rq, http.StatusOK, "deck_page.html", title, shell{}, data)
}

func apiToggleCheckbox(w http.ResponseWriter, r *http.Request) {
	path := safePath(r.PathValue("slug"))
	if path == "" || !exists(path) {
		http.NotFound(w, r)
		return
	}
	idx, ok := formInt(r, "idx")
	if !ok || idx < 0 {
		badRequestPage(w)
		return
	}
	content, err := readText(path)
	if err == nil {
		err = writeText(path, toggleNthCheckbox(content, idx, true))
	}
	if err != nil {
		serverError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
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
		v := data.Get(k.key)
		if truthyValue(v) {
			l, ok := v.([]any)
			if !ok {
				badRequestPage(w)
				return
			}
			for _, t := range l {
				s, ok := t.(string)
				if !ok {
					badRequestPage(w)
					return
				}
				texts = append(texts, s)
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
	q := pyStrip(args.Get("q"))
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
	q := pyStrip(r.URL.Query().Get("q"))
	unlinked := r.URL.Query().Get("unlinked") == "1"
	var results []M
	if q != "" {
		results = doSearch(rq, q, unlinked, searchPageLimit, false)
	}
	page(w, rq, http.StatusOK, "search.html", "Search: "+q, shell{}, M{"q": q, "results": results,
		"unlinked": unlinked, "limit": searchPageLimit})
}
