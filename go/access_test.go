package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
)

func withUsers(t *testing.T, names ...string) {
	t.Helper()
	settings().Users = map[string]*User{}
	for _, n := range names {
		settings().Users[n] = &User{Name: n}
	}
}

func TestAccessWithoutUsers(t *testing.T) {
	testWiki(t, nil)
	for _, slug := range []string{"index", "private/alice/diary", "private/x"} {
		if p := placeOf(slug); p.user() != "" || !p.shared() || !canSee(asker{}, slug) || !canWrite(asker{}, slug) {
			t.Errorf("%s: private without users", slug)
		}
	}
	if !canSeePath(asker{}, "private/alice/_tasks/tasks.json") || !canSeePath(asker{}, "private/alice/_assets/a.png") ||
		canSeePath(asker{}, ".leaf/config") || canSeePath(asker{}, ".zone/config") || canSeePath(asker{}, ".zone/secret_key") {
		t.Error("paths")
	}
}

func TestAccessWithUsers(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "mary-ann")
	for _, c := range []struct {
		slug, owner string
		private     bool
		alice, bob  bool
	}{
		{"index", "", false, true, true},
		{"private", "", false, true, true}, // a page named private, not in the folder
		{"privately/x", "", false, true, true},
		{"notes/private/alice/x", "", false, true, true},
		{"private/alice/diary", "alice", true, true, false},
		{"Private/Alice/Diary", "alice", true, true, false},
		{"private/alice", "", true, false, false}, // a page beside the folders: no one's
		{"private/carol/x", "", true, false, false},
		{"private/mary-ann/x", "mary-ann", true, false, false},
		// Judged as safePath opens them, cleaned.
		{"private/bob/../alice/diary", "alice", true, true, false},
		{"private/alice/../bob/x", "", true, false, false},
		{"private/alice/./diary", "alice", true, true, false},
		{"notes/../private/alice/x", "alice", true, true, false},
		{"private/alice/../../index", "", false, true, true},
	} {
		if p := placeOf(c.slug); p.user() != c.owner || p.shared() == c.private {
			t.Errorf("placeOf(%s) = %+v", c.slug, p)
		}
		if canSee(asUser("alice"), c.slug) != c.alice || canSee(asUser("bob"), c.slug) != c.bob || canWrite(asUser("alice"), c.slug) != c.alice {
			t.Errorf("%s: alice %v bob %v", c.slug, canSee(asUser("alice"), c.slug), canSee(asUser("bob"), c.slug))
		}
		if canSee(asker{}, c.slug) != !c.private {
			t.Errorf("%s: no user", c.slug)
		}
	}
	for path, want := range map[string]bool{
		"index.md": true, "private/alice/diary.md": true, "private/bob/x.md": false, "private/alice/img.png": true,
		"_assets/a.png": true, "private/alice/_assets/a.png": true, "private/mary-ann/_assets/a.png": false,
		"private/_assets/a.png": false, "_tasks/tasks.json": true, "private/alice/_tasks/tasks.json": true,
		"private/mary-ann/_tasks/tasks.json": false, "_tasks/users.json": true,
	} {
		if canSeePath(asUser("alice"), path) != want {
			t.Errorf("canSeePath(alice, %s) != %v", path, want)
		}
	}
}

func TestHiddenPageAndSharing(t *testing.T) {
	testWiki(t, map[string]string{"secret/x": "# X\n"}) // noshare secret/ (testWiki)
	withUsers(t, "alice")
	alice, aliceSharing := &Req{user: asUser("alice")}, &Req{user: asUser("alice"), sharing: true}
	if hiddenPage(alice, "private/alice/x") || !hiddenPage(&Req{user: asUser("bob")}, "private/alice/x") {
		t.Error("private")
	}
	if hiddenPage(alice, "secret/x") || !hiddenPage(aliceSharing, "secret/x") || !hiddenPage(aliceSharing, "private/bob/x") {
		t.Error("sharing")
	}
	if hiddenPage(nil, "private/bob/x") {
		t.Error("no request (Zone itself) sees everything")
	}
}

func TestStrayPrivate(t *testing.T) {
	testWiki(t, map[string]string{"private/alice/a": "# A\n", "private/carol/c": "# C\n", "private/loose": "# L\n"})
	if strayPrivate() != nil {
		t.Error("without users")
	}
	withUsers(t, "alice")
	_ = os.WriteFile(filepath.Join(cfg.Data, privateDir, ".DS_Store"), nil, 0o666)
	if got := strayPrivate(); !slices.Equal(got, []string{"private/carol", "private/loose.md"}) {
		t.Errorf("%v", got)
	}
}

