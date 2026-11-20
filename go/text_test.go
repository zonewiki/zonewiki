package main

import (
	"reflect"
	"strings"
	"testing"
	"zonewiki/internal/mdtext"
)

// Page text helpers (mdtext.go, pages.go): slugs, titles, frontmatter,
// checkboxes. golden_test.go also checks them against Python's answers.

func TestRenderFrontmatterHTML(t *testing.T) {
	if got := renderFrontmatterHTML(mdtext.Frontmatter{}, true, true); got != "" {
		t.Errorf("empty: %q", got)
	}
	both := mdtext.Frontmatter{Tldr: "Summary here", Tags: []string{"work", "urgent"}}
	html := renderFrontmatterHTML(both, true, true)
	if !strings.HasPrefix(html, `<details class="page-frontmatter">`) || strings.Contains(strings.Split(html, "<summary>")[0], " open") {
		t.Errorf("not collapsed: %q", html)
	}
	expectIn(t, html, `<p class="page-tldr">Summary here</p>`, `class="task-tag page-tag"`,
		`href="/search?q=%23work"`, `href="/search?q=%23urgent"`)
	html = renderFrontmatterHTML(both, false, true)
	expectIn(t, html, `<p class="page-tldr">Summary here</p>`)
	expectNot(t, html, "task-tag")
	html = renderFrontmatterHTML(both, true, false)
	expectIn(t, html, `class="task-tag page-tag"`)
	expectNot(t, html, "page-tldr")
	if renderFrontmatterHTML(mdtext.Frontmatter{Tags: []string{"work"}}, false, true) != "" ||
		renderFrontmatterHTML(mdtext.Frontmatter{Tldr: "Summary"}, true, false) != "" {
		t.Error("nothing left to show should render nothing")
	}
	html = renderFrontmatterHTML(mdtext.Frontmatter{Tldr: "<script>alert(1)</script>"}, true, true)
	expectIn(t, html, "&lt;script&gt;")
	expectNot(t, html, "<script>")
}
func TestRenderTagsPills(t *testing.T) {
	if renderTagsPillsHTML(nil) != "" {
		t.Error("no tags should render nothing")
	}
	expectIn(t, renderTagsPillsHTML([]string{"work", "urgent"}), `class="task-tag page-tag"`,
		`href="/search?q=%23work"`, `href="/search?q=%23urgent"`)
	// A tag with a space stays a word search.
	expectIn(t, renderTagsPillsHTML([]string{"my tag"}), `href="/search?q=my+tag"`)
}

// [[Page#Section]] links to the page: a backlink, renamed with it.
func TestSectionLinks(t *testing.T) {
	if got := pageLinkTargets("[[Garden#Beds]] [[#Here]]", "x"); !reflect.DeepEqual(got, map[string]bool{"garden": true}) {
		t.Errorf("targets %v", got)
	}
	if got := rewritePageLinks("[[garden#Beds|b]] [[Garden#Paths]] [[#garden]]", "garden", "yard", "", ""); got != "[[yard#Beds|b]] [[yard#Paths]] [[#garden]]" {
		t.Errorf("rename %q", got)
	}
	if got := mdtext.PlainTaskTitle("Weed [[Garden#Beds]] and [[Yard|the yard]]"); got != "Weed Garden › Beds and the yard" {
		t.Errorf("title %q", got)
	}
}

