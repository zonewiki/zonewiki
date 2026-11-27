package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// countingWiki: a fresh wiki that counts, as a serving Zonewiki does, with
// the counts of whatever test ran before forgotten.
func countingWiki(t *testing.T) {
	t.Helper()
	testWiki(t, nil)
	usageStats.Lock()
	usageStats.on, usageStats.days, usageStats.seen, usageStats.dirty, usageStats.flushed, usageStats.failed = true, nil, nil, false, time.Time{}, false
	usageStats.Unlock()
	t.Cleanup(func() {
		usageStats.Lock()
		usageStats.on, usageStats.days, usageStats.seen = false, nil, nil
		usageStats.Unlock()
	})
}

func readCounts(t *testing.T) usageCounts {
	t.Helper()
	var c usageCounts
	b, err := os.ReadFile(filepath.Join(usageDir(), "counts.json"))
	if err == nil {
		err = json.Unmarshal(b, &c)
	}
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestUsageCountsNothingUnlessServing(t *testing.T) {
	countingWiki(t)
	usageStats.Lock()
	usageStats.on = false
	usageStats.Unlock()
	countUsage(func(d *usageDay) { d.Logins++ })
	usageActive("")
	usageCommit("Update index", []string{"index.md"})
	flushUsage()
	if _, err := os.Stat(usageDir()); !os.IsNotExist(err) {
		t.Fatalf("a command's work was counted: %v", err)
	}
}

func TestUsageCommitsByKind(t *testing.T) {
	countingWiki(t)
	usageCommit("Update index", []string{"index.md"})
	usageCommit("Add a file", []string{"_assets/a.png", "notes/a.md"})
	usageCommit("Update task", []string{"_tasks/tasks.json"})
	usageCommit("Update task", []string{"private/ana/_tasks/tasks.json", "groups/dev/_tasks/lists/x/tasks.json"})
	usageCommit("Rename a page", []string{"a.md", "_tasks/tasks.json"}) // a rename re-points tasks: a page's change
	usageCommit("Update index (MCP)"+commitTrailers("Claude", ""), []string{"index.md"})
	usageCommit("Update task (MCP)"+commitTrailers("", ""), []string{"_tasks/tasks.json"})
	// A message is one line unless an assistant's: a page named like the trailer is no assistant.
	usageCommit("Update Assistant: notes", []string{"assistant-notes.md"})
	flushUsage()
	got := *readCounts(t).Days[now().Format(usageDateFormat)]
	if want := (usageDay{PageEdits: 4, TaskEdits: 2, AssistantEdits: 2}); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestUsageKeepsEightWeeksAndAUsersLastDay(t *testing.T) {
	countingWiki(t)
	at := clock(t, time.Date(2026, 3, 1, 23, 50, 0, 0, time.Local))
	usageActive("")
	usageActive("") // the same person, the same day
	countUsage(func(d *usageDay) { d.PageViews += 3 })
	flushUsage()
	c := readCounts(t)
	if d := c.Days["2026-03-01"]; d == nil || d.Users != 1 || d.PageViews != 3 || c.LastActive["2026-03-01"] != 1 {
		t.Fatalf("the first day: %+v, %v", d, c.LastActive)
	}

	// The next day is a day of its own, and the person's last.
	*at = at.Add(20 * time.Minute)
	usageActive("")
	flushUsage()
	c = readCounts(t)
	if len(c.Days) != 2 || c.Days["2026-03-02"].Users != 1 || len(c.LastActive) != 1 || c.LastActive["2026-03-02"] != 1 {
		t.Fatalf("the second day: %+v, %v", c.Days, c.LastActive)
	}

	// The last day kept is usageDays-1 days back; a restart reads the files.
	*at = time.Date(2026, 3, 1, 12, 0, 0, 0, time.Local).AddDate(0, 0, usageDays-1)
	usageStats.Lock()
	usageStats.days, usageStats.seen = nil, nil
	usageStats.Unlock()
	countUsage(func(d *usageDay) { d.Searches++ })
	flushUsage()
	if c = readCounts(t); len(c.Days) != 3 || c.Days["2026-03-01"] == nil || c.Days["2026-03-01"].PageViews != 3 {
		t.Fatalf("after a restart, on the last day the first is kept: %+v", c.Days)
	}
	*at = at.AddDate(0, 0, 2)
	countUsage(func(d *usageDay) { d.Searches++ })
	flushUsage()
	c = readCounts(t)
	if len(c.Days) != 2 || c.Days["2026-03-01"] != nil || c.Days["2026-03-02"] != nil || len(c.LastActive) != 0 {
		t.Fatalf("two days later both are gone, and the person's last day with them: %+v, %v", c.Days, c.LastActive)
	}
	var seen map[string]string
	b, _ := os.ReadFile(filepath.Join(usageDir(), "users.json"))
	if err := json.Unmarshal(b, &seen); err != nil || len(seen) != 0 {
		t.Fatalf("users.json: %s, %v", b, err)
	}
}

func TestUsageIsWrittenAtMostOnceAMinute(t *testing.T) {
	countingWiki(t)
	at := clock(t, time.Date(2026, 3, 1, 12, 0, 0, 0, time.Local))
	countUsage(func(d *usageDay) { d.PageViews++ })
	flushUsageSoon() // the first: written
	countUsage(func(d *usageDay) { d.PageViews++ })
	*at = at.Add(usageFlushEvery - time.Second)
	flushUsageSoon()
	if n := readCounts(t).Days["2026-03-01"].PageViews; n != 1 {
		t.Fatalf("written again within the minute: %d", n)
	}
	*at = at.Add(time.Second)
	flushUsageSoon()
	if n := readCounts(t).Days["2026-03-01"].PageViews; n != 2 {
		t.Fatalf("not written after a minute: %d", n)
	}
	// users.json holds names: the owner's alone.
	if fi, err := os.Stat(filepath.Join(usageDir(), "users.json")); err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("users.json: %v, %v", fi, err)
	}
}
