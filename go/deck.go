// The deck: the /api/deck routes (their task writes as
// functions the MCP tools share), the [[deck]] embed's HTML, the module
// counts.
package main

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"regexp"
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
func jsonObject(r *http.Request) (*Obj, error) {
	body, _ := io.ReadAll(r.Body)
	v, err := loads(body)
	o, ok := v.(*Obj)
	if err != nil || !ok {
		return nil, badRequest("Request body must be a JSON object")
	}
	return o, nil
}

func asStr(v any, name string) (string, error) {
	s, ok := v.(string)
	if !ok {
		return "", badRequest(name + " must be a string")
	}
	return s, nil
}

// asInt is Python's int(): numbers and numeric strings, not a bool.
func asInt(v any, name string) (int, error) {
	bad := badRequest(name + " must be an integer")
	switch v := v.(type) {
	case int64:
		return int(v), nil
	case float64:
		if v != v || v > 1e18 || v < -1e18 {
			return 0, bad
		}
		return int(v), nil
	case string:
		// int("  -12 "), int("1_000"): a sign, digits, single underscores between.
		s := pyStrip(v)
		neg := strings.HasPrefix(s, "-")
		s = strings.TrimPrefix(strings.TrimPrefix(s, "-"), "+")
		if !pyIntRE.MatchString(s) {
			return 0, bad
		}
		n := pyAtoi(strings.ReplaceAll(s, "_", ""))
		if neg {
			n = -n
		}
		return n, nil
	}
	return 0, bad
}

var pyIntRE = regexp.MustCompile(`^` + pyDigit + `+(?:_` + pyDigit + `+)*$`)

// asStrList: a list of strings, or one string split on commas; stripped,
// empties left out.
func asStrList(v any, name string) ([]string, error) {
	var items []any
	switch v := v.(type) {
	case string:
		for _, s := range strings.Split(v, ",") {
			items = append(items, s)
		}
	case []any:
		items = v
	default:
		return nil, badRequest(name + " must be a list of strings")
	}
	out := []string{}
	for _, it := range items {
		s, ok := it.(string)
		if !ok {
			return nil, badRequest(name + " must be a list of strings")
		}
		if s = pyStrip(s); s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// idList: the body's ids, a list of task-id strings.
func idList(data *Obj) ([]string, error) {
	v, ok := data.Lookup("ids")
	if !ok {
		return []string{}, nil
	}
	l, isList := v.([]any)
	if !isList {
		return nil, badRequest("ids must be a list of task ids")
	}
	out := []string{}
	for _, x := range l {
		s, ok := x.(string)
		if !ok {
			return nil, badRequest("ids must be a list of task ids")
		}
		out = append(out, s)
	}
	return out, nil
}

// strptime's %Y-%m-%d: four digits, then a month and a day of one or two.
var strptimeDayRE = regexp.MustCompile(`^(\d{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])$`)

// validateDate: v as a date field: falsy is nil, else a valid YYYY-MM-DD.
func validateDate(v any) (any, error) {
	if !truthyValue(v) {
		return nil, nil
	}
	bad := badRequest("Invalid date format, expected YYYY-MM-DD")
	s, ok := v.(string)
	if !ok {
		return nil, bad
	}
	m := strptimeDayRE.FindStringSubmatch(s)
	if m == nil {
		return nil, bad
	}
	y, _ := strconv.Atoi(m[1])
	mo, _ := strconv.Atoi(m[2])
	d, _ := strconv.Atoi(strings.TrimSpace(m[3]))
	if d > daysIn(y, time.Month(mo)) {
		return nil, bad
	}
	return s, nil
}

func commentText(v any) (string, error) {
	s, err := asStr(v, "text")
	if err != nil {
		return "", err
	}
	if s = pyStrip(s); s == "" {
		return "", badRequest("Comment cannot be empty")
	}
	return runePrefix(s, commentMax), nil
}

// ---- the embed ----

const deckPlaceholder = "LEAFDECKPLACEHOLDER"

// buildDeckEmbed renders the deck app for a page (instance numbers a
// page's embeds); headless hides it but for its edit modal (the editor's
// Make task, c t on any page), and deck.js skips its shortcuts.
func buildDeckEmbed(params DeckParams, instance int, headless bool) string {
	// limit=N: the WIP limit (Focus warning, Plan day flags).
	limit := ""
	if n, err := strconv.Atoi(pyStrip(params.Get("limit"))); err == nil {
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
		if name = runePrefix(pyStrip(strings.ReplaceAll(name, "_", " ")), 30); name != "" {
			labels[i] = name
		}
	}
	lists := deckLists()
	names = sortedKeys(lists)
	homes := NewObj()
	for _, n := range names {
		h := lists[n].Title
		if h == "" {
			h = lists[n].Slug
		}
		homes.Set(n, h)
	}
	listsJSON, homesJSON := dumpsLine(strs(names)), dumpsLine(homes)
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
		"lists_json": listsJSON, "homes_json": homesJSON,
	})
	if err != nil {
		logf("deck embed: %v", err)
	}
	return b.String()
}

