// Tasks on disk and their model: the _deck/ files
// (open tasks, completed, yearly archives; with users each user's private
// ones in private/<user>/_deck/) under one lock with atomic writes,
// recurrence, history, comments, and what pages need from tasks (links to
// pages, [[task:…]] references). A task is a *Task (task.go).
package main

import (
	"cmp"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
)

// tasksMu serializes every read-modify-write of the task files: two
// concurrent task changes would otherwise lose each other's writes. Write
// paths hold it for the whole read-modify-write-commit; functions named
// …Locked expect it held (Go's mutex isn't reentrant, Python's RLock was).
var tasksMu sync.Mutex

const tasksDirName = "_deck"

// tasksDir: the folder of owner's tasks files: _deck/ for the shared tasks
// (owner ""), private/<owner>/_deck/ for a user's private ones: private
// because it lies in their folder, like their pages. The files in each are
// the same: tasks.json, completed.json, completed-YYYY.json.
func tasksDir(owner string) string {
	if owner == "" {
		return filepath.Join(cfg.Data, tasksDirName)
	}
	return filepath.Join(cfg.Data, privateDir, owner, tasksDirName)
}

func tasksFile(owner string) string      { return filepath.Join(tasksDir(owner), "tasks.json") }
func completedPath(owner string) string { return filepath.Join(tasksDir(owner), "completed.json") }
func archivePath(owner, year string) string {
	return filepath.Join(tasksDir(owner), "completed-"+year+".json")
}

// taskOwners: whose tasks files user reads and writes: the shared ones and,
// with users, their own.
func taskOwners(user string) []string {
	if privateFolder(user) == "" {
		return []string{""}
	}
	return []string{"", user}
}

// everyOwner: the owners of every task list, for what Zone does over all of
// them (the yearly archive, a rename's links, the search index).
func everyOwner() []string { return append([]string{""}, sortedKeys(settings().Users)...) }

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

// errTasksCorrupt: a task list file is there but unreadable; a write refuses
// rather than overwriting it with a partial list.
var errTasksCorrupt = errors.New("task storage temporarily unavailable, please retry")

// initTasks files completed tasks of earlier years into their archives.
func initTasks() { archiveOldCompleted() }

// timestamp is the current time as tasks store it (created_at, completed_at).
func timestamp() string { return now().Format("2006-01-02T15:04:05") }

func writeTasks(path string, tasks []*Task) error {
	text, err := encodeTasks(tasks)
	if err != nil {
		return err
	}
	return atomicWrite(path, text)
}

// atomicWrite writes a temp file and renames it into place: a reader sees
// the whole old file or the whole new one, never a half-written one.
func atomicWrite(path, text string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil { // _deck/ in a new wiki
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	_, err = tmp.WriteString(text)
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Chmod(tmp.Name(), 0o644)
	}
	if err == nil {
		err = os.Rename(tmp.Name(), path)
	}
	if err != nil {
		os.Remove(tmp.Name())
	}
	return err
}

// readListStrict reads a task list for a write: a file that is there but
// unusable (empty, not JSON, not a list) is errTasksCorrupt, never [].
func readListStrict(path string) ([]*Task, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return []*Task{}, nil
	}
	if err != nil || strings.TrimSpace(string(raw)) == "" {
		return nil, errTasksCorrupt
	}
	tasks, ok := parseTasks(raw)
	if !ok {
		return nil, errTasksCorrupt
	}
	return tasks, nil
}

// readListLenient reads a task list for reading: missing or unreadable is [].
func readListLenient(path string) []*Task {
	raw, err := os.ReadFile(path)
	if err != nil {
		return []*Task{}
	}
	tasks, ok := parseTasks(raw)
	if !ok {
		return []*Task{}
	}
	return tasks
}

// parseTasks: the tasks of a file's text (taskFromJSON); ok false when it
// isn't a JSON list. What isn't an object is no task.
func parseTasks(raw []byte) ([]*Task, bool) {
	var l []json.RawMessage
	if json.Unmarshal(raw, &l) != nil || l == nil {
		return nil, false
	}
	out := make([]*Task, 0, len(l))
	for _, x := range l {
		if o, ok := as[jsonObj](x); ok {
			out = append(out, taskFromJSON(o))
		}
	}
	return out, true
}

