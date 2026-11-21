"""Layout checks that need a real rendering engine, plus a screenshot set.

The overflow test is the mechanical part of "does it render correctly on a
phone": nothing may push the page wider than the viewport. The screenshot
test writes PNGs to tests/e2e/_screenshots/ for a human (or an agent) to look at;
it asserts only that they were produced.
"""

import re
from datetime import date

import pytest
from playwright.sync_api import expect

from conftest import DESKTOP, PHONE, SCREENSHOT_DIR, TOUCH_PHONE, box_of, open_tab, still
from tests import settings

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
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    for title, extra in [("Short task", {}),
                         ("A task with a rather long title that should wrap on narrow screens",
                          {"tags": ["work", "layout"]}),
                         ("Planned today", {"start_date": _today()})]:
        page.request.post("/api/tasks", data={"title": title, **extra})


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
    ("tasks-all", "/page/tasks", "open"),
    ("tasks-plan", "/page/tasks", "plan"),
    ("tasks-focus", "/page/tasks", "focus"),
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


def test_version_bottom_left_only_beside_the_column(new_page, wiki):
    wide = new_page(viewport=DESKTOP)
    wide.goto("/page/index")
    build = wide.locator(".zone-build")
    expect(build).to_be_visible()
    expect(build).to_have_text(re.compile(r"^Zone \d+\.\d+\.\d+"))
    box, col = box_of(build), box_of(wide.locator("main"))
    assert box["x"] < 20 and box["y"] + box["height"] > DESKTOP["height"] - 20, "bottom left"
    assert box["x"] + box["width"] < col["x"] + 40, "left of the text column"
    wide.goto("/edit/index")
    expect(wide.locator(".zone-build")).to_be_hidden()
    narrow = new_page(viewport={"width": 1000, "height": 700})
    narrow.goto("/page/index")
    expect(narrow.locator(".zone-build")).to_be_hidden()
    phone = new_page(viewport=PHONE)
    phone.goto("/page/index")
    expect(phone.locator(".zone-build")).to_be_hidden()


def test_tldr_toggle_reveals_text_below_tags(page, wiki):
    wiki.write("stress", STRESS)
    page.goto("/page/stress")
    tldr = page.locator(".page-tldr")
    label = page.locator("label.tldr-label")
    expect(tldr).to_be_hidden()
    before = box_of(label)
    label.click()
    expect(tldr).to_be_visible()
    after = box_of(label)
    assert (before["x"], before["y"]) == (after["x"], after["y"]), "label must not move"
    assert box_of(tldr)["y"] > after["y"] + after["height"] - 1, "text goes below the row"


def test_tldr_text_lines_up_with_the_row_beside_side_columns(new_page, wiki):
    # From 1320px the page column is wider than the text, which is centred in it.
    wiki.write("stress", STRESS)
    pg = new_page(viewport={"width": 1440, "height": 900})
    pg.goto("/page/stress")
    pg.locator("label.tldr-label").click()
    row = box_of(pg.locator(".page-meta-wrapper"))
    tldr = box_of(pg.locator(".page-tldr"))
    assert abs(tldr["x"] - row["x"]) < 1 and abs(tldr["width"] - row["width"]) < 1


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
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    pg.goto("/page/tasks?view=open&group=none")
    for label, above, below in pg.evaluate(_TAB_TEXT_GAPS):
        assert abs(above - below) <= 1.0, f"{label}: {above:.1f}px above vs {below:.1f}px below"


_BOARD_HEADER_GAPS = """() => [...document.querySelectorAll('.board-day-divider')].map(d => {
  const r = d.getBoundingClientRect(), bw = parseFloat(getComputedStyle(d).borderTopWidth);
  const label = d.querySelector('.board-day-fold') || d.querySelector('.board-day-label');
  const cs = getComputedStyle(label);
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const m = ctx.measureText('H');
  const text = [...label.childNodes].find(n => n.nodeType === 3);
  const range = document.createRange(); range.selectNodeContents(text);
  const baseline = range.getBoundingClientRect().top + m.fontBoundingBoxAscent;
  return [text.textContent, baseline - m.actualBoundingBoxAscent - r.top - bw, r.bottom - bw - baseline];
})"""


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_board_header_labels_vertically_centred(new_page, wiki, viewport):
    # Regression: uneven padding and line height 1.6 left the capitals of
    # a header with cards 3px above centre (4.6px above, 11.2px below).
    pg = new_page(viewport=viewport)
    pg.request.post("/api/tasks", data={"title": "Loose"})
    pg.request.post("/api/tasks", data={"title": "Started", "start_date": date.today().isoformat()})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    pg.goto("/page/tasks?view=open&group=day")
    pg.locator(".board-day-divider").first.wait_for()
    rows = pg.evaluate(_BOARD_HEADER_GAPS)
    assert len(rows) >= 9          # with cards (Unplanned, Today) and empty ones
    for label, above, below in rows:
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
    assert all(box_of(a)["height"] >= 36 for a in tip.locator("a").all())
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
    # The tooltip shows while the pointer is over the chip (CSS :hover), and
    # the pointer is moved there once: a chip that a late font or badge moves
    # afterwards leaves it behind, and the tooltip never shows.
    still(page)
    page.locator(".bl-count").hover()
    expect(tip).to_be_visible()
    page.locator(".bl-count").click()           # desktop click still jumps to the section
    expect(page).to_have_url(re.compile(r"#backlinks$"))
    page.wait_for_function("() => scrollY > 0")


