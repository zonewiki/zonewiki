// What Zone does with tasks beyond keeping them: a series' next occurrence,
// completing and reopening, history and comments, a rename followed in the
// tasks' links, and what pages need from tasks (links to pages, [[task:…]]
// references). A task is a *Task (package taskmodel); its files, their lock
// and their writes are package taskfiles' (taskFiles, below).
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"slices"
	"strings"
	"time"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"
	"zonewiki/internal/taskmodel"
)

// taskFiles: the wiki's task files (package taskfiles): where they are,
// reading them, and writing them under their lock. What the store needs of
// Zone it is handed here: the wiki's folder, its users, who may write, the
// clock, and how a write is committed.
var taskFiles *taskfiles.Store

func init() {
	taskFiles = &taskfiles.Store{
		Root:       func() string { return cfg.Data },
		PrivateDir: privateDir,
		Users:      func() []string { return sortedKeys(settings().Users) },
		Private:    func(user string) bool { return privateFolder(user) != "" },
		NoTasks:    func(user string) bool { return user == visitor },
		MayWrite:   mayWriteTasks,
		Commit:     gitCommit,
		Now:        func() time.Time { return now() },
	}
}

// closedYearsIn: the years with closed tasks in scope (a list, or ""
// for all) that user sees, newest first; this year always.
func closedYearsIn(user, scope string) []string {
	return taskFiles.ClosedYears(user, func(t *Task) bool { return inScope(t, scope) })
}

// timestamp is the current time as tasks store it (created_at, closed_at).
func timestamp() string { return now().Format("2006-01-02T15:04:05") }

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

// uuid4 is a random (version 4) UUID, in its usual 36-character form.
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
		if t.SpawnedFrom != "" && t.SpawnedFrom == task.ID && t.IsOpen() {
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
	next, nextRec := taskmodel.NextOccurrence(rec, base, anchored, day0, skip)
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
	spawned.SortOrder, spawned.Importance = task.SortOrder, task.Importance
	spawned.Recurrence, spawned.SpawnedFrom = nextRec, task.ID
	spawned.Owner = task.Owner // a private series stays private
	spawned.Assignee = task.Assignee
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
	"reopened": true, "wontdo": true, "skipped": true, "repeat": true, "private": true, "shared": true, "assigned": true}

const historyLimit = 30

