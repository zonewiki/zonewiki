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
	"unicode"
	"zonewiki/internal/mdtext"
)

// assistantMark ends the first line of a commit an assistant made (mcpMark),
// for whoever reads the history with git. It says nothing to Zone: a page's
// name can end in it too ("notes (MCP)"), so what an assistant wrote is told
// by its trailer (commitMarkOf), and the mark is taken off only there.
const assistantMark = "(MCP)"

// viaTrailerKey names the trailer every commit an assistant writes carries
// (commitTrailers): which connection it came through, by the name its
// client gave when it was let in (grants.go), tokenVia for an MCP token.
// The name is the client's claim, and shown as one. A trailer is a line
// after the first, which no name in a commit message reaches (A name holds
// no line break in docs/spec/security.md).
const viaTrailerKey = "Assistant"

// tokenVia: what the Assistant trailer names for a write with an MCP token,
// which names no client (clientName keeps a client from calling itself so).
const tokenVia = "MCP token"

// sourceTrailerKey names the trailer that says where an assistant's write
// has its text from, when not from its user: a web page, a mail, a file
// (the write tools' source). It is what the assistant says, and shown as
// that; what it tells a reader is that nobody here wrote these words.
const sourceTrailerKey = "Source"

// commitTrailers: what follows the first line of an assistant's commit
// message: which connection it came through (via, a client's name; "" an
// MCP token) and where its text is from, each a line.
func commitTrailers(via, source string) string {
	name := tokenVia
	if via != "" {
		name = clientName(via)
	}
	lines := []string{viaTrailerKey + ": " + name}
	if source = onOneLine(source, sourceMax); source != "" {
		lines = append(lines, sourceTrailerKey+": "+source)
	}
	return "\n\n" + strings.Join(lines, "\n")
}

// withTrailers: a commit message with these trailer lines added to the ones
// it has (Zone's messages are a first line and, for an assistant's, its
// trailers: commitTrailers), or after it.
func withTrailers(message string, lines []string) string {
	if len(lines) == 0 {
		return message
	}
	if strings.Contains(message, "\n\n") {
		return message + "\n" + strings.Join(lines, "\n")
	}
	return message + "\n\n" + strings.Join(lines, "\n")
}

// sourceMax: how long a write's source may be, in characters.
const sourceMax = 200

// onOneLine: s on one line, without control characters, at most max characters.
func onOneLine(s string, max int) string {
	s = strings.Join(strings.FieldsFunc(s, func(r rune) bool { return unicode.IsControl(r) || unicode.IsSpace(r) }), " ")
	return cutRunes(s, max, "")
}

// markFormat: a commit's Assistant and Source trailers in a git log format,
// for commitMarkOf: each value alone, on one line (the first, should a
// commit have several), with a \x1f between them.
const markFormat = "%(trailers:key=" + viaTrailerKey + ",valueonly,separator=%x20)\x1f" +
	"%(trailers:key=" + sourceTrailerKey + ",valueonly,separator=%x20)"

// commitMark: what a commit says of an assistant's part in it, from its
// trailers alone (commitMarkOf).
type commitMark struct {
	// Assistant: the connection an assistant wrote it through, as its
	// Assistant trailer names it (a client's name, noClientName, tokenVia);
	// "" for a commit no assistant wrote.
	Assistant string
	// Source: where its text is from, when the assistant said; "" for a
	// commit no assistant wrote.
	Source string
}

// commitMarkOf: the mark of a commit whose Assistant and Source trailers
// have these values (as markFormat gives them). The one place that decides
// whether an assistant wrote a commit: by the trailer that each of its
// writes carries (commitTrailers), never by its message, which a page's
// name can make read like one ("Update notes (MCP)"). A commit from before
// 2026-11-20 has no such trailer when written with an MCP token, and is no
// assistant's; one made by hand says what its author wrote, as its author
// does.
func commitMarkOf(assistant, source string) commitMark {
	if assistant = onOneLine(assistant, clientNameMost); assistant == "" {
		return commitMark{}
	}
	return commitMark{Assistant: assistant, Source: onOneLine(source, sourceMax)}
}

