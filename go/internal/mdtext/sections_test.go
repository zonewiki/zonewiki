package mdtext

import (
	"reflect"
	"strings"
	"testing"
)

// Sections as the ✎ buttons count them: ## and ### (ATX, up to three
// spaces in, or setext ---), not in code, not --- after a list item or a
// blank line; the parts join to the page again.
func TestSplitSectionsCases(t *testing.T) {
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
		{"## S\n\n1. first\n---\n\nmore\n", []string{"", "## S\n\n", "1. first\n---\n\nmore\n"}},
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
		got := SplitSections(c.content)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("SplitSections(%q):\n got %q\nwant %q", c.content, got, c.want)
		}
		if strings.Join(got, "") != c.content {
			t.Errorf("SplitSections(%q) doesn't join to the text", c.content)
		}
	}
}

func TestContentHash(t *testing.T) {
	if ContentHash("hello") != ContentHash("hello") || ContentHash("a") == ContentHash("b") || len(ContentHash("some content")) != 16 {
		t.Error("ContentHash: 16 hex characters, the same for the same text")
	}
}

func TestSectionCharRange(t *testing.T) {
	content := "pre\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
	for _, idx := range []int{0, 4} {
		if _, _, ok := SectionCharRange(content, idx); ok {
			t.Errorf("section %d shouldn't exist", idx)
		}
	}
	s, e, _ := SectionCharRange(content, 2)
	if got := RuneSlice(content, s, e); got != "## B\n\nb body\n\n" {
		t.Errorf("section 2 is %q", got)
	}
	// The preamble and the sections' ranges cover the text.
	parts := SplitSections(content)
	whole := parts[0]
	for i := 1; i < len(parts); i++ {
		s, e, _ := SectionCharRange(content, i)
		whole += RuneSlice(content, s, e)
	}
	if whole != content {
		t.Errorf("ranges cover %q", whole)
	}
}

// splice puts a section's new text in at its range on the page as loaded.
func splice(t *testing.T, content string, idx int, text string) (string, string, int, int) {
	t.Helper()
	s, e, ok := SectionCharRange(content, idx)
	if !ok {
		t.Fatalf("no section %d", idx)
	}
	merged, hash, ns, ne, ok := SpliceSection(&content, ContentHash(content), s, e, text)
	if !ok {
		t.Fatalf("splice of section %d refused", idx)
	}
	return merged, hash, ns, ne
}

func TestSpliceSection(t *testing.T) {
	content := "pre\n\n## A\n\nbody\n"
	hash := ContentHash(content)
	// Refused: no file, the file changed (another hash), a range past the end.
	if _, _, _, _, ok := SpliceSection(nil, "abc", 0, 10, "x"); ok {
		t.Error("splice into no file")
	}
	if _, _, _, _, ok := SpliceSection(&content, "wrong_hash", 5, 18, "new"); ok {
		t.Error("splice with the wrong hash")
	}
	if _, _, _, _, ok := SpliceSection(&content, hash, 0, len(content)+10, "new"); ok {
		t.Error("splice past the end")
	}
	changed := "pre\n\n## A\n\nSOMEONE ELSE EDITED\n"
	s, e, _ := SectionCharRange(content, 1)
	if _, _, _, _, ok := SpliceSection(&changed, hash, s, e, "## A\n\nmy edit\n"); ok {
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
	parts := SplitSections(three)
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
	if h1 != ContentHash(merged) || !strings.HasPrefix(RuneSlice(merged, s1, e1), "## A\n\nmuch longer replacement content") {
		t.Errorf("anchors %q %d %d of %q", h1, s1, e1, merged)
	}
	merged2, _, _, _, ok := SpliceSection(&merged, h1, s1, e1, "## A\n\nsecond edit\n")
	if !ok || merged2 != "pre\n\n## A\n\nsecond edit\n\n## B\n\nb\n" {
		t.Errorf("second splice %v %q", ok, merged2)
	}
}