func TestQuicknoteNotesInPrivateFolders(t *testing.T) {
	testWiki(t, nil)
	note := "private/alice/2026-10-27_12-00-00_idea"
	if !isQuicknote("2026-10-27_12-00-00_idea") || isQuicknote(note) {
		t.Error("without users only top-level notes are notes")
	}
	withUsers(t, "alice")
	if !isQuicknote(note) || isQuicknote("private/carol/2026-10-27_12-00-00_idea") ||
		isQuicknote("private/alice/x/2026-10-27_12-00-00_idea") || isQuicknote("notes/2026-10-27_12-00-00_idea") {
		t.Error("with users: at the top of a user's folder")
	}
	if noteDate(note) != "2026-10-27" || privateFolder(asUser("alice")) != "private/alice" || privateFolder(asker{}) != "" {
		t.Error("noteDate, privateFolder")
	}
}

func TestInQuicknote(t *testing.T) {
	testWiki(t, nil)
	old, mine, theirs := "2026-10-27_12-00-00_idea", "private/alice/2026-10-27_12-00-00_idea", "private/carol/2026-10-27_12-00-00_idea"
	if !inQuicknote(asker{}, old) || inQuicknote(asker{}, "notes") {
		t.Error("without users: the top-level notes")
	}
	withUsers(t, "alice")
	if !inQuicknote(asUser("alice"), mine) || inQuicknote(asUser("alice"), old) || inQuicknote(asUser("alice"), theirs) || !inQuicknote(asker{}, old) {
		t.Error("with users: only the user's own; Zone itself sees all")
	}
}

func TestHideNames(t *testing.T) {
	testWiki(t, nil)
	msg := "Promote private/alice/2026-10-27_12-00-00_idea to idea (MCP)"
	if hideNames(asUser("bob"), msg, nil) != msg {
		t.Error("without users nothing is private")
	}
	withUsers(t, "alice", "bob")
	for user, want := range map[string]string{
		"alice": msg,
		"bob":   "Promote a private page to idea (MCP)",
	} {
		if got := hideNames(asUser(user), msg, nil); got != want {
			t.Errorf("%s: %q", user, got)
		}
	}
	for msg, want := range map[string]string{
		"Rename private/bob/a to private/alice/b.": "Rename private/bob/a to a private page.",
		// Only a path that starts there.
		"Rename notprivate/x to archive/x":       "Rename notprivate/x to archive/x",
		"Edit notes/private/alice/x":             "Edit notes/private/alice/x",
		"Upload private/alice/_assets/photo.png": "Upload a private page",
		"Move (private/alice/a) to x":            "Move (a private page) to x",
		"private/alice/a, private/bob/b":         "a private page, private/bob/b",
		// A folder made by hand in another case is the same user's (placeOf).
		"Rename Private/alice/a to x": "Rename a private page to x",
		// A name in any script, with spaces: hidden whole, up to what goes on
		// with the message.
		"Rename private/alice/gehälter-übersicht to notes":      "Rename a private page to notes",
		"Upload private/alice/_assets/Arztbrief März.pdf":       "Upload a private page",
		"Upload private/alice/_assets/Arztbrief März.pdf (MCP)": "Upload a private page (MCP)",
		"Move private/alice/a b/ to private/alice/c d/":         "Move a private page to a private page",
		"Rename private/alice/a to notes, then private/bob/b":   "Rename a private page to notes, then private/bob/b",
		// A task's file, of a task no one has (made yet, or any more): its
		// id and its name are no one's to read.
		"Upload _tasks/files/0a1b2c3d-0000-4000-8000-00000000abcd/_assets/scan.png": "Upload a task's file",
		"Upload x_tasks/files/0a1b/_assets/scan.png":                                "Upload x_tasks/files/0a1b/_assets/scan.png",
	} {
		if got := hideNames(asUser("bob"), msg, nil); got != want {
			t.Errorf("%q: %q", msg, got)
		}
	}
	// The commit's own files that bob may not see are taken out whole,
	// whatever their names hold; a longer name isn't cut by a shorter one.
	for _, c := range []struct {
		msg    string
		hidden []string
		want   string
	}{
		{"Upload private/alice/_assets/Brief to Mom (Kopie).pdf", []string{"private/alice/_assets/Brief to Mom (Kopie).pdf"},
			"Upload a private page"},
		{"Rename private/alice/Brief, 1 to brief", []string{"private/alice/Brief, 1.md"}, "Rename a private page to brief"},
		{"Rename private/alice/ab to private/alice/a", []string{"private/alice/a.md"}, "Rename a private page to a private page"},
		{"Share groups/dev/plan (alt) to plan", []string{"groups/dev/plan (alt).md"}, "Share a group's page to plan"},
	} {
		if got := hideNames(asUser("bob"), c.msg, c.hidden); got != c.want {
			t.Errorf("%q, %q: %q", c.msg, c.hidden, got)
		}
	}
	// alice sees her own names.
	if msg := "Upload private/alice/_assets/Arztbrief März.pdf"; hideNames(asUser("alice"), msg, nil) != msg {
		t.Error("alice's own upload")
	}
}

