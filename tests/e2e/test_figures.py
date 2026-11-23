"""Math (KaTeX) and Mermaid diagrams, rendered in the browser from the
vendored copies (js/figures.js); the fixtures fail a test on a request to
another host or a Content-Security-Policy violation."""

from playwright.sync_api import expect

FIGURES = """# Figures

Euler: $e^{i\\pi} + 1 = 0$, and it costs $5 or $10.

$$
\\int_0^1 x^2\\,dx = \\frac{1}{3}
$$

```math
a^2 + b^2 = c^2
```

```mermaid
flowchart LR
  Write --> Commit --> Search
```

```python
print("still highlighted")
```
"""


def test_math_and_diagrams_render(page, wiki):
    wiki.write("figures", FIGURES)
    page.goto("/page/figures")
    article = page.locator("article.page-content")
    expect(article.locator(".math-inline .katex")).to_have_count(1)
    expect(article.locator(".math-display .katex-display")).to_have_count(2)   # $$ and ```math
    expect(article.locator(".math-inline").first).to_have_attribute("data-tex", "e^{i\\pi} + 1 = 0")
    expect(article).to_contain_text("it costs $5 or $10.")                    # prices stay text
    svg = article.locator(".mermaid-diagram svg")
    expect(svg).to_have_count(1)
    expect(svg).to_contain_text("Commit")
    expect(article.locator("code.language-mermaid")).to_have_count(0)
    expect(article.locator("pre code.language-python.hljs")).to_have_count(1)
    # KaTeX's fonts come from the vendored copy.
    assert page.evaluate("document.fonts.check('16px KaTeX_Main')")


def test_a_page_without_figures_loads_neither_library(page, wiki):
    wiki.write("plain", "# Plain\n\nJust text, $5 and $6.\n\n```js\nlet a = 1;\n```\n")
    loaded = []
    page.on("request", lambda r: loaded.append(r.url) if "/vendor/katex/" in r.url or "/vendor/mermaid/" in r.url else None)
    page.goto("/page/plain")
    expect(page.locator("pre code.hljs")).to_have_count(1)
    page.wait_for_load_state("networkidle")
    assert loaded == []


def test_a_broken_diagram_keeps_its_source(page, wiki):
    wiki.write("broken", "# Broken\n\n```mermaid\nflowchart LR\n  A --> \n  ((\n```\n\n$\\frac{1}{$\n")
    page.goto("/page/broken")
    article = page.locator("article.page-content")
    expect(article.locator(".mermaid-error")).to_contain_text("Diagram:")
    expect(article.locator("pre code.language-mermaid")).to_contain_text("flowchart LR")
    # KaTeX shows bad TeX as it is, in its error colour.
    expect(article.locator(".math .katex-error")).to_have_count(1)


def test_editor_preview_renders_figures(page, wiki):
    page.goto("/edit/preview-figures")
    page.keyboard.press("Control+End")
    page.keyboard.type("Area $\\pi r^2$\n\n```mermaid\nflowchart TD\n  A --> B\n```\n")
    preview = page.locator("#editor-preview")
    expect(preview.locator(".math-inline .katex")).to_have_count(1)
    expect(preview.locator(".mermaid-diagram svg")).to_have_count(1)


def test_task_notes_render_math(page, wiki):
    page.request.post("/api/tasks", data={"title": "Sums", "description": "Total: $\\sum x_i$"})
    page.goto("/tasks?view=open&group=none")
    desc = page.locator(".task-item").filter(has=page.get_by_text("Sums", exact=True)).locator(".task-desc")
    expect(desc.locator(".math-inline .katex")).to_have_count(1)


def test_presentation_shows_figures(page, wiki):
    wiki.write("deck", "# Deck\n\n## Formula\n\n$$\nE = mc^2\n$$\n\n## Flow\n\n```mermaid\nflowchart LR\n  A --> B\n```\n")
    page.goto("/page/deck")
    expect(page.locator("article .mermaid-diagram svg")).to_have_count(1)
    page.keyboard.press("p")
    overlay = page.locator(".presentation-overlay")
    expect(overlay).to_be_visible()
    page.keyboard.press("ArrowRight")
    expect(overlay.locator(".katex-display")).to_be_visible()
    page.keyboard.press("ArrowRight")
    expect(overlay.locator(".mermaid-diagram svg")).to_be_visible()


MATH_BOXES = "# Boxes\n\n- [ ] first\n\n$$\n- [ ] not a box\n## not a heading\n$$\n\n- [ ] second\n"


