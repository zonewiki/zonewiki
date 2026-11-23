// Who a comment or a history entry is by, in what a client sends
// (docs/spec/users.md, Who made a change): the lists come back whole for an
// undo (a deleted task restored, a comment's delete undone, an import), and
// a client could write any name into them. So a name other than the
// sender's own stays only on what Zonewiki itself has seen under that name: that
// comment, with that text, on that task; that entry of that task's history;
// and on a task that comes back (restored, imported) only when it comes back
// as Zonewiki had it (checkArrived), not as another task under its id.
package main

import (
	"slices"
	"sync"
	"zonewiki/internal/taskmodel"
)

// seenBy: the comments and history entries Zonewiki took out of the tasks'
// files or was about to replace, as it had them, so the undo that sends
// them back is believed. In memory, the oldest forgotten past seenByMax:
// an undo after a restart, or a very late one, loses the names of others.
var seenBy struct {
	sync.Mutex
	set   map[string]bool
	order []string
}

const seenByMax = 20000

// commentKey: the comment c of the task id as Zonewiki had it: its id, who
// wrote it and its text, so neither moves to another task nor says another
// thing under that name.
func commentKey(id string, c Comment) string {
	return "c\x00" + id + "\x00" + c.ID + "\x00" + c.By + "\x00" + c.Text
}

// taskKey: the task t as Zonewiki had it, what it is to its readers (its id, title,
// description, when it was made, who has it, the occurrence before it): a
// snapshot of another task under its id (deleted, its id taken again), or
// of it given to another or put in another series, is not it
// (checkArrived).
func taskKey(t *Task) string {
	return "k\x00" + t.ID + "\x00" + t.Title + "\x00" + t.Description + "\x00" + t.CreatedAt + "\x00" + t.Assignee + "\x00" + t.SpawnedFrom
}

// knownTask: with users, t comes back from a snapshot as Zonewiki had it
// (taskKey): the undo of its delete, or an import of it deleted since.
func knownTask(t *Task) bool {
	if settings().Users == nil {
		return false
	}
	seenBy.Lock()
	defer seenBy.Unlock()
	return seenBy.set[taskKey(t)]
}

// mayChangeComment: with users a comment is its author's: only they edit or
// delete it (an admin may delete any). One by no one (from before users)
// is anyone's.
func mayChangeComment(user asker, c Comment, deleting bool) bool {
	s := settings()
	return s.Users == nil || c.By == "" || c.By == user.Name || deleting && s.isAdmin(user.Name)
}

// errNotYourComment: what a change to someone else's comment answers (403).
var errNotYourComment = refuseAs(refusedReadOnly, "Only its author changes a comment")

// othersCommentsKept: a write that sends the comments of the task id
// whole (the undo of a comment's delete or edit) changes only the sender's
// own: every comment of someone else's was there with that text (in old, or
// taken out of that task before: rememberBy), and none of theirs is
// dropped, but by an admin.
func othersCommentsKept(user asker, id string, old, now []Comment) error {
	if settings().Users == nil {
		return nil
	}
	was := map[string]Comment{}
	for _, c := range old {
		was[c.ID] = c
	}
	sent := map[string]Comment{}
	for _, c := range now {
		sent[c.ID] = c
		if _, there := was[c.ID]; there || c.By == "" || c.By == user.Name {
			continue
		}
		seenBy.Lock()
		known := seenBy.set[commentKey(id, c)]
		seenBy.Unlock()
		if !known {
			return errNotYourComment
		}
	}
	for _, c := range old {
		if c.By == "" || c.By == user.Name {
			continue
		}
		if n, there := sent[c.ID]; !there && !mayChangeComment(user, c, true) || there && (n.By != c.By || n.Text != c.Text) {
			return errNotYourComment
		}
	}
	return nil
}

func historyKey(taskID string, e HistoryEntry) string {
	return "h\x00" + taskID + "\x00" + e.At + "\x00" + e.Event + "\x00" + e.From + "\x00" + e.To + "\x00" + e.By
}

