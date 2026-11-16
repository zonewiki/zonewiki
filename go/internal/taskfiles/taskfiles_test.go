package taskfiles

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/taskmodel"
)

// A store on a temp folder: users alice and bob, the clock at 2026-06-01,
// and its commits recorded.
type fixture struct {
	*Store
	root    string
	commits []string  // "user|message|file file"
	viewer  string    // who may not write
	now     time.Time // the clock
}

func newStore(t *testing.T, users ...string) *fixture {
	t.Helper()
	forgetAll()
	f := &fixture{root: t.TempDir(), now: time.Date(2026, 6, 1, 12, 0, 0, 0, time.UTC)}
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
		Now: func() time.Time { return f.now },
	}
	return f
}

func task(id, owner, closedAt string) *Task {
	return &Task{ID: id, Title: "Task " + id, Owner: owner, ClosedAt: closedAt}
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

// forgetAll: nothing is known of any file, as when Zone starts.
func forgetAll() {
	parsed.Lock()
	clear(parsed.files)
	parsed.Unlock()
}

// put writes a file as a hand on the host does, not as Zone.
func (f *fixture) put(t *testing.T, rel, text string) {
	t.Helper()
	p := filepath.Join(f.root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(text), 0o666); err != nil {
		t.Fatal(err)
	}
}

// ids: the ids of the tasks a file holds, as the file has them (parsed
// here, not by the store).
func (f *fixture) ids(t *testing.T, rel string) []string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(f.root, filepath.FromSlash(rel)))
	if err != nil {
		t.Errorf("%s: %v", rel, err)
		return nil
	}
	var l []struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(raw, &l); err != nil {
		t.Errorf("%s: %v", rel, err)
	}
	out := []string{}
	for _, x := range l {
		out = append(out, x.ID)
	}
	return out
}

// names: every file under the wiki's folder, sorted.
func (f *fixture) names(t *testing.T) []string {
	t.Helper()
	out := []string{}
	_ = filepath.WalkDir(f.root, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			rel, _ := filepath.Rel(f.root, p)
			out = append(out, filepath.ToSlash(rel))
		}
		return nil
	})
	slices.Sort(out)
	return out
}

// write writes the open and the closed tasks user sees, as a write does.
func (f *fixture) write(t *testing.T, user string, active, closed []*Task, message string) {
	t.Helper()
	f.Lock()
	defer f.Unlock()
	if err := f.WriteBothLocked(user, active, closed, message); err != nil {
		t.Fatal(err)
	}
}

// rewrite: a write as the server does one: the tasks user sees read for it,
// changed by change, and written, under the lock.
func (f *fixture) rewrite(t *testing.T, user, message string, change func(active, closed []*Task) ([]*Task, []*Task)) {
	t.Helper()
	f.Lock()
	defer f.Unlock()
	active, closed, err := f.ReadForWriteLocked(user)
	if err != nil {
		t.Fatal(err)
	}
	active, closed = change(active, closed)
	if err := f.WriteBothLocked(user, active, closed, message); err != nil {
		t.Fatal(err)
	}
}

// A write files each task by its owner and, closed, by its month: this
// month's in closed.json, an earlier month's (of any year) in
// closed-YYYY-MM.json; a read gives a user the shared tasks and their own,
// marked as whose they are.
func TestWriteFilesByOwnerAndMonth(t *testing.T) {
	f := newStore(t, "alice", "bob")
	f.Lock()
	active := []*Task{task("s1", "", ""), task("a1", "alice", "")}
	closed := []*Task{task("s2", "", "2026-03-01T10:00:00"), task("s3", "", "2024-05-01T10:00:00"), task("a2", "alice", "2025-01-02T10:00:00"),
		task("s4", "", "2026-06-01T09:00:00")}
	err := f.WriteBothLocked("alice", active, closed, "Write")
	f.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	for rel, want := range map[string]string{"_tasks/tasks.json": "s1", "private/alice/_tasks/tasks.json": "a1", "_tasks/closed.json": "s4",
		"_tasks/closed-2026-03.json": "s2", "_tasks/closed-2024-05.json": "s3", "private/alice/_tasks/closed-2025-01.json": "a2"} {
		if got := f.text(t, rel); !strings.Contains(got, `"id": "`+want+`"`) || strings.Count(got, `"id"`) != 1 {
			t.Errorf("%s: %q, want only %s", rel, got, want)
		}
	}
	// No file for no tasks: alice closed nothing this month, nobody has a year's file.
	if got := f.names(t); !reflect.DeepEqual(got, []string{"_tasks/closed-2024-05.json", "_tasks/closed-2026-03.json", "_tasks/closed.json", "_tasks/tasks.json",
		"private/alice/_tasks/closed-2025-01.json", "private/alice/_tasks/tasks.json"}) {
		t.Errorf("the files: %v", got)
	}
	want := "alice|Write|_tasks/tasks.json private/alice/_tasks/tasks.json _tasks/closed-2026-03.json _tasks/closed-2024-05.json _tasks/closed.json private/alice/_tasks/closed-2025-01.json"
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
	if got := ids(f.All("alice")); !reflect.DeepEqual(got, []string{"s1", "a1", "s4", "s3", "s2", "a2"}) {
		t.Errorf("all of alice's: %v", got)
	}
	for _, x := range f.All("alice") {
		if want := map[bool]string{true: "alice", false: ""}[strings.HasPrefix(x.ID, "a")]; x.Owner != want {
			t.Errorf("%s is %q's, want %q's", x.ID, x.Owner, want)
		}
	}
	if got := ids(f.Every()); len(got) != 6 {
		t.Errorf("every owner's: %v", got)
	}
	open, done := f.Snapshot("bob")
	if !reflect.DeepEqual(ids(open), []string{"s1"}) || !reflect.DeepEqual(ids(done), []string{"s4", "s3", "s2"}) {
		t.Errorf("bob's snapshot: %v %v", ids(open), ids(done))
	}
	// Written again as it is: no file changes, nothing to commit.
	f.Lock()
	active, closed, err = f.ReadForWriteLocked("alice")
	if err == nil {
		err = f.WriteBothLocked("alice", active, closed, "Again")
	}
	f.Unlock()
	if err != nil || len(f.commits) != 1 {
		t.Errorf("an unchanged write: %v, commits %q", err, f.commits)
	}
}

// The Closed view of a year, and the years there are: over the months'
// files, a year's file from before the months, both, and what is still in
// closed.json.
func TestClosedByYear(t *testing.T) {
	f := newStore(t, "alice")
	f.Lock()
	_ = f.WriteBothLocked("alice", nil, []*Task{task("now", "", "2026-06-01T10:00:00"), task("spring", "", "2026-03-01T10:00:00"), task("undated", "", "x"),
		task("old", "", "2024-05-01T10:00:00"), task("older", "", "2024-01-01T10:00:00"), task("hers", "alice", "2023-01-02T10:00:00")}, "Write")
	f.Unlock()
	// A year's file from before the months, beside a month's file of that
	// year, and another year only in such a file; not Zone's writes.
	f.put(t, "_tasks/closed-2024.json", `[{"id": "yearfile", "title": "T", "closed_at": "2024-07-01T10:00:00"}]`)
	f.put(t, "_tasks/closed-2022.json", `[{"id": "y22", "title": "T", "closed_at": "2022-07-01T10:00:00"}]`)
	f.put(t, "private/alice/_tasks/closed-2021.json", `[{"id": "h21", "title": "T", "closed_at": "2021-07-01T10:00:00"}]`)
	for year, want := range map[string][]string{"2026": {"now", "undated", "spring"}, "2024": {"older", "old", "yearfile"}, "2023": {"hers"},
		"2022": {"y22"}, "2021": {"h21"}, "2025": {}, "2027": {}} {
		if got := ids(f.ClosedInYear("alice", year)); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: %v, want %v", year, got, want)
		}
	}
	if got := ids(f.ClosedInYear("", "2023")); len(got) != 0 {
		t.Errorf("alice's 2023 for someone else: %v", got)
	}
	if got := ids(f.ClosedInYear("", "2021")); len(got) != 0 {
		t.Errorf("alice's 2021 for someone else: %v", got)
	}
	for _, x := range f.ClosedInYear("alice", "2021") {
		if x.Owner != "alice" {
			t.Errorf("%s: %q's", x.ID, x.Owner)
		}
	}
	every := func(*Task) bool { return true }
	if got := f.ClosedYears("alice", every); !reflect.DeepEqual(got, []string{"2026", "2024", "2023", "2022", "2021"}) {
		t.Errorf("alice's years: %v", got)
	}
	if got := f.ClosedYears("", every); !reflect.DeepEqual(got, []string{"2026", "2024", "2022"}) {
		t.Errorf("without her own: %v", got)
	}
	if got := f.ClosedYears("alice", func(t *Task) bool { return t.ID == "hers" }); !reflect.DeepEqual(got, []string{"2026", "2023"}) {
		t.Errorf("a scope: %v", got)
	}
	if got := f.ClosedYears("alice", func(t *Task) bool { return t.ID == "yearfile" }); !reflect.DeepEqual(got, []string{"2026", "2024"}) {
		t.Errorf("a scope with a task in a year's file: %v", got)
	}
	// A scope asks whose a task is (a private list's): the archives' tasks
	// say it, those Zone wrote (2023) and those it only read (2021).
	if got := f.ClosedYears("alice", func(t *Task) bool { return t.Owner == "alice" }); !reflect.DeepEqual(got, []string{"2026", "2023", "2021"}) {
		t.Errorf("her own tasks' years: %v", got)
	}
	if got := f.ClosedYears("alice", func(t *Task) bool { return t.Owner == "" }); !reflect.DeepEqual(got, []string{"2026", "2024", "2022"}) {
		t.Errorf("the shared tasks' years: %v", got)
	}
	if f.PastYear(task("x", "", "2025-12-31T23:59:59")) != "2025" || f.PastYear(task("x", "", "2026-01-01T00:00:00")) != "" ||
		ClosedYear(task("x", "", "20")) != "" {
		t.Error("past years")
	}
	for at, want := range map[string]string{"2026-05-31T23:59:59": "2026-05", "2026-06-01T00:00:00": "", "2026-07-01T00:00:00": "",
		"2025-12-31T23:59:59": "2025-12", "2025-06-01": "2025-06", "": "", "x": "", "2025": "", "2025-1-02T10:00:00": "", "2025/01/02": ""} {
		if got := pastMonthOf(task("x", "", at), f.currentMonth()); got != want {
			t.Errorf("the month of %q: %q, want %q", at, got, want)
		}
	}
}

