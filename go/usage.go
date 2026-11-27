// Usage (docs/spec/users.md, Usage): how much the wiki is used, as counts a
// day: sign-ins, people active, page views, searches, changes to pages and
// tasks, what assistants wrote and asked. Numbers only: no page, no task, no
// query, and no name beside a count. The one thing kept by name is the day
// each user was last active, in a file of its own.
//
// The counts are for the wiki's admins (/usage) and, in
// .zone/usage/counts.json, for whoever runs the host; .zone/usage/users.json
// (the last day by name) is read by Zonewiki alone. Both are state, out of
// git like all of .zone/, and hold usageDays days: what is older is dropped
// at each write.
package main

import (
	"encoding/json"
	"errors"
	"html/template"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/atomicfile"
)

const (
	// usageDays: how many days of counts are kept, today included, and for
	// how long a user's last day.
	usageDays = 56
	// usageFlushEvery: counts go to their files at the end of a request,
	// when the last write is this long ago, and at an idle exit. A Zonewiki
	// that is killed loses the counts of under a minute's use.
	usageFlushEvery = time.Minute
	usageDateFormat = "2006-01-02"
)

// usageDay: one day's counts.
type usageDay struct {
	Logins         int `json:"logins,omitempty"`          // sessions begun: a sign-in, a welcome link
	Users          int `json:"users,omitempty"`           // people active: a signed-in request, or one of their assistant's
	PageViews      int `json:"page_views,omitempty"`      // pages shown
	Searches       int `json:"searches,omitempty"`        // search requests of the browser (the dialog asks as one types)
	PageEdits      int `json:"page_edits,omitempty"`      // commits by people that changed pages or files
	TaskEdits      int `json:"task_edits,omitempty"`      // commits by people that changed tasks
	AssistantEdits int `json:"assistant_edits,omitempty"` // commits by assistants, pages and tasks
	MCPCalls       int `json:"mcp_calls,omitempty"`       // tools assistants called, reads included
}

func (d *usageDay) add(o *usageDay) {
	d.Logins += o.Logins
	d.Users += o.Users
	d.PageViews += o.PageViews
	d.Searches += o.Searches
	d.PageEdits += o.PageEdits
	d.TaskEdits += o.TaskEdits
	d.AssistantEdits += o.AssistantEdits
	d.MCPCalls += o.MCPCalls
}

// usageCounts: .zone/usage/counts.json. LastActive says for each day how
// many users were last active on it (within usageDays), so that "active in
// the last N days" can be worked out at any later time, without a name.
type usageCounts struct {
	Days       map[string]*usageDay `json:"days"`
	LastActive map[string]int       `json:"last_active"`
	Written    string               `json:"written"` // when, RFC 3339
}

// usageStats: the counts in memory, as the files have them plus what came since
// the last write. on: this process serves the wiki (a command's commits,
// a test's, count nothing). seen: each user's last active day ("" is the
// one person of a wiki without users).
var usageStats struct {
	sync.Mutex
	on      bool
	dir     string
	days    map[string]*usageDay
	seen    map[string]string
	dirty   bool
	flushed time.Time
	failed  bool // the last write failed, and it was said
}

func usageDir() string { return statePath("usage") }

// startUsage: the server counts from here on.
func startUsage() {
	usageStats.Lock()
	defer usageStats.Unlock()
	usageStats.on = true
}

// loadUsage reads the files once per data folder; the caller holds the
// lock. A file that can't be read starts empty: counts are no reason to
// refuse anyone.
func loadUsage() {
	dir := usageDir()
	if usageStats.days != nil && usageStats.dir == dir {
		return
	}
	usageStats.dir, usageStats.days, usageStats.seen = dir, map[string]*usageDay{}, map[string]string{}
	var counts usageCounts
	if readUsageFile(filepath.Join(dir, "counts.json"), &counts) {
		for day, d := range counts.Days {
			if d != nil {
				usageStats.days[day] = d
			}
		}
	}
	readUsageFile(filepath.Join(dir, "users.json"), &usageStats.seen)
	if usageStats.seen == nil {
		usageStats.seen = map[string]string{}
	}
}

func readUsageFile(path string, into any) bool {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return false
	}
	if err == nil {
		err = json.Unmarshal(b, into)
	}
	if err != nil {
		logf("%s/usage/%s: %v; counting from nothing", stateDirName, filepath.Base(path), err)
		return false
	}
	return true
}

// countUsage adds to today's counts, where this process serves the wiki.
func countUsage(count func(d *usageDay)) {
	usageStats.Lock()
	defer usageStats.Unlock()
	if !usageStats.on {
		return
	}
	loadUsage()
	count(usageToday())
	usageStats.dirty = true
}

