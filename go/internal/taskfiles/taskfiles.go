// Package taskfiles is the tasks on disk: the files of a wiki's task lists,
// read and written under one lock.
//
// The shared tasks are in _tasks/ (tasks.json: the open ones;
// completed.json: this year's completed; completed-YYYY.json: an earlier
// year's), a user's private ones in the same files under
// private/<user>/_tasks/: private because they lie in their folder, like
// their pages. A task's file says whose it is (Task.Owner, "" the shared
// ones), and a write files it there.
//
// A Store knows nothing of Zone's process: the wiki's folder, its users,
// the clock, who may write and how a write is committed are handed to it.
package taskfiles

import (
	"cmp"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/atomicfile"
	"zonewiki/internal/taskmodel"
)

type Task = taskmodel.Task

// DirName is the tasks' folder, in the wiki and in a user's private folder.
const DirName = "_tasks"

// ErrCorrupt: a task list file is there but unreadable; a write refuses
// rather than overwriting it with a partial list.
var ErrCorrupt = errors.New("task storage temporarily unavailable, please retry")

// Store is a wiki's task files. Its fields are set once, before use.
type Store struct {
	// Root: the wiki's folder.
	Root func() string
	// PrivateDir: the folder of the users' private folders, in Root.
	PrivateDir string
	// Users: the users who may have task files of their own, sorted; none
	// without users.
	Users func() []string
	// Private: user has task files of their own (with users, a user).
	Private func(user string) bool
	// MayWrite: nil when user may write the task files, else why not (a
	// viewer). Every write asks, so nothing above it can forget to.
	MayWrite func(user string) error
	// Commit commits the files a write changed, by user ("": Zone itself).
	Commit func(user, message string, paths ...string) error
	// Now: the clock (which year completed tasks belong to the archives).
	Now func() time.Time

	mu sync.Mutex
}

// Lock serializes every read-modify-write of the task files: two concurrent
// task changes would otherwise lose each other's writes. A write holds it
// from its read to its commit; the methods named …Locked expect it held
// (Go's mutex isn't reentrant), the others take it themselves or only read.
func (s *Store) Lock()   { s.mu.Lock() }
func (s *Store) Unlock() { s.mu.Unlock() }

// ---- where the files are ----

// Dir: the folder of owner's task files: _tasks/ for the shared tasks
// (owner ""), private/<owner>/_tasks/ for a user's private ones.
func (s *Store) Dir(owner string) string {
	base := s.Root()
	if owner != "" {
		base = filepath.Join(base, s.PrivateDir, owner)
	}
	return filepath.Join(base, DirName)
}

// OpenPath: owner's open tasks; CompletedPath: this year's completed ones;
// ArchivePath: an earlier year's.
func (s *Store) OpenPath(owner string) string { return filepath.Join(s.Dir(owner), "tasks.json") }
func (s *Store) CompletedPath(owner string) string {
	return filepath.Join(s.Dir(owner), "completed.json")
}
func (s *Store) ArchivePath(owner, year string) string {
	return filepath.Join(s.Dir(owner), "completed-"+year+".json")
}

var archiveNameRE = regexp.MustCompile(`^completed-(\d{4})\.json$`)

// Archives: owner's yearly archives (completed-2025.json …), sorted.
func (s *Store) Archives(owner string) []string {
	entries, _ := os.ReadDir(s.Dir(owner))
	var out []string
	for _, e := range entries {
		if archiveNameRE.MatchString(e.Name()) {
			out = append(out, filepath.Join(s.Dir(owner), e.Name()))
		}
	}
	sort.Strings(out)
	return out
}

// File: a task file and whose it is ("" the shared ones).
type File struct{ Path, Owner string }

// Files: every task file of owners: open, completed, archives.
func (s *Store) Files(owners []string) []File {
	var out []File
	for _, o := range owners {
		for _, p := range append([]string{s.OpenPath(o), s.CompletedPath(o)}, s.Archives(o)...) {
			out = append(out, File{p, o})
		}
	}
	return out
}

// ---- whose files ----

// Owners: whose task files user reads and writes: the shared ones and,
// with users, their own.
func (s *Store) Owners(user string) []string {
	if !s.Private(user) {
		return []string{""}
	}
	return []string{"", user}
}

// EveryOwner: the owners of every task list, for what is done over all of
// them (the yearly archive, a rename's links, the search index).
func (s *Store) EveryOwner() []string { return append([]string{""}, s.Users()...) }

// eachOwner: the tasks list gives for each of owners, marked as theirs
// (Task.Owner), in that order.
func eachOwner(owners []string, list func(owner string) []*Task) []*Task {
	out := []*Task{}
	for _, o := range owners {
		for _, t := range list(o) {
			t.Owner = o
			out = append(out, t)
		}
	}
	return out
}