// A month ends, and a year: what closed.json holds stays there, and in its
// year's Closed view, until the next write (or start), which files it under
// its month; a task closed then is this month's.
func TestAMonthAndAYearEnd(t *testing.T) {
	f := newStore(t)
	f.now = time.Date(2026, 12, 30, 12, 0, 0, 0, time.UTC)
	f.write(t, "", []*Task{task("open", "", "")}, []*Task{task("nov", "", "2026-11-30T23:59:59"), task("dec", "", "2026-12-01T00:00:00")}, "Write")
	if got := f.names(t); !reflect.DeepEqual(got, []string{"_tasks/closed-2026-11.json", "_tasks/closed.json", "_tasks/tasks.json"}) {
		t.Fatalf("in december: %v", got)
	}
	// The year turns; nothing is written.
	f.now = time.Date(2027, 1, 1, 0, 0, 1, 0, time.UTC)
	if got := ids(f.ClosedInYear("", "2027")); len(got) != 0 {
		t.Errorf("2027, before anything was closed in it: %v", got)
	}
	if got := ids(f.ClosedInYear("", "2026")); !reflect.DeepEqual(got, []string{"dec", "nov"}) {
		t.Errorf("2026, seen from 2027: %v", got)
	}
	if got := f.ClosedYears("", func(*Task) bool { return true }); !reflect.DeepEqual(got, []string{"2027", "2026"}) {
		t.Errorf("the years: %v", got)
	}
	if f.PastYear(task("x", "", "2026-12-01T00:00:00")) != "2026" {
		t.Error("december isn't last year")
	}
	// A task is closed in January: one write, which also files December.
	f.commits = nil
	f.rewrite(t, "", "Close", func(active, closed []*Task) ([]*Task, []*Task) {
		active[0].ClosedAt = "2027-01-01T00:00:01"
		return nil, append(closed, active[0])
	})
	for rel, want := range map[string]string{"_tasks/closed.json": "open", "_tasks/closed-2026-12.json": "dec", "_tasks/closed-2026-11.json": "nov"} {
		if got := f.text(t, rel); !strings.Contains(got, `"id": "`+want+`"`) || strings.Count(got, `"id"`) != 1 {
			t.Errorf("%s: %q, want only %s", rel, got, want)
		}
	}
	if want := []string{"|Close|_tasks/tasks.json _tasks/closed-2026-12.json _tasks/closed.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	if got := ids(f.ClosedInYear("", "2027")); !reflect.DeepEqual(got, []string{"open"}) {
		t.Errorf("2027: %v", got)
	}
	if got := ids(f.ClosedInYear("", "2026")); !reflect.DeepEqual(got, []string{"nov", "dec"}) {
		t.Errorf("2026: %v", got)
	}
	// February: January's task is in closed.json until Zone starts.
	f.now = time.Date(2027, 2, 1, 0, 0, 0, 0, time.UTC)
	f.commits = nil
	f.ArchiveOldClosed()
	if want := []string{"|Archive closed tasks|_tasks/closed-2027-01.json _tasks/closed.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("a start in february: %q, want %q", f.commits, want)
	}
	if f.text(t, "_tasks/closed.json") != "[]\n" || !strings.Contains(f.text(t, "_tasks/closed-2027-01.json"), `"id": "open"`) {
		t.Errorf("after it: %q %q", f.text(t, "_tasks/closed.json"), f.text(t, "_tasks/closed-2027-01.json"))
	}
	if got := ids(f.ClosedInYear("", "2027")); !reflect.DeepEqual(got, []string{"open"}) {
		t.Errorf("2027, from february: %v", got)
	}
	// The clock set back (a host's wrong date put right): January is this
	// month again, its file goes; and a task closed "next month" is this
	// month's, in closed.json.
	f.now = time.Date(2027, 1, 15, 0, 0, 0, 0, time.UTC)
	f.rewrite(t, "", "Back", func(active, closed []*Task) ([]*Task, []*Task) {
		return active, append(closed, task("ahead", "", "2027-03-01T10:00:00"))
	})
	if got := f.ids(t, "_tasks/closed.json"); !reflect.DeepEqual(got, []string{"open", "ahead"}) {
		t.Errorf("closed.json with the clock set back: %v", got)
	}
	if got := f.names(t); !reflect.DeepEqual(got, []string{"_tasks/closed-2026-11.json", "_tasks/closed-2026-12.json", "_tasks/closed.json", "_tasks/tasks.json"}) {
		t.Errorf("the files with the clock set back: %v", got)
	}
	if got := ids(f.ClosedInYear("", "2027")); !reflect.DeepEqual(got, []string{"open", "ahead"}) {
		t.Errorf("2027 with it: %v", got)
	}
}

// A year's file from before the months (closed-2025.json): the start files
// its tasks under their months and removes it, every owner's in one commit
// that has the removal; a second start changes nothing.
func TestAYearsFileIsTakenOverAtStart(t *testing.T) {
	f := newStore(t, "alice")
	f.put(t, "_tasks/tasks.json", "[]\n")
	for owner, id := range map[string]string{"": "s", "alice": "a"} {
		if err := Write(f.ArchivePath(owner, "2025"), []*Task{task(id+"-feb", owner, "2025-02-03T10:00:00"), task(id+"-feb2", owner, "2025-02-20T10:00:00"),
			task(id+"-dec", owner, "2025-12-31T23:59:59")}); err != nil {
			t.Fatal(err)
		}
		if err := Write(f.ClosedPath(owner), []*Task{task(id+"-may", owner, "2026-05-03T10:00:00"), task(id+"-new", owner, "2026-06-01T10:00:00")}); err != nil {
			t.Fatal(err)
		}
	}
	before := ids(f.All("alice"))
	f.ArchiveOldClosed()
	for _, dir := range []string{"_tasks/", "private/alice/_tasks/"} {
		id := map[string]string{"_tasks/": "s", "private/alice/_tasks/": "a"}[dir]
		for name, want := range map[string][]string{"closed.json": {id + "-new"}, "closed-2025-02.json": {id + "-feb", id + "-feb2"},
			"closed-2025-12.json": {id + "-dec"}, "closed-2026-05.json": {id + "-may"}} {
			if got := f.ids(t, dir+name); !reflect.DeepEqual(got, want) {
				t.Errorf("%s: %v, want %v", dir+name, got, want)
			}
		}
		if _, err := os.Stat(filepath.Join(f.root, dir+"closed-2025.json")); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("%sclosed-2025.json is still there (%v)", dir, err)
		}
	}
	// The files that take tasks first, those left without any after them.
	want := "|Archive closed tasks|_tasks/closed-2025-02.json _tasks/closed-2025-12.json _tasks/closed-2026-05.json _tasks/closed.json _tasks/closed-2025.json" +
		" private/alice/_tasks/closed-2025-02.json private/alice/_tasks/closed-2025-12.json private/alice/_tasks/closed-2026-05.json private/alice/_tasks/closed.json private/alice/_tasks/closed-2025.json"
	if len(f.commits) != 1 || f.commits[0] != want {
		t.Errorf("commits %q,\nwant one: %q", f.commits, want)
	}
	after := ids(f.All("alice"))
	slices.Sort(before)
	slices.Sort(after)
	if !reflect.DeepEqual(before, after) || len(after) != 10 {
		t.Errorf("tasks before %v, after %v", before, after)
	}
	if got := ids(f.ClosedInYear("alice", "2025")); !reflect.DeepEqual(got, []string{"s-feb", "s-feb2", "s-dec", "a-feb", "a-feb2", "a-dec"}) {
		t.Errorf("2025: %v", got)
	}
	files := f.names(t)
	f.ArchiveOldClosed()
	if len(f.commits) != 1 || !reflect.DeepEqual(f.names(t), files) {
		t.Errorf("a second start: commits %q, files %v", f.commits, f.names(t))
	}
	if got := f.Files([]string{""}); len(got) != 5 || got[2].Path != f.ArchivePath("", "2025-02") || got[0].Owner != "" {
		t.Errorf("the shared files: %+v", got)
	}
}

