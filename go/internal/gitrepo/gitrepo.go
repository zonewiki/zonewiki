// Package gitrepo runs git in a repository: one process a call, under a
// time limit and a context, its output as text. What Zone asks of git, and
// what it makes of the answers, is the server's (go/gitstore.go).
package gitrepo

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"slices"
	"strings"
	"time"
)

// TimedOut: the exit code of a git that ran out of time or was stopped (as
// the shell's timeout gives it).
const TimedOut = 124

// git (2.47+) starts its automatic maintenance after a commit as a detached
// background process. In the container Zone is PID 1 and never reaps that
// orphan, so every commit left a zombie process behind until the PID limit
// was reached and nothing could start any more. Not detached, git runs the
// (usually trivial) maintenance itself and reaps its own children.
//
// Every path Zone hands git is a file's name, never a pattern: a page may be
// called anything ("*", "p?", ":(glob)**/x"), and read as a pathspec such a
// name would match other files, another user's private pages among them
// (git show <sha> -- '*.md'). So git takes all pathspecs literally
// (GIT_LITERAL_PATHSPECS, git's --literal-pathspecs for every call); a call
// that means a pattern, or a command that refuses the setting (check-ignore),
// turns it off for itself (Call.Env).
var baseEnv = append(os.Environ(),
	"GIT_LITERAL_PATHSPECS=1",
	"GIT_CONFIG_COUNT=2",
	"GIT_CONFIG_KEY_0=maintenance.autoDetach", "GIT_CONFIG_VALUE_0=false",
	"GIT_CONFIG_KEY_1=gc.autoDetach", "GIT_CONFIG_VALUE_1=false",
)

// Repo is a repository's working tree.
type Repo struct{ Dir string }

// Result: how a call ended. Code is git's exit code, TimedOut when it ran
// out of time or its context ended, 1 when it couldn't start.
type Result struct {
	Code           int
	Stdout, Stderr string
}

// Call: how a call runs: at most Timeout, with Input on git's stdin and
// Env after git's own environment (GIT_INDEX_FILE=…).
type Call struct {
	Timeout time.Duration
	Input   string
	Env     []string
}

// Run runs git with args in the repository. A git that can't start, runs
// out of time or is stopped with ctx counts as failed, like one that exits
// non-zero. Line ends in its output are "\n".
func (r Repo) Run(ctx context.Context, call Call, args ...string) Result {
	ctx, cancel := context.WithTimeout(ctx, call.Timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir, cmd.Env = r.Dir, append(slices.Clone(baseEnv), call.Env...)
	if call.Input != "" {
		cmd.Stdin = strings.NewReader(call.Input)
	}
	// A process git started (a hook, an alias) may hold its output open
	// after git is killed: stop waiting for it soon after.
	cmd.WaitDelay = time.Second
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	switch ctx.Err() {
	case context.DeadlineExceeded:
		return Result{TimedOut, "", fmt.Sprintf("git timed out after %ds", int(call.Timeout.Seconds()))}
	case context.Canceled:
		return Result{TimedOut, "", "git was stopped: the request ended"}
	}
	code := 0
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		code = exit.ExitCode()
	} else if err != nil {
		return Result{1, "", err.Error()}
	}
	return Result{code, newlines(stdout.String()), newlines(stderr.String())}
}

// newlines: s with "\n" for every line end.
func newlines(s string) string {
	if !strings.Contains(s, "\r") {
		return s
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
}