// ---- reading and writing a file ----

// ReadStrict reads a task list for a write: a file that is there but
// unusable (empty, not JSON, not a list) is ErrCorrupt, never [].
func ReadStrict(path string) ([]*Task, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return []*Task{}, nil
	}
	if err != nil || strings.TrimSpace(string(raw)) == "" {
		return nil, ErrCorrupt
	}
	tasks, ok := taskmodel.Parse(raw)
	if !ok {
		return nil, ErrCorrupt
	}
	return tasks, nil
}

// ReadLenient reads a task list for reading: missing or unreadable is [].
func ReadLenient(path string) []*Task {
	raw, err := os.ReadFile(path)
	if err != nil {
		return []*Task{}
	}
	tasks, ok := taskmodel.Parse(raw)
	if !ok {
		return []*Task{}
	}
	return tasks
}

// Write writes a task list file, atomically.
func Write(path string, tasks []*Task) error {
	text, err := taskmodel.Encode(tasks)
	if err != nil {
		return err
	}
	return atomicfile.Write(path, text)
}

// ---- years ----

var yearRE = regexp.MustCompile(`^\d{4}$`)

// CompletedYear: the year a completed task belongs to ("" when unusable).
func CompletedYear(t *Task) string {
	if year := t.CompletedAt; len(year) >= 4 && yearRE.MatchString(year[:4]) {
		return year[:4]
	}
	return ""
}

// CurrentYear: this year, as the files name it.
func (s *Store) CurrentYear() string { return s.Now().Format("2006") }

// PastYear: the task's completion year when it is before this one (its
// archive's), else "" (the current completed file).
func (s *Store) PastYear(t *Task) string { return pastYearOf(t, s.CurrentYear()) }

func pastYearOf(t *Task, current string) string {
	if y := CompletedYear(t); y != "" && y < current {
		return y
	}
	return ""
}

// ---- reading what a user sees ----

// ReadOpenStrict: the open tasks user sees, read for a write.
func (s *Store) ReadOpenStrict(user string) ([]*Task, error) {
	var err error
	tasks := eachOwner(s.Owners(user), func(o string) []*Task {
		l, e := ReadStrict(s.OpenPath(o))
		err = cmp.Or(err, e)
		return l
	})
	return tasks, err
}

// readOpen: the open tasks user sees (shared and their own).
func (s *Store) readOpen(user string) []*Task {
	return eachOwner(s.Owners(user), func(o string) []*Task { return ReadLenient(s.OpenPath(o)) })
}

// readCompleted: this year's completed tasks user sees.
func (s *Store) readCompleted(user string) []*Task {
	return eachOwner(s.Owners(user), func(o string) []*Task { return ReadLenient(s.CompletedPath(o)) })
}

// readArchived: the archived tasks (earlier years') of owners.
func (s *Store) readArchived(owners []string) []*Task {
	return eachOwner(owners, func(o string) []*Task {
		var tasks []*Task
		for _, p := range s.Archives(o) {
			tasks = append(tasks, ReadLenient(p)...)
		}
		return tasks
	})
}

// Open: the open tasks user sees, under the lock: a task made private or
// shared moves between two files, and a read between their writes would
// miss it.
func (s *Store) Open(user string) []*Task {
	s.Lock()
	defer s.Unlock()
	return s.readOpen(user)
}

// All: every task user sees, archived ones too, one snapshot.
func (s *Store) All(user string) []*Task {
	s.Lock()
	defer s.Unlock()
	return slices.Concat(s.readOpen(user), s.readCompleted(user), s.readArchived(s.Owners(user)))
}

// Every: every task of every owner (Task.Owner says whose), for what keeps
// all of them and filters by user later (the search index).
func (s *Store) Every() []*Task {
	s.Lock()
	defer s.Unlock()
	return s.EveryLocked()
}

func (s *Store) EveryLocked() []*Task {
	owners := s.EveryOwner()
	return slices.Concat(eachOwner(owners, func(o string) []*Task { return ReadLenient(s.OpenPath(o)) }),
		eachOwner(owners, func(o string) []*Task { return ReadLenient(s.CompletedPath(o)) }),
		s.readArchived(owners))
}

// IDsLocked: the ids of every owner's tasks, so a restored task never takes
// one another file has.
func (s *Store) IDsLocked() map[string]bool {
	ids := map[string]bool{}
	for _, t := range s.EveryLocked() {
		ids[t.ID] = true
	}
	return ids
}