// Without a start: the first write by a user files their year's files (the
// shared one and their own) under the months and removes them, in that
// write's commit; another user's is left for theirs. A year's file with a
// month's file of that year beside it goes into it.
func TestAYearsFileIsTakenOverByAWrite(t *testing.T) {
	f := newStore(t, "alice", "bob")
	f.put(t, "_tasks/tasks.json", `[{"id": "open", "title": "Open"}]`)
	f.put(t, "_tasks/closed-2025.json", `[{"id": "s-feb", "title": "T", "closed_at": "2025-02-03T10:00:00"}, {"id": "s-mar", "title": "T", "closed_at": "2025-03-03T10:00:00"}]`)
	f.put(t, "_tasks/closed-2025-03.json", `[{"id": "s-mar0", "title": "T", "closed_at": "2025-03-01T10:00:00"}]`)
	f.put(t, "private/alice/_tasks/closed-2024.json", `[{"id": "a-old", "title": "T", "closed_at": "2024-02-03T10:00:00"}]`)
	f.put(t, "private/bob/_tasks/closed-2024.json", `[{"id": "b-old", "title": "T", "closed_at": "2024-02-03T10:00:00"}]`)
	f.rewrite(t, "alice", "Retitle", func(active, closed []*Task) ([]*Task, []*Task) {
		active[0].Title = "Changed"
		return active, closed
	})
	if got := f.names(t); !reflect.DeepEqual(got, []string{"_tasks/closed-2025-02.json", "_tasks/closed-2025-03.json", "_tasks/tasks.json",
		"private/alice/_tasks/closed-2024-02.json", "private/bob/_tasks/closed-2024.json"}) {
		t.Errorf("the files: %v", got)
	}
	if got := f.ids(t, "_tasks/closed-2025-03.json"); !reflect.DeepEqual(got, []string{"s-mar0", "s-mar"}) {
		t.Errorf("march: %v", got)
	}
	want := "alice|Retitle|_tasks/tasks.json _tasks/closed-2025-02.json _tasks/closed-2025-03.json _tasks/closed-2025.json" +
		" private/alice/_tasks/closed-2024-02.json private/alice/_tasks/closed-2024.json"
	if len(f.commits) != 1 || f.commits[0] != want {
		t.Errorf("commits %q,\nwant one: %q", f.commits, want)
	}
	if got := ids(f.ClosedInYear("bob", "2024")); !reflect.DeepEqual(got, []string{"b-old"}) {
		t.Errorf("bob's 2024, still in his year's file: %v", got)
	}
	if got := ids(f.ClosedInYear("alice", "2024")); !reflect.DeepEqual(got, []string{"a-old"}) {
		t.Errorf("alice's 2024: %v", got)
	}
	// The next write has nothing to file.
	f.rewrite(t, "alice", "Again", func(active, closed []*Task) ([]*Task, []*Task) {
		active[0].Title = "Changed again"
		return active, closed
	})
	if len(f.commits) != 2 || f.commits[1] != "alice|Again|_tasks/tasks.json" {
		t.Errorf("the next write: %q", f.commits)
	}
}

// An archive whose last task is reopened (or deleted) is removed, and the
// removal is in the write's commit; closed.json stays, empty; closed again
// later, the task is this month's.
func TestAnEmptiedArchiveIsRemoved(t *testing.T) {
	f := newStore(t, "alice")
	f.write(t, "alice", nil, []*Task{task("s1", "", "2026-03-01T10:00:00"), task("s2", "", "2026-03-02T10:00:00"), task("s3", "", "2026-06-01T10:00:00"),
		task("a1", "alice", "2025-03-01T10:00:00")}, "Write")
	reopen := func(id string) {
		t.Helper()
		f.rewrite(t, "alice", "Reopen "+id, func(active, closed []*Task) ([]*Task, []*Task) {
			i := slices.IndexFunc(closed, func(x *Task) bool { return x.ID == id })
			if i < 0 {
				t.Fatalf("no closed task %s", id)
			}
			x := closed[i]
			x.ClosedAt = ""
			return append(active, x), slices.Delete(closed, i, i+1)
		})
	}
	f.commits = nil
	reopen("s1") // not the last one: the file stays
	if got := f.ids(t, "_tasks/closed-2026-03.json"); !reflect.DeepEqual(got, []string{"s2"}) {
		t.Errorf("march after one left: %v", got)
	}
	reopen("s2")
	reopen("a1")
	reopen("s3")
	if got := f.names(t); !reflect.DeepEqual(got, []string{"_tasks/closed.json", "_tasks/tasks.json", "private/alice/_tasks/tasks.json"}) {
		t.Errorf("the files: %v", got)
	}
	if f.text(t, "_tasks/closed.json") != "[]\n" {
		t.Errorf("closed.json: %q", f.text(t, "_tasks/closed.json"))
	}
	want := []string{"alice|Reopen s1|_tasks/tasks.json _tasks/closed-2026-03.json", "alice|Reopen s2|_tasks/tasks.json _tasks/closed-2026-03.json",
		"alice|Reopen a1|private/alice/_tasks/tasks.json private/alice/_tasks/closed-2025-03.json", "alice|Reopen s3|_tasks/tasks.json _tasks/closed.json"}
	if !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q,\nwant %q", f.commits, want)
	}
	if got := ids(f.Open("alice")); !reflect.DeepEqual(got, []string{"s1", "s2", "s3", "a1"}) {
		t.Errorf("open: %v", got)
	}
	if got := f.ClosedYears("alice", func(*Task) bool { return true }); !reflect.DeepEqual(got, []string{"2026"}) {
		t.Errorf("the years: %v", got)
	}
	// A removed file isn't remembered as it was: the same month again.
	f.rewrite(t, "alice", "Close", func(active, closed []*Task) ([]*Task, []*Task) {
		active[1].ClosedAt = "2026-03-02T10:00:00"
		return slices.Delete(slices.Clone(active), 1, 2), append(closed, active[1])
	})
	if got := f.ids(t, "_tasks/closed-2026-03.json"); !reflect.DeepEqual(got, []string{"s2"}) {
		t.Errorf("march again: %v", got)
	}
	if got := ids(f.ClosedInYear("alice", "2026")); !reflect.DeepEqual(got, []string{"s2"}) {
		t.Errorf("2026: %v", got)
	}
	// An empty archive from before (Zone left one when its last task went) goes at the start.
	f.put(t, "_tasks/closed-2020.json", "[]\n")
	f.put(t, "private/alice/_tasks/closed-2019-04.json", "[]")
	f.commits = nil
	f.ArchiveOldClosed()
	if got := f.names(t); slices.Contains(got, "_tasks/closed-2020.json") || slices.Contains(got, "private/alice/_tasks/closed-2019-04.json") {
		t.Errorf("empty archives after a start: %v", got)
	}
	if len(f.commits) != 1 || !strings.HasSuffix(f.commits[0], "|_tasks/closed-2020.json private/alice/_tasks/closed-2019-04.json") {
		t.Errorf("commits: %q", f.commits)
	}
}

