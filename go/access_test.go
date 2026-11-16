package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
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
		if p := placeOf(slug); p.user() != "" || !p.shared() || !canSee("", slug) || !canWrite("", slug) {
			t.Errorf("%s: private without users", slug)
		}
	}
	if !canSeePath("", "private/alice/_tasks/tasks.json") || !canSeePath("", "private/alice/_assets/a.png") ||
		canSeePath("", ".leaf/config") || canSeePath("", ".zone/config") || canSeePath("", ".zone/secret_key") {
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
		if canSee("alice", c.slug) != c.alice || canSee("bob", c.slug) != c.bob || canWrite("alice", c.slug) != c.alice {
			t.Errorf("%s: alice %v bob %v", c.slug, canSee("alice", c.slug), canSee("bob", c.slug))
		}
		if canSee("", c.slug) != !c.private {
			t.Errorf("%s: no user", c.slug)
		}
	}
	for path, want := range map[string]bool{
		"index.md": true, "private/alice/diary.md": true, "private/bob/x.md": false, "private/alice/img.png": true,
		"_assets/a.png": true, "private/alice/_assets/a.png": true, "private/mary-ann/_assets/a.png": false,
		"private/_assets/a.png": false, "_tasks/tasks.json": true, "private/alice/_tasks/tasks.json": true,
		"private/mary-ann/_tasks/tasks.json": false, "_tasks/users.json": true,
	} {
		if canSeePath("alice", path) != want {
			t.Errorf("canSeePath(alice, %s) != %v", path, want)
		}
	}
}

func TestHiddenPageAndSharing(t *testing.T) {
	testWiki(t, map[string]string{"secret/x": "# X\n"}) // noshare secret/ (testWiki)
	withUsers(t, "alice")
	alice, aliceSharing := &Req{user: "alice"}, &Req{user: "alice", sharing: true}
	if hiddenPage(alice, "private/alice/x") || !hiddenPage(&Req{user: "bob"}, "private/alice/x") {
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
	if noteDate(note) != "2026-10-27" || privateFolder("alice") != "private/alice" || privateFolder("") != "" {
		t.Error("noteDate, privateFolder")
	}
}

func TestInQuicknote(t *testing.T) {
	testWiki(t, nil)
	old, mine, theirs := "2026-10-27_12-00-00_idea", "private/alice/2026-10-27_12-00-00_idea", "private/carol/2026-10-27_12-00-00_idea"
	if !inQuicknote("", old) || inQuicknote("", "notes") {
		t.Error("without users: the top-level notes")
	}
	withUsers(t, "alice")
	if !inQuicknote("alice", mine) || inQuicknote("alice", old) || inQuicknote("alice", theirs) || !inQuicknote("", old) {
		t.Error("with users: only the user's own; Zone itself sees all")
	}
}

func TestHideNames(t *testing.T) {
	testWiki(t, nil)
	msg := "Promote private/alice/2026-10-27_12-00-00_idea to idea (MCP)"
	if hideNames("bob", msg) != msg {
		t.Error("without users nothing is private")
	}
	withUsers(t, "alice", "bob")
	for user, want := range map[string]string{
		"alice": msg,
		"bob":   "Promote a private page to idea (MCP)",
	} {
		if got := hideNames(user, msg); got != want {
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
	} {
		if got := hideNames("bob", msg); got != want {
			t.Errorf("%q: %q", msg, got)
		}
	}
}

// Only a shared page taken into a private folder is refused.
func TestRefuseMakingPrivate(t *testing.T) {
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
		if got := refuseMakingPrivate(map[string]string{c.from: c.to}) != ""; got != c.want {
			t.Errorf("%s → %s: %v", c.from, c.to, got)
		}
	}
	settings().Users = nil
	if refuseMakingPrivate(map[string]string{"ideas": "private/alice/ideas"}) != "" {
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
	if !shared.shared() || alice.user() != "alice" || !alice.own("alice") || alice.own("bob") || none.user() != "" || none.shared() ||
		none.sees("alice") || none.sees("") || shared.own("alice") {
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
