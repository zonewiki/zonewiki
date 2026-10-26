package main

import (
	"strings"
	"testing"
)

func render(t *testing.T, src string, env Env) string {
	t.Helper()
	out, err := Render(src, &env)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func expect(t *testing.T, html string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(html, w) {
			t.Errorf("missing %q in\n%s", w, html)
		}
	}
}

func refuse(t *testing.T, html string, unwanted ...string) {
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
	expect(t, html,
		`<li class="task-list-item"><input class="task-list-item-checkbox" type="checkbox" /> open</li>`,
		`<input class="task-list-item-checkbox" type="checkbox" checked="checked" /> done`,
		`<li>[ ]</li>`, `<li>[ ]x</li>`, `<li>plain</li>`,
		`<p><input class="task-list-item-checkbox" type="checkbox" /> loose</p>`)
	refuse(t, html, "disabled")
}

func TestAlerts(t *testing.T) {
	html := render(t, "> [!NOTE]\n> Body *here*.\n\n> [!warning]\n> Lower case.\n\n> [!TIP] not alone\n\n> [!NOPE]\n> x\n", Env{})
	expect(t, html,
		"<div class=\"markdown-alert markdown-alert-note\">\n<p class=\"markdown-alert-title\">Note</p>\n<p>Body <em>here</em>.</p>\n</div>",
		`<div class="markdown-alert markdown-alert-warning">`, `<p class="markdown-alert-title">Warning</p>`,
		"<blockquote>\n<p>[!TIP] not alone</p>", "<p>[!NOPE]\nx</p>")
}

// Heading ids are what mdtext.section_anchor computes for [[Page#Section]]:
// h1–h3 only, unique with -1, -2 …
func TestHeadingIDs(t *testing.T) {
	html := render(t, "# Über *alles*\n## Hello `code` & more ![x](i.png)\n## Hello `code` & more\n#### Deep\n", Env{})
	expect(t, html, `<h1 id="über-alles">`, `<h2 id="hello-code--more">`, `<h2 id="hello-code--more-1">`, "<h4>Deep</h4>")
}

func TestSections(t *testing.T) {
	src := "# Title\n## One\n### Two\n#### Not one\n## Three\n"
	html := render(t, src, Env{EditSlug: "notes/my page", SectionChips: map[string]string{"two": "<details>☑ 1</details>"}})
	expect(t, html,
		`<div class="section-header section-header-h2"><h2 id="one">One</h2><a href="/edit/notes/my%20page?section=1" class="section-edit-btn" title="Edit section">✎</a></div>`,
		`<h3 id="two">Two</h3><details>☑ 1</details><a href="/edit/notes/my%20page?section=2"`,
		`?section=3"`, "<h1 id=\"title\">Title</h1>\n", "<h4>Not one</h4>")
	// Without edit_slug (a preview): no ✎, the chips still.
	html = render(t, src, Env{SectionChips: map[string]string{"two": "<details>☑ 1</details>"}})
	expect(t, html, "<h3 id=\"two\">Two</h3><details>☑ 1</details>\n")
	refuse(t, html, "section-header", "✎")
}

func TestImages(t *testing.T) {
	html := render(t, "![alt|300](x.png)\n\nText ![i|20](y.png) more\n\n[![a](b.png)](c)\n", Env{})
	expect(t, html,
		`<p class="img-block"><img src="x.png" alt="alt" style="max-width:100%;width:300px" /></p>`,
		`<p>Text <img src="y.png" alt="i" style="max-width:100%;width:20px" /> more</p>`,
		`<p><a href="c"><img src="b.png" alt="a" /></a></p>`)
}

func TestExternalLinks(t *testing.T) {
	html := render(t, "[a](https://example.com) [b](/page/x) [c](http://leaf.local:5000/page/y) www.example.org http://nas:5000/x <https://auto.link> me@example.com\n",
		Env{Host: "leaf.local:5000"})
	expect(t, html,
		`<a href="https://example.com" target="_blank" rel="noopener noreferrer">a</a>`,
		`<a href="/page/x">b</a>`, `<a href="http://leaf.local:5000/page/y">c</a>`,
		`<a href="http://www.example.org" target="_blank" rel="noopener noreferrer">www.example.org</a>`,
		`<a href="http://nas:5000/x" target="_blank" rel="noopener noreferrer">http://nas:5000/x</a>`,
		`<a href="https://auto.link" target="_blank" rel="noopener noreferrer">https://auto.link</a>`,
		`<a href="mailto:me@example.com">me@example.com</a>`)
}

func TestTaskDates(t *testing.T) {
	html := render(t, "- [x] done <!--t:2026-06-12T18:35:00 d:2026-06-13T09:00:00-->\n- [x] old <!--d:2026-06-13T09:00:00-->\n\n`<!--d:2026-06-13T09:00:00-->`\n", Env{})
	expect(t, html,
		`<span class="task-date task-date-created" title="Created 2026-06-12T18:35:00">2026-06-12</span><span class="task-date task-date-done" title="Done 2026-06-13T09:00:00">2026-06-13</span>`,
		`old <span class="task-date task-date-done" title="Done 2026-06-13T09:00:00">2026-06-13</span>`,
		`<code>&lt;!--d:2026-06-13T09:00:00--&gt;</code>`)
}

// The rest is goldmark's: tables, strikethrough, footnotes, deflists, raw HTML.
func TestGoldmarkFeatures(t *testing.T) {
	html := render(t, "| a | b |\n|---|:-:|\n| 1 | ~~2~~ |\n\nA note[^1].\n\n[^1]: The note.\n\nTerm\n: Def\n\n<div class=\"x\">raw</div>\n", Env{})
	expect(t, html, "<table>", "<del>2</del>", `class="footnote-ref"`, "The note.", "<dt>Term</dt>", `<div class="x">raw</div>`)
}
