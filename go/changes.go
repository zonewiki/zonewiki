// Changes (/changes): the wiki's recent commits in the browser, as the MCP
// tool history gives them without a slug: when, who, what, which pages and
// tasks, each as its reader may see it (docs/spec/rendering.md, Changes).
package main

import (
	"fmt"
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

// byAssistant: the commit with this message (its first line) was written by an assistant.
func byAssistant(msg string) bool { return strings.HasSuffix(msg, assistantMark) }

// viaTrailerKey names the trailer of a commit made through a connection: which
// assistant wrote it, by the name its client gave when it was let in
// (grants.go). The name is the client's claim, and shown as one.
const viaTrailerKey = "Assistant"

// viaTrailer: what follows the first line of such a commit's message; ""
// for no connection, and for a client that gave no name.
func viaTrailer(via string) string {
	if via = clientName(via); via == noClientName {
		return ""
	}
	return "\n\n" + viaTrailerKey + ": " + via
}

// viaOfCommit: which assistant a commit says it was written through: its
// trailer, when its message is an assistant's ("" otherwise: the trailer
// alone, in a commit made by hand, says nothing).
func viaOfCommit(msg, trailer string) string {
	if !byAssistant(msg) {
		return ""
	}
	if via := clientName(trailer); via != noClientName {
		return via
	}
	return ""
}

// throughWhom: " through Claude" after who a commit is by; "" for none.
func throughWhom(via string) string {
	if via == "" {
		return ""
	}
	return " through " + via
}

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
func wikiChanges(user asker, of changesOf, limit int) []LogEntry {
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
	shown := 0 // in the list's address only when asked for
	if limit != changesShown {
		shown = limit
	}
	v := &changesPageView{Of: of, Users: users, Days: changeDays(entries, time.Now(), undoOffers(rq.user, entries)),
		Back: changesURL(of, shown), Undone: q.Get("undone"), UndoError: q.Get("undo_error"),
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
	Back           string // this list's address, where an undo comes back to
	Undone         string // what an undo just did, in words
	UndoError      string // or why it didn't
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
	Via                     string // which assistant, when the commit says
	Items                   []changeItem
	undoOffer
}

// undoOffer: what a row of Changes offers to undo. Undo: the commit
// itself (its sha), "" when it can't be. Session: the commits of the
// assistant's run of work this one is the latest of (itself among them),
// when they can be undone together; SessionSays what that would do.
type undoOffer struct {
	Undo        string
	Session     []string
	SessionSays string
}

// undoWindow: how far back Changes looks for whether a page was changed
// again: a commit older than the wiki's latest undoWindow offers no Undo
// (a page's history restores any version).
const undoWindow = 1000

// sessionGap: an assistant's commits no further apart are one run of work.
const sessionGap = 30 * time.Minute

// undoOffers: for each of the commits (newest first, as user sees them)
// what Changes offers to undo, by sha. It offers what undoChangesOp will
// most likely do, cheaply, from what the list knows; the operation checks
// again, and exactly.
func undoOffers(user asker, entries []LogEntry) map[string]undoOffer {
	offers := map[string]undoOffer{}
	if len(entries) == 0 || changesNothing(user) || user.Name == visitor {
		return offers
	}
	latest := latestCommits(undoWindow)
	// pagesOnly: a commit of the kind an undo takes: pages alone, all of
	// them seen and the user's to write, none moved.
	pagesOnly := func(e LogEntry) bool {
		if e.Hidden > 0 || len(e.Files) == 0 || strings.Contains(e.Kinds, "A") && strings.Contains(e.Kinds, "D") {
			return false
		}
		for _, f := range e.Files {
			slug, isPage := strings.CutSuffix(f, ".md")
			if !isPage || safePath(slug) == "" || !canWrite(user, slug) || latest[f] == nil {
				return false
			}
		}
		return true
	}
	// together: nothing but these commits changed their pages since the
	// oldest of them did.
	together := func(run []LogEntry) bool {
		touched := map[string]map[string]bool{} // file → the run's commits that changed it
		for _, e := range run {
			for _, f := range e.Files {
				if touched[f] == nil {
					touched[f] = map[string]bool{}
				}
				touched[f][e.Sha] = true
			}
		}
		for f, ours := range touched {
			if len(latest[f]) < len(ours) {
				return false
			}
			for _, sha := range latest[f][:len(ours)] {
				if !ours[sha] {
					return false
				}
			}
		}
		return true
	}
	runs := map[string][]LogEntry{} // who and through what → their run of work being read
	finish := func(key string) {
		if run := runs[key]; len(run) > 1 && together(run) {
			o := offers[run[0].Sha]
			for _, e := range run {
				o.Session = append(o.Session, e.Sha)
			}
			by := "an assistant"
			if run[0].Via != "" {
				by = run[0].Via
			}
			o.SessionSays = fmt.Sprintf("the %d changes %s made from %s to %s", len(run), by,
				run[len(run)-1].Time, run[0].Time)
			offers[run[0].Sha] = o
		}
		delete(runs, key)
	}
	for _, e := range entries {
		if !pagesOnly(e) {
			continue
		}
		if together([]LogEntry{e}) {
			offers[e.Sha] = undoOffer{Undo: e.Sha}
		}
		if !byAssistant(e.Msg) {
			continue
		}
		key := e.Author + "\x00" + e.Via
		if run := runs[key]; len(run) > 0 && commitTime(run[len(run)-1]).Sub(commitTime(e)) > sessionGap {
			finish(key)
		}
		runs[key] = append(runs[key], e)
	}
	for key := range runs {
		finish(key)
	}
	return offers
}

// commitTime: when a commit was made, to the minute.
func commitTime(e LogEntry) time.Time {
	t, _ := time.Parse("2006-01-02 15:04", e.Time)
	return t
}

// undoChangesRoute: POST /changes/undo: the changes named by sha (one, or
// a session's) undone (undoChangesOp); back to the list it came from,
// which says what was done or why not.
func undoChangesRoute(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		badRequestPage(w)
		return
	}
	// Back to the list as it was: only its own filters, read as it reads them.
	back := url.Values{}
	if u, err := url.Parse(r.PostForm.Get("back")); err == nil {
		for _, key := range []string{"user", "under", "since", "assistant", "n"} {
			if v := u.Query().Get(key); v != "" {
				back.Set(key, v)
			}
		}
	}
	done, err := undoChangesOp(userOf(r), r.Form["sha"], "", nil)
	if rf, refused := refusalOf(err); refused {
		if rf.kind == refusedNotFound || rf.kind == refusedReadOnly {
			pageFailed(w, r, err)
			return
		}
		back.Set("undo_error", rf.msg)
	} else if err != nil {
		serverError(w, r, err)
		return
	} else {
		back.Set("undone", undoneSays(done))
	}
	http.Redirect(w, r, "/changes?"+back.Encode(), http.StatusSeeOther)
}

// undoneSays: what an undo did, in a sentence.
func undoneSays(u *undone) string {
	var parts []string
	if len(u.back) > 0 {
		parts = append(parts, strings.Join(u.back, ", ")+" as before")
	}
	if len(u.gone) > 0 {
		parts = append(parts, strings.Join(u.gone, ", ")+" deleted again")
	}
	return "Undone: " + strings.Join(parts, "; ") + "."
}

// changeItem: something a commit changed: a page (Href: what the commit
// changed on it), the tasks, or another file by its path (no Href).
type changeItem struct{ Label, Href string }

// changeDays: commits, newest first, as the rows of their days, each with
// what it offers to undo.
func changeDays(entries []LogEntry, now time.Time, offers map[string]undoOffer) []changeDay {
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
		row := changeRow{Time: clock, Rel: e.Rel, Author: e.Author, Assistant: byAssistant(e.Msg), Via: e.Via, undoOffer: offers[e.Sha],
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
