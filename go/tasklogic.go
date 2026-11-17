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
	"net/http"
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
		Member:     listMember,
		Commit:     gitCommit,
		Now:        func() time.Time { return now() },
	}
}

// closedYearsIn: the years with closed tasks in scope (a list, or ""
// for all) that user sees, newest first; this year always.
func closedYearsIn(user, scope string) []string {
	lists := taskLists(user)
	return taskFiles.ClosedYears(user, scopeFilter(user, scope, lists))
}

// tasksWord: the word of the tasks user's views of scopes hold
// (taskFiles.Word): another one after a write to a list one of them shows,
// to the user's Inbox, or to a registry they read; not after a write to a
// list none of them shows, nor to another user's own tasks. A scope that
// is every task ("": All tasks, a tag's view), My tasks or several lists
// takes every list the user sees; no scope at all is every task.
func tasksWord(user string, scopes ...string) string {
	every := []taskfiles.Place{}
	for _, o := range taskFiles.Owners(user) {
		every = append(every, taskfiles.Place{Owner: o, Every: true})
	}
	lists := taskLists(user)
	var places []taskfiles.Place
	for _, scope := range scopes {
		l, ok := resolveList(scope, lists)
		switch {
		case ok:
			places = append(places, taskfiles.Place{Owner: l.Owner, List: l.Name})
		case scope != "" && isInbox(scope, lists):
			places = append(places, taskfiles.Place{Owner: inboxOwner(user)})
		default:
			return taskFiles.Word(user, every)
		}
	}
	if len(places) == 0 {
		return taskFiles.Word(user, every)
	}
	// The registries the user reads, whatever the lists: a list made, a pin.
	for _, o := range taskFiles.Owners(user) {
		places = append(places, taskfiles.Place{Owner: o})
	}
	return taskFiles.Word(user, places)
}

