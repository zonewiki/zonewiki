package main

import "testing"

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
