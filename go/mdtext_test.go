package main

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/settingsfile"
)

var sharing = &Req{sharing: true}

// prepare is renderMd up to markdown: code masked, task lists and task refs
// swapped for placeholders, wikilinks' pipes escaped, code back.
func prepare(text string) (string, []tasksEmbed, []string) {
	text, stash := mdtext.MaskCode(text)
	text, embeds := processTasksEmbeds(nil, text, "", false)
	text, refs := maskTaskRefs(text)
	return mdtext.UnmaskCode(escapeWikilinkPipes(text), stash), embeds, refs
}

// testWiki points cfg (and settings) at a fresh wiki with these pages (slug → text);
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
	if err := settingsfile.Write(filepath.Join(dir, ".zone", "config"), "noshare = #noshare, secret/\n"); err != nil {
		t.Fatal(err)
	}
	c, s, err := loadConfig(func(k string) string { return map[string]string{"ZONE_DATA": dir}[k] })
	if err != nil {
		t.Fatal(err)
	}
	c.GitEnabled = false
	old, oldSettings := cfg, settings()
	cfg = c
	current.Store(s)
	t.Cleanup(func() { cfg = old; current.Store(oldSettings) })
}
func TestWikilinks(t *testing.T) {
	testWiki(t, nil)
	slugs := map[string]bool{"index": true, "notes/my-page": true, "secret": true, "Odd Name": true}
	cases := map[string]string{
		"[[Notes/My Page]]":          `<a href="/page/notes/my-page" class="wikilink">Notes/My Page</a>`,
		"[[notes/my-page|the page]]": `<a href="/page/notes/my-page" class="wikilink">the page</a>`,
		"[[Missing <b>]]":            `<a href="/page/missing-b" class="wikilink wikilink-missing">Missing &lt;b&gt;</a>`,
		// A label is a link's text: markdown in it renders.
		"[[index#Über **all** `x`]]": `<a href="/page/index#über-all-x" class="wikilink">index › Über <strong>all</strong> <code>x</code></a>`,
		"[[#Here [[x]]]]":            `[[#Here <a href="/page/x" class="wikilink wikilink-missing">x</a>]]`,
		"[[#Section one]]":           `<a href="#section-one" class="wikilink">Section one</a>`,
		"[[secret|words stay]]":      `<span class="wikilink-hidden">words stay</span>`,
		"[[Odd Name]]":               `<a href="/page/Odd%20Name" class="wikilink">Odd Name</a>`,
		"[[it's]]":                   `<a href="/page/it-s" class="wikilink wikilink-missing">it's</a>`,
		"see ![[index]]":             `see !<a href="/page/index" class="wikilink">index</a>`,
		"*[[index]]*":                `<em><a href="/page/index" class="wikilink">index</a></em>`,
		// What isn't text keeps its [[…]]: code, math, an HTML attribute.
		"`[[index]]` $[[x]]$ <span title=\"[[index]]\">t</span>": `<code>[[index]]</code> <span class="math math-inline">[[x]]</span> <span title="[[index]]">t</span>`,
		"[[a\nb]]": "[[a\nb]]",
	}
	for in, want := range cases {
		got := markdown(escapeWikilinkPipes(in), &Env{Wikilink: wikilinker(sharing, slugs, "")}, true)
		if got != want {
			t.Errorf("%s:\n got %s\nwant %s", in, got, want)
		}
	}
	// Without Env.Wikilink, a wikilink is its text.
	if got := markdown("[[index]]", &Env{}, true); got != "[[index]]" {
		t.Errorf("no Wikilink: %s", got)
	}
}

// A table splits its rows at |, not a wikilink's.
func TestWikilinkInTable(t *testing.T) {
	testWiki(t, nil)
	src := "| a | b |\n|---|---|\n| [[index|Home]] | [[x#S|y]] `p\\|q` |\n"
	got := markdown(escapeWikilinkPipes(src), &Env{Wikilink: wikilinker(nil, map[string]bool{"index": true}, "")}, false)
	for _, want := range []string{
		`<td><a href="/page/index" class="wikilink">Home</a></td>`,
		`<td><a href="/page/x#s" class="wikilink wikilink-missing">y</a> <code>p|q</code></td>`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("%s\nwant %s", got, want)
		}
	}
	if strings.Count(got, "<td>") != 2 {
		t.Errorf("cells: %s", got)
	}
}

