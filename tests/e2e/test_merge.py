"""Merge on save in the editor: two people on one page (alice and bob, each
in their own browser context). Changes in different lines merge into the
textarea; the same lines bring up the banner (Reload theirs, Keep mine,
Merge by hand)."""

import re

import pytest
from playwright.sync_api import expect

from conftest import wait_until
from tests import settings

PAGE = "# Plans\n\n## One\n\nFirst.\n\n## Two\n\nSecond.\n"


@pytest.fixture()
def editors(page, new_page, wiki, monkeypatch):
    """alice's and bob's editors on plans, both loaded from the same text."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    wiki.write("plans", PAGE)
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "Plans")
    bob = new_page()
    for pg, name in ((page, "alice"), (bob, "bob")):
        pg.request.post("/login", form={"username": name, "password": "pw"})
        pg.goto("/edit/plans")
    return page, bob


def type_after(pg, text, anchor):
    """Types text at the end of the line that holds anchor."""
    pg.evaluate("""([anchor]) => {
        const t = document.getElementById('editor-textarea');
        const at = t.value.indexOf(anchor) + anchor.length;
        t.focus();
        t.setSelectionRange(at, at);
    }""", [anchor])
    pg.keyboard.type(text)


def saved(pg):
    expect(pg.locator("#save-dot")).not_to_have_class("save-dot unsaved", timeout=6000)


def test_changes_in_other_lines_merge_into_the_textarea(editors, wiki):
    alice, bob = editors
    type_after(bob, " By Bob.", "Second.")
    saved(bob)
    type_after(alice, " By Alice.", "First.")
    saved(alice)
    textarea = alice.locator("#editor-textarea")
    expect(textarea).to_have_value(PAGE.replace("First.", "First. By Alice.").replace("Second.", "Second. By Bob."))
    expect(alice.locator("#merge-note")).to_have_text("Merged bob’s changes")
    expect(alice.locator(".conflict-banner")).to_have_count(0)
    assert wiki.read("plans") == textarea.input_value()
    # Her cursor stayed where she typed.
    assert alice.evaluate("document.getElementById('editor-textarea').selectionStart") == \
        textarea.input_value().index("By Alice.") + len("By Alice.")
    # bob's next save takes alice's line in the same way.
    type_after(bob, "!", "By Bob.")
    saved(bob)
    expect(bob.locator("#editor-textarea")).to_have_value(re.compile("First. By Alice."))
    # The merge came in as one undoable step.
    bob.keyboard.press("Control+z")
    expect(bob.locator("#editor-textarea")).to_have_value(PAGE.replace("Second.", "Second. By Bob.!"))


def test_typing_on_while_a_merge_comes_back_settles(editors, wiki):
    """alice types on before her save's merged answer is back: her textarea
    is left alone, and her next save merges bob's change in again."""
    alice, bob = editors
    type_after(bob, " By Bob.", "Second.")
    saved(bob)
    alice.evaluate("""() => {
        const f = window.fetch;
        window.fetch = async (...args) => {
            const res = await f(...args);
            if (String(args[0]).includes('/api/save/')) await new Promise(r => setTimeout(r, 1000));
            return res;
        };
    }""")
    type_after(alice, " A", "First.")
    alice.wait_for_timeout(2300)             # the save is out, its answer held back
    alice.keyboard.type("B")
    saved(alice)
    want = PAGE.replace("First.", "First. AB").replace("Second.", "Second. By Bob.")
    expect(alice.locator("#editor-textarea")).to_have_value(want, timeout=10000)
    assert wiki.read("plans") == want


@pytest.fixture()
def clash(editors):
    """bob and alice changed the same line; alice's save is refused."""
    alice, bob = editors
    type_after(bob, " By Bob.", "First.")
    saved(bob)
    type_after(alice, " By Alice.", "First.")
    banner = alice.locator(".conflict-banner")
    expect(banner).to_contain_text("bob changed the same lines", timeout=6000)
    return alice, bob, banner


def test_merge_by_hand_waits_for_the_markers_to_go(clash, wiki):
    alice, _, banner = clash
    banner.get_by_role("button", name="Merge by hand").click()
    expect(banner).to_have_count(0)
    textarea = alice.locator("#editor-textarea")
    expect(textarea).to_have_value(re.compile(r"<<<<<<< yours\nFirst\. By Alice\.\n=======\nFirst\. By Bob\.\n>>>>>>> bob"))
    alice.wait_for_timeout(2500)
    assert "By Alice" not in wiki.read("plans")      # not saved with the markers in
    resolved = PAGE.replace("First.", "First. By Bob and Alice.")
    alice.evaluate("""([text]) => {
        const t = document.getElementById('editor-textarea');
        t.focus();
        t.select();
        document.execCommand('insertText', false, text);
    }""", [resolved])
    saved(alice)
    assert wiki.read("plans") == resolved
    expect(alice.locator(".conflict-banner")).to_have_count(0)


def test_reload_theirs_keeps_mine_in_the_history(clash, wiki):
    alice, _, banner = clash
    banner.get_by_role("button", name="Reload theirs").click()
    expect(alice.locator("#editor-textarea")).to_have_value(PAGE.replace("First.", "First. By Bob."))
    log = wiki.git("log", "--format=%an %s").splitlines()
    assert log[:3] == ["alice Reload plans", "alice Set aside an edit of plans", "bob Save plans"]
    assert "First. By Alice." in wiki.git("show", "HEAD~1:plans.md")


