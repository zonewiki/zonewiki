package main

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"zonewiki/internal/taskfiles"
)

// writeTasks writes owner's open tasks as a wiki from before has them.
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
	for _, t := range taskFiles.Open(user) {
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

// mustList makes a list, as New list… does.
func mustList(t *testing.T, user, name string, private bool) string {
	t.Helper()
	ref, err := createListOp(user, name, private, "")
	if err != nil {
		t.Fatal(err)
	}
	return ref
}

func refusedWith(err error, kind refusal) bool {
	rf, ok := refusalOf(err)
	return ok && rf.kind == kind
}

// A wiki from before lists were places: each list a page declared (outside
// code) becomes a list with its first page's settings, each task gets the
// first A–Z of its tags that is a list as its list and loses that tag, the
// pins move to the registry and leave the pages.
func TestListsTakenOverFromPages(t *testing.T) {
	testWiki(t, map[string]string{
		"shop":        "# Shop\n\n[[tasks: list=Groceries keep=1 labels=Catalogue,,Shopping limit=8 tag=weekly pin=1]]\n",
		"a-list":      "# A\n\n[[tasks: list=groceries view=open]]\n", // first A–Z: its settings count
		"code":        "# Code\n\n`[[tasks: list=nope]]`\n\n```\n[[tasks: list=nada pin=1]]\n```\n",
		"home":        "# Home\n\n[[tasks: list=chores pin=default]]\n",
		"secret/plan": "# Plan\n\n[[tasks: list=plans]]\n", // a page not for sharing
		"me":          "# Me\n\n[[tasks: tag=me pin=1]]\n", // a pinned view that is no list
		// Views by a list's tag, which its tasks lose: they show the list.
		"views": "# Views\n\n[[tasks: tag=Groceries]]\n\n[[tasks: tag=weekly,#groceries view=open]]\n\n" +
			"[[tasks: tag=-chores]] [[tasks: tag=chores|work]] [[tasks: tag=work]] `[[tasks: tag=chores]]`\n\n" +
			"[Chores](/tasks?tag=chores&view=open), [both](/tasks?tag=chores%2Cwork), [work](/tasks?tag=work), [set](/tasks?list=plans&tag=chores)\n",
	})
	writeTasks(t, "",
		oldTask("1", "Milk", "Groceries", "weekly"),
		oldTask("2", "Both", "groceries", "chores"),
		oldTask("3", "Plain", "work"),
		&Task{ID: "4", Title: "Stale", List: "long-gone", Tags: []string{"x"}})
	if err := os.WriteFile(oldAllTasksFile(), []byte(`{"all": {"view": "open", "limit": "5"}}`), 0o666); err != nil {
		t.Fatal(err)
	}
	migrateLists()
	if listsUnmigrated {
		t.Fatal("the migration failed")
	}
	lists := taskLists("")
	if got := sortedKeys(lists); !slices.Equal(got, []string{"chores", "groceries", "plans"}) {
		t.Fatalf("lists %v", got)
	}
	g := lists["groceries"].Params
	if g.Get("view") != "open" || g.Get("keep") != "" || g.Get("limit") != "" || g.Get("list") != "groceries" {
		t.Errorf("groceries has %v: the first page A–Z is a-list", g.m)
	}
	if !lists["plans"].NoShare || lists["chores"].NoShare {
		t.Error("noshare: a list whose page was not for sharing stays out of sharing mode")
	}
	tasks := openByID("")
	for id, want := range map[string][2]string{"1": {"groceries", "weekly"}, "2": {"chores", "groceries"}, "3": {"", "work"}, "4": {"", "x"}} {
		if got := tasks[id]; got.List != want[0] || strings.Join(got.Tags, ",") != want[1] {
			t.Errorf("task %s: list %q tags %v, want %v", id, got.List, got.Tags, want)
		}
	}
	// The pins: the default one first, then by name; code and a view that is no list aside.
	if got := refsOf(pinnedLists("", lists)); !slices.Equal(got, []string{"chores", "groceries"}) {
		t.Errorf("pinned %v", got)
	}
	for _, slug := range []string{"shop", "home", "me"} {
		if text, _ := readText(safePath(slug)); strings.Contains(text, "pin=") {
			t.Errorf("%s keeps its pin=: %q", slug, text)
		}
	}
	if text, _ := readText(safePath("code")); !strings.Contains(text, "list=nada pin=1") {
		t.Error("code was rewritten")
	}
	if text, _ := readText(safePath("shop")); !strings.Contains(text, "[[tasks: list=Groceries keep=1 labels=Catalogue,,Shopping limit=8 tag=weekly]]") {
		t.Errorf("shop: %q", text)
	}
	wantViews := "# Views\n\n[[tasks: list=groceries]]\n\n[[tasks: list=groceries tag=weekly view=open]]\n\n" +
		"[[tasks: tag=-chores]] [[tasks: tag=chores|work]] [[tasks: tag=work]] `[[tasks: tag=chores]]`\n\n" +
		"[Chores](/tasks?list=chores&view=open), [both](/tasks?list=chores&tag=work), [work](/tasks?tag=work), [set](/tasks?list=plans&tag=chores)\n"
	if text, _ := readText(safePath("views")); text != wantViews {
		t.Errorf("views: %q", text)
	}
	// All tasks' settings moved into the registry.
	if p := allTasksParams(); p.Get("view") != "open" || p.Get("limit") != "5" {
		t.Errorf("All tasks: %v", p.m)
	}
	if _, err := os.Stat(oldAllTasksFile()); err == nil {
		t.Error("settings.json is still there")
	}
	// Run again (the next start): nothing changes.
	before := fileText(t, taskFiles.OpenPath("")) + fileText(t, taskFiles.ListsPath(""))
	putPage(t, "later", "# Later\n\n[[tasks: list=typed-later pin=1]]\n")
	migrateLists()
	if after := fileText(t, taskFiles.OpenPath("")) + fileText(t, taskFiles.ListsPath("")); after != before {
		t.Errorf("a second run changed the files:\n%s", after)
	}
	if _, ok := taskLists("")["typed-later"]; ok {
		t.Error("an embed typed after the migration made a list")
	}
}

// With users: a private page's list is its user's own, unless a shared page
// declares the name too and they have no private task with it; private
// tasks stay private, in a list of their user's (one without any goes to
// "private"); a shared task never gets a private list. A user's pins come
// before the wiki's.
func TestListsTakenOverPerUser(t *testing.T) {
	testWiki(t, map[string]string{
		"tasks/gifts":         "# Gifts\n\n[[tasks: list=gifts pin=1]]\n",
		"tasks/diary":         "# Diary\n\n[[tasks: list=diary]]\n",
		"private/alice/gifts": "# My gifts\n\n[[tasks: list=gifts]]\n",
		"private/alice/diary": "# Diary\n\n[[tasks: list=diary]]\n", // she has no private diary task: the shared list
		"private/alice/mine":  "# Mine\n\n[[tasks: list=secret pin=1]]\n",
		"private/bob/nothing": "# Nothing\n",
	})
	withUsers(t, "alice", "bob")
	writeTasks(t, "", oldTask("s1", "Scarf", "gifts"), oldTask("s2", "Hers, shared", "secret"))
	writeTasks(t, "alice", oldTask("a1", "Cake", "Gifts"), oldTask("a2", "Loose"), oldTask("a3", "Hidden", "secret"))
	writeTasks(t, "bob", oldTask("b1", "Bob's", "gifts"))
	migrateLists()
	if listsUnmigrated {
		t.Fatal("the migration failed")
	}
	alice, bob := taskLists("alice"), taskLists("bob")
	if got := sortedKeys(alice); !slices.Equal(got, []string{"diary", "gifts", "private", "secret", "shared/gifts"}) {
		t.Errorf("alice's lists %v", got)
	}
	if !alice["gifts"].Private() || alice["shared/gifts"].Private() || alice["diary"].Private() || !alice["secret"].Private() {
		t.Errorf("alice's lists %+v", alice)
	}
	// Bob declared nothing: his private task with the shared list's tag has
	// no list of his own to be in, so it goes to his "private".
	if got := sortedKeys(bob); !slices.Equal(got, []string{"diary", "gifts", "private"}) {
		t.Errorf("bob's lists %v", got)
	}
	a := openByID("alice")
	for id, want := range map[string][3]string{
		"s1": {"gifts", "", "shared/gifts"}, "s2": {"", "", ""}, // a shared task with her private list's tag: no list, the tag stays
		"a1": {"gifts", "alice", "gifts"}, "a2": {"private", "alice", "private"}, "a3": {"secret", "alice", "secret"},
	} {
		got := a[id]
		if got == nil || got.List != want[0] || got.Owner != want[1] || taskList(got, alice) != want[2] {
			t.Errorf("task %s: %+v, want list %q owner %q ref %q", id, got, want[0], want[1], want[2])
		}
	}
	if tags := a["s2"].Tags; !slices.Equal(tags, []string{"secret"}) {
		t.Errorf("s2's tags %v", tags)
	}
	b := openByID("bob")
	if b["b1"].List != "private" || b["b1"].Owner != "bob" || !slices.Equal(b["b1"].Tags, []string{"gifts"}) || b["a1"] != nil {
		t.Errorf("bob's task %+v", b["b1"])
	}
	// No task changed its file: nobody sees more or less than before.
	if strings.Contains(fileText(t, taskFiles.OpenPath("")), "Cake") || strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "Scarf") {
		t.Error("a task moved between files")
	}
	// Alice's own pins first, then the wiki's; her "private" too. Bob had
	// none: the wiki's, and his "private".
	if got := refsOf(pinnedLists("alice", alice)); !slices.Equal(got, []string{"secret", "shared/gifts", "private"}) {
		t.Errorf("alice's pins %v", got)
	}
	if got := refsOf(pinnedLists("bob", bob)); !slices.Equal(got, []string{"gifts", "private"}) {
		t.Errorf("bob's pins %v", got)
	}
	if got := refsOf(pinnedLists("", taskLists(""))); !slices.Equal(got, []string{"gifts"}) {
		t.Errorf("the wiki's pins %v", got)
	}
	if d := lookupDefaultList("alice"); d == nil || d.Ref != "secret" || !d.Private() {
		t.Errorf("alice's default list %+v", d)
	}
	// Scopes tell the two lists of one name apart.
	if !inScope(a["a1"], "gifts", alice) || inScope(a["s1"], "gifts", alice) || !inScope(a["s1"], "shared/gifts", alice) ||
		!inScope(a["s1"], "secret|shared/gifts", alice) || inScope(a["a2"], "gifts|secret", alice) || !inScope(a["a2"], "", alice) {
		t.Error("inScope")
	}
	// On a shared page a name is the shared list, on her own page hers.
	if l, ok := listOnPage("gifts", "tasks/gifts", "alice", alice); !ok || l.Ref != "shared/gifts" {
		t.Errorf("gifts on a shared page: %+v", l)
	}
	if l, ok := listOnPage("gifts", "private/alice/gifts", "alice", alice); !ok || l.Ref != "gifts" {
		t.Errorf("gifts on her page: %+v", l)
	}
	if _, ok := listOnPage("secret", "tasks/gifts", "alice", alice); ok {
		t.Error("her private list on a shared page")
	}
}