// logEvent adds an event to a task's history, by user ("" no one: Zone, or
// no users); from and to are the days a dated event moved between, or the
// users an assignment went from and to ("" none).
func logEvent(t *Task, user, event, from, to string) {
	entry := HistoryEntry{At: now().Format("2006-01-02T15:04"), Event: event, By: user}
	if taskmodel.DatedEvents[event] || taskmodel.NamedEvents[event] {
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
	for _, e := range jsonobj.List(raw) {
		at, isStr := jsonobj.As[string](e["at"])
		event, isStrEvent := jsonobj.As[string](e["event"])
		if !isStr || !isStrEvent || !historyEvents[event] {
			continue
		}
		by, _ := jsonobj.As[string](e["by"])
		entry := HistoryEntry{At: runePrefix(at, 16), Event: event, By: byUser(by)}
		if taskmodel.DatedEvents[event] {
			if s, ok := jsonobj.As[string](e["from"]); ok && isoDateRE.MatchString(s) {
				entry.From = s
			}
			if s, ok := jsonobj.As[string](e["to"]); ok && isoDateRE.MatchString(s) {
				entry.To = s
			}
		}
		if taskmodel.NamedEvents[event] {
			from, _ := jsonobj.As[string](e["from"])
			to, _ := jsonobj.As[string](e["to"])
			entry.From, entry.To = byUser(from), byUser(to)
		}
		out = append(out, entry)
	}
	if len(out) > historyLimit {
		out = out[len(out)-historyLimit:]
	}
	return out
}

// ---- comments ----

// A task's comments: markdown, a line or several, oldest first, {id, at, by, text}
// (by: who wrote it, with users), never capped; undo of other changes
// leaves them alone.
const commentMax = 2000

func newCommentID() string { return strings.ReplaceAll(uuid4(), "-", "")[:8] }

// cleanComments validates a comment list from a request or snapshot.
func cleanComments(raw json.RawMessage) []Comment {
	var cs []Comment
	for _, c := range jsonobj.List(raw) {
		text, isText := jsonobj.As[string](c["text"])
		at, isAt := jsonobj.As[string](c["at"])
		if !isText || !isAt {
			continue
		}
		id, _ := jsonobj.As[string](c["id"])
		by, _ := jsonobj.As[string](c["by"])
		if !snapshotIDRE.MatchString(id) {
			id = "" // a new one (cleanCommentList)
		}
		cs = append(cs, Comment{ID: id, At: at, By: byUser(by), Text: text})
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

// completeTask stamps a task done (by user, at at; "" now); returns
// the spawned recurring instance, if any.
func completeTask(user string, t *Task, at string, active []*Task) *Task {
	if at == "" {
		at = timestamp()
	}
	t.ClosedAt, t.WontDo = at, false
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
	if spawn.Title != task.Title || spawn.Description != task.Description || !slices.Equal(spawn.Tags, task.Tags) ||
		spawn.Importance != task.Importance {
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
// open instance spawned when it was closed, unless that was changed
// since (untouchedSpawn); protect ids are never removed.
func uncompleteTask(user string, t *Task, active []*Task, protect map[string]bool) []*Task {
	if t.Recurrence != nil {
		kept := active[:0:0]
		for _, a := range active {
			if a.SpawnedFrom != "" && a.SpawnedFrom == t.ID && a.IsOpen() && untouchedSpawn(a, t) && !protect[a.ID] {
				continue
			}
			kept = append(kept, a)
		}
		active = kept
	}
	t.ClosedAt, t.WontDo = "", false
	logEvent(t, user, "reopened", "", "")
	return active
}

// renamedTags: tags with old (lowercase) as name, first spelling kept if the
// task has both; changed: they differ from tags.
func renamedTags(tags []string, old, name string) (out []string, changed bool) {
	out = slices.Clone(tags)
	for i, t := range out {
		if strings.ToLower(t) == old {
			out[i] = name
		}
	}
	out = taskmodel.CleanTags(out)
	return out, !slices.Equal(out, tags)
}

// renamePageRefs follows a page rename in the tasks: links in task titles
// and descriptions rewritten (by rewrite, given the files' owner: "" the
// shared ones), every owner's tasks. Writes the files that changed, doesn't
// commit: returns them for the rename's commit.
func renamePageRefs(rewrite func(owner, text string) string) ([]string, error) {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	var changed []string
	for _, f := range taskFiles.Files(taskFiles.EveryOwner()) {
		p := f.Path
		tasks, err := taskfiles.ReadStrict(p)
		if err != nil {
			return changed, err
		}
		dirty := false
		for _, t := range tasks {
			for _, field := range []*string{&t.Title, &t.Description} {
				if *field != "" {
					if n := rewrite(f.Owner, *field); n != *field {
						*field = n
						dirty = true
					}
				}
			}
		}
		if dirty {
			if err := taskfiles.Write(p, tasks); err != nil {
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

// findTaskBacklinks: those of the open tasks (a user's: rq.openTasks)
// whose title or notes link to slug (a wikilink, or a markdown link to
// /page/<slug>).
func findTaskBacklinks(open []*Task, slug string) []TaskLink {
	var out []TaskLink
	for _, t := range open {
		text := t.Title + "\n" + t.Description
		found, section, anchor := false, "", ""
		for _, m := range mdtext.WikilinkRE.FindAllStringSubmatch(text, -1) {
			l := parseWikilink(m[0])
			page, sec := strings.TrimSpace(l.page), strings.TrimSpace(l.section)
			if page != "" && (page == slug || mdtext.ToSlug(page) == slug) {
				found = true
				if sec != "" && section == "" {
					section, anchor = sec, mdtext.SectionAnchor(sec)
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
// task is as none). An open task wins over a closed one; a closed
// recurring task stands for its series: the reference follows
// spawned_from to the occurrence current now.
func resolveTaskRefs(user string, refs []string) map[string]*Task {
	out := map[string]*Task{}
	if len(refs) == 0 {
		return out
	}
	active, done := taskFiles.Snapshot(user)
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
		for task != nil && !task.IsOpen() && successor[task.ID] != nil && !seen[task.ID] {
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
	tasks := taskFiles.All(user)
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
	stamps := []string{t.ClosedAt, t.CreatedAt}
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

// What a snapshot's ids and times may be (sanitizeSnapshot): an id as Zone
// makes them (a UUID) or as a file written by hand may have it (letters,
// digits, - and _), a time as timestamp() writes it, or its day alone.
var (
	snapshotIDRE   = regexp.MustCompile(`^[0-9A-Za-z_-]{1,64}$`)
	snapshotTimeRE = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$`)
)

// byUser: who a snapshot's history entry or comment says it is by, if that
// could be a user's name; else no one.
func byUser(by string) string {
	if userNameRE.MatchString(by) {
		return by
	}
	return ""
}

// sanitizeSnapshot rebuilds a task from an untrusted snapshot (undo after
// delete), known fields only; nil without a usable title. A bad optional
// value falls back to its default.
func sanitizeSnapshot(snap jsonObj) *Task {
	title, _ := jsonobj.As[string](snap["title"])
	if title = strings.TrimSpace(title); title == "" {
		return nil
	}
	t := newTask()
	t.Title = title
	t.Description, _ = jsonobj.As[string](snap["description"])
	t.Tags = taskmodel.CleanTags(jsonobj.TextList(snap["tags"]))
	// An assignee only as a write would take it: a snapshot is whatever
	// its sender made it, and an export may come from another wiki.
	if s, ok := jsonobj.As[string](snap["assignee"]); ok && settings().assignable(taskmodel.CleanAssignee(s)) {
		t.Assignee = taskmodel.CleanAssignee(s)
	}
	for f, field := range map[string]*string{"due_date": &t.DueDate, "start_date": &t.StartDate, "last_done": &t.LastDone} {
		if s, ok := jsonobj.As[string](snap[f]); ok {
			if _, ok := parseDay(s); ok {
				*field = s
			}
		}
	}
	// Ids and times only as Zone writes them: the browser builds the cards
	// from them, and a snapshot is whatever its sender made it. An id of
	// another shape gets a new one (newTask's), a time that is none is now.
	for f, field := range map[string]*string{"id": &t.ID, "spawned_from": &t.SpawnedFrom} {
		if s, ok := jsonobj.As[string](snap[f]); ok && snapshotIDRE.MatchString(s) {
			*field = s
		}
	}
	if s, ok := jsonobj.As[string](snap["created_at"]); ok && snapshotTimeRE.MatchString(s) {
		t.CreatedAt = s
	}
	if s, ok := jsonobj.As[string](snap["closed_at"]); ok && s != "" {
		if t.ClosedAt = s; !snapshotTimeRE.MatchString(s) {
			t.ClosedAt = timestamp()
		}
	}
	if f, ok := jsonobj.As[float64](snap["sort_order"]); ok {
		t.SortOrder = &f
	}
	t.WontDo, t.Keep = jsonobj.True(snap["wont_do"]), jsonobj.True(snap["keep"])
	importance, _ := jsonobj.As[string](snap["importance"])
	t.Importance = taskmodel.CleanImportance(importance)
	t.History = cleanHistory(snap["history"])
	t.Comments = cleanComments(snap["comments"])
	if !t.Keep {
		t.Recurrence = taskmodel.CleanRecurrence(snap["recurrence"])
	}
	return t
}
