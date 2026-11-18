// Version history and uncommitted changes: a page's
// history, revisions, diffs, restore, deleted pages, committing.
package main

import (
	"html/template"
	"net/http"
	"path/filepath"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
)

func historyRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /page/_deleted", deletedPagesView)
	mux.HandleFunc("GET /changes", changesView)
	mux.HandleFunc("GET /history/{slug...}", historyView)
	mux.HandleFunc("GET /revision/{rest...}", revisionView)
	mux.HandleFunc("GET /diff/{rest...}", diffView)
	mux.HandleFunc("POST /restore/{rest...}", writesPages(restoreRevision))
	mux.HandleFunc("GET /uncommitted", uncommittedView)
	mux.HandleFunc("GET /diff-uncommitted/{slug...}", diffUncommitted)
	mux.HandleFunc("GET /api/uncommitted", apiUncommitted)
	mux.HandleFunc("POST /api/commit-all", writesPages(apiCommitAll))
	mux.HandleFunc("POST /api/commit-file", writesPages(apiCommitFile))
}

// pageLog: a page's versions (gitLog) user may see: not those from when
// it was another user's private page, before a rename.
func pageLog(user, path string) []GitEntry {
	var out []GitEntry
	for _, e := range gitLog(path, 0) {
		if canSeePath(user, e.Path) {
			e.Msg = hideNames(user, e.Msg)
			if user == visitor && e.Author != visitorAuthor {
				e.Author = "" // a public wiki's visitor learns no user's name
			}
			out = append(out, e)
		}
	}
	return out
}

// slugAndSha splits "<slug>/<sha>": a slug may hold "/", the sha is the last part.
func slugAndSha(rest string) (string, string, bool) {
	i := strings.LastIndex(rest, "/")
	if i <= 0 || i == len(rest)-1 {
		return "", "", false
	}
	return rest[:i], rest[i+1:], true
}

func deletedPagesView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	// Sharing mode: a deleted page's tags are gone with it; its folder still counts.
	var pages []DeletedPage
	for _, p := range listDeletedPages() {
		if !hiddenPage(rq, p.Slug) {
			pages = append(pages, p)
		}
	}
	page(w, rq, http.StatusOK, "history/deleted.html", "Deleted Pages", shell{}, &deletedListView{Pages: pages,
		BreadcrumbHTML: buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "")})
}

// deletedListView: the pages deleted in git history (history/deleted.html).
type deletedListView struct {
	view
	Pages          []DeletedPage
	BreadcrumbHTML template.HTML
}

// historyListView: a page's versions (history/history.html).
type historyListView struct {
	view
	Slug, PageTitle         string
	PageExists, Uncommitted bool // Uncommitted: the file differs from its last commit
	Rows                    []historyRow
	BreadcrumbHTML          template.HTML
}

// historyRow: a version in the history, Href its revision (the page itself
// for the current one).
type historyRow struct {
	E       GitEntry
	Label   string
	Current bool
	Href    string
}

// revisionPageView: a page as it was at Sha (history/revision.html).
type revisionPageView struct {
	view
	Slug, Sha, RestoreSha, PageTitle, When string // RestoreSha: Sha's parent for a deletion
	PageExists, Current                    bool
	Entries                                []GitEntry
	I                                      int  // Sha's place in Entries, 0 if HasI is false
	HasI                                   bool // Sha is in the page's log
	Back                                   int  // versions back from the page as it is
	ArticleHTML, BreadcrumbHTML            template.HTML
}

// diffPageView: what a version changed (history/diff.html).
type diffPageView struct {
	view
	Slug, Sha, PageTitle, When, What string
	PageExists                       bool
	Entries                          []GitEntry
	I                                int
	HasI                             bool
	DiffLines                        []diffLine
	BreadcrumbHTML                   template.HTML
}

// uncommittedListView: the files changed since their commit (history/uncommitted.html).
type uncommittedListView struct {
	view
	Files          []UncommittedFile
	BreadcrumbHTML template.HTML
}

// uncommittedDiffView: a file's changes since its commit (history/diff_uncommitted.html).
type uncommittedDiffView struct {
	view
	PageTitle, Filepath string
	DiffLines           []diffLine
	BreadcrumbHTML      template.HTML
}

func historyView(w http.ResponseWriter, r *http.Request) {
	rq, slug := newReq(r), r.PathValue("slug")
	if refusePage(w, r, rq, slug) {
		return
	}
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	path := safePath(slug)
	if path == "" {
		badRequestPage(w)
		return
	}
	pageExists := exists(path)
	title := mdtext.SlugDisplayName(slug)
	if pageExists {
		text, _ := readText(path)
		title = mdtext.PageTitle(text, slug)
	}
	entries := pageLog(rq.user, path)
	if len(entries) == 0 && !pageExists {
		http.NotFound(w, r)
		return
	}
	current := isCurrent(path, entries)
	var rows []historyRow
	for i, e := range entries {
		href := pathURL("revision", slug) + "/" + e.Sha
		if current && i == 0 {
			href = pathURL("page", slug)
		}
		rows = append(rows, historyRow{E: e, Label: versionLabel(e, entries), Current: current && i == 0, Href: href})
	}
	bc := buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "", crumb{title, ""})
	if pageExists {
		bc = buildBreadcrumb(rq, slug, title, "", "", crumb{"History", ""})
	}
	page(w, rq, http.StatusOK, "history/history.html", "History — "+title, shell{}, &historyListView{Slug: slug,
		PageTitle: title, PageExists: pageExists, Rows: rows, Uncommitted: pageExists && !current,
		BreadcrumbHTML: bc})
}