// What isn't a clean path, as git names a file with -z, is no one's to
// see: git's quoting of a name it printed without -z (which named no
// private folder), a backslash, a control character, a "." or ".." part.
func TestCanSeePathOnlyOfCleanPaths(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	for path, want := range map[string]bool{
		"private/alice/_assets/Arztbrief März.pdf": true, "private/alice/notes/": true, "notes/a b.md": true,
		`_assets/Offer "final".pdf`: true, `"notes".md`: false,
		`"private/alice/_assets/Arztbrief M\303\244rz.pdf"`: false, `"notes/K\303\274che.md"`: false,
		`notes/a\b.md`: false, "notes/a\nb.md": false, "notes/a\x1eb.md": false, "notes/../private/alice/x.md": false,
		"notes/./x.md": false, "/notes/x.md": false, "notes//x.md": false, "": false,
	} {
		if got := canSeePath(asUser("alice"), path); got != want {
			t.Errorf("canSeePath(alice, %q) = %v", path, got)
		}
		if strings.HasPrefix(path, "private/alice") && canSeePath(asUser("bob"), path) {
			t.Errorf("canSeePath(bob, %q)", path)
		}
	}
}

// Only a shared page taken into a private folder goes to fewer people.
func TestNarrowedBy(t *testing.T) {
	testWiki(t, map[string]string{})
	withUsers(t, "alice", "bob")
	for _, c := range []struct {
		from, to string
		want     bool
	}{
		{"ideas", "private/alice/ideas", true},
		{"ideas", "notes/ideas", false},
		{"private/alice/x", "private/alice/y", false},
		{"private/alice/x", "shared/x", false},
		{"ideas", "private/carol/ideas", true}, // no one's: hidden from everyone
	} {
		if got := len(narrowedBy(map[string]string{c.from: c.to})) > 0; got != c.want {
			t.Errorf("%s → %s: %v", c.from, c.to, got)
		}
	}
	settings().Users = nil
	if len(narrowedBy(map[string]string{"ideas": "private/alice/ideas"})) > 0 {
		t.Error("without users private/ is an ordinary folder")
	}
}

// A visit to Private whose home is there doesn't wait for a page write.
func TestPrivateHomeDoesntWaitForWrites(t *testing.T) {
	testWiki(t, map[string]string{"private/alice/index": "# Alice\n"})
	s := *settings()
	s.Users = map[string]*User{"alice": {Name: "alice"}}
	current.Store(&s)
	pagesMu.Lock()
	defer pagesMu.Unlock()
	done := make(chan int)
	go func() {
		mux := http.NewServeMux()
		pageRoutes(mux) // as the route is registered
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, withUser(httptest.NewRequest("GET", "/private", nil), "alice"))
		done <- w.Code
	}()
	select {
	case code := <-done:
		if code != http.StatusFound {
			t.Errorf("%d", code)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("waited for pagesMu")
	}
}

// A place and the places within it: who sees one sees the other.
func TestPlaceWithin(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	shared, alice, bob, none := placeOf("notes/x"), placeOf("private/alice/x"), placeOf("private/bob/x"), placeOf("private/carol/x")
	if !shared.shared() || alice.user() != "alice" || !alice.own(asUser("alice")) || alice.own(asUser("bob")) || none.user() != "" || none.shared() ||
		none.sees(asUser("alice")) || none.sees(asker{}) || shared.own(asUser("alice")) {
		t.Errorf("places %+v %+v %+v", shared, alice, none)
	}
	for _, c := range []struct {
		name string
		p, q place
		want bool
	}{
		{"shared in shared", shared, shared, true}, {"alice in shared", alice, shared, true}, {"no one's in shared", none, shared, true},
		{"alice in alice", alice, placeOf("private/alice/deep/y"), true},
		{"shared in alice", shared, alice, false}, {"alice in bob", alice, bob, false}, {"alice in no one's", alice, none, false},
		{"shared in no one's", shared, none, false},
	} {
		if got := c.p.within(c.q); got != c.want {
			t.Errorf("%s: %v", c.name, got)
		}
	}
}

