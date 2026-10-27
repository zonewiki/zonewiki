// The deck: the /api/deck routes (their task writes as
// functions the MCP tools share), the [[deck]] embed's HTML, the module
// counts.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

// apiError is a refused request: status and the reason deck.js shows.
type apiError struct {
	status int
	msg    string
}

func (e *apiError) Error() string { return e.msg }

func badRequest(msg string) error { return &apiError{http.StatusBadRequest, msg} }

var errNotFound = &apiError{http.StatusNotFound, "Not Found"}

// deckRoute answers a deck API request: a refusal as {"error": …} (400),
// 404, or 503 when a deck file is unreadable (the client retries).
func deckRoute(h func(w http.ResponseWriter, r *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		err := h(w, r)
		var ae *apiError
		switch {
		case err == nil:
		case errors.Is(err, errDeckCorrupt):
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		case errors.As(err, &ae) && ae.status == http.StatusBadRequest:
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": ae.msg})
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
	body, _ := io.ReadAll(r.Body)
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

const deckPlaceholder = "LEAFDECKPLACEHOLDER"

// buildDeckEmbed renders the deck app for user on a page (instance numbers
// a page's embeds); headless hides it but for its edit modal (the editor's
// Make task, c t on any page), and deck.js skips its shortcuts. private:
// a new task here is private (a page in user's own folder).
func buildDeckEmbed(user string, params DeckParams, instance int, headless, private bool) string {
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
	lists := deckLists(user)
	names = sortedKeys(lists)
	privateNames := sortedKeys(privateLists(lists, user))
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
	err := partials.ExecuteTemplate(&b, "deck_embed", map[string]any{
		"tag": params.Get("tag"), "list": cleanList(params.Get("list")), "view": embedView(params),
		"limit": limit, "labels": labels, "instance_id": instance, "sfx": sfx, "headless": headless,
		// keep=1: new tasks here are keep tasks; home=1: the user's own deck.
		"keep": keep, "home": isTrue(params.Get("home")),
		// With users, the modal's Private box; ticked for a new task here.
		"can_private": privateFolder(user) != "", "private": private && privateFolder(user) != "",
		"private_folder": privateFolder(user), "private_lists_json": jsonText(privateNames),
		"lists_json": listsJSON, "homes_json": homesJSON,
	})
	if err != nil {
		logf("deck embed: %v", err)
	}
	return b.String()
}

type deckEmbed struct{ placeholder, html string }

// processDeckEmbeds swaps each [[deck …]] for a placeholder; the embeds'
// HTML replaces them after rendering (injectDeckEmbeds). private: the page
// is in rq's user's own folder.
func processDeckEmbeds(rq *Req, text string, private bool) (string, []deckEmbed) {
	var embeds []deckEmbed
	text = deckRE.ReplaceAllStringFunc(text, func(m string) string {
		g := deckRE.FindStringSubmatch(m)
		ph := deckPlaceholder + "_" + strconv.Itoa(len(embeds))
		embeds = append(embeds, deckEmbed{ph, buildDeckEmbed(rq.who(), parseDeckParams(g[1]), len(embeds), false, private)})
		return ph
	})
	return text, embeds
}

// injectDeckEmbeds: the last first, as _1 is a prefix of _10.
func injectDeckEmbeds(html string, embeds []deckEmbed) string {
	for i := len(embeds) - 1; i >= 0; i-- {
		e := embeds[i]
		html = strings.ReplaceAll(html, "<p>"+e.placeholder+"</p>", e.html)
		html = strings.ReplaceAll(html, e.placeholder, e.html)
	}
	return html
}

// DeckCounts is what the Deck module links show.
type DeckCounts struct {
	Count   int    `json:"count"`
	Overdue int    `json:"overdue"`
	Today   int    `json:"today"`
	Focus   int    `json:"focus"`
	Title   string `json:"-"`
}

// deckCounts: the open tasks in Focus (started, or due today or earlier)
// and of those how many are overdue or due today; of the deck home's view
// (its list= and tag=) when a page declares one.
func deckCounts(rq *Req) DeckCounts {
	day := isoDay(today())
	var params DeckParams
	if h := rq.deckHome(); h != nil {
		params = h.Params
	}
	scope, tag := cleanList(params.Get("list")), params.Get("tag")
	var c DeckCounts
	for _, t := range readDeck(rq.who()) {
		if !inScope(t, scope) || !tagParamMatches(t, tag) {
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

// ---- the API ----

func deckRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/deck", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, readDeck(userOf(r)))
		return nil
	}))
	// A hidden deck app, for a page without one that wants its edit modal
	// (js/actions.js openHomeTask: c t, the New task button).
	mux.HandleFunc("GET /api/deck/headless", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		rq := newReq(r)
		_, _ = io.WriteString(w, buildDeckEmbed(rq.user, DeckParams{}, 0, true, rq.homePrivate()))
	})
	mux.HandleFunc("GET /api/deck/completed", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		year, err := yearArg(r)
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, completedInYear(userOf(r), year))
		return nil
	}))
	mux.HandleFunc("GET /api/deck/completed-years", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, map[string]any{"years": completedYears(userOf(r), cleanList(r.URL.Query().Get("list")))})
		return nil
	}))
	mux.HandleFunc("GET /api/deck/completed-count", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		scope, n := cleanList(r.URL.Query().Get("list")), 0
		for _, t := range completedInYear(userOf(r), currentYear()) {
			if inScope(t, scope) {
				n++
			}
		}
		writeJSON(w, http.StatusOK, map[string]int{"count": n})
		return nil
	}))
	mux.HandleFunc("GET /api/deck/due-count", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, deckCounts(newReq(r)))
	})
	mux.HandleFunc("POST /api/deck", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		t, err := createTask(userOf(r), data, "Create deck task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, t)
		return nil
	}))
	mux.HandleFunc("PUT /api/deck/{id}", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
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
	mux.HandleFunc("POST /api/deck/{id}/toggle-check", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
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
	mux.HandleFunc("POST /api/deck/{id}/comments", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		text, err := asStr(data["text"], "text")
		if err != nil {
			return err
		}
		t, err := addComment(userOf(r), r.PathValue("id"), text, "Comment on deck task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, t)
		return nil
	}))
	mux.HandleFunc("PUT /api/deck/{id}/comments/{cid}", deckRoute(apiEditComment))
	mux.HandleFunc("DELETE /api/deck/{id}/comments/{cid}", deckRoute(apiDeleteComment))
	mux.HandleFunc("DELETE /api/deck/{id}", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		if err := deleteTask(userOf(r), r.PathValue("id"), "Delete deck task"); err != nil {
			return err
		}
		w.WriteHeader(http.StatusNoContent)
		return nil
	}))
	mux.HandleFunc("POST /api/deck/batch-delete", deckRoute(apiBatchDelete))
	mux.HandleFunc("POST /api/deck/restore", deckRoute(apiRestore))
	mux.HandleFunc("POST /api/deck/batch-complete", deckRoute(apiBatchComplete))
	mux.HandleFunc("POST /api/deck/reorder", deckRoute(apiReorder))
	mux.HandleFunc("POST /api/deck/batch-tag", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
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
		tasks, err := tagTasks(userOf(r), ids, tags[0], tags[1], "Tag deck tasks")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, tasks)
		return nil
	}))
	mux.HandleFunc("POST /api/deck/batch-update", deckRoute(apiBatchUpdate))
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
	deckMu.Lock()
	defer deckMu.Unlock()
	t := newTask()
	if err := applyTaskFields(user, t, data); err != nil {
		return nil, err
	}
	t.History = []HistoryEntry{} // its first dates are not changes
	// Unless the request says, a new task in one of the user's private lists is private.
	if !data.has("private") && hasPrivateList(user, t.Tags) {
		t.Owner = user
	}
	logEvent(t, user, "created", "", "")
	active, err := readDeckStrict(user)
	if err != nil {
		return nil, err
	}
	return t, writeDeckLocked(user, append(active, t), message)
}

