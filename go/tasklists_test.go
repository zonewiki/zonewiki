package main

import (
	"encoding/json"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/taskfiles"
	"zonewiki/internal/textsearch"
)

// writeTasks writes owner's open tasks as a file edited by hand has them.
func writeTasks(t *testing.T, owner string, tasks ...*Task) {
	t.Helper()
	if err := taskfiles.Write(taskFiles.OpenPath(owner), tasks); err != nil {
		t.Fatal(err)
	}
}

func oldTask(id, title string, tags ...string) *Task {
	return &Task{ID: id, Title: title, Tags: tags, CreatedAt: "2026-01-01T10:00:00"}
}

// openByID: the open tasks user sees, by id.
func openByID(user string) map[string]*Task {
	out := map[string]*Task{}
	for _, t := range taskFiles.Open(asUser(user)) {
		out[t.ID] = t
	}
	return out
}

func refsOf(lists []ListInfo) []string {
	out := []string{}
	for _, l := range lists {
		out = append(out, l.Ref)
	}
	return out
}

// mustList makes a list, unpinned (as an assistant's create_task_list does).
func mustList(t *testing.T, user, name string, private bool) string {
	t.Helper()
	ref, err := createListOp(asUser(user), name, private, false, "", "")
	if err != nil {
		t.Fatal(err)
	}
	return ref
}

func refusedWith(err error, kind refusal) bool {
	rf, ok := refusalOf(err)
	return ok && rf.kind == kind
}

// A list's name: one word, as an embed can write it; one a user sees is taken.
func TestCreateList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob", "vera")
	settings().Users["vera"].Role = roleViewer
	if ref := mustList(t, "alice", " #Shop List ", false); ref != "shop-list" {
		t.Errorf("made %q", ref)
	}
	if ref := mustList(t, "alice", "gifts", true); ref != "gifts" || !taskLists(asUser("alice"))["gifts"].Private() {
		t.Errorf("private list %q", ref)
	}
	if _, ok := taskLists(asUser("bob"))["gifts"]; ok {
		t.Error("bob sees alice's list")
	}
	for _, bad := range []string{"", "  ", "a,b", "a|b", "a=b", "a[b", `a"b`, "a/b", "none", " #None ", "inbox", " #Inbox "} {
		if _, err := createListOp(asUser("alice"), bad, false, false, "", ""); !refusedWith(err, refusedInvalid) {
			t.Errorf("%q: %v", bad, err)
		}
	}
	// A name of a list she sees, shared or her own, either way.
	for _, c := range []struct {
		name    string
		private bool
	}{{"Shop-List", false}, {"shop-list", true}, {"gifts", false}, {"gifts", true}} {
		if _, err := createListOp(asUser("alice"), c.name, c.private, false, "", ""); !refusedWith(err, refusedExists) {
			t.Errorf("%+v: %v", c, err)
		}
	}
	// Bob doesn't see her gifts: his shared one is made, and she has two.
	mustList(t, "bob", "gifts", false)
	if got := sortedKeys(taskLists(asUser("alice"))); !slices.Equal(got, []string{"gifts", "shared/gifts", "shop-list"}) {
		t.Errorf("alice's lists %v", got)
	}
	if _, err := createListOp(asUser("vera"), "x", false, false, "", ""); !refusedWith(err, refusedReadOnly) {
		t.Errorf("a viewer made a list: %v", err)
	}
}

// A task goes into a list by naming it; a new one may start private, and a
// private list's task is in its user's files. One that names no list is in
// its maker's Inbox, private too; a shared task is always in a list.
func TestTaskInAList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "shop", false)
	mustList(t, "alice", "gifts", true)
	milk := newTestTask(t, "alice", "Milk", false, "list", "Shop")
	cake := newTestTask(t, "alice", "Cake", false, "list", "gifts")
	loose := newTestTask(t, "alice", "Loose", false, "list", "", "tags", "shop") // a tag is a tag
	if milk.List != "shop" || milk.Owner != "" || cake.List != "gifts" || cake.Owner != "alice" || loose.List != "" || loose.Owner != "alice" {
		t.Fatalf("milk %+v, cake %+v, loose %+v", milk, cake, loose)
	}
	if !strings.Contains(fileText(t, taskFiles.PathIn("alice", "gifts", "tasks.json")), "Cake") || strings.Contains(fileText(t, taskFiles.PathIn("", "shop", "tasks.json")), "Cake") {
		t.Error("the private list's task isn't in her files")
	}
	set := func(id string, kv ...any) error {
		data := jsonObj{}
		for i := 0; i+1 < len(kv); i += 2 {
			data.Set(kv[i].(string), kv[i+1])
		}
		_, err := updateTask(asUser("alice"), id, data, "")
		return err
	}
	if err := set(milk.ID, "list", "nope"); err == nil {
		t.Error("a list that isn't there")
	}
	if err := set(milk.ID, "list", "gifts"); err == nil {
		t.Error("a shared task went into a private list")
	}
	if err := set(cake.ID, "list", "shop"); err == nil {
		t.Error("a private task was shared without saying so")
	}
	if err := set(cake.ID, "list", "gifts", "title", "Cake!"); err != nil { // the list it is in: no change, no question
		t.Error(err)
	}
	if err := set(cake.ID, "list", "shop", "share", true); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[cake.ID]; got == nil || got.List != "shop" || got.Owner != "" {
		t.Errorf("shared into shop: %+v", got)
	}
	// A shared task doesn't leave its list for none: that is an Inbox, and
	// what others have seen stays shared.
	for _, none := range []any{"", "inbox", "none", nil} {
		if err := set(milk.ID, "list", none); err != errSharedListed || openByID("bob")[milk.ID].List != "shop" {
			t.Errorf("a shared task out of its list (%v): %v", none, err)
		}
	}
	// A private one does: into her Inbox, still hers alone, no share needed;
	// and back into a private list.
	extra := newTestTask(t, "alice", "Extra", false, "list", "gifts")
	if err := set(extra.ID, "list", ""); err != nil {
		t.Errorf("a private task into the Inbox: %v", err)
	}
	if got := openByID("alice")[extra.ID]; got.List != "" || got.Owner != "alice" || openByID("bob")[extra.ID] != nil {
		t.Errorf("in her Inbox: %+v", got)
	}
	if err := set(extra.ID, "list", "gifts"); err != nil || openByID("alice")[extra.ID].List != "gifts" {
		t.Errorf("from the Inbox into her private list: %v", err)
	}
	// The Inbox's task is shared by filing it into a shared list, and only
	// when the write says so.
	if err := set(loose.ID, "list", "shop"); err == nil {
		t.Error("an Inbox task was shared without saying so")
	}
	if err := set(loose.ID, "list", "shop", "share", true); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[loose.ID]; got == nil || got.List != "shop" || got.Owner != "" {
		t.Errorf("filed into shop: %+v", got)
	}
	// Bob has no list "gifts": hers is not his to name.
	data := jsonObj{}
	data.Set("title", "Sneaky")
	data.Set("list", "gifts")
	if _, err := createTask(asUser("bob"), data, "c"); err == nil {
		t.Error("bob made a task in alice's list")
	}
}

