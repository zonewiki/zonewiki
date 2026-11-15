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
// Each list has its files in a folder of its own, lists/<name>/ in its
// owner's _tasks/ (the same three kinds of file); the tasks in no list (an
// Inbox's) are in _tasks/ itself. So a write costs what its list weighs,
// not what the wiki weighs: a file is written whole and kept whole in git
// at every change. Which list a task is in is the task's to say (its
// `list`), as ever: the folder is where a write files it (dirOf), a read
// takes every folder, and a task found in another list's folder (a file
// moved by hand, a list renamed, a wiki from before the folders) is filed
// where it belongs by the next write of its owner's tasks, or at start
// (ArchiveOldClosed).
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
	// scope: the closed files the write in progress read
	// (ReadTaskForWriteLocked), which are the only ones it may write whole;
	// nil: it read every one (ReadForWriteLocked). Lives as long as the lock
	// is held.
	scope map[string]bool
}

// Lock serializes every read-modify-write of the task files: two concurrent
// task changes would otherwise lose each other's writes. A write holds it
// from its read to its commit; the methods named …Locked expect it held
// (Go's mutex isn't reentrant), the others take it themselves or only read.
func (s *Store) Lock()   { s.mu.Lock() }
func (s *Store) Unlock() { s.scope = nil; s.mu.Unlock() }

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

// ListsDirName: the folder of the lists' folders, in an owner's Dir.
const ListsDirName = "lists"

// listDirRE: a list's name that is its folder's name as it is: lowercase
// words as cleanList leaves them. Any other name (a dot first, a path
// separator: none a list has, but a file edited by hand may) has no folder,
// and its tasks are kept with the Inbox's.
var listDirRE = regexp.MustCompile(`^[^./\\\x00][^/\\\x00]{0,99}$`)

// dirOf: the folder of the files a task of owner's in list is kept in:
// the list's own, or owner's Dir for the tasks in no list.
func (s *Store) dirOf(owner, list string) string {
	if list == "" || !listDirRE.MatchString(list) {
		return s.Dir(owner)
	}
	return filepath.Join(s.Dir(owner), ListsDirName, list)
}

// dirs: the folders that hold task files of owner's: their Dir, then each
// list's that is there, A–Z.
func (s *Store) dirs(owner string) []string {
	out := []string{s.Dir(owner)}
	entries, _ := os.ReadDir(filepath.Join(s.Dir(owner), ListsDirName))
	for _, e := range entries {
		if e.IsDir() {
			out = append(out, filepath.Join(s.Dir(owner), ListsDirName, e.Name()))
		}
	}
	return out
}

// The files of one folder: its open tasks, this month's closed ones (and
// those without a usable date), and an archive of a month ("2026-03":
// closed-2026-03.json; until 2026-11 a year's, "2025": closed-2025.json).
func openIn(dir string) string            { return filepath.Join(dir, "tasks.json") }
func closedIn(dir string) string          { return filepath.Join(dir, "closed.json") }
func archiveIn(dir, period string) string { return filepath.Join(dir, "closed-"+period+".json") }

// OpenPath: the open tasks of owner's that are in no list (the Inbox's).
func (s *Store) OpenPath(owner string) string { return openIn(s.Dir(owner)) }

// ClosedPath, ArchivePath: the same of their closed ones: this month's, and
// a month's archive. PathIn: one of those files' names in the folder of
// owner's list.
func (s *Store) ClosedPath(owner string) string          { return closedIn(s.Dir(owner)) }
func (s *Store) ArchivePath(owner, period string) string { return archiveIn(s.Dir(owner), period) }
func (s *Store) PathIn(owner, list, file string) string {
	return filepath.Join(s.dirOf(owner, list), file)
}

// Archives: every archive of owner's, of every folder.
func (s *Store) Archives(owner string) []string { return s.archives(owner) }

// An archive's name: closed-YYYY-MM.json, a month's; closed-YYYY.json, a
// year's, from before the months (its tasks are filed by month at the next
// start). The year is the first group.
var archiveNameRE = regexp.MustCompile(`^closed-(\d{4})(-\d{2})?\.json$`)

// archiveYear: the year an archive file is of.
func archiveYear(path string) string { return archiveNameRE.FindStringSubmatch(filepath.Base(path))[1] }