// CompletedInYear: the Completed view of one year, of the tasks user sees
// (this year's view takes tasks without a usable date too).
func (s *Store) CompletedInYear(user, year string) []*Task {
	current := s.CurrentYear()
	s.Lock()
	tasks := s.readCompleted(user)
	if year != current {
		tasks = append(tasks, eachOwner(s.Owners(user), func(o string) []*Task {
			return ReadLenient(s.ArchivePath(o, year))
		})...)
	}
	s.Unlock()
	out := []*Task{}
	for _, t := range tasks {
		if year == current && pastYearOf(t, current) == "" || year != current && CompletedYear(t) == year {
			out = append(out, t)
		}
	}
	return out
}

// CompletedYears: the years with completed tasks user sees that in takes,
// newest first; this year always.
func (s *Store) CompletedYears(user string, in func(*Task) bool) []string {
	current := s.CurrentYear()
	years := map[string]bool{current: true}
	s.Lock()
	for _, t := range s.readCompleted(user) {
		if in(t) && pastYearOf(t, current) != "" {
			years[CompletedYear(t)] = true
		}
	}
	for _, o := range s.Owners(user) {
		for _, p := range s.Archives(o) {
			for _, t := range ReadLenient(p) {
				if in(t) {
					years[archiveNameRE.FindStringSubmatch(filepath.Base(p))[1]] = true
					break
				}
			}
		}
	}
	s.Unlock()
	out := slices.Sorted(maps.Keys(years))
	slices.Reverse(out)
	return out
}

// ---- reading for a write ----

// readCompletedAllStrict: every completed task of owner's, the archives
// first, for a write (writeCompleted files each by year again).
func (s *Store) readCompletedAllStrict(owner string) ([]*Task, error) {
	var tasks []*Task
	for _, p := range s.Archives(owner) {
		l, err := ReadStrict(p)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, l...)
	}
	l, err := ReadStrict(s.CompletedPath(owner))
	if err != nil {
		return nil, err
	}
	return append(tasks, l...), nil
}

// readOwnerStrict: owner's open tasks and every completed one, read for a
// write (ErrCorrupt when a file is unreadable), marked as owner's.
func (s *Store) readOwnerStrict(owner string) (active, completed []*Task, err error) {
	if active, err = ReadStrict(s.OpenPath(owner)); err != nil {
		return nil, nil, err
	}
	if completed, err = s.readCompletedAllStrict(owner); err != nil {
		return nil, nil, err
	}
	for _, t := range slices.Concat(active, completed) {
		t.Owner = owner
	}
	return active, completed, nil
}

// ReadForWriteLocked: the open tasks and every completed task user sees
// (shared and their own), read for a write.
func (s *Store) ReadForWriteLocked(user string) (active, completed []*Task, err error) {
	active, completed = []*Task{}, []*Task{}
	for _, o := range s.Owners(user) {
		a, c, err := s.readOwnerStrict(o)
		if err != nil {
			return nil, nil, err
		}
		active, completed = append(active, a...), append(completed, c...)
	}
	return active, completed, nil
}

// ---- writing ----

// byOwner: tasks split by their owner, each of user's owners present (one
// whose last task left still gets its file written); a task of someone
// else's is refused: it can't have been read for user.
func (s *Store) byOwner(user string, tasks []*Task) (map[string][]*Task, error) {
	out := map[string][]*Task{}
	for _, o := range s.Owners(user) {
		out[o] = []*Task{}
	}
	for _, t := range tasks {
		if _, ok := out[t.Owner]; !ok {
			return nil, errors.New("tasks: a task of " + t.Owner + "'s in a write by " + user)
		}
		out[t.Owner] = append(out[t.Owner], t)
	}
	return out, nil
}

// writeOpenLocked writes the open tasks into their owners' files, those
// whose tasks changed (and the shared one when it is missing); returns
// their paths.
func (s *Store) writeOpenLocked(user string, tasks []*Task) ([]string, error) {
	if err := s.MayWrite(user); err != nil {
		return nil, err
	}
	split, err := s.byOwner(user, tasks)
	if err != nil {
		return nil, err
	}
	var changed []string
	for _, o := range s.Owners(user) {
		text, err := taskmodel.Encode(split[o])
		if err != nil {
			return changed, err
		}
		raw, rerr := os.ReadFile(s.OpenPath(o))
		if rerr == nil && string(raw) == text || errors.Is(rerr, os.ErrNotExist) && o != "" && len(split[o]) == 0 {
			continue
		}
		if err := atomicfile.Write(s.OpenPath(o), text); err != nil {
			return changed, err
		}
		changed = append(changed, s.OpenPath(o))
	}
	return changed, nil
}

