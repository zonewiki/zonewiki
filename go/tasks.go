// The tasks: the /api/tasks routes (their task writes as
// functions the MCP tools share), the [[tasks:]] embed's HTML, the module
// counts.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"zonewiki/internal/mdtext"
)

// apiError is a refused request: status and the reason tasks.js shows.
type apiError struct {
	status int
	msg    string
}

func (e *apiError) Error() string { return e.msg }

func badRequest(msg string) error { return &apiError{http.StatusBadRequest, msg} }

var errNotFound = &apiError{http.StatusNotFound, "Not Found"}

// tasksRoute answers a task list API request: a refusal as {"error": …} (400,
// 403 for a viewer's change to a shared task),
// 404, or 503 when a task list file is unreadable (the client retries).
func tasksRoute(h func(w http.ResponseWriter, r *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		err := h(w, r)
		var ae *apiError
		switch {
		case err == nil:
		case errors.Is(err, errTasksCorrupt):
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		case errors.As(err, &ae) && (ae.status == http.StatusBadRequest || ae.status == http.StatusForbidden):
			// tasks.js shows the error (a viewer's: readOnlyMsg).
			writeJSON(w, ae.status, map[string]string{"error": ae.msg})
		case errors.As(err, &ae):
			http.Error(w, ae.msg, ae.status)
		default:
			logf("%s %s: %v", r.Method, r.URL.Path, err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		}
	}
}

// jsonObject is the request's JSON body, which must be an object (any
// content type: text/plain bodies too).
func jsonObject(r *http.Request) (jsonObj, error) {
	body, err := io.ReadAll(r.Body)
	if tooLarge(err) {
		return nil, &apiError{http.StatusRequestEntityTooLarge, "Too large: at most " + strconv.Itoa(bodyMax>>20) + " MB"}
	}
	o, ok := parseObj(body)
	if !ok {
		return nil, badRequest("Request body must be a JSON object")
	}
	return o, nil
}

func asStr(raw json.RawMessage, name string) (string, error) {
	s, ok := as[string](raw)
	if !ok {
		return "", badRequest(name + " must be a string")
	}
	return s, nil
}

func asBool(raw json.RawMessage, name string) (bool, error) {
	b, ok := as[bool](raw)
	if !ok {
		return false, badRequest(name + " must be true or false")
	}
	return b, nil
}

// boolField: data's k as true or false; false when it is missing.
func boolField(data jsonObj, k string) (bool, error) {
	if !data.has(k) {
		return false, nil
	}
	return asBool(data[k], k)
}

// asInt: a whole number, or a string of one ("12", " -3 ").
func asInt(raw json.RawMessage, name string) (int, error) {
	if n, ok := wholeNumber(raw); ok {
		return n, nil
	}
	if s, ok := as[string](raw); ok {
		if n, err := strconv.Atoi(strings.TrimSpace(s)); err == nil {
			return n, nil
		}
	}
	return 0, badRequest(name + " must be an integer")
}

// asStrList: a list of strings, or one string split on commas; stripped,
// empties left out.
func asStrList(raw json.RawMessage, name string) ([]string, error) {
	items, ok := as[[]string](raw)
	if s, isStr := as[string](raw); isStr {
		items, ok = strings.Split(s, ","), true
	}
	if !ok {
		return nil, badRequest(name + " must be a list of strings")
	}
	out := []string{}
	for _, s := range items {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// listField: data's k as a list, [] when it is missing; ok false when it
// is something else.
func listField(data jsonObj, k string) ([]json.RawMessage, bool) {
	if !data.has(k) {
		return []json.RawMessage{}, true
	}
	return as[[]json.RawMessage](data[k])
}

// idList: the body's ids, a list of task-id strings.
func idList(data jsonObj) ([]string, error) {
	if !data.has("ids") {
		return []string{}, nil
	}
	ids, ok := as[[]string](data["ids"])
	if !ok {
		return nil, badRequest("ids must be a list of task ids")
	}
	return ids, nil
}

// strptime's %Y-%m-%d: four digits, then a month and a day of one or two.
var strptimeDayRE = regexp.MustCompile(`^(\d{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])$`)

// validateDate: raw as a date field: null or "" is none (""), else a
// valid YYYY-MM-DD.
func validateDate(raw json.RawMessage) (string, error) {
	s, ok := as[string](raw)
	if isNull(raw) || ok && s == "" {
		return "", nil
	}
	bad := badRequest("Invalid date format, expected YYYY-MM-DD")
	if !ok {
		return "", bad
	}
	m := strptimeDayRE.FindStringSubmatch(s)
	if m == nil {
		return "", bad
	}
	y, _ := strconv.Atoi(m[1])
	mo, _ := strconv.Atoi(m[2])
	d, _ := strconv.Atoi(strings.TrimSpace(m[3]))
	if d > daysIn(y, time.Month(mo)) {
		return "", bad
	}
	return s, nil
}

// commentText: a comment's text, stripped and capped; empty is refused.
func commentText(s string) (string, error) {
	if s = strings.TrimSpace(s); s == "" {
		return "", badRequest("Comment cannot be empty")
	}
	return runePrefix(s, commentMax), nil
}

// ---- the embed ----

const tasksPlaceholder = "LEAFTASKSPLACEHOLDER"

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
	listsJSON, homesJSON := jsonText(names), jsonText(homes)
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
	var ae *apiError
	pe, refused := refusalOf(err)
	switch {
	case err == nil:
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	case errors.As(err, &ae):
		writeJSON(w, ae.status, map[string]string{"error": ae.msg})
	case refused && pe.kind == refusedReadOnly:
		writeJSON(w, http.StatusForbidden, map[string]string{"error": pe.msg})
	case refused && pe.kind == refusedNotFound:
		writeJSON(w, http.StatusNotFound, map[string]string{"error": pe.msg})
	case refused:
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": pe.msg})
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

// ---- the API ----

func tasksRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/tasks", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, openTasks(userOf(r)))
		return nil
	}))
	// A hidden task app, for a page without one that wants its edit modal
	// (js/actions.js openDefaultTask: c t, the New task button).
	mux.HandleFunc("GET /api/tasks/headless", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		rq := newReq(r)
		_, _ = io.WriteString(w, buildTasksEmbed(rq, TasksParams{}, 0, true, rq.defaultPrivate(), ""))
	})
	mux.HandleFunc("POST /api/tasks/settings", writesPages(tasksSettingsRoute))
	mux.HandleFunc("GET /api/tasks/completed", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		year, err := yearArg(r)
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, completedInYear(userOf(r), year))
		return nil
	}))
	mux.HandleFunc("GET /api/tasks/completed-years", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, map[string]any{"years": completedYears(userOf(r), cleanList(r.URL.Query().Get("list")))})
		return nil
	}))
	mux.HandleFunc("GET /api/tasks/completed-count", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		scope, n := cleanList(r.URL.Query().Get("list")), 0
		for _, t := range completedInYear(userOf(r), currentYear()) {
			if inScope(t, scope) {
				n++
			}
		}
		writeJSON(w, http.StatusOK, map[string]int{"count": n})
		return nil
	}))
	mux.HandleFunc("GET /api/tasks/due-count", func(w http.ResponseWriter, r *http.Request) {
		// ?places=1: also each place's counts, for the list switcher.
		rq := newReq(r)
		if r.URL.Query().Get("places") == "" {
			writeJSON(w, http.StatusOK, taskCounts(rq))
			return
		}
		c := taskCounts(rq)
		writeJSON(w, http.StatusOK, map[string]any{"count": c.Count, "overdue": c.Overdue, "today": c.Today, "focus": c.Focus, "places": rq.placeCounts()})
	})
	mux.HandleFunc("POST /api/tasks", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		t, err := createTask(userOf(r), data, "Create task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, t)
		return nil
	}))
	mux.HandleFunc("PUT /api/tasks/{id}", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		t, err := updateTask(userOf(r), r.PathValue("id"), data, "")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, t)
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/{id}/toggle-check", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		idx, err := asInt(data["idx"], "idx")
		if err != nil {
			return err
		}
		if idx < 0 {
			return badRequest("idx must not be negative")
		}
		t, err := toggleNoteCheck(userOf(r), r.PathValue("id"), idx)
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, t)
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/{id}/comments", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		text, err := asStr(data["text"], "text")
		if err != nil {
			return err
		}
		t, err := addComment(userOf(r), r.PathValue("id"), text, "Comment on task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, t)
		return nil
	}))
	mux.HandleFunc("PUT /api/tasks/{id}/comments/{cid}", tasksRoute(apiEditComment))
	mux.HandleFunc("DELETE /api/tasks/{id}/comments/{cid}", tasksRoute(apiDeleteComment))
	mux.HandleFunc("DELETE /api/tasks/{id}", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		if err := deleteTask(userOf(r), r.PathValue("id"), "Delete task"); err != nil {
			return err
		}
		w.WriteHeader(http.StatusNoContent)
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/batch-delete", tasksRoute(apiBatchDelete))
	mux.HandleFunc("POST /api/tasks/restore", tasksRoute(apiRestore))
	mux.HandleFunc("GET /api/tasks/export", tasksRoute(apiExport))
	mux.HandleFunc("POST /api/tasks/import", tasksRoute(apiImport))
	mux.HandleFunc("POST /api/tasks/batch-complete", tasksRoute(apiBatchComplete))
	mux.HandleFunc("POST /api/tasks/reorder", tasksRoute(apiReorder))
	mux.HandleFunc("POST /api/tasks/batch-tag", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		ids, err := idList(data)
		if err != nil {
			return err
		}
		var tags [2][]string
		for i, k := range []string{"add", "remove"} {
			if _, ok := listField(data, k); !ok {
				return badRequest("add/remove must be lists of strings")
			}
			if data.has(k) {
				if tags[i], err = asStrList(data[k], k); err != nil {
					return err
				}
			}
		}
		tasks, err := tagTasks(userOf(r), ids, tags[0], tags[1], "Tag tasks")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, tasks)
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/batch-update", tasksRoute(apiBatchUpdate))
}

