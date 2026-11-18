package taskfiles

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"zonewiki/internal/asker"
)

// membersStore: alice, bob and carol; the shared list hiring is for alice
// and the group @ops (carol's), with an open task, one closed this month
// and one archived; the list team is for everyone, with a task; one task in
// no list.
func membersStore(t *testing.T) *fixture {
	t.Helper()
	f := newStore(t, "alice", "bob", "carol")
	f.Member = func(asks asker.Asker, who []string) bool {
		user := asks.Name
		return slices.Contains(who, user) || user == "carol" && slices.Contains(who, "@ops")
	}
	hiring := &List{Name: "hiring", Settings: map[string]string{"view": "open"}}
	hiring.SetFor([]string{"@ops", "alice"})
	hiring.SetOwners([]string{"alice"})
	f.Lock()
	if _, err := f.WriteListsLocked(asker.User("alice"), "", &Registry{Lists: []*List{{Name: "team"}, hiring}}); err != nil {
		t.Fatal(err)
	}
	f.Unlock()
	in := func(id, list, closedAt string) *Task { x := task(id, "", closedAt); x.List = list; return x }
	f.write(t, "alice", []*Task{in("h1", "hiring", ""), in("t1", "team", ""), in("n1", "", "")},
		[]*Task{in("h2", "hiring", "2026-06-01T10:00"), in("h3", "hiring", "2026-03-02T10:00")}, "Set up")
	return f
}

// The list's entry is in its own folder, not in the file everyone reads;
// the registry read back has it, with whom it is for.
func TestAListForSomeIsKeptInItsFolder(t *testing.T) {
	f := membersStore(t)
	if text := f.text(t, "_tasks/lists.json"); strings.Contains(text, "hiring") || !strings.Contains(text, `{"name": "team"}`) {
		t.Errorf("lists.json: %s", text)
	}
	if text := f.text(t, "_tasks/lists/hiring/list.json"); text != `{"name": "hiring", "view": "open", "for": ["@ops", "alice"], "owners": ["alice"]}`+"\n" {
		t.Errorf("list.json: %q", text)
	}
	l := f.ReadLists("").Find("hiring")
	if l == nil || !slices.Equal(l.For(), []string{"@ops", "alice"}) || !slices.Equal(l.Owners(), []string{"alice"}) || l.Settings["view"] != "open" {
		t.Errorf("read back: %+v", l)
	}
	if !slices.Equal(f.ForOfList("hiring"), []string{"@ops", "alice"}) || f.ForOfList("team") != nil || f.ForOfList("") != nil {
		t.Error("ForOfList")
	}
	// Written again as read: nothing to commit.
	f.Lock()
	defer f.Unlock()
	r, err := f.ReadListsStrict("")
	if err != nil {
		t.Fatal(err)
	}
	if p, err := f.WriteListsLocked(asker.User("bob"), "", r); len(p) != 0 || err != nil {
		t.Errorf("an unchanged registry written: %q, %v", p, err)
	}
}

// Who sees a task is decided by where it is kept: every reader of a user's
// leaves out the folder of a list they are none of.
func TestOnlyItsMembersReadAListForSome(t *testing.T) {
	f := membersStore(t)
	any := func(*Task) bool { return true }
	for user, sees := range map[string]bool{"alice": true, "carol": true, "bob": false} {
		has := func(what string, tasks []*Task, id string) {
			t.Helper()
			if slices.Contains(ids(tasks), id) != sees {
				t.Errorf("%s, %s: %s there: %v", user, what, id, !sees)
			}
		}
		has("Open", f.Open(asker.User(user)), "h1")
		all := f.All(asker.User(user))
		has("All", all, "h1")
		has("All", all, "h2")
		has("All", all, "h3")
		has("RecentClosed", f.RecentClosed(asker.User(user), any), "h2")
		has("ClosedInYear", f.ClosedInYear(asker.User(user), "2026"), "h3")
		page, total := f.ClosedPage(asker.User(user), "2026", any, 50, "")
		has("ClosedPage", page, "h2")
		if n := f.CountClosedInYear(asker.User(user), "2026", any); n != total || (n == 2) != sees {
			t.Errorf("%s counts %d closed (the page %d)", user, n, total)
		}
		open, done := f.Snapshot(asker.User(user))
		has("Snapshot", append(open, done...), "h3")
		if f.SeesList(asker.User(user), "hiring") != sees || !f.SeesList(asker.User(user), "team") || !f.SeesList(asker.User(user), "") {
			t.Errorf("SeesList for %s", user)
		}
		// What is for everyone is everyone's.
		if got := ids(f.Open(asker.User(user))); !slices.Contains(got, "t1") || !slices.Contains(got, "n1") {
			t.Errorf("%s misses the shared tasks: %v", user, got)
		}
	}
	// Zone itself reads all of them (the search index, which filters later).
	if got := ids(f.Every()); !slices.Contains(got, "h1") || !slices.Contains(got, "h3") {
		t.Errorf("Every: %v", got)
	}
	// The counts' stamp of someone who is none of them doesn't move with the list.
	before, alices := f.Stamp(asker.User("bob")), f.Stamp(asker.User("alice"))
	f.one(t, "alice", "h1", "Change", func(active, closed []*Task) ([]*Task, []*Task) {
		for _, x := range active {
			if x.ID == "h1" {
				x.Title = "Changed"
			}
		}
		return active, closed
	})
	if f.Stamp(asker.User("alice")) == alices {
		t.Error("alice's stamp stayed")
	}
	if strings.Split(f.Stamp(asker.User("bob")), " ")[0] != strings.Split(before, " ")[0] || strings.Contains(before, "hiring") {
		t.Errorf("bob's stamp names or follows the list: %q", before)
	}
}

