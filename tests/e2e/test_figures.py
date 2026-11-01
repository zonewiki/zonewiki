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
    page.goto("/tasks?view=open")
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
