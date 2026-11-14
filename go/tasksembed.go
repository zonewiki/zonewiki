// The [[tasks:]] embed: its HTML, its settings dialog (an embed's params
// written back into the page, a list's and All tasks' into the lists'
// registry), and the counts the Tasks links and the pinned lists show.
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
)

const tasksPlaceholder = "ZONETASKSPLACEHOLDER"

// tasksEmbedView: what the tasks_embed template shows: a task app as its
// embed's params and the page it stands on say.
type tasksEmbedView struct {
	Tag, View     string
	List          string           // the list it shows, as the user names it (a ref); "" none
	Lists         string           // several lists as one (the pinned lists): their refs, "|" between
	Plan          string           // "on", "off" or "": the embed doesn't say
	Limit         string           // the WIP limit, "" none
	Labels        []string         // the Open and Focus tabs' names
	InstanceID    int              // which embed of the page
	Sfx           string           // "-<InstanceID>" after the first: its elements' ids end in it
	Headless      bool             // hidden: only its edit modal is used (the editor's Make task, a new task from any page)
	Keep          string           // "1": new tasks here are keep tasks
	Pinned        bool             // the user's default list or their pinned lists: its New is the one New task clicks on its page
	NewTags       string           // new tasks' tags, comma-separated
	CanAssign     bool             // with users: the modal has the Assignee field
	Assignee      string           // assignee=, with users: whose tasks it shows
	PrivateFolder string           // the user's private folder: where a private task's pasted files go
	ListsJSON     string           // the lists the user sees, [{ref, private}], as JSON: the List field's choices
	DefaultList   string           // the user's default list (a ref): where a new task goes in a view of no one list
	Settings      *embedSettingsAt // where the settings dialog writes; nil: no dialog (the ⋯ menu)
}

// embedSettingsAt: where a task app's settings are written, for its
// dialog: the Index-th embed of Page as written (Raw; Index < 0: All
// tasks'), and the list it shows (List, a ref; its settings as JSON, and
// whether the user pinned it: Pin "1"), "" for none. A list's own
// settings go to its registry, the embed's (the tab, a filter) to the page.
type embedSettingsAt struct {
	Page         string
	Index        int
	Raw          string
	Embed        bool // an embed on a page, whose params can be written there
	List         string
	ListSettings string
	Pin          string
}

// listEmbedParams: what an embed of the list l shows: the list's own
// settings, and of the embed's params how the page looks at the list: the
// tab it opens on (view) and a filter (tag, assignee). What the list is
// (limit, plan, keep, labels, new) is said on the list alone.
func listEmbedParams(l ListInfo, embed TasksParams) TasksParams {
	p := l.Params
	for _, k := range []string{"view", "tag", "assignee"} {
		if embed.Has(k) {
			p = p.With(k, embed.Get(k))
		}
	}
	p.src = embed.src
	return p
}

// paramsScope: which lists' tasks a view with these params shows: one
// list's ref, several joined by "|" (lists=), or "" for every task.
func paramsScope(p TasksParams) string {
	if s := strings.TrimSpace(p.Get("lists")); s != "" {
		return strings.ToLower(s)
	}
	return cleanList(p.Get("list"))
}

