"""Folding sections on a page view (js/fold.js): the chevron by each ## and
###, fold all, and what unfolds a folded section."""

import re

import pytest
from playwright.sync_api import expect

from conftest import PHONE, TOUCH_PHONE

BIG = {"width": 1600, "height": 900}

FOLDS = """# Folds

Intro.

## One

First text.

### Detail

Detail text with a [link](#two) to Two.

```
code
```

## Two

Second text with zebra.

## Empty

## Three

Third text.
"""


def _fold(page, hid, alt=False):
    page.locator(f"article #{hid} .section-fold").click(modifiers=["Alt"] if alt else [])


def test_fold_hides_the_section_up_to_the_next_heading(page, wiki):
    path = wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    one = page.locator("article #one .section-fold")
    expect(one).to_have_attribute("aria-expanded", "true")
    # A heading with nothing under it has no chevron.
    expect(page.locator("article #empty .section-fold")).to_have_count(0)
    _fold(page, "one")
    expect(one).to_have_attribute("aria-expanded", "false")
    # A wavy line under a folded heading in place of its straight rule.
    header = page.locator("article .section-header", has=page.locator("#one"))
    expect(header).to_have_css("border-bottom-color", "rgba(0, 0, 0, 0)")
    assert "svg" in header.evaluate("h => getComputedStyle(h, '::after').maskImage")
    expect(page.get_by_text("First text.")).to_be_hidden()
    expect(page.locator("article #detail")).to_be_hidden()        # its ### too
    expect(page.locator("article pre")).to_be_hidden()
    expect(page.get_by_text("Second text")).to_be_visible()       # up to the next ##
    expect(page.locator("article #one")).to_be_visible()
    # Find in page can still reach it.
    assert page.locator("article pre").get_attribute("hidden") == "until-found"
    # A folded section takes no room: Two follows One's heading.
    one_box = page.locator("article #one").bounding_box()
    two_box = page.locator("article #two").bounding_box()
    assert two_box["y"] - (one_box["y"] + one_box["height"]) < 80
    _fold(page, "one")
    expect(page.get_by_text("First text.")).to_be_visible()
    expect(page.locator("article pre")).to_be_visible()
    # View only: the file is untouched, and the page opens unfolded.
    _fold(page, "one")
    assert path.read_text(encoding="utf-8") == FOLDS
    page.reload()
    expect(page.get_by_text("First text.")).to_be_visible()
    assert not page.js_errors and not page.csp_violations


def test_a_folded_subsection_stays_folded_inside_its_section(page, wiki):
    wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    _fold(page, "detail")
    expect(page.get_by_text("Detail text")).to_be_hidden()
    detail = page.locator("article #detail")
    expect(detail).to_have_css("border-bottom-color", "rgba(0, 0, 0, 0)")
    assert "svg" in detail.evaluate("h => getComputedStyle(h, '::after').maskImage")
    expect(page.get_by_text("First text.")).to_be_visible()
    _fold(page, "one")
    _fold(page, "one")
    expect(page.locator("article #detail")).to_be_visible()
    expect(page.get_by_text("Detail text")).to_be_hidden()


def test_alt_click_and_z_fold_every_section(page, wiki):
    wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    _fold(page, "two", alt=True)
    for text in ("First text.", "Second text", "Third text."):
        expect(page.get_by_text(text)).to_be_hidden()
    expect(page.get_by_text("Intro.")).to_be_visible()            # before the first heading
    _fold(page, "three", alt=True)
    expect(page.get_by_text("Third text.")).to_be_visible()
    expect(page.get_by_text("First text.")).to_be_visible()
    page.keyboard.press("z")
    expect(page.get_by_text("Second text")).to_be_hidden()
    _fold(page, "two")                                            # one open: z folds it again
    page.keyboard.press("z")
    expect(page.get_by_text("Second text")).to_be_hidden()
    page.keyboard.press("z")
    expect(page.get_by_text("Second text")).to_be_visible()
    expect(page.get_by_text("Detail text")).to_be_visible()


def test_links_into_a_folded_section_unfold_it(new_page, wiki):
    wiki.write("folds", FOLDS)
    page = new_page(viewport=BIG)
    page.goto("/page/folds")
    page.keyboard.press("z")
    # The side TOC's link to a ###: it and its ## open.
    page.locator(".rail-toc a[href='#detail']").click()
    expect(page.get_by_text("Detail text")).to_be_visible()
    expect(page.get_by_text("First text.")).to_be_visible()
    expect(page.get_by_text("Second text")).to_be_hidden()
    # A link in the text to a folded section.
    page.locator("article p a[href='#two']").click()
    expect(page.get_by_text("Second text")).to_be_visible()
    # Going back to an anchor (hashchange).
    page.keyboard.press("z")
    page.evaluate("location.hash = '#three'")
    expect(page.get_by_text("Third text.")).to_be_visible()
    # The browser's find in page unfolds what it found (beforematch).
    page.evaluate("""document.querySelector('article pre')
      .dispatchEvent(new Event('beforematch', { bubbles: true }))""")
    expect(page.locator("article pre")).to_be_visible()
    expect(page.get_by_text("First text.")).to_be_visible()