// A rename changes the list's name on its tasks, in the pins, and where
// pages and tasks' text name it; nothing else of that name.
func TestRenameList(t *testing.T) {
	testWiki(t, map[string]string{
		"shopping":            "# Shopping\n\n[[tasks: list=shop view=open]]\n\nAlso [here](/tasks?list=shop&view=open), `[[tasks: list=shop]]`.\n",
		"other":               "# Other\n\n[[tasks: list=shopping tag=shop]]\n",
		"private/alice/gifts": "# Mine\n\n[[tasks: list=gifts]] and [[tasks: list=shop]]\n",
		"private/bob/gifts":   "# Bob's\n\n[[tasks: list=gifts]]\n",
	})
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "shop", false)
	mustList(t, "alice", "gifts", true)
	mustList(t, "bob", "gifts", true)
	milk := newTestTask(t, "alice", "Milk, see /tasks?list=shop", false, "list", "shop", "tags", "shop")
	cake := newTestTask(t, "alice", "Cake", false, "list", "gifts")
	bobs := newTestTask(t, "bob", "Bob's", false, "list", "gifts")
	for _, u := range []string{"alice", "bob"} {
		if err := pinListOp(asUser(u), "shop", "first"); err != nil {
			t.Fatal(err)
		}
	}
	if err := pinListOp(asUser("alice"), "gifts", "1"); err != nil {
		t.Fatal(err)
	}
	pagesMu.Lock()
	defer pagesMu.Unlock()
	if _, err := renameListOp(asUser("alice"), "shop", "gifts", ""); !refusedWith(err, refusedExists) {
		t.Errorf("renamed onto a list she sees: %v", err)
	}
	if _, err := renameListOp(asUser("bob"), "nope", "x", ""); !refusedWith(err, refusedNotFound) {
		t.Errorf("a list that isn't there: %v", err)
	}
	done, err := renameListOp(asUser("alice"), "shop", "Market", "")
	if err != nil {
		t.Fatal(err)
	}
	if done.Ref != "market" || done.Tasks != 1 || !slices.Equal(done.Pages, []string{"private/alice/gifts", "shopping"}) {
		t.Errorf("renamed: %+v", done)
	}
	got := openByID("alice")[milk.ID]
	if got.List != "market" || !slices.Equal(got.Tags, []string{"shop"}) || got.Title != "Milk, see /tasks?list=market" {
		t.Errorf("its task: %+v", got)
	}
	text, _ := readText(safePath("shopping"))
	if want := "# Shopping\n\n[[tasks: list=market view=open]]\n\nAlso [here](/tasks?list=market&view=open), `[[tasks: list=shop]]`.\n"; text != want {
		t.Errorf("the page:\n%s", text)
	}
	if text, _ := readText(safePath("other")); !strings.Contains(text, "list=shopping tag=shop") {
		t.Errorf("another list's page: %s", text)
	}
	if text, _ := readText(safePath("private/alice/gifts")); !strings.Contains(text, "[[tasks: list=gifts]] and [[tasks: list=market]]") {
		t.Errorf("her page: %s", text)
	}
	for _, u := range []string{"alice", "bob"} {
		if pins := refsOf(pinnedLists(asUser(u), taskLists(asUser(u)))); len(pins) == 0 || pins[0] != "market" {
			t.Errorf("%s's pins %v", u, pins)
		}
	}
	// Her private list: only hers, her pages, her pin.
	if _, err := renameListOp(asUser("alice"), "gifts", "presents", ""); err != nil {
		t.Fatal(err)
	}
	if openByID("alice")[cake.ID].List != "presents" || openByID("bob")[bobs.ID].List != "gifts" {
		t.Error("the private rename's tasks")
	}
	if text, _ := readText(safePath("private/alice/gifts")); !strings.Contains(text, "list=presents") {
		t.Errorf("her page: %s", text)
	}
	if text, _ := readText(safePath("private/bob/gifts")); !strings.Contains(text, "list=gifts") {
		t.Errorf("bob's page: %s", text)
	}
	if got := refsOf(pinnedLists(asUser("alice"), taskLists(asUser("alice")))); !slices.Equal(got, []string{"market", "presents"}) {
		t.Errorf("her pins %v", got)
	}
}

// Deleting a list moves its tasks, never drops them; a private list's go
// to a shared list only when the delete says share, and with no list named
// to their user's Inbox; a shared list's then go to #general.
func TestDeleteList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	for _, l := range []struct {
		name    string
		private bool
	}{{"shop", false}, {"market", false}, {"gifts", true}, {"wishes", true}} {
		mustList(t, "alice", l.name, l.private)
	}
	milk := newTestTask(t, "alice", "Milk", false, "list", "shop")
	cake := newTestTask(t, "alice", "Cake", false, "list", "gifts")
	if err := pinListOp(asUser("bob"), "shop", "1"); err != nil {
		t.Fatal(err)
	}
	if _, err := deleteListOp(asUser("alice"), "shop", "gifts", false, false, false, ""); err == nil {
		t.Error("shared tasks moved into a private list")
	}
	if _, err := deleteListOp(asUser("alice"), "shop", "shop", false, false, false, ""); err == nil {
		t.Error("moved into itself")
	}
	if _, err := deleteListOp(asUser("bob"), "gifts", "", true, false, false, ""); !refusedWith(err, refusedNotFound) {
		t.Errorf("bob deleted alice's list: %v", err)
	}
	if n, err := deleteListOp(asUser("alice"), "shop", "market", false, false, false, ""); err != nil || n != 1 {
		t.Fatalf("delete: %d, %v", n, err)
	}
	if _, ok := taskLists(asUser("alice"))["shop"]; ok || openByID("bob")[milk.ID].List != "market" {
		t.Error("shop is still there, or its task didn't move")
	}
	if got := pinnedLists(asUser("bob"), taskLists(asUser("bob"))); len(got) != 0 {
		t.Errorf("bob still pins it: %v", refsOf(got))
	}
	// Her private list: to another of hers, no question.
	if _, err := deleteListOp(asUser("alice"), "gifts", "wishes", false, false, false, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("alice")[cake.ID]; got.List != "wishes" || got.Owner != "alice" {
		t.Errorf("cake %+v", got)
	}
	// To no list: her Inbox. Its tasks stay hers alone, share or not.
	if _, err := deleteListOp(asUser("alice"), "wishes", "", false, false, false, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("alice")[cake.ID]; got == nil || got.List != "" || got.Owner != "alice" || openByID("bob")[cake.ID] != nil {
		t.Errorf("cake after the delete: %+v", got)
	}
	if !strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "Cake") || strings.Contains(fileText(t, taskFiles.OpenPath("")), "Cake") {
		t.Error("cake left her file")
	}
	if _, ok := taskLists(asUser("alice"))["wishes"]; ok {
		t.Error("wishes is still there")
	}
	// A private list's tasks into a shared one: only when the delete says share.
	mustList(t, "alice", "plans", true)
	plan := newTestTask(t, "alice", "Plan", false, "list", "plans")
	if _, err := deleteListOp(asUser("alice"), "plans", "market", false, false, false, ""); err == nil {
		t.Fatal("private tasks shared without saying so")
	}
	if _, err := deleteListOp(asUser("alice"), "plans", "market", true, false, false, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[plan.ID]; got == nil || got.List != "market" || got.Owner != "" || got.History[len(got.History)-2].Event != "shared" || got.Assignee != "alice" {
		t.Errorf("plan after the delete: %+v", got)
	}
	// A shared list with no list named: its tasks go to #general, made for them.
	if n, err := deleteListOp(asUser("bob"), "market", "", false, false, false, ""); err != nil || n != 2 {
		t.Fatalf("delete market: %d, %v", n, err)
	}
	if l, ok := taskLists(asUser("alice"))[generalListName]; !ok || l.Private() {
		t.Errorf("#general: %+v", taskLists(asUser("alice")))
	}
	for _, id := range []string{milk.ID, plan.ID} {
		if got := openByID("alice")[id]; got == nil || got.List != generalListName || got.Owner != "" {
			t.Errorf("after market went: %+v", got)
		}
	}
	// #general itself, while it has tasks, needs a list named; another takes them.
	if _, err := deleteListOp(asUser("alice"), generalListName, "", false, false, false, ""); !refusedWith(err, refusedInvalid) {
		t.Errorf("#general deleted into itself: %v", err)
	}
	mustList(t, "alice", "team", false)
	if _, err := deleteListOp(asUser("alice"), generalListName, "team", false, false, false, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[milk.ID]; got.List != "team" {
		t.Errorf("milk %+v", got)
	}
	// An empty #general goes without a list named.
	mustList(t, "alice", generalListName, false)
	if _, err := deleteListOp(asUser("alice"), generalListName, "", false, false, false, ""); err != nil {
		t.Errorf("an empty #general: %v", err)
	}
}

// Without users a deleted list's tasks are in no list, the wiki's Inbox.
func TestDeleteListWithoutUsers(t *testing.T) {
	testWiki(t, nil)
	mustList(t, "", "shop", false)
	milk := newTestTask(t, "", "Milk", false, "list", "shop")
	if n, err := deleteListOp(asker{}, "shop", "", false, false, false, ""); err != nil || n != 1 {
		t.Fatalf("delete: %d, %v", n, err)
	}
	if got := openByID("")[milk.ID]; got == nil || got.List != "" || got.Owner != "" || len(taskLists(asker{})) != 0 {
		t.Errorf("milk %+v, lists %v", got, sortedKeys(taskLists(asker{})))
	}
}

// A private list made shared takes its tasks with it; never the other way.
func TestShareList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "gifts", true)
	mustList(t, "alice", "shop", false)
	cake := newTestTask(t, "alice", "Cake", false, "list", "gifts")
	if _, err := shareListOp(asUser("alice"), "shop", "", ""); !refusedWith(err, refusedUnchanged) {
		t.Errorf("a shared list shared: %v", err)
	}
	if _, err := shareListOp(asUser("bob"), "gifts", "", ""); !refusedWith(err, refusedNotFound) {
		t.Errorf("bob shared alice's list: %v", err)
	}
	if _, err := shareListOp(asUser("alice"), "gifts", "", ""); err != nil {
		t.Fatal(err)
	}
	if l, ok := taskLists(asUser("bob"))["gifts"]; !ok || l.Private() {
		t.Errorf("bob's view of it: %+v", l)
	}
	if got := openByID("bob")[cake.ID]; got == nil || got.List != "gifts" || got.Owner != "" {
		t.Errorf("its task: %+v", got)
	}
	// A name a shared list has already: hers stays hers.
	mustList(t, "alice", "wishes", true)
	mustList(t, "bob", "wishes", false)
	if _, err := shareListOp(asUser("alice"), "wishes", "", ""); !refusedWith(err, refusedExists) {
		t.Errorf("shared onto a shared list's name: %v", err)
	}
}

