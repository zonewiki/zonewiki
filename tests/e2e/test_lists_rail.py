"""The task lists in the left column from 1024px wide (docs/spec/tasks.md,
The lists in the left column): every list a line, the row of pills hidden
there; their counts, the pins dragged up and down, the find field and the
lists new to this browser; and below it the row as one line that scrolls
and fades. What the row does otherwise is in test_tasks.py."""

import re
from datetime import date

from playwright.sync_api import expect

from conftest import PHONE, SCREENSHOT_DIR, TABLET, box_of, list_page, make_list

BIG = {"width": 1600, "height": 900}


def rail_names(page):
    return page.locator(".lists-rail .lists-rail-item .lists-rail-name")


def test_the_lists_are_the_left_column_on_a_big_screen(new_page, wiki):
    page = new_page(viewport=BIG)
    make_list(page, "shop", pin=True)
    make_list(page, "work")
    make_list(page, "garden")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop", "due_date": date.today().isoformat()})
    page.goto("/tasks?list=shop")
    rail = page.locator(".lists-rail")
    expect(rail).to_be_visible()
    expect(page.locator(".tasks-places")).to_be_hidden()
    # The views (My tasks, the Inbox, All tasks), Pinned, every other list A–Z.
    expect(rail_names(page)).to_have_text(["My tasks", "Inbox", "All tasks", "shop", "garden", "work"])
    expect(rail.locator(".lists-rail-head")).to_have_text(["Pinned", "Lists"])
    shop = rail.locator('[data-place="list:shop"]')
    expect(shop).to_have_attribute("aria-current", "page")
    expect(shop.locator("svg")).to_have_count(0)                  # no pin: the heading says it
    expect(shop.locator(".module-due")).to_have_text("1")
    expect(rail.locator('[data-place="list:work"] .module-counts')).to_have_count(0)   # no counts on the others
    # One rhythm: the names start in one line, every line as high, the
    # same room before each group's heading, a heading smaller and lighter
    # than a list's name.
    names = [b for b in (box_of(n) for n in rail_names(page).all())]
    assert len({round(b["x"]) for b in names}) == 1, names
    # A small icon before every name: a view its own, every list the list's.
    icon = lambda sel: rail.locator(sel).evaluate(
        "el => { const s = getComputedStyle(el, '::before');"
        " return [s.maskImage || s.webkitMaskImage, parseFloat(s.width), parseFloat(s.height)]; }")
    icons = {sel: icon(sel) for sel in ('[data-place="mine"]', '[data-place="inbox"]', ".lists-rail-all",
                                        '[data-place="list:shop"]', '[data-place="list:work"]')}
    assert all(mask.startswith('url("data:image/svg+xml') and w == h and 10 <= w <= 16 for mask, w, h in icons.values()), icons
    masks = [mask for mask, _, _ in icons.values()]
    assert len(set(masks[:4])) == 4 and masks[3] == masks[4], masks
    for item, name in zip(rail.locator(".lists-rail-item").all(), names):
        left = box_of(item)["x"] + item.evaluate("el => parseFloat(getComputedStyle(el).paddingLeft)")
        assert name["x"] - left >= 13, (name, left)
    lines = [box_of(i)["height"] for i in rail.locator(".lists-rail-item").all()]
    assert len({round(h) for h in lines}) == 1, lines
    heads = rail.locator(".lists-rail-head")
    gaps = [heads.nth(i).evaluate("h => h.getBoundingClientRect().top - h.previousElementSibling?.getBoundingClientRect().bottom"
                                  " || h.getBoundingClientRect().top - h.parentElement.previousElementSibling.lastElementChild.getBoundingClientRect().bottom")
            for i in range(heads.count())]
    assert max(gaps) - min(gaps) < 1, gaps
    style = lambda loc: loc.evaluate("el => [parseFloat(getComputedStyle(el).fontSize), getComputedStyle(el).color]")
    (hsize, hcolor), (nsize, ncolor) = style(heads.first), style(rail.locator(".lists-rail-item").first)
    assert hsize < nsize and hcolor != ncolor
    # Left of the sheet, which sits where a page's does.
    r, sheet = box_of(rail), box_of(page.locator(".sheet"))
    assert r["x"] + r["width"] < sheet["x"]
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.screenshot(path=str(SCREENSHOT_DIR / f"lists-rail-{scheme}.png"))
    # Its counts are kept with the Tasks link's.
    page.request.post("/api/tasks", data={"title": "Bread", "list": "shop", "due_date": date.today().isoformat()})
    page.evaluate("import('/public/js/chrome.js').then(m => m.refreshDueBadge())")
    expect(shop.locator(".module-due")).to_have_text("2")
    # A line goes to its list.
    rail.locator('[data-place="list:work"]').click()
    page.wait_for_url(re.compile(r"/tasks\?list=work$"))
    expect(page.locator('.lists-rail [aria-current]')).to_have_attribute("data-place", "list:work")
    page.locator(".lists-rail-all").click()
    page.wait_for_url(re.compile(r"/tasks$"))
    expect(page.locator(".lists-rail-all")).to_have_attribute("aria-current", "page")
    # Narrower: the row of pills again, no column.
    page.set_viewport_size(TABLET)
    expect(rail).to_be_hidden()
    expect(page.locator(".tasks-places")).to_be_visible()