// A write by someone who is none of them leaves the list's files as they
// are: it didn't read them, so it must not write them.
func TestAWriteByAnyoneElseLeavesAListForSomeAlone(t *testing.T) {
	f := membersStore(t)
	files := []string{"_tasks/lists/hiring/tasks.json", "_tasks/lists/hiring/closed.json",
		"_tasks/lists/hiring/closed-2026-03.json", "_tasks/lists/hiring/list.json"}
	before := map[string]string{}
	for _, p := range files {
		if before[p] = f.text(t, p); before[p] == "" {
			t.Fatalf("%s isn't there", p)
		}
	}
	same := func(what string) {
		t.Helper()
		for _, p := range files {
			if f.text(t, p) != before[p] {
				t.Errorf("%s changed %s", what, p)
			}
		}
	}
	// Everything bob sees read and written (a batch, a delete, an import).
	f.rewrite(t, "bob", "Complete", func(active, closed []*Task) ([]*Task, []*Task) {
		for _, x := range active {
			if x.ID == "t1" {
				x.ClosedAt = "2026-06-01T11:00"
				return slices.DeleteFunc(active, func(y *Task) bool { return y.ID == "t1" }), append(closed, x)
			}
		}
		t.Fatal("no t1")
		return nil, nil
	})
	same("a write of everything bob sees")
	// One task changed.
	f.one(t, "bob", "n1", "Change", func(active, closed []*Task) ([]*Task, []*Task) {
		active[0].Title = "Changed"
		return active, closed
	})
	same("a write of one task")
	// A task of bob's that names the list (it can't have been read for him): refused, nothing written.
	f.Lock()
	active, err := f.ReadOpenStrict(asker.User("bob"))
	if err != nil {
		t.Fatal(err)
	}
	smuggled := task("x1", "", "")
	smuggled.List = "hiring"
	if err := f.WriteOpenLocked(asker.User("bob"), append(active, smuggled), "Smuggle"); err == nil {
		t.Error("a task written into a list its writer doesn't see")
	}
	f.Unlock()
	same("a refused write")
	// A member's write reaches them.
	f.one(t, "carol", "h1", "Change", func(active, closed []*Task) ([]*Task, []*Task) {
		for _, x := range active {
			if x.ID == "h1" {
				x.Title = "Carol's"
			}
		}
		return active, closed
	})
	if !strings.Contains(f.text(t, "_tasks/lists/hiring/tasks.json"), "Carol's") {
		t.Error("a member's write didn't reach the list")
	}
	// A start's filing (Zone itself) keeps them where they are.
	f.ArchiveOldClosed()
	f.FileByList()
	if got := f.ids(t, "_tasks/lists/hiring/closed-2026-03.json"); !slices.Equal(got, []string{"h3"}) {
		t.Errorf("after a start: %v", got)
	}
}

// For everyone again: the entry is back in the registry and the file is
// gone. Deleted: the folder keeps what says whose its history is.
func TestAListForSomeWidenedAndGone(t *testing.T) {
	f := membersStore(t)
	change := func(edit func(r *Registry)) []string {
		t.Helper()
		f.Lock()
		defer f.Unlock()
		r, err := f.ReadListsStrict("")
		if err != nil {
			t.Fatal(err)
		}
		edit(r)
		paths, err := f.WriteListsLocked(asker.User("alice"), "", r)
		if err != nil {
			t.Fatal(err)
		}
		var rel []string
		for _, p := range paths {
			x, _ := filepath.Rel(f.root, p)
			rel = append(rel, filepath.ToSlash(x))
		}
		return rel
	}
	// Narrowing team: its folder says so before the registry stops naming it.
	if got := change(func(r *Registry) { r.Find("team").SetFor([]string{"alice"}) }); !slices.Equal(got, []string{"_tasks/lists/team/list.json", "_tasks/lists.json"}) {
		t.Errorf("narrowing wrote %v", got)
	}
	if slices.Contains(ids(f.Open(asker.User("bob"))), "t1") || !slices.Contains(ids(f.Open(asker.User("alice"))), "t1") {
		t.Error("a narrowed list's tasks")
	}
	// And for everyone again.
	if got := change(func(r *Registry) { r.Find("team").SetFor(nil) }); !slices.Equal(got, []string{"_tasks/lists/team/list.json", "_tasks/lists.json"}) {
		t.Errorf("widening wrote %v", got)
	}
	if _, err := os.Stat(filepath.Join(f.root, "_tasks/lists/team/list.json")); err == nil || !strings.Contains(f.text(t, "_tasks/lists.json"), `{"name": "team"}`) {
		t.Error("a widened list keeps its file, or isn't in the registry")
	}
	if !slices.Contains(ids(f.Open(asker.User("bob"))), "t1") {
		t.Error("a widened list's tasks")
	}
	// Gone: no list anymore, and its folder still its members'.
	change(func(r *Registry) {
		r.Lists = slices.DeleteFunc(r.Lists, func(l *List) bool { return l.Name == "hiring" })
	})
	if f.ReadLists("").Find("hiring") != nil {
		t.Error("a deleted list is still one")
	}
	if text := f.text(t, "_tasks/lists/hiring/list.json"); text != `{"name": "hiring", "for": ["@ops", "alice"], "gone": true}`+"\n" {
		t.Errorf("what it left: %q", text)
	}
	if !slices.Equal(f.ForOfList("hiring"), []string{"@ops", "alice"}) || f.SeesList(asker.User("bob"), "hiring") || slices.Contains(ids(f.Open(asker.User("bob"))), "h1") {
		t.Error("a deleted list's folder is everyone's")
	}
}