def test_keep_mine_overwrites_with_theirs_in_git(clash, wiki):
    alice, _, banner = clash
    banner.get_by_role("button", name="Keep mine").click()
    expect(banner).to_have_count(0)
    assert wiki.read("plans") == PAGE.replace("First.", "First. By Alice.")
    assert wiki.git("log", "-1", "--format=%an %s").strip() == "bob Save plans"
    assert "First. By Bob." in wiki.git("show", "HEAD:plans.md")
    # Her editor saves on from what Keep mine wrote: no merge, no banner.
    type_after(alice, " Again.", "By Alice.")
    saved(alice)
    expect(alice.locator("#merge-note")).to_be_hidden()
    expect(alice.locator(".conflict-banner")).to_have_count(0)
    assert wiki.read("plans") == PAGE.replace("First.", "First. By Alice. Again.")


def test_done_on_a_clash_shows_the_banner_with_its_choices(editors):
    alice, bob = editors
    type_after(bob, " By Bob.", "First.")
    saved(bob)
    type_after(alice, " By Alice.", "First.")
    alice.get_by_role("button", name="Done").click()     # before her auto-save
    banner = alice.locator(".conflict-banner")
    expect(banner).to_contain_text("bob changed the same lines")
    expect(banner.get_by_role("button", name="Merge by hand")).to_be_visible()
    assert "/edit/plans" in alice.url


def test_changes_left_in_conflict_come_back_on_the_next_visit(editors, wiki):
    """alice leaves the page before her clashing change is saved: it is kept
    for her (not in the page), and her next visit offers the choices."""
    alice, bob = editors
    type_after(bob, " By Bob.", "First.")
    saved(bob)
    type_after(alice, " By Alice.", "First.")
    alice.goto("/page/index")                  # before her auto-save: the save on leaving
    kept = wiki.root / ".zone/drafts/alice/plans.json"
    wait_until(alice, kept.exists)
    assert "By Alice" not in wiki.read("plans")
    alice.goto("/edit/plans")
    banner = alice.locator(".conflict-banner")
    expect(banner).to_contain_text("You left changes here")
    expect(banner.get_by_role("button", name="Merge by hand")).to_be_visible()
    banner.get_by_role("button", name="Keep mine").click()
    expect(banner).to_have_count(0)
    assert wiki.read("plans") == PAGE.replace("First.", "First. By Alice.")
    assert not kept.exists()
    assert "First. By Bob." in wiki.git("show", "HEAD:plans.md")     # his, in git


def wait_for(pg, path):
    for _ in range(50):
        if path.exists():
            return True
        pg.wait_for_timeout(100)
    return False


@pytest.mark.parametrize("state", ["the banner up", "markers left"])
def test_leaving_in_a_conflict_keeps_the_text(clash, wiki, state):
    """Leaving the page with the banner up, or in a merge by hand not done:
    nothing is written, but the text comes back on the next visit."""
    alice, _, banner = clash
    if state == "markers left":
        banner.get_by_role("button", name="Merge by hand").click()
    alice.goto("/page/index")
    assert wait_for(alice, wiki.root / ".zone/drafts/alice/plans.json")
    assert "By Alice" not in wiki.read("plans")
    alice.goto("/edit/plans")
    expect(alice.locator(".conflict-banner")).to_contain_text("You left changes here")


def test_a_merge_under_a_dialog_still_reaches_the_textarea(editors, wiki):
    """The merged answer comes while the search dialog is open (the textarea
    can't take focus): the textarea still gets the merged text, and the
    dialog's field none of it."""
    alice, bob = editors
    type_after(bob, " By Bob.", "Second.")
    saved(bob)
    type_after(alice, " By Alice.", "First.")
    alice.keyboard.press("Control+p")
    search = alice.get_by_placeholder("Search pages…")
    expect(search).to_be_visible()
    saved(alice)
    assert search.input_value() == ""
    want = PAGE.replace("First.", "First. By Alice.").replace("Second.", "Second. By Bob.")
    expect(alice.locator("#editor-textarea")).to_have_value(want)
    assert wiki.read("plans") == want


@pytest.mark.parametrize("old,new", [
    ("\nfoo", "bar\nfoo"),          # a change at the very start, the text starting with a line break
    ("a\nb\nc", "a\nB\nc"),
    ("a\nb", "a"),
    ("", "x\n"),
])
def test_replace_changed_lines(page, wiki, old, new):
    wiki.write("plans", PAGE)
    page.goto("/edit/plans")
    got = page.evaluate("""async ([old, text]) => {
        const { replaceChangedLines } = await import('/public/js/editor.js');
        const t = document.createElement('textarea');
        document.body.append(t);
        t.value = old;
        replaceChangedLines(t, text);
        return t.value;
    }""", [old, new])
    assert got == new


def section_clash(page, wiki, theirs):
    """An editor on ## Two, then the page changed to theirs, the same lines too."""
    wiki.write("plans", PAGE)
    page.goto("/edit/plans?section=2")
    wiki.write("plans", theirs)
    type_after(page, " Mine.", "Second.")
    banner = page.locator(".conflict-banner")
    expect(banner).to_be_visible()
    return banner


def test_keep_mine_finds_its_section_after_one_was_added_above(page, wiki):
    theirs = PAGE.replace("## One", "## New\n\nNew.\n\n## One").replace("Second.", "Second, theirs.")
    banner = section_clash(page, wiki, theirs)
    banner.get_by_role("button", name="Keep mine").click()
    expect(banner).to_have_count(0)
    assert wiki.read("plans") == theirs.replace("Second, theirs.", "Second. Mine.")


def test_keep_mine_writes_nothing_when_its_section_is_gone(page, wiki):
    theirs = PAGE.replace("## Two\n\nSecond.", "## Renamed\n\nSecond, theirs.")
    banner = section_clash(page, wiki, theirs)
    banner.get_by_role("button", name="Keep mine").click()
    expect(banner).to_contain_text("nothing was overwritten")
    assert wiki.read("plans") == theirs