// buildTasksEmbed renders the task app for user on a page (instance numbers
// a page's embeds); headless hides it but for its edit modal (the editor's
// Make task, c t on any page), and tasks.js skips its shortcuts. params:
// what it shows, a list's by its ref as the user names it (listEmbedParams
// for an embed of one). page: the page whose text holds the embed's params
// (params.src), "" for none. It has the settings dialog (the ⋯ menu) where
// something can be written: the page's embed, the list it shows, the
// settings of All tasks; not for a viewer nor in sharing mode.
func buildTasksEmbed(rq *Req, params TasksParams, instance int, headless bool, page string) string {
	user := rq.who()
	lists := rq.taskLists()
	list, shown := resolveList(params.Get("list"), lists)
	var settingsAt *embedSettingsAt
	if !headless && rq != nil && !rq.sharing {
		at := &embedSettingsAt{Index: -1}
		src := params.src
		switch {
		case src != nil && src.index >= 0 && page != "" && canWrite(user, page):
			at.Page, at.Index, at.Raw, at.Embed = page, src.index, src.raw, true
		case src != nil && src.index < 0 && page == "" && mayWriteTasks(user) == nil:
			at.Raw = src.raw
		default:
			at = nil
		}
		if shown && canWriteList(user, list) {
			if at == nil {
				at = &embedSettingsAt{Index: -1}
			}
			set := map[string]string{}
			for _, k := range listSettingKeys {
				if v := list.Params.Get(k); v != "" {
					set[k] = v
				}
			}
			if list.NoShare {
				set["noshare"] = "1"
			}
			at.List, at.ListSettings = list.Ref, jsonobj.Text(set)
			for _, p := range rq.pinnedLists() {
				if p.Ref == list.Ref {
					at.Pin = "1"
				}
			}
		}
		settingsAt = at
	}
	// view= with plan=: the Open tab as plan= says, whichever of its two
	// views view= names.
	view, plan := embedView(params), embedPlan(params)
	switch {
	case plan != "" && (view == "open" || view == "plan"):
		view = map[string]string{"on": "plan", "off": "open"}[plan]
	case view == "open":
		plan = "off" // view=open has always been the one list
	}
	// limit=N: the WIP limit (Focus warning, Plan day flags).
	limit := ""
	if n, err := strconv.Atoi(strings.TrimSpace(params.Get("limit"))); err == nil {
		limit = strconv.Itoa(max(1, min(99, n)))
	}
	// labels=Catalogue,Shopping: the embed's own names for Open and Focus;
	// "_" stands for a space, an empty entry keeps the default. Of three
	// names (from when Plan was a tab) the first and the last count.
	labels := []string{"Open", "Focus"}
	names := strings.Split(params.Get("labels"), ",")
	if len(names) == 3 {
		names = []string{names[0], names[2]}
	}
	for i, name := range names {
		if i >= 2 {
			break
		}
		if name = runePrefix(strings.TrimSpace(strings.ReplaceAll(name, "_", " ")), 30); name != "" {
			labels[i] = name
		}
	}
	type listChoice struct {
		Ref     string `json:"ref"`
		Private bool   `json:"private"`
	}
	choices := []listChoice{}
	for _, ref := range sortedKeys(lists) {
		choices = append(choices, listChoice{ref, lists[ref].Private()})
	}
	def := ""
	if d := rq.defaultList(); d != nil {
		def = d.Ref
	}
	scope := paramsScope(params)
	sfx := ""
	if instance > 0 {
		sfx = "-" + strconv.Itoa(instance)
	}
	keep := ""
	if isTrue(params.Get("keep")) {
		keep = "1"
	}
	v := tasksEmbedView{
		Tag: params.Get("tag"), View: view, Plan: plan, Settings: settingsAt,
		Limit: limit, Labels: labels, InstanceID: instance, Sfx: sfx, Headless: headless,
		Keep: keep, Pinned: def != "" && (scope == def || strings.Contains(scope, "|")),
		NewTags:       strings.Join(newTaskTags(params), ","),
		CanAssign:     settings().Users != nil,
		Assignee:      assigneeParam(params),
		PrivateFolder: privateFolder(user),
		ListsJSON:     jsonobj.Text(choices), DefaultList: def,
	}
	if strings.Contains(scope, "|") {
		v.Lists = scope
	} else if shown {
		v.List = list.Ref
	}
	var b bytes.Buffer
	if err := partials.ExecuteTemplate(&b, "tasks_embed", v); err != nil {
		logf("task list embed: %v", err)
	}
	return b.String()
}

// noListHTML: what stands in the place of an embed whose list=<name> no
// list has (for whoever looks): a line that says so, and for who may make
// lists a button that makes it.
func noListHTML(rq *Req, name string) string {
	var b bytes.Buffer
	err := partials.ExecuteTemplate(&b, "tasks_no_list", map[string]any{
		"Name": name, "Create": rq != nil && !rq.sharing && !listsUnmigrated && mayWriteTasks(rq.who()) == nil})
	if err != nil {
		logf("task list embed: %v", err)
	}
	return b.String()
}

