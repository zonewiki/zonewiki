// Changes (/changes): the wiki's recent commits in the browser, as the MCP
// tool history gives them without a slug: when, who, what, which pages and
// tasks, each as its reader may see it (docs/spec/rendering.md, Changes).
package main

import (
	"html/template"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"
)

// assistantMark ends the message of a commit an assistant made (msgSuffix).
const assistantMark = "(MCP)"

// byAssistant: the commit with this message was written by an assistant.
func byAssistant(msg string) bool { return strings.HasSuffix(msg, assistantMark) }

// changesOf: which of the wiki's commits to list. Since a day (YYYY-MM-DD),
// Under a folder, By a user (the commit's author), Assistant: only what an
// assistant wrote; each empty or false for all. Sharing: the browser is in
// sharing mode, which leaves out a commit that changed a page not for
// sharing.
type changesOf struct {
	Since, Under, By   string
	Assistant, Sharing bool
}

// wikiChanges: the wiki's latest commits, newest first, as user sees them,
// for every door that lists them (Changes in the browser, the MCP tool
// history): of a commit only the files they may see (canSeePath), not a
// commit that changed none of those, and its message without the names of
// what they don't see (hideNames). In sharing mode a commit shows only
// when all it changed shows: its message names what it changed.
func wikiChanges(user string, of changesOf, limit int) []LogEntry {
	see := func(path string) bool {
		if !canSeePath(user, path) {
			return false
		}
		slug, isPage := strings.CutSuffix(path, ".md")
		return !(of.Sharing && isPage && isNoshare(slug))
	}
	keep := func(e LogEntry) bool {
		return (of.By == "" || e.Author == of.By) && (!of.Assistant || byAssistant(e.Msg)) &&
			!(of.Sharing && e.Hidden > 0)
	}
	entries := gitLogAll(of.Since, of.Under, limit, see, keep)
	for i := range entries {
		entries[i].Msg = hideNames(user, entries[i].Msg)
	}
	return entries
}

const (
	changesShown = 50  // commits on /changes
	changesMost  = 500 // and at most, with "Show more"
)

var dayRE = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// changesView: the page /changes. Its filters are the query: user, under,
// since, assistant=1; n how many to show.
func changesView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	q := r.URL.Query()
	of := changesOf{Under: cleanFolder(q.Get("under")), By: strings.TrimSpace(q.Get("user")),
		Assistant: q.Get("assistant") != "", Sharing: rq.sharing}
	if since := strings.TrimSpace(q.Get("since")); dayRE.MatchString(since) {
		of.Since = since
	}
	users := userNames()
	if !slices.Contains(users, of.By) {
		of.By = "" // no user's name: all of them (and a wiki without users has none)
	}
	limit := changesShown
	if n, err := strconv.Atoi(q.Get("n")); err == nil {
		limit = min(max(n, 1), changesMost)
	}
	entries := wikiChanges(rq.user, of, limit)
	v := &changesPageView{Of: of, Users: users, Days: changeDays(entries, time.Now()),
		Filtered:       of.Since != "" || of.Under != "" || of.By != "" || of.Assistant,
		BreadcrumbHTML: breadcrumb(rq, []crumb{{"Changes", ""}}, "", false, "", "")}
	if len(entries) == limit && limit < changesMost {
		v.More = changesURL(of, min(2*limit, changesMost))
	}
	page(w, rq, http.StatusOK, "history/changes.html", "Changes", shell{}, v)
}

// userNames: the wiki's users by name, sorted; none without users.
func userNames() []string {
	var names []string
	for name := range settings().Users {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// changesURL: /changes with these filters, showing n (0: as many as it does unasked).
func changesURL(of changesOf, n int) string {
	q := url.Values{}
	set := func(key, value string) {
		if value != "" {
			q.Set(key, value)
		}
	}
	set("user", of.By)
	set("under", of.Under)
	set("since", of.Since)
	if of.Assistant {
		q.Set("assistant", "1")
	}
	if n > 0 {
		q.Set("n", strconv.Itoa(n))
	}
	if len(q) == 0 {
		return "/changes"
	}
	return "/changes?" + q.Encode()
}

// changesPageView: the wiki's commits by day (history/changes.html).
type changesPageView struct {
	view
	Of             changesOf
	Users          []string // for the filter by user; none without users
	Days           []changeDay
	Filtered       bool   // a filter is set
	More           string // where the list goes on: the same with more commits
	BreadcrumbHTML template.HTML
}

// changeDay: a day's commits, newest first; Label is Today, Yesterday or the day.
type changeDay struct {
	Label string
	Rows  []changeRow
}

// changeRow: a commit: Time its time of day, What its message without the
// assistant's mark, Items what it changed.
type changeRow struct {
	Time, Rel, What, Author string
	Assistant               bool
	Items                   []changeItem
}

// changeItem: something a commit changed: a page (Href: what the commit
// changed on it), the tasks, or another file by its path (no Href).
type changeItem struct{ Label, Href string }

// changeDays: commits, newest first, as the rows of their days.
func changeDays(entries []LogEntry, now time.Time) []changeDay {
	today, yesterday := now.Format("2006-01-02"), now.AddDate(0, 0, -1).Format("2006-01-02")
	var days []changeDay
	last := ""
	for _, e := range entries {
		day, clock, _ := strings.Cut(e.Time, " ")
		if day != last || len(days) == 0 {
			label := day
			switch day {
			case today:
				label = "Today"
			case yesterday:
				label = "Yesterday"
			}
			days, last = append(days, changeDay{Label: label}), day
		}
		row := changeRow{Time: clock, Rel: e.Rel, Author: e.Author, Assistant: byAssistant(e.Msg),
			What: strings.TrimSpace(strings.TrimSuffix(e.Msg, assistantMark)), Items: changeItems(e)}
		days[len(days)-1].Rows = append(days[len(days)-1].Rows, row)
	}
	return days
}

// changeItems: what a commit changed, each thing once: its pages by slug,
// "Tasks" for the task files, any other file (an upload) by its path.
func changeItems(e LogEntry) []changeItem {
	var items []changeItem
	seen := map[string]bool{}
	for _, f := range e.Files {
		item := changeItem{Label: f}
		if slug, isPage := strings.CutSuffix(f, ".md"); isTaskFile(f) {
			item = changeItem{Label: "Tasks", Href: "/tasks"}
		} else if isPage && safePath(slug) != "" {
			item = changeItem{Label: slug, Href: pathURL("diff", slug) + "/" + e.Sha}
		}
		if key := item.Label + "\x00" + item.Href; !seen[key] {
			seen[key] = true
			items = append(items, item)
		}
	}
	return items
}
