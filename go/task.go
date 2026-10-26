// A deck task: its fields as Leaf knows them, and whatever else a file
// gave it (keys of older versions, or added by hand), kept as it was.
package main

import (
	"bytes"
	"encoding/json"
	"math"
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
	Extra                  *Obj // keys Leaf doesn't know, in the file's order
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

// HistoryEntry: what happened to a task (event) and when (at); the dated
// events (start, due, repeat) say from and to which day ("" none).
type HistoryEntry struct {
	At, Event, From, To string
}

type Comment struct {
	ID, At, Text string
}

var taskKeys = map[string]bool{"id": true, "title": true, "description": true, "tags": true,
	"start_date": true, "due_date": true, "created_at": true, "completed_at": true, "wont_do": true,
	"keep": true, "last_done": true, "sort_order": true, "recurrence": true, "spawned_from": true,
	"history": true, "comments": true}

// strField: a field as text: a string as it is, null as "", anything else
// as Python's str() of it (a file edited by hand).
func strField(o *Obj, k string) string {
	switch v := o.Get(k).(type) {
	case nil:
		return ""
	case string:
		return v
	default:
		return pyStr(v)
	}
}

// taskFromObj reads a task from a file's (or a request's) object,
// leniently: a value of the wrong kind is its field's default, never an
// error, so an odd file still reads (and a write doesn't refuse it).
func taskFromObj(o *Obj) *Task {
	t := &Task{ID: strField(o, "id"), Title: strField(o, "title"), Description: strField(o, "description"),
		StartDate: strField(o, "start_date"), DueDate: strField(o, "due_date"),
		CreatedAt: strField(o, "created_at"), CompletedAt: strField(o, "completed_at"),
		WontDo: o.Truthy("wont_do"), Keep: o.Truthy("keep"), LastDone: strField(o, "last_done"),
		SpawnedFrom: strField(o, "spawned_from"), Tags: []string{}, History: []HistoryEntry{}, Comments: []Comment{}}
	if tags, ok := o.Get("tags").([]any); ok {
		t.Tags = cleanTags(tags) // "local llm", "#work" from a file edited by hand
	}
	if f, ok := number(o.Get("sort_order")); ok && !math.IsNaN(f) && !math.IsInf(f, 0) {
		t.SortOrder = &f
	}
	if rec, ok := o.Get("recurrence").(*Obj); ok && rec.Len() > 0 {
		t.Recurrence = recurrenceFromObj(rec)
	}
	for _, x := range o.List("history") {
		if e, ok := x.(*Obj); ok {
			t.History = append(t.History, HistoryEntry{At: strField(e, "at"), Event: strField(e, "event"),
				From: strField(e, "from"), To: strField(e, "to")})
		}
	}
	for _, x := range o.List("comments") {
		if c, ok := x.(*Obj); ok {
			t.Comments = append(t.Comments, Comment{ID: strField(c, "id"), At: strField(c, "at"), Text: strField(c, "text")})
		}
	}
	for _, k := range o.Keys() {
		if !taskKeys[k] {
			if t.Extra == nil {
				t.Extra = NewObj()
			}
			t.Extra.Set(k, o.Get(k))
		}
	}
	return t
}

// recurrenceFromObj: a series as stored; a missing or bad type is daily,
// a missing or bad interval 1.
func recurrenceFromObj(rec *Obj) *Recurrence {
	r := &Recurrence{Type: "daily", Interval: 1, From: "due"}
	if s, ok := rec.Get("type").(string); ok && s != "" {
		r.Type = s
	}
	if n, ok := intValue(rec.Get("interval")); ok && n >= 1 {
		r.Interval = min(n, recIntervalMax)
	}
	if rec.Str("from") == "completion" {
		r.From = "completion"
	}
	if n, ok := intValue(rec.Get("day")); ok && n >= 1 && n <= 31 {
		r.Day = n
	}
	if n, ok := intValue(rec.Get("lead")); ok && n >= 0 {
		r.Lead = &n
	}
	return r
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// obj: the task as JSON's object, in the order the files keep, the keys
// Leaf doesn't know last.
func (t *Task) obj() *Obj {
	tags := make([]any, len(t.Tags))
	for i, s := range t.Tags {
		tags[i] = s
	}
	var sortOrder, rec any
	if t.SortOrder != nil {
		sortOrder = *t.SortOrder
	}
	if r := t.Recurrence; r != nil {
		o := NewObj("type", r.Type, "interval", int64(r.Interval), "from", r.From)
		if r.Day > 0 {
			o.Set("day", int64(r.Day))
		}
		if r.Lead != nil {
			o.Set("lead", int64(*r.Lead))
		}
		rec = o
	}
	history := make([]any, len(t.History))
	for i, e := range t.History {
		o := NewObj("at", e.At, "event", e.Event)
		if datedEvents[e.Event] {
			o.Set("from", nullable(e.From))
			o.Set("to", nullable(e.To))
		}
		history[i] = o
	}
	comments := make([]any, len(t.Comments))
	for i, c := range t.Comments {
		comments[i] = NewObj("id", c.ID, "at", c.At, "text", c.Text)
	}
	o := NewObj("id", t.ID, "title", t.Title, "description", t.Description, "tags", tags,
		"start_date", nullable(t.StartDate), "due_date", nullable(t.DueDate),
		"created_at", nullable(t.CreatedAt), "completed_at", nullable(t.CompletedAt),
		"wont_do", t.WontDo, "keep", t.Keep, "last_done", nullable(t.LastDone),
		"sort_order", sortOrder, "recurrence", rec, "spawned_from", nullable(t.SpawnedFrom),
		"history", history, "comments", comments)
	if t.Extra != nil {
		for _, k := range t.Extra.Keys() {
			o.Set(k, t.Extra.Get(k))
		}
	}
	return o
}

func (t *Task) MarshalJSON() ([]byte, error) { return t.obj().MarshalJSON() }

// encodeTasks: a deck file's text, two spaces of indent, text as it is
// (no \u escapes but those JSON needs).
func encodeTasks(tasks []*Task) (string, error) {
	if tasks == nil {
		tasks = []*Task{}
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(tasks); err != nil {
		return "", err
	}
	return b.String(), nil
}

// isOpen: not completed (nor won't do).
func (t *Task) isOpen() bool { return t.CompletedAt == "" }

// copyTask: a copy whose lists the original doesn't share.
func copyTask(t *Task) *Task {
	c := *t
	c.Tags = append([]string{}, t.Tags...)
	c.History = append([]HistoryEntry{}, t.History...)
	c.Comments = append([]Comment{}, t.Comments...)
	if t.Recurrence != nil {
		r := *t.Recurrence
		c.Recurrence = &r
	}
	return &c
}
