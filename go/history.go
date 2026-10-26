// Version history and uncommitted changes: a page's
// history, revisions, diffs, restore, deleted pages, committing.
package main

import (
	"net/http"
	"path/filepath"
	"strings"
)

func historyRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /page/_deleted", deletedPagesView)
	mux.HandleFunc("GET /history/{slug...}", historyView)
	mux.HandleFunc("GET /revision/{rest...}", revisionView)
	mux.HandleFunc("GET /diff/{rest...}", diffView)
	mux.HandleFunc("POST /restore/{rest...}", restoreRevision)
	mux.HandleFunc("GET /uncommitted", uncommittedView)
	mux.HandleFunc("GET /diff-uncommitted/{slug...}", diffUncommitted)
	mux.HandleFunc("GET /api/uncommitted", apiUncommitted)
	mux.HandleFunc("POST /api/commit-all", apiCommitAll)
	mux.HandleFunc("POST /api/commit-file", apiCommitFile)
}

// slugAndSha splits "<slug>/<sha>" (Flask's <path:slug>/<sha>).
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
	page(w, rq, http.StatusOK, "history/deleted.html", "Deleted Pages", shell{}, M{"pages": pages,
		"breadcrumb_html": buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "")})
}

func historyView(w http.ResponseWriter, r *http.Request) {
	rq, slug := newReq(r), r.PathValue("slug")
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
		return
	}
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	path := safePath(slug)
	if path == "" {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	pageExists := exists(path)
	title := slugDisplayName(slug)
	if pageExists {
		text, _ := readText(path)
		title = pageTitle(text, slug)
	}
	entries := gitLog(path, 0)
	if len(entries) == 0 && !pageExists {
		http.NotFound(w, r)
		return
	}
	current := isCurrent(path, entries)
	var rows []M
	for i, e := range entries {
		href := pathURL("revision", slug) + "/" + e.Sha
		if current && i == 0 {
			href = pathURL("page", slug)
		}
		rows = append(rows, M{"e": e, "label": versionLabel(e, entries), "current": current && i == 0, "href": href})
	}
	bc := buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "", crumb{title, ""})
	if pageExists {
		bc = buildBreadcrumb(rq, slug, title, "", "", crumb{"History", ""})
	}
	page(w, rq, http.StatusOK, "history/history.html", "History — "+title, shell{}, M{"slug": slug,
		"page_title": title, "page_exists": pageExists, "rows": rows, "uncommitted": pageExists && !current,
		"breadcrumb_html": bc})
}

func revisionView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	slug, sha, ok := slugAndSha(r.PathValue("rest"))
	if !ok {
		http.NotFound(w, r)
		return
	}
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
		return
	}
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	path := safePath(slug)
	if !shaRE.MatchString(sha) || path == "" {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	entries := gitLog(path, 0)
	i := versionIndex(entries, sha)
	content, found := gitShow(sha, versionPath(path, entries, sha))
	restoreSha := sha
	if !found {
		// A deletion commit: the file isn't at this sha; its parent has it.
		if par := runGit("rev-parse", "--verify", sha+"^"); par.Code == 0 {
			restoreSha = pyStrip(par.Stdout)
			content, found = gitShow(restoreSha, versionPath(path, entries, sha))
		}
		if !found {
			http.NotFound(w, r)
			return
		}
	}
	title := pageTitle(content, slug)
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
	page(w, rq, http.StatusOK, "history/revision.html", when+" — "+title, shell{}, M{"slug": slug, "sha": sha,
		"restore_sha": restoreSha, "page_title": title, "page_exists": pageExists, "entries": entries,
		"i": max(i, 0), "has_i": i >= 0, "current": current, "when": when, "back": back,
		"article_html": htmlOf(renderMd(rq, stripH1(content), RenderOpts{})), "breadcrumb_html": bc})
}