def test_the_column_comes_at_1024px_before_the_pages_side_columns(new_page, wiki):
    # The Tasks page needs only its left column, so it has it from 1024px:
    # the lists and the sheet, no right column (a list's Backlinks at the
    # end and in the breadcrumb's chip), the row of pills hidden; just
    # below, the row and no column.
    page = new_page(viewport={"width": 1024, "height": 800})
    # A wiki with no list has no column: the sheet as wide as before.
    page.goto("/tasks")
    expect(page.locator(".page-grid")).not_to_have_class(re.compile(r"\blists-grid\b"))
    expect(page.locator(".rail-lists")).to_be_hidden()
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=shop pin=1]]\n")
    make_list(page, "work")
    page.goto("/tasks?list=shop")
    rail, sheet = page.locator(".lists-rail"), page.locator(".sheet")
    expect(rail).to_be_visible()
    expect(page.locator(".tasks-places")).to_be_hidden()
    expect(page.locator(".rail-context")).to_be_hidden()
    expect(page.locator(".page-foot #backlinks a")).to_have_text(["Shop"])
    expect(page.locator(".breadcrumb-right")).to_be_visible()
    r, s = box_of(rail), box_of(sheet)
    assert r["x"] + r["width"] < s["x"], (r, s)
    assert s["width"] >= 700, s
    assert s["x"] + s["width"] <= 1024 - 16, s                        # no sideways scrolling
    assert page.evaluate("document.documentElement.scrollWidth") <= 1024
    page.screenshot(path=str(SCREENSHOT_DIR / "lists-rail-1024.png"))
    # Wider, up to the pages' side columns: the sheet grows to a page's
    # width, no more, and the two stay together in the middle.
    page.set_viewport_size({"width": 1300, "height": 800})
    s = box_of(sheet)
    assert 800 <= s["width"] <= 820, s
    r = box_of(rail)
    left, right = r["x"], 1300 - (s["x"] + s["width"])
    assert abs(left - right) < 2, (left, right)
    page.screenshot(path=str(SCREENSHOT_DIR / "lists-rail-1300.png"))
    # Just below 1024px: the row, no column.
    page.set_viewport_size({"width": 1023, "height": 800})
    expect(rail).to_be_hidden()
    expect(page.locator(".tasks-places")).to_be_visible()


