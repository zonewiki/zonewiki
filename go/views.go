// Zone's views: the page shell every view shares, the
// breadcrumb and module switcher, menus and dialogs, side columns, around
// each view's template (templates/, html/template, auto-escaped). A value
// that is HTML Zone built itself is a template.HTML; nothing else is.
package main

import (
	"bytes"
	"embed"
	"html"
	"html/template"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
	"zonewiki/internal/mdtext"
)

//go:embed templates
var templateFS embed.FS

// M is a template's data.
type M = map[string]any

var funcs = template.FuncMap{
	"url": pathURL,
	"dict": func(kv ...any) M {
		m := M{}
		for i := 0; i+1 < len(kv); i += 2 {
			m[kv[i].(string)] = kv[i+1]
		}
		return m
	},
	"add": func(a, b int) int { return a + b },
	// text escapes only what HTML needs: html/template also writes "+" as
	// &#43;, which reads the same but isn't the text a test looks for.
	"text":       func(s string) template.HTML { return template.HTML(html.EscapeString(s)) },
	"runePrefix": runePrefix,
	// initial: a name's first letter, upper case (the user button's face).
	"initial": func(s string) string {
		if s == "" {
			return ""
		}
		r, _ := utf8.DecodeRuneInString(s)
		return strings.ToUpper(string(r))
	},
	"lowerFirst": func(s string) string {
		r, n := utf8.DecodeRuneInString(s)
		return strings.ToLower(string(r)) + s[n:]
	},
}

// partials: the layout and partials, whose named templates Go code renders
// too (the breadcrumb, the task list embed, macros); views: each view's
// template with them.
var (
	partials *template.Template
	views    = map[string]*template.Template{}
)

func init() {
	partials = template.Must(template.New("").Funcs(funcs).ParseFS(templateFS,
		"templates/layout.html", "templates/partials/*.html"))
	for _, name := range []string{"login.html", "view.html", "edit.html", "new.html", "notes.html",
		"hidden.html", "deleted_page.html", "tasks_page.html", "search.html", "history/deleted.html",
		"history/diff.html", "history/diff_uncommitted.html", "history/history.html",
		"history/revision.html", "history/uncommitted.html", "users.html", "welcome.html", "signup.html", "assistant.html",
		"password.html",
		"setup.html"} {
		views[name] = template.Must(template.Must(partials.Clone()).ParseFS(templateFS, "templates/"+name))
	}
}

// macro renders a named template of the partials to HTML.
func macro(name string, data any) template.HTML {
	var b bytes.Buffer
	if err := partials.ExecuteTemplate(&b, name, data); err != nil {
		logf("rendering %s: %v", name, err)
	}
	return template.HTML(b.String())
}

// diffLine is a line of a unified diff with its class.
type diffLine struct{ Class, Text string }

// diffLines: a unified diff as classed lines: git's file headers (before a
// file's first @@) are left out; a hunk header is a quiet "⋯".
func diffLines(diff string) []diffLine {
	var lines []diffLine
	inHeader := false
	for _, l := range mdtext.SplitLines(diff) {
		line := mdtext.LineBody(l)
		if strings.HasPrefix(line, "diff --git ") {
			inHeader = true
		}
		switch {
		case strings.HasPrefix(line, "@@"):
			inHeader = false
			lines = append(lines, diffLine{"diff-hunk", "⋯"})
		case inHeader:
		case strings.HasPrefix(line, "+"):
			lines = append(lines, diffLine{"diff-add", line})
		case strings.HasPrefix(line, "-"):
			lines = append(lines, diffLine{"diff-del", line})
		default:
			lines = append(lines, diffLine{"diff-ctx", line})
		}
	}
	return lines
}

// Zone's modules: the wiki, the tasks, with users your Private home, and the
// notes. Every view names the one it belongs to (<body data-module>:
// "private" for a page in your private folder); the matching links are
// aria-current, and js/actions.js makes "New" create that module's kind.
// With users the switcher has two groups by kind, the pages (Wiki, Private)
// and your tools (Tasks, Notes); Private and Notes carry a lock, as only you
// see them. Without users one group.
var modules = [][3]string{{"wiki", "Wiki", "/"}, {"private", "Private", "/private"}, {"tasks", "Tasks", "/tasks"}, {"notes", "Notes", "/notes"}}

