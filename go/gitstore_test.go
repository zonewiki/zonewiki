package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/gitrepo"
)

func TestRelativeTime(t *testing.T) {
	at := clock(t, time.Date(2026, 5, 1, 12, 0, 0, 0, time.Local))
	for _, c := range []struct {
		ago  time.Duration
		want string
	}{
		{5 * time.Second, "just now"}, {65 * time.Second, "1 minute ago"}, {5*time.Minute + 5*time.Second, "5 minutes ago"},
		{61 * time.Minute, "1 hour ago"}, {181 * time.Minute, "3 hours ago"}, {24*time.Hour + time.Minute, "1 day ago"},
		{240*time.Hour + time.Minute, "10 days ago"}, {65 * 24 * time.Hour, "2 months ago"},
		{366 * 24 * time.Hour, "1 year ago"}, {800 * 24 * time.Hour, "2 years ago"},
	} {
		if got := relativeTime(at.Add(-c.ago)); got != c.want {
			t.Errorf("%v ago: %q, want %q", c.ago, got, c.want)
		}
	}
}

func TestCommitMessage(t *testing.T) {
	for path, want := range map[string]string{
		"notes.md": "Save notes", "journal/day.md": "Save journal/day", "_assets/a.png": "Update _assets/a.png",
	} {
		if got := commitMessage(path); got != want {
			t.Errorf("commitMessage(%q) = %q, want %q", path, got, want)
		}
	}
}

// Only what comes before the first @@ is a header: a removed line "-- x"
// reads "--- x" in the diff and stays.
func TestDiffLines(t *testing.T) {
	if diffLines("") != nil {
		t.Error("an empty diff has lines")
	}
	got := diffLines("diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n context\n--- x\n+++ y")
	want := []diffLine{{"diff-hunk", "⋯"}, {"diff-ctx", " context"}, {"diff-del", "--- x"}, {"diff-add", "+++ y"}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v", got)
	}
}

// Regression: git (2.47+) detached its auto-maintenance after each commit;
// with Zone as the container's PID 1 nobody reaped it, and zombies piled
// up to the PID limit.
func TestGitDoesNotDetach(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	for _, key := range []string{"maintenance.autoDetach", "gc.autoDetach"} {
		if r := runGit("config", "--get", key); strings.TrimSpace(r.Stdout) != "false" {
			t.Errorf("%s: %q", key, r.Stdout)
		}
	}
	// Nor does a commit pack: Zone does, between commits (packSoon).
	for key, want := range map[string]string{"gc.auto": "0", "maintenance.auto": "false"} {
		if r := runGit("config", "--get", key); strings.TrimSpace(r.Stdout) != want {
			t.Errorf("%s: %q, want %s", key, r.Stdout, want)
		}
	}
}

// packed waits for a pack that runs to end (at most limit); false: one still runs.
func packed(limit time.Duration) bool {
	for end := time.Now().Add(limit); ; time.Sleep(5 * time.Millisecond) {
		packing.Lock()
		running := packing.running
		packing.Unlock()
		if !running || time.Now().After(end) {
			return !running
		}
	}
}

// gitObjects: the wiki's loose objects and packs.
func gitObjects(t *testing.T) (loose, packs int) {
	t.Helper()
	r := runGit("count-objects", "-v")
	if r.Code != 0 {
		t.Fatalf("count-objects: %s", r.Stderr)
	}
	for _, line := range strings.Split(r.Stdout, "\n") {
		key, value, _ := strings.Cut(line, ": ")
		n, _ := strconv.Atoi(value)
		switch key {
		case "count":
			loose = n
		case "packs":
			packs = n
		}
	}
	return loose, packs
}

