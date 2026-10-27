package main

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// testWiki points cfg at a fresh wiki with these pages (slug → text);
// pages under secret/ are not for sharing.
func testWiki(t *testing.T, pages map[string]string) {
	t.Helper()
	dir := t.TempDir()
	for slug, text := range pages {
		p := filepath.Join(dir, filepath.FromSlash(slug)+".md")
		_ = os.MkdirAll(filepath.Dir(p), 0o777)
		if err := os.WriteFile(p, []byte(text), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	c, err := loadConfig(func(k string) string {
		return map[string]string{"LEAF_DATA": dir, "LEAF_NOSHARE": "secret/"}[k]
	})
	if err != nil {
		t.Fatal(err)
	}
	c.GitEnabled = false
	old := cfg
	cfg = c
	t.Cleanup(func() { cfg = old })
}

var sharing = &Req{sharing: true}

func TestWikilinks(t *testing.T) {
	testWiki(t, nil)
	slugs := map[string]bool{"index": true, "notes/my-page": true, "secret": true, "Odd Name": true}
	cases := map[string]string{
		"[[Notes/My Page]]":          `<a href="/page/notes/my-page" class="wikilink">Notes/My Page</a>`,
		"[[notes/my-page|the page]]": `<a href="/page/notes/my-page" class="wikilink">the page</a>`,
		"[[Missing <b>]]":            `<a href="/page/missing-b" class="wikilink wikilink-missing">Missing &lt;b&gt;</a>`,
		"[[index#Über **all** `x`]]": "<a href=\"/page/index#über-all\" class=\"wikilink\">index › Über **all** `x`</a>",
		"[[#Here [[x]]]]":            `[[#Here <a href="/page/x" class="wikilink wikilink-missing">x</a>]]`,
		"[[#Section one]]":           `<a href="#section-one" class="wikilink">Section one</a>`,
		"[[secret|words stay]]":      `<span class="wikilink-hidden">words stay</span>`,
		"[[Odd Name]]":               `<a href="/page/Odd%20Name" class="wikilink">Odd Name</a>`,
		"[[it's]]":                   `<a href="/page/it-s" class="wikilink wikilink-missing">it&#39;s</a>`,
	}
	for in, want := range cases {
		if got := processWikilinks(sharing, in, slugs); got != want {
			t.Errorf("%s:\n got %s\nwant %s", in, got, want)
		}
	}
}

// prepare is renderMd up to markdown: code masked, decks and task refs
// swapped for placeholders, wikilinks, code back.
func prepare(text string) (string, []deckEmbed, []string) {
	text, stash := maskCode(text)
	text, embeds := processDeckEmbeds(nil, text, false)
	text, refs := maskTaskRefs(text)
	return unmaskCode(processWikilinks(nil, text, map[string]bool{"index": true}), stash), embeds, refs
}

// Code is masked: [[…]] in it stays as written.
func TestPrepareMasksCode(t *testing.T) {
	testWiki(t, nil)
	src := "`[[index]]` ``a`[[deck]]`b`` [[index]]\n```\n[[deck]] [[task:abcd1234]]\n```\n````x\n```\n[[index]]\n````\n"
	text, embeds, refs := prepare(src)
	want := "`[[index]]` ``a`[[deck]]`b`` " + `<a href="/page/index" class="wikilink">index</a>` +
		"\n```\n[[deck]] [[task:abcd1234]]\n```\n````x\n```\n[[index]]\n````\n"
	if text != want || embeds != nil || refs != nil {
		t.Errorf("got %q %v %v", text, embeds, refs)
	}
}

func TestPreparePlaceholders(t *testing.T) {
	testWiki(t, nil)
	text, embeds, refs := prepare("[[deck]] [[DECK list=work]] [[task:ABCD-1234]]\u00a0[[deck\u00a0view=x]]")
	if text != "LEAFDECKPLACEHOLDER_0 LEAFDECKPLACEHOLDER_1 \ue0020\ue003\u00a0LEAFDECKPLACEHOLDER_2" {
		t.Errorf("text %q", text)
	}
	if len(embeds) != 3 || !strings.Contains(embeds[1].html, `data-init-list="work"`) ||
		!strings.Contains(embeds[2].html, `id="tasks-app-2"`) || !reflect.DeepEqual(refs, []string{"abcd-1234"}) {
		t.Errorf("embeds %d, refs %q", len(embeds), refs)
	}
}

// A page's own text like our tokens comes back as written.
func TestTextLikeTokens(t *testing.T) {
	testWiki(t, nil)
	for _, in := range []string{"x \ue0005\ue001 y", "`code` \ue0000\ue001 \ue000١\ue001"} {
		if text, _, _ := prepare(in); text != in {
			t.Errorf("got %q", text)
		}
	}
	text, _, refs := prepare("\ue0020\ue003 [[task:ABCD1234]]")
	if text != "\ue0020\ue003 \ue0021\ue003" || !reflect.DeepEqual(refs, []string{"\ue0020\ue003", "abcd1234"}) {
		t.Errorf("got %q %q", text, refs)
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
	meta, body := parseFrontmatter(src)
	want := Frontmatter{Tags: []string{"a", "b c"}, Sources: []string{"[label, with comma](https://x.org)", "[[page|a, b]]"},
		Tldr: "folded lines\npara"}
	if !reflect.DeepEqual(meta, want) || body != "# Body" {
		t.Errorf("got %+v %q", meta, body)
	}
	meta, _ = parseFrontmatter("---\ntldr: |\n    one\n      two\n---\n")
	if meta.Tldr != "one\n  two" {
		t.Errorf("literal tldr %q", meta.Tldr)
	}
	meta, _ = parseFrontmatter("---\ntldr: \"plain\n  goes on\"\n---\n")
	if meta.Tldr != "plain goes on" {
		t.Errorf("plain tldr %q", meta.Tldr)
	}
	// No closing fence: no frontmatter, the text unchanged.
	if meta, body := parseFrontmatter("---\ntags: a\n"); meta.Tags != nil || body != "---\ntags: a\n" {
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
		if got := toSlug(in); got != want {
			t.Errorf("%q: got %q, want %q", in, got, want)
		}
	}
}

// Lines of whitespace only (\r, tabs …) are blank to dedent.
func TestDedent(t *testing.T) {
	for in, want := range map[string]string{
		"    literal\n      more\n ": "literal\n  more\n",
		"    a\n\r\n  b\n \t":        "  a\n\nb\n",
	} {
		if got := dedent(in); got != want {
			t.Errorf("%q: got %q, want %q", in, got, want)
		}
	}
}

// Sections and their offsets count characters, as the editor does.
func TestSectionsCountCharacters(t *testing.T) {
	text := "# Ü\n\nä\n## Zwei\nö\n### Drei\nx\n"
	start, end, ok := sectionCharRange(text, 1)
	if !ok || start != 7 || end != 17 || runeSlice(text, start, end) != "## Zwei\nö\n" {
		t.Fatalf("%d %d %v", start, end, ok)
	}
	merged, _, s, e, ok := spliceSection(&text, contentHash(text), start, end, "## Zwei\nneu")
	if !ok || merged != "# Ü\n\nä\n## Zwei\nneu\n### Drei\nx\n" || s != 7 || e != 19 {
		t.Fatalf("%q %d %d", merged, s, e)
	}
}

// The Nth checkbox is the Nth task line, frontmatter and code aside.
func TestToggleNthCheckbox(t *testing.T) {
	text := "---\n- [ ] not one\n---\n- [ ] a\n```\n- [ ] code\n```\n> - [x] b <!--t:2026-01-01T10:00:00 d:2026-01-02T10:00:00-->\n- [ ]\n"
	got := toggleNthCheckbox(text, 1, true)
	if !strings.Contains(got, "> - [ ] b <!--t:2026-01-01T10:00:00-->\n") {
		t.Errorf("untick: %q", got)
	}
	got = toggleNthCheckbox(text, 0, true)
	if !strings.Contains(got, "- [x] a <!--d:") || toggleNthCheckbox(text, 2, true) != text {
		t.Errorf("tick: %q", got)
	}
}