// ---- the settings dialog ----

// allTasksParams: the params of All tasks (/tasks), which no page's
// [[tasks: …]] holds: they are in the shared registry ("all": the params
// an embed would have, allSettingKeys). None when they can't be read.
func allTasksParams() TasksParams {
	set, err := checkEmbedSettings(taskFiles.ReadLists("").All, allSettingKeys)
	if err != nil {
		logf("%s: %v", taskFiles.ListsPath(""), err)
		set = nil
	}
	raw := setEmbedArgs("", set)
	p := parseTasksParams(raw)
	p.src = &embedSrc{-1, raw}
	return p
}

// setAllTasksParams writes the settings of All tasks with those of set
// changed, in a commit by user; was: the params as the dialog saw them.
func setAllTasksParams(user, was string, set map[string]string) error {
	if listsUnmigrated {
		return errListsUnmigrated
	}
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	set, err := checkEmbedSettings(set, allSettingKeys)
	if err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	if allTasksParams().src.raw != was {
		return errEmbedChanged
	}
	reg, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return err
	}
	reg.All = parseTasksParams(setEmbedArgs(was, set)).m
	path, err := taskFiles.WriteListsLocked(user, "", reg)
	if err != nil {
		return err
	}
	return commitLists(user, "Change the settings of All tasks", []string{path})
}

// setEmbedParamsOp changes the params of the index-th [[tasks: …]] of the
// page slug (setEmbedParams), in a commit by user.
func setEmbedParamsOp(user, slug string, index int, was string, set map[string]string) error {
	set, err := checkEmbedSettings(set, embedSettingKeys)
	if err != nil {
		return err
	}
	_, _, _, err = changePageOp(user, slug, "Change the task list's settings on", "", func(old string) (string, error) {
		text, err := setEmbedParams(old, index, was, set)
		if err == nil && text == old {
			return "", refuseAs(refusedUnchanged, "Nothing to change")
		}
		return text, err
	})
	if pe, ok := refusalOf(err); ok && pe.kind == refusedUnchanged {
		return nil
	}
	return err
}

// tasksSettingsRoute: the settings dialog's Save: {page, index, was, set}
// for an embed on a page, {all: true, was, set} for All tasks, {list, set}
// for a list's own settings (list: its ref); set: the params to change, ""
// taking one out. Answers {ok} or {error}.
func tasksSettingsRoute(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Page  string            `json:"page"`
		Index int               `json:"index"`
		All   bool              `json:"all"`
		List  string            `json:"list"`
		Was   string            `json:"was"`
		Set   map[string]string `json:"set"`
	}
	raw, err := io.ReadAll(r.Body)
	if err != nil || json.Unmarshal(raw, &body) != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Request body must be a JSON object"})
		return
	}
	user := userOf(r)
	if body.List != "" {
		err = setListSettingsOp(user, body.List, body.Set)
	} else if body.All {
		err = setAllTasksParams(user, body.Was, body.Set)
	} else {
		err = setEmbedParamsOp(user, body.Page, body.Index, body.Was, body.Set)
	}
	rf, refused := refusalOf(err)
	switch {
	case err == nil:
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	case refused:
		writeJSON(w, rf.kind.status(), map[string]string{"error": rf.msg})
	default:
		// Written, but not committed (notCommitted): the page has the change.
		logf("%s %s: %v", r.Method, r.URL.Path, err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "The settings couldn't be saved (Zone's log says why)"})
	}
}

type tasksEmbed struct{ placeholder, html string }