// versionArg: the page and the version a /revision/ or /diff/ URL names,
// and the page's file; answered (404, 400, the hidden page) when there are none.
func versionArg(w http.ResponseWriter, r *http.Request, rq *Req) (slug, sha, path string, ok bool) {
	slug, sha, ok = slugAndSha(r.PathValue("rest"))
	switch {
	case !ok:
		http.NotFound(w, r)
	case refusePage(w, r, rq, slug):
	case !cfg.GitEnabled:
		http.NotFound(w, r)
	default:
		if path = safePath(slug); !shaRE.MatchString(sha) || path == "" {
			badRequestPage(w)
			return "", "", "", false
		}
		return slug, sha, path, true
	}
	return "", "", "", false
}

func revisionView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	slug, sha, path, ok := versionArg(w, r, rq)
	if !ok {
		return
	}
	entries := pageLog(rq.user, path)
	i := versionIndex(entries, sha)
	content, found := gitShow(sha, versionPath(path, entries, sha))
	restoreSha := sha
	if !found {
		// A deletion commit: the file isn't at this sha; its parent has it.
		if par := runGit("rev-parse", "--verify", sha+"^"); par.Code == 0 {
			restoreSha = strings.TrimSpace(par.Stdout)
			content, found = gitShow(restoreSha, versionPath(path, entries, sha))
		}
		if !found {
			http.NotFound(w, r)
			return
		}
	}
	title := mdtext.PageTitle(content, slug)
	pageExists := exists(path)
	current := isCurrent(path, entries)
	when := runePrefix(sha, 7)
	if i >= 0 {
		when = entries[i].Time
	}
	bc := buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "", crumb{title, ""})
	if pageExists {
		bc = buildBreadcrumb(rq, slug, title, "", "", crumb{"History", pathURL("history", slug)}, crumb{when, ""})
	}
	// How many versions back (the newest is 0 while it is the page as it is now).
	back := 0
	if i >= 0 {
		back = i
		if pageExists && !current {
			back = i + 1
		}
	}
	page(w, rq, http.StatusOK, "history/revision.html", when+" — "+title, shell{}, &revisionPageView{Slug: slug, Sha: sha,
		RestoreSha: restoreSha, PageTitle: title, PageExists: pageExists, Entries: entries,
		I: max(i, 0), HasI: i >= 0, Current: current, When: when, Back: back,
		ArticleHTML: htmlOf(renderMd(rq, mdtext.StripH1(content), RenderOpts{})), BreadcrumbHTML: bc})
}

func diffView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	slug, sha, path, ok := versionArg(w, r, rq)
	if !ok {
		return
	}
	entries := pageLog(rq.user, path)
	i := versionIndex(entries, sha)
	diff, found := versionDiff(path, entries, sha)
	if !found {
		http.NotFound(w, r)
		return
	}
	title := mdtext.SlugDisplayName(slug)
	pageExists := exists(path)
	when, what := runePrefix(sha, 7), ""
	if i >= 0 {
		when, what = entries[i].Time, versionLabel(entries[i], entries)
	}
	bc := buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "", crumb{title, pathURL("history", slug)}, crumb{"Changes", ""})
	if pageExists {
		bc = buildBreadcrumb(rq, slug, title, "", "", crumb{"History", pathURL("history", slug)}, crumb{"Changes", ""})
	}
	page(w, rq, http.StatusOK, "history/diff.html", "Changes "+when+" — "+title, shell{}, &diffPageView{Slug: slug, Sha: sha,
		PageTitle: title, PageExists: pageExists, Entries: entries, I: max(i, 0), HasI: i >= 0,
		When: when, What: what, DiffLines: diffLines(diff), BreadcrumbHTML: bc})
}

func restoreRevision(w http.ResponseWriter, r *http.Request) {
	slug, sha, ok := slugAndSha(r.PathValue("rest"))
	if !ok || !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	if !shaRE.MatchString(sha) || safePath(slug) == "" {
		badRequestPage(w)
		return
	}
	// A page that already reads as the version: nothing to do but show it.
	if _, _, err := restorePageOp(userOf(r), slug, sha, ""); err != nil {
		if pe, ok := refusalOf(err); !ok || pe.kind != refusedUnchanged {
			pageFailed(w, r, err)
			return
		}
	}
	http.Redirect(w, r, pathURL("page", slug), http.StatusFound)
}