// A wiki that never had lists gets no registry at start; its first list
// makes it.
func TestNothingToTakeOver(t *testing.T) {
	testWiki(t, map[string]string{"index": "# Home\n\n[[tasks:]]\n"})
	writeTasks(t, "", oldTask("1", "Plain", "work"))
	before := fileText(t, taskFiles.OpenPath(""))
	migrateLists()
	if _, err := os.Stat(taskFiles.ListsPath("")); err == nil || fileText(t, taskFiles.OpenPath("")) != before || listsUnmigrated {
		t.Error("a wiki without lists was changed")
	}
	mustList(t, "", "shop", false)
	if !strings.Contains(fileText(t, taskFiles.ListsPath("")), `"version": 1`) {
		t.Errorf("the registry: %s", fileText(t, taskFiles.ListsPath("")))
	}
}

// A task file that can't be read stops the migration before anything is
// written, and no list is made or changed until a start succeeds.
func TestListsNotTakenOverFromABrokenWiki(t *testing.T) {
	testWiki(t, map[string]string{"shop": "# Shop\n\n[[tasks: list=shop pin=1]]\n"})
	t.Cleanup(func() { listsUnmigrated = false })
	if err := os.MkdirAll(taskFiles.Dir(""), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(taskFiles.OpenPath(""), []byte("[ half"), 0o666); err != nil {
		t.Fatal(err)
	}
	migrateLists()
	if !listsUnmigrated {
		t.Fatal("migrated over a file that can't be read")
	}
	if _, err := os.Stat(taskFiles.ListsPath("")); err == nil {
		t.Error("a registry was written")
	}
	if text, _ := readText(safePath("shop")); !strings.Contains(text, "pin=1") {
		t.Error("the page lost its pin")
	}
	if _, err := createListOp("", "x", false, ""); err == nil {
		t.Error("a list made in a wiki not taken over")
	}
	if err := pinListOp("", "shop", "1"); err == nil {
		t.Error("a pin set in a wiki not taken over")
	}
}

// A list's name: one word, as an embed can write it; one a user sees is taken.
func TestCreateList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob", "vera")
	settings().Users["vera"].Role = roleViewer
	if ref := mustList(t, "alice", " #Shop List ", false); ref != "shop-list" {
		t.Errorf("made %q", ref)
	}
	if ref := mustList(t, "alice", "gifts", true); ref != "gifts" || !taskLists("alice")["gifts"].Private() {
		t.Errorf("private list %q", ref)
	}
	if _, ok := taskLists("bob")["gifts"]; ok {
		t.Error("bob sees alice's list")
	}
	for _, bad := range []string{"", "  ", "a,b", "a|b", "a=b", "a[b", `a"b`, "a/b"} {
		if _, err := createListOp("alice", bad, false, ""); !refusedWith(err, refusedInvalid) {
			t.Errorf("%q: %v", bad, err)
		}
	}
	// A name of a list she sees, shared or her own, either way.
	for _, c := range []struct {
		name    string
		private bool
	}{{"Shop-List", false}, {"shop-list", true}, {"gifts", false}, {"gifts", true}} {
		if _, err := createListOp("alice", c.name, c.private, ""); !refusedWith(err, refusedExists) {
			t.Errorf("%+v: %v", c, err)
		}
	}
	// Bob doesn't see her gifts: his shared one is made, and she has two.
	mustList(t, "bob", "gifts", false)
	if got := sortedKeys(taskLists("alice")); !slices.Equal(got, []string{"gifts", "shared/gifts", "shop-list"}) {
		t.Errorf("alice's lists %v", got)
	}
	if _, err := createListOp("vera", "x", false, ""); !refusedWith(err, refusedReadOnly) {
		t.Errorf("a viewer made a list: %v", err)
	}
}