// processTasksEmbeds swaps each [[tasks: …]] for a placeholder; the embeds'
// HTML replaces them after rendering (injectTasksEmbeds). page: the page
// rendered ("" none): a list=<name> there is the list of that name as the
// page has it (listOnPage), and a name no list has a line that says so.
// settings: text is that page's as its file has it (a page view, not a
// preview or an old version), so the embeds get the settings dialog.
func processTasksEmbeds(rq *Req, text, page string, settings bool) (string, []tasksEmbed) {
	at := ""
	if settings {
		at = page
	}
	lists := rq.taskLists()
	var embeds []tasksEmbed
	if rq.who() == visitor {
		// A public wiki's visitor sees no task, so no task list: the page
		// reads as without its embeds.
		return tasksRE.ReplaceAllString(text, ""), nil
	}
	text = tasksRE.ReplaceAllStringFunc(text, func(m string) string {
		g := tasksRE.FindStringSubmatch(m)
		ph := tasksPlaceholder + "_" + strconv.Itoa(len(embeds))
		params := parseTasksParams(embedArgs(g))
		params.src = &embedSrc{len(embeds), embedArgs(g)}
		var html string
		if name := cleanList(params.Get("list")); name == "" {
			html = buildTasksEmbed(rq, params, len(embeds), false, at)
		} else if l, ok := listOnPage(name, page, rq.who(), lists); ok {
			html = buildTasksEmbed(rq, listEmbedParams(l, params), len(embeds), false, at)
		} else {
			html = noListHTML(rq, name)
		}
		if settings && len(embeds) == 0 {
			html = tasksInitial(rq) + html
		}
		embeds = append(embeds, tasksEmbed{ph, html})
		return ph
	})
	return text, embeds
}

// assignablePeople: who a task can be assigned to, and who asks ("me"): for
// the browser's completion, its cards and its @me. No names in sharing mode.
func assignablePeople(r *http.Request) map[string]any {
	names := []string{}
	if !sharingOn(r) {
		names = settings().assignableUsers()
	}
	return map[string]any{"users": names, "me": userOf(r)}
}

// tasksInitial: what a page view with a task list brings along, so the list
// is drawn when the page is, with no request of its own: the user's open
// tasks as GET /api/tasks answers them, and their notes' and comments' HTML
// as POST /api/render-notes would, in the tasks' order (js/taskdata.js
// initialTasks, js/tasktexts.js textsOf). A data block, which no browser
// runs; json.Marshal writes <, > and & as \u escapes, so no text in a task
// ends it.
func tasksInitial(rq *Req) string {
	tasks := taskFiles.Open(rq.who())
	var notes, comments []string
	for _, t := range tasks {
		if t.Description != "" {
			notes = append(notes, t.Description)
		}
		for _, c := range t.Comments {
			comments = append(comments, c.Text)
		}
	}
	block := map[string]any{
		"tasks": apiTasks(tasks), "notes": renderNotes(rq, notes, false), "comments": renderNotes(rq, comments, true)}
	// With users, who a task can be assigned to and who looks, as GET
	// /api/tasks/assignable answers (js/taskutil.js setPeople).
	if settings().Users != nil {
		block["people"] = assignablePeople(rq.r)
	}
	data, err := json.Marshal(block)
	if err != nil {
		logf("task list embed: %v", err)
		return ""
	}
	return `<script type="application/json" id="tasks-initial">` + string(data) + `</script>`
}

// injectTasksEmbeds: the last first, as _1 is a prefix of _10.
func injectTasksEmbeds(html string, embeds []tasksEmbed) string {
	for i := len(embeds) - 1; i >= 0; i-- {
		e := embeds[i]
		html = strings.ReplaceAll(html, "<p>"+e.placeholder+"</p>", e.html)
		html = strings.ReplaceAll(html, e.placeholder, e.html)
	}
	return html
}

// TaskCounts is what the Tasks module links show.
type TaskCounts struct {
	Count   int    `json:"count"`
	Overdue int    `json:"overdue"`
	Today   int    `json:"today"`
	Focus   int    `json:"focus"`
	Title   string `json:"-"`
}

// taskCounts: the open tasks in Focus (started, or due today or earlier)
// and of those how many are overdue or due today; of the pinned lists
// (pinnedView) when pages pin some, else of every task.
func taskCounts(rq *Req) TaskCounts {
	tasks := rq.openTasks()
	if len(rq.pinnedLists()) == 0 {
		return countTasks(rq.who(), tasks, rq.taskLists())
	}
	return countTasks(rq.who(), tasks, rq.taskLists(), rq.pinnedView())
}

