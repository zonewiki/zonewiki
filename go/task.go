// A task: its fields as Zone knows them, and whatever else a file
// gave it (keys of older versions, or added by hand), kept as it was.
package main

import (
	"bytes"
	"encoding/json"
	"strconv"
	"zonewiki/internal/jsonobj"
)

type Task struct {
	ID          string
	Title       string
	Description string // the notes (markdown)
	Tags        []string
	// Dates as YYYY-MM-DD, timestamps as YYYY-MM-DDTHH:MM:SS; "" is none
	// (null in the file).
	StartDate, DueDate     string
	CreatedAt, CompletedAt string
	WontDo                 bool // completed as won't do
	Keep                   bool // a keep task: done returns it to the pool
	LastDone               string
	SortOrder              *float64 // its place in the manual order; nil: none
	Recurrence             *Recurrence
	SpawnedFrom            string // the occurrence this one follows
	History                []HistoryEntry
	Comments               []Comment
	Extra                  map[string]json.RawMessage // keys Zone doesn't know, as the file had them
	// Owner: the user whose private task it is, "" a shared one. Not in
	// the file: it is whose files the task was read from (tasksDir), and a
	// write files it there; the API shows it as "private".
	Owner string
}

// Recurrence: a task's series. Day (monthly, yearly) is the day of month
// it returns to, 0 when not anchored; Lead the remembered gap between a
// clamped start and the due date.
type Recurrence struct {
	Type     string // daily, weekly, monthly, yearly
	Interval int    // every N
	From     string // "due" (the dates) or "completion"
	Day      int
	Lead     *int
}

// HistoryEntry: what happened to a task (event), when (at) and by whom (by:
// a user, with users; "" none); the dated events (start, due, repeat) say
// from and to which day ("" none).
type HistoryEntry struct {
	At, Event, By, From, To string
}

// Comment: a status line on a task; By is who wrote it (with users; ""
// none, and not in the file).
type Comment struct {
	ID, At, By, Text string
}

var taskKeys = map[string]bool{"id": true, "title": true, "description": true, "tags": true,
	"start_date": true, "due_date": true, "created_at": true, "completed_at": true, "wont_do": true,
	"keep": true, "last_done": true, "sort_order": true, "recurrence": true, "spawned_from": true,
	"history": true, "comments": true, "private": true}

// strField: a field as text: a string as it is, a number or a bool as its
// JSON text (an id of 7 is "7"), anything else "" (a file edited by hand).
func strField(o jsonObj, k string) string {
	s, _ := jsonobj.ScalarText(o[k])
	return s
}

// textList: a list's items as text (scalarText), the others left out.
func textList(raw json.RawMessage) []string {
	l, _ := jsonobj.As[[]json.RawMessage](raw)
	var out []string
	for _, x := range l {
		if s, ok := jsonobj.ScalarText(x); ok {
			out = append(out, s)
		}
	}
	return out
}

// taskFromJSON reads a task from a file's object, leniently: a value of the
// wrong kind is its field's default, never an error, so an odd file still
// reads (and a write doesn't refuse it).
func taskFromJSON(o jsonObj) *Task {
	t := &Task{ID: strField(o, "id"), Title: strField(o, "title"), Description: strField(o, "description"),
		StartDate: strField(o, "start_date"), DueDate: strField(o, "due_date"),
		CreatedAt: strField(o, "created_at"), CompletedAt: strField(o, "completed_at"),
		WontDo: jsonobj.True(o["wont_do"]), Keep: jsonobj.True(o["keep"]), LastDone: strField(o, "last_done"),
		SpawnedFrom: strField(o, "spawned_from"), History: []HistoryEntry{}, Comments: []Comment{}}
	t.Tags = cleanTags(textList(o["tags"])) // "local llm", "#work" from a file edited by hand
	if f, ok := jsonobj.As[float64](o["sort_order"]); ok {
		t.SortOrder = &f
	}
	if rec, ok := jsonobj.As[jsonObj](o["recurrence"]); ok && len(rec) > 0 {
		t.Recurrence = recurrenceFromJSON(rec)
	}
	for _, e := range objList(o["history"]) {
		t.History = append(t.History, HistoryEntry{At: strField(e, "at"), Event: strField(e, "event"),
			By: strField(e, "by"), From: strField(e, "from"), To: strField(e, "to")})
	}
	for _, c := range objList(o["comments"]) {
		t.Comments = append(t.Comments, Comment{ID: strField(c, "id"), At: strField(c, "at"), By: strField(c, "by"),
			Text: strField(c, "text")})
	}
	for k, v := range o {
		if !taskKeys[k] {
			if t.Extra == nil {
				t.Extra = map[string]json.RawMessage{}
			}
			t.Extra[k] = v
		}
	}
	return t
}

// objList: a list's objects, the other items left out.
func objList(raw json.RawMessage) []jsonObj {
	l, _ := jsonobj.As[[]json.RawMessage](raw)
	var out []jsonObj
	for _, x := range l {
		if o, ok := jsonobj.As[jsonObj](x); ok {
			out = append(out, o)
		}
	}
	return out
}

