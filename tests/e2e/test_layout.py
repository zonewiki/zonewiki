"""Layout checks that need a real rendering engine, plus a screenshot set.

The overflow test is the mechanical part of "does it render correctly on a
phone": nothing may push the page wider than the viewport. The screenshot
test writes PNGs to tests/e2e/_screenshots/ for a human (or an agent) to look at;
it asserts only that they were produced.
"""

import pytest
from playwright.sync_api import expect

from conftest import DESKTOP, PHONE, SCREENSHOT_DIR

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
                         ("Planned today", {"focus_date": _today()})]:
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
    ("deck-focus", "/page/deck", "focus"),
    ("deck-plan", "/page/deck", "due"),
    ("deck-backlog", "/page/deck", "open"),
]


def open_view(pg, url, tab):
    pg.goto(url)
    if tab:
        pg.locator("#tasks-tabs").select_option(tab)
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