TASKS_HEADER_PARTS = ["#tasks-tabs", "#task-filter-search", "#task-add-btn"]
# On a phone the filter field is behind the search button, on the tabs' line.
PHONE_HEADER_PARTS = ["#tasks-tabs", ".tasks-search-btn", "#task-add-btn"]


def tasks_header_rows(pg, parts=TASKS_HEADER_PARTS) -> int:
    """Number of distinct lines the Open tab's header controls sit on."""
    centres = sorted(
        (lambda b: b["y"] + b["height"] / 2)(box_of(pg.locator(sel)))
        for sel in parts)
    return 1 + sum(1 for a, b in zip(centres, centres[1:]) if b - a > 8)


@pytest.mark.parametrize("ctx,rows", [({"viewport": DESKTOP}, 1), (TOUCH_PHONE, 1)],
                         ids=["desktop", "phone"])
def test_tasks_all_tab_header_is_compact(new_page, wiki, ctx, rows):
    # Tabs + search + New: one line on desktop and on a phone, where the
    # filter field is behind the search button (it used to be two lines on a
    # phone, and before that five); the Open tab's control and order are on
    # a second one, and the field, asked for, on a third.
    pg = new_page(**ctx)
    seed(wiki, pg)
    open_view(pg, "/page/tasks", "open")
    phone = ctx is TOUCH_PHONE
    assert tasks_header_rows(pg, PHONE_HEADER_PARTS if phone else TASKS_HEADER_PARTS) == rows
    field = pg.locator("#task-filter-search")
    if phone:
        expect(field).to_be_hidden()
        assert tasks_header_rows(pg, PHONE_HEADER_PARTS + ["#task-sort"]) == 2
        pg.locator(".tasks-search-btn").tap()
        expect(field).to_be_focused()
        assert tasks_header_rows(pg, PHONE_HEADER_PARTS + ["#task-sort", "#task-filter-search"]) == 3
        # Left empty it is put away again; while it filters it stays.
        pg.locator("#tasks-list").tap(position={"x": 5, "y": 5})
        expect(field).to_be_hidden()
        pg.locator(".tasks-search-btn").tap()
        field.fill("zzz")
        pg.locator("#tasks-list").tap(position={"x": 5, "y": 5})
        expect(field).to_be_visible()
    assert box_of(field)["width"] >= 100
    assert horizontal_overflow(pg) <= 0


def test_long_breadcrumb_trail_fits_on_phone(new_page, wiki):
    # Regression: the navigation trail (up to 6 visited pages) was one flex
    # row that neither wrapped nor shrank, so a few long titles made the page
    # wider than the phone. The full-screen tasks edit modal grew with it and
    # its Save/Cancel bar ended up off-screen.
    pg = new_page(**TOUCH_PHONE)
    pg.request.post("/api/tasks", data={"title": "Water plants", "start_date": _today()})
    # A full trail (5 pages before the current one) of titles with long words.
    for i in range(5):
        wiki.write(f"long-{i}", f"# Infrastructure-Troubleshooting{i} Kubernetes\n")
        pg.goto(f"/page/long-{i}")
    pg.goto("/tasks?view=today")
    # A phone shows at most 4 pages: the last 3 visited and the current one.
    crumbs = pg.locator(".breadcrumb > a")   # after the module switcher
    expect(crumbs).to_have_count(4)  # 3 pages, current
    expect(crumbs.nth(0)).to_contain_text("Troubleshooting2")
    assert horizontal_overflow(pg) <= 0
    # Long titles are cut off, and no name is cut down to less than 72px of
    # it: the oldest pages leave the row for that (fitTrail).
    widths = crumbs.evaluate_all("els => els.filter(e => e.offsetParent).map(e => [e.clientWidth, e.scrollWidth])")
    assert 1 <= len(widths) < 4 and all(sw <= cw or cw >= 72 for cw, sw in widths), widths
    expect(crumbs.last).to_be_visible()             # the page itself stays

    water = pg.locator(".task-item").filter(has=pg.get_by_text("Water plants", exact=True)).locator(".task-content")
    water.tap()     # selects
    water.tap()     # opens
    modal = pg.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    box = box_of(modal)
    assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"] + 0.5, box
    save = box_of(modal.get_by_role("button", name="Save"))
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
    done = box_of(pg.get_by_role("button", name="Done"))
    assert done["x"] >= 0 and done["x"] + done["width"] <= PHONE["width"], done
    # One line: the slug is cut off rather than wrapped into a tall column.
    assert box_of(pg.locator(".editor-toolbar"))["height"] < 60


