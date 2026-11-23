"""An assistant connects through OAuth, in a browser (docs/spec/mcp.md,
Connecting with OAuth): the page policy lets a form lead only to Zonewiki
itself, so Allow answers with a page that sends the browser on to the
client. These tests hold that it gets there in Chromium."""

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect

from tests import settings

CALLBACK = "https://client.example/callback"
VERIFIER = "v" * 43 + "-zone-browser-test"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


def client_side(page):
    """The client's callback page, answered here (the tests run offline)."""
    page.route("https://client.example/**", lambda r: r.fulfill(
        status=200, content_type="text/html", body="<h1>Back at the client</h1>"))


def ask(page, name="Claude"):
    """A client registered, and the address it opens in the user's browser."""
    resp = page.request.post("/oauth/register", data={"client_name": name, "redirect_uris": [CALLBACK]})
    assert resp.status == 201
    client_id = resp.json()["client_id"]
    return client_id, "/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK, "state": "s-1",
        "code_challenge": CHALLENGE, "code_challenge_method": "S256"})


def test_allow_sends_the_browser_back_to_the_client_and_its_code_works(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", ""), "bob": ("pw", "")})
    client_side(page)
    client_id, address = ask(page)
    # Signed out: through the login, and back to the question.
    page.goto(address)
    page.get_by_placeholder("User name").fill("alice")
    page.get_by_placeholder("Password").fill("pw")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("h2")).to_have_text("Connect an assistant?")
    expect(page.locator(".oauth-client")).to_have_text("Claude")
    expect(page.locator(".oauth-host")).to_have_text("client.example")
    # The page fits a phone.
    page.set_viewport_size({"width": 375, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.get_by_label("Password").fill("wrong")
    page.get_by_role("button", name="Allow").click()
    expect(page.get_by_role("alert")).to_have_text("Incorrect password: nothing was allowed.")
    page.get_by_label("Password").fill("pw")
    page.keyboard.press("Enter")   # Enter allows, as the first button
    page.wait_for_url(re.compile(r"^https://client\.example/callback\?"))
    expect(page.locator("h1")).to_have_text("Back at the client")
    assert page.csp_violations == []
    query = parse_qs(urlsplit(page.url).query)
    assert query["state"] == ["s-1"] and "error" not in query
    # The code is the client's to turn into tokens; /mcp takes them.
    resp = page.request.post("/oauth/token", form={
        "grant_type": "authorization_code", "client_id": client_id, "code": query["code"][0],
        "redirect_uri": CALLBACK, "code_verifier": VERIFIER})
    assert resp.status == 200
    access = resp.json()["access_token"]
    resp = page.request.post("/mcp", headers={"Authorization": f"Bearer {access}"},
                             data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status == 200
    # Connect an assistant lists it, and ends it.
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto("/assistant")
    row = page.locator(".assistant-connections tbody tr")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Claude")
    expect(row).to_contain_text("client.example")
    page.set_viewport_size({"width": 375, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    row.get_by_role("button", name="End").click()
    expect(page.get_by_role("status")).to_contain_text("Ended the connection")
    assert page.request.post("/mcp", headers={"Authorization": f"Bearer {access}"},
                             data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status == 401


def test_deny_sends_the_browser_back_with_the_refusal(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "")})
    client_side(page)
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    _, address = ask(page)
    page.goto(address)
    page.get_by_role("button", name="Deny").click()
    page.wait_for_url(re.compile(r"^https://client\.example/callback\?"))
    query = parse_qs(urlsplit(page.url).query)
    assert query["error"] == ["access_denied"] and "code" not in query
    assert page.csp_violations == []


def test_only_read_is_chosen_on_the_question_and_said_on_the_page(page, wiki, monkeypatch):
    from tests.e2e.conftest import SCREENSHOT_DIR
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "")})
    client_side(page)
    _, address = ask(page)
    page.goto(address)
    page.get_by_placeholder("User name").fill("alice")
    page.get_by_placeholder("Password").fill("pw")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_label("Read and change")).to_be_checked()
    page.screenshot(path=str(SCREENSHOT_DIR / "oauth-may.png"))
    page.set_viewport_size({"width": 375, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.get_by_label("Only read").check()
    page.get_by_label("Password").fill("pw")
    page.get_by_role("button", name="Allow").click()
    page.wait_for_url(re.compile(r"^https://client\.example/callback\?"))
    code = parse_qs(urlsplit(page.url).query)["code"][0]
    client_id = parse_qs(urlsplit(address).query)["client_id"][0]
    tokens = page.request.post("/oauth/token", form={
        "grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": CALLBACK,
        "code_verifier": VERIFIER}).json()
    call = page.request.post("/mcp", headers={"Authorization": f"Bearer {tokens['access_token']}"}, data={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "write_page", "arguments": {"slug": "x", "content": "x"}}}).json()
    assert call["result"]["isError"] and "only reads" in call["result"]["content"][0]["text"]
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto("/assistant")
    row = page.locator(".assistant-connections tbody tr")
    expect(row.locator(".assistant-may")).to_have_text("Only read")
    expect(row.locator(".assistant-change > summary")).to_have_text("Change…")
    page.screenshot(path=str(SCREENSHOT_DIR / "assistant-may.png"))
    assert page.csp_violations == []


def test_what_it_sees_is_chosen_on_the_question(page, wiki, monkeypatch):
    from tests.e2e.conftest import SCREENSHOT_DIR
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev, ops"}})
    wiki.write("private/alice/diary", "# Diary\n\nwombatquill\n")
    wiki.write("groups/dev/roadmap", "# Roadmap\n\nwombatquill\n")
    wiki.write("groups/ops/runbook", "# Runbook\n\nwombatquill\n")
    client_side(page)
    _, address = ask(page)
    page.goto(address)
    page.get_by_placeholder("User name").fill("alice")
    page.get_by_placeholder("Password").fill("pw")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_label("The shared wiki")).to_be_disabled()
    for place in ("Your private pages, notes and tasks", "The group dev", "The group ops"):
        expect(page.get_by_label(place)).to_be_checked()
    page.get_by_label("Your private pages, notes and tasks").uncheck()
    page.get_by_label("The group ops").uncheck()
    page.screenshot(path=str(SCREENSHOT_DIR / "oauth-sees.png"), full_page=True)
    page.set_viewport_size({"width": 375, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.get_by_label("Password").fill("pw")
    page.get_by_role("button", name="Allow").click()
    page.wait_for_url(re.compile(r"^https://client\.example/callback\?"))
    code = parse_qs(urlsplit(page.url).query)["code"][0]
    client_id = parse_qs(urlsplit(address).query)["client_id"][0]
    tokens = page.request.post("/oauth/token", form={
        "grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": CALLBACK,
        "code_verifier": VERIFIER}).json()
    found = page.request.post("/mcp", headers={"Authorization": f"Bearer {tokens['access_token']}"}, data={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "grep_wiki", "arguments": {"pattern": "wombatquill"}}}).json()["result"]["content"][0]["text"]
    assert "groups/dev/roadmap" in found and "diary" not in found and "runbook" not in found
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto("/assistant")
    expect(page.locator(".assistant-sees")).to_have_text("Sees only: the shared wiki; the group dev")
    page.locator(".assistant-connections").screenshot(path=str(SCREENSHOT_DIR / "assistant-sees.png"))
    page.set_viewport_size({"width": 375, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.csp_violations == []


def connected(page, address, client_id):
    """Allow as whoever is signed in: the new connection's access token."""
    page.goto(address)
    page.get_by_label("Password").fill("pw")
    page.get_by_role("button", name="Allow").click()
    page.wait_for_url(re.compile(r"^https://client\.example/callback\?"))
    code = parse_qs(urlsplit(page.url).query)["code"][0]
    return page.request.post("/oauth/token", form={
        "grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": CALLBACK,
        "code_verifier": VERIFIER}).json()["access_token"]


def test_change_opens_in_a_dialog_and_asks_the_password_only_to_give_more(page, wiki, monkeypatch):
    from tests.e2e.conftest import SCREENSHOT_DIR
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}})
    client_side(page)
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    client_id, address = ask(page)
    access = connected(page, address, client_id)

    def writes():
        return not page.request.post("/mcp", headers={"Authorization": f"Bearer {access}"}, data={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "write_page", "arguments": {"slug": "x", "content": "x"}}}).json()["result"]["isError"]

    page.goto("/assistant")
    row = page.locator(".assistant-connections tbody tr")
    dialog = page.locator("#connection-change-dialog")
    password = dialog.get_by_label("Password")
    expect(row.locator(".assistant-change-form")).to_be_hidden()
    row.locator(".assistant-change > summary").click()
    expect(dialog).to_be_visible()
    expect(dialog.locator(".modal-label")).to_have_text("Change Claude")
    # What it has now is ticked; taking away asks no password.
    expect(dialog.get_by_label("Read and change")).to_be_checked()
    for place in ("Your private pages, notes and tasks", "The group dev"):
        expect(dialog.get_by_label(place)).to_be_checked()
    expect(password).to_be_hidden()
    dialog.get_by_label("Only read").check()
    dialog.get_by_label("The group dev").uncheck()
    expect(password).to_be_hidden()
    page.set_viewport_size({"width": 375, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.set_viewport_size({"width": 1280, "height": 800})
    dialog.screenshot(path=str(SCREENSHOT_DIR / "assistant-change.png"))
    dialog.get_by_role("button", name="Save").click()
    page.wait_for_url(re.compile(r"/assistant\?done=change$"))
    expect(page.get_by_role("status")).to_contain_text("Changed what that assistant may do and see")
    expect(row.locator(".assistant-may")).to_contain_text("Only read")
    expect(row.locator(".assistant-sees")).to_have_text("Sees only: the shared wiki; your private pages, notes and tasks")
    assert not writes()                                   # its token, from its next call

    # Cancel keeps nothing of what was ticked.
    row.locator(".assistant-change > summary").click()
    dialog.get_by_label("The group dev").check()
    expect(password).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    row.locator(".assistant-change > summary").click()
    expect(dialog.get_by_label("The group dev")).not_to_be_checked()
    expect(password).to_be_hidden()

    # Giving more shows the password field; a wrong one comes back open, as chosen.
    dialog.get_by_label("Read and change").check()
    expect(password).to_be_visible()
    dialog.get_by_role("button", name="Save").click()       # required: the browser asks, nothing is sent
    expect(dialog).to_be_visible()
    password.fill("wrong")
    dialog.get_by_role("button", name="Save").click()
    page.wait_for_url(re.compile(r"/change$"))
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("alert")).to_have_text("Incorrect password: nothing was changed.")
    expect(dialog.get_by_label("Read and change")).to_be_checked()
    expect(password).to_be_visible()
    password.fill("pw")
    dialog.get_by_role("button", name="Save").click()
    page.wait_for_url(re.compile(r"/assistant\?done=change$"))
    expect(row.locator(".assistant-may")).to_contain_text("Read and change")
    assert writes()