// Outside a table a wikilink's | stays as written: in raw HTML, an HTML
// block, code, across a line break.
func TestWikilinkPipesOnlyInTables(t *testing.T) {
	testWiki(t, nil)
	env := &Env{Wikilink: wikilinker(nil, map[string]bool{"page": true}, "")}
	for src, want := range map[string]string{
		"<span title=\"[[a|b]]\">t</span>\n": `<span title="[[a|b]]">t</span>`,
		// An HTML block's text: links, as before; its attributes: as written.
		"<div title=\"[[page|x]]\">\n[[page|Label]]\n</div>\n": `<div title="[[page|x]]">` + "\n" + `<a href="/page/page" class="wikilink">Label</a>`,
		"    [[a|b]]\n":                      "<pre><code>[[a|b]]",
		"[[page|\nLabel]]\n\n| x |\n|---|\n": "[[page|\nLabel]]",
	} {
		got := markdown(escapeWikilinkPipes(src), env, false)
		if !strings.Contains(got, want) || strings.Contains(got, `\|`) {
			t.Errorf("%q:\n%s\nwant %s", src, got, want)
		}
	}
}

// A heading's id has a wikilink's label, as [[Page#Section]] makes it.
func TestWikilinkInHeading(t *testing.T) {
	testWiki(t, nil)
	got := markdown("## See [[index|the **home**]]\n", &Env{Wikilink: wikilinker(nil, map[string]bool{"index": true}, "")}, false)
	if want := mdtext.SectionAnchor("See [[index|the **home**]]"); !strings.Contains(got, `id="`+want+`"`) || want != "see-the-home" {
		t.Errorf("%s (want id %s)", got, want)
	}
}

// Code is masked: [[…]] in it stays as written.
func TestPrepareMasksCode(t *testing.T) {
	testWiki(t, nil)
	src := "`[[index]]` ``a`[[tasks:]]`b`` [[index]]\n```\n[[tasks:]] [[task:abcd1234]]\n```\n````x\n```\n[[index]]\n````\n"
	text, embeds, refs := prepare(src)
	want := src
	if text != want || embeds != nil || refs != nil {
		t.Errorf("got %q %v %v", text, embeds, refs)
	}
}
func TestPreparePlaceholders(t *testing.T) {
	testWiki(t, nil)
	text, embeds, refs := prepare("[[tasks:]] [[TASKS: list=work]] [[task:ABCD-1234]]\u00a0[[tasks:\u00a0view=x]]")
	if text != "ZONETASKSPLACEHOLDER_0 ZONETASKSPLACEHOLDER_1 \ue0020\ue003\u00a0ZONETASKSPLACEHOLDER_2" {
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

// The Nth checkbox is the Nth task line, frontmatter and code aside.
// A checkbox line's key stays when it is ticked and unticked (stamps and
// all), and differs from another line's.
func TestTaskLineKeys(t *testing.T) {
	text := "---\n- [ ] not one\n---\n- [ ] a\n```\n- [ ] code\n```\n> - [x] b <!--t:2026-01-01T10:00:00 d:2026-01-02T10:00:00-->\n- [ ] c\n"
	keys := mdtext.TaskLineKeys(text)
	if len(keys) != 3 || keys[0] == keys[1] || keys[1] == keys[2] || len(keys[0]) != 8 {
		t.Fatalf("%v", keys)
	}
	for i := range keys {
		ticked := mdtext.TaskLineKeys(mdtext.ToggleNthCheckbox(text, i, true, now()))
		if !slices.Equal(ticked, keys) {
			t.Errorf("toggling %d: %v, was %v", i, ticked, keys)
		}
	}
}
func TestToggleNthCheckbox(t *testing.T) {
	text := "---\n- [ ] not one\n---\n- [ ] a\n```\n- [ ] code\n```\n> - [x] b <!--t:2026-01-01T10:00:00 d:2026-01-02T10:00:00-->\n- [ ]\n"
	got := mdtext.ToggleNthCheckbox(text, 1, true, now())
	if !strings.Contains(got, "> - [ ] b <!--t:2026-01-01T10:00:00-->\n") {
		t.Errorf("untick: %q", got)
	}
	got = mdtext.ToggleNthCheckbox(text, 0, true, now())
	if !strings.Contains(got, "- [x] a <!--d:") || mdtext.ToggleNthCheckbox(text, 2, true, now()) != text {
		t.Errorf("tick: %q", got)
	}
}
