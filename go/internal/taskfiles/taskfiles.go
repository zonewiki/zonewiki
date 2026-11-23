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
// moved by hand, a list renamed) is filed where it belongs by the next
// write of its owner's tasks, or at start (ArchiveOldClosed).
//
// A Store knows nothing of Zonewiki's process: the wiki's folder, its users,
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
	"zonewiki/internal/asker"
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
	Private func(user asker.Asker) bool
	// NoTasks: user sees no task at all (a public wiki's visitor); nil: no
	// such user. Every read asks (Owners), so nothing above it can forget to.
	NoTasks func(user asker.Asker) bool
	// MayWrite: nil when user may write the task files, else why not (a
	// viewer). Every write asks, so nothing above it can forget to.
	MayWrite func(user asker.Asker) error
	// GroupsDir: the folder of the groups' folders, in Root.
	GroupsDir string
	// Groups: the groups whose lists and tasks user reads and writes (they
	// are in them: the group's place is theirs); for Everyone every group.
	// nil: no groups. Every read and write of a user's asks, through Owners.
	Groups func(user asker.Asker) []string
	// Commit commits the files a write changed, by user ("": Zonewiki itself);
	// tasks: the ids of the tasks the write changed (Touched), for the
	// commit to name.
	Commit func(user, message string, tasks []string, paths ...string) error
	// Now: the clock (which year closed tasks belong to the archives).
	Now func() time.Time

	// mu: one write at a time, from its read to its commit (Lock).
	mu sync.Mutex
	// view: what readers see. A reader holds it shared for as long as it
	// reads (Open, All, Snapshot …); a write holds it alone from its first
	// file to its last (hold, release), so no reader sees a task between two
	// files, and lets it go before it commits: a reader waits for the files
	// of a write, never for git.
	view sync.RWMutex
	// locked: mu is held (a write is in progress); held: it holds view.
	locked, held bool
	// scope: the closed files the write in progress read
	// (ReadTasksForWriteLocked), which are the only ones it may write whole;
	// nil: it read every one (ReadForWriteLocked). Lives as long as the lock
	// is held.
	scope map[string]bool
	// touched: the tasks the write in progress changed (note, Touched).
	// Lives as long as the lock is held, or until the write's commit.
	touched touchedTasks
}

// touchedTasks: of each task a write's files held or hold, by id, what the
// first file that held it had (was) and what the file that holds it has now
// (now), and the ids in the order they came.
type touchedTasks struct {
	was, now map[string]*Task
	order    []string
}

// Lock serializes every read-modify-write of the task files: two concurrent
// task changes would otherwise lose each other's writes. A write holds it
// from its read to its commit; the methods named …Locked expect it held
// (Go's mutex isn't reentrant), the others take it themselves or only read.
// Readers don't take it: they wait only while a write replaces its files
// (view), not while it reads, decides or commits. So a section under Lock
// calls none of the reading methods that aren't named …Locked.
func (s *Store) Lock() { s.mu.Lock(); s.locked = true }
func (s *Store) Unlock() {
	s.Release()
	s.scope, s.locked, s.touched = nil, false, touchedTasks{}
	s.mu.Unlock()
}

// note: the write in progress is about to write tasks into the file at
// path, or remove it (tasks nil): what the file held and what it will hold
// are kept for Touched. What it held is what the store knows of it, which
// the write read (peekFile: no copy, nothing of it changed).
func (s *Store) note(path string, tasks []*Task) {
	t := &s.touched
	if t.was == nil {
		t.was, t.now = map[string]*Task{}, map[string]*Task{}
	}
	add := func(id string) {
		if _, ok := t.was[id]; !ok {
			if _, ok := t.now[id]; !ok {
				t.order = append(t.order, id)
			}
		}
	}
	if old, _, ok := peekFile(path); ok {
		for _, x := range old {
			add(x.ID)
			if _, ok := t.was[x.ID]; !ok {
				t.was[x.ID] = x
			}
		}
	}
	for _, x := range tasks {
		add(x.ID)
		t.now[x.ID] = x
	}
}

// Touched: the ids of the tasks the write in progress changed so far, in
// the order it came to them, and forgets them (for its commit): a task it
// made, deleted or changed (Same), wherever it is kept now; not one that
// only moved between files (a month's archive) or whose file was written
// for another's sake.
func (s *Store) Touched() []string {
	t := s.touched
	s.touched = touchedTasks{}
	var out []string
	for _, id := range t.order {
		was, wok := t.was[id]
		now, nok := t.now[id]
		if !wok || !nok || !now.Same(was) {
			out = append(out, id)
		}
	}
	return out
}