// withGroups: users as "name" or "name:group,group", and the groups made of
// groups (group → what it includes), as a settings file would give them.
func withGroups(t *testing.T, includes map[string][]string, users ...string) {
	t.Helper()
	s := settings()
	s.Users, s.Includes = map[string]*User{}, includes
	for _, u := range users {
		name, groups, _ := strings.Cut(u, ":")
		s.Users[name] = &User{Name: name}
		if groups != "" {
			s.Users[name].Groups = strings.Split(groups, ",")
		}
	}
	s.joinGroups()
}

// A group's folder (docs/spec/users.md, Group pages): groups/<group>/ is
// its members', whoever they are now; anything else in groups/ no one's.
func TestGroupFolders(t *testing.T) {
	testWiki(t, nil)
	withGroups(t, map[string][]string{"product": {"dev", "design"}}, "alice:dev", "bob:design", "carol", "dana:ops", "eve:dev")
	settings().Users["dana"].Role = roleAdmin
	settings().Users["eve"].Disabled = true
	for _, c := range []struct {
		slug               string
		kind               placeKind
		group              string
		alice, bob, others bool
	}{
		{"groups/dev/roadmap", ofGroup, "dev", true, false, false},
		{"Groups/Dev/Roadmap", ofGroup, "dev", true, false, false},
		{"groups/dev/deep/down/x", ofGroup, "dev", true, false, false},
		{"groups/design/x", ofGroup, "design", false, true, false},
		{"groups/product/x", ofGroup, "product", true, true, false}, // by way of dev, of design
		{"groups/nobody/x", ofGroup, "nobody", false, false, false}, // a group nobody is in: its folder all the same
		{"groups/dev", ofNoOne, "", false, false, false},            // a page beside the folders
		{"groups/Not_A_Group/x", ofNoOne, "", false, false, false},
		{"groups/ops/../dev/x", ofGroup, "dev", true, false, false}, // judged as it is opened, cleaned
		{"groups/dev/../../index", ofEveryone, "", true, true, true},
		{"groups", ofEveryone, "", true, true, true}, // a page named groups
		{"notes/groups/dev/x", ofEveryone, "", true, true, true},
		{"private/groups/dev/x", ofNoOne, "", false, false, false},
	} {
		p := placeOf(c.slug)
		if p.kind != c.kind || p.group() != c.group || p.user() != "" {
			t.Errorf("placeOf(%s) = %+v", c.slug, p)
		}
		if canSee(asUser("alice"), c.slug) != c.alice || canSee(asUser("bob"), c.slug) != c.bob {
			t.Errorf("%s: alice %v bob %v", c.slug, canSee(asUser("alice"), c.slug), canSee(asUser("bob"), c.slug))
		}
		// carol is in no group, dana an admin in another, eve disabled, a visitor and Zone's own "" in none.
		for _, who := range []string{"carol", "dana", "eve", visitor, "", "nobody"} {
			if canSee(asUser(who), c.slug) != c.others || canWrite(asUser(who), c.slug) && !c.others {
				t.Errorf("%s: %q sees %v", c.slug, who, canSee(asUser(who), c.slug))
			}
		}
	}
	for path, want := range map[string]bool{
		"groups/dev/roadmap.md": true, "groups/dev/_assets/a.png": true, "groups/design/_assets/a.png": false,
		"groups/_assets/a.png": false, "groups/product/x.md": true, "groups/nobody/x.md": false, "groups/loose.md": false,
	} {
		if canSeePath(asUser("alice"), path) != want {
			t.Errorf("canSeePath(alice, %s) != %v", path, want)
		}
		if canSeePath(asUser("carol"), path) || canSeePath(asUser(visitor), path) {
			t.Errorf("%s: seen by someone in no group", path)
		}
	}
	// Whoever sees one place sees the other: to more people, never to fewer.
	shared, hers, dev, design, product, ops := placeOf("x"), placeOf("private/alice/x"), placeOf("groups/dev/x"), placeOf("groups/design/x"),
		placeOf("groups/product/x"), placeOf("groups/ops/x")
	for _, c := range []struct {
		name string
		p, q place
		want bool
	}{
		{"hers in her group's", hers, dev, true}, {"hers in a group that includes hers", hers, product, true},
		{"hers in a group she isn't in", hers, design, false}, {"hers in shared", hers, shared, true},
		{"dev in shared", dev, shared, true}, {"dev in dev", dev, placeOf("groups/dev/sub/y"), true},
		{"dev in product", dev, product, true}, {"design in product", design, product, true},
		{"product in dev", product, dev, false}, {"dev in design", dev, design, false}, {"dev in ops", dev, ops, false},
		{"dev in hers", dev, hers, false}, {"shared in dev", shared, dev, false}, {"shared in product", shared, product, false},
		{"no one's in dev", placeOf("groups/loose"), dev, false},
	} {
		if got := c.p.within(c.q); got != c.want {
			t.Errorf("%s: %v", c.name, got)
		}
	}
	// A commit message names no page of a group to who isn't in it.
	msg := "Rename groups/dev/roadmap to roadmap, and notgroups/dev/x"
	if got := hideNames(asUser("carol"), msg, nil); got != "Rename a group's page to roadmap, and notgroups/dev/x" {
		t.Errorf("hideNames for carol: %q", got)
	}
	if got := hideNames(asUser("alice"), msg, nil); got != msg {
		t.Errorf("hideNames for alice: %q", got)
	}
	// A group's guide is an admin's who is in the group; the others in it read it.
	withGroups(t, nil, "alice:dev", "dana:dev", "frank")
	settings().Users["dana"].Role, settings().Users["frank"].Role = roleAdmin, roleAdmin
	for who, want := range map[string]bool{"alice": false, "dana": true, "frank": false} {
		if canWrite(asUser(who), "groups/dev/AGENTS") != want || canWrite(asUser(who), "groups/dev/sub/claude") != want {
			t.Errorf("%s writes the group's guide: %v", who, !want)
		}
	}
	if !canSee(asUser("alice"), "groups/dev/AGENTS") || !readOnly(asUser("alice"), "groups/dev/AGENTS") || readOnly(asUser("frank"), "groups/dev/AGENTS") ||
		!canWrite(asUser("alice"), "groups/dev/roadmap") || readOnlyWhy(asUser("alice"), "groups/dev/AGENTS") != groupGuideMsg {
		t.Error("the group's guide for an editor in it")
	}
	// Without users groups/ is an ordinary folder.
	settings().Users = nil
	if p := placeOf("groups/dev/x"); !p.shared() || !canSee(asker{}, "groups/dev/x") {
		t.Error("groups/ without users")
	}
}