def test_desktop_keeps_the_full_breadcrumb_trail(page, wiki):
    for i in range(5):
        wiki.write(f"p{i}", f"# Page {i}\n")
        page.goto(f"/page/p{i}")
    page.goto("/tasks")
    expect(page.locator(".breadcrumb > a")).to_have_count(5)  # 5 pages (current is text)


def test_trail_drops_its_oldest_pages_where_the_window_is_not_wide(new_page, wiki):
    # Regression: below the side columns' width (1320px) the row has room
    # for two or three names next to the switcher and the top actions, and a
    # full trail was six names cut down to a letter each ("R… › M… › B…").
    names = ["Garden plan", "Raised bed plan", "Meeting notes October", "Budget 2026",
             "Tools to buy", "Seeds", "Compost heap"]
    for n in names:
        wiki.write(n.lower().replace(" ", "-"), f"# {n}\n\ntext\n")
    pg = new_page(viewport={"width": 1000, "height": 600})
    for n in names:
        pg.goto("/page/" + n.lower().replace(" ", "-"))
    shown = "els => els.filter(e => e.offsetParent).map(e => [e.textContent, e.clientWidth, e.scrollWidth])"
    crumbs = pg.locator(".breadcrumb > .trail-link, .breadcrumb > span:not([class])")
    expect(pg.locator(".breadcrumb.trail-ready")).to_have_count(1)
    expect(pg.locator(".breadcrumb > .trail-link.trail-off").first).to_be_attached()
    row = crumbs.evaluate_all(shown)
    # The page itself and the latest before it, each readable: whole, or at least 72px of it.
    assert [r[0] for r in row][-1] == "Compost heap" and 2 <= len(row) < 6, row
    assert [r[0] for r in row] == names[-len(row):], row
    assert all(sw <= cw or cw >= 72 for _, cw, sw in row), row
    # The divider after the switcher stays, and one separator between the names.
    assert pg.locator(".breadcrumb > .breadcrumb-sep:visible").count() == len(row)
    assert horizontal_overflow(pg) <= 0
    # A wider window has them all again; narrower, fewer.
    pg.set_viewport_size({"width": 1600, "height": 600})
    expect(pg.locator(".breadcrumb > .trail-link.trail-off")).to_have_count(0)
    assert [r[0] for r in crumbs.evaluate_all(shown)] == names[1:]
    pg.set_viewport_size({"width": 700, "height": 600})
    # Fewer than at 1000px (how many depends on the top actions' width).
    pg.wait_for_function(
        "n => document.querySelectorAll('.breadcrumb > .trail-link:not(.trail-off)').length < n",
        arg=len(row) - 1, timeout=3000)
    expect(pg.locator(".breadcrumb > span:not([class])")).to_have_text("Compost heap")


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

