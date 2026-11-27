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
// with Zonewiki as the container's PID 1 nobody reaped it, and zombies piled
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
	// Nor does a commit pack: Zonewiki does, between commits (packSoon).
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

// Commits leave loose objects; every packEvery commits Zonewiki packs them in
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
	for _, e := range gitLogAll("", nil, 3, see, nil) {
		got = append(got, e.Files...)
	}
	if !reflect.DeepEqual(got, want[:3]) {
		t.Errorf("%v, want %v", got, want[:3])
	}
	if n := len(gitLogAll("", nil, 10, see, nil)); n != 5 { // and the initial import (index.md)
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
	for _, e := range gitLogAll("", nil, 10, nil, nil) {
		files = append(files, e.Files...)
	}
	for _, name := range odd {
		if !slices.Contains(files, name) {
			t.Errorf("gitLogAll: %q not in %q", name, files)
		}
		if latestCommits(10).files[name] == nil {
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
// file or the repository changed: Zonewiki's own commit, a change on disk, a
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
	if changed == nil || commit != nil || asked != 1 || !strings.HasPrefix(changed.At, now().Format("2006-01-02 ")) || changed.Rel != "just now" {
		t.Fatalf("after Zonewiki's commit: %+v %v, asked %d", changed, commit, asked)
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
	// A commit made in a terminal, not by Zonewiki.
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

// Before Zonewiki exits idle, a draft is committed in the name of who wrote it:
// only memory knows, and the next Zonewiki couldn't say.
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
// users (a clone of its remote): Zonewiki won't start on it, open.
func TestMissingUsers(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	_ = os.Remove(configPath()) // testWiki's
	cfg.GitEnabled = true
	gitSetup()
	writeTestPage(t, "p.md")
	gitCommit("", "By Zonewiki", filepath.Join(cfg.Data, "p.md"))
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

// gitWrites: helpers that change the test wiki's pages and commit each
// change, as Zonewiki's page writes do (rel: a file's path in the wiki).
type gitWrites struct{ t *testing.T }

func (g gitWrites) at(rel string) string { return filepath.Join(cfg.Data, filepath.FromSlash(rel)) }

func (g gitWrites) write(rel, text string) {
	g.t.Helper()
	if err := os.MkdirAll(filepath.Dir(g.at(rel)), 0o777); err != nil {
		g.t.Fatal(err)
	}
	if err := os.WriteFile(g.at(rel), []byte(text), 0o666); err != nil {
		g.t.Fatal(err)
	}
	if err := gitCommit("", "Save "+rel, g.at(rel)); err != nil {
		g.t.Fatal(err)
	}
}

func (g gitWrites) move(from, to string) {
	g.t.Helper()
	if err := os.MkdirAll(filepath.Dir(g.at(to)), 0o777); err != nil {
		g.t.Fatal(err)
	}
	if err := os.Rename(g.at(from), g.at(to)); err != nil {
		g.t.Fatal(err)
	}
	if err := gitCommit("", "Rename "+from, g.at(from), g.at(to)); err != nil {
		g.t.Fatal(err)
	}
}

func (g gitWrites) remove(rel string) {
	g.t.Helper()
	if err := os.Remove(g.at(rel)); err != nil {
		g.t.Fatal(err)
	}
	if err := gitCommit("", "Delete "+rel, g.at(rel)); err != nil {
		g.t.Fatal(err)
	}
}

// gitLog follows renames itself (a log of one path, which the
// commit-graph's filters answer, instead of --follow, which reads the whole
// history) and has what git log --follow has: the same commits, the file's
// name at each and the lines each added and deleted. Across a rename, one
// with an edit, a move between folders, a page deleted and made again (its
// older commits are the name's, as --follow has them) and a rename onto a
// name a deleted page had (the deleted page's commits are not), with and
// without a limit.
func TestGitLogFollowsRenamesAsGitDoes(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	g := gitWrites{t}
	text := "# Page\n\none\ntwo\nthree\nfour\nfive\nsix\n"
	g.write("a.md", text)
	g.write("a.md", text+"seven\n")
	g.move("a.md", "b.md")
	g.write("b.md", text+"eight\n")
	// A rename with an edit, then a move to another folder.
	if err := os.WriteFile(g.at("b.md"), []byte(text+"eight\nnine\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	g.move("b.md", "notes/c.md")
	g.move("notes/c.md", "archive/c.md")
	g.write("archive/c.md", text+"ten\n")
	// Deleted and made again: one name's history.
	g.write("d.md", "# D\n\nfirst life\n")
	g.remove("d.md")
	g.write("d.md", "# D\n\nsecond life\n")
	g.write("d.md", "# D\n\nsecond life, edited\n")
	// A rename onto a name a deleted page had.
	g.write("e.md", "# E\n\nthe old e\n")
	g.remove("e.md")
	x := "# X\n\nx's own text, long enough to be found again\nline\nline\n"
	g.write("x.md", x)
	g.write("x.md", x+"more\n")
	g.move("x.md", "e.md")
	g.write("solo.md", "# Solo\n")

	// What git log --follow says: a commit, the file's name there and its
	// lines added and deleted.
	follow := func(rel string) []string {
		t.Helper()
		var out []string
		for _, record := range logRecords(runGit("log", "--follow", "-z", "--numstat", "--format=\x1e%H", "--", rel).Stdout) {
			name, added, deleted := "", 0, 0
			for i := 0; i < len(record.fields); i++ {
				cols := strings.SplitN(record.fields[i], "\t", 3)
				if len(cols) != 3 {
					continue
				}
				added, _ = strconv.Atoi(cols[0])
				deleted, _ = strconv.Atoi(cols[1])
				if name = cols[2]; name == "" && i+2 < len(record.fields) {
					name, i = record.fields[i+2], i+2 // a rename: the old name, the new
				}
			}
			out = append(out, fmt.Sprintf("%s %s +%d -%d", record.head[:8], name, added, deleted))
		}
		return out
	}
	ours := func(rel string, max int) []string {
		var out []string
		for _, e := range gitLog(g.at(rel), max) {
			out = append(out, fmt.Sprintf("%s %s +%d -%d", e.Sha[:8], e.Path, e.Added, e.Deleted))
		}
		return out
	}
	for rel, n := range map[string]int{"archive/c.md": 7, "d.md": 4, "e.md": 3, "solo.md": 1} {
		want := follow(rel)
		if len(want) != n {
			t.Fatalf("%s: git log --follow has %d commits, the test meant %d: %q", rel, len(want), n, want)
		}
		if got := ours(rel, 0); !reflect.DeepEqual(got, want) {
			t.Errorf("%s:\n got %q\nwant %q", rel, got, want)
		}
		for _, max := range []int{1, 3, 5} {
			if got := ours(rel, max); !reflect.DeepEqual(got, want[:min(max, len(want))]) {
				t.Errorf("%s, at most %d: %q, want %q", rel, max, got, want[:min(max, len(want))])
			}
		}
	}
}

// The deleted pages are read from the whole history once, and after that
// from what was committed since: a page deleted later is there too (once,
// with its latest deletion), one made again is gone, and a history that was
// rewritten is read anew. Each comes with its deleting commit and its title
// as its last version has it.
func TestDeletedPagesReadWhatIsNew(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	g := gitWrites{t}
	write := func(slug, title string) { g.write(slug+".md", "# "+title+"\n") }
	remove := func(slug string) { g.remove(slug + ".md") }
	list := func() []string {
		var out []string
		for _, p := range listDeletedPages() {
			if _, subject, ok := commitOf(p.Sha); !ok || subject != "Delete "+p.Slug+".md" {
				t.Errorf("%s: %q is not its deletion (%q)", p.Slug, p.Sha, subject)
			}
			out = append(out, p.Slug+": "+p.Title)
		}
		return out
	}
	write("one", "One")
	write("two", "Two")
	remove("one")
	before := strings.TrimSpace(runGit("rev-parse", "HEAD").Stdout)
	if got := list(); !reflect.DeepEqual(got, []string{"one: One"}) {
		t.Fatalf("first: %q", got)
	}
	remove("two")
	write("three", "Three")
	write("three", "Three, changed")
	remove("three")
	if got := list(); !reflect.DeepEqual(got, []string{"three: Three, changed", "two: Two", "one: One"}) {
		t.Errorf("after more deletions: %q", got)
	}
	write("one", "One again")
	remove("one")
	if got := list(); !reflect.DeepEqual(got, []string{"one: One again", "three: Three, changed", "two: Two"}) {
		t.Errorf("deleted again: %q", got)
	}
	write("two", "Two again")
	if got := list(); !reflect.DeepEqual(got, []string{"one: One again", "three: Three, changed"}) {
		t.Errorf("made again: %q", got)
	}
	// The history rewritten: back to before, and another deletion on it.
	if r := runGit("reset", "-q", "--hard", before); r.Code != 0 {
		t.Fatal(r.Stderr)
	}
	write("four", "Four")
	remove("four")
	if got := list(); !reflect.DeepEqual(got, []string{"four: Four", "one: One"}) {
		t.Errorf("rewritten: %q", got)
	}
}

// Zonewiki writes the commit-graph, with the paths each commit changed, when it
// packs, and at the start for a wiki that has none (git's own maintenance,
// which would write it, is off): a log of one path asks its filters.
func TestTheCommitGraphIsWritten(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	if !packed(10 * time.Second) {
		t.Fatal("a pack runs before the test")
	}
	gitWrites{t}.write("p.md", "# P\n")
	repo := wikiRepo()
	if repo.HasGraph() {
		t.Fatal("a graph before the start")
	}
	packAtStart()
	if !packed(30*time.Second) || !repo.HasGraph() {
		t.Fatal("no graph after the start")
	}
	r := repo.Run(t.Context(), gitrepo.Call{Timeout: time.Minute, Env: []string{"GIT_TRACE2_PERF=1"}},
		"log", "--format=%H", "--", "index.md")
	if !strings.Contains(r.Stderr, `"filter_not_present":0`) {
		t.Errorf("the log didn't use the graph's filters: %s", r.Stderr)
	}
}

// A wiki folder inside another repository's working tree gets a
// repository of its own only where the outer one ignores the folder (a
// home folder kept in git for its dotfiles); otherwise Zonewiki refuses to
// start, and says how to go on. A folder that is a repository already is
// the wiki's wherever it lies, and a folder in no repository is made one.
func TestAWikiInsideAnotherRepository(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	git := func(dir string, args ...string) {
		t.Helper()
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	start := func(wiki string) error {
		t.Helper()
		testWiki(t, nil)
		real, err := filepath.EvalSymlinks(wiki)
		if err != nil {
			t.Fatal(err)
		}
		cfg.Data, cfg.GitEnabled = real, true
		return gitSetup()
	}
	outer, _ := filepath.EvalSymlinks(t.TempDir())
	git(outer, "init", "-q")
	notes := filepath.Join(outer, "docs", "notes")
	if err := os.MkdirAll(notes, 0o777); err != nil {
		t.Fatal(err)
	}

	// Not ignored: refused, nothing made, and the message says how to go on.
	err := start(notes)
	if err == nil || !strings.Contains(err.Error(), "is inside the git repository "+outer) ||
		!strings.Contains(err.Error(), ".gitignore (docs/notes/)") {
		t.Fatalf("not refused as it should be: %v", err)
	}
	if _, err := os.Stat(filepath.Join(notes, ".git")); err == nil {
		t.Error("a nested repository was made all the same")
	}

	// Ignored by the outer repository (the folder, or a folder above it): its own repository.
	for _, ignore := range []string{"docs/notes/\n", "docs/\n", "*\n"} {
		if err := os.WriteFile(filepath.Join(outer, ".gitignore"), []byte(ignore), 0o666); err != nil {
			t.Fatal(err)
		}
		if err := start(notes); err != nil {
			t.Fatalf("ignored as %q, refused: %v", ignore, err)
		}
		if _, err := os.Stat(filepath.Join(notes, ".git")); err != nil {
			t.Fatalf("ignored as %q, no repository made", ignore)
		}
		_ = os.RemoveAll(filepath.Join(notes, ".git"))
	}

	// A folder whose name has glob characters is judged by that name alone.
	odd := filepath.Join(outer, "a*", "x")
	if err := os.MkdirAll(odd, 0o777); err != nil {
		t.Fatal(err)
	}
	_ = os.MkdirAll(filepath.Join(outer, "ab", "x"), 0o777)
	if err := os.WriteFile(filepath.Join(outer, ".gitignore"), []byte("ab/\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	if err := start(odd); err == nil {
		t.Error("a*/x passed for ab/x, which is ignored")
	}

	// A repository already (made by hand, or by Zonewiki before): the wiki's, wherever it lies.
	_ = os.Remove(filepath.Join(outer, ".gitignore"))
	git(notes, "init", "-q")
	if err := start(notes); err != nil {
		t.Fatalf("an existing repository refused: %v", err)
	}

	// The outer repository's own top is no folder inside it: Zonewiki goes on with it.
	if err := start(outer); err != nil {
		t.Fatalf("the repository's top refused: %v", err)
	}
}

// TestZonewikisOwnCommitsShowNoName: Zonewiki's own commits show no author,
// under its name now and under "Zone", the name older wikis' commits carry.
func TestZonewikisOwnCommitsShowNoName(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice")
	for name, want := range map[string]string{"Zonewiki": "", "Zone": "", "alice": "alice"} {
		if got := commitAuthor(name); got != want {
			t.Errorf("commitAuthor(%q) = %q, want %q", name, got, want)
		}
	}
}
