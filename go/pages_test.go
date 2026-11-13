package main

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/mdtext"
)

// The wiki's pages (pages.go): listing, safe paths, link targets and
// backlinks, renames' references; and the tasks syntax's tag rules.

func TestIterPagesSkipsUnderscoreAndHiddenFolders(t *testing.T) {
	pages := map[string]string{"index": "# Home\n", "top": "# Top\n", "a/b/deep": "# Deep\n"}
	for _, skipped := range []string{"_assets/x", ".leaf/y", ".zone/y", ".git/objects/z", "a/_drafts/w", "a/.hidden/v"} {
		pages[skipped] = "# no\n"
	}
	testWiki(t, pages)
	var slugs []string
	for _, p := range iterPages() {
		slugs = append(slugs, p.Slug)
		if filepath.Ext(p.Path) != ".md" {
			t.Errorf("not a page: %s", p.Path)
		}
	}
	sort.Strings(slugs)
	if !reflect.DeepEqual(slugs, []string{"a/b/deep", "index", "top"}) {
		t.Errorf("pages %q", slugs)
	}
	// What iterPages skips no route reads or writes; a page named _x is fine.
	for _, slug := range []string{".leaf/index/n", ".zone/index/n", ".git/n", "_tasks/n", "a/_drafts/n", "a/.hidden/n", "a/../.leaf/n"} {
		if safePath(slug) != "" {
			t.Errorf("safePath(%q) allowed", slug)
		}
	}
	for _, slug := range []string{"_notes", "a/_notes"} {
		if safePath(slug) == "" {
			t.Errorf("safePath(%q) refused", slug)
		}
	}
}

func TestUpdateReferences(t *testing.T) {
	testWiki(t, map[string]string{"old-page": "# Old\n\ncontent\n", "referencer": "# Ref\n\n[[old-page]]\n", "no-link": "# No Link\n\nbody\n"})
	got, err := updateReferences("old-page", "new-page", "")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []string{filepath.Join(cfg.Data, "referencer.md")}) {
		t.Errorf("modified %q", got)
	}
	if b, _ := os.ReadFile(filepath.Join(cfg.Data, "referencer.md")); string(b) != "# Ref\n\n[[new-page]]\n" {
		t.Errorf("referencer %q", b)
	}
	if got, err := updateReferences("no-link", "elsewhere", ""); got != nil || err != nil {
		t.Errorf("nothing links to no-link: %q, %v", got, err)
	}
}