def test_display_math_is_no_checkbox_or_heading(page, wiki):
    # The preview's checkboxes and the page's agree with the source: a box
    # inside $$ is none, so the second box ticks "second".
    wiki.write("boxes", MATH_BOXES)
    page.goto("/edit/boxes")
    boxes = page.locator("#editor-preview .task-list-item-checkbox")
    expect(boxes).to_have_count(2)
    boxes.nth(1).click()
    expect(page.locator("#editor-textarea")).to_have_value(MATH_BOXES.replace("- [ ] second", "- [x] second"))
    # The page view's (the server counts them).
    wiki.write("boxes2", MATH_BOXES)
    page.goto("/page/boxes2")
    expect(page.locator(".page-content h2")).to_have_count(0)
    box = page.locator(".page-content .task-list-item-checkbox").nth(1)
    box.click()
    expect(box).to_be_checked()
    page.wait_for_load_state("networkidle")
    assert wiki.read("boxes2").startswith(MATH_BOXES.replace("- [ ] second", "- [x] second").rstrip("\n"))


def test_preview_marks_the_block_after_display_math(page, wiki):
    text = "# Doc\n\nIntro\n\n$$\na\n\nb\n$$\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\nAfter para\n"
    wiki.write("doc", text)
    page.goto("/edit/doc")
    preview = page.locator("#editor-preview")
    expect(preview.locator(".mermaid-diagram svg")).to_have_count(1)
    page.locator("#editor-textarea").evaluate(
        "(t, at) => { t.focus(); t.setSelectionRange(at, at); document.dispatchEvent(new Event('selectionchange')); }",
        text.index("After para"))
    expect(preview.locator(".preview-hl")).to_have_text("After para")


def test_math_in_the_side_toc(new_page, wiki):
    wiki.write("toc", "# Toc\n\n## Energy $E=mc^2$\n\ntext\n\n## Two\n\ntext\n\n## Three\n\ntext\n")
    page = new_page(viewport={"width": 1600, "height": 900})
    page.goto("/page/toc")
    expect(page.locator(".rail-toc .math .katex")).to_have_count(1)
    expect(page.locator("article h2 .katex")).to_have_count(1)


def test_a_section_link_stays_in_view_as_figures_render(page, wiki):
    filler = "Some text that fills the page.\n\n" * 6
    wiki.write("long", "# Long\n\n```mermaid\nflowchart TD\n  A --> B --> C --> D --> E\n```\n\n$$\n\\sum_{i=1}^n \\frac{1}{i^2}\n$$\n\n"
               + filler + "## Target\n\n" + filler * 6)
    page.goto("/page/long#target")
    expect(page.locator("article .mermaid-diagram svg")).to_have_count(1)
    expect(page.locator("article .katex-display")).to_have_count(1)
    top = page.locator("#target").evaluate("h => h.getBoundingClientRect().top")
    assert 0 <= top < 200, top


def test_task_comments_render_math(page, wiki):
    tid = page.request.post("/api/tasks", data={"title": "Proof"}).json()["id"]
    page.request.post(f"/api/tasks/{tid}/comments", data={"text": "since $a^2 \\ge 0$"})
    page.goto("/tasks?view=open&group=none")
    card = page.locator(".task-item").filter(has=page.get_by_text("Proof", exact=True))
    expect(card.locator(".task-card-comments .katex")).to_have_count(1)


def test_a_diagram_makes_no_form(page, wiki):
    # Mermaid's labels are HTML that strict cleans with DOMPurify's defaults,
    # which keep form controls: a form in a label posted to Zonewiki as the
    # reader with one click. A diagram's own %%{init}%% can't put them back,
    # and its labels keep the HTML they may have (bold, line breaks).
    wiki.write("victim", "# Victim\n\nkeep me\n")
    wiki.write("flow", "# Flow\n\n```mermaid\n"
               '%%{init: {"dompurifyConfig": {"ADD_TAGS": ["form", "button"]}, "securityLevel": "loose"}}%%\n'
               "flowchart TD\n"
               "  A[\"<form method='post' action='/delete/victim'><button id='go'>Approve</button></form>\"]"
               " --> B[\"<b>bold</b><br>two <input name='x' formaction='/delete/victim'>"
               " <span id='tasks-app' name='n'>s</span>\"]\n"
               "  B -- \"<button id='go2'>edge</button>\" --> C[\"<iframe srcdoc='x'></iframe>c\"]\n"
               "```\n")
    page.goto("/page/flow")
    svg = page.locator("article.page-content .mermaid-diagram svg")
    expect(svg).to_have_count(1)
    expect(svg).to_contain_text("Approve")
    expect(svg.locator("b")).to_have_text("bold")
    expect(svg.locator("form, input, button, iframe, [action], [formaction], [srcdoc]")).to_have_count(0)
    expect(page.locator("#go, #go2, #tasks-app, [name='n']")).to_have_count(0)
    assert (wiki.root / "victim.md").exists()
