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
    monkeypatch.setattr(settings, "USERS", dict(USERS))
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
        assert 'class="breadcrumb"' in body   # the module switcher, as on every view
        assert not re.search(r'class="module-link"[^>]*aria-current', body)   # in no module
        assert 'href="/admin"' in admin.get("/page/index", headers=HOST).data.decode()
        resp = admin.get("/admin", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/admin/users"

    @pytest.mark.parametrize("path", ["/admin/users/bob/role", "/admin/users/bob/disable", "/admin/users/bob/enable",
                                      "/admin/users/bob/remove", "/admin/users/bob/groups", "/groups/includes"])
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

    def test_groups(self, admin, git_wiki_dir):
        # Who belongs together is said on the users: the Groups field sets a
        # user's groups (cleaned, A–Z), empty takes them out of every one. It
        # takes the groups there are: a new one is made on Groups.
        (git_wiki_dir / "groups" / "ghosts").mkdir(parents=True)       # a folder nobody sees: its group can be given
        resp = post(admin, "/admin/users/carol/groups", groups="Ghosts, @ghosts")
        assert resp.status_code == 303 and "groups = ghosts" in section(git_wiki_dir, "carol")
        post(admin, "/admin/users/carol/groups", groups="")
        assert post(admin, "/groups", name="dev", people="").status_code == 303       # made on Groups, by alice
        assert post(admin, "/groups", name="hiring", people="").status_code == 303
        resp = post(admin, "/admin/users/bob/groups", groups="Hiring, @dev")
        assert resp.status_code == 303 and resp.headers["Location"].startswith("/admin/users?done=groups")
        assert "groups = dev, hiring" in section(git_wiki_dir, "bob")
        page = admin.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "bob is in dev, hiring now." in page
        assert 'name="groups" value="dev, hiring" aria-label="Groups of bob"' in page and 'data-pick="groups"' in page
        assert '<datalist id="user-groups"><option value="dev"><option value="ghosts"><option value="hiring"></datalist>' in page
        assert 'href="/groups"' in page and "Make a group of groups" not in page    # groups are made and changed on Groups
        before = config(git_wiki_dir)
        for groups, why in (("dev ops!", "isn&#39;t a group&#39;s name"), ("dev, ops", "There is no group called ops")):
            resp = post(admin, "/admin/users/bob/groups", groups=groups)       # nothing changes
            assert resp.status_code in (400, 409) and why in resp.data.decode(), groups
            assert config(git_wiki_dir) == before
        resp = post(admin, "/admin/users/bob/groups", groups="")
        assert "groups" not in section(git_wiki_dir, "bob")
        assert "bob is in no group now." in admin.get(resp.headers["Location"], headers=HOST).data.decode()

    def test_a_group_made_of_groups(self, admin, client, git_wiki_dir):
        # On Groups an admin makes a group of other groups, and who is in one
        # of them is in it (docs/spec/users.md, Groups). dev and design are
        # made there by alice, who then leaves them to bob and carol.
        assert post(admin, "/groups", name="dev", people="bob").status_code == 303
        assert post(admin, "/groups", name="design", people="carol").status_code == 303
        post(admin, "/admin/users/alice/groups", groups="")
        resp = post(admin, "/groups/includes", group="@Product", includes="Dev, @design")
        assert resp.status_code == 303 and resp.headers["Location"].startswith("/groups?done=includes&group=product")
        assert "\n[group product]\nincludes = design, dev\n" in config(git_wiki_dir)
        assert oct((git_wiki_dir / ".zone" / "config").stat().st_mode & 0o777) == "0o600"
        page = admin.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "product includes design, dev: bob, carol." in page
        assert 'name="includes" value="design, dev" list="all-groups" data-pick="groups" data-pick-not="product"' in page
        assert '<datalist id="all-groups"><option value="design"><option value="dev"><option value="product"></datalist>' in page
        who = lambda g: " ".join(re.sub(r"<[^>]+>", "", page.split(f'data-group="{g}"', 1)[1].split(
            '<span class="group-who">', 1)[1].split('<span class="group-pages">', 1)[0]).split())
        assert who("product") == "everyone in design (carol) and dev (bob)"    # Other groups: she is in none of them
        assert who("dev") == "bob · owned by alice"
        assert ">Other groups</h2>" in page and ">Make a group of groups</button>" in page
        post(admin, "/admin/users/alice/groups", groups="product")         # named on it, beside the groups
        page = admin.get("/groups", headers=HOST).data.decode()
        assert who("product") == "alice, and everyone in design (carol) and dev (bob)"
        post(admin, "/admin/users/alice/groups", groups="")
        page = admin.get("/admin/users", headers=HOST).data.decode()
        assert '<span class="user-also" title="By way of a group they are in (on Groups)">also in product</span>' in page
        assert '<option value="product">' in page                      # the fields offer it too
        for _ in range(50):
            if "product includes design, dev (by alice)" in client.live.log.read_text():
                break
            time.sleep(.05)
        assert "Change settings: product includes design, dev (by alice)" in client.live.log.read_text()
        # What the file can't say, or a group there isn't, is refused, and nothing changes.
        before = config(git_wiki_dir)
        for data, why in (({"group": "dev", "includes": "product"}, "includes itself"),
                          ({"group": "product", "includes": "dev ops!"}, "isn&#39;t a group&#39;s name"),
                          ({"group": "product", "includes": "dev, ops"}, "There is no group called ops"),
                          ({"group": "Pro duct", "includes": "dev"}, "isn&#39;t a group&#39;s name"),
                          ({"group": "", "includes": "dev"}, "isn&#39;t a group&#39;s name")):
            resp = post(admin, "/groups/includes", **data)
            assert resp.status_code in (400, 409) and why in resp.data.decode(), data
            assert config(git_wiki_dir) == before
        # The form that makes a group of groups makes one: it changes no group there is (Manage… does), and
        # takes no name something goes by.
        for name, why in (("product", "There is a group product already"), ("dev", "There is a group dev already")):
            resp = post(admin, "/groups/includes", group=name, includes="design", new="1")
            assert resp.status_code in (400, 409) and why in resp.data.decode(), name
            assert config(git_wiki_dir) == before
        (git_wiki_dir / "groups/ghosts").mkdir(parents=True)
        (git_wiki_dir / "groups/ghosts/x.md").write_text("# X\n")
        resp = post(admin, "/groups/includes", group="ghosts", includes="dev", new="1")
        assert resp.status_code in (400, 409) and "is taken" in resp.data.decode() and config(git_wiki_dir) == before
        # Nor does the form without new (Manage…'s), which would make a group that isn't there all the same.
        resp = post(admin, "/groups/includes", group="ghosts", includes="dev")
        assert resp.status_code in (400, 409) and "is taken" in resp.data.decode() and config(git_wiki_dir) == before
        assert 'name="new" value="1"' in admin.get("/groups", headers=HOST).data.decode()
        # Taking a group out says whom that takes the larger group from; with
        # nothing left the section goes.
        resp = post(admin, "/groups/includes", group="product", includes="dev")
        page = admin.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "product includes dev: bob. No longer in it: carol. What is for @product they no longer see." in page
        page = admin.get("/groups?done=includes&group=product&left=mallory,carol", headers=HOST).data.decode()
        assert "No longer in it: carol." in page and "mallory" not in page    # only users' names
        resp = post(admin, "/groups/includes", group="product", includes="")
        assert "[group product]" not in config(git_wiki_dir)
        assert "product: nobody is in it. No longer in it: bob." in admin.get(resp.headers["Location"], headers=HOST).data.decode()

    def test_disable_and_enable(self, admin, client, git_wiki_dir):
        assert post(admin, "/admin/users/bob/disable").status_code == 303
        assert "disabled = yes" in section(git_wiki_dir, "bob")
        body = admin.get("/admin/users", headers=HOST).data.decode()
        assert "Disabled" in body and 'action="/admin/users/bob/enable"' in body
        assert post(admin, "/admin/users/bob/enable").status_code == 303
        assert "disabled" not in section(git_wiki_dir, "bob")

    def test_a_shared_account_is_marked_and_is_made_no_admin(self, monkeypatch, users, client, git_wiki_dir):
        """shared = yes (zonewiki user shared): several people sign in to
        it, so the Users tab says so and none of them administers the wiki."""
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "bob": {"shared": "yes"}})
        admin = login(client, "alice")
        body = admin.get("/admin/users", headers=HOST).data.decode()
        assert body.count('class="user-fact" title="Several people sign in to this account') == 1
        resp = post(admin, "/admin/users/bob/role", role="admin")
        assert "role = admin" not in section(git_wiki_dir, "bob")
        page = resp.data.decode() if resp.status_code != 303 else admin.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "bob is shared and an admin" in page
        assert post(admin, "/admin/users/bob/role", role="viewer").status_code == 303   # a viewer it may be
        assert "role = viewer" in section(git_wiki_dir, "bob")

    def test_remove_says_their_files_stay(self, admin, git_wiki_dir):
        (git_wiki_dir / "private" / "carol").mkdir(parents=True)
        (git_wiki_dir / "private" / "carol" / "x.md").write_text("# X\n")
        resp = post(admin, "/admin/users/carol/remove")
        assert resp.status_code == 303 and section(git_wiki_dir, "carol") is None
        body = admin.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "Removed carol." in body and "private/carol/" in body
        assert (git_wiki_dir / "private" / "carol" / "x.md").exists()

    def test_the_change_is_logged_with_the_admin_and_not_committed(self, admin, client, git_wiki_dir):
        post(admin, "/admin/users/bob/role", role="viewer")
        for _ in range(50):   # the reload's line comes once the request has ended
            if "Change settings: bob's role (by alice)" in client.live.log.read_text():
                break
            time.sleep(.05)
        assert "Change settings: bob's role (by alice)" in client.live.log.read_text()
        assert ".zone/" not in subprocess.run(["git", "ls-files"], cwd=git_wiki_dir, capture_output=True,
                                              text=True, env=settings.GIT_ENV).stdout

    def test_the_change_applies_at_once(self, admin, client, git_wiki_dir):
        post(admin, "/admin/users/carol/role", role="admin")
        login(client, "carol")
        assert client.get("/admin/users", headers=HOST).status_code == 200


