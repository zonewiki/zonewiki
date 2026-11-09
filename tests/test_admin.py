"""The admin page's Users tab (/admin/users, docs/spec/users.md, Managing users): an admin
changes roles, disables, enables and removes users in the browser; it
writes the settings file as zonewiki user does, under the same rules."""

import hashlib
import json
import re
import subprocess
import time

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", ""), "carol": ("carol-pw", "")}


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})


def login(client, name):
    client.post("/logout", headers=HOST)
    resp = client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST)
    assert resp.status_code == 302
    return client


@pytest.fixture()
def admin(client, users):
    return login(client, "alice")


def config(wiki):
    return (wiki / ".zone" / "config").read_text()


def section(wiki, name):
    """The lines of a user's section in the settings file."""
    m = re.search(rf"^\[user {name}\]\n(.*?)(?=^\[|\Z)", config(wiki), re.M | re.S)
    return m.group(1) if m else None


def post(client, path, **data):
    return client.post(path, data=data, headers=HOST)


class TestWhoSeesIt:

    def test_only_admins_see_it_and_their_menu_offers_it(self, admin):
        body = admin.get("/admin/users", headers=HOST).data.decode()
        assert "<h1>Admin</h1>" in body and "bob" in body and "carol" in body
        assert '<a href="/admin/users" aria-current="page">Users</a>' in body
        assert 'href="/admin"' in admin.get("/page/index", headers=HOST).data.decode()
        resp = admin.get("/admin", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/admin/users"

    @pytest.mark.parametrize("path", ["/admin/users/bob/role", "/admin/users/bob/disable", "/admin/users/bob/enable",
                                      "/admin/users/bob/remove"])
    def test_anyone_else_gets_a_404_and_nothing_changes(self, client, users, git_wiki_dir, path):
        login(client, "bob")
        before = config(git_wiki_dir)
        assert client.get("/admin/users", headers=HOST).status_code == 404
        assert client.get("/admin", headers=HOST).status_code == 404
        assert post(client, path, role="admin").status_code == 404
        assert config(git_wiki_dir) == before
        assert 'href="/admin"' not in client.get("/page/index", headers=HOST).data.decode()

    def test_not_without_users(self, client, wiki_dir):
        assert client.get("/admin/users", headers=HOST).status_code == 404

    def test_not_from_another_site(self, admin, git_wiki_dir):
        resp = admin.post("/admin/users/bob/disable", headers={**HOST, "Origin": "https://evil.example"})
        assert resp.status_code == 403 and "disabled" not in section(git_wiki_dir, "bob")

    def test_a_user_who_isnt_there_is_a_404(self, admin):
        assert post(admin, "/admin/users/zed/disable").status_code == 404


class TestChanges:

    def test_role(self, admin, git_wiki_dir):
        resp = post(admin, "/admin/users/bob/role", role="viewer")
        assert resp.status_code == 303 and resp.headers["Location"].startswith("/admin/users?done=role")
        assert "role = viewer" in section(git_wiki_dir, "bob")
        assert "bob is a viewer now." in admin.get(resp.headers["Location"], headers=HOST).data.decode()
        post(admin, "/admin/users/bob/role", role="editor")   # the default: the line goes
        assert "role" not in section(git_wiki_dir, "bob")
        assert post(admin, "/admin/users/bob/role", role="boss").status_code == 400

    def test_disable_and_enable(self, admin, client, git_wiki_dir):
        assert post(admin, "/admin/users/bob/disable").status_code == 303
        assert "disabled = yes" in section(git_wiki_dir, "bob")
        body = admin.get("/admin/users", headers=HOST).data.decode()
        assert "Disabled" in body and 'action="/admin/users/bob/enable"' in body
        assert post(admin, "/admin/users/bob/enable").status_code == 303
        assert "disabled" not in section(git_wiki_dir, "bob")

    def test_remove_says_their_files_stay(self, admin, git_wiki_dir):
        (git_wiki_dir / "private" / "carol").mkdir(parents=True)
        (git_wiki_dir / "private" / "carol" / "x.md").write_text("# X\n")
        resp = post(admin, "/admin/users/carol/remove")
        assert resp.status_code == 303 and section(git_wiki_dir, "carol") is None
        body = admin.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "Removed carol." in body and "private/carol/" in body
        assert (git_wiki_dir / "private" / "carol" / "x.md").exists()

    def test_the_change_is_committed_by_the_admin(self, admin, git_wiki_dir):
        post(admin, "/admin/users/bob/role", role="viewer")
        # The reload's commit runs once the request has ended.
        for _ in range(50):
            log = subprocess.run(["git", "log", "-1", "--format=%an|%s", "--", ".zone/config"], cwd=git_wiki_dir,
                                 capture_output=True, text=True, env=settings.GIT_ENV).stdout.strip()
            if log != "Zone|Change settings":
                break
            time.sleep(.05)
        assert log == "alice|Change settings: bob's role"

    def test_the_change_applies_at_once(self, admin, client, git_wiki_dir):
        post(admin, "/admin/users/carol/role", role="admin")
        login(client, "carol")
        assert client.get("/admin/users", headers=HOST).status_code == 200


class TestRefused:

    def test_the_last_admin_cant_go(self, admin, git_wiki_dir):
        before = config(git_wiki_dir)
        for path, data in [("/admin/users/alice/role", {"role": "editor"}), ("/admin/users/alice/disable", {}),
                           ("/admin/users/alice/remove", {})]:
            resp = post(admin, path, **data)
            body = resp.data.decode()
            assert resp.status_code == 409 and "without an admin" in body, path
        assert config(git_wiki_dir) == before

    def test_the_last_user_is_removed_only_on_the_host(self, client, monkeypatch, git_wiki_dir):
        monkeypatch.setattr(settings, "LEAF_USERS", {"alice": USERS["alice"]})
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        login(client, "alice")
        resp = post(client, "/admin/users/alice/remove")
        assert resp.status_code == 409 and "last user" in resp.data.decode()
        assert section(git_wiki_dir, "alice") is not None

    def test_demoting_yourself_with_another_admin_leads_home(self, admin, git_wiki_dir):
        post(admin, "/admin/users/bob/role", role="admin")
        resp = post(admin, "/admin/users/alice/role", role="editor")
        assert resp.status_code == 303 and resp.headers["Location"] == "/"
        assert admin.get("/admin/users", headers=HOST).status_code == 404

    def test_a_change_meanwhile_is_not_overwritten(self, admin, git_wiki_dir):
        """saveConfig writes only onto the file as read: the page reads it at
        each request, so a change by hand before is kept."""
        path = git_wiki_dir / ".zone" / "config"
        path.write_text(path.read_text() + "\n# a note by hand\n")
        post(admin, "/admin/users/bob/role", role="viewer")
        assert "# a note by hand" in config(git_wiki_dir) and "role = viewer" in section(git_wiki_dir, "bob")


def make_link(admin, path, **data):
    """The link a POST on the Users page shows, as a path."""
    resp = post(admin, path, **data)
    assert resp.status_code == 200, resp.data.decode()
    m = re.search(r'<code class="users-link">https?://[^/]+(/welcome/[0-9a-f]{64})</code>', resp.data.decode())
    assert m, resp.data.decode()
    return m.group(1)


class TestLinks:

    def test_an_invite_adds_the_user_with_their_role_and_signs_them_in(self, admin, client, git_wiki_dir):
        link = make_link(admin, "/admin/users/invite", name="dave", role="viewer")
        assert "dave" in admin.get("/admin/users", headers=HOST).data.decode()   # listed as open
        client.post("/logout", headers=HOST)
        page = client.get(link, headers=HOST)
        body = page.data.decode()
        assert page.status_code == 200 and "Welcome to" in body and "dave" in body
        assert page.headers["Referrer-Policy"] == "no-referrer"
        assert section(git_wiki_dir, "dave") is None   # opening it changes nothing
        resp = post(client, link, password="dave-pw", again="dave-pw")
        assert resp.status_code == 303
        assert "role = viewer" in section(git_wiki_dir, "dave")
        assert ">dave</span>" in client.get("/page/index", headers=HOST).data.decode()
        # Once only.
        client.post("/logout", headers=HOST)
        assert client.get(link, headers=HOST).status_code == 404
        assert client.post("/login", data={"username": "dave", "password": "dave-pw"},
                           headers=HOST).status_code == 302

    def test_the_passwords_must_agree(self, admin, client, git_wiki_dir):
        link = make_link(admin, "/admin/users/invite", name="dave", role="editor")
        client.post("/logout", headers=HOST)
        assert "The passwords differ." in post(client, link, password="a", again="b").data.decode()
        assert "Choose a password." in post(client, link, password="", again="").data.decode()
        assert section(git_wiki_dir, "dave") is None

    def test_a_reset_sets_a_new_password_and_ends_the_old_sessions(self, admin, client, git_wiki_dir):
        link = make_link(admin, "/admin/users/bob/reset")
        login(client, "bob")
        assert client.get("/page/index", headers=HOST).status_code == 200
        old_session = client.cookies["session"]
        before = section(git_wiki_dir, "bob")
        client.post("/logout", headers=HOST)
        resp = post(client, link, password="new-pw", again="new-pw")
        assert resp.status_code == 303 and section(git_wiki_dir, "bob") != before
        assert ">bob</span>" in client.get("/page/index", headers=HOST).data.decode()   # signed in by the link
        client.set_cookie("session", old_session)
        assert client.get("/page/index", headers=HOST).status_code == 302   # the old session ended

    def test_invite_refuses_a_user_there_and_a_bad_name(self, admin):
        for data in [{"name": "bob", "role": "editor"}, {"name": "Bad Name", "role": "editor"},
                     {"name": "dave", "role": "boss"}]:
            resp = post(admin, "/admin/users/invite", **data)
            assert resp.status_code == 409 and "not made" in resp.data.decode()

    def test_revoke(self, admin, client):
        link = make_link(admin, "/admin/users/invite", name="dave", role="editor")
        resp = post(admin, "/admin/users/dave/revoke")
        assert resp.status_code == 303
        assert "Revoked the link for dave" in admin.get(resp.headers["Location"], headers=HOST).data.decode()
        client.post("/logout", headers=HOST)
        assert client.get(link, headers=HOST).status_code == 404
        login(client, "alice")
        assert post(admin, "/admin/users/dave/revoke").status_code == 404

    def test_a_new_link_replaces_the_old(self, admin, client):
        old = make_link(admin, "/admin/users/bob/reset")
        new = make_link(admin, "/admin/users/bob/reset")
        client.post("/logout", headers=HOST)
        assert client.get(old, headers=HOST).status_code == 404 and client.get(new, headers=HOST).status_code == 200

    def test_an_expired_link_doesnt_work(self, client, users, git_wiki_dir):
        token = "ab" * 32
        (git_wiki_dir / ".zone" / "links").write_text(json.dumps([{
            "hash": "sha256:" + hashlib.sha256(token.encode()).hexdigest(), "user": "dave", "kind": "invite",
            "role": "editor", "by": "alice", "expires": "2020-01-01T00:00:00Z"}]))
        body = client.get(f"/welcome/{token}", headers=HOST).data.decode()
        assert "This link doesn't work" in body

    def test_wrong_links_are_throttled(self, client, users):
        for _ in range(5):
            assert client.get("/welcome/" + "0" * 64, headers=HOST).status_code == 404
        assert client.get("/welcome/" + "0" * 64, headers=HOST).status_code == 429

    def test_only_admins_make_links(self, client, users):
        login(client, "bob")
        assert post(client, "/admin/users/invite", name="dave", role="admin").status_code == 404
        assert post(client, "/admin/users/carol/reset").status_code == 404

    def test_links_are_capped_per_admin(self, admin):
        for i in range(10):
            make_link(admin, "/admin/users/invite", name=f"u{i}", role="editor")
        assert post(admin, "/admin/users/invite", name="u10", role="editor").status_code == 429
