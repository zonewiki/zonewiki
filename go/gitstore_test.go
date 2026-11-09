package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
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
	for _, e := range gitLogAll("", "", 3, see) {
		got = append(got, e.Files...)
	}
	if !reflect.DeepEqual(got, want[:3]) {
		t.Errorf("%v, want %v", got, want[:3])
	}
	if n := len(gitLogAll("", "", 10, see)); n != 5 { // and the initial import (index.md)
		t.Errorf("all of them: %d", n)
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

// untrackSettings takes a settings file Zone used to commit out of git, in
// a commit of its own; what was staged by hand stays staged, the file on disk.
func TestUntrackSettings(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	// As an older Zone left it: the file committed, its exception in exclude.
	writeTestPage(t, ".zone/config")
	writeTestPage(t, "p.md")
	runGit("add", "-f", ".zone/config", "p.md")
	runGit("commit", "-q", "-m", "As before")
	writeTestPage(t, "staged.md")
	runGit("add", "staged.md")
	excludeState(cfg.Data)
	untrackSettings()
	if runGit("ls-files", "--", ".zone/config").Stdout != "" {
		t.Error("still tracked")
	}
	if got := runGit("log", "-1", "--format=%an|%s", "--name-status").Stdout; !strings.Contains(got, "Zone|Keep the settings file out of git") ||
		!strings.Contains(got, "D\t.zone/config") || strings.Contains(got, "staged.md") {
		t.Errorf("commit: %q", got)
	}
	if !exists(filepath.Join(cfg.Data, ".zone", "config")) {
		t.Error("the file went")
	}
	if status := runGit("status", "--porcelain").Stdout; !strings.Contains(status, "A  staged.md") || strings.Contains(status, ".zone") {
		t.Errorf("status: %q", status)
	}
	head := runGit("rev-parse", "HEAD").Stdout
	untrackSettings() // again: nothing to do
	if runGit("rev-parse", "HEAD").Stdout != head {
		t.Error("a second commit")
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
