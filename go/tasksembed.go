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
	"zonewiki/internal/taskfiles"
)

const tasksPlaceholder = "ZONETASKSPLACEHOLDER"

// tasksEmbedView: what the tasks_embed template shows: a task app as its
// embed's params and the page it stands on say.
type tasksEmbedView struct {
	Tag, View     string
	List          string           // the list it shows, as the user names it (a ref); "" none
	Lists         string           // several lists as one (lists=): their refs, "|" between
	Out           string           // My tasks: the lists left out of it, their refs, "|" between
	Mine          string           // My tasks (mine=1): "assign" where assignment counts for the user, else "lists"
	Group         string           // none, day, person, list (tasksGroups) or "": the embed doesn't say
	Limit         string           // the WIP limit, "" none
	Labels        []string         // the Open and Focus tabs' names
	InstanceID    int              // which embed of the page
	Sfx           string           // "-<InstanceID>" after the first: its elements' ids end in it
	Headless      bool             // hidden: only its edit modal is used (the editor's Make task, a new task from any page)
	Keep          string           // "1": new tasks here are keep tasks
	Pinned        bool             // the Inbox or My tasks: its New is the one New task clicks on its page
	NewTags       string           // new tasks' tags, comma-separated
	CanAssign     bool             // with users: the modal has the Assignee field
	Assignee      string           // assignee=, with users: whose tasks it shows
	PrivateFolder string           // the user's private folder: where a private task's pasted files go
	ListsJSON     string           // the lists the user sees, [{ref, private}], as JSON: the List field's choices
	InboxPrivate  bool             // with users: a task without a list is in the user's Inbox, private
	Settings      *embedSettingsAt // where the settings dialog writes; nil: no dialog (the ⋯ menu)
}

// embedSettingsAt: where a task app's settings are written, for its
// dialog: the Index-th embed of Page as written (Raw; Index < 0: All
// tasks', or My tasks' with Mine), and the list it shows (List, a ref; its
// settings as JSON, and whether the user pinned it: Pin "1"), "" for none.
// A list's own settings go to its registry, the embed's (the tab, a
// filter) to the page.
type embedSettingsAt struct {
	Page         string
	Index        int
	Raw          string
	Embed        bool // an embed on a page, whose params can be written there
	Mine         bool // My tasks on /tasks: its settings are the user's (mineParams)
	Mail         bool // My tasks: Zone could mail the user about a task assigned to them (it sends mail, they have an address): the dialog has the box
	List         string
	ListSettings string
	Pin          string
	Home         bool // the list can be left out of the user's My tasks (its setting home)
}

// listEmbedParams: what an embed of the list l shows: the list's own
// settings, and of the embed's params how the page looks at the list: the
// tab it opens on (view) and a filter (tag, assignee). What the list is
// (limit, group, keep, labels, new) is said on the list alone.
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