// Commits leave loose objects; every packEvery commits Zone packs them in
// the background, one pack at a time (a commit that comes while one runs
// starts no second, and is not forgotten), and at the start when many are
// loose. The loose objects go only while no commit runs. The history is
// whole afterwards.
func TestPackSoon(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	if !packed(10 * time.Second) {
		t.Fatal("a pack runs before the test")
	}
	state := func() (commits int, running bool) {
		packing.Lock()
		defer packing.Unlock()
		return packing.commits, packing.running
	}
	set := func(commits int, running bool) {
		packing.Lock()
		defer packing.Unlock()
		packing.commits, packing.running = commits, running
	}
	t.Cleanup(func() { packed(10 * time.Second); set(0, false) })
	n := 0
	commit := func() {
		t.Helper()
		n++
		name := fmt.Sprintf("p%d.md", n)
		writeTestPage(t, name)
		if err := gitCommit("", "Add "+name, filepath.Join(cfg.Data, name)); err != nil {
			t.Fatal(err)
		}
	}
	// A commit counts, and packs nothing.
	set(0, false)
	for range 3 {
		commit()
	}
	loose, packs := gitObjects(t)
	if c, running := state(); c != 3 || running || loose < 6 || packs != 0 {
		t.Fatalf("after 3 commits: counted %d, running %v, %d loose objects, %d packs", c, running, loose, packs)
	}
	// A commit that commits nothing (the file is as committed) doesn't count.
	if err := gitCommit("", "Nothing", filepath.Join(cfg.Data, "p1.md")); err != nil {
		t.Fatal(err)
	}
	if c, _ := state(); c != 3 {
		t.Errorf("a commit of nothing counted: %d", c)
	}
	// Up to the one before packEvery: nothing.
	set(packEvery-2, false)
	commit()
	if c, running := state(); c != packEvery-1 || running {
		t.Fatalf("one before: counted %d, running %v", c, running)
	}
	// While a pack runs (here: one that never ends), the next starts none.
	set(packEvery-1, true)
	commit()
	commit()
	time.Sleep(100 * time.Millisecond)
	if c, running := state(); c != packEvery+1 || !running {
		t.Errorf("while one runs: counted %d, running %v", c, running)
	}
	if l, p := gitObjects(t); p != 0 || l <= loose {
		t.Errorf("a second pack ran beside the first: %d loose objects, %d packs", l, p)
	}
	// It ended: the next commit packs, what it made too or not (it runs beside).
	set(packEvery+1, false)
	commit()
	if !packed(30 * time.Second) {
		t.Fatal("the pack didn't end")
	}
	if c, _ := state(); c != 0 {
		t.Errorf("counted after a pack started: %d", c)
	}
	if l, p := gitObjects(t); p != 1 || l > 3 {
		t.Errorf("after the pack: %d loose objects, %d packs", l, p)
	}
	// The packEvery-th commit packs, counted from there.
	for range packEvery - 1 {
		packing.Lock()
		packing.commits++ // a commit, without git
		packing.Unlock()
	}
	if _, running := state(); running {
		t.Fatal("packed before its time")
	}
	commit()
	if c, running := state(); c != 0 || !packed(30*time.Second) {
		t.Errorf("the %dth commit: counted %d, running %v", packEvery, c, running)
	}
	if _, p := gitObjects(t); p != 2 {
		t.Errorf("packs after the second: %d", p)
	}
	// The loose objects go while no commit runs: with a commit under way
	// (here: its lock held) the pack is made and waits with removing them.
	commit()
	commit()
	before, _ := gitObjects(t)
	commitMu.Lock()
	set(0, true)
	go packNow(0)
	for end := time.Now().Add(30 * time.Second); time.Now().Before(end); time.Sleep(5 * time.Millisecond) {
		if _, p := gitObjects(t); p == 3 {
			break
		}
	}
	time.Sleep(100 * time.Millisecond)
	l, p := gitObjects(t)
	_, running := state()
	commitMu.Unlock()
	if p != 3 || l != before || before < 4 || !running {
		t.Errorf("beside a commit: %d loose objects (were %d), %d packs, running %v", l, before, p, running)
	}
	if !packed(30 * time.Second) {
		t.Fatal("the pack didn't end after the commit")
	}
	if l, p := gitObjects(t); l != 0 || p != 3 {
		t.Errorf("after the commit: %d loose objects, %d packs", l, p)
	}
	// At the start: a few loose objects are left alone,
	commit()
	commit()
	packAtStart()
	if !packed(30 * time.Second) {
		t.Fatal("the start's look didn't end")
	}
	if l, p := gitObjects(t); l < 4 || l >= packLoose || p != 3 || looseObjects() != l {
		t.Errorf("a start with few loose objects: %d (counted %d), %d packs", l, looseObjects(), p)
	}
	// many are packed.
	for i := range packLoose {
		if err := os.WriteFile(filepath.Join(cfg.Data, fmt.Sprintf("loose%d.md", i)), []byte(fmt.Sprintf("# %d\n", i)), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	if r := runGit("add", "-A"); r.Code != 0 {
		t.Fatalf("add: %s", r.Stderr)
	}
	if got := looseObjects(); got < packLoose {
		t.Fatalf("loose objects before a start: %d", got)
	}
	packAtStart()
	if !packed(30 * time.Second) {
		t.Fatal("the start's pack didn't end")
	}
	if l, p := gitObjects(t); l != 0 || p != 4 {
		t.Errorf("after a start: %d loose objects, %d packs", l, p)
	}
	if c, _ := state(); c != 2 {
		t.Errorf("the start's pack changed the count: %d", c)
	}
	// The history is whole: every commit, every page, nothing left to commit.
	if r := runGit("fsck", "--full", "--strict"); r.Code != 0 {
		t.Errorf("fsck: %s%s", r.Stderr, r.Stdout)
	}
	if r := runGit("log", "--format=%s"); strings.Count(r.Stdout, "\nAdd p") != n-1 || !strings.HasPrefix(r.Stdout, fmt.Sprintf("Add p%d.md\n", n)) {
		t.Errorf("the log:\n%s", r.Stdout)
	}
	if r := runGit("show", "HEAD:p1.md"); r.Stdout != "# P\n" {
		t.Errorf("the first page: %q", r.Stdout)
	}
	if r := runGit("status", "--porcelain", "--", "p1.md", fmt.Sprintf("p%d.md", n)); strings.TrimSpace(r.Stdout) != "" {
		t.Errorf("status: %q", r.Stdout)
	}
	// A join doesn't wait for a commit, nor a commit for it: with a commit
	// under way (its lock held) the one pack is written, and the packs it
	// stands for stay until the commit has ended.
	commit()
	set(0, true)
	go packNow(0)
	if !packed(30*time.Second) || wikiRepo().TimeToJoin() {
		t.Fatal("five packs of a size are to be joined, and were not")
	}
	if l, p := gitObjects(t); l != 0 || p != 1 {
		t.Errorf("after the join: %d loose objects, %d packs", l, p)
	}
	commit()
	commitMu.Lock()
	joined := make(chan gitrepo.Result, 1)
	go func() { joined <- joinPacks(wikiRepo()) }()
	for end := time.Now().Add(30 * time.Second); time.Now().Before(end); time.Sleep(5 * time.Millisecond) {
		if _, p := gitObjects(t); p == 2 {
			break
		}
	}
	time.Sleep(100 * time.Millisecond)
	l, p = gitObjects(t)
	commitMu.Unlock()
	if p != 2 || l < 3 || len(joined) != 0 {
		t.Errorf("a join beside a commit: %d loose objects, %d packs, ended %v", l, p, len(joined) != 0)
	}
	if r := <-joined; r.Code != 0 {
		t.Errorf("the join: %+v", r)
	}
	if l, p := gitObjects(t); l != 0 || p != 1 {
		t.Errorf("after the commit: %d loose objects, %d packs", l, p)
	}
	// A wiki without git packs nothing, at the start or later.
	cfg.GitEnabled = false
	set(packEvery-1, false)
	packAtStart()
	if err := gitCommit("", "No git", filepath.Join(cfg.Data, "p1.md")); err != nil {
		t.Fatal(err)
	}
	if c, running := state(); c != packEvery-1 || running {
		t.Errorf("without git: counted %d, running %v", c, running)
	}
}

// A hung git counts as failed (124), so a request doesn't block on it.
func TestHungGitCountsAsFailed(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	start := time.Now()
	r := runGitFor(50*time.Millisecond, "-c", "alias.hang=!sleep 5", "hang")
	if r.Code != 124 || !strings.Contains(r.Stderr, "timed out") || time.Since(start) > 3*time.Second {
		t.Errorf("%+v after %v", r, time.Since(start))
	}
}

// gitLogAll fills its limit with the commits see takes, however far back
// they are: here across several of its (growing) batches.
func TestGitLogAllFillsTheLimit(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	var want []string
	for i := range 160 {
		name := fmt.Sprintf("hidden/p%d.md", i)
		if i%40 == 0 {
			name = fmt.Sprintf("p%d.md", i)
			want = append([]string{name}, want...)
		}
		writeTestPage(t, name)
		gitCommit("", "Add "+name, filepath.Join(cfg.Data, name))
	}
	see := func(path string) bool { return !strings.HasPrefix(path, "hidden/") }
	var got []string
	for _, e := range gitLogAll("", "", 3, see, nil) {
		got = append(got, e.Files...)
	}
	if !reflect.DeepEqual(got, want[:3]) {
		t.Errorf("%v, want %v", got, want[:3])
	}
	if n := len(gitLogAll("", "", 10, see, nil)); n != 5 { // and the initial import (index.md)
		t.Errorf("all of them: %d", n)
	}
}

// A name git would quote without -z (a letter outside ASCII, a space, a
// comma, a quote) comes out of the history as the file's own: in the
// wiki's log (Changes, which judged a quoted name a shared file's), across
// a rename in a page's log, in the latest commits and among the deleted
// pages (which left such a page out).
func TestGitLogsNameFilesAsTheyAre(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	odd := []string{"notes/Kündigung an Chef.md", "notes/Brief, Kopie (1).md", "_assets/Arztbrief März.pdf"}
	for _, name := range odd {
		writeTestPage(t, name)
		gitCommit("", "Add "+name, filepath.Join(cfg.Data, name))
	}
	var files []string
	for _, e := range gitLogAll("", "", 10, nil, nil) {
		files = append(files, e.Files...)
	}
	for _, name := range odd {
		if !slices.Contains(files, name) {
			t.Errorf("gitLogAll: %q not in %q", name, files)
		}
		if latestCommits(10)[name] == nil {
			t.Errorf("latestCommits: no %q", name)
		}
	}
	old, renamed := filepath.Join(cfg.Data, odd[0]), filepath.Join(cfg.Data, "notes/Größe.md")
	if err := os.Rename(old, renamed); err != nil {
		t.Fatal(err)
	}
	gitCommit("", "Rename", old, renamed)
	entries := gitLog(renamed, 0)
	if len(entries) != 2 || entries[0].Path != "notes/Größe.md" || entries[1].Path != odd[0] {
		t.Errorf("gitLog: %+v", entries)
	}
	if err := os.Remove(filepath.Join(cfg.Data, odd[1])); err != nil {
		t.Fatal(err)
	}
	gitCommit("", "Delete", filepath.Join(cfg.Data, odd[1]))
	if deleted := listDeletedPages(); len(deleted) != 1 || deleted[0].Slug != "notes/Brief, Kopie (1)" {
		t.Errorf("listDeletedPages: %+v", deleted)
	}
}

// A commit starts only after a NUL: a \x1e in a subject (a tag's name in
// its rename's message) doesn't start another, whose author and files a
// writer would choose.
func TestLogRecordsStartAfterANul(t *testing.T) {
	out := "\x1eaaa\x1falice\x1fRename tag #x\x1ebbb\x1fdana\x1fy\x00\nM\x00notes/a.md\x00\x1eccc\x1fbob\x1fEmpty\x00"
	want := []logRecord{
		{"aaa\x1falice\x1fRename tag #x\x1ebbb\x1fdana\x1fy", []string{"M", "notes/a.md"}},
		{"ccc\x1fbob\x1fEmpty", nil},
	}
	if got := logRecords(out); !reflect.DeepEqual(got, want) {
		t.Errorf("%q", got)
	}
}

// A page view asks git about a page once, and again only after the page's
// file or the repository changed: Zone's own commit, a change on disk, a
// commit made in a terminal.
func TestLastChangedAsksGitOnce(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	path := filepath.Join(cfg.Data, "p.md")
	writeTestPage(t, "p.md")
	view := func() (changed *changedView, commit *commitView, asked int64) {
		before := changeLookups.Load()
		changed, commit = lastChanged("p", path, "P")
		return changed, commit, changeLookups.Load() - before
	}
	if changed, commit, asked := view(); changed == nil || commit == nil || commit.Filepath != "p.md" || asked != 1 {
		t.Fatalf("a new page, not committed: %v %v, asked %d", changed, commit, asked)
	}
	if _, commit, asked := view(); commit == nil || asked != 0 {
		t.Errorf("seen again: %v, asked %d", commit, asked)
	}
	gitCommit("", "Add p", path)
	changed, commit, asked := view()
	if changed == nil || commit != nil || asked != 1 || changed.Date != now().Format("2006-01-02") || changed.Rel != "just now" {
		t.Fatalf("after Zone's commit: %+v %v, asked %d", changed, commit, asked)
	}
	for range 3 {
		if _, commit, asked := view(); commit != nil || asked != 0 {
			t.Errorf("committed, seen again: %v, asked %d", commit, asked)
		}
	}
	// A change on disk, as from an editor: another size.
	if err := os.WriteFile(path, []byte("# P\n\nMore.\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	if _, commit, asked := view(); commit == nil || asked != 1 {
		t.Errorf("after a change on disk: %v, asked %d", commit, asked)
	}
	// A commit made in a terminal, not by Zone.
	if r := runGit("commit", "-q", "-m", "by hand", "--", "p.md"); r.Code != 0 {
		t.Fatal(r.Stderr)
	}
	if _, commit, asked := view(); commit != nil || asked != 1 {
		t.Errorf("after a commit by hand: %v, asked %d", commit, asked)
	}
	// A page git never saw, and no repository's reflog: asked each time.
	if changed, commit, _ := view(); changed == nil || commit != nil {
		t.Errorf("committed: %v %v", changed, commit)
	}
	if err := os.Remove(filepath.Join(cfg.Data, ".git", "logs", "HEAD")); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if _, _, asked := view(); asked != 1 {
			t.Errorf("without a reflog: asked %d", asked)
		}
	}
}

// Before Zone exits idle, a draft is committed in the name of who wrote it:
// only memory knows, and the next Zone couldn't say.
func TestCommitDrafts(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	configWiki(t, "[user alice]\npassword = "+hashPassword("alice-pw")+"\n[user bob]\npassword = "+hashPassword("bob-pw")+"\n")
	cfg.GitEnabled = true
	gitSetup()
	t.Cleanup(func() { forgetDrafts("p.md", "q.md") })
	for _, rel := range []string{"p.md", "q.md"} {
		writeTestPage(t, rel)
	}
	noteDraft(filepath.Join(cfg.Data, "p.md"), "alice")
	commitDrafts()
	if got := strings.TrimSpace(runGit("log", "-1", "--format=%an: %s", "--", "p.md").Stdout); got != "alice: Save p" {
		t.Errorf("alice's draft: %q", got)
	}
	if isUncommitted(filepath.Join(cfg.Data, "p.md")) || !isUncommitted(filepath.Join(cfg.Data, "q.md")) {
		t.Error("the draft isn't committed, or a file nobody's draft is")
	}
	if draftAuthor(filepath.Join(cfg.Data, "p.md")) != "" {
		t.Error("the draft is still remembered")
	}
}

func writeTestPage(t *testing.T, rel string) {
	t.Helper()
	p := filepath.Join(cfg.Data, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte("# P\n"), 0o666); err != nil {
		t.Fatal(err)
	}
}

// A wiki whose history has users' commits but no settings file lost its
// users (a clone of its remote): Zone won't start on it, open.
func TestMissingUsers(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	_ = os.Remove(configPath()) // testWiki's
	cfg.GitEnabled = true
	gitSetup()
	writeTestPage(t, "p.md")
	gitCommit("", "By Zone", filepath.Join(cfg.Data, "p.md"))
	if err := missingUsers(); err != nil {
		t.Errorf("no users ever: %v", err)
	}
	writeTestPage(t, "q.md")
	gitCommit("maria", "By maria", filepath.Join(cfg.Data, "q.md"))
	err := missingUsers()
	if err == nil || !strings.Contains(err.Error(), "maria made changes") || !strings.Contains(err.Error(), "create an empty") {
		t.Fatalf("users in history: %v", err)
	}
	writeTestPage(t, ".zone/config") // any file, an empty one too: on purpose
	if err := missingUsers(); err != nil {
		t.Errorf("with a file: %v", err)
	}
}

// A wiki's repository is told not to look for differences between uploads
// when it packs (noDeltaForUploads): its own attributes, in no commit,
// written once and beside what is there.
func TestNoDeltaForUploads(t *testing.T) {
	testWiki(t, map[string]string{"p": "# P\n"})
	cfg.GitEnabled = true
	gitSetup()
	if !isDir(filepath.Join(cfg.Data, ".git")) {
		t.Fatal("no repository")
	}
	path := filepath.Join(cfg.Data, ".git", "info", "attributes")
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("*.psd binary"), 0o666); err != nil {
		t.Fatal(err)
	}
	noDeltaForUploads(cfg.Data)
	noDeltaForUploads(cfg.Data)
	b, _ := os.ReadFile(path)
	if want := "*.psd binary\n_assets/** -delta\nprivate/*/_assets/** -delta\ngroups/*/_assets/** -delta\n_tasks/files/*/_assets/** -delta\n"; string(b) != want {
		t.Errorf("attributes: %q, want %q", b, want)
	}
	if out := runGitEnv(gitTimeout, "", []string{"GIT_LITERAL_PATHSPECS=0"}, "check-attr", "delta", "--", "_assets/photo.png", "private/anna/_assets/a.pdf", "p.md").Stdout; !strings.Contains(out, "_assets/photo.png: delta: unset") ||
		!strings.Contains(out, "private/anna/_assets/a.pdf: delta: unset") || !strings.Contains(out, "p.md: delta: unspecified") {
		t.Errorf("git reads them as: %s", out)
	}
	if out := runGit("status", "--porcelain").Stdout; strings.Contains(out, "attributes") {
		t.Errorf("the attributes are in the work tree: %s", out)
	}
}