// usageToday: today's counts; the caller holds the lock.
func usageToday() *usageDay {
	today := now().Format(usageDateFormat)
	d := usageStats.days[today]
	if d == nil {
		d = &usageDay{}
		usageStats.days[today] = d
	}
	return d
}

// usageActive: user asked for something today (signed in, or through their
// assistant): the day's people go up by one the first time, and the day is
// the user's last. A visitor is nobody.
func usageActive(user string) {
	if user == visitor {
		return
	}
	usageStats.Lock()
	defer usageStats.Unlock()
	if !usageStats.on {
		return
	}
	loadUsage()
	today := now().Format(usageDateFormat)
	if usageStats.seen[user] == today {
		return
	}
	usageStats.seen[user] = today
	usageToday().Users++
	usageStats.dirty = true
}

// usageCommit counts a commit made while serving: an assistant's by its
// trailer (commitTrailers), anyone else's as a change to tasks when all it
// changed is task files, else to pages. Zonewiki's own commits at start come
// before startUsage.
func usageCommit(message string, changed []string) {
	countUsage(func(d *usageDay) {
		switch {
		case strings.Contains(message, "\n"+viaTrailerKey+": "):
			d.AssistantEdits++
		case !slices.ContainsFunc(changed, func(rel string) bool { return !isTaskFile(rel) }):
			d.TaskEdits++
		default:
			d.PageEdits++
		}
	})
}

// usagePatterns: the routes counted as they are answered, by the mux's
// pattern: a page shown, a search asked.
var usagePatterns = map[string]func(d *usageDay, r *http.Request){
	"GET /page/{slug...}": func(d *usageDay, r *http.Request) { d.PageViews++ },
	"GET /search":         usageSearch,
	"GET /api/search":     usageSearch,
}

func usageSearch(d *usageDay, r *http.Request) {
	if strings.TrimSpace(r.URL.Query().Get("q")) != "" {
		d.Searches++
	}
}

// statusWriter notes the status a route answers with.
type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(status int) {
	if s.status == 0 {
		s.status = status
	}
	s.ResponseWriter.WriteHeader(status)
}

func (s *statusWriter) Write(b []byte) (int, error) {
	if s.status == 0 {
		s.status = http.StatusOK
	}
	return s.ResponseWriter.Write(b)
}

func (s *statusWriter) Unwrap() http.ResponseWriter { return s.ResponseWriter }

// usageGate counts the routes of usagePatterns that answered with a page
// or a result (200), before next.
func usageGate(mux *http.ServeMux, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, pattern := mux.Handler(r)
		count := usagePatterns[pattern]
		if count == nil {
			next.ServeHTTP(w, r)
			return
		}
		sw := &statusWriter{ResponseWriter: w}
		next.ServeHTTP(sw, r)
		if sw.status == http.StatusOK {
			countUsage(func(d *usageDay) { count(d, r) })
		}
	})
}

// flushUsageSoon writes the counts when they changed and the last write is
// usageFlushEvery ago: at the end of a request, so nothing runs for it
// while nobody asks.
func flushUsageSoon() {
	usageStats.Lock()
	due := usageStats.on && usageStats.dirty && now().Sub(usageStats.flushed) >= usageFlushEvery
	usageStats.Unlock()
	if due {
		flushUsage()
	}
}

// flushUsage writes both files, without the days and the users past
// usageDays and the users the wiki no longer has. A failure is logged once,
// and the counts stay in memory for the next try.
func flushUsage() {
	usageStats.Lock()
	defer usageStats.Unlock()
	if !usageStats.on || !usageStats.dirty {
		return
	}
	usageStats.flushed = now()
	pruneUsage()
	counts := usageCounts{Days: usageStats.days, LastActive: map[string]int{}, Written: now().UTC().Format(time.RFC3339)}
	for _, day := range usageStats.seen {
		counts.LastActive[day]++
	}
	err := os.MkdirAll(usageStats.dir, 0o777)
	if err == nil {
		b, _ := json.MarshalIndent(counts, "", "  ")
		err = atomicfile.Write(filepath.Join(usageStats.dir, "counts.json"), string(b)+"\n")
	}
	if err == nil {
		b, _ := json.MarshalIndent(usageStats.seen, "", "  ")
		err = atomicfile.WriteOwnerOnly(filepath.Join(usageStats.dir, "users.json"), string(b)+"\n")
	}
	if err != nil {
		if !usageStats.failed {
			logf("the usage counts could not be written (they stay in memory): %v", err)
		}
		usageStats.failed = true
		return
	}
	usageStats.dirty, usageStats.failed = false, false
}