// An asker that only reads (an assistant's read-only connection) sees what
// its user sees and changes nothing: the permission model says so itself,
// whichever door asks.
func TestAReadOnlyAskerChangesNothing(t *testing.T) {
	testWiki(t, map[string]string{"plans": "# Plans\n", "private/alice/diary": "# Diary\n", "private/bob/own": "# Own\n"})
	withUsers(t, "alice", "bob")
	alice, reading := asUser("alice"), asker{Name: "alice", ReadOnly: true}
	for _, slug := range []string{"plans", "private/alice/diary"} {
		if !canSee(reading, slug) || !canWrite(alice, slug) {
			t.Errorf("%s: alice writes it, and reading she sees it", slug)
		}
		if canWrite(reading, slug) || !readOnly(reading, slug) {
			t.Errorf("%s: written by an asker that only reads", slug)
		}
		if why := readOnlyWhy(reading, slug); why != readOnlyConnectionMsg {
			t.Errorf("%s: told %q", slug, why)
		}
	}
	if canSee(reading, "private/bob/own") {
		t.Error("reading, alice sees bob's page")
	}
	if mayWriteTasks(alice) != nil {
		t.Error("alice may not write tasks")
	}
	if rf, ok := refusalOf(mayWriteTasks(reading)); !ok || rf.kind != refusedReadOnly || rf.msg != readOnlyConnectionMsg {
		t.Errorf("tasks for an asker that only reads: %v", mayWriteTasks(reading))
	}
	if !changesNothing(reading) || changesNothing(alice) {
		t.Error("changesNothing")
	}
	// The operations refuse, not only the doors.
	if err := createPageOp(reading, "new", "# New\n", ""); err == nil {
		t.Error("a page made by an asker that only reads")
	}
	if _, _, _, err := changePageOp(reading, "plans", "Update", "", func(string) (string, error) { return "# Changed\n", nil }); err == nil {
		t.Error("a page changed by an asker that only reads")
	}
	if _, err := createTask(reading, jsonObj{"title": json.RawMessage(`"x"`)}, "Create task"); err == nil {
		t.Error("a task made by an asker that only reads")
	}
	if _, err := reorderTasks(reading, nil, orderPos{At: "top"}, "Reorder tasks"); !refusedWith(err, refusedReadOnly) {
		t.Errorf("tasks moved in the manual order by an asker that only reads: %v", err)
	}
	for _, tool := range toolList(reading) {
		if name := tool["name"].(string); writeTool(name) {
			t.Errorf("an asker that only reads is given %s", name)
		}
	}
}