LONG = "# Release\n\nIntro text.\n\n" + "".join(
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
    expect(toc.locator("a")).to_have_count(7)                 # every ## and ###
    expect(links.locator(".backlinks a", has_text="Plans")).to_be_visible()
    # The sections at the end and the breadcrumb's backlinks chip are gone.
    expect(page.locator(".page-foot")).to_be_hidden()
    expect(page.locator(".bl-count")).to_be_hidden()
    t, c, r = (box_of(page.locator(s)) for s in (".rail-left", ".page-content", ".rail-context"))
    assert t["x"] + t["width"] < c["x"] and c["x"] + c["width"] < r["x"]
    assert abs(c["width"] - 46 * 16) < 2                    # the reading width
    # Both columns stay in view while the page scrolls.
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(200)
    assert 0 <= box_of(toc)["y"] < 100 and 0 <= box_of(links)["y"] < 100


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
    text = box_of(page.locator(".page-content > p").first)
    short, long_ = (box_of(b) for b in page.locator(".page-content > pre").all())
    small, wide = (box_of(b) for b in page.locator(".page-content > table").all())
    for box in (short, small):                         # as wide as the text
        assert abs(box["x"] - text["x"]) < 1 and abs(box["width"] - text["width"]) < 1
    centre = text["x"] + text["width"] / 2
    for box in (long_, wide):                          # wider, centred on the text
        assert box["width"] > text["width"] + 100
        assert abs(box["x"] + box["width"] / 2 - centre) < 1
        assert box["width"] <= box_of(page.locator(".page-col"))["width"] + 1


def test_a_pages_sheet_is_as_wide_as_other_views_unless_it_needs_room(new_page, wiki):
    # A page with only text: its sheet as wide as Notes'. One with a wide code
    # block or table: the whole middle column, so they stay on the sheet.
    _side_wiki(wiki)
    wiki.write("plain", "# Plain\n\nJust text, and [[Release]].\n")
    page = new_page(viewport=BIG)
    page.goto("/notes")
    notes = box_of(page.locator(".sheet"))
    page.goto("/page/plain")
    plain = box_of(page.locator(".page-col"))
    assert abs(plain["width"] - notes["width"]) < 1 and abs(plain["x"] - notes["x"]) < 1
    page.goto("/page/release")
    expect(page.locator(".page-col")).to_have_class(re.compile(r"\bsheet-wide\b"))
    wide = box_of(page.locator(".page-col"))
    assert wide["width"] > notes["width"] + 100
    for el in page.locator(".page-content > :is(pre, table)").all():
        box = box_of(el)
        assert box["x"] >= wide["x"] and box["x"] + box["width"] <= wide["x"] + wide["width"]


def test_no_side_columns_below_1320(new_page, wiki):
    _side_wiki(wiki)
    page = new_page(viewport={"width": 1280, "height": 900})
    page.goto("/page/release")
    expect(page.locator(".rail-left")).to_be_hidden()
    expect(page.locator(".rail-context")).to_be_hidden()
    expect(page.locator(".page-foot .backlinks a", has_text="Plans")).to_be_visible()
    expect(page.locator(".bl-count")).to_be_visible()
    text = box_of(page.locator(".page-content > p").first)
    for el in page.locator(".page-content > :is(pre, table)").all():
        assert box_of(el)["width"] <= box_of(page.locator(".page-content"))["width"] + 1
    assert text["width"] < 800


def test_top_row_stays_put_across_views_on_big_screens(new_page, wiki):
    # Regression: only page views were wide, so on Notes or the Tasks the
    # switcher and the top actions jumped inward. Every view shares the top
    # row; the text column sits in the same place on each.
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    rows = []
    for url in ("/page/release", "/notes", "/tasks"):
        page.goto(url)
        sw, ta = box_of(page.locator(".breadcrumb .module-switch")), box_of(page.locator(".top-actions"))
        h1 = box_of(page.locator("h1").first)
        rows.append((round(sw["x"]), round(ta["x"] + ta["width"]), round(h1["x"])))
    assert len(set(rows)) == 1, rows


def test_editor_badges_clear_of_the_breadcrumb(page, wiki):
    # Regression: the badges, fixed at the top left, covered the start of
    # the editor's breadcrumb (the logo and the first crumb).
    wiki.write("draft", "# Draft\n\nNot committed yet.\n")   # → uncommitted badge
    page.set_viewport_size({"width": 1600, "height": 800})
    page.goto("/edit/draft")
    badge = page.locator("#uncommitted-badge")
    expect(badge).to_be_visible()
    b = box_of(badge)
    for sel in (".editor-pane .breadcrumb > *", ".editor-toolbar button, .editor-toolbar a", "#editor-textarea"):
        for el in page.locator(sel).all():
            if el.is_visible():      # a button the toolbar hides has no box
                box = box_of(el)
                assert(b["x"] + b["width"] <= box["x"] or box["x"] + box["width"] <= b["x"] or
                        b["y"] + b["height"] <= box["y"] or box["y"] + box["height"] <= b["y"]), sel


def test_long_side_toc_fades_and_follows(new_page, wiki):
    # A TOC taller than the window scrolls in its column: a fade shows there
    # is more, and the section being read stays in view.
    text = "# Many\n\n" + "".join(f"## Section {i}\n\n" + "Text. " * 80 + "\n\n" for i in range(1, 41))
    wiki.write("many", text)
    page = new_page(viewport={"width": 1600, "height": 600})
    page.goto("/page/many")
    rail = page.locator(".rail-left")
    expect(rail).to_have_class(re.compile(r"\brail-more-below\b"))
    expect(rail).not_to_have_class(re.compile(r"\brail-more-above\b"))
    page.evaluate("window.scrollBy(0, document.getElementById('section-30').getBoundingClientRect().top - 40)")
    active = page.locator(".rail-toc a.active")
    expect(active).to_have_text("Section 30")
    expect(rail).to_have_class(re.compile(r"\brail-more-above\b"))
    a, r = box_of(active), box_of(rail)
    assert r["y"] <= a["y"] and a["y"] + a["height"] <= r["y"] + r["height"]
    assert page.locator(".rail-context").evaluate("el => el.className").count("rail-more") == 0


def test_back_to_top_in_the_left_column(new_page, wiki):
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    page.goto("/page/release#part-1")
    top = page.locator(".rail-top")
    page.evaluate("window.scrollTo(0, 0)")
    expect(top).to_be_hidden()
    page.evaluate("window.scrollTo(0, 2000)")
    expect(top).to_be_visible()
    top.click()
    page.wait_for_function("() => window.scrollY === 0")
    assert page.url.endswith("/page/release")      # no #hash left behind
    expect(top).to_be_hidden()


def test_page_actions_stay_in_the_header_when_scrolled(new_page, wiki):
    # Regression: a copy of Edit and ⋯ showed at the top of the right column
    # once the header's scrolled away. They stay where they are, in the
    # header, and scroll away with it.
    _side_wiki(wiki)
    page = new_page(viewport=BIG)
    page.goto("/page/release")
    actions = page.locator(".page-header-actions")
    before = box_of(actions)
    page.evaluate("window.scrollTo(0, 1500)")
    scrolled = page.evaluate("window.scrollY")
    assert scrolled > before["y"] + before["height"]    # the header is out of view
    after = box_of(actions)
    assert after["x"] == before["x"] and after["y"] == before["y"] - scrolled
    expect(page.locator(".rail-context").get_by_role("link", name="Edit")).to_have_count(0)
    expect(page.locator(".page-menu-btn")).to_have_count(1)


def test_page_menu(page, wiki):
    # The header's ⋯: keyboard (↓/↑ wrap, Esc back to the ⋯), History,
    # Rename… in a dialog, Delete… in its confirmation dialog.
    wiki.write("talk", "# Talk\n\n## One\n\nText.\n")
    page.goto("/page/talk")
    more = page.locator(".page-header-actions").get_by_role("button", name="More actions")
    menu = page.locator("#page-menu")
    expect(menu).to_be_hidden()
    more.click()
    expect(more).to_have_attribute("aria-expanded", "true")
    assert menu.evaluate("m => [...m.querySelectorAll('[role=menuitem]')].map(i => i.firstChild.textContent)") \
        == ["Present", "History", "Print…", "Rename…", "Delete…"]
    expect(menu.get_by_role("menuitem", name="Present")).to_be_focused()
    page.keyboard.press("ArrowUp")                  # wraps to the last
    expect(menu.get_by_role("menuitem", name="Delete…")).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(more).to_be_focused()
    expect(more).to_have_attribute("aria-expanded", "false")
    # A click outside closes it.
    more.click()
    page.mouse.click(5, 400)
    expect(menu).to_be_hidden()
    # Rename… renames from the page view and lands on the renamed page.
    more.click()
    menu.get_by_role("menuitem", name="Rename…").click()
    field = page.locator("#page-rename-input")
    expect(field).to_be_focused()
    field.fill("speech")
    page.locator("#page-rename-dialog").get_by_role("button", name="Rename").click()
    page.wait_for_url("**/page/speech")
    assert (wiki.root / "speech.md").exists() and not (wiki.root / "talk.md").exists()
    # A taken name brings the dialog back with the error.
    wiki.write("other", "# Other\n")
    more.click()
    menu.get_by_role("menuitem", name="Rename…").click()
    field.fill("other")
    page.keyboard.press("Enter")
    expect(page.locator("#page-rename-dialog")).to_be_visible()
    expect(page.locator("#page-rename-dialog")).to_contain_text('A page named "other" already exists')
    expect(page).not_to_have_url(re.compile("rename_error"))   # waits: page.url may be a moment behind the dialog
    page.keyboard.press("Escape")
    # Delete… asks first.
    more.click()
    menu.get_by_role("menuitem", name="Delete…").click()
    expect(page.locator("#page-delete-dialog")).to_be_visible()
    page.locator("#page-delete-dialog").get_by_role("button", name="Cancel").click()
    assert (wiki.root / "speech.md").exists()
    # History is a link.
    more.click()
    expect(menu.get_by_role("menuitem", name="History")).to_have_attribute("href", "/history/speech")


def test_section_jump_keys(page, wiki):
    # ] / [ step through the page's sections (any width), the anchor in the
    # URL; the side TOC follows. At the ends nothing happens.
    _side_wiki(wiki)
    page.goto("/page/release")
    top = lambda hid: page.evaluate(f"document.getElementById('{hid}').getBoundingClientRect().top")
    page.keyboard.press("]")
    assert abs(top("part-1") - 40) < 2 and page.url.endswith("#part-1")
    page.keyboard.press("]")
    page.keyboard.press("]")
    assert abs(top("part-3") - 40) < 2 and page.url.endswith("#part-3")
    page.keyboard.press("[")
    assert abs(top("part-2") - 40) < 2 and page.url.endswith("#part-2")
    page.keyboard.press("[")
    page.keyboard.press("[")                     # before the first: the top
    assert page.evaluate("window.scrollY") == 0
    for _ in range(10):
        page.keyboard.press("]")
    assert page.url.endswith("#code-and-tables")  # stops at the last


def test_wide_images_use_the_middle_column(new_page, wiki):
    assets = wiki.root / "_assets"
    assets.mkdir(exist_ok=True)
    for name, w in (("wide.svg", 1400), ("small.svg", 200)):
        (assets / name).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="80">'
                                   f'<rect width="{w}" height="80" fill="#888"/></svg>')
    wiki.write("pics", "# Pics\n\nSome text to measure the column by, long enough to fill a line of it.\n\n"
                       "![wide](/_assets/wide.svg)\n\n![small](/_assets/small.svg)\n\n"
                       "In a sentence ![wide](/_assets/wide.svg) stays.\n\n[[tasks:]]\n")
    page = new_page(viewport=BIG)
    page.goto("/page/pics")
    text = box_of(page.locator(".page-content > p").first)
    col = box_of(page.locator(".page-col"))
    imgs = page.locator(".page-content img")
    expect(imgs.first).to_have_js_property("complete", True)
    wide, small, inline = (box_of(i) for i in imgs.all())
    assert wide["width"] > text["width"] + 100 and wide["width"] <= col["width"] + 1
    assert abs(wide["x"] + wide["width"] / 2 - (text["x"] + text["width"] / 2)) < 1
    assert small["width"] == 200 and abs(small["x"] - text["x"]) < 1
    assert inline["width"] <= text["width"] + 1
    tasks = box_of(page.locator(".tasks-app"))     # a task list keeps the text's width
    assert abs(tasks["width"] - text["width"]) < 1 and abs(tasks["x"] - text["x"]) < 1


