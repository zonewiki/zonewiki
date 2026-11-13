"""Private pages in the browser (docs/spec/users.md): the Private and Notes
pills, the user menu (Sign out), Make private / Make shared in the page menu, and `[[`
completion offering a new page in your private folder."""

import re

import pytest
from playwright.sync_api import expect

from conftest import TOUCH_PHONE
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    """The browser, signed in as alice (bob exists too)."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    return page


# ── The user menu ───────────────────────────────────────────────────────────────

def test_the_menu_opens_by_click_and_closes_on_escape(alice):
    alice.goto("/page/index")
    btn = alice.locator(".top-user")
    menu = alice.locator(".module-menu")
    expect(btn).to_be_visible()
    expect(menu).to_be_hidden()
    btn.click()
    expect(menu).to_be_visible()
    expect(btn).to_have_attribute("aria-expanded", "true")
    expect(menu.get_by_role("menuitem")).to_have_text(["Connect an assistant", "Sign out"])
    expect(menu.locator(".menu-who")).to_have_text("Signed in as alice")
    # The first item has the focus; it sits under the button.
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    b, m = btn.bounding_box(), menu.bounding_box()
    assert m["y"] >= b["y"] + b["height"] and abs(m["x"] - b["x"]) < 4
    alice.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    # A note is private with users: the dialog says so.
    alice.keyboard.press("c"); alice.keyboard.press("n")
    expect(alice.locator("#modal .modal-label")).to_have_text("New private note")
    expect(alice.locator("#modal .modal-desc")).to_contain_text("Only you see it")
    alice.keyboard.press("Escape")
    expect(btn).to_have_attribute("aria-expanded", "false")
    expect(btn).to_be_focused()


def test_the_menu_works_from_the_keyboard_and_closes_on_a_click_elsewhere(alice):
    alice.goto("/page/index")
    btn = alice.locator(".top-user")
    menu = alice.locator(".module-menu")
    btn.focus()
    alice.keyboard.press("ArrowDown")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    alice.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem", name="Sign out")).to_be_focused()
    alice.keyboard.press("ArrowDown")   # wraps: Sign out is the last item
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    alice.mouse.click(5, 400)
    expect(menu).to_be_hidden()


def test_the_menu_leads_to_connect_an_assistant(alice, wiki):
    """The token is made with the password, shown once, and revoked (/assistant)."""
    alice.goto("/page/index")
    alice.locator(".top-user").click()
    alice.get_by_role("menuitem", name="Connect an assistant").click()
    alice.wait_for_url("**/assistant")
    expect(alice.locator("h1")).to_have_text("Connect an assistant")
    expect(alice.get_by_role("button", name="Make a new token")).to_be_visible()   # she has one (t1)
    alice.get_by_label("Password").fill("wrong")
    alice.get_by_role("button", name="Make a new token").click()
    expect(alice.get_by_role("alert")).to_have_text("Incorrect password: no token was made.")
    alice.get_by_label("Password").fill("pw")
    alice.get_by_role("button", name="Make a new token").click()
    token = alice.locator("#assistant-token")
    expect(token).to_have_text(re.compile(r"^[0-9a-f]{64}$"))
    shown = token.inner_text()
    resp = alice.request.post("/mcp", headers={"Authorization": f"Bearer {shown}"},
                              data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status == 200
    # The page fits a phone: nothing scrolls sideways, the long lines wrap.
    alice.set_viewport_size({"width": 375, "height": 700})
    assert alice.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    alice.get_by_role("button", name="Revoke the token").click()
    expect(alice.get_by_role("status")).to_contain_text("Revoked the token")
    expect(alice.locator("#assistant-token")).to_have_count(0)
    assert alice.request.post("/mcp", headers={"Authorization": f"Bearer {shown}"},
                              data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status == 401


def test_pages_and_tools_are_a_pill_each(alice):
    # Wiki · Private (the pages), Tasks · Notes (the tools); a lock in
    # Private and in Notes, as only you see them.
    alice.goto("/page/index")
    groups = alice.locator(".breadcrumb .module-group")
    expect(groups).to_have_count(2)
    expect(groups.nth(0).locator(".module-label")).to_have_text(["Wiki", "Private"])
    expect(groups.nth(1).locator(".module-label")).to_have_text(["Tasks", "Notes"])
    links = alice.locator(".breadcrumb .module-link")
    for key in ("private", "notes"):
        expect(links.and_(alice.locator(f"[data-module={key}]")).locator(".module-lock")).to_be_visible()
    for key in ("wiki", "tasks"):
        expect(links.and_(alice.locator(f"[data-module={key}]")).locator(".module-lock")).to_have_count(0)
    g0, g1 = groups.nth(0).bounding_box(), groups.nth(1).bounding_box()
    assert g0["x"] + g0["width"] < g1["x"] and abs(g0["y"] - g1["y"]) < 1   # two pills in a row
    expect(alice.locator(".top-user")).not_to_have_attribute("aria-current", "page")
    alice.get_by_role("link", name="Private").click()
    alice.wait_for_url("**/page/private/alice/index")
    expect(alice.locator(".breadcrumb .module-link[data-module=private]")).to_have_attribute("aria-current", "page")
    expect(alice.locator("h1")).to_contain_text("Private")
    expect(alice.locator("h1 .lock")).to_be_visible()
    # The home is a module's own page: not a stop in the navigation trail.
    alice.goto("/page/index")
    expect(alice.locator(".breadcrumb > a")).to_have_count(0)


def test_quicknote_pill_and_g_p_goes_home(alice):
    alice.goto("/page/index")
    alice.get_by_role("link", name="Notes").click()
    alice.wait_for_url("**/notes")
    expect(alice.locator(".breadcrumb .module-link[data-module=notes]")).to_have_attribute("aria-current", "page")
    alice.keyboard.press("g")
    alice.keyboard.press("p")
    alice.wait_for_url("**/page/private/alice/index")


def test_sign_out_is_in_the_menu_on_desktop(alice):
    alice.goto("/page/index")
    expect(alice.locator(".signout-form")).to_be_hidden()
    alice.locator(".top-user").click()
    alice.get_by_role("menuitem", name="Sign out").click()
    alice.wait_for_url("**/login**")


def test_without_users_there_is_no_menu(page, wiki):
    page.goto("/page/index")
    expect(page.locator(".module-menu-btn")).to_have_count(0)
    expect(page.locator(".breadcrumb .module-link[data-module=notes]")).to_be_visible()


def test_the_phone_has_private_and_quicknote_tabs(new_page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1")})
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"username": "alice", "password": "pw"})
    phone.goto("/page/index")
    bar = phone.locator(".action-buttons")
    items = bar.locator(".action-module, button")
    expect(items).to_have_text(["Wiki", "Private", "Tasks", "Notes", "Note", "Task", "Search"])
    assert all(i.bounding_box()["width"] >= 40 for i in items.all())
    # Notes' icon carries a small lock, inside its tab and clear of the label.
    tab, lock = bar.locator("[data-module=notes]"), bar.locator(".action-lock")
    expect(lock).to_have_count(1)
    expect(lock).to_be_visible()
    t, l, lab = tab.bounding_box(), lock.bounding_box(), tab.locator(".action-label").bounding_box()
    assert t["x"] <= l["x"] and l["x"] + l["width"] <= t["x"] + t["width"] and l["y"] + l["height"] <= lab["y"]
    # The user button is for desktops; the name and Sign out stay in the top row.
    expect(phone.locator(".top-user")).to_be_hidden()
    expect(phone.get_by_role("button", name="Sign out")).to_be_visible()
    bar.get_by_role("link", name="Private").tap()
    phone.wait_for_url("**/page/private/alice/index")
    expect(bar.locator(".module-link[data-module=private]")).to_have_attribute("aria-current", "page")


# ── Make shared ──────────────────────────────────────────────────────────────

def test_make_shared_from_the_page_menu(alice, wiki):
    wiki.write("plans", "# Plans\n\nSee [[private/alice/ideas]].\n")
    wiki.write("private/alice/ideas", "# Ideas\n\nOne.\n")
    wiki.write("private/alice/diary", "# Diary\n\nSee [[private/alice/ideas]].\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    alice.goto("/page/ideas")   # a shared page: no move offered, no lock
    alice.goto("/page/plans")
    alice.locator(".page-menu-btn").first.click()
    expect(alice.get_by_role("menuitem", name="Make shared…")).to_have_count(0)
    alice.goto("/page/private/alice/ideas")
    expect(alice.locator("h1 .lock")).to_be_visible()
    alice.locator(".page-menu-btn").first.click()
    expect(alice.get_by_role("menuitem", name="Make private…")).to_have_count(0)
    alice.get_by_role("menuitem", name="Make shared…").click()
    dialog = alice.locator("#page-privacy-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("ideas")
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert (wiki.root / "private/alice/ideas.md").exists()
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Make shared…").click()
    dialog.get_by_role("button", name="Make shared").click()
    alice.wait_for_url("**/page/ideas")
    expect(alice.locator("h1 .lock")).to_have_count(0)
    # The links to it follow, on her private pages and on the shared one.
    assert "[[ideas]]" in wiki.read("plans") and "[[ideas]]" in wiki.read("private/alice/diary")


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
    expect(banner).to_contain_text("link to private/alice/garden instead")
    expect(banner.get_by_role("button")).to_have_text(["Keep shared"])
    # Keep shared: the banner goes, the page stays shared.
    banner.get_by_role("button", name="Keep shared").click()
    expect(banner).to_have_count(0)
    diary.keyboard.press("Control+End")
    diary.keyboard.type("beds")
    expect(diary.locator("#save-dot")).not_to_have_class("save-dot unsaved", timeout=5000)


def test_the_editors_preview_marks_the_link_too(alice, wiki):
    wiki.write("private/alice/diary", "# Diary\n\n")
    alice.goto("/edit/private/alice/diary")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("See [[Garden]]")
    expect(alice.locator("#editor-preview a.wikilink-shared-new")).to_have_count(1)


# ── Private pages are not offered where the text is shared ───────────────────

def test_completion_lists_private_pages_only_on_private_pages(alice, wiki):
    wiki.write("secretplans", "# Shared\n")
    wiki.write("private/alice/secretdiary", "# Diary\n\n")
    wiki.write("notes", "# Notes\n\n")
    items = alice.locator(".wikilink-ac-item")
    alice.goto("/edit/notes")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secret")
    expect(items).to_have_text(["secretplans"])
    alice.goto("/edit/private/alice/secretdiary")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secret")
    expect(items).to_have_text([re.compile("^New private page: secret"), "private/alice/secretdiary", "secretplans"])


def test_a_typed_link_from_a_shared_page_to_a_private_one_is_marked(alice, wiki):
    wiki.write("private/alice/diary", "# Diary\n")
    wiki.write("notes", "# Notes\n\n")
    alice.goto("/edit/notes")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[private/alice/diary]]")
    link = alice.locator("#editor-preview a.wikilink-private")
    expect(link).to_have_attribute("title", "Links to a private page: others see plain text")


def test_without_users_private_is_an_ordinary_folder_for_completion(page, wiki):
    wiki.write("private/diary", "# Diary\n\n")
    wiki.write("notes", "# Notes\n\n")
    items = page.locator(".wikilink-ac-item")
    page.goto("/edit/notes")
    page.keyboard.press("Control+End")
    page.keyboard.type("[[dia")
    expect(items).to_have_text(["private/diary"])
    page.goto("/edit/private/diary")
    page.keyboard.press("Control+End")
    page.keyboard.type("[[note")
    expect(items).to_have_text(["notes"])   # no "New private page" offer


# ── The pills' labels ───────────────────────────────────────────────────────────

_PILL_LABEL_GAPS = """() => [...document.querySelectorAll('.module-switch .module-link')].map(a => {
  const r = a.getBoundingClientRect(), label = a.querySelector('.module-label');
  const cs = getComputedStyle(label);
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const m = ctx.measureText('H');
  const range = document.createRange(); range.selectNodeContents(label.firstChild);
  const box = range.getClientRects()[0];
  // The baseline: the text's line box less the font's descent below it.
  const baseline = box.bottom - m.fontBoundingBoxDescent;
  return [label.textContent, baseline - m.actualBoundingBoxAscent - r.top, r.bottom - baseline];
})"""


def test_the_pills_labels_are_vertically_centred(alice):
    # Regression: padding tuned for Windows' Segoe UI (more below than above)
    # put Wiki, Private, Tasks and Notes high in their pills with other fonts.
    alice.goto("/page/index")
    rows = alice.evaluate(_PILL_LABEL_GAPS)
    assert [r[0] for r in rows] == ["Wiki", "Private", "Tasks", "Notes"]
    for label, above, below in rows:
        assert abs(above - below) <= 1.0, f"{label}: {above:.1f}px above vs {below:.1f}px below"
