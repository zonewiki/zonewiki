package main

import (
	"math/rand"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"zonewiki/internal/mdtext"
)

// Sections as the parser finds them: ## and ### (ATX, up to three spaces in,
// or setext ---) directly in the page; the parts join to the page again.
func TestPageSectionsCases(t *testing.T) {
	cases := []struct {
		content string
		want    []string
	}{
		{"Just text\n", []string{"Just text\n"}},
		{"# Title\n\nBody text.\n", []string{"# Title\n\nBody text.\n"}},
		{"#### Deep heading\n\nBody.\n", []string{"#### Deep heading\n\nBody.\n"}},
		{"Preamble text.\n\n## Section A\n\nContent A.\n", []string{"Preamble text.\n\n", "## Section A\n\nContent A.\n"}},
		{"Preamble.\n\n### Section A\n\nContent A.\n", []string{"Preamble.\n\n", "### Section A\n\nContent A.\n"}},
		{"Preamble.\n\n## Alpha\n\nContent alpha.\n\n## Beta\n\nContent beta.\n\n## Gamma\n\nContent gamma.\n", []string{"Preamble.\n\n", "## Alpha\n\nContent alpha.\n\n", "## Beta\n\nContent beta.\n\n", "## Gamma\n\nContent gamma.\n"}},
		{"Intro.\n\n## Section 1\n\nBody.\n\n### Subsection 1.1\n\nSub body.\n\n## Section 2\n\nBody 2.\n", []string{"Intro.\n\n", "## Section 1\n\nBody.\n\n", "### Subsection 1.1\n\nSub body.\n\n", "## Section 2\n\nBody 2.\n"}},
		{"## First\n\nContent.\n", []string{"", "## First\n\nContent.\n"}},
		{"Pre.\n\n```\n## Not a heading\n```\n\n## Real\n\nText.\n", []string{"Pre.\n\n```\n## Not a heading\n```\n\n", "## Real\n\nText.\n"}},
		{"Pre.\n\n~~~\n### Not\n~~~\n\n## Real\n\nText.\n", []string{"Pre.\n\n~~~\n### Not\n~~~\n\n", "## Real\n\nText.\n"}},
		{"Pre.\n\n```\n## Fake\n\n## Also fake\n", []string{"Pre.\n\n```\n## Fake\n\n## Also fake\n"}},
		{"Preamble.\n\n## One\n\nContent one.\n\n### Sub\n\nSub content.\n\n## Two\n\nContent two.\n", []string{"Preamble.\n\n", "## One\n\nContent one.\n\n", "### Sub\n\nSub content.\n\n", "## Two\n\nContent two.\n"}},
		{"", []string{""}},
		{"Preamble.\n\n##NoSpace\n\n## Real\n\nBody.\n", []string{"Preamble.\n\n##NoSpace\n\n", "## Real\n\nBody.\n"}},
		{"pre\n\n## A\n\na\n\n## B\n\nb\n\n## C\n\nc\n", []string{"pre\n\n", "## A\n\na\n\n", "## B\n\nb\n\n", "## C\n\nc\n"}},
		{"# Title\n\n- some item\n- \n\n## Real Section\n\nbody\n", []string{"# Title\n\n- some item\n- \n\n", "## Real Section\n\nbody\n"}},
		{"## To discuss\n\n- [ ] Task one\n- [ ] Task two\n---\n- note\n", []string{"", "## To discuss\n\n- [ ] Task one\n- [ ] Task two\n---\n- note\n"}},
		{"# Page\n\n## To discuss\n\n- [ ] Timebooking?\n- [ ] Lee?\n- [ ] ownership of PaaS\n---\n- potential candidates\n\n## Next Section\n\nother content\n", []string{"# Page\n\n", "## To discuss\n\n- [ ] Timebooking?\n- [ ] Lee?\n- [ ] ownership of PaaS\n---\n- potential candidates\n\n", "## Next Section\n\nother content\n"}},
		{"## S\n\n- last item\n---\n\nmore text\n", []string{"", "## S\n\n- last item\n---\n\nmore text\n"}},
		{"## S\n\n* last item\n---\n\nmore\n", []string{"", "## S\n\n* last item\n---\n\nmore\n"}},
		{"## S\n\n+ last item\n---\n\nmore\n", []string{"", "## S\n\n+ last item\n---\n\nmore\n"}},
		{"## S\n\n  - indented item\n---\n\nmore\n", []string{"", "## S\n\n  - indented item\n---\n\nmore\n"}},
		{"## S\n\nSome text\n===\n\nmore\n", []string{"", "## S\n\nSome text\n===\n\nmore\n"}},
		{"Preamble\n\nMy Heading\n----------\n\nbody\n", []string{"Preamble\n\n", "My Heading\n----------\n\nbody\n"}},
		{"## A\n\ntext a\n\n---\n\nmore a\n\n## B\n\ntext b\n", []string{"", "## A\n\ntext a\n\n---\n\nmore a\n\n", "## B\n\ntext b\n"}},
		{"# Title\n\n## Section One\n\n- alpha\n- beta\n---\nextra\n\n## Section Two\n\nbody\n", []string{"# Title\n\n", "## Section One\n\n- alpha\n- beta\n---\nextra\n\n", "## Section Two\n\nbody\n"}},
		{"## Parent\n\n### Child\n\nbody\n", []string{"", "## Parent\n\n", "### Child\n\nbody\n"}},
		{"preamble\n\n### A\n\na body\n\n### B\n\nb body\n", []string{"preamble\n\n", "### A\n\na body\n\n", "### B\n\nb body\n"}},
		{"## Section\n\n```\nsome text\n---\n```\n\nbody\n", []string{"", "## Section\n\n```\nsome text\n---\n```\n\nbody\n"}},
		{"## S\n\n1. first\n---\n\nmore\n", []string{"", "## S\n\n1. first\n---\n\nmore\n"}},
		{"Preamble.\n\nSection One\n-----------\n\nContent 1.\n", []string{"Preamble.\n\n", "Section One\n-----------\n\nContent 1.\n"}},
		{"Preamble.\n\nSection One\n-----------\n\nContent 1.\n\nSection Two\n-----------\n\nContent 2.\n", []string{"Preamble.\n\n", "Section One\n-----------\n\nContent 1.\n\n", "Section Two\n-----------\n\nContent 2.\n"}},
		{"Preamble.\n\nHeading A\n---------\n\nBody A.\n\nHeading B\n---------\n\nBody B.\n", []string{"Preamble.\n\n", "Heading A\n---------\n\nBody A.\n\n", "Heading B\n---------\n\nBody B.\n"}},
		{"## Real\n\nbody\n\n---\n\nmore body\n", []string{"", "## Real\n\nbody\n\n---\n\nmore body\n"}},
		{"Preamble.\n\n```\nNot a heading\n-------------\n```\n\n## Real\n\nbody\n", []string{"Preamble.\n\n```\nNot a heading\n-------------\n```\n\n", "## Real\n\nbody\n"}},
		{"pre\n\n ## Indented One\n\nbody\n", []string{"pre\n\n", " ## Indented One\n\nbody\n"}},
		{"pre\n\n  ## Two Spaces\n\nbody\n", []string{"pre\n\n", "  ## Two Spaces\n\nbody\n"}},
		{"pre\n\n   ## Three Spaces\n\nbody\n", []string{"pre\n\n", "   ## Three Spaces\n\nbody\n"}},
		{"pre\n\n    ## Four Spaces — code\n\nbody\n", []string{"pre\n\n    ## Four Spaces — code\n\nbody\n"}},
		{"pre\n\n  ## A\n\nbody a\n\n  ## B\n\nbody b\n", []string{"pre\n\n", "  ## A\n\nbody a\n\n", "  ## B\n\nbody b\n"}},
	}
	for _, c := range cases {
		got := pageSections(c.content)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("pageSections(%q):\n got %q\nwant %q", c.content, got, c.want)
		}
		if strings.Join(got, "") != c.content {
			t.Errorf("pageSections(%q) doesn't join to the text", c.content)
		}
	}
}