// Hold: the write in progress (under Lock) is about to change task files or
// registries: readers wait from here until Release, so that they see every
// file of the write or none. Every write of the store's takes it itself;
// code that writes a task file on its own (taskfiles.Write) calls it first.
// Without Lock (a command without a running Zonewiki, a test) there is no write
// in progress to hold it for.
func (s *Store) Hold() {
	if s.locked && !s.held {
		s.view.Lock()
		s.held = true
	}
}

// Release: the files of the write in progress (under Lock) are written:
// readers go on. Before a commit, which no reader has to wait for; Unlock
// does it at the latest.
func (s *Store) Release() {
	if s.held {
		s.held = false
		s.view.Unlock()
	}
}

// ---- where the files are ----

// Dir: the folder of owner's task files: _tasks/ for the shared tasks
// (owner ""), private/<owner>/_tasks/ for a user's private ones,
// groups/<group>/_tasks/ for a group's (owner "@<group>", GroupOwner).
// Where a task is kept is who sees it, as for a page: the place's folder.
func (s *Store) Dir(owner string) string {
	base := s.Root()
	if g, ok := GroupOf(owner); ok {
		base = filepath.Join(base, s.GroupsDir, g)
	} else if owner != "" {
		base = filepath.Join(base, s.PrivateDir, owner)
	}
	return filepath.Join(base, DirName)
}

// FileOwner: whose task file rel is (slash-separated, relative to Root, as
// git names it): a file of open or closed tasks (tasks.json, closed.json,
// closed-YYYY-MM.json) in an owner's _tasks/ folder or in
// a list's folder there; "" the shared ones, a user's name, "@<group>" a
// group's (Dir's places). False for any other file: a registry
// (lists.json), a task's files, a page.
func (s *Store) FileOwner(rel string) (owner string, ok bool) {
	parts := strings.Split(rel, "/")
	name, dir := parts[len(parts)-1], parts[:len(parts)-1]
	if name != "tasks.json" && name != "closed.json" && !archiveNameRE.MatchString(name) {
		return "", false
	}
	if n := len(dir); n >= 3 && dir[n-2] == ListsDirName && dir[n-3] == DirName && listDirRE.MatchString(dir[n-1]) {
		dir = dir[:n-2]
	}
	switch {
	case len(dir) == 1 && dir[0] == DirName:
		return "", true
	case len(dir) == 3 && dir[2] == DirName && dir[0] == s.PrivateDir && dir[1] != "" && !strings.HasPrefix(dir[1], "@"):
		return dir[1], true
	case len(dir) == 3 && dir[2] == DirName && dir[0] == s.GroupsDir && s.GroupsDir != "" && dir[1] != "":
		return GroupOwner(dir[1]), true
	}
	return "", false
}

// GroupOwner: a group's lists and tasks as an owner: "@dev" (no user's name
// starts with @).
func GroupOwner(group string) string { return "@" + group }