type deckEmbed struct{ placeholder, html string }

// processDeckEmbeds swaps each [[deck …]] for a placeholder; the embeds'
// HTML replaces them after rendering (injectDeckEmbeds).
func processDeckEmbeds(rq *Req, text string) (string, []deckEmbed) {
	var embeds []deckEmbed
	text = deckRE.ReplaceAllStringFunc(text, func(m string) string {
		g := deckRE.FindStringSubmatch(m)
		ph := deckPlaceholder + "_" + strconv.Itoa(len(embeds))
		embeds = append(embeds, deckEmbed{ph, buildDeckEmbed(parseDeckParams(g[1]), len(embeds), false)})
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
	for _, t := range readDeck() {
		if !inScope(t, scope) || !tagParamMatches(t, tag) {
			continue
		}
		due, start := t.Str("due_date"), t.Str("start_date")
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
		writeJSON(w, http.StatusOK, objList(readDeck()))
		return nil
	}))
	// A hidden deck app, for a page without one that wants its edit modal
	// (js/actions.js openHomeTask: c t, the New task button).
	mux.HandleFunc("GET /api/deck/headless", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(w, buildDeckEmbed(DeckParams{}, 0, true))
	})
	mux.HandleFunc("GET /api/deck/completed", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		year, err := yearArg(r)
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, objList(completedInYear(year)))
		return nil
	}))
	mux.HandleFunc("GET /api/deck/completed-years", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, map[string]any{"years": completedYears(cleanList(r.URL.Query().Get("list")))})
		return nil
	}))
	mux.HandleFunc("GET /api/deck/completed-count", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		scope, n := cleanList(r.URL.Query().Get("list")), 0
		for _, t := range completedInYear(currentYear()) {
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
		t, err := createTask(data, "Create deck task")
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
		t, err := updateTask(r.PathValue("id"), data, "")
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
		idx, err := asInt(data.Get("idx"), "idx")
		if err != nil {
			return err
		}
		if idx < 0 {
			return badRequest("idx must not be negative")
		}
		t, err := toggleNoteCheck(r.PathValue("id"), idx)
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
		t, err := addComment(r.PathValue("id"), data.Get("text"), "Comment on deck task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, t)
		return nil
	}))
	mux.HandleFunc("PUT /api/deck/{id}/comments/{cid}", deckRoute(apiEditComment))
	mux.HandleFunc("DELETE /api/deck/{id}/comments/{cid}", deckRoute(apiDeleteComment))
	mux.HandleFunc("DELETE /api/deck/{id}", deckRoute(func(w http.ResponseWriter, r *http.Request) error {
		if err := deleteTask(r.PathValue("id"), "Delete deck task"); err != nil {
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
		add, rem := orDefault(data, "add", []any{}), orDefault(data, "remove", []any{})
		if _, ok := add.([]any); !ok {
			return badRequest("add/remove must be lists of strings")
		}
		if _, ok := rem.([]any); !ok {
			return badRequest("add/remove must be lists of strings")
		}
		addTags, err := asStrList(add, "add")
		if err != nil {
			return err
		}
		remTags, err := asStrList(rem, "remove")
		if err != nil {
			return err
		}
		tasks, err := tagTasks(ids, addTags, remTags, "Tag deck tasks")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, objList(tasks))
		return nil
	}))
	mux.HandleFunc("POST /api/deck/batch-update", deckRoute(apiBatchUpdate))
}

