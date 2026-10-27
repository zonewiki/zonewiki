// Deck tasks on disk and their model: the _deck/ files
// (open tasks, completed, yearly archives) under one lock with atomic
// writes, recurrence, history, comments, and what pages need from tasks
// (links to pages, [[task:…]] references). A task is a *Task (task.go).
package main

import (
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

// deckMu serializes every read-modify-write of the deck's files: two
// concurrent task changes would otherwise lose each other's writes. Write
// paths hold it for the whole read-modify-write-commit; functions named
// …Locked expect it held (Go's mutex isn't reentrant, Python's RLock was).
var deckMu sync.Mutex

const deckDirName = "_deck"

func deckFile() string      { return filepath.Join(cfg.Data, deckDirName, "tasks.json") }
func completedPath() string { return filepath.Join(cfg.Data, deckDirName, "completed.json") }
func archivePath(year string) string {
	return filepath.Join(cfg.Data, deckDirName, "completed-"+year+".json")
}

// errDeckCorrupt: a deck file is there but unreadable; a write refuses
// rather than overwriting it with a partial list.
var errDeckCorrupt = errors.New("deck storage temporarily unavailable, please retry")

// initDeck files completed tasks of earlier years into their archives.
func initDeck() { archiveOldCompleted() }

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
// unusable (empty, not JSON, not a list) is errDeckCorrupt, never [].
func readListStrict(path string) ([]*Task, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return []*Task{}, nil
	}
	if err != nil || strings.TrimSpace(string(raw)) == "" {
		return nil, errDeckCorrupt
	}
	tasks, ok := parseTasks(raw)
	if !ok {
		return nil, errDeckCorrupt
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

func readDeckStrict() ([]*Task, error) { return readListStrict(deckFile()) }

// readDeck: the open tasks.
func readDeck() []*Task { return readListLenient(deckFile()) }

func readCompleted() []*Task { return readListLenient(completedPath()) }

var archiveNameRE = regexp.MustCompile(`^completed-(\d{4})\.json$`)

// archiveGlob: the yearly archives (completed-2025.json …), sorted.
func archiveGlob() []string {
	entries, _ := os.ReadDir(filepath.Join(cfg.Data, deckDirName))
	var out []string
	for _, e := range entries {
		if archiveNameRE.MatchString(e.Name()) {
			out = append(out, filepath.Join(cfg.Data, deckDirName, e.Name()))
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

// readCompletedAllStrict: every completed task, the archives first, for a
// write (writeCompleted files each by year again).
func readCompletedAllStrict() ([]*Task, error) {
	var tasks []*Task
	for _, p := range archiveGlob() {
		l, err := readListStrict(p)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, l...)
	}
	l, err := readListStrict(completedPath())
	if err != nil {
		return nil, err
	}
	return append(tasks, l...), nil
}

// writeCompleted writes completed tasks split by year (this year's into
// completed.json, earlier ones into completed-YYYY.json); only files whose
// tasks change (with reformat also those in another format). Returns
// their paths, for the commit.
func writeCompleted(completed []*Task, reformat bool) ([]string, error) {
	current := currentYear()
	order := []string{completedPath()}
	byPath := map[string][]*Task{completedPath(): {}}
	for _, p := range archiveGlob() {
		order = append(order, p)
		byPath[p] = []*Task{}
	}
	for _, t := range completed {
		p := completedPath()
		if y := pastYearOf(t, current); y != "" {
			p = archivePath(y)
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

// completedInYear: the Completed view of one year (this year's view takes
// tasks without a usable date too).
func completedInYear(year string) []*Task {
	current := currentYear()
	deckMu.Lock()
	tasks := readCompleted()
	if year != current {
		tasks = append(tasks, readListLenient(archivePath(year))...)
	}
	deckMu.Unlock()
	out := []*Task{}
	for _, t := range tasks {
		if year == current && pastYearOf(t, current) == "" || year != current && completedYear(t) == year {
			out = append(out, t)
		}
	}
	return out
}

// completedYears: years with completed tasks in scope, newest first; this
// year always.
func completedYears(scope string) []string {
	current := currentYear()
	years := map[string]bool{current: true}
	deckMu.Lock()
	for _, t := range readCompleted() {
		if inScope(t, scope) && pastYearOf(t, current) != "" {
			years[completedYear(t)] = true
		}
	}
	for _, p := range archiveGlob() {
		for _, t := range readListLenient(p) {
			if inScope(t, scope) {
				years[archiveNameRE.FindStringSubmatch(filepath.Base(p))[1]] = true
				break
			}
		}
	}
	deckMu.Unlock()
	out := sortedKeys(years)
	sort.Sort(sort.Reverse(sort.StringSlice(out)))
	return out
}

func readArchived() []*Task {
	var tasks []*Task
	for _, p := range archiveGlob() {
		tasks = append(tasks, readListLenient(p)...)
	}
	return tasks
}

// archiveOldCompleted rolls earlier years' completed tasks into their
// archives (at start; every write files by year too), and writes files in
// another format (the Python Leaf's key order, a hand-edited file) as
// Leaf writes them, so later commits show only what changed. An
// unreadable file is left alone: this is never worth failing over.
func archiveOldCompleted() {
	deckMu.Lock()
	defer deckMu.Unlock()
	active, completed, err := readForWriteLocked()
	if err != nil {
		return
	}
	moved := false
	for _, t := range readCompleted() {
		moved = moved || pastYear(t) != ""
	}
	var changed []string
	if raw, err := os.ReadFile(deckFile()); err == nil {
		if text, err := encodeTasks(active); err == nil && string(raw) != text && atomicWrite(deckFile(), text) == nil {
			changed = append(changed, deckFile())
		}
	}
	more, err := writeCompleted(completed, true)
	if changed = append(changed, more...); err != nil || len(changed) == 0 {
		return
	}
	msg := "Write the deck's files in Leaf's format"
	if moved {
		msg = "Archive completed deck tasks"
	}
	gitCommit("", msg, changed...)
}

// readAll: open and completed tasks, one consistent snapshot.
func readAll() []*Task {
	deckMu.Lock()
	defer deckMu.Unlock()
	return append(readDeck(), readCompleted()...)
}

// allTasks: every task, archived ones too.
func allTasks() []*Task { return append(readAll(), readArchived()...) }

// readForWriteLocked: the open tasks and every completed task, read for a
// write (errDeckCorrupt when a file is unreadable).
func readForWriteLocked() (active, completed []*Task, err error) {
	if active, err = readDeckStrict(); err != nil {
		return nil, nil, err
	}
	if completed, err = readCompletedAllStrict(); err != nil {
		return nil, nil, err
	}
	return active, completed, nil
}

// writeDeckLocked writes the open tasks, committed by user.
func writeDeckLocked(user string, tasks []*Task, message string) error {
	if err := writeTasks(deckFile(), tasks); err != nil {
		return err
	}
	gitCommit(user, message, deckFile())
	return nil
}

// writeBothLocked writes the open tasks and every completed task (filed by
// year) in one commit, by user.
func writeBothLocked(user string, active, completed []*Task, message string) error {
	if err := writeTasks(deckFile(), active); err != nil {
		return err
	}
	changed, err := writeCompleted(completed, false)
	if err != nil {
		return err
	}
	gitCommit(user, message, append([]string{deckFile()}, changed...)...)
	return nil
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
func spawnRecurring(task *Task, active []*Task, skip bool) *Task {
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
	// The next occurrence carries the series' history and comments on.
	spawned.History = append([]HistoryEntry{}, task.History...)
	spawned.Comments = append([]Comment{}, task.Comments...)
	when := newDue
	if when == "" {
		when = newStart
	}
	logEvent(spawned, "repeat", "", when)
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
	"reopened": true, "wontdo": true, "skipped": true, "repeat": true}

var datedEvents = map[string]bool{"start": true, "due": true, "repeat": true}

const historyLimit = 30

// logEvent adds an event to a task's history; from and to are the days a
// dated event moved between ("" none).
func logEvent(t *Task, event, from, to string) {
	entry := HistoryEntry{At: now().Format("2006-01-02T15:04"), Event: event}
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
		entry := HistoryEntry{At: runePrefix(at, 16), Event: event}
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
func keepDone(t *Task) {
	logEvent(t, "done", "", "")
	t.LastDone = isoDay(today())
	t.StartDate, t.DueDate = "", ""
}

// completeTask stamps a task completed; returns the spawned recurring
// instance, if any.
func completeTask(t *Task, at string, active []*Task) *Task {
	if at == "" {
		at = timestamp()
	}
	t.CompletedAt, t.WontDo = at, false
	logEvent(t, "done", "", "")
	return spawnRecurring(t, active, false)
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
func uncompleteTask(t *Task, active []*Task, protect map[string]bool) []*Task {
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
	logEvent(t, "reopened", "", "")
	return active
}

// renamePageRefs follows a page rename in the deck: links in task titles
// and descriptions rewritten (by rewrite). Writes the files that changed,
// doesn't commit: returns them for the rename's commit.
func renamePageRefs(rewrite func(string) string) ([]string, error) {
	deckMu.Lock()
	defer deckMu.Unlock()
	paths := append([]string{deckFile(), completedPath()}, archiveGlob()...)
	var changed []string
	for _, p := range paths {
		tasks, err := readListStrict(p)
		if err != nil {
			return changed, err
		}
		dirty := false
		for _, t := range tasks {
			for _, field := range []*string{&t.Title, &t.Description} {
				if *field != "" {
					if n := rewrite(*field); n != *field {
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

// findTaskBacklinks: open tasks whose title or notes link to slug (a
// wikilink, or a markdown link to /page/<slug>).
func findTaskBacklinks(slug string) []TaskLink {
	var out []TaskLink
	for _, t := range readAll() {
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
// task, nil when none has it. An open task wins over a completed one; a
// completed recurring task stands for its series: the reference follows
// spawned_from to the occurrence current now.
func resolveTaskRefs(refs []string) map[string]*Task {
	out := map[string]*Task{}
	if len(refs) == 0 {
		return out
	}
	deckMu.Lock()
	active := readDeck()
	done := append(readCompleted(), readArchived()...)
	deckMu.Unlock()
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

// findTasks: every task whose id starts with prefix (lowercase); only the
// one with exactly that id when there is one.
func findTasks(prefix string) []*Task {
	deckMu.Lock()
	tasks := append(append(readDeck(), readCompleted()...), readArchived()...)
	deckMu.Unlock()
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