// Pins are the user's own (without users the wiki's): one who set none has
// none, whatever anyone else pinned.
func TestPins(t *testing.T) {
	testWiki(t, nil)
	mustList(t, "", "shop", false)
	mustList(t, "", "work", false)
	if err := pinListOp(asker{}, "shop", "1"); err != nil {
		t.Fatal(err)
	}
	if err := pinListOp(asker{}, "work", "1"); err != nil {
		t.Fatal(err)
	}
	if got := refsOf(pinnedLists(asker{}, taskLists(asker{}))); !slices.Equal(got, []string{"shop", "work"}) {
		t.Errorf("without users: %v", got)
	}
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "gifts", true)
	pins := func(u string) []string { return refsOf(pinnedLists(asUser(u), taskLists(asUser(u)))) }
	if got := pins("bob"); len(got) != 0 {
		t.Errorf("bob has pins he didn't set: %v", got)
	}
	if err := pinListOp(asUser("alice"), "gifts", "1"); err != nil {
		t.Fatal(err)
	}
	if err := pinListOp(asUser("alice"), "shop", "1"); err != nil {
		t.Fatal(err)
	}
	if err := pinListOp(asUser("alice"), "work", "first"); err != nil {
		t.Fatal(err)
	}
	if got := pins("alice"); !slices.Equal(got, []string{"work", "gifts", "shop"}) {
		t.Errorf("alice's pins %v", got)
	}
	if err := pinListOp(asUser("alice"), "shop", "0"); err != nil {
		t.Fatal(err)
	}
	if got := pins("alice"); !slices.Equal(got, []string{"work", "gifts"}) || len(pins("bob")) != 0 {
		t.Errorf("after her unpin: alice %v, bob %v", got, pins("bob"))
	}
	for _, r := range []string{"gifts", "work"} {
		if err := pinListOp(asUser("alice"), r, "0"); err != nil {
			t.Fatal(err)
		}
	}
	if got := pins("alice"); len(got) != 0 {
		t.Errorf("she unpinned everything, and has %v", got)
	}
	if err := pinListOp(asUser("bob"), "gifts", "1"); !refusedWith(err, refusedNotFound) {
		t.Errorf("bob pinned alice's list: %v", err)
	}
	if err := pinListOp(asUser("alice"), "work", "maybe"); !refusedWith(err, refusedInvalid) {
		t.Errorf("pin=maybe: %v", err)
	}
}

// My tasks' rule, case by case: the cases the browser's rule runs through
// too (TasksCore.inMine, tests/test_tasks_core.py).
func TestInMineCases(t *testing.T) {
	raw, err := os.ReadFile("testdata/mine_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name string
		Task struct {
			List, Assignee string
			Private        bool
		}
		Out, Assigns, Want bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) == 0 {
		t.Fatalf("the cases: %v", err)
	}
	// anna asks.
	for _, c := range cases {
		task := &Task{List: c.Task.List, Assignee: c.Task.Assignee}
		if c.Task.Private {
			task.Owner = "anna"
		}
		if got := inMine(task, c.Out, asUser("anna"), c.Assigns); got != c.Want {
			t.Errorf("%s: %v, want %v", c.Name, got, c.Want)
		}
	}
}

