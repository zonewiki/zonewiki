// Package taskmodel is a task as Zonewiki knows it: its fields, how a task list
// file is read (leniently) and written (in a fixed key order), and a
// series' dates. Nothing here knows the wiki, its users or the clock: the
// files and their lock are package taskfiles', the commits the server's.
package taskmodel

import (
	"bytes"
	"encoding/json"
	"errors"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
)

type Task struct {
	ID          string
	Title       string
	Description string // the notes (markdown)
	Tags        []string
	// List: the list the task is in, by its name; "" none (and no key in the
	// file). Which list of that name is the task's file's to say: a task in
	// a user's files is in one of their lists, any other in a shared one.
	List string
	// Assignee: the user the task is assigned to, "" no one (and no key in
	// the file). A name, whether or not it still is a user who can be
	// assigned: the server decides what a name that isn't counts as.
	Assignee string
	// CreatedBy: the user who made the task, "" unknown (and no key in the
	// file): a wiki without users, or a task written by hand.
	CreatedBy string
	// Followers: the users who follow the task (no key in the file when
	// there are none): they are told of its news as who made it is, without
	// it being theirs to do. Names, each once, in the order they came.
	Followers []string
	// Dates as YYYY-MM-DD, timestamps as YYYY-MM-DDTHH:MM:SS; "" is none
	// (null in the file).
	StartDate, DueDate  string
	CreatedAt, ClosedAt string
	WontDo              bool // closed as won't do
	Keep                bool // a keep task: done returns it to the pool
	// Importance: "high" or "low"; "" is normal, which the file doesn't
	// say, so a wiki that sets none keeps its files as they were.
	Importance  string
	LastDone    string
	SortOrder   *float64 // its place in the manual order; nil: none
	Recurrence  *Recurrence
	SpawnedFrom string // the occurrence this one follows
	History     []HistoryEntry
	Comments    []Comment
	Extra       map[string]json.RawMessage // keys Zonewiki doesn't know, as the file had them
	// Owner: the user whose private task it is, "" a shared one. Not in
	// the file: it is whose files the task was read from (tasksDir), and a
	// write files it there; the API shows it as "private".
	Owner string
}

// Clone: a copy of the task that shares nothing with it that can change.
func (t *Task) Clone() *Task {
	c := *t
	c.Tags = slices.Clone(t.Tags)
	c.Followers = slices.Clone(t.Followers)
	c.History = slices.Clone(t.History)
	c.Comments = slices.Clone(t.Comments)
	c.Extra = maps.Clone(t.Extra)
	if t.SortOrder != nil {
		v := *t.SortOrder
		c.SortOrder = &v
	}
	if t.Recurrence != nil {
		r := *t.Recurrence
		if r.Lead != nil {
			l := *r.Lead
			r.Lead = &l
		}
		c.Recurrence = &r
	}
	return &c
}