def test_tasks_quicknote_and_wiki_content_share_one_width(new_page, wiki):
    # Every view's content has the text's width, on big screens and small:
    # a page's text, a task list embed on it, /tasks, Notes and search results.
    _side_wiki(wiki)
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    wiki.write("2026-10-01_09-00-00_note", "# Note\n")
    # 1440: side columns, but less room than BIG; they used to squeeze the
    # page's text there.
    for viewport in (BIG, {"width": 1440, "height": 900}, {"width": 1100, "height": 900}):
        page = new_page(viewport=viewport)
        boxes = {}
        page.goto("/page/release")
        boxes["text"] = box_of(page.locator(".page-content"))
        page.goto("/page/tasks")
        boxes["embed"] = box_of(page.locator(".tasks-app"))
        page.goto("/tasks")
        boxes["tasks"] = box_of(page.locator(".tasks-app"))
        page.goto("/notes")
        boxes["quicknote"] = box_of(page.locator(".notes-list"))
        page.goto("/search?q=note")
        boxes["search"] = box_of(page.locator(".search-results"))
        edges = {k: (round(b["x"]), round(b["width"])) for k, b in boxes.items()}
        assert len(set(edges.values())) == 1, (viewport, edges)


def test_section_task_chip_opens_its_tasks(page, wiki):
    wiki.write("garden", "# Garden\n\n## Beds\n\nSoil.\n")
    page.request.post("/api/tasks", data={"title": "Weed [[Garden#Beds]]"})
    page.goto("/page/garden")
    chip = page.locator(".section-header .section-tasks")
    expect(chip.locator("summary")).to_have_text("☑ 1")
    expect(chip.locator("ul")).to_be_hidden()
    chip.locator("summary").click()
    link = chip.locator("ul a")
    expect(link).to_have_text("Weed Garden › Beds")
    link.click()
    page.wait_for_url(re.compile(r"/tasks\?task="))


