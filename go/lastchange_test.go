package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// lastCommitsWiki: a wiki with users (alice, bob) in git, and lastCommits
// starting over in it.
func lastCommitsWiki(t *testing.T, users bool) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	text := ""
	if users {
		text = "[user alice]\npassword = " + hashPassword("alice-pw") + "\n[user bob]\npassword = " + hashPassword("bob-pw") + "\n"
	}
	configWiki(t, text)
	cfg.GitEnabled = true
	gitSetup()
}

// writePageSaying writes a page whose text is what (each change another).
func writePageSaying(t *testing.T, rel, what string) string {
	t.Helper()
	path := filepath.Join(cfg.Data, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("# P\n\n"+what+"\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	return path
}

// commitPage changes a page and commits it, by Zone's gitCommit.
func commitPage(t *testing.T, rel, user, msg string) string {
	t.Helper()
	path := writePageSaying(t, rel, msg)
	if err := gitCommit(user, msg, path); err != nil {
		t.Fatal(err)
	}
	return path
}

// byHand changes a page and commits it as a terminal would, by author.
func byHand(t *testing.T, rel, author string) {
	t.Helper()
	writePageSaying(t, rel, "by hand, "+author)
	if r := runGit("add", "--", rel); r.Code != 0 {
		t.Fatal(r.Stderr)
	}
	if r := runGit("commit", "-q", "--author", author+" <"+author+"@example.com>", "-m", "by hand", "--", rel); r.Code != 0 {
		t.Fatal(r.Stderr)
	}
}

// whoFor: who changed each page last, as whoChanged tells user.
func whoFor(t *testing.T, user asker, slugs ...string) map[string]string {
	t.Helper()
	by := whoChanged(&Req{user: user})
	if by == nil {
		t.Fatal("not known")
	}
	out := map[string]string{}
	for _, slug := range slugs {
		st, err := os.Stat(filepath.Join(cfg.Data, slug+".md"))
		if err != nil {
			t.Fatal(err)
		}
		out[slug] = by(slug, st.ModTime())
	}
	return out
}

func wantWho(t *testing.T, what string, got, want map[string]string) {
	t.Helper()
	for slug, w := range want {
		if got[slug] != w {
			t.Errorf("%s: %s is %q, want %q", what, slug, got[slug], w)
		}
	}
}

// A page's last commit says who made it, and whether through an assistant
// and which: a user, a user's assistant, someone committing in a terminal;
// not Zone's own commit, nor a page whose file changed since (whose that
// change is isn't known), nor one never committed. A visitor learns no
// user's name, but an assistant's, and that a visitor made a change.
func TestWhoChangedAPage(t *testing.T) {
	lastCommitsWiki(t, true)
	commitPage(t, "p.md", "alice", "Update p")
	commitPage(t, "q.md", "bob", "Update q (MCP)"+commitTrailers("Claude", ""))
	commitPage(t, "r.md", "alice", "Update r (MCP)"+commitTrailers("", ""))
	commitPage(t, "s (MCP).md", "alice", "Update s (MCP)") // a person's: no trailer says an assistant's
	commitPage(t, "z.md", "", "Update z")
	byHand(t, "notes/h.md", "carol")
	commitPage(t, "v.md", visitor, "Update v")
	later := commitPage(t, "later.md", "alice", "Update later")
	writeTestPage(t, "new.md")
	writePageSaying(t, "later.md", "Changed on disk")
	soon := time.Now().Add(5 * time.Second)
	if err := os.Chtimes(later, soon, soon); err != nil {
		t.Fatal(err)
	}
	slugs := []string{"p", "q", "r", "s (MCP)", "z", "notes/h", "v", "later", "new"}
	wantWho(t, "alice", whoFor(t, asUser("alice"), slugs...), map[string]string{
		"p": "by alice", "q": "by bob through Claude", "r": "by alice's assistant", "s (MCP)": "by alice", "z": "",
		"notes/h": "by carol", "v": "by anonymous", "later": "", "new": "",
	})
	wantWho(t, "a visitor", whoFor(t, asUser(visitor), slugs...), map[string]string{
		"p": "", "q": "by an assistant through Claude", "r": "by an assistant", "s (MCP)": "", "z": "",
		"notes/h": "", "v": "by anonymous", "later": "", "new": "",
	})
}

// Without users there is nobody to name: only whether an assistant made it.
func TestWhoChangedWithoutUsers(t *testing.T) {
	lastCommitsWiki(t, false)
	commitPage(t, "p.md", "", "Update p")
	commitPage(t, "q.md", "", "Update q (MCP)"+commitTrailers("Claude", ""))
	byHand(t, "h.md", "carol")
	wantWho(t, "no users", whoFor(t, asUser(""), "p", "q", "h"), map[string]string{
		"p": "", "q": "by an assistant through Claude", "h": "",
	})
}

// The history is read once, and only as far back as the pages need: here
// not the commit that made both pages, since later ones changed each. Then
// git is asked again only when the repository moved, by Zone's commit or
// one made in a terminal, for the commits since; a reset to an older
// commit reads the history again; a page deleted has no last change.
func TestLastCommitsFollowTheRepository(t *testing.T) {
	lastCommitsWiki(t, true)
	a, b := writePageSaying(t, "a.md", "a"), writePageSaying(t, "b.md", "b")
	if err := gitCommit("bob", "Make a and b", a, b); err != nil {
		t.Fatal(err)
	}
	for i := range 20 {
		commitPage(t, "other.md", "bob", "Update other "+itoa(i))
	}
	commitPage(t, "a.md", "alice", "Update a")
	commitPage(t, "b.md", "alice", "Update b")
	if err := os.Remove(filepath.Join(cfg.Data, "other.md")); err != nil {
		t.Fatal(err)
	}
	if err := gitCommit("bob", "Delete other", filepath.Join(cfg.Data, "other.md")); err != nil {
		t.Fatal(err)
	}
	before, scanned := lastCommitGits.Load(), scannedCommits.Load()
	alice := asUser("alice")
	wantWho(t, "scanned", whoFor(t, alice, "a", "b"), map[string]string{"a": "by alice", "b": "by alice"})
	if n := scannedCommits.Load() - scanned; n != 3 {
		t.Errorf("the scan read %d commits, want 3 (the deletion, b, a)", n)
	}
	if n := lastCommitGits.Load() - before; n != 2 {
		t.Errorf("the scan asked git %d times, want 2 (the head, the log)", n)
	}
	asked := func() int64 {
		before := lastCommitGits.Load()
		if whoChanged(&Req{user: alice}) == nil {
			t.Fatal("not known")
		}
		return lastCommitGits.Load() - before
	}
	if n := asked(); n != 0 {
		t.Errorf("nothing moved: git asked %d times", n)
	}
	commitPage(t, "a.md", "bob", "Update a (MCP)"+commitTrailers("Claude", ""))
	byHand(t, "b.md", "carol")
	commitPage(t, "c.md", "alice", "Update c")
	if err := os.Remove(b); err != nil {
		t.Fatal(err)
	}
	if err := gitCommit("bob", "Delete b", b); err != nil {
		t.Fatal(err)
	}
	writeTestPage(t, "b.md") // there again, never committed
	wantWho(t, "moved", whoFor(t, alice, "a", "b", "c"), map[string]string{
		"a": "by bob through Claude", "b": "", "c": "by alice",
	})
	if n := asked(); n != 0 {
		t.Errorf("seen the moves: git asked %d times", n)
	}
	if err := os.Remove(filepath.Join(cfg.Data, "b.md")); err != nil {
		t.Fatal(err)
	}
	// Back three commits (to bob's assistant's change of a): c is not in
	// git any more, b is alice's again (its file as old as her commit: a
	// checkout makes it newer, and then whose it is isn't said).
	if r := runGit("reset", "-q", "--hard", "HEAD~3"); r.Code != 0 {
		t.Fatal(r.Stderr)
	}
	past := time.Now().Add(-time.Hour)
	if err := os.Chtimes(b, past, past); err != nil {
		t.Fatal(err)
	}
	scanned = scannedCommits.Load()
	wantWho(t, "reset", whoFor(t, alice, "a", "b"), map[string]string{"a": "by bob through Claude", "b": "by alice"})
	if scannedCommits.Load() == scanned {
		t.Error("a reset didn't read the history again")
	}
}