// pinnedView: the params of the view of the pinned lists: one list's own;
// several as one task app, the default list's params showing the tasks of
// all of them (lists=a|b). A new task there goes to the default list.
// For a user who set Pinned to their own tasks (onlyMine) only those
// (assignee=me), of one list too.
func (rq *Req) pinnedView() TasksParams {
	pinned := rq.pinnedLists()
	params := pinned[0].Params
	if len(pinned) > 1 {
		refs := make([]string, len(pinned))
		for i, l := range pinned {
			refs[i] = l.Ref
		}
		params = params.With("list", "").With("lists", strings.Join(refs, "|"))
	}
	if rq.onlyMine() {
		params = params.With("assignee", "me")
	}
	return params
}

// myLists: the user's pinned lists are a view of their own, Pinned
// (/tasks?pinned=1): several lists as one, or their tasks alone (onlyMine).
func (rq *Req) myLists() bool { return len(rq.pinnedLists()) > 1 || rq.onlyMine() }

// pinnedLink: one of the lists the pinned view shows, under its heading:
// the list at /tasks?list= ("#name").
type pinnedLink struct{ Href, Label string }

// listHref: the Tasks page of the list the user names ref.
func listHref(ref string) string { return "/tasks?list=" + url.QueryEscape(ref) }

// pinnedSources: what the pinned view (pinnedView) is made of: the pinned
// lists in their order, and the pages that embed them (each list's
// listPages), once each, A–Z by title.
func (rq *Req) pinnedSources() ([]pinnedLink, []PageRef) {
	var links []pinnedLink
	var pages []PageRef
	seen := map[string]bool{}
	for _, l := range rq.pinnedLists() {
		links = append(links, pinnedLink{listHref(l.Ref), "#" + l.Name})
		for _, p := range rq.listPages(l.Ref) {
			if !seen[p.Slug] {
				seen[p.Slug] = true
				pages = append(pages, p)
			}
		}
	}
	slices.SortStableFunc(pages, func(a, b PageRef) int { return strings.Compare(strings.ToLower(a.Title), strings.ToLower(b.Title)) })
	return links, pages
}

// assigneeParam: an embed's assignee= as the browser gets it: lowercase,
// without "@", and nothing without users (there '@' is no filter word).
func assigneeParam(p TasksParams) string {
	if settings().Users == nil {
		return ""
	}
	return strings.ReplaceAll(strings.ToLower(strings.TrimSpace(p.Get("assignee"))), "@", "")
}

// countTasks: taskCounts of the open tasks the views with params show
// user (any of them; none: every task); lists: those user sees.
func countTasks(user string, tasks []*Task, lists map[string]ListInfo, params ...TasksParams) TaskCounts {
	day := isoDay(today())
	shows := func(t *Task) bool {
		for _, p := range params {
			if inScope(t, paramsScope(p), lists) && tagParamMatches(t, p.Get("tag")) &&
				assigneeParamMatches(t, p.Get("assignee"), user) {
				return true
			}
		}
		return len(params) == 0
	}
	var c TaskCounts
	for _, t := range tasks {
		if !shows(t) {
			continue
		}
		due, start := t.DueDate, t.StartDate
		if start != "" && start <= day || due != "" && due <= day {
			c.Focus++
		}
		if due != "" && due < day {
			c.Overdue++
		} else if due == day {
			c.Today++
		}
	}
	c.Count = c.Overdue + c.Today
	return c
}

// TaskPlace is an entry of the task list switcher (tasks_page.html): a
// list, or Pinned (the pinned lists together). Key names it
// for the counts' refresh (js/chrome.js): "list:<ref>", "all", "pinned".
// Pinned: a pinned list, or Pinned itself; First: the first of several pinned
// lists, where a new task goes (its pill carries a plus).
type TaskPlace struct {
	Key, Label, Title, Href         string
	Pinned, First, Private, Current bool
	Counts                          TaskCounts
}