// sectionHeadingHTML: the rendered headings with a ✎: their text and number.
var sectionHeadingHTML = regexp.MustCompile(`(?s)<div class="section-header[^>]*><h[23][^>]*>(.*?)</h[23]>.*?\?section=(\d+)"`)

// Each ✎ opens the section of its own heading, whatever else the page holds:
// heads are the headings with a ✎, in order.
func TestEachPencilOpensItsSection(t *testing.T) {
	cases := []struct {
		name, src string
		heads     []string
	}{
		{"frontmatter", "---\ntags: a\ntldr: b\n---\n\n# T\n\n## One\n\n## Two\n", []string{"One", "Two"}},
		{"frontmatter ending in a blank", "---\ntags: a\n\n---\n\n## One\n\n## Two\n", []string{"One", "Two"}},
		{"frontmatter with a ## line", "---\n## comment\ntags: a\n---\n\n## One\n\n## Two\n", []string{"One", "Two"}},
		{"--- under the title", "# T\n---\n\n## One\n\n---\n\n## Two\n", []string{"One", "Two"}},
		{"tab after the marks", "## One\n\n##\tTab\n\n## Three\n", []string{"One", "Tab", "Three"}},
		{"empty heading", "## One\n\n##\n\n## Three\n", []string{"One", "", "Three"}},
		{"quote", "## One\n\n> ## Quoted\n\n## Three\n", []string{"One", "Three"}},
		{"list", "## One\n\n- ## Listed\n\n## Three\n", []string{"One", "Three"}},
		{"in a list item, 2 spaces", "## One\n\n- item\n\n  ## Nested\n\n## Three\n", []string{"One", "Three"}},
		{"in a list item, 4 spaces", "## One\n\n1. item\n\n    ## Nested\n\n## Three\n", []string{"One", "Three"}},
		{"footnote", "## One\n\ntext[^1]\n\n[^1]: note\n\n    ## In note\n\n## Three\n", []string{"One", "Three"}},
		{"html comment", "## One\n\n<!--\n## Hidden\n-->\n\n## Three\n", []string{"One", "Three"}},
		{"html block", "## One\n\n<div>\n## Inside\n</div>\n\n## Three\n", []string{"One", "Three"}},
		{"--- in an html block", "## One\n\n<div>\nfoo\n---\n</div>\n\n## Three\n", []string{"One", "Three"}},
		{"--- after a numbered list", "## One\n\n1. first\n---\n\n## Three\n", []string{"One", "Three"}},
		{"--- after indented code", "## One\n\n    code\n---\n\n## Three\n", []string{"One", "Three"}},
		{"--- indented 4", "## One\n\ntext\n    ---\n\n## Three\n", []string{"One", "Three"}},
		{"--- after a table", "## One\n\n| a |\n|---|\n| b |\n---\n\n## Three\n", []string{"One", "Three"}},
		{"--- after a quote", "## One\n\n> q\n---\n\n## Three\n", []string{"One", "Three"}},
		{"--- after #### ", "## One\n\n#### Four\n---\n\n## Three\n", []string{"One", "Three"}},
		{"--- after a definition", "## One\n\nTerm\n: def\n---\n\n## Three\n", []string{"One", "Three"}},
		{"setext, two lines", "## One\n\nline a\nline b\n---\n\nbody\n\n## Three\n", []string{"One", "line a\nline b", "Three"}},
		{"--- two dashes", "## One\n\ntext\n--\n\n## Three\n", []string{"One", "text", "Three"}},
		{"4 spaces are code", "## One\n\n    ```\n\n## Two\n\n    ```\n\n## Three\n", []string{"One", "Two", "Three"}},
		{"a fence's info with a backtick", "## One\n\n``` a`b\n\n## Two\n", []string{"One", "Two"}},
		{"code at a line's start", "## One\n\n```x``` text\n\n## Two\n", []string{"One", "Two"}},
		{"line separator", "## One\n\nfoo\u2028## Not\n\n## Three\n", []string{"One", "Three"}},
		{"form feed", "## One\n\nfoo\f## Not\n\n## Three\n", []string{"One", "Three"}},
		{"crlf", "## One\r\n\r\nfoo\r\n---\r\n\r\n## Three\r\n", []string{"One", "foo", "Three"}},
		{"math", "## One\n\n$$\n## Tex\n$$\n\n## Three\n", []string{"One", "Three"}},
		{"a fence in a list", "## One\n\n- x\n  ```\n  ## Code\n  ```\n\n## Three\n", []string{"One", "Three"}},
		{"closing marks", "## One ##\n\n### Two ###\n", []string{"One", "Two"}},
		{"not the first character", "## Ein\n\nÄÖÜ 😀\n\n## Zwei\n", []string{"Ein", "Zwei"}},
	}
	for _, c := range cases {
		_, shown := mdtext.ParseFrontmatter(mdtext.StripH1(c.src))
		found := sectionHeadingHTML.FindAllStringSubmatch(markdown(shown, &Env{EditSlug: "p"}, false), -1)
		sections := pageSections(c.src)
		if strings.Join(sections, "") != c.src {
			t.Errorf("%s: %q doesn't join to the text", c.name, sections)
		}
		if len(found) != len(c.heads) || len(sections)-1 != len(c.heads) {
			t.Errorf("%s: %d ✎ and %d sections, want %d: %q", c.name, len(found), len(sections)-1, len(c.heads), sections)
			continue
		}
		for i, m := range found {
			if m[1] != c.heads[i] || m[2] != strconv.Itoa(i+1) {
				t.Errorf("%s: ✎ %d is %q numbered %s, want %q", c.name, i+1, m[1], m[2], c.heads[i])
			}
			first, _, _ := strings.Cut(sections[i+1], "\n")
			if !strings.Contains(first, strings.Split(c.heads[i], "\n")[0]) {
				t.Errorf("%s: ✎ %d %q opens %q", c.name, i+1, c.heads[i], sections[i+1])
			}
		}
	}
}