// What is for today and what is overdue, case by case: the cases the
// browser's rule runs through too (TasksCore.isFocus, isOverdue).
func TestFocusCases(t *testing.T) {
	raw, err := os.ReadFile("testdata/focus_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Today string
		Task        struct {
			StartDate string `json:"start_date"`
			DueDate   string `json:"due_date"`
		}
		Focus, Overdue bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 10 {
		t.Fatalf("the cases: %v (%d)", err, len(cases))
	}
	for _, c := range cases {
		task := &Task{StartDate: c.Task.StartDate, DueDate: c.Task.DueDate}
		if f, o := taskFocus(task, c.Today), taskOverdue(task, c.Today); f != c.Focus || o != c.Overdue {
			t.Errorf("%s: focus %v overdue %v, want %v %v", c.Name, f, o, c.Focus, c.Overdue)
		}
	}
}

// The filter, case by case: the cases the browser's filter runs through too
// (TasksCore.parseFilter and matchesFilter, tests/test_tasks_core.py). anna
// asks, in a wiki with anna and bob.
func TestFilterCases(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "anna", "bob")
	raw, err := os.ReadFile("testdata/filter_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Q string
		Task    struct {
			Title, Description, List, Assignee, Importance string
			CreatedBy                                      string `json:"created_by"`
			Tags, Followers                                []string
			Private                                        bool
		}
		Want bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 40 {
		t.Fatalf("the cases: %v (%d)", err, len(cases))
	}
	for _, c := range cases {
		task := &Task{Title: c.Task.Title, Description: c.Task.Description, List: c.Task.List, Assignee: c.Task.Assignee,
			Importance: c.Task.Importance, CreatedBy: c.Task.CreatedBy, Tags: c.Task.Tags, Followers: c.Task.Followers}
		if c.Task.Private {
			task.Owner = "anna"
		}
		if got := parseTaskFilter(c.Q).matches(task, asUser("anna")); got != c.Want {
			t.Errorf("%s (%q): %v, want %v", c.Name, c.Q, got, c.Want)
		}
	}
}

// My tasks on a wiki: what is the user's: their own tasks (the Inbox, their
// private lists) and the shared ones assigned to them; without users every
// task. A list left out (home=off) is out of it; pins change nothing. Its
// counts are the Tasks links'.
func TestMyTasks(t *testing.T) {
	testWiki(t, nil)
	day := isoDay(today())
	// Without lists and users: every task.
	newTestTask(t, "", "Loose", false, "due_date", day)
	all := func(u string) []string {
		in, out := mineFilter(asUser(u), taskLists(asUser(u))), []string{}
		for _, task := range taskFiles.Open(asUser(u)) {
			if in(task) {
				out = append(out, task.Title)
			}
		}
		slices.Sort(out)
		return out
	}
	off := func(u, ref, v string) {
		t.Helper()
		if err := setListSettingsOp(asUser(u), ref, map[string]string{"home": v}); err != nil {
			t.Fatal(err)
		}
	}
	if got := all(""); !slices.Equal(got, []string{"Loose"}) {
		t.Errorf("without lists: %v", got)
	}
	// Without users: every list's tasks, pinned or not.
	mustList(t, "", "work", false)
	mustList(t, "", "shop", false)
	newTestTask(t, "", "Report", false, "list", "work")
	newTestTask(t, "", "Milk", false, "list", "shop", "due_date", day)
	if got := all(""); !slices.Equal(got, []string{"Loose", "Milk", "Report"}) {
		t.Errorf("nothing pinned: %v", got)
	}
	if err := pinListOp(asker{}, "work", "1"); err != nil {
		t.Fatal(err)
	}
	if got := all(""); !slices.Equal(got, []string{"Loose", "Milk", "Report"}) {
		t.Errorf("work pinned: %v", got)
	}
	if c := taskCounts(&Req{}); c.Count != 2 {
		t.Errorf("the Tasks links count %+v, want the two due tasks", c)
	}
	// But a list that is left out.
	off("", "shop", "off")
	if got := all(""); !slices.Equal(got, []string{"Loose", "Report"}) {
		t.Errorf("shop left out: %v", got)
	}
	if c := taskCounts(&Req{}); c.Count != 1 {
		t.Errorf("the Tasks links count %+v, want the one due task not left out", c)
	}
	if err := setListSettingsOp(asker{}, "shop", map[string]string{"home": "maybe"}); !refusedWith(err, refusedInvalid) {
		t.Errorf("home=maybe: %v", err)
	}
	// With users: a shared task is in My tasks of whom it is assigned to,
	// wherever it is (in a list left out too), and in nobody else's; one's own
	// tasks are, but those of a list left out.
	withUsers(t, "alice", "bob", "vera")
	settings().Users["vera"].Role = roleViewer
	mustList(t, "alice", "diary", true)
	mustList(t, "alice", "someday", true)
	newTestTask(t, "alice", "Bob's report", false, "list", "work", "assignee", "bob")
	newTestTask(t, "alice", "Alice's milk", false, "list", "shop", "assignee", "alice")
	capture := newTestTask(t, "alice", "Capture", false, "list", "")
	newTestTask(t, "alice", "Entry", false, "list", "diary")
	newTestTask(t, "alice", "Maybe", false, "list", "someday")
	if capture.Owner != "alice" || capture.List != "" {
		t.Fatalf("a capture: %+v", capture)
	}
	if got := all("alice"); !slices.Equal(got, []string{"Alice's milk", "Capture", "Entry", "Maybe"}) {
		t.Errorf("alice: %v", got)
	}
	off("alice", "someday", "off")
	if got := all("alice"); !slices.Equal(got, []string{"Alice's milk", "Capture", "Entry"}) {
		t.Errorf("alice, someday left out: %v", got)
	}
	if got := all("bob"); !slices.Equal(got, []string{"Bob's report"}) {
		t.Errorf("bob: %v", got)
	}
	// Pins change nothing.
	for _, pin := range []string{"1", "0"} {
		for _, ref := range []string{"diary", "someday", "shop"} {
			if err := pinListOp(asUser("alice"), ref, pin); err != nil {
				t.Fatal(err)
			}
		}
		if got := all("alice"); !slices.Equal(got, []string{"Alice's milk", "Capture", "Entry"}) {
			t.Errorf("alice, pin=%s: %v", pin, got)
		}
	}
	// A viewer, to whom no task is assigned: every task she sees but those of
	// a list left out.
	if got := all("vera"); !slices.Equal(got, []string{"Bob's report", "Loose", "Report"}) {
		t.Errorf("vera: %v", got)
	}
	// The scope by its name, as the routes and the counts ask for it.
	if paramsScope(mineParams(asUser("alice"))) != mineScope || paramsScope(mineScopeParams) != mineScope {
		t.Error("mine=1 isn't the scope of My tasks")
	}
	in := scopeFilter(asUser("bob"), mineScope, taskLists(asUser("bob")))
	if in(&Task{List: "work"}) || !in(&Task{List: "work", Assignee: "bob"}) || in(&Task{List: "work", Assignee: "alice"}) {
		t.Error("scopeFilter(mineScope)")
	}
}

// My tasks has settings of its own, the user's: not a list's, and not what
// a new task gets.
func TestMyTasksSettings(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "work", false)
	if p := mineParams(asUser("alice")); p.src == nil || p.src.raw != "" || p.Get("mine") != "1" {
		t.Fatalf("unset: %v %+v", p.m, p.src)
	}
	if err := setMineParams(asUser("alice"), "", map[string]string{"view": "open", "limit": "5"}); err != nil {
		t.Fatal(err)
	}
	p := mineParams(asUser("alice"))
	if p.Get("view") != "open" || p.Get("limit") != "5" || paramsScope(p) != mineScope {
		t.Errorf("alice's: %v", p.m)
	}
	if q := mineParams(asUser("bob")); q.Has("view") || q.Has("limit") {
		t.Errorf("bob has alice's settings: %v", q.m)
	}
	if !strings.Contains(fileText(t, taskFiles.ListsPath("alice")), `"mine": {"view": "open", "limit": "5"}`) {
		t.Errorf("her registry:\n%s", fileText(t, taskFiles.ListsPath("alice")))
	}
	if err := setMineParams(asUser("alice"), "", map[string]string{"limit": "9"}); err != errEmbedChanged {
		t.Errorf("a save from an old dialog: %v", err)
	}
	if err := setMineParams(asUser("alice"), p.src.raw, map[string]string{"limit": ""}); err != nil {
		t.Fatal(err)
	}
	if p := mineParams(asUser("alice")); p.Has("limit") || p.Get("view") != "open" {
		t.Errorf("after taking the limit out: %v", p.m)
	}
	for _, bad := range []map[string]string{{"new": "x"}, {"keep": "1"}, {"tag": "x"}, {"view": "week"}} {
		if err := setMineParams(asUser("alice"), mineParams(asUser("alice")).src.raw, bad); !refusedWith(err, refusedInvalid) {
			t.Errorf("%v: %v", bad, err)
		}
	}
}

// A list's settings are its registry's: what List settings… may set, each
// checked as an embed's would be.
func TestListSettings(t *testing.T) {
	testWiki(t, nil)
	mustList(t, "", "shop", false)
	if err := setListSettingsOp(asker{}, "shop", map[string]string{"limit": "8", "keep": "yes", "labels": "Catalogue,Shopping", "noshare": "1"}); err != nil {
		t.Fatal(err)
	}
	l := taskLists(asker{})["shop"]
	if l.Params.Get("limit") != "8" || l.Params.Get("keep") != "1" || l.Params.Get("labels") != "Catalogue,Shopping" || !l.NoShare {
		t.Errorf("settings %v noshare %v", l.Params.m, l.NoShare)
	}
	if err := setListSettingsOp(asker{}, "shop", map[string]string{"limit": "", "noshare": ""}); err != nil {
		t.Fatal(err)
	}
	if l := taskLists(asker{})["shop"]; l.Params.Has("limit") || l.NoShare || l.Params.Get("keep") != "1" {
		t.Errorf("after taking two out: %v", l.Params.m)
	}
	for _, bad := range []map[string]string{{"limit": "0"}, {"view": "week"}, {"tag": "x"}, {"list": "other"}, {"pin": "1"}} {
		if err := setListSettingsOp(asker{}, "shop", bad); !refusedWith(err, refusedInvalid) {
			t.Errorf("%v: %v", bad, err)
		}
	}
	if err := setListSettingsOp(asker{}, "nope", map[string]string{"limit": "3"}); !refusedWith(err, refusedNotFound) {
		t.Errorf("a list that isn't there: %v", err)
	}
	if !strings.Contains(fileText(t, filepath.Join(cfg.Data, "_tasks", "lists.json")), `{"name": "shop", "keep": "1", "labels": "Catalogue,Shopping"}`) {
		t.Errorf("the registry:\n%s", fileText(t, taskFiles.ListsPath("")))
	}
}