@pytest.mark.parametrize("width", [1920, 1000])
def test_top_row_is_one_aligned_row(new_page, wiki, monkeypatch, width):
    # Regression: the status badges were fixed to the window's corner, above
    # and left of the row, and the icons' padding pushed them inside the
    # content's edge. Now badges, switcher and icons share the row's centre
    # line, and the last button (the search pill) ends on the content's right edge.
    monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", True)
    _side_wiki(wiki)                                   # uncommitted pages: the badge shows
    page = new_page(viewport={"width": width, "height": 700})
    page.goto("/page/release")
    expect(page.locator("#uncommitted-badge")).to_be_visible()
    centre = lambda sel: (lambda b: b["y"] + b["height"] / 2)(box_of(page.locator(sel).first))
    row = centre(".breadcrumb .module-switch")
    for sel in (".remote-badge", "#uncommitted-badge", ".top-actions button"):
        assert abs(centre(sel) - row) < 1.5, sel
    last = box_of(page.locator(".top-actions button").last)     # the search pill's border
    edge = box_of(page.locator("main > .breadcrumb"))
    assert abs(last["x"] + last["width"] - (edge["x"] + edge["width"])) < 1
    badges = box_of(page.locator("#uncommitted-badge"))
    assert badges["x"] + badges["width"] < box_of(page.locator(".top-actions"))["x"]
    # 🌐 left of the module switcher, outside the content's edge.
    globe, switch = box_of(page.locator(".remote-badge")), box_of(page.locator(".breadcrumb .module-switch"))
    assert globe["x"] + globe["width"] <= switch["x"] and globe["x"] >= 0


