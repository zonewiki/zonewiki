"""Users ([user …] in .zone/config, docs/spec/users.md): a login per user, the session
naming its user, per-user MCP tokens, and commits authored by whoever made
the change."""

import json
import re
import subprocess
from io import BytesIO

import pytest

from tests import settings
from tests.conftest import HOST, content_hash

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "")}


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))


def login(client, name, password):
    return client.post("/login", data={"username": name, "password": password}, headers=HOST)


def mcp(client, token, tool="list_pages", **args):
    return client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})


def authors(wiki):
    """Each commit's author and subject, newest first."""
    out = subprocess.run(["git", "log", "--format=%an|%s"], cwd=wiki, capture_output=True, text=True,
                         check=True, env=settings.GIT_ENV).stdout
    return [tuple(line.split("|", 1)) for line in out.splitlines()]


class TestLogin:

    def test_login_form_asks_for_the_user_only_with_users(self, client, users, monkeypatch):
        body = client.get("/login", headers=HOST).data.decode()
        assert 'name="username"' in body and 'autocomplete="username"' in body
        monkeypatch.setattr(settings, "USERS", {})
        monkeypatch.setattr(settings, "PASSWORD", "pw")
        assert 'name="username"' not in client.get("/login", headers=HOST).data.decode()

    def test_each_user_signs_in_with_their_own_password(self, client, users):
        assert client.get("/page/index", headers=HOST).status_code == 302
        assert login(client, "alice", "bob-pw").status_code == 200
        resp = login(client, "Alice", "alice-pw")   # names are lowercase; case and spaces don't matter
        assert resp.status_code == 302
        body = client.get("/page/index", headers=HOST).data.decode()
        assert '<span class="signout-user" title="Signed in as alice">alice</span>' in body
        client.post("/logout", headers=HOST)
        assert login(client, "bob", "bob-pw").status_code == 302
        assert ">bob</span>" in client.get("/page/index", headers=HOST).data.decode()

    def test_wrong_login_keeps_the_name_and_says_so(self, client, users):
        for name, password in [("alice", "nope"), ("carol", "alice-pw"), ("", "")]:
            resp = login(client, name, password)
            body = resp.data.decode()
            assert resp.status_code == 200 and "Incorrect user name or password." in body
        body = login(client, '"><b>x', "pw").data.decode()
        assert 'value="&#34;&gt;&lt;b&gt;x"' in body and "<b>x" not in body
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_wrong_logins_are_throttled(self, client, users):
        for _ in range(5):
            login(client, "alice", "guess")
        resp = login(client, "alice", "alice-pw")
        assert resp.status_code == 429 and "Try again in" in resp.data.decode()

    def test_logins_sent_at_once_are_counted_one_by_one(self, client, users):
        # Not all let in before the first counts as a failure: five are
        # checked, the rest wait.
        import threading
        client.get("/login", headers=HOST)   # the server with these users is up
        statuses = []
        def guess(i):
            statuses.append(login(client, "alice", f"guess{i}").status_code)
        threads = [threading.Thread(target=guess, args=(i,)) for i in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(statuses) == [200] * 5 + [429] * 10

    def test_an_ipv6_network_is_one_client(self, client, users):
        # A client has a whole /64 to take addresses from: they count together.
        def guess(addr):
            return client.post("/login", data={"username": "alice", "password": "guess"},
                               headers={**HOST, "X-Forwarded-For": addr})
        for i in range(5):
            assert guess(f"2001:db8:0:1::{i + 1}").status_code == 200
        assert guess("2001:db8:0:1:ffff::9").status_code == 429
        assert guess("2001:db8:0:2::1").status_code == 200   # another network

    def test_a_removed_user_is_signed_out(self, client, users, monkeypatch):
        login(client, "alice", "alice-pw")
        assert client.get("/page/index", headers=HOST).status_code == 200
        monkeypatch.setattr(settings, "USERS", {"bob": USERS["bob"]})
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_switching_from_a_password_to_users_signs_out(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "pw")
        client.post("/login", data={"password": "pw"}, headers=HOST)
        assert client.get("/page/index", headers=HOST).status_code == 200
        monkeypatch.setattr(settings, "PASSWORD", "")
        monkeypatch.setattr(settings, "USERS", dict(USERS))
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_no_name_without_users(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "pw")
        client.post("/login", data={"password": "pw"}, headers=HOST)
        body = client.get("/page/index", headers=HOST).data.decode()
        assert "signout-form" in body and "signout-user" not in body


class TestDisabled:
    """disabled = yes in a user's section: no sign-in, sessions end, the MCP
    token is refused; the files stay."""

    def test_a_disabled_user_cannot_sign_in_and_is_told_so_only_with_the_password(self, client, users, monkeypatch):
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"disabled": "yes"}})
        body = login(client, "alice", "alice-pw").data.decode()
        assert "Your account is disabled" in body
        assert client.get("/page/index", headers=HOST).status_code == 302
        body = login(client, "alice", "wrong").data.decode()
        assert "Incorrect user name or password." in body and "disabled" not in body

    def test_disabling_stops_the_session_and_enabling_lets_it_go_on(self, client, users, monkeypatch):
        login(client, "alice", "alice-pw")
        assert client.get("/page/index", headers=HOST).status_code == 200
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"disabled": "yes"}})
        assert client.get("/page/index", headers=HOST).status_code == 302
        # Enabled again, a session not yet expired works again (a new
        # password, not disabling, is what ends a stolen one).
        monkeypatch.setattr(settings, "USER_SETTINGS", {})
        assert client.get("/page/index", headers=HOST).status_code == 200

    def test_a_disabled_users_token_is_refused(self, client, users, monkeypatch):
        assert mcp(client, "alice-token").status_code == 200
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"disabled": "yes"}})
        assert mcp(client, "alice-token").status_code in (401, 403)


class TestMCP:

    def test_each_user_has_their_own_token(self, client, users):
        assert mcp(client, "alice-token").status_code == 200
        assert mcp(client, "wrong").status_code == 401
        assert mcp(client, "").status_code == 401   # bob has none: no empty token gets in

    def test_off_without_any_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "USERS", {"bob": USERS["bob"]})
        resp = mcp(client, "x")
        assert resp.status_code == 403 and "./run.sh user token NAME" in resp.get_json()["error"]


