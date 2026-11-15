package taskmodel

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"zonewiki/internal/jsonobj"
)

// A task's assignee is kept after its tags, lowercase and without the "@"
// it is typed with; a task without one has no key, in its file and in the
// API's form.
func TestAssigneeInTheFile(t *testing.T) {
	tasks, ok := Parse([]byte(`[{"id": "a1", "title": "One", "assignee": " @Bob "}, {"id": "a2", "title": "Two", "assignee": ["x"]}]`))
	if !ok || tasks[0].Assignee != "bob" || tasks[1].Assignee != "" {
		t.Fatalf("read: %v %+v", ok, tasks)
	}
	got, err := Encode(tasks)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "\"tags\": [],\n    \"assignee\": \"bob\",\n    \"start_date\"") {
		t.Errorf("the key's place:\n%s", got)
	}
	if strings.Count(got, "assignee") != 1 {
		t.Errorf("a task without one has no key:\n%s", got)
	}
	api, _ := tasks[1].JSON(true)
	if strings.Contains(string(api), "assignee") {
		t.Errorf("the API's form: %s", api)
	}
	if again, ok := Parse([]byte(got)); !ok || again[0].Assignee != "bob" || len(again[0].Extra) != 0 {
		t.Errorf("read again: %+v", again[0])
	}
}

// An assignment's history entry says from and to whom, null for no one.
func TestAssignedEventNamesItsUsers(t *testing.T) {
	b, err := HistoryEntry{At: "2026-01-02T10:00", Event: "assigned", By: "alice", To: "bob"}.MarshalJSON()
	if want := `{"at":"2026-01-02T10:00","event":"assigned","by":"alice","from":null,"to":"bob"}`; err != nil || string(b) != want {
		t.Errorf("%s %v", b, err)
	}
}

// A task as the Python Zone wrote it (its key order, 1.0, a key it no
// longer knows, a field missing) reads into a Task and is written in Zone's
// order, the unknown keys last (A–Z) and kept; written again, nothing
// changes.
func TestTaskFileRoundTrip(t *testing.T) {
	old := `[
  {
    "id": "a1",
    "zz": {"a": [1, 2.50]},
    "title": "Call <Bob> & \"Al\"",
    "description": "ü",
    "tags": ["work", "#Home"],
    "due_date": "2026-03-01",
    "created_at": "2026-01-02T10:00:00",
    "closed_at": null,
    "wont_do": false,
    "sort_order": 1.0,
    "recurrence": {"type": "monthly", "interval": 2, "from": "due", "day": 31},
    "blocked_by": ["x"],
    "spawned_from": null,
    "history": [{"at": "2026-01-02T10:00", "event": "due", "from": null, "to": "2026-03-01"}, "junk"]
  }
]`
	tasks, ok := Parse([]byte(old))
	if !ok {
		t.Fatal("not a task list")
	}
	got, err := Encode(tasks)
	if err != nil {
		t.Fatal(err)
	}
	want := `[
  {
    "id": "a1",
    "title": "Call <Bob> & \"Al\"",
    "description": "ü",
    "tags": [
      "work",
      "Home"
    ],
    "start_date": null,
    "due_date": "2026-03-01",
    "created_at": "2026-01-02T10:00:00",
    "closed_at": null,
    "wont_do": false,
    "keep": false,
    "last_done": null,
    "sort_order": 1,
    "recurrence": {
      "type": "monthly",
      "interval": 2,
      "from": "due",
      "day": 31
    },
    "spawned_from": null,
    "history": [
      {
        "at": "2026-01-02T10:00",
        "event": "due",
        "from": null,
        "to": "2026-03-01"
      }
    ],
    "comments": [],
    "blocked_by": [
      "x"
    ],
    "zz": {
      "a": [
        1,
        2.50
      ]
    }
  }
]
`
	if got != want {
		t.Fatalf("got\n%s", got)
	}
	again, _ := Parse([]byte(got))
	if again, _ := Encode(again); again != got {
		t.Errorf("not stable:\n%s", again)
	}
}

