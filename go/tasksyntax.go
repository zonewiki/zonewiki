// The tasks' syntax in page text: [[tasks: …]] embeds and
// their params, [[task:<id>]] references, and how tags are written.
package main

import (
	"net/url"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskmodel"
)

var spaceRunRE = regexp.MustCompile(mdtext.SpaceClass + `+`)

// cleanList: a list name as stored and matched: trimmed, lowercase, one
// word, no leading "#"; "" for no list.
func cleanList(name string) string {
	return strings.Trim(spaceRunRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(name), "#"))), "-"), "-")
}

var (
	// [[tasks: …]], with a colon so a link [[Tasks]] stays a link.
	tasksRE   = regexp.MustCompile(`(?i)\[\[tasks:` + mdtext.SpaceClass + `*([^\]]*)\]\]`)
	taskRefRE = regexp.MustCompile(`\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]`)
	mdLinkRE  = regexp.MustCompile(`\[([^\]]*)\]\(/page/([^)]+)\)`)
)

// TasksParams are an embed's key=value pairs, in order. src: where they are
// written, for the settings dialog (nil: nowhere it can change).
type TasksParams struct {
	keys []string
	m    map[string]string
	src  *embedSrc
}

// embedSrc: where an embed's params are written: the index-th [[tasks: …]]
// of a page, outside code (-1: the settings of All tasks, allTasksFile),
// and the params as written there.
type embedSrc struct {
	index int
	raw   string
}

func (p TasksParams) Get(k string) string { return p.m[k] }

func (p TasksParams) Has(k string) bool { _, ok := p.m[k]; return ok }

// With is a copy with k set (last when new).
func (p TasksParams) With(k, v string) TasksParams {
	c := TasksParams{keys: append([]string(nil), p.keys...), m: map[string]string{}, src: p.src}
	for key, val := range p.m {
		c.m[key] = val
	}
	if _, ok := c.m[k]; !ok {
		c.keys = append(c.keys, k)
	}
	c.m[k] = v
	return c
}

// embedArgs: what follows "tasks:" in a tasksRE match.
func embedArgs(m []string) string { return m[1] }

// parseTasksParams reads [[tasks: key=val …]]'s pairs.
func parseTasksParams(raw string) TasksParams {
	p := TasksParams{m: map[string]string{}}
	for _, token := range strings.Fields(raw) {
		k, v, ok := strings.Cut(token, "=")
		if !ok {
			continue
		}
		k = strings.ToLower(strings.TrimSpace(k))
		if _, seen := p.m[k]; !seen {
			p.keys = append(p.keys, k)
		}
		p.m[k] = strings.TrimSpace(v)
	}
	return p
}

var (
	filterWordRE = regexp.MustCompile(`[^,|]+`)
	embedWordRE  = regexp.MustCompile(mdtext.NonSpaceClass + `+`)
	// A link to /tasks with a query, as a page or a task writes it: on its
	// own or after this wiki's address.
	tasksURLRE = regexp.MustCompile(`(^|[\s(<"'\[]|https?://[^\s/)]+)/tasks\?([^\s)"'\]<>#]*)`)
)

// renameInFilter: a tag= or new= value with the tag old (lowercase) renamed;
// a word keeps its "-" or "#", the commas and pipes stay.
func renameInFilter(raw, old, name string) string {
	return filterWordRE.ReplaceAllStringFunc(raw, func(word string) string {
		bare := strings.TrimLeft(word, "-#")
		if strings.ToLower(bare) == old {
			return word[:len(word)-len(bare)] + name
		}
		return word
	})
}

// embedUsesTag: an embed names the tag old (lowercase) in tag= or new=.
func embedUsesTag(p TasksParams, old string) bool {
	return renameInFilter(p.Get("tag"), old, "\x00") != p.Get("tag") ||
		renameInFilter(p.Get("new"), old, "\x00") != p.Get("new")
}