// archivesIn: a folder's archives (closed-2026-03.json …, closed-2025.json), sorted.
func archivesIn(dir string) []string {
	entries, _ := os.ReadDir(dir)
	var out []string
	for _, e := range entries {
		if !e.IsDir() && archiveNameRE.MatchString(e.Name()) {
			out = append(out, filepath.Join(dir, e.Name()))
		}
	}
	sort.Strings(out)
	return out
}

// archives: every archive of owner's, of every folder.
func (s *Store) archives(owner string) []string {
	var out []string
	for _, d := range s.dirs(owner) {
		out = append(out, archivesIn(d)...)
	}
	return out
}

// File: a task file and whose it is ("" the shared ones).
type File struct{ Path, Owner string }

// Files: every task file of owners, of every folder: open, closed, archives.
func (s *Store) Files(owners []string) []File {
	var out []File
	for _, o := range owners {
		for _, d := range s.dirs(o) {
			for _, p := range append([]string{openIn(d), closedIn(d)}, archivesIn(d)...) {
				out = append(out, File{p, o})
			}
		}
	}
	return out
}

// unique: tasks without those whose id an earlier one has. One task in two
// files is a write that stopped between them (a task moves to another
// list's folder: its new file is written first, so nothing is lost); the
// first read is kept, the next write of these tasks drops the other.
func unique(tasks []*Task) []*Task {
	seen := make(map[string]bool, len(tasks))
	out := tasks[:0]
	for _, t := range tasks {
		if t.ID != "" && seen[t.ID] {
			continue
		}
		seen[t.ID] = true
		out = append(out, t)
	}
	return out
}

// openOf, closedOf, archivedOf: owner's open tasks, their closed ones of
// the current files, and their archived ones, of every folder. strict: for
// a write, where a file that is there but unreadable is ErrCorrupt.
func (s *Store) openOf(owner string, strict bool) ([]*Task, error) {
	tasks, err := s.readEach(owner, strict, func(dir string) []string { return []string{openIn(dir)} })
	// In the order they were made, as one file kept them (a new task is
	// its file's last): what shows tasks newest first goes by this order
	// where two were made in the same second.
	slices.SortStableFunc(tasks, func(a, b *Task) int { return cmp.Compare(a.CreatedAt, b.CreatedAt) })
	return tasks, err
}
func (s *Store) closedOf(owner string, strict bool) ([]*Task, error) {
	return s.readEach(owner, strict, func(dir string) []string { return []string{closedIn(dir)} })
}
func (s *Store) archivedOf(owner string, strict bool) ([]*Task, error) {
	return s.readEach(owner, strict, archivesIn)
}
func (s *Store) readEach(owner string, strict bool, files func(dir string) []string) ([]*Task, error) {
	tasks := []*Task{}
	for _, d := range s.dirs(owner) {
		for _, p := range files(d) {
			if !strict {
				tasks = append(tasks, ReadLenient(p)...)
				continue
			}
			l, err := ReadStrict(p)
			if err != nil {
				return nil, err
			}
			tasks = append(tasks, l...)
		}
	}
	return unique(tasks), nil
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
	ok    bool      // it held a list of tasks
	used  time.Time // when it was last read
}

// archiveKept: how long an archive (an earlier month's closed tasks) stays
// parsed after it was last read. The open tasks and this month's closed
// ones are read all the time; an archive is read by the Closed view of its
// year, a search's index and a change to an old task, and a wiki keeps
// years of them: parsed and kept, they are most of what Zone holds in
// memory (62 of 163 MB with 80,000 tasks, measured 2026-11-15).
const archiveKept = 10 * time.Minute

// forgetOldArchives drops the archives nobody read for archiveKept (with
// parsed locked; when a file is parsed, which is when the cache grows).
func forgetOldArchives(now time.Time) {
	for path, f := range parsed.files {
		if archiveNameRE.MatchString(filepath.Base(path)) && now.Sub(f.used) > archiveKept {
			delete(parsed.files, path)
		}
	}
}

var parsed = struct {
	sync.Mutex
	files map[string]parsedFile
}{files: map[string]parsedFile{}}

// readFile: path's tasks as copies of their own (a caller may change them);
// exists: there is such a file to read; ok: it holds a list of tasks.
func readFile(path string) (tasks []*Task, exists, ok bool) {
	kept, exists, ok := peekFile(path)
	if !ok {
		return nil, exists, false
	}
	out := make([]*Task, len(kept))
	for i, t := range kept {
		out[i] = t.Clone()
	}
	return out, true, true
}

