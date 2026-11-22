"""Change password (/password, docs/spec/users.md): whoever is signed in
sets a new password in the browser, as zonewiki user passwd (or config
passwd, without users) does on the host."""

import re

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", ""), "carol": ("carol-pw", "")}
NEW = "a new password"


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"}})


def login(client, name, password=None):
    client.cookies.clear()
    return client.post("/login", data={"username": name, "password": password or USERS[name][0]}, headers=HOST)


@pytest.fixture()
def bob(client, users):
    assert login(client, "bob").status_code == 302
    return client


def config(wiki):
    return (wiki / ".zone" / "config").read_text()


def section(wiki, name):
    """The lines of a user's section in the settings file."""
    m = re.search(rf"^\[user {name}\]\n(.*?)(?=^\[|\Z)", config(wiki), re.M | re.S)
    return m.group(1) if m else None


def change(client, current, new=NEW, again=None, **more):
    return client.post("/password", headers=HOST,
                       data={"current": current, "password": new, "again": new if again is None else again, **more})


def signed_in(client):
    return client.get("/page/index", headers=HOST).status_code == 200


class TestWithUsers:

    def test_the_menu_and_the_help_dialog_offer_it(self, bob):
        body = bob.get("/page/index", headers=HOST).data.decode()
        assert '<a href="/password" role="menuitem" class="menu-password">Change password</a>' in body
        assert '· <a href="/password">Change password</a> · <a href="/groups">Groups</a></p>' in body
        resp = bob.get("/password", headers=HOST)
        body = resp.data.decode()
        assert resp.status_code == 200 and "<h1>Change your password</h1>" in body
        assert "You sign in as <strong>bob</strong>" in body and 'role="status"' not in body
        assert body.count('type="password"') == 3 and 'autocomplete="new-password"' in body
        assert not re.search(r'class="module-link"[^>]*aria-current', body)   # in no module

    def test_a_user_changes_their_password(self, bob, wiki_dir):
        bob.get("/password", headers=HOST)
        before, alices = section(wiki_dir, "bob"), section(wiki_dir, "alice")
        resp = change(bob, "bob-pw")
        assert resp.status_code == 303 and resp.headers["Location"] == "/password?done"
        body = bob.get("/password?done", headers=HOST).data.decode()   # still signed in
        assert "Your password is changed" in body
        after = section(wiki_dir, "bob")
        assert after != before and "password = pbkdf2-sha256:" in after and NEW not in config(wiki_dir)
        assert section(wiki_dir, "alice") == alices
        # The new one signs in, the old one no longer.
        assert login(bob, "bob", "bob-pw").status_code == 200
        assert login(bob, "bob", NEW).status_code == 302 and signed_in(bob)

    def test_it_ends_the_other_sessions_and_keeps_this_one(self, client, users):
        login(client, "bob")
        phone = client.cookies["session"]
        login(client, "bob")
        here = client.cookies["session"]
        assert change(client, "bob-pw").status_code == 303
        assert client.cookies["session"] != here and signed_in(client)
        mine = client.cookies["session"]
        for old in (phone, here):
            client.set_cookie("session", old)
            assert client.get("/page/index", headers=HOST).status_code == 302
        # Still the same session: signing out ends it, a copy of its cookie too.
        client.set_cookie("session", mine)
        client.post("/logout", headers=HOST)
        client.set_cookie("session", mine)
        assert not signed_in(client)

    def test_the_token_stays(self, client, users, wiki_dir):
        login(client, "alice")
        assert change(client, "alice-pw").status_code == 303
        resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp.status_code == 200 and "role = admin" in section(wiki_dir, "alice")

    @pytest.mark.parametrize("current,new,again,says", [
        ("wrong", NEW, None, "Incorrect password: nothing was changed."),
        ("", NEW, None, "Incorrect password: nothing was changed."),
        ("alice-pw", NEW, None, "Incorrect password: nothing was changed."),   # another user's
        ("bob-pw", "short", None, "Choose a password of at least 8 characters."),
        ("bob-pw", NEW, "another new one", "The new passwords differ."),
    ])
    def test_what_it_refuses(self, bob, wiki_dir, current, new, again, says):
        bob.get("/password", headers=HOST)
        before = config(wiki_dir)
        resp = change(bob, current, new, again)
        assert resp.status_code == 403 and says in resp.data.decode()
        assert config(wiki_dir) == before and signed_in(bob)

    def test_wrong_passwords_are_throttled_as_at_the_login(self, bob, wiki_dir):
        for _ in range(5):
            assert change(bob, "wrong").status_code == 403
        before = config(wiki_dir)
        resp = change(bob, "bob-pw")   # even the right one must wait
        assert resp.status_code == 429 and "Try again in" in resp.data.decode()
        assert config(wiki_dir) == before

    def test_a_viewer_changes_theirs(self, client, users):
        login(client, "carol")
        assert change(client, "carol-pw").status_code == 303
        assert login(client, "carol", NEW).status_code == 302

    def test_it_is_only_ones_own(self, bob, wiki_dir):
        """No field names a user: a name sent along changes nothing of theirs."""
        bob.get("/password", headers=HOST)
        alices = section(wiki_dir, "alice")
        assert change(bob, "bob-pw", username="alice", name="alice", user="alice").status_code == 303
        assert section(wiki_dir, "alice") == alices
        assert login(bob, "alice").status_code == 302

    def test_signed_out_it_asks_to_sign_in(self, client, users, wiki_dir):
        resp = client.get("/password", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")
        before = config(wiki_dir)
        assert change(client, "alice-pw").status_code in (302, 401)
        assert config(wiki_dir) == before

    def test_another_sites_post_is_refused(self, bob, wiki_dir):
        bob.get("/password", headers=HOST)
        before = config(wiki_dir)
        resp = bob.post("/password", data={"current": "bob-pw", "password": NEW, "again": NEW},
                        headers={**HOST, "Origin": "https://evil.example"})
        assert resp.status_code == 403 and config(wiki_dir) == before


class TestWithoutUsers:

    def test_it_changes_the_wikis_password(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "one password")
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 302
        other = client.cookies["session"]
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 302
        body = client.get("/password", headers=HOST).data.decode()
        assert "<h1>Change the password</h1>" in body and "Everyone signs in" in body
        assert '<a href="/password">Change password</a>' in client.get("/page/index", headers=HOST).data.decode()
        assert change(client, "wrong").status_code == 403
        assert change(client, "one password").status_code == 303
        assert "The password is changed" in client.get("/password?done", headers=HOST).data.decode()
        assert "[user" not in config(wiki_dir) and NEW not in config(wiki_dir)
        client.set_cookie("session", other)
        assert not signed_in(client)
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 200
        assert client.post("/login", data={"password": NEW}, headers=HOST).status_code == 302

    def test_without_a_login_there_is_none(self, client):
        assert client.get("/password", headers=HOST).status_code == 404
        assert change(client, "").status_code == 404
        assert 'href="/password"' not in client.get("/page/index", headers=HOST).data.decode()


class TestASharedAccount:
    """An account several people sign in to (shared = yes): none of them
    changes its password, or the others are locked out."""

    @pytest.fixture()
    def demo(self, client, monkeypatch):
        monkeypatch.setattr(settings, "USERS", {**USERS, "demo": ("demo-pw", "")})
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "demo": {"shared": "yes"}})
        assert client.post("/login", data={"username": "demo", "password": "demo-pw"}, headers=HOST).status_code == 302
        return client

    def test_the_menu_and_the_help_dialog_leave_it_out(self, demo):
        body = demo.get("/page/index", headers=HOST).data.decode()
        assert 'href="/password"' not in body
        assert 'class="menu-signout"' in body and 'class="menu-assistant"' in body   # the rest of the menu is there

    def test_the_page_says_why_and_has_no_form(self, demo):
        resp = demo.get("/password?done", headers=HOST)
        body = resp.data.decode()
        assert resp.status_code == 200 and "<h1>Change your password</h1>" in body
        assert "<strong>demo</strong> is an account several people sign in to" in body
        assert "<form method=\"post\" action=\"/password\"" not in body and 'type="password"' not in body
        assert "is changed" not in body

    def test_the_right_password_changes_nothing(self, demo, wiki_dir):
        before = section(wiki_dir, "demo")
        resp = change(demo, "demo-pw")
        assert resp.status_code == 403
        assert "demo is an account several people sign in to: its password is set by whoever runs this wiki" in resp.data.decode()
        assert section(wiki_dir, "demo") == before
        assert signed_in(demo)
        assert login(demo, "demo", NEW).status_code == 200            # the new one signs nobody in
        assert login(demo, "demo", "demo-pw").status_code == 302

    def test_a_wrong_password_is_not_counted(self, demo):
        """Refused before the password is looked at: a visitor's wrong tries
        there don't make the others wait at the login."""
        for _ in range(12):
            assert change(demo, "wrong").status_code == 403
        assert login(demo, "demo", "demo-pw").status_code == 302

    def test_another_user_of_the_wiki_still_changes_theirs(self, demo, wiki_dir):
        assert login(demo, "bob").status_code == 302
        assert 'href="/password"' in demo.get("/page/index", headers=HOST).data.decode()
        assert change(demo, "bob-pw").status_code == 303
