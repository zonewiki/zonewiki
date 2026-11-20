// Task writes: creating, changing, commenting on, tagging, moving in the
// manual order and deleting tasks, as functions the /api/tasks routes
// (tasksapi.go) and the MCP tools (mcptasks.go) share, and the reading of
// the values a write is given.
package main

import (
	"cmp"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/taskmodel"
)

func asStr(raw json.RawMessage, name string) (string, error) {
	s, ok := jsonobj.As[string](raw)
	if !ok {
		return "", refuse(name + " must be a string")
	}
	return s, nil
}

func asBool(raw json.RawMessage, name string) (bool, error) {
	b, ok := jsonobj.As[bool](raw)
	if !ok {
		return false, refuse(name + " must be true or false")
	}
	return b, nil
}

// boolField: data's k as true or false; false when it is missing.
func boolField(data jsonObj, k string) (bool, error) {
	if !data.Has(k) {
		return false, nil
	}
	return asBool(data[k], k)
}

// asInt: a whole number, or a string of one ("12", " -3 ").
func asInt(raw json.RawMessage, name string) (int, error) {
	if n, ok := jsonobj.WholeNumber(raw); ok {
		return n, nil
	}
	if s, ok := jsonobj.As[string](raw); ok {
		if n, err := strconv.Atoi(strings.TrimSpace(s)); err == nil {
			return n, nil
		}
	}
	return 0, refuse(name + " must be an integer")
}