// renameInEmbed: an embed's params with the tag old (lowercase) renamed, the
// other params and the spacing as written.
func renameInEmbed(args, old, name string) string {
	return embedWordRE.ReplaceAllStringFunc(args, func(word string) string {
		k, v, ok := strings.Cut(word, "=")
		switch key := strings.ToLower(k); {
		case !ok:
		case key == "tag" || key == "new":
			return k + "=" + renameInFilter(v, old, name)
		}
		return word
	})
}

// renameInTasksURL: a link's query with the tag old (lowercase) renamed in
// tag=; the other params as written.
func renameInTasksURL(query, old, name string) string {
	parts := strings.Split(query, "&")
	for i, part := range parts {
		k, v, _ := strings.Cut(part, "=")
		value, err := url.QueryUnescape(v)
		if err != nil {
			continue
		}
		renamed := value
		if k == "tag" {
			renamed = renameInFilter(value, old, name)
		}
		if renamed != value {
			parts[i] = k + "=" + url.QueryEscape(renamed)
		}
	}
	return strings.Join(parts, "&")
}

// renameTagInText: page or task text with the tag old (lowercase) renamed
// in its [[tasks: …]] embeds and its links to /tasks; code stays as written,
// as it does for a page's links.
func renameTagInText(text, old, name string) string {
	masked, stash := mdtext.MaskCode(text)
	masked = tasksRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := tasksRE.FindStringSubmatchIndex(m) // at[2:4]: the args
		return m[:at[2]] + renameInEmbed(m[at[2]:at[3]], old, name) + m[at[3]:]
	})
	masked = tasksURLRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := strings.Index(m, "/tasks?") + len("/tasks?")
		return m[:at] + renameInTasksURL(m[at:], old, name)
	})
	return mdtext.UnmaskCode(masked, stash)
}

// The params the settings dialog sets on an embed (js/tasksettings.js), in
// the order new ones are written; those of All tasks, which has no filter.
// Not list=: which list an embed shows is the page's to say, and a list is
// renamed by renameListOp. A list's own settings are listSettingKeys.
var (
	embedSettingKeys = []string{"tag", "assignee", "new", "view", "group", "limit", "labels"}
	allSettingKeys   = []string{"new", "view", "group", "limit", "labels"}
	embedValueRE     = regexp.MustCompile(`^[^\[\]` + "`" + `]*$`)
)

// assigneeParamRE: assignee= as the dialog may write it: names, me and
// none, joined as tag= joins tags (a|b, a,-b).
var assigneeParamRE = regexp.MustCompile(`^-?[a-z0-9]+(-[a-z0-9]+)*([|,]-?[a-z0-9]+(-[a-z0-9]+)*)*$`)

// checkEmbedSettings: set as params the dialog may write, each value
// trimmed; "" takes a param out. keys: those allowed.
func checkEmbedSettings(set map[string]string, keys []string) (map[string]string, error) {
	out := map[string]string{}
	for k, v := range set {
		v = strings.TrimSpace(v)
		bad := func() error { return refuse("Not a value for " + k + ": " + strconv.Quote(v)) }
		if !slices.Contains(keys, k) {
			return nil, refuse("Not a setting: " + strconv.Quote(k))
		}
		if len(v) > 300 || !embedValueRE.MatchString(v) || len(strings.Fields(v)) > 1 {
			return nil, bad()
		}
		if v != "" {
			switch k {
			case "view":
				if v = strings.ToLower(v); !slices.Contains(tasksViews, v) {
					return nil, bad()
				}
			case "group":
				if v = strings.ToLower(v); !slices.Contains(tasksGroups, v) {
					return nil, bad()
				}
			case "limit":
				if n, err := strconv.Atoi(v); err != nil || n < 1 || n > 99 {
					return nil, refuse("The limit is a number from 1 to 99")
				}
			case "keep", "noshare":
				if !isTrue(v) {
					return nil, bad()
				}
				v = "1"
			case "home":
				if v = strings.ToLower(v); v != "off" {
					return nil, refuse("home is off (the list's tasks are left out of My tasks) or not set")
				}
			case "mail":
				// The kinds of mail turned on, "," between (mailKinds);
				// "off" from before they were opt-in reads as none.
				asked := strings.Split(strings.ToLower(v), ",")
				for _, k := range asked {
					if k != "" && k != "off" && !slices.Contains(mailKinds, k) {
						return nil, bad()
					}
				}
				var kinds []string
				for _, k := range mailKinds { // in one order, whatever was sent
					if slices.Contains(asked, k) {
						kinds = append(kinds, k)
					}
				}
				v = strings.Join(kinds, ",")
			case "new":
				v = strings.Join(taskmodel.CleanTags(strings.Split(v, ",")), ",")
			case "assignee":
				if v = strings.ReplaceAll(strings.ToLower(v), "@", ""); !assigneeParamRE.MatchString(v) {
					return nil, refuse("Not a value for assignee: user names, me or none, as a|b (either) or -a (not theirs)")
				}
			}
		}
		out[k] = v
	}
	return out, nil
}