// Pages put together at random from what trips a line-based reading: as many
// ✎ as sections, always, and the sections join to the page.
func TestPencilsAndSectionsAgreeOnAnyPage(t *testing.T) {
	blocks := []string{"## A\n", "### B\n", "##\n", "##\tC\n", "# H\n", "#### D\n", "text\n", "text\n---\n", "---\n", "===\n", "\n", "\n",
		"- item\n", "  ## in item\n", "1. one\n", "> ## quote\n", "> q\n", "```\n", "~~~\n", "    code\n", "$$\n", "<div>\n", "</div>\n",
		"<!--\n", "-->\n", "| a |\n", "|---|\n", "[^1]: note\n", "[^1]\n", "Term\n", ": def\n", "```x``` y\n", "tags: a\n", "é😀\n", "a\r\n", "## E ##\r\n"}
	rng := rand.New(rand.NewSource(1))
	for range 3000 {
		var b strings.Builder
		for range rng.Intn(14) {
			b.WriteString(blocks[rng.Intn(len(blocks))])
		}
		src := b.String()
		sections := pageSections(src)
		if strings.Join(sections, "") != src {
			t.Fatalf("%q doesn't join to the text: %q", src, sections)
		}
		_, shown := mdtext.ParseFrontmatter(mdtext.StripH1(src))
		if n := strings.Count(markdown(shown, &Env{EditSlug: "p"}, false), `class="section-edit-btn"`); n != len(sections)-1 {
			t.Fatalf("%d ✎ for %d sections: %q", n, len(sections)-1, src)
		}
	}
}

