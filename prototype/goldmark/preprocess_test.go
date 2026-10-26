package main

import (
	"reflect"
	"strings"
	"testing"
)

var testWiki = &Wiki{
	Slugs:  map[string]bool{"index": true, "notes/my-page": true, "secret": true, "Odd Name": true},
	Hidden: map[string]bool{"secret": true},
}

func TestWikilinks(t *testing.T) {
	cases := map[string]string{
		"[[Notes/My Page]]":          `<a href="/page/notes/my-page" class="wikilink">Notes/My Page</a>`,
		"[[notes/my-page|the page]]": `<a href="/page/notes/my-page" class="wikilink">the page</a>`,
		"[[Missing <b>]]":            `<a href="/page/missing-b" class="wikilink wikilink-missing">Missing &lt;b&gt;</a>`,
		"[[index#Über **all** `x`]]": "<a href=\"/page/index#über-all\" class=\"wikilink\">index › Über **all** `x`</a>",
		"[[#Here [[x]]]]":            `[[#Here <a href="/page/x" class="wikilink wikilink-missing">x</a>]]`,
		"[[#Section one]]":           `<a href="#section-one" class="wikilink">Section one</a>`,
		"[[secret|words stay]]":      `<span class="wikilink-hidden">words stay</span>`,
		"[[Odd Name]]":               `<a href="/page/Odd%20Name" class="wikilink">Odd Name</a>`,
		"[[it's]]":                   `<a href="/page/it-s" class="wikilink wikilink-missing">it&#x27;s</a>`,
	}
	for in, want := range cases {
		if got := Wikilinks(in, testWiki); got != want {
			t.Errorf("%s:\n got %s\nwant %s", in, got, want)
		}
	}
}

// Code is masked: [[…]] in it stays as written.
func TestPrepareMasksCode(t *testing.T) {
	src := "`[[index]]` ``a`[[deck]]`b`` [[index]]\n```\n[[deck]] [[task:abcd1234]]\n```\n````x\n```\n[[index]]\n````\n"
	p := Prepare(src, testWiki)
	want := "`[[index]]` ``a`[[deck]]`b`` " + `<a href="/page/index" class="wikilink">index</a>` +
		"\n```\n[[deck]] [[task:abcd1234]]\n```\n````x\n```\n[[index]]\n````\n"
	if p.Text != want || p.Decks != nil || p.TaskRefs != nil {
		t.Errorf("got %q %v %v", p.Text, p.Decks, p.TaskRefs)
	}
}

func TestPreparePlaceholders(t *testing.T) {
	p := Prepare("[[deck]] [[DECK list=work]] [[task:ABCD-1234]]\u00a0[[deck\u00a0view=x]]", testWiki)
	if p.Text != "LEAFDECKPLACEHOLDER_0 LEAFDECKPLACEHOLDER_1 \ue0020\ue003\u00a0LEAFDECKPLACEHOLDER_2" {
		t.Errorf("text %q", p.Text)
	}
	if !reflect.DeepEqual(p.Decks, []string{"", "list=work", "view=x"}) || !reflect.DeepEqual(p.TaskRefs, []string{"abcd-1234"}) {
		t.Errorf("decks %q, refs %q", p.Decks, p.TaskRefs)
	}
}

// Mask characters in a page's own text are left alone (Python's
// unmask_code raises IndexError on them).
func TestUnmaskForeignTokens(t *testing.T) {
	if p := Prepare("x \ue0005\ue001 y", testWiki); p.Text != "x \ue0005\ue001 y" {
		t.Errorf("got %q", p.Text)
	}
}

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
	got := splitLines("a\nb\r\nc\rd\u2028e\x1cf")
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

// Lines of any Python whitespace (\r, U+2028, \x1c …) are blank to dedent.
func TestDedent(t *testing.T) {
	for in, want := range map[string]string{
		"    literal\n      more\n ": "literal\n  more\n",
		"    a\n\r\n  b\n\x1c":       "  a\n\nb\n",
	} {
		if got := dedent(in); got != want {
			t.Errorf("%q: got %q, want %q", in, got, want)
		}
	}
}