// setEmbedArgs: an embed's params as written with those of set changed: a
// param keeps its place, a new one goes last (in embedSettingKeys' order),
// one set to "" goes; the others stay as written.
func setEmbedArgs(args string, set map[string]string) string {
	var out []string
	done := map[string]bool{}
	for _, word := range strings.Fields(args) {
		k, _, ok := strings.Cut(word, "=")
		key := strings.ToLower(k)
		v, changes := set[key]
		switch {
		case !ok || !changes:
			out = append(out, word)
		case !done[key] && v != "":
			out = append(out, key+"="+v)
		}
		done[key] = done[key] || ok && changes
	}
	// (mail: My tasks' own, mineSettingKeys.)
	for _, k := range append(slices.Clone(embedSettingKeys), "mail") {
		if v := set[k]; v != "" && !done[k] {
			out = append(out, k+"="+v)
		}
	}
	return strings.Join(out, " ")
}

// errEmbedChanged: the embed isn't written as the dialog saw it.
var errEmbedChanged = refuseAs(refusedConflict, "The page has changed since. Reload it and try again.")

// setEmbedParams: text with the params of its n-th [[tasks: …]] outside
// code changed (setEmbedArgs); was: the params as the caller saw them, and
// anything else there is errEmbedChanged.
func setEmbedParams(text string, n int, was string, set map[string]string) (string, error) {
	masked, stash := mdtext.MaskCode(text)
	all := tasksRE.FindAllStringSubmatchIndex(masked, -1)
	if n < 0 || n >= len(all) {
		return "", errEmbedChanged
	}
	at := all[n]
	from, to := at[2], at[3] // the args
	if masked[from:to] != was {
		return "", errEmbedChanged
	}
	args := setEmbedArgs(was, set)
	if args != "" {
		args = " " + args
	}
	head := strings.TrimRight(masked[:from], " \t")
	return mdtext.UnmaskCode(head+args+masked[to:], stash), nil
}

// What the Open tab shows, by the names group= uses: one list (none), the
// Plan board by start date (day), a section per assignee (person), a
// section per list (list).
var tasksGroups = []string{"none", "day", "person", "list"}

// embedGroup: what a view says its Open tab shows: its group=, "" when it
// says none (the board by day then). (Until 2026-11 view=open|plan and
// plan=on|off said it too: migrateViewsLocked rewrote those.)
func embedGroup(p TasksParams) string {
	if v := strings.ToLower(strings.TrimSpace(p.Get("group"))); slices.Contains(tasksGroups, v) {
		return v
	}
	return ""
}

// The tasks' views, by the names [[tasks: view=…]] and ?view= use: which
// tasks a view shows. Today: what is to do now; open: every open task (how,
// group= says); closed: the closed ones. (Until 2026-11 today was called
// focus, and plan was the Open tab as a board.)
var tasksViews = []string{"today", "open", "closed"}

// embedView: the view an embed opens on, "" for the default or an unknown name.
func embedView(p TasksParams) string {
	v := strings.ToLower(strings.TrimSpace(p.Get("view")))
	for _, name := range tasksViews {
		if v == name {
			return v
		}
	}
	return ""
}

