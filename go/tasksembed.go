// The [[tasks:]] embed: its HTML, its settings dialog (an embed's params
// written back into the page, a list's and All tasks' into the lists'
// registry), and the counts the Tasks links and the pinned lists show.
package main

import (
	"bytes"
	"encoding/json"
	"html/template"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"sync"
	"zonewiki/internal/jsonobj"
)

const tasksPlaceholder = "ZONETASKSPLACEHOLDER"

// tasksEmbedView: what the tasks_embed template shows: a task app as its
// embed's params and the page it stands on say.
type tasksEmbedView struct {
	Tag, View     string
	DefaultView   string           // the tab the view opens on when nothing says: "open", "" Today
	List          string           // the list it shows, as the user names it (a ref); "" none
	Out           string           // My tasks: the lists left out of it, their refs, "|" between
	Mine          string           // My tasks (mine=1): "assign" where assignment counts for the user, else "lists"
	Group         string           // none, day, person, list (tasksGroups) or "": the embed doesn't say
	Limit         string           // the WIP limit, "" none
	Labels        []string         // the Open and Focus tabs' names
	InstanceID    int              // which embed of the page
	Sfx           string           // "-<InstanceID>" after the first: its elements' ids end in it
	Headless      bool             // hidden: only its edit modal is used (the editor's Make task, a new task from any page)
	Keep          string           // "1": new tasks here are keep tasks
	Home          bool             // the Inbox or My tasks (data-home): its + New is the one the phone's Task tab clicks on its page
	NewTags       string           // new tasks' tags, comma-separated
	CanAssign     bool             // with users: the modal has the Assignee field
	Assignee      string           // assignee=, with users: whose tasks it shows
	PrivateFolder string           // the user's private folder: where a private task's pasted files go
	ListsJSON     string           // the lists the user sees, [{ref, private}], as JSON: the List field's choices
	InboxPrivate  bool             // with users: a task without a list is in the user's Inbox, private
	Settings      *embedSettingsAt // where the settings dialog writes; nil: no dialog (List settings… in the ⋯ menu)
	Menu          bool             // the ⋯ menu: Export tasks for whoever sees them, the rest as Writes and Settings say
	Writes        bool             // the user may write tasks here (data-writes): the menu's Import tasks… and Mark all read
	Data          template.HTML    // the page's data block of tasks (tasksInitial), as the first list's first child; "" none
}

// embedSettingsAt: where a task app's settings are written, for its
// dialog: the Index-th embed of Page as written (Raw; Index < 0: All
// tasks', or My tasks' with Mine), and the list it shows (List, a ref; its
// settings as JSON), "" for none.
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
	Home         bool // the list can be left out of the user's My tasks (its setting home)
}

// listEmbedParams: what an embed of the list l shows: the list's own
// settings, and of the embed's params how the page looks at the list: the
// tab it opens on (view), how the Open tab shows its tasks (group) and a
// filter (tag, assignee). What the list is (limit, keep, labels, new) is
// said on the list alone.
func listEmbedParams(l ListInfo, embed TasksParams) TasksParams { return onPage(l.Params, embed) }

// onPage: what an embed on a page shows, with the settings of what it is
// (shown: a list's, My tasks', the Inbox's or All tasks'), and the page's
// own over them: the tab, the grouping, a filter (embedSettingKeys). An
// embed says which tasks and how it looks at them; what they are (a WIP
// limit, the tabs' names, keep and a new task's tags) is said once, where
// they are.
func onPage(shown, embed TasksParams) TasksParams {
	p := shown
	for _, k := range embedSettingKeys {
		if embed.Has(k) {
			p = p.With(k, embed.Get(k))
		}
	}
	p.src = embed.src
	return p
}

