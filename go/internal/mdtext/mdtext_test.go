package mdtext

import (
	"reflect"
	"strings"
	"testing"
)

func TestFrontmatter(t *testing.T) {
	src := strings.Join([]string{
		"---",
		"tags: [a, \"b c\"]",
		"sources:",
		"  - [label, with comma](https://x.org)",
		"  - '[[page|a, b]]'",
		"tldr: >-",
		"  folded",
		"  lines",
		"",
		"  para",
		"title: ignored",
		"---",
		"",
		"# Body",
	}, "\n")
	meta, body := ParseFrontmatter(src)
	want := Frontmatter{Tags: []string{"a", "b c"}, Sources: []string{"[label, with comma](https://x.org)", "[[page|a, b]]"},
		Tldr: "folded lines\npara"}
	if !reflect.DeepEqual(meta, want) || body != "# Body" {
		t.Errorf("got %+v %q", meta, body)
	}
	meta, _ = ParseFrontmatter("---\ntldr: |\n    one\n      two\n---\n")
	if meta.Tldr != "one\n  two" {
		t.Errorf("literal tldr %q", meta.Tldr)
	}
	meta, _ = ParseFrontmatter("---\ntldr: \"plain\n  goes on\"\n---\n")
	if meta.Tldr != "plain goes on" {
		t.Errorf("plain tldr %q", meta.Tldr)
	}
	// No closing fence: no frontmatter, the text unchanged.
	if meta, body := ParseFrontmatter("---\ntags: a\n"); meta.Tags != nil || body != "---\ntags: a\n" {
		t.Errorf("unclosed: %+v %q", meta, body)
	}
}

func TestSplitLines(t *testing.T) {
	got := SplitLines("a\nb\r\nc\rd\u2028e\x1cf")
	want := []string{"a\n", "b\r\n", "c\r", "d\u2028", "e\x1c", "f"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
}

func TestToSlug(t *testing.T) {
	for in, want := range map[string]string{" Notes / My Page! ": "notes/my-page", "Ärger": "rger", "a//b": "a/b"} {
		if got := ToSlug(in); got != want {
			t.Errorf("%q: got %q, want %q", in, got, want)
		}
	}
}

// Lines of whitespace only (\r, tabs …) are blank to Dedent.
func TestDedent(t *testing.T) {
	for in, want := range map[string]string{
		"    literal\n      more\n ": "literal\n  more\n",
		"    a\n\r\n  b\n \t":        "  a\n\nb\n",
	} {
		if got := Dedent(in); got != want {
			t.Errorf("%q: got %q, want %q", in, got, want)
		}
	}
}

// A splice's offsets count characters, as the editor does.
func TestSpliceCountsCharacters(t *testing.T) {
	text := "# Ü\n\nä\n## Zwei\nö\n### Drei\nx\n"
	start, end := 7, 17
	if RuneSlice(text, start, end) != "## Zwei\nö\n" {
		t.Fatalf("%q", RuneSlice(text, start, end))
	}
	merged, _, s, e, ok := SpliceSection(&text, ContentHash(text), start, end, "## Zwei\nneu")
	if !ok || merged != "# Ü\n\nä\n## Zwei\nneu\n### Drei\nx\n" || s != 7 || e != 19 {
		t.Fatalf("%q %d %d", merged, s, e)
	}
}