// Same: the two tasks are written alike: every field that is in a file the
// same (a list that is empty and one that isn't there are one), whoever's
// they are (Owner is not in the file).
func (t *Task) Same(o *Task) bool {
	if t.ID != o.ID || t.Title != o.Title || t.Description != o.Description || t.List != o.List ||
		t.Assignee != o.Assignee || t.CreatedBy != o.CreatedBy || t.StartDate != o.StartDate || t.DueDate != o.DueDate ||
		t.CreatedAt != o.CreatedAt || t.ClosedAt != o.ClosedAt || t.WontDo != o.WontDo || t.Keep != o.Keep ||
		t.Importance != o.Importance || t.LastDone != o.LastDone || t.SpawnedFrom != o.SpawnedFrom ||
		!slices.Equal(t.Followers, o.Followers) {
		return false
	}
	if (t.SortOrder == nil) != (o.SortOrder == nil) || t.SortOrder != nil && *t.SortOrder != *o.SortOrder {
		return false
	}
	if (t.Recurrence == nil) != (o.Recurrence == nil) {
		return false
	}
	if a, b := t.Recurrence, o.Recurrence; a != nil {
		if a.Type != b.Type || a.Interval != b.Interval || a.From != b.From || a.Day != b.Day ||
			(a.Lead == nil) != (b.Lead == nil) || a.Lead != nil && *a.Lead != *b.Lead {
			return false
		}
	}
	return slices.Equal(t.Tags, o.Tags) && slices.Equal(t.History, o.History) && slices.Equal(t.Comments, o.Comments) &&
		maps.EqualFunc(t.Extra, o.Extra, func(a, b json.RawMessage) bool { return bytes.Equal(a, b) })
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

// CleanImportance: a task's importance as it is kept: "high", "low", or ""
// for normal (also for "normal" and anything else).
func CleanImportance(s string) string {
	switch s = strings.ToLower(strings.TrimSpace(s)); s {
	case "high", "low":
		return s
	}
	return ""
}

var taskKeys = map[string]bool{"id": true, "title": true, "description": true, "tags": true, "list": true, "assignee": true, "created_by": true, "followers": true,
	"start_date": true, "due_date": true, "created_at": true, "closed_at": true, "wont_do": true,
	"keep": true, "importance": true, "last_done": true, "sort_order": true, "recurrence": true, "spawned_from": true,
	"history": true, "comments": true, "private": true}

// FromJSON reads a task from a file's object, leniently: a value of the
// wrong kind is its field's default, never an error, so an odd file still
// reads (and a write doesn't refuse it).
func FromJSON(o jsonobj.Obj) *Task {
	t := &Task{ID: o.Str("id"), Title: o.Str("title"), Description: o.Str("description"),
		StartDate: o.Str("start_date"), DueDate: o.Str("due_date"),
		CreatedAt: o.Str("created_at"), ClosedAt: o.Str("closed_at"),
		WontDo: jsonobj.True(o["wont_do"]), Keep: jsonobj.True(o["keep"]), LastDone: o.Str("last_done"),
		Importance:  CleanImportance(o.Str("importance")),
		SpawnedFrom: o.Str("spawned_from"), History: []HistoryEntry{}, Comments: []Comment{}}
	t.Tags = CleanTags(jsonobj.TextList(o["tags"])) // "local llm", "#work" from a file edited by hand
	t.List = strings.ToLower(strings.TrimSpace(o.Str("list")))
	t.Assignee = CleanAssignee(o.Str("assignee"))
	t.CreatedBy = CleanAssignee(o.Str("created_by"))
	t.Followers = CleanFollowers(jsonobj.TextList(o["followers"]))
	if f, ok := jsonobj.As[float64](o["sort_order"]); ok {
		t.SortOrder = &f
	}
	if rec, ok := jsonobj.As[jsonobj.Obj](o["recurrence"]); ok && len(rec) > 0 {
		t.Recurrence = recurrenceFromJSON(rec)
	}
	for _, e := range jsonobj.List(o["history"]) {
		t.History = append(t.History, HistoryEntry{At: e.Str("at"), Event: e.Str("event"),
			By: e.Str("by"), From: e.Str("from"), To: e.Str("to")})
	}
	for _, c := range jsonobj.List(o["comments"]) {
		t.Comments = append(t.Comments, Comment{ID: c.Str("id"), At: c.Str("at"), By: c.Str("by"),
			Text: c.Str("text")})
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

// recurrenceFromJSON: a series as stored; a missing or bad type is daily,
// a missing or bad interval 1.
func recurrenceFromJSON(rec jsonobj.Obj) *Recurrence {
	r := &Recurrence{Type: "daily", Interval: 1, From: "due"}
	if s, ok := jsonobj.As[string](rec["type"]); ok && s != "" {
		r.Type = s
	}
	if n, ok := jsonobj.WholeNumber(rec["interval"]); ok && n >= 1 {
		r.Interval = min(n, RecIntervalMax)
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
// the named ones from and to which user, the others neither; by only when someone is named, so a wiki without
// users keeps its files as they were.
func (e HistoryEntry) MarshalJSON() ([]byte, error) {
	if !DatedEvents[e.Event] && !NamedEvents[e.Event] {
		// to: only a look's ("seen", as the API answers it: the last comment
		// it saw, which the browser's news rule reads); no file has one.
		to := ""
		if e.Event == "seen" {
			to = e.To
		}
		return jsonobj.MarshalText(struct {
			At    string `json:"at"`
			Event string `json:"event"`
			By    string `json:"by,omitempty"`
			To    string `json:"to,omitempty"`
		}{e.At, e.Event, e.By, to})
	}
	return jsonobj.MarshalText(struct {
		At    string  `json:"at"`
		Event string  `json:"event"`
		By    string  `json:"by,omitempty"`
		From  *string `json:"from"`
		To    *string `json:"to"`
	}{e.At, e.Event, e.By, jsonobj.Nullable(e.From), jsonobj.Nullable(e.To)})
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

// MarshalJSON refuses: a task has two JSON forms, and which one is meant
// is said where it is written (JSON, Encode).
func (t *Task) MarshalJSON() ([]byte, error) {
	return nil, errors.New("taskmodel: a task is written with JSON(private) or Encode, not json.Marshal")
}

// fileTask: a task as its file keeps it.
type fileTask struct{ *Task }

func (f fileTask) MarshalJSON() ([]byte, error) { return f.JSON(false) }

// JSON writes the task's fields in the order the files keep, the keys Zonewiki
// doesn't know last, A–Z. private: as the API and the browser get it with
// users, "private" (whether it is its user's own) after the fields.
func (t *Task) JSON(private bool) ([]byte, error) {
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
		List        string         `json:"list,omitempty"`
		Assignee    string         `json:"assignee,omitempty"`
		CreatedBy   string         `json:"created_by,omitempty"`
		Followers   []string       `json:"followers,omitempty"`
		StartDate   *string        `json:"start_date"`
		DueDate     *string        `json:"due_date"`
		CreatedAt   *string        `json:"created_at"`
		ClosedAt    *string        `json:"closed_at"`
		WontDo      bool           `json:"wont_do"`
		Keep        bool           `json:"keep"`
		Importance  string         `json:"importance,omitempty"`
		LastDone    *string        `json:"last_done"`
		SortOrder   *float64       `json:"sort_order"`
		Recurrence  *Recurrence    `json:"recurrence"`
		SpawnedFrom *string        `json:"spawned_from"`
		History     []HistoryEntry `json:"history"`
		Comments    []Comment      `json:"comments"`
	}{t.ID, t.Title, t.Description, tags, t.List, t.Assignee, t.CreatedBy, t.Followers, jsonobj.Nullable(t.StartDate), jsonobj.Nullable(t.DueDate),
		jsonobj.Nullable(t.CreatedAt), jsonobj.Nullable(t.ClosedAt), t.WontDo, t.Keep, t.Importance, jsonobj.Nullable(t.LastDone),
		t.SortOrder, t.Recurrence, jsonobj.Nullable(t.SpawnedFrom), history, comments})
	if err == nil && private {
		b = append(b[:len(b)-1], `,"private":`+strconv.FormatBool(t.Private())+"}"...)
	}
	if err != nil || len(t.Extra) == 0 {
		return b, err
	}
	b = b[:len(b)-1] // the closing }
	for _, k := range slices.Sorted(maps.Keys(t.Extra)) {
		key, _ := jsonobj.MarshalText(k)
		b = append(append(append(append(b, ','), key...), ':'), t.Extra[k]...)
	}
	return append(b, '}'), nil
}

// Encode: a task list file's text, two spaces of indent, text as it is
// (no \u escapes but those JSON needs).
func Encode(tasks []*Task) (string, error) {
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

// IsOpen: not closed (neither done nor won't do).
func (t *Task) IsOpen() bool { return t.ClosedAt == "" }

// Private: the task is a user's own (Owner their name), not a shared one:
// everyone's ("") or a group's ("@<group>").
func (t *Task) Private() bool { return t.Owner != "" && !strings.HasPrefix(t.Owner, "@") }

// Parse: a task list file's tasks; ok false when it isn't a list. An item
// that isn't an object is left out.
func Parse(raw []byte) ([]*Task, bool) {
	var l []json.RawMessage
	if json.Unmarshal(raw, &l) != nil || l == nil {
		return nil, false
	}
	out := make([]*Task, 0, len(l))
	for _, x := range l {
		if o, ok := jsonobj.As[jsonobj.Obj](x); ok {
			out = append(out, FromJSON(o))
		}
	}
	return out, true
}

var spaceRunRE = regexp.MustCompile(mdtext.SpaceClass + `+`)

// CleanTags: tags as the filter can match them: one word each (whitespace
// inside becomes "-"), no leading "#", no empties, no case-insensitive
// duplicates (the first spelling wins).
func CleanTags(tags []string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, t := range tags {
		tag := strings.Trim(spaceRunRE.ReplaceAllString(strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(t), "#")), "-"), "-")
		if tag != "" && !seen[strings.ToLower(tag)] {
			seen[strings.ToLower(tag)] = true
			out = append(out, tag)
		}
	}
	return out
}

// DatedEvents: the history events that say from and to which day.
var DatedEvents = map[string]bool{"start": true, "due": true, "repeat": true}

// NamedEvents: the history events that say from and to which user.
var NamedEvents = map[string]bool{"assigned": true, "mentioned": true}

// CleanFollowers: followers as they are kept: names as CleanAssignee leaves
// them, each once, in their order; nil for none.
func CleanFollowers(names []string) []string {
	var out []string
	for _, n := range names {
		if n = CleanAssignee(n); n != "" && !slices.Contains(out, n) {
			out = append(out, n)
		}
	}
	return out
}

// CleanAssignee: an assignee as it is kept: a user's name is lowercase,
// and a leading "@" is how it is typed, not part of it.
func CleanAssignee(s string) string {
	return strings.ToLower(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(s), "@")))
}