// paramsScope: which tasks a view with these params shows: My tasks
// (mine=1: mineScope), one list's ref, or "" for every task. (Several
// lists as one are a request's, ?lists=: queryScope.)
func paramsScope(p TasksParams) string {
	if isTrue(p.Get("mine")) {
		return mineScope
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
// settings of All tasks; not for a viewer nor in sharing mode. The ⋯ menu
// itself is for whoever sees the tasks (Export tasks), not in sharing mode.
func buildTasksEmbed(rq *Req, params TasksParams, instance int, headless bool, page, data string) string {
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
			if u := settings().Users[user.Name]; at.Mine && cfg.Mail.Host != "" && u != nil && u.Email != "" && mineAssigns(user) {
				at.Mail = true
			}
		default:
			at = nil
		}
		if shown && canManageList(user, list) {
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
		}
		settingsAt = at
	}
	// view=: the tab; group=: how the Open tab shows its tasks.
	view, group := embedView(params), embedGroup(params)
	// limit=N: the WIP limit (Focus warning, Plan day flags).
	limit := ""
	if n, err := strconv.Atoi(strings.TrimSpace(params.Get("limit"))); err == nil {
		limit = strconv.Itoa(max(1, min(99, n)))
	}
	// labels=Catalogue,Shopping: the embed's own names for Open and Today;
	// "_" stands for a space, an empty entry keeps the default.
	labels := []string{"Open", "Today"}
	names := strings.Split(params.Get("labels"), ",")
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
		// A group's list: the group, and its members, who its tasks can be
		// given to.
		Group   string   `json:"group,omitempty"`
		Members []string `json:"members,omitempty"`
	}
	choices := []listChoice{}
	for _, ref := range sortedKeys(lists) {
		l := lists[ref]
		choices = append(choices, listChoice{Ref: ref, Private: l.Private(), Group: l.Group(), Members: listMembers(l)})
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
		Keep: keep, Home: scope == mineScope || isInbox(scope, lists),
		NewTags:       strings.Join(newTaskTags(params), ","),
		CanAssign:     settings().Users != nil,
		Assignee:      assigneeParam(params),
		PrivateFolder: privateFolder(user),
		ListsJSON:     jsonobj.Text(choices),
		InboxPrivate:  inboxOwner(user) != "",
		Data:          template.HTML(data),
		// The ⋯ menu where there are settings, or where the tasks are shown
		// for themselves: on /tasks, or an embed on its page's own view, not
		// in a preview or an old version (page "": params of a page's text).
		Menu: settingsAt != nil || !headless && rq != nil && !rq.sharing && (page != "" || params.src == nil || params.src.index < 0),
	}
	// Import into a list is for who may write its tasks (canWriteList: a
	// list shown is one the user sees), as apiImport asks.
	v.Writes = v.Menu && mayWriteTasks(user) == nil
	// What is yours to do opens on Today: My tasks, a list of your own, a
	// view of every task where that is the Tasks links' (a wiki without
	// lists) or a page's. A place you look into opens on Open: a shared list
	// (a team's backlog, and what no one has), the Inbox (what was captured,
	// with or without a date), and All tasks beside My tasks.
	allTasksPage := page == "" && !headless && scope == "" && len(lists) > 0 // /tasks, where the Tasks links go to My tasks
	switch {
	case shown && !list.Private() && settings().Users != nil, isInbox(scope, lists), allTasksPage:
		v.DefaultView = "open"
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
// lists a button that makes it; instance: which embed of the page (its
// id, by which js/forms.js knows its form is Zone's).
func noListHTML(rq *Req, name string, instance int) string {
	sfx := ""
	if instance > 0 {
		sfx = "-" + strconv.Itoa(instance)
	}
	var b bytes.Buffer
	err := partials.ExecuteTemplate(&b, "tasks_no_list", map[string]any{
		"Name": name, "Sfx": sfx, "Create": rq != nil && !rq.sharing && mayWriteTasks(rq.who()) == nil})
	if err != nil {
		logf("task list embed: %v", err)
	}
	return b.String()
}

// ---- the settings dialog ----

// allTasksParams: the params of All tasks (/tasks), which no page's
// [[tasks: …]] holds: they are in the shared registry ("all": the params
// an embed would have, allSettingKeys). A value that can't be read is left
// out, the others stay (validSettings).
func allTasksParams() TasksParams {
	set := validSettings(taskFiles.ReadLists("").All, allSettingKeys, taskFiles.ListsPath(""))
	raw := setEmbedArgs("", set)
	p := parseTasksParams(raw)
	p.src = &embedSrc{-1, raw}
	return p
}

// setAllTasksParams writes the settings of All tasks with those of set
// changed, in a commit by user; was: the params as the dialog saw them.
func setAllTasksParams(user asker, was string, set map[string]string) error {
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
	return commitLists(user, "Change the settings of All tasks", path)
}

// setEmbedParamsOp changes the params of the index-th [[tasks: …]] of the
// page slug (setEmbedParams), in a commit by user.
func setEmbedParamsOp(user asker, slug string, index int, was string, set map[string]string) error {
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
	if rq.who().Name == visitor {
		// A public wiki's visitor sees no task, so no task list: the page
		// reads as without its embeds.
		return tasksRE.ReplaceAllString(text, ""), nil
	}
	// What the page's lists show: the tasks it brings along are those
	// (tasksInitial), in the first list's own element.
	type embedded struct {
		params  TasksParams
		missing string // list=<name> that no list has
	}
	var found []embedded
	var scopes []string
	text = tasksRE.ReplaceAllStringFunc(text, func(m string) string {
		g := tasksRE.FindStringSubmatch(m)
		ph := tasksPlaceholder + "_" + strconv.Itoa(len(found))
		params := parseTasksParams(embedArgs(g))
		params.src = &embedSrc{len(found), embedArgs(g)}
		if name := cleanList(params.Get("list")); name == "" || isInbox(name, lists) {
			// No list=: All tasks, or My tasks with mine=1; list=inbox: the
			// Inbox (the tasks in no list). Each with its own settings, as
			// /tasks shows it.
			shown := allTasksParams()
			switch {
			case paramsScope(params) == mineScope:
				shown = mineParams(rq.who())
			case name != "":
				shown = inboxParams()
			}
			shown = onPage(shown, params)
			found = append(found, embedded{params: shown})
			scopes = append(scopes, paramsScope(shown))
		} else if l, ok := listOnPage(name, page, rq.who(), lists); ok {
			shown := listEmbedParams(l, params)
			found = append(found, embedded{params: shown})
			scopes = append(scopes, paramsScope(shown))
		} else {
			found = append(found, embedded{missing: name})
		}
		return ph
	})
	data := ""
	if settings && len(scopes) > 0 {
		data = tasksInitial(rq, scopes...)
	}
	for i, e := range found {
		html := noListHTML(rq, e.missing, i)
		if e.missing == "" {
			html = buildTasksEmbed(rq, e.params, i, false, at, data)
			data = ""
		}
		embeds = append(embeds, tasksEmbed{tasksPlaceholder + "_" + strconv.Itoa(i), html})
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
	return map[string]any{"users": names, "me": userOf(r).Name}
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
	word := tasksWord(rq.who(), scopes...) // of these tasks: read before them (tasksapi.go, Zone-Tasks)
	tasks := scopedTasks(rq.who(), rq.openTasks(), rq.taskLists(), scopes...)
	if slices.Contains(scopes, mineScope) && !slices.Contains(scopes, "") {
		tasks = withUpdates(rq.who(), tasks, rq.openTasks(), rq.recentClosed()) // My tasks' Updates
	}
	block := map[string]any{}
	// With users, who a task can be assigned to and who looks, as GET
	// /api/tasks/assignable answers (js/taskutil.js setPeople).
	if settings().Users != nil {
		block["people"] = assignablePeople(rq.r)
	}
	// Not a long list: its tasks are megabytes, and a page goes as it is
	// while GET /api/tasks answers gzipped (gzipTasks), a tenth of it. The
	// list then asks for its tasks when it starts, as one that starts later
	// does; it renders a card's texts when its details are opened anyway
	// (js/taskdata.js lazyTexts).
	if len(tasks) > lazyTextsFrom {
		return taskData(block)
	}
	var notes, comments []string
	for _, t := range tasks {
		if t.Description != "" {
			notes = append(notes, t.Description)
		}
		for _, c := range t.Comments {
			comments = append(comments, c.Text)
		}
	}
	block["word"], block["order"], block["tasks"] = word, mineOrder(rq.who()), apiTasks(tasks)
	block["notes"], block["comments"] = renderNotes(rq, notes, false), renderNotes(rq, comments, true)
	return taskData(block)
}

// taskData: block as the page's data block of tasks (tasksInitial): a
// <script> of JSON (no browser runs it) in the first list's own element
// (buildTasksEmbed's data), where js/taskdata.js looks for it. Not found
// by an id: a page's raw HTML could give one of its elements that id
// first, with tasks of its own whose notes' "rendered HTML" would go
// into the cards as it is, and it can't make a <script> (defuseHTML).
func taskData(block map[string]any) string {
	data, err := json.Marshal(block)
	if err != nil {
		logf("task list embed: %v", err)
		return ""
	}
	return `<script type="application/json" class="tasks-initial">` + string(data) + `</script>`
}

// lazyTextsFrom: a page whose lists show more tasks than this doesn't bring
// them (the list asks for them), and such a list renders a card's texts
// when its details are opened (LAZY_FROM in js/taskdata.js is the same
// number).
const lazyTextsFrom = 150

// scopedTasks: those of tasks that a view of any of scopes shows user
// (scopeFilter); every task when one of them is "" (every task).
func scopedTasks(user asker, tasks []*Task, lists map[string]ListInfo, scopes ...string) []*Task {
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
	// touched yet (newsFor: newsGiven). Such a task often has no date, so it is in
	// neither of the counts above; without this one, nothing outside My
	// tasks would say that it is there.
	New   int    `json:"new,omitempty"`
	Title string `json:"-"`
}

// taskCounts: the open tasks in Focus (started, or due today or earlier)
// and of those how many are overdue or due today, of My tasks (mineFilter):
// what the Tasks links open. With no list in the wiki that is every task.
func taskCounts(rq *Req) TaskCounts {
	return rq.keptCounts("mine", func() TaskCounts {
		c := countTasks(rq.who(), rq.openTasks(), rq.taskLists(), mineScopeParams)
		// New: every task with news for the user, wherever it is and also one
		// closed lately: what they gave away is not in My tasks.
		c.New = len(updatesFor(rq.who(), rq.openTasks(), rq.recentClosed()))
		return c
	})
}

// countsKept: the counts each user was last shown (the Tasks links' and the
// list switcher's, by a place's key), for as long as what they are counted
// from is what it was: the task files and registries the user reads
// (taskFiles.Stamp: a write of Zone's, a change by hand), what the user has
// seen (seenVersion), the day (what is due), and the settings (who is a
// user). Every page view shows the Tasks
// links' counts and every open page asks for them once a minute: counted
// anew each time, that is every open task the user sees read and looked
// through, for a number that changes with a write.
var countsKept = struct {
	sync.Mutex
	of map[string]*userCounts
}{of: map[string]*userCounts{}}

type userCounts struct {
	stamp, day string
	set        *Settings
	counts     map[string]TaskCounts
}

// keptCounts: the counts of the place key for the request's user, counted
// by count when what was kept is no longer true.
func (rq *Req) keptCounts(key string, count func() TaskCounts) TaskCounts {
	user := rq.who()
	// The stamp before the count: a write between the two gives counts kept
	// under a stamp that is no longer the files', which are then counted anew.
	stamp, day, set := rq.tasksStamp()+itoa(int(seenVersion(user.Name))), isoDay(today()), settings()
	countsKept.Lock()
	kept := countsKept.of[user.Name]
	if kept == nil || kept.stamp != stamp || kept.day != day || kept.set != set {
		kept = &userCounts{stamp: stamp, day: day, set: set, counts: map[string]TaskCounts{}}
		countsKept.of[user.Name] = kept
	}
	c, ok := kept.counts[key]
	countsKept.Unlock()
	if ok {
		return c
	}
	c = count()
	countsKept.Lock()
	kept.counts[key] = c
	countsKept.Unlock()
	return c
}

// tasksStamp: taskFiles.Stamp of the request's user, looked up once a
// request.
func (rq *Req) tasksStamp() string {
	if rq == nil {
		return taskFiles.Stamp(asker{})
	}
	if rq.stamp == "" {
		rq.stamp = taskFiles.Stamp(rq.user) + "."
	}
	return rq.stamp
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
func countTasks(user asker, tasks []*Task, lists map[string]ListInfo, params ...TasksParams) TaskCounts {
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
		if taskFocus(t, day) {
			c.Focus++
		}
		if taskOverdue(t, day) {
			c.Overdue++
		} else if t.DueDate == day {
			c.Today++
		}
		if newsFor(t, user).Kind != "" {
			c.New++
		}
	}
	c.Count = c.Overdue + c.Today
	return c
}

// taskFocus: the task is for today (the Today tab, the Tasks links' count):
// started, or due today or earlier. taskOverdue: its due date has passed.
// The browser's rule is TasksCore.isFocus and isOverdue: both run through
// go/testdata/focus_cases.json.
func taskFocus(t *Task, day string) bool {
	return t.StartDate != "" && t.StartDate <= day || t.DueDate != "" && t.DueDate <= day
}

func taskOverdue(t *Task, day string) bool { return t.DueDate != "" && t.DueDate < day }

// TaskPlace is an entry of the task list switcher (tasks_page.html): a
// list, or My tasks. Key names it for the counts' refresh (js/chrome.js):
// "list:<ref>", "all", "mine", "inbox". Pinned: a pinned list.
type TaskPlace struct {
	Key, Label, Title, Href  string
	Pinned, Private, Current bool
	Group                    string // a group's list: the group, for its 👥 and tooltip
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
	title := "Every task but those of the lists left out of My tasks"
	if mineAssigns(user) {
		title = "Your own tasks and what is assigned to you"
	}
	out := []TaskPlace{{Key: "mine", Label: "My tasks", Title: title, Href: mineHref, Counts: taskCounts(rq)}}
	if rq.hasInbox() {
		p := TaskPlace{Key: inboxRef, Label: "Inbox", Title: "Inbox: new tasks land here", Href: listHref(inboxRef),
			Private: inboxOwner(user) != "",
			Counts:  rq.keptCounts(inboxRef, func() TaskCounts { return countTasks(user, rq.openTasks(), lists, inboxParams()) })}
		out = append(out, p)
	}
	for _, l := range pinned {
		if rq.listHidden(l) {
			continue
		}
		out = append(out, TaskPlace{Key: "list:" + l.Ref, Label: l.Name, Title: "Pinned list #" + l.Name, Href: listHref(l.Ref),
			Pinned: true, Private: l.Private(), Group: l.Group(),
			Counts: rq.keptCounts("list:"+l.Ref, func() TaskCounts { return countTasks(user, rq.openTasks(), lists, l.Params) })})
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
	// Others (with users): the lists for everyone that the user has nothing
	// in; Lists are then theirs: their private ones, those for some that
	// they are one of, and those for everyone with a task assigned to them.
	// Among forty lists of a company the few that are one's own come first.
	Others []TaskPlace
	Filter bool
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
	// With users: which of them are the user's.
	user := rq.who()
	var has map[string]bool
	if privateFolder(user) != "" {
		has = map[string]bool{}
		for _, t := range rq.openTasks() {
			if !privateOwner(t.Owner) && t.Assignee == user.Name {
				has[taskList(t, lists)] = true
			}
		}
	}
	for _, l := range rest {
		p := TaskPlace{Key: "list:" + l.Ref, Label: l.Name, Title: "List #" + l.Name, Href: listHref(l.Ref),
			Private: l.Private(), Group: l.Group(), Current: place == "list:"+l.Ref}
		if p.Current {
			more.Label, more.Current = l.Name, true
		}
		if has == nil || l.Private() || l.Group() != "" || has[l.Ref] {
			more.Lists = append(more.Lists, p)
		} else {
			more.Others = append(more.Others, p)
		}
	}
	if place == "all" {
		more.Label, more.Current, more.All = "All tasks", true, true
	}
	more.Filter = len(more.Lists)+len(more.Others) >= moreFilterFrom
	return more
}

// hasInbox: the user has an Inbox to show: anyone without users; with users
// who may write tasks (a viewer makes none), and not in sharing mode, where
// what is private isn't shown.
func (rq *Req) hasInbox() bool {
	user := rq.who()
	if user.Name == visitor {
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