// peekFile: path's tasks as the cache keeps them, for a reader that only
// looks: no copies, so nothing of them may be changed, and whose they are
// is not in them (Owner is ""). A write replaces a file's tasks, it never
// changes them, so they stay what they were for whoever still holds them.
func peekFile(path string) (tasks []*Task, exists, ok bool) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, !errors.Is(err, os.ErrNotExist), false
	}
	now := time.Now()
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
		forgetOldArchives(now)
	}
	f.used = now
	parsed.files[path] = f
	return f.tasks, true, f.ok
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
	changed(path)
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
		parsed.files[path] = parsedFile{info: info, tasks: kept, ok: true, used: time.Now()}
	}
	return nil
}

// forget: path is no longer the file that was parsed (it is gone).
func forget(path string) {
	parsed.Lock()
	delete(parsed.files, path)
	parsed.Unlock()
	changed(path)
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
// started, each under the folder it is in (a list's, an owner's _tasks/;
// a registry under its own path), so that a page can ask whether the tasks
// it holds are still what the server has (Word) without fetching them, and
// isn't told of a write to a list it doesn't show.
var changes = struct {
	sync.Mutex
	in map[string]int64
}{in: map[string]int64{}}

var started = time.Now().UnixNano()

// changed: a file at path was written or removed.
func changed(path string) {
	changes.Lock()
	changes.in[filepath.Dir(path)]++
	changes.Unlock()
}

// Place: tasks a view holds: owner's in one list ("" none: an Inbox), or
// with Every all of owner's.
type Place struct {
	Owner, List string
	Every       bool
}

// Word: a word that is another one after any write of a task file of
// places or of their owners' registries, and after a restart. Not after a
// file changed by hand, nor after a write to a list that isn't among them.
func (s *Store) Word(places []Place) string {
	changes.Lock()
	defer changes.Unlock()
	var n int64
	counted := map[string]bool{}
	add := func(dir string) {
		if !counted[dir] {
			counted[dir] = true
			n += changes.in[dir]
		}
	}
	for _, p := range places {
		base := s.Dir(p.Owner)
		add(base) // the owner's registry is there, and their tasks in no list
		if !p.Every {
			add(s.dirOf(p.Owner, p.List))
			continue
		}
		for dir := range changes.in {
			if strings.HasPrefix(dir, base+string(filepath.Separator)) {
				add(dir)
			}
		}
	}
	return strconv.FormatInt(started, 36) + "-" + strconv.FormatInt(n, 10)
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
		l, e := s.openOf(o, true)
		err = cmp.Or(err, e)
		return l
	})
	return tasks, err
}

// readOpen: the open tasks user sees (shared and their own).
func (s *Store) readOpen(user string) []*Task {
	return eachOwner(s.Owners(user), s.lenient(s.openOf))
}

// readClosed: the closed tasks user sees that are in closed.json: this
// month's (and, until the next write, an earlier month's).
func (s *Store) readClosed(user string) []*Task {
	return eachOwner(s.Owners(user), s.lenient(s.closedOf))
}

// readArchived: the archived tasks (earlier months') of owners.
func (s *Store) readArchived(owners []string) []*Task {
	return eachOwner(owners, s.lenient(s.archivedOf))
}

// lenient: read as a reader takes it: what can't be read is no task.
func (s *Store) lenient(read func(owner string, strict bool) ([]*Task, error)) func(string) []*Task {
	return func(owner string) []*Task {
		tasks, _ := read(owner, false)
		return tasks
	}
}

