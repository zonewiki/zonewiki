package main

import (
	"reflect"
	"strings"
	"testing"
)

// Page text helpers (mdtext.go, pages.go): slugs, titles, frontmatter,
// checkboxes. golden_test.go also checks them against Python's answers.

func TestToSlugCases(t *testing.T) {
	for in, want := range map[string]string{
		"hello world": "hello-world", "My Page": "my-page", "foo & bar": "foo-bar", "a  --  b": "a-b",
		"  my-page  ": "my-page", "journal/my entry": "journal/my-entry", "": "", "café": "caf",
	} {
		if got := toSlug(in); got != want {
			t.Errorf("toSlug(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestPageTitle(t *testing.T) {
	cases := []struct{ content, slug, want string }{
		{"# My Title\n\nBody.", "slug", "My Title"},
		{"\n# Title Here\n\nbody", "slug", "Title Here"},
		{"No heading here.", "my-page", "My Page"},
		{"# Title  \n\nbody", "slug", "Title"},
		{"## Section\n\nbody", "my-slug", "My Slug"},
		{"", "some-page", "Some Page"},
		// Code, frontmatter and lookalikes aren't headings.
		{"Intro\n\n```bash\n# install deps\n```\n", "my-page", "My Page"},
		{"```\n# nope\n```\n\n# Real Title\n", "x", "Real Title"},
		{"---\ntags: a\n---\n# Title\n", "x", "Title"},
		{"#hashtag\n", "tagged-page", "Tagged Page"},
		{"#\nnext line\n", "empty-h", "Empty H"},
	}
	for _, c := range cases {
		if got := pageTitle(c.content, c.slug); got != c.want {
			t.Errorf("pageTitle(%q, %q) = %q, want %q", c.content, c.slug, got, c.want)
		}
	}
}

func TestStripH1(t *testing.T) {
	for in, want := range map[string]string{
		"# Title\n\nBody text.\n":           "\nBody text.\n",
		"## Section\n\nBody.\n":             "## Section\n\nBody.\n",
		"# First\n\n# Second\n\nBody.\n":    "\n# Second\n\nBody.\n",
		"":                                  "",
		"Just text.\n":                      "Just text.\n",
		"---\ntags: a\n---\n\nBody only.\n": "---\ntags: a\n---\n\nBody only.\n",
		"---\ntags: a, b\n---\n\n# Title\n\nBody.\n": "---\ntags: a, b\n---\n\n\nBody.\n",
	} {
		if got := stripH1(in); got != want {
			t.Errorf("stripH1(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestParseFrontmatterCases(t *testing.T) {
	cases := []struct {
		content string
		want    Frontmatter
		body    string // "" for: don't check
	}{
		{"# Title\n\nBody.\n", Frontmatter{}, "# Title\n\nBody.\n"},
		{"---\ntags: work, urgent\ntldr: One line summary.\n---\n\n# Title\n\nBody.\n",
			Frontmatter{Tags: []string{"work", "urgent"}, Tldr: "One line summary."}, "# Title\n\nBody.\n"},
		{"---\ntags: [work, urgent]\n---\nBody.\n", Frontmatter{Tags: []string{"work", "urgent"}}, "Body.\n"},
		{"---\ntldr: \"Quoted summary\"\n---\nBody.\n", Frontmatter{Tldr: "Quoted summary"}, ""},
		{"---\ntags: a\n\n# Title\n\nBody.\n", Frontmatter{}, "---\ntags: a\n\n# Title\n\nBody.\n"},
		{"---\ncreated: 2026-01-01\ntags: a\n---\nBody.\n", Frontmatter{Tags: []string{"a"}}, ""},
		{"---\ntags:\ntldr:\n---\nBody.\n", Frontmatter{}, ""},
		{"---\nsources: https://a.example/x, [Doc, v2](https://b.example), [[people/alex|Alex, A.]]\n---\nBody.\n",
			Frontmatter{Sources: []string{"https://a.example/x", "[Doc, v2](https://b.example)", "[[people/alex|Alex, A.]]"}}, ""},
		{"---\ntags:\n  - work\n  - 'urgent'\nsources:\n- https://a.example/x?q=1:2\n- [[index]]\ntldr: Short.\n- stray item\n---\nBody.\n",
			Frontmatter{Tags: []string{"work", "urgent"}, Sources: []string{"https://a.example/x?q=1:2", "[[index]]"}, Tldr: "Short."}, ""},
		{"---\nsources: [[index]]\n---\nBody.\n", Frontmatter{Sources: []string{"[[index]]"}}, ""},
		{"---\ntldr: >-\n  One line\n  goes on.\n\n  Second: part.\ntags: a\n---\nBody.\n",
			Frontmatter{Tldr: "One line goes on.\nSecond: part.", Tags: []string{"a"}}, ""},
		{"---\ntldr: |\n  - first\n    more\n  - second\n---\nBody.\n", Frontmatter{Tldr: "- first\n  more\n- second"}, ""},
		{"---\ntldr: \"Starts here\n  and: ends here\"\ntags: a\n---\nBody.\n",
			Frontmatter{Tldr: "Starts here and: ends here", Tags: []string{"a"}}, ""},
		{"---\ntldr: >\n  Last\n---\nBody.\n", Frontmatter{Tldr: "Last"}, ""},
		{"---\ntldr: >\ntags: 'a'\n---\nBody.\n", Frontmatter{Tags: []string{"a"}}, ""},
		{"# Title\n\n---\ntags: a\n---\n", Frontmatter{}, "# Title\n\n---\ntags: a\n---\n"},
	}
	for _, c := range cases {
		meta, body := parseFrontmatter(c.content)
		if !reflect.DeepEqual(meta, c.want) || c.body != "" && body != c.body {
			t.Errorf("parseFrontmatter(%q) = %+v, %q; want %+v, %q", c.content, meta, body, c.want, c.body)
		}
	}
}

func TestRenderFrontmatterHTML(t *testing.T) {
	if got := renderFrontmatterHTML(Frontmatter{}, true, true); got != "" {
		t.Errorf("empty: %q", got)
	}
	both := Frontmatter{Tldr: "Summary here", Tags: []string{"work", "urgent"}}
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
	if renderFrontmatterHTML(Frontmatter{Tags: []string{"work"}}, false, true) != "" ||
		renderFrontmatterHTML(Frontmatter{Tldr: "Summary"}, true, false) != "" {
		t.Error("nothing left to show should render nothing")
	}
	html = renderFrontmatterHTML(Frontmatter{Tldr: "<script>alert(1)</script>"}, true, true)
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

func TestSplitWikilink(t *testing.T) {
	for in, want := range map[string][3]string{
		"Garden":                       {"Garden", "", "Garden"},
		"Garden|the garden":            {"Garden", "", "the garden"},
		"Garden#Beds":                  {"Garden", "Beds", "Garden › Beds"},
		" Garden # Raised beds | beds": {"Garden", "Raised beds", "beds"},
		"#Beds":                        {"", "Beds", "Beds"},
	} {
		page, section, label := splitWikilink(in)
		if [3]string{page, section, label} != want {
			t.Errorf("splitWikilink(%q) = %q %q %q, want %q", in, page, section, label, want)
		}
	}
}

// [[Page#Section]] links to the page: a backlink, renamed with it.
func TestSectionLinks(t *testing.T) {
	if got := pageLinkTargets("[[Garden#Beds]] [[#Here]]", "x"); !reflect.DeepEqual(got, map[string]bool{"garden": true}) {
		t.Errorf("targets %v", got)
	}
	if got := rewritePageLinks("[[garden#Beds|b]] [[Garden#Paths]] [[#garden]]", "garden", "yard", "", ""); got != "[[yard#Beds|b]] [[yard#Paths]] [[#garden]]" {
		t.Errorf("rename %q", got)
	}
	if got := plainTaskTitle("Weed [[Garden#Beds]] and [[Yard|the yard]]"); got != "Weed Garden › Beds and the yard" {
		t.Errorf("title %q", got)
	}
}

// Text like the masking tokens survives masking and renames as written.
func TestMaskLookalikes(t *testing.T) {
	text := "`a` 0 ``b`1`` 9\n```\n0\n```\n"
	if got := unmaskCode(maskCode(text)); got != text {
		t.Errorf("round trip %q", got)
	}
	if got := rewritePageLinks("`code` 0 [[old]]\n", "old", "new", "", ""); got != "`code` 0 [[new]]\n" {
		t.Errorf("rename %q", got)
	}
}

func TestToggleCheckboxCases(t *testing.T) {
	toggle := func(content string, idx int) string { return toggleNthCheckbox(content, idx, true) }
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

func toggle1(content string, idx int) string { return toggleNthCheckbox(content, idx, true) }

// Scratch notes moved to the wiki (Move to wiki) or into a page (Append).
func TestPromotedSlug(t *testing.T) {
	for _, c := range [][4]string{
		{"", "Meeting with Alex", "", "meeting-with-alex"},
		{"conversations/", "Alex & Sam", "2026-07-08", "conversations/2026-07-08_alex-sam"},
		{"A/B", "x/y", "", "a/b/x-y"},
		{"people", "  ", "", ""},
	} {
		if got := promotedSlug(c[0], c[1], c[2]); got != c[3] {
			t.Errorf("promotedSlug(%q, %q, %q) = %q, want %q", c[0], c[1], c[2], got, c[3])
		}
	}
	for _, c := range [][3]string{{"a/b", "Title", "[[a/b|Title]]"}, {"b", "x | [y]", "[[b|x  y]]"}, {"b", "[]", "[[b|b]]"}} {
		if got := pageWikilink(c[0], c[1]); got != c[2] {
			t.Errorf("pageWikilink(%q, %q) = %q, want %q", c[0], c[1], got, c[2])
		}
	}
}

func TestNoteAsSection(t *testing.T) {
	note := "---\ntags: alex, 1on1\ntldr: All good\n---\n# Title\n\nIntro\n\n## Topics\n\n### Detail\n\n```\n# not a heading\n```\n"
	section, tags := noteAsSection(note, "Alex 1:1 (2026-10-08)")
	if section != "## Alex 1:1 (2026-10-08)\n\n_All good_\n\nIntro\n\n### Topics\n\n#### Detail\n\n```\n# not a heading\n```\n" ||
		!reflect.DeepEqual(tags, []string{"alex", "1on1"}) {
		t.Errorf("note: %q %q", section, tags)
	}
	for in, want := range map[string]string{
		"# Only a title\n":                                           "## H\n",
		"---\ntldr: |\n  a\n  b\n---\n# T\n":                         "## H\n\n_a_\n_b_\n",
		"---\nsources: https://a.example, [[b]]\n---\n# T\n\nText\n": "## H\n\nText\n\nSources:\n\n- https://a.example\n- [[b]]\n",
	} {
		if section, tags := noteAsSection(in, "H"); section != want || len(tags) != 0 {
			t.Errorf("noteAsSection(%q) = %q %q, want %q", in, section, tags, want)
		}
	}
}

func TestAddFrontmatterTags(t *testing.T) {
	for _, c := range []struct {
		in   string
		tags []string
		want string
	}{
		{"# P\n", []string{"a"}, "---\ntags: a\n---\n# P\n"},
		{"---\ntags: [A, b]\n---\n# P\n", []string{"a", "c"}, "---\ntags: A, b, c\n---\n# P\n"},
		{"---\ntldr: x\n---\n# P\n", []string{"c"}, "---\ntags: c\ntldr: x\n---\n# P\n"},
		{"# P\n", nil, "# P\n"},
		{"---\ntags:\n  - a\n  - b\ntldr: x\n---\n# P\n", []string{"c"}, "---\ntags: a, b, c\ntldr: x\n---\n# P\n"},
	} {
		if got := addFrontmatterTags(c.in, c.tags); got != c.want {
			t.Errorf("addFrontmatterTags(%q, %q) = %q, want %q", c.in, c.tags, got, c.want)
		}
	}
}
