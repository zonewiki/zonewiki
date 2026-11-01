package main

import (
	"fmt"
	"strings"
	"sync"
	"testing"
)

func render(t *testing.T, src string, env Env) string {
	t.Helper()
	return markdown(src, &env, false)
}

func expectIn(t *testing.T, html string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(html, w) {
			t.Errorf("missing %q in\n%s", w, html)
		}
	}
}

func expectNot(t *testing.T, html string, unwanted ...string) {
	t.Helper()
	for _, u := range unwanted {
		if strings.Contains(html, u) {
			t.Errorf("unexpected %q in\n%s", u, html)
		}
	}
}

// The lines mdtext._TASK_ITEM_RE counts get a checkbox, no others: the
// Nth checkbox ticks the Nth of them.
func TestCheckboxes(t *testing.T) {
	html := render(t, "- [ ] open\n- [x] done\n- [X] Done\n- [ ]\n- [ ]x\n- plain\n\n1. [ ] numbered\n\n- [ ] loose\n\n- [x]\ttab\n", Env{})
	if n := strings.Count(html, `class="task-list-item-checkbox"`); n != 6 { // as mdtext._task_item_lines counts
		t.Errorf("%d checkboxes, want 6:\n%s", n, html)
	}
	expectIn(t, html,
		`<li class="task-list-item"><input class="task-list-item-checkbox" type="checkbox" /> open</li>`,
		`<input class="task-list-item-checkbox" type="checkbox" checked="checked" /> done`,
		`<li>[ ]</li>`, `<li>[ ]x</li>`, `<li>plain</li>`,
		`<p><input class="task-list-item-checkbox" type="checkbox" /> loose</p>`)
	expectNot(t, html, "disabled")
}

func TestAlerts(t *testing.T) {
	html := render(t, "> [!NOTE]\n> Body *here*.\n\n> [!warning]\n> Lower case.\n\n> [!TIP] not alone\n\n> [!NOPE]\n> x\n", Env{})
	expectIn(t, html,
		"<div class=\"markdown-alert markdown-alert-note\">\n<p class=\"markdown-alert-title\">Note</p>\n<p>Body <em>here</em>.</p>\n</div>",
		`<div class="markdown-alert markdown-alert-warning">`, `<p class="markdown-alert-title">Warning</p>`,
		"<blockquote>\n<p>[!TIP] not alone</p>", "<p>[!NOPE]\nx</p>")
}

// Heading ids are what mdtext.section_anchor computes for [[Page#Section]]:
// h1–h3 only, unique with -1, -2 …
func TestHeadingIDs(t *testing.T) {
	html := render(t, "# Über *alles*\n## Hello `code` & more ![x](i.png)\n## Hello `code` & more\n#### Deep\n", Env{})
	expectIn(t, html, `<h1 id="über-alles">`, `<h2 id="hello-code--more">`, `<h2 id="hello-code--more-1">`, "<h4>Deep</h4>")
}

func TestSections(t *testing.T) {
	src := "# Title\n## One\n### Two\n#### Not one\n## Three\n"
	html := render(t, src, Env{EditSlug: "notes/my page", SectionChips: map[string]string{"two": "<details>☑ 1</details>"}})
	expectIn(t, html,
		`<div class="section-header section-header-h2"><h2 id="one">One</h2><a href="/edit/notes/my%20page?section=1" class="section-edit-btn" title="Edit section">✎</a></div>`,
		`<h3 id="two">Two</h3><details>☑ 1</details><a href="/edit/notes/my%20page?section=2"`,
		`?section=3"`, "<h1 id=\"title\">Title</h1>\n", "<h4>Not one</h4>")
	// Without edit_slug (a preview): no ✎, the chips still.
	html = render(t, src, Env{SectionChips: map[string]string{"two": "<details>☑ 1</details>"}})
	expectIn(t, html, "<h3 id=\"two\">Two</h3><details>☑ 1</details>\n")
	expectNot(t, html, "section-header", "✎")
}

func TestImages(t *testing.T) {
	html := render(t, "![alt|300](x.png)\n\nText ![i|20](y.png) more\n\n[![a](b.png)](c)\n", Env{})
	expectIn(t, html,
		`<p class="img-block"><img src="x.png" alt="alt" style="max-width:100%;width:300px" /></p>`,
		`<p>Text <img src="y.png" alt="i" style="max-width:100%;width:20px" /> more</p>`,
		`<p><a href="c"><img src="b.png" alt="a" /></a></p>`)
}

func TestExternalLinks(t *testing.T) {
	html := render(t, "[a](https://example.com) [b](/page/x) [c](http://leaf.local:5000/page/y) www.example.org http://nas:5000/x <https://auto.link> me@example.com\n",
		Env{Host: "leaf.local:5000"})
	expectIn(t, html,
		`<a href="https://example.com" target="_blank" rel="noopener noreferrer">a</a>`,
		`<a href="/page/x">b</a>`, `<a href="http://leaf.local:5000/page/y">c</a>`,
		`<a href="http://www.example.org" target="_blank" rel="noopener noreferrer">www.example.org</a>`,
		`<a href="http://nas:5000/x" target="_blank" rel="noopener noreferrer">http://nas:5000/x</a>`,
		`<a href="https://auto.link" target="_blank" rel="noopener noreferrer">https://auto.link</a>`,
		`<a href="mailto:me@example.com">me@example.com</a>`)
}