// OpenOf: owner's open tasks, read without the lock (the user commands,
// which have no running Zone).
func (s *Store) OpenOf(owner string) []*Task { return s.lenient(s.openOf)(owner) }

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
	return slices.Concat(eachOwner(owners, s.lenient(s.openOf)), eachOwner(owners, s.lenient(s.closedOf)),
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
		for _, p := range s.archives(o) {
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
		for _, p := range s.archives(o) {
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
	tasks, err := s.archivedOf(owner, true)
	if err != nil {
		return nil, err
	}
	l, err := s.closedOf(owner, true)
	if err != nil {
		return nil, err
	}
	return unique(append(tasks, l...)), nil
}

// readOwnerStrict: owner's open tasks and every closed one, read for a
// write (ErrCorrupt when a file is unreadable), marked as owner's.
func (s *Store) readOwnerStrict(owner string) (active, closed []*Task, err error) {
	if active, err = s.openOf(owner, true); err != nil {
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
	s.scope = nil
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

// ReadTaskForWriteLocked: what a write of the one task id needs: the open
// tasks user sees and, of the closed ones, those of the file that holds the
// task (none when it is open, or when no file has it). A change of a task
// then reads and copies its own file, not every closed task of the wiki's
// years; the write that follows (WriteBothLocked) writes whole only the
// closed files read here, and reads any other it has to put a task into
// (writeClosed).
func (s *Store) ReadTaskForWriteLocked(user, id string) (active, closed []*Task, err error) {
	// Closed tasks that wait to be filed (a month has ended, a year's file
	// from before the months): this write reads everything and files them,
	// as every write did; the next one reads its own file again.
	if s.unfiled(user) {
		return s.ReadForWriteLocked(user)
	}
	s.scope = map[string]bool{}
	if active, err = s.ReadOpenStrict(user); err != nil {
		return nil, nil, err
	}
	for _, t := range active {
		if t.ID == id {
			return active, []*Task{}, nil
		}
	}
	// A closed one: this month's files first, then the archives, the
	// newest first (what is changed is mostly what was closed lately).
	type file struct{ path, owner string }
	var current, archives []file
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(o) {
			current = append(current, file{closedIn(d), o})
			for _, p := range archivesIn(d) {
				archives = append(archives, file{p, o})
			}
		}
	}
	sort.SliceStable(archives, func(i, j int) bool { return filepath.Base(archives[i].path) > filepath.Base(archives[j].path) })
	for _, f := range append(current, archives...) {
		kept, _, ok := peekFile(f.path)
		if !ok || !slices.ContainsFunc(kept, func(t *Task) bool { return t.ID == id }) {
			continue
		}
		if closed, err = ReadStrict(f.path); err != nil {
			return nil, nil, err
		}
		for _, t := range closed {
			t.Owner = f.owner
		}
		s.scope[f.path] = true
		return active, closed, nil
	}
	return active, []*Task{}, nil
}

// unfiled: a closed task user sees is not in its month's file: a year's
// file from before the months is there, or a closed.json holds a task of
// an earlier month. Found by the files' names and a look at this month's
// files, which are parsed anyway.
func (s *Store) unfiled(user string) bool {
	current := s.currentMonth()
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(o) {
			for _, p := range archivesIn(d) {
				if !monthRE.MatchString(strings.TrimSuffix(strings.TrimPrefix(filepath.Base(p), "closed-"), ".json")) {
					return true // closed-YYYY.json
				}
			}
			old := false
			lookAt(closedIn(d), o, func(t *Task) { old = old || pastMonthOf(t, current) != "" })
			if old {
				return true
			}
		}
	}
	return false
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

// byDir: owner's tasks by the folder each is kept in (dirOf), with every
// folder of theirs that is there: one whose last task left has its file
// taken away. The folders in the order they are written: those that gain a
// task first (a list's new folder, a task moved there), so a write that
// stops halfway leaves a task in two files, never in none; then the others.
func (s *Store) byDir(owner string, tasks []*Task) (order []string, in map[string][]*Task) {
	in = map[string][]*Task{}
	have := s.dirs(owner)
	for _, d := range have {
		in[d] = []*Task{}
	}
	for _, t := range tasks {
		d := s.dirOf(owner, t.List)
		if _, ok := in[d]; !ok {
			order = append(order, d) // a folder that isn't there yet
		}
		in[d] = append(in[d], t)
	}
	return append(order, have...), in
}

// emptiesLast: the files (or folders) that keep tasks before those left
// without any, in their order otherwise.
func emptiesLast(order []string, tasks map[string][]*Task) {
	slices.SortStableFunc(order, func(a, b string) int {
		switch ea, eb := len(tasks[a]) == 0, len(tasks[b]) == 0; {
		case ea == eb:
			return 0
		case ea:
			return 1
		}
		return -1
	})
}

// writeOpenLocked writes the open tasks into their owners' files, each
// list's into its folder, those whose tasks changed (and the shared one of
// no list when it is missing); returns their paths.
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
		more, err := s.writeOpenOf(o, split[o], false, true)
		changed = append(changed, more...)
		if err != nil {
			return changed, err
		}
	}
	return changed, nil
}

// writeOpenOf writes owner's open tasks, each folder's file when its tasks
// changed (reformat: also when it is in another format); returns the paths
// written and removed. A list's file left without a task is removed, and
// its folder when nothing else is in it; the file of the tasks in no list
// stays, empty (create: the shared one is written even when it wasn't
// there, as a user's write does; not what only files tasks where they belong).
func (s *Store) writeOpenOf(owner string, tasks []*Task, reformat, create bool) ([]string, error) {
	var changed []string
	order, in := s.byDir(owner, tasks)
	// A list's emptied file goes last: a task that left it for another
	// folder is in that one by then.
	emptiesLast(order, in)
	for _, d := range order {
		p, own := openIn(d), d == s.Dir(owner)
		if !reformat && holds(p, in[d]) {
			continue
		}
		raw, rerr := os.ReadFile(p)
		if len(in[d]) == 0 && !own {
			if rerr == nil && os.Remove(p) == nil {
				forget(p)
				changed = append(changed, p)
			}
			_ = os.Remove(d) // when nothing else is in it
			continue
		}
		text, err := taskmodel.Encode(in[d])
		if err != nil {
			return changed, err
		}
		if rerr == nil && string(raw) == text || errors.Is(rerr, os.ErrNotExist) && (owner != "" || !create) && len(in[d]) == 0 {
			continue
		}
		if err := writeTasks(p, in[d], text); err != nil {
			return changed, err
		}
		changed = append(changed, p)
	}
	return changed, nil
}

// writeClosed writes owner's closed tasks, each list's in its folder, filed
// by month (this month's, and those without a usable date, into
// closed.json, earlier ones into closed-YYYY-MM.json); only files whose
// tasks change (with reformat also those in another format). An archive
// left without a task is removed: a year's file from before the months
// (closed-YYYY.json), whose tasks are in their months' files from the first
// write on; and so is a list's closed.json (that of the tasks in no list
// stays). Returns the paths written and removed, for the commit.
func (s *Store) writeClosed(owner string, closed []*Task, reformat bool) ([]string, error) {
	current := s.currentMonth()
	var order []string
	byPath := map[string][]*Task{}
	if s.scope == nil {
		for _, d := range s.dirs(owner) {
			for _, p := range append([]string{closedIn(d)}, archivesIn(d)...) {
				order = append(order, p)
				byPath[p] = []*Task{}
			}
		}
	} else {
		// A write that read some closed files (ReadTaskForWriteLocked):
		// those are the ones it writes whole; any other stays as it is.
		for p := range s.scope {
			if strings.HasPrefix(p, s.Dir(owner)+string(filepath.Separator)) {
				order = append(order, p)
				byPath[p] = []*Task{}
			}
		}
		sort.Strings(order)
	}
	var fresh []string // files that aren't there yet: written first (byDir)
	for _, t := range closed {
		d := s.dirOf(owner, t.List)
		p := closedIn(d)
		if m := pastMonthOf(t, current); m != "" {
			p = archiveIn(d, m)
		}
		if _, ok := byPath[p]; !ok {
			// A file this write didn't read takes a task (one just closed,
			// one filed under its month): what it holds is read now, so it
			// is never written without it.
			var there []*Task
			if s.scope != nil && exists(p) {
				var err error
				if there, err = ReadStrict(p); err != nil {
					return nil, err
				}
				for _, x := range there {
					x.Owner = owner
				}
			}
			if there != nil {
				order = append(order, p)
				byPath[p] = slices.DeleteFunc(there, func(x *Task) bool { return x.ID == t.ID })
			} else {
				fresh = append(fresh, p)
			}
		}
		byPath[p] = append(byPath[p], t)
	}
	order = append(fresh, order...)
	emptiesLast(order, byPath)
	keep := closedIn(s.Dir(owner))
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
		// A file that holds no task anymore goes: a year's archive, whose
		// tasks are filed by month now; a month's whose tasks were all
		// reopened; a list's closed.json.
		if err == nil && len(tasks) == 0 && p != keep {
			if os.Remove(p) == nil {
				forget(p)
				changed = append(changed, p)
			}
			_ = os.Remove(filepath.Dir(p)) // a list's folder, when nothing else is in it
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
	moved, renamed, filed := false, false, false
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
			moved = moved || pastMonthOf(t, s.currentMonth()) != "" && !exists(archiveIn(s.dirOf(o, t.List), pastMonthOf(t, s.currentMonth())))
		}
		// A task in another folder than its list's (a wiki from before the
		// folders: every task that is in a list): filed where it belongs.
		for _, f := range s.Files([]string{o}) {
			for _, t := range ReadLenient(f.Path) {
				filed = filed || filepath.Dir(f.Path) != s.dirOf(o, t.List)
			}
		}
		more, _ := s.writeOpenOf(o, active, true, false)
		changed = append(changed, more...)
		more, _ = s.writeClosed(o, closed, true)
		changed = append(changed, more...)
	}
	if len(changed) == 0 {
		return
	}
	msg := "Write the task files in Zone's format"
	if filed {
		msg = "File the tasks by list"
	}
	if moved {
		msg = "Archive closed tasks"
	}
	if renamed {
		msg = "Name the task files of closed tasks closed"
	}
	_ = s.Commit("", msg, changed...)
}

