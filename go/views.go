// Zonewiki's views: the page shell every view shares, the
// breadcrumb and module switcher, menus and dialogs, side columns, around
// each view's template (templates/, html/template, auto-escaped). A value
// that is HTML Zonewiki built itself is a template.HTML; nothing else is.
package main

import (
	"bytes"
	"embed"
	"fmt"
	"html"
	"html/template"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
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
		"hidden.html", "deleted_page.html", "tasks_page.html", "search.html", "history/changes.html", "history/deleted.html",
		"history/diff.html", "history/diff_uncommitted.html", "history/history.html",
		"history/revision.html", "history/uncommitted.html", "users.html", "admin_wiki.html", "groups.html", "welcome.html", "signup.html", "assistant.html",
		"password.html", "email.html", "usage.html", "oauth.html", "help.html",
		"setup.html", "import.html"} {
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

// Zonewiki's modules: the wiki, the tasks, with users your Private home, and the
// notes. Every view names the one it belongs to (<body data-module>:
// "private" for a page in your private folder); the matching links are
// aria-current, and js/actions.js makes "New" create that module's kind.
// With users the switcher has two groups by kind, the pages (Wiki, Private)
// and your tools (Tasks, Notes); Private and Notes carry a lock, as only you
// see them. Without users one group.
var modules = [][3]string{{"wiki", "Wiki", "/"}, {"groups", "Groups", "/groups"}, {"private", "Private", "/private"}, {"tasks", "Tasks", "/tasks"}, {"notes", "Notes", "/notes"}}

// taskCountsTitle: the Tasks links' tooltip, "Tasks: 2 updates for you · 5
// for today · 1 overdue · 1 due today" (js/chrome.js taskCountsTitle builds
// the same).
func taskCountsTitle(c TaskCounts) string {
	var parts []string
	if c.New > 0 {
		parts = append(parts, itoa(c.New)+" update"+map[bool]string{true: "", false: "s"}[c.New == 1]+" for you")
	}
	if c.Focus > 0 {
		parts = append(parts, itoa(c.Focus)+" for today")
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

// tasksLinkHref: where the Tasks links go: back to the view of the list
// switcher /tasks showed last in this browser (tasksPlaceCookie), while
// the user still sees it there; else My tasks (/tasks?mine=1). With no
// list in the wiki /tasks, which is the same tasks.
func (rq *Req) tasksLinkHref() string {
	if len(rq.taskLists()) == 0 {
		return "/tasks"
	}
	place := rq.tasksPlace // on /tasks the view it shows, else the cookie's
	if place == "" && rq.r != nil {
		if c, err := rq.r.Cookie(tasksPlaceCookie); err == nil {
			place, _ = url.QueryUnescape(c.Value)
		}
	}
	if href := rq.placeHref(place); href != "" {
		return href
	}
	return mineHref
}

// tasksPlaceCookie: the view /tasks showed last (a key of the list
// switcher: "mine", "inbox", "list:<ref>", "all"), for the Tasks links to
// go back to. A cookie of the browser's session: it holds nothing but the
// key, and placeHref asks again whether the user sees that view.
const tasksPlaceCookie = "zone-tasks-place"

// rememberTasksPlace: /tasks shows place; the Tasks links go there from now.
func rememberTasksPlace(w http.ResponseWriter, r *http.Request, place string) {
	value := url.QueryEscape(place)
	if c, err := r.Cookie(tasksPlaceCookie); err == nil && c.Value == value {
		return
	}
	http.SetCookie(w, &http.Cookie{Name: tasksPlaceCookie, Value: value, Path: "/",
		HttpOnly: true, Secure: isHTTPS(r), SameSite: http.SameSiteLaxMode})
}

// placeHref: where a view of the list switcher is, if the user sees it now
// ("" if not: a list deleted or no longer theirs to see, one left out in
// sharing mode, the Inbox where they have none).
func (rq *Req) placeHref(place string) string {
	switch place {
	case "mine":
		return mineHref
	case "all":
		return "/tasks"
	case inboxRef:
		if rq.hasInbox() {
			return listHref(inboxRef)
		}
		return ""
	}
	if ref, ok := strings.CutPrefix(place, "list:"); ok {
		if l, ok := resolveList(ref, rq.taskLists()); ok && l.Ref == ref && !rq.listHidden(l) {
			return listHref(l.Ref)
		}
	}
	return ""
}

// moduleLinks: the module links (where each goes, whether it is the current
// view's, the Tasks's counts, the group it is shown in, whether only you see
// it). With users Private follows Wiki, Tasks and Notes are a group of their
// own. The list ends with the menu ("menu": the desktop's button at the top
// right; with users who is signed in and Sign out too), in every wiki, for
// everyone but a public wiki's visitor.
// moduleLink: a module's link in the breadcrumb's switcher and the phone's
// tab bar. Counts: the Tasks link's (nil for the others); Own: only the user sees what is
// behind it (Private, Notes with users); Menu: not a link but the entry
// that stands for the menu (the user menu with users).
type moduleLink struct {
	Key, Label, Href string
	Current          bool
	Counts           *TaskCounts
	Group            int
	Own              bool
	Menu             bool
	Tip              string // the Groups link's tooltip: the group, or the groups
	// The Groups link, in the switcher: Name, the one group the user is in,
	// which the pill is called by; Groups, the several they are in, which
	// the pill opens as a menu (the tab bar says Groups and goes to /groups).
	Name   string
	Groups []string
}

func moduleLinks(rq *Req) []moduleLink {
	current := currentModule(rq)
	users := privateFolder(rq.who()) != ""
	viewer := settings().isViewer(rq.who().Name)
	var out []moduleLink
	groups := groupsFor(rq.who()) // the groups the user is in: Groups, for who is in one
	for _, m := range modules {
		if m[0] == "private" && !users || viewer && (m[0] == "private" || m[0] == "notes") {
			continue // a viewer makes nothing: no private pages, no notes
		}
		if m[0] == "groups" && len(groups) == 0 {
			continue
		}
		if rq.who().Name == visitor && m[0] != "wiki" {
			continue // a visitor has the wiki's pages alone
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
		if m[0] == "groups" {
			// One group: its home, under its name. Several: the list of them.
			link.Tip, link.Groups = "Your groups: "+strings.Join(groups, ", "), groups
			if len(groups) == 1 {
				link.Href, link.Tip = "/"+groupsDir+"/"+groups[0], "@"+groups[0]+": "+groupWho(groups[0])+" its pages"
				link.Name, link.Groups = groups[0], nil
			}
		}
		if m[0] == "tasks" {
			counts := rq.taskCounts()
			link.Counts = &counts
		}
		out = append(out, link)
	}
	if rq.who().Name != visitor {
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
	case "/groups":
		return "groups"
	case "/search", "/login", "/assistant", "/password", "/email", "/oauth/authorize":
		return ""
	}
	if p == "/admin" || strings.HasPrefix(p, "/admin/") { // in no module
		return ""
	}
	m := moduleViewRE.FindStringSubmatch(p)
	if rq.who().Name == visitor {
		return "wiki" // a public wiki's visitor has the pages alone
	}
	if m != nil && isQuicknote(m[1]) {
		return "notes"
	}
	if m != nil && ownPrivate(rq.who(), m[1]) {
		return "private"
	}
	if m != nil && placeOf(m[1]).kind == ofGroup && canSee(rq.who(), m[1]) {
		return "groups"
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
//
// A page in a private or a group's folder names its place, not its path:
// "🔒 Private › Diary" and "👥 dev › Roadmap", the first crumb leading to
// the place's home, so such a folder is never taken for one of the wiki's.
func buildBreadcrumb(rq *Req, slug, title string, right template.HTML, query string, extra ...crumb) template.HTML {
	parts := strings.Split(slug, "/")
	var crumbs []crumb
	first := 0
	if rq != nil {
		switch p := placeOf(slug); {
		case p.own(rq.who()) && len(parts) > 2:
			crumbs, first = append(crumbs, crumb{"🔒 Private", "/private"}), 2
		case p.kind == ofGroup && p.sees(rq.who()) && len(parts) > 2:
			crumbs, first = append(crumbs, crumb{"👥 " + p.name, "/" + groupsDir + "/" + p.name}), 2
		}
	}
	for i, part := range parts[:len(parts)-1] {
		if i < first {
			continue
		}
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
	Title              string       // the window's title, and a view's heading
	Editor, Wide       bool         // the editor's page; a page view's side columns
	ShowActions        bool         // new note, new task, search (not on the login page, nor in the editor)
	BadgeInForm        bool         // the remote badge in the view's form (the login page), not the corner
	Module             string       // the module the page is in (currentModule); "" in the editor
	ModuleLinks        []moduleLink // the module switcher's and the action bar's links (moduleLinks)
	Sharing, Users     bool
	Semantic           string // semanticStatus
	AppName, Version   string
	Build, SourceURL   string
	Commit             string // the build's commit, "" when unknown
	Dirty, AllowRemote bool
	Locale, DateFormat string
	SignOut            bool   // signed in, so Sign out is offered
	Notice             string // the notice setting: a line at the top of every page, for everyone
	Imprint, Privacy   string // the imprint and privacy settings' links, for whoever isn't signed in; "" for the signed in and in the editor
	ChangePassword     bool   // signed in, and the password is theirs to change (no shared account's)
	SetLogin           string // the wiki asks for no login: where the menu's Set a login leads (setLoginHref)
	// Settings: the settings pages as tabs (settingsTabs): the menu's
	// Settings opens the first; nil where there is one page alone, which the
	// menu then names. SettingsTab: the one this view is, "" on any other.
	Settings    []settingsTab
	SettingsTab string
	User        string // who is signed in, with users
	Watches     bool   // they can watch pages: the menu has Watched pages
	Watched     int    // how many watched pages have news for them (watchCount)
	Admin       bool   // they are an admin: the user menu offers Admin
	Usage       bool   // they see the wiki's usage (seesUsage): the help dialog links to it
	Viewer      bool   // they are a viewer: nothing to make (no New note, New task)
	// Visitor: nobody is signed in, and the wiki is public (access.go): the
	// wiki's pages alone, no tasks or notes; SignIn: where they sign in, to
	// come back to this view.
	Visitor bool
	SignIn  string
	// Groups: the groups whose places are theirs (groupsFor), for the
	// search modal's Where menu; nil without users or actions.
	Groups []string
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
		SignOut: s.authOn() && isSignedIn(rq.r), ChangePassword: mayChangePassword(rq), SetLogin: setLoginHref(), User: rq.user.Name, Users: s.Users != nil, Admin: s.isAdmin(rq.user.Name),
		Viewer: s.isViewer(rq.user.Name), Notice: s.Notice,
	}
	if !o.editor && !isSignedIn(rq.r) {
		sh.Imprint, sh.Privacy = s.Imprint, s.Privacy
	}
	if rq.user.Name == visitor {
		// No name, nothing of users (private pages, assignees), nothing to
		// make but, where visitors edit, pages.
		sh.User, sh.Users, sh.Viewer, sh.Visitor = "", false, true, true
		sh.SignIn = "/login?next=" + url.QueryEscape(rq.r.URL.Path)
	}
	if sh.Users {
		sh.Groups = groupsFor(rq.user)
	}
	sh.Settings, sh.SettingsTab = settingsTabs(rq), o.settings
	sh.Usage = seesUsage(rq)
	if !o.editor {
		sh.Module = currentModule(rq)
	}
	if sh.ShowActions {
		sh.ModuleLinks = moduleLinks(rq)
		if sh.Watches = mayWatch(rq.user) && cfg.GitEnabled; sh.Watches {
			sh.Watched = rq.watchCount()
		}
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
//
// settings: the view is one of the settings pages, this tab of theirs
// (settingsTabs).
type shell struct {
	editor, noActions, wide, badgeInForm bool
	settings                             string
}

// settingsTab: one of the settings pages, a tab of the row they share.
type settingsTab struct{ Key, Label, Href string }

// mayChangePassword: who asks is signed in with a password that is theirs
// to change in the browser (not a shared account's).
func mayChangePassword(rq *Req) bool {
	s := settings()
	return s.authOn() && isSignedIn(rq.r) && !s.sharedAccount(rq.user.Name)
}

// settingsTabs: the pages about whoever is signed in (with no users, about
// the wiki), which the menu has as one entry, Settings, and which show each
// other as a row of tabs (partials/settings_tabs.html): Connect an
// assistant, Change password where there is one of theirs to change, Mail
// for a user of a wiki that sends mail (email.go), with users Groups, and Usage for whoever sees it (seesUsage: an admin; with no
// users whoever is signed in). Each keeps its own address; the menu's entry
// opens the first. Nil where there is one page alone (a wiki without users
// and without a login: Usage is there by its address, and from the help
// dialog), which the menu names instead, and for a visitor.
func settingsTabs(rq *Req) []settingsTab {
	if rq == nil || rq.r == nil || rq.user.Name == visitor {
		return nil
	}
	tabs := []settingsTab{{"assistant", "Assistant", "/assistant"}}
	if mayChangePassword(rq) {
		tabs = append(tabs, settingsTab{"password", "Password", "/password"})
	}
	if hasMailTab(rq) {
		tabs = append(tabs, settingsTab{"email", "Mail", "/email"})
	}
	if rq.user.Name != "" {
		tabs = append(tabs, settingsTab{"groups", "Groups", "/groups"})
	}
	if seesUsage(rq) && settings().authOn() {
		tabs = append(tabs, settingsTab{"usage", "Usage", "/usage"})
	}
	if len(tabs) < 2 {
		return nil
	}
	return tabs
}

// settingsCrumb: a settings page's breadcrumb: Settings where the pages are
// tabs, the page's own name (own) where it stands alone.
func settingsCrumb(rq *Req, own string) []crumb {
	if settingsTabs(rq) != nil {
		return []crumb{{"Settings", ""}}
	}
	return []crumb{{own, ""}}
}

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
// Backlinks and Linked tasks.
func linkSections(rq *Req, backlinks []PageRef, tasks []TaskLink) []linkSection {
	var sections []linkSection
	if len(backlinks) > 0 {
		sections = append(sections, linkSection{ID: "backlinks", Heading: "Backlinks", Links: pageLinks(backlinks)})
	}
	if len(tasks) > 0 {
		lists := rq.taskLists()
		var links []pageLink
		for _, t := range tasks {
			links = append(links, pageLink{Href: taskHref(t.ID, "open", taskList(taskLinkObj(t), lists), ""),
				Title: mdtext.PlainTaskTitle(t.Title), Section: t.Section, Anchor: t.Anchor})
		}
		sections = append(sections, linkSection{ID: "task-backlinks", Heading: "Linked tasks", Links: links})
	}
	return sections
}

// Big screens show a page in three columns: the table of contents on the
// left, the page, and what links to it on the right.
const railTocMinHeadings = 3

// railToc: the left column's "On this page": the page's ## and ###
// headings as its render parsed them (Env.TOC, tocLabel: their links
// flattened, no link inside a link) when there are at least three (""
// otherwise; the column stays).
func railToc(toc []tocEntry) template.HTML {
	if len(toc) < railTocMinHeadings {
		return ""
	}
	items := make([]string, len(toc))
	for i, e := range toc {
		cls := ""
		if e.Level == 3 {
			cls = ` class="toc-h3"`
		}
		items[i] = `<li` + cls + `><a href="#` + html.EscapeString(e.ID) + `">` + e.Label + `</a></li>`
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

// shownFile: one of a page's Files: the upload's link, its name, size and
// kind (fileKind, its icon).
type shownFile struct{ Href, Name, Size, Kind string }

// fileKinds: a file's kind by its extension, for its icon in Files (the
// icon_file template): pictures, sound and film, documents, spreadsheets,
// archives; any other is a "file". By the extension, not the host's mime
// table, which knows no office types in the container.
var fileKinds = map[string]string{}

func init() {
	for kind, exts := range map[string]string{
		"image":   "png jpg jpeg gif webp avif svg bmp ico tif tiff heic",
		"media":   "mp3 m4a ogg oga opus wav flac aac mp4 m4v webm mov mkv ogv avi",
		"doc":     "pdf doc docx odt rtf txt md pages ppt pptx odp key epub",
		"sheet":   "xls xlsx ods csv tsv numbers",
		"archive": "zip gz tgz tar bz2 xz 7z rar zst",
	} {
		for _, ext := range strings.Fields(exts) {
			fileKinds["."+ext] = kind
		}
	}
}

func fileKind(name string) string {
	if kind := fileKinds[strings.ToLower(filepath.Ext(name))]; kind != "" {
		return kind
	}
	return "file"
}

// pageFiles: a page's Files: the uploads its text links to or shows (links
// and images, the targets its render parsed, in the page's order, each once),
// those the reader may open (uploadFile, the door an assistant's read_file
// asks too: a shared upload, their own private ones, a group's they are in,
// a task's they see). A link to an upload that isn't there, or isn't theirs,
// is left out alike, so the list says nothing of another's files. A link
// with the wiki's address before it counts, one to another site doesn't.
func pageFiles(rq *Req, targets []string) []shownFile {
	var files []shownFile
	seen := map[string]bool{}
	for _, target := range targets {
		if !strings.Contains(target, "_assets/") {
			continue
		}
		u, err := url.Parse(strings.TrimSpace(target))
		if err != nil || u.Scheme != "" && u.Scheme != "http" && u.Scheme != "https" ||
			u.Host != "" && !strings.EqualFold(u.Host, requestHost(rq)) {
			continue
		}
		path, name, ok := uploadFile(u.Path, rq.user)
		if !ok || seen[path] {
			continue
		}
		seen[path] = true
		st, err := os.Stat(path)
		if err != nil {
			continue
		}
		files = append(files, shownFile{Href: pathURL(strings.Split(strings.TrimPrefix(u.Path, "/"), "/")...),
			Name: name, Size: formatSize(st.Size()), Kind: fileKind(name)})
	}
	return files
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
	Git                     bool   // History
	Quicknote               bool   // a note: Move to wiki…, Append to page…
	Renamable               bool   // Rename…, Delete…
	Watches                 bool   // Watch or Unwatch: the reader can watch pages
	Watching                bool   // and watches this one
	WatchedBy               string // the watched folder it is in, "" for none
	RenameError, RenameName string
	RenameAsk               string        // a rename to other readers, asked first: what it would do (the form then confirms RenameName)
	SharedTarget            string        // Make shared…: where the private page would go ("": not offered, or there are several places)
	ShareTargets            []shareTarget // Share…: the places the page can go to, more than one, or from a group's folder
	ShareFrom               string        // the group whose page it is ("": the user's own private page)
	Date                    string        // the note's date, to keep in front of its name
	Folders                 []folderChoice
	PromoteError            string
	AppendError             string
	AppendHeading           string
	WikiPages               []PageRef // the pages a note can be linked from or appended to
}

func pageMenu(rq *Req, slug, title, renameError, promoteError, appendError, renameName, renameAsk string) *pageMenuView {
	// A visitor moves nothing: no Rename…, Delete… or Move to wiki….
	visits := rq.who().Name == visitor
	m := &pageMenuView{Slug: slug, Title: title, Git: cfg.GitEnabled, Quicknote: isQuicknote(slug) && !visits,
		Renamable: !isProtected(slug) && canWrite(rq.who(), slug) && !visits, RenameError: renameError,
		RenameName: renameName, RenameAsk: renameAsk}
	if m.Watches = mayWatch(rq.who()) && cfg.GitEnabled && !isQuicknote(slug); m.Watches {
		w := watchesOf(rq.who().Name)
		_, m.Watching = w.Watch[slug]
		m.WatchedBy = w.folderOf(slug)
	}
	// A private page with nowhere to go but to everyone keeps the plain
	// Make shared…; with groups to go to, or from a group's folder, Share….
	if targets := shareTargets(rq.who(), slug); len(targets) == 1 && targets[0].Group == "" && placeOf(slug).kind == ofUser {
		m.SharedTarget = targets[0].Slug
	} else if len(targets) > 0 {
		m.ShareTargets, m.ShareFrom = targets, placeOf(slug).group()
	}
	if m.Quicknote {
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
		m.Folders = folderChoices(rq)
		m.PromoteError, m.AppendError = promoteError, appendError
		m.AppendHeading = appendHeading(slug, title)
		m.WikiPages = wiki
	}
	return m
}

// lastChanged: for the page's meta row, when it last changed,
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

// changedView: when a page last changed (Rel, "3 days ago", shown; At, its
// day and time, on hover and on paper; ISO, for <time>); commitView: the file
// its Commit button commits.
type changedView struct{ At, ISO, Rel string }
type commitView struct{ Filepath, Title string }

// lastChanged: when a page last changed, and its Commit button while it has
// changes not committed. What git says is kept per page (pageChanges) for as
// long as neither the page's file nor the repository changed, so a view of a
// page seen before starts no git.
func lastChanged(slug, path, title string) (*changedView, *commitView) {
	if !cfg.GitEnabled {
		return nil, nil
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil, nil
	}
	c := changeOf(slug, path, st)
	switch {
	case c.dirty:
		return changedAt(st.ModTime()), &commitView{slug + ".md", title}
	case !c.at.IsZero():
		return changedAt(c.at), nil
	}
	return nil, nil
}

func changedAt(t time.Time) *changedView {
	t = t.In(zone())
	return &changedView{t.Format("2006-01-02 15:04"), t.Format(time.RFC3339), relativeTime(t)}
}

// changeFacts: what git says of a page: it has changes not committed
// (dirty), or its last commit's time; neither for
// a page git doesn't know. stamp: what they were found under.
type changeFacts struct {
	stamp string
	dirty bool
	at    time.Time
}

// pageChanges: the pages' changeFacts by slug. A gone page's entry stays
// until the map is emptied, at changesMax.
var pageChanges = struct {
	sync.Mutex
	pages map[string]changeFacts
}{pages: map[string]changeFacts{}}

const changesMax = 20000

// changeLookups counts the times git was asked (the tests').
var changeLookups atomic.Int64

// changeOf: slug's changeFacts, from pageChanges while its stamp holds.
func changeOf(slug, path string, st os.FileInfo) changeFacts {
	stamp, stamped := changeStamp(st)
	if stamped {
		pageChanges.Lock()
		c, ok := pageChanges.pages[slug]
		pageChanges.Unlock()
		if ok && c.stamp == stamp {
			return c
		}
	}
	changeLookups.Add(1)
	c := changeFacts{stamp: stamp}
	// Single-file status: much cheaper than the whole tree's. Without the
	// optional lock, so that asking doesn't write the index it is stamped by
	// (git refreshes it after a look, for as long as a file is as new as it).
	if r := runGit("--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--", slug+".md"); strings.TrimSpace(r.Stdout) != "" {
		c.dirty = true
	} else if rel, ok := wikiRel(path); ok {
		// The file is as committed under this name, so its latest commit is
		// this name's (no --follow, which walks the whole history: 0.1 s at
		// 200,000 commits, where the commit-graph's filters answer in 5 ms).
		out := strings.TrimSpace(runGit("log", "-n", "1", "--format=%at", "--", rel).Stdout)
		if secs, err := strconv.ParseInt(out, 10, 64); err == nil {
			c.at = time.Unix(secs, 0)
		}
	}
	// The stamp is from before git was asked: a change meanwhile makes it
	// differ at the next view, which asks again.
	if stamped {
		pageChanges.Lock()
		if len(pageChanges.pages) >= changesMax {
			pageChanges.pages = map[string]changeFacts{}
		}
		pageChanges.pages[slug] = c
		pageChanges.Unlock()
	}
	return c
}

// changeStamp: what a page's changeFacts depend on, as a string that differs
// when any of it changed: the page's file (its time and size), and the
// repository's reflog and index, which every commit, checkout, pull, reset,
// add and rm writes, Zonewiki's own and those made in a terminal. Without a
// reflog (a repository that keeps none, or a .git that is a file) nothing is
// kept, and git is asked at every view, as before.
func changeStamp(page os.FileInfo) (string, bool) {
	log, err := os.Stat(filepath.Join(cfg.Data, ".git", "logs", "HEAD"))
	if err != nil {
		return "", false
	}
	stamp := fmt.Sprintf("%d/%d %d/%d", page.ModTime().UnixNano(), page.Size(), log.ModTime().UnixNano(), log.Size())
	if index, err := os.Stat(filepath.Join(cfg.Data, ".git", "index")); err == nil {
		stamp += fmt.Sprintf(" %d/%d", index.ModTime().UnixNano(), index.Size())
	}
	return stamp, true
}

func itoa(n int) string { return strconv.Itoa(n) }