def test_the_rows_views_have_their_icons_and_a_rule_after_them(new_page, wiki):
    # On a phone the row says which pills are views and which are lists:
    # My tasks and the Inbox start with the column's icons, a hairline
    # follows them, and a pinned list is its name alone (no pin glyph).
    page = new_page(viewport=PHONE)
    make_list(page, "shop", pin=True)
    page.goto("/tasks?mine=1")
    row = page.locator(".tasks-places")
    expect(row.locator(".tasks-place .tasks-place-label")).to_have_text(["My tasks", "Inbox", "shop", "More"])
    icon = lambda sel: row.locator(sel).evaluate(
        "el => { const s = getComputedStyle(el, '::before');"
        " return [s.content, s.maskImage || s.webkitMaskImage, parseFloat(s.width), parseFloat(s.height)]; }")
    mine, inbox, shop = (icon(f'[data-place="{key}"]') for key in ("mine", "inbox", "list:shop"))
    for content, mask, w, h in (mine, inbox):
        assert content == '""' and mask.startswith('url("data:image/svg+xml') and w == h and 10 <= w <= 16, (mine, inbox)
    assert mine[1] != inbox[1], (mine, inbox)
    assert shop[0] in ("none", "normal"), shop                    # a list: no icon
    expect(row.locator('[data-place="list:shop"] svg')).to_have_count(0)
    # The icon sits before the name, in the pill.
    pill, label = box_of(row.locator('[data-place="inbox"]')), box_of(row.locator('[data-place="inbox"] .tasks-place-label'))
    assert label["x"] - pill["x"] >= 13 + 8, (pill, label)
    # One rule, between the Inbox and the first list, thin and visible.
    rule = row.locator(".tasks-places-rule")
    expect(rule).to_have_count(1)
    r, before, after = box_of(rule), box_of(row.locator('[data-place="inbox"]')), box_of(row.locator('[data-place="list:shop"]'))
    assert before["x"] + before["width"] <= r["x"] and r["x"] + r["width"] <= after["x"], (before, r, after)
    assert r["width"] == 1 and 10 <= r["height"] <= before["height"], r
    page.screenshot(path=str(SCREENSHOT_DIR / "pills-views-phone.png"))
    # Without a pin the rule is before More.
    page.request.post("/tasks/pin-list", form={"list": "shop", "pin": "0"})
    page.goto("/tasks?mine=1")
    assert rule.evaluate("el => [el.previousElementSibling.dataset.place, el.nextElementSibling.className]") == ["inbox", "lists-more"]


def test_the_row_is_one_line_that_fades_where_it_goes_on(new_page, wiki):
    # Below the column the row never wraps: it scrolls sideways, an end with
    # pills out of sight fades, and More's panel hangs under More, inside
    # the row, even with More scrolled out of sight.
    page = new_page(viewport=TABLET)
    make_list(page, "shop", pin=True)
    page.goto("/tasks?list=shop")
    nav, group = page.locator(".tasks-places"), page.locator(".tasks-places-group")
    expect(nav).to_be_visible()
    assert nav.evaluate("el => [el.classList.contains('more-before'), el.classList.contains('more-after')]") == [False, False]
    for i in range(12):
        make_list(page, f"a-long-list-name-{i}", pin=True)
    page.goto("/tasks?list=shop")
    pills = [box_of(p) for p in page.locator(".tasks-places .tasks-place").all()]
    assert len({round(b["y"]) for b in pills}) == 1, pills              # one line
    g = box_of(group)
    assert g["height"] < 2 * pills[0]["height"], g
    expect(nav).to_have_class(re.compile(r"\bmore-after\b"))
    expect(nav).not_to_have_class(re.compile(r"\bmore-before\b"))
    fade = nav.evaluate("el => { const s = getComputedStyle(el, '::after'); return [s.content, s.backgroundImage]; }")
    assert fade[0] == '""' and "gradient" in fade[1], fade
    page.screenshot(path=str(SCREENSHOT_DIR / "lists-row-fade.png"))
    group.evaluate("el => { el.scrollLeft = el.scrollWidth / 2; }")
    expect(nav).to_have_class(re.compile(r"\bmore-before\b"))
    expect(nav).to_have_class(re.compile(r"\bmore-after\b"))
    group.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
    expect(nav).to_have_class(re.compile(r"\bmore-before\b"))
    expect(nav).not_to_have_class(re.compile(r"\bmore-after\b"))
    # More's panel, opened with More out of sight to the right: More comes
    # into view, the panel under it, within the row, and not clipped.
    group.evaluate("el => { el.scrollLeft = 0; }")
    more = page.locator(".lists-more summary")
    more.focus()
    page.keyboard.press("Enter")
    panel = page.locator(".lists-more-panel")
    expect(panel).to_be_visible()
    n, p, m = box_of(nav), box_of(panel), box_of(more)
    assert n["x"] - 1 <= p["x"] and p["x"] + p["width"] <= n["x"] + n["width"] + 1, (n, p)
    assert m["x"] + m["width"] <= n["x"] + n["width"] + 1, (m, n)       # More is in view
    assert p["y"] > m["y"] + m["height"], (p, m)
    hit = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('.lists-more-panel') !== null",
                        [p["x"] + p["width"] / 2, p["y"] + p["height"] / 2])
    assert hit, "the panel is covered or clipped"
    page.screenshot(path=str(SCREENSHOT_DIR / "lists-row-more.png"))