// FileByList files every owner's tasks that are in another folder than
// their list's where they belong, in one commit (at start, after what gave
// tasks another list in the files they were in: the lists' migrations).
func (s *Store) FileByList() {
	s.Lock()
	defer s.Unlock()
	if changed := s.FileByListLocked(s.EveryOwner()); len(changed) > 0 {
		_ = s.Commit("", "File the tasks by list", changed...)
	}
}

// FileByListLocked files every task of owners' that is in another folder
// than its list's where it belongs (after a write that put tasks back into
// the files they were read from with another list: a list renamed), and
// returns the paths written and removed, for that write's commit.
func (s *Store) FileByListLocked(owners []string) []string {
	var changed []string
	for _, o := range owners {
		active, closed, err := s.readOwnerStrict(o)
		if err != nil {
			continue
		}
		more, _ := s.writeOpenOf(o, active, false, false)
		changed = append(changed, more...)
		more, _ = s.writeClosed(o, closed, false)
		changed = append(changed, more...)
	}
	return changed
}

// RecentClosed: the closed tasks user sees of this month and the last (the
// current closed files and last month's archives): what a page view may
// read beside the open tasks (they are parsed once), where an older archive
// is never read for it.
//
// Those of them keep takes, as copies; keep looks at each without one
// (lookAt), so a page view copies the few it wants, not two months' tasks.
func (s *Store) RecentClosed(user string, keep func(*Task) bool) []*Task {
	last := "closed-" + s.Now().AddDate(0, 0, 1-s.Now().Day()).AddDate(0, -1, 0).Format("2006-01") + ".json"
	s.Lock()
	defer s.Unlock()
	out := []*Task{}
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(o) {
			files := []string{closedIn(d)}
			if p := filepath.Join(d, last); exists(p) {
				files = append(files, p)
			}
			for _, p := range files {
				lookAt(p, o, func(t *Task) {
					if keep(t) {
						c := t.Clone()
						out = append(out, c)
					}
				})
			}
		}
	}
	return unique(out)
}