class TestRefused:

    def test_the_last_admin_cant_step_down(self, admin, git_wiki_dir):
        before = config(git_wiki_dir)
        resp = post(admin, "/admin/users/alice/role", role="editor")
        assert resp.status_code == 409 and "without an admin" in resp.data.decode()
        assert config(git_wiki_dir) == before

    @pytest.mark.parametrize("action", ["disable", "remove"])
    def test_not_yourself_even_with_another_admin(self, admin, git_wiki_dir, action):
        post(admin, "/admin/users/bob/role", role="admin")
        before = config(git_wiki_dir)
        resp = post(admin, f"/admin/users/alice/{action}")
        body = resp.data.decode()
        assert resp.status_code == 400 and f"you can&#39;t {action} yourself here" in body
        assert config(git_wiki_dir) == before

    def test_your_own_row_offers_no_disable_or_remove(self, admin):
        body = admin.get("/admin/users", headers=HOST).data.decode()
        assert 'action="/admin/users/alice/disable"' not in body and 'action="/admin/users/alice/remove"' not in body
        assert 'action="/admin/users/alice/reset"' in body and 'action="/admin/users/alice/role"' in body
        assert 'action="/admin/users/bob/disable"' in body and 'action="/admin/users/bob/remove"' in body

    def test_another_admin_can_remove_you(self, admin, client, git_wiki_dir):
        post(admin, "/admin/users/bob/role", role="admin")
        login(client, "bob")
        assert post(client, "/admin/users/alice/remove").status_code == 303
        assert section(git_wiki_dir, "alice") is None

    def test_the_last_user_is_removed_only_on_the_host(self, client, monkeypatch, git_wiki_dir):
        monkeypatch.setattr(settings, "USERS", {"alice": USERS["alice"]})
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        login(client, "alice")
        resp = post(client, "/admin/users/alice/remove")
        assert resp.status_code == 400 and "zonewiki user remove alice" in resp.data.decode()
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
        assert page.headers["Referrer-Policy"] == "same-origin"   # no other site learns the address
        assert section(git_wiki_dir, "dave") is None   # opening it changes nothing
        resp = post(client, link, password="dave-password", again="dave-password")
        assert resp.status_code == 303
        assert "role = viewer" in section(git_wiki_dir, "dave")
        assert "Signed in as <strong>dave</strong>" in client.get("/page/index", headers=HOST).data.decode()
        # Once only.
        client.post("/logout", headers=HOST)
        assert client.get(link, headers=HOST).status_code == 404
        assert client.post("/login", data={"username": "dave", "password": "dave-password"},
                           headers=HOST).status_code == 302

    def test_the_passwords_must_agree(self, admin, client, git_wiki_dir):
        link = make_link(admin, "/admin/users/invite", name="dave", role="editor")
        client.post("/logout", headers=HOST)
        assert "The passwords differ." in post(client, link, password="one password", again="another one").data.decode()
        assert section(git_wiki_dir, "dave") is None

    def test_a_password_has_at_least_8_characters(self, admin, client, git_wiki_dir):
        # As on the first-run page: a link doesn't set a password of one letter.
        link = make_link(admin, "/admin/users/invite", name="dave", role="editor")
        client.post("/logout", headers=HOST)
        for short in ["", "a", "1234567"]:
            assert "at least 8 characters" in post(client, link, password=short, again=short).data.decode()
        assert section(git_wiki_dir, "dave") is None
        assert post(client, link, password="12345678", again="12345678").status_code == 303

    def test_a_reset_sets_a_new_password_and_ends_the_old_sessions(self, admin, client, git_wiki_dir):
        link = make_link(admin, "/admin/users/bob/reset")
        login(client, "bob")
        assert client.get("/page/index", headers=HOST).status_code == 200
        old_session = client.cookies["session"]
        before = section(git_wiki_dir, "bob")
        client.post("/logout", headers=HOST)
        resp = post(client, link, password="new-password", again="new-password")
        assert resp.status_code == 303 and section(git_wiki_dir, "bob") != before
        assert "Signed in as <strong>bob</strong>" in client.get("/page/index", headers=HOST).data.decode()   # signed in by the link
        client.set_cookie("session", old_session)
        assert client.get("/page/index", headers=HOST).status_code == 302   # the old session ended

    def test_invite_refuses_a_user_there_and_a_bad_name(self, admin):
        for data in [{"name": "bob", "role": "editor"}, {"name": "Bad Name", "role": "editor"},
                     {"name": "dave", "role": "boss"}, {"name": "me", "role": "editor"},
                     {"name": "none", "role": "editor"}]:
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

    def test_the_page_says_a_reset_is_a_way_into_the_account(self, admin, client, git_wiki_dir):
        # Zonewiki sends no mail: the admin holds the link, and it sets the
        # password for whoever opens it. The page doesn't claim otherwise.
        help_text = admin.get("/admin/users", headers=HOST).data.decode()
        assert "whoever opens that link signs in as them" in help_text
        link = make_link(admin, "/admin/users/bob/reset")
        client.post("/logout", headers=HOST)
        post(client, link, password="the admin's own", again="the admin's own")
        assert "Signed in as <strong>bob</strong>" in client.get("/page/index", headers=HOST).data.decode()

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

    @pytest.mark.parametrize("change", [("role", {"role": "editor"}), ("disable", {}), ("remove", {}), ("by hand", {})])
    def test_a_link_ends_when_its_maker_is_no_admin(self, client, users, git_wiki_dir, monkeypatch, change):
        """A link gives what its maker may give: carol's invite of an admin
        and her reset of bob's password stop working when she is demoted,
        disabled or removed (on the Users tab, or in the settings file by
        hand), and stay ended when she is an admin again."""
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "admin"}})
        login(client, "carol")
        links = [make_link(client, "/admin/users/invite", name="dave", role="admin"), make_link(client, "/admin/users/bob/reset")]
        login(client, "alice")
        host = make_link(client, "/admin/users/invite", name="erin", role="editor")   # alice's stays
        kind, data = change
        if kind == "by hand":
            monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        else:
            assert post(client, f"/admin/users/carol/{kind}", **data).status_code == 303
        client.post("/logout", headers=HOST)
        for link in links:
            assert client.get(link, headers=HOST).status_code == 404, kind
            resp = post(client, link, password="taken-over", again="taken-over")
            assert resp.status_code == 404 and section(git_wiki_dir, "dave") is None
        assert client.get(host, headers=HOST).status_code == 200
        assert '"by": "carol"' not in (git_wiki_dir / ".zone/links").read_text()
        if kind == "role":
            login(client, "alice")
            assert post(client, "/admin/users/carol/role", role="admin").status_code == 303
            client.post("/logout", headers=HOST)
            assert all(client.get(link, headers=HOST).status_code == 404 for link in links)

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
