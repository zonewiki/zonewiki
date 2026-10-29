"""Private pages in the browser (docs/spec/users.md): the ☰ menu (Private home,
Scratch notes), Make private / Make shared in the page menu, and `[[`
completion offering a new page in your private folder."""

import re

import pytest
from playwright.sync_api import expect

from conftest import TOUCH_PHONE
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    """The browser, signed in as alice (bob exists too)."""
    monkeypatch.setattr(settings, "LEAF_USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    return page


# ── The ☰ menu ───────────────────────────────────────────────────────────────

def test_the_menu_opens_by_click_and_closes_on_escape(alice):
    alice.goto("/page/index")
    btn = alice.locator(".breadcrumb .module-menu-btn")
    menu = alice.locator(".module-menu")
    expect(btn).to_be_visible()
    expect(menu).to_be_hidden()
    btn.click()
    expect(menu).to_be_visible()
    expect(btn).to_have_attribute("aria-expanded", "true")
    expect(menu.get_by_role("menuitem")).to_have_text(["🔒Private home", "Scratch notes"])
    # The first item has the focus; it sits under the button.
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    b, m = btn.bounding_box(), menu.bounding_box()
    assert m["y"] >= b["y"] + b["height"] and abs(m["x"] - b["x"]) < 4
    alice.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(btn).to_have_attribute("aria-expanded", "false")
    expect(btn).to_be_focused()


def test_the_menu_works_from_the_keyboard_and_closes_on_a_click_elsewhere(alice):
    alice.goto("/page/index")
    btn = alice.locator(".breadcrumb .module-menu-btn")
    menu = alice.locator(".module-menu")
    btn.focus()
    alice.keyboard.press("ArrowDown")
    expect(menu).to_be_visible()
    alice.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem").nth(1)).to_be_focused()
    alice.keyboard.press("ArrowDown")   # wraps
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    alice.mouse.click(5, 400)
    expect(menu).to_be_hidden()


def test_private_home_is_reached_from_the_menu_and_the_menu_marks_it(alice):
    alice.goto("/page/index")
    expect(alice.locator(".breadcrumb .module-menu-btn")).not_to_have_attribute("aria-current", "page")
    alice.locator(".breadcrumb .module-menu-btn").click()
    alice.get_by_role("menuitem", name="Private home").click()
    alice.wait_for_url("**/page/private/alice/index")
    expect(alice.locator(".breadcrumb .module-menu-btn")).to_have_attribute("aria-current", "page")
    expect(alice.locator("h1")).to_contain_text("Private")
    expect(alice.locator("h1 .lock")).to_be_visible()
    # The home is a module's own page: not a stop in the navigation trail.
    alice.goto("/page/index")
    expect(alice.locator(".breadcrumb > a")).to_have_count(0)


def test_scratch_notes_from_the_menu_and_g_p_goes_home(alice):
    alice.goto("/page/index")
    alice.locator(".breadcrumb .module-menu-btn").click()
    alice.get_by_role("menuitem", name="Scratch notes").click()
    alice.wait_for_url("**/page/_scratch")
    expect(alice.locator(".breadcrumb .module-menu-btn")).to_have_attribute("aria-current", "page")
    alice.keyboard.press("g")
    alice.keyboard.press("p")
    alice.wait_for_url("**/page/private/alice/index")


def test_without_users_there_is_no_menu(page, wiki):
    page.goto("/page/index")
    expect(page.locator(".module-menu-btn")).to_have_count(0)
    expect(page.locator(".breadcrumb .module-link[data-module=scratch]")).to_be_visible()


def test_the_phone_has_the_menu_as_a_tab_and_a_bottom_sheet(new_page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "LEAF_USERS", {"alice": ("pw", "t1")})
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"username": "alice", "password": "pw"})
    phone.goto("/page/index")
    bar = phone.locator(".action-buttons")
    expect(bar.locator(".action-module, button")).to_have_text(["Wiki", "Deck", "More", "Note", "Task", "Search"])
    # The switcher's own ☰ is for desktops.
    expect(phone.locator(".breadcrumb .module-menu-btn")).to_be_hidden()
    menu = phone.locator(".module-menu")
    bar.locator(".module-menu-btn").tap()
    expect(menu).to_be_visible()
    m, b = menu.bounding_box(), bar.bounding_box()
    assert m["x"] == 0 and m["width"] == 390 and m["y"] + m["height"] <= 844   # a sheet across the bottom
    menu.get_by_role("menuitem", name="Private home").tap()
    phone.wait_for_url("**/page/private/alice/index")
    expect(bar.locator(".module-menu-btn")).to_have_attribute("aria-current", "page")