func uncommittedView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	files := visibleUncommitted(rq, uncommittedEntries())
	page(w, rq, http.StatusOK, "history/uncommitted.html", "Uncommitted Changes", shell{}, &uncommittedListView{Files: files,
		BreadcrumbHTML: buildBreadcrumb(rq, "uncommitted", "Uncommitted Changes", "", "")})
}

func diffUncommitted(w http.ResponseWriter, r *http.Request) {
	rq, slug := newReq(r), r.PathValue("slug")
	if refusePage(w, r, rq, slug) {
		return
	}
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	path := safePath(slug)
	if path == "" || !exists(path) {
		http.NotFound(w, r)
		return
	}
	rel, _ := wikiRel(path)
	var diff gitResult
	if strings.HasPrefix(runGit("status", "--porcelain", "--", rel).Stdout, "??") {
		diff = runGit("diff", "--no-index", "--", "/dev/null", rel)
	} else {
		diff = runGit("diff", "--", rel)
	}
	text := diff.Stdout
	if text == "" {
		text = "(no changes)"
	}
	content, _ := readText(path)
	title := mdtext.PageTitle(content, slug)
	page(w, rq, http.StatusOK, "history/diff_uncommitted.html", "Diff — "+title, shell{}, &uncommittedDiffView{PageTitle: title,
		Filepath: rel, DiffLines: diffLines(text),
		BreadcrumbHTML: buildBreadcrumb(rq, "uncommitted", "Uncommitted Changes", "", "",
			crumb{title, "/uncommitted"}, crumb{"Diff", ""})})
}

func apiUncommitted(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	out := []M{}
	for _, f := range visibleUncommitted(rq, uncommittedEntries()) {
		var slug any
		if f.Slug != "" {
			slug = f.Slug
		}
		out = append(out, M{"slug": slug, "title": f.Title, "filepath": f.Filepath})
	}
	writeJSON(w, http.StatusOK, out)
}

// visibleUncommitted: the uncommitted files the request may see: pages
// as hiddenPage says, other files (uploads, tasks files) as canSeePath.
func visibleUncommitted(rq *Req, files []UncommittedFile) []UncommittedFile {
	var out []UncommittedFile
	for _, f := range files {
		if canSeePath(rq.user, f.Filepath) && (f.Slug == "" || !hiddenPage(rq, f.Slug)) {
			out = append(out, f)
		}
	}
	return out
}

// mayCommit: user may commit the uncommitted file f: a page they may write
// (a commit puts its text in the history under a name, so it is a write:
// not a guide's by an editor, canWrite), any other file they see (uploads,
// task files: canSeePath, which visibleUncommitted asked).
func mayCommit(user string, f UncommittedFile) bool {
	slug, page := strings.CutSuffix(f.Filepath, ".md")
	return !page || safePath(slug) == "" || canWrite(user, slug)
}

// apiCommitAll commits every uncommitted file the user may see and, a page,
// may write (mayCommit), one commit each.
func apiCommitAll(w http.ResponseWriter, r *http.Request) {
	if !cfg.GitEnabled {
		writeJSON(w, http.StatusBadRequest, M{"error": "Git not enabled"})
		return
	}
	committed := []string{}
	for _, f := range visibleUncommitted(&Req{user: userOf(r)}, gitUncommittedFiles()) {
		if !mayCommit(userOf(r), f) {
			continue
		}
		path := filepath.Join(cfg.Data, f.Filepath)
		if err := gitCommit(commitAuthorOf(path, userOf(r)), commitMessage(f.Filepath), path); err != nil {
			writeJSON(w, http.StatusInternalServerError, M{"error": err.Error(), "committed": len(committed), "files": committed})
			return
		}
		committed = append(committed, f.Filepath)
	}
	writeJSON(w, http.StatusOK, M{"committed": len(committed), "files": committed})
}

// apiCommitFile commits one file of the uncommitted list (the message is
// the server's; the client sends only the path).
func apiCommitFile(w http.ResponseWriter, r *http.Request) {
	if !cfg.GitEnabled {
		writeJSON(w, http.StatusBadRequest, M{"error": "Git not enabled"})
		return
	}
	data, _ := jsonObject(r)
	fp, _ := jsonobj.As[string](data["filepath"])
	if fp == "" {
		writeJSON(w, http.StatusBadRequest, M{"error": "No filepath provided"})
		return
	}
	found := false
	for _, f := range visibleUncommitted(&Req{user: userOf(r)}, gitUncommittedFiles()) {
		if f.Filepath == fp {
			if !mayCommit(userOf(r), f) {
				writeJSON(w, http.StatusForbidden, M{"error": readOnlyWhy(userOf(r), strings.TrimSuffix(f.Filepath, ".md"))})
				return
			}
			found = true
		}
	}
	if !found {
		writeJSON(w, http.StatusBadRequest, M{"error": "File is not uncommitted"})
		return
	}
	path := filepath.Join(cfg.Data, fp)
	if err := gitCommit(commitAuthorOf(path, userOf(r)), commitMessage(fp), path); err != nil {
		writeJSON(w, http.StatusInternalServerError, M{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, M{"success": true})
}