// yearArg: ?year=YYYY (a year of the Completed view), default this year.
func yearArg(r *http.Request) (string, error) {
	year := r.URL.Query().Get("year")
	if year == "" {
		year = currentYear()
	}
	if !regexp.MustCompile(`^\d{4}$`).MatchString(year) {
		return "", badRequest("year must be YYYY")
	}
	return year, nil
}

// createTask: a new open task from the fields in data (as the API takes
// them); the MCP tools share it.
func createTask(data *Obj, message string) (*Obj, error) {
	title, err := asStr(orDefault(data, "title", ""), "title")
	if err != nil {
		return nil, err
	}
	if pyStrip(title) == "" {
		return nil, badRequest("Title cannot be empty")
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	t := newTask()
	if err := applyTaskFields(t, data); err != nil {
		return nil, err
	}
	t.Set("history", []any{}) // its first dates are not changes
	logEvent(t, "created", nil, nil)
	active, err := readDeckStrict()
	if err != nil {
		return nil, err
	}
	return t, writeDeckLocked(append(active, t), message)
}

// applyTaskFields applies the field changes in data (not completion).
func applyTaskFields(t *Obj, data *Obj) error {
	oldDue := t.Get("due_date")
	if data.Has("title") {
		s, err := asStr(data.Get("title"), "title")
		if err != nil {
			return err
		}
		if s = pyStrip(s); s == "" {
			return badRequest("Title cannot be empty")
		}
		t.Set("title", s)
	}
	if data.Has("description") {
		s, err := asStr(orNone(data.Get("description")), "description")
		if err != nil {
			return err
		}
		t.Set("description", pyStrip(s))
	}
	if data.Has("tags") {
		v := data.Get("tags")
		if !truthyValue(v) {
			v = []any{}
		}
		tags, err := asStrList(v, "tags")
		if err != nil {
			return err
		}
		t.Set("tags", strs(cleanTagStrings(tags)))
	}
	if data.Has("comments") { // the whole list, for undoing a comment's delete or edit
		if _, ok := data.Get("comments").([]any); !ok {
			return badRequest("comments must be a list")
		}
		t.Set("comments", cleanComments(data.Get("comments")))
	}
	if data.Has("due_date") {
		d, err := validateDate(data.Get("due_date"))
		if err != nil {
			return err
		}
		t.Set("due_date", d)
		if !pyEqual(d, oldDue) {
			logEvent(t, "due", oldDue, d)
		}
	}
	if data.Has("sort_order") {
		v := data.Get("sort_order")
		switch v.(type) {
		case nil, int64, float64:
		default:
			return badRequest("sort_order must be a number")
		}
		t.Set("sort_order", v)
	}
	if data.Has("keep") {
		t.Set("keep", data.Truthy("keep"))
	}
	if data.Has("last_done") {
		d, err := validateDate(data.Get("last_done"))
		if err != nil {
			return err
		}
		t.Set("last_done", d)
	}
	oldStart := t.Get("start_date")
	oldRec, _ := t.Get("recurrence").(*Obj)
	if oldRec == nil {
		oldRec = NewObj()
	}
	if data.Has("start_date") {
		d, err := validateDate(data.Get("start_date"))
		if err != nil {
			return err
		}
		t.Set("start_date", d)
		if !pyEqual(d, oldStart) {
			logEvent(t, "start", oldStart, d)
		}
	}
	if data.Has("rec_type") {
		rtype, _ := data.Get("rec_type").(string)
		switch rtype {
		case "daily", "weekly", "monthly", "yearly":
			n, err := asInt(orDefault(data, "rec_interval", int64(1)), "rec_interval")
			if err != nil {
				return err
			}
			n = max(1, n)
			if n > recIntervalMax {
				return badRequest("rec_interval must be at most " + strconv.Itoa(recIntervalMax))
			}
			from := "due"
			if pyEqual(data.Get("rec_from"), "completion") {
				from = "completion"
			}
			rec := NewObj("type", rtype, "interval", int64(n), "from", from)
			// Re-saving the modal re-sends the recurrence: keep the series'
			// day of month unless its type or primary date changed …
			oldPrimary := firstTruthy(oldDue, oldStart)
			newPrimary := firstTruthy(t.Get("due_date"), t.Get("start_date"))
			if oldRec.Truthy("day") && pyEqual(oldRec.Get("type"), rtype) && pyEqual(newPrimary, oldPrimary) {
				rec.Set("day", oldRec.Get("day"))
			}
			// … and a clamped start's remembered gap while neither date changed.
			if oldRec.Get("lead") != nil && pyEqual(t.Get("due_date"), oldDue) && pyEqual(t.Get("start_date"), oldStart) {
				rec.Set("lead", oldRec.Get("lead"))
			}
			t.Set("recurrence", rec)
		default:
			t.Set("recurrence", nil)
		}
	}
	// A date changed by hand defines the gap from now on; moving the primary
	// date re-anchors the series' day of month.
	if rec, ok := t.Get("recurrence").(*Obj); ok && rec.Len() > 0 {
		if rec.Has("lead") && (!pyEqual(t.Get("due_date"), oldDue) || !pyEqual(t.Get("start_date"), oldStart)) {
			rec.Delete("lead")
		}
		if rec.Has("day") && !pyEqual(firstTruthy(t.Get("due_date"), t.Get("start_date")), firstTruthy(oldDue, oldStart)) {
			rec.Delete("day")
		}
	}
	// An undo sends the whole old recurrence, day and lead included.
	if data.Has("recurrence") {
		rec := cleanRecurrence(data.Get("recurrence"))
		if rec == nil {
			t.Set("recurrence", nil)
			if data.Get("recurrence") != nil {
				return badRequest("recurrence must be null or have a type")
			}
		} else {
			t.Set("recurrence", rec)
		}
	}
	// Keep and Repeat answer the same question: a keep task never repeats.
	if t.Truthy("keep") {
		t.Set("recurrence", nil)
	}
	return nil
}

// firstTruthy is Python's a or b.
func firstTruthy(a, b any) any {
	if truthyValue(a) {
		return a
	}
	return b
}

// findForWriteLocked: the open tasks, every completed task, and the one
// with id (404 when none has it).
func findForWriteLocked(id string) (active, completed []*Obj, task *Obj, err error) {
	if active, err = readDeckStrict(); err != nil {
		return
	}
	if completed, err = readCompletedAllStrict(); err != nil {
		return
	}
	for _, t := range append(append([]*Obj{}, active...), completed...) {
		if t.Str("id") == id {
			return active, completed, t, nil
		}
	}
	return nil, nil, nil, errNotFound
}

func without(tasks []*Obj, id string) []*Obj {
	out := make([]*Obj, 0, len(tasks))
	for _, t := range tasks {
		if t.Str("id") != id {
			out = append(out, t)
		}
	}
	return out
}

// updateTask changes a task's fields and status (complete, won't do, skip,
// reopen) from data as the API takes it; suffix follows the commit message
// (the MCP tools' summary).
func updateTask(id string, data *Obj, suffix string) (*Obj, error) {
	deckMu.Lock()
	defer deckMu.Unlock()
	return updateTaskLocked(id, data, suffix)
}

func updateTaskLocked(id string, data *Obj, suffix string) (*Obj, error) {
	active, completed, t, err := findForWriteLocked(id)
	if err != nil {
		return nil, err
	}
	if err := applyTaskFields(t, data); err != nil {
		return nil, err
	}
	didSkip := data.Truthy("skip") && !t.Truthy("completed_at")
	switch {
	case didSkip:
		// Skip this occurrence: archived as won't-do, the next one spawned
		// (a plain won't-do ends the series).
		logEvent(t, "skipped", nil, nil)
		spawned := spawnRecurring(t, active, true)
		t.Set("completed_at", timestamp())
		t.Set("wont_do", true)
		active = without(active, id)
		completed = append(completed, t)
		if spawned != nil {
			active = append(active, spawned)
		}
	case data.Has("wont_do"):
		if data.Truthy("wont_do") && !t.Truthy("completed_at") {
			// Won't do: to completed, abandoned; no next occurrence.
			t.Set("completed_at", timestamp())
			t.Set("wont_do", true)
			logEvent(t, "wontdo", nil, nil)
			active = without(active, id)
			completed = append(completed, t)
		} else if !data.Truthy("wont_do") && t.Truthy("completed_at") {
			// Reopen (also takes away what a skip spawned).
			active = uncompleteTask(t, active, nil)
			completed = without(completed, id)
			active = append(active, t)
		}
	case data.Has("completed"):
		switch {
		case data.Truthy("completed") && t.Truthy("keep") && !t.Truthy("completed_at"):
			keepDone(t) // stays open, back in the pool
		case data.Truthy("completed") && !t.Truthy("completed_at"):
			spawned := completeTask(t, "", active)
			active = without(active, id)
			completed = append(completed, t)
			if spawned != nil {
				active = append(active, spawned)
			}
		case !data.Truthy("completed") && t.Truthy("completed_at"):
			active = uncompleteTask(t, active, nil)
			completed = without(completed, id)
			active = append(active, t)
		}
	}
	// An undo sends the old history back with the old fields.
	if data.Has("history") {
		t.Set("history", cleanHistory(data.Get("history")))
	}
	msg := "Update deck task"
	switch {
	case didSkip:
		msg = "Skip deck task"
	case data.Has("wont_do"):
		msg = "Abandon deck task"
		if !data.Truthy("wont_do") {
			msg = "Reopen deck task"
		}
	case data.Has("completed"):
		msg = "Complete deck task"
		if !data.Truthy("completed") {
			msg = "Uncomplete deck task"
		}
	}
	return t, writeBothLocked(active, completed, msg+suffix)
}

// toggleNoteCheck ticks or unticks the Nth checkbox of a task's notes.
func toggleNoteCheck(id string, idx int) (*Obj, error) {
	deckMu.Lock()
	defer deckMu.Unlock()
	_, _, t, err := findForWriteLocked(id)
	if err != nil {
		return nil, err
	}
	text := pyStr(orNone(t.Get("description")))
	toggled := toggleNthCheckbox(text, idx, false)
	if toggled == text {
		return t, nil
	}
	return updateTaskLocked(id, NewObj("description", toggled), "")
}

// addComment adds a comment (a status line) to a task, open or done.
func addComment(id string, text any, message string) (*Obj, error) {
	s, err := commentText(text)
	if err != nil {
		return nil, err
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	active, completed, t, err := findForWriteLocked(id)
	if err != nil {
		return nil, err
	}
	t.Set("comments", append(cleanComments(t.Get("comments")),
		NewObj("id", newCommentID(), "at", now().Format("2006-01-02T15:04"), "text", s)))
	return t, writeBothLocked(active, completed, message)
}

// taskComment: a task's comment by id (its comments cleaned first).
func taskComment(t *Obj, cid string) (*Obj, error) {
	comments := cleanComments(t.Get("comments"))
	t.Set("comments", comments)
	for _, c := range comments {
		if c.(*Obj).Str("id") == cid {
			return c.(*Obj), nil
		}
	}
	return nil, errNotFound
}

func apiEditComment(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	text, err := commentText(data.Get("text"))
	if err != nil {
		return err
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	active, completed, t, err := findForWriteLocked(r.PathValue("id"))
	if err != nil {
		return err
	}
	c, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	c.Set("text", text)
	if err := writeBothLocked(active, completed, "Edit deck task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, t)
	return nil
}

func apiDeleteComment(w http.ResponseWriter, r *http.Request) error {
	deckMu.Lock()
	defer deckMu.Unlock()
	active, completed, t, err := findForWriteLocked(r.PathValue("id"))
	if err != nil {
		return err
	}
	c, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	var kept []any
	for _, x := range t.List("comments") {
		if x != any(c) {
			kept = append(kept, x)
		}
	}
	t.Set("comments", listOrEmpty(kept))
	if err := writeBothLocked(active, completed, "Delete deck task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, t)
	return nil
}

func deleteTask(id, message string) error {
	deckMu.Lock()
	defer deckMu.Unlock()
	return deleteTasksLocked(map[string]bool{id: true}, message)
}

func deleteTasksLocked(ids map[string]bool, message string) error {
	active, err := readDeckStrict()
	if err != nil {
		return err
	}
	completed, err := readCompletedAllStrict()
	if err != nil {
		return err
	}
	keep := func(tasks []*Obj) []*Obj {
		out := []*Obj{}
		for _, t := range tasks {
			if !ids[t.Str("id")] {
				out = append(out, t)
			}
		}
		return out
	}
	return writeBothLocked(keep(active), keep(completed), message)
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
	if err := deleteTasksLocked(idSet(ids), "Delete deck tasks"); err != nil {
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
	snaps, ok := data.Get("tasks").([]any)
	if !ok || len(snaps) == 0 {
		return badRequest("Bad Request")
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	active, err := readDeckStrict()
	if err != nil {
		return err
	}
	completed, err := readCompletedAllStrict()
	if err != nil {
		return err
	}
	existing := map[string]bool{}
	for _, t := range append(append([]*Obj{}, active...), completed...) {
		existing[t.Str("id")] = true
	}
	restored := []*Obj{}
	for _, s := range snaps {
		snap, ok := s.(*Obj)
		if !ok {
			continue
		}
		t := sanitizeSnapshot(snap)
		if t == nil || existing[t.Str("id")] {
			continue
		}
		existing[t.Str("id")] = true
		if t.Truthy("completed_at") {
			completed = append(completed, t)
		} else {
			active = append(active, t)
		}
		restored = append(restored, t)
	}
	if len(restored) > 0 {
		if err := writeBothLocked(active, completed, "Restore deck tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusCreated, objList(restored))
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
	markCompleted := truthyValue(orDefault(data, "completed", true))
	if len(ids) == 0 {
		return badRequest("Bad Request")
	}
	set := idSet(ids)
	deckMu.Lock()
	defer deckMu.Unlock()
	active, err := readDeckStrict()
	if err != nil {
		return err
	}
	completed, err := readCompletedAllStrict()
	if err != nil {
		return err
	}
	stamp := timestamp()
	updated := []*Obj{}
	if markCompleted {
		for _, t := range active {
			if set[t.Str("id")] && t.Truthy("keep") && !t.Truthy("completed_at") {
				keepDone(t) // stays open, back in the pool
				updated = append(updated, t)
			}
		}
		var moving, spawned []*Obj
		movingIDs := map[string]bool{}
		for _, t := range active {
			if set[t.Str("id")] && !t.Truthy("completed_at") && !t.Truthy("keep") {
				moving = append(moving, t)
				movingIDs[t.Str("id")] = true
			}
		}
		for _, t := range moving {
			if s := completeTask(t, stamp, active); s != nil {
				spawned = append(spawned, s)
			}
			updated = append(updated, t)
		}
		var rest []*Obj
		for _, t := range active {
			if !movingIDs[t.Str("id")] {
				rest = append(rest, t)
			}
		}
		active = append(rest, spawned...)
		completed = append(completed, moving...)
	} else {
		var moving []*Obj
		movingIDs := map[string]bool{}
		for _, t := range completed {
			if set[t.Str("id")] && t.Truthy("completed_at") {
				moving = append(moving, t)
				movingIDs[t.Str("id")] = true
			}
		}
		for _, t := range moving {
			active = uncompleteTask(t, active, set)
			updated = append(updated, t)
		}
		var rest []*Obj
		for _, t := range completed {
			if !movingIDs[t.Str("id")] {
				rest = append(rest, t)
			}
		}
		completed = rest
		active = append(active, moving...)
	}
	msg := "Reopen deck tasks"
	if markCompleted {
		msg = "Complete deck tasks"
	}
	if err := writeBothLocked(nonNil(active), nonNil(completed), msg); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, objList(updated))
	return nil
}

func nonNil(tasks []*Obj) []*Obj {
	if tasks == nil {
		return []*Obj{}
	}
	return tasks
}

func apiReorder(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	order, ok1 := orDefault(data, "order", []any{}).([]any)
	updates, ok2 := orDefault(data, "updates", []any{}).([]any)
	allObjs := true
	for _, u := range updates {
		if _, ok := u.(*Obj); !ok {
			allObjs = false
		}
	}
	if !ok1 || !ok2 || !allObjs {
		return badRequest("order must be a list of ids, updates a list of objects")
	}
	if len(order) == 0 && len(updates) == 0 {
		return badRequest("Bad Request")
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	active, err := readDeckStrict()
	if err != nil {
		return err
	}
	byID := map[string]*Obj{}
	for _, t := range active {
		byID[t.Str("id")] = t
	}
	for i, id := range order {
		if s, ok := id.(string); ok && byID[s] != nil {
			byID[s].Set("sort_order", int64(i))
		}
	}
	for _, x := range updates {
		u := x.(*Obj)
		id, _ := u.Get("id").(string)
		t := byID[id]
		val := u.Get("sort_order")
		// null clears it: the task leaves the manual order.
		if val == nil && t != nil {
			t.Set("sort_order", nil)
			continue
		}
		switch val.(type) {
		case int64, float64:
			if t != nil {
				t.Set("sort_order", val)
			}
		}
	}
	if err := writeDeckLocked(active, "Reorder deck"); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// tagTasks adds the tags add to the tasks ids and takes remove off them
// (case ignored; a tag a task has keeps its spelling), open and completed,
// in one commit; the batch-tag route and the MCP tools share it.
func tagTasks(ids, add, remove []string, message string) ([]*Obj, error) {
	set := idSet(ids)
	addTags := cleanTagStrings(add)
	removeTags := map[string]bool{}
	for _, t := range cleanTagStrings(remove) {
		removeTags[strings.ToLower(t)] = true
	}
	if len(set) == 0 || len(addTags) == 0 && len(removeTags) == 0 {
		return nil, badRequest("Give task ids and tags to add or remove")
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	active, err := readDeckStrict()
	if err != nil {
		return nil, err
	}
	completed, err := readCompletedAllStrict()
	if err != nil {
		return nil, err
	}
	updated := []*Obj{}
	for _, t := range append(append([]*Obj{}, active...), completed...) {
		if !set[t.Str("id")] {
			continue
		}
		current := tagList(t)
		have := map[string]bool{}
		for _, c := range current {
			have[strings.ToLower(c)] = true
		}
		for _, tag := range addTags {
			if !have[strings.ToLower(tag)] {
				current = append(current, tag)
				have[strings.ToLower(tag)] = true
			}
		}
		kept := []string{}
		for _, c := range current {
			if !removeTags[strings.ToLower(c)] {
				kept = append(kept, c)
			}
		}
		t.Set("tags", strs(kept))
		updated = append(updated, t)
	}
	return updated, writeBothLocked(active, completed, message)
}

// apiBatchUpdate changes fields of several tasks in one commit: tasks is a
// list of {id, …fields as PUT takes them}; status changes are refused, ids
// no longer there skipped.
func apiBatchUpdate(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	changes, ok := data.Get("tasks").([]any)
	valid := ok && len(changes) > 0
	for _, c := range changes {
		o, isObj := c.(*Obj)
		if !isObj {
			valid = false
			break
		}
		if _, isStr := o.Get("id").(string); !isStr {
			valid = false
		}
	}
	if !valid {
		return badRequest("tasks must be a list of objects with an id")
	}
	for _, c := range changes {
		for _, k := range []string{"completed", "wont_do", "skip"} {
			if c.(*Obj).Has(k) {
				return badRequest("batch-update changes fields, not status")
			}
		}
	}
	deckMu.Lock()
	defer deckMu.Unlock()
	active, err := readDeckStrict()
	if err != nil {
		return err
	}
	completed, err := readCompletedAllStrict()
	if err != nil {
		return err
	}
	byID := map[string]*Obj{}
	for _, t := range append(append([]*Obj{}, active...), completed...) {
		byID[t.Str("id")] = t
	}
	updated := []*Obj{}
	for _, c := range changes {
		change := c.(*Obj)
		t := byID[change.Str("id")]
		if t == nil {
			continue
		}
		if err := applyTaskFields(t, change); err != nil {
			return err
		}
		if change.Has("history") {
			t.Set("history", cleanHistory(change.Get("history")))
		}
		updated = append(updated, t)
	}
	if len(updated) > 0 {
		if err := writeBothLocked(active, completed, "Update deck tasks"); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusOK, objList(updated))
	return nil
}
