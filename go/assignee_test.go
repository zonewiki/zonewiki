package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Who a task can be assigned to: a user who isn't disabled, pending or a
// viewer, and isn't called by a reserved name.
func TestAssignable(t *testing.T) {
	s := &Settings{Users: map[string]*User{
		"alice": {Name: "alice", Role: roleAdmin}, "bob": {Name: "bob", Role: roleEditor}, "ed": {Name: "ed"},
		"carol": {Name: "carol", Role: roleViewer}, "dan": {Name: "dan", Disabled: true},
		"eve": {Name: "eve", Disabled: true, Pending: true},
	}}
	if got := strings.Join(s.assignableUsers(), " "); got != "alice bob ed" {
		t.Errorf("assignableUsers: %q", got)
	}
	for name, why := range map[string]string{"zed": "is not a user", "carol": "is a viewer", "dan": "is disabled",
		"eve": "waiting for an admin"} {
		if s.assignable(name) || !strings.Contains(s.notAssignable(name), why) {
			t.Errorf("%s: assignable %v, %q", name, s.assignable(name), s.notAssignable(name))
		}
	}
	if (&Settings{}).assignable("alice") || len((&Settings{}).assignableUsers()) != 0 {
		t.Error("without users no one is assignable")
	}
}

// No new user is called me or none: zonewiki user add and an invite refuse.
func TestReservedUserNames(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := userMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, errs.String()
	}
	if code, errs := run("password\npassword\n", "add", "alice"); code != 0 {
		t.Fatalf("add alice: %d %q", code, errs)
	}
	for _, name := range []string{"me", "none"} {
		if code, errs := run("password\npassword\n", "add", name); code == 0 || !strings.Contains(errs, "can't be a user name") {
			t.Errorf("add %s: %d %q", name, code, errs)
		}
		if code, errs := run("", "invite", name); code == 0 || !strings.Contains(errs, "can't be a user name") {
			t.Errorf("invite %s: %d %q", name, code, errs)
		}
	}
	if code, errs := run("password\npassword\n", "add", "men"); code != 0 {
		t.Errorf("add men: %d %q", code, errs)
	}
	// Nor does a settings file written by hand have one: it is refused, naming the line.
	hash := hashPassword("p")
	for _, name := range []string{"me", "none"} {
		f, err := parseConfig("[user alice]\npassword = " + hash + "\n\n[user " + name + "]\npassword = " + hash + "\n")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := loadSettings(f); err == nil || !strings.Contains(err.Error(), "can't be a user name") || !strings.Contains(err.Error(), "line 4") {
			t.Errorf("a file with [user %s]: %v", name, err)
		}
	}
}

// Disabling or removing a user says how many open tasks they still have.
func TestUserCommandsNameTheTasksToHandOn(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := userMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String() + errs.String()
	}
	for _, name := range []string{"alice", "bob", "carol"} {
		if code, out := run("password\npassword\n", "add", name); code != 0 {
			t.Fatalf("add %s: %d %q", name, code, out)
		}
	}
	if err := os.MkdirAll(filepath.Join(wiki, "_tasks"), 0o755); err != nil {
		t.Fatal(err)
	}
	tasks := `[{"id": "a", "title": "One", "assignee": "bob"}, {"id": "b", "title": "Two", "assignee": "bob"},
		{"id": "c", "title": "Done", "assignee": "bob", "closed_at": "2026-01-02T10:00:00"}, {"id": "d", "title": "Hers", "assignee": "carol"}]`
	if err := os.WriteFile(filepath.Join(wiki, "_tasks", "tasks.json"), []byte(tasks), 0o644); err != nil {
		t.Fatal(err)
	}
	if code, out := run("", "disable", "bob"); code != 0 || !strings.Contains(out, "2 open tasks are assigned to bob") ||
		!strings.Contains(out, "/tasks?assignee=bob") {
		t.Errorf("disable bob: %d %q", code, out)
	}
	if code, out := run("", "remove", "carol"); code != 0 || !strings.Contains(out, "1 open task is assigned to carol and now counts as no one's") {
		t.Errorf("remove carol: %d %q", code, out)
	}
	if code, out := run("", "disable", "alice"); code == 0 && strings.Contains(out, "assigned to") {
		t.Errorf("disable alice, who has no tasks: %q", out)
	}
}
