package main

import (
	"reflect"
	"sort"
	"strings"
	"testing"
)

// A list is declared by a page's [[deck list=…]], outside code; the first
// page A–Z is its home. A task belongs to the first (A–Z) of its tags that
// is a list.
func TestDeckLists(t *testing.T) {
	testWiki(t, map[string]string{
		"shop": "# Shop\n\n[[deck list=Groceries keep=1 labels=Catalogue,,Shopping]]\n",
		"code": "# Code\n\n`[[deck list=nope]]`\n\n```\n[[deck list=nada]]\n```\n",
		"home": "# Home\n\n[[deck list=chores]]\n",
	})
	lists := deckLists()
	var names []string
	for n := range lists {
		names = append(names, n)
	}
	sort.Strings(names)
	if !reflect.DeepEqual(names, []string{"chores", "groceries"}) || lists["groceries"].Slug != "shop" ||
		lists["groceries"].Params.Get("keep") != "1" {
		t.Errorf("lists %+v", lists)
	}
	for tags, want := range map[string]string{"work": "", "GROCERIES": "groceries", "groceries chores": "chores"} {
		if got := taskList(&Task{Tags: strings.Fields(tags)}, nil); got != want {
			t.Errorf("taskList(%q) = %q, want %q", tags, got, want)
		}
	}
	putPage(t, "a-list", "# A\n\n[[deck list=groceries]]\n")
	if home := deckLists()["groceries"].Slug; home != "a-list" {
		t.Errorf("home %q", home)
	}
}

// A task's last activity: its completion, history, comments, creation.
func TestLastActivity(t *testing.T) {
	// As a file has them: a junk history entry is no event.
	task := func(s string) *Task { o, _ := parseObj([]byte(s)); return taskFromJSON(o) }
	for _, c := range []struct {
		t    *Task
		want string
	}{
		{task(`{"created_at": "2026-01-02T10:00:00"}`), "2026-01-02"},
		{task(`{"created_at": "2026-01-02T10:00:00", "history": [{"at": "2026-03-04T09:00", "event": "start"}]}`), "2026-03-04"},
		{task(`{"created_at": "2026-01-02T10:00:00", "completed_at": "2026-05-06T08:00:00",
			"history": [{"at": "2026-03-04T09:00"}, "junk"]}`), "2026-05-06"},
		{task(`{"created_at": "2026-01-02T10:00:00", "comments": [{"at": "2026-04-01T09:00", "text": "x"}]}`), "2026-04-01"},
		{task(`{}`), ""},
	} {
		if got := lastActivity(c.t); got != c.want {
			t.Errorf("lastActivity(%+v) = %q, want %q", c.t, got, c.want)
		}
	}
}

// Dates as <time> elements the browser shows in LEAF_LOCALE (js/util.js
// localizeDates); another year's with its year, LEAF_DATE_FORMAT=iso as ISO.
func TestShortDate(t *testing.T) {
	testWiki(t, nil)
	for _, c := range [][3]string{
		{"2026-10-05", "2026-10-18", `<time class="local-date" datetime="2026-10-05">Oct 5</time>`},
		{"2025-10-05T09:30", "2026-10-18", `<time class="local-date" datetime="2025-10-05">Oct 5, 2025</time>`},
		{"<b>", "2026-10-18", "&lt;b&gt;"},
	} {
		if got := shortDate(c[0], c[1]); got != c[2] {
			t.Errorf("shortDate(%q) = %q, want %q", c[0], got, c[2])
		}
	}
	cfg.DateFormat = "iso"
	if got := shortDate("2026-10-05T09:30", "2026-10-18"); got != `<time class="local-date" datetime="2026-10-05">2026-10-05</time>` {
		t.Errorf("iso: %q", got)
	}
}

func TestParseDeckParams(t *testing.T) {
	for raw, want := range map[string]map[string]string{
		"":         {},
		"tag=work": {"tag": "work"},
		"tag=work view=plan limit=5 sort=due_date": {"tag": "work", "view": "plan", "limit": "5", "sort": "due_date"},
		"list=groceries":     {"list": "groceries"},
		"Tag=work VIEW=plan": {"tag": "work", "view": "plan"},
	} {
		if got := parseDeckParams(raw).m; !reflect.DeepEqual(got, want) {
			t.Errorf("parseDeckParams(%q) = %v, want %v", raw, got, want)
		}
	}
}