// A file whose links can't be re-pointed (here an unreadable task list)
// doesn't stop the others, and the caller hears of it.
func TestUpdateReferencesReportsWhatFailed(t *testing.T) {
	testWiki(t, map[string]string{"old-page": "# Old\n", "referencer": "# Ref\n\n[[old-page]]\n"})
	tasks := filepath.Join(cfg.Data, tasksDirName, "tasks.json")
	if err := os.MkdirAll(filepath.Dir(tasks), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(tasks, []byte("{not json"), 0o666); err != nil {
		t.Fatal(err)
	}
	got, err := updateReferences("old-page", "new-page", "")
	if len(got) != 1 {
		t.Errorf("the page's link: %q", got)
	}
	var left *linksLeft
	if !errors.As(err, &left) || !strings.Contains(err.Error(), "not every link") {
		t.Errorf("error: %v", err)
	}
	if _, err := rewriteMovedLinks(map[string]string{"old-page": "new-page"}); err == nil {
		t.Error("rewriteMovedLinks: no error")
	}
}

// A folder move that fails part way moves back what it moved.
func TestMoveFolderFailingMovesBack(t *testing.T) {
	testWiki(t, map[string]string{"a/one": "# One\n", "a/two": "# Two\n"})
	// b/two.md is a folder with something in it: the second rename fails.
	if err := os.MkdirAll(filepath.Join(cfg.Data, "b", "two.md", "x"), 0o777); err != nil {
		t.Fatal(err)
	}
	if _, _, err := moveFolder("", "a", "b", "Move a/ to b/"); err == nil {
		t.Fatal("no error")
	}
	for _, slug := range []string{"a/one", "a/two"} {
		if !exists(filepath.Join(cfg.Data, filepath.FromSlash(slug)+".md")) {
			t.Errorf("%s isn't back", slug)
		}
	}
	if exists(filepath.Join(cfg.Data, "b", "one.md")) {
		t.Error("b/one.md stayed")
	}
}

func TestPlainTaskTitle(t *testing.T) {
	for in, want := range map[string]string{
		"a < b & c":                   "a < b & c",
		"[[My Page]]":                 "My Page",
		"[[my-page|Label]]":           "Label",
		"see [x](/page/foo) now":      "see x now",
		"read [x](https://e.x) today": "read x today", // a pasted URL with its name: the label
		"[](https://e.x)":             "https://e.x",
	} {
		if got := mdtext.PlainTaskTitle(in); got != want {
			t.Errorf("mdtext.PlainTaskTitle(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestNewTaskTags(t *testing.T) {
	for raw, want := range map[string][]string{
		"list=Shop tag=weekly,food": {"shop", "weekly", "food"},
		"tag=work,-mary":            {"work"},
		"list=task tag=a|b":         {"task"},
		"":                          nil,
		"list=task tag=Task":        {"task"},
	} {
		if got := newTaskTags(parseTasksParams(raw)); len(got)+len(want) > 0 && !reflect.DeepEqual(got, want) {
			t.Errorf("newTaskTags(%q) = %q, want %q", raw, got, want)
		}
	}
}

// An embed's tag= on the server (the due count), as tasks-core.js reads it.
func TestTagParamMatches(t *testing.T) {
	for _, c := range []struct {
		raw  string
		tags []string
		want bool
	}{
		{"", []string{"a"}, true}, {"a", []string{"A", "b"}, true}, {"a,b", []string{"a"}, false}, {"a,b", []string{"a", "b"}, true},
		{"a,-b", []string{"a", "b"}, false}, {"a,-b", []string{"a"}, true}, {"a|b", []string{"b"}, true}, {"a|b", []string{"c"}, false},
	} {
		if got := tagParamMatches(&Task{Tags: c.tags}, c.raw); got != c.want {
			t.Errorf("tag=%q on %v: %v", c.raw, c.tags, got)
		}
	}
}

// Markdown links count as links (backlinks, renames): [x](slug),
// [x](slug.md), [x](/page/slug), with an anchor or a title; not URLs.
func TestMarkdownLinksAreBacklinks(t *testing.T) {
	testWiki(t, map[string]string{
		"ivo-petrenko": "# Ivo Petrenko\n", "signals": "# Signals\n", "people/ivo": "# Ivo\n", "golf": "# Golf\n",
		"state": "# State\n\n## Team\n\n- Kim\n  - [Ivo](ivo-petrenko)\n    - [ ] task\n[[Signals]]\n" +
			"[Ivo](/page/people/ivo#role \"Ivo\") and [x](/page/)\n[Ghost](nonexistent-page)\n[l](https://example.com/golf)\n",
		"a": "# A\n\n[Golf](golf.md#werkstatt)\n",
		"b": "# B\n\n[Golf](golf \"the car\")\n",
	})
	targets := pageLinkTargets(pageText(t, "state"), "state")
	for _, want := range []string{"ivo-petrenko", "signals", "people/ivo"} {
		if !targets[want] {
			t.Errorf("%s not a target of state: %v", want, targets)
		}
	}
	if targets["golf"] {
		t.Error("an external URL is a target of state")
	}
	backlinks := func(slug string) []string {
		var out []string
		for _, b := range findBacklinks(slug) {
			out = append(out, b.Slug)
		}
		return out
	}
	if got := backlinks("ivo-petrenko"); !reflect.DeepEqual(got, []string{"state"}) {
		t.Errorf("backlinks of ivo-petrenko %q", got)
	}
	if got := backlinks("people/ivo"); !reflect.DeepEqual(got, []string{"state"}) || allBacklinkCounts(nil)["people/ivo"] != 1 {
		t.Errorf("backlinks of people/ivo %q", got)
	}
	if got := backlinks("golf"); !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Errorf("backlinks of golf %q", got)
	}
}

func pageText(t *testing.T, slug string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(cfg.Data, slug+".md"))
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestParseWikilinkRoundTrips(t *testing.T) {
	for _, link := range []string{"[[Page]]", "[[Page|Label]]", "[[Page#Sec]]", "[[ a/b #Sec|Label ]]", "[[#Sec]]", "[[Page#]]", "[[Page|]]"} {
		if got := parseWikilink(link).String(); got != link {
			t.Errorf("%s came back as %s", link, got)
		}
	}
	l := parseWikilink("[[Old Page#Intro|see]]")
	if l.page != "Old Page" || l.section != "Intro" || l.label != "see" || !l.hasSection || !l.hasLabel {
		t.Errorf("parts %+v", l)
	}
}

func TestRelInside(t *testing.T) {
	for p, want := range map[string]string{"/w": ".", "/w/a/b.md": "a/b.md", "/w/..x/c": "..x/c"} {
		if rel, ok := relInside("/w", p); !ok || rel != want {
			t.Errorf("relInside(/w, %s) = %q, %v", p, rel, ok)
		}
	}
	for _, p := range []string{"/", "/x", "/w/../x", "/wiki"} {
		if _, ok := relInside("/w", p); ok {
			t.Errorf("relInside(/w, %s) inside", p)
		}
	}
}

func TestRetag(t *testing.T) {
	got := retag([]string{"Work", "home"}, []string{"work", "New", "new"}, []string{"HOME"})
	if !reflect.DeepEqual(got, []string{"Work", "New"}) {
		t.Errorf("retag: %q", got)
	}
	if got := retag(nil, nil, nil); got == nil || len(got) != 0 {
		t.Errorf("retag of nothing: %#v", got)
	}
}

func TestRenameTagInText(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"[[tasks: list=shop keep=1]]", "[[tasks: list=food keep=1]]"},
		{"[[tasks:  LIST=Shop   view=open]]", "[[tasks:  LIST=food   view=open]]"},
		{"[[tasks: tag=work,-shop]]", "[[tasks: tag=work,-food]]"},
		{"[[tasks: tag=#shop|home new=shop,x]]", "[[tasks: tag=#food|home new=food,x]]"},
		{"[[tasks: list=shopping tag=workshop labels=shop,b]]", "[[tasks: list=shopping tag=workshop labels=shop,b]]"},
		{"[go](/tasks?list=shop)", "[go](/tasks?list=food)"},
		{"[go](/tasks?view=open&tag=shop#x)", "[go](/tasks?view=open&tag=food#x)"},
		{"http://wiki.lan/tasks?list=shop and /tasks?tag=a%7Cshop", "http://wiki.lan/tasks?list=food and /tasks?tag=a%7Cfood"},
		{"[go](/tasks?list=other&view=shop)", "[go](/tasks?list=other&view=shop)"},
		{"/mytasks?list=shop", "/mytasks?list=shop"},
		{"#shop and shop stay, [[shop]] too", "#shop and shop stay, [[shop]] too"},
		{"`[[tasks: list=shop]]` and `/tasks?list=shop`", "`[[tasks: list=shop]]` and `/tasks?list=shop`"},
		{"```\n[[tasks: list=shop]]\n```\n", "```\n[[tasks: list=shop]]\n```\n"},
	} {
		if got := renameTagInText(c.in, "shop", "food"); got != c.want {
			t.Errorf("%q: got %q, want %q", c.in, got, c.want)
		}
	}
	if got := renameTagInText("[[tasks: list=shop tag=shop]]", "shop", "Food"); got != "[[tasks: list=food tag=Food]]" {
		t.Errorf("a list's name is lowercase, a tag keeps its spelling: %q", got)
	}
}

func TestRenamedTags(t *testing.T) {
	for _, c := range []struct {
		in, want []string
		changed  bool
	}{
		{[]string{"a", "Shop", "b"}, []string{"a", "food", "b"}, true},
		{[]string{"food", "shop"}, []string{"food"}, true},
		{[]string{"Food", "shop"}, []string{"Food"}, true},
		{[]string{"a"}, []string{"a"}, false},
		{[]string{}, []string{}, false},
	} {
		got, changed := renamedTags(c.in, "shop", "food")
		if !slices.Equal(got, c.want) || changed != c.changed {
			t.Errorf("%q: got %q %v", c.in, got, changed)
		}
	}
}

// A page that is gone doesn't stay in the per-page cache: the next look at
// the wiki's pages drops it (it used to stay for as long as Zone ran).
func TestPageMetaForgetsPagesThatAreGone(t *testing.T) {
	testWiki(t, map[string]string{"a": "# A\n\n[[b]]\n", "b": "# B\n", "c": "# C\n\n[[b]]\n"})
	at := clock(t, time.Date(2026, 5, 1, 12, 0, 0, 0, time.Local))
	cached := func(slug string) bool {
		metaMu.Lock()
		defer metaMu.Unlock()
		_, ok := metaCache[slug]
		return ok
	}
	if got := findBacklinks("b"); len(got) != 2 || !cached("a") || !cached("c") {
		t.Fatalf("backlinks %v, cached a %v c %v", got, cached("a"), cached("c"))
	}
	if err := os.Remove(filepath.Join(cfg.Data, "c.md")); err != nil {
		t.Fatal(err)
	}
	*at = at.Add(2 * time.Minute) // a look at every page: at most once a minute
	if got := findBacklinks("b"); len(got) != 1 || got[0].Slug != "a" {
		t.Errorf("backlinks after the delete: %v", got)
	}
	if cached("c") || !cached("a") {
		t.Errorf("cached: c %v (gone), a %v (there)", cached("c"), cached("a"))
	}
	// Asked for by name: gone at once.
	_ = os.Remove(filepath.Join(cfg.Data, "a.md"))
	if title, _ := pageMeta(filepath.Join(cfg.Data, "a.md"), "a"); title != "A" || cached("a") {
		t.Errorf("a: title %q, cached %v", title, cached("a"))
	}
}