// lookAt shows fn each task of the file at path, owner's, without copying
// what the cache keeps: a look-alike of the task whose lists and texts are
// the cache's own, so fn only reads it (and keeps none of it).
func lookAt(path, owner string, fn func(*Task)) {
	tasks, _, ok := peekFile(path)
	if !ok {
		return
	}
	for _, t := range tasks {
		shown := *t
		shown.Owner = owner
		fn(&shown)
	}
}

// CountClosedInYear: how many of the closed tasks of a year user sees in
// takes (the Closed link's number, asked for after every write): counted
// by looking, without a copy of a year's tasks.
func (s *Store) CountClosedInYear(user, year string, in func(*Task) bool) int {
	current := s.CurrentYear()
	s.Lock()
	defer s.Unlock()
	n := 0
	count := func(t *Task) {
		if (year == current && pastYearOf(t, current) == "" || year != current && ClosedYear(t) == year) && in(t) {
			n++
		}
	}
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(o) {
			lookAt(closedIn(d), o, count)
			for _, p := range archivesIn(d) {
				if archiveYear(p) == year {
					lookAt(p, o, count)
				}
			}
		}
	}
	return n
}

// Snapshot: the open tasks user sees and every closed one (this month's
// and the archives'), read together.
func (s *Store) Snapshot(user string) (open, done []*Task) {
	s.Lock()
	defer s.Unlock()
	return s.readOpen(user), append(s.readClosed(user), s.readArchived(s.Owners(user))...)
}