# ── Make private / Make shared ───────────────────────────────────────────────

def test_make_private_and_make_shared_from_the_page_menu(alice, wiki):
    wiki.write("plans", "# Plans\n\nSee [[ideas]].\n")
    wiki.write("ideas", "# Ideas\n\nOne.\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    alice.goto("/page/ideas")
    expect(alice.locator("h1 .lock")).to_have_count(0)
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Make private…").click()
    dialog = alice.locator("#page-privacy-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("private/alice/ideas")
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert (wiki.root / "ideas.md").exists()
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Make private…").click()
    dialog.get_by_role("button", name="Make private").click()
    alice.wait_for_url("**/page/private/alice/ideas")
    expect(alice.locator("h1 .lock")).to_be_visible()
    assert "[[private/alice/ideas]]" in wiki.read("plans")
    # And back.
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Make shared…").click()
    dialog.get_by_role("button", name="Make shared").click()
    alice.wait_for_url("**/page/ideas")
    expect(alice.locator("h1 .lock")).to_have_count(0)
    assert "[[ideas]]" in wiki.read("plans")


def test_the_page_list_and_search_mark_private_pages(alice, wiki):
    wiki.write("private/alice/diary", "# Diary\n\nzebracorn\n")
    wiki.write("ideas", "# Ideas\n\nzebracorn\n")
    alice.goto("/page/index")
    alice.keyboard.press("Control+p")
    items = alice.locator("#search-modal .page-list-item")
    expect(items.filter(has_text="Diary").locator(".lock")).to_be_visible()
    expect(items.filter(has_text="Ideas").locator(".lock")).to_have_count(0)
    alice.locator("#modal-filter").fill("zebracorn")
    cards = alice.locator("#search-modal .search-result-card")
    expect(cards.filter(has_text="Diary").locator(".result-meta")).to_contain_text("🔒")
    expect(cards.filter(has_text="Ideas").locator(".result-meta")).not_to_contain_text("🔒")


# ── [[ completion ────────────────────────────────────────────────────────────

def test_completion_offers_a_new_private_page_on_a_private_page(alice, wiki):
    wiki.write("private/alice/diary", "# Diary\n\n")
    alice.goto("/edit/private/alice/diary")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("See [[Garden beds")
    item = alice.locator(".wikilink-ac-item")
    expect(item).to_have_text(re.compile(r"New private page: Garden beds"))
    alice.keyboard.press("Enter")
    expect(alice.locator("#editor-textarea")).to_have_value(re.compile(r"See \[\[private/alice/garden-beds\|Garden beds\]\]"))


def test_the_offer_comes_first_unless_the_name_is_an_existing_page(alice, wiki):
    wiki.write("garden", "# Garden\n")
    wiki.write("gardening", "# Gardening\n")
    wiki.write("old-plans", "# Old plans\n")
    wiki.write("plans", "# Plans\n")
    wiki.write("private/alice/diary", "# Diary\n\n")
    alice.goto("/edit/private/alice/diary")
    alice.keyboard.press("Control+End")
    items = alice.locator(".wikilink-ac-item")
    # Partly typed: the offer first, so Enter makes the private page.
    alice.keyboard.type("[[gard")
    expect(items).to_have_text([re.compile("^New private page: gard"), "garden", "gardening"])
    alice.keyboard.press("Enter")
    expect(alice.locator("#editor-textarea")).to_have_value(re.compile(r"\[\[private/alice/gard\|gard\]\]"))
    # A whole page name: that page first (even when others sort before it), the offer second.
    alice.keyboard.type(" [[plans")
    expect(items).to_have_text(["plans", re.compile("^New private page: plans"), "old-plans"])
    alice.keyboard.press("Enter")
    expect(alice.locator("#editor-textarea")).to_have_value(re.compile(r"\[\[plans\]\]"))


def test_a_shared_page_gets_no_offer(alice, wiki):
    wiki.write("notes", "# Notes\n\n")
    alice.goto("/edit/notes")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[brandnew")
    expect(alice.locator(".wikilink-ac-item")).to_have_count(0)


# ── A link from a private page to a new shared page ──────────────────────────

@pytest.fixture()
def diary(alice, wiki):
    wiki.write("private/alice/diary", "# Diary\n\nSee [[Garden]].\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    alice.goto("/page/private/alice/diary")
    return alice


def test_the_link_is_marked_and_opens_a_warning_editor(diary):
    link = diary.locator("a.wikilink-shared-new")
    expect(link).to_have_attribute("title", "Creates a shared page")
    assert "\U0001F310" in link.evaluate("el => getComputedStyle(el, '::after').content")
    link.click()
    diary.wait_for_url("**/edit/garden?from=private")
    banner = diary.locator("#shared-new-banner")
    expect(banner).to_contain_text("This new page is shared")
    # Keep shared: the banner goes, the page stays shared.
    banner.get_by_role("button", name="Keep shared").click()
    expect(banner).to_have_count(0)
    diary.keyboard.press("Control+End")
    diary.keyboard.type("beds")
    expect(diary.locator("#save-dot")).not_to_have_class("save-dot unsaved", timeout=5000)


def test_make_it_private_instead_moves_what_is_typed(diary, wiki):
    diary.locator("a.wikilink-shared-new").click()
    diary.wait_for_url("**/edit/garden?from=private")
    diary.keyboard.press("Control+End")
    diary.keyboard.type("beds and paths")
    diary.get_by_role("button", name="Make it private instead").click()
    diary.wait_for_url("**/edit/private/alice/garden")
    expect(diary.locator("#editor-textarea")).to_have_value(re.compile(r"beds and paths"))
    expect(diary.locator("#shared-new-banner")).to_have_count(0)
    # Nothing shared was left behind, nor ever committed.
    diary.wait_for_timeout(500)   # a save on the way out would have made it by now
    assert not (wiki.root / "garden.md").exists()
    assert (wiki.root / "private/alice/garden.md").read_text().endswith("beds and paths")
    assert wiki.git("log", "--all", "--format=%s", "--", "garden.md").strip() == ""
    # The rename re-points the diary's link: it now goes to the private page.
    assert "[[private/alice/garden]]" in wiki.read("private/alice/diary")


def test_make_it_private_instead_before_typing_only_moves_the_editor(diary, wiki):
    diary.locator("a.wikilink-shared-new").click()
    diary.get_by_role("button", name="Make it private instead").click()
    diary.wait_for_url("**/edit/private/alice/garden")
    expect(diary.locator("#editor-textarea")).to_have_value(re.compile(r"^# Garden"))
    diary.wait_for_timeout(500)
    assert not (wiki.root / "garden.md").exists()


def test_the_editors_preview_marks_the_link_too(alice, wiki):
    wiki.write("private/alice/diary", "# Diary\n\n")
    alice.goto("/edit/private/alice/diary")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("See [[Garden]]")
    expect(alice.locator("#editor-preview a.wikilink-shared-new")).to_have_count(1)
