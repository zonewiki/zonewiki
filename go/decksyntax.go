// The deck's syntax in page text: [[deck …]] embeds and
// their params, [[task:<id>]] references, and how tags are written.
package main

import (
	"fmt"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
)

var spaceRunRE = regexp.MustCompile(pySpace + `+`)

// pyStr is Python's str() of a JSON value.
func pyStr(v any) string {
	switch v := v.(type) {
	case nil:
		return "None"
	case string:
		return v
	case bool:
		if v {
			return "True"
		}
		return "False"
	case int64:
		return strconv.FormatInt(v, 10)
	case int:
		return strconv.Itoa(v)
	case float64:
		return pyFloatRepr(v)
	}
	return fmt.Sprint(v)
}

// cleanTags: tags as the filter can match them: one word each (whitespace
// inside becomes "-"), no leading "#", no empties, no case-insensitive
// duplicates (the first spelling wins).
func cleanTags(tags []any) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, t := range tags {
		tag := strings.Trim(spaceRunRE.ReplaceAllString(pyStrip(strings.TrimLeft(pyStrip(pyStr(t)), "#")), "-"), "-")
		if tag != "" && !seen[strings.ToLower(tag)] {
			seen[strings.ToLower(tag)] = true
			out = append(out, tag)
		}
	}
	return out
}

func cleanTagStrings(tags []string) []string {
	l := make([]any, len(tags))
	for i, t := range tags {
		l[i] = t
	}
	return cleanTags(l)
}

// cleanList: a list name as stored and matched: trimmed, lowercase, one
// word, no leading "#"; "" for no list.
func cleanList(name string) string {
	return strings.Trim(spaceRunRE.ReplaceAllString(strings.ToLower(pyStrip(strings.TrimLeft(pyStrip(name), "#"))), "-"), "-")
}

var (
	deckRE    = regexp.MustCompile(`(?i)\[\[deck(?:` + pySpace + `+([^\]]*))?\]\]`)
	taskRefRE = regexp.MustCompile(`\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]`)
	mdLinkRE  = regexp.MustCompile(`\[([^\]]*)\]\(/page/([^)]+)\)`)
)

// DeckParams are an embed's key=value pairs, in order.
type DeckParams struct {
	keys []string
	m    map[string]string
}

func (p DeckParams) Get(k string) string { return p.m[k] }

func (p DeckParams) Has(k string) bool { _, ok := p.m[k]; return ok }

// With is a copy with k set (last when new).
func (p DeckParams) With(k, v string) DeckParams {
	c := DeckParams{keys: append([]string(nil), p.keys...), m: map[string]string{}}
	for key, val := range p.m {
		c.m[key] = val
	}
	if _, ok := c.m[k]; !ok {
		c.keys = append(c.keys, k)
	}
	c.m[k] = v
	return c
}

// parseDeckParams reads [[deck key=val …]]'s pairs.
func parseDeckParams(raw string) DeckParams {
	p := DeckParams{m: map[string]string{}}
	for _, token := range pySplit(raw) {
		k, v, ok := strings.Cut(token, "=")
		if !ok {
			continue
		}
		k = strings.ToLower(pyStrip(k))
		if _, seen := p.m[k]; !seen {
			p.keys = append(p.keys, k)
		}
		p.m[k] = pyStrip(v)
	}
	return p
}

// The deck's views, by the names [[deck view=…]] and ?view= use.
var deckViews = []string{"focus", "open", "plan", "completed"}

// embedView: the view an embed opens on, "" for the default or an unknown name.
func embedView(p DeckParams) string {
	v := strings.ToLower(pyStrip(p.Get("view")))
	for _, name := range deckViews {
		if v == name {
			return v
		}
	}
	return ""
}

// isTrue: a yes/no param (keep=1, home=yes).
func isTrue(v string) bool {
	switch strings.ToLower(pyStrip(v)) {
	case "1", "true", "yes":
		return true
	}
	return false
}

func taskTagsLower(t *Obj) map[string]bool {
	out := map[string]bool{}
	for _, tag := range t.List("tags") {
		out[strings.ToLower(pyStr(tag))] = true
	}
	return out
}

// tagParamMatches: a task passes an embed's tag= (DeckCore.tagParamToQuery):
// `a|b` any of them, else comma-separated tags all there, `-tag` excluding.
func tagParamMatches(t *Obj, raw string) bool {
	raw = pyStrip(raw)
	if raw == "" {
		return true
	}
	tags := taskTagsLower(t)
	if strings.Contains(raw, "|") {
		for _, x := range strings.Split(raw, "|") {
			if pyStrip(x) != "" && tags[strings.ToLower(strings.TrimLeft(pyStrip(x), "#"))] {
				return true
			}
		}
		return false
	}
	for _, x := range strings.Split(raw, ",") {
		x = strings.ToLower(strings.TrimLeft(pyStrip(x), "#"))
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
func newTaskTags(p DeckParams) []string {
	tags := []any{cleanList(p.Get("list"))}
	if raw := pyStrip(p.Get("tag")); raw != "" && !strings.Contains(raw, "|") {
		for _, t := range strings.Split(raw, ",") {
			if t = pyStrip(t); !strings.HasPrefix(t, "-") {
				tags = append(tags, t)
			}
		}
	}
	return cleanTags(tags)
}

var (
	plusDaysRE = regexp.MustCompile(`^\+(` + pyDigit + `{1,3})([dw]?)$`)
	isoDateRE  = regexp.MustCompile(`^` + pyDigit + `{4}-` + pyDigit + `{2}-` + pyDigit + `{2}$`)
)

var weekdays = []string{"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

// parseDateWord: a date as quick-add reads one (DeckCore.parseDateWord),
// YYYY-MM-DD, "" when it isn't one: today · tomorrow/tmrw · mon…sun (the
// next one, never today) · +3 / +3d / +2w · 2026-10-03.
func parseDateWord(word string, today time.Time) string {
	w := strings.ToLower(pyStrip(word))
	switch w {
	case "today":
		return isoDay(today)
	case "tomorrow", "tmrw":
		return isoDay(today.AddDate(0, 0, 1))
	}
	if m := plusDaysRE.FindStringSubmatch(w); m != nil {
		n := pyAtoi(m[1])
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

// pyAtoi is int() of Unicode digits.
func pyAtoi(s string) int {
	n := 0
	for _, r := range s {
		zero := r
		for unicode.IsDigit(zero - 1) {
			zero--
		}
		n = n*10 + int(r-zero)%10
	}
	return n
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

// inScope: a task belongs to a deck view: scope is a list name (its tasks)
// or "" for every task.
func inScope(t *Obj, scope string) bool {
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

// isWhole: a float64 that is an integer (Python's int() of it is exact).
func isWhole(f float64) bool { return f == math.Trunc(f) && !math.IsInf(f, 0) }
