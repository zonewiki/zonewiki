"""The shortcuts dialog (? or Ctrl+/): a cheat sheet that fits a desktop
screen without scrolling, scrolls inside a phone's, and names every key the
page's code answers."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import PHONE, SCREENSHOT_DIR, box_of
from tests import settings

PUBLIC = Path(__file__).parents[2] / "public"


@pytest.fixture()
def signed_in(new_page, wiki, monkeypatch):
    """A page maker signed in as alice: with users the dialog has its most
    rows (g p, g g, a, @)."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1")})

    def make(**kwargs):
        page = new_page(**kwargs)
        page.request.post("/login", form={"username": "alice", "password": "pw"})
        page.goto("/page/index")
        return page
    return make


def open_dialog(page):
    page.keyboard.press("?")
    dialog = page.locator("#shortcuts-modal")
    expect(dialog).to_be_visible()
    return dialog


def geometry(dialog):
    return dialog.evaluate("""d => {
      const r = d.getBoundingClientRect();
      return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
              scroll: d.scrollHeight, client: d.clientHeight,
              vw: innerWidth, vh: innerHeight};
    }""")


@pytest.mark.parametrize("size", [(1280, 720), (1440, 900), (1920, 1080)])
def test_the_whole_cheat_sheet_fits_a_desktop_screen(signed_in, size):
    w, h = size
    for scheme in ("light", "dark"):
        page = signed_in(viewport={"width": w, "height": h}, color_scheme=scheme)
        dialog = open_dialog(page)
        page.screenshot(path=str(SCREENSHOT_DIR / f"shortcuts-{w}x{h}-{scheme}.png"))
        g = geometry(dialog)
        assert g["top"] >= 0 and g["left"] >= 0, g
        assert g["bottom"] <= g["vh"] and g["right"] <= g["vw"], g
        assert g["scroll"] <= g["client"], f"the dialog scrolls at {w}×{h}: {g}"
        # The groups sit side by side, not in one long column.
        lefts = {round(b["x"]) for b in (box_of(s) for s in dialog.locator(".sc-group").all())}
        assert len(lefts) >= 4, lefts


def test_on_a_phone_it_scrolls_inside_the_screen(signed_in):
    for scheme in ("light", "dark"):
        page = signed_in(viewport=PHONE, color_scheme=scheme)
        dialog = open_dialog(page)
        g = geometry(dialog)
        assert g["top"] >= 0 and g["bottom"] <= g["vh"], g
        assert g["left"] >= 0 and g["right"] <= g["vw"], g
        assert g["scroll"] > g["client"], g   # more than fits: it scrolls, inside
        page.screenshot(path=str(SCREENSHOT_DIR / f"shortcuts-phone-{scheme}.png"))
        # Scrolled to its end, the × to close it is still on screen.
        dialog.evaluate("d => d.scrollTo(0, d.scrollHeight)")
        close = dialog.locator(".sc-close")
        expect(close).to_be_in_viewport()
        expect(dialog.locator(".sc-foot")).to_be_in_viewport()
        close.click()
        expect(dialog).to_be_hidden()


# How a key the code compares e.key with is written on a key cap.
CAPS = {"ArrowDown": "↓", "ArrowUp": "↑", "ArrowRight": "→", "ArrowLeft": "←",
        " ": "Space", "Escape": "Esc", "ContextMenu": "Menu", "PageDown": "PgDn",
        "PageUp": "PgUp"}


def keys_in(js: str) -> set[str]:
    """The keys a module's keydown handlers compare e.key with."""
    keys = set(re.findall(r"e\.key === '([^']+)'", js))
    for group in re.findall(r"\[((?:'[^']+',\s*)+'[^']+')\]\.includes\(e\.key\)", js):
        keys.update(re.findall(r"'([^']+)'", group))
    if "/^[0-7]$/.test(e.key)" in js:
        keys.update("01234567")
    return keys


def test_every_key_the_code_answers_is_in_the_dialog(signed_in):
    page = signed_in()
    dialog = open_dialog(page)
    caps = set()
    for text in dialog.locator("dt kbd, h3 kbd").all_inner_texts():
        caps.add(text.lower())
        caps.update(p.lower() for p in re.split(r"[+/]", text) if p)
    caps.update(str(n) for n in range(1, 8))   # 1–7
    missing = {}
    for module in ("js/taskkeys.js", "js/shortcuts.js", "js/present.js", "js/search.js", "js/mdedit.js"):
        for key in keys_in((PUBLIC / module).read_text(encoding="utf-8")):
            if CAPS.get(key, key).lower() not in caps:
                missing.setdefault(module, []).append(key)
    assert not missing, missing
    # g's destinations, as shortcuts.js names them.
    dest = re.search(r"const dest = \{([^}]*)\}", (PUBLIC / "js/shortcuts.js").read_text(encoding="utf-8"))
    go_to = dialog.locator(".sc-group", has=page.locator("h3", has_text="Go to"))
    shown = set(go_to.locator("dt kbd").all_inner_texts())
    assert set(re.findall(r"(\w): ", dest.group(1))) | {"p", "g"} <= shown