// isTrue: a yes/no param (keep=1, home=yes).
func isTrue(v string) bool { return truthy(strings.TrimSpace(v)) }

func taskTagsLower(t *Task) map[string]bool {
	out := map[string]bool{}
	for _, tag := range t.Tags {
		out[strings.ToLower(tag)] = true
	}
	return out
}

// retag: tags with those of add it lacks appended and those of remove taken
// off, case ignored (a tag it has keeps its spelling).
func retag(tags, add, remove []string) []string {
	all := append([]string{}, tags...)
	have := map[string]bool{}
	for _, t := range tags {
		have[strings.ToLower(t)] = true
	}
	for _, t := range add {
		if !have[strings.ToLower(t)] {
			all = append(all, t)
			have[strings.ToLower(t)] = true
		}
	}
	drop := map[string]bool{}
	for _, t := range remove {
		drop[strings.ToLower(t)] = true
	}
	out := []string{}
	for _, t := range all {
		if !drop[strings.ToLower(t)] {
			out = append(out, t)
		}
	}
	return out
}

// tagParamMatches: a task passes an embed's tag= (TasksCore.tagParamToQuery):
// `a|b` any of them, else comma-separated tags all there, `-tag` excluding.
func tagParamMatches(t *Task, raw string) bool {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return true
	}
	tags := taskTagsLower(t)
	if strings.Contains(raw, "|") {
		for _, x := range strings.Split(raw, "|") {
			if strings.TrimSpace(x) != "" && tags[strings.ToLower(strings.TrimLeft(strings.TrimSpace(x), "#"))] {
				return true
			}
		}
		return false
	}
	for _, x := range strings.Split(raw, ",") {
		x = strings.ToLower(strings.TrimLeft(strings.TrimSpace(x), "#"))
		if strings.HasPrefix(x, "-") {
			if tags[x[1:]] {
				return false
			}
		} else if x != "" && !tags[x] {
			return false
		}
	}
	return true
}

// assigneeGone: the shared task's assignee no longer has it
// (TasksCore.assigneeGone): someone who can no longer be given tasks, or
// who is none of those its list is for (a list narrowed since, a group
// they left) and so doesn't see it. The name stays on the task: back among
// them, it is theirs as before; until then it counts as no one's.
func assigneeGone(t *Task) bool {
	return t.Assignee != "" && (!settings().assignable(t.Assignee) || !taskFiles.SeesList(t.Assignee, t.List))
}

// assigneeIs: t's assignee is what word names, for user: a user's name,
// "me" (user) or "none": no one, or someone who can no longer be given
// tasks (TasksCore.assigneeIs). A private task has no assignee and is its
// user's own: it is "me" to them, and not "none".
func assigneeIs(t *Task, word, user string) bool {
	switch word {
	case "none":
		return t.Owner == "" && (t.Assignee == "" || assigneeGone(t))
	case "me":
		return user != "" && (t.Assignee == user || t.Owner == user)
	}
	return t.Assignee == word
}

// assigneeParamMatches: a task passes an embed's assignee= for user, read
// as tag= is (tagParamMatches): `a|b` either's, else each word must hold,
// `-a` not theirs. Without users every task passes.
func assigneeParamMatches(t *Task, raw, user string) bool {
	raw = strings.ToLower(strings.TrimSpace(raw))
	if raw == "" || settings().Users == nil {
		return true
	}
	word := func(x string) string { return strings.TrimPrefix(strings.TrimSpace(x), "@") }
	if strings.Contains(raw, "|") {
		for _, x := range strings.Split(raw, "|") {
			if word(x) != "" && assigneeIs(t, word(x), user) {
				return true
			}
		}
		return false
	}
	for _, x := range strings.Split(raw, ",") {
		x = strings.TrimSpace(x)
		if strings.HasPrefix(x, "-") {
			if assigneeIs(t, word(x[1:]), user) {
				return false
			}
		} else if word(x) != "" && !assigneeIs(t, word(x), user) {
			return false
		}
	}
	return true
}

