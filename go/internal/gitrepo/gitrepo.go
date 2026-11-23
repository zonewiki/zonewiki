// Package gitrepo runs git in a repository: one process a call, under a
// time limit and a context, its output as text. What Zonewiki asks of git, and
// what it makes of the answers, is the server's (go/gitstore.go).
package gitrepo

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"time"
)

// TimedOut: the exit code of a git that ran out of time or was stopped (as
// the shell's timeout gives it).
const TimedOut = 124

// git (2.47+) starts its automatic maintenance after a commit as a detached
// background process. In the container Zonewiki is PID 1 and never reaps that
// orphan, so every commit left a zombie process behind until the PID limit
// was reached and nothing could start any more. Not detached, git runs the
// (usually trivial) maintenance itself and reaps its own children.
//
// Every path Zonewiki hands git is a file's name, never a pattern: a page may be
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
// Whoever commits packs when it suits them (Pack, Prune, Join, Drop).
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

// WriteGraph adds the commits that aren't in the repository's commit-graph
// to it, with the paths each one changed (Bloom filters; their version 2,
// which hashes names beyond ASCII right), as a layer of its own (--split:
// it costs what is new, and git joins the layers as they grow). git's own
// maintenance would write it (gc), but Zonewiki turns that off (baseEnv), so
// without this no wiki has one, and a log of one path (a page's history,
// its latest change, whether a name was ever used) reads and compares every
// commit of the history: measured 2026-11-19 on 200,000 commits, 0.4 to
// 2.2 s, and 5 to 80 ms with the graph. The first write of a long history
// takes seconds (4 s at 200,000 commits); a commit meanwhile is no matter,
// git takes its lock only to put the new graph in place.
func (r Repo) WriteGraph(ctx context.Context, timeout time.Duration) Result {
	return r.Run(ctx, Call{Timeout: timeout}, "-c", "commitGraph.changedPathsVersion=2",
		"commit-graph", "write", "--reachable", "--changed-paths", "--split")
}

// HasGraph: the repository has a commit-graph (WriteGraph), in one file or
// in layers.
func (r Repo) HasGraph() bool {
	info := filepath.Join(r.Dir, ".git", "objects", "info")
	for _, f := range []string{"commit-graph", filepath.Join("commit-graphs", "commit-graph-chain")} {
		if _, err := os.Stat(filepath.Join(info, f)); err == nil {
			return true
		}
	}
	return false
}

// packDir: where the repository's packs are.
func (r Repo) packDir() string { return filepath.Join(r.Dir, ".git", "objects", "pack") }

// packs: the repository's packs (their .pack files).
func (r Repo) packs() []string {
	packs, _ := filepath.Glob(filepath.Join(r.packDir(), "*.pack"))
	return packs
}

// TimeToJoin: the repository's packs are to be made one (Join): there are
// more than packsMany, and those beside the largest outweigh half of it, or
// there are more than packsMost. By their weight and not by their number
// alone: a join reads the whole history, so one every so many commits costs
// more each time, for ever (measured 2026-11-16 on 40 task files: 3 s at
// 50,000 commits, 23 s at 200,000, on 16 cores), while one that waits until
// the new packs are half of what is joined costs a share of the history
// that stays the same, and the repository is at most half again its joined
// size meanwhile. packsMost bounds their number where the largest pack is
// mostly uploads, which no number of changes outweighs.
func (r Repo) TimeToJoin() bool {
	packs := r.packs()
	if len(packs) <= packsMany {
		return false
	}
	var largest, all int64
	for _, p := range packs {
		if info, err := os.Stat(p); err == nil {
			all += info.Size()
			largest = max(largest, info.Size())
		}
	}
	return (all-largest)*2 > largest || len(packs) > packsMost
}