def test_copying_a_folded_sections_link_keeps_it_folded(page, wiki):
    wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _fold(page, "two")
    page.locator("article #two").hover()
    page.locator("article #two .heading-link").click()
    expect(page.locator(".task-undo-toast")).to_contain_text("Link copied")
    expect(page.get_by_text("Second text")).to_be_hidden()


def test_search_highlight_in_a_folded_section_unfolds_it(page, wiki):
    wiki.write("folds", FOLDS + "\nzebra again.\n")
    page.goto("/page/folds?hl=zebra")
    expect(page.locator(".search-hl-bar")).to_contain_text("1 of 2")
    page.keyboard.press("z")
    expect(page.get_by_text("Second text")).to_be_hidden()
    page.keyboard.press("n")                                      # 2 of 2, in Three
    expect(page.get_by_text("zebra again.")).to_be_visible()
    page.keyboard.press("n")                                      # back to 1, in Two
    expect(page.get_by_text("Second text")).to_be_visible()


def test_section_jump_skips_folded_headings(page, wiki):
    wiki.write("folds", FOLDS + "\nFiller.\n" * 200)
    page.goto("/page/folds")
    _fold(page, "one")
    page.keyboard.press("]")
    expect(page).to_have_url(re.compile(r"#one$"))
    page.keyboard.press("]")
    expect(page).to_have_url(re.compile(r"#two$"))                # not #detail


def test_presentation_and_print_show_folded_sections(page, wiki):
    wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    page.keyboard.press("z")
    page.keyboard.press("p")
    overlay = page.locator(".presentation-overlay")
    expect(overlay).to_be_visible()
    page.keyboard.press("ArrowRight")
    expect(overlay.get_by_text("First text.")).to_be_visible()
    expect(overlay.locator(".section-fold")).to_be_hidden()
    page.keyboard.press("ArrowRight")                             # ### Detail starts a slide
    expect(overlay.get_by_text("Detail text")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_text("First text.")).to_be_hidden()
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    expect(page.get_by_text("First text.")).to_be_visible()
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    expect(page.get_by_text("First text.")).to_be_hidden()


def test_chevron_sits_in_the_margin_and_the_link_icon_after_the_heading(page, wiki):
    wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    h = page.locator("article #one")
    fold, link = h.locator(".section-fold"), h.locator(".heading-link")
    expect(fold).to_have_css("opacity", "0")
    h.hover()
    expect(fold).not_to_have_css("opacity", "0")
    text = page.locator("article").bounding_box()
    f, l = fold.bounding_box(), link.bounding_box()
    assert f["x"] + f["width"] <= text["x"] + 1                  # in the margin
    assert l["x"] > text["x"] + 20                               # the link icon after the text
    _fold(page, "one")
    page.mouse.move(0, 0)
    expect(fold).not_to_have_css("opacity", "0")                 # a folded one stays shown


@pytest.mark.parametrize("touch", [False, True])
def test_chevron_on_phones_is_shown_after_the_heading(new_page, wiki, touch):
    wiki.write("folds", FOLDS)
    page = new_page(**TOUCH_PHONE) if touch else new_page(viewport=PHONE)
    page.goto("/page/folds")
    h = page.locator("article #one")
    fold = h.locator(".section-fold")
    expect(fold).not_to_have_css("opacity", "0")
    # After the text, so headings stay aligned with or without one.
    assert fold.bounding_box()["x"] > page.locator("article #empty").bounding_box()["x"] + 20
    if touch:
        assert fold.bounding_box()["height"] >= 24
        fold.tap()
    else:
        fold.click()
    expect(page.get_by_text("First text.")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]


def test_folded_label_in_the_wavy_line_unfolds(page, wiki):
    wiki.write("folds", FOLDS)
    page.goto("/page/folds")
    header = page.locator("article .section-header", has=page.locator("#two"))
    label = header.locator(".section-folded-label")
    expect(label).to_be_hidden()
    _fold(page, "two")
    expect(label).to_have_text("folded")
    # In the middle of the line under the header.
    h, l = header.bounding_box(), label.bounding_box()
    assert abs((l["x"] + l["width"] / 2) - (h["x"] + h["width"] / 2)) < 2
    assert l["y"] < h["y"] + h["height"] < l["y"] + l["height"]
    label.click()
    expect(page.get_by_text("Second text")).to_be_visible()
    expect(label).to_be_hidden()
    # A ###'s is on its own line, under the heading.
    _fold(page, "detail")
    detail = page.locator("article #detail")
    label = detail.locator(".section-folded-label")
    d, l = detail.bounding_box(), label.bounding_box()
    assert abs((l["x"] + l["width"] / 2) - (d["x"] + d["width"] / 2)) < 2
    label.click()
    expect(page.get_by_text("Detail text")).to_be_visible()
    # Not part of the heading's text for search highlights.
    page.goto("/page/folds?hl=folded")
    expect(page.locator(".search-hl-bar")).to_have_count(0)
