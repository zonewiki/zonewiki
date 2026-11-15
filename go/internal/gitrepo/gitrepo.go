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
	"path/filepath"
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
//
// A commit doesn't pack: git's own maintenance after a commit (gc.auto) runs
// inside it, and when its turn comes that one commit, and the request that
// made it, waits for the whole repository's loose objects to be packed.
// Whoever commits packs when it suits them (Pack, Prune).
var baseEnv = append(os.Environ(),
	"GIT_LITERAL_PATHSPECS=1",
	"GIT_CONFIG_COUNT=4",
	"GIT_CONFIG_KEY_0=maintenance.autoDetach", "GIT_CONFIG_VALUE_0=false",
	"GIT_CONFIG_KEY_1=gc.autoDetach", "GIT_CONFIG_VALUE_1=false",
	"GIT_CONFIG_KEY_2=gc.auto", "GIT_CONFIG_VALUE_2=0",
	"GIT_CONFIG_KEY_3=maintenance.auto", "GIT_CONFIG_VALUE_3=false",
)

// Pack packs the repository's loose objects into a pack of their own (git
// repack -l: what is packed already stays as it is, so it costs what is
// new): the versions of a file become differences, and a task file written
// whole at every change takes what changed. The objects it packed are still
// there loose as well, until Prune. Beside commits; it may take long in a
// large repository, timeout bounds it.
func (r Repo) Pack(ctx context.Context, timeout time.Duration) Result {
	return r.Run(ctx, Call{Timeout: timeout}, "repack", "-l", "-q")
}

// ManyPacks: the repository has more packs than packsMany: time for Join.
func (r Repo) ManyPacks() bool {
	packs, _ := filepath.Glob(filepath.Join(r.Dir, ".git", "objects", "pack", "*.pack"))
	return len(packs) > packsMany
}

// Join makes the repository's packs one (git repack -a -d -l), with the
// differences between a file's versions computed anew (-f, in a window of
// 50). Without -f git keeps the differences the packs have, and a pack of
// what was loose holds each file it touches whole once more: measured on
// 5,000 tasks in 40 files (2026-11-15), 10,000 changes took 57 MB packed in
// steps, 52 MB joined without -f and 8 MB joined with it, in under a
// second. So a change costs what it changed (about 0.4 KB with its commit
// and trees) once it is joined, and a task file needs no other format for
// that. Like Prune not beside a commit: it removes loose objects' folders
// too. It reads every object, so it takes as long as the repository is
// large; uploads are left out of the comparing (NoDeltaAttributes), which
// is what would make it slow.
func (r Repo) Join(ctx context.Context, timeout time.Duration) Result {
	return r.Run(ctx, Call{Timeout: timeout}, "repack", "-a", "-d", "-l", "-q", "-f", "--window=50")
}

// NoDeltaAttributes: lines for the repository's .git/info/attributes that
// keep git from looking for differences between the files under each of
// folders (glob patterns of folders: "_assets", "private/*/_assets"):
// uploads, images and PDFs, which have none worth finding. With 300 MB of
// them a Join took 15 s, and 0.6 s with these lines, for the same size.
func NoDeltaAttributes(folders ...string) []string {
	out := make([]string, len(folders))
	for i, f := range folders {
		out[i] = f + "/** -delta"
	}
	return out
}

// Prune removes the loose objects that are in a pack (git prune-packed),
// after Pack. Not beside a commit, or any git that writes objects: it
// removes the folders it empties, and a git about to write an object into
// one of them fails (git add: "unable to create temporary file: No such
// file or directory"), which is why Pack leaves it to its caller, who knows
// when no commit runs. Quick: it reads no object.
func (r Repo) Prune(ctx context.Context, timeout time.Duration) Result {
	return r.Run(ctx, Call{Timeout: timeout}, "prune-packed", "-q")
}

// packsMany: more packs than this are made one: after a few packs (some
// hundred commits), so what is packed in steps, each step holding the files
// it touched whole, doesn't add up (it was 20 until Join computed the
// differences anew: joining then gained little).
const packsMany = 4

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