// ByAssistant: an assistant wrote the commit.
func (m commitMark) ByAssistant() bool { return m.Assistant != "" }

// Via: which assistant the commit names as its writer: its connection's
// name; "" for none (no assistant's, an MCP token's, a client that gave no
// name).
func (m commitMark) Via() string {
	if m.Assistant == tokenVia || m.Assistant == noClientName {
		return ""
	}
	return m.Assistant
}

// through: " through Claude" after who a commit is by, " through an
// assistant" for one that names none; "" for no assistant's.
func (m commitMark) through() string {
	switch {
	case !m.ByAssistant():
		return ""
	case m.Via() != "":
		return " through " + m.Via()
	}
	return " through an assistant"
}

// commitWhat: what a commit with this first line and mark says it did, as
// the history shows it: an assistant's without assistantMark.
func commitWhat(subject string, m commitMark) string {
	if !m.ByAssistant() {
		return subject
	}
	return strings.TrimSpace(strings.TrimSuffix(subject, assistantMark))
}

// taskTrailerKey names the trailers of a commit that changed tasks, a task
// each, by its id (taskTrailers): which tasks Changes names for it, and
// whether a later commit changed one of them again (undoOffers), without a
// task file read. The task store knows which tasks a write changed
// (taskfiles.Store.Touched); a commit names at most taskTrailersMost, and
// says how many more it changed (moreTasksTrailerKey).
const (
	taskTrailerKey      = "Task"
	moreTasksTrailerKey = "More-Tasks"
	taskTrailersMost    = 20
)

// taskTrailers: the trailer lines naming the tasks ids a commit changed:
// "Task: <id>" for each of the first taskTrailersMost whose id is one plain
// word (taskFolderRE: what Zone makes, an import keeps; nothing that could
// start a line of its own), "More-Tasks: N" for the rest.
func taskTrailers(ids []string) []string {
	var lines []string
	more := 0
	for _, id := range ids {
		if len(lines) < taskTrailersMost && taskFolderRE.MatchString(id) {
			lines = append(lines, taskTrailerKey+": "+id)
		} else {
			more++
		}
	}
	if more > 0 {
		lines = append(lines, moreTasksTrailerKey+": "+strconv.Itoa(more))
	}
	return lines
}

// tasksFormat: a commit's Task trailers in a git log format, for
// taskMarkOf: their values with a comma between them, a \x1f, and its
// More-Tasks trailer's.
const tasksFormat = "%(trailers:key=" + taskTrailerKey + ",valueonly,separator=%x2C)\x1f" +
	"%(trailers:key=" + moreTasksTrailerKey + ",valueonly,separator=%x2C)"

// taskMark: which tasks a commit says it changed (its Task trailers), by
// id, and how many more it changed than it names. A commit that changed
// task files and names none is from before 2026-11-20, or a write of every
// owner's files that doesn't name them (a tag or list renamed).
type taskMark struct {
	Tasks     []string
	MoreTasks int
}

// taskMarkOf: the task mark of a commit whose Task and More-Tasks trailers
// have these values (as tasksFormat gives them).
func taskMarkOf(ids, more string) taskMark {
	var m taskMark
	for _, id := range strings.Split(ids, ",") {
		if id = strings.TrimSpace(id); taskFolderRE.MatchString(id) && !slices.Contains(m.Tasks, id) {
			m.Tasks = append(m.Tasks, id)
		}
	}
	m.MoreTasks, _ = strconv.Atoi(strings.TrimSpace(more))
	return m
}

// fromWhere: "; from https://…" after who a commit is by; "" for none.
func fromWhere(source string) string {
	if source == "" {
		return ""
	}
	return "; from " + source
}