// Values of the wrong kind are their field's default, never an error; a
// number or a bool where text belongs is its JSON text.
func TestTaskFromOddValues(t *testing.T) {
	v, _ := jsonobj.Parse([]byte(`{"id": 7, "title": null, "description": true, "tags": "work", "sort_order": "x",
		"recurrence": {"interval": "2"}, "keep": 1, "comments": [{"id": "c", "at": "2026-01-01T10:00", "text": "hi"}, 3]}`))
	task := FromJSON(v)
	if task.ID != "7" || task.Title != "" || task.Description != "true" || len(task.Tags) != 0 || task.SortOrder != nil || task.Keep ||
		task.Recurrence == nil || task.Recurrence.Type != "daily" || task.Recurrence.Interval != 1 || len(task.Comments) != 1 {
		t.Errorf("%+v %+v", task, task.Recurrence)
	}
	if text, _ := Encode([]*Task{task}); !strings.Contains(text, `"tags": []`) {
		t.Errorf("no tags must be [], not null:\n%s", text)
	}
}

// Same: two tasks are written alike. Every field a file has is noticed, a
// list that is empty and one that isn't there are one, and whose the task
// is (not in the file) doesn't count; what Same takes for the same is
// written the same.
func TestSame(t *testing.T) {
	full := func() *Task {
		order, lead := 1.5, 2
		return &Task{ID: "a1", Title: "One", Description: "Notes", Tags: []string{"a", "b"}, List: "shop", Assignee: "bob",
			StartDate: "2026-01-01", DueDate: "2026-01-02", CreatedAt: "2026-01-01T10:00:00", ClosedAt: "2026-01-03T10:00:00",
			WontDo: true, Keep: true, Importance: "high", LastDone: "2026-01-02T10:00:00", SortOrder: &order,
			Recurrence:  &Recurrence{Type: "monthly", Interval: 2, From: "due", Day: 3, Lead: &lead},
			SpawnedFrom: "a0",
			History:     []HistoryEntry{{At: "2026-01-01T10:00:00", Event: "due", By: "alice", From: "2026-01-01", To: "2026-01-02"}},
			Comments:    []Comment{{ID: "c1", At: "2026-01-01T10:00:00", By: "alice", Text: "Hm"}},
			Extra:       map[string]json.RawMessage{"zz": json.RawMessage(`7`)}}
	}
	if a, b := full(), full(); !a.Same(b) || !b.Same(a) || !a.Same(a) || !a.Same(a.Clone()) {
		t.Fatal("a task isn't the same as its like")
	}
	changes := map[string]func(*Task){
		"ID": func(x *Task) { x.ID = "a2" }, "Title": func(x *Task) { x.Title = "one" }, "Description": func(x *Task) { x.Description += " " },
		"Tags": func(x *Task) { x.Tags[1] = "c" }, "List": func(x *Task) { x.List = "" }, "Assignee": func(x *Task) { x.Assignee = "" },
		"StartDate": func(x *Task) { x.StartDate = "" }, "DueDate": func(x *Task) { x.DueDate = "2026-01-03" },
		"CreatedAt": func(x *Task) { x.CreatedAt = "" }, "ClosedAt": func(x *Task) { x.ClosedAt = "" },
		"WontDo": func(x *Task) { x.WontDo = false }, "Keep": func(x *Task) { x.Keep = false }, "Importance": func(x *Task) { x.Importance = "" },
		"LastDone": func(x *Task) { x.LastDone = "" }, "SortOrder": func(x *Task) { *x.SortOrder = 2 }, "Recurrence": func(x *Task) { x.Recurrence = nil },
		"SpawnedFrom": func(x *Task) { x.SpawnedFrom = "" }, "History": func(x *Task) { x.History[0].To = "" },
		"Comments": func(x *Task) { x.Comments[0].Text = "Hm." }, "Extra": func(x *Task) { x.Extra["zz"] = json.RawMessage(`8`) },
		// A list's length and order, a pointer there or not, a part of a part.
		"a tag more":       func(x *Task) { x.Tags = append(x.Tags, "c") },
		"a tag less":       func(x *Task) { x.Tags = x.Tags[:1] },
		"no tags":          func(x *Task) { x.Tags = nil },
		"the tags' order":  func(x *Task) { x.Tags = []string{"b", "a"} },
		"no order":         func(x *Task) { x.SortOrder = nil },
		"order 0":          func(x *Task) { *x.SortOrder = 0 },
		"series type":      func(x *Task) { x.Recurrence.Type = "yearly" },
		"series interval":  func(x *Task) { x.Recurrence.Interval = 3 },
		"series from":      func(x *Task) { x.Recurrence.From = "completion" },
		"series day":       func(x *Task) { x.Recurrence.Day = 0 },
		"series lead":      func(x *Task) { *x.Recurrence.Lead = 0 },
		"series no lead":   func(x *Task) { x.Recurrence.Lead = nil },
		"history at":       func(x *Task) { x.History[0].At = "" },
		"history event":    func(x *Task) { x.History[0].Event = "start" },
		"history by":       func(x *Task) { x.History[0].By = "" },
		"history from":     func(x *Task) { x.History[0].From = "" },
		"history more":     func(x *Task) { x.History = append(x.History, HistoryEntry{Event: "done"}) },
		"no history":       func(x *Task) { x.History = nil },
		"comment id":       func(x *Task) { x.Comments[0].ID = "c2" },
		"comment at":       func(x *Task) { x.Comments[0].At = "" },
		"comment by":       func(x *Task) { x.Comments[0].By = "" },
		"no comments":      func(x *Task) { x.Comments = nil },
		"another key":      func(x *Task) { x.Extra["yy"] = json.RawMessage(`1`) },
		"no unknown keys":  func(x *Task) { x.Extra = nil },
		"another key only": func(x *Task) { x.Extra = map[string]json.RawMessage{"yy": json.RawMessage(`7`)} },
	}
	// Every field of a task has a change here (Owner is not in the file): a
	// new field can't be forgotten in Same without this failing.
	fields := reflect.TypeOf(Task{})
	for i := range fields.NumField() {
		if name := fields.Field(i).Name; name != "Owner" && changes[name] == nil {
			t.Errorf("no change of %s is tried: is it in Same?", name)
		}
	}
	for what, change := range changes {
		a, b := full(), full()
		change(b)
		if a.Same(b) || b.Same(a) {
			t.Errorf("%s: a change isn't noticed", what)
		}
		ta, _ := Encode([]*Task{a})
		tb, _ := Encode([]*Task{b})
		if ta == tb {
			t.Errorf("%s: the test's change doesn't show in the file", what)
		}
	}
	// Whose it is doesn't count.
	a, b := full(), full()
	b.Owner = "alice"
	if !a.Same(b) || !b.Same(a) {
		t.Error("Owner counts")
	}
	// Empty and missing lists are one, and are written alike.
	bare := &Task{ID: "x", Title: "Bare"}
	empty := &Task{ID: "x", Title: "Bare", Tags: []string{}, History: []HistoryEntry{}, Comments: []Comment{}, Extra: map[string]json.RawMessage{}}
	if !bare.Same(empty) || !empty.Same(bare) {
		t.Error("nil and empty lists differ")
	}
	tb, _ := Encode([]*Task{bare})
	te, _ := Encode([]*Task{empty})
	if tb != te {
		t.Errorf("but they are written differently:\n%s\n%s", tb, te)
	}
	// A task read from its file is the same as the one written.
	text, _ := Encode([]*Task{full(), bare})
	if read, ok := Parse([]byte(text)); !ok || !read[0].Same(full()) || !read[1].Same(bare) || read[0].Same(read[1]) {
		t.Errorf("read again: %+v", read)
	}
}
