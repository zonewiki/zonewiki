"""Connect an assistant (/assistant, docs/spec/mcp.md): whoever is signed
in makes and revokes their MCP token in the browser, as zonewiki user token
(or config token, without users) does on the host."""

import hashlib
import re

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", ""), "carol": ("carol-pw", "")}
TOKEN = re.compile(r'id="assistant-token">([0-9a-f]{64})<')


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"}})


def login(client, name):
    client.post("/logout", headers=HOST)
    resp = client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST)
    assert resp.status_code == 302
    return client


@pytest.fixture()
def bob(client, users):
    return login(client, "bob")


def config(wiki):
    return (wiki / ".zone" / "config").read_text()


def section(wiki, name):
    """The lines of a user's section in the settings file."""
    m = re.search(rf"^\[user {name}\]\n(.*?)(?=^\[|\Z)", config(wiki), re.M | re.S)
    return m.group(1) if m else None


def kept(token):
    return "mcp-token = sha256:" + hashlib.sha256(token.encode()).hexdigest()


def make(client, password=None):
    """POST /assistant/token: the response and the token it shows (None: none)."""
    resp = client.post("/assistant/token", data={} if password is None else {"password": password}, headers=HOST)
    m = TOKEN.search(resp.data.decode())
    return resp, m.group(1) if m else None


def mcp(client, token, tool=None, **args):
    """A call of /mcp with token: the status, and a tool's text."""
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    if tool:
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json=message)
    text = ""
    if tool and resp.status_code == 200:
        text = "".join(c["text"] for c in resp.get_json()["result"]["content"])
    return resp.status_code, text