var yearArgRE = regexp.MustCompile(`^\d{4}$`)

// yearArg: ?year=YYYY (a year of the Completed view), default this year.
func yearArg(r *http.Request) (string, error) {
	year := r.URL.Query().Get("year")
	if year == "" {
		year = currentYear()
	}
	if !yearArgRE.MatchString(year) {
		return "", badRequest("year must be YYYY")
	}
	return year, nil
}

// createTask: a new open task from the fields in data (as the API takes
// them); the MCP tools share it.
func createTask(user string, data jsonObj, message string) (*Task, error) {
	title := ""
	if data.has("title") {
		var err error
		if title, err = asStr(data["title"], "title"); err != nil {
			return nil, err
		}
	}
	if strings.TrimSpace(title) == "" {
		return nil, badRequest("Title cannot be empty")
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	t := newTask()
	if err := applyTaskFields(user, t, data, true); err != nil {
		return nil, err
	}
	t.History = []HistoryEntry{} // its first dates are not changes
	logEvent(t, user, "created", "", "")
	active, err := readTasksStrict(user)
	if err != nil {
		return nil, err
	}
	return t, writeTasksLocked(user, append(active, t), message)
}

// applyTaskFields applies the field changes in data (not completion); fresh:
// t is a task being created, the only time it may start private.
func applyTaskFields(user string, t *Task, data jsonObj, fresh bool) error {
	oldDue := t.DueDate
	if data.has("title") {
		s, err := asStr(data["title"], "title")
		if err != nil {
			return err
		}
		if s = strings.TrimSpace(s); s == "" {
			return badRequest("Title cannot be empty")
		}
		t.Title = s
	}
	if data.has("description") {
		t.Description = ""
		if !data.null("description") {
			s, err := asStr(data["description"], "description")
			if err != nil {
				return err
			}
			t.Description = strings.TrimSpace(s)
		}
	}
	if data.has("tags") {
		t.Tags = []string{}
		if !data.null("tags") {
			tags, err := asStrList(data["tags"], "tags")
			if err != nil {
				return err
			}
			t.Tags = cleanTags(tags)
		}
	}
	if data.has("comments") { // the whole list, for undoing a comment's delete or edit
		if _, ok := as[[]json.RawMessage](data["comments"]); !ok {
			return badRequest("comments must be a list")
		}
		t.Comments = cleanComments(data["comments"])
		checkBy(user, t)
	}
	if data.has("due_date") {
		d, err := validateDate(data["due_date"])
		if err != nil {
			return err
		}
		t.DueDate = d
		if d != oldDue {
			logEvent(t, user, "due", oldDue, d)
		}
	}
	if data.has("sort_order") {
		f, ok := as[float64](data["sort_order"])
		switch {
		case data.null("sort_order"):
			t.SortOrder = nil
		case ok:
			t.SortOrder = &f
		default:
			return badRequest("sort_order must be a number")
		}
	}
	if data.has("keep") {
		keep, err := asBool(data["keep"], "keep")
		if err != nil {
			return err
		}
		t.Keep = keep
	}
	if data.has("private") { // a new task starts private, or a private one is shared; a shared one stays shared
		private, err := asBool(data["private"], "private")
		if err != nil {
			return err
		}
		if private && privateFolder(user) == "" {
			return badRequest("Only a signed-in user's tasks can be private")
		}
		if private && t.Owner == "" && !fresh {
			return badRequest("A shared task stays shared: others have seen it. Make a new private task and copy the text")
		}
		was := t.Owner
		t.Owner = ""
		if private {
			t.Owner = user
		}
		if t.Owner != was && !private {
			logEvent(t, user, "shared", "", "")
		}
	}
	if data.has("last_done") {
		d, err := validateDate(data["last_done"])
		if err != nil {
			return err
		}
		t.LastDone = d
	}
	oldStart := t.StartDate
	oldRec := t.Recurrence
	if oldRec == nil {
		oldRec = &Recurrence{}
	}
	if data.has("start_date") {
		d, err := validateDate(data["start_date"])
		if err != nil {
			return err
		}
		t.StartDate = d
		if d != oldStart {
			logEvent(t, user, "start", oldStart, d)
		}
	}
	if data.has("rec_type") {
		rtype, _ := as[string](data["rec_type"])
		switch rtype {
		case "daily", "weekly", "monthly", "yearly":
			n := 1
			if data.has("rec_interval") {
				var err error
				if n, err = asInt(data["rec_interval"], "rec_interval"); err != nil {
					return err
				}
			}
			n = max(1, n)
			if n > recIntervalMax {
				return badRequest("rec_interval must be at most " + strconv.Itoa(recIntervalMax))
			}
			from := "due"
			if s, _ := as[string](data["rec_from"]); s == "completion" {
				from = "completion"
			}
			rec := &Recurrence{Type: rtype, Interval: n, From: from}
			// Re-saving the modal re-sends the recurrence: keep the series'
			// day of month unless its type or primary date changed …
			oldPrimary := firstNonEmpty(oldDue, oldStart)
			newPrimary := firstNonEmpty(t.DueDate, t.StartDate)
			if oldRec.Day > 0 && oldRec.Type == rtype && newPrimary == oldPrimary {
				rec.Day = oldRec.Day
			}
			// … and a clamped start's remembered gap while neither date changed.
			if oldRec.Lead != nil && t.DueDate == oldDue && t.StartDate == oldStart {
				lead := *oldRec.Lead
				rec.Lead = &lead
			}
			t.Recurrence = rec
		default:
			t.Recurrence = nil
		}
	}
	// A date changed by hand defines the gap from now on; moving the primary
	// date re-anchors the series' day of month.
	if rec := t.Recurrence; rec != nil {
		if rec.Lead != nil && (t.DueDate != oldDue || t.StartDate != oldStart) {
			rec.Lead = nil
		}
		if rec.Day > 0 && firstNonEmpty(t.DueDate, t.StartDate) != firstNonEmpty(oldDue, oldStart) {
			rec.Day = 0
		}
	}
	// An undo sends the whole old recurrence, day and lead included.
	if data.has("recurrence") {
		rec := cleanRecurrence(data["recurrence"])
		t.Recurrence = rec
		if rec == nil && !data.null("recurrence") {
			return badRequest("recurrence must be null or have a type")
		}
	}
	// Keep and Repeat answer the same question: a keep task never repeats.
	if t.Keep {
		t.Recurrence = nil
	}
	return nil
}

// firstNonEmpty: a, or b when a is empty.
func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// findForWriteLocked: the open tasks and every completed task user sees,
// and the one with id (404 when none of them has it).
func findForWriteLocked(user, id string) (active, completed []*Task, task *Task, err error) {
	if active, completed, err = readForWriteLocked(user); err != nil {
		return
	}
	for _, t := range slices.Concat(active, completed) {
		if t.ID == id {
			return active, completed, t, nil
		}
	}
	return nil, nil, nil, errNotFound
}

func without(tasks []*Task, id string) []*Task { return withoutIDs(tasks, map[string]bool{id: true}) }

// withoutIDs: tasks but those with an id in ids.
func withoutIDs(tasks []*Task, ids map[string]bool) []*Task {
	out := make([]*Task, 0, len(tasks))
	for _, t := range tasks {
		if !ids[t.ID] {
			out = append(out, t)
		}
	}
	return out
}

// updateTask changes a task's fields and status (complete, won't do, skip,
// reopen) from data as the API takes it; suffix follows the commit message
// (the MCP tools' summary).
func updateTask(user, id string, data jsonObj, suffix string) (*Task, error) {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	return updateTaskLocked(user, id, data, suffix)
}

func updateTaskLocked(user, id string, data jsonObj, suffix string) (*Task, error) {
	var status [3]bool
	for i, k := range []string{"skip", "wont_do", "completed"} {
		var err error
		if status[i], err = boolField(data, k); err != nil {
			return nil, err
		}
	}
	skip, wontDo, done := status[0], status[1], status[2]
	active, completed, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	rememberBy(t) // an undo sends its comments and history back (taskby.go)
	if err := applyTaskFields(user, t, data, false); err != nil {
		return nil, err
	}
	didSkip := skip && t.isOpen()
	switch {
	case didSkip:
		// Skip this occurrence: archived as won't-do, the next one spawned
		// (a plain won't-do ends the series).
		logEvent(t, user, "skipped", "", "")
		spawned := spawnRecurring(user, t, active, true)
		t.CompletedAt = timestamp()
		t.WontDo = true
		active = without(active, id)
		completed = append(completed, t)
		if spawned != nil {
			active = append(active, spawned)
		}
	case data.has("wont_do"):
		if wontDo && t.isOpen() {
			// Won't do: to completed, abandoned; no next occurrence.
			t.CompletedAt = timestamp()
			t.WontDo = true
			logEvent(t, user, "wontdo", "", "")
			active = without(active, id)
			completed = append(completed, t)
		} else if !wontDo && !t.isOpen() {
			// Reopen (also takes away what a skip spawned).
			active = uncompleteTask(user, t, active, nil)
			completed = without(completed, id)
			active = append(active, t)
		}
	case data.has("completed"):
		switch {
		case done && t.Keep && t.isOpen():
			keepDone(user, t) // stays open, back in the pool
		case done && t.isOpen():
			spawned := completeTask(user, t, "", active)
			active = without(active, id)
			completed = append(completed, t)
			if spawned != nil {
				active = append(active, spawned)
			}
		case !done && !t.isOpen():
			active = uncompleteTask(user, t, active, nil)
			completed = without(completed, id)
			active = append(active, t)
		}
	}
	// An undo sends the old history back with the old fields.
	if data.has("history") {
		t.History = cleanHistory(data["history"])
		checkBy(user, t)
	}
	msg := "Update task"
	switch {
	case didSkip:
		msg = "Skip task"
	case data.has("wont_do"):
		msg = "Abandon task"
		if !wontDo {
			msg = "Reopen task"
		}
	case data.has("completed"):
		msg = "Complete task"
		if !done {
			msg = "Uncomplete task"
		}
	}
	return t, writeBothLocked(user, active, completed, msg+suffix)
}

// toggleNoteCheck ticks or unticks the Nth checkbox of a task's notes.
func toggleNoteCheck(user, id string, idx int) (*Task, error) {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	_, _, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	text := t.Description
	toggled := mdtext.ToggleNthCheckbox(text, idx, false, now())
	if toggled == text {
		return t, nil
	}
	data := jsonObj{}
	data.set("description", toggled)
	return updateTaskLocked(user, id, data, "")
}

// addComment adds a comment (a status line) to a task, open or done.
func addComment(user, id string, text string, message string) (*Task, error) {
	s, err := commentText(text)
	if err != nil {
		return nil, err
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	t.Comments = append(cleanCommentList(t.Comments),
		Comment{ID: newCommentID(), At: now().Format("2006-01-02T15:04"), By: user, Text: s})
	return t, writeBothLocked(user, active, completed, message)
}

// taskComment: the index of a task's comment by id (its comments cleaned
// first).
func taskComment(t *Task, cid string) (int, error) {
	t.Comments = cleanCommentList(t.Comments)
	for i, c := range t.Comments {
		if c.ID == cid {
			return i, nil
		}
	}
	return 0, errNotFound
}

func apiEditComment(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	text, err := asStr(data["text"], "text")
	if err != nil {
		return err
	}
	if text, err = commentText(text); err != nil {
		return err
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, t, err := findForWriteLocked(userOf(r), r.PathValue("id"))
	if err != nil {
		return err
	}
	i, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	t.Comments[i].Text = text
	if err := writeBothLocked(userOf(r), active, completed, "Edit task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, t)
	return nil
}

func apiDeleteComment(w http.ResponseWriter, r *http.Request) error {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, t, err := findForWriteLocked(userOf(r), r.PathValue("id"))
	if err != nil {
		return err
	}
	i, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	rememberBy(t) // the undo sends it back
	t.Comments = slices.Delete(t.Comments, i, i+1)
	if err := writeBothLocked(userOf(r), active, completed, "Delete task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, t)
	return nil
}

func deleteTask(user, id, message string) error {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	return deleteTasksLocked(user, map[string]bool{id: true}, message)
}

func deleteTasksLocked(user string, ids map[string]bool, message string) error {
	active, completed, err := readForWriteLocked(user)
	if err != nil {
		return err
	}
	for _, t := range slices.Concat(active, completed) {
		if ids[t.ID] {
			rememberBy(t) // the undo restores it
		}
	}
	return writeBothLocked(user, withoutIDs(active, ids), withoutIDs(completed, ids), message)
}

func idSet(ids []string) map[string]bool {
	set := map[string]bool{}
	for _, id := range ids {
		set[id] = true
	}
	return set
}

func apiBatchDelete(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	ids, err := idList(data)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return badRequest("Bad Request")
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	if err := deleteTasksLocked(userOf(r), idSet(ids), "Delete tasks"); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// apiRestore re-inserts task snapshots (undo after delete), their ids and
// dates kept; ids that exist already are skipped.
func apiRestore(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	snaps, ok := as[[]json.RawMessage](data["tasks"])
	if !ok || len(snaps) == 0 {
		return badRequest("Bad Request")
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	user := userOf(r)
	active, completed, err := readForWriteLocked(user)
	if err != nil {
		return err
	}
	existing := taskIDsLocked() // every owner's: ids stay unique across the files
	restored := []*Task{}
	for _, s := range snaps {
		snap, ok := as[jsonObj](s)
		if !ok {
			continue
		}
		t := sanitizeSnapshot(snap)
		if t == nil || existing[t.ID] {
			continue
		}
		checkBy(user, t)
		if jsonTrue(snap["private"]) && privateFolder(user) != "" {
			t.Owner = user
		}
		existing[t.ID] = true
		if !t.isOpen() {
			completed = append(completed, t)
		} else {
			active = append(active, t)
		}
		restored = append(restored, t)
	}
	if len(restored) > 0 {
		if err := writeBothLocked(user, active, completed, "Restore tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusCreated, restored)
	return nil
}

// ---- export and import ----

// importMax: the most tasks one import takes.
const importMax = 10000

// importIDRE: an id an imported task may keep (a UUID, as Zone makes them).
var importIDRE = regexp.MustCompile(`^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$`)

// apiExport: the tasks a list shows (?list=, ?tag=, as an embed's params;
// neither: every task the user sees), open and completed of every year,
// as a file to save: {zone_tasks: 1, list, tag, exported_at, tasks}, each
// task as the API shows it. apiImport reads it back.
func apiExport(w http.ResponseWriter, r *http.Request) error {
	list, tag := cleanList(r.URL.Query().Get("list")), strings.TrimSpace(r.URL.Query().Get("tag"))
	tasksMu.Lock()
	active, completed, err := readForWriteLocked(userOf(r))
	tasksMu.Unlock()
	if err != nil {
		return err
	}
	tasks := []*Task{}
	for _, t := range slices.Concat(active, completed) {
		if inScope(t, list) && tagParamMatches(t, tag) {
			tasks = append(tasks, t)
		}
	}
	name := firstNonEmpty(list, "all")
	// The list's name is the request's: as a quoted, escaped parameter, so
	// a " in it can't end the file name and add one of its own.
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment",
		map[string]string{"filename": "zone-tasks-" + name + "-" + isoDay(today()) + ".json"}))
	writeJSON(w, http.StatusOK, map[string]any{"zone_tasks": 1, "list": list, "tag": tag, "exported_at": timestamp(), "tasks": tasks})
	return nil
}

// apiImport adds an export's tasks: {file, list, tag, new, also, private}. file
// is what apiExport wrote (or a bare list of tasks); list, tag and new are
// the params of the list imported into, whose tags the tasks get as a new
// task there would (newTaskTags, or new=), in place of the tag of the list
// they were exported from; private: into the user's own tasks, where a
// task exported private goes anyway. A task
// keeps its id, and one whose id is here already is skipped, so the same
// file imported twice adds nothing. also: more tags for them, the user's
// own for this import. Answers {added, skipped, ids}: ids, the added
// tasks', for the notice's Undo (batch-delete).
func apiImport(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	user := userOf(r)
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	notExport := badRequest("Not a file of tasks exported from Zone")
	snaps, ok := as[[]json.RawMessage](data["file"])
	from := ""
	if !ok {
		file, isObj := as[jsonObj](data["file"])
		if snaps, ok = as[[]json.RawMessage](file["tasks"]); !isObj || !ok {
			return notExport
		}
		s, _ := as[string](file["list"])
		from = cleanList(s)
	}
	if len(snaps) > importMax {
		return badRequest("Too many tasks: at most " + strconv.Itoa(importMax) + " in one import")
	}
	str := func(k string) string { s, _ := as[string](data[k]); return s }
	add := cleanTags(strings.Split(str("new"), ","))
	if len(add) == 0 {
		add = newTaskTags(parseTasksParams("list=" + cleanList(str("list")) + " tag=" + strings.Join(strings.Fields(str("tag")), "")))
	}
	// also: tags of the user's own for this import, to find its tasks by.
	add = cleanTags(append(add, strings.Split(str("also"), ",")...))
	var remove []string
	if from != "" && !slices.ContainsFunc(add, func(t string) bool { return strings.EqualFold(t, from) }) {
		remove = []string{from}
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, err := readForWriteLocked(user)
	if err != nil {
		return err
	}
	existing := taskIDsLocked() // every owner's: ids stay unique across the files
	skipped, ids := 0, []string{}
	for _, s := range snaps {
		snap, _ := as[jsonObj](s)
		t := sanitizeSnapshot(snap)
		if t == nil {
			return notExport
		}
		if id, _ := as[string](snap["id"]); !importIDRE.MatchString(id) {
			t.ID = uuid4()
		}
		if existing[t.ID] {
			skipped++
			continue
		}
		// An admin's import keeps the file's names (a wiki moved, a
		// backup put back); anyone else's only their own.
		if !settings().isAdmin(user) {
			checkBy(user, t)
		}
		existing[t.ID] = true
		t.Tags = retag(t.Tags, add, remove)
		// A task exported private stays private, wherever it is imported.
		if (jsonTrue(data["private"]) || jsonTrue(snap["private"])) && privateFolder(user) != "" {
			t.Owner = user
		}
		if t.isOpen() {
			active = append(active, t)
		} else {
			completed = append(completed, t)
		}
		ids = append(ids, t.ID)
	}
	if len(ids) > 0 {
		if err := writeBothLocked(user, active, completed, "Import "+strconv.Itoa(len(ids))+" tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"added": len(ids), "skipped": skipped, "ids": ids})
	return nil
}

func apiBatchComplete(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	ids, err := idList(data)
	if err != nil {
		return err
	}
	markCompleted := true
	if data.has("completed") {
		if markCompleted, err = asBool(data["completed"], "completed"); err != nil {
			return err
		}
	}
	if len(ids) == 0 {
		return badRequest("Bad Request")
	}
	set := idSet(ids)
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, err := readForWriteLocked(userOf(r))
	if err != nil {
		return err
	}
	stamp := timestamp()
	updated := []*Task{}
	if markCompleted {
		for _, t := range active {
			if set[t.ID] && t.Keep && t.isOpen() {
				keepDone(userOf(r), t) // stays open, back in the pool
				updated = append(updated, t)
			}
		}
		moving, movingIDs := selectTasks(active, func(t *Task) bool { return set[t.ID] && t.isOpen() && !t.Keep })
		var spawned []*Task
		for _, t := range moving {
			if s := completeTask(userOf(r), t, stamp, active); s != nil {
				spawned = append(spawned, s)
			}
			updated = append(updated, t)
		}
		active = append(withoutIDs(active, movingIDs), spawned...)
		completed = append(completed, moving...)
	} else {
		moving, movingIDs := selectTasks(completed, func(t *Task) bool { return set[t.ID] && !t.isOpen() })
		for _, t := range moving {
			active = uncompleteTask(userOf(r), t, active, set)
			updated = append(updated, t)
		}
		completed = withoutIDs(completed, movingIDs)
		active = append(active, moving...)
	}
	msg := "Reopen tasks"
	if markCompleted {
		msg = "Complete tasks"
	}
	if err := writeBothLocked(userOf(r), active, completed, msg); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, updated)
	return nil
}

// selectTasks: the tasks that pass keep, and their ids.
func selectTasks(tasks []*Task, keep func(*Task) bool) ([]*Task, map[string]bool) {
	var out []*Task
	ids := map[string]bool{}
	for _, t := range tasks {
		if keep(t) {
			out = append(out, t)
			ids[t.ID] = true
		}
	}
	return out, ids
}

func apiReorder(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	order, ok1 := listField(data, "order")
	rawUpdates, ok2 := listField(data, "updates")
	updates := make([]jsonObj, len(rawUpdates))
	for i, u := range rawUpdates {
		var isObj bool
		if updates[i], isObj = as[jsonObj](u); !isObj {
			ok2 = false
		}
	}
	if !ok1 || !ok2 {
		return badRequest("order must be a list of ids, updates a list of objects")
	}
	if len(order) == 0 && len(updates) == 0 {
		return badRequest("Bad Request")
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, err := readTasksStrict(userOf(r))
	if err != nil {
		return err
	}
	byID := map[string]*Task{}
	for _, t := range active {
		byID[t.ID] = t
	}
	for i, id := range order {
		if s, ok := as[string](id); ok && byID[s] != nil {
			f := float64(i)
			byID[s].SortOrder = &f
		}
	}
	for _, u := range updates {
		id, _ := as[string](u["id"])
		t := byID[id]
		// null clears it: the task leaves the manual order.
		if u.null("sort_order") && t != nil {
			t.SortOrder = nil
			continue
		}
		if f, ok := as[float64](u["sort_order"]); ok && t != nil {
			t.SortOrder = &f
		}
	}
	if err := writeTasksLocked(userOf(r), active, "Reorder tasks"); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// tagTasks adds the tags add to the tasks ids and takes remove off them
// (case ignored; a tag a task has keeps its spelling), open and completed,
// in one commit; the batch-tag route and the MCP tools share it.
func tagTasks(user string, ids, add, remove []string, message string) ([]*Task, error) {
	set := idSet(ids)
	addTags, removeTags := cleanTags(add), cleanTags(remove)
	if len(set) == 0 || len(addTags) == 0 && len(removeTags) == 0 {
		return nil, badRequest("Give task ids and tags to add or remove")
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, err := readForWriteLocked(user)
	if err != nil {
		return nil, err
	}
	updated := []*Task{}
	for _, t := range slices.Concat(active, completed) {
		if !set[t.ID] {
			continue
		}
		t.Tags = retag(t.Tags, addTags, removeTags)
		updated = append(updated, t)
	}
	return updated, writeBothLocked(user, active, completed, message)
}

// apiBatchUpdate changes fields of several tasks in one commit: tasks is a
// list of {id, …fields as PUT takes them}; status changes are refused, ids
// no longer there skipped.
func apiBatchUpdate(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	raw, ok := as[[]json.RawMessage](data["tasks"])
	valid := ok && len(raw) > 0
	changes := make([]jsonObj, len(raw))
	for i, c := range raw {
		o, isObj := as[jsonObj](c)
		if _, isStr := as[string](o["id"]); !isObj || !isStr {
			valid = false
			break
		}
		changes[i] = o
	}
	if !valid {
		return badRequest("tasks must be a list of objects with an id")
	}
	for _, c := range changes {
		for _, k := range []string{"completed", "wont_do", "skip"} {
			if c.has(k) {
				return badRequest("batch-update changes fields, not status")
			}
		}
	}
	tasksMu.Lock()
	defer tasksMu.Unlock()
	active, completed, err := readForWriteLocked(userOf(r))
	if err != nil {
		return err
	}
	byID := map[string]*Task{}
	for _, t := range slices.Concat(active, completed) {
		byID[t.ID] = t
	}
	updated := []*Task{}
	for _, change := range changes {
		id, _ := as[string](change["id"])
		t := byID[id]
		if t == nil {
			continue
		}
		rememberBy(t)
		if err := applyTaskFields(userOf(r), t, change, false); err != nil {
			return err
		}
		if change.has("history") {
			t.History = cleanHistory(change["history"])
			checkBy(userOf(r), t)
		}
		updated = append(updated, t)
	}
	if len(updated) > 0 {
		if err := writeBothLocked(userOf(r), active, completed, "Update tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusOK, updated)
	return nil
}