func diffView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	slug, sha, ok := slugAndSha(r.PathValue("rest"))
	if !ok {
		http.NotFound(w, r)
		return
	}
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
		return
	}
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	path := safePath(slug)
	if !shaRE.MatchString(sha) || path == "" {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	entries := gitLog(path, 0)
	i := versionIndex(entries, sha)
	paths := []string{versionPath(path, entries, sha)}
	if i >= 0 && i+1 < len(entries) {
		paths = append(paths, filepath.Join(cfg.Data, filepath.FromSlash(entries[i+1].Path)))
	}
	diff, found := gitDiff(sha, paths...)
	if !found {
		http.NotFound(w, r)
		return
	}
	title := slugDisplayName(slug)
	pageExists := exists(path)
	when, what := runePrefix(sha, 7), ""
	if i >= 0 {
		when, what = entries[i].Time, versionLabel(entries[i], entries)
	}
	bc := buildBreadcrumb(rq, "_deleted", "Deleted Pages", "", "", crumb{title, pathURL("history", slug)}, crumb{"Changes", ""})
	if pageExists {
		bc = buildBreadcrumb(rq, slug, title, "", "", crumb{"History", pathURL("history", slug)}, crumb{"Changes", ""})
	}
	page(w, rq, http.StatusOK, "history/diff.html", "Changes "+when+" — "+title, shell{}, M{"slug": slug, "sha": sha,
		"page_title": title, "page_exists": pageExists, "entries": entries, "i": max(i, 0), "has_i": i >= 0,
		"when": when, "what": what, "diff_lines": diffLines(diff), "breadcrumb_html": bc})
}

func restoreRevision(w http.ResponseWriter, r *http.Request) {
	slug, sha, ok := slugAndSha(r.PathValue("rest"))
	if !ok || !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	path := safePath(slug)
	if !shaRE.MatchString(sha) || path == "" {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	content, found := gitShow(sha, versionPath(path, gitLog(path, 0), sha))
	if !found {
		http.NotFound(w, r)
		return
	}
	if err := writeText(path, content); err != nil {
		serverError(w, r, err)
		return
	}
	gitCommit("Restore "+slug+" to "+sha[:7], path)
	http.Redirect(w, r, pathURL("page", slug), http.StatusFound)
}

func uncommittedView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	var files []UncommittedFile
	for _, f := range uncommittedEntries() {
		if f.Slug == "" || !hiddenPage(rq, f.Slug) {
			files = append(files, f)
		}
	}
	page(w, rq, http.StatusOK, "history/uncommitted.html", "Uncommitted Changes", shell{}, M{"files": files,
		"breadcrumb_html": buildBreadcrumb(rq, "uncommitted", "Uncommitted Changes", "", "")})
}

func diffUncommitted(w http.ResponseWriter, r *http.Request) {
	rq, slug := newReq(r), r.PathValue("slug")
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
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
	rel, _ := filepath.Rel(cfg.Data, path)
	rel = filepath.ToSlash(rel)
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
	title := pageTitle(content, slug)
	page(w, rq, http.StatusOK, "history/diff_uncommitted.html", "Diff — "+title, shell{}, M{"page_title": title,
		"filepath": rel, "diff_lines": diffLines(text),
		"breadcrumb_html": buildBreadcrumb(rq, "uncommitted", "Uncommitted Changes", "", "",
			crumb{title, "/uncommitted"}, crumb{"Diff", ""})})
}

func apiUncommitted(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	out := []M{}
	for _, f := range uncommittedEntries() {
		if f.Slug != "" && hiddenPage(rq, f.Slug) {
			continue
		}
		var slug any
		if f.Slug != "" {
			slug = f.Slug
		}
		out = append(out, M{"slug": slug, "title": f.Title, "filepath": f.Filepath})
	}
	writeJSON(w, http.StatusOK, out)
}

// apiCommitAll commits every uncommitted file, one commit each.
func apiCommitAll(w http.ResponseWriter, r *http.Request) {
	if !cfg.GitEnabled {
		writeJSON(w, http.StatusBadRequest, M{"error": "Git not enabled"})
		return
	}
	committed := []string{}
	for _, f := range gitUncommittedFiles() {
		gitCommit(commitMessage(f.Filepath), filepath.Join(cfg.Data, f.Filepath))
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
	fp := ""
	if data != nil {
		fp, _ = data.Get("filepath").(string)
	}
	if fp == "" {
		writeJSON(w, http.StatusBadRequest, M{"error": "No filepath provided"})
		return
	}
	found := false
	for _, f := range gitUncommittedFiles() {
		if f.Filepath == fp {
			found = true
		}
	}
	if !found {
		writeJSON(w, http.StatusBadRequest, M{"error": "File is not uncommitted"})
		return
	}
	gitCommit(commitMessage(fp), filepath.Join(cfg.Data, fp))
	writeJSON(w, http.StatusOK, M{"success": true})
}