// asStrList: a list of strings, or one string split on commas; stripped,
// empties left out.
func asStrList(raw json.RawMessage, name string) ([]string, error) {
	items, ok := jsonobj.As[[]string](raw)
	if s, isStr := jsonobj.As[string](raw); isStr {
		items, ok = strings.Split(s, ","), true
	}
	if !ok {
		return nil, refuse(name + " must be a list of strings")
	}
	out := []string{}
	for _, s := range items {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// listField: data's k as a list, [] when it is missing; ok false when it
// is something else.
func listField(data jsonObj, k string) ([]json.RawMessage, bool) {
	if !data.Has(k) {
		return []json.RawMessage{}, true
	}
	return jsonobj.As[[]json.RawMessage](data[k])
}

// idList: the body's ids, a list of task-id strings.
func idList(data jsonObj) ([]string, error) {
	if !data.Has("ids") {
		return []string{}, nil
	}
	ids, ok := jsonobj.As[[]string](data["ids"])
	if !ok {
		return nil, refuse("ids must be a list of task ids")
	}
	return ids, nil
}

// strptime's %Y-%m-%d: four digits, then a month and a day of one or two.
var strptimeDayRE = regexp.MustCompile(`^(\d{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])$`)

// cleanDay: s as a task's date is kept, YYYY-MM-DD with its month and day
// zero-padded; ok false when it is no day. Read as strptime's %Y-%m-%d (a
// month and a day of one or two digits, a space before a day of one), so a
// date the API took before is still one; kept padded, since every date is
// compared as text, here and in the browser (2026-1-5 would sort after
// 2026-10-01).
func cleanDay(s string) (string, bool) {
	m := strptimeDayRE.FindStringSubmatch(s)
	if m == nil {
		return "", false
	}
	y, _ := strconv.Atoi(m[1])
	mo, _ := strconv.Atoi(m[2])
	d, _ := strconv.Atoi(strings.TrimSpace(m[3]))
	if d > taskmodel.DaysIn(y, time.Month(mo)) {
		return "", false
	}
	return fmt.Sprintf("%s-%02d-%02d", m[1], mo, d), true
}

// validateDate: raw as a date field: null or "" is none (""), else a
// valid date, as YYYY-MM-DD (cleanDay).
func validateDate(raw json.RawMessage) (string, error) {
	s, ok := jsonobj.As[string](raw)
	if jsonobj.IsNull(raw) || ok && s == "" {
		return "", nil
	}
	day, valid := cleanDay(s)
	if !ok || !valid {
		return "", refuse("Invalid date format, expected YYYY-MM-DD")
	}
	return day, nil
}

// commentText: a comment's text, stripped and capped; empty is refused.
func commentText(s string) (string, error) {
	// Lines as a file keeps them, whatever the browser's form sent.
	s = strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
	if s = strings.TrimSpace(s); s == "" {
		return "", refuse("Comment cannot be empty")
	}
	return runePrefix(s, commentMax), nil
}

// createTask: a new open task from the fields in data (as the API takes
// them); the MCP tools share it.
func createTask(user asker, data jsonObj, message string) (*Task, error) {
	title := ""
	if data.Has("title") {
		var err error
		if title, err = asStr(data["title"], "title"); err != nil {
			return nil, err
		}
	}
	if strings.TrimSpace(title) == "" {
		return nil, refuse("Title cannot be empty")
	}
	pos, positioned, err := positionField(data)
	if err != nil {
		return nil, err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	t := newTask()
	// id: the id its dialog made up, so that the files pasted there before
	// it was saved are its files (taskUploadPlace). One of Zone's form, that
	// no task has, and whose folder, if it has one, is user's: the files
	// they pasted (seesTaskFiles), not another's nor a deleted task's.
	if data.Has("id") && !data.Null("id") {
		id, err := asStr(data["id"], "id")
		if err != nil {
			return nil, err
		}
		if !uuid4RE.MatchString(id) {
			return nil, refuse("id must be a new task's id as Zone makes them (a version 4 UUID); leave it out")
		}
		_, err = os.Stat(filepath.Join(cfg.Data, filepath.FromSlash(taskFilesDir), id))
		if taskFiles.IDsLocked()[id] || err == nil && !seesTaskFiles(user, id) {
			return nil, refuseAs(refusedExists, "There is a task with this id already")
		}
		t.ID = id
	}
	if err := applyTaskFields(user, t, data, true); err != nil {
		return nil, err
	}
	t.History = []HistoryEntry{} // its first dates are not changes
	logEvent(t, user.Name, "created", "", "")
	if settings().Users != nil {
		t.CreatedBy = user.Name // who gave it, when it is someone else's to do
	}
	named := logMentions(t, user, "", t.Description)
	active, err := taskFiles.ReadOpenStrict(user)
	if err != nil {
		return nil, err
	}
	active = append(active, t)
	// position: where it goes in the manual order; without, at the top, as
	// a new task goes (unless the write gives it a sort_order itself).
	if !positioned && t.SortOrder == nil {
		pos, positioned = orderPos{At: "top"}, true
	}
	if positioned {
		if err := moveTo(active, []string{t.ID}, pos); err != nil {
			return nil, err
		}
	}
	if err := taskFiles.WriteOpenLocked(user, active, message); err != nil {
		return t, err
	}
	dropUnsaved(t.ID) // its files, if any were pasted before, are its readers' now
	tellAssigned(user.Name, nil, t)
	tellMentioned(user.Name, t, named)
	return t, nil
}

// applyTaskFields applies the field changes in data (not completion); fresh:
// t is a task being created, the only time it may go into a private list.
func applyTaskFields(user asker, t *Task, data jsonObj, fresh bool) error {
	oldDue, oldStart, oldRec := t.DueDate, t.StartDate, t.Recurrence
	if oldRec == nil {
		oldRec = &Recurrence{}
	}
	// In this order: the history logs a due date's change before a start
	// date's, and the recurrence goes by both.
	steps := []func() error{
		func() error { return applyText(user, t, data) },
		func() error { return applyDate(user, t, data, "due_date", "due", &t.DueDate) },
		func() error { return applyOrder(t, data) },
		func() error { return applyList(user, t, data, fresh) },
		func() error { return applyAssignee(user, t, data) }, // after its list: a private task has none
		func() error { return applyFollow(user, t, data) },   // after its list too: only a shared task is followed
		func() error { return applyDate(user, t, data, "last_done", "", &t.LastDone) },
		func() error { return applyDate(user, t, data, "start_date", "start", &t.StartDate) },
		func() error { return applyRecurrence(t, data, oldDue, oldStart, oldRec) },
	}
	for _, step := range steps {
		if err := step(); err != nil {
			return err
		}
	}
	// Keep and Repeat answer the same question: a keep task never repeats.
	if t.Keep {
		t.Recurrence = nil
	}
	return nil
}

// applyText: a write's title, description, tags and comments.
func applyText(user asker, t *Task, data jsonObj) error {
	if data.Has("title") {
		s, err := asStr(data["title"], "title")
		if err != nil {
			return err
		}
		if s = strings.TrimSpace(s); s == "" {
			return refuse("Title cannot be empty")
		}
		t.Title = s
	}
	if data.Has("description") {
		t.Description = ""
		if !data.Null("description") {
			s, err := asStr(data["description"], "description")
			if err != nil {
				return err
			}
			t.Description = strings.TrimSpace(s)
		}
	}
	if data.Has("tags") {
		t.Tags = []string{}
		if !data.Null("tags") {
			tags, err := asStrList(data["tags"], "tags")
			if err != nil {
				return err
			}
			t.Tags = taskmodel.CleanTags(tags)
		}
	}
	if data.Has("comments") { // the whole list, for undoing a comment's delete or edit
		if _, ok := jsonobj.As[[]json.RawMessage](data["comments"]); !ok {
			return refuse("comments must be a list")
		}
		old := t.Comments
		t.Comments = cleanComments(data["comments"])
		checkBy(user.Name, t)
		ownNewComments(user.Name, old, t)
		if err := othersCommentsKept(user, t.ID, old, t.Comments); err != nil {
			t.Comments = old
			return err
		}
	}
	return nil
}

// applyDate: a write's date key into field; a change is logged as event
// ("": not logged).
func applyDate(user asker, t *Task, data jsonObj, key, event string, field *string) error {
	if !data.Has(key) {
		return nil
	}
	d, err := validateDate(data[key])
	if err != nil {
		return err
	}
	old := *field
	*field = d
	if event != "" && d != old {
		logEvent(t, user.Name, event, old, d)
	}
	return nil
}

// applyOrder: a write's sort_order and keep.
func applyOrder(t *Task, data jsonObj) error {
	if data.Has("sort_order") {
		f, ok := jsonobj.As[float64](data["sort_order"])
		switch {
		case data.Null("sort_order"):
			t.SortOrder = nil
		case ok:
			t.SortOrder = &f
		default:
			return refuse("sort_order must be a number")
		}
	}
	if data.Has("keep") {
		keep, err := asBool(data["keep"], "keep")
		if err != nil {
			return err
		}
		t.Keep = keep
	}
	if data.Null("importance") && data.Has("importance") {
		t.Importance = "" // null: normal
	} else if data.Has("importance") {
		s, ok := jsonobj.As[string](data["importance"])
		switch s = strings.ToLower(strings.TrimSpace(s)); {
		case ok && (s == "high" || s == "low" || s == "normal" || s == ""):
			t.Importance = taskmodel.CleanImportance(s)
		default:
			return refuse("importance must be high, normal or low")
		}
	}
	return nil
}

// applyList: a write's list: the list the task is in, as user names it (a
// ref), or none ("", null, "inbox"): the Inbox. Where a task is kept
// follows its list: in a private list or in its user's Inbox it is theirs
// alone, in a shared list shared. A new task that names no list is in its
// maker's Inbox; it may start in any list. A shared task never goes into a
// private list nor into an Inbox (with users a shared task is always in a
// list), and a private task leaves for a shared list only when the write
// says share. Only a change is checked. private is no field of a write: a
// task is private by its list.
func applyList(user asker, t *Task, data jsonObj, fresh bool) error {
	if data.Has("private") {
		return refuse("private is not set on a task: a task is private when its list is. Give list")
	}
	if !data.Has("list") {
		if fresh {
			if lacksPrivate(user) {
				return refuse(noPrivateMsg)
			}
			t.List, t.Owner = "", inboxOwner(user)
		}
		return nil
	}
	ref := ""
	if !data.Null("list") {
		s, err := asStr(data["list"], "list")
		if err != nil {
			return err
		}
		ref = cleanList(s)
	}
	share, err := boolField(data, "share")
	if err != nil {
		return err
	}
	lists := taskLists(user)
	name, owner := "", inboxOwner(user)
	if ref != "" && !isInbox(ref, lists) {
		l, ok := resolveList(ref, lists)
		if !ok {
			return refuse("No list #" + ref)
		}
		if !canWriteList(user, l) {
			return errReadOnly
		}
		name, owner = l.Name, l.Owner
	}
	if !fresh && name == t.List && owner == t.Owner {
		return nil
	}
	if owner != "" && lacksPrivate(user) {
		return refuse(noPrivateMsg) // into the Inbox: the lists it sees are shared ones
	}
	switch {
	case fresh:
	case owner != "" && t.Owner == "" && name == "":
		return errSharedListed
	case owner != "" && t.Owner == "":
		return errSharedStays
	case owner == "" && t.Owner != "" && !share:
		return shareNeeded("The task")
	case owner == "" && t.Owner != "":
		t.Owner = owner
		sharedBy(t, user.Name, !data.Has("assignee"))
	case owner == "":
		// Between shared lists: to a list fewer people see only by who made
		// the task or an admin, as a page goes to fewer people (checkReaders).
		if lost := taskLosers(t.List, name); len(lost) > 0 && !mayNarrowTask(user, t) {
			return refuseAs(refusedReadOnly, namesOr(lost, 3, "")+" would no longer see the task: #"+name+" is for fewer people than it is seen by now. "+
				"Only who made a task, or an admin, moves it there")
		}
	}
	t.List, t.Owner = name, owner
	return nil
}

// taskLosers: who sees a shared task in the list from and wouldn't in the
// list to (a list for some that they are none of), A–Z; none without
// users, and when to is for everyone.
func taskLosers(from, to string) []string {
	s := settings()
	if s.Users == nil || len(taskFiles.ForOfList(to)) == 0 {
		return nil
	}
	var out []string
	for _, name := range sortedKeys(s.Users) {
		if !s.Users[name].Disabled && taskFiles.SeesList(asUser(name), from) && !taskFiles.SeesList(asUser(name), to) {
			out = append(out, name)
		}
	}
	return out
}

// mayNarrowTask: user may move the shared task t to a list fewer people
// see: who made it (a task nobody is known to have made: anyone, as for
// deleting it), or an admin.
func mayNarrowTask(user asker, t *Task) bool {
	return t.Maker() == user.Name || t.Maker() == "" || settings().isAdmin(user.Name)
}

// sharedBy: the private task t (its Owner set to none already) is shared
// now, by user: its history says so, and, when the write says nothing of
// who has it (takes), it is given to them: what was theirs stays in their
// My tasks, whatever door shared it (the dialog, a move, a list made shared
// or deleted into a shared one, an assistant). A write that names an
// assignee, or says none, decides for itself (applyAssignee runs next).
func sharedBy(t *Task, user string, takes bool) {
	logEvent(t, user, "shared", "", "")
	if takes && t.Assignee == "" && settings().Users != nil && settings().assignable(user) {
		logEvent(t, user, "assigned", "", user)
		t.Assignee = user
	}
}

// applyAssignee: a write's assignee: a user who can be assigned
// (assignable), "me" for whoever writes, or none ("", null, "none"). Only a
// change is checked, so a task keeps a name that no longer can be assigned
// through every other edit; a private task has none. A name the task had
// before (its history says so) is taken back as it is, so an undo of the
// change that replaced a former assignee goes through.
func applyAssignee(user asker, t *Task, data jsonObj) error {
	if !data.Has("assignee") {
		return nil
	}
	name := ""
	if !data.Null("assignee") {
		s, err := asStr(data["assignee"], "assignee")
		if err != nil {
			return err
		}
		name = taskmodel.CleanAssignee(s)
	}
	switch {
	case name == "none":
		name = ""
	case name == "me" && user.Name != "":
		name = user.Name
	}
	if name == t.Assignee {
		return nil
	}
	s := settings()
	switch {
	case name == "":
	case s.Users == nil:
		return refuse("This wiki has no users to assign a task to")
	case t.Owner != "":
		return refuse("A private task is yours alone. Put it in a shared list to assign it (list)")
	case !s.assignable(name) && !hadAssignee(t, name):
		return refuse(s.notAssignable(name))
	case !taskFiles.SeesList(asUser(name), t.List) && !hadAssignee(t, name):
		// A list for some: a task there is given only to one of them (anyone
		// else wouldn't see it).
		return refuse(name + " isn't one of those the list #" + t.List + " is for: a task there is given to one of them")
	}
	logEvent(t, user.Name, "assigned", t.Assignee, name)
	t.Assignee = name
	return nil
}

// applyFollow: a write's follow: true, who writes follows the task from now
// on; false, no longer. To follow a task is to be told of its news as who
// made it is (newsFor, tellComment), without it being one's own to do: it
// is in no My tasks for it. Only oneself, as nobody writes under another's
// name (taskby.go): the write names no one. Only a shared task, with users:
// a private one has nobody else. Who follows is the task's (Followers, in
// its file): the news' rule and the mail read the task alone.
//
// Following is a look at the task: what was said before is no news to who
// starts to follow now, so their look is noted (markSeen), which writes no
// task. Nothing in the history: that someone follows is news to no one.
func applyFollow(user asker, t *Task, data jsonObj) error {
	if !data.Has("follow") {
		return nil
	}
	follow, err := asBool(data["follow"], "follow")
	if err != nil {
		return err
	}
	has := slices.Contains(t.Followers, user.Name)
	switch {
	case follow == has:
	case !follow:
		t.Followers = slices.DeleteFunc(slices.Clone(t.Followers), func(n string) bool { return n == user.Name })
		if len(t.Followers) == 0 {
			t.Followers = nil
		}
	case settings().Users == nil || user.Name == "":
		return refuse("This wiki has no users, so nobody follows a task")
	case t.Owner != "":
		return refuse("A private task is yours alone: there is no one else's news to follow. Put it in a shared list first (list)")
	default:
		t.Followers = append(slices.Clone(t.Followers), user.Name)
		markSeen(user.Name, t)
	}
	return nil
}

// followersSeen: of the task's followers those who see it (canSeeTask):
// who is told of its news, and whose names are shown. One who no longer
// sees its list (a list narrowed, a group left) stays in the file and is
// told nothing, as an assignee who doesn't (assigneeGone).
func followersSeen(t *Task) []string {
	var out []string
	for _, name := range t.Followers {
		if canSeeTask(asUser(name), t) && settings().Users[name] != nil {
			out = append(out, name)
		}
	}
	return out
}

// assignedOpen: how many open shared tasks each name is assigned, read
// from the file (the user commands have no running Zone).
func assignedOpen() map[string]int {
	n := map[string]int{}
	for _, t := range taskFiles.OpenOf("") {
		if t.Assignee != "" && t.IsOpen() {
			n[t.Assignee]++
		}
	}
	return n
}

// handOnNote: what disabling or removing name says of the open tasks
// they are assigned; "" when they have none.
func handOnNote(name string) string {
	n := assignedOpen()[name]
	if n == 0 {
		return ""
	}
	tasks := "tasks are"
	if n == 1 {
		tasks = "task is"
	}
	return fmt.Sprintf("%d open %s assigned to %s and now %s as no one's (the filter @none): hand them on at /tasks?assignee=%s",
		n, tasks, name, map[bool]string{true: "counts", false: "count"}[n == 1], name)
}

// hadAssignee: t's history says it was assigned to name.
func hadAssignee(t *Task, name string) bool {
	for _, e := range t.History {
		if e.Event == "assigned" && (e.From == name || e.To == name) {
			return true
		}
	}
	return false
}

// applyRecurrence: a write's recurrence (rec_type, rec_interval, rec_from,
// or the whole recurrence an undo sends), and what a changed date does to
// the one the task has. oldDue, oldStart, oldRec: the task's before the
// write.
func applyRecurrence(t *Task, data jsonObj, oldDue, oldStart string, oldRec *Recurrence) error {
	if data.Has("rec_type") {
		rtype, _ := jsonobj.As[string](data["rec_type"])
		switch rtype {
		case "daily", "weekly", "monthly", "yearly":
			n := 1
			if data.Has("rec_interval") {
				var err error
				if n, err = asInt(data["rec_interval"], "rec_interval"); err != nil {
					return err
				}
			}
			n = max(1, n)
			if n > taskmodel.RecIntervalMax {
				return refuse("rec_interval must be at most " + strconv.Itoa(taskmodel.RecIntervalMax))
			}
			from := "due"
			if s, _ := jsonobj.As[string](data["rec_from"]); s == "completion" {
				from = "completion"
			}
			rec := &Recurrence{Type: rtype, Interval: n, From: from}
			// Re-saving the modal re-sends the recurrence: keep the series'
			// day of month unless its type or primary date changed …
			oldPrimary := firstNonEmpty(oldDue, oldStart)
			newPrimary := firstNonEmpty(t.DueDate, t.StartDate)
			if oldRec.Day > 0 && oldRec.Type == rtype && newPrimary == oldPrimary {
				rec.Day = oldRec.Day
			}
			// … and a clamped start's remembered gap while neither date changed.
			if oldRec.Lead != nil && t.DueDate == oldDue && t.StartDate == oldStart {
				lead := *oldRec.Lead
				rec.Lead = &lead
			}
			t.Recurrence = rec
		default:
			t.Recurrence = nil
		}
	}
	// A date changed by hand defines the gap from now on; moving the primary
	// date re-anchors the series' day of month.
	if rec := t.Recurrence; rec != nil {
		if rec.Lead != nil && (t.DueDate != oldDue || t.StartDate != oldStart) {
			rec.Lead = nil
		}
		if rec.Day > 0 && firstNonEmpty(t.DueDate, t.StartDate) != firstNonEmpty(oldDue, oldStart) {
			rec.Day = 0
		}
	}
	// An undo sends the whole old recurrence, day and lead included.
	if data.Has("recurrence") {
		rec := taskmodel.CleanRecurrence(data["recurrence"])
		t.Recurrence = rec
		if rec == nil && !data.Null("recurrence") {
			return refuse("recurrence must be null or have a type")
		}
	}
	return nil
}

// firstNonEmpty: a, or b when a is empty.
func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// findForWriteLocked: the task with id (404 when user sees none such), and
// what a write of it takes: the open tasks user sees and, when it is a
// closed one, the closed tasks of its file (taskFiles.ReadTaskForWriteLocked):
// not every closed task of the wiki.
func findForWriteLocked(user asker, id string) (active, closed []*Task, task *Task, err error) {
	if active, closed, err = taskFiles.ReadTaskForWriteLocked(user, id); err != nil {
		return
	}
	for _, t := range slices.Concat(active, closed) {
		if t.ID == id {
			return active, closed, t, nil
		}
	}
	return nil, nil, nil, errNotFound
}

func without(tasks []*Task, id string) []*Task { return withoutIDs(tasks, map[string]bool{id: true}) }

// withoutIDs: tasks but those with an id in ids.
func withoutIDs(tasks []*Task, ids map[string]bool) []*Task {
	out := make([]*Task, 0, len(tasks))
	for _, t := range tasks {
		if !ids[t.ID] {
			out = append(out, t)
		}
	}
	return out
}

// updateTask changes a task's fields and status (complete, won't do, skip,
// reopen) from data as the API takes it; suffix follows the commit message
// (the MCP tools' summary).
func updateTask(user asker, id string, data jsonObj, suffix string) (*Task, error) {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	return updateTaskLocked(user, id, data, suffix)
}

// changeTaskFields: the fields in data on the task t, as every write that
// changes a task's fields makes them, one task's (updateTaskLocked) or
// several's (the batch update): the text is the one the change started
// from (textUnchanged: or it is a conflict), the fields are applied, and
// who the description names now is logged. Returns them, to be told once
// the task is written (tellMentioned).
func changeTaskFields(user asker, t *Task, data jsonObj) (named []string, err error) {
	rememberBy(t) // an undo sends its comments and history back (taskby.go)
	if err := textUnchanged(t, data); err != nil {
		return nil, err
	}
	described := t.Description
	if err := applyTaskFields(user, t, data, false); err != nil {
		return nil, err
	}
	return logMentions(t, user, described, t.Description), nil
}

// sentBack: an undo sends the old history back with the old fields: of
// the entries Zone has had for the task (knownHistory). Who made it is
// written on the task first (created_by, for a task from before it was),
// so no history sent makes it anyone else's.
func sentBack(user asker, t *Task, data jsonObj) {
	if data.Has("history") {
		if settings().Users != nil && t.CreatedBy == "" {
			t.CreatedBy = t.Maker()
		}
		t.History = knownHistory(t.ID, sentHistory(cleanHistory(data["history"]), t.History), t.History)
		checkBy(user.Name, t)
	}
}

func updateTaskLocked(user asker, id string, data jsonObj, suffix string) (*Task, error) {
	var status [3]bool
	for i, k := range []string{"skip", "wont_do", "completed"} {
		var err error
		if status[i], err = boolField(data, k); err != nil {
			return nil, err
		}
	}
	skip, wontDo, done := status[0], status[1], status[2]
	pos, positioned, err := positionField(data)
	if err != nil {
		return nil, err
	}
	active, closed, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	before := assignedBefore{t.ID: t.Assignee}
	// seen: the user opened a task that has news for them (the browser says
	// so: markSeen in js/taskdata.js). Noted once, so it is no longer new to
	// them, and whoever gave it reads in its History that it arrived. For any
	// other task it says nothing.
	seen, err := boolField(data, "seen")
	if err != nil {
		return nil, err
	}
	if seen && newsFor(t, user).Kind != "" {
		// Their look is theirs: kept beside the tasks (taskseen.go), so this
		// writes no task and commits nothing.
		markSeen(user.Name, t)
	}
	named, err := changeTaskFields(user, t, data)
	if err != nil {
		return nil, err
	}
	didSkip := skip && t.IsOpen()
	switch {
	case didSkip:
		// Skip this occurrence: archived as won't-do, the next one spawned
		// (a plain won't-do ends the series).
		logEvent(t, user.Name, "skipped", "", "")
		spawned := spawnRecurring(user, t, active, true)
		t.ClosedAt = timestamp()
		t.WontDo = true
		active = without(active, id)
		closed = append(closed, t)
		if spawned != nil {
			active = append(active, spawned)
		}
	case data.Has("wont_do"):
		if wontDo && t.IsOpen() {
			// Won't do: to closed, abandoned; no next occurrence.
			t.ClosedAt = timestamp()
			t.WontDo = true
			logEvent(t, user.Name, "wontdo", "", "")
			active = without(active, id)
			closed = append(closed, t)
		} else if !wontDo && !t.IsOpen() {
			// Reopen (also takes away what a skip spawned).
			active = uncompleteTask(user, t, active, nil)
			closed = without(closed, id)
			active = append(active, t)
		}
	case data.Has("completed"):
		switch {
		case done && t.Keep && t.IsOpen():
			keepDone(user, t) // stays open, back in the pool
		case done && t.IsOpen():
			spawned := completeTask(user, t, "", active)
			active = without(active, id)
			closed = append(closed, t)
			if spawned != nil {
				active = append(active, spawned)
			}
		case !done && !t.IsOpen():
			active = uncompleteTask(user, t, active, nil)
			closed = without(closed, id)
			active = append(active, t)
		}
	}
	// position: its place in the manual order, which only an open task has
	// (still, or reopened by this write).
	if positioned {
		if err := moveTo(active, []string{t.ID}, pos); err != nil {
			return nil, err
		}
	}
	sentBack(user, t, data)
	msg := "Update task"
	switch {
	case didSkip:
		msg = "Skip task"
	case data.Has("wont_do"):
		msg = "Close task as won't do"
		if !wontDo {
			msg = "Reopen task"
		}
	case data.Has("completed"):
		msg = "Complete task"
		if !done {
			msg = "Uncomplete task"
		}
	}
	if err := taskFiles.WriteBothLocked(user, active, closed, msg+suffix); err != nil {
		return t, err
	}
	tellAssigned(user.Name, before, t)
	tellMentioned(user.Name, t, named)
	return t, nil
}

// textUnchanged: a write that changes a task's title or description may say
// what it started from (was: {title, description}, as the dialog does):
// when the task's text is another one by now, someone else changed it
// meanwhile, and writing would take their change away. Refused (409), with
// nothing written: the dialog keeps what was typed. Fields are merged by
// writing only what changed; a text can't be.
func textUnchanged(t *Task, data jsonObj) error {
	if !data.Has("was") {
		return nil
	}
	was, ok := jsonobj.As[jsonObj](data["was"])
	if !ok {
		return refuse("was must be an object")
	}
	for k, now := range map[string]string{"title": t.Title, "description": t.Description} {
		if !was.Has(k) || !data.Has(k) {
			continue
		}
		if s, _ := jsonobj.As[string](was[k]); strings.TrimSpace(s) != now {
			return refuseAs(refusedConflict, "Someone changed this task's "+k+" while you were editing it. Copy what you wrote, close the dialog and open the task again.")
		}
	}
	return nil
}

// toggleNoteCheck ticks or unticks the Nth checkbox of a task's notes.
func toggleNoteCheck(user asker, id string, idx int) (*Task, error) {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	_, _, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	text := t.Description
	toggled := toggleNthCheckbox(text, idx, false, now())
	if toggled == text {
		return t, nil
	}
	data := jsonObj{}
	data.Set("description", toggled)
	return updateTaskLocked(user, id, data, "")
}

// addComment adds a comment (a status line) to a task, open or done.
func addComment(user asker, id string, text string, message string) (*Task, error) {
	s, err := commentText(text)
	if err != nil {
		return nil, err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, closed, t, err := findForWriteLocked(user, id)
	if err != nil {
		return nil, err
	}
	t.Comments = append(cleanCommentList(t.Comments),
		Comment{ID: newCommentID(), At: now().Format("2006-01-02T15:04"), By: user.Name, Text: s})
	named := logMentions(t, user, "", s)
	if err := taskFiles.WriteBothLocked(user, active, closed, message); err != nil {
		return t, err
	}
	tellComment(user.Name, t, s, named)
	return t, nil
}

// taskComment: the index of a task's comment by id (its comments cleaned
// first).
func taskComment(t *Task, cid string) (int, error) {
	t.Comments = cleanCommentList(t.Comments)
	for i, c := range t.Comments {
		if c.ID == cid {
			return i, nil
		}
	}
	return 0, errNotFound
}

// errNotYours: a task only its maker or an admin deletes.
var errNotYours = refuseAs(refusedReadOnly, "Only who made a task, or an admin, deletes it")

// brought: the tasks a user brought in with an import (apiImport), which
// its undo deletes again though others made them, by id; in memory, the
// oldest forgotten past broughtMax.
var brought = struct {
	sync.Mutex
	by    map[string]string // task id → who imported it
	order []string
}{by: map[string]string{}}

const broughtMax = 20000

// noteBrought: user imported the tasks ids.
func noteBrought(user string, ids []string) {
	brought.Lock()
	defer brought.Unlock()
	for _, id := range ids {
		if _, ok := brought.by[id]; !ok {
			brought.order = append(brought.order, id)
		}
		brought.by[id] = user
	}
	if over := len(brought.order) - broughtMax; over > 0 {
		for _, id := range brought.order[:over] {
			delete(brought.by, id)
		}
		brought.order = append([]string(nil), brought.order[over:]...)
	}
}

// mayDeleteTask: user may delete the task t for good: without users
// anyone; a task of their own (a private one, or one they made, which is
// also what takes back a task just made: the editor's Make task); an admin
// any; and what they imported, while Zone runs (the import's undo). A task
// someone else made is closed (Won't do), not deleted: others have seen it.
func mayDeleteTask(user asker, t *Task) bool {
	s := settings()
	if s.Users == nil || t.Owner != "" || t.Maker() == user.Name || t.Maker() == "" || s.isAdmin(user.Name) {
		return true
	}
	brought.Lock()
	defer brought.Unlock()
	return brought.by[t.ID] == user.Name
}

func deleteTask(user asker, id, message string) error {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	return deleteTasksLocked(user, map[string]bool{id: true}, message)
}

func deleteTasksLocked(user asker, ids map[string]bool, message string) error {
	active, closed, err := taskFiles.ReadTasksForWriteLocked(user, ids)
	if err != nil {
		return err
	}
	for _, t := range slices.Concat(active, closed) {
		if ids[t.ID] {
			if !mayDeleteTask(user, t) {
				return errNotYours
			}
			rememberBy(t) // the undo restores it
		}
	}
	return taskFiles.WriteBothLocked(user, withoutIDs(active, ids), withoutIDs(closed, ids), message)
}

func idSet(ids []string) map[string]bool {
	set := map[string]bool{}
	for _, id := range ids {
		set[id] = true
	}
	return set
}

// selectTasks: the tasks that pass keep, and their ids.
func selectTasks(tasks []*Task, keep func(*Task) bool) ([]*Task, map[string]bool) {
	var out []*Task
	ids := map[string]bool{}
	for _, t := range tasks {
		if keep(t) {
			out = append(out, t)
			ids[t.ID] = true
		}
	}
	return out, ids
}

// tagTasks adds the tags add to the tasks ids and takes remove off them
// (case ignored; a tag a task has keeps its spelling), open and closed,
// in one commit; the batch-tag route and the MCP tools share it.
func tagTasks(user asker, ids, add, remove []string, message string) ([]*Task, error) {
	set := idSet(ids)
	addTags, removeTags := taskmodel.CleanTags(add), taskmodel.CleanTags(remove)
	if len(set) == 0 || len(addTags) == 0 && len(removeTags) == 0 {
		return nil, refuse("Give task ids and tags to add or remove")
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, closed, err := taskFiles.ReadTasksForWriteLocked(user, set)
	if err != nil {
		return nil, err
	}
	updated := []*Task{}
	for _, t := range slices.Concat(active, closed) {
		if !set[t.ID] {
			continue
		}
		t.Tags = retag(t.Tags, addTags, removeTags)
		updated = append(updated, t)
	}
	return updated, taskFiles.WriteBothLocked(user, active, closed, message)
}

// ---- the manual order ----

// taskMove: a task given its place in the manual order between two tasks,
// named by their ids: After, the one now before it ("": none), and Before,
// the next one after it that doesn't move ("": none). What a list sends
// for a drop (TasksCore.orderMoves), and what a position comes to
// (movesTo).
type taskMove struct{ ID, After, Before string }

// placeTasks gives each moved task of active (the open tasks a write has)
// its place between its two (placeBetween), one after the other, in the
// order the server has by then: so a list that is stale in a browser
// doesn't put its old order back over what someone ranked meanwhile. A
// task or neighbour that isn't among active counts as none. A task not yet
// placed is placed first where it is shown (placeUnplaced).
func placeTasks(active []*Task, moves []taskMove) {
	if len(moves) == 0 {
		return
	}
	placeUnplaced(active)
	byID := make(map[string]*Task, len(active))
	for _, t := range active {
		byID[t.ID] = t
	}
	for _, m := range moves {
		if t := byID[m.ID]; t != nil {
			placeBetween(active, t, byID[m.After], byID[m.Before])
		}
	}
}

// placeUnplaced gives each task of active that has no place in the manual
// order (no sort_order: a task from a file edited by hand, an import, an
// older Zone; a new one is placed when it is made) the place it is shown
// at: above every placed task, newest first, as the views list them
// (TasksCore.byOpenOrder, which reverses the list first, so of two made at
// the same moment the later in the file comes first). So a move, or a
// position, lands where it was asked for, and nothing jumps.
func placeUnplaced(active []*Task) {
	var unplaced []*Task
	top, placed := 0.0, false
	for _, t := range active {
		switch {
		case t.SortOrder == nil:
			unplaced = append(unplaced, t)
		case !placed || *t.SortOrder < top:
			top, placed = *t.SortOrder, true
		}
	}
	slices.Reverse(unplaced)
	slices.SortStableFunc(unplaced, func(a, b *Task) int { return strings.Compare(b.CreatedAt, a.CreatedAt) })
	for i, t := range unplaced {
		f := top - float64(len(unplaced)-i)
		t.SortOrder = &f
	}
}

// placeBetween gives t the place in the manual order between the tasks
// after (the one before it; nil: none) and before (the one that follows;
// nil: none): a number between theirs (Task.SortOrder), so no other task
// is written. A neighbour that is in no order (anymore) counts as none, and
// two that are not in that order (the other way round, or one number: a
// list in a browser that was stale, lists numbered apart) leave after to
// say where. When the numbers between two are used up, the tasks' order is
// numbered anew, as it stands, and the place taken then.
func placeBetween(tasks []*Task, t, after, before *Task) {
	val := func(n *Task) (float64, bool) {
		if n == nil || n == t || n.SortOrder == nil {
			return 0, false
		}
		return *n.SortOrder, true
	}
	place := func() (float64, bool) {
		lo, hasLo := val(after)
		hi, hasHi := val(before)
		if hasLo && hasHi && hi <= lo {
			hasHi = false
		}
		switch {
		case hasLo && hasHi:
			mid := lo + (hi-lo)/2
			return mid, mid > lo && mid < hi && hi-lo > 1e-6
		case hasLo:
			return lo + 1, true
		case hasHi:
			return hi - 1, true
		}
		return 0, true
	}
	f, ok := place()
	if !ok {
		// No room left between them: every ranked task numbered 0, 1, 2 … in
		// the order they are in.
		var ranked []*Task
		for _, x := range tasks {
			if x.SortOrder != nil && x != t {
				ranked = append(ranked, x)
			}
		}
		slices.SortStableFunc(ranked, func(a, b *Task) int { return cmp.Compare(*a.SortOrder, *b.SortOrder) })
		for i, x := range ranked {
			n := float64(i)
			x.SortOrder = &n
		}
		f, _ = place()
	}
	t.SortOrder = &f
}

// orderPos: a position in the manual order that a write puts tasks at: At
// "top" or "bottom", or "after" or "before" the task Task (its id).
type orderPos struct{ At, Task string }

// field: pos as a write's position (positionField).
func (pos orderPos) field() any {
	if pos.Task == "" {
		return pos.At
	}
	return map[string]string{pos.At: pos.Task}
}

// positionField: a write's position in the manual order, data's
// "position": "top", "bottom", {"after": id} or {"before": id}; has is
// false without one.
func positionField(data jsonObj) (pos orderPos, has bool, err error) {
	if !data.Has("position") || data.Null("position") {
		return pos, false, nil
	}
	bad := refuse(`position must be "top", "bottom", {"after": id} or {"before": id}`)
	if s, ok := jsonobj.As[string](data["position"]); ok {
		if s != "top" && s != "bottom" {
			return pos, false, bad
		}
		return orderPos{At: s}, true, nil
	}
	o, ok := jsonobj.As[jsonObj](data["position"])
	if !ok || len(o) != 1 {
		return pos, false, bad
	}
	for _, at := range []string{"after", "before"} {
		if id, ok := jsonobj.As[string](o[at]); ok && id != "" {
			return orderPos{At: at, Task: id}, true, nil
		}
	}
	return pos, false, bad
}

// movesTo: the moves (placeTasks) that put the tasks ids, in that order, at
// pos in the manual order of active, the open tasks the user sees: the
// first right after pos's task, right before it, above the first task or
// below the last; each next one right after the one before it. Every task
// of active has its place (moveTo places the unplaced first).
// The manual order is one for all tasks (sort_order), so a position
// between two tasks there is between them in every view that shows both:
// their list's, All tasks, search_tasks'. My tasks' own order, the user's
// alone once they rank there (mineOrder), is another: as a move in a
// list's view does, this leaves it as it is. A task that is closed, or
// that the user doesn't see, is none of active: refused in the same words
// either way.
func movesTo(active []*Task, ids []string, pos orderPos) ([]taskMove, error) {
	byID := make(map[string]*Task, len(active))
	for _, t := range active {
		byID[t.ID] = t
	}
	moving := idSet(ids)
	for _, id := range ids {
		if byID[id] == nil {
			return nil, refuse("Only an open task has a position in the manual order")
		}
	}
	// The ranked tasks that stay where they are, top first.
	var ranked []*Task
	for _, t := range active {
		if t.SortOrder != nil && !moving[t.ID] {
			ranked = append(ranked, t)
		}
	}
	slices.SortStableFunc(ranked, func(a, b *Task) int { return cmp.Compare(*a.SortOrder, *b.SortOrder) })
	var after, before string // the first task's neighbours
	switch pos.At {
	case "top":
		if len(ranked) > 0 {
			before = ranked[0].ID
		}
	case "bottom":
		if len(ranked) > 0 {
			after = ranked[len(ranked)-1].ID
		}
	case "after", "before":
		by := byID[pos.Task]
		switch {
		case by == nil:
			return nil, refuse("The task to put it after or before is closed or not there: only open tasks have a position in the manual order")
		case moving[by.ID]:
			return nil, refuse("A task can't be put after or before itself")
		}
		// Its neighbour on that side: the nearest task with another number
		// (there is no number between two of the same).
		i := slices.Index(ranked, by)
		if pos.At == "after" {
			after = by.ID
			for _, t := range ranked[i+1:] {
				if *t.SortOrder > *by.SortOrder {
					before = t.ID
					break
				}
			}
		} else {
			before = by.ID
			for j := i - 1; j >= 0; j-- {
				if *ranked[j].SortOrder < *by.SortOrder {
					after = ranked[j].ID
					break
				}
			}
		}
	default:
		return nil, refuse("A position is top, bottom, after or before a task")
	}
	moves := make([]taskMove, 0, len(ids))
	for _, id := range ids {
		if !slices.ContainsFunc(moves, func(m taskMove) bool { return m.ID == id }) {
			moves = append(moves, taskMove{ID: id, After: after, Before: before})
			after = id
		}
	}
	return moves, nil
}

// moveTo puts the tasks ids, in that order, at pos in the manual order of
// active (movesTo, placeTasks), or refuses with nothing changed.
func moveTo(active []*Task, ids []string, pos orderPos) error {
	placeUnplaced(active)
	moves, err := movesTo(active, ids, pos)
	if err == nil {
		placeTasks(active, moves)
	}
	return err
}

// reorderTasks puts the open tasks ids, in that order, at pos in the manual
// order (moveTo: what a move in a list's view does, POST
// /api/tasks/reorder's moves), in one commit by user; the MCP tools'
// update_task. Returns those tasks, as written, in that order.
func reorderTasks(user asker, ids []string, pos orderPos, message string) ([]*Task, error) {
	if err := mayWriteTasks(user); err != nil {
		return nil, err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, err := taskFiles.ReadOpenStrict(user)
	if err != nil {
		return nil, err
	}
	if err := moveTo(active, ids, pos); err != nil {
		return nil, err
	}
	byID := make(map[string]*Task, len(active))
	for _, t := range active {
		byID[t.ID] = t
	}
	moved := make([]*Task, 0, len(ids))
	for _, id := range ids {
		if t := byID[id]; !slices.Contains(moved, t) {
			moved = append(moved, t)
		}
	}
	return moved, taskFiles.WriteOpenLocked(user, active, message)
}
