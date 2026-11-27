"""The first-run page (docs/spec/security.md, First run): a wiki other
devices can reach, with no login yet, shows and changes nothing until a
password is set in the browser."""

import http.client
import re

import pytest

from tests import settings
from tests.conftest import HOST, write_page
from tests.goserver import GoServer, _flags, _settings

FORM = {"username": "", "password": "correct horse", "again": "correct horse"}


@pytest.fixture()
def fresh(client, git_wiki_dir, monkeypatch):
    """A wiki without a login, not on purpose, reached as from another
    device (ZONE_ALLOW_REMOTE, the tests' default)."""
    monkeypatch.setattr(settings, "NO_LOGIN", False)
    write_page(git_wiki_dir, "plans", "# Plans\n\nsecret words\n")
    return git_wiki_dir


def by_hand(server, path):
    """A GET to a Zonewiki started on its own (GoServer), as localhost: status, Location."""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request("GET", path, headers=HOST)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location")
    finally:
        conn.close()


def config(wiki):
    path = wiki / ".zone" / "config"
    return path.read_text() if path.exists() else ""


class TestBeforeALoginIsSet:

    def test_every_page_leads_to_the_form(self, client, fresh):
        for path in ("/", "/page/plans", "/edit/plans", "/tasks", "/search?q=secret", "/login", "/admin/users"):
            resp = client.get(path, headers=HOST)
            while resp.status_code == 302 and resp.headers["Location"] != "/setup":
                resp = client.get(resp.headers["Location"], headers=HOST)   # / → /page/index, /login → /
            assert resp.status_code == 302 and resp.headers["Location"] == "/setup", path
        body = client.get("/setup", headers=HOST).get_data(as_text=True)
        assert 'action="/setup"' in body and "has no login yet" in body and "secret words" not in body
        assert "config set no-login yes" in body

    def test_nothing_is_read_or_written(self, client, fresh):
        for method, path, kwargs in [
                ("GET", "/api/pages", {}), ("GET", "/api/tasks", {}), ("GET", "/api/search?q=secret", {}),
                ("GET", "/api/version", {}), ("POST", "/api/save/plans", {"data": {"content": "# Mine\n"}}),
                ("POST", "/delete/plans", {}), ("POST", "/api/tasks", {"json": {"title": "T"}}),
                ("POST", "/api/preview", {"data": "[[plans]]"}), ("POST", "/new", {"data": {"slug": "x"}})]:
            resp = client.open(path, method=method, headers=HOST, **kwargs)
            assert resp.status_code == 403 and "no login yet" in resp.get_data(as_text=True), path
            assert "secret" not in resp.get_data(as_text=True)
        assert (fresh / "plans.md").read_text() == "# Plans\n\nsecret words\n"
        assert not (fresh / "_tasks" / "tasks.json").exists()

    def test_the_browser_code_and_the_healthcheck_answer(self, client, fresh):
        assert client.get("/public/style.css", headers=HOST).status_code == 200
        assert client.get("/api/health", headers=HOST).status_code == 200
        assert client.post("/mcp", headers=HOST, json={}).status_code == 403   # off: no token

    def test_another_site_cant_send_the_form(self, client, fresh):
        resp = client.post("/setup", data=FORM, headers={**HOST, "Origin": "https://elsewhere.example"})
        assert resp.status_code == 403 and config(fresh) == ""


