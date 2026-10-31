// The tasks' syntax in page text: [[tasks: …]] embeds and
// their params, [[task:<id>]] references, and how tags are written.
package main

import (
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var spaceRunRE = regexp.MustCompile(spaceClass + `+`)

// cleanTags: tags as the filter can match them: one word each (whitespace
// inside becomes "-"), no leading "#", no empties, no case-insensitive
// duplicates (the first spelling wins).
func cleanTags(tags []string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, t := range tags {
		tag := strings.Trim(spaceRunRE.ReplaceAllString(strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(t), "#")), "-"), "-")
		if tag != "" && !seen[strings.ToLower(tag)] {
			seen[strings.ToLower(tag)] = true
			out = append(out, tag)
		}
	}
	return out
}

// cleanList: a list name as stored and matched: trimmed, lowercase, one
// word, no leading "#"; "" for no list.
func cleanList(name string) string {
	return strings.Trim(spaceRunRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(name), "#"))), "-"), "-")
}

var (
	// [[tasks: …]], with a colon so a link [[Tasks]] stays a link; [[tasks: …]]
	// is how a page wrote it before Deck was called Tasks.
	// is how a page wrote it before Deck was called Tasks. embedArgs reads
	// the params from either.
	tasksRE   = regexp.MustCompile(`(?i)\[\[(?:tasks:` + spaceClass + `*([^\]]*)|deck(?:` + spaceClass + `+([^\]]*))?)\]\]`)
	taskRefRE = regexp.MustCompile(`\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]`)
	mdLinkRE  = regexp.MustCompile(`\[([^\]]*)\]\(/page/([^)]+)\)`)
)

// TasksParams are an embed's key=value pairs, in order.
type TasksParams struct {
	keys []string
	m    map[string]string
}

func (p TasksParams) Get(k string) string { return p.m[k] }

func (p TasksParams) Has(k string) bool { _, ok := p.m[k]; return ok }

// With is a copy with k set (last when new).
func (p TasksParams) With(k, v string) TasksParams {
	c := TasksParams{keys: append([]string(nil), p.keys...), m: map[string]string{}}
	for key, val := range p.m {
		c.m[key] = val
	}
	if _, ok := c.m[k]; !ok {
		c.keys = append(c.keys, k)
	}
	c.m[k] = v
	return c
}

// embedArgs: what follows "tasks:" or "deck" in a tasksRE match.
func embedArgs(m []string) string { return m[1] + m[2] }

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

// The tasks' views, by the names [[tasks: view=…]] and ?view= use.
var tasksViews = []string{"focus", "open", "plan", "completed"}

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

// newTaskTags: the tags a new task gets in an embed with these params: the
// list= tag, then the single tags of tag= (not an a|b group, not -tag).
func newTaskTags(p TasksParams) []string {
	tags := []string{cleanList(p.Get("list"))}
	if raw := strings.TrimSpace(p.Get("tag")); raw != "" && !strings.Contains(raw, "|") {
		for _, t := range strings.Split(raw, ",") {
			if t = strings.TrimSpace(t); !strings.HasPrefix(t, "-") {
				tags = append(tags, t)
			}
		}
	}
	return cleanTags(tags)
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

// inScope: a task belongs to a task list view: scope is a list name (its tasks)
// or "" for every task.
func inScope(t *Task, scope string) bool {
	return scope == "" || taskTagsLower(t)[scope]
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

// isWhole: a float64 that is an integer (Python's int() of it is exact).
func isWhole(f float64) bool { return f == math.Trunc(f) && !math.IsInf(f, 0) }
