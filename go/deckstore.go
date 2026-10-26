// Deck tasks on disk and their model (src/deckstore.py): the _deck/ files
// (open tasks, completed, yearly archives) under one lock with atomic
// writes, recurrence, history, comments, and what pages need from tasks
// (links to pages, [[task:…]] references). A task is an *Obj, so a file
// written back keeps its keys' order and any key Leaf doesn't know.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"regexp"
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

func writeTasks(path string, tasks []*Obj) error {
	l := make([]any, len(tasks))
	for i, t := range tasks {
		l[i] = t
	}
	return atomicWrite(path, dumps(l))
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
func readListStrict(path string) ([]*Obj, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return []*Obj{}, nil
	}
	if err != nil || pyStrip(string(raw)) == "" {
		return nil, errDeckCorrupt
	}
	v, err := loads(raw)
	l, ok := v.([]any)
	if err != nil || !ok {
		return nil, errDeckCorrupt
	}
	return normalizeList(l), nil
}

// readListLenient reads a task list for reading: missing or unreadable is [].
func readListLenient(path string) []*Obj {
	raw, err := os.ReadFile(path)
	if err != nil {
		return []*Obj{}
	}
	v, err := loads(raw)
	l, ok := v.([]any)
	if err != nil || !ok {
		return []*Obj{}
	}
	return normalizeList(l)
}

// normalizeList: the tasks of a file, their tags cleaned (a file edited by
// hand may have "local llm" or "#work"). What isn't an object is no task.
func normalizeList(l []any) []*Obj {
	out := make([]*Obj, 0, len(l))
	for _, v := range l {
		t, ok := v.(*Obj)
		if !ok {
			continue
		}
		if tags, ok := t.Get("tags").([]any); ok {
			t.Set("tags", strs(cleanTags(tags)))
		}
		out = append(out, t)
	}
	return out
}

// strs is a []string as JSON's []any.
func strs(s []string) []any {
	l := make([]any, len(s))
	for i, x := range s {
		l[i] = x
	}
	return l
}

// tagList is a task's tags as strings.
func tagList(t *Obj) []string {
	var out []string
	for _, x := range t.List("tags") {
		out = append(out, pyStr(x))
	}
	return out
}

func readDeckStrict() ([]*Obj, error) { return readListStrict(deckFile()) }

// readDeck: the open tasks.
func readDeck() []*Obj { return readListLenient(deckFile()) }

func readCompleted() []*Obj { return readListLenient(completedPath()) }

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

var yearRE = regexp.MustCompile(`^` + pyDigit + `{4}$`)

