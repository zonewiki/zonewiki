package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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
	if hideNames(asUser("bob"), msg) != msg {
		t.Error("without users nothing is private")
	}
	withUsers(t, "alice", "bob")
	for user, want := range map[string]string{
		"alice": msg,
		"bob":   "Promote a private page to idea (MCP)",
	} {
		if got := hideNames(asUser(user), msg); got != want {
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
	} {
		if got := hideNames(asUser("bob"), msg); got != want {
			t.Errorf("%q: %q", msg, got)
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
	if got := hideNames(asUser("carol"), msg); got != "Rename a group's page to roadmap, and notgroups/dev/x" {
		t.Errorf("hideNames for carol: %q", got)
	}
	if got := hideNames(asUser("alice"), msg); got != msg {
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