def test_your_lists_and_other_lists(new_page, wiki, monkeypatch):
    from tests import settings
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", ""), "bob": ("pw", "")})
    page = new_page(viewport=BIG)
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    make_list(page, "mine", private=True)
    page.goto("/tasks")
    rail = page.locator(".lists-rail")
    expect(rail.locator(".lists-rail-head")).to_have_text(["Your lists", "Other lists"])
    # Hers (a private list) first, then the lists for everyone she has nothing in.
    rest = rail.locator(".lists-rail-rest .lists-rail-item, .lists-rail-rest .lists-rail-head").all_text_contents()
    assert rest.index("mine") < rest.index("Other lists") < rest.index("team"), rest
    # Its lock after the name, which starts where the others' do.
    mine = rail.locator('[data-place="list:mine"]')
    expect(mine.locator(".lists-rail-mark")).to_have_count(1)
    assert box_of(mine.locator(".lists-rail-mark"))["x"] > box_of(mine.locator(".lists-rail-name"))["x"]
    assert round(box_of(mine.locator(".lists-rail-name"))["x"]) == round(box_of(rail.locator('[data-place="list:team"] .lists-rail-name'))["x"])
    expect(rail.locator('[data-place="inbox"] svg')).to_have_count(0)   # the Inbox, always hers, has no lock here
    page.screenshot(path=str(SCREENSHOT_DIR / "lists-rail-users.png"))


def test_dragging_a_pin_in_the_column_reorders_the_pins(new_page, wiki):
    page = new_page(viewport=BIG)
    for name in ("a", "b", "c"):
        make_list(page, name, pin=True)
    page.goto("/tasks")
    line = lambda name: page.locator(f'.lists-rail [data-place="list:{name}"]')
    expect(line("a")).to_have_attribute("draggable", "true")
    expect(page.locator('.lists-rail [data-place="mine"]')).not_to_have_attribute("draggable", "true")
    with page.expect_response("**/tasks/pin-order") as saved:
        line("c").drag_to(line("a"), target_position={"x": 20, "y": 2})     # onto a's top half: above it
    assert saved.value.ok
    expect(rail_names(page)).to_have_text(["My tasks", "Inbox", "All tasks", "c", "a", "b"])
    assert '"pinned": ["c", "a", "b"]' in (wiki.root / "_tasks/lists.json").read_text()


def test_the_column_finds_a_list_and_marks_new_ones(new_page, wiki):
    page = new_page(viewport=BIG)
    for i in range(8):
        make_list(page, f"list{i}")
    page.goto("/tasks")
    expect(page.locator(".lists-rail .lists-more-new")).to_have_count(0)    # a first visit knows them all
    field = page.locator(".lists-rail-filter")
    field.fill("list3")
    expect(page.locator(".lists-rail-rest .lists-rail-item:visible")).to_have_text(["list3"])
    expect(page.locator(".lists-rail-all")).to_be_visible()
    field.press("Enter")
    page.wait_for_url(re.compile(r"/tasks\?list=list3$"))
    # Made by someone else since: new beside it, and seen once the column showed it.
    make_list(page, "garden")
    page.goto("/tasks")
    expect(page.locator('.lists-rail [data-place="list:garden"] .lists-more-new')).to_have_text("new")
    expect(page.locator(".lists-more-btn")).not_to_have_class(re.compile("has-new"))
    page.reload()
    expect(page.locator(".lists-rail .lists-more-new")).to_have_count(0)