// An asker that was given less than its user sees: not their private place,
// or only some of their groups. Each only ever takes away.
func TestANarrowedAskerSeesLess(t *testing.T) {
	testWiki(t, map[string]string{"plans": "# Plans\n", "private/alice/diary": "# Diary\n",
		"groups/dev/roadmap": "# Roadmap\n", "groups/ops/runbook": "# Runbook\n", "groups/hr/files": "# Files\n"})
	withUsers(t, "alice", "bob")
	settings().Users["alice"].In = []string{"dev", "ops"}
	alice := asUser("alice")
	sees := func(user asker) string {
		var out []string
		for _, slug := range []string{"plans", "private/alice/diary", "groups/dev/roadmap", "groups/ops/runbook", "groups/hr/files"} {
			if canSee(user, slug) != canWrite(user, slug) {
				t.Errorf("%s: seen and written differ for %+v", slug, user)
			}
			if canSee(user, slug) != canSeePath(user, slug+".md") {
				t.Errorf("%s: canSee and canSeePath differ for %+v", slug, user)
			}
			if canSee(user, slug) {
				out = append(out, slug)
			}
		}
		return strings.Join(out, " ")
	}
	for _, c := range []struct {
		user asker
		want string
	}{
		{alice, "plans private/alice/diary groups/dev/roadmap groups/ops/runbook"},
		{asker{Name: "alice", NoPrivate: true}, "plans groups/dev/roadmap groups/ops/runbook"},
		{alice.WithGroups([]string{"dev"}), "plans private/alice/diary groups/dev/roadmap"},
		{alice.WithGroups(nil), "plans private/alice/diary"},
		{alice.WithGroups([]string{"hr", "dev"}), "plans private/alice/diary groups/dev/roadmap"}, // given a group she isn't in: nothing
		{asker{Name: "alice", NoPrivate: true}.WithGroups(nil), "plans"},
	} {
		if got := sees(c.user); got != c.want {
			t.Errorf("%+v sees %q, want %q", c.user, got, c.want)
		}
	}
	narrowed := asker{Name: "alice", NoPrivate: true}.WithGroups([]string{"dev"})
	if got := groupsFor(narrowed); !reflect.DeepEqual(got, []string{"dev"}) {
		t.Errorf("groupsFor: %v", got)
	}
	if !lacksPrivate(narrowed) || lacksPrivate(alice) || placeOf("private/alice/diary").own(narrowed) {
		t.Error("the private place of an asker that wasn't given it")
	}
	// Lists and tasks follow the places: a private one with the private
	// place, a group's with the group, a shared one always.
	private, ofDev, ofOps, shared := ListInfo{Name: "own", Owner: "alice"}, ListInfo{Name: "d", Owner: "@dev"},
		ListInfo{Name: "o", Owner: "@ops"}, ListInfo{Name: "s"}
	if canSeeList(narrowed, private) || !canSeeList(narrowed, ofDev) || canSeeList(narrowed, ofOps) || !canSeeList(narrowed, shared) {
		t.Error("lists for a narrowed asker")
	}
	if !canSeeList(alice, ofOps) {
		t.Error("a group's list for one of its members")
	}
	if canSeeTask(narrowed, &Task{ID: "x", Owner: "alice"}) || !canSeeTask(alice, &Task{ID: "x", Owner: "alice"}) {
		t.Error("a private task for an asker without the private place")
	}
	if !canSeeTask(narrowed, &Task{ID: "x", Owner: "@dev"}) || canSeeTask(narrowed, &Task{ID: "x", Owner: "@ops"}) {
		t.Error("a group's task for a narrowed asker")
	}
}