// rememberBy notes who t's comments and history entries are by, before
// they leave the file (a delete) or a request replaces them.
func rememberBy(tasks ...*Task) {
	if settings().Users == nil {
		return
	}
	seenBy.Lock()
	defer seenBy.Unlock()
	if seenBy.set == nil {
		seenBy.set = map[string]bool{}
	}
	add := func(key string) {
		if !seenBy.set[key] {
			seenBy.set[key] = true
			seenBy.order = append(seenBy.order, key)
		}
	}
	// Those by no one too: an undo may send them back (knownHistory,
	// ownNewComments), and nothing else may.
	for _, t := range tasks {
		add(taskKey(t))
		for _, c := range t.Comments {
			add(commentKey(t.ID, c))
		}
		for _, e := range t.History {
			add(historyKey(t.ID, e))
		}
	}
	if over := len(seenBy.order) - seenByMax; over > 0 {
		for _, key := range seenBy.order[:over] {
			delete(seenBy.set, key)
		}
		seenBy.order = append([]string(nil), seenBy.order[over:]...)
	}
}

// knownHistory: with users, the history a client sent for the task id (an
// undo sends an older one back whole) as it may replace had, the one its
// file has: only entries Zonewiki has had for the task (in had, or replaced
// before: rememberBy), so nobody writes an event that wasn't, such as a
// "created" in their own name (who made a task may delete it, created_by) or an
// "assigned" that hands it to whom the history says had it (hadAssignee);
// and its "created" entry stays, so nobody makes a task no one's by
// dropping it.
func knownHistory(id string, sent, had []HistoryEntry) []HistoryEntry {
	if settings().Users == nil {
		return sent
	}
	has := map[string]bool{}
	for _, e := range had {
		has[historyKey(id, e)] = true
	}
	seenBy.Lock()
	out := slices.DeleteFunc(sent, func(e HistoryEntry) bool {
		k := historyKey(id, e)
		return !has[k] && !seenBy.set[k]
	})
	seenBy.Unlock()
	for _, e := range had {
		if e.Event == "created" && !slices.Contains(out, e) {
			out = insertByTime(out, e)
		}
	}
	return out
}

// ownNewComments: with users, a comment a whole-list write brings that the
// task didn't have (by its id: old) and Zonewiki never had by no one, but
// with no name (sent so, or a name checkBy dropped), is the sender's: by
// no one it would be everyone's to change (mayChangeComment).
func ownNewComments(user string, old []Comment, t *Task) {
	if settings().Users == nil {
		return
	}
	had := map[string]bool{}
	for _, c := range old {
		had[c.ID] = true
	}
	seenBy.Lock()
	defer seenBy.Unlock()
	for i, c := range t.Comments {
		if c.By == "" && !had[c.ID] && !seenBy.set[commentKey(t.ID, c)] {
			t.Comments[i].By = user
		}
	}
}

// checkBy: with users, the names in a task's comments and history as
// user's request sent them. Their own name stays, and another's on a
// comment (of this task, with this text) or a history entry (of this task,
// at that time) Zonewiki has seen as theirs; any other name is dropped: by no
// one.
func checkBy(user string, t *Task) {
	if settings().Users == nil {
		return
	}
	seenBy.Lock()
	defer seenBy.Unlock()
	checkByLocked(user, t, true)
}

// checkArrived: checkBy for a task that comes back from a snapshot (an
// undo's restore, an import), which may be any task under any id: names
// other than user's stay only when it comes back as Zonewiki had it (taskKey),
// so a task deleted for good lends its id to no other task that would carry
// its people's names; nor does it keep a series it says it is in
// (spawned_from), which would make a [[task:…]] of a closed task stand for
// it (resolveTaskRefs). Returns whether it came back as Zonewiki had it.
func checkArrived(user string, t *Task) bool {
	if settings().Users == nil {
		return false
	}
	seenBy.Lock()
	defer seenBy.Unlock()
	known := seenBy.set[taskKey(t)]
	checkByLocked(user, t, known)
	if !known {
		t.SpawnedFrom = ""
	}
	return known
}

// checkByLocked: checkBy (seenBy locked); known false: Zonewiki believes no
// other name on t.
func checkByLocked(user string, t *Task, known bool) {
	for i, c := range t.Comments {
		if c.By != "" && c.By != user && (!known || !seenBy.set[commentKey(t.ID, c)]) {
			t.Comments[i].By = ""
		}
	}
	// An entry Zonewiki hasn't had keeps no one else in it: not who did it, nor
	// whom it was given to (an "assigned" to bob says he had it, and who had
	// a task may be given it again: hadAssignee).
	for i, e := range t.History {
		if known && seenBy.set[historyKey(t.ID, e)] {
			continue
		}
		if e.By != user {
			t.History[i].By = ""
		}
		if taskmodel.NamedEvents[e.Event] {
			if e.From != user {
				t.History[i].From = ""
			}
			if e.To != user {
				t.History[i].To = ""
			}
		}
	}
}
