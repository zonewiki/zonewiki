package main

import (
	"strings"
	"testing"
	"zonewiki/internal/mdtext"
)

// $…$ and $$…$$ keep their TeX as written, escaped, in a .math element.
func TestMath(t *testing.T) {
	html := render(t, "Euler: $e^{i\\pi} + 1 = 0$ and $a_1 * b_2 -- c's$.\n\nShown: $$\\sum_{i=1}^n i$$ inline.\n", Env{})
	expectIn(t, html,
		`<span class="math math-inline">e^{i\pi} + 1 = 0</span>`,
		`<span class="math math-inline">a_1 * b_2 -- c's</span>`,
		`<span class="math math-display">\sum_{i=1}^n i</span>`)
	expectIn(t, render(t, "$x < y & z$\n", Env{}), `<span class="math math-inline">x &lt; y &amp; z</span>`)
}

// Prices, shell variables with spaces and escaped dollars stay text.
func TestMathNotMath(t *testing.T) {
	for _, src := range []string{
		"It costs $5 and $10.", "From $5–$10 a month.", "$ 5 and 6$", "Pay \\$5 or \\$6\\$.",
		"US$ is $", "`$x$` in code", "$$ $$ none", "a $ b",
	} {
		expectNot(t, render(t, src+"\n", Env{}), `class="math`)
	}
	expectIn(t, render(t, "`$x$` in code\n", Env{}), "<code>$x$</code>")
}

// A $$ line opens display math up to a line ending in $$; $$tex$$ alone
// on its line is one; markdown inside it stays as written.
func TestMathBlock(t *testing.T) {
	html := render(t, "Before\n$$\n\\begin{aligned}\na &= *b* \\\\\n# c &< d\n\\end{aligned}\n$$\nAfter\n\n$$x^2$$\n\n  $$ y $$\n", Env{})
	expectIn(t, html,
		"<p>Before</p>\n<div class=\"math math-display\">\\begin{aligned}\na &amp;= *b* \\\\\n# c &amp;&lt; d\n\\end{aligned}\n</div>\n<p>After</p>",
		"<div class=\"math math-display\">x^2</div>",
		"<div class=\"math math-display\"> y </div>")
	expectNot(t, html, "<em>", "<h1")
	// The closing line may hold the last of the TeX; an unclosed block runs to the end.
	expectIn(t, render(t, "$$\na\nb $$\n", Env{}), "<div class=\"math math-display\">a\nb </div>")
	expectIn(t, render(t, "$$\nopen\n", Env{}), "<div class=\"math math-display\">open\n</div>")
	expectIn(t, render(t, "text\n\n$$\n", Env{}), "<div class=\"math math-display\"></div>")
	// A line that only starts with $$ is a paragraph.
	expectIn(t, render(t, "$$100 bills\n", Env{}), "<p>$$100 bills</p>")
}

// One paragraph's inline content (comments) has inline math, no blocks.
func TestMathInline(t *testing.T) {
	expectIn(t, markdown("see $x$", &Env{}, true), `see <span class="math math-inline">x</span>`)
	expectNot(t, markdown("$$\nx\n$$", &Env{}, true), "<div")
}

// A ```math fence is display math, made on the server like $$…$$.
func TestMathFence(t *testing.T) {
	html := render(t, "```math\na < b\n```\n\n```python\nx = 1\n```\n", Env{})
	expectIn(t, html, "<div class=\"math math-display\">a &lt; b\n</div>", `<code class="language-python">`)
	expectNot(t, html, "language-math")
}

// In a table cell \| is the cell's pipe: $\|x\|$ is |x|.
func TestMathInTable(t *testing.T) {
	html := render(t, "| norm | value |\n|---|---|\n| $\\|x\\|$ | 1 |\n", Env{})
	expectIn(t, html, `<td><span class="math math-inline">|x|</span></td>`)
	expectIn(t, render(t, "Outside $\\|x\\|$\n", Env{}), `<span class="math math-inline">\|x\|</span>`)
}

// A heading's id keeps its math's TeX and its code's text, as
// [[Page#Section]] computes it from the source ($a_1 b_2$ and `a_b` are no
// emphasis there either).
func TestMathHeadingID(t *testing.T) {
	for _, h := range []string{"Area $x_1$", "Sum $a_1 + b_2$ here", "Price $5 and $6",
		"Code `$x$` and $y$", "Hello `code` & more", "Run `snake_case_name`", "Pad `` a`b ``"} {
		want := mdtext.SectionAnchor(h)
		expectIn(t, render(t, "## "+h+"\n", Env{}), `<h2 id="`+want+`">`)
	}
	if got := mdtext.SectionAnchor("Sum $a_1 + b_2$ here"); got != "sum-a_1--b_2-here" {
		t.Errorf("mdtext.SectionAnchor = %q", got)
	}
}

// Display math is a fence to the source-side counts: a heading, a task
// item or a setext underline inside it is no section and no checkbox.
func TestMathBlockIsAFence(t *testing.T) {
	text := "# T\n\n## One\n\n$$\n## not a heading\n- [ ] not a task\nx\n---\n$$\n\n$$- [ ] y$$\n\n- [ ] real\n\n## Two\n"
	if got := len(pageSections(text)); got != 3 { // the intro, One, Two
		t.Errorf("%d sections, want 3: %q", got, pageSections(text))
	}
	if got := len(taskItems(text, false)); got != 1 {
		t.Errorf("%d task lines, want 1", got)
	}
	html := render(t, text, Env{})
	if n := strings.Count(html, "<h2"); n != 2 {
		t.Errorf("%d h2, want 2:\n%s", n, html)
	}
	if n := strings.Count(html, `type="checkbox"`); n != 1 {
		t.Errorf("%d checkboxes, want 1:\n%s", n, html)
	}
	// Unclosed: the rest of the page is math, to both.
	if got, want := len(pageSections("## A\n\n$$\n## B\n")), len(pageSections("## A\n")); got != want {
		t.Errorf("unclosed $$: %d sections, want %d", got, want)
	}
}

// Math with a [[ in it is masked as code is, so no [[…]] in it is rewritten.
func TestMathMasked(t *testing.T) {
	masked, stash := mdtext.MaskCode("see $[[e]]$, $$[[f]]$$ and [[g]], \\$[[h]] $ x\n$$\n[[i]]\n$$\n")
	expectNot(t, masked, "[[e]]", "[[f]]", "[[i]]")
	expectIn(t, masked, "[[g]]", "[[h]]")
	if mdtext.UnmaskCode(masked, stash) != "see $[[e]]$, $$[[f]]$$ and [[g]], \\$[[h]] $ x\n$$\n[[i]]\n$$\n" {
		t.Error("unmask doesn't give the text back")
	}
	// Math without [[ needs no mask, and a link's own math stays in the link.
	if masked, _ := mdtext.MaskCode("$x$ and [[h#Area $x_1$]]"); masked != "$x$ and [[h#Area $x_1$]]" {
		t.Errorf("masked %q", masked)
	}
	if got := mdtext.InlineMathSpans("`$a$` $b$ \\$c$ $d$"); len(got) != 2 || got[0] != [3]int{6, 9, 1} {
		t.Errorf("mdtext.InlineMathSpans = %v", got)
	}
}