// taskCountsTitle: the Tasks links' tooltip, "Tasks: 5 in Focus · 1 overdue
// · 1 due today" (js/chrome.js taskCountsTitle builds the same).
func taskCountsTitle(c TaskCounts) string {
	var parts []string
	if c.Focus > 0 {
		parts = append(parts, itoa(c.Focus)+" in Focus")
	}
	if c.Overdue > 0 {
		parts = append(parts, itoa(c.Overdue)+" overdue")
	}
	if c.Today > 0 {
		parts = append(parts, itoa(c.Today)+" due today")
	}
	if len(parts) == 0 {
		return "Tasks"
	}
	return "Tasks: " + strings.Join(parts, " · ")
}

// taskCounts once per request, with its tooltip.
func (rq *Req) taskCounts() TaskCounts {
	if rq.counts == nil {
		c := taskCounts(rq)
		c.Title = taskCountsTitle(c)
		rq.counts = &c
	}
	return *rq.counts
}

// tasksLinkHref: where the Tasks links go: the pinned lists, as the list
// switcher opens them: several as one (/tasks?pinned=1), one at
// /tasks?list=, a single pin without a list its page; none pinned, /tasks.
func (rq *Req) tasksLinkHref() string {
	pinned := rq.pinnedLists()
	switch {
	case len(pinned) > 1:
		return "/tasks?pinned=1"
	case len(pinned) == 0:
		return "/tasks"
	}
	if l := cleanList(pinned[0].Params.Get("list")); l != "" {
		return "/tasks?list=" + url.QueryEscape(l)
	}
	return pathURL("page", pinned[0].Slug)
}

// moduleLinks: the module links (where each goes, whether it is the current
// view's, the Tasks's counts, the group it is shown in, whether only you see
// it). With users Private follows Wiki, Tasks and Notes are a group of their
// own, and the list ends with the user menu ("menu": who is signed in, Sign
// out; the desktop's user button).
// moduleLink: a module's link in the breadcrumb's switcher and the phone's
// tab bar. Counts: the Tasks link's (nil for the others); PinnedPages: the
// pinned lists' pages, space-separated; Own: only the user sees what is
// behind it (Private, Notes with users); Menu: not a link but the Account
// entry that stands for the user menu (with users).
type moduleLink struct {
	Key, Label, Href string
	Current          bool
	Counts           *TaskCounts
	Group            int
	Own              bool
	PinnedPages      string
	Menu             bool
}

func moduleLinks(rq *Req) []moduleLink {
	current := currentModule(rq)
	users := privateFolder(rq.who()) != ""
	viewer := settings().isViewer(rq.who())
	var out []moduleLink
	for _, m := range modules {
		if m[0] == "private" && !users || viewer && (m[0] == "private" || m[0] == "notes") {
			continue // a viewer makes nothing: no private pages, no notes
		}
		group := 0
		if users && (m[0] == "tasks" || m[0] == "notes") {
			group = 1
		}
		href := m[2]
		if m[0] == "tasks" {
			href = rq.tasksLinkHref()
		}
		own := users && (m[0] == "private" || m[0] == "notes")
		link := moduleLink{Key: m[0], Label: m[1], Href: href, Current: current == m[0], Group: group, Own: own}
		if m[0] == "tasks" {
			counts := rq.taskCounts()
			link.Counts = &counts
			// The pinned lists' pages, no stops in the navigation trail (js/chrome.js).
			var slugs []string
			for _, h := range rq.pinnedLists() {
				slugs = append(slugs, h.Slug)
			}
			link.PinnedPages = strings.Join(slugs, " ")
		}
		out = append(out, link)
	}
	if users {
		out = append(out, moduleLink{Key: "menu", Label: "Account", Menu: true})
	}
	return out
}

