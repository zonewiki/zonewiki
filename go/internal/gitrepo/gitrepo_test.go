package gitrepo

import (
	"bufio"
	"context"
	"crypto/rand"
	"fmt"
	"io"
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

// Stream hands git's output over as it comes: read to its end, git's exit
// is Run's; read stopped early, git is stopped, and that is no failure.
func TestStream(t *testing.T) {
	r := repo(t)
	for i := range 3 {
		commitFiles(t, r, Call{}, i, 1)
	}
	call := Call{Timeout: time.Minute}
	var all []byte
	res := r.Stream(context.Background(), call, func(out io.Reader) bool {
		all, _ = io.ReadAll(out)
		return false
	}, "log", "--format=%s")
	if res.Code != 0 || string(all) != "Files from 2\nFiles from 1\nFiles from 0\n" {
		t.Errorf("to its end: %+v %q", res, all)
	}
	var first string
	res = r.Stream(context.Background(), call, func(out io.Reader) bool {
		first, _ = bufio.NewReader(out).ReadString('\n')
		return true
	}, "-c", "alias.slow=!git log --format=%s; sleep 5", "slow")
	if res.Code != 0 || first != "Files from 2\n" {
		t.Errorf("stopped early: %+v %q", res, first)
	}
	res = r.Stream(context.Background(), call, func(out io.Reader) bool { return false }, "no-such-command")
	if res.Code == 0 || res.Stderr == "" {
		t.Errorf("an unknown command: %+v", res)
	}
	res = r.Stream(context.Background(), Call{Timeout: 50 * time.Millisecond}, func(out io.Reader) bool {
		_, _ = io.ReadAll(out)
		return false
	}, "-c", "alias.hang=!sleep 5", "hang")
	if res.Code != TimedOut {
		t.Errorf("timeout: %+v", res)
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
	// Many by now, and as heavy as the first: time to join. Join writes the
	// one pack and removes nothing (a commit may run); what is committed
	// before the old ones are dropped stays.
	if !r.TimeToJoin() {
		t.Fatalf("%d packs of a size are not to be joined", len(pack()))
	}
	many := pack()
	old, res := r.Join(ctx, time.Minute)
	if res.Code != 0 || len(old) != len(many) {
		t.Fatalf("join: %+v, %d packs to drop of %d", res, len(old), len(many))
	}
	if got := pack(); len(got) != len(many)+1 {
		t.Errorf("after the join, before the drop: %d packs, were %d", len(got), len(many))
	}
	commitFiles(t, r, Call{}, 1000+commits, 1)
	commits++
	whole("joined, not dropped", commits)
	r.Drop(old)
	if res := r.Prune(ctx, time.Minute); res.Code != 0 {
		t.Fatalf("prune: %+v", res)
	}
	if r.TimeToJoin() {
		t.Error("still to be joined after Join")
	}
	if l, p := objects(t, r); l != 3 || p != 1 {
		t.Errorf("after many packs: %d loose objects (want the 3 of the commit beside the join), %d packs", l, p)
	}
	if left, _ := filepath.Glob(filepath.Join(r.Dir, ".git", "objects", "pack", "*")); len(left) > 3 {
		t.Errorf("files of dropped packs are left: %v", left)
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

// The packs are joined by their weight, not by their number alone: beside
// a large pack the small ones wait until they are many (packsMost). A pack
// kept by hand stays, and what git left half written long ago goes.
func TestTimeToJoin(t *testing.T) {
	r := repo(t)
	ctx := context.Background()
	packs := func() []string {
		l, _ := filepath.Glob(filepath.Join(r.Dir, ".git", "objects", "pack", "*.pack"))
		return l
	}
	step := func(i int) {
		t.Helper()
		commitFiles(t, r, Call{}, i, 1)
		if res := r.Pack(ctx, time.Minute); res.Code != 0 {
			t.Fatalf("pack: %+v", res)
		}
		if res := r.Prune(ctx, time.Minute); res.Code != 0 {
			t.Fatalf("prune: %+v", res)
		}
	}
	// A large pack: a file nothing compresses.
	big := make([]byte, 300_000)
	_, _ = rand.Read(big)
	if err := os.WriteFile(filepath.Join(r.Dir, "big.bin"), big, 0o666); err != nil {
		t.Fatal(err)
	}
	step(0)
	kept := packs()
	for i := 1; len(packs()) <= packsMany+2; i++ {
		step(i)
		if r.TimeToJoin() {
			t.Fatalf("%d packs, the small ones a fraction of the large: not to be joined", len(packs()))
		}
	}
	for i := 100; len(packs()) <= packsMost; i++ {
		if r.TimeToJoin() {
			t.Fatalf("%d packs are not yet too many", len(packs()))
		}
		step(i)
	}
	if !r.TimeToJoin() {
		t.Fatalf("%d packs are too many", len(packs()))
	}
	// A pack kept by hand is none of those to drop, and git leaves its
	// objects out of the new one.
	if err := os.WriteFile(strings.TrimSuffix(kept[0], ".pack")+".keep", nil, 0o666); err != nil {
		t.Fatal(err)
	}
	stale := filepath.Join(r.Dir, ".git", "objects", "pack", "tmp_pack_left")
	fresh := filepath.Join(r.Dir, ".git", "objects", "pack", ".tmp-1-pack-being-written")
	for _, f := range []string{stale, fresh} {
		if err := os.WriteFile(f, []byte("x"), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	long := time.Now().Add(-2 * tempsStale)
	if err := os.Chtimes(stale, long, long); err != nil {
		t.Fatal(err)
	}
	before := len(packs())
	old, res := r.Join(ctx, time.Minute)
	if res.Code != 0 || len(old) != before-1 || slicesContain(old, kept[0]) {
		t.Fatalf("join beside a kept pack: %+v, %d to drop of %d, the kept one among them: %v", res, len(old), before, slicesContain(old, kept[0]))
	}
	r.Drop(old)
	if got := packs(); len(got) != 2 || !slicesContain(got, kept[0]) {
		t.Errorf("after the drop: %d packs, the kept one there: %v", len(got), slicesContain(got, kept[0]))
	}
	if _, err := os.Stat(stale); err == nil {
		t.Error("a pack left half written long ago is still there")
	}
	if _, err := os.Stat(fresh); err != nil {
		t.Error("a pack being written was removed")
	}
	if res := r.Run(ctx, Call{Timeout: time.Minute}, "fsck", "--full", "--strict"); res.Code != 0 {
		t.Errorf("fsck: %s%s", res.Stderr, res.Stdout)
	}
	if got := must(t, r, Call{}, "show", "HEAD:f1.txt"); got != "file 1\n" {
		t.Errorf("a file of an early commit: %q", got)
	}
	// Nothing new: git writes no pack that wasn't there, and nothing is to drop.
	if old, res := r.Join(ctx, time.Minute); res.Code != 0 || len(old) != 0 {
		t.Errorf("a join of nothing new: %+v, %d to drop", res, len(old))
	}
	if got := packs(); len(got) != 2 {
		t.Errorf("after a join of nothing new: %d packs", len(got))
	}
	// Its time limit holds, and nothing is dropped then.
	if old, res := r.Join(ctx, time.Nanosecond); res.Code != TimedOut || len(old) != 0 {
		t.Errorf("a join out of time: %+v, %d to drop", res, len(old))
	}
}
