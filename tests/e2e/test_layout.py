"""Layout checks that need a real rendering engine, plus a screenshot set.

The overflow test is the mechanical part of "does it render correctly on a
phone": nothing may push the page wider than the viewport. The screenshot
test writes PNGs to tests/e2e/_screenshots/ for a human (or an agent) to look at;
it asserts only that they were produced.
"""

import re

import pytest
from playwright.sync_api import expect

from conftest import DESKTOP, PHONE, SCREENSHOT_DIR, TOUCH_PHONE, open_tab

# A page that stresses every wide element: long unbroken words/URLs, a wide
# table, a long code line, an oversized image, and a deep list.
STRESS = """---
tags: layout, stress-test, phone
tldr: A page full of things that like to overflow narrow screens.
---
# Layout Stress

> [!WARNING]
> A callout with a https://example.com/very/long/bare/url/that/must/wrap/somewhere link.

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
    pg.goto("/page/deck?view=open")
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


DECK_HEADER_PARTS = ["#tasks-tabs", "#task-filter-search", "#task-add-btn"]


def deck_header_rows(pg) -> int:
    """Number of distinct lines the Open tab's header controls sit on."""
    centres = sorted(
        (lambda b: b["y"] + b["height"] / 2)(pg.locator(sel).bounding_box())
        for sel in DECK_HEADER_PARTS)
    return 1 + sum(1 for a, b in zip(centres, centres[1:]) if b - a > 8)


@pytest.mark.parametrize("ctx,rows", [({"viewport": DESKTOP}, 1), (TOUCH_PHONE, 2)],
                         ids=["desktop", "phone"])
def test_deck_all_tab_header_is_compact(new_page, wiki, ctx, rows):
    # Tabs + search + New: one line on desktop, two on a
    # phone (it used to be two and five).
    pg = new_page(**ctx)
    seed(wiki, pg)
    open_view(pg, "/page/deck", "open")
    assert deck_header_rows(pg) == rows
    assert pg.locator("#task-filter-search").bounding_box()["width"] >= 100
    assert horizontal_overflow(pg) <= 0


def test_long_breadcrumb_trail_fits_on_phone(new_page, wiki):
    # Regression: the navigation trail (up to 6 visited pages) was one flex
    # row that neither wrapped nor shrank, so a few long titles made the page
    # wider than the phone. The full-screen deck edit modal grew with it and
    # its Save/Cancel bar ended up off-screen.
    pg = new_page(**TOUCH_PHONE)
    pg.request.post("/api/deck", data={"title": "Water plants", "start_date": _today()})
    # A full trail (5 pages before the current one) of titles with long words.
    for i in range(5):
        wiki.write(f"long-{i}", f"# Infrastructure-Troubleshooting{i} Kubernetes\n")
        pg.goto(f"/page/long-{i}")
    pg.goto("/deck?view=focus")
    # A phone shows at most 4 pages: the last 3 visited and the current one.
    crumbs = pg.locator(".breadcrumb > a")   # after the module switcher
    expect(crumbs).to_have_count(4)  # 3 pages, current
    expect(crumbs.nth(0)).to_contain_text("Troubleshooting2")
    assert horizontal_overflow(pg) <= 0
    last = crumbs.nth(2)
    assert last.evaluate("el => el.scrollWidth > el.clientWidth"), "long titles are cut off"

    pg.locator(".task-item").filter(has=pg.get_by_text("Water plants", exact=True)) \
        .locator(".task-content").tap()
    modal = pg.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    box = modal.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"] + 0.5, box
    save = modal.get_by_role("button", name="Save").bounding_box()
    assert save["x"] + save["width"] <= PHONE["width"] and save["y"] + save["height"] <= PHONE["height"], save


@pytest.mark.parametrize("slug,title", [
    ("short", "A page title that goes on and on and on and does not stop anywhere soon"),
    ("a-page-slug-that-goes-on-and-on-and-does-not-stop", "Long slug"),
    ("unbroken", "Supercalifragilisticexpialidocious-Infrastructure-Troubleshooting"),
    # No hyphen to break at: the slug label cannot wrap.
    ("supercalifragilisticexpialidocious_infrastructure_troubleshooting", "Unbreakable slug"),
])
def test_editor_toolbar_fits_on_phone_with_long_title(new_page, wiki, slug, title):
    # Regression: a long page title or slug pushed the Done button off-screen.
    wiki.write(slug, f"# {title}\n\nText.\n")
    pg = new_page(**TOUCH_PHONE)
    pg.goto(f"/edit/{slug}")
    assert horizontal_overflow(pg) <= 0
    done = pg.get_by_role("button", name="Done").bounding_box()
    assert done["x"] >= 0 and done["x"] + done["width"] <= PHONE["width"], done
    # One line: the slug is cut off rather than wrapped into a tall column.
    assert pg.locator(".editor-toolbar").bounding_box()["height"] < 60


def test_desktop_keeps_the_full_breadcrumb_trail(page, wiki):
    for i in range(5):
        wiki.write(f"p{i}", f"# Page {i}\n")
        page.goto(f"/page/p{i}")
    page.goto("/deck")
    expect(page.locator(".breadcrumb > a")).to_have_count(5)  # 5 pages (current is text)