// applyTaskFields applies the field changes in data (not completion).
func applyTaskFields(user string, t *Task, data jsonObj) error {
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
	if data.has("private") { // moves it into the user's own files, or out to the shared ones
		private, err := asBool(data["private"], "private")
		if err != nil {
			return err
		}
		if private && privateFolder(user) == "" {
			return badRequest("Only a signed-in user's tasks can be private")
		}
		t.Owner = ""
		if private {
			t.Owner = user
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

// firstNonEmpty is Python's a or b, of two strings.
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
	deckMu.Lock()
	defer deckMu.Unlock()
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
	if err := applyTaskFields(user, t, data); err != nil {
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
	}
	msg := "Update deck task"
	switch {
	case didSkip:
		msg = "Skip deck task"
	case data.has("wont_do"):
		msg = "Abandon deck task"
		if !wontDo {
			msg = "Reopen deck task"
		}
	case data.has("completed"):
		msg = "Complete deck task"
		if !done {
			msg = "Uncomplete deck task"
		}
	}
	return t, writeBothLocked(user, active, completed, msg+suffix)
}

// toggleNoteCheck ticks or unticks the Nth checkbox of a task's notes.
func toggleNoteCheck(user, id string, idx int) (*Task, error) {
	deckMu.Lock()
	defer deckMu.Unlock()
	_, _, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	text := t.Description
	toggled := toggleNthCheckbox(text, idx, false)
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
	deckMu.Lock()
	defer deckMu.Unlock()
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
	deckMu.Lock()
	defer deckMu.Unlock()
	active, completed, t, err := findForWriteLocked(userOf(r), r.PathValue("id"))
	if err != nil {
		return err
	}
	i, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	t.Comments[i].Text = text
	if err := writeBothLocked(userOf(r), active, completed, "Edit deck task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, t)
	return nil
}

func apiDeleteComment(w http.ResponseWriter, r *http.Request) error {
	deckMu.Lock()
	defer deckMu.Unlock()
	active, completed, t, err := findForWriteLocked(userOf(r), r.PathValue("id"))
	if err != nil {
		return err
	}
	i, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	t.Comments = slices.Delete(t.Comments, i, i+1)
	if err := writeBothLocked(userOf(r), active, completed, "Delete deck task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, t)
	return nil
}

func deleteTask(user, id, message string) error {
	deckMu.Lock()
	defer deckMu.Unlock()
	return deleteTasksLocked(user, map[string]bool{id: true}, message)
}

func deleteTasksLocked(user string, ids map[string]bool, message string) error {
	active, completed, err := readForWriteLocked(user)
	if err != nil {
		return err
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
	deckMu.Lock()
	defer deckMu.Unlock()
	if err := deleteTasksLocked(userOf(r), idSet(ids), "Delete deck tasks"); err != nil {
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
	deckMu.Lock()
	defer deckMu.Unlock()
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
		if err := writeBothLocked(user, active, completed, "Restore deck tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusCreated, restored)
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
	deckMu.Lock()
	defer deckMu.Unlock()
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
	msg := "Reopen deck tasks"
	if markCompleted {
		msg = "Complete deck tasks"
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
	deckMu.Lock()
	defer deckMu.Unlock()
	active, err := readDeckStrict(userOf(r))
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
	if err := writeDeckLocked(userOf(r), active, "Reorder deck"); err != nil {
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
	deckMu.Lock()
	defer deckMu.Unlock()
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
	deckMu.Lock()
	defer deckMu.Unlock()
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
		if err := applyTaskFields(userOf(r), t, change); err != nil {
			return err
		}
		if change.has("history") {
			t.History = cleanHistory(change["history"])
		}
		updated = append(updated, t)
	}
	if len(updated) > 0 {
		if err := writeBothLocked(userOf(r), active, completed, "Update deck tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusOK, updated)
	return nil
}
