// Package taskfiles is the tasks on disk: the files of a wiki's task lists,
// read and written under one lock.
//
// The shared tasks are in _tasks/ (tasks.json: the open ones;
// closed.json: this month's closed; closed-YYYY-MM.json: an earlier
// month's), a user's private ones in the same files under
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
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
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
	// NoTasks: user sees no task at all (a public wiki's visitor); nil: no
	// such user. Every read asks (Owners), so nothing above it can forget to.
	NoTasks func(user string) bool
	// MayWrite: nil when user may write the task files, else why not (a
	// viewer). Every write asks, so nothing above it can forget to.
	MayWrite func(user string) error
	// Commit commits the files a write changed, by user ("": Zone itself).
	Commit func(user, message string, paths ...string) error
	// Now: the clock (which year closed tasks belong to the archives).
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

// OpenPath: owner's open tasks; ClosedPath: this month's closed ones (and
// those without a usable date).
func (s *Store) OpenPath(owner string) string { return filepath.Join(s.Dir(owner), "tasks.json") }
func (s *Store) ClosedPath(owner string) string {
	return filepath.Join(s.Dir(owner), "closed.json")
}

// ArchivePath: owner's archive of a month ("2026-03": closed-2026-03.json;
// until 2026-11 a year's, "2025": closed-2025.json).
func (s *Store) ArchivePath(owner, period string) string {
	return filepath.Join(s.Dir(owner), "closed-"+period+".json")
}

// An archive's name: closed-YYYY-MM.json, a month's; closed-YYYY.json, a
// year's, from before the months (its tasks are filed by month at the next
// start). The year is the first group.
var archiveNameRE = regexp.MustCompile(`^closed-(\d{4})(-\d{2})?\.json$`)

// archiveYear: the year an archive file is of.
func archiveYear(path string) string { return archiveNameRE.FindStringSubmatch(filepath.Base(path))[1] }

// Archives: owner's archives (closed-2026-03.json …, closed-2025.json), sorted.
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

// Files: every task file of owners: open, closed, archives.
func (s *Store) Files(owners []string) []File {
	var out []File
	for _, o := range owners {
		for _, p := range append([]string{s.OpenPath(o), s.ClosedPath(o)}, s.Archives(o)...) {
			out = append(out, File{p, o})
		}
	}
	return out
}

// ---- whose files ----

// Owners: whose task files user reads and writes: the shared ones and,
// with users, their own.
func (s *Store) Owners(user string) []string {
	if s.NoTasks != nil && s.NoTasks(user) {
		return nil
	}
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

// What a file held when it was last parsed: every page view reads the open
// tasks (the Tasks links' count), and a write reads every file, so a file
// is parsed once and handed out as copies until it changes. Zone is one
// process and writes its task files here (Write, which forgets the file); a
// file changed by hand is a file with another time, size or inode.
type parsedFile struct {
	info  os.FileInfo
	tasks []*Task
	ok    bool // it held a list of tasks
}

var parsed = struct {
	sync.Mutex
	files map[string]parsedFile
}{files: map[string]parsedFile{}}

// readFile: path's tasks as copies of their own (a caller may change them);
// exists: there is such a file to read; ok: it holds a list of tasks.
func readFile(path string) (tasks []*Task, exists, ok bool) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, !errors.Is(err, os.ErrNotExist), false
	}
	parsed.Lock()
	defer parsed.Unlock()
	f, hit := parsed.files[path]
	if !hit || !os.SameFile(f.info, info) || !f.info.ModTime().Equal(info.ModTime()) || f.info.Size() != info.Size() {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, true, false
		}
		// The file as it was read: a write between the stat and the read
		// shows as a change next time.
		f = parsedFile{info: info}
		if strings.TrimSpace(string(raw)) != "" {
			f.tasks, f.ok = taskmodel.Parse(raw)
		}
		parsed.files[path] = f
	}
	if !f.ok {
		return nil, true, false
	}
	out := make([]*Task, len(f.tasks))
	for i, t := range f.tasks {
		out[i] = t.Clone()
	}
	return out, true, true
}

// ReadStrict reads a task list for a write: a file that is there but
// unusable (empty, not JSON, not a list) is ErrCorrupt, never [].
func ReadStrict(path string) ([]*Task, error) {
	tasks, exists, ok := readFile(path)
	switch {
	case !exists:
		return []*Task{}, nil
	case !ok:
		return nil, ErrCorrupt
	}
	return tasks, nil
}

// ReadLenient reads a task list for reading: missing or unreadable is [].
func ReadLenient(path string) []*Task {
	tasks, _, ok := readFile(path)
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
	return writeTasks(path, tasks, text)
}