// A write changes, and commits, only the files whose tasks it changed, and
// the others it neither reads nor encodes: a change to an open task leaves
// the closed files alone, however large they are.
func TestAWriteSkipsTheFilesItLeavesAlone(t *testing.T) {
	f := newStore(t, "alice")
	var closed []*Task
	long := strings.Repeat("A long description, as tasks have them. ", 40)
	for i := range 6000 {
		at := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC).AddDate(0, 0, i%880) // 2024-01 … 2026-05
		x := task("c"+strconv.Itoa(i), "", at.Format("2006-01-02T15:04:05"))
		x.Description, x.Tags = long, []string{"a", "b"}
		x.History = []taskmodel.HistoryEntry{{At: x.ClosedAt, Event: "done"}}
		closed = append(closed, x)
	}
	closed = append(closed, task("june", "", "2026-06-01T10:00:00"), task("hers", "alice", "2026-02-01T10:00:00"))
	f.write(t, "alice", []*Task{task("s1", "", ""), task("s2", "", ""), task("a1", "alice", "")}, closed, "Write")
	archives := append(f.Archives(""), f.ClosedPath(""), f.ArchivePath("alice", "2026-02"))
	if len(archives) != 31 {
		t.Fatalf("%d closed files", len(archives))
	}
	if info, _ := os.Stat(f.ArchivePath("", "2025-01")); info == nil || info.Size() < 300_000 {
		t.Fatalf("a month's file isn't large: %v", info)
	}
	stat := func() map[string]os.FileInfo {
		out := map[string]os.FileInfo{}
		for _, p := range archives {
			info, err := os.Stat(p)
			if err != nil {
				t.Fatal(err)
			}
			out[p] = info
		}
		return out
	}
	check := func(what string, before map[string]os.FileInfo, want ...string) {
		t.Helper()
		for p, info := range stat() {
			if !os.SameFile(info, before[p]) || !info.ModTime().Equal(before[p].ModTime()) {
				t.Errorf("%s: %s was written", what, p)
			}
		}
		if !reflect.DeepEqual(f.commits, want) {
			t.Errorf("%s: commits %q, want %q", what, f.commits, want)
		}
		f.commits = nil
	}
	// As written, and as read anew (another process's cache: none).
	for _, fresh := range []bool{false, true} {
		if fresh {
			forgetAll()
		}
		before := stat()
		f.commits = nil
		var read []*Task
		f.rewrite(t, "alice", "Retitle", func(active, closed []*Task) ([]*Task, []*Task) {
			active[1].Title += "!"
			read = closed
			return active, closed
		})
		check("a shared open task", before, "alice|Retitle|_tasks/tasks.json")
		// What the write was handed is what the files are known to hold.
		byPath := map[string][]*Task{}
		for _, x := range read {
			p := f.ClosedPath(x.Owner)
			if m := pastMonthOf(x, f.currentMonth()); m != "" {
				p = f.ArchivePath(x.Owner, m)
			}
			byPath[p] = append(byPath[p], x)
		}
		for _, p := range archives {
			if !holds(p, byPath[p]) {
				t.Errorf("%s isn't known to hold its tasks (fresh %v)", p, fresh)
			}
		}
		f.rewrite(t, "alice", "Hers", func(active, closed []*Task) ([]*Task, []*Task) {
			active[2].Title += "!"
			return active, closed
		})
		check("her open task", before, "alice|Hers|private/alice/_tasks/tasks.json")
		f.rewrite(t, "alice", "Nothing", func(active, closed []*Task) ([]*Task, []*Task) { return active, closed })
		check("nothing", before)
		f.Lock()
		active, err := f.ReadOpenStrict("alice")
		if err == nil {
			active[0].Title += "?"
			err = f.WriteOpenLocked("alice", active, "Open only")
		}
		f.Unlock()
		if err != nil {
			t.Fatal(err)
		}
		check("the open tasks alone", before, "alice|Open only|_tasks/tasks.json")
	}
	// One closed task changes: its month's file, and no other.
	before := stat()
	f.rewrite(t, "alice", "Edit", func(active, closed []*Task) ([]*Task, []*Task) {
		i := slices.IndexFunc(closed, func(x *Task) bool { return x.ID == "c400" })
		closed[i].Title = "Edited"
		return active, closed
	})
	month := f.ArchivePath("", "2025-02") // day 400 of 2024-01-01 on
	if !reflect.DeepEqual(f.commits, []string{"alice|Edit|_tasks/closed-2025-02.json"}) || !strings.Contains(f.text(t, "_tasks/closed-2025-02.json"), `"title": "Edited"`) {
		t.Errorf("a closed task's edit: %q", f.commits)
	}
	for p, info := range stat() {
		if (p == month) == os.SameFile(info, before[p]) {
			t.Errorf("%s: written %v", p, p != month)
		}
	}
}

// holds: a file is known to hold tasks only as long as it is the file that
// was read or written, and the tasks are those, in that order.
func TestHolds(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tasks.json")
	order := 1.0
	tasks := []*Task{{ID: "1", Title: "One", Tags: []string{"a"}, SortOrder: &order, Owner: "alice"}, {ID: "2", Title: "Two"}}
	if holds(path, nil) || holds(path, []*Task{}) {
		t.Error("a file that isn't there holds no tasks")
	}
	if err := Write(path, tasks); err != nil {
		t.Fatal(err)
	}
	if !holds(path, tasks) {
		t.Error("not known after a write")
	}
	// What the caller does to its tasks afterwards is not what the file holds.
	tasks[0].Tags[0] = "z"
	if holds(path, tasks) {
		t.Error("a tag changed in place")
	}
	tasks[0].Tags[0] = "a"
	*tasks[0].SortOrder = 2
	if holds(path, tasks) {
		t.Error("an order changed in place")
	}
	*tasks[0].SortOrder = 1
	read, err := ReadStrict(path)
	if err != nil || !holds(path, read) || !holds(path, tasks) {
		t.Errorf("not known after a read: %v", err)
	}
	if read[0].Owner != "" {
		t.Errorf("a read after a write says whose the task is (%q): the file doesn't", read[0].Owner)
	}
	for what, other := range map[string][]*Task{"fewer": read[:1], "more": append(slices.Clone(read), task("3", "", "")),
		"another order": {read[1], read[0]}, "none": {}} {
		if holds(path, other) {
			t.Errorf("holds %s", what)
		}
	}
	read[1].Title = "Changed"
	if holds(path, read) {
		t.Error("holds a changed task")
	}
	// Read from a file Zone didn't write (another process's cache: none).
	forgetAll()
	if holds(path, tasks) {
		t.Error("known without having been read")
	}
	if read, _ = ReadStrict(path); !holds(path, read) {
		t.Error("not known after the first read")
	}
	// Changed by hand: no longer the file that was read.
	text, _ := os.ReadFile(path)
	if err := os.WriteFile(path, []byte(strings.Replace(string(text), "Two", "Two, by hand", 1)), 0o666); err != nil {
		t.Fatal(err)
	}
	if holds(path, read) {
		t.Error("holds what a hand edit replaced")
	}
	// Unusable, empty, gone.
	for _, bad := range []string{"{", ""} {
		_ = os.WriteFile(path, []byte(bad), 0o666)
		if _, err := ReadStrict(path); !errors.Is(err, ErrCorrupt) || holds(path, []*Task{}) || holds(path, nil) {
			t.Errorf("an unusable file (%q) holds no tasks: %v", bad, err)
		}
	}
	_ = os.Remove(path)
	if ReadLenient(path); holds(path, []*Task{}) {
		t.Error("a removed file holds no tasks")
	}
}

