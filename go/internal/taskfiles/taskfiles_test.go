package taskfiles

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
)

// A store on a temp folder: users alice and bob, the clock at 2026-06-01,
// and its commits recorded.
type fixture struct {
	*Store
	root    string
	commits []string // "user|message|file file"
	viewer  string   // who may not write
}

func newStore(t *testing.T, users ...string) *fixture {
	t.Helper()
	f := &fixture{root: t.TempDir()}
	f.Store = &Store{
		Root:       func() string { return f.root },
		PrivateDir: "private",
		Users:      func() []string { return users },
		Private:    func(user string) bool { return slices.Contains(users, user) },
		MayWrite: func(user string) error {
			if user != "" && user == f.viewer {
				return errors.New("read only")
			}
			return nil
		},
		Commit: func(user, message string, paths ...string) error {
			var rel []string
			for _, p := range paths {
				r, _ := filepath.Rel(f.root, p)
				rel = append(rel, filepath.ToSlash(r))
			}
			f.commits = append(f.commits, user+"|"+message+"|"+strings.Join(rel, " "))
			return nil
		},
		Now: func() time.Time { return time.Date(2026, 6, 1, 12, 0, 0, 0, time.UTC) },
	}
	return f
}

func task(id, owner, completedAt string) *Task {
	return &Task{ID: id, Title: "Task " + id, Owner: owner, CompletedAt: completedAt}
}

func ids(tasks []*Task) []string {
	out := []string{}
	for _, t := range tasks {
		out = append(out, t.ID)
	}
	return out
}

func (f *fixture) text(t *testing.T, rel string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(f.root, rel))
	if err != nil {
		return ""
	}
	return string(b)
}

// A write files each task by its owner and, completed, by its year; a read
// gives a user the shared tasks and their own, marked as whose they are.
func TestWriteFilesByOwnerAndYear(t *testing.T) {
	f := newStore(t, "alice", "bob")
	f.Lock()
	active := []*Task{task("s1", "", ""), task("a1", "alice", "")}
	completed := []*Task{task("s2", "", "2026-03-01T10:00:00"), task("s3", "", "2024-05-01T10:00:00"), task("a2", "alice", "2025-01-02T10:00:00")}
	err := f.WriteBothLocked("alice", active, completed, "Write")
	f.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	for rel, want := range map[string]string{"_tasks/tasks.json": "s1", "private/alice/_tasks/tasks.json": "a1", "_tasks/completed.json": "s2",
		"_tasks/completed-2024.json": "s3", "private/alice/_tasks/completed-2025.json": "a2"} {
		if got := f.text(t, rel); !strings.Contains(got, `"id": "`+want+`"`) || strings.Count(got, `"id"`) != 1 {
			t.Errorf("%s: %q, want only %s", rel, got, want)
		}
	}
	want := "alice|Write|_tasks/tasks.json private/alice/_tasks/tasks.json _tasks/completed.json _tasks/completed-2024.json private/alice/_tasks/completed-2025.json"
	if len(f.commits) != 1 || f.commits[0] != want {
		t.Errorf("commits %q,\nwant one: %q", f.commits, want)
	}
	if got := ids(f.Open("alice")); !reflect.DeepEqual(got, []string{"s1", "a1"}) {
		t.Errorf("alice's open tasks: %v", got)
	}
	if got := f.Open("alice"); got[0].Owner != "" || got[1].Owner != "alice" {
		t.Errorf("owners: %q %q", got[0].Owner, got[1].Owner)
	}
	if got := ids(f.Open("bob")); !reflect.DeepEqual(got, []string{"s1"}) {
		t.Errorf("bob sees alice's: %v", got)
	}
	if got := ids(f.All("alice")); !reflect.DeepEqual(got, []string{"s1", "a1", "s2", "s3", "a2"}) {
		t.Errorf("all of alice's: %v", got)
	}
	if got := ids(f.Every()); len(got) != 5 {
		t.Errorf("every owner's: %v", got)
	}
	open, done := f.Snapshot("bob")
	if !reflect.DeepEqual(ids(open), []string{"s1"}) || !reflect.DeepEqual(ids(done), []string{"s2", "s3"}) {
		t.Errorf("bob's snapshot: %v %v", ids(open), ids(done))
	}
	// Written again as it is: no file changes, nothing to commit.
	f.Lock()
	active, completed, err = f.ReadForWriteLocked("alice")
	if err == nil {
		err = f.WriteBothLocked("alice", active, completed, "Again")
	}
	f.Unlock()
	if err != nil || len(f.commits) != 1 {
		t.Errorf("an unchanged write: %v, commits %q", err, f.commits)
	}
}