// completedYear: the year a completed task belongs to ("" when unusable).
func completedYear(t *Obj) string {
	year := runePrefix(t.Str("completed_at"), 4)
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
func pastYear(t *Obj) string { return pastYearOf(t, currentYear()) }

func pastYearOf(t *Obj, current string) string {
	if y := completedYear(t); y != "" && y < current {
		return y
	}
	return ""
}

// readCompletedAllStrict: every completed task, the archives first, for a
// write (writeCompleted files each by year again).
func readCompletedAllStrict() ([]*Obj, error) {
	var tasks []*Obj
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
// content changes. Returns their paths, for the commit.
func writeCompleted(completed []*Obj) ([]string, error) {
	current := currentYear()
	order := []string{completedPath()}
	byPath := map[string][]*Obj{completedPath(): {}}
	for _, p := range archiveGlob() {
		order = append(order, p)
		byPath[p] = []*Obj{}
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
			continue // no file for no tasks (Python wrote an empty one, and committed it)
		}
		if err == nil {
			if v, err := loads(raw); err == nil && pyEqual(v, objList(tasks)) {
				continue
			}
		}
		if err := writeTasks(p, tasks); err != nil {
			return changed, err
		}
		changed = append(changed, p)
	}
	return changed, nil
}

func objList(tasks []*Obj) []any {
	l := make([]any, len(tasks))
	for i, t := range tasks {
		l[i] = t
	}
	return l
}

// completedInYear: the Completed view of one year (this year's view takes
// tasks without a usable date too).
func completedInYear(year string) []*Obj {
	current := currentYear()
	deckMu.Lock()
	tasks := readCompleted()
	if year != current {
		tasks = append(tasks, readListLenient(archivePath(year))...)
	}
	deckMu.Unlock()
	out := []*Obj{}
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

func readArchived() []*Obj {
	var tasks []*Obj
	for _, p := range archiveGlob() {
		tasks = append(tasks, readListLenient(p)...)
	}
	return tasks
}

// archiveOldCompleted rolls earlier years' completed tasks into their
// archives (at start; every write files by year too). An unreadable file
// is left alone: archiving is never worth failing over.
func archiveOldCompleted() {
	deckMu.Lock()
	defer deckMu.Unlock()
	completed, err := readCompletedAllStrict()
	if err != nil {
		return
	}
	if changed, err := writeCompleted(completed); err == nil && len(changed) > 0 {
		gitCommit("Archive completed deck tasks", changed...)
	}
}

// readAll: open and completed tasks, one consistent snapshot.
func readAll() []*Obj {
	deckMu.Lock()
	defer deckMu.Unlock()
	return append(readDeck(), readCompleted()...)
}

// allTasks: every task, archived ones too.
func allTasks() []*Obj { return append(readAll(), readArchived()...) }

func writeDeckLocked(tasks []*Obj, message string) error {
	if err := writeTasks(deckFile(), tasks); err != nil {
		return err
	}
	gitCommit(message, deckFile())
	return nil
}

// writeBothLocked writes the open tasks and every completed task (filed by
// year) in one commit.
func writeBothLocked(active, completed []*Obj, message string) error {
	if err := writeTasks(deckFile(), active); err != nil {
		return err
	}
	changed, err := writeCompleted(completed)
	if err != nil {
		return err
	}
	gitCommit(message, append([]string{deckFile()}, changed...)...)
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

// newTask: a task with every field at its default (the schema, in order),
// then overrides (key, value pairs).
func newTask(overrides ...any) *Obj {
	t := NewObj(
		"id", uuid4(), "title", "", "description", "", "tags", []any{},
		"due_date", nil, "created_at", timestamp(), "completed_at", nil, "wont_do", false,
		"start_date", nil, "sort_order", nil, "recurrence", nil, "spawned_from", nil,
		"keep", false, "last_done", nil, "history", []any{}, "comments", []any{},
	)
	for i := 0; i+1 < len(overrides); i += 2 {
		t.Set(overrides[i].(string), overrides[i+1])
	}
	return t
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

// intValue is a JSON int (not a bool), ok false otherwise.
func intValue(v any) (int, bool) {
	switch v := v.(type) {
	case int64:
		return int(v), true
	case int:
		return v, true
	case float64:
		if isWhole(v) {
			return int(v), true
		}
	}
	return 0, false
}

// nextOccurrence steps a recurrence from base and catches up to >= today
// (> today with afterToday: a skipped occurrence isn't offered again).
// Monthly and yearly series remember their day of month in recurrence.day
// (stepping from a clamped date would drift: Jan 31 → Feb 28 → Mar 28);
// from the completion day there is no anchor.
func nextOccurrence(rec *Obj, base time.Time, anchored bool, today time.Time, afterToday bool) (time.Time, *Obj) {
	rtype := "daily"
	if v, ok := rec.Lookup("type"); ok {
		rtype = pyStr(v)
	}
	interval := 1
	if v, ok := rec.Lookup("interval"); ok {
		interval, _ = intValue(v)
	}
	rec = rec.Copy()
	day := 0
	if (rtype == "monthly" || rtype == "yearly") && anchored {
		day, _ = intValue(rec.Get("day"))
		if day == 0 {
			day = base.Day()
		}
		rec.Set("day", int64(day))
	}
	next := recStep(base, rtype, interval, day)
	for next.Before(today) || afterToday && next.Equal(today) {
		next = recStep(next, rtype, interval, day)
	}
	return next, rec
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
func spawnRecurring(task *Obj, active []*Obj, skip bool) *Obj {
	rec, ok := task.Get("recurrence").(*Obj)
	if !ok || rec.Len() == 0 {
		return nil
	}
	for _, t := range active {
		if t.Str("spawned_from") == task.Str("id") && t.Get("spawned_from") != nil && !t.Truthy("completed_at") {
			return nil
		}
	}
	day0 := today()
	due, hasDue := parseTaskDate(task.Get("due_date"))
	start, hasStart := parseTaskDate(task.Get("start_date"))
	primary, hasPrimary := due, hasDue
	if !hasDue {
		primary, hasPrimary = start, hasStart
	}
	anchored := hasPrimary && pyStr(rec.Get("from")) != "completion"
	base := day0
	if anchored {
		base = primary
	}
	next, nextRec := nextOccurrence(rec, base, anchored, day0, skip)
	nextRec.Delete("lead")
	var newDue, newStart any
	switch {
	case hasDue:
		newDue = isoDay(next)
		if hasStart {
			lead := daysBetween(start, due)
			if v, ok := rec.Lookup("lead"); ok {
				lead, _ = intValue(v)
			}
			ns := next.AddDate(0, 0, -lead)
			if ns.Before(day0) {
				ns = day0
				nextRec.Set("lead", int64(lead))
			}
			newStart = isoDay(ns)
		}
	case hasStart:
		newStart = isoDay(next)
	default:
		// No date at all: the series gets a due date (API-created only).
		newDue = isoDay(next)
	}
	sortOrder := any(int64(0))
	if v, ok := task.Lookup("sort_order"); ok {
		sortOrder = v
	}
	tags := task.Get("tags")
	if tags == nil {
		tags = []any{}
	}
	spawned := newTask("title", task.Get("title"), "description", orDefault(task, "description", ""),
		"tags", tags, "due_date", newDue, "start_date", newStart, "sort_order", sortOrder,
		"recurrence", nextRec, "spawned_from", task.Get("id"))
	// The next occurrence carries the series' history and comments on.
	spawned.Set("history", append([]any{}, task.List("history")...))
	spawned.Set("comments", append([]any{}, task.List("comments")...))
	when := newDue
	if when == nil {
		when = newStart
	}
	logEvent(spawned, "repeat", nil, when)
	return spawned
}

// orDefault is Python's t.get(k, d).
func orDefault(t *Obj, k string, d any) any {
	if v, ok := t.Lookup(k); ok {
		return v
	}
	return d
}

// parseTaskDate reads a YYYY-MM-DD field.
func parseTaskDate(v any) (time.Time, bool) {
	s, ok := v.(string)
	if !ok || s == "" {
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

func logEvent(t *Obj, event string, from, to any) {
	entry := NewObj("at", now().Format("2006-01-02T15:04"), "event", event)
	if datedEvents[event] {
		entry.Set("from", from)
		entry.Set("to", to)
	}
	h := t.List("history")
	if len(h) > historyLimit-1 {
		h = h[len(h)-(historyLimit-1):]
	}
	t.Set("history", append(append([]any{}, h...), entry))
}

var ymdRE = regexp.MustCompile(`^` + pyDigit + `{4}-` + pyDigit + `{2}-` + pyDigit + `{2}$`)

// cleanHistory validates a history list from a request or snapshot.
func cleanHistory(v any) []any {
	l, _ := v.([]any)
	out := []any{}
	for _, x := range l {
		e, ok := x.(*Obj)
		if !ok {
			continue
		}
		at, isStr := e.Get("at").(string)
		if !isStr || !historyEvents[e.Str("event")] {
			continue
		}
		if _, isStrEvent := e.Get("event").(string); !isStrEvent {
			continue
		}
		entry := NewObj("at", runePrefix(at, 16), "event", e.Str("event"))
		if datedEvents[e.Str("event")] {
			for _, k := range []string{"from", "to"} {
				if s, ok := e.Get(k).(string); ok && ymdRE.MatchString(s) {
					entry.Set(k, s)
				} else {
					entry.Set(k, nil)
				}
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

// A task's comments: short status lines, oldest first, {id, at, text},
// never capped; undo of other changes leaves them alone.
const commentMax = 2000

func newCommentID() string { return strings.ReplaceAll(uuid4(), "-", "")[:8] }

// cleanComments validates a comment list from a request or snapshot.
func cleanComments(v any) []any {
	l, _ := v.([]any)
	out := []any{}
	seen := map[string]bool{}
	for _, x := range l {
		c, ok := x.(*Obj)
		if !ok {
			continue
		}
		text, isText := c.Get("text").(string)
		at, isAt := c.Get("at").(string)
		if !isText || pyStrip(text) == "" || !isAt {
			continue
		}
		id, _ := c.Get("id").(string)
		if id == "" || seen[id] {
			id = newCommentID()
		}
		seen[id] = true
		out = append(out, NewObj("id", id, "at", runePrefix(at, 16), "text", runePrefix(pyStrip(text), commentMax)))
	}
	return out
}

// keepDone completes a keep task: never archived, it returns to Open
// without start or due date (it leaves Focus), the day kept in last_done.
func keepDone(t *Obj) {
	logEvent(t, "done", nil, nil)
	t.Set("last_done", isoDay(today()))
	t.Set("start_date", nil)
	t.Set("due_date", nil)
}

// completeTask stamps a task completed; returns the spawned recurring
// instance, if any.
func completeTask(t *Obj, at string, active []*Obj) *Obj {
	if at == "" {
		at = timestamp()
	}
	t.Set("completed_at", at)
	t.Set("wont_do", false)
	logEvent(t, "done", nil, nil)
	return spawnRecurring(t, active, false)
}

func listOrEmpty(v any) []any {
	if l, ok := v.([]any); ok {
		return l
	}
	return []any{}
}

// untouchedSpawn: the occurrence task spawned is still as the spawn made it
// (its history ends with the "repeat", its title, notes, tags and comments
// are task's); only such a spawn may go when task is reopened.
func untouchedSpawn(spawn, task *Obj) bool {
	h := spawn.List("history")
	if len(h) == 0 {
		return false
	}
	last, ok := h[len(h)-1].(*Obj)
	if !ok || last.Str("event") != "repeat" || spawn.Truthy("keep") {
		return false
	}
	if !pyEqual(spawn.Get("title"), task.Get("title")) ||
		descOf(spawn) != descOf(task) ||
		!pyEqual(listOrEmpty(spawn.Get("tags")), listOrEmpty(task.Get("tags"))) {
		return false
	}
	comments := listOrEmpty(task.Get("comments"))
	for _, c := range listOrEmpty(spawn.Get("comments")) {
		found := false
		for _, d := range comments {
			if pyEqual(c, d) {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}
	return true
}

// descOf is (t.get("description") or "").
func descOf(t *Obj) any {
	if v := t.Get("description"); truthyValue(v) {
		return v
	}
	return ""
}

// uncompleteTask clears the completion and returns active without the
// open instance spawned when it was completed, unless that was changed
// since (untouchedSpawn); protect ids are never removed.
func uncompleteTask(t *Obj, active []*Obj, protect map[string]bool) []*Obj {
	if t.Truthy("recurrence") {
		drop := map[string]bool{}
		for _, a := range active {
			if a.Get("spawned_from") != nil && a.Str("spawned_from") == t.Str("id") && !a.Truthy("completed_at") &&
				untouchedSpawn(a, t) && !protect[a.Str("id")] {
				drop[a.Str("id")] = true
			}
		}
		kept := active[:0:0]
		for _, a := range active {
			if !drop[a.Str("id")] {
				kept = append(kept, a)
			}
		}
		active = kept
	}
	t.Set("completed_at", nil)
	t.Set("wont_do", false)
	logEvent(t, "reopened", nil, nil)
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
			for _, field := range []string{"title", "description"} {
				if s, ok := t.Get(field).(string); ok && s != "" {
					if n := rewrite(s); n != s {
						t.Set(field, n)
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
		if t.Truthy("completed_at") {
			continue
		}
		text := t.Str("title") + "\n" + t.Str("description")
		found, section, anchor := false, "", ""
		for _, m := range wikilinkRE.FindAllStringSubmatch(text, -1) {
			target, _, _ := strings.Cut(m[1], "|")
			page, sec, _ := strings.Cut(target, "#")
			page, sec = pyStrip(page), pyStrip(sec)
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
			out = append(out, TaskLink{ID: t.Str("id"), Title: t.Str("title"), Tags: tagList(t),
				Section: section, Anchor: anchor})
		}
	}
	return out
}

// resolveTaskRefs maps each reference (an id prefix, lowercased) to its
// task, nil when none has it. An open task wins over a completed one; a
// completed recurring task stands for its series: the reference follows
// spawned_from to the occurrence current now.
func resolveTaskRefs(refs []string) map[string]*Obj {
	out := map[string]*Obj{}
	if len(refs) == 0 {
		return out
	}
	deckMu.Lock()
	active := readDeck()
	done := append(readCompleted(), readArchived()...)
	deckMu.Unlock()
	successor := map[string]*Obj{}
	for _, t := range append(append([]*Obj{}, active...), done...) {
		if t.Truthy("spawned_from") {
			successor[t.Str("spawned_from")] = t
		}
	}
	for _, ref := range refs {
		ref = strings.ToLower(ref)
		var task *Obj
		for _, list := range [][]*Obj{active, done} {
			for _, t := range list {
				if strings.HasPrefix(strings.ToLower(t.Str("id")), ref) {
					task = t
					break
				}
			}
			if task != nil {
				break
			}
		}
		seen := map[string]bool{}
		for task != nil && task.Truthy("completed_at") && successor[task.Str("id")] != nil && !seen[task.Str("id")] {
			seen[task.Str("id")] = true
			task = successor[task.Str("id")]
		}
		out[ref] = task
	}
	return out
}

// findTasks: every task whose id starts with prefix (lowercase); only the
// one with exactly that id when there is one.
func findTasks(prefix string) []*Obj {
	deckMu.Lock()
	tasks := append(append(readDeck(), readCompleted()...), readArchived()...)
	deckMu.Unlock()
	var found, exact []*Obj
	for _, t := range tasks {
		id := strings.ToLower(t.Str("id"))
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
func lastActivity(t *Obj) string {
	stamps := []any{t.Get("completed_at"), t.Get("created_at")}
	for _, e := range t.List("history") {
		if o, ok := e.(*Obj); ok {
			stamps = append(stamps, o.Get("at"))
		}
	}
	for _, c := range t.List("comments") {
		if o, ok := c.(*Obj); ok {
			stamps = append(stamps, o.Get("at"))
		}
	}
	best := ""
	for _, s := range stamps {
		if !truthyValue(s) {
			continue
		}
		str := pyStr(s)
		if ymdPrefixRE.MatchString(str) {
			if d := runePrefix(str, 10); d > best {
				best = d
			}
		}
	}
	return best
}

var ymdPrefixRE = regexp.MustCompile(`^` + pyDigit + `{4}-` + pyDigit + `{2}-` + pyDigit + `{2}`)

// cleanRecurrence: a recurrence from a snapshot or an undo, its fields
// validated; nil when it names no repeat type.
func cleanRecurrence(v any) *Obj {
	rec, ok := v.(*Obj)
	if !ok {
		return nil
	}
	rtype, _ := rec.Get("type").(string)
	switch rtype {
	case "daily", "weekly", "monthly", "yearly":
	default:
		return nil
	}
	interval := int64(1)
	if n, ok := rec.Get("interval").(int64); ok && n >= 1 && n <= recIntervalMax {
		interval = n
	}
	from := "due"
	if rec.Str("from") == "completion" {
		from = "completion"
	}
	out := NewObj("type", rtype, "interval", interval, "from", from)
	if n, ok := rec.Get("day").(int64); ok && n >= 1 && n <= 31 {
		out.Set("day", n)
	}
	if n, ok := rec.Get("lead").(int64); ok && n >= 0 {
		out.Set("lead", n)
	}
	return out
}

// sanitizeSnapshot rebuilds a task from an untrusted snapshot (undo after
// delete), known fields only; nil without a usable title. A bad optional
// value falls back to its default.
func sanitizeSnapshot(snap *Obj) *Obj {
	title := pyStrip(pyStr(orNone(snap.Get("title"))))
	if !snap.Truthy("title") || title == "" {
		return nil
	}
	t := newTask("title", title)
	t.Set("description", "")
	if snap.Truthy("description") {
		t.Set("description", pyStr(snap.Get("description")))
	}
	tags, _ := snap.Get("tags").([]any)
	t.Set("tags", strs(cleanTags(tags)))
	for _, f := range []string{"due_date", "start_date", "last_done"} {
		if s, ok := snap.Get(f).(string); ok {
			if _, ok := parseDay(s); ok {
				t.Set(f, s)
			}
		}
	}
	if s, ok := snap.Get("id").(string); ok && s != "" {
		t.Set("id", s)
	}
	for _, f := range []string{"created_at", "completed_at", "spawned_from"} {
		if s, ok := snap.Get(f).(string); ok && s != "" {
			t.Set(f, s)
		}
	}
	switch v := snap.Get("sort_order").(type) {
	case int64, float64:
		t.Set("sort_order", v)
	}
	t.Set("wont_do", snap.Truthy("wont_do"))
	t.Set("keep", snap.Truthy("keep"))
	t.Set("history", cleanHistory(snap.Get("history")))
	t.Set("comments", cleanComments(snap.Get("comments")))
	if rec := cleanRecurrence(snap.Get("recurrence")); rec != nil && !t.Truthy("keep") {
		t.Set("recurrence", rec)
	} else {
		t.Set("recurrence", nil)
	}
	return t
}

// orNone: a missing value as "" (Python's `or ""`).
func orNone(v any) any {
	if !truthyValue(v) {
		return ""
	}
	return v
}
