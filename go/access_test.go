package main

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func withUsers(t *testing.T, names ...string) {
	t.Helper()
	cfg.Users = map[string]*User{}
	for _, n := range names {
		cfg.Users[n] = &User{Name: n}
	}
}

func TestAccessWithoutUsers(t *testing.T) {
	testWiki(t, nil)
	for _, slug := range []string{"index", "private/alice/diary", "private/x"} {
		if o, p := pageOwner(slug); o != "" || p || !canSee("", slug) || !canWrite("", slug) {
			t.Errorf("%s: private without users", slug)
		}
	}
	if !canSeePath("", "_deck/users/alice/tasks.json") || !canSeePath("", "_assets/private/alice/a.png") {
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
	} {
		if o, p := pageOwner(c.slug); o != c.owner || p != c.private {
			t.Errorf("pageOwner(%s) = %q %v", c.slug, o, p)
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
		"_assets/a.png": true, "_assets/private/alice/a.png": true, "_assets/private/mary-ann/a.png": false,
		"_assets/private/a.png": false, "_deck/tasks.json": true, "_deck/users/alice/tasks.json": true,
		"_deck/users/mary-ann/tasks.json": false, "_deck/users.json": true,
	} {
		if canSeePath("alice", path) != want {
			t.Errorf("canSeePath(alice, %s) != %v", path, want)
		}
	}
}

func TestHiddenPageAndSharing(t *testing.T) {
	testWiki(t, map[string]string{"secret/x": "# X\n"}) // LEAF_NOSHARE=secret/ (testWiki)
	withUsers(t, "alice")
	alice, aliceSharing := &Req{user: "alice"}, &Req{user: "alice", sharing: true}
	if hiddenPage(alice, "private/alice/x") || !hiddenPage(&Req{user: "bob"}, "private/alice/x") {
		t.Error("private")
	}
	if hiddenPage(alice, "secret/x") || !hiddenPage(aliceSharing, "secret/x") || !hiddenPage(aliceSharing, "private/bob/x") {
		t.Error("sharing")
	}
	if hiddenPage(nil, "private/bob/x") {
		t.Error("no request (Leaf itself) sees everything")
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

func TestScratchNotesInPrivateFolders(t *testing.T) {
	testWiki(t, nil)
	note := "private/alice/2026-10-27_12-00-00_idea"
	if !isScratch("2026-10-27_12-00-00_idea") || isScratch(note) {
		t.Error("without users only top-level notes are scratch notes")
	}
	withUsers(t, "alice")
	if !isScratch(note) || isScratch("private/carol/2026-10-27_12-00-00_idea") ||
		isScratch("private/alice/x/2026-10-27_12-00-00_idea") || isScratch("notes/2026-10-27_12-00-00_idea") {
		t.Error("with users: at the top of a user's folder")
	}
	if noteDate(note) != "2026-10-27" || privateFolder("alice") != "private/alice" || privateFolder("") != "" {
		t.Error("noteDate, privateFolder")
	}
}