// A snapshot (an undo's, an import's) puts a task back where it was, and
// never makes a shared task private nor a private one shared: a private
// task whose list is gone is in its user's Inbox, a shared one's, with
// users, in #general.
func TestPlaceSnapshot(t *testing.T) {
	testWiki(t, nil)
	snap := func(list string, private bool) jsonObj {
		o := jsonObj{}
		o.Set("list", list)
		o.Set("private", private)
		return o
	}
	type place struct {
		list             string
		private          bool
		wantList, wantBy string
		general          bool
	}
	check := func(user string, cases []place) {
		t.Helper()
		lists := taskLists(asUser(user))
		for _, c := range cases {
			task := &Task{Assignee: "bob"}
			general := placeSnapshot(asUser(user), task, snap(c.list, c.private), lists)
			if task.List != c.wantList || task.Owner != c.wantBy || general != c.general || (task.Owner != "") != (task.Assignee == "") {
				t.Errorf("%q %+v: list %q owner %q general %v assignee %q", user, c, task.List, task.Owner, general, task.Assignee)
			}
		}
	}
	// Without users: nothing is private, and a task whose list is gone is in none.
	mustList(t, "", "shop", false)
	check("", []place{
		{"shop", false, "shop", "", false},
		{"shop", true, "shop", "", false},
		{"long-gone", false, "", "", false},
		{"long-gone", true, "", "", false},
		{"", false, "", "", false},
	})
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "gifts", true)
	check("alice", []place{
		{"shop", false, "shop", "", false},
		{"gifts", true, "gifts", "alice", false},
		{"gifts", false, generalListName, "", true},     // a shared task with her private list's name: #general
		{"shop", true, "", "alice", false},              // a private one whose list is shared: her Inbox
		{"long-gone", true, "", "alice", false},         // its list is gone
		{"", true, "", "alice", false},                  // it was in her Inbox
		{"long-gone", false, generalListName, "", true}, // a shared one's too
		{"", false, generalListName, "", true},          // a shared task is always in a list
	})
	// No list is made for a user's private tasks any more.
	if _, ok := taskLists(asUser("alice"))["private"]; ok {
		t.Error("a list private was made")
	}
}

// A new list is pinned for who made it, last (createListOp's pin): in their
// own registry; nobody else's.
func TestANewListIsPinnedForWhoMadeIt(t *testing.T) {
	testWiki(t, nil)
	pins := func(u string) []string { return refsOf(pinnedLists(asUser(u), taskLists(asUser(u)))) }
	make := func(user, name string, private bool) {
		t.Helper()
		if _, err := createListOp(asUser(user), name, private, true, "", ""); err != nil {
			t.Fatal(err)
		}
	}
	make("", "shop", false)
	make("", "work", false)
	if got := pins(""); !slices.Equal(got, []string{"shop", "work"}) {
		t.Errorf("without users: %v", got)
	}
	mustList(t, "", "quiet", false)
	if got := pins(""); !slices.Equal(got, []string{"shop", "work"}) {
		t.Errorf("an unpinned list is pinned: %v", got)
	}
	withUsers(t, "alice", "bob")
	make("alice", "team", false)
	make("alice", "gifts", true)
	if got := pins("alice"); !slices.Equal(got, []string{"team", "gifts"}) {
		t.Errorf("alice: %v", got)
	}
	if got := pins("bob"); len(got) != 0 {
		t.Errorf("bob has pins he didn't set: %v", got)
	}
}

// The pinned lists in another order: those named first, the rest as they
// were; nothing is pinned or unpinned by it, and the same order writes nothing.
func TestPinOrder(t *testing.T) {
	testWiki(t, nil)
	for _, n := range []string{"a", "b", "c", "d"} {
		if _, err := createListOp(asker{}, n, false, n != "d", "", ""); err != nil {
			t.Fatal(err)
		}
	}
	pins := func(u string) []string { return refsOf(pinnedLists(asUser(u), taskLists(asUser(u)))) }
	if err := pinOrderOp(asker{}, []string{"c", "d", "nope", "a", "c"}); err != nil {
		t.Fatal(err)
	}
	if got := pins(""); !slices.Equal(got, []string{"c", "a", "b"}) {
		t.Errorf("pins %v", got)
	}
	if err := pinOrderOp(asker{}, []string{"c", "a"}); err != nil {
		t.Errorf("the same order: %v", err)
	}
	// With users the order is the user's own.
	withUsers(t, "alice", "bob", "vera")
	for _, n := range []string{"a", "b", "c"} {
		if err := pinListOp(asUser("alice"), n, "1"); err != nil {
			t.Fatal(err)
		}
	}
	if err := pinOrderOp(asUser("alice"), []string{"c"}); err != nil {
		t.Fatal(err)
	}
	if got := pins("alice"); !slices.Equal(got, []string{"c", "a", "b"}) || len(pins("bob")) != 0 {
		t.Errorf("alice %v, bob %v", got, pins("bob"))
	}
}

// "inbox" names the Inbox where a list is named (a view's scope): the tasks
// in no list; "none" named them before and still does. In a wiki that has a
// list called so, from before the name was kept, it is that list.
func TestInboxScope(t *testing.T) {
	testWiki(t, nil)
	mustList(t, "", "shop", false)
	lists := taskLists(asker{})
	filed, loose := &Task{ID: "1", Title: "Filed", List: "shop"}, &Task{ID: "2", Title: "Loose"}
	if !isInbox(" Inbox ", lists) || !isInbox(" None ", lists) || isInbox("shop", lists) || isInbox("", lists) {
		t.Error("isInbox")
	}
	for _, ref := range []string{"inbox", "none"} {
		if !inScope(loose, ref, lists) || inScope(filed, ref, lists) {
			t.Errorf("inScope(%s)", ref)
		}
	}
	if !inScope(filed, "shop", lists) || inScope(loose, "shop", lists) {
		t.Error("inScope")
	}
	if c := countTasks(asker{}, []*Task{filed, loose, {ID: "3", Title: "Due", DueDate: isoDay(today())}}, lists, inboxParams()); c.Count != 1 || c.Focus != 1 {
		t.Errorf("the Inbox's counts %+v", c)
	}
	for _, name := range []string{"none", "inbox"} {
		if err := os.WriteFile(taskFiles.ListsPath(""), []byte(`{"version": 2, "lists": [{"name": "`+name+`"}, {"name": "shop"}]}`), 0o644); err != nil {
			t.Fatal(err)
		}
		lists = taskLists(asker{})
		own := &Task{ID: "4", Title: "In the list", List: name}
		if isInbox(name, lists) || !inScope(own, name, lists) || inScope(loose, name, lists) {
			t.Errorf("a list called %s is that list", name)
		}
	}
}