// What safePath opens is the file the slug names, which placeOf judged: no
// symlink on the way to it (a shared name linking to a private page, a
// folder linking to a private folder, a link out of the wiki), no name a
// file system reads as another (Windows' '\', its dropped trailing '.' and
// ' ', its ':' and its devices' names: TestWindowsReadsNoNameAsAnother);
// iterPages lists none of those; and placeOf folds the case of
// private/ and groups/ as a case-insensitive volume does.
func TestSafePathOpensWhatPlaceOfJudged(t *testing.T) {
	testWiki(t, map[string]string{"notes/a": "# A\n", "private/alice/diary": "# Diary\n", "odd./x": "# Odd\n", "dots.": "# Dots\n"})
	withUsers(t, "alice", "bob")
	root := cfg.Data
	for link, target := range map[string]string{
		"leak.md": filepath.Join(root, "private/alice/diary.md"), "peek": filepath.Join(root, "private/alice"),
		"notes/passwd.md": "/etc/passwd", "state": filepath.Join(root, ".zone"), "dangling.md": filepath.Join(root, "nowhere.md"),
	} {
		if err := os.Symlink(target, filepath.Join(root, link)); err != nil {
			t.Fatal(err)
		}
	}
	for _, slug := range []string{"leak", "peek/diary", "peek/new", "notes/passwd", "state/config", "dangling",
		`private\alice\diary`, "private./alice/diary", "private /alice/diary", "odd./x", "dots.", "notes/a ",
		"", ".", "..", "../x", "notes/../../x", "/etc/passwd"} {
		if p := safePath(slug); p != "" {
			t.Errorf("safePath(%q) = %s", slug, p)
		}
	}
	for slug, file := range map[string]string{"notes/a": "notes/a.md", "notes/new": "notes/new.md", "notes/": "notes.md",
		"notes//a": "notes/a.md", "private/alice/./diary": "private/alice/diary.md", "private/alice/..": "private.md"} {
		if p := safePath(slug); p != filepath.Join(root, filepath.FromSlash(file)) {
			t.Errorf("safePath(%q) = %q, want %s", slug, p, file)
		}
	}
	// What a slug ending in .. opens (private.md, beside the folders) is what it is judged as.
	if !placeOf("private/alice/..").shared() {
		t.Error("private/alice/.. is judged other than the page it opens")
	}
	var slugs []string
	for _, p := range iterPages() {
		slugs = append(slugs, p.Slug)
	}
	if slices.Sort(slugs); !slices.Equal(slugs, []string{"notes/a", "private/alice/diary"}) {
		t.Errorf("iterPages: %q", slugs)
	}
	// A wiki whose folder is a symlink finds its pages all the same.
	link := filepath.Join(t.TempDir(), "wiki")
	if err := os.Symlink(root, link); err != nil {
		t.Fatal(err)
	}
	cfg.Data = link
	if p := safePath("notes/a"); p != filepath.Join(root, "notes/a.md") {
		t.Errorf("through a linked wiki folder: %q", p)
	}
	cfg.Data = root
	for rel, want := range map[string]place{"PRIVATE/alice/x": {ofUser, "alice"}, "Private/Alice/x": {ofUser, "alice"},
		"Groups/Dev/x": {ofGroup, "dev"}, "groupſ/dev/x": {ofGroup, "dev"}, "privateſ/alice/x": {}} {
		if p := placeOf(rel); p != want {
			t.Errorf("placeOf(%q) = %+v, want %+v", rel, p, want)
		}
	}
}

// A name Windows would read as another file, a stream or a device is no
// page's or folder's on any system (a wiki's files are copied and cloned):
// safePath opens none, iterPages lists none. An upload may have a ':'
// (a screenshot named by the time), but no device's name.
func TestWindowsReadsNoNameAsAnother(t *testing.T) {
	testWiki(t, map[string]string{"notes/a": "# A\n", "nul": "# Nul\n", "aux/x": "# X\n", "notes/10:30": "# Time\n", "COM1.old": "# Port\n"})
	withUsers(t, "alice")
	for _, slug := range []string{"nul", "NUL", "con", "prn", "aux", "aux/x", "notes/aux", "com1", "COM9", "lpt0", "com¹", "conin$", "CONOUT$",
		"nul.old", "notes/con.backup", "nul .x", "notes/10:30", "a:b", "notes:x/a", "notes/a:$DATA"} {
		if p := safePath(slug); p != "" {
			t.Errorf("safePath(%q) = %s", slug, p)
		}
	}
	// Names that only look like one.
	for _, slug := range []string{"console", "nulls", "com", "com10", "lpt", "auxiliary", "notes/con-artists", "icon", "x.nul", "com1x"} {
		if safePath(slug) == "" {
			t.Errorf("safePath(%q) refused", slug)
		}
	}
	var slugs []string
	for _, p := range iterPages() {
		slugs = append(slugs, p.Slug)
	}
	if !slices.Equal(slugs, []string{"notes/a"}) {
		t.Errorf("iterPages: %q", slugs)
	}
	for name, want := range map[string]bool{"shot 10:30.png": true, "console.log": true, "nul": false, "NUL.png": false, "aux.tar.gz": false, "com3.txt": false} {
		if got := uploadNamed(name); got != want {
			t.Errorf("uploadNamed(%q) = %v", name, got)
		}
	}
}