// Text like the masking tokens survives masking and renames as written.
func TestMaskLookalikes(t *testing.T) {
	text := "`a` 0 ``b`1`` 9\n```\n0\n```\n"
	if got := mdtext.UnmaskCode(mdtext.MaskCode(text)); got != text {
		t.Errorf("round trip %q", got)
	}
	if got := rewritePageLinks("`code` 0 [[old]]\n", "old", "new", "", ""); got != "`code` 0 [[new]]\n" {
		t.Errorf("rename %q", got)
	}
}
func TestToggleCheckboxCases(t *testing.T) {
	toggle := func(content string, idx int) string { return toggleNthCheckbox(content, idx, true, now()) }
	// Ticking stamps the day, unticking takes it off; the created date stays.
	expectIn(t, toggle("- [ ] task\n", 0), "<!--d:")
	if got := toggle("- [x] task <!--d:2026-01-10T09:00:00-->\n", 0); strings.Contains(got, "<!--d:") || !strings.Contains(got, "[ ]") {
		t.Errorf("untick %q", got)
	}
	expectIn(t, toggle("- [ ] task <!--t:2026-01-01T08:00:00-->\n", 0), "<!--t:2026-01-01T08:00:00 d:")
	if got := toggle("- [x] task <!--t:2026-01-01T08:00:00 d:2026-01-10T09:00:00-->\n", 0); !strings.Contains(got, "<!--t:2026-01-01T08:00:00-->") || strings.Contains(got, " d:") {
		t.Errorf("untick keeps created %q", got)
	}
	expectIn(t, toggle("- [X] task\n", 0), "[ ]")
	if toggle("- [ ] only one\n", 5) != "- [ ] only one\n" {
		t.Error("out of range should change nothing")
	}

	// Only task items count: not code, prose, a second bracket, frontmatter.
	cases := []struct {
		content string
		idx     int
		want    []string // in the result
	}{
		{"- [ ] task1\n- [ ] task2\n- [x] task3\n", 0, []string{"- [x] task1"}},
		{"- [ ] task1\n- [ ] task2\n- [x] task3\n", 2, []string{"- [ ] task3"}},
		{"- [ ] real task 1\n```\n[x] this is code, not a checkbox\n```\n- [ ] real task 2\n", 1,
			[]string{"- [x] real task 2", "[x] this is code"}},
		{"- [ ] real task\n~~~python\nstatus = '[x] done'\n~~~\n- [ ] another task\n", 1,
			[]string{"- [x] another task", "'[x] done'"}},
		{"- [ ] first\n```\n[x] fake1\n[ ] fake2\n```\n- [x] second\n```\n[ ] fake3\n```\n- [ ] third\n", 1, []string{"- [ ] second"}},
		{"- [ ] first\n```\n[x] fake1\n[ ] fake2\n```\n- [x] second\n```\n[ ] fake3\n```\n- [ ] third\n", 2, []string{"- [x] third"}},
		{"intro\n\n- [ ] task\n\n```\n[x] code\n```\n\noutro\n", 0, []string{"intro\n", "outro\n", "```\n[x] code\n```\n"}},
		{"Press [x] to close.\n\n- [ ] task\n", 0, []string{"Press [x] to close.\n", "- [x] task"}},
		{"- [ ] a [x] b\n- [ ] c\n", 1, []string{"- [ ] a [x] b\n", "- [x] c"}},
		{"- see [ ] here\n- [ ] real\n", 0, []string{"- [x] real"}},
		{"---\ntags: [a, b]\ntldr: [x] marks\n---\n# T\n\n- [ ] one\n", 0, []string{"- [x] one"}},
	}
	for _, c := range cases {
		expectIn(t, toggle(c.content, c.idx), c.want...)
	}
	for _, content := range []string{"- [ ] task A\n```\n- [ ] fake checkbox\n```\n",
		"- [ ] first\n```\n[x] fake1\n[ ] fake2\n```\n- [x] second\n```\n[ ] fake3\n```\n- [ ] third\n"} {
		if n := strings.Count(content, "- ["); toggle(content, n) != content {
			t.Errorf("index past the last task item changed %q", content)
		}
	}
	for _, line := range []string{"* [ ] star", "+ [ ] plus", "1. [ ] num", "2) [ ] paren", "  - [ ] nested", "> - [ ] quoted"} {
		expectIn(t, toggle(line+"\n", 0), "[x]")
	}
}

// The Nth rendered checkbox is the Nth line the toggle counts.
func TestToggleMatchesRenderedCheckboxes(t *testing.T) {
	testWiki(t, nil)
	content := "Intro with [x] and [ ] in prose.\n\n- [ ] first\n- plain [ ] item\n  - [x] second (nested)\n" +
		"\n```\n- [ ] in code\n```\n\n1. [ ] third\n> - [ ] fourth\n- [ ] fifth [x] trailing\n"
	html := renderMd(&Req{}, content, RenderOpts{})
	if n := strings.Count(html, "task-list-item-checkbox"); n != 5 {
		t.Fatalf("%d checkboxes rendered, want 5", n)
	}
	before := strings.Split(content, "\n")
	for i, label := range []string{"first", "second", "third", "fourth", "fifth"} {
		after := strings.Split(toggle1(content, i), "\n")
		var changed []string
		for j := range before {
			if before[j] != after[j] {
				changed = append(changed, before[j])
			}
		}
		if len(changed) != 1 || !strings.Contains(changed[0], label) {
			t.Errorf("checkbox %d changed %q", i, changed)
		}
	}
}
func toggle1(content string, idx int) string {
	return toggleNthCheckbox(content, idx, true, now())
}