func TestSectionRange(t *testing.T) {
	content := "pre\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
	for _, idx := range []int{0, 4} {
		if _, _, ok := sectionRange(content, idx); ok {
			t.Errorf("section %d shouldn't exist", idx)
		}
	}
	s, e, _ := sectionRange(content, 2)
	if got := mdtext.RuneSlice(content, s, e); got != "## B\n\nb body\n\n" {
		t.Errorf("section 2 is %q", got)
	}
	// The preamble and the sections' ranges cover the text.
	parts := pageSections(content)
	whole := parts[0]
	for i := 1; i < len(parts); i++ {
		s, e, _ := sectionRange(content, i)
		whole += mdtext.RuneSlice(content, s, e)
	}
	if whole != content {
		t.Errorf("ranges cover %q", whole)
	}
}

// splice puts a section's new text in at its range on the page as loaded.
func splice(t *testing.T, content string, idx int, text string) (string, string, int, int) {
	t.Helper()
	s, e, ok := sectionRange(content, idx)
	if !ok {
		t.Fatalf("no section %d", idx)
	}
	merged, hash, ns, ne, ok := mdtext.SpliceSection(&content, mdtext.ContentHash(content), s, e, text)
	if !ok {
		t.Fatalf("splice of section %d refused", idx)
	}
	return merged, hash, ns, ne
}