// paramsScope: which tasks a view with these params shows: My tasks
// (mine=1: mineScope), one list's ref, several joined by "|" (lists=), or
// "" for every task.
func paramsScope(p TasksParams) string {
	if isTrue(p.Get("mine")) {
		return mineScope
	}
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
			at.Raw, at.Mine = src.raw, paramsScope(params) == mineScope
			if u := settings().Users[user]; at.Mine && cfg.Mail.Host != "" && u != nil && u.Email != "" && mineAssigns(user) {
				at.Mail = true
			}
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
			// Left out of My tasks counts for a list that is the user's own.
			at.Home = list.Private() || !mineAssigns(user)
			for _, p := range rq.pinnedLists() {
				if p.Ref == list.Ref {
					at.Pin = "1"
				}
			}
		}
		settingsAt = at
	}
	// view= with group=: the Open tab as group= says, whichever of its two
	// views view= names (open: the one list; plan: grouped, by day unless
	// group= says by what).
	view, group := embedView(params), embedGroup(params)
	switch {
	case group != "" && (view == "open" || view == "plan"):
		view = "plan"
		if group == "none" {
			view = "open"
		}
	case view == "open":
		group = "none" // view=open has always been the one list
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
		Tag: params.Get("tag"), View: view, Group: group, Settings: settingsAt,
		Limit: limit, Labels: labels, InstanceID: instance, Sfx: sfx, Headless: headless,
		Keep: keep, Pinned: scope == mineScope || isInbox(scope, lists),
		NewTags:       strings.Join(newTaskTags(params), ","),
		CanAssign:     settings().Users != nil,
		Assignee:      assigneeParam(params),
		PrivateFolder: privateFolder(user),
		ListsJSON:     jsonobj.Text(choices),
		InboxPrivate:  inboxOwner(user) != "",
	}
	if scope == mineScope {
		// My tasks: the browser needs the lists left out of it and whether
		// assignment counts (TasksCore.inMine, as mineFilter).
		refs := []string{}
		for _, ref := range sortedKeys(lists) {
			if lists[ref].LeftOut() {
				refs = append(refs, ref)
			}
		}
		v.Out, v.Mine = strings.Join(refs, "|"), "lists"
		if mineAssigns(user) {
			v.Mine = "assign"
		}
	} else if strings.Contains(scope, "|") {
		v.Lists = scope
	} else if shown {
		v.List = list.Ref
	} else if isInbox(scope, lists) {
		v.List = inboxRef // the Inbox: the tasks in no list; a new one there is in none
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
// for an embed on a page, {all: true, was, set} for All tasks, {mine: true,
// was, set} for the user's My tasks, {list, set} for a list's own settings
// (list: its ref); set: the params to change, "" taking one out. Answers
// {ok} or {error}.
func tasksSettingsRoute(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Page  string            `json:"page"`
		Index int               `json:"index"`
		All   bool              `json:"all"`
		Mine  bool              `json:"mine"`
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
	} else if body.Mine {
		err = setMineParams(user, body.Was, body.Set)
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
	// What the page's lists show: the tasks it brings along are those
	// (tasksInitial).
	var scopes []string
	text = tasksRE.ReplaceAllStringFunc(text, func(m string) string {
		g := tasksRE.FindStringSubmatch(m)
		ph := tasksPlaceholder + "_" + strconv.Itoa(len(embeds))
		params := parseTasksParams(embedArgs(g))
		params.src = &embedSrc{len(embeds), embedArgs(g)}
		var html string
		if name := cleanList(params.Get("list")); name == "" || isInbox(name, lists) {
			// No list=, or list=inbox (the tasks in no list): the embed's own params.
			html = buildTasksEmbed(rq, params, len(embeds), false, at)
			scopes = append(scopes, paramsScope(params))
		} else if l, ok := listOnPage(name, page, rq.who(), lists); ok {
			shown := listEmbedParams(l, params)
			html = buildTasksEmbed(rq, shown, len(embeds), false, at)
			scopes = append(scopes, paramsScope(shown))
		} else {
			html = noListHTML(rq, name)
		}
		embeds = append(embeds, tasksEmbed{ph, html})
		return ph
	})
	if settings && len(embeds) > 0 {
		embeds[0].html = tasksInitial(rq, scopes...) + embeds[0].html
	}
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
//
// scopes: what the page's lists show (paramsScope of each): the tasks of
// any of them come along, and no others, so a page that shows My tasks or
// one list doesn't carry every open task the user sees, with every
// description rendered. A list of every task ("": All tasks, a tag's view)
// takes them all.
func tasksInitial(rq *Req, scopes ...string) string {
	word := taskfiles.Changes() // of these tasks: read before them (tasksapi.go, Zone-Tasks)
	tasks := scopedTasks(rq.who(), taskFiles.Open(rq.who()), rq.taskLists(), scopes...)
	var notes, comments []string
	// Not of a long list: there the browser asks for a card's texts when its
	// details are opened (js/taskdata.js lazyTexts), and the page doesn't
	// carry every description rendered.
	texts := tasks
	if len(tasks) > lazyTextsFrom {
		texts = nil
	}
	for _, t := range texts {
		if t.Description != "" {
			notes = append(notes, t.Description)
		}
		for _, c := range t.Comments {
			comments = append(comments, c.Text)
		}
	}
	block := map[string]any{
		"word": word, "order": mineOrder(rq.who()), "tasks": apiTasks(tasks), "notes": renderNotes(rq, notes, false), "comments": renderNotes(rq, comments, true)}
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

// lazyTextsFrom: a page with more tasks than this brings them without their
// texts' HTML (LAZY_FROM in js/taskdata.js is the same number).
const lazyTextsFrom = 150

// scopedTasks: those of tasks that a view of any of scopes shows user
// (scopeFilter); every task when one of them is "" (every task).
func scopedTasks(user string, tasks []*Task, lists map[string]ListInfo, scopes ...string) []*Task {
	if slices.Contains(scopes, "") {
		return tasks
	}
	in := make([]func(*Task) bool, len(scopes))
	for i, s := range scopes {
		in[i] = scopeFilter(user, s, lists)
	}
	out := []*Task{}
	for _, t := range tasks {
		if slices.ContainsFunc(in, func(f func(*Task) bool) bool { return f(t) }) {
			out = append(out, t)
		}
	}
	return out
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
	Count   int `json:"count"`
	Overdue int `json:"overdue"`
	Today   int `json:"today"`
	Focus   int `json:"focus"`
	// New: the open tasks someone else gave the user that they haven't
	// touched yet (givenTo). Such a task often has no date, so it is in
	// neither of the counts above; without this one, nothing outside My
	// tasks would say that it is there.
	New   int    `json:"new,omitempty"`
	Title string `json:"-"`
}

// taskCounts: the open tasks in Focus (started, or due today or earlier)
// and of those how many are overdue or due today, of My tasks (mineFilter):
// what the Tasks links open. With no list in the wiki that is every task.
func taskCounts(rq *Req) TaskCounts {
	return countTasks(rq.who(), rq.openTasks(), rq.taskLists(), mineScopeParams)
}

// mineScopeParams: params of the scope of My tasks, without its settings
// (mineParams has those), for counting.
var mineScopeParams = TasksParams{}.With("mine", "1")

// mineHref: My tasks, where the Tasks links go.
const mineHref = "/tasks?mine=1"

// listLink: a list as a link under a heading: the list at /tasks?list=
// ("#name").
type listLink struct{ Href, Label string }

// listHref: the Tasks page of the list the user names ref.
func listHref(ref string) string { return "/tasks?list=" + url.QueryEscape(ref) }

// leftOutLinks: the lists left out of the user's My tasks (LeftOut), as far
// as that counts for them: their own (private ones; without users, or for
// one no task is assigned to, any), A–Z.
func (rq *Req) leftOutLinks() []listLink {
	var links []listLink
	lists, assigns := rq.taskLists(), mineAssigns(rq.who())
	for _, ref := range sortedKeys(lists) {
		if l := lists[ref]; l.LeftOut() && (l.Private() || !assigns) && !rq.listHidden(l) {
			links = append(links, listLink{listHref(l.Ref), "#" + l.Name})
		}
	}
	return links
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
	in := make([]func(*Task) bool, len(params))
	for i, p := range params {
		in[i] = scopeFilter(user, paramsScope(p), lists)
	}
	shows := func(t *Task) bool {
		for i, p := range params {
			if in[i](t) && tagParamMatches(t, p.Get("tag")) &&
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
		if givenTo(t, user) {
			c.New++
		}
	}
	c.Count = c.Overdue + c.Today
	return c
}

// TaskPlace is an entry of the task list switcher (tasks_page.html): a
// list, or My tasks. Key names it for the counts' refresh (js/chrome.js):
// "list:<ref>", "all", "mine", "inbox". Pinned: a pinned list.
type TaskPlace struct {
	Key, Label, Title, Href  string
	Pinned, Private, Current bool
	Counts                   TaskCounts
}

// taskPlaces: the pills of the list switcher: My tasks (/tasks?mine=1,
// with the counts the Tasks links have), the Inbox (for who has one: not a
// viewer among users), then the pinned lists in their order, each at
// /tasks?list=, each with its counts; in sharing mode not a list that is
// not for sharing, nor a user's Inbox. What isn't pinned is under More
// (taskMore).
func (rq *Req) taskPlaces() []TaskPlace {
	user := rq.who()
	pinned, lists := rq.pinnedLists(), rq.taskLists()
	tasks := rq.openTasks()
	title := "Every task but those of the lists left out of My tasks"
	if mineAssigns(user) {
		title = "Your own tasks and what is assigned to you"
	}
	out := []TaskPlace{{Key: "mine", Label: "My tasks", Title: title, Href: mineHref, Counts: taskCounts(rq)}}
	if rq.hasInbox() {
		p := TaskPlace{Key: inboxRef, Label: "Inbox", Title: "Inbox: new tasks land here", Href: listHref(inboxRef),
			Private: inboxOwner(user) != "", Counts: countTasks(user, tasks, lists, inboxParams())}
		out = append(out, p)
	}
	for _, l := range pinned {
		if rq.listHidden(l) {
			continue
		}
		out = append(out, TaskPlace{Key: "list:" + l.Ref, Label: l.Name, Title: "Pinned list #" + l.Name, Href: listHref(l.Ref),
			Pinned: true, Private: l.Private(), Counts: countTasks(user, tasks, lists, l.Params)})
	}
	return out
}

// listHidden: in sharing mode a list that is not for sharing shows nowhere
// in the switcher.
func (rq *Req) listHidden(l ListInfo) bool { return rq != nil && rq.sharing && l.NoShare }

// TasksMore: the switcher's last pill, More: the lists the user sees and
// hasn't pinned (Lists, A–Z, their own before the shared one of a name; no
// counts) and All tasks. Label: "More", or the view the page shows
// when that is one of these (Current: the pill is the shaded one; All: it
// is All tasks). Filter: enough lists that a field to find one by name
// helps. The pill has no number.
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

// hasInbox: the user has an Inbox to show: anyone without users; with users
// who may write tasks (a viewer makes none), and not in sharing mode, where
// what is private isn't shown.
func (rq *Req) hasInbox() bool {
	user := rq.who()
	if user == visitor {
		return false
	}
	if inboxOwner(user) == "" {
		return true
	}
	return mayWriteTasks(user) == nil && !(rq != nil && rq.sharing)
}

// inboxParams: the params of the Inbox's view (/tasks?list=inbox): All
// tasks' own, of the tasks in no list. It has no settings of its own, so no
// settings dialog (no src).
func inboxParams() TasksParams {
	p := allTasksParams().With("list", inboxRef)
	p.src = nil
	return p
}

// placeCounts: each place's counts by its key (GET /api/tasks/due-count?places=1):
// the pills'.
func (rq *Req) placeCounts() map[string]TaskCounts {
	out := map[string]TaskCounts{}
	for _, p := range rq.taskPlaces() {
		out[p.Key] = p.Counts
	}
	return out
}