// writeCompleted writes owner's completed tasks split by year (this year's
// into completed.json, earlier ones into completed-YYYY.json); only files
// whose tasks change (with reformat also those in another format). Returns
// their paths, for the commit.
func (s *Store) writeCompleted(owner string, completed []*Task, reformat bool) ([]string, error) {
	current := s.CurrentYear()
	order := []string{s.CompletedPath(owner)}
	byPath := map[string][]*Task{s.CompletedPath(owner): {}}
	for _, p := range s.Archives(owner) {
		order = append(order, p)
		byPath[p] = []*Task{}
	}
	for _, t := range completed {
		p := s.CompletedPath(owner)
		if y := pastYearOf(t, current); y != "" {
			p = s.ArchivePath(owner, y)
		}
		if _, ok := byPath[p]; !ok {
			order = append(order, p)
		}
		byPath[p] = append(byPath[p], t)
	}
	var changed []string
	for _, p := range order {
		tasks := byPath[p]
		raw, err := os.ReadFile(p)
		if errors.Is(err, os.ErrNotExist) && len(tasks) == 0 {
			continue // no file for no tasks
		}
		text, encErr := taskmodel.Encode(tasks)
		if encErr != nil {
			return changed, encErr
		}
		if err == nil && (string(raw) == text || !reformat && sameTasks(raw, text)) {
			continue
		}
		if err := atomicfile.Write(p, text); err != nil {
			return changed, err
		}
		changed = append(changed, p)
	}
	return changed, nil
}

// sameTasks: a file holds the tasks text has, whatever its format.
func sameTasks(raw []byte, text string) bool {
	tasks, ok := taskmodel.Parse(raw)
	if !ok {
		return false
	}
	have, err := taskmodel.Encode(tasks)
	return err == nil && have == text
}

// CommitFiles commits the task files a write changed, by user.
func (s *Store) CommitFiles(user, message string, changed []string) {
	if len(changed) > 0 {
		_ = s.Commit(user, message, changed...)
	}
}

// WriteOpenLocked writes the open tasks (each into its owner's file),
// committed by user.
func (s *Store) WriteOpenLocked(user string, tasks []*Task, message string) error {
	changed, err := s.writeOpenLocked(user, tasks)
	s.CommitFiles(user, message, changed)
	return err
}

// WriteBothLocked writes the open tasks and every completed task (filed by
// owner and year) in one commit, by user.
func (s *Store) WriteBothLocked(user string, active, completed []*Task, message string) error {
	if err := s.MayWrite(user); err != nil {
		return err
	}
	changed, err := s.writeOpenLocked(user, active)
	if err == nil {
		var split map[string][]*Task
		if split, err = s.byOwner(user, completed); err == nil {
			for _, o := range s.Owners(user) {
				more, werr := s.writeCompleted(o, split[o], false)
				changed = append(changed, more...)
				if err = werr; err != nil {
					break
				}
			}
		}
	}
	s.CommitFiles(user, message, changed)
	return err
}

// ArchiveOldCompleted rolls earlier years' completed tasks into their
// archives (at start; every write files by year too), and writes files in
// another format (a hand-edited file) as Zone writes them, so later commits
// show only what changed; every owner's files, in one commit. An unreadable
// file is left alone (its owner's files are): this is never worth failing
// over.
func (s *Store) ArchiveOldCompleted() {
	s.Lock()
	defer s.Unlock()
	moved := false
	var changed []string
	for _, o := range s.EveryOwner() {
		active, completed, err := s.readOwnerStrict(o)
		if err != nil {
			continue
		}
		for _, t := range ReadLenient(s.CompletedPath(o)) {
			moved = moved || s.PastYear(t) != ""
		}
		if raw, err := os.ReadFile(s.OpenPath(o)); err == nil {
			if text, err := taskmodel.Encode(active); err == nil && string(raw) != text && atomicfile.Write(s.OpenPath(o), text) == nil {
				changed = append(changed, s.OpenPath(o))
			}
		}
		more, _ := s.writeCompleted(o, completed, true)
		changed = append(changed, more...)
	}
	if len(changed) == 0 {
		return
	}
	msg := "Write the task files in Zone's format"
	if moved {
		msg = "Archive completed tasks"
	}
	_ = s.Commit("", msg, changed...)
}

// Snapshot: the open tasks user sees and every completed one (this year's
// and the archives'), read together.
func (s *Store) Snapshot(user string) (open, done []*Task) {
	s.Lock()
	defer s.Unlock()
	return s.readOpen(user), append(s.readCompleted(user), s.readArchived(s.Owners(user))...)
}