// writeTasks writes a task file, atomically: tasks as text (their
// taskmodel.Encode). Every write of one passes here: the change is counted,
// and the file is known as parsed from then on (copies of tasks), so the
// next read doesn't parse what was just written.
func writeTasks(path string, tasks []*Task, text string) error {
	parsed.Lock()
	defer parsed.Unlock()
	delete(parsed.files, path)
	changes.Add(1)
	if err := atomicfile.Write(path, text); err != nil {
		return err
	}
	if info, err := os.Stat(path); err == nil {
		// As a parse of the file would give them: whose they are is not in it
		// (a reader says: eachOwner, readOwnerStrict).
		kept := make([]*Task, len(tasks))
		for i, t := range tasks {
			kept[i] = t.Clone()
			kept[i].Owner = ""
		}
		parsed.files[path] = parsedFile{info: info, tasks: kept, ok: true}
	}
	return nil
}

// forget: path is no longer the file that was parsed (it is gone).
func forget(path string) {
	parsed.Lock()
	delete(parsed.files, path)
	parsed.Unlock()
	changes.Add(1)
}

func exists(path string) bool { _, err := os.Stat(path); return err == nil }

// holds: path is known to hold exactly tasks (as it was parsed or written,
// and it is that file still): a write of them changes nothing, and needs
// neither the file read nor the tasks encoded to find that out. A large
// file whose tasks a write didn't touch (the closed tasks, when an open one
// changes) costs a comparison.
func holds(path string, tasks []*Task) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	parsed.Lock()
	defer parsed.Unlock()
	f, hit := parsed.files[path]
	if !hit || !f.ok || len(f.tasks) != len(tasks) ||
		!os.SameFile(f.info, info) || !f.info.ModTime().Equal(info.ModTime()) || f.info.Size() != info.Size() {
		return false
	}
	for i, t := range tasks {
		if !t.Same(f.tasks[i]) {
			return false
		}
	}
	return true
}

// changes counts the writes of task files and registries since Zone
// started, so that a page can ask whether its tasks are still what the
// server has (Changes) without fetching them.
var (
	changes atomic.Int64
	started = time.Now().UnixNano()
)

// Changes: a word that is another one after any write of a task file or a
// lists' registry, and after a restart. Not after a file changed by hand.
func Changes() string {
	return strconv.FormatInt(started, 36) + "-" + strconv.FormatInt(changes.Load(), 10)
}

// ---- years ----

var yearRE = regexp.MustCompile(`^\d{4}$`)

// ClosedYear: the year a closed task belongs to ("" when unusable).
func ClosedYear(t *Task) string {
	if year := t.ClosedAt; len(year) >= 4 && yearRE.MatchString(year[:4]) {
		return year[:4]
	}
	return ""
}

// CurrentYear: this year, as the files name it.
func (s *Store) CurrentYear() string { return s.Now().Format("2006") }

// PastYear: the task's completion year when it is before this one (its
// archive's), else "" (the current closed file).
func (s *Store) PastYear(t *Task) string { return pastYearOf(t, s.CurrentYear()) }

func pastYearOf(t *Task, current string) string {
	if y := ClosedYear(t); y != "" && y < current {
		return y
	}
	return ""
}

var monthRE = regexp.MustCompile(`^\d{4}-\d{2}$`)

// pastMonthOf: the month a closed task is archived under ("2026-03") when
// it was closed before the month current, else "" (the current closed
// file, closed.json, which also takes a task without a usable date). The
// closed tasks are filed by month so that completing one writes a month's
// tasks, not a year's: in a large wiki that file is megabytes, written
// whole, and kept whole in git, at every completion.
func pastMonthOf(t *Task, current string) string {
	if m := t.ClosedAt; len(m) >= 7 && monthRE.MatchString(m[:7]) && m[:7] < current {
		return m[:7]
	}
	return ""
}

// currentMonth: this month, as the files name it.
func (s *Store) currentMonth() string { return s.Now().Format("2006-01") }

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

// readClosed: the closed tasks user sees that are in closed.json: this
// month's (and, until the next write, an earlier month's).
func (s *Store) readClosed(user string) []*Task {
	return eachOwner(s.Owners(user), func(o string) []*Task { return ReadLenient(s.ClosedPath(o)) })
}

// readArchived: the archived tasks (earlier months') of owners.
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
	return slices.Concat(s.readOpen(user), s.readClosed(user), s.readArchived(s.Owners(user)))
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
		eachOwner(owners, func(o string) []*Task { return ReadLenient(s.ClosedPath(o)) }),
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