// The Inbox: a task that names no list. With users it is its maker's alone,
// kept in their files, with no list made for it; without users it is the
// wiki's, in the shared file. A task there has no assignee.
func TestInbox(t *testing.T) {
	testWiki(t, nil)
	if inboxOwner(asker{}) != "" {
		t.Error("an Inbox owner without users")
	}
	loose := newTestTask(t, "", "Loose", false)
	if loose.List != "" || loose.Owner != "" || !strings.Contains(fileText(t, taskFiles.OpenPath("")), "Loose") {
		t.Errorf("without users: %+v", loose)
	}
	withUsers(t, "alice", "bob", "vera")
	settings().Users["vera"].Role = roleViewer
	if inboxOwner(asUser("alice")) != "alice" || inboxOwner(asUser(visitor)) != "" {
		t.Error("inboxOwner")
	}
	make := func(user string, kv ...any) (*Task, error) {
		data := jsonObj{}
		for i := 0; i+1 < len(kv); i += 2 {
			data.Set(kv[i].(string), kv[i+1])
		}
		return createTask(asUser(user), data, "Create task")
	}
	// No list, an empty one, null, "inbox" and "none": her Inbox.
	for i, list := range []any{"-", "", nil, "inbox", " Inbox ", "none"} {
		kv := []any{"title", "Capture " + string(rune('a'+i))}
		if list != "-" {
			kv = append(kv, "list", list)
		}
		task, err := make("alice", kv...)
		if err != nil || task.List != "" || task.Owner != "alice" {
			t.Errorf("list %v: %+v, %v", list, task, err)
			continue
		}
		if _, sees := openByID("bob")[task.ID]; sees {
			t.Errorf("list %v: bob sees alice's capture", list)
		}
		if !mineFilter(asUser("alice"), taskLists(asUser("alice")))(task) {
			t.Errorf("list %v: her capture isn't in her My tasks", list)
		}
	}
	if !strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "Capture a") || strings.Contains(fileText(t, taskFiles.OpenPath("")), "Capture") {
		t.Error("her captures aren't in her files alone")
	}
	// No list is made for them, and nothing is pinned.
	if len(taskLists(asUser("alice"))) != 0 || len(pinnedLists(asUser("alice"), taskLists(asUser("alice")))) != 0 || fileText(t, taskFiles.ListsPath("alice")) != "" {
		t.Errorf("her lists %v", sortedKeys(taskLists(asUser("alice"))))
	}
	// An Inbox task is nobody else's: no assignee, when made or later, not
	// even oneself.
	for _, who := range []string{"bob", "alice", "me"} {
		if _, err := make("alice", "title", "For "+who, "assignee", who); !refusedWith(err, refusedInvalid) || !strings.Contains(err.Error(), "shared list") {
			t.Errorf("an Inbox task for %s: %v", who, err)
		}
	}
	capture, _ := make("alice", "title", "Mine")
	set := jsonObj{}
	set.Set("assignee", "bob")
	if _, err := updateTask(asUser("alice"), capture.ID, set, ""); !refusedWith(err, refusedInvalid) {
		t.Errorf("an Inbox task assigned: %v", err)
	}
	// Filed into a shared list with share, it can be, in the same write.
	mustList(t, "alice", "team", false)
	set.Set("list", "team")
	if _, err := updateTask(asUser("alice"), capture.ID, set, ""); err == nil {
		t.Error("filed without share")
	}
	set.Set("share", true)
	if _, err := updateTask(asUser("alice"), capture.ID, set, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[capture.ID]; got == nil || got.List != "team" || got.Owner != "" || got.Assignee != "bob" {
		t.Errorf("filed: %+v", got)
	}
	// A viewer makes no task, so has no Inbox to fill.
	if _, err := make("vera", "title", "Hers"); !refusedWith(err, refusedReadOnly) {
		t.Errorf("a viewer's task: %v", err)
	}
	// The task made before there were users is shared and in no list: a
	// stray until the next start, which puts it in #general.
	if got := openByID("bob")[loose.ID]; got == nil || got.Owner != "" || got.List != "" {
		t.Errorf("the task from before: %+v", got)
	}
	if err := fileInGeneral(); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[loose.ID]; got == nil || got.Owner != "" || got.List != generalListName {
		t.Errorf("the task from before, after a start: %+v", got)
	}
}

// writeRegistry writes owner's lists.json as written by hand.
func writeRegistry(t *testing.T, owner, text string) {
	t.Helper()
	if err := os.MkdirAll(taskFiles.Dir(owner), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(taskFiles.ListsPath(owner), []byte(text), 0o666); err != nil {
		t.Fatal(err)
	}
}

// taskFilesText: every owner's task files and registries, to see whether a
// start changed any.
func taskFilesText(t *testing.T) string {
	t.Helper()
	out := ""
	for _, o := range taskFiles.EveryOwner() {
		out += fileText(t, taskFiles.OpenPath(o)) + fileText(t, taskFiles.ClosedPath(o)) + fileText(t, taskFiles.ListsPath(o))
	}
	return out
}

// startAgain: a second start changes nothing.
func startAgain(t *testing.T) {
	t.Helper()
	before := taskFilesText(t)
	if err := fileInGeneral(); err != nil {
		t.Fatal(err)
	}
	if after := taskFilesText(t); after != before {
		t.Errorf("a second start changed the files:\n%s", after)
	}
}

func inList(list string, tasks ...*Task) []*Task {
	for _, t := range tasks {
		t.List = list
	}
	return tasks
}

// With users a shared task is always in a list: a start that reads the task
// files puts the shared tasks without one, open and closed, in #general,
// made for them and pinned for the wiki, and files them in its folder (a
// wiki whose users were turned on later). A user's own tasks without a list
// are their Inbox and stay.
func TestSharedTasksWithoutAListGoToGeneral(t *testing.T) {
	testWiki(t, nil)
	writeRegistry(t, "", `{"lists": [{"name": "work"}], "pinned": ["work"]}`)
	writeTasks(t, "", oldTask("1", "Loose"), inList("work", oldTask("2", "Report"))[0])
	if err := taskfiles.Write(taskFiles.ClosedPath(""), []*Task{{ID: "3", Title: "Done", ClosedAt: "2026-02-01T10:00:00", CreatedAt: "2026-01-01T10:00:00"}}); err != nil {
		t.Fatal(err)
	}
	// Without users: nothing moves, and no list is made.
	if err := fileInGeneral(); err != nil {
		t.Fatal(err)
	}
	if got := openByID("")["1"]; got.List != "" || len(taskLists(asker{})) != 1 {
		t.Fatalf("without users: %+v, lists %v", got, sortedKeys(taskLists(asker{})))
	}
	startAgain(t)
	// Users are turned on: the next start files them.
	withUsers(t, "alice", "bob")
	writeTasks(t, "alice", oldTask("a1", "Hers"))
	if err := fileInGeneral(); err != nil {
		t.Fatal(err)
	}
	a := openByID("alice")
	if a["1"].List != generalListName || a["1"].Owner != "" || a["2"].List != "work" || a["a1"].List != "" || a["a1"].Owner != "alice" {
		t.Errorf("the tasks: %+v %+v %+v", a["1"], a["2"], a["a1"])
	}
	general := ""
	for _, f := range taskFiles.Files([]string{""}) {
		if filepath.Dir(f.Path) == filepath.Dir(taskFiles.PathIn("", generalListName, "tasks.json")) {
			general += fileText(t, f.Path)
		}
	}
	if !strings.Contains(general, `"title": "Done"`) || !strings.Contains(general, `"title": "Loose"`) {
		t.Errorf("the tasks aren't filed in #general's folder:\n%s", general)
	}
	if text := fileText(t, taskFiles.OpenPath("")); strings.Contains(text, "Loose") {
		t.Errorf("the open task is still in no list's file:\n%s", text)
	}
	if l, ok := taskLists(asUser("bob"))[generalListName]; !ok || l.Private() || l.LeftOut() {
		t.Errorf("#general: %+v", l)
	}
	if got := taskFiles.ReadLists("").Pinned; !slices.Equal(got, []string{"work", generalListName}) {
		t.Errorf("the shared registry's pins %v", got)
	}
	startAgain(t)
	// A stray that turns up later (a file edited by hand) goes there too;
	// the list is there, and an unpin of it stays.
	if err := pinListOp(asker{}, generalListName, "0"); err != nil {
		t.Fatal(err)
	}
	writeTasks(t, "", oldTask("9", "Stray"))
	if err := fileInGeneral(); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")["9"]; got == nil || got.List != generalListName {
		t.Errorf("the stray: %+v", got)
	}
	if got := taskFiles.ReadLists("").Pinned; !slices.Equal(got, []string{"work"}) {
		t.Errorf("#general was pinned again: %v", got)
	}
	startAgain(t)
}

