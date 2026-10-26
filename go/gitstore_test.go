package main

import (
	"os/exec"
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
// with Leaf as the container's PID 1 nobody reaped it, and zombies piled
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
