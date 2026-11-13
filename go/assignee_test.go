package main

import (
	"bytes"
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
		"me":  {Name: "me", Role: roleEditor}, "none": {Name: "none", Role: roleEditor},
	}}
	if got := strings.Join(s.assignableUsers(), " "); got != "alice bob ed" {
		t.Errorf("assignableUsers: %q", got)
	}
	for name, why := range map[string]string{"zed": "is not a user", "carol": "is a viewer", "dan": "is disabled",
		"eve": "waiting for an admin", "me": "whoever is signed in", "none": "no one"} {
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
	if code, errs := run("pw\npw\n", "add", "alice"); code != 0 {
		t.Fatalf("add alice: %d %q", code, errs)
	}
	for _, name := range []string{"me", "none"} {
		if code, errs := run("pw\npw\n", "add", name); code == 0 || !strings.Contains(errs, "can't be a user name") {
			t.Errorf("add %s: %d %q", name, code, errs)
		}
		if code, errs := run("", "invite", name); code == 0 || !strings.Contains(errs, "can't be a user name") {
			t.Errorf("invite %s: %d %q", name, code, errs)
		}
	}
	if code, errs := run("pw\npw\n", "add", "men"); code != 0 {
		t.Errorf("add men: %d %q", code, errs)
	}
}