// pruneUsage drops the days before the first of the usageDays kept, the
// users last active before it, and those who are no users now; the caller
// holds the lock.
func pruneUsage() {
	first := usageFirstDay()
	for day := range usageStats.days {
		if day < first {
			delete(usageStats.days, day)
		}
	}
	s := settings()
	for user, day := range usageStats.seen {
		if day < first || (s.Users == nil) != (user == "") || (s.Users != nil && s.Users[user] == nil) {
			delete(usageStats.seen, user)
		}
	}
}

// usageFirstDay: the oldest day kept.
func usageFirstDay() string {
	return now().AddDate(0, 0, -(usageDays - 1)).Format(usageDateFormat)
}

// seesUsage: who asks may see the wiki's usage: an admin, and in a wiki
// without users whoever is let in (signed in with its one password, or
// anyone where it has no login); never a visitor.
func seesUsage(rq *Req) bool {
	if rq == nil || rq.r == nil {
		return false
	}
	s := settings()
	if s.Users == nil {
		return rq.user.Name != visitor && (!s.authOn() || isSignedIn(rq.r))
	}
	return s.isAdmin(rq.user.Name)
}

func usageRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /usage", usagePage)
}

// usageView: the page (usage.html).
type usageView struct {
	view
	BreadcrumbHTML template.HTML
	Spans          []string   // the columns: the last 7 days, 30 days, all that is kept
	Rows           []usageRow // a count each
	Days           []usageDayRow
	Users          []usageUser // with users: each one's last active day
	Kept           int         // usageDays
	Weeks          int
}

// usageRow: one count over each span; People (who was active) has no sums:
// its numbers are of users, from their last days.
type usageRow struct {
	Label, Hint string
	Counts      []int
}

// usageDayRow: a day that counted something, the newest first.
type usageDayRow struct {
	Day string
	usageDay
}

// usageUser: a user and the day they were last active ("": not within
// usageDays).
type usageUser struct{ Name, Last string }

// usagePage: /usage, for whoever seesUsage; anyone else gets a 404, as for
// a page that isn't there.
func usagePage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !seesUsage(rq) {
		http.NotFound(w, r)
		return
	}
	spans := []int{7, 30, usageDays}
	v := &usageView{Spans: []string{"Last 7 days", "Last 30 days", "Last 8 weeks"}, Kept: usageDays, Weeks: usageDays / 7,
		BreadcrumbHTML: breadcrumb(rq, settingsCrumb(rq, "Usage"), "", false, "", "")}
	sums := make([]usageDay, len(spans))
	people := make([]int, len(spans))
	s := settings()

	usageStats.Lock()
	loadUsage()
	today := now()
	for i, n := range spans {
		first := today.AddDate(0, 0, -(n - 1)).Format(usageDateFormat)
		for day, d := range usageStats.days {
			if day >= first {
				sums[i].add(d)
			}
		}
		for user, day := range usageStats.seen {
			if day >= first && ((s.Users == nil && user == "") || (s.Users != nil && s.Users[user] != nil)) {
				people[i]++
			}
		}
	}
	first := usageFirstDay()
	for day, d := range usageStats.days {
		if day >= first && *d != (usageDay{}) {
			v.Days = append(v.Days, usageDayRow{day, *d})
		}
	}
	if s.Users != nil {
		for name := range s.Users {
			last := usageStats.seen[name]
			if last < first {
				last = ""
			}
			v.Users = append(v.Users, usageUser{name, last})
		}
	}
	usageStats.Unlock()

	slices.SortFunc(v.Days, func(a, b usageDayRow) int { return strings.Compare(b.Day, a.Day) })
	slices.SortFunc(v.Users, func(a, b usageUser) int {
		if a.Last != b.Last {
			return strings.Compare(b.Last, a.Last) // the latest first, never last
		}
		return strings.Compare(a.Name, b.Name)
	})
	row := func(label, hint string, of func(d *usageDay) int) {
		counts := make([]int, len(spans))
		for i := range spans {
			counts[i] = of(&sums[i])
		}
		v.Rows = append(v.Rows, usageRow{label, hint, counts})
	}
	v.Rows = append(v.Rows, usageRow{"People active", "signed in, or through their assistant", people})
	row("Sign-ins", "", func(d *usageDay) int { return d.Logins })
	row("Pages shown", "", func(d *usageDay) int { return d.PageViews })
	row("Searches", "in the browser; the search dialog asks as you type", func(d *usageDay) int { return d.Searches })
	row("Changes to pages", "by people", func(d *usageDay) int { return d.PageEdits })
	row("Changes to tasks", "by people", func(d *usageDay) int { return d.TaskEdits })
	row("Changes by assistants", "pages and tasks", func(d *usageDay) int { return d.AssistantEdits })
	row("Calls by assistants", "every tool an assistant used, reading included", func(d *usageDay) int { return d.MCPCalls })
	page(w, rq, http.StatusOK, "usage.html", "Usage", shell{noActions: false, settings: "usage"}, v)
}