func TestTaskDates(t *testing.T) {
	html := render(t, "- [x] done <!--t:2026-06-12T18:35:00 d:2026-06-13T09:00:00-->\n- [x] old <!--d:2026-06-13T09:00:00-->\n\n`<!--d:2026-06-13T09:00:00-->`\n", Env{})
	expectIn(t, html,
		`<span class="task-date task-date-created" title="Created 2026-06-12T18:35:00">2026-06-12</span><span class="task-date task-date-done" title="Done 2026-06-13T09:00:00">2026-06-13</span>`,
		`old <span class="task-date task-date-done" title="Done 2026-06-13T09:00:00">2026-06-13</span>`,
		`<code>&lt;!--d:2026-06-13T09:00:00--&gt;</code>`)
}

// The rest is goldmark's: tables, strikethrough, footnotes, deflists, raw HTML.
func TestGoldmarkFeatures(t *testing.T) {
	html := render(t, "| a | b |\n|---|:-:|\n| 1 | ~~2~~ |\n\nA note[^1].\n\n[^1]: The note.\n\nTerm\n: Def\n\n<div class=\"x\">raw</div>\n", Env{})
	expectIn(t, html, "<table>", "<del>2</del>", `class="footnote-ref"`, "The note.", "<dt>Term</dt>", `<div class="x">raw</div>`)
}

// markdown-it's smartquotes and replacements: curly quotes and dashes, not « ».
func TestTypographer(t *testing.T) {
	html := render(t, "\"Quoted\" it's -- a --- b... <<x>>\n", Env{})
	expectIn(t, html, "&ldquo;Quoted&rdquo;", "it&rsquo;s", "&ndash;", "&mdash;", "&hellip;", "&lt;&lt;x&gt;&gt;")
}

// Links markdown-it refuses are their text: javascript:, vbscript:, data:
// but an image's.
func TestUnsafeLinks(t *testing.T) {
	html := render(t, "[a](javascript:alert(1)) [b](VBScript:x) [c](data:text/html,x) [d](data:image/png;base64,AA) [e](file:///x)\n", Env{})
	expectIn(t, html, "<p>a b c ", `<a href="data:image/png;base64,AA"`, `<a href="file:///x"`)
	expectNot(t, html, "javascript", "VBScript", "text/html")
}

// Comments and sources render inline: no block syntax.
func TestInline(t *testing.T) {
	if got := markdown("# not a heading *but em*", &Env{}, true); got != "# not a heading <em>but em</em>" {
		t.Errorf("got %q", got)
	}
	if got := markdown("- not a list", &Env{}, true); got != "- not a list" {
		t.Errorf("got %q", got)
	}
}

// A page's raw HTML can't make or submit a form, nor refresh or re-base
// the page: those tags show as text; the rest stays as written, and Zone's
// own checkboxes are inputs still.
func TestRawHTMLDefused(t *testing.T) {
	html := render(t, "<form method=\"post\" action=\"/delete/x\">\n<button>Go</button>\n</form>\n\n"+
		"Inline <button formaction=\"/delete/y\" form=\"page-menu\">click</button> and <INPUT type=submit>.\n\n"+
		"<meta http-equiv=\"refresh\" content=\"0; url=https://evil.example\">\n\n"+
		"<div class=\"note\">kept <b>bold</b> <formula>x</formula></div>\n\n- [ ] mine\n", Env{})
	expectNot(t, html, "<form ", "<button", "<INPUT", "<meta", "</form>", "</button>")
	expectIn(t, html, "&lt;form method", "&lt;button>Go&lt;/button>", "&lt;/form>", "&lt;button formaction",
		"&lt;INPUT type=submit>", "&lt;meta http-equiv", `<div class="note">kept <b>bold</b> <formula>x</formula></div>`,
		`<input class="task-list-item-checkbox" type="checkbox" />`)
	if notes := markdown("a <form action=/x><button>b</button></form>", &Env{}, true); strings.Contains(notes, "<form ") {
		t.Errorf("a task's notes (inline): %s", notes)
	}
}

// Renders at once each keep their own Env and section numbers, though
// they share pooled goldmark instances.
func TestConcurrentRendersKeepTheirEnv(t *testing.T) {
	var wg sync.WaitGroup
	errs := make(chan string, 64)
	for i := range 64 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			slug := fmt.Sprintf("p%d", i)
			html := markdown("## One\n\ntext\n\n## Two\n", &Env{EditSlug: slug}, false)
			for _, want := range []string{"/edit/" + slug + "?section=1", "/edit/" + slug + "?section=2"} {
				if !strings.Contains(html, want) {
					errs <- fmt.Sprintf("%s: no %s in %s", slug, want, html)
				}
			}
			if strings.Count(html, "?section=") != 2 {
				errs <- slug + ": " + html
			}
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		t.Error(e)
	}
}
