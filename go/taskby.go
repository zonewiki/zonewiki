// Who a comment or a history entry is by, in what a client sends
// (docs/spec/users.md, Who made a change): the lists come back whole for an
// undo (a deleted task restored, a comment's delete undone, an import), and
// a client could write any name into them. So a name other than the
// sender's own stays only on what Zone itself has seen under that name.
package main

import (
	"slices"
	"sync"
)

// seenBy: the comments and history entries Zone took out of the tasks'
// files or was about to replace, as it had them, so the undo that sends
// them back is believed. In memory, the oldest forgotten past seenByMax:
// an undo after a restart, or a very late one, loses the names of others.
var seenBy struct {
	sync.Mutex
	set   map[string]bool
	order []string
}

const seenByMax = 20000

func commentKey(c Comment) string { return "c\x00" + c.ID + "\x00" + c.By }

// commentTextKey: a comment with its text, for othersCommentsKept.
func commentTextKey(c Comment) string { return "t\x00" + c.ID + "\x00" + c.By + "\x00" + c.Text }

// mayChangeComment: with users a comment is its author's: only they edit or
// delete it (an admin may delete any). One by no one (from before users)
// is anyone's.
func mayChangeComment(user asker, c Comment, deleting bool) bool {
	s := settings()
	return s.Users == nil || c.By == "" || c.By == user.Name || deleting && s.isAdmin(user.Name)
}

// errNotYourComment: what a change to someone else's comment answers (403).
var errNotYourComment = refuseAs(refusedReadOnly, "Only its author changes a comment")

// othersCommentsKept: a write that sends a task's comments whole (the undo
// of a comment's delete or edit) changes only the sender's own: every
// comment of someone else's was there with that text (in old, or taken out
// before: rememberBy), and none of theirs is dropped, but by an admin.
func othersCommentsKept(user asker, old, now []Comment) error {
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
		known := seenBy.set[commentTextKey(c)]
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
		for _, c := range t.Comments {
			add(commentKey(c))
			add(commentTextKey(c))
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
// file has: only entries Zone has had for the task (in had, or replaced
// before: rememberBy), so nobody writes an event that wasn't, such as a
// "created" in their own name (who made a task may delete it, Maker) or an
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
// task didn't have (by its id: old) and Zone never had by no one, but
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
		if c.By == "" && !had[c.ID] && !seenBy.set[commentTextKey(c)] {
			t.Comments[i].By = user
		}
	}
}

// checkBy: with users, the names in a task's comments and history as
// user's request sent them. Their own name stays, and another's on a
// comment (by its id) or a history entry (of this task, at that time)
// Zone has seen as theirs; any other name is dropped: by no one.
func checkBy(user string, t *Task) {
	if settings().Users == nil {
		return
	}
	seenBy.Lock()
	defer seenBy.Unlock()
	for i, c := range t.Comments {
		if c.By != "" && c.By != user && !seenBy.set[commentKey(c)] {
			t.Comments[i].By = ""
		}
	}
	for i, e := range t.History {
		if e.By != "" && e.By != user && !seenBy.set[historyKey(t.ID, e)] {
			t.History[i].By = ""
		}
	}
}