func TestSpliceSection(t *testing.T) {
	content := "pre\n\n## A\n\nbody\n"
	hash := mdtext.ContentHash(content)
	// Refused: no file, the file changed (another hash), a range past the end.
	if _, _, _, _, ok := mdtext.SpliceSection(nil, "abc", 0, 10, "x"); ok {
		t.Error("splice into no file")
	}
	if _, _, _, _, ok := mdtext.SpliceSection(&content, "wrong_hash", 5, 18, "new"); ok {
		t.Error("splice with the wrong hash")
	}
	if _, _, _, _, ok := mdtext.SpliceSection(&content, hash, 0, len(content)+10, "new"); ok {
		t.Error("splice past the end")
	}
	changed := "pre\n\n## A\n\nSOMEONE ELSE EDITED\n"
	s, e, _ := sectionRange(content, 1)
	if _, _, _, _, ok := mdtext.SpliceSection(&changed, hash, s, e, "## A\n\nmy edit\n"); ok {
		t.Error("splice into a file changed since")
	}

	three := "pre\n\n## Alpha\n\nContent alpha.\n\n## Beta\n\nContent beta.\n\n## Gamma\n\nContent gamma.\n"
	merged, _, _, _ := splice(t, three, 2, "## Beta\n\nUpdated beta.\n")
	if merged != "pre\n\n## Alpha\n\nContent alpha.\n\n## Beta\n\nUpdated beta.\n\n## Gamma\n\nContent gamma.\n" {
		t.Errorf("middle section: %q", merged)
	}
	merged, _, _, _ = splice(t, three, 1, "## Alpha\n\nnew\n")
	if !strings.HasPrefix(merged, "pre\n\n## Alpha\n\nnew\n") || strings.Count(merged, "## Beta") != 1 || !strings.Contains(merged, "Content gamma.") {
		t.Errorf("first section: %q", merged)
	}
	merged, _, _, _ = splice(t, three, 3, "## Gamma\n\nupdated 3\n")
	if !strings.Contains(merged, "Content alpha.") || !strings.Contains(merged, "Content beta.") || !strings.HasSuffix(merged, "updated 3\n") {
		t.Errorf("last section: %q", merged)
	}
	// A section's own text back changes nothing.
	parts := pageSections(three)
	for i := 1; i < len(parts); i++ {
		if merged, _, _, _ := splice(t, three, i, parts[i]); merged != three {
			t.Errorf("round trip of section %d: %q", i, merged)
		}
	}
	// The page keeps its last newline.
	if merged, _, _, _ := splice(t, content, 1, "## A\n\nupdated"); !strings.HasSuffix(merged, "\n") {
		t.Errorf("trailing newline: %q", merged)
	}

	// The new hash and range let the next save (auto-save) splice again.
	page := "pre\n\n## A\n\nshort\n\n## B\n\nb\n"
	merged, h1, s1, e1 := splice(t, page, 1, "## A\n\nmuch longer replacement content\n")
	if h1 != mdtext.ContentHash(merged) || !strings.HasPrefix(mdtext.RuneSlice(merged, s1, e1), "## A\n\nmuch longer replacement content") {
		t.Errorf("anchors %q %d %d of %q", h1, s1, e1, merged)
	}
	merged2, _, _, _, ok := mdtext.SpliceSection(&merged, h1, s1, e1, "## A\n\nsecond edit\n")
	if !ok || merged2 != "pre\n\n## A\n\nsecond edit\n\n## B\n\nb\n" {
		t.Errorf("second splice %v %q", ok, merged2)
	}
}

func TestFindSection(t *testing.T) {
	text := "pré\n\n## A\n\nä\n\n## Twin\n\n## B\n\n## Twin\n"
	cases := []struct {
		idx     int
		heading string
		found   int
		text    string
	}{
		{1, "## A", 1, "## A\n\nä\n\n"}, // at its number
		{3, "## A", 1, "## A\n\nä\n\n"}, // moved
		{4, "## Twin", 4, "## Twin\n"},  // several, this one at its number
		{1, "## Twin", 0, ""},           // several, neither at its number
		{1, "## Gone", 0, ""},           // none
		{0, "## A", 1, "## A\n\nä\n\n"}, // no number: by the heading alone
		{0, "## Twin", 0, ""},           // no number, several
		{1, "", 0, ""},                  // no heading given
	}
	for _, c := range cases {
		found, s, e, ok := findSection(text, c.idx, headingKey(c.heading))
		if ok != (c.found > 0) || (ok && (found != c.found || mdtext.RuneSlice(text, s, e) != c.text)) {
			t.Errorf("findSection(%d, %q) = %d %q %v", c.idx, c.heading, found, mdtext.RuneSlice(text, s, e), ok)
		}
	}
}