// Join writes one pack of everything the repository's packs and loose
// objects hold (git repack -a -l), with the differences between a file's
// versions computed anew (-f, in a window of 50), and returns the packs it
// stands for now, for Drop. Without -f git keeps the differences the packs
// have, and a pack of what was loose holds each file it touches whole once
// more: measured on 5,000 tasks in 40 files (2026-11-15), 10,000 changes
// took 57 MB packed in steps, 52 MB joined without -f and 8 MB joined with
// it. So a change costs what it changed (about 0.4 KB with its commit and
// trees) once it is joined, and a task file needs no other format for that.
//
// Beside commits, like Pack: it removes nothing (no -d), so nobody waits
// for it, however long the history is; what is committed meanwhile stays
// loose, for the next Pack. It reads every object, so it takes as long as
// the repository is large, on half of the machine's processors (the other
// half answers requests); uploads are left out of the comparing
// (NoDeltaAttributes). A pack that is kept by hand (a .keep file beside it)
// is left out by git and is none of those returned. No packs are returned
// when it failed, or when git wrote no pack that wasn't there (nothing to
// join): there is nothing to drop then.
func (r Repo) Join(ctx context.Context, timeout time.Duration) (old []string, res Result) {
	r.removeStaleTemps()
	for _, p := range r.packs() {
		if _, err := os.Stat(strings.TrimSuffix(p, ".pack") + ".keep"); err != nil {
			old = append(old, p)
		}
	}
	before := r.packs()
	threads := strconv.Itoa(max(1, runtime.NumCPU()/2))
	res = r.Run(ctx, Call{Timeout: timeout}, "-c", "pack.threads="+threads, "repack", "-a", "-l", "-q", "-f", "--window=50")
	if res.Code != 0 {
		return nil, res
	}
	fresh := 0
	for _, p := range r.packs() {
		if !slices.Contains(before, p) {
			fresh++
		}
	}
	if fresh == 0 {
		return nil, res
	}
	return old, res
}

// Drop removes packs that another pack stands for (Join's), each with the
// files that belong to it; its index first, as git does, so that no git
// finds a pack that is half gone. A git that is reading one of them looks
// for its objects again and finds them in the pack that stands for it (as
// beside git's own repack -d). Quick: it reads no object.
func (r Repo) Drop(packs []string) {
	for _, p := range packs {
		stem := strings.TrimSuffix(p, ".pack")
		for _, ext := range []string{".idx", ".rev", ".bitmap", ".mtimes", ".pack"} {
			_ = os.Remove(stem + ext)
		}
	}
}

// tempsStale: how old a pack that git was writing is before it counts as
// left behind (removeStaleTemps): longer than any Pack or Join runs.
const tempsStale = 24 * time.Hour

// removeStaleTemps removes the packs git was writing when it was stopped (a
// Join out of time, a Zonewiki that ended): git leaves them where they are, as
// large as they got, and nothing else of Zonewiki's would take them away.
func (r Repo) removeStaleTemps() {
	entries, _ := os.ReadDir(r.packDir())
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), "tmp_") && !strings.HasPrefix(e.Name(), ".tmp-") {
			continue
		}
		if info, err := e.Info(); err == nil && !info.IsDir() && time.Since(info.ModTime()) > tempsStale {
			_ = os.Remove(filepath.Join(r.packDir(), e.Name()))
		}
	}
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

// packsMany: with no more packs than this nothing is joined (TimeToJoin):
// a few packs cost nothing, and a young repository joins as it did when
// this number alone decided. packsMost: more are joined whatever they weigh
// (git's own limit, gc.autoPackLimit, is 50 too).
const (
	packsMany = 4
	packsMost = 50
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

// Stream runs git as Run does, but hands its output to read as it comes
// instead of keeping it: for an answer too long to hold (the history from
// its start). When read returns true it has what it wanted, and git is
// stopped, which is no failure; Stdout is empty either way.
func (r Repo) Stream(ctx context.Context, call Call, read func(io.Reader) bool, args ...string) Result {
	ctx, cancel := context.WithTimeout(ctx, call.Timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir, cmd.Env = r.Dir, append(slices.Clone(baseEnv), call.Env...)
	if call.Input != "" {
		cmd.Stdin = strings.NewReader(call.Input)
	}
	cmd.WaitDelay = time.Second
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.StdoutPipe()
	if err == nil {
		err = cmd.Start()
	}
	if err != nil {
		return Result{1, "", err.Error()}
	}
	enough := read(out)
	if enough {
		_ = cmd.Process.Kill()
	} else {
		_, _ = io.Copy(io.Discard, out) // what read left, so that git ends
	}
	err = cmd.Wait()
	switch ctx.Err() {
	case context.DeadlineExceeded:
		return Result{TimedOut, "", fmt.Sprintf("git timed out after %ds", int(call.Timeout.Seconds()))}
	case context.Canceled:
		return Result{TimedOut, "", "git was stopped: the request ended"}
	}
	if enough {
		return Result{0, "", newlines(stderr.String())}
	}
	code := 0
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		code = exit.ExitCode()
	} else if err != nil {
		return Result{1, "", err.Error()}
	}
	return Result{code, "", newlines(stderr.String())}
}

// newlines: s with "\n" for every line end.
func newlines(s string) string {
	if !strings.Contains(s, "\r") {
		return s
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
}