class TestSettingTheLogin:

    def test_a_password_for_the_wiki(self, client, fresh):
        resp = client.post("/setup", data=FORM, headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/"
        assert re.search(r"^password = pbkdf2-sha256:600000:", config(fresh), re.M) and "[user" not in config(fresh)
        assert (fresh / ".zone" / "config").stat().st_mode & 0o777 == 0o600
        # Signed in with it; anyone else meets the login.
        assert "secret words" in client.get("/page/plans", headers=HOST).get_data(as_text=True)
        client.post("/logout", headers=HOST)
        assert "/login" in client.get("/page/plans", headers=HOST).headers["Location"]
        assert client.post("/login", data={"password": "correct horse"}, headers=HOST).status_code == 302
        assert client.get("/page/plans", headers=HOST).status_code == 200

    def test_with_a_name_the_first_user_an_admin(self, client, fresh):
        resp = client.post("/setup", data={**FORM, "username": " Alice "}, headers=HOST)
        assert resp.status_code == 303
        assert "[user alice]" in config(fresh) and "role = admin" in config(fresh)
        assert client.get("/admin/users", headers=HOST).status_code == 200
        client.post("/logout", headers=HOST)
        assert client.post("/login", data={"username": "alice", "password": "correct horse"},
                           headers=HOST).status_code == 302

    @pytest.mark.parametrize("form,why", [
        ({**FORM, "password": "short", "again": "short"}, "at least 8 characters"),
        ({**FORM, "again": "correct horses"}, "The passwords differ"),
        ({**FORM, "username": "Al ice!"}, "A user name has"),
        ({**FORM, "username": "me"}, "be a user name"),
        ({**FORM, "username": "None"}, "be a user name"),
        ({**FORM, "password": "", "again": ""}, "at least 8 characters"),
    ])
    def test_a_form_that_wont_do_sets_nothing(self, client, fresh, form, why):
        resp = client.post("/setup", data=form, headers=HOST)
        assert resp.status_code == 400 and why in resp.get_data(as_text=True)
        assert config(fresh) == ""
        assert client.get("/page/plans", headers=HOST).headers["Location"] == "/setup"

    def test_only_once(self, client, fresh):
        assert client.post("/setup", data=FORM, headers=HOST).status_code == 303
        before = config(fresh)
        client.post("/logout", headers=HOST)
        # The form is gone, and sending it again sets no other password.
        resp = client.post("/setup", data={**FORM, "password": "another one", "again": "another one"}, headers=HOST)
        assert resp.status_code == 302 and config(fresh) == before
        assert client.get("/setup", headers=HOST).status_code == 302
        assert client.post("/login", data={"password": "another one"}, headers=HOST).status_code == 200


class TestALoginOnPurpose:
    """A wiki on localhost alone needs no login, and whoever opens it may
    give it one: Set a login in the menu opens the first run's form."""

    @pytest.fixture()
    def local(self, fresh, monkeypatch):
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        return fresh

    @staticmethod
    def menu(client):
        body = client.get("/page/plans", headers=HOST).get_data(as_text=True)
        return body.split('class="module-menu page-menu"', 1)[1].split("</div>", 1)[0]

    def test_the_menu_leads_to_the_form(self, client, local):
        assert '<a href="/setup" role="menuitem" class="menu-login">Set a login</a>' in self.menu(client)
        resp = client.get("/setup", headers=HOST)
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200 and 'action="/setup"' in body
        # Said as what it is: not the first run's words, and a way back.
        assert "Set a login for" in body and "whoever can open it reads and changes everything" in body
        assert '<a href="/">Not now</a>' in body
        assert "reached from other devices" not in body and "no-login yes" not in body and "secret words" not in body
        # The wiki goes on without one meanwhile.
        assert client.get("/page/plans", headers=HOST).status_code == 200

    def test_a_password_for_the_wiki(self, client, local):
        resp = client.post("/setup", data=FORM, headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/"
        assert re.search(r"^password = pbkdf2-sha256:600000:", config(local), re.M) and "[user" not in config(local)
        assert (local / ".zone" / "config").stat().st_mode & 0o777 == 0o600
        # Signed in with it; the menu has Settings now (Change password is a tab of it), and the form is gone.
        menu = self.menu(client)
        assert 'class="menu-settings"' in menu and "Set a login" not in menu
        assert '<a href="/password">Password</a>' in client.get("/assistant", headers=HOST).get_data(as_text=True)
        assert client.get("/setup", headers=HOST).status_code == 302
        client.post("/logout", headers=HOST)
        assert "/login" in client.get("/page/plans", headers=HOST).headers["Location"]
        assert client.post("/login", data={"password": "correct horse"}, headers=HOST).status_code == 302

    def test_with_a_name_the_first_user_and_the_wikis_token_ends(self, client, local, monkeypatch):
        monkeypatch.setattr(settings, "MCP_TOKEN", "wiki-token-wiki-token-wiki-token")
        assert "the wiki's assistant token ends" in client.get("/setup", headers=HOST).get_data(as_text=True)
        assert client.post("/setup", data={**FORM, "username": "alice"}, headers=HOST).status_code == 303
        assert "[user alice]" in config(local) and "role = admin" in config(local)
        assert not re.search(r"^mcp-token", config(local), re.M)
        assert client.get("/admin/users", headers=HOST).status_code == 200

    def test_no_login_on_purpose_on_localhost_gives_way(self, client, local, monkeypatch):
        # no-login = yes says nothing on localhost alone; a login set there ends it.
        monkeypatch.setattr(settings, "CONFIG_FILE", "no-login = yes\n")
        assert client.get("/setup", headers=HOST).status_code == 200
        assert client.post("/setup", data=FORM, headers=HOST).status_code == 303
        assert "no-login" not in config(local) and re.search(r"^password = ", config(local), re.M)

    def test_a_wiki_open_to_the_network_on_purpose_has_no_form(self, client, fresh, monkeypatch):
        # Anyone on the network would lock everyone else out: there the login
        # is set on the host, and the menu says how.
        monkeypatch.setattr(settings, "CONFIG_FILE", "no-login = yes\n")
        assert 'href="/help/self-hosting#setting-a-login"' in self.menu(client) and 'href="/setup"' not in self.menu(client)
        assert client.get("/setup", headers=HOST).headers["Location"] == "/"
        assert client.post("/setup", data=FORM, headers=HOST).status_code == 302 and "password" not in config(fresh)
        # Names in hosts count as the network too.
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        monkeypatch.setattr(settings, "HOSTS", frozenset({"wiki.example.org"}))
        assert client.post("/setup", data=FORM, headers={"Host": "wiki.example.org"}).status_code == 302
        assert "password" not in config(fresh)

    def test_another_site_cant_send_the_form(self, client, local):
        resp = client.post("/setup", data=FORM, headers={**HOST, "Origin": "https://elsewhere.example"})
        assert resp.status_code == 403 and config(local) == ""

    def test_only_where_there_is_no_login(self, client, local, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "wiki-pw")
        assert client.get("/setup", headers=HOST).status_code == 302
        assert client.post("/setup", data=FORM, headers=HOST).status_code == 302
        assert client.post("/login", data={"password": "correct horse"}, headers=HOST).status_code == 200   # the old one holds


class TestWhenThereIsNoPage:

    def test_no_login_on_purpose(self, client, fresh, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "no-login = yes\n")
        assert client.get("/page/plans", headers=HOST).status_code == 200
        assert client.get("/setup", headers=HOST).headers["Location"] == "/"
        assert client.post("/setup", data=FORM, headers=HOST).status_code == 302 and "password" not in config(fresh)

    def test_on_localhost_alone_zone_needs_no_login(self, client, fresh, monkeypatch):
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        assert client.get("/page/plans", headers=HOST).status_code == 200
        assert config(fresh) == ""

    @pytest.mark.parametrize("bind,page", [("0.0.0.0", True), ("192.168.1.5", True), ("::", True),
                                           ("127.0.0.1", False), ("localhost", False), ("::1", False), ("", False)])
    def test_a_port_published_beyond_localhost_counts(self, client, fresh, monkeypatch, bind, page):
        # compose hands Zonewiki the address its port is published on (ZONE_BIND).
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        monkeypatch.setattr(settings, "ENV", {"ZONE_BIND": bind})
        resp = client.get("/page/plans", headers=HOST)
        assert (resp.status_code, resp.headers.get("Location")) == ((302, "/setup") if page else (200, None))

    def test_started_by_hand_a_port_alone_is_localhost_only(self, fresh, tmp_path, monkeypatch):
        # The downloaded binary, run as the README says: on the network it would
        # have no login, and the Host check is no door (a client sends Host: localhost).
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        server = GoServer(_settings(), tmp_path / "server.log", ":0", _flags())
        try:
            assert server.host == "127.0.0.1"
            assert by_hand(server, "/page/plans") == (200, None)
        finally:
            server.stop()

    def test_started_by_hand_open_to_the_network_asks_for_a_login(self, fresh, tmp_path, monkeypatch):
        for remote, addr in [(True, ":0"), (False, "0.0.0.0:0")]:
            monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", remote)
            server = GoServer(_settings(), tmp_path / "server.log", addr, _flags())
            try:
                assert server.host in ("[::]", "0.0.0.0"), addr
                assert by_hand(server, "/page/plans") == (302, "/setup"), addr
            finally:
                server.stop()

    def test_names_in_hosts_count(self, client, fresh, monkeypatch):
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        monkeypatch.setattr(settings, "HOSTS", frozenset({"wiki.example.org"}))
        assert client.get("/page/plans", headers={"Host": "wiki.example.org"}).headers["Location"] == "/setup"

    def test_a_wiki_with_a_login_has_none(self, client, fresh, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "wiki-pw")
        assert client.get("/setup", headers=HOST).status_code == 302
        assert client.post("/setup", data=FORM, headers=HOST).status_code == 302
        assert client.post("/login", data={"password": "correct horse"}, headers=HOST).status_code == 200

    def test_the_log_says_so_at_start(self, client, fresh):
        client.get("/setup", headers=HOST)
        assert "asks for a password in the browser" in client.live.log.read_text()