// readTasksStrict: the open tasks user sees, read for a write.
func readTasksStrict(user string) ([]*Task, error) {
	var err error
	tasks := eachOwner(taskOwners(user), func(o string) []*Task {
		l, e := readListStrict(tasksFile(o))
		err = cmp.Or(err, e)
		return l
	})
	return tasks, err
}

// readTasks: the open tasks user sees (shared and their own).
func readTasks(user string) []*Task {
	return eachOwner(taskOwners(user), func(o string) []*Task { return readListLenient(tasksFile(o)) })
}

// readCompleted: this year's completed tasks user sees.
func readCompleted(user string) []*Task {
	return eachOwner(taskOwners(user), func(o string) []*Task { return readListLenient(completedPath(o)) })
}

var archiveNameRE = regexp.MustCompile(`^completed-(\d{4})\.json$`)

// archiveGlob: owner's yearly archives (completed-2025.json …), sorted.
func archiveGlob(owner string) []string {
	entries, _ := os.ReadDir(tasksDir(owner))
	var out []string
	for _, e := range entries {
		if archiveNameRE.MatchString(e.Name()) {
			out = append(out, filepath.Join(tasksDir(owner), e.Name()))
		}
	}
	sort.Strings(out)
	return out
}

var yearRE = regexp.MustCompile(`^\d{4}$`)

// completedYear: the year a completed task belongs to ("" when unusable).
func completedYear(t *Task) string {
	year := runePrefix(t.CompletedAt, 4)
	if yearRE.MatchString(year) {
		return year
	}
	return ""
}

// runePrefix is s[:n] in characters.
func runePrefix(s string, n int) string {
	i := 0
	for j := range s {
		if i == n {
			return s[:j]
		}
		i++
	}
	return s
}

func currentYear() string { return now().Format("2006") }

// pastYear: the task's completion year when it is before this one (its
// archive's), else "" (the current completed file).
func pastYear(t *Task) string { return pastYearOf(t, currentYear()) }

func pastYearOf(t *Task, current string) string {
	if y := completedYear(t); y != "" && y < current {
		return y
	}
	return ""
}

// readCompletedAllStrict: every completed task of owner's, the archives
// first, for a write (writeCompleted files each by year again).
func readCompletedAllStrict(owner string) ([]*Task, error) {
	var tasks []*Task
	for _, p := range archiveGlob(owner) {
		l, err := readListStrict(p)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, l...)
	}
	l, err := readListStrict(completedPath(owner))
	if err != nil {
		return nil, err
	}
	return append(tasks, l...), nil
}