// A shared task file that can't be read stops the start's #general before
// anything is written, and the start leaves no mark, so the next one reads
// the task files again.
func TestGeneralStopsAtAFileItCantRead(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice")
	writeTasks(t, "", oldTask("1", "Loose"))
	broken := taskFiles.PathIn("", "work", "tasks.json")
	if err := os.MkdirAll(filepath.Dir(broken), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(broken, []byte("[ half"), 0o666); err != nil {
		t.Fatal(err)
	}
	before := fileText(t, taskFiles.OpenPath(""))
	if err := fileInGeneral(); err == nil || !strings.Contains(err.Error(), broken) {
		t.Errorf("the error: %v", err)
	}
	if fileText(t, taskFiles.OpenPath("")) != before || exists(taskFiles.ListsPath("")) {
		t.Error("something was written")
	}
	setUpTasks()
	if exists(tasksCheckedPath()) {
		t.Error("the start left its mark")
	}
}

// A wiki with users that never had a list or a registry: its shared tasks
// without a list go to #general at start all the same.
func TestGeneralInAWikiWithoutARegistry(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice")
	if err := os.MkdirAll(taskFiles.Dir(""), 0o777); err != nil {
		t.Fatal(err)
	}
	writeTasks(t, "", oldTask("1", "Loose"))
	if err := fileInGeneral(); err != nil {
		t.Fatal(err)
	}
	if got := openByID("alice")["1"]; got == nil || got.List != generalListName || got.Owner != "" {
		t.Errorf("the task: %+v", got)
	}
	if text := fileText(t, taskFiles.ListsPath("")); strings.Contains(text, "version") || !strings.Contains(text, `"pinned": ["general"]`) {
		t.Errorf("the registry:\n%s", text)
	}
	startAgain(t)
}

// A start reads every task file to file tasks and set up the lists once;
// from then on, while git says the task files are as Zonewiki left them, a
// start reads the open tasks' files and this month's closed ones, not the
// archives. A file changed by hand, users turned on or off, or no mark, and
// it reads them again.
func TestAStartReadsTheArchivesWhenItMust(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	taskMarkDelay = 10 * time.Millisecond
	t.Cleanup(func() { taskMarkDelay = 3 * time.Second })
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "shop", false)
	done := newTestTask(t, "alice", "Milk", false, "list", "shop")
	newTestTask(t, "alice", "Eggs", false, "list", "shop")
	closeIt := jsonObj{}
	closeIt.Set("completed", true)
	if _, err := updateTask(asUser("alice"), done.ID, closeIt, ""); err != nil {
		t.Fatal(err)
	}
	// Closed years ago: an archive.
	closedFile := taskFiles.PathIn("", "shop", "closed.json")
	tasks := taskfiles.ReadLenient(closedFile)
	tasks[0].ClosedAt = "2001-05-01T10:00:00"
	if err := taskfiles.Write(closedFile, tasks); err != nil {
		t.Fatal(err)
	}
	archive := taskFiles.PathIn("", "shop", "closed-2001-05.json")
	start := func() (parsed int64, readArchive bool) {
		taskfiles.ForgetParsed()
		before := taskfiles.Parsed()
		setUpTasks()
		return taskfiles.Parsed() - before, taskfiles.KeepsParsed(archive)
	}
	if tasksChecked() {
		t.Fatal("checked before any start")
	}
	start() // files the old task under its month, and leaves the mark
	if !exists(archive) || !tasksChecked() {
		t.Fatalf("after the first start: archive %v, checked %v", exists(archive), tasksChecked())
	}
	if n, read := start(); read {
		t.Errorf("a start with nothing to do read the archive (%d files parsed)", n)
	}
	// A task file changed by hand: read again, and written in Zonewiki's format.
	raw, _ := os.ReadFile(archive)
	if err := os.WriteFile(archive, append([]byte(" "), raw...), 0o666); err != nil {
		t.Fatal(err)
	}
	if tasksChecked() {
		t.Error("checked with a task file changed by hand")
	}
	if _, read := start(); !read || !tasksChecked() {
		t.Errorf("after a hand edit: read %v, checked %v", read, tasksChecked())
	}
	// A task file committed by hand (or pulled): no commit of Zonewiki's.
	raw, _ = os.ReadFile(archive)
	if err := os.WriteFile(archive, append([]byte("\n"), raw...), 0o666); err != nil {
		t.Fatal(err)
	}
	if r := runGit("commit", "-qam", "By hand"); r.Code != 0 || tasksChecked() {
		t.Errorf("checked with a task file committed by hand (%d %s)", r.Code, r.Stderr)
	}
	start()
	// Zonewiki's own commit of a task file: the mark follows it.
	title := jsonObj{}
	title.Set("title", "Eggs, a dozen")
	for _, x := range taskFiles.Open(asUser("alice")) {
		if x.Title == "Eggs" {
			if _, err := updateTask(asUser("alice"), x.ID, title, ""); err != nil {
				t.Fatal(err)
			}
		}
	}
	for i := 0; i < 200 && !tasksChecked(); i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if !tasksChecked() {
		mark, _ := os.ReadFile(tasksCheckedPath())
		t.Errorf("not checked after Zonewiki's own commit of a task file: mark %q, head %q, status %q, log %q", mark,
			runGit("rev-parse", "HEAD").Stdout, runGit("status", "--porcelain", "--untracked-files=all").Stdout,
			runGit("log", "--format=%h %s", "-4").Stdout)
	}
	// Users turned off: the mark is of a wiki with users.
	settings().Users = nil
	if tasksChecked() {
		t.Error("checked though the wiki has no users now")
	}
	// Without git nothing says that the files didn't change.
	withUsers(t, "alice", "bob")
	start()
	cfg.GitEnabled = false
	if tasksChecked() {
		t.Error("checked without git")
	}
}

