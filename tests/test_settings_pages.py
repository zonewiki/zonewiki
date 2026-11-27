"""The settings pages (docs/spec/navigation.md, The settings pages): the
menu has one entry, Settings, for Connect an assistant, Change password,
Groups and (for an admin) Usage, which show each other as a row of tabs.
Each keeps its address."""
import re

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", ""), "bob": ("bob-pw", ""), "demo": ("demo-pw", "")}


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "demo": {"shared": "yes"}})


def login(client, name):
    client.cookies.clear()
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def body(client, path):
    resp = client.get(path, headers=HOST)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def menu(text):
    """The menu's entries, as (href or "", label)."""
    html = text.split('class="module-menu page-menu"', 1)[1].split("</div>", 2)
    html = html[1] if "menu-who" in html[0] else html[0]
    return re.findall(r'<(?:a href="([^"]*)"|button)[^>]*role="menuitem"[^>]*>([^<]+)<', html)


def tabs(text):
    """The row of tabs: [(href, label)], and the label of the one open."""
    m = re.search(r'<nav class="admin-tabs settings-tabs" aria-label="Settings">(.*?)</nav>', text, re.S)
    if not m:
        return [], None
    row = re.findall(r'<a href="([^"]*)"( aria-current="page")?>([^<]+)</a>', m.group(1))
    current = [label for _, cur, label in row if cur]
    assert len(current) <= 1
    return [(href, label) for href, _, label in row], current[0] if current else None


def crumbs(text):
    """The breadcrumb's words after the module switcher."""
    return re.findall(r'class="breadcrumb-sep">›</span><(?:span|a [^>]*)>([^<]+)<', text)


ALL = [("/assistant", "Assistant"), ("/password", "Password"), ("/groups", "Groups")]


class TestWithUsers:

    def test_the_menu_has_settings_for_the_three_pages(self, client, users):
        entries = menu(body(login(client, "bob"), "/page/index"))
        assert entries == [("/changes", "Changes"), ("/changes?watched=1", "Watched pages"), ("/assistant", "Settings"),
                           ("", "Sharing mode"), ("/help", "Help"), ("", "Sign out")]
        # An admin's has Admin too, an entry of its own.
        entries = menu(body(login(client, "alice"), "/page/index"))
        assert entries[:4] == [("/changes", "Changes"), ("/changes?watched=1", "Watched pages"), ("/assistant", "Settings"), ("/admin", "Admin")]

    @pytest.mark.parametrize("path, label, heading", [
        ("/assistant", "Assistant", "Connect an assistant"),
        ("/password", "Password", "Change your password"),
        ("/groups", "Groups", "Groups")])
    def test_each_page_shows_the_others_as_tabs(self, client, users, path, label, heading):
        text = body(login(client, "bob"), path)
        assert tabs(text) == (ALL, label)
        # The row is above the page's heading, on the sheet.
        assert text.index('class="admin-tabs settings-tabs"') < text.index(f"<h1>{heading}</h1>")

    def test_an_admin_has_usage_too(self, client, users):
        login(client, "alice")
        assert tabs(body(client, "/assistant")) == (ALL + [("/usage", "Usage")], "Assistant")
        text = body(client, "/usage")
        assert tabs(text) == (ALL + [("/usage", "Usage")], "Usage") and crumbs(text) == ["Settings"]
        assert text.index('class="admin-tabs settings-tabs"') < text.index("<h1>Usage</h1>")

    def test_the_breadcrumb_says_settings(self, client, users):
        login(client, "bob")
        for path in ("/assistant", "/password"):
            assert crumbs(body(client, path)) == ["Settings"]
        # Groups is the Groups module's page too, and keeps its own.
        assert crumbs(body(client, "/groups")) == ["Groups"]

    def test_no_other_page_has_the_row(self, client, users):
        login(client, "alice")
        for path in ("/page/index", "/tasks", "/notes", "/admin/users", "/help"):
            assert tabs(body(client, path)) == ([], None)

    def test_a_refused_change_keeps_the_row(self, client, users):
        login(client, "bob")
        resp = client.post("/password", headers=HOST, data={"current": "wrong", "password": "a new password", "again": "a new password"})
        assert resp.status_code == 403 and tabs(resp.get_data(as_text=True)) == (ALL, "Password")
        resp = client.post("/groups", headers=HOST, data={"name": "!!", "people": ""})
        assert resp.status_code >= 400 and tabs(resp.get_data(as_text=True)) == (ALL, "Groups")

    def test_a_shared_account_has_no_password_tab(self, client, users):
        login(client, "demo")
        text = body(client, "/assistant")
        assert tabs(text) == ([("/assistant", "Assistant"), ("/groups", "Groups")], "Assistant")
        assert menu(text)[2] == ("/assistant", "Settings")
        # Its password page, opened by its address, says why, under the tabs it has.
        assert tabs(body(client, "/password")) == ([("/assistant", "Assistant"), ("/groups", "Groups")], None)


class TestWithoutUsers:

    def test_with_a_password_settings_has_three_tabs(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "one password")
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 302
        three = [("/assistant", "Assistant"), ("/password", "Password"), ("/usage", "Usage")]
        text = body(client, "/assistant")
        assert tabs(text) == (three, "Assistant")
        assert menu(text) == [("/changes", "Changes"), ("/assistant", "Settings"), ("", "Sharing mode"), ("/help", "Help"), ("", "Sign out")]
        assert tabs(body(client, "/password")) == (three, "Password")
        assert tabs(body(client, "/usage")) == (three, "Usage")

    def test_without_a_login_the_menu_names_the_one_page(self, client):
        text = body(client, "/assistant")
        assert tabs(text) == ([], None)
        entries = menu(text)
        assert entries[0] == ("/changes", "Changes") and entries[1] == ("/assistant", "Connect an assistant") and entries[2][1] == "Set a login"
        assert "Settings" not in [label for _, label in entries]
        assert crumbs(text) == ["Assistant"]
        # Usage is a page by its address, which the help dialog links to.
        assert ' · <a href="/usage">Usage</a></p>' in text
        text = body(client, "/usage")
        assert tabs(text) == ([], None) and crumbs(text) == ["Usage"] and "<h1>Usage</h1>" in text