// writeCompleted writes owner's completed tasks split by year (this year's into
// completed.json, earlier ones into completed-YYYY.json); only files whose
// tasks change (with reformat also those in another format). Returns
// their paths, for the commit.
func writeCompleted(owner string, completed []*Task, reformat bool) ([]string, error) {
	current := currentYear()
	order := []string{completedPath(owner)}
	byPath := map[string][]*Task{completedPath(owner): {}}
	for _, p := range archiveGlob(owner) {
		order = append(order, p)
		byPath[p] = []*Task{}
	}
	for _, t := range completed {
		p := completedPath(owner)
		if y := pastYearOf(t, current); y != "" {
			p = archivePath(owner, y)
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
		text, encErr := encodeTasks(tasks)
		if encErr != nil {
			return changed, encErr
		}
		if err == nil && (string(raw) == text || !reformat && sameTasks(raw, text)) {
			continue
		}
		if err := atomicWrite(p, text); err != nil {
			return changed, err
		}
		changed = append(changed, p)
	}
	return changed, nil
}

// sameTasks: a file holds the tasks text has, whatever its format.
func sameTasks(raw []byte, text string) bool {
	tasks, ok := parseTasks(raw)
	if !ok {
		return false
	}
	have, err := encodeTasks(tasks)
	return err == nil && have == text
}

// completedInYear: the Completed view of one year, of the tasks user sees
// (this year's view takes tasks without a usable date too).
func completedInYear(user, year string) []*Task {
	current := currentYear()
	tasksMu.Lock()
	tasks := readCompleted(user)
	if year != current {
		tasks = append(tasks, eachOwner(taskOwners(user), func(o string) []*Task {
			return readListLenient(archivePath(o, year))
		})...)
	}
	tasksMu.Unlock()
	out := []*Task{}
	for _, t := range tasks {
		if year == current && pastYearOf(t, current) == "" || year != current && completedYear(t) == year {
			out = append(out, t)
		}
	}
	return out
}

// completedYears: years with completed tasks in scope that user sees,
// newest first; this year always.
func completedYears(user, scope string) []string {
	current := currentYear()
	years := map[string]bool{current: true}
	tasksMu.Lock()
	for _, t := range readCompleted(user) {
		if inScope(t, scope) && pastYearOf(t, current) != "" {
			years[completedYear(t)] = true
		}
	}
	for _, o := range taskOwners(user) {
		for _, p := range archiveGlob(o) {
			for _, t := range readListLenient(p) {
				if inScope(t, scope) {
					years[archiveNameRE.FindStringSubmatch(filepath.Base(p))[1]] = true
					break
				}
			}
		}
	}
	tasksMu.Unlock()
	out := sortedKeys(years)
	sort.Sort(sort.Reverse(sort.StringSlice(out)))
	return out
}

// readArchived: the archived tasks (earlier years') of owners.
func readArchived(owners []string) []*Task {
	return eachOwner(owners, func(o string) []*Task {
		var tasks []*Task
		for _, p := range archiveGlob(o) {
			tasks = append(tasks, readListLenient(p)...)
		}
		return tasks
	})
}

// archiveOldCompleted rolls earlier years' completed tasks into their
// archives (at start; every write files by year too), and writes files in
// another format (the Python Zone's key order, a hand-edited file) as
// Zone writes them, so later commits show only what changed; every
// owner's files, in one commit. An unreadable file is left alone (its
// owner's files are): this is never worth failing over.
func archiveOldCompleted() {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	moved := false
	var changed []string
	for _, o := range everyOwner() {
		active, completed, err := readOwnerStrict(o)
		if err != nil {
			continue
		}
		for _, t := range readListLenient(completedPath(o)) {
			moved = moved || pastYear(t) != ""
		}
		if raw, err := os.ReadFile(tasksFile(o)); err == nil {
			if text, err := encodeTasks(active); err == nil && string(raw) != text && atomicWrite(tasksFile(o), text) == nil {
				changed = append(changed, tasksFile(o))
			}
		}
		more, _ := writeCompleted(o, completed, true)
		changed = append(changed, more...)
	}
	if len(changed) == 0 {
		return
	}
	msg := "Write the task files in Zone's format"
	if moved {
		msg = "Archive completed tasks"
	}
	gitCommit("", msg, changed...)
}

// readAll: the open and completed tasks user sees, one consistent snapshot.
func readAll(user string) []*Task {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	return append(readTasks(user), readCompleted(user)...)
}

// openTasks: the open tasks user sees, under the lock: a task made private
// or shared moves between two files, and a read between their writes
// would miss it.
func openTasks(user string) []*Task {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	return readTasks(user)
}

// allTasks: every task user sees, archived ones too, one snapshot.
func allTasks(user string) []*Task {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	return slices.Concat(readTasks(user), readCompleted(user), readArchived(taskOwners(user)))
}

// everyTask: every task of every owner (Task.Owner says whose), for what
// keeps all of them and filters by user later (the search index).
func everyTask() []*Task {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	return everyTaskLocked()
}

func everyTaskLocked() []*Task {
	owners := everyOwner()
	return slices.Concat(eachOwner(owners, func(o string) []*Task { return readListLenient(tasksFile(o)) }),
		eachOwner(owners, func(o string) []*Task { return readListLenient(completedPath(o)) }),
		readArchived(owners))
}

// readOwnerStrict: owner's open tasks and every completed one, read for a
// write (errTasksCorrupt when a file is unreadable), marked as owner's.
func readOwnerStrict(owner string) (active, completed []*Task, err error) {
	if active, err = readListStrict(tasksFile(owner)); err != nil {
		return nil, nil, err
	}
	if completed, err = readCompletedAllStrict(owner); err != nil {
		return nil, nil, err
	}
	for _, t := range slices.Concat(active, completed) {
		t.Owner = owner
	}
	return active, completed, nil
}

// readForWriteLocked: the open tasks and every completed task user sees
// (shared and their own), read for a write.
func readForWriteLocked(user string) (active, completed []*Task, err error) {
	active, completed = []*Task{}, []*Task{}
	for _, o := range taskOwners(user) {
		a, c, err := readOwnerStrict(o)
		if err != nil {
			return nil, nil, err
		}
		active, completed = append(active, a...), append(completed, c...)
	}
	return active, completed, nil
}

// byOwner: tasks split by their owner, each of user's owners present
// (one whose last task left still gets its file written); a task of
// someone else's is refused: it can't have been read for user.
func byOwner(user string, tasks []*Task) (map[string][]*Task, error) {
	out := map[string][]*Task{}
	for _, o := range taskOwners(user) {
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
func writeOpenLocked(user string, tasks []*Task) ([]string, error) {
	split, err := byOwner(user, tasks)
	if err != nil {
		return nil, err
	}
	var changed []string
	for _, o := range taskOwners(user) {
		text, err := encodeTasks(split[o])
		if err != nil {
			return changed, err
		}
		raw, rerr := os.ReadFile(tasksFile(o))
		if rerr == nil && string(raw) == text || errors.Is(rerr, os.ErrNotExist) && o != "" && len(split[o]) == 0 {
			continue
		}
		if err := atomicWrite(tasksFile(o), text); err != nil {
			return changed, err
		}
		changed = append(changed, tasksFile(o))
	}
	return changed, nil
}

// commitTasks commits the tasks files a write changed, by user.
func commitTasks(user, message string, changed []string) {
	if len(changed) > 0 {
		gitCommit(user, message, changed...)
	}
}

// writeTasksLocked writes the open tasks (each into its owner's file),
// committed by user.
func writeTasksLocked(user string, tasks []*Task, message string) error {
	changed, err := writeOpenLocked(user, tasks)
	commitTasks(user, message, changed)
	return err
}

// writeBothLocked writes the open tasks and every completed task (filed by
// owner and year) in one commit, by user.
func writeBothLocked(user string, active, completed []*Task, message string) error {
	changed, err := writeOpenLocked(user, active)
	if err == nil {
		var split map[string][]*Task
		if split, err = byOwner(user, completed); err == nil {
			for _, o := range taskOwners(user) {
				more, werr := writeCompleted(o, split[o], false)
				changed = append(changed, more...)
				if err = werr; err != nil {
					break
				}
			}
		}
	}
	commitTasks(user, message, changed)
	return err
}

// taskIDsLocked: the ids of every owner's tasks, so a restored task never
// takes one another file has.
func taskIDsLocked() map[string]bool {
	ids := map[string]bool{}
	for _, t := range everyTaskLocked() {
		ids[t.ID] = true
	}
	return ids
}

// uuid4 is a random UUID, as Python's str(uuid.uuid4()).
func uuid4() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	h := hex.EncodeToString(b)
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

// newTask: a new task, created now, every other field at its default.
func newTask() *Task {
	return &Task{ID: uuid4(), CreatedAt: timestamp(), Tags: []string{}, History: []HistoryEntry{}, Comments: []Comment{}}
}

// ---- recurrence ----

func daysIn(year int, month time.Month) int {
	return time.Date(year, month+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

// addMonths adds months, clamping the day to the month's last (Jan 31 + 1
// month = Feb 28/29); day (when > 0) is the wanted day of month, so a
// series on the 31st returns to it after a shorter month.
func addMonths(base time.Time, months, day int) time.Time {
	m := int(base.Month()) - 1 + months
	year := base.Year() + floorDiv(m, 12)
	month := time.Month(m - floorDiv(m, 12)*12 + 1)
	if day <= 0 {
		day = base.Day()
	}
	return time.Date(year, month, min(day, daysIn(year, month)), 0, 0, 0, 0, time.Local)
}

func floorDiv(a, b int) int {
	q := a / b
	if a%b != 0 && (a < 0) != (b < 0) {
		q--
	}
	return q
}

// recIntervalMax: the largest "every N", far beyond any real series and
// small enough that stepping never leaves the calendar.
const recIntervalMax = 999

func recStep(base time.Time, rtype string, interval, day int) time.Time {
	switch rtype {
	case "weekly":
		return base.AddDate(0, 0, 7*interval)
	case "monthly":
		return addMonths(base, interval, day)
	case "yearly":
		return addMonths(base, 12*interval, day) // Feb 29 → Feb 28
	}
	return base.AddDate(0, 0, interval) // daily (and any unknown type)
}

// nextOccurrence steps a recurrence from base and catches up to >= today
// (> today with afterToday: a skipped occurrence isn't offered again).
// Monthly and yearly series remember their day of month in rec.Day
// (stepping from a clamped date would drift: Jan 31 → Feb 28 → Mar 28);
// from the completion day there is no anchor. Returns the series as the
// next occurrence carries it.
func nextOccurrence(rec *Recurrence, base time.Time, anchored bool, today time.Time, afterToday bool) (time.Time, *Recurrence) {
	next := *rec
	day := 0
	if (rec.Type == "monthly" || rec.Type == "yearly") && anchored {
		if day = rec.Day; day == 0 {
			day = base.Day()
		}
		next.Day = day
	}
	at := recStep(base, rec.Type, rec.Interval, day)
	for at.Before(today) || afterToday && at.Equal(today) {
		at = recStep(at, rec.Type, rec.Interval, day)
	}
	return at, &next
}

// spawnRecurring: the next instance of a recurring task, or nil (none, or
// the one spawned before is still open in active: reopening kept it).
//
// The series runs on the task's due date, else its start date;
// recurrence.from "completion" steps from today instead. With both dates
// the start keeps its gap to the due date; a start that would be before
// today is today, and the series then remembers the gap (recurrence.lead).
// skip catches up past today: a backlog of missed occurrences collapses
// into the next one after today.
func spawnRecurring(user string, task *Task, active []*Task, skip bool) *Task {
	rec := task.Recurrence
	if rec == nil {
		return nil
	}
	for _, t := range active {
		if t.SpawnedFrom != "" && t.SpawnedFrom == task.ID && t.isOpen() {
			return nil
		}
	}
	day0 := today()
	due, hasDue := parseTaskDate(task.DueDate)
	start, hasStart := parseTaskDate(task.StartDate)
	primary, hasPrimary := due, hasDue
	if !hasDue {
		primary, hasPrimary = start, hasStart
	}
	anchored := hasPrimary && rec.From != "completion"
	base := day0
	if anchored {
		base = primary
	}
	next, nextRec := nextOccurrence(rec, base, anchored, day0, skip)
	nextRec.Lead = nil
	var newDue, newStart string
	switch {
	case hasDue:
		newDue = isoDay(next)
		if hasStart {
			lead := daysBetween(start, due)
			if rec.Lead != nil {
				lead = *rec.Lead
			}
			ns := next.AddDate(0, 0, -lead)
			if ns.Before(day0) {
				ns = day0
				nextRec.Lead = &lead
			}
			newStart = isoDay(ns)
		}
	case hasStart:
		newStart = isoDay(next)
	default:
		// No date at all: the series gets a due date (API-created only).
		newDue = isoDay(next)
	}
	spawned := newTask()
	spawned.Title, spawned.Description = task.Title, task.Description
	spawned.Tags = append([]string{}, task.Tags...)
	spawned.DueDate, spawned.StartDate = newDue, newStart
	spawned.SortOrder = task.SortOrder
	spawned.Recurrence, spawned.SpawnedFrom = nextRec, task.ID
	spawned.Owner = task.Owner // a private series stays private
	// The next occurrence carries the series' history and comments on.
	spawned.History = append([]HistoryEntry{}, task.History...)
	spawned.Comments = append([]Comment{}, task.Comments...)
	when := newDue
	if when == "" {
		when = newStart
	}
	logEvent(spawned, user, "repeat", "", when)
	return spawned
}

// parseTaskDate reads a YYYY-MM-DD field.
func parseTaskDate(s string) (time.Time, bool) {
	if s == "" {
		return time.Time{}, false
	}
	return parseDay(s)
}

func daysBetween(a, b time.Time) int {
	return int(b.Sub(a).Round(time.Hour).Hours() / 24)
}

// ---- history ----

// A task's history: what happened and when, newest last, capped. Undo
// sends the old history back with the old fields, so an undone change
// leaves no trace.
var historyEvents = map[string]bool{"created": true, "start": true, "due": true, "done": true,
	"reopened": true, "wontdo": true, "skipped": true, "repeat": true, "private": true, "shared": true}

var datedEvents = map[string]bool{"start": true, "due": true, "repeat": true}

const historyLimit = 30

// logEvent adds an event to a task's history, by user ("" no one: Zone, or
// no users); from and to are the days a dated event moved between ("" none).
func logEvent(t *Task, user, event, from, to string) {
	entry := HistoryEntry{At: now().Format("2006-01-02T15:04"), Event: event, By: user}
	if datedEvents[event] {
		entry.From, entry.To = from, to
	}
	h := t.History
	if len(h) > historyLimit-1 {
		h = h[len(h)-(historyLimit-1):]
	}
	t.History = append(append([]HistoryEntry{}, h...), entry)
}

// cleanHistory validates a history list from a request or snapshot.
func cleanHistory(raw json.RawMessage) []HistoryEntry {
	out := []HistoryEntry{}
	for _, e := range objList(raw) {
		at, isStr := as[string](e["at"])
		event, isStrEvent := as[string](e["event"])
		if !isStr || !isStrEvent || !historyEvents[event] {
			continue
		}
		by, _ := as[string](e["by"])
		entry := HistoryEntry{At: runePrefix(at, 16), Event: event, By: by}
		if datedEvents[event] {
			if s, ok := as[string](e["from"]); ok && isoDateRE.MatchString(s) {
				entry.From = s
			}
			if s, ok := as[string](e["to"]); ok && isoDateRE.MatchString(s) {
				entry.To = s
			}
		}
		out = append(out, entry)
	}
	if len(out) > historyLimit {
		out = out[len(out)-historyLimit:]
	}
	return out
}

// ---- comments ----

// A task's comments: short status lines, oldest first, {id, at, by, text}
// (by: who wrote it, with users), never capped; undo of other changes
// leaves them alone.
const commentMax = 2000

func newCommentID() string { return strings.ReplaceAll(uuid4(), "-", "")[:8] }

// cleanComments validates a comment list from a request or snapshot.
func cleanComments(raw json.RawMessage) []Comment {
	var cs []Comment
	for _, c := range objList(raw) {
		text, isText := as[string](c["text"])
		at, isAt := as[string](c["at"])
		if !isText || !isAt {
			continue
		}
		id, _ := as[string](c["id"])
		by, _ := as[string](c["by"])
		cs = append(cs, Comment{ID: id, At: at, By: by, Text: text})
	}
	return cleanCommentList(cs)
}

// cleanCommentList: comments with text and a time, each with an id of its
// own, the time to the minute, the text stripped and capped.
func cleanCommentList(cs []Comment) []Comment {
	out := []Comment{}
	seen := map[string]bool{}
	for _, c := range cs {
		if strings.TrimSpace(c.Text) == "" || c.At == "" {
			continue
		}
		id := c.ID
		if id == "" || seen[id] {
			id = newCommentID()
		}
		seen[id] = true
		out = append(out, Comment{ID: id, At: runePrefix(c.At, 16), By: c.By,
			Text: runePrefix(strings.TrimSpace(c.Text), commentMax)})
	}
	return out
}

// keepDone completes a keep task: never archived, it returns to Open
// without start or due date (it leaves Focus), the day kept in last_done.
func keepDone(user string, t *Task) {
	logEvent(t, user, "done", "", "")
	t.LastDone = isoDay(today())
	t.StartDate, t.DueDate = "", ""
}

// completeTask stamps a task completed (by user, at at; "" now); returns
// the spawned recurring instance, if any.
func completeTask(user string, t *Task, at string, active []*Task) *Task {
	if at == "" {
		at = timestamp()
	}
	t.CompletedAt, t.WontDo = at, false
	logEvent(t, user, "done", "", "")
	return spawnRecurring(user, t, active, false)
}

// untouchedSpawn: the occurrence task spawned is still as the spawn made it
// (its history ends with the "repeat", its title, notes, tags and comments
// are task's); only such a spawn may go when task is reopened.
func untouchedSpawn(spawn, task *Task) bool {
	h := spawn.History
	if len(h) == 0 || h[len(h)-1].Event != "repeat" || spawn.Keep {
		return false
	}
	if spawn.Title != task.Title || spawn.Description != task.Description || !slices.Equal(spawn.Tags, task.Tags) {
		return false
	}
	for _, c := range spawn.Comments {
		if !slices.Contains(task.Comments, c) {
			return false
		}
	}
	return true
}

// uncompleteTask clears the completion and returns active without the
// open instance spawned when it was completed, unless that was changed
// since (untouchedSpawn); protect ids are never removed.
func uncompleteTask(user string, t *Task, active []*Task, protect map[string]bool) []*Task {
	if t.Recurrence != nil {
		kept := active[:0:0]
		for _, a := range active {
			if a.SpawnedFrom != "" && a.SpawnedFrom == t.ID && a.isOpen() && untouchedSpawn(a, t) && !protect[a.ID] {
				continue
			}
			kept = append(kept, a)
		}
		active = kept
	}
	t.CompletedAt, t.WontDo = "", false
	logEvent(t, user, "reopened", "", "")
	return active
}

// renamePageRefs follows a page rename in the tasks: links in task titles
// and descriptions rewritten (by rewrite, given the files' owner: "" the
// shared ones), every owner's tasks. Writes the files that changed, doesn't
// commit: returns them for the rename's commit.
func renamePageRefs(rewrite func(owner, text string) string) ([]string, error) {
	tasksMu.Lock()
	defer tasksMu.Unlock()
	type file struct{ path, owner string }
	var paths []file
	var changed []string
	for _, o := range everyOwner() {
		for _, p := range append([]string{tasksFile(o), completedPath(o)}, archiveGlob(o)...) {
			paths = append(paths, file{p, o})
		}
	}
	for _, f := range paths {
		p := f.path
		tasks, err := readListStrict(p)
		if err != nil {
			return changed, err
		}
		dirty := false
		for _, t := range tasks {
			for _, field := range []*string{&t.Title, &t.Description} {
				if *field != "" {
					if n := rewrite(f.owner, *field); n != *field {
						*field = n
						dirty = true
					}
				}
			}
		}
		if dirty {
			if err := writeTasks(p, tasks); err != nil {
				return changed, err
			}
			changed = append(changed, p)
		}
	}
	return changed, nil
}

// TaskLink is an open task that links to a page: to one of its sections,
// the first such link's section as written and its heading id (anchor).
type TaskLink struct {
	ID, Title       string
	Tags            []string
	Section, Anchor string
}

// findTaskBacklinks: open tasks user sees whose title or notes link to
// slug (a wikilink, or a markdown link to /page/<slug>).
func findTaskBacklinks(user, slug string) []TaskLink {
	var out []TaskLink
	for _, t := range readAll(user) {
		if !t.isOpen() {
			continue
		}
		text := t.Title + "\n" + t.Description
		found, section, anchor := false, "", ""
		for _, m := range wikilinkRE.FindAllStringSubmatch(text, -1) {
			l := parseWikilink(m[0])
			page, sec := strings.TrimSpace(l.page), strings.TrimSpace(l.section)
			if page != "" && (page == slug || toSlug(page) == slug) {
				found = true
				if sec != "" && section == "" {
					section, anchor = sec, sectionAnchor(sec)
				}
			}
		}
		for _, m := range mdLinkRE.FindAllStringSubmatch(text, -1) {
			page, frag, _ := strings.Cut(m[2], "#")
			if page == slug {
				found = true
				if frag != "" && section == "" {
					section, anchor = strings.ReplaceAll(frag, "-", " "), frag
				}
			}
		}
		if found {
			out = append(out, TaskLink{ID: t.ID, Title: t.Title, Tags: t.Tags, Section: section, Anchor: anchor})
		}
	}
	return out
}

// resolveTaskRefs maps each reference (an id prefix, lowercased) to its
// task among those user sees, nil when none has it (another user's private
// task is as none). An open task wins over a completed one; a completed
// recurring task stands for its series: the reference follows
// spawned_from to the occurrence current now.
func resolveTaskRefs(user string, refs []string) map[string]*Task {
	out := map[string]*Task{}
	if len(refs) == 0 {
		return out
	}
	tasksMu.Lock()
	active := readTasks(user)
	done := append(readCompleted(user), readArchived(taskOwners(user))...)
	tasksMu.Unlock()
	successor := map[string]*Task{}
	for _, t := range append(append([]*Task{}, active...), done...) {
		if t.SpawnedFrom != "" {
			successor[t.SpawnedFrom] = t
		}
	}
	for _, ref := range refs {
		ref = strings.ToLower(ref)
		var task *Task
		for _, list := range [][]*Task{active, done} {
			for _, t := range list {
				if strings.HasPrefix(strings.ToLower(t.ID), ref) {
					task = t
					break
				}
			}
			if task != nil {
				break
			}
		}
		seen := map[string]bool{}
		for task != nil && !task.isOpen() && successor[task.ID] != nil && !seen[task.ID] {
			seen[task.ID] = true
			task = successor[task.ID]
		}
		out[ref] = task
	}
	return out
}

// findTasks: every task user sees whose id starts with prefix (lowercase);
// only the one with exactly that id when there is one.
func findTasks(user, prefix string) []*Task {
	tasks := allTasks(user)
	var found, exact []*Task
	for _, t := range tasks {
		id := strings.ToLower(t.ID)
		if strings.HasPrefix(id, prefix) {
			found = append(found, t)
			if id == prefix {
				exact = append(exact, t)
			}
		}
	}
	if len(exact) > 0 {
		return exact
	}
	return found
}

// lastActivity: the day a task last changed as far as it records.
func lastActivity(t *Task) string {
	stamps := []string{t.CompletedAt, t.CreatedAt}
	for _, e := range t.History {
		stamps = append(stamps, e.At)
	}
	for _, c := range t.Comments {
		stamps = append(stamps, c.At)
	}
	best := ""
	for _, s := range stamps {
		if ymdPrefixRE.MatchString(s) {
			if d := runePrefix(s, 10); d > best {
				best = d
			}
		}
	}
	return best
}

var ymdPrefixRE = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}`)

// cleanRecurrence: a recurrence from a request (an undo), its fields
// validated; nil when it names no repeat type.
func cleanRecurrence(raw json.RawMessage) *Recurrence {
	rec, ok := as[jsonObj](raw)
	if !ok {
		return nil
	}
	rtype, _ := as[string](rec["type"])
	switch rtype {
	case "daily", "weekly", "monthly", "yearly":
	default:
		return nil
	}
	out := &Recurrence{Type: rtype, Interval: 1, From: "due"}
	if n, ok := as[int](rec["interval"]); ok && n >= 1 && n <= recIntervalMax {
		out.Interval = n
	}
	if s, _ := as[string](rec["from"]); s == "completion" {
		out.From = "completion"
	}
	if n, ok := as[int](rec["day"]); ok && n >= 1 && n <= 31 {
		out.Day = n
	}
	if n, ok := as[int](rec["lead"]); ok && n >= 0 {
		out.Lead = &n
	}
	return out
}

// sanitizeSnapshot rebuilds a task from an untrusted snapshot (undo after
// delete), known fields only; nil without a usable title. A bad optional
// value falls back to its default.
func sanitizeSnapshot(snap jsonObj) *Task {
	title, _ := as[string](snap["title"])
	if title = strings.TrimSpace(title); title == "" {
		return nil
	}
	t := newTask()
	t.Title = title
	t.Description, _ = as[string](snap["description"])
	t.Tags = cleanTags(textList(snap["tags"]))
	for f, field := range map[string]*string{"due_date": &t.DueDate, "start_date": &t.StartDate, "last_done": &t.LastDone} {
		if s, ok := as[string](snap[f]); ok {
			if _, ok := parseDay(s); ok {
				*field = s
			}
		}
	}
	for f, field := range map[string]*string{"id": &t.ID, "created_at": &t.CreatedAt,
		"completed_at": &t.CompletedAt, "spawned_from": &t.SpawnedFrom} {
		if s, ok := as[string](snap[f]); ok && s != "" {
			*field = s
		}
	}
	if f, ok := as[float64](snap["sort_order"]); ok {
		t.SortOrder = &f
	}
	t.WontDo, t.Keep = jsonTrue(snap["wont_do"]), jsonTrue(snap["keep"])
	t.History = cleanHistory(snap["history"])
	t.Comments = cleanComments(snap["comments"])
	if !t.Keep {
		t.Recurrence = cleanRecurrence(snap["recurrence"])
	}
	return t
}