// A file changed by hand, on the host, while Zone runs: a write that reads
// after the edit keeps it (and leaves the file as the hand wrote it, when
// its tasks don't change); an edit between a write's read and its write is
// not taken for the file Zone read: the write is not skipped, the file is
// what Zone wrote, in the commit, and the next read is of that file.
func TestAFileChangedByHand(t *testing.T) {
	f := newStore(t)
	f.write(t, "", []*Task{task("o1", "", "")}, []*Task{task("c1", "", "2026-03-01T10:00:00"), task("c2", "", "2026-03-02T10:00:00"),
		task("n1", "", "2026-06-01T10:00:00")}, "Write")
	march, open := "_tasks/closed-2026-03.json", "_tasks/tasks.json"
	edit := func(rel, from, to string) string {
		t.Helper()
		text := f.text(t, rel)
		if !strings.Contains(text, from) || len(from) == len(to) {
			t.Fatalf("%s: no %q to replace by a text of another length", rel, from)
		}
		text = strings.Replace(text, from, to, 1)
		f.put(t, rel, text)
		return text
	}
	// Before a write's read: the write has the edit, and keeps it.
	byHand := edit(march, `"title": "Task c1"`, `"title":   "Task c1, by hand"`)
	edit(open, `"title": "Task o1"`, `"title": "Task o1, by hand"`)
	f.commits = nil
	f.rewrite(t, "", "Add", func(active, closed []*Task) ([]*Task, []*Task) {
		if closed[0].Title != "Task c1, by hand" || active[0].Title != "Task o1, by hand" {
			t.Errorf("the write read %q, %q", closed[0].Title, active[0].Title)
		}
		return append(active, task("o2", "", "")), closed
	})
	if got := f.text(t, march); got != byHand {
		t.Errorf("a file whose tasks didn't change was written:\n%s", got)
	}
	if !strings.Contains(f.text(t, open), "Task o1, by hand") || !reflect.DeepEqual(f.commits, []string{"|Add|_tasks/tasks.json"}) {
		t.Errorf("the open tasks: %q, commits %q", f.text(t, open), f.commits)
	}
	// The edited closed task changes: the file is written, with the edit.
	f.rewrite(t, "", "Tag", func(active, closed []*Task) ([]*Task, []*Task) {
		closed[1].Tags = []string{"x"}
		return active, closed
	})
	if got := f.text(t, march); !strings.Contains(got, `"title": "Task c1, by hand"`) || !strings.Contains(got, `"x"`) {
		t.Errorf("after a change to the file's tasks:\n%s", got)
	}
	// Between a write's read and its write: closed and open files alike.
	f.commits = nil
	f.rewrite(t, "", "Unchanged", func(active, closed []*Task) ([]*Task, []*Task) {
		edit(march, `"title": "Task c2"`, `"title": "Task c2, edited meanwhile"`)
		edit(open, `"title": "Task o2"`, `"title": "Task o2, edited meanwhile"`)
		return active, closed
	})
	if !reflect.DeepEqual(f.commits, []string{"|Unchanged|_tasks/tasks.json _tasks/closed-2026-03.json"}) {
		t.Errorf("a write over a hand edit was skipped: %q", f.commits)
	}
	for _, rel := range []string{march, open} {
		if strings.Contains(f.text(t, rel), "edited meanwhile") {
			t.Errorf("%s: the write left the file it hadn't read:\n%s", rel, f.text(t, rel))
		}
	}
	// What Zone knows of the files is what they hold.
	forgetNothing := ids(f.All(""))
	titles := func() []string {
		var out []string
		for _, x := range f.All("") {
			out = append(out, x.Title)
		}
		return out
	}
	known := titles()
	forgetAll()
	if got := titles(); !reflect.DeepEqual(got, known) || len(forgetNothing) != 5 {
		t.Errorf("Zone took the files for %v, they hold %v", known, got)
	}
	// An edit that only reformats, between read and write: the same tasks, so
	// the closed file stays as the hand wrote it; the open one is Zone's again.
	f.commits = nil
	var compact string
	f.rewrite(t, "", "Reformat", func(active, closed []*Task) ([]*Task, []*Task) {
		compact = edit(march, `"title": "Task c2"`, `"title":      "Task c2"`)
		return active, closed
	})
	if f.text(t, march) != compact || len(f.commits) != 0 {
		t.Errorf("a reformatted closed file: commits %q", f.commits)
	}
	// A hand edit that makes a file unusable refuses the next write.
	f.put(t, march, "[{")
	f.Lock()
	_, _, err := f.ReadForWriteLocked("")
	f.Unlock()
	if !errors.Is(err, ErrCorrupt) {
		t.Errorf("an unusable archive, read for a write: %v", err)
	}
}