// taskPlaces: the pills of the list switcher: with several pinned lists
// Pinned (all of them as one, /tasks?pinned=1), then the pinned lists in
// their order, each at /tasks?list=, each with its counts; in sharing mode
// not a list that is not for sharing. What isn't pinned is under More
// (taskMore).
func (rq *Req) taskPlaces() []TaskPlace {
	user := rq.who()
	pinned, lists := rq.pinnedLists(), rq.taskLists()
	var out []TaskPlace
	tasks := rq.openTasks()
	if rq.myLists() {
		title := "Your pinned lists, as one"
		if rq.onlyMine() {
			title = "Your tasks in your pinned lists"
		}
		out = append(out, TaskPlace{Key: "pinned", Label: "Pinned", Title: title, Href: "/tasks?pinned=1",
			Pinned: true, Counts: countTasks(user, tasks, lists, rq.pinnedView())})
	}
	for i, l := range pinned {
		if rq.listHidden(l) {
			continue
		}
		p := TaskPlace{Key: "list:" + l.Ref, Label: l.Name, Title: "Pinned list #" + l.Name, Href: listHref(l.Ref),
			Pinned: true, Private: l.Private(), Counts: countTasks(user, tasks, lists, l.Params)}
		if i == 0 && len(pinned) > 1 {
			p.First, p.Title = true, p.Title+": new tasks go here"
		}
		out = append(out, p)
	}
	return out
}

// listHidden: in sharing mode a list that is not for sharing shows nowhere
// in the switcher.
func (rq *Req) listHidden(l ListInfo) bool { return rq != nil && rq.sharing && l.NoShare }

// TasksMore: the switcher's last pill, More: the lists the user sees and
// hasn't pinned (Lists, A–Z, their own before the shared one of a name; no
// counts) and All tasks. Label: "More", or the view the page shows when
// that is one of these (Current: the pill is the shaded one; All: it is
// All tasks). Filter: enough lists that a field to find one by name helps.
type TasksMore struct {
	Label        string
	Current, All bool
	Lists        []TaskPlace
	Filter       bool
}

// moreFilterFrom: how many lists More holds before it has a filter field.
const moreFilterFrom = 8

// taskMore: More for a page whose view is place (a TaskPlace's key, "all",
// or "" for none); nil when the wiki has no list to switch between, and so
// no switcher.
func (rq *Req) taskMore(place string) *TasksMore {
	lists := rq.taskLists()
	isPinned := map[string]bool{}
	for _, l := range rq.pinnedLists() {
		isPinned[l.Ref] = true
	}
	more := &TasksMore{Label: "More"}
	any := false
	rest := make([]ListInfo, 0, len(lists))
	for _, l := range lists {
		if rq.listHidden(l) {
			continue
		}
		any = true
		if !isPinned[l.Ref] {
			rest = append(rest, l)
		}
	}
	if !any {
		return nil
	}
	slices.SortFunc(rest, func(a, b ListInfo) int {
		if c := strings.Compare(a.Name, b.Name); c != 0 {
			return c
		}
		return strings.Compare(a.Ref, b.Ref) // the user's own before the shared one of that name
	})
	for _, l := range rest {
		p := TaskPlace{Key: "list:" + l.Ref, Label: l.Name, Title: "List #" + l.Name, Href: listHref(l.Ref),
			Private: l.Private(), Current: place == "list:"+l.Ref}
		if p.Current {
			more.Label, more.Current = l.Name, true
		}
		more.Lists = append(more.Lists, p)
	}
	if place == "all" {
		more.Label, more.Current, more.All = "All tasks", true, true
	}
	more.Filter = len(more.Lists) >= moreFilterFrom
	return more
}

// placeCounts: each place's counts by its key (GET /api/tasks/due-count?places=1).
func (rq *Req) placeCounts() map[string]TaskCounts {
	out := map[string]TaskCounts{}
	for _, p := range rq.taskPlaces() {
		out[p.Key] = p.Counts
	}
	return out
}