// A task goes into a list by naming it; a new one may start private, and a
// private list's task is in its user's files.
func TestTaskInAList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "shop", false)
	mustList(t, "alice", "gifts", true)
	milk := newTestTask(t, "alice", "Milk", false, "list", "Shop")
	cake := newTestTask(t, "alice", "Cake", false, "list", "gifts")
	loose := newTestTask(t, "alice", "Loose", false, "tags", "shop") // a tag is a tag
	if milk.List != "shop" || milk.Owner != "" || cake.List != "gifts" || cake.Owner != "alice" || loose.List != "" {
		t.Fatalf("milk %+v, cake %+v, loose %+v", milk, cake, loose)
	}
	if !strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "Cake") || strings.Contains(fileText(t, taskFiles.OpenPath("")), "Cake") {
		t.Error("the private list's task isn't in her files")
	}
	set := func(id string, kv ...any) error {
		data := jsonObj{}
		for i := 0; i+1 < len(kv); i += 2 {
			data.Set(kv[i].(string), kv[i+1])
		}
		_, err := updateTask("alice", id, data, "")
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
	if err := set(milk.ID, "list", ""); err != nil || openByID("alice")[milk.ID].List != "" {
		t.Errorf("out of its list: %v", err)
	}
	// Bob has no list "gifts": hers is not his to name.
	data := jsonObj{}
	data.Set("title", "Sneaky")
	data.Set("list", "gifts")
	if _, err := createTask("bob", data, "c"); err == nil {
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
		if err := pinListOp(u, "shop", "default"); err != nil {
			t.Fatal(err)
		}
	}
	if err := pinListOp("alice", "gifts", "1"); err != nil {
		t.Fatal(err)
	}
	pagesMu.Lock()
	defer pagesMu.Unlock()
	if _, err := renameListOp("alice", "shop", "gifts", ""); !refusedWith(err, refusedExists) {
		t.Errorf("renamed onto a list she sees: %v", err)
	}
	if _, err := renameListOp("bob", "nope", "x", ""); !refusedWith(err, refusedNotFound) {
		t.Errorf("a list that isn't there: %v", err)
	}
	done, err := renameListOp("alice", "shop", "Market", "")
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
		if d := lookupDefaultList(u); d == nil || d.Ref != "market" {
			t.Errorf("%s's default list %+v", u, d)
		}
	}
	// Her private list: only hers, her pages, her pin.
	if _, err := renameListOp("alice", "gifts", "presents", ""); err != nil {
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
	if got := refsOf(pinnedLists("alice", taskLists("alice"))); !slices.Equal(got, []string{"market", "presents"}) {
		t.Errorf("her pins %v", got)
	}
}