// newTaskTags: the tags a new task gets in an embed with these params:
// those of new=, else the single tags of tag= (not an a|b group, not -tag).
// Its list is no tag: the task is in it (Task.List).
func newTaskTags(p TasksParams) []string {
	if tags := taskmodel.CleanTags(strings.Split(p.Get("new"), ",")); len(tags) > 0 {
		return tags
	}
	var tags []string
	if raw := strings.TrimSpace(p.Get("tag")); raw != "" && !strings.Contains(raw, "|") {
		for _, t := range strings.Split(raw, ",") {
			if t = strings.TrimSpace(t); !strings.HasPrefix(t, "-") {
				tags = append(tags, t)
			}
		}
	}
	return taskmodel.CleanTags(tags)
}

var (
	plusDaysRE = regexp.MustCompile(`^\+(\d{1,3})([dw]?)$`)
	isoDateRE  = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
)

var weekdays = []string{"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

// parseDateWord: a date as quick-add reads one (TasksCore.parseDateWord),
// YYYY-MM-DD, "" when it isn't one: today · tomorrow/tmrw · mon…sun (the
// next one, never today) · +3 / +3d / +2w · 2026-10-03.
func parseDateWord(word string, today time.Time) string {
	w := strings.ToLower(strings.TrimSpace(word))
	switch w {
	case "today":
		return isoDay(today)
	case "tomorrow", "tmrw":
		return isoDay(today.AddDate(0, 0, 1))
	}
	if m := plusDaysRE.FindStringSubmatch(w); m != nil {
		n, _ := strconv.Atoi(m[1]) // at most three digits
		if m[2] == "w" {
			n *= 7
		}
		return isoDay(today.AddDate(0, 0, n))
	}
	if isoDateRE.MatchString(w) {
		if d, ok := parseDay(w); ok {
			return isoDay(d)
		}
		return ""
	}
	if len([]rune(w)) >= 3 {
		for i, name := range weekdays {
			if strings.HasPrefix(name, w) {
				wd := (int(today.Weekday()) + 6) % 7
				return isoDay(today.AddDate(0, 0, (i-wd+6)%7+1))
			}
		}
	}
	return ""
}

func isoDay(t time.Time) string { return t.Format("2006-01-02") }

// parseDay reads YYYY-MM-DD (ASCII digits, a real day).
func parseDay(s string) (time.Time, bool) {
	t, err := time.ParseInLocation("2006-01-02", s, time.Local)
	return t, err == nil
}

// today is the server's date.
func today() time.Time {
	n := now()
	return time.Date(n.Year(), n.Month(), n.Day(), 0, 0, 0, 0, time.Local)
}

// shortIDLen: [[task:…]] names a task by this many characters of its id.
const shortIDLen = 8

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// keySet: m's keys as a set.
func keySet[V any](m map[string]V) map[string]bool {
	set := make(map[string]bool, len(m))
	for k := range m {
		set[k] = true
	}
	return set
}

// taskFilter: what the task list's filter field says (TasksCore.parseFilter
// in the browser, whose words these are): the Closed view asks the server
// with it, which answers the tasks it matches, a page of them.
type taskFilter struct {
	text, importance            string
	tagGroups, assigneeGroups   [][]string
	listGroups, fromGroups      [][]string
	excludeTags, excludeLists   []string
	excludeAssignees, excludeBy []string
	updated, following          bool
}

// parseTaskFilter: the filter the words of q make: #tag (#a|b either),
// -#tag, list:name, -list:name, !high and !low, is:updated, is:following, with users
// @name and from:name (and their -), the rest text.
func parseTaskFilter(q string) taskFilter {
	users := settings().Users != nil
	var f taskFilter
	var text []string
	parts := func(w, sigil string) []string {
		var out []string
		for _, p := range strings.Split(w, "|") {
			if p = strings.ToLower(strings.TrimPrefix(p, sigil)); p != "" {
				out = append(out, p)
			}
		}
		return out
	}
	lists := func(w string) []string {
		var out []string
		for _, p := range strings.Split(w[len("list:"):], "|") {
			if p = strings.ToLower(strings.TrimPrefix(strings.ToLower(p), "shared/")); p != "" {
				out = append(out, p)
			}
		}
		return out
	}
	from := func(w string) []string {
		var out []string
		for _, p := range strings.Split(w[len("from:"):], "|") {
			if p = strings.ToLower(strings.TrimPrefix(p, "@")); p != "" {
				out = append(out, p)
			}
		}
		return out
	}
	has := func(w, prefix string) bool {
		return len(w) > len(prefix) && strings.EqualFold(w[:len(prefix)], prefix)
	}
	for _, w := range strings.Fields(q) {
		low := strings.ToLower(w)
		switch {
		case low == "is:updated":
			f.updated = true
		case low == "is:following":
			f.following = true
		case low == "!" || low == "!high":
			f.importance = "high"
		case low == "!low":
			f.importance = "low"
		case has(w, "-list:"):
			f.excludeLists = append(f.excludeLists, lists(w[1:])...)
		case has(w, "list:"):
			f.listGroups = append(f.listGroups, lists(w))
		case users && has(w, "-from:"):
			f.excludeBy = append(f.excludeBy, from(w[1:])...)
		case users && has(w, "from:"):
			f.fromGroups = append(f.fromGroups, from(w))
		case strings.HasPrefix(w, "-#"):
			f.excludeTags = append(f.excludeTags, parts(w[1:], "#")...)
		case strings.HasPrefix(w, "#"):
			if g := parts(w, "#"); len(g) > 0 {
				f.tagGroups = append(f.tagGroups, g)
			}
		case users && strings.HasPrefix(w, "-@"):
			f.excludeAssignees = append(f.excludeAssignees, parts(w[1:], "@")...)
		case users && strings.HasPrefix(w, "@"):
			if g := parts(w, "@"); len(g) > 0 {
				f.assigneeGroups = append(f.assigneeGroups, g)
			}
		default:
			text = append(text, w)
		}
	}
	f.text = strings.ToLower(strings.Join(text, " "))
	return f
}

// matches: the task passes the filter, for user (TasksCore.matchesFilter).
// t is only read.
func (f taskFilter) matches(t *Task, user string) bool {
	if f.importance != "" && t.Importance != f.importance {
		return false
	}
	if f.updated && newsFor(t, user).Kind == "" {
		return false
	}
	if f.following && !slices.Contains(t.Followers, user) {
		return false
	}
	if f.text != "" && !strings.Contains(strings.ToLower(t.Title), f.text) && !strings.Contains(strings.ToLower(t.Description), f.text) {
		return false
	}
	tags := taskTagsLower(t)
	for _, tag := range f.excludeTags {
		if tags[tag] {
			return false
		}
	}
	any := func(group []string, is func(string) bool) bool { return slices.ContainsFunc(group, is) }
	assignee := func(w string) bool { return assigneeIs(t, w, user) }
	if any(f.excludeAssignees, assignee) {
		return false
	}
	for _, g := range f.assigneeGroups {
		if !any(g, assignee) {
			return false
		}
	}
	// Who made it: a private task is its user's own making.
	maker := t.Maker()
	if maker == "" && t.Owner != "" {
		maker = user
	}
	by := func(w string) bool {
		if w == "me" {
			w = user
		}
		return maker != "" && w == maker
	}
	if any(f.excludeBy, by) {
		return false
	}
	for _, g := range f.fromGroups {
		if !any(g, by) {
			return false
		}
	}
	inList := func(n string) bool {
		if n == "inbox" || n == "none" {
			return t.List == ""
		}
		return strings.ToLower(t.List) == n
	}
	if any(f.excludeLists, inList) {
		return false
	}
	for _, g := range f.listGroups {
		if !any(g, inList) {
			return false
		}
	}
	for _, g := range f.tagGroups {
		if !any(g, func(tag string) bool { return tags[tag] }) {
			return false
		}
	}
	return true
}