// changesOf: which of the wiki's commits to list. Since a day (YYYY-MM-DD),
// Under a folder, By a user (the commit's author), Assistant: only what an
// assistant wrote; Via: only what one wrote through this connection (its
// Assistant trailer, as commitMarkOf reads it: a client's name, tokenVia,
// noClientName); Outside: only what an assistant said its text is from
// (its Source trailer); each empty or false for all. Sharing: the browser
// is in sharing mode, which leaves out a commit that changed a page not
// for sharing.
type changesOf struct {
	Since, Under, By, Via       string
	Assistant, Outside, Sharing bool
}

// takes: of says to list a commit with this author and mark (the filters
// asked of each commit as it is read: gitLogAll's keep).
func (of changesOf) takes(author string, m commitMark) bool {
	return (of.By == "" || author == of.By) && (!of.Assistant || m.ByAssistant()) &&
		(of.Via == "" || m.Assistant == of.Via) && (!of.Outside || m.Source != "")
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
	keep := func(e LogEntry) bool { return of.takes(e.Author, e.commitMark) && !(of.Sharing && e.Hidden > 0) }
	entries := gitLogAll(of.Since, of.Under, limit, see, keep)
	for i := range entries {
		entries[i].Msg = hideNames(user, entries[i].Msg, entries[i].hidden)
	}
	return entries
}

const (
	changesShown = 50  // commits on /changes
	changesMost  = 500 // and at most, with "Show more"
)

var dayRE = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// changesView: the page /changes. Its filters are the query: user, under,
// since, assistant=1, via, outside=1; n how many to show.
func changesView(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !cfg.GitEnabled {
		http.NotFound(w, r)
		return
	}
	q := r.URL.Query()
	of := changesOf{Under: cleanFolder(q.Get("under")), By: strings.TrimSpace(q.Get("user")),
		Via: onOneLine(q.Get("via"), clientNameMost), Assistant: q.Get("assistant") != "", Outside: q.Get("outside") != "",
		Sharing: rq.sharing}
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
	v := &changesPageView{Of: of, Users: users, Vias: changesVias(rq.user, of, entries),
		Days: changeDays(entries, time.Now(), undoOffers(rq.user, entries), tasksOfChanges(rq.user, entries, rq.sharing)),
		Back: changesURL(of, shown), Undone: q.Get("undone"), UndoError: q.Get("undo_error"),
		Filtered:       of.Since != "" || of.Under != "" || of.By != "" || of.Via != "" || of.Assistant || of.Outside,
		BreadcrumbHTML: breadcrumb(rq, []crumb{{"Changes", ""}}, "", false, "", "")}
	if len(entries) == limit && limit < changesMost {
		v.More = changesURL(of, min(2*limit, changesMost))
	}
	page(w, rq, http.StatusOK, "history/changes.html", "Changes", shell{}, v)
}