// heldScopes: the scopes of the lists a page holds, as its requests name
// them (?held=a,b: js/chrome.js pageScopes); none: every task.
func heldScopes(r *http.Request) []string {
	var out []string
	for _, s := range strings.Split(r.URL.Query().Get("held"), ",") {
		if s = cleanList(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// queryScope: the scope a request names: ?mine=1 My tasks (mineScope),
// ?list= one list (by its ref), ?lists= several ("|" between), as
// scopeFilter takes them; "" for every task.
func queryScope(r *http.Request) string {
	if isTrue(r.URL.Query().Get("mine")) {
		return mineScope
	}
	if s := strings.TrimSpace(r.URL.Query().Get("lists")); s != "" {
		return strings.ToLower(s)
	}
	return cleanList(r.URL.Query().Get("list"))
}

// placeSnapshot: where a task from a snapshot (an undo's, an export's)
// goes for user: the list it names, when user sees a list of that name
// that is as private as the snapshot says it was. A private task whose
// list is gone (or that was in none) goes to user's Inbox. A shared one
// whose list is gone goes, with users, to the shared list "general"
// (general: it has to be there, ensureGeneralLocked), since a shared task
// is always in a list; without users to no list, the wiki's Inbox. So a
// snapshot never makes a shared task private nor a private one shared. A
// private task has no assignee.
func placeSnapshot(user string, t *Task, snap jsonObj, lists map[string]ListInfo) (general bool) {
	private := jsonobj.True(snap["private"]) && privateFolder(user) != ""
	name, _ := jsonobj.As[string](snap["list"])
	name = cleanList(name)
	t.List, t.Owner = "", ""
	found := false
	for _, l := range lists {
		if name != "" && l.Name == name && l.Private() == private {
			t.List, t.Owner, found = l.Name, l.Owner, true
		}
	}
	switch {
	case found:
	case private:
		t.Owner = user
	case settings().Users != nil:
		t.List, general = generalListName, true
	}
	if t.Owner != "" {
		t.Assignee = ""
	}
	return general
}

// givenToOthers: how many of the open shared tasks user made someone else
// has: what they gave away, and are waiting for. My tasks links to them
// (givenHref): they are not the user's to do, so they are not in it.
func givenToOthers(user string, open []*Task) int {
	n := 0
	for _, t := range open {
		if t.Owner == "" && t.Assignee != "" && t.Assignee != user && t.Maker() == user {
			n++
		}
	}
	return n
}

// givenHref: the view of what the user gave to others: All tasks, of the
// tasks from them that are not theirs to do, a section per person.
const givenHref = "/tasks?view=open&group=person&from=me&assignee=-me,-none"

// News: what someone else did on a task that user has to do with and
// hasn't seen: its kind, who did it and when ("" kind: nothing).
type News struct{ Kind, By, At string }

// The kinds of news: a task given to the user (its card says "new"), a
// comment, a mention of them, the task closed or reopened, and for who made
// it that it was given to someone else.
const (
	newsGiven    = "given"
	newsComment  = "comment"
	newsMention  = "mention"
	newsClosed   = "closed"
	newsReopened = "reopened"
	newsHandedOn = "handed"
)

// newsFor: what a shared task has for user that they haven't seen (Updates
// in docs/spec/tasks.md): its mark on their cards, and what the Tasks links
// count (TaskCounts.New). From the task and from when user last looked at
// it (taskseen.go). User has to do with a task they are assigned, that they
// made or that they follow (Task.Followers: who follows is told what who
// made it is told); a mention reaches them whatever the task. What counts is someone else's and
// after the last thing user did on the task (anything of theirs in its
// history, or their look at it, "seen", which is noted when they open the
// task: markSeen; or a comment of theirs):
//   - it was given to them: the last "assigned" entry, or "created" for a
//     task made with their name on it, while it is open;
//   - a comment;
//   - a mention of them ("mentioned", logged with the comment or the
//     description that names them);
//   - it was closed, or reopened;
//   - for who made it or follows it: it was given to someone else.
//
// Not a date, a tag, the title or the order. No time takes news away: a
// task given a week ago and not looked at is the one that needs the mark.
// The latest of them is the news. History entries are in order, comments
// are, and between the two the minute decides: within one minute a comment
// of the user's own, or their look at the task ("seen"), counts as the
// later one. A look says which comment was the last one then (its "to": a
// comment's id, noComment for none; taskseen.go), and every comment after
// that one is news, whatever the minute: one written a moment after the
// look is not lost. A look that doesn't say (from before 2026-11-16) goes
// by the minute. The browser has the same rule
// (TasksCore.newsFor; go/testdata/news_cases.json runs through both).
func newsFor(t *Task, user string) News {
	if user == "" || t.Owner != "" {
		return News{}
	}
	minute := func(at string) string { return runePrefix(at, 16) }
	has, made := t.Assignee == user, t.Maker() == user || slices.Contains(t.Followers, user)
	mine, myAct, sawIt, sawComment := -1, "", false, ""
	lastAssigned := -1
	// With who looked at it (taskseen.go): a look is the user's, not the task's.
	history := historyWithSeen(t)
	for i, e := range history {
		if e.By == user {
			mine, myAct, sawIt, sawComment = i, minute(e.At), e.Event == "seen", ""
			if sawIt {
				sawComment = e.To
			}
		}
		if e.Event == "assigned" {
			lastAssigned = i
		}
	}
	myComment, said := -1, ""
	for i, c := range t.Comments {
		if c.By == user {
			myComment, said = i, minute(c.At)
		}
	}
	var news News
	take := func(kind, by, at string) {
		if at >= news.At {
			news = News{kind, by, at}
		}
	}
	for i := mine + 1; i < len(history); i++ {
		e := history[i]
		at := minute(e.At)
		if e.By == "" || e.By == user || said != "" && at <= said {
			continue
		}
		to := strings.ToLower(e.To)
		switch e.Event {
		case "assigned":
			if has && to == user && i == lastAssigned && t.IsOpen() {
				take(newsGiven, e.By, at)
			} else if made && to != "" && to != user {
				take(newsHandedOn, e.By, at)
			}
		case "created":
			if has && lastAssigned < 0 && t.IsOpen() {
				take(newsGiven, e.By, at)
			}
		case "done", "wontdo":
			if (has || made) && !t.IsOpen() {
				take(newsClosed, e.By, at)
			}
		case "reopened":
			if (has || made) && t.IsOpen() {
				take(newsReopened, e.By, at)
			}
		case "mentioned":
			if to == user {
				take(newsMention, e.By, at)
			}
		}
	}
	if has || made {
		// after: the look named the last comment it saw, and the task still
		// has it (or had none then): what comes after it is news.
		from, after := myComment+1, sawComment == noComment
		if i := slices.IndexFunc(t.Comments, func(c Comment) bool { return c.ID != "" && c.ID == sawComment }); i >= 0 {
			from, after = max(from, i+1), true
		}
		for _, c := range t.Comments[from:] {
			if at := minute(c.At); c.By != "" && c.By != user && (after || myAct == "" || at > myAct || at == myAct && !sawIt) {
				take(newsComment, c.By, at)
			}
		}
	}
	return news
}

// updatesFor: the tasks with news for user (newsFor): of the open tasks
// they see, whatever list they are in, and of the closed ones of this month
// and the last (a task they gave away that was done). What the Tasks links
// count, and what My tasks shows under Updates.
func updatesFor(user string, open, recentClosed []*Task) []*Task {
	if user == "" || settings().Users == nil {
		return nil
	}
	var out []*Task
	for _, t := range slices.Concat(open, recentClosed) {
		if newsFor(t, user).Kind != "" {
			out = append(out, t)
		}
	}
	return out
}

// recentUpdates: the closed tasks of this month and the last that have news
// for user (taskFiles.RecentClosed copies those alone).
func recentUpdates(user string) []*Task {
	if user == "" || settings().Users == nil {
		return nil
	}
	return taskFiles.RecentClosed(user, func(t *Task) bool { return newsFor(t, user).Kind != "" })
}

// withUpdates: tasks (those a view of My tasks holds) and, after them, the
// tasks with news for user that aren't among them: what they gave away,
// what mentions them, what was closed. My tasks shows those under Updates
// alone.
func withUpdates(user string, tasks, open, recentClosed []*Task) []*Task {
	have := make(map[string]bool, len(tasks))
	for _, t := range tasks {
		have[t.ID] = true
	}
	for _, t := range updatesFor(user, open, recentClosed) {
		if !have[t.ID] {
			tasks = append(tasks, t)
		}
	}
	return tasks
}

// givenTo: someone else gave user this task and they haven't opened it: the
// news its card calls "new".
func givenTo(t *Task, user string) bool { return newsFor(t, user).Kind == newsGiven }

// mentionRE: @name in a text: a user's name after a space, a bracket or the
// text's start (not the @ of a mail address).
var mentionRE = regexp.MustCompile(`(?:^|[\s(\[{>,;:])@([a-z0-9][a-z0-9-]*)`)

// mentionsOf: the users a comment or a description names with @name, each
// once, in the text's order: users who can be given tasks, not in code.
func mentionsOf(text string) []string {
	s := settings()
	if s.Users == nil || !strings.Contains(text, "@") {
		return nil
	}
	masked, _ := mdtext.MaskCode(text)
	var out []string
	for _, m := range mentionRE.FindAllStringSubmatch(strings.ToLower(masked), -1) {
		if name := m[1]; s.assignable(name) && !slices.Contains(out, name) {
			out = append(out, name)
		}
	}
	return out
}

// logMentions logs "mentioned" for each user text names that before didn't
// (a comment: before is ""; a description: what it was), by user, on a
// shared task: it is news to them (newsFor), and whoever turned it on is
// mailed (tellMentioned). Not for naming oneself. Returns who was named.
func logMentions(t *Task, user, before, text string) []string {
	if t.Owner != "" || user == "" {
		return nil
	}
	had := mentionsOf(before)
	var named []string
	for _, name := range mentionsOf(text) {
		// (In a list for some, a name that is none of them is text: they
		// don't see the task.)
		if name != user && !slices.Contains(had, name) && taskFiles.SeesList(name, t.List) {
			logEvent(t, user, "mentioned", "", name)
			named = append(named, name)
		}
	}
	return named
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

// uuid4RE: an id as uuid4 makes them.
var uuid4RE = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

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
	spawned.Owner, spawned.List = task.Owner, task.List // a series stays in its list (a private one private)
	spawned.Assignee = task.Assignee
	spawned.CreatedBy = task.Maker()                 // the series' maker
	spawned.Followers = slices.Clone(task.Followers) // who follows the series follows on
	// The next occurrence carries the series' history on (it is capped:
	// capHistory), so the series reads as one log. Not its comments: they
	// have no cap, and each occurrence was a copy more of all of them (a
	// daily task with ten comments: ten more a day, for good, until 2026-11).
	// They stay on the occurrence they were written on, which this one names
	// (spawned_from; its dialog links to it).
	spawned.History = append([]HistoryEntry{}, task.History...)
	spawned.Comments = nil
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
	"reopened": true, "wontdo": true, "skipped": true, "repeat": true, "private": true, "shared": true, "assigned": true, "seen": true, "mentioned": true}

const historyLimit = 30

// logEvent adds an event to a task's history, by user ("" no one: Zone, or
// no users); from and to are the days a dated event moved between, or the
// users an assignment went from and to ("" none).
func logEvent(t *Task, user, event, from, to string) {
	entry := HistoryEntry{At: now().Format("2006-01-02T15:04"), Event: event, By: user}
	if taskmodel.DatedEvents[event] || taskmodel.NamedEvents[event] {
		entry.From, entry.To = from, to
	}
	t.History = capHistory(append(append([]HistoryEntry{}, t.History...), entry))
}

// capHistory: h with at most historyLimit entries beside those a task is
// read by for good: who made it ("created"), who has it (the last
// "assigned") and who shared it (the last "shared"). The oldest of the rest
// go: a task's dates moved forty times still says who made it, and its news
// is still right (newsFor).
func capHistory(h []HistoryEntry) []HistoryEntry {
	kept := map[int]bool{}
	for i, e := range h {
		if e.Event == "created" {
			kept[i] = true
			break
		}
	}
	for _, event := range []string{"assigned", "shared"} {
		for i := len(h) - 1; i >= 0; i-- {
			if h[i].Event == event {
				kept[i] = true
				break
			}
		}
	}
	over := len(h) - len(kept) - historyLimit
	if over <= 0 {
		return h
	}
	out := make([]HistoryEntry, 0, len(h)-over)
	for i, e := range h {
		if !kept[i] && over > 0 {
			over--
			continue
		}
		out = append(out, e)
	}
	return out
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
	return capHistory(out)
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
			taskFiles.Hold()
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
	List, Owner     string // the list the task is in, and whose private task it is (Task.List, Task.Owner)
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
			out = append(out, TaskLink{ID: t.ID, Title: t.Title, List: t.List, Owner: t.Owner, Section: section, Anchor: anchor})
		}
	}
	return out
}

// taskRecords: the tasks user sees as the search keeps them (taskSearchDocs:
// a record each, the task without its description, comments and history),
// the open ones and the closed ones. Only to read: they are the search's
// own. What needs a task by its id asks here, so no closed task of the
// wiki's years is read and copied to show a chip.
func taskRecords(user string) (open, done []*SearchDoc) {
	for _, d := range taskSearchDocs() {
		switch {
		case !canSeeTask(user, d.Task):
		case d.Task.IsOpen():
			open = append(open, d)
		default:
			done = append(done, d)
		}
	}
	return open, done
}

// resolveTaskRefs maps each reference (an id prefix, lowercased) to its
// task among those user sees, nil when none has it (another user's private
// task is as none). An open task wins over a closed one; a closed
// recurring task stands for its series: the reference follows
// spawned_from to the occurrence current now. The tasks are records
// (taskRecords): what a chip, a link or a line of facts shows, to read only.
func resolveTaskRefs(user string, refs []string) map[string]*Task {
	out := map[string]*Task{}
	if len(refs) == 0 {
		return out
	}
	active, done := taskRecords(user)
	successor := map[string]*Task{}
	for _, d := range slices.Concat(active, done) {
		if d.Task.SpawnedFrom != "" {
			successor[d.Task.SpawnedFrom] = d.Task
		}
	}
	for _, ref := range refs {
		ref = strings.ToLower(ref)
		var task *Task
		for _, list := range [][]*SearchDoc{active, done} {
			for _, d := range list {
				if strings.HasPrefix(strings.ToLower(d.Task.ID), ref) {
					task = d.Task
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
// only the one with exactly that id when there is one. Each whole, read
// from its file.
func findTasks(user, prefix string) []*Task {
	var found, exact []*Task
	open, done := taskRecords(user)
	for _, d := range slices.Concat(open, done) {
		id := strings.ToLower(d.Task.ID)
		if strings.HasPrefix(id, prefix) {
			t := d.wholeTask()
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
	t.History = sentHistory(cleanHistory(snap["history"]), nil)
	t.Comments = cleanComments(snap["comments"])
	if !t.Keep {
		t.Recurrence = taskmodel.CleanRecurrence(snap["recurrence"])
	}
	return t
}