class TestSigningOut:
    """Signing out ends the session, not only the browser's cookie of it
    (docs/spec/security.md, Signing out): a copy of the cookie taken before
    stops working, and Zone remembers that across a restart."""

    def test_a_copy_of_the_cookie_stops_working(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        copy = client.cookies["session"]
        assert client.get("/page/index", headers=HOST).status_code == 200
        client.post("/logout", headers=HOST)
        client.set_cookie("session", copy)
        resp = client.get("/page/index", headers=HOST)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]
        # Kept as state beside the session key: the id and until when, not the cookie.
        kept = json.loads((git_wiki_dir / ".zone" / "signed-out").read_text())
        assert len(kept) == 1 and copy not in json.dumps(kept)
        assert ".zone" not in subprocess.run(["git", "status", "--porcelain"], cwd=git_wiki_dir,
                                             capture_output=True, text=True).stdout

    def test_other_sessions_of_the_user_go_on(self, client, users):
        login(client, "alice", "alice-pw")
        phone = client.cookies["session"]
        client.cookies.clear()
        login(client, "alice", "alice-pw")
        client.post("/logout", headers=HOST)
        client.set_cookie("session", phone)
        assert client.get("/page/index", headers=HOST).status_code == 200
        # And signing in again works.
        client.post("/logout", headers=HOST)
        login(client, "alice", "alice-pw")
        assert client.get("/page/index", headers=HOST).status_code == 200

    def test_the_wikis_one_password_too(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "one password")
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 302
        copy = client.cookies["session"]
        client.post("/logout", headers=HOST)
        client.set_cookie("session", copy)
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_a_cookie_zone_didnt_sign_signs_no_one_out(self, client, git_wiki_dir, users):
        client.set_cookie("session", "eyJhdXRoIjp0cnVlfQ.forged")
        client.post("/logout", headers=HOST)
        assert not (git_wiki_dir / ".zone" / "signed-out").exists()


