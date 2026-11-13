package gitrepo

import (
	"context"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func repo(t *testing.T) Repo {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	r := Repo{t.TempDir()}
	if res := r.Run(context.Background(), Call{Timeout: time.Minute}, "init", "-q"); res.Code != 0 {
		t.Fatalf("git init: %s", res.Stderr)
	}
	return r
}

// Output and exit code come back; input goes to git's stdin; a path is a
// name, not a pattern, unless the call says otherwise.
func TestRun(t *testing.T) {
	r := repo(t)
	call := Call{Timeout: time.Minute}
	if res := r.Run(context.Background(), call, "rev-parse", "--is-inside-work-tree"); res.Code != 0 || strings.TrimSpace(res.Stdout) != "true" {
		t.Errorf("rev-parse: %+v", res)
	}
	if res := r.Run(context.Background(), call, "no-such-command"); res.Code == 0 || res.Stderr == "" {
		t.Errorf("an unknown command: %+v", res)
	}
	res := r.Run(context.Background(), Call{Timeout: time.Minute, Input: "a\r\nb\n"}, "hash-object", "--stdin")
	if res.Code != 0 || len(strings.TrimSpace(res.Stdout)) < 40 {
		t.Errorf("stdin: %+v", res)
	}
	for _, key := range []string{"maintenance.autoDetach", "gc.autoDetach"} {
		if res := r.Run(context.Background(), call, "config", "--get", key); strings.TrimSpace(res.Stdout) != "false" {
			t.Errorf("%s = %q, want false (no zombies from a detached maintenance)", key, res.Stdout)
		}
	}
	if res := r.Run(context.Background(), call, "ls-files", "--", "*.md"); res.Code != 0 {
		t.Errorf("a literal pathspec: %+v", res)
	}
}

// A git that hangs is stopped at its time limit, or when its context ends.
func TestRunStops(t *testing.T) {
	r := repo(t)
	start := time.Now()
	res := r.Run(context.Background(), Call{Timeout: 50 * time.Millisecond}, "-c", "alias.hang=!sleep 5", "hang")
	if res.Code != TimedOut || !strings.Contains(res.Stderr, "timed out") || time.Since(start) > 3*time.Second {
		t.Errorf("timeout: %+v after %s", res, time.Since(start))
	}
	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(50*time.Millisecond, cancel)
	start = time.Now()
	res = r.Run(ctx, Call{Timeout: time.Minute}, "-c", "alias.hang=!sleep 5", "hang")
	if res.Code != TimedOut || !strings.Contains(res.Stderr, "stopped") || time.Since(start) > 3*time.Second {
		t.Errorf("cancelled: %+v after %s", res, time.Since(start))
	}
}