// No heading given finds nothing on a page whose headings are empty either:
// an empty heading's line still has its #s.
func TestEmptyHeadingIsNoEmptyLine(t *testing.T) {
	for _, text := range []string{"##\n\nx\n", "\n##\n\nx\n", "pre\n\n## \n\nx\n", "pre\n\n##\n"} {
		if _, _, _, ok := findSection(text, 1, headingKey("")); ok {
			t.Errorf("%q: found a section with no heading given", text)
		}
	}
}

// A page with Windows line breaks: its heading is found when merging, and a
// splice keeps the breaks before the next heading.
func TestSectionsOnACRLFPage(t *testing.T) {
	text := "## A\r\n\r\nx\r\n\r\n## B\r\n"
	_, s, e, ok := findSection(text, 0, headingKey("## A"))
	if !ok || mdtext.RuneSlice(text, s, e) != "## A\r\n\r\nx\r\n\r\n" {
		t.Fatalf("%d %d %v", s, e, ok)
	}
	merged, _, _, _, _ := mdtext.SpliceSection(&text, mdtext.ContentHash(text), s, e, "## A\n\nnew\n")
	if merged != "## A\n\nnew\r\n\r\n## B\r\n" {
		t.Errorf("%q", merged)
	}
}

// What the MCP tools take for a section: the parser's headings too.
func TestSectionLinesGoByTheParser(t *testing.T) {
	lines := func(s string) []string { return mdtext.SplitLines(s) }
	cases := []struct {
		name, text, heading string
		start, end          int
	}{
		{"a setext heading ends a section", "# P\n\n## A\n\na\n\nB\n---\n\nb\n", "A", 2, 6},
		{"a setext heading is one", "## A\n\nB title\n---\n\nb\n\n## C\n", "B title", 2, 7},
		{"not one in a comment", "## A\n\n<!--\n## hidden\n-->\n\nb\n\n## C\n", "A", 0, 8},
		{"not one in frontmatter", "---\n## note\ntags: a\n---\n\n## note\n\nreal\n", "note", 5, 8},
		{"after inline code at a line's start", "```x``` y\n\n## After\n\nb\n", "After", 2, 5},
		{"subsections belong to it", "## A\n\n### B\n\nb\n\n## C\n", "A", 0, 6},
		{"a page break in the text", "## A\n\nx\fy\n\n## B\n", "A", 0, 5},
	}
	for _, c := range cases {
		start, end, ok := sectionLines(lines(c.text), c.heading)
		if !ok || start != c.start || end != c.end {
			t.Errorf("%s: %d-%d %v, want %d-%d", c.name, start, end, ok, c.start, c.end)
		}
	}
	if _, _, ok := sectionLines(lines("<!--\n## hidden\n-->\n"), "hidden"); ok {
		t.Error("a ## in a comment is no heading")
	}
	got, ok := appendAt("## A\n\na\n\n<!--\n## hidden\n-->\n\nb\n\n## C\n", "ADDED", "A")
	if !ok || got != "## A\n\na\n\n<!--\n## hidden\n-->\n\nb\n\nADDED\n\n## C\n" {
		t.Errorf("%v %q", ok, got)
	}
	if got := noHeading("p", "---\n## no\n---\nT\n---\n\n## A ##\n", "x"); got != `p has no heading "x". Its headings: "T", "A".` {
		t.Error(got)
	}
}

// Search's chunks and the names of sections tasks link to: the same headings.
func TestChunksGoByTheParser(t *testing.T) {
	got := chunkSections("Intro\n\nTwo\nlines\n---\n\nbody\n\n   ## Indented\n\nmore\n\n##\u00a0no heading\n\n<!--\n## hidden\n-->\n", maxChunkChars)
	want := []chunkSection{
		{"", "Intro", 0, ""},
		{"Two lines", "body", 2, "Two lines"},
		{"Indented", "more\n\n##\u00a0no heading\n\n<!--\n## hidden\n-->", 2, "Indented"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v", got)
	}
}