class TestPrivateHome:
    """/private opens the signed-in user's private home, private/<user>/index
    (docs/spec/users.md, Private pages): made on the first visit, never
    renamed or deleted."""

    def test_opens_your_own_home_and_makes_it_once(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        resp = client.get("/private", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/page/private/alice/index"
        home = git_wiki_dir / "private/alice/index.md"
        assert home.read_text().startswith("# Private\n") and "## Links" in home.read_text()
        assert authors(git_wiki_dir)[0] == ("alice", "Create private/alice/index")
        assert "hidden from the other users of this wiki." in client.get("/page/private/alice/index", headers=HOST).data.decode()
        home.write_text("# Mine\n\n[[plans]]\n")
        client.get("/private", headers=HOST)
        assert home.read_text() == "# Mine\n\n[[plans]]\n"   # a later visit leaves it alone

    def test_each_user_gets_their_own(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.get("/private", headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        resp = client.get("/private", headers=HOST)
        assert resp.headers["Location"] == "/page/private/bob/index"
        assert (git_wiki_dir / "private/bob/index.md").exists()

    def test_needs_a_login(self, client, users):
        resp = client.get("/private", headers=HOST)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]

    def test_there_is_no_private_home_without_users(self, client, wiki_dir):
        assert client.get("/private", headers=HOST).status_code == 404
        assert not (wiki_dir / "private").exists()

    def test_it_is_never_renamed_or_deleted(self, client, wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.get("/private", headers=HOST)
        home = wiki_dir / "private/alice/index.md"
        assert client.post("/delete/private/alice/index", headers=HOST).status_code == 400
        assert client.post("/rename/private/alice/index", data={"new_name": "private/alice/start"},
                           headers=HOST).status_code == 400
        for tool, args in [("delete_page", {"slug": "private/alice/index"}),
                           ("rename_page", {"slug": "private/alice/index", "new_slug": "private/alice/start"})]:
            body = mcp(client, "alice-token", tool, **args).get_json()
            assert "can't be" in str(body), tool
        assert home.exists() and not (wiki_dir / "private/alice/start.md").exists()
        # Another page in the folder is an ordinary private page.
        write = wiki_dir / "private/alice/diary.md"
        write.write_text("# Diary\n")
        assert client.post("/delete/private/alice/diary", headers=HOST).status_code in (200, 302)

    def test_without_users_private_index_is_an_ordinary_page(self, client, wiki_dir):
        (wiki_dir / "private/alice").mkdir(parents=True)
        (wiki_dir / "private/alice/index.md").write_text("# Index\n")
        assert client.post("/delete/private/alice/index", headers=HOST).status_code in (200, 302)
        assert not (wiki_dir / "private/alice/index.md").exists()


class TestMakeShared:
    """Make shared (the page menu: a rename out of the user's private folder), a
    🔒 on private pages, and what a rename into the folder does to the links."""

    PLANS = "# Plans\n\nSee [[ideas]].\n"

    @pytest.fixture()
    def alice(self, client, git_wiki_dir, users):
        (git_wiki_dir / "plans.md").write_text(self.PLANS)
        (git_wiki_dir / "ideas.md").write_text("# Ideas\n\nA shared idea.\n")
        subprocess.run(["git", "add", "-A"], cwd=git_wiki_dir, check=True, env=settings.GIT_ENV)
        subprocess.run(["git", "commit", "-qm", "Pages"], cwd=git_wiki_dir, check=True, env=settings.GIT_ENV)
        login(client, "alice", "alice-pw")
        return client

    def view(self, client, slug):
        return client.get(f"/page/{slug}", headers=HOST).data.decode()

    def test_a_shared_page_offers_no_move_and_has_no_lock(self, alice):
        body = self.view(alice, "ideas")
        assert "Make private" not in body and "Make shared" not in body and 'class="lock"' not in body

    def test_a_private_page_offers_make_shared_and_has_a_lock(self, alice, git_wiki_dir):
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/notes/deep.md").parent.mkdir()
        (git_wiki_dir / "private/alice/notes/deep.md").write_text("# Deep\n")
        body = self.view(alice, "private/alice/notes/deep")
        assert "Make shared…" in body and "Make private" not in body
        assert 'name="new_name" value="notes/deep"' in body
        assert '<h1>Deep <span class="lock"' in body

    def test_not_offered_for_homes_quicknotes_or_without_users(self, alice, client, wiki_dir, monkeypatch):
        alice.get("/private", headers=HOST)
        note = alice.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        for slug in ("index", "private/alice/index", note):
            body = self.view(alice, slug)
            assert "Make shared" not in body, slug
        monkeypatch.setattr(settings, "USERS", {})
        monkeypatch.setattr(settings, "CONFIG_FILE", "# no login, on purpose\n")   # else: missingUsers
        assert "Make shared" not in self.view(client, "private/alice/index")

    def test_a_shared_page_cannot_be_renamed_into_the_folder(self, alice, git_wiki_dir):
        """Others have seen it, and link to it: shared stays shared. Nothing changes."""
        before = (git_wiki_dir / "plans.md").read_text()
        head = authors(git_wiki_dir)[0]
        for url, back in (("/rename/ideas", "/edit/ideas"), ("/rename/ideas", "/page/ideas")):
            data = {"new_name": "private/alice/ideas"}
            if back == "/page/ideas":
                data["from"] = "page"
            resp = alice.post(url, data=data, headers=HOST)
            loc = resp.headers["Location"]
            assert resp.status_code == 302 and loc.startswith(back + "?rename_name=private/alice/ideas&rename_error=")
            assert "stays+shared" in loc or "stays%20shared" in loc
        assert (git_wiki_dir / "ideas.md").exists() and not (git_wiki_dir / "private/alice/ideas.md").exists()
        assert (git_wiki_dir / "plans.md").read_text() == before and authors(git_wiki_dir)[0] == head
        body = alice.get("/page/ideas?rename_error=x&rename_name=private/alice/ideas", headers=HOST).get_data(as_text=True)
        assert 'name="unlink"' not in body

    def test_making_it_shared_takes_its_private_images_along(self, alice, git_wiki_dir):
        """Others can't open /private/alice/_assets/…: the page gets copies in
        _assets/ (a taken name gets a number), her own files stay."""
        mine = git_wiki_dir / "private/alice/_assets"
        mine.mkdir(parents=True)
        (mine / "a.png").write_bytes(b"A")
        (mine / "b.png").write_bytes(b"B")
        (git_wiki_dir / "_assets").mkdir(exist_ok=True)
        (git_wiki_dir / "_assets/b.png").write_bytes(b"other b")        # a different file by that name
        (git_wiki_dir / "private/alice/trip.md").write_text(
            "# Trip\n\n![a](/private/alice/_assets/a.png) ![b](/private/alice/_assets/b.png)\n"
            "`/private/alice/_assets/a.png`\n")
        (git_wiki_dir / "private/alice/other.md").write_text("# Other\n\n![a](/private/alice/_assets/a.png)\n")
        subprocess.run(["git", "add", "-A"], cwd=git_wiki_dir, check=True, env=settings.GIT_ENV)
        subprocess.run(["git", "commit", "-qm", "Her pages"], cwd=git_wiki_dir, check=True, env=settings.GIT_ENV)
        resp = alice.post("/rename/private/alice/trip", data={"from": "page", "new_name": "trip"}, headers=HOST)
        assert resp.headers["Location"] == "/page/trip"
        assert (git_wiki_dir / "trip.md").read_text() == (
            "# Trip\n\n![a](/_assets/a.png) ![b](/_assets/b-2.png)\n`/private/alice/_assets/a.png`\n")
        assert (git_wiki_dir / "_assets/a.png").read_bytes() == b"A"
        assert (git_wiki_dir / "_assets/b-2.png").read_bytes() == b"B"
        assert (git_wiki_dir / "_assets/b.png").read_bytes() == b"other b"
        assert (mine / "a.png").exists() and "/private/alice/_assets/a.png" in (git_wiki_dir / "private/alice/other.md").read_text()
        # One commit, with the copies.
        shown = subprocess.run(["git", "log", "-1", "--grep=Rename private/alice/trip", "--name-only", "--format="],
                               cwd=git_wiki_dir, capture_output=True, text=True, check=True).stdout
        assert "_assets/b-2.png" in shown and "_assets/a.png" in shown and "trip.md" in shown

    def test_making_it_shared_re_points_every_link(self, alice, git_wiki_dir):
        """Her own pages and tasks, and the shared page that linked to her private
        one (marked 🔒 for her, plain text for the others), now reach it."""
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/ideas.md").write_text("# Ideas\n")
        links = "[[private/alice/ideas]] [[private/alice/ideas|Our ideas]] [x](/page/private/alice/ideas) `[[private/alice/ideas]]`\n"
        (git_wiki_dir / "private/alice/diary.md").write_text("# Diary\n\n" + links)
        (git_wiki_dir / "plans.md").write_text("# Plans\n\n" + links)
        mine = alice.post("/api/tasks", json={"title": "Think about [[private/alice/ideas]]", "private": True}).get_json()["id"]
        alice.post("/rename/private/alice/ideas", data={"from": "page", "new_name": "shared-ideas"}, headers=HOST)
        want = "# {}\n\n[[shared-ideas]] [[shared-ideas|Our ideas]] [x](/page/shared-ideas) `[[private/alice/ideas]]`\n"
        assert (git_wiki_dir / "private/alice/diary.md").read_text() == want.format("Diary")
        assert (git_wiki_dir / "plans.md").read_text() == want.format("Plans")
        titles = {t["id"]: t["title"] for t in alice.get("/api/tasks", headers=HOST).get_json()}
        assert titles[mine] == "Think about [[shared-ideas]]"

    def test_a_name_taken_comes_back_with_the_error(self, alice, git_wiki_dir):
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/ideas.md").write_text("# Mine\n")
        resp = alice.post("/rename/ideas", data={"from": "page", "new_name": "private/alice/ideas"}, headers=HOST)
        assert "rename_error=A+page+named" in resp.headers["Location"] and (git_wiki_dir / "ideas.md").exists()

    def test_page_lists_and_search_mark_private_pages(self, alice, git_wiki_dir):
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/diary.md").write_text("# Diary\n\nzebracorn\n")
        (git_wiki_dir / "ideas.md").write_text("# Ideas\n\nzebracorn\n")
        pages = {p["slug"]: p for p in alice.get("/api/pages-list", headers=HOST).get_json()}
        assert pages["private/alice/diary"]["private"] is True and pages["ideas"]["private"] is False
        hits = {r["slug"]: r for r in alice.get("/api/search?q=zebracorn", headers=HOST).get_json()}
        assert hits["private/alice/diary"]["meta"].startswith("🔒 ") and "🔒" not in hits["ideas"]["meta"]


class TestModuleMenu:
    """With users the switcher has two pills by kind, Wiki · Private (pages)
    and Tasks · Notes (tools), Private and Notes with a lock, and a user menu
    (who, Sign out) behind the user button; without them one pill with Notes
    third."""

    @staticmethod
    def parts(body):
        crumb = body.split('<div class="breadcrumb"', 1)[1].split("</nav>", 1)[0]
        top = body.split('<div class="top-actions">', 1)[1].split("</div>", 1)[0] if '<div class="top-actions">' in body else ""
        bar = body.split('<nav class="action-buttons"', 1)[1].split("</nav>", 1)[0]
        return crumb, bar, top

    def test_pages_and_tools_are_a_group_each(self, client, users):
        login(client, "alice", "alice-pw")
        body = client.get("/page/index", headers=HOST).data.decode()
        crumb, bar, top = self.parts(body)
        groups = re.findall(r'<span class="module-group">(.*?)</span>(?=<span class="module-group">|</nav>|$)', crumb + "</nav>", re.S)
        assert [re.findall(r'data-module="(\w+)"', g) for g in groups] == [["wiki", "private"], ["tasks", "notes"]]
        assert 'href="/private"' in groups[0] and 'href="/notes"' in groups[1]
        # A lock in Private and in Notes: only you see them; not in Wiki or Tasks.
        links = dict(re.findall(r'data-module="(\w+)"(.*?)</a>', crumb, re.S))
        assert [k for k, v in links.items() if "module-lock" in v] == ["private", "notes"]
        assert 'title="Private: only you see it"' in crumb and 'title="Notes: only you see it"' in crumb
        # The phone's bar: the four modules with a divider between the groups, then the actions.
        assert re.findall(r'class="action-label"[^>]*>(\w+)<', bar) == ["Wiki", "Private", "Tasks", "Notes", "Note", "Task", "Search"]
        assert bar.count('class="action-sep"') == 3
        # Notes' tab carries a lock (Private's icon is one); no other tab does.
        assert bar.count('class="action-lock"') == 1
        assert bar.index('data-module="notes"') < bar.index('class="action-lock"') < bar.index('class="action-label"', bar.index('data-module="notes"'))
        assert bar.index('data-module="private"') < bar.index('class="action-sep"') < bar.index('data-module="tasks"')
        assert "module-menu-btn" not in bar and "module-menu-btn" not in crumb
        # Desktop: the user button after Search opens the menu: who, Connect an assistant, Change password and Sign out only.
        assert top.index("top-search") < top.index('class="top-user module-menu-btn"')
        assert '>A</span></button>' in top and 'title="alice"' in top and 'aria-haspopup="menu" aria-expanded="false"' in top
        assert body.count('class="module-menu page-menu"') == 1 and 'module-menu' not in bar
        menu = body.split('class="module-menu page-menu"', 1)[1].split("</div></div>", 1)[0]
        assert re.findall(r'href="([^"]*)"', menu) == ["/assistant", "/password"]
        assert 'Signed in as <strong>alice</strong>' in menu and 'action="/logout"' in menu and ">Sign out</button>" in menu

    def test_a_private_list_is_in_its_users_switcher_alone(self, client, wiki_dir, users):
        # The task list switcher lists a list declared in your folder with a
        # lock; another user doesn't see it at all.
        (wiki_dir / "shop.md").write_text("# Shop\n\n[[tasks: list=shop]]\n", encoding="utf-8")
        (wiki_dir / "private/alice").mkdir(parents=True)
        (wiki_dir / "private/alice/gifts.md").write_text("# Gifts\n\n[[tasks: list=gifts]]\n", encoding="utf-8")
        login(client, "alice", "alice-pw")
        nav = client.get("/tasks", headers=HOST).data.decode().split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        entries = dict(re.findall(r'data-place="([^"]+)"(.*?)</a>', nav, re.S))
        assert list(entries) == ["list:gifts", "list:shop", "all"]
        assert "tasks-place-lock" in entries["list:gifts"] and "only you see it" in entries["list:gifts"]
        assert "tasks-place-lock" not in entries["list:shop"]
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        body = client.get("/tasks", headers=HOST).data.decode()
        assert 'data-place="list:shop"' in body and "gifts" not in body
        # alice's page embedding a shared list is no Backlink of it for bob.
        (wiki_dir / "private/alice/errands.md").write_text("# Errands\n\n[[tasks: list=shop]]\n", encoding="utf-8")
        body = client.get("/tasks?list=shop", headers=HOST).data.decode()
        assert 'href="/page/shop"' in body and "errands" not in body
        assert "gifts" not in str(client.get("/api/tasks/due-count?places=1").get_json())

    def test_an_empty_quicknote_says_it_is_private(self, client, users):
        login(client, "alice", "alice-pw")
        assert "only you see them" in client.get("/notes", headers=HOST).data.decode()

    def test_without_users_quicknote_stays_a_pill_in_the_third_place(self, client, wiki_dir):
        body = client.get("/page/index", headers=HOST).data.decode()
        crumb, bar, top = self.parts(body)
        assert "module-menu" not in body and "top-user" not in top
        assert crumb.count('class="module-group"') == 1 and 'data-module="private"' not in crumb and "module-lock" not in crumb
        assert "action-lock" not in body.split('<nav class="action-buttons"', 1)[1].split("</nav>", 1)[0]
        assert crumb.index('data-module="tasks"') < crumb.index('data-module="notes"')
        assert re.findall(r'class="action-label"[^>]*>(\w+)<', bar)[:3] == ["Wiki", "Tasks", "Notes"]

    def test_the_pills_are_current_in_private_and_quicknote_views(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.get("/private", headers=HOST)
        (git_wiki_dir / "private/alice/diary.md").write_text("# Diary\n")
        note = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        (git_wiki_dir / "plans.md").write_text("# Plans\n")
        for url, module in [("/page/private/alice/index", "private"), ("/page/private/alice/diary", "private"),
                            ("/notes", "notes"), (f"/page/{note}", "notes"),
                            ("/page/plans", "wiki"), ("/tasks", "tasks")]:
            body = client.get(url, headers=HOST).data.decode()
            assert f'<body data-module="{module}"' in body, url
            crumb, bar, top = self.parts(body)
            for part in (crumb, bar):
                current = re.findall(r'data-module="(\w+)"[^>]*aria-current="page"', part)
                assert current == [module], (url, current)


class TestSharedNewPage:
    """A link on a private page to a page that doesn't exist would make a shared
    page: the link is marked, and the editor it opens warns and says how to make
    it private."""

    @pytest.fixture()
    def alice(self, client, git_wiki_dir, users):
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/diary.md").write_text(
            "# Diary\n\n[[Garden]] [[garden#beds|Beds]] [[Plans]] [[private/alice/later]] [[private/bob/x]]\n")
        (git_wiki_dir / "plans.md").write_text("# Plans\n\n[[Garden]]\n")
        login(client, "alice", "alice-pw")
        return client

    def view(self, client, slug):
        return client.get(f"/page/{slug}", headers=HOST).data.decode()

    def test_a_link_to_a_new_shared_page_is_marked(self, alice):
        body = self.view(alice, "private/alice/diary")
        assert ('<a href="/page/garden?from=private" class="wikilink wikilink-missing wikilink-shared-new" '
                'title="Creates a shared page">Garden</a>') in body
        assert '<a href="/page/garden?from=private#beds" class="wikilink wikilink-missing wikilink-shared-new"' in body
        # an existing page, a new private page, another user's: nothing to warn of
        assert '<a href="/page/plans" class="wikilink">Plans</a>' in body
        assert 'href="/page/private/alice/later" class="wikilink wikilink-missing">' in body
        assert body.count("wikilink-shared-new") == 2

    def test_a_shared_page_marks_nothing(self, alice):
        assert "wikilink-shared-new" not in self.view(alice, "plans") and "from=private" not in self.view(alice, "plans")

    def test_without_users_nothing_is_marked(self, client, wiki_dir):
        (wiki_dir / "private/alice").mkdir(parents=True)
        (wiki_dir / "private/alice/diary.md").write_text("# Diary\n\n[[Garden]]\n")
        assert "wikilink-shared-new" not in self.view(client, "private/alice/diary")

    def test_the_preview_marks_by_the_page_it_is_for(self, alice):
        def preview(query):
            return alice.post(f"/api/preview{query}", data="[[Garden]]", headers=HOST).data.decode()
        assert "wikilink-shared-new" in preview("?page=private/alice/diary")
        assert "wikilink-shared-new" not in preview("")
        assert "wikilink-shared-new" not in preview("?page=plans")
        assert "wikilink-shared-new" not in preview("?page=private/bob/x")   # not hers

    def test_the_missing_page_opens_its_editor_with_the_hint(self, alice):
        resp = alice.get("/page/garden?from=private", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/edit/garden?from=private"
        assert alice.get("/page/garden", headers=HOST).headers["Location"] == "/edit/garden"
        assert alice.get("/page/garden?from=nonsense", headers=HOST).headers["Location"] == "/edit/garden"

    def test_the_editor_warns_and_names_the_private_page(self, alice, git_wiki_dir):
        body = alice.get("/edit/garden?from=private", headers=HOST).data.decode()
        assert 'id="shared-new-banner"' in body and "<code>private/alice/garden</code>" in body
        assert "This new page is shared" in body
        for url in ("/edit/garden", "/edit/garden?from=other", "/edit/plans?from=private",   # no hint; a page that exists
                    "/edit/private/alice/garden?from=private"):                               # private already
            assert "shared-new-banner" not in alice.get(url, headers=HOST).data.decode(), url
        (git_wiki_dir / "private/alice/garden.md").write_text("# Mine\n")
        assert "shared-new-banner" not in alice.get("/edit/garden?from=private", headers=HOST).data.decode()

    def test_the_editor_does_not_warn_without_users(self, client, wiki_dir):
        assert "shared-new-banner" not in client.get("/edit/garden?from=private", headers=HOST).data.decode()

    def test_a_saved_shared_page_cannot_be_made_private(self, alice, git_wiki_dir):
        """There is no button for it: once saved, the page is shared for good."""
        alice.post("/api/save/garden", data={"content": "# Garden\n\nbeds\n"}, headers=HOST)
        resp = alice.post("/rename/garden", data={"new_name": "private/alice/garden"}, headers=HOST)
        assert "rename_error=" in resp.headers["Location"]
        assert (git_wiki_dir / "garden.md").exists() and not (git_wiki_dir / "private/alice/garden.md").exists()


class TestLinkToPrivateFromShared:
    """A shared page linking to the user's private page: marked for the author
    (others see plain text), so it is noticed."""

    @pytest.fixture()
    def alice(self, client, git_wiki_dir, users):
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/diary.md").write_text("# Diary\n\n[[private/alice/other]] [[plans]]\n")
        (git_wiki_dir / "private/alice/other.md").write_text("# Other\n")
        (git_wiki_dir / "plans.md").write_text("# Plans\n\n[[private/alice/diary]] [[index]]\n")
        login(client, "alice", "alice-pw")
        return client

    def view(self, client, slug):
        return client.get(f"/page/{slug}", headers=HOST).data.decode()

    def test_the_author_sees_the_mark(self, alice):
        body = self.view(alice, "plans")
        assert ('<a href="/page/private/alice/diary" class="wikilink wikilink-private" '
                'title="Links to a private page: others see plain text">private/alice/diary</a>') in body
        assert body.count("wikilink-private") == 1   # [[index]] is a shared page

    def test_private_pages_link_privately_without_a_mark(self, alice):
        assert "wikilink-private" not in self.view(alice, "private/alice/diary")

    def test_others_see_plain_text_and_no_mark(self, alice):
        alice.post("/logout", headers=HOST)
        login(alice, "bob", "bob-pw")
        body = self.view(alice, "plans")
        assert "wikilink-private" not in body and 'href="/page/private/alice/diary"' not in body

    def test_the_preview_marks_by_the_page_it_is_for(self, alice):
        def preview(query):
            return alice.post(f"/api/preview{query}", data="[[private/alice/diary]]", headers=HOST).data.decode()
        assert "wikilink-private" in preview("?page=plans")
        assert "wikilink-private" not in preview("?page=private/alice/other")
        assert "wikilink-private" not in preview("")

    def test_without_users_nothing_is_marked(self, client, wiki_dir):
        (wiki_dir / "private/alice").mkdir(parents=True)
        (wiki_dir / "private/alice/diary.md").write_text("# Diary\n")
        (wiki_dir / "plans.md").write_text("# Plans\n\n[[private/alice/diary]]\n")
        assert "wikilink-private" not in client.get("/page/plans", headers=HOST).data.decode()


class TestLinkToPrivateFromShared:
    """A shared page linking to the user's private page: marked for the author
    (others see plain text), so it is noticed."""

    @pytest.fixture()
    def alice(self, client, git_wiki_dir, users):
        (git_wiki_dir / "private/alice").mkdir(parents=True)
        (git_wiki_dir / "private/alice/diary.md").write_text("# Diary\n\n[[private/alice/other]] [[plans]]\n")
        (git_wiki_dir / "private/alice/other.md").write_text("# Other\n")
        (git_wiki_dir / "plans.md").write_text("# Plans\n\n[[private/alice/diary]] [[index]]\n")
        login(client, "alice", "alice-pw")
        return client

    def view(self, client, slug):
        return client.get(f"/page/{slug}", headers=HOST).data.decode()

    def test_the_author_sees_the_mark(self, alice):
        body = self.view(alice, "plans")
        assert ('<a href="/page/private/alice/diary" class="wikilink wikilink-private" '
                'title="Links to a private page: others see plain text">private/alice/diary</a>') in body
        assert body.count("wikilink-private") == 1   # [[index]] is a shared page

    def test_private_pages_link_privately_without_a_mark(self, alice):
        assert "wikilink-private" not in self.view(alice, "private/alice/diary")

    def test_others_see_plain_text_and_no_mark(self, alice):
        alice.post("/logout", headers=HOST)
        login(alice, "bob", "bob-pw")
        body = self.view(alice, "plans")
        assert "wikilink-private" not in body and 'href="/page/private/alice/diary"' not in body

    def test_the_preview_marks_by_the_page_it_is_for(self, alice):
        def preview(query):
            return alice.post(f"/api/preview{query}", data="[[private/alice/diary]]", headers=HOST).data.decode()
        assert "wikilink-private" in preview("?page=plans")
        assert "wikilink-private" not in preview("?page=private/alice/other")
        assert "wikilink-private" not in preview("")

    def test_without_users_nothing_is_marked(self, client, wiki_dir):
        (wiki_dir / "private/alice").mkdir(parents=True)
        (wiki_dir / "private/alice/diary.md").write_text("# Diary\n")
        (wiki_dir / "plans.md").write_text("# Plans\n\n[[private/alice/diary]]\n")
        assert "wikilink-private" not in client.get("/page/plans", headers=HOST).data.decode()


class TestAuthors:

    def test_commits_are_by_whoever_made_the_change(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.post("/new", data={"slug": "plans"}, headers=HOST)
        client.post("/api/tasks", json={"title": "Call Sam"}, headers=HOST)
        client.post("/api/notes", data={"title": "Idea"}, headers=HOST)
        client.post("/api/upload", data={"file": (BytesIO(b"\x89PNG"), "a.png")},
                    content_type="multipart/form-data", headers=HOST)
        mcp(client, "alice-token", "write_page", slug="from-mcp", content="# From MCP\n")
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        client.post("/rename/plans", data={"new_name": "plans-2026"}, headers=HOST)
        log = authors(git_wiki_dir)
        assert log[0] == ("bob", "Rename plans to plans-2026")
        by = {subject: author for author, subject in log[1:]}
        assert by["Create plans"] == "alice" and by["Upload a.png"] == "alice"
        assert by["Create task"] == "alice"
        assert [a for a, s in log if s.startswith("Create from-mcp")] == ["alice"]   # alice's token
        assert [a for a, s in log if s.endswith("_idea")] == ["alice"]               # the note
        assert log[-1][0] == "Zone"                                                  # Zone's start-up commit

    def test_without_users_zone_is_the_author(self, client, git_wiki_dir):
        client.post("/new", data={"slug": "plans"}, headers=HOST)
        assert authors(git_wiki_dir)[0] == ("Zone", "Create plans")

    def test_history_names_the_author(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.post("/new", data={"slug": "plans"}, headers=HOST)
        body = client.get("/history/plans", headers=HOST).data.decode()
        assert '<span class="hist-by"> · alice</span>' in body
        client.post("/logout", headers=HOST)
        text = mcp(client, "alice-token", "history", slug="plans").get_json()["result"]["content"][0]["text"]
        assert "Create plans (by alice)" in text

    def test_comments_name_their_author(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        task = client.post("/api/tasks", json={"title": "Call Sam"}, headers=HOST).get_json()
        resp = client.post(f"/api/tasks/{task['id']}/comments", json={"text": "Left a message"}, headers=HOST)
        [comment] = resp.get_json()["comments"]
        assert comment["by"] == "alice"
        resp = client.put(f"/api/tasks/{task['id']}/comments/{comment['id']}", json={"text": "Called"}, headers=HOST)
        assert resp.get_json()["comments"][0]["by"] == "alice"   # an edit keeps the author
        assert '"by": "alice"' in (git_wiki_dir / "_tasks" / "tasks.json").read_text()
        client.post("/logout", headers=HOST)
        text = mcp(client, "alice-token", "search_tasks", query="Sam").get_json()["result"]["content"][0]["text"]
        assert "(alice): Called" in text

    def test_task_history_names_who_did_what(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        task = client.post("/api/tasks", json={"title": "Water plants", "due_date": "2030-01-01",
                                              "recurrence": {"type": "weekly", "interval": 1}}, headers=HOST).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"due_date": "2030-01-02"}, headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        done = client.put(f"/api/tasks/{task['id']}", json={"completed": True}, headers=HOST).get_json()
        events = [(e["event"], e.get("by")) for e in done["history"]]
        assert events == [("created", "alice"), ("due", "alice"), ("done", "bob")]
        [spawn] = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["spawned_from"] == task["id"]]
        assert spawn["history"][-1]["event"] == "repeat" and spawn["history"][-1]["by"] == "bob"
        # Undo puts a snapshot back: the authors stay.
        client.put(f"/api/tasks/{task['id']}", json={"completed": False, "history": done["history"][:2]}, headers=HOST)
        [back] = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] == task["id"]]
        assert [e.get("by") for e in back["history"]][:2] == ["alice", "alice"]

    def test_nobody_writes_under_anothers_name(self, client, users):
        # The lists a client sends whole (an undo, a restore, an import) may
        # say anything: a name other than the sender's stays only on what
        # Zone has seen under it (docs/spec/users.md, Who made a change).
        login(client, "bob", "bob-pw")
        forged_c = {"id": "c-forged", "at": "2026-01-02T10:00", "by": "alice", "text": "I approve"}
        forged_h = {"at": "2026-01-02T10:00", "event": "done", "by": "alice"}
        own_c = {"id": "c-own", "at": "2026-01-02T10:00", "by": "bob", "text": "Mine"}
        task = client.post("/api/tasks", json={"title": "Budget"}, headers=HOST).get_json()
        put = client.put(f"/api/tasks/{task['id']}", json={"comments": [forged_c, own_c], "history": [forged_h]},
                         headers=HOST).get_json()
        assert [(c["text"], c.get("by")) for c in put["comments"]] == [("I approve", None), ("Mine", "bob")]
        assert [e.get("by") for e in put["history"]] == [None]
        [batch] = client.post("/api/tasks/batch-update", json={"tasks": [{"id": task["id"], "history": [forged_h]}]},
                             headers=HOST).get_json()
        assert [e.get("by") for e in batch["history"]] == [None]
        snap = {"id": "restored-1", "title": "Restored", "comments": [forged_c], "history": [forged_h]}
        [restored] = client.post("/api/tasks/restore", json={"tasks": [snap]}, headers=HOST).get_json()
        assert "by" not in restored["comments"][0] and "by" not in restored["history"][0]
        client.post("/api/tasks/import", json={"file": [{**snap, "id": "00000000-0000-4000-8000-000000000001"}]},
                    headers=HOST)
        imported = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["title"] == "Restored"]
        assert len(imported) == 2
        assert all("by" not in t["comments"][0] and "by" not in t["history"][0] for t in imported)

    def test_an_undo_keeps_the_names_zone_has_seen(self, client, users):
        login(client, "alice", "alice-pw")
        task = client.post("/api/tasks", json={"title": "Call Sam"}, headers=HOST).get_json()
        task = client.post(f"/api/tasks/{task['id']}/comments", json={"text": "Left a message"}, headers=HOST).get_json()
        [comment] = task["comments"]
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        # A comment's delete, undone with the whole list.
        client.delete(f"/api/tasks/{task['id']}/comments/{comment['id']}", headers=HOST)
        back = client.put(f"/api/tasks/{task['id']}", json={"comments": task["comments"]}, headers=HOST).get_json()
        assert [(c["text"], c["by"]) for c in back["comments"]] == [("Left a message", "alice")]
        # The task's delete, undone with its snapshot.
        client.delete(f"/api/tasks/{task['id']}", headers=HOST)
        [restored] = client.post("/api/tasks/restore", json={"tasks": [back]}, headers=HOST).get_json()
        assert restored["comments"][0]["by"] == "alice"
        assert [e["by"] for e in restored["history"]] == [e["by"] for e in back["history"]] == ["alice"]
        # Seen as alice's is that comment, not another under its name.
        other = {**comment, "id": "c-other"}
        put = client.put(f"/api/tasks/{task['id']}", json={"comments": [other]}, headers=HOST).get_json()
        assert "by" not in put["comments"][0]

    def test_an_admins_import_keeps_the_files_names(self, client, users, monkeypatch):
        # A wiki moved or a backup put back: the admin's to do.
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        login(client, "alice", "alice-pw")
        client.post("/api/tasks/import", json={"file": [{
            "id": "00000000-0000-4000-8000-000000000002", "title": "Moved",
            "comments": [{"id": "c1", "at": "2026-01-02T10:00", "by": "bob", "text": "hi"}]}]}, headers=HOST)
        [moved] = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["title"] == "Moved"]
        assert moved["comments"][0]["by"] == "bob"

    def test_without_users_comments_and_history_have_no_author(self, client, git_wiki_dir):
        task = client.post("/api/tasks", json={"title": "Call Sam"}, headers=HOST).get_json()
        client.post(f"/api/tasks/{task['id']}/comments", json={"text": "Left a message"}, headers=HOST)
        client.put(f"/api/tasks/{task['id']}", json={"due_date": "2030-01-02"}, headers=HOST)
        assert '"by"' not in (git_wiki_dir / "_tasks" / "tasks.json").read_text()
        assert "hist-by" not in client.get("/history/index", headers=HOST).data.decode()


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


class TestDrafts:
    """Several people on one page: the text someone auto-saved is
    committed in their name before another's write to the page, so it is
    in git under its author and not in the other's commit."""

    PAGE = "# Plans\n\nThe start.\n\n- [ ] Item\n"
    BOBS = "# Plans\n\nThe start.\n\n- [ ] Item\n\nBob's line.\n"

    @pytest.fixture()
    def bobs_draft(self, client, git_wiki_dir, users):
        """plans, committed; bob's auto-save on it, not committed; alice signed in."""
        (git_wiki_dir / "plans.md").write_text(self.PAGE)
        git(git_wiki_dir, "add", "-A")
        git(git_wiki_dir, "commit", "-q", "-m", "Plans")
        login(client, "bob", "bob-pw")
        assert client.post("/api/save/plans", data={"content": self.BOBS, "file_hash": content_hash(self.PAGE)},
                           headers=HOST).status_code == 200
        client.post("/logout", headers=HOST)
        login(client, "alice", "alice-pw")
        return git_wiki_dir

    def test_another_users_save_commits_the_draft_first(self, client, bobs_draft):
        alices = self.BOBS + "\nAlice's line.\n"
        assert client.post("/api/save/plans", data={"content": alices, "file_hash": content_hash(self.BOBS)},
                           headers=HOST).status_code == 200
        assert authors(bobs_draft)[0] == ("bob", "Save plans")
        assert git(bobs_draft, "show", "HEAD:plans.md") == self.BOBS
        client.post("/edit/plans", data={"content": alices, "file_hash": content_hash(alices)}, headers=HOST)
        assert authors(bobs_draft)[:2] == [("alice", "Update plans"), ("bob", "Save plans")]
        diff = git(bobs_draft, "show", "HEAD", "--format=")
        assert "+Alice's line." in diff and "+Bob's line." not in diff

    def test_done_by_another_user_doesnt_take_the_draft(self, client, bobs_draft):
        client.post("/edit/plans", data={"content": self.BOBS, "file_hash": content_hash(self.BOBS)}, headers=HOST)
        assert authors(bobs_draft)[0] == ("bob", "Save plans")
        assert git(bobs_draft, "status", "--porcelain") == ""

    def test_ones_own_drafts_stay_uncommitted(self, client, bobs_draft):
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        more = self.BOBS + "More.\n"
        client.post("/api/save/plans", data={"content": more, "file_hash": content_hash(self.BOBS)}, headers=HOST)
        assert "Save plans" not in [s for a, s in authors(bobs_draft)]   # no commit per pause in typing
        client.post("/edit/plans", data={"content": more, "file_hash": content_hash(more)}, headers=HOST)
        assert authors(bobs_draft)[0] == ("bob", "Update plans")

    @pytest.mark.parametrize("write", ["checkbox", "delete", "rename", "commit-all", "commit-file", "mcp"])
    def test_other_writes_commit_the_draft_first(self, client, bobs_draft, write):
        """Bob's line goes into git in a commit of his, never in one of alice's."""
        if write == "checkbox":
            client.post("/api/toggle-checkbox/plans", data={"idx": "0"}, headers=HOST)
        elif write == "delete":
            client.post("/delete/plans", headers=HOST)
        elif write == "rename":
            client.post("/rename/plans", data={"new_name": "plans-2"}, headers=HOST)
        elif write == "commit-all":
            client.post("/api/commit-all", headers=HOST)
        elif write == "commit-file":
            client.post("/api/commit-file", json={"filepath": "plans.md"}, headers=HOST)
        else:
            mcp(client, "alice-token", "edit_page", slug="plans", old_string="The start.", new_string="The begin.")
        with_line = [author for author, diff in self.commits(bobs_draft) if "+Bob's line." in diff]
        assert with_line == ["bob"]

    @staticmethod
    def commits(wiki):
        """Each commit's author and diff, newest first."""
        shas = git(wiki, "log", "--format=%H").split()
        return [(git(wiki, "show", "-s", "--format=%an", sha).strip(), git(wiki, "show", "--format=", sha))
                for sha in shas]

    @pytest.mark.parametrize("write", ["rename a page it links to", "append a note to it"])
    def test_writes_that_change_it_on_the_way_commit_the_draft_first(self, client, bobs_draft, write):
        """A rename re-points links on bob's page, an append adds to it:
        his draft goes into git in his name first, not in alice's commit."""
        (bobs_draft / "golf.md").write_text("# Golf\n")
        git(bobs_draft, "add", "golf.md")
        git(bobs_draft, "commit", "-q", "-m", "Golf")
        (bobs_draft / "plans.md").write_text(self.BOBS + "\nSee [[golf]].\n")   # still bob's draft
        if write.startswith("rename"):
            client.post("/rename/golf", data={"new_name": "golfing"}, headers=HOST)
        else:
            note = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
            client.post(f"/append/{note}", data={"page": "plans", "heading": "Idea"}, headers=HOST)
        with_line = [author for author, diff in self.commits(bobs_draft) if "+Bob's line." in diff]
        assert with_line == ["bob"]

    def test_a_merge_names_whose_change_it_met(self, client, git_wiki_dir, users):
        """alice's save from the page as it was meets bob's draft: merged,
        named, and his draft committed as his first."""
        (git_wiki_dir / "plans.md").write_text(self.PAGE)
        login(client, "alice", "alice-pw")
        client.get("/edit/plans", headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        client.post("/api/save/plans", data={"content": self.BOBS, "file_hash": content_hash(self.PAGE)}, headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "alice", "alice-pw")
        resp = client.post("/api/save/plans", data={"content": self.PAGE.replace("The start.", "The begin."),
                                                    "file_hash": content_hash(self.PAGE), "merge": "1"}, headers=HOST)
        assert resp.get_json()["by"] == "bob" and "Bob's line." in resp.get_json()["content"]
        assert authors(git_wiki_dir)[0] == ("bob", "Save plans")

    def test_without_users_nothing_changes(self, client, git_wiki_dir):
        (git_wiki_dir / "plans.md").write_text(self.PAGE)
        client.post("/api/save/plans", data={"content": self.BOBS, "file_hash": content_hash(self.PAGE)},
                    headers=HOST)
        client.post("/api/save/plans", data={"content": self.BOBS + "x\n", "file_hash": content_hash(self.BOBS)},
                    headers=HOST)
        assert "Save plans" not in [s for a, s in authors(git_wiki_dir)]