// Deleting a list moves its tasks, never drops them; a private list's go
// to a shared place only when the delete says share.
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
	if err := pinListOp("bob", "shop", "1"); err != nil {
		t.Fatal(err)
	}
	if _, err := deleteListOp("alice", "shop", "gifts", false, ""); err == nil {
		t.Error("shared tasks moved into a private list")
	}
	if _, err := deleteListOp("alice", "shop", "shop", false, ""); err == nil {
		t.Error("moved into itself")
	}
	if _, err := deleteListOp("bob", "gifts", "", true, ""); !refusedWith(err, refusedNotFound) {
		t.Errorf("bob deleted alice's list: %v", err)
	}
	if n, err := deleteListOp("alice", "shop", "market", false, ""); err != nil || n != 1 {
		t.Fatalf("delete: %d, %v", n, err)
	}
	if _, ok := taskLists("alice")["shop"]; ok || openByID("bob")[milk.ID].List != "market" {
		t.Error("shop is still there, or its task didn't move")
	}
	if got := pinnedLists("bob", taskLists("bob")); len(got) != 0 {
		t.Errorf("bob still pins it: %v", refsOf(got))
	}
	// Her private list: to another of hers, no question.
	if _, err := deleteListOp("alice", "gifts", "wishes", false, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("alice")[cake.ID]; got.List != "wishes" || got.Owner != "alice" {
		t.Errorf("cake %+v", got)
	}
	// To no list: its tasks would be shared.
	if _, err := deleteListOp("alice", "wishes", "", false, ""); err == nil {
		t.Fatal("private tasks shared without saying so")
	}
	if _, err := deleteListOp("alice", "wishes", "", true, ""); err != nil {
		t.Fatal(err)
	}
	if got := openByID("bob")[cake.ID]; got == nil || got.List != "" || got.Owner != "" || got.History[len(got.History)-1].Event != "shared" {
		t.Errorf("cake after the delete: %+v", got)
	}
	if strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "Cake") {
		t.Error("cake is still in her file")
	}
}