class TestWithUsers:

    def test_the_menu_offers_it_and_the_page_says_where_to_connect(self, bob):
        assert '<a href="/assistant" role="menuitem"' in bob.get("/page/index", headers=HOST).data.decode()
        resp = bob.get("/assistant", headers=HOST)
        body = resp.data.decode()
        assert resp.status_code == 200 and "<h1>Connect an assistant</h1>" in body
        assert "as you, <strong>bob</strong>" in body and "<code>http://localhost:5000/mcp</code>" in body
        assert "You have no token" in body and "Revoke" not in body and not TOKEN.search(body)
        assert 'class="breadcrumb"' in body   # the module switcher, as on every view
        assert not re.search(r'class="module-link"[^>]*aria-current', body)   # in no module

    def test_a_user_makes_their_token(self, bob, wiki_dir):
        resp, token = make(bob, "bob-pw")
        assert resp.status_code == 200 and token
        assert resp.headers["Cache-Control"] == "no-store"
        body = resp.data.decode()
        assert f'claude mcp add --transport http zone http://localhost:5000/mcp --header "Authorization: Bearer {token}"' in body
        # Each line to copy comes with a Copy button, hidden until a script shows it.
        lines = re.findall(r'<div class="copy-line"[^>]*><code[^>]*>([^<]*)</code>(<button type="button" class="btn btn-sm copy-btn" hidden>)?', body)
        assert [text for text, _ in lines][:2] == ["http://localhost:5000/mcp", token] and all(button for _, button in lines)
        # Kept as its hash, in bob's section; the assistant acts as bob.
        assert kept(token) in section(wiki_dir, "bob") and token not in config(wiki_dir)
        status, text = mcp(bob, token, "write_page", note=True, slug="From the assistant", content="Hello\n")
        assert status == 200 and list((wiki_dir / "private/bob").glob("*from-the-assistant*")), text
        # Shown once: the page says there is one, and how to end it.
        body = bob.get("/assistant", headers=HOST).data.decode()
        assert "You have a token" in body and token not in body and "Revoke the token" in body
        assert "Make a new token" in body

    def test_making_one_takes_the_password(self, bob, wiki_dir):
        before = config(wiki_dir)
        for password in ("wrong", "", None, "alice-pw"):
            resp, token = make(bob, password)
            assert resp.status_code == 403 and token is None
            assert "Incorrect password: no token was made." in resp.data.decode()
        assert config(wiki_dir) == before

    def test_wrong_passwords_are_throttled_as_at_the_login(self, bob, wiki_dir):
        for _ in range(5):
            assert make(bob, "wrong")[0].status_code == 403
        resp, token = make(bob, "bob-pw")   # even the right one must wait
        assert resp.status_code == 429 and token is None and "Try again in" in resp.data.decode()
        assert "mcp-token" not in section(wiki_dir, "bob")

    def test_a_new_token_replaces_the_old_one(self, client, users, wiki_dir):
        alice = login(client, "alice")
        assert mcp(alice, "alice-token")[0] == 200
        _, token = make(alice, "alice-pw")
        assert mcp(alice, "alice-token")[0] == 401 and mcp(alice, token)[0] == 200
        assert section(wiki_dir, "alice").count("mcp-token") == 1

    def test_revoking_ends_it_and_leaves_the_others(self, bob, wiki_dir):
        _, token = make(bob, "bob-pw")
        alices = section(wiki_dir, "alice")
        resp = bob.post("/assistant/revoke", headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/assistant?done=revoke"
        body = bob.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "Revoked the token" in body and "You have no token" in body
        assert "mcp-token" not in section(wiki_dir, "bob") and section(wiki_dir, "alice") == alices
        assert mcp(bob, token)[0] == 401 and mcp(bob, "alice-token")[0] == 200

    def test_the_page_says_revoked_only_when_it_is(self, bob):
        make(bob, "bob-pw")
        assert "Revoked the token" not in bob.get("/assistant?done=revoke", headers=HOST).data.decode()

    def test_a_viewer_makes_one_and_their_assistant_only_reads(self, client, users, wiki_dir):
        carol = login(client, "carol")
        assert "You are a viewer" in carol.get("/assistant", headers=HOST).data.decode()
        resp, token = make(carol, "carol-pw")
        assert resp.status_code == 200 and kept(token) in section(wiki_dir, "carol")
        assert mcp(carol, token, "read_page", slug="index")[0] == 200
        _, text = mcp(carol, token, "write_page", slug="carols", content="Hers\n")
        assert "you are a viewer" in text and not (wiki_dir / "carols.md").exists()
        assert carol.post("/assistant/revoke", headers=HOST).status_code == 303
        assert "mcp-token" not in section(wiki_dir, "carol")

    def test_it_is_only_ones_own(self, client, users, wiki_dir):
        """No field names a user: a name sent along changes nothing of theirs."""
        bob = login(client, "bob")
        alices = section(wiki_dir, "alice")
        resp = bob.post("/assistant/token", data={"password": "bob-pw", "username": "alice", "name": "alice"}, headers=HOST)
        assert resp.status_code == 200 and section(wiki_dir, "alice") == alices
        assert "mcp-token" in section(wiki_dir, "bob")

    def test_signed_out_it_asks_to_sign_in(self, client, users, wiki_dir):
        resp = client.get("/assistant", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")
        before = config(wiki_dir)
        for path in ("/assistant/token", "/assistant/revoke"):
            assert client.post(path, data={"password": "alice-pw"}, headers=HOST).status_code in (302, 401)
        assert config(wiki_dir) == before

    def test_another_sites_post_is_refused(self, bob, wiki_dir):
        evil = {**HOST, "Origin": "https://evil.example"}
        assert bob.post("/assistant/token", data={"password": "bob-pw"}, headers=evil).status_code == 403
        make(bob, "bob-pw")
        assert bob.post("/assistant/revoke", headers=evil).status_code == 403
        assert "mcp-token" in section(wiki_dir, "bob")


class TestWithoutUsers:

    def test_with_a_password_it_makes_the_wikis_token(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "pw")
        assert client.post("/login", data={"password": "pw"}, headers=HOST).status_code == 302
        body = client.get("/assistant", headers=HOST).data.decode()
        assert "The wiki has no token" in body and "as you" not in body and 'type="password"' in body
        assert mcp(client, "anything")[0] == 401   # no token is set (with no login either, /mcp is off: 403)
        assert make(client, "wrong")[0].status_code == 403
        resp, token = make(client, "pw")
        assert resp.status_code == 200 and "The wiki's new token" in resp.data.decode()
        assert kept(token) in config(wiki_dir).split("[user")[0]
        assert mcp(client, token)[0] == 200
        assert client.post("/assistant/revoke", headers=HOST).status_code == 303
        assert "mcp-token" not in config(wiki_dir) and mcp(client, token)[0] == 401

    def test_without_a_login_it_asks_for_no_password(self, client, wiki_dir):
        body = client.get("/assistant", headers=HOST).data.decode()
        assert 'type="password"' not in body and "The wiki has no token" in body
        resp, token = make(client)
        assert resp.status_code == 200 and mcp(client, token)[0] == 200
        assert "The wiki has a token" in client.get("/assistant", headers=HOST).data.decode()

    def test_a_new_one_replaces_the_one_from_the_host(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "MCP_TOKEN", "from-the-host")
        assert mcp(client, "from-the-host")[0] == 200
        _, token = make(client)
        assert mcp(client, "from-the-host")[0] == 401 and mcp(client, token)[0] == 200
        assert config(wiki_dir).count("mcp-token") == 1

    def test_the_address_is_the_base_urls(self, client, monkeypatch):
        monkeypatch.setattr(settings, "BASE_URL", "https://wiki.example.org")
        assert "<code>https://wiki.example.org/mcp</code>" in client.get("/assistant", headers=HOST).data.decode()

    def test_the_help_dialog_links_to_it(self, client):
        assert '<a href="/assistant">Connect an AI assistant</a>' in client.get("/page/index", headers=HOST).data.decode()