// GroupOf: the group whose owner name this is ("@dev": dev), false for the
// shared files and a user's.
func GroupOf(owner string) (string, bool) {
	if g, ok := strings.CutPrefix(owner, "@"); ok && g != "" {
		return g, true
	}
	return "", false
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

// Everyone: who reads every folder (dirs): Zonewiki itself, for what is done
// over all the tasks (a start's filing, a rename's links, the search index,
// which filters by user later). No user's name: a name has no NUL.
var Everyone = asker.Asker{Name: "\x00everyone"}

// dirs: the folders that hold task files of owner's: their Dir, then each
// list's that is there, A–Z. **Who sees a task is decided by where it is
// kept**: whose files a user reads and writes is Owners, so a write never
// touches the files of a place its user doesn't see.
func (s *Store) dirs(who asker.Asker, owner string) []string {
	out := []string{s.Dir(owner)}
	entries, _ := os.ReadDir(filepath.Join(s.Dir(owner), ListsDirName))
	for _, e := range entries {
		dir := filepath.Join(s.Dir(owner), ListsDirName, e.Name())
		if e.IsDir() {
			out = append(out, dir)
		}
	}
	return out
}

// The files of one folder: its open tasks, this month's closed ones (and
// those without a usable date), and an archive of a month ("2026-03":
// closed-2026-03.json).
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
func (s *Store) Archives(owner string) []string { return s.archives(Everyone, owner) }

// An archive's name: closed-YYYY-MM.json, a month's. The year is the
// first group.
var archiveNameRE = regexp.MustCompile(`^closed-(\d{4})-\d{2}\.json$`)

// archiveYear: the year an archive file is of.
func archiveYear(path string) string { return archiveNameRE.FindStringSubmatch(filepath.Base(path))[1] }

// archivesIn: a folder's archives (closed-2026-03.json …), sorted.
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
func (s *Store) archives(who asker.Asker, owner string) []string {
	var out []string
	for _, d := range s.dirs(who, owner) {
		out = append(out, archivesIn(d)...)
	}
	return out
}

// File: a task file and whose it is ("" the shared ones).
type File struct{ Path, Owner string }

// Files: every task file of owners, of every folder: open, closed,
// archives.
func (s *Store) Files(owners []string) []File {
	var out []File
	for _, o := range owners {
		for _, d := range s.dirs(Everyone, o) {
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
func (s *Store) openOf(who asker.Asker, owner string, strict bool) ([]*Task, error) {
	tasks, err := s.readEach(who, owner, strict, func(dir string) []string { return []string{openIn(dir)} })
	// In the order they were made, as one file kept them (a new task is
	// its file's last): what shows tasks newest first goes by this order
	// where two were made in the same second.
	slices.SortStableFunc(tasks, func(a, b *Task) int { return cmp.Compare(a.CreatedAt, b.CreatedAt) })
	return tasks, err
}
func (s *Store) closedOf(who asker.Asker, owner string, strict bool) ([]*Task, error) {
	return s.readEach(who, owner, strict, func(dir string) []string { return []string{closedIn(dir)} })
}
func (s *Store) archivedOf(who asker.Asker, owner string, strict bool) ([]*Task, error) {
	return s.readEach(who, owner, strict, archivesIn)
}
func (s *Store) readEach(who asker.Asker, owner string, strict bool, files func(dir string) []string) ([]*Task, error) {
	return s.readFiles(who, owner, strict, files)
}

// readFiles: the tasks of owner's files that files names in each folder
// (dirs), each once; strict: a file that is there but unreadable is
// ErrCorrupt. Every task in a folder of owner's is seen by who sees owner's
// place: who sees a task is where it is kept.
func (s *Store) readFiles(who asker.Asker, owner string, strict bool, files func(dir string) []string) ([]*Task, error) {
	tasks := []*Task{}
	for _, d := range s.dirs(who, owner) {
		for _, p := range files(d) {
			var l []*Task
			if strict {
				var err error
				if l, err = ReadStrict(p); err != nil {
					return nil, err
				}
			} else {
				l = ReadLenient(p)
			}
			tasks = append(tasks, l...)
		}
	}
	return unique(tasks), nil
}

// ---- whose files ----

// Owners: whose task files user reads and writes: the shared ones and,
// with users, their own and those of the groups they are in.
func (s *Store) Owners(user asker.Asker) []string {
	if s.NoTasks != nil && s.NoTasks(user) {
		return nil
	}
	out := []string{""}
	if s.Private(user) {
		out = append(out, user.Name)
	}
	return append(out, s.groupOwners(user)...)
}

// groupOwners: the owner names of the groups user reads and writes.
func (s *Store) groupOwners(user asker.Asker) []string {
	if s.Groups == nil {
		return nil
	}
	var out []string
	for _, g := range s.Groups(user) {
		out = append(out, GroupOwner(g))
	}
	return out
}

// EveryOwner: the owners of every task list, for what is done over all of
// them (the yearly archive, a rename's links, the search index).
func (s *Store) EveryOwner() []string {
	return append(append([]string{""}, s.Users()...), s.groupOwners(Everyone)...)
}

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
// is parsed once and handed out as copies until it changes. Zonewiki is one
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
// years of them: parsed and kept, they are most of what Zonewiki holds in
// memory (62 of 163 MB with 80,000 tasks, measured 2026-11-15).
const archiveKept = 10 * time.Minute

// parses counts the task files parsed (Parsed): what a test and the bench
// ask to see that something didn't read the wiki's task files.
var parses atomic.Int64

// Parsed: how many task files were parsed since Zonewiki started.
func Parsed() int64 { return parses.Load() }

// ForgetParsed: nothing is kept parsed, as when Zonewiki starts.
func ForgetParsed() {
	parsed.Lock()
	clear(parsed.files)
	parsed.Unlock()
}

// KeepsParsed: the task file at path is kept parsed (it was read, and not
// forgotten since).
func KeepsParsed(path string) bool {
	parsed.Lock()
	defer parsed.Unlock()
	_, ok := parsed.files[path]
	return ok
}

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
		parses.Add(1)
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

// changes counts the writes of task files and registries since Zonewiki
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
// file changed by hand, nor after a write to a list that isn't among them,
// nor after one to a place they don't see: their browser has nothing to load.
func (s *Store) Word(who asker.Asker, places []Place) string {
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

// Stamp: a word that is another one when a file changed that the counts of
// user's tasks are read from: the registries they read, the open tasks,
// this month's closed ones and last month's archives (RecentClosed), of
// every folder. By the files' times and sizes, so a change by hand counts
// as one of Zonewiki's does. It costs a look at each file, not a read.
func (s *Store) Stamp(user asker.Asker) string {
	last := s.Now().AddDate(0, -1, 0).Format("2006-01")
	var b strings.Builder
	add := func(path string) {
		if info, err := os.Stat(path); err == nil {
			b.WriteString(strconv.FormatInt(info.ModTime().UnixNano(), 36))
			b.WriteByte(':')
			b.WriteString(strconv.FormatInt(info.Size(), 36))
		}
		b.WriteByte(' ')
	}
	var places []Place
	for _, o := range s.Owners(user) {
		places = append(places, Place{Owner: o, Every: true})
		add(s.ListsPath(o))
		for _, d := range s.dirs(user, o) {
			b.WriteString(filepath.Base(d))
			b.WriteByte('=')
			add(openIn(d))
			add(closedIn(d))
			add(archiveIn(d, last))
		}
	}
	// Zonewiki's own writes by their count too (Word): two of them within one
	// tick of the file system's clock that leave a file its size are two.
	return b.String() + s.Word(user, places)
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
func (s *Store) ReadOpenStrict(user asker.Asker) ([]*Task, error) {
	var err error
	tasks := eachOwner(s.Owners(user), func(o string) []*Task {
		l, e := s.openOf(user, o, true)
		err = cmp.Or(err, e)
		return l
	})
	return tasks, err
}

// readOpen: the open tasks user sees (shared and their own).
func (s *Store) readOpen(user asker.Asker) []*Task {
	return eachOwner(s.Owners(user), s.lenient(user, s.openOf))
}

// readClosed: the closed tasks user sees that are in closed.json: this
// month's (and, until the next write, an earlier month's).
func (s *Store) readClosed(user asker.Asker) []*Task {
	return eachOwner(s.Owners(user), s.lenient(user, s.closedOf))
}

// readArchived: the archived tasks (earlier months') of owners, as who sees them.
func (s *Store) readArchived(who asker.Asker, owners []string) []*Task {
	return eachOwner(owners, s.lenient(who, s.archivedOf))
}

// lenient: read as a reader takes it: what can't be read is no task.
func (s *Store) lenient(who asker.Asker, read func(who asker.Asker, owner string, strict bool) ([]*Task, error)) func(string) []*Task {
	return func(owner string) []*Task {
		tasks, _ := read(who, owner, false)
		return tasks
	}
}

// OpenOf: owner's open tasks, read without the lock (the user commands,
// which have no running Zonewiki).
func (s *Store) OpenOf(owner string) []*Task { return s.lenient(Everyone, s.openOf)(owner) }

// Open: the open tasks user sees, under the lock: a task made private or
// shared moves between two files, and a read between their writes would
// miss it.
func (s *Store) Open(user asker.Asker) []*Task {
	s.view.RLock()
	defer s.view.RUnlock()
	return s.readOpen(user)
}

// All: every task user sees, archived ones too, one snapshot.
func (s *Store) All(user asker.Asker) []*Task {
	s.view.RLock()
	defer s.view.RUnlock()
	return slices.Concat(s.readOpen(user), s.readClosed(user), s.readArchived(user, s.Owners(user)))
}

// Every: every task of every owner (Task.Owner says whose), for what keeps
// all of them and filters by user later (the search index).
func (s *Store) Every() []*Task {
	s.view.RLock()
	defer s.view.RUnlock()
	return s.EveryLocked()
}

func (s *Store) EveryLocked() []*Task {
	owners := s.EveryOwner()
	return slices.Concat(eachOwner(owners, s.lenient(Everyone, s.openOf)), eachOwner(owners, s.lenient(Everyone, s.closedOf)),
		s.readArchived(Everyone, owners))
}

// Find: the task id among every owner's (Task.Owner says whose; the caller
// asks who may see it), nil for none: the open and this month's closed ones
// first, the archives only for one that isn't there. Without one of that id
// as it is, one whose id is it in another case: a folder named by the id is
// that task's on a file system that ignores case (macOS, Windows), so who
// asks by it is asked about that task.
func (s *Store) Find(id string) *Task {
	s.view.RLock()
	defer s.view.RUnlock()
	owners := s.EveryOwner()
	var folded *Task
	for _, read := range []func() []*Task{
		func() []*Task { return eachOwner(owners, s.lenient(Everyone, s.openOf)) },
		func() []*Task { return eachOwner(owners, s.lenient(Everyone, s.closedOf)) },
		func() []*Task { return s.readArchived(Everyone, owners) },
	} {
		for _, t := range read() {
			if t.ID == id {
				return t
			}
			if folded == nil && strings.EqualFold(t.ID, id) {
				folded = t
			}
		}
	}
	return folded
}

// IDsLocked: the ids of every owner's tasks, in lower case, so a restored
// or imported task never takes one another file has, in any case (their
// files' folders would be one on a file system that ignores case): look an
// id up in lower case.
func (s *Store) IDsLocked() map[string]bool {
	ids := map[string]bool{}
	for _, t := range s.EveryLocked() {
		ids[strings.ToLower(t.ID)] = true
	}
	return ids
}

// ClosedInYear: the Closed view of one year, of the tasks user sees
// (this year's view takes tasks without a usable date too).
func (s *Store) ClosedInYear(user asker.Asker, year string) []*Task {
	current := s.CurrentYear()
	s.view.RLock()
	// The current file (this month's, and what has no usable date) and the
	// year's archives.
	tasks := s.readClosed(user)
	tasks = append(tasks, eachOwner(s.Owners(user), func(o string) []*Task {
		l, _ := s.readFiles(user, o, false, func(dir string) []string {
			return slices.DeleteFunc(archivesIn(dir), func(p string) bool { return archiveYear(p) != year })
		})
		return l
	})...)
	s.view.RUnlock()
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
func (s *Store) ClosedYears(user asker.Asker, in func(*Task) bool) []string {
	current := s.CurrentYear()
	years := map[string]bool{current: true}
	s.view.RLock()
	for _, t := range s.readClosed(user) {
		if in(t) && pastYearOf(t, current) != "" {
			years[ClosedYear(t)] = true
		}
	}
	for _, o := range s.Owners(user) {
		for _, p := range s.archives(user, o) {
			for _, t := range ReadLenient(p) {
				if t.Owner = o; in(t) {
					years[archiveYear(p)] = true
					break
				}
			}
		}
	}
	s.view.RUnlock()
	out := slices.Sorted(maps.Keys(years))
	slices.Reverse(out)
	return out
}

// ---- reading for a write ----

// readClosedAllStrict: every closed task of owner's, the archives
// first, for a write (writeClosed files each by month again).
func (s *Store) readClosedAllStrict(who asker.Asker, owner string) ([]*Task, error) {
	tasks, err := s.archivedOf(who, owner, true)
	if err != nil {
		return nil, err
	}
	l, err := s.closedOf(who, owner, true)
	if err != nil {
		return nil, err
	}
	return unique(append(tasks, l...)), nil
}

// readOwnerStrict: owner's open tasks and every closed one, read for a
// write (ErrCorrupt when a file is unreadable), marked as owner's.
func (s *Store) readOwnerStrict(who asker.Asker, owner string) (active, closed []*Task, err error) {
	if active, err = s.openOf(who, owner, true); err != nil {
		return nil, nil, err
	}
	if closed, err = s.readClosedAllStrict(who, owner); err != nil {
		return nil, nil, err
	}
	for _, t := range slices.Concat(active, closed) {
		t.Owner = owner
	}
	return active, closed, nil
}

// ReadForWriteLocked: the open tasks and every closed task user sees
// (shared and their own), read for a write.
func (s *Store) ReadForWriteLocked(user asker.Asker) (active, closed []*Task, err error) {
	s.scope = nil
	active, closed = []*Task{}, []*Task{}
	for _, o := range s.Owners(user) {
		a, c, err := s.readOwnerStrict(user, o)
		if err != nil {
			return nil, nil, err
		}
		active, closed = append(active, a...), append(closed, c...)
	}
	return active, closed, nil
}

// ReadTaskForWriteLocked: what a write of the one task id needs
// (ReadTasksForWriteLocked with that one id).
func (s *Store) ReadTaskForWriteLocked(user asker.Asker, id string) (active, closed []*Task, err error) {
	return s.ReadTasksForWriteLocked(user, map[string]bool{id: true})
}

// ReadTasksForWriteLocked: what a write of the tasks ids needs: the open
// tasks user sees and, of the closed ones, those of the files that hold one
// of the tasks (none when they are all open, or when no file has them). A
// change of tasks then reads and copies their own files, not every closed
// task of the wiki's years; the write that follows (WriteBothLocked) writes
// whole only the closed files read here, and reads any other it has to put
// a task into (writeClosed).
func (s *Store) ReadTasksForWriteLocked(user asker.Asker, ids map[string]bool) (active, closed []*Task, err error) {
	// Closed tasks that wait to be filed (a month has ended): this write
	// reads everything and files them,
	// as every write did; the next one reads its own files again.
	if s.unfiled(user) {
		return s.ReadForWriteLocked(user)
	}
	s.scope = map[string]bool{}
	if active, err = s.ReadOpenStrict(user); err != nil {
		return nil, nil, err
	}
	closed = []*Task{}
	found := map[string]bool{} // an id counts once, wherever else it is too
	for _, t := range active {
		if ids[t.ID] {
			found[t.ID] = true
		}
	}
	if len(found) >= len(ids) {
		return active, closed, nil
	}
	// Closed ones: this month's files first, then the archives, the
	// newest first (what is changed is mostly what was closed lately),
	// until each task is found.
	type file struct{ path, owner string }
	var current, archives []file
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(user, o) {
			current = append(current, file{closedIn(d), o})
			for _, p := range archivesIn(d) {
				archives = append(archives, file{p, o})
			}
		}
	}
	sort.SliceStable(archives, func(i, j int) bool { return filepath.Base(archives[i].path) > filepath.Base(archives[j].path) })
	for _, f := range append(current, archives...) {
		if len(found) >= len(ids) {
			break
		}
		kept, _, ok := peekFile(f.path)
		if !ok || !slices.ContainsFunc(kept, func(t *Task) bool { return ids[t.ID] && !found[t.ID] }) {
			continue
		}
		tasks, err := ReadStrict(f.path)
		if err != nil {
			return nil, nil, err
		}
		for _, t := range tasks {
			t.Owner = f.owner
			if ids[t.ID] {
				found[t.ID] = true
			}
		}
		closed = append(closed, tasks...)
		s.scope[f.path] = true
	}
	return active, closed, nil
}

// unfiled: a closed task user sees is not in its month's file: a
// closed.json holds a task of an earlier month. Found by a look at this
// month's files, which are parsed anyway.
func (s *Store) unfiled(user asker.Asker) bool { return s.unfiledOf(user, s.Owners(user)) }

// Unfiled: any owner's closed tasks wait to be filed (unfiled): what a
// start has to do something about (ArchiveOldClosed), found without
// reading the archives.
func (s *Store) Unfiled() bool { return s.unfiledOf(Everyone, s.EveryOwner()) }

func (s *Store) unfiledOf(who asker.Asker, owners []string) bool {
	current := s.currentMonth()
	for _, o := range owners {
		for _, d := range s.dirs(who, o) {
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
func (s *Store) byOwner(user asker.Asker, tasks []*Task) (map[string][]*Task, error) {
	out := map[string][]*Task{}
	for _, o := range s.Owners(user) {
		out[o] = []*Task{}
	}
	for _, t := range tasks {
		if _, ok := out[t.Owner]; !ok {
			return nil, errors.New("tasks: a task of " + t.Owner + "'s in a write by " + user.Name)
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
func (s *Store) byDir(who asker.Asker, owner string, tasks []*Task) (order []string, in map[string][]*Task) {
	in = map[string][]*Task{}
	have := s.dirs(who, owner)
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
func (s *Store) writeOpenLocked(user asker.Asker, tasks []*Task) ([]string, error) {
	if err := s.MayWrite(user); err != nil {
		return nil, err
	}
	split, err := s.byOwner(user, tasks)
	if err != nil {
		return nil, err
	}
	var changed []string
	for _, o := range s.Owners(user) {
		more, err := s.writeOpenOf(user, o, split[o], false, true)
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
func (s *Store) writeOpenOf(who asker.Asker, owner string, tasks []*Task, reformat, create bool) ([]string, error) {
	s.Hold()
	var changed []string
	order, in := s.byDir(who, owner, tasks)
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
			if rerr == nil {
				s.note(p, nil)
			}
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
		s.note(p, in[d])
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
// left without a task is removed, and so is a list's closed.json (that of
// the tasks in no list stays). Returns the paths written and removed, for the commit.
func (s *Store) writeClosed(who asker.Asker, owner string, closed []*Task, reformat bool) ([]string, error) {
	s.Hold()
	current := s.currentMonth()
	var order []string
	byPath := map[string][]*Task{}
	if s.scope == nil {
		for _, d := range s.dirs(who, owner) {
			for _, p := range append([]string{closedIn(d)}, archivesIn(d)...) {
				order = append(order, p)
				byPath[p] = []*Task{}
			}
		}
	} else {
		// A write that read some closed files (ReadTasksForWriteLocked):
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
		// A file that holds no task anymore goes: a month's whose tasks were
		// all reopened; a list's closed.json.
		if err == nil && len(tasks) == 0 && p != keep {
			s.note(p, nil)
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
		s.note(p, tasks)
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

// CommitFiles commits the task files a write changed, by user, naming the
// tasks it changed (Touched).
func (s *Store) CommitFiles(user asker.Asker, message string, changed []string) {
	s.Release()
	touched := s.Touched()
	if len(changed) > 0 {
		_ = s.Commit(user.Name, message, touched, changed...)
	}
}

// WriteOpenLocked writes the open tasks (each into its owner's file),
// committed by user; also: files written with them (a list's registry)
// that belong in that commit.
func (s *Store) WriteOpenLocked(user asker.Asker, tasks []*Task, message string, also ...string) error {
	changed, err := s.writeOpenLocked(user, tasks)
	s.CommitFiles(user, message, append(changed, also...))
	return err
}

// WriteBothLocked writes the open tasks and every closed task (filed by
// owner and month) in one commit, by user; also: files written with them
// (the lists' registries) that belong in that commit.
func (s *Store) WriteBothLocked(user asker.Asker, active, closed []*Task, message string, also ...string) error {
	if err := s.MayWrite(user); err != nil {
		return err
	}
	changed, err := s.writeOpenLocked(user, active)
	if err == nil {
		var split map[string][]*Task
		if split, err = s.byOwner(user, closed); err == nil {
			for _, o := range s.Owners(user) {
				more, werr := s.writeClosed(user, o, split[o], false)
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

// ArchiveOldClosed rolls earlier months' closed tasks into their
// archives (at start; every write files by month too), files a task found
// in another folder than its list's there, and writes files in another
// format (a hand-edited file) as Zonewiki writes them, so later commits show
// only what changed; every owner's files, in one commit. An unreadable
// file is left alone (its owner's files are): this is never worth failing
// over.
func (s *Store) ArchiveOldClosed() {
	s.Lock()
	defer s.Unlock()
	moved := false
	var changed []string
	for _, o := range s.EveryOwner() {
		active, closed, err := s.readOwnerStrict(Everyone, o)
		if err != nil {
			continue
		}
		for _, t := range closed {
			moved = moved || pastMonthOf(t, s.currentMonth()) != "" && !exists(archiveIn(s.dirOf(o, t.List), pastMonthOf(t, s.currentMonth())))
		}
		more, _ := s.writeOpenOf(Everyone, o, active, true, false)
		changed = append(changed, more...)
		more, _ = s.writeClosed(Everyone, o, closed, true)
		changed = append(changed, more...)
	}
	if len(changed) == 0 {
		return
	}
	msg := "Write the task files in Zonewiki's format"
	if moved {
		msg = "Archive closed tasks"
	}
	_ = s.Commit("", msg, s.Touched(), changed...)
}

// FileByListLocked files every task of owners' that is in another folder
// than its list's where it belongs (after a write that put tasks back into
// the files they were read from with another list: a list renamed, the
// shared tasks a start put in #general), and
// returns the paths written and removed, for that write's commit.
func (s *Store) FileByListLocked(owners []string) []string {
	s.Hold()
	var changed []string
	for _, o := range owners {
		active, closed, err := s.readOwnerStrict(Everyone, o)
		if err != nil {
			continue
		}
		more, _ := s.writeOpenOf(Everyone, o, active, false, false)
		changed = append(changed, more...)
		more, _ = s.writeClosed(Everyone, o, closed, false)
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
func (s *Store) RecentClosed(user asker.Asker, keep func(*Task) bool) []*Task {
	last := "closed-" + s.Now().AddDate(0, 0, 1-s.Now().Day()).AddDate(0, -1, 0).Format("2006-01") + ".json"
	s.view.RLock()
	defer s.view.RUnlock()
	out := []*Task{}
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(user, o) {
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

// ClosedPage: the Closed view of one year as it shows it: of the closed
// tasks user sees that keep takes, the latest closed first, the first limit
// (and as many more as it takes to have the task must among them, "" for
// none), as copies; and how many there are in all. keep looks at each task
// without a copy (lookAt), so a year's closed tasks are looked through and
// a page of them is copied: the view of a team's year is tens of megabytes
// of tasks, and it shows fifty.
func (s *Store) ClosedPage(user asker.Asker, year string, keep func(*Task) bool, limit int, must string) (page []*Task, total int) {
	current := s.CurrentYear()
	type found struct{ closedAt, id, path, owner string }
	var all []found
	s.view.RLock()
	defer s.view.RUnlock()
	for _, o := range s.Owners(user) {
		for _, d := range s.dirs(user, o) {
			files := []string{closedIn(d)}
			for _, p := range archivesIn(d) {
				if archiveYear(p) == year {
					files = append(files, p)
				}
			}
			for _, p := range files {
				lookAt(p, o, func(t *Task) {
					if (year == current && pastYearOf(t, current) == "" || year != current && ClosedYear(t) == year) && keep(t) {
						all = append(all, found{t.ClosedAt, t.ID, p, o})
					}
				})
			}
		}
	}
	sort.SliceStable(all, func(i, j int) bool {
		return cmp.Or(cmp.Compare(all[j].closedAt, all[i].closedAt), cmp.Compare(all[i].id, all[j].id)) < 0
	})
	seen := map[string]bool{}
	unique := all[:0]
	for _, f := range all {
		if !seen[f.id] {
			seen[f.id] = true
			unique = append(unique, f)
		}
	}
	all = unique
	if must != "" {
		if i := slices.IndexFunc(all, func(f found) bool { return f.id == must }); i >= limit {
			limit = i + 1
		}
	}
	page = []*Task{}
	for _, f := range all[:min(limit, len(all))] {
		if t := TaskIn(f.path, f.id, f.owner); t != nil {
			page = append(page, t)
		}
	}
	return page, len(all)
}

// Look shows fn each task of the task file at path, owner's, without a copy
// (lookAt): for who keeps something of each and not the task (the search's
// records). False when the file is not there or can't be read.
func Look(path, owner string, fn func(*Task)) bool {
	_, _, ok := peekFile(path)
	if ok {
		lookAt(path, owner, fn)
	}
	return ok
}

// TaskIn: the task id of the task file at path, owner's, as a copy; nil
// when the file doesn't hold it (any more).
func TaskIn(path, id, owner string) *Task {
	tasks, _, ok := peekFile(path)
	if !ok {
		return nil
	}
	for _, t := range tasks {
		if t.ID == id {
			c := t.Clone()
			c.Owner = owner
			return c
		}
	}
	return nil
}

// IsArchive: path is an archive, an earlier month's closed tasks: a file
// that rarely changes.
func IsArchive(path string) bool { return archiveNameRE.MatchString(filepath.Base(path)) }

// CountClosedInYear: how many of the closed tasks of a year user sees in
// takes (the Closed link's number, asked for after every write): counted
// by looking, without a copy of a year's tasks.
func (s *Store) CountClosedInYear(user asker.Asker, year string, in func(*Task) bool) int {
	current := s.CurrentYear()
	s.view.RLock()
	defer s.view.RUnlock()
	n := 0
	for _, o := range s.Owners(user) {
		count := func(t *Task) {
			if (year == current && pastYearOf(t, current) == "" || year != current && ClosedYear(t) == year) && in(t) {
				n++
			}
		}
		for _, d := range s.dirs(user, o) {
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

// ReadAllStrict: the open tasks user sees and every closed one (this
// month's and the archives'), read together as a reader takes them (what
// user doesn't see left out: shows), but strictly: a file that is there and
// can't be read is ErrCorrupt, so a copy of them (an export) never lacks a
// file's tasks without a word. Under the readers' lock, not the write lock
// (Lock): writes go on deciding meanwhile.
func (s *Store) ReadAllStrict(user asker.Asker) (open, closed []*Task, err error) {
	s.view.RLock()
	defer s.view.RUnlock()
	open, closed = []*Task{}, []*Task{}
	for _, o := range s.Owners(user) {
		a, err := s.readFiles(user, o, true, func(dir string) []string { return []string{openIn(dir)} })
		if err != nil {
			return nil, nil, err
		}
		c, err := s.readFiles(user, o, true, func(dir string) []string { return append(archivesIn(dir), closedIn(dir)) })
		if err != nil {
			return nil, nil, err
		}
		for _, t := range slices.Concat(a, c) {
			t.Owner = o
		}
		open, closed = append(open, a...), append(closed, c...)
	}
	return open, closed, nil
}