// The Completed view of a year, and the years there are.
func TestCompletedByYear(t *testing.T) {
	f := newStore(t, "alice")
	f.Lock()
	_ = f.WriteBothLocked("alice", nil, []*Task{task("now", "", "2026-03-01T10:00:00"), task("undated", "", "x"),
		task("old", "", "2024-05-01T10:00:00"), task("hers", "alice", "2023-01-02T10:00:00")}, "Write")
	f.Unlock()
	if got := ids(f.CompletedInYear("alice", "2026")); !reflect.DeepEqual(got, []string{"now", "undated"}) {
		t.Errorf("this year: %v", got)
	}
	if got := ids(f.CompletedInYear("alice", "2024")); !reflect.DeepEqual(got, []string{"old"}) {
		t.Errorf("2024: %v", got)
	}
	every := func(*Task) bool { return true }
	if got := f.CompletedYears("alice", every); !reflect.DeepEqual(got, []string{"2026", "2024", "2023"}) {
		t.Errorf("alice's years: %v", got)
	}
	if got := f.CompletedYears("", every); !reflect.DeepEqual(got, []string{"2026", "2024"}) {
		t.Errorf("without her own: %v", got)
	}
	if got := f.CompletedYears("alice", func(t *Task) bool { return t.ID == "hers" }); !reflect.DeepEqual(got, []string{"2026", "2023"}) {
		t.Errorf("a scope: %v", got)
	}
	if f.PastYear(task("x", "", "2025-12-31T23:59:59")) != "2025" || f.PastYear(task("x", "", "2026-01-01T00:00:00")) != "" ||
		CompletedYear(task("x", "", "20")) != "" {
		t.Error("past years")
	}
}

// A file that is there but unreadable refuses the write: nothing is
// overwritten with a list that lacks its tasks.
func TestACorruptFileRefusesTheWrite(t *testing.T) {
	f := newStore(t)
	_ = os.MkdirAll(f.Dir(""), 0o777)
	for _, text := range []string{"", "{", `{"not": "a list"}`} {
		_ = os.WriteFile(f.OpenPath(""), []byte(text), 0o666)
		f.Lock()
		_, _, err := f.ReadForWriteLocked("")
		f.Unlock()
		if !errors.Is(err, ErrCorrupt) {
			t.Errorf("%q read for a write: %v", text, err)
		}
		if _, err := f.ReadOpenStrict(""); !errors.Is(err, ErrCorrupt) {
			t.Errorf("%q read strictly: %v", text, err)
		}
		if got := f.Open(""); len(got) != 0 {
			t.Errorf("%q read for showing: %v", text, got)
		}
		if got := f.text(t, "_tasks/tasks.json"); got != text {
			t.Errorf("the file changed: %q", got)
		}
	}
	// A file that isn't there is an empty list, also for a write.
	_ = os.Remove(f.OpenPath(""))
	if got, err := f.ReadOpenStrict(""); err != nil || len(got) != 0 {
		t.Errorf("no file: %v %v", got, err)
	}
}

// Every write asks who may write, and takes only tasks of the files it
// read for that user.
func TestWhoMayWrite(t *testing.T) {
	f := newStore(t, "alice", "bob", "carol")
	f.viewer = "carol"
	f.Lock()
	defer f.Unlock()
	for name, err := range map[string]error{
		"open": f.WriteOpenLocked("carol", []*Task{task("c", "", "")}, "x"),
		"both": f.WriteBothLocked("carol", []*Task{task("c", "", "")}, nil, "x"),
	} {
		if err == nil || err.Error() != "read only" {
			t.Errorf("a viewer's write (%s): %v", name, err)
		}
	}
	if err := f.WriteOpenLocked("bob", []*Task{task("a", "alice", "")}, "x"); err == nil || !strings.Contains(err.Error(), "alice") {
		t.Errorf("alice's task in bob's write: %v", err)
	}
	if len(f.commits) != 0 || f.text(t, "_tasks/tasks.json") != "" || f.text(t, "private/alice/_tasks/tasks.json") != "" {
		t.Errorf("something was written: %q", f.commits)
	}
	// Without users no one has files of their own.
	if got := newStore(t).Owners("alice"); !reflect.DeepEqual(got, []string{""}) {
		t.Errorf("owners without users: %v", got)
	}
	if got := f.Owners("alice"); !reflect.DeepEqual(got, []string{"", "alice"}) {
		t.Errorf("alice's owners: %v", got)
	}
	if got := f.EveryOwner(); !reflect.DeepEqual(got, []string{"", "alice", "bob", "carol"}) {
		t.Errorf("every owner: %v", got)
	}
}

// At start, completed tasks of earlier years go into their archives, every
// owner's in one commit; run again, there is nothing to do.
func TestArchiveOldCompleted(t *testing.T) {
	f := newStore(t, "alice")
	for owner, id := range map[string]string{"": "s", "alice": "a"} {
		if err := Write(f.CompletedPath(owner), []*Task{task(id+"-old", owner, "2025-02-03T10:00:00"), task(id+"-new", owner, "2026-02-03T10:00:00")}); err != nil {
			t.Fatal(err)
		}
	}
	f.ArchiveOldCompleted()
	for rel, want := range map[string]string{"_tasks/completed.json": "s-new", "_tasks/completed-2025.json": "s-old",
		"private/alice/_tasks/completed.json": "a-new", "private/alice/_tasks/completed-2025.json": "a-old"} {
		if got := f.text(t, rel); !strings.Contains(got, want) || strings.Count(got, `"id"`) != 1 {
			t.Errorf("%s: %q", rel, got)
		}
	}
	if len(f.commits) != 1 || !strings.HasPrefix(f.commits[0], "|Archive completed tasks|") || strings.Count(f.commits[0], ".json") != 4 {
		t.Errorf("commits: %q", f.commits)
	}
	f.ArchiveOldCompleted()
	if len(f.commits) != 1 {
		t.Errorf("a second run committed: %q", f.commits)
	}
	if got := f.Files([]string{""}); len(got) != 3 || got[2].Path != f.ArchivePath("", "2025") || got[0].Owner != "" {
		t.Errorf("the shared files: %+v", got)
	}
}
