package gitrepo

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
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
	// A commit doesn't pack (Pack does, when its caller says).
	for key, want := range map[string]string{"gc.auto": "0", "maintenance.auto": "false"} {
		if res := r.Run(context.Background(), call, "config", "--get", key); strings.TrimSpace(res.Stdout) != want {
			t.Errorf("%s = %q, want %s (a commit doesn't pack)", key, res.Stdout, want)
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

// must runs git and fails the test when git does.
func must(t *testing.T, r Repo, call Call, args ...string) string {
	t.Helper()
	if call.Timeout == 0 {
		call.Timeout = time.Minute
	}
	res := r.Run(context.Background(), call, args...)
	if res.Code != 0 {
		t.Fatalf("git %s: %d %s%s", strings.Join(args, " "), res.Code, res.Stderr, res.Stdout)
	}
	return res.Stdout
}

// objects: the repository's loose objects and packs (git count-objects -v).
func objects(t *testing.T, r Repo) (loose, packs int) {
	t.Helper()
	for _, line := range strings.Split(must(t, r, Call{}, "count-objects", "-v"), "\n") {
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

// commitFiles writes n files (their names from first on) and commits them.
func commitFiles(t *testing.T, r Repo, call Call, first, n int) {
	t.Helper()
	for i := first; i < first+n; i++ {
		if err := os.WriteFile(filepath.Join(r.Dir, fmt.Sprintf("f%d.txt", i)), []byte(fmt.Sprintf("file %d\n", i)), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	must(t, r, Call{}, "add", "-A")
	must(t, r, call, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", fmt.Sprintf("Files from %d", first))
}

// A commit doesn't pack, even in a repository set to pack at the first
// loose object (its own config says gc.auto = 1): the environment's setting
// wins. The same commit without that environment does pack, so the test
// would notice.
func TestACommitDoesNotPack(t *testing.T) {
	r := repo(t)
	for _, kv := range [][2]string{{"gc.auto", "1"}, {"gc.autoDetach", "false"}, {"maintenance.autoDetach", "false"}} {
		must(t, r, Call{}, "config", kv[0], kv[1])
	}
	// git counts the loose objects by one of their 256 folders: enough of
	// them that it has some.
	commitFiles(t, r, Call{}, 0, 3000)
	loose, packs := objects(t, r)
	if loose < 3000 || packs != 0 {
		t.Fatalf("after a commit: %d loose objects, %d packs: it packed", loose, packs)
	}
	commitFiles(t, r, Call{}, 3000, 1)
	if l, p := objects(t, r); l <= loose || p != 0 {
		t.Errorf("after another: %d loose objects, %d packs", l, p)
	}
	// Without the environment's settings git does what the repository says.
	commitFiles(t, r, Call{Env: []string{"GIT_CONFIG_COUNT=0"}}, 3001, 1)
	if l, p := objects(t, r); l != 0 || p != 1 {
		t.Errorf("the same commit with git's own packing on: %d loose objects, %d packs (this test notices nothing)", l, p)
	}
}

// Pack packs the loose objects into a pack of their own, and leaves the
// packs there are; Prune then removes the objects that are packed, and no
// other; many packs become one. The history is whole at every step.
func TestPack(t *testing.T) {
	r := repo(t)
	ctx := context.Background()
	if res := r.Pack(ctx, time.Minute); res.Code != 0 {
		t.Errorf("an empty repository: %+v", res)
	}
	if res := r.Prune(ctx, time.Minute); res.Code != 0 {
		t.Errorf("an empty repository, pruned: %+v", res)
	}
	for i := range 5 {
		commitFiles(t, r, Call{}, i*10, 10)
	}
	whole := func(what string, commits int) {
		t.Helper()
		if res := r.Run(ctx, Call{Timeout: time.Minute}, "fsck", "--full", "--strict"); res.Code != 0 {
			t.Errorf("%s: fsck: %s%s", what, res.Stderr, res.Stdout)
		}
		if got := strings.Count(must(t, r, Call{}, "log", "--format=%s"), "\n"); got != commits {
			t.Errorf("%s: %d commits, want %d", what, got, commits)
		}
		if got := must(t, r, Call{}, "show", "HEAD:f7.txt"); got != "file 7\n" {
			t.Errorf("%s: a file of the first commit: %q", what, got)
		}
		if got := must(t, r, Call{}, "status", "--porcelain"); got != "" {
			t.Errorf("%s: the working tree: %q", what, got)
		}
	}
	loose, packs := objects(t, r)
	if loose < 60 || packs != 0 {
		t.Fatalf("before: %d loose objects, %d packs", loose, packs)
	}
	// Prune alone removes nothing: nothing is packed.
	if res := r.Prune(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("prune: %+v", res)
	}
	if l, p := objects(t, r); l != loose || p != 0 {
		t.Errorf("pruned before a pack: %d loose objects (were %d), %d packs", l, loose, p)
	}
	if res := r.Pack(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("pack: %+v", res)
	}
	// Packed, and still there loose: Pack removes nothing (a commit may run).
	if l, p := objects(t, r); l != loose || p != 1 {
		t.Errorf("after the pack: %d loose objects (were %d), %d packs", l, loose, p)
	}
	whole("packed", 5)
	// What a commit adds between Pack and Prune stays.
	commitFiles(t, r, Call{}, 500, 1)
	if res := r.Prune(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("prune: %+v", res)
	}
	if l, p := objects(t, r); l != 3 || p != 1 {
		t.Errorf("after the prune: %d loose objects (want the 3 of the commit since), %d packs", l, p)
	}
	whole("pruned", 6)
	if res := r.Pack(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("pack: %+v", res)
	}
	if res := r.Prune(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("prune: %+v", res)
	}
	if l, p := objects(t, r); l != 0 || p != 2 {
		t.Errorf("after a second pack: %d loose objects, %d packs", l, p)
	}
	// Nothing new: nothing happens, the pack stays the file it is.
	pack := func() []string {
		l, _ := filepath.Glob(filepath.Join(r.Dir, ".git", "objects", "pack", "*.pack"))
		return l
	}
	first := pack()
	if res := r.Pack(ctx, time.Minute); res.Code != 0 || len(pack()) != 2 || !slicesContain(pack(), first[0]) || !slicesContain(pack(), first[1]) {
		t.Errorf("a pack of nothing: %+v, packs %v", res, pack())
	}
	// What is new gets a pack of its own, up to packsMany + 1 of them; the
	// next pack makes them one.
	commits := 6
	for len(pack()) <= packsMany {
		before := len(pack())
		commitFiles(t, r, Call{}, 1000+commits, 1)
		commits++
		if res := r.Pack(ctx, time.Minute); res.Code != 0 {
			t.Fatalf("pack: %+v", res)
		}
		if res := r.Prune(ctx, time.Minute); res.Code != 0 {
			t.Fatalf("prune: %+v", res)
		}
		if l, _ := objects(t, r); l != 0 || len(pack()) != before+1 || !slicesContain(pack(), first[0]) {
			t.Fatalf("pack %d: %d loose objects, packs %d, the first one kept: %v", before+1, l, len(pack()), slicesContain(pack(), first[0]))
		}
	}
	commitFiles(t, r, Call{}, 1000+commits, 1)
	commits++
	if res := r.Pack(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("pack of many: %+v", res)
	}
	// Many by now: Join makes them one (and removes what is loose and packed).
	if !r.ManyPacks() {
		t.Fatalf("%d packs are not many", len(pack()))
	}
	if res := r.Join(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("join: %+v", res)
	}
	if r.ManyPacks() {
		t.Error("still many packs after Join")
	}
	if l, p := objects(t, r); l != 0 || p != 1 {
		t.Errorf("after many packs: %d loose objects, %d packs", l, p)
	}
	whole("many packs made one", commits)
	// Their time limits hold.
	if res := r.Pack(ctx, time.Nanosecond); res.Code != TimedOut {
		t.Errorf("a pack out of time: %+v", res)
	}
	if res := r.Prune(ctx, time.Nanosecond); res.Code != TimedOut {
		t.Errorf("a prune out of time: %+v", res)
	}
	whole("a pack stopped", commits)
}

func slicesContain(l []string, s string) bool {
	for _, x := range l {
		if x == s {
			return true
		}
	}
	return false
}
