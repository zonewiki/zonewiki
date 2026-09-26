"""Layout checks that need a real rendering engine, plus a screenshot set.

The overflow test is the mechanical part of "does it render correctly on a
phone": nothing may push the page wider than the viewport. The screenshot
test writes PNGs to tests/e2e/_screenshots/ for a human (or an agent) to look at;
it asserts only that they were produced.
"""

import re

import pytest
from playwright.sync_api import expect

from conftest import DESKTOP, PHONE, SCREENSHOT_DIR, open_tab

# A page that stresses every wide element: long unbroken words/URLs, a wide
# table, a long code line, an oversized image, and a deep list.
STRESS = """---
tags: layout, stress-test, phone
tldr: A page full of things that like to overflow narrow screens.
---
# Layout Stress

## Long words

Supercalifragilisticexpialidocious_and_then_some_more_unbroken_text_here
and a bare URL https://example.com/a/really/long/path/that/never/breaks/anywhere/at/all

## Table

| Column one | Column two | Column three | Column four | Column five | Column six |
|------------|------------|--------------|-------------|-------------|------------|
| aaaaaaaaaa | bbbbbbbbbb | cccccccccccc | ddddddddddd | eeeeeeeeeee | ffffffffff |

## Code

```python
def a_function_with_a_very_long_signature(argument_one, argument_two, argument_three, argument_four):
    return argument_one
```

## Image

![wide|2000](/public/missing.png)

## Lists

- one
  - two
    - three
      - four

- [ ] a task
- [x] a done task

See [[index]] and [[missing page]].
"""


def seed(wiki, page):
    wiki.write("stress", STRESS)
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    for title, extra in [("Short task", {}),
                         ("A task with a rather long title that should wrap on narrow screens",
                          {"tags": ["work", "layout"]}),
                         ("Planned today", {"start_date": _today()})]:
        page.request.post("/api/deck", data={"title": title, **extra})


def _today():
    from datetime import date
    return date.today().isoformat()


def horizontal_overflow(page) -> int:
    """How many CSS px the document is wider than the viewport (0 = fits)."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


VIEWS = [
    ("page", "/page/stress", None),
    ("editor", "/edit/stress", None),
    ("deck-all", "/page/deck", "open"),
    ("deck-plan", "/page/deck", "due"),
    ("deck-focus", "/page/deck", "focus"),
]


def open_view(pg, url, tab):
    pg.goto(url)
    if tab:
        open_tab(pg, tab)
        expect(pg.locator("#tasks-list > li").first).to_be_visible()


@pytest.mark.parametrize("name,url,tab", VIEWS, ids=[v[0] for v in VIEWS])
def test_no_horizontal_scroll_on_phone(new_page, wiki, name, url, tab):
    pg = new_page(viewport=PHONE)
    seed(wiki, pg)
    open_view(pg, url, tab)
    assert horizontal_overflow(pg) <= 0, f"{name} is wider than a {PHONE['width']}px phone"


def test_mobile_preview_button_only_on_phone(new_page, wiki):
    phone = new_page(viewport=PHONE)
    phone.goto("/edit/index")
    expect(phone.locator("#mobile-preview-btn")).to_be_visible()
    desktop = new_page(viewport=DESKTOP)
    desktop.goto("/edit/index")
    expect(desktop.locator("#mobile-preview-btn")).to_be_hidden()


def test_tldr_toggle_reveals_text_below_tags(page, wiki):
    wiki.write("stress", STRESS)
    page.goto("/page/stress")
    tldr = page.locator(".page-tldr")
    label = page.locator("label.tldr-label")
    expect(tldr).to_be_hidden()
    before = label.bounding_box()
    label.click()
    expect(tldr).to_be_visible()
    after = label.bounding_box()
    assert (before["x"], before["y"]) == (after["x"], after["y"]), "label must not move"
    assert tldr.bounding_box()["y"] > after["y"] + after["height"] - 1, "text goes below the row"


def test_screenshots(new_page, wiki):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    shots = []
    for scheme in ("light", "dark"):
        for vp_name, vp in (("phone", PHONE), ("desktop", DESKTOP)):
            pg = new_page(viewport=vp, color_scheme=scheme)
            if not shots:
                seed(wiki, pg)
            for name, url, tab in VIEWS:
                open_view(pg, url, tab)
                path = SCREENSHOT_DIR / f"{name}-{vp_name}-{scheme}.png"
                pg.screenshot(path=path, full_page=True)
                shots.append(path)
    assert all(p.stat().st_size > 0 for p in shots)


# Gap above the capital letters vs gap below the baseline, per view tab.
# Uses the font's own metrics, so it holds for whatever font the browser picks.
_TAB_TEXT_GAPS = """() => [...document.querySelectorAll('#tasks-tabs button')].map(b => {
  const r = b.getBoundingClientRect(), cs = getComputedStyle(b);
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const m = ctx.measureText('H');
  const range = document.createRange(); range.selectNodeContents(b);
  const baseline = range.getBoundingClientRect().top + m.fontBoundingBoxAscent;
  return [b.textContent, baseline - m.actualBoundingBoxAscent - r.top, r.bottom - baseline];
})"""


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_view_tab_labels_vertically_centred(new_page, wiki, viewport):
    # Regression: line-height 1.2 put all spare leading below the text, so
    # the labels sat visibly high (caps 1.3px above centre).
    pg = new_page(viewport=viewport)
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck")
    for label, above, below in pg.evaluate(_TAB_TEXT_GAPS):
        assert abs(above - below) <= 1.0, f"{label}: {above:.1f}px above vs {below:.1f}px below"


# ── Backlinks chip ("← N" at the top right) ──────────────────────────────────

def _backlinked_page(wiki):
    wiki.write("target", "# Target\n\n" + "Filler paragraph.\n\n" * 80)
    wiki.write("alpha", "# Alpha\n\nSee [[target]].\n")
    wiki.write("beta", "# Beta\n\nSee [[target]].\n")


def test_backlinks_chip_tap_opens_list_without_scrolling(new_page, wiki):
    # Regression: phones have no hover, so tapping the chip only ever followed
    # its #backlinks href and scrolled to the bottom of the page.
    from conftest import TOUCH_PHONE
    _backlinked_page(wiki)
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/target")
    chip, tip = phone.locator(".bl-count"), phone.locator(".bl-tooltip")
    expect(chip).to_have_text("← 2")
    expect(tip).to_be_hidden()
    chip.tap()
    expect(tip).to_be_visible()
    assert phone.evaluate("scrollY") == 0
    # Links are finger-sized.
    assert all(a.bounding_box()["height"] >= 36 for a in tip.locator("a").all())
    chip.tap()                                  # a second tap closes it
    expect(tip).to_be_hidden()
    chip.tap()
    phone.locator("h1").first.tap()             # so does a tap elsewhere
    expect(tip).to_be_hidden()
    chip.tap()
    tip.get_by_role("link", name="Beta").tap()  # and its links navigate
    expect(phone).to_have_url(re.compile(r"/page/beta$"))


def test_backlinks_chip_desktop_hover_and_click(page, wiki):
    _backlinked_page(wiki)
    page.goto("/page/target")
    tip = page.locator(".bl-tooltip")
    expect(tip).to_be_hidden()
    page.locator(".bl-count").hover()
    expect(tip).to_be_visible()
    page.locator(".bl-count").click()           # desktop click still jumps to the section
    expect(page).to_have_url(re.compile(r"#backlinks$"))
    page.wait_for_function("scrollY > 0")