def test_history_flow(page, wiki):
    # History lists versions; a row opens one, ← Older / Newer → step through
    # them, Restore asks first, and "What this save changed" shows the diff.
    for text in ("v1", "v2", "v3"):
        page.request.post("/edit/talk", form={"content": f"# Talk\n\n{text}\n"})
    page.goto("/history/talk")
    rows = page.locator(".hist-row")
    expect(rows).to_have_count(3)
    expect(rows.first.locator(".hist-badge")).to_have_text("Current")
    rows.nth(2).locator(".hist-main").click()
    expect(page.locator(".version-where")).to_contain_text("2 versions back")
    expect(page.locator("article.page-content")).to_have_text("v1")
    page.get_by_role("link", name="Newer →").click()
    expect(page.locator("article.page-content")).to_have_text("v2")
    # Declining the question changes nothing; accepting restores. The question
    # is asked by a module, so wait until the page's modules have run: a click
    # before that submits the form unasked.
    page.wait_for_load_state("load")
    page.once("dialog", lambda d: d.dismiss())
    page.get_by_role("button", name="Restore this version").click()
    expect(page.locator(".version-where")).to_contain_text("1 version back")
    assert "v3" in wiki.read("talk")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Restore this version").click()
    page.wait_for_url("**/page/talk")
    assert "v2" in wiki.read("talk")
    page.goto("/history/talk")
    expect(rows.first.locator(".hist-what")).to_contain_text("Restored the version of")
    rows.nth(1).get_by_role("link", name="Changes").click()
    expect(page.locator(".diff-view .diff-add")).to_have_text("+v3")
    page.get_by_role("link", name="View this version").click()
    expect(page.locator("article.page-content")).to_have_text("v3")


def test_move_quicknote_to_wiki(page, wiki):
    # A note's ⋯ has Move to wiki…: folder, name (its title), the
    # date and the page to link from, with the page's slug previewed while
    # typing.
    note = "2026-10-08_14-03-11_alex-notes"
    wiki.write(note, "# Alex notes\n\nText.\n")
    wiki.write("conversations/2026-07-08_debrief", "# Debrief\n")
    wiki.write("conversations/index", "# Conversations\n")
    page.goto(f"/page/{note}")
    page.locator(".page-header-actions").get_by_role("button", name="More actions").click()
    page.locator("#page-menu").get_by_role("menuitem", name="Move to wiki…").click()
    dialog = page.locator("#page-promote-dialog")
    expect(page.locator("#promote-name")).to_be_focused()
    expect(page.locator("#promote-slug")).to_have_text("alex-notes")
    link_from = page.locator("#promote-link-from")
    # No folder: the home page is suggested to link from.
    expect(link_from).to_have_value("index")
    # A folder without an index page: none.
    page.locator("#promote-folder").fill("journal")
    expect(link_from).to_have_value("")
    page.locator("#promote-folder").fill("conversations/")
    # The folder's index page is suggested to link from.
    expect(link_from).to_have_value("conversations/index")
    page.locator("#promote-name").fill("Alex sync")
    dialog.get_by_label("Keep the date").check()
    expect(page.locator("#promote-slug")).to_have_text("conversations/2026-10-08_alex-sync")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    dialog.get_by_role("button", name="Move").click()
    # The page to link from opens in the editor, the link in the clipboard.
    page.wait_for_url("**/edit/conversations/index")
    assert (wiki.root / "conversations" / "2026-10-08_alex-sync.md").exists()
    link = "[[conversations/2026-10-08_alex-sync|Alex notes]]"
    hint = page.locator(".paste-hint")
    expect(hint).to_contain_text("Link copied.")
    expect(hint.locator("code")).to_have_text(link)
    assert page.evaluate("navigator.clipboard.readText()") == link
    textarea = page.locator("#editor-textarea")
    textarea.focus()
    page.keyboard.press("Control+End")
    page.keyboard.press("Control+v")
    expect(textarea).to_have_value(f"# Conversations\n{link}")
    hint.get_by_role("button", name="Close").click()
    expect(hint).to_have_count(0)
    assert not (wiki.root / f"{note}.md").exists()