// A private list made shared takes its tasks with it; never the other way.
func TestShareList(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "gifts", true)
	mustList(t, "alice", "shop", false)
	cake := newTestTask(t, "alice", "Cake", false, "list", "gifts")
	if _, err := shareListOp("alice", "shop", ""); !refusedWith(err, refusedUnchanged) {
		t.Errorf("a shared list shared: %v", err)
	}
	if _, err := shareListOp("bob", "gifts", ""); !refusedWith(err, refusedNotFound) {
		t.Errorf("bob shared alice's list: %v", err)
	}
	if _, err := shareListOp("alice", "gifts", ""); err != nil {
		t.Fatal(err)
	}
	if l, ok := taskLists("bob")["gifts"]; !ok || l.Private() {
		t.Errorf("bob's view of it: %+v", l)
	}
	if got := openByID("bob")[cake.ID]; got == nil || got.List != "gifts" || got.Owner != "" {
		t.Errorf("its task: %+v", got)
	}
	// A name a shared list has already: hers stays hers.
	mustList(t, "alice", "wishes", true)
	mustList(t, "bob", "wishes", false)
	if _, err := shareListOp("alice", "wishes", ""); !refusedWith(err, refusedExists) {
		t.Errorf("shared onto a shared list's name: %v", err)
	}
}