// The search keeps a task's record, not the task: what is long (its
// description, comments, history) is read from its file for a hit. Docs
// are made file by file: a change makes one file's anew and leaves the
// others as they are, unread; an archive's are kept in Zonewiki's folder, so a
// start knows the wiki's closed tasks of years without reading them, and
// whose they are is the folder's to say, never the saved file's.
func TestTheSearchKeepsRecordsOfTasks(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "shop", false)
	mustList(t, "alice", "diary", true)
	milk := newTestTask(t, "alice", "Milk", false, "list", "shop", "description", "Oat, the barista kind")
	old := newTestTask(t, "alice", "Old errand", false, "list", "shop", "description", "Zephyrscale audit")
	hers := newTestTask(t, "alice", "Dear diary", false, "list", "diary", "description", "Quillfeather notes")
	if _, err := addComment(asUser("bob"), milk.ID, "Two litres please", "Comment on task"); err != nil {
		t.Fatal(err)
	}
	// Two closed years ago, a shared one and one of hers: archives.
	closeIt := jsonObj{}
	closeIt.Set("completed", true)
	for _, id := range []string{old.ID, hers.ID} {
		if _, err := updateTask(asUser("alice"), id, closeIt, ""); err != nil {
			t.Fatal(err)
		}
	}
	for _, f := range []string{taskFiles.PathIn("", "shop", "closed.json"), taskFiles.PathIn("alice", "diary", "closed.json")} {
		tasks := taskfiles.ReadLenient(f)
		tasks[0].ClosedAt = "2001-05-01T10:00:00"
		if err := taskfiles.Write(f, tasks); err != nil {
			t.Fatal(err)
		}
	}
	taskFiles.ArchiveOldClosed()
	archive, herArchive := taskFiles.PathIn("", "shop", "closed-2001-05.json"), taskFiles.PathIn("alice", "diary", "closed-2001-05.json")
	if !exists(archive) || !exists(herArchive) {
		t.Fatal("no archives to test with")
	}
	byID := func() map[string]*SearchDoc {
		out := map[string]*SearchDoc{}
		for _, d := range taskSearchDocs() {
			out[d.taskID()] = d
		}
		return out
	}
	docs := byID()
	d := docs[milk.ID]
	if d == nil || d.Task.Description != "" || d.Task.Comments != nil || d.Task.History != nil || d.Task.Title != "Milk" || d.Task.List != "shop" {
		t.Fatalf("the record: %+v", d.Task)
	}
	if text, ok := d.taskText(); !ok || !strings.Contains(text, "barista") || !strings.Contains(text, "Two litres") {
		t.Errorf("its text, from its file: %q", text)
	}
	if full := d.wholeTask(); full.Description == "" || len(full.Comments) != 1 || len(full.History) == 0 {
		t.Errorf("the whole task: %+v", full)
	}
	if !slices.Contains(d.tokens(), "barista") {
		t.Errorf("its tokens: %v", d.tokens())
	}
	if docs[hers.ID].Task.Owner != "alice" || docs[old.ID].Task.Owner != "" || docs[old.ID].taskStatus() != "done" || docs[old.ID].taskYear() != "2001" {
		t.Errorf("the archives' docs: %+v %+v", docs[hers.ID].Task, docs[old.ID].tk)
	}
	// A change of one task: its file's docs anew, the others the docs they were.
	title := jsonObj{}
	title.Set("title", "Oat milk")
	if _, err := updateTask(asUser("alice"), milk.ID, title, ""); err != nil {
		t.Fatal(err)
	}
	after := byID()
	if after[milk.ID] == d || after[milk.ID].Task.Title != "Oat milk" || after[old.ID] != docs[old.ID] || after[hers.ID] != docs[hers.ID] {
		t.Error("a change of one task: its doc is the old one, or another's is a new one")
	}
	// A start: nothing parsed, nothing known. The archives' docs come from
	// Zonewiki's folder, with their keys; the archives aren't read.
	taskDocs.mu.Lock()
	taskDocs.done, taskDocs.docs, taskDocs.files = false, nil, nil
	taskDocs.mu.Unlock()
	taskfiles.ForgetParsed()
	again := byID()
	if taskfiles.KeepsParsed(archive) || taskfiles.KeepsParsed(herArchive) {
		t.Error("a start read the archives for the search's docs")
	}
	for _, id := range []string{old.ID, hers.ID} {
		a, b := again[id], after[id]
		if a == nil || a.key != b.key || *a.tk != *b.tk || a.Task.Title != b.Task.Title || a.Task.Owner != b.Task.Owner || a.Task.ClosedAt != b.Task.ClosedAt {
			t.Errorf("an archive's doc from Zonewiki's folder: %+v %+v, was %+v %+v", a.Task, a.tk, b.Task, b.tk)
		}
	}
	if text, ok := again[old.ID].taskText(); !ok || !strings.Contains(text, "Zephyrscale") {
		t.Errorf("an archived task's text, read when asked for: %q", text)
	}
	// What is saved names no owner: hers is hers by its folder alone.
	// What is saved has her record; whose it is comes from the archive's folder.
	if raw, _ := os.ReadFile(archiveDocsPath(herArchive)); !strings.Contains(string(raw), "Dear diary") || strings.Contains(string(raw), "Quillfeather") {
		t.Errorf("what is saved of her archive: %q", raw)
	}
	if !canSeeTask(asUser("alice"), again[hers.ID].Task) || canSeeTask(asUser("bob"), again[hers.ID].Task) {
		t.Error("who sees her archived task")
	}
	// A start's first search: by the index file, which has every task's
	// words; an archive is read for a hit in it, not to search.
	if got := retrieve(&Req{user: asUser("alice")}, "barista", 5, "", false); len(got) == 0 {
		t.Fatal("no hit before the start")
	}
	restartSearch()
	taskDocs.mu.Lock()
	taskDocs.done, taskDocs.docs, taskDocs.files = false, nil, nil
	taskDocs.mu.Unlock()
	taskfiles.ForgetParsed()
	before := taskfiles.Parsed()
	if got := retrieve(&Req{user: asUser("alice")}, "barista", 5, "", false); len(got) == 0 || got[0].taskID() != milk.ID {
		t.Errorf("a start's search for an open task: %d hits", len(got))
	}
	// (Her own archive is read: what is private is in no index file, its
	// words are added for its user at each search's index.)
	if taskfiles.KeepsParsed(archive) {
		t.Errorf("a start's first search read the shared archive (%d files parsed)", taskfiles.Parsed()-before)
	}
	if got := retrieve(&Req{user: asUser("alice")}, "zephyrscale", 5, "", false); len(got) == 0 || got[0].taskID() != old.ID {
		t.Errorf("a search for an archived task: %d hits", len(got))
	}
	if got := retrieve(&Req{user: asUser("bob")}, "quillfeather", 5, "", false); len(got) != 0 {
		t.Errorf("bob finds her archived task: %d hits", len(got))
	}
	if got := retrieve(&Req{user: asUser("alice")}, "quillfeather", 5, "", false); len(got) == 0 || got[0].taskID() != hers.ID {
		t.Errorf("she doesn't find her archived task: %d hits", len(got))
	}
	// Her own archive was read for her index once; a change elsewhere makes
	// her index anew without reading it again (viewTokens).
	taskfiles.ForgetParsed()
	other := jsonObj{}
	other.Set("title", "Oat milk, two litres")
	if _, err := updateTask(asUser("alice"), milk.ID, other, ""); err != nil {
		t.Fatal(err)
	}
	if got := retrieve(&Req{user: asUser("alice")}, "litres", 5, "", false); len(got) == 0 || got[0].taskID() != milk.ID {
		t.Errorf("a search after a change: %d hits", len(got))
	}
	if taskfiles.KeepsParsed(herArchive) {
		t.Error("her index made anew read her archive again")
	}
	// The archive changes: what was saved is of another file, and is not taken.
	if _, err := updateTask(asUser("alice"), old.ID, title, ""); err != nil {
		t.Fatal(err)
	}
	if got := byID()[old.ID]; got.Task.Title != "Oat milk" {
		t.Errorf("after the archive changed: %+v", got.Task)
	}
}

// The search keeps nothing per viewer: each user's private tasks are one
// layer, made once and kept while they are the same tasks, however many
// others search in between, and everyone finds their own and nobody else's.
func TestTheSearchKeepsNothingPerViewer(t *testing.T) {
	testWiki(t, nil)
	var names []string
	for i := 0; i < 12; i++ {
		names = append(names, "user"+string(rune('a'+i)))
	}
	withUsers(t, names...)
	mustList(t, names[0], "team", false)
	newTestTask(t, names[0], "Shared zephyrscale audit", false, "list", "team")
	for _, u := range names {
		newTestTask(t, u, "Own quillfeather of "+u, true)
	}
	restartSearch()
	finds := func(u string) (own, others int) {
		for _, d := range retrieve(&Req{user: asUser(u)}, "quillfeather", 50, "", false) {
			if d.Task.Owner == u {
				own++
			} else {
				others++
			}
		}
		return
	}
	// The layers made, of whichever index the search asks (the sections').
	layers := func() map[docPlace]*textsearch.Layer {
		made := map[docPlace]*textsearch.Layer{}
		for _, c := range []*indexCache{&searchIndexCache, &chunkIndexCache} {
			c.mu.Lock()
			for p, l := range c.places {
				if l.layer != nil {
					if made[p] != nil {
						t.Fatal("both indexes are asked: tell them apart here")
					}
					made[p] = l.layer
				}
			}
			c.mu.Unlock()
		}
		return made
	}
	var first map[docPlace]*textsearch.Layer
	for round := 0; round < 2; round++ {
		for _, u := range names {
			if own, others := finds(u); own != 1 || others != 0 {
				t.Fatalf("round %d, %s finds %d of their own and %d of others", round, u, own, others)
			}
			if got := retrieve(&Req{user: asUser(u)}, "zephyrscale", 5, "", false); len(got) != 1 {
				t.Fatalf("%s finds %d shared tasks", u, len(got))
			}
		}
		if round == 0 {
			first = layers()
			if len(first) != len(names) {
				t.Fatalf("%d layers made, want one for each of the %d users' tasks", len(first), len(names))
			}
		}
	}
	// The second round made none anew: every viewer's is still the one.
	if again := layers(); !maps.Equal(first, again) {
		t.Error("a layer was made again though its tasks are the same")
	}
	// One user's new private task makes their layer anew, and only theirs.
	newTestTask(t, names[3], "Own quillfeather again", true)
	for _, u := range names {
		want := 1
		if u == names[3] {
			want = 2
		}
		if own, others := finds(u); own != want || others != 0 {
			t.Fatalf("after a new task, %s finds %d of their own and %d of others", u, own, others)
		}
	}
	changed := 0
	for p, l := range layers() {
		if first[p] != l {
			changed++
			if p.name != names[3] {
				t.Errorf("the layer of %s was made again for a task of %s", p.name, names[3])
			}
		}
	}
	if changed != 1 {
		t.Errorf("%d layers made anew, want the one whose tasks changed", changed)
	}
}