// At the start a file in another format (edited by hand) is written as Zone
// writes it, also one Zone has read already; a plain write leaves a closed
// file whose tasks didn't change as it is.
func TestTheStartWritesZonesFormat(t *testing.T) {
	f := newStore(t)
	f.put(t, "_tasks/tasks.json", `[{"id": "o1", "title": "Open"}]`)
	raw := `[{"id": "c1", "title": "Closed", "closed_at": "2026-03-01T10:00:00"}]`
	f.put(t, "_tasks/closed-2026-03.json", raw)
	f.rewrite(t, "", "Add", func(active, closed []*Task) ([]*Task, []*Task) { return append(active, task("o2", "", "")), closed })
	if f.text(t, "_tasks/closed-2026-03.json") != raw || !reflect.DeepEqual(f.commits, []string{"|Add|_tasks/tasks.json"}) {
		t.Errorf("a plain write: %q", f.commits)
	}
	f.commits = nil
	f.ArchiveOldClosed()
	if got := f.text(t, "_tasks/closed-2026-03.json"); got == raw || !strings.Contains(got, "\n    \"id\": \"c1\",\n") {
		t.Errorf("after the start:\n%s", got)
	}
	if !reflect.DeepEqual(f.commits, []string{"|Write the task files in Zone's format|_tasks/closed-2026-03.json"}) {
		t.Errorf("commits: %q", f.commits)
	}
	f.ArchiveOldClosed()
	if len(f.commits) != 1 {
		t.Errorf("a second start: %q", f.commits)
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

// At start, closed tasks of earlier months go into their archives, every
// owner's in one commit; run again, there is nothing to do.
func TestArchiveOldClosed(t *testing.T) {
	f := newStore(t, "alice")
	for owner, id := range map[string]string{"": "s", "alice": "a"} {
		if err := Write(f.ClosedPath(owner), []*Task{task(id+"-old", owner, "2025-02-03T10:00:00"), task(id+"-spring", owner, "2026-02-03T10:00:00"),
			task(id+"-new", owner, "2026-06-01T08:00:00"), task(id+"-undated", owner, "sometime")}); err != nil {
			t.Fatal(err)
		}
	}
	f.ArchiveOldClosed()
	for rel, want := range map[string][]string{"_tasks/closed.json": {"s-new", "s-undated"}, "_tasks/closed-2025-02.json": {"s-old"}, "_tasks/closed-2026-02.json": {"s-spring"},
		"private/alice/_tasks/closed.json": {"a-new", "a-undated"}, "private/alice/_tasks/closed-2025-02.json": {"a-old"},
		"private/alice/_tasks/closed-2026-02.json": {"a-spring"}} {
		if got := f.ids(t, rel); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: %v, want %v", rel, got, want)
		}
	}
	want := "|Archive closed tasks|_tasks/closed-2025-02.json _tasks/closed-2026-02.json _tasks/closed.json" +
		" private/alice/_tasks/closed-2025-02.json private/alice/_tasks/closed-2026-02.json private/alice/_tasks/closed.json"
	if len(f.commits) != 1 || f.commits[0] != want {
		t.Errorf("commits: %q,\nwant one: %q", f.commits, want)
	}
	f.ArchiveOldClosed()
	if len(f.commits) != 1 {
		t.Errorf("a second run committed: %q", f.commits)
	}
	if got := f.Files([]string{""}); len(got) != 4 || got[2].Path != f.ArchivePath("", "2025-02") || got[3].Path != f.ArchivePath("", "2026-02") || got[0].Owner != "" {
		t.Errorf("the shared files: %+v", got)
	}
	if got := f.Files([]string{"alice"}); len(got) != 4 || got[1].Path != f.ClosedPath("alice") || got[3].Owner != "alice" {
		t.Errorf("alice's files: %+v", got)
	}
	// Only closed-YYYY-MM.json and closed-YYYY.json are archives.
	for _, name := range []string{"closed-backup.json", "closed-2025-2.json", "closed-2025-02-01.json", "closed-25.json", "xclosed-2025.json", "closed-2025.json.bak"} {
		f.put(t, "_tasks/"+name, `[{"id": "stray", "title": "T", "closed_at": "2001-01-01T10:00:00"}]`)
	}
	f.ArchiveOldClosed()
	if len(f.commits) != 1 || len(f.Archives("")) != 2 || slices.Contains(ids(f.All("")), "stray") {
		t.Errorf("other files taken for archives: %q %v", f.commits, f.Archives(""))
	}
}

// A wiki from before the files and the field were renamed (completed.json,
// completed-YYYY.json, completed_at): at start they get their new names and
// are written with closed_at, every owner's in one commit, and nothing is
// lost. A file is never renamed over one that is there.
func TestOldCompletedFilesAreRenamed(t *testing.T) {
	f := newStore(t, "alice")
	old := func(id, at string) string {
		return `{"id": "` + id + `", "title": "Task ` + id + `", "completed_at": ` + at + `, "wont_do": false}`
	}
	write := func(rel, text string) {
		t.Helper()
		p := filepath.Join(f.root, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("_tasks/tasks.json", "["+old("open", "null")+"]")
	write("_tasks/completed.json", "["+old("new", `"2026-02-03T10:00:00"`)+", "+old("last", `"2025-12-30T10:00:00"`)+", "+old("now", `"2026-06-01T10:00:00"`)+"]")
	write("_tasks/completed-2024.json", "["+old("old", `"2024-05-06T10:00:00"`)+"]")
	write("private/alice/_tasks/completed.json", "["+old("hers", `"2026-03-04T10:00:00"`)+"]")
	f.ArchiveOldClosed()
	for rel, want := range map[string]string{"_tasks/closed.json": `"closed_at": "2026-06-01T10:00:00"`, "_tasks/closed-2026-02.json": `"closed_at": "2026-02-03T10:00:00"`,
		"_tasks/closed-2025-12.json": `"closed_at": "2025-12-30T10:00:00"`, "_tasks/closed-2024-05.json": `"closed_at": "2024-05-06T10:00:00"`,
		"private/alice/_tasks/closed-2026-03.json": `"closed_at": "2026-03-04T10:00:00"`, "_tasks/tasks.json": `"closed_at": null`} {
		if got := f.text(t, rel); !strings.Contains(got, want) || strings.Count(got, `"id"`) != 1 || strings.Contains(got, "completed_at") {
			t.Errorf("%s: %q", rel, got)
		}
	}
	// The old names are gone, and the year's file a rename made (its tasks
	// are in their month's); her closed.json, without a task of this month, is empty.
	for _, rel := range []string{"_tasks/completed.json", "_tasks/completed-2024.json", "private/alice/_tasks/completed.json", "_tasks/closed-2024.json"} {
		if _, err := os.Stat(filepath.Join(f.root, rel)); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("%s is still there", rel)
		}
	}
	if got := f.text(t, "private/alice/_tasks/closed.json"); got != "[]\n" {
		t.Errorf("alice's closed.json: %q", got)
	}
	if len(f.commits) != 1 || !strings.HasPrefix(f.commits[0], "|Name the task files of closed tasks closed|") {
		t.Fatalf("commits: %q", f.commits)
	}
	for _, rel := range []string{"_tasks/completed.json", "_tasks/closed.json", "_tasks/completed-2024.json", "_tasks/closed-2024.json", "_tasks/closed-2024-05.json",
		"_tasks/closed-2025-12.json", "_tasks/closed-2026-02.json", "_tasks/tasks.json", "private/alice/_tasks/completed.json", "private/alice/_tasks/closed.json",
		"private/alice/_tasks/closed-2026-03.json"} {
		if !strings.Contains(f.commits[0]+" ", " "+rel+" ") && !strings.Contains(f.commits[0], "|"+rel+" ") {
			t.Errorf("%s isn't in the commit: %q", rel, f.commits[0])
		}
	}
	if got := ids(f.All("alice")); len(got) != 6 {
		t.Errorf("tasks afterwards: %v", got)
	}
	f.ArchiveOldClosed()
	if len(f.commits) != 1 {
		t.Errorf("a second run committed: %q", f.commits)
	}
	// Both names there: the new one is the wiki's, the old one is left alone.
	write("_tasks/completed.json", "["+old("stray", `"2026-01-01T10:00:00"`)+"]")
	f.ArchiveOldClosed()
	if len(f.commits) != 1 || !strings.Contains(f.text(t, "_tasks/completed.json"), "stray") || strings.Contains(f.text(t, "_tasks/closed.json"), "stray") {
		t.Errorf("a file was renamed over another: %q", f.commits)
	}
}

// A file is parsed once and handed out as copies: what a reader does to its
// tasks shows in no later read, and a file that changed (written by Zone,
// or by hand) is read again.
func TestReadsAreCopiesOfAFileParsedOnce(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tasks.json")
	order := 1.0
	if err := Write(path, []*Task{{ID: "1", Title: "One", Tags: []string{"a"}, SortOrder: &order}}); err != nil {
		t.Fatal(err)
	}
	first := ReadLenient(path)
	first[0].Title, first[0].Tags[0] = "Changed", "z"
	*first[0].SortOrder = 9
	second, err := ReadStrict(path)
	if err != nil || len(second) != 1 || second[0].Title != "One" || second[0].Tags[0] != "a" || *second[0].SortOrder != 1 {
		t.Errorf("a read changed the next one: %+v, %v", second[0], err)
	}
	// Written by Zone: the next read has it.
	if err := Write(path, []*Task{{ID: "1", Title: "Two"}}); err != nil {
		t.Fatal(err)
	}
	if got := ReadLenient(path); len(got) != 1 || got[0].Title != "Two" {
		t.Errorf("after a write: %+v", got)
	}
	// Changed by hand (another size): read again; and unusable then.
	text, _ := os.ReadFile(path)
	if err := os.WriteFile(path, []byte(strings.Replace(string(text), "Two", "Three, by hand", 1)), 0o666); err != nil {
		t.Fatal(err)
	}
	if got := ReadLenient(path); len(got) != 1 || got[0].Title != "Three, by hand" {
		t.Errorf("after a hand edit: %+v", got)
	}
	if err := os.WriteFile(path, []byte("not json at all"), 0o666); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadStrict(path); !errors.Is(err, ErrCorrupt) {
		t.Errorf("an unusable file: %v", err)
	}
	if got := ReadLenient(path); len(got) != 0 {
		t.Errorf("an unusable file, read leniently: %+v", got)
	}
	// Gone: no tasks, and no error.
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if got, err := ReadStrict(path); err != nil || len(got) != 0 {
		t.Errorf("a missing file: %+v, %v", got, err)
	}
}

// in: a task of owner's in a list.
func in(list string, t *Task) *Task {
	t.List = list
	return t
}

// Each list's tasks are in a folder of its own, lists/<name>/, the same
// three kinds of file; the tasks in no list stay in _tasks/ itself. A write
// touches the files of the lists it changes and no other; a task that moves
// to another list moves between two files in one commit; a list's file left
// without a task goes, and its folder with it.
func TestAListsTasksAreInItsFolder(t *testing.T) {
	f := newStore(t, "alice")
	f.write(t, "alice", []*Task{task("i1", "", ""), in("shop", task("s1", "", "")), in("shop", task("s2", "", "")),
		in("team", task("t1", "", "")), in("diary", task("d1", "alice", ""))},
		[]*Task{in("shop", task("s3", "", "2026-06-01T09:00:00")), in("shop", task("s4", "", "2025-02-01T09:00:00"))}, "Write")
	for rel, want := range map[string][]string{"_tasks/tasks.json": {"i1"}, "_tasks/lists/shop/tasks.json": {"s1", "s2"},
		"_tasks/lists/team/tasks.json": {"t1"}, "_tasks/lists/shop/closed.json": {"s3"}, "_tasks/lists/shop/closed-2025-02.json": {"s4"},
		"private/alice/_tasks/lists/diary/tasks.json": {"d1"}} {
		if got := f.ids(t, rel); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: %v, want %v", rel, got, want)
		}
	}
	if got := ids(f.Open("alice")); len(got) != 5 {
		t.Errorf("alice's open tasks: %v", got)
	}
	if _, closed := f.Snapshot("alice"); len(closed) != 2 {
		t.Errorf("the closed tasks: %v", ids(closed))
	}
	// One task of one list changes: one file.
	f.commits = nil
	f.rewrite(t, "alice", "Retitle", func(active, closed []*Task) ([]*Task, []*Task) {
		for _, x := range active {
			if x.ID == "t1" {
				x.Title = "Another"
			}
		}
		return active, closed
	})
	if want := []string{"alice|Retitle|_tasks/lists/team/tasks.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	// A task moves to another list: the file that takes it is written
	// first; the last task of a list leaves: its file and folder go.
	f.commits = nil
	f.rewrite(t, "alice", "Move", func(active, closed []*Task) ([]*Task, []*Task) {
		for _, x := range active {
			if x.ID == "t1" {
				x.List = "shop"
			}
		}
		return active, closed
	})
	if want := []string{"alice|Move|_tasks/lists/shop/tasks.json _tasks/lists/team/tasks.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	if _, err := os.Stat(filepath.Join(f.root, "_tasks/lists/team")); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the emptied list's folder is still there (%v)", err)
	}
	if got := f.ids(t, "_tasks/lists/shop/tasks.json"); !reflect.DeepEqual(got, []string{"s1", "s2", "t1"}) {
		t.Errorf("shop: %v", got)
	}
}

// A wiki from before the folders with months already archived: the start
// moves the archives into their lists' folders, and the commit says that it
// filed the tasks, not that it archived any: it is the one to revert for
// an older Zone.
func TestAStartThatFilesArchivesSaysItFiled(t *testing.T) {
	f := newStore(t, "alice")
	f.put(t, "_tasks/tasks.json", `[{"id":"s1","title":"Milk","list":"shop"}]`)
	f.put(t, "_tasks/closed-2020-03.json", `[{"id":"s2","title":"Eggs","list":"shop","closed_at":"2020-03-01T09:00:00"}]`)
	f.ArchiveOldClosed()
	if len(f.commits) != 1 || !strings.HasPrefix(f.commits[0], "|File the tasks by list|") {
		t.Fatalf("commits %q", f.commits)
	}
	if got := f.ids(t, "_tasks/lists/shop/closed-2020-03.json"); !reflect.DeepEqual(got, []string{"s2"}) {
		t.Errorf("the archive in its list's folder: %v", got)
	}
}

// A wiki from before the folders has every task in _tasks/: the start
// files each where its list is, in one commit, and a second start changes
// nothing. A task that is in two files (a write that stopped between them)
// is one task to every reader.
func TestAStartFilesTheTasksByList(t *testing.T) {
	f := newStore(t, "alice")
	f.put(t, "_tasks/tasks.json", `[{"id":"i1","title":"Inbox"},{"id":"s1","title":"Milk","list":"shop"}]`)
	f.put(t, "_tasks/closed.json", `[{"id":"s2","title":"Eggs","list":"shop","closed_at":"2026-06-01T09:00:00"}]`)
	f.put(t, "private/alice/_tasks/tasks.json", `[{"id":"d1","title":"Dear diary","list":"diary"}]`)
	if got := ids(f.Open("alice")); !reflect.DeepEqual(got, []string{"i1", "s1", "d1"}) {
		t.Fatalf("read before the start: %v", got)
	}
	f.ArchiveOldClosed()
	if len(f.commits) != 1 || !strings.HasPrefix(f.commits[0], "|File the tasks by list|") {
		t.Fatalf("commits %q", f.commits)
	}
	for rel, want := range map[string][]string{"_tasks/tasks.json": {"i1"}, "_tasks/lists/shop/tasks.json": {"s1"},
		"_tasks/lists/shop/closed.json": {"s2"}, "_tasks/closed.json": {}, "private/alice/_tasks/lists/diary/tasks.json": {"d1"},
		"private/alice/_tasks/tasks.json": {}} {
		if got := f.ids(t, rel); !reflect.DeepEqual(got, want) && !(len(got) == 0 && len(want) == 0) {
			t.Errorf("%s: %v, want %v", rel, got, want)
		}
	}
	f.commits = nil
	forgetAll()
	f.ArchiveOldClosed()
	if len(f.commits) != 0 {
		t.Errorf("a second start: %q", f.commits)
	}
	// One task in two files: read once, and the next write of these tasks
	// leaves it where its list is.
	f.put(t, "_tasks/tasks.json", `[{"id":"i1","title":"Inbox"},{"id":"s1","title":"Milk","list":"shop"}]`)
	forgetAll()
	if got := ids(f.Open("")); !reflect.DeepEqual(got, []string{"i1", "s1"}) {
		t.Errorf("read with a task in two files: %v", got)
	}
	f.rewrite(t, "", "Touch", func(active, closed []*Task) ([]*Task, []*Task) { return active, closed })
	if got := f.ids(t, "_tasks/tasks.json"); !reflect.DeepEqual(got, []string{"i1"}) {
		t.Errorf("after a write: %v", got)
	}
}

// A list's name that is no folder's name (a file edited by hand) never
// leaves the tasks' folder: its tasks are kept with those of no list.
func TestAListNameThatIsNoFolder(t *testing.T) {
	f := newStore(t)
	f.write(t, "", []*Task{in("../../etc", task("x1", "", "")), in(".hidden", task("x2", "", ""))}, nil, "Write")
	if got := f.names(t); !reflect.DeepEqual(got, []string{"_tasks/tasks.json"}) {
		t.Errorf("the files: %v", got)
	}
	if got := ids(f.Open("")); !reflect.DeepEqual(got, []string{"x1", "x2"}) {
		t.Errorf("read back: %v", got)
	}
}

// one: a write of the one task id as the server does it: what it needs
// read (ReadTaskForWriteLocked), changed by change, and written.
func (f *fixture) one(t *testing.T, user, id, message string, change func(active, closed []*Task) ([]*Task, []*Task)) {
	t.Helper()
	f.Lock()
	defer f.Unlock()
	active, closed, err := f.ReadTaskForWriteLocked(user, id)
	if err != nil {
		t.Fatal(err)
	}
	active, closed = change(active, closed)
	if err := f.WriteBothLocked(user, active, closed, message); err != nil {
		t.Fatal(err)
	}
}

// A write of one task reads the open tasks and, of the closed ones, the
// file that holds it: no other closed file is read, copied or written, so
// an archive that can't be read doesn't stop it, and what a file holds that
// the write didn't read is there afterwards.
func TestAWriteOfOneTaskReadsItsOwnFile(t *testing.T) {
	f := newStore(t, "alice")
	f.write(t, "alice", []*Task{in("shop", task("o1", "", "")), in("shop", task("o2", "", "")), task("i1", "", "")},
		[]*Task{in("shop", task("c-now", "", "2026-06-01T09:00:00")), in("shop", task("c-may", "", "2026-05-02T09:00:00")),
			in("shop", task("c-may2", "", "2026-05-03T09:00:00")), in("team", task("c-old", "", "2025-01-02T09:00:00"))}, "Write")
	f.put(t, "_tasks/lists/team/closed-2024-01.json", "not json")
	move := func(id string, closeIt bool, at string) func(active, closed []*Task) ([]*Task, []*Task) {
		return func(active, closed []*Task) ([]*Task, []*Task) {
			if closeIt {
				for i, x := range active {
					if x.ID == id {
						x.ClosedAt = at
						return slices.Delete(active, i, i+1), append(closed, x)
					}
				}
			} else {
				for i, x := range closed {
					if x.ID == id {
						x.ClosedAt = ""
						return append(active, x), slices.Delete(closed, i, i+1)
					}
				}
			}
			t.Fatalf("%s isn't there to move", id)
			return nil, nil
		}
	}
	// An open task changes: its file, and nothing of the closed ones.
	f.commits = nil
	f.one(t, "alice", "o1", "Retitle", func(active, closed []*Task) ([]*Task, []*Task) {
		if len(closed) != 0 {
			t.Errorf("closed tasks read for an open one: %v", ids(closed))
		}
		for _, x := range active {
			if x.ID == "o1" {
				x.Title = "Another"
			}
		}
		return active, closed
	})
	if want := []string{"alice|Retitle|_tasks/lists/shop/tasks.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	// It is completed: this month's file of its list takes it, beside what was there.
	f.commits = nil
	f.one(t, "alice", "o1", "Done", move("o1", true, "2026-06-01T10:00:00"))
	if got := f.ids(t, "_tasks/lists/shop/closed.json"); !reflect.DeepEqual(got, []string{"c-now", "o1"}) {
		t.Errorf("this month's closed tasks: %v", got)
	}
	if want := []string{"alice|Done|_tasks/lists/shop/tasks.json _tasks/lists/shop/closed.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	// A task of an archive changes: that archive, with its other task.
	f.commits = nil
	f.one(t, "alice", "c-may", "Retitle", func(active, closed []*Task) ([]*Task, []*Task) {
		if got := ids(closed); !reflect.DeepEqual(got, []string{"c-may", "c-may2"}) {
			t.Errorf("read for a task of May's archive: %v", got)
		}
		closed[0].Title = "Another"
		return active, closed
	})
	if want := []string{"alice|Retitle|_tasks/lists/shop/closed-2026-05.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	// It is reopened: out of its archive, which keeps the other; the last one out takes the file away.
	f.one(t, "alice", "c-may", "Reopen", move("c-may", false, ""))
	if got := f.ids(t, "_tasks/lists/shop/closed-2026-05.json"); !reflect.DeepEqual(got, []string{"c-may2"}) {
		t.Errorf("May's archive: %v", got)
	}
	f.one(t, "alice", "c-may2", "Reopen", move("c-may2", false, ""))
	if _, err := os.Stat(filepath.Join(f.root, "_tasks/lists/shop/closed-2026-05.json")); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the emptied archive is still there (%v)", err)
	}
	if got := f.ids(t, "_tasks/lists/shop/tasks.json"); !reflect.DeepEqual(got, []string{"o2", "c-may", "c-may2"}) {
		t.Errorf("the open tasks: %v", got)
	}
	// Nothing else was touched, the file that can't be read included.
	if got := f.ids(t, "_tasks/lists/team/closed-2025-01.json"); !reflect.DeepEqual(got, []string{"c-old"}) {
		t.Errorf("another list's archive: %v", got)
	}
	if f.text(t, "_tasks/lists/team/closed-2024-01.json") != "not json" {
		t.Error("the unreadable archive was written")
	}
	// A task no file has: nothing read for it but the open ones, and the caller finds none.
	f.Lock()
	_, closed, err := f.ReadTaskForWriteLocked("alice", "nope")
	f.Unlock()
	if err != nil || len(closed) != 0 {
		t.Errorf("a task that isn't there: %v, %v", ids(closed), err)
	}
	// A write that reads everything still refuses over the unreadable file.
	f.Lock()
	_, _, err = f.ReadForWriteLocked("alice")
	f.Unlock()
	if !errors.Is(err, ErrCorrupt) {
		t.Errorf("a full read beside an unreadable archive: %v", err)
	}
}

// A write of several tasks (a batch, a delete) reads the open tasks and the
// closed files that hold one of them, each once: no other closed file is
// read or written, so an archive that can't be read doesn't stop it.
func TestAWriteOfSeveralTasksReadsTheirFiles(t *testing.T) {
	f := newStore(t, "alice")
	f.write(t, "alice", []*Task{in("shop", task("o1", "", "")), task("i1", "", ""), task("mine", "alice", "")},
		[]*Task{in("shop", task("c-now", "", "2026-06-01T09:00:00")), in("shop", task("c-may", "", "2026-05-02T09:00:00")),
			in("shop", task("c-may2", "", "2026-05-03T09:00:00")), in("shop", task("c-apr", "", "2026-04-02T09:00:00")),
			in("team", task("c-old", "", "2025-01-02T09:00:00")), task("mine-old", "alice", "2025-03-02T09:00:00")}, "Write")
	f.put(t, "_tasks/lists/team/closed-2024-01.json", "not json")
	some := func(message string, named []string, want []string, change func(active, closed []*Task) ([]*Task, []*Task)) {
		t.Helper()
		f.commits = nil
		f.Lock()
		defer f.Unlock()
		set := map[string]bool{}
		for _, id := range named {
			set[id] = true
		}
		active, closed, err := f.ReadTasksForWriteLocked("alice", set)
		if err != nil {
			t.Fatal(err)
		}
		if got := ids(closed); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: closed tasks read %v, want %v", message, got, want)
		}
		active, closed = change(active, closed)
		if err := f.WriteBothLocked("alice", active, closed, message); err != nil {
			t.Fatal(err)
		}
	}
	retitle := func(named ...string) func(active, closed []*Task) ([]*Task, []*Task) {
		return func(active, closed []*Task) ([]*Task, []*Task) {
			for _, x := range slices.Concat(active, closed) {
				if slices.Contains(named, x.ID) {
					x.Title = "Another"
				}
			}
			return active, closed
		}
	}
	// Open tasks only, of two owners: no closed file is read.
	some("Open ones", []string{"o1", "mine"}, []string{}, retitle("o1", "mine"))
	if want := []string{"alice|Open ones|_tasks/lists/shop/tasks.json private/alice/_tasks/tasks.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	// An open one, two of one archive, one of another owner's and one nobody
	// has: each file once, April's and the unreadable one not at all.
	some("Mixed", []string{"i1", "c-may", "c-may2", "mine-old", "nope"}, []string{"c-may", "c-may2", "mine-old"},
		retitle("i1", "c-may", "c-may2", "mine-old"))
	if want := []string{"alice|Mixed|_tasks/tasks.json _tasks/lists/shop/closed-2026-05.json private/alice/_tasks/closed-2025-03.json"}; !reflect.DeepEqual(f.commits, want) {
		t.Errorf("commits %q, want %q", f.commits, want)
	}
	// Tasks of two files are deleted: both files written without them, and
	// the one left without a task removed.
	some("Delete", []string{"c-now", "c-may"}, []string{"c-now", "c-may", "c-may2"}, func(active, closed []*Task) ([]*Task, []*Task) {
		return active, slices.DeleteFunc(closed, func(x *Task) bool { return x.ID == "c-now" || x.ID == "c-may" })
	})
	if got := f.ids(t, "_tasks/lists/shop/closed-2026-05.json"); !reflect.DeepEqual(got, []string{"c-may2"}) {
		t.Errorf("May's archive: %v", got)
	}
	if _, err := os.Stat(filepath.Join(f.root, "_tasks/lists/shop/closed.json")); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the list's emptied closed.json is still there (%v)", err)
	}
	// One is closed and one reopened in one write: this month's file takes
	// the one, the archive loses the other.
	some("Both ways", []string{"o1", "c-may2"}, []string{"c-may2"}, func(active, closed []*Task) ([]*Task, []*Task) {
		var a, c []*Task
		for _, x := range slices.Concat(active, closed) {
			switch x.ID {
			case "o1":
				x.ClosedAt = "2026-06-01T10:00:00"
				c = append(c, x)
			case "c-may2":
				x.ClosedAt = ""
				a = append(a, x)
			default:
				if x.ClosedAt == "" {
					a = append(a, x)
				} else {
					c = append(c, x)
				}
			}
		}
		return a, c
	})
	if got := f.ids(t, "_tasks/lists/shop/closed.json"); !reflect.DeepEqual(got, []string{"o1"}) {
		t.Errorf("this month's closed tasks: %v", got)
	}
	if got := f.ids(t, "_tasks/lists/shop/tasks.json"); !reflect.DeepEqual(got, []string{"c-may2"}) {
		t.Errorf("the open tasks: %v", got)
	}
	// What the writes didn't name is as it was.
	if got := f.ids(t, "_tasks/lists/shop/closed-2026-04.json"); !reflect.DeepEqual(got, []string{"c-apr"}) {
		t.Errorf("April's archive: %v", got)
	}
	if got := f.ids(t, "_tasks/lists/team/closed-2025-01.json"); !reflect.DeepEqual(got, []string{"c-old"}) {
		t.Errorf("another list's archive: %v", got)
	}
	if f.text(t, "_tasks/lists/team/closed-2024-01.json") != "not json" {
		t.Error("the unreadable archive was written")
	}
}

// An archive nobody read for a while is no longer kept parsed; the open
// tasks and this month's closed ones stay.
func TestIdleArchivesAreForgotten(t *testing.T) {
	f := newStore(t)
	f.write(t, "", []*Task{task("o1", "", "")}, []*Task{task("c1", "", "2026-06-01T09:00:00"), task("c2", "", "2025-01-02T09:00:00")}, "Write")
	_ = f.All("")
	kept := func() []string {
		parsed.Lock()
		defer parsed.Unlock()
		var out []string
		for p := range parsed.files {
			out = append(out, filepath.Base(p))
		}
		slices.Sort(out)
		return out
	}
	if got := kept(); !reflect.DeepEqual(got, []string{"closed-2025-01.json", "closed.json", "tasks.json"}) {
		t.Fatalf("kept after a read of everything: %v", got)
	}
	parsed.Lock()
	forgetOldArchives(time.Now().Add(archiveKept + time.Minute))
	parsed.Unlock()
	if got := kept(); !reflect.DeepEqual(got, []string{"closed.json", "tasks.json"}) {
		t.Errorf("kept after the archive was idle: %v", got)
	}
	if got := ids(f.ClosedInYear("", "2025")); !reflect.DeepEqual(got, []string{"c2"}) {
		t.Errorf("read again: %v", got)
	}
}

// A reader waits for a write's files, not for its commit: while git runs,
// the open tasks are read, and they are what the write left.
func TestAReadDoesntWaitForACommit(t *testing.T) {
	f := newStore(t)
	committing, done := make(chan bool), make(chan bool)
	f.Store.Commit = func(user, message string, paths ...string) error {
		committing <- true
		<-done
		return nil
	}
	go func() {
		f.Lock()
		defer f.Unlock()
		if err := f.WriteBothLocked("", []*Task{task("o1", "", "")}, []*Task{task("c1", "", "2026-06-01T09:00:00")}, "Write"); err != nil {
			t.Error(err)
		}
	}()
	<-committing
	read := make(chan []string)
	go func() { read <- ids(f.All("")) }()
	select {
	case got := <-read:
		if !reflect.DeepEqual(got, []string{"o1", "c1"}) {
			t.Errorf("read during the commit: %v", got)
		}
	case <-time.After(5 * time.Second):
		t.Error("a read waited for the commit")
	}
	close(done)
	f.Lock() // the write is over
	f.Unlock()
}

// While a write is between its files, a reader waits: it sees all of the
// write or none of it.
func TestAReadWaitsForAWritesFiles(t *testing.T) {
	f := newStore(t)
	f.Lock()
	f.Hold()
	read := make(chan []string)
	go func() { read <- ids(f.Open("")) }()
	select {
	case <-read:
		t.Fatal("a read went on between a write's files")
	case <-time.After(50 * time.Millisecond):
	}
	f.Release()
	select {
	case <-read:
	case <-time.After(5 * time.Second):
		t.Error("a read still waits after the files were written")
	}
	f.Unlock()
	// Without Lock nothing is held: a command without a running Zone.
	f.Hold()
	_ = f.Open("")
}

// Stamp is another word after a write of Zone's and after a file changed by
// hand, and the same while nothing changed; another user's own tasks don't
// change it.
func TestStamp(t *testing.T) {
	f := newStore(t, "anna", "bob")
	f.write(t, "", []*Task{task("o1", "", "")}, nil, "Write")
	a := f.Stamp("anna")
	if f.Stamp("anna") != a {
		t.Error("the stamp changed with nothing written")
	}
	f.write(t, "bob", []*Task{task("o1", "", ""), task("p1", "bob", "")}, nil, "Write")
	if f.Stamp("anna") != a {
		t.Error("bob's own task changed anna's stamp")
	}
	if f.Stamp("bob") == a {
		t.Error("bob's stamp is anna's")
	}
	f.write(t, "", []*Task{task("o1", "", ""), task("o2", "", "")}, nil, "Write")
	b := f.Stamp("anna")
	if b == a {
		t.Error("a write left the stamp")
	}
	if err := os.WriteFile(f.OpenPath(""), []byte("[]\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if f.Stamp("anna") == b {
		t.Error("a change by hand left the stamp")
	}
}