var moduleViewRE = regexp.MustCompile(`^/(?:page|history)/(.+)$`)

// currentModule: the module the request's view belongs to: "tasks",
// "notes", "private" (a page in your own private folder), "wiki", or ""
// for views of none (search results, login).
func currentModule(rq *Req) string {
	if rq == nil || rq.r == nil {
		return ""
	}
	p := rq.r.URL.Path
	switch p {
	case "/tasks":
		return "tasks"
	case "/notes":
		return "notes"
	case "/search", "/login", "/assistant", "/password":
		return ""
	}
	if p == "/admin" || strings.HasPrefix(p, "/admin/") { // in no module
		return ""
	}
	m := moduleViewRE.FindStringSubmatch(p)
	if m != nil && isQuicknote(m[1]) {
		return "notes"
	}
	if strings.HasPrefix(p, "/page/") && m != nil && rq.isPinnedPage(m[1]) {
		return "tasks"
	}
	if m != nil && ownPrivate(rq.who(), m[1]) {
		return "private"
	}
	return "wiki"
}

// crumb is a breadcrumb's step: a label and, unless it is where you are, a link.
type crumb struct{ Label, Href string }

// breadcrumbView: what the breadcrumb template shows.
type breadcrumbView struct {
	Crumbs      []crumb
	Key, Title  string // the view's name for the navigation trail
	HasKey      bool   // false: no trail stop
	RightHTML   template.HTML
	ModuleLinks []moduleLink
	AppName     string
}

// breadcrumb: the module switcher, then crumbs. key and title name the
// view for the navigation trail (js/chrome.js); hasKey false: no trail stop.
func breadcrumb(rq *Req, crumbs []crumb, key string, hasKey bool, title string, right template.HTML) template.HTML {
	return macro("breadcrumb", breadcrumbView{Crumbs: crumbs, Key: key, HasKey: hasKey, Title: title,
		RightHTML: right, ModuleLinks: moduleLinks(rq), AppName: appName})
}

// buildBreadcrumb: a page's breadcrumb: its folders, the page, and extra
// crumbs after it. The page is a link when more follows or with a query
// (added to it, e.g. "?tag=task").
func buildBreadcrumb(rq *Req, slug, title string, right template.HTML, query string, extra ...crumb) template.HTML {
	parts := strings.Split(slug, "/")
	var crumbs []crumb
	for i, part := range parts[:len(parts)-1] {
		crumbs = append(crumbs, crumb{mdtext.TitleWords(strings.ReplaceAll(part, "-", " ")), pathURL("page", strings.Join(parts[:i+1], "/"))})
	}
	href := ""
	if len(extra) > 0 || query != "" {
		href = pathURL("page", slug) + query
	}
	crumbs = append(crumbs, crumb{title, href})
	return breadcrumb(rq, append(crumbs, extra...), slug, true, title, right)
}

// pageShell: what every page's layout shows (templates/layout.html and the
// partials it calls with it: shortcuts, top_actions, action_bar), the same
// for every view. Templates reach it as .Shell.
type pageShell struct {
	Title              string // the window's title, and a view's heading
	Editor, Wide       bool   // the editor's page; a page view's side columns
	ShowActions        bool   // new note, new task, search (not on the login page, nor in the editor)
	BadgeInForm        bool   // the remote badge in the view's form (the login page), not the corner
	Module             string // the module the page is in (currentModule); "" in the editor
	TasksNew           *tasksNew
	ModuleLinks        []moduleLink // the module switcher's and the action bar's links (moduleLinks)
	Sharing, Users     bool
	Semantic           string // semanticStatus
	AppName, Version   string
	Build, SourceURL   string
	Commit             string // the build's commit, "" when unknown
	Dirty, AllowRemote bool
	Locale, DateFormat string
	SignOut            bool   // signed in, so Sign out is offered
	User               string // who is signed in, with users
	Admin              bool   // they are an admin: the user menu offers Admin
	Viewer             bool   // they are a viewer: nothing to make (no New note, New task)
}