// ClosedInYear: the Closed view of one year, of the tasks user sees
// (this year's view takes tasks without a usable date too).
func (s *Store) ClosedInYear(user, year string) []*Task {
	current := s.CurrentYear()
	s.Lock()
	// The current file (this month's, and what has no usable date) and the
	// year's archives.
	tasks := s.readClosed(user)
	tasks = append(tasks, eachOwner(s.Owners(user), func(o string) []*Task {
		var l []*Task
		for _, p := range s.Archives(o) {
			if archiveYear(p) == year {
				l = append(l, ReadLenient(p)...)
			}
		}
		return l
	})...)
	s.Unlock()
	out := []*Task{}
	for _, t := range tasks {
		if year == current && pastYearOf(t, current) == "" || year != current && ClosedYear(t) == year {
			out = append(out, t)
		}
	}
	return out
}

// ClosedYears: the years with closed tasks user sees that in takes,
// newest first; this year always.
func (s *Store) ClosedYears(user string, in func(*Task) bool) []string {
	current := s.CurrentYear()
	years := map[string]bool{current: true}
	s.Lock()
	for _, t := range s.readClosed(user) {
		if in(t) && pastYearOf(t, current) != "" {
			years[ClosedYear(t)] = true
		}
	}
	for _, o := range s.Owners(user) {
		for _, p := range s.Archives(o) {
			for _, t := range ReadLenient(p) {
				if t.Owner = o; in(t) {
					years[archiveYear(p)] = true
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

// readClosedAllStrict: every closed task of owner's, the archives
// first, for a write (writeClosed files each by month again).
func (s *Store) readClosedAllStrict(owner string) ([]*Task, error) {
	var tasks []*Task
	for _, p := range s.Archives(owner) {
		l, err := ReadStrict(p)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, l...)
	}
	l, err := ReadStrict(s.ClosedPath(owner))
	if err != nil {
		return nil, err
	}
	return append(tasks, l...), nil
}

// readOwnerStrict: owner's open tasks and every closed one, read for a
// write (ErrCorrupt when a file is unreadable), marked as owner's.
func (s *Store) readOwnerStrict(owner string) (active, closed []*Task, err error) {
	if active, err = ReadStrict(s.OpenPath(owner)); err != nil {
		return nil, nil, err
	}
	if closed, err = s.readClosedAllStrict(owner); err != nil {
		return nil, nil, err
	}
	for _, t := range slices.Concat(active, closed) {
		t.Owner = owner
	}
	return active, closed, nil
}

// ReadForWriteLocked: the open tasks and every closed task user sees
// (shared and their own), read for a write.
func (s *Store) ReadForWriteLocked(user string) (active, closed []*Task, err error) {
	active, closed = []*Task{}, []*Task{}
	for _, o := range s.Owners(user) {
		a, c, err := s.readOwnerStrict(o)
		if err != nil {
			return nil, nil, err
		}
		active, closed = append(active, a...), append(closed, c...)
	}
	return active, closed, nil
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
		if holds(s.OpenPath(o), split[o]) {
			continue
		}
		text, err := taskmodel.Encode(split[o])
		if err != nil {
			return changed, err
		}
		raw, rerr := os.ReadFile(s.OpenPath(o))
		if rerr == nil && string(raw) == text || errors.Is(rerr, os.ErrNotExist) && o != "" && len(split[o]) == 0 {
			continue
		}
		if err := writeTasks(s.OpenPath(o), split[o], text); err != nil {
			return changed, err
		}
		changed = append(changed, s.OpenPath(o))
	}
	return changed, nil
}

// writeClosed writes owner's closed tasks filed by month (this month's, and
// those without a usable date, into closed.json, earlier ones into
// closed-YYYY-MM.json); only files whose tasks change (with reformat also
// those in another format). An archive left without a task is removed: a
// year's file from before the months (closed-YYYY.json), whose tasks are in
// their months' files from the first write on. Returns the paths written
// and removed, for the commit.
func (s *Store) writeClosed(owner string, closed []*Task, reformat bool) ([]string, error) {
	current := s.currentMonth()
	order := []string{s.ClosedPath(owner)}
	byPath := map[string][]*Task{s.ClosedPath(owner): {}}
	for _, p := range s.Archives(owner) {
		order = append(order, p)
		byPath[p] = []*Task{}
	}
	for _, t := range closed {
		p := s.ClosedPath(owner)
		if m := pastMonthOf(t, current); m != "" {
			p = s.ArchivePath(owner, m)
		}
		if _, ok := byPath[p]; !ok {
			order = append(order, p)
		}
		byPath[p] = append(byPath[p], t)
	}
	var changed []string
	for _, p := range order {
		tasks := byPath[p]
		if !reformat && holds(p, tasks) {
			continue
		}
		raw, err := os.ReadFile(p)
		if errors.Is(err, os.ErrNotExist) && len(tasks) == 0 {
			continue // no file for no tasks
		}
		// An archive that holds no task anymore goes: a year's, whose tasks
		// are filed by month now; a month's whose tasks were all reopened.
		if err == nil && len(tasks) == 0 && p != s.ClosedPath(owner) {
			if os.Remove(p) == nil {
				forget(p)
				changed = append(changed, p)
			}
			continue
		}
		text, encErr := taskmodel.Encode(tasks)
		if encErr != nil {
			return changed, encErr
		}
		if err == nil && (string(raw) == text || !reformat && sameTasks(raw, text)) {
			continue
		}
		if err := writeTasks(p, tasks, text); err != nil {
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
// committed by user; also: files written with them (a list's registry)
// that belong in that commit.
func (s *Store) WriteOpenLocked(user string, tasks []*Task, message string, also ...string) error {
	changed, err := s.writeOpenLocked(user, tasks)
	s.CommitFiles(user, message, append(changed, also...))
	return err
}

// WriteBothLocked writes the open tasks and every closed task (filed by
// owner and month) in one commit, by user; also: files written with them
// (the lists' registries) that belong in that commit.
func (s *Store) WriteBothLocked(user string, active, closed []*Task, message string, also ...string) error {
	if err := s.MayWrite(user); err != nil {
		return err
	}
	changed, err := s.writeOpenLocked(user, active)
	if err == nil {
		var split map[string][]*Task
		if split, err = s.byOwner(user, closed); err == nil {
			for _, o := range s.Owners(user) {
				more, werr := s.writeClosed(o, split[o], false)
				changed = append(changed, more...)
				if err = werr; err != nil {
					break
				}
			}
		}
	}
	s.CommitFiles(user, message, append(changed, also...))
	return err
}

// Until the files were named after what they hold (closed: done or won't
// do), they were completed.json and completed-YYYY.json, and a task had
// completed_at.
var oldClosedNameRE = regexp.MustCompile(`^completed(-\d{4})?\.json$`)

// renameOldClosed gives owner's task files of the old names their new ones
// (never over a file that is there), and returns both paths of each: the
// one that went and the one that came. FromJSON reads the old field, and
// ArchiveOldClosed then writes the files as Zone writes them now.
func (s *Store) renameOldClosed(owner string) []string {
	entries, _ := os.ReadDir(s.Dir(owner))
	var changed []string
	for _, e := range entries {
		m := oldClosedNameRE.FindStringSubmatch(e.Name())
		if m == nil || e.IsDir() {
			continue
		}
		from, to := filepath.Join(s.Dir(owner), e.Name()), filepath.Join(s.Dir(owner), "closed"+m[1]+".json")
		if _, err := os.Lstat(to); err == nil {
			continue
		}
		if os.Rename(from, to) == nil {
			changed = append(changed, from, to)
		}
	}
	return changed
}

// ArchiveOldClosed rolls earlier months' closed tasks into their
// archives (at start; every write files by month too), gives files of the
// old names their new ones (renameOldClosed), and writes files in
// another format (a hand-edited file) as Zone writes them, so later commits
// show only what changed; every owner's files, in one commit. An unreadable
// file is left alone (its owner's files are): this is never worth failing
// over.
func (s *Store) ArchiveOldClosed() {
	s.Lock()
	defer s.Unlock()
	moved, renamed := false, false
	var changed []string
	for _, o := range s.EveryOwner() {
		if old := s.renameOldClosed(o); len(old) > 0 {
			changed, renamed = append(changed, old...), true
		}
		active, closed, err := s.readOwnerStrict(o)
		if err != nil {
			continue
		}
		for _, t := range closed {
			moved = moved || pastMonthOf(t, s.currentMonth()) != "" && !exists(s.ArchivePath(o, pastMonthOf(t, s.currentMonth())))
		}
		if raw, err := os.ReadFile(s.OpenPath(o)); err == nil {
			if text, err := taskmodel.Encode(active); err == nil && string(raw) != text && writeTasks(s.OpenPath(o), active, text) == nil {
				changed = append(changed, s.OpenPath(o))
			}
		}
		more, _ := s.writeClosed(o, closed, true)
		changed = append(changed, more...)
	}
	if len(changed) == 0 {
		return
	}
	msg := "Write the task files in Zone's format"
	if moved {
		msg = "Archive closed tasks"
	}
	if renamed {
		msg = "Name the task files of closed tasks closed"
	}
	_ = s.Commit("", msg, changed...)
}

// Snapshot: the open tasks user sees and every closed one (this month's
// and the archives'), read together.
func (s *Store) Snapshot(user string) (open, done []*Task) {
	s.Lock()
	defer s.Unlock()
	return s.readOpen(user), append(s.readClosed(user), s.readArchived(s.Owners(user))...)
}