// Pins are the user's own; one who set none has the wiki's, and starts
// from them. The default list comes first.
func TestPins(t *testing.T) {
	testWiki(t, nil)
	mustList(t, "", "shop", false)
	mustList(t, "", "work", false)
	if err := pinListOp("", "shop", "1"); err != nil {
		t.Fatal(err)
	}
	if err := pinListOp("", "work", "1"); err != nil {
		t.Fatal(err)
	}
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "gifts", true)
	pins := func(u string) []string { return refsOf(pinnedLists(u, taskLists(u))) }
	if got := pins("bob"); !slices.Equal(got, []string{"shop", "work"}) {
		t.Errorf("bob has the wiki's pins: %v", got)
	}
	if err := pinListOp("alice", "gifts", "default"); err != nil {
		t.Fatal(err)
	}
	if got := pins("alice"); !slices.Equal(got, []string{"gifts", "shop", "work"}) {
		t.Errorf("alice's pins %v", got)
	}
	if err := pinListOp("alice", "shop", "0"); err != nil {
		t.Fatal(err)
	}
	if got := pins("alice"); !slices.Equal(got, []string{"gifts", "work"}) || !slices.Equal(pins("bob"), []string{"shop", "work"}) {
		t.Errorf("after her unpin: alice %v, bob %v", got, pins("bob"))
	}
	for _, r := range []string{"gifts", "work"} {
		if err := pinListOp("alice", r, "0"); err != nil {
			t.Fatal(err)
		}
	}
	if got := pins("alice"); len(got) != 0 {
		t.Errorf("she unpinned everything, and has %v", got)
	}
	if err := pinListOp("bob", "gifts", "1"); !refusedWith(err, refusedNotFound) {
		t.Errorf("bob pinned alice's list: %v", err)
	}
	if err := pinListOp("alice", "work", "maybe"); !refusedWith(err, refusedInvalid) {
		t.Errorf("pin=maybe: %v", err)
	}
	// The pinned lists as one view: the default list's settings, all their tasks.
	if err := setListSettingsOp("bob", "shop", map[string]string{"view": "focus", "new": "weekly"}); err != nil {
		t.Fatal(err)
	}
	v := (&Req{user: "bob"}).pinnedView()
	if v.Get("lists") != "shop|work" || v.Get("list") != "" || v.Get("view") != "focus" || !slices.Equal(newTaskTags(v), []string{"weekly"}) {
		t.Errorf("the pinned view: %v", v.m)
	}
}

