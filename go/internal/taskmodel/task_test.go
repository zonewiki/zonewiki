package taskmodel

import (
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