def test_append_quicknote_to_page(page, wiki):
    # Append to page…: pick the page, keep the suggested heading, and land
    # on the new section there.
    note = "2026-10-08_14-03-11_alex-notes"
    wiki.write(note, "# Alex notes\n\nText.\n")
    wiki.write("people/alex", "# Alex\n\nAbout.\n")
    page.goto(f"/page/{note}")
    page.locator(".page-header-actions").get_by_role("button", name="More actions").click()
    page.locator("#page-menu").get_by_role("menuitem", name="Append to page…").click()
    dialog = page.locator("#page-append-dialog")
    expect(page.locator("#append-page")).to_be_focused()
    expect(page.locator("#append-heading")).to_have_value("Alex notes (2026-10-08)")
    page.locator("#append-page").fill("people/alex")
    dialog.get_by_role("button", name="Append").click()
    page.wait_for_url("**/page/people/alex#alex-notes-2026-10-08")
    expect(page.locator("#alex-notes-2026-10-08")).to_be_visible()
    assert not (wiki.root / f"{note}.md").exists()
    # A page that doesn't exist brings the dialog back with the error.
    wiki.write(note, "# Alex notes\n")
    page.goto(f"/page/{note}")
    page.locator(".page-header-actions").get_by_role("button", name="More actions").click()
    page.locator("#page-menu").get_by_role("menuitem", name="Append to page…").click()
    page.locator("#append-page").fill("nowhere")
    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("There is no page “nowhere”")
    expect(page).not_to_have_url(re.compile("append_error"))   # waits: page.url may be a moment behind the dialog


@pytest.mark.parametrize("width", [390, 1100, 1600])
def test_uncommitted_list_layout(new_page, wiki, width):
    # Commit All sits in the header, ending where the list ends (it was a
    # bare button under the list, at the window's edge from 1320px), and on
    # a phone the list fits the screen.
    wiki.write("loose", "# Loose\n")
    wiki.write("other-notes", "# Other notes with a longer title\n")
    page = new_page(viewport={"width": width, "height": 700})
    page.goto("/uncommitted")
    btn = box_of(page.locator(".page-header-actions #commit-all-btn"))
    table = box_of(page.locator(".hist-table"))
    assert abs(btn["x"] + btn["width"] - (table["x"] + table["width"])) < 1
    assert horizontal_overflow(page) <= 0
    assert table["x"] + table["width"] <= width


def test_heading_links_copy_page_and_section_urls(page, wiki):
    # A link icon after the title and each heading, hidden until hovered;
    # a click copies the page's or the section's URL.
    wiki.write("linked", "# Linked\n\n## First part\n\nText.\n\n### A detail\n\nMore.\n")
    page.goto("/page/linked")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    title_link = page.locator(".page-header h1 .heading-link")
    section_link = page.locator("article h2#first-part .heading-link")
    expect(page.locator("article .heading-link")).to_have_count(2)
    # After the heading's text, not in the margin left of it.
    for h, link in ((page.locator(".page-header h1"), title_link), (page.locator("article h2#first-part"), section_link)):
        assert box_of(link)["x"] > box_of(h)["x"] + 20
    expect(section_link).to_have_css("opacity", "0")
    page.locator("article h2#first-part").hover()
    expect(section_link).not_to_have_css("opacity", "0")
    section_link.click()
    expect(page.locator(".task-undo-toast")).to_contain_text("Link copied")
    # A ✓ in the icon's place for a moment.
    expect(section_link).to_have_class(re.compile(r"\bcopied\b"))
    expect(section_link).not_to_have_class(re.compile(r"\bcopied\b"), timeout=3000)
    base = page.url.split("#")[0]
    assert page.evaluate("navigator.clipboard.readText()") == base + "#first-part"
    assert page.url.endswith("#first-part")
    page.locator(".page-header h1").hover()
    title_link.click()
    assert page.evaluate("navigator.clipboard.readText()") == base
    # The rail's "On this page" and the title text are unchanged.
    expect(page.locator(".page-header h1")).to_have_text("Linked")


def test_rename_into_private_is_refused(page, wiki, monkeypatch):
    # A shared page isn't an editor's to take from the others: the dialog comes back with the reason.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    wiki.write("ideas", "# Ideas\n")
    page.goto("/page/ideas")
    page.locator(".page-header-actions").get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Rename…").click()
    dialog = page.locator("#page-rename-dialog")
    dialog.locator("#page-rename-input").fill("private/alice/ideas")
    dialog.get_by_role("button", name="Rename").click()
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Ask an admin")
    expect(dialog.locator("#page-rename-input")).to_have_value("private/alice/ideas")
    expect(page).not_to_have_url(re.compile("rename_"))
    assert (wiki.root / "ideas.md").exists()
