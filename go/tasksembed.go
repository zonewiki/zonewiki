// The [[tasks:]] embed: its HTML, its settings dialog (the params written
// back into the page, All tasks' in _tasks/settings.json), and the counts the
// Tasks links and the pinned lists show.
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
)

const tasksPlaceholder = "ZONETASKSPLACEHOLDER"

// buildTasksEmbed renders the task app for user on a page (instance numbers
// a page's embeds); headless hides it but for its edit modal (the editor's
// Make task, c t on any page), and tasks.js skips its shortcuts. private:
// a new task here is private (a page in user's own folder). page: the page
// whose text holds the params (params.src), "" for none: with it, or with
// the settings of All tasks, the embed has the settings dialog (the gear),
// but for a viewer and in sharing mode.
func buildTasksEmbed(rq *Req, params TasksParams, instance int, headless, private bool, page string) string {
	user := rq.who()
	var settingsAt M
	if src := params.src; src != nil && !headless && rq != nil && !rq.sharing && (src.index < 0 && page == "" && mayWriteTasks(user) == nil ||
		src.index >= 0 && page != "" && canWrite(user, page)) {
		settingsAt = M{"page": page, "index": src.index, "raw": src.raw}
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
	lists := rq.taskLists()
	names = sortedKeys(lists)
	homes := map[string]string{}
	for _, n := range names {
		homes[n] = firstNonEmpty(lists[n].Title, lists[n].Slug)
	}
	listsJSON, homesJSON := jsonobj.Text(names), jsonobj.Text(homes)
	sfx := ""
	if instance > 0 {
		sfx = "-" + strconv.Itoa(instance)
	}
	keep := ""
	if isTrue(params.Get("keep")) {
		keep = "1"
	}
	var b bytes.Buffer
	err := partials.ExecuteTemplate(&b, "tasks_embed", map[string]any{
		"tag": params.Get("tag"), "list": cleanList(params.Get("list")), "view": view, "plan": plan, "settings": settingsAt,
		"limit": limit, "labels": labels, "instance_id": instance, "sfx": sfx, "headless": headless,
		// keep=1: new tasks here are keep tasks; pin=1: a pinned list (its New
		// is the one New task clicks on its page); new=: new tasks' tags.
		"keep": keep, "pinned": pinnedParam(params), "new_tags": strings.Join(cleanTags(strings.Split(params.Get("new"), ",")), ","),
		// With users, the modal's Private box; ticked for a new task here.
		"can_private": privateFolder(user) != "", "private": private && privateFolder(user) != "",
		"private_folder": privateFolder(user),
		"lists_json":     listsJSON, "homes_json": homesJSON,
	})
	if err != nil {
		logf("task list embed: %v", err)
	}
	return b.String()
}

// ---- the settings dialog ----

// allTasksFile: the settings of All tasks (/tasks), which no page's
// [[tasks: …]] holds: {"all": {"view": "open", …}}, the params an embed
// would have (allSettingKeys).
func allTasksFile() string { return filepath.Join(tasksDir(""), "settings.json") }

// allTasksParams: the params of All tasks, from allTasksFile; none when
// there is no file or it can't be read.
func allTasksParams() TasksParams {
	var stored map[string]map[string]string
	if raw, err := os.ReadFile(allTasksFile()); err == nil {
		if err := json.Unmarshal(raw, &stored); err != nil {
			logf("%s: %v", allTasksFile(), err)
		}
	}
	set, err := checkEmbedSettings(stored["all"], allSettingKeys)
	if err != nil {
		logf("%s: %v", allTasksFile(), err)
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
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	set, err := checkEmbedSettings(set, allSettingKeys)
	if err != nil {
		return err
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	if allTasksParams().src.raw != was {
		return errEmbedChanged
	}
	p := parseTasksParams(setEmbedArgs(was, set))
	text, err := json.MarshalIndent(map[string]map[string]string{"all": p.m}, "", "  ")
	if err != nil {
		return err
	}
	if err := atomicWrite(allTasksFile(), string(text)+"\n"); err != nil {
		return err
	}
	commitTasks(user, "Change the settings of All tasks", []string{allTasksFile()})
	return nil
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
// for an embed on a page, {all: true, was, set} for All tasks; set: the
// params to change, "" taking one out. Answers {ok} or {error}.
func tasksSettingsRoute(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Page  string            `json:"page"`
		Index int               `json:"index"`
		All   bool              `json:"all"`
		Was   string            `json:"was"`
		Set   map[string]string `json:"set"`
	}
	raw, err := io.ReadAll(r.Body)
	if err != nil || json.Unmarshal(raw, &body) != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Request body must be a JSON object"})
		return
	}
	user := userOf(r)
	if body.All {
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
// rendered ("" none); in rq's user's own folder its embeds make private
// tasks. settings: text is that page's as its file has it (a page view,
// not a preview or an old version), so the embeds get the settings dialog.
func processTasksEmbeds(rq *Req, text, page string, settings bool) (string, []tasksEmbed) {
	at := ""
	if settings {
		at = page
	}
	private := page != "" && ownPrivate(rq.who(), page)
	var embeds []tasksEmbed
	text = tasksRE.ReplaceAllStringFunc(text, func(m string) string {
		g := tasksRE.FindStringSubmatch(m)
		ph := tasksPlaceholder + "_" + strconv.Itoa(len(embeds))
		params := parseTasksParams(embedArgs(g))
		params.src = &embedSrc{len(embeds), embedArgs(g)}
		embeds = append(embeds, tasksEmbed{ph, buildTasksEmbed(rq, params, len(embeds), false, private, at)})
		return ph
	})
	return text, embeds
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
	tasks := openTasks(rq.who())
	if len(rq.pinnedLists()) == 0 {
		return countTasks(tasks)
	}
	return countTasks(tasks, rq.pinnedView())
}

// pinnedView: the params of the view of the pinned lists: one list's own;
// several as one task app, the default list's params showing the tasks of
// any of them (tag=a|b: each list, or a list-less pin's single tag; other
// pins can't be written so and are left out), new tasks getting the default
// list's tags (new=).
func (rq *Req) pinnedView() TasksParams {
	pinned := rq.pinnedLists()
	if len(pinned) == 1 {
		return pinned[0].Params
	}
	var words []string
	for _, h := range pinned {
		if l := cleanList(h.Params.Get("list")); l != "" {
			words = append(words, l)
		} else if tag := strings.TrimSpace(h.Params.Get("tag")); tag != "" && !strings.ContainsAny(tag, "|,-") {
			words = append(words, strings.ToLower(strings.TrimLeft(tag, "#")))
		}
	}
	def := pinned[0].Params
	return def.With("list", "").With("tag", strings.Join(words, "|")).With("new", strings.Join(newTaskTags(def), ","))
}

// pinnedLink: one of the lists the Pinned view shows, under its heading: a
// list at /tasks?list= ("#name"), a pin without a list its page, by its title.
type pinnedLink struct{ Href, Label string }

// pinnedSources: what the Pinned view (pinnedView) is made of: the pinned
// lists in their order, and the pages that embed them (each list's
// listPages, a list-less pin's own page), once each, A–Z by title.
func (rq *Req) pinnedSources() ([]pinnedLink, []PageRef) {
	var links []pinnedLink
	var pages []PageRef
	seen := map[string]bool{}
	add := func(refs ...PageRef) {
		for _, p := range refs {
			if !seen[p.Slug] {
				seen[p.Slug] = true
				pages = append(pages, p)
			}
		}
	}
	for _, h := range rq.pinnedLists() {
		if l := cleanList(h.Params.Get("list")); l != "" {
			links = append(links, pinnedLink{"/tasks?list=" + url.QueryEscape(l), "#" + l})
			add(rq.listPages(l)...)
		} else if !hiddenPage(rq, h.Slug) {
			links = append(links, pinnedLink{pathURL("page", h.Slug), h.Title})
			add(PageRef{h.Slug, h.Title})
		}
	}
	slices.SortStableFunc(pages, func(a, b PageRef) int { return strings.Compare(strings.ToLower(a.Title), strings.ToLower(b.Title)) })
	return links, pages
}

// countTasks: taskCounts of the open tasks the views with params show
// (any of them; none: every task).
func countTasks(tasks []*Task, params ...TasksParams) TaskCounts {
	day := isoDay(today())
	shows := func(t *Task) bool {
		for _, p := range params {
			if inScope(t, cleanList(p.Get("list"))) && tagParamMatches(t, p.Get("tag")) {
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
// list, all tasks, or Pinned (the pinned lists together, or a single pin
// without a list). Key names it for the counts' refresh (js/chrome.js):
// "list:<name>", "all", "pinned". Pinned: a pinned list, or Pinned.
type TaskPlace struct {
	Key, Label, Title, Href  string
	Pinned, Private, Current bool
	Counts                   TaskCounts
}

// taskPlaces: where the user's tasks are, for the switcher, each list named
// by its name, not by a page that embeds it: with several pinned lists
// Pinned (all of them as one, /tasks?pinned=1), then the pinned lists
// (their embeds' params and counts, wherever else they are declared), then
// each other list a page declares A–Z, each at /tasks?list= (the list with
// its params and the pages that embed it), and all tasks, each with its
// counts; in sharing mode not a list whose page is not for sharing. A
// single pin without a list is "Pinned", at its page. Nil when there is
// nothing to switch between (no pin, no list).
func (rq *Req) taskPlaces() []TaskPlace {
	user := rq.who()
	pinned, lists := rq.pinnedLists(), rq.taskLists()
	var out []TaskPlace
	tasks := openTasks(user)
	if len(pinned) > 1 {
		out = append(out, TaskPlace{Key: "pinned", Label: "Pinned", Title: "Your pinned lists, as one", Href: "/tasks?pinned=1",
			Pinned: true, Counts: countTasks(tasks, rq.pinnedView())})
	}
	isPinned := map[string]bool{}
	for _, h := range pinned {
		l := cleanList(h.Params.Get("list"))
		switch {
		case l != "":
			isPinned[l] = true
			out = append(out, TaskPlace{Key: "list:" + l, Label: l, Title: "Pinned list #" + l, Href: "/tasks?list=" + url.QueryEscape(l),
				Pinned: true, Private: ownPrivate(user, h.Slug), Counts: countTasks(tasks, h.Params)})
		case len(pinned) == 1:
			out = append(out, TaskPlace{Key: "pinned", Label: "Pinned", Title: "Your pinned tasks", Href: pathURL("page", h.Slug),
				Pinned: true, Private: ownPrivate(user, h.Slug), Counts: countTasks(tasks, h.Params)})
		}
	}
	for _, name := range sortedKeys(lists) {
		l := lists[name]
		if isPinned[name] || hiddenPage(rq, l.Slug) {
			continue
		}
		out = append(out, TaskPlace{Key: "list:" + name, Label: name, Title: "List #" + name,
			Href: "/tasks?list=" + url.QueryEscape(name), Private: ownPrivate(user, l.Slug), Counts: countTasks(tasks, l.Params.With("list", name))})
	}
	if len(out) == 0 {
		return nil
	}
	return append(out, TaskPlace{Key: "all", Label: "All", Title: "Every task", Href: "/tasks", Counts: countTasks(tasks)})
}

// placeCounts: each place's counts by its key (GET /api/tasks/due-count?places=1).
func (rq *Req) placeCounts() map[string]TaskCounts {
	out := map[string]TaskCounts{}
	for _, p := range rq.taskPlaces() {
		out[p.Key] = p.Counts
	}
	return out
}