// What uploadPath gives, for every door to an upload, is the file the name
// says in the place's uploads: no '\' (which leads out of the folder on
// Windows) or other name safePath refuses, no file or folder reached
// through a symlink; validName refuses a '\' too.
func TestUploadPathIsTheNamesOwnFile(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	root := cfg.Data
	for _, dir := range []string{"_assets", "private/alice/_assets", "private/bob"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0o777); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "private/alice/_assets/photo.png"), []byte("hers"), 0o666); err != nil {
		t.Fatal(err)
	}
	for link, target := range map[string]string{
		"_assets/leak.png":    filepath.Join(root, "private/alice/_assets/photo.png"),
		"_assets/passwd.txt":  "/etc/passwd",
		"_assets/gone.png":    filepath.Join(root, "nowhere.png"),
		"private/bob/_assets": filepath.Join(root, "private/alice/_assets"),
	} {
		if err := os.Symlink(target, filepath.Join(root, link)); err != nil {
			t.Fatal(err)
		}
	}
	bob, alice := place{ofUser, "bob"}, place{ofUser, "alice"}
	for _, c := range []struct {
		p    place
		name string
	}{
		{place{}, "leak.png"}, {place{}, "passwd.txt"}, {place{}, "gone.png"}, {bob, "photo.png"}, {bob, "new.png"},
		{bob, `..\alice\_assets\photo.png`}, {place{}, `a\..\..\private\alice\_assets\photo.png`},
		{place{}, ".."}, {place{}, ".git"}, {place{}, "a/b.png"}, {place{}, "pic."}, {place{}, "pic "}, {place{}, ""},
	} {
		if p, ok := uploadPath(c.p, c.name); ok {
			t.Errorf("uploadPath(%v, %q) = %s", c.p, c.name, p)
		}
	}
	if p, ok := uploadPath(alice, "photo.png"); !ok || p != filepath.Join(root, "private/alice/_assets/photo.png") {
		t.Errorf("her own upload: %q %v", p, ok)
	}
	if p, ok := uploadPath(place{}, "new.png"); !ok || p != filepath.Join(root, "_assets/new.png") {
		t.Errorf("a new shared upload: %q %v", p, ok)
	}
	if uploadName(`C:\Users\bob\pic.png`) != "" || uploadName("pic.") != "" || uploadName("dir/pic.png") != "pic.png" {
		t.Error("uploadName")
	}
	if validName(`..\..\etc\passwd`) {
		t.Error(`validName takes a '\'`)
	}
}

// A name a group went by is its own to take back, and no other group's: not
// a new group's, nor another's renamed, though the name's folder has no
// history yet. A group that went by a name and was renamed before it had a
// page left the name free, and took it back with the next group's past
// once that one had moved on. Two groups that went by one name (a wiki
// from before) take it back neither.
func TestANameAGroupWentByIsTakenForOthers(t *testing.T) {
	testWiki(t, nil)
	s := settings()
	s.Was = map[string][]string{"zzz": {"finance"}}
	if !groupNameTaken(s, "finance", "") {
		t.Error("a new group takes a name zzz went by")
	}
	if !groupNameTaken(s, "finance", "people") {
		t.Error("another group's rename takes a name zzz went by")
	}
	if groupNameTaken(s, "finance", "zzz") {
		t.Error("zzz can't take its own name back")
	}
	if groupNameTaken(s, "fresh", "") {
		t.Error("a name nothing went by is taken")
	}
	s.Was["people"] = []string{"finance"}
	if !groupNameTaken(s, "finance", "zzz") || !groupNameTaken(s, "finance", "people") {
		t.Error("one of two groups that went by a name takes it back")
	}
}