// A list's settings are its registry's: what List settings… may set, each
// checked as an embed's would be.
func TestListSettings(t *testing.T) {
	testWiki(t, nil)
	mustList(t, "", "shop", false)
	if err := setListSettingsOp("", "shop", map[string]string{"limit": "8", "keep": "yes", "labels": "Catalogue,Shopping", "noshare": "1"}); err != nil {
		t.Fatal(err)
	}
	l := taskLists("")["shop"]
	if l.Params.Get("limit") != "8" || l.Params.Get("keep") != "1" || l.Params.Get("labels") != "Catalogue,Shopping" || !l.NoShare {
		t.Errorf("settings %v noshare %v", l.Params.m, l.NoShare)
	}
	if err := setListSettingsOp("", "shop", map[string]string{"limit": "", "noshare": ""}); err != nil {
		t.Fatal(err)
	}
	if l := taskLists("")["shop"]; l.Params.Has("limit") || l.NoShare || l.Params.Get("keep") != "1" {
		t.Errorf("after taking two out: %v", l.Params.m)
	}
	for _, bad := range []map[string]string{{"limit": "0"}, {"view": "week"}, {"tag": "x"}, {"list": "other"}, {"pin": "1"}} {
		if err := setListSettingsOp("", "shop", bad); !refusedWith(err, refusedInvalid) {
			t.Errorf("%v: %v", bad, err)
		}
	}
	if err := setListSettingsOp("", "nope", map[string]string{"limit": "3"}); !refusedWith(err, refusedNotFound) {
		t.Errorf("a list that isn't there: %v", err)
	}
	if !strings.Contains(fileText(t, filepath.Join(cfg.Data, "_tasks", "lists.json")), `{"name": "shop", "keep": "1", "labels": "Catalogue,Shopping"}`) {
		t.Errorf("the registry:\n%s", fileText(t, taskFiles.ListsPath("")))
	}
}

// A snapshot (an undo's, an import's) puts a task back where it was, and
// never makes a shared task private nor a private one shared.
func TestPlaceSnapshot(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	mustList(t, "alice", "shop", false)
	mustList(t, "alice", "gifts", true)
	lists := taskLists("alice")
	snap := func(list string, private bool) jsonObj {
		o := jsonObj{}
		o.Set("list", list)
		o.Set("private", private)
		return o
	}
	for _, c := range []struct {
		list             string
		private          bool
		wantList, wantBy string
		inbox            bool
	}{
		{"shop", false, "shop", "", false},
		{"gifts", true, "gifts", "alice", false},
		{"gifts", false, "", "", false},               // a shared task with her private list's name: no list
		{"shop", true, "private", "alice", true},      // a private one whose list is shared: her "private"
		{"long-gone", true, "private", "alice", true}, // its list is gone
		{"long-gone", false, "", "", false},           // a shared one's too
		{"", false, "", "", false},
	} {
		task := &Task{Assignee: "bob"}
		inbox := placeSnapshot("alice", task, snap(c.list, c.private), lists)
		if task.List != c.wantList || task.Owner != c.wantBy || inbox != c.inbox || (task.Owner != "") != (task.Assignee == "") {
			t.Errorf("%+v: list %q owner %q inbox %v assignee %q", c, task.List, task.Owner, inbox, task.Assignee)
		}
	}
	if got := reflect.TypeOf(lists).Kind(); got != reflect.Map {
		t.Fatal(got)
	}
}