def test_code_block_padding_survives_the_highlight_theme(page, wiki):
    # Regression: highlight.js's theme pads `pre code.hljs` by 1em, on top
    # of the pre's own padding.
    wiki.write("code", "# Code\n\n```python\nprint('hi')\n```\n")
    page.goto("/page/code")
    code = page.locator(".page-content pre code")
    expect(code).to_have_class(re.compile(r"\bhljs\b"))   # highlighted, from the local copy
    assert code.evaluate("c => getComputedStyle(c).paddingTop") == "0px"


# ── Side columns (big screens) ───────────────────────────────────────────────
# From 1320px a page view shows its table of contents on the left and what
# links to it on the right, both sticky; the text keeps its width.

BIG = {"width": 1600, "height": 900}

LONG = "# Release\n\n[[toc]]\n\nIntro text.\n\n" + "".join(
    f"## Part {i}\n\n" + "A paragraph of text that fills the line nicely. " * 12 + "\n\n" for i in range(1, 7)
) + """## Code and tables

```
short
```

```
""" + "x" * 300 + """
```

| A | B |
|---|---|
| 1 | 2 |

| """ + " | ".join(f"Column {i} with a long header" for i in range(8)) + """ |
|""" + "---|" * 8 + """
| """ + " | ".join("value" for _ in range(8)) + """ |
"""


def _side_wiki(wiki):
    wiki.write("release", LONG)
    wiki.write("plans", "# Plans\n\nSee [[Release]].\n")


def test_side_columns_on_big_screens(new_page, wiki):
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    page.goto("/page/release")
    toc, links = page.locator(".rail-toc"), page.locator(".rail-context")
    expect(toc).to_be_visible()
    expect(links.locator(".backlinks a", has_text="Plans")).to_be_visible()
    # The inline copies and the breadcrumb's backlinks chip are gone.
    expect(page.locator(".page-content .toc")).to_be_hidden()
    expect(page.locator(".page-foot")).to_be_hidden()
    expect(page.locator(".bl-count")).to_be_hidden()
    t, c, r = (page.locator(s).bounding_box() for s in (".rail-left", ".page-content", ".rail-context"))
    assert t["x"] + t["width"] < c["x"] and c["x"] + c["width"] < r["x"]
    assert abs(c["width"] - 46 * 16) < 2                    # the reading width
    # Both columns stay in view while the page scrolls.
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(200)
    assert 0 <= toc.bounding_box()["y"] < 100 and 0 <= links.bounding_box()["y"] < 100


def test_side_toc_marks_the_section_being_read(new_page, wiki):
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    page.goto("/page/release")
    active = page.locator(".rail-toc a.active")
    expect(active).to_have_text("Part 1")
    page.locator("#part-4").scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, document.getElementById('part-4').getBoundingClientRect().top - 40)")
    expect(active).to_have_text("Part 4")
    expect(active).to_have_attribute("aria-current", "location")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    expect(active).to_have_text("Code and tables")   # the end: the last section
    page.locator(".rail-toc a", has_text="Part 2").click()
    page.wait_for_url("**#part-2")
    expect(active).to_have_text("Part 2")


def test_wide_code_and_tables_grow_short_ones_dont(new_page, wiki):
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    page.goto("/page/release")
    text = page.locator(".page-content > p").first.bounding_box()
    short, long_ = (b.bounding_box() for b in page.locator(".page-content > pre").all())
    small, wide = (b.bounding_box() for b in page.locator(".page-content > table").all())
    for box in (short, small):                         # as wide as the text
        assert abs(box["x"] - text["x"]) < 1 and abs(box["width"] - text["width"]) < 1
    centre = text["x"] + text["width"] / 2
    for box in (long_, wide):                          # wider, centred on the text
        assert box["width"] > text["width"] + 100
        assert abs(box["x"] + box["width"] / 2 - centre) < 1
        assert box["width"] <= page.locator(".page-col").bounding_box()["width"] + 1


def test_no_side_columns_below_1320(new_page, wiki):
    _side_wiki(wiki)
    page = new_page(viewport={"width": 1280, "height": 900})
    page.goto("/page/release")
    expect(page.locator(".rail-left")).to_be_hidden()
    expect(page.locator(".rail-context")).to_be_hidden()
    expect(page.locator(".page-content .toc")).to_be_visible()
    expect(page.locator(".page-foot .backlinks a", has_text="Plans")).to_be_visible()
    expect(page.locator(".bl-count")).to_be_visible()
    text = page.locator(".page-content > p").first.bounding_box()
    for el in page.locator(".page-content > :is(pre, table)").all():
        assert el.bounding_box()["width"] <= page.locator(".page-content").bounding_box()["width"] + 1
    assert text["width"] < 800


def test_top_row_stays_put_across_views_on_big_screens(new_page, wiki):
    # Regression: only page views were wide, so on Scratch or the Deck the
    # switcher and the top actions jumped inward. Every view shares the top
    # row; the text column sits in the same place on each.
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    rows = []
    for url in ("/page/release", "/page/_scratch", "/deck"):
        page.goto(url)
        sw, ta = page.locator(".breadcrumb .module-switch").bounding_box(), page.locator(".top-actions").bounding_box()
        h1 = page.locator("h1").first.bounding_box()
        rows.append((round(sw["x"]), round(ta["x"] + ta["width"]), round(h1["x"])))
    assert len(set(rows)) == 1, rows