// tasksNew: how a new task from any page (c t, New task) starts, as the
// default list makes them.
type tasksNew struct {
	Tags                 string
	Keep, Today, Private bool
}

// view: what every view's data embeds, its fields the template's; page
// sets its Shell (.Shell in the templates).
type view struct{ Shell *pageShell }

func (v *view) setShell(s *pageShell) { v.Shell = s }

// viewData: a view's data, a struct embedding view (nil: a view that
// shows only the shell).
type viewData interface{ setShell(*pageShell) }

// page renders a view (its template) in the page shell. noActions drops
// the action buttons, new note and search (the login page has nothing to
// search or write); the editor has its own toolbar instead. wide: a page
// view with side columns on big screens.
func page(w http.ResponseWriter, rq *Req, status int, name, title string, o shell, data viewData) {
	s := settings()
	sh := &pageShell{
		Title: title, Editor: o.editor, Wide: o.wide, ShowActions: !o.noActions && !o.editor,
		BadgeInForm: o.badgeInForm, Sharing: rq.sharing, Semantic: semanticStatus(),
		AppName: appName, Version: version, Build: cfg.Build, SourceURL: sourceURL, AllowRemote: cfg.AllowRemote,
		Locale: s.Locale, DateFormat: s.DateFormat,
		// Only signed in: the login page has no one to sign out.
		SignOut: s.authOn() && isSignedIn(rq.r), User: rq.user, Users: s.Users != nil, Admin: s.isAdmin(rq.user),
		Viewer: s.isViewer(rq.user),
	}
	if !o.editor {
		sh.Module = currentModule(rq)
	}
	if !o.noActions {
		if def := rq.defaultList(); def != nil {
			// Its "+ New" on Focus (its view, or the default) starts the task
			// today; elsewhere a new task has no start date.
			v := embedView(def.Params)
			sh.TasksNew = &tasksNew{Tags: strings.Join(newTaskTags(def.Params), ","),
				Keep: isTrue(def.Params.Get("keep")), Today: v == "" || v == "focus", Private: rq.defaultPrivate()}
		}
	}
	if sh.ShowActions {
		sh.ModuleLinks = moduleLinks(rq)
	}
	// The build's commit, shown in the corner: "abc1234 2026-09-26 12:00 +dirty".
	if fields := strings.Fields(cfg.Build); cfg.Build != "unknown" && len(fields) > 0 {
		sh.Commit, sh.Dirty = fields[0], strings.Contains(cfg.Build, "+dirty")
	}
	if data == nil {
		data = &view{}
	}
	data.setShell(sh)
	var buf bytes.Buffer
	if err := views[name].ExecuteTemplate(&buf, "layout", data); err != nil {
		logf("rendering %s: %v", name, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(buf.Bytes())
}

// shell: how a view sits in the page shell.
type shell struct{ editor, noActions, wide, badgeInForm bool }

// sharingCookie: set to 1 by a browser in sharing mode (js/actions.js).
const sharingCookie = "zone-sharing"

// sharingOn: the request comes from a browser in sharing mode.
func sharingOn(r *http.Request) bool {
	if r == nil {
		return false
	}
	c, err := r.Cookie(sharingCookie)
	return err == nil && c.Value == "1"
}

func isSignedIn(r *http.Request) bool {
	if r == nil {
		return false
	}
	_, ok, _ := signedIn(r)
	return ok
}

// hiddenPageView: what a page not for sharing shows in sharing mode:
// neither its title nor its text.
func hiddenPageView(w http.ResponseWriter, rq *Req) {
	page(w, rq, http.StatusOK, "hidden.html", "Hidden page", shell{}, &hiddenView{
		BreadcrumbHTML: breadcrumb(rq, []crumb{{"Hidden page", ""}}, "", false, "", "")})
}

// hiddenView: a page not for sharing, in sharing mode (hidden.html).
type hiddenView struct {
	view
	BreadcrumbHTML template.HTML
}

// pageLinks: pages as links.
func pageLinks(pages []PageRef) []pageLink {
	out := []pageLink{}
	for _, p := range pages {
		out = append(out, pageLink{Href: pathURL("page", p.Slug), Title: p.Title})
	}
	return out
}

// pageLink: a link in a page's link sections: to a page, or (Linked tasks)
// to a task, with the section of the page it links to (Section as written,
// Anchor its heading's id).
type pageLink struct{ Href, Title, Section, Anchor string }

// linkSection: Backlinks, Linked tasks or Related: a heading over links
// (the link_section template).
type linkSection struct {
	ID, Heading string
	Links       []pageLink
}

// linkSections: the sections at a page's end (and in the right column):
// Backlinks and Linked tasks. prefer: the lists the page declares.
func linkSections(rq *Req, backlinks []PageRef, tasks []TaskLink, prefer []string) []linkSection {
	var sections []linkSection
	if len(backlinks) > 0 {
		sections = append(sections, linkSection{ID: "backlinks", Heading: "Backlinks", Links: pageLinks(backlinks)})
	}
	if len(tasks) > 0 {
		lists := rq.taskLists()
		var links []pageLink
		for _, t := range tasks {
			links = append(links, pageLink{Href: taskHref(t.ID, "open", linkList(taskLinkObj(t), prefer, lists), ""),
				Title: mdtext.PlainTaskTitle(t.Title), Section: t.Section, Anchor: t.Anchor})
		}
		sections = append(sections, linkSection{ID: "task-backlinks", Heading: "Linked tasks", Links: links})
	}
	return sections
}

// Big screens show a page in three columns: the table of contents on the
// left, the page, and what links to it on the right.
var tocHeadingRE = regexp.MustCompile(`<h([23]) id="([^"]+)">`)

var anchorTagRE = regexp.MustCompile(`</?a\b[^>]*>`)

const railTocMinHeadings = 3

// railTocItems: the left column's "On this page": the page's ## and ###
// headings when there are at least three ("" otherwise; the column stays).
func railTocItems(article string) template.HTML {
	var items []string
	for i := 0; i < len(article); {
		m := tocHeadingRE.FindStringSubmatchIndex(article[i:])
		if m == nil {
			break
		}
		level, id := article[i+m[2]:i+m[3]], article[i+m[4]:i+m[5]]
		start := i + m[1]
		end := strings.Index(article[start:], "</h"+level+">")
		if end < 0 {
			break
		}
		label := article[start : start+end]
		i = start + end + len("</h1>")
		// A heading's own links are flattened: no link inside a link.
		cls := ""
		if level == "3" {
			cls = ` class="toc-h3"`
		}
		items = append(items, `<li`+cls+`><a href="#`+id+`">`+strings.TrimSpace(anchorTagRE.ReplaceAllString(label, ""))+`</a></li>`)
	}
	if len(items) < railTocMinHeadings {
		return ""
	}
	return template.HTML("<ul>\n" + strings.Join(items, "\n") + "\n</ul>")
}

// outlinks: the right column's "Links to": the existing pages this page
// links to, A–Z by title.
func outlinks(rq *Req, slug, path string) []pageLink {
	_, targets := pageMeta(path, slug)
	var pages []PageRef
	for t := range targets {
		if t == slug || hiddenPage(rq, t) {
			continue
		}
		if p := safePath(t); p != "" && exists(p) {
			title, _ := pageMeta(p, t)
			pages = append(pages, PageRef{t, title})
		}
	}
	sort.SliceStable(pages, func(i, j int) bool {
		a, b := strings.ToLower(pages[i].Title), strings.ToLower(pages[j].Title)
		if a != b {
			return a < b
		}
		return pages[i].Slug < pages[j].Slug
	})
	return pageLinks(pages)
}

// pasteHint: the editor's hint after Move to wiki (?paste=<slug>): the
// moved page's link, copied (copied) or to copy.
// pasteHintView: the editor's hint after Move to wiki: the moved page's
// link, copied already or to copy.
type pasteHintView struct {
	Link   string
	Copied bool
}

func pasteHint(rq *Req, slug string, copied bool) *pasteHintView {
	if slug == "" {
		return nil
	}
	p := safePath(slug)
	if p == "" || !exists(p) || hiddenPage(rq, slug) {
		return nil
	}
	text, _ := readText(p)
	return &pasteHintView{Link: mdtext.PageWikilink(slug, mdtext.PageTitle(text, slug)), Copied: copied}
}

// buildDeleteDialog: the page view's delete confirmation; none for the home page.
func buildDeleteDialog(slug, title string) template.HTML {
	if isProtected(slug) {
		return ""
	}
	return macro("delete_dialog", struct {
		Slug, Title string
		Git         bool
	}{slug, title, cfg.GitEnabled})
}

// pageMenu: what the page view's ⋯ menu and its dialogs need.
// pageMenuView: the page view's ⋯ menu and the dialogs behind it
// (page_menu). The fields after SharedTarget are a note's: Move to wiki and
// Append to page.
type pageMenuView struct {
	Slug, Title             string
	Git                     bool // History
	Quicknote               bool // a note: Move to wiki…, Append to page…
	Renamable               bool // Rename…, Delete…
	RenameError, RenameName string
	SharedTarget            string // Make shared…: where the private page would go ("": not offered)
	Date                    string // the note's date, to keep in front of its name
	Folders                 []string
	PromoteError            string
	AppendError             string
	AppendHeading           string
	WikiPages               []PageRef // the pages a note can be linked from or appended to
}

func pageMenu(rq *Req, slug, title, renameError, promoteError, appendError, renameName string) *pageMenuView {
	m := &pageMenuView{Slug: slug, Title: title, Git: cfg.GitEnabled, Quicknote: isQuicknote(slug),
		Renamable: !isProtected(slug) && canWrite(rq.who(), slug), RenameError: renameError,
		RenameName: renameName}
	if target, ok := sharedMove(rq.who(), slug); ok {
		m.SharedTarget = target
	}
	if isQuicknote(slug) {
		var wiki []PageRef
		for _, p := range rq.pageFiles() {
			if !isQuicknote(p.Slug) && !hiddenPage(rq, p.Slug) {
				t, _ := pageMeta(p.Path, p.Slug)
				wiki = append(wiki, PageRef{p.Slug, t})
			}
		}
		sort.Slice(wiki, func(i, j int) bool {
			if wiki[i].Slug != wiki[j].Slug {
				return wiki[i].Slug < wiki[j].Slug
			}
			return wiki[i].Title < wiki[j].Title
		})
		m.Date = noteDate(slug)
		m.Folders = pageFolders(rq)
		m.PromoteError, m.AppendError = promoteError, appendError
		m.AppendHeading = appendHeading(slug, title)
		m.WikiPages = wiki
	}
	return m
}

// lastChanged: for the page's meta row, when it last changed ({date, rel}),
// and while it has uncommitted changes what its Commit needs. nil, nil
// without git or history.
// metaRowView: the row below a page's title (meta_row): its tags and tldr,
// when it last changed, and Commit while that change is uncommitted.
type metaRowView struct {
	TagsHTML template.HTML
	Tldr     string
	Changed  *changedView
	Commit   *commitView
}

// changedView: when a page last changed; commitView: the file its Commit
// button commits.
type changedView struct{ Date, Rel string }
type commitView struct{ Filepath, Title string }

func lastChanged(slug, path, title string) (*changedView, *commitView) {
	if !cfg.GitEnabled {
		return nil, nil
	}
	filepath := slug + ".md"
	// Single-file status: much cheaper than the whole tree's.
	r := runGit("status", "--porcelain", "--untracked-files=all", "--", filepath)
	if strings.TrimSpace(r.Stdout) != "" {
		if st, err := os.Stat(path); err == nil {
			return &changedView{st.ModTime().Format("2006-01-02"), relativeTime(st.ModTime())},
				&commitView{filepath, title}
		}
	}
	if entries := gitLog(path, 1); len(entries) > 0 {
		return &changedView{entries[0].Date, entries[0].Rel}, nil
	}
	return nil, nil
}

func itoa(n int) string { return strconv.Itoa(n) }