// recurrenceFromJSON: a series as stored; a missing or bad type is daily,
// a missing or bad interval 1.
func recurrenceFromJSON(rec jsonObj) *Recurrence {
	r := &Recurrence{Type: "daily", Interval: 1, From: "due"}
	if s, ok := jsonobj.As[string](rec["type"]); ok && s != "" {
		r.Type = s
	}
	if n, ok := jsonobj.WholeNumber(rec["interval"]); ok && n >= 1 {
		r.Interval = min(n, recIntervalMax)
	}
	if s, _ := jsonobj.As[string](rec["from"]); s == "completion" {
		r.From = "completion"
	}
	if n, ok := jsonobj.WholeNumber(rec["day"]); ok && n >= 1 && n <= 31 {
		r.Day = n
	}
	if n, ok := jsonobj.WholeNumber(rec["lead"]); ok && n >= 0 {
		r.Lead = &n
	}
	return r
}

// nullable: "" as null.
func nullable(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func (r *Recurrence) MarshalJSON() ([]byte, error) {
	return jsonobj.MarshalText(struct {
		Type     string `json:"type"`
		Interval int    `json:"interval"`
		From     string `json:"from"`
		Day      int    `json:"day,omitempty"`
		Lead     *int   `json:"lead,omitempty"`
	}{r.Type, r.Interval, r.From, r.Day, r.Lead})
}

// MarshalJSON: the dated events say from and to which day (null none),
// the others neither; by only when someone is named, so a wiki without
// users keeps its files as they were.
func (e HistoryEntry) MarshalJSON() ([]byte, error) {
	if !datedEvents[e.Event] {
		return jsonobj.MarshalText(struct {
			At    string `json:"at"`
			Event string `json:"event"`
			By    string `json:"by,omitempty"`
		}{e.At, e.Event, e.By})
	}
	return jsonobj.MarshalText(struct {
		At    string  `json:"at"`
		Event string  `json:"event"`
		By    string  `json:"by,omitempty"`
		From  *string `json:"from"`
		To    *string `json:"to"`
	}{e.At, e.Event, e.By, nullable(e.From), nullable(e.To)})
}

// MarshalJSON: by only when someone is named, so a wiki without users
// keeps its files as they were.
func (c Comment) MarshalJSON() ([]byte, error) {
	return jsonobj.MarshalText(struct {
		ID   string `json:"id"`
		At   string `json:"at"`
		By   string `json:"by,omitempty"`
		Text string `json:"text"`
	}{c.ID, c.At, c.By, c.Text})
}

// MarshalJSON: a task as the API and the browser get it: as in its file
// and, with users, "private" (whether it is its user's own).
func (t *Task) MarshalJSON() ([]byte, error) { return t.marshal(settings().Users != nil) }

// fileTask: a task as its file keeps it.
type fileTask struct{ *Task }

func (f fileTask) MarshalJSON() ([]byte, error) { return f.marshal(false) }

// marshal writes the fields in the order the files keep (private after
// them), the keys Zone doesn't know last, A–Z.
func (t *Task) marshal(private bool) ([]byte, error) {
	tags, history, comments := t.Tags, t.History, t.Comments
	if tags == nil {
		tags = []string{}
	}
	if history == nil {
		history = []HistoryEntry{}
	}
	if comments == nil {
		comments = []Comment{}
	}
	b, err := jsonobj.MarshalText(struct {
		ID          string         `json:"id"`
		Title       string         `json:"title"`
		Description string         `json:"description"`
		Tags        []string       `json:"tags"`
		StartDate   *string        `json:"start_date"`
		DueDate     *string        `json:"due_date"`
		CreatedAt   *string        `json:"created_at"`
		CompletedAt *string        `json:"completed_at"`
		WontDo      bool           `json:"wont_do"`
		Keep        bool           `json:"keep"`
		LastDone    *string        `json:"last_done"`
		SortOrder   *float64       `json:"sort_order"`
		Recurrence  *Recurrence    `json:"recurrence"`
		SpawnedFrom *string        `json:"spawned_from"`
		History     []HistoryEntry `json:"history"`
		Comments    []Comment      `json:"comments"`
	}{t.ID, t.Title, t.Description, tags, nullable(t.StartDate), nullable(t.DueDate),
		nullable(t.CreatedAt), nullable(t.CompletedAt), t.WontDo, t.Keep, nullable(t.LastDone),
		t.SortOrder, t.Recurrence, nullable(t.SpawnedFrom), history, comments})
	if err == nil && private {
		b = append(b[:len(b)-1], `,"private":`+strconv.FormatBool(t.Owner != "")+"}"...)
	}
	if err != nil || len(t.Extra) == 0 {
		return b, err
	}
	b = b[:len(b)-1] // the closing }
	for _, k := range sortedKeys(t.Extra) {
		key, _ := jsonobj.MarshalText(k)
		b = append(append(append(append(b, ','), key...), ':'), t.Extra[k]...)
	}
	return append(b, '}'), nil
}

// encodeTasks: a task list file's text, two spaces of indent, text as it is
// (no \u escapes but those JSON needs).
func encodeTasks(tasks []*Task) (string, error) {
	file := make([]fileTask, len(tasks))
	for i, t := range tasks {
		file[i] = fileTask{t}
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(file); err != nil {
		return "", err
	}
	return b.String(), nil
}

// isOpen: not completed (nor won't do).
func (t *Task) isOpen() bool { return t.CompletedAt == "" }
