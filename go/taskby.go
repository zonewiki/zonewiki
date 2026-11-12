// Who a comment or a history entry is by, in what a client sends
// (docs/spec/users.md, Who made a change): the lists come back whole for an
// undo (a deleted task restored, a comment's delete undone, an import), and
// a client could write any name into them. So a name other than the
// sender's own stays only on what Zone itself has seen under that name.
package main

import "sync"

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
	for _, t := range tasks {
		for _, c := range t.Comments {
			if c.By != "" {
				add(commentKey(c))
			}
		}
		for _, e := range t.History {
			if e.By != "" {
				add(historyKey(t.ID, e))
			}
		}
	}
	if over := len(seenBy.order) - seenByMax; over > 0 {
		for _, key := range seenBy.order[:over] {
			delete(seenBy.set, key)
		}
		seenBy.order = append([]string(nil), seenBy.order[over:]...)
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
