// What a user has seen of the tasks' news (Updates in docs/spec/tasks.md):
// kept beside the tasks, in Zonewiki's folder, not in them.
package main

import (
	"cmp"
	"encoding/json"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/atomicfile"
)

// seenStore: when each user last looked at a task that had news for them:
// .zone/seen/<user>.json, {"<task id>": {"at": "2026-11-15T09:30", "after":
// "…"}}, out of git as all of Zonewiki's folder is. after names the last entry
// the task's history had then (lookAfter): a history says its times to the
// minute, and what happens within the minute of a look, after it, is news. It is the user's state, not the task's: a look
// writes no task file, makes no commit, and loads nobody's list again.
// Lost with Zonewiki's folder, the tasks show their news again, and nothing
// else happens. Read once (every user's: a task's History says who saw it),
// written when a user looks.
var seenStore = struct {
	sync.Mutex
	dir     string                     // the folder by was read from
	by      map[string]map[string]look // user → task id → their look
	byTask  map[string]map[string]look // task id → user → the same looks
	version map[string]int64           // user → their looks since Zonewiki started (keptCounts)
}{}

// lookAfter: how a look names the history entry it came after: a few
// characters that another entry of the task doesn't have.
func lookAfter(taskID string, e HistoryEntry) string { return sha1Hex(historyKey(taskID, e))[:12] }

// seenKept: how many tasks a user's file names before those that can have
// no news anymore are taken out (canHaveNews).
const seenKept = 300

// look: when a user looked at a task, the last entry its history had, and
// the last comment it had (its id; noComment when it had none): a
// comment's time is to the
// minute too, and one written after a look within its minute is news.
type look struct {
	At      string `json:"at"`
	After   string `json:"after,omitempty"`
	Comment string `json:"comment,omitempty"`
}

// noComment: what a look names as the last comment of a task without one.
const noComment = "-"

func seenDir() string { return statePath("seen") }

// seenPath: user's file, "" for a name that is no file's.
func seenPath(user string) string {
	if user == "" || user != filepath.Base(user) || strings.HasPrefix(user, ".") {
		return ""
	}
	return filepath.Join(seenDir(), user+".json")
}

// loadSeen reads every user's file, once for a wiki's folder (seenStore
// locked). A file that can't be read is no looks.
func loadSeen() {
	dir := seenDir()
	if seenStore.by != nil && seenStore.dir == dir {
		return
	}
	seenStore.dir, seenStore.by, seenStore.version = dir, map[string]map[string]look{}, map[string]int64{}
	seenStore.byTask = map[string]map[string]look{}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		user, isJSON := strings.CutSuffix(e.Name(), ".json")
		if e.IsDir() || !isJSON {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		seen := map[string]look{}
		if json.Unmarshal(raw, &seen) == nil {
			seenStore.by[user] = seen
			for id, at := range seen {
				noteSeen(id, user, at)
			}
		}
	}
}

// noteSeen: user looked at the task id at that time, in byTask (seenStore
// locked).
func noteSeen(id, user string, at look) {
	if seenStore.byTask[id] == nil {
		seenStore.byTask[id] = map[string]look{}
	}
	seenStore.byTask[id][user] = at
}

// seenOf: who looked at the task id and when (a copy), nil when no one did.
func seenOf(id string) map[string]look {
	seenStore.Lock()
	defer seenStore.Unlock()
	loadSeen()
	return maps.Clone(seenStore.byTask[id])
}

// seenVersion: a number that is another one after user looked at a task
// (what their counts are kept by: keptCounts).
func seenVersion(user string) int64 {
	seenStore.Lock()
	defer seenStore.Unlock()
	loadSeen()
	return seenStore.version[user]
}

// seenWrite: one write of a user's file at a time.
var seenWrite sync.Mutex