// changesVias: the connections Changes offers to filter by, A–Z: user's
// own (grantsOf, and tokenVia when they have an MCP token: the wiki's,
// without users), the ones the commits listed name (which user sees on
// them), and the one asked for. None where there are none.
func changesVias(user asker, of changesOf, entries []LogEntry) []string {
	var names []string
	add := func(name string) {
		if name != "" && !slices.Contains(names, name) {
			names = append(names, name)
		}
	}
	if user.Name != visitor {
		for _, g := range grantsOf(user.Name) {
			add(clientName(g.Client))
		}
		s := settings()
		if u := s.Users[user.Name]; s.Users == nil && s.MCPToken != "" || u != nil && u.MCPToken != "" {
			add(tokenVia)
		}
	}
	for _, e := range entries {
		add(e.Assistant)
	}
	add(of.Via)
	sort.Strings(names)
	return names
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
	set("via", of.Via)
	if of.Assistant {
		q.Set("assistant", "1")
	}
	if of.Outside {
		q.Set("outside", "1")
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
	Vias           []string // for the filter by connection (changesVias)
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
	Source                  string // where its text is from, when the assistant said
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
			if run[0].Via() != "" {
				by = run[0].Via()
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
		if !e.ByAssistant() {
			continue
		}
		key := e.Author + "\x00" + e.Assistant
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
		for _, key := range []string{"user", "under", "since", "via", "assistant", "outside", "n"} {
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
// changed on it), a task (Task: its id; Href: the task in its list), the
// tasks without a name, or another file by its path (no Href).
type changeItem struct{ Label, Href, Task string }

// changedTasks: the tasks a list of commits names (their Task trailers) as
// their reader sees them now: seen, those they see, as they are now (the
// search's records, tasksOfIDs: no task file read for them); gone, the ids
// no task has any more (deleted for good); and the lists they see, for a
// task's link. A task someone else sees is in neither: it isn't named,
// nor counted.
type changedTasks struct {
	seen  map[string]*Task
	gone  map[string]bool
	lists map[string]ListInfo
}

// tasksOfChanges: the tasks the commits name, as user sees them. In
// sharing mode not one in a list that is not for sharing, nor one in no
// list (with users a user's Inbox), as the list switcher leaves them out
// (listHidden).
func tasksOfChanges(user asker, entries []LogEntry, sharing bool) changedTasks {
	ids := map[string]bool{}
	for _, e := range entries {
		for _, id := range e.Tasks {
			ids[id] = true
		}
	}
	ct := changedTasks{seen: map[string]*Task{}, gone: map[string]bool{}}
	if len(ids) == 0 {
		return ct
	}
	ct.seen, ct.gone = tasksOfIDs(user, ids)
	ct.lists = taskLists(user)
	if sharing {
		for id, t := range ct.seen {
			l, ok := ct.lists[taskList(t, ct.lists)]
			if ok && l.NoShare || !ok && settings().Users != nil {
				delete(ct.seen, id)
			}
		}
	}
	return ct
}

// changeDays: commits, newest first, as the rows of their days, each with
// what it offers to undo and the tasks it changed as ct has them.
func changeDays(entries []LogEntry, now time.Time, offers map[string]undoOffer, ct changedTasks) []changeDay {
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
		row := changeRow{Time: clock, Rel: e.Rel, Author: e.Author, Assistant: e.ByAssistant(), Via: e.Via(), Source: e.Source,
			undoOffer: offers[e.Sha], What: commitWhat(e.Msg, e.commitMark), Items: changeItems(e, ct)}
		days[len(days)-1].Rows = append(days[len(days)-1].Rows, row)
	}
	return days
}

// changeItems: what a commit changed, each thing once: its pages by slug,
// any other file (an upload) by its path, then the tasks it names that its
// reader sees (ct), each by its title now; "Tasks" for its task files when
// it names none of them ("other tasks" beside those it names when it
// changed more than it names, or one deleted for good since). A task the
// reader doesn't see adds nothing.
func changeItems(e LogEntry, ct changedTasks) []changeItem {
	var items []changeItem
	seen := map[string]bool{}
	add := func(item changeItem) {
		if key := item.Label + "\x00" + item.Href; !seen[key] {
			seen[key] = true
			items = append(items, item)
		}
	}
	tasks := false
	for _, f := range e.Files {
		if isTaskFile(f) {
			tasks = true
			continue
		}
		item := changeItem{Label: f}
		if slug, isPage := strings.CutSuffix(f, ".md"); isPage && safePath(slug) != "" {
			item = changeItem{Label: slug, Href: pathURL("diff", slug) + "/" + e.Sha}
		}
		add(item)
	}
	if !tasks {
		return items
	}
	named, unnamed := 0, len(e.Tasks) == 0 || e.MoreTasks > 0
	for _, id := range e.Tasks {
		if t := ct.seen[id]; t != nil {
			add(changeItem{Label: taskLabel(t), Href: taskLink(t, taskList(t, ct.lists)), Task: t.ID})
			named++
		} else if ct.gone[id] {
			unnamed = true
		}
	}
	if named == 0 || unnamed {
		label := "Tasks"
		if named > 0 {
			label = "other tasks"
		}
		add(changeItem{Label: label, Href: "/tasks"})
	}
	return items
}

// taskLabel: a task as Changes names it: its title, plain and short.
func taskLabel(t *Task) string {
	if title := cutRunes(mdtext.PlainTaskTitle(t.Title), 80, "…"); title != "" {
		return title
	}
	return "a task"
}
