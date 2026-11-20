package taskfiles

import (
	"slices"
	"strings"
	"testing"
	"zonewiki/internal/asker"
)

// groupsStore: alice, bob and carol; alice and carol are in the group ops,
// whose list hiring (in groups/ops/_tasks/) has an open task, one closed
// this month and one archived; the shared list team has a task; one task is
// in no list.
func groupsStore(t *testing.T) *fixture {
	t.Helper()
	f := newStore(t, "alice", "bob", "carol")
	f.GroupsDir = "groups"
	f.Groups = func(asks asker.Asker) []string {
		if asks == Everyone || asks.Name == "alice" || asks.Name == "carol" {
			return []string{"ops"}
		}
		return nil
	}
	ops := GroupOwner("ops")
	f.Lock()
	if _, err := f.WriteListsLocked(asker.User("alice"), "", &Registry{Lists: []*List{{Name: "team"}}}); err != nil {
		t.Fatal(err)
	}
	hiring := &List{Name: "hiring", Settings: map[string]string{"view": "open"}}
	hiring.SetOwners([]string{"alice"})
	if _, err := f.WriteListsLocked(asker.User("alice"), ops, &Registry{Lists: []*List{hiring}}); err != nil {
		t.Fatal(err)
	}
	f.Unlock()
	in := func(id, owner, list, closedAt string) *Task { x := task(id, owner, closedAt); x.List = list; return x }
	f.write(t, "alice", []*Task{in("h1", ops, "hiring", ""), in("t1", "", "team", ""), in("n1", "", "", "")},
		[]*Task{in("h2", ops, "hiring", "2026-06-01T10:00"), in("h3", ops, "hiring", "2026-03-02T10:00")}, "Set up")
	return f
}

// A group's lists and tasks are kept in its folder, as its pages are, and
// read as an owner of their own: "@ops".
func TestAGroupsTasksAreKeptInItsFolder(t *testing.T) {
	f := groupsStore(t)
	if g, ok := GroupOf("@ops"); !ok || g != "ops" {
		t.Errorf("GroupOf: %q %v", g, ok)
	}
	if _, ok := GroupOf(""); ok {
		t.Error("the shared files are a group's")
	}
	if g, ok := GroupOf("alice"); ok || g != "" {
		t.Error("a user's files are a group's")
	}
	if !strings.Contains(f.text(t, "groups/ops/_tasks/lists.json"), `{"name": "hiring", "view": "open", "owners": ["alice"]}`) {
		t.Errorf("the group's registry: %s", f.text(t, "groups/ops/_tasks/lists.json"))
	}
	if strings.Contains(f.text(t, "_tasks/lists.json"), "hiring") {
		t.Error("the group's list is in the registry everyone reads")
	}
	if got := f.ids(t, "groups/ops/_tasks/lists/hiring/tasks.json"); !slices.Equal(got, []string{"h1"}) {
		t.Errorf("its open tasks: %v", got)
	}
	if got := f.ids(t, "groups/ops/_tasks/lists/hiring/closed-2026-03.json"); !slices.Equal(got, []string{"h3"}) {
		t.Errorf("its archive: %v", got)
	}
	if got := f.ReadLists(GroupOwner("ops")).Find("hiring"); got == nil || got.Owner != "@ops" {
		t.Errorf("read back: %+v", got)
	}
}

// Who reads and writes a group's files is who is in it (Owners): every
// read of a user's leaves the others' groups out, and a write never touches
// them.
func TestOnlyAGroupsMembersReadItsTasks(t *testing.T) {
	f := groupsStore(t)
	if got := f.Owners(asker.User("carol")); !slices.Equal(got, []string{"", "carol", "@ops"}) {
		t.Errorf("carol's owners: %v", got)
	}
	if got := f.Owners(asker.User("bob")); !slices.Equal(got, []string{"", "bob"}) {
		t.Errorf("bob's owners: %v", got)
	}
	if got := f.EveryOwner(); !slices.Equal(got, []string{"", "alice", "bob", "carol", "@ops"}) {
		t.Errorf("every owner: %v", got)
	}
	open := func(user string) []string {
		out := []string{}
		for _, t := range f.Open(asker.User(user)) {
			out = append(out, t.ID+":"+t.Owner)
		}
		slices.Sort(out)
		return out
	}
	if got := open("carol"); !slices.Equal(got, []string{"h1:@ops", "n1:", "t1:"}) {
		t.Errorf("carol reads %v", got)
	}
	if got := open("bob"); !slices.Equal(got, []string{"n1:", "t1:"}) {
		t.Errorf("bob reads %v", got)
	}
	// A task of the group in a write of bob's is refused, nothing written.
	f.Lock()
	defer f.Unlock()
	err := f.WriteOpenLocked(asker.User("bob"), []*Task{{ID: "x", Title: "X", Owner: "@ops", List: "hiring"}}, "Sneak")
	if err == nil {
		t.Error("bob wrote into the group's folder")
	}
	if got := f.ids(t, "groups/ops/_tasks/lists/hiring/tasks.json"); !slices.Equal(got, []string{"h1"}) {
		t.Errorf("the group's tasks after: %v", got)
	}
}