// markSeen: user looked at the task t now. Their file is written; no task
// is.
func markSeen(user string, t *Task) {
	path := seenPath(user)
	if path == "" {
		return
	}
	at := look{At: now().Format("2006-01-02T15:04")}
	if n := len(t.History); n > 0 {
		at.After = lookAfter(t.ID, t.History[n-1])
	}
	at.Comment = noComment
	if n := len(t.Comments); n > 0 && t.Comments[n-1].ID != "" {
		at.Comment = t.Comments[n-1].ID
	}
	ids := []string{t.ID}
	seenWrite.Lock()
	defer seenWrite.Unlock()
	seenStore.Lock()
	loadSeen()
	if seenStore.by[user] == nil {
		seenStore.by[user] = map[string]look{}
	}
	for _, id := range ids {
		seenStore.by[user][id] = at
		noteSeen(id, user, at)
	}
	seenStore.version[user]++
	many := len(seenStore.by[user]) > seenKept
	seenStore.Unlock()
	var keep map[string]bool
	if many {
		keep = canHaveNews(user) // reads the tasks: not under seenStore's lock
	}
	seenStore.Lock()
	seen := seenStore.by[user]
	for id := range seen {
		if keep != nil && !keep[id] {
			delete(seen, id)
			delete(seenStore.byTask[id], user)
		}
	}
	text, err := json.MarshalIndent(seen, "", "  ")
	seenStore.Unlock()
	if err == nil {
		err = atomicfile.WriteOwnerOnly(path, string(text)+"\n")
	}
	if err != nil {
		logf("tasks: what %s has seen could not be written: %v", user, err)
	}
}

// canHaveNews: the tasks that can still have news for user: the open ones
// they see, and those closed within two months (news of a closed task is
// told for this month and the last: Store.RecentClosed). A look at any other
// is taken out of their file once it names more than seenKept tasks.
func canHaveNews(user string) map[string]bool {
	since := now().AddDate(0, -2, 0).Format(time.DateOnly)
	keep := map[string]bool{}
	open, done := taskRecords(asUser(user))
	for _, d := range open {
		keep[d.Task.ID] = true
	}
	for _, d := range done {
		if d.Task.ClosedAt >= since {
			keep[d.Task.ID] = true
		}
	}
	return keep
}

// historyWithSeen: the task's history with who looked at it (seenStore), as
// "seen" entries where their time puts them: what the news' rule reads
// (newsFor) and what the API answers, so the browser's rule and a task's
// History ("Seen · anna") have them as they always did. A "seen" entry in
// the task's file (written there by hand or by an import) is none.
func historyWithSeen(t *Task) []HistoryEntry {
	if t.Private() {
		return t.History
	}
	seen := seenOf(t.ID)
	isSeen := func(e HistoryEntry) bool { return e.Event == "seen" }
	if len(seen) == 0 && !slices.ContainsFunc(t.History, isSeen) {
		return t.History
	}
	out := make([]HistoryEntry, 0, len(t.History)+len(seen))
	for _, e := range t.History {
		if !isSeen(e) {
			out = append(out, e)
		}
	}
	users := make([]string, 0, len(seen))
	for user := range seen {
		users = append(users, user)
	}
	sort.Slice(users, func(i, j int) bool {
		return cmp.Or(cmp.Compare(seen[users[i]].At, seen[users[j]].At), cmp.Compare(users[i], users[j])) < 0
	})
	for _, user := range users {
		// To: the last comment the look saw, which the news' rule reads.
		at, e := seen[user], HistoryEntry{At: seen[user].At, Event: "seen", By: user, To: seen[user].Comment}
		// Right after the entry that was the last when they looked, when the
		// history still has it; else where its time puts it.
		i := -1
		if at.After != "" {
			for j := len(out) - 1; j >= 0; j-- {
				if out[j].Event != "seen" && out[j].At <= at.At && lookAfter(t.ID, out[j]) == at.After {
					i = j + 1
					break
				}
			}
		}
		if i < 0 {
			out = insertByTime(out, e)
			continue
		}
		// After the looks of others that are there already.
		for i < len(out) && out[i].Event == "seen" && out[i].At <= at.At {
			i++
		}
		out = slices.Insert(out, i, e)
	}
	return out
}

// insertByTime puts e into the history h (oldest first) after the last
// entry that is not later than it: within one minute a look counts as the
// later one.
func insertByTime(h []HistoryEntry, e HistoryEntry) []HistoryEntry {
	i := len(h)
	for i > 0 && h[i-1].At > e.At {
		i--
	}
	return slices.Insert(h, i, e)
}

// sentHistory: a history a client sent for a task (an undo sends the old
// one back whole), as it is kept: without the looks, which are not the
// task's (historyWithSeen added them to what the client was answered), but
// with the "seen" entries its file had.
func sentHistory(sent, had []HistoryEntry) []HistoryEntry {
	out := slices.DeleteFunc(sent, func(e HistoryEntry) bool { return e.Event == "seen" })
	for _, e := range had {
		if e.Event == "seen" {
			out = insertByTime(out, e)
		}
	}
	return capHistory(out)
}
