"""Usage (/usage, docs/spec/users.md): how much the wiki is used, as counts
a day, for its admins; the files in .zone/usage/ (numbers in one, the day
each user was last active in the other)."""
import json
import re
import subprocess
from datetime import date

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", ""), "bob": ("bob-pw", "bob-token"), "carol": ("carol-pw", "")}
BOB_MCP = {"Authorization": "Bearer bob-token"}


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"}})


def login(client, name):
    client.cookies.clear()
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def page(client):
    resp = client.get("/usage", headers=HOST)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def sums(text):
    """The first table: label → [last 7 days, last 30 days, last 8 weeks]."""
    table = re.search(r'<table class="usage-table usage-sums">(.*?)</table>', text, re.S).group(1)
    rows = re.findall(r'<tr><th scope="row">([^<]+?)(?: <span class="usage-hint">[^<]*</span>)?</th>(.*?)</tr>', table, re.S)
    return {label: [int(n) for n in re.findall(r"<td>(\d+)</td>", cells)] for label, cells in rows}


def last_active(text):
    table = re.search(r'<table class="usage-table usage-users">(.*?)</table>', text, re.S)
    if not table:
        return None
    return dict(re.findall(r'<tr><th scope="row">([^<]+)</th><td>(?:<time datetime="([^"]*)">|<span)', table.group(1)))


def tool(client, name, **args):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    resp = client.post("/mcp", json=msg, headers={**HOST, **BOB_MCP})
    assert resp.status_code == 200 and not resp.get_json()["result"]["isError"], resp.get_data(as_text=True)


TODAY = date.today().isoformat()


class TestWhoSeesIt:

    def test_an_admin_has_the_tab_and_the_page(self, client, users):
        text = page(login(client, "alice"))
        assert "<h1>Usage</h1>" in text
        row = re.search(r'<nav class="admin-tabs settings-tabs" aria-label="Settings">(.*?)</nav>', text, re.S).group(1)
        assert '<a href="/usage" aria-current="page">Usage</a>' in row
        other = client.get("/assistant", headers=HOST).get_data(as_text=True)
        assert other.count('<a href="/usage">Usage</a>') == 2    # the tab, and the help dialog's link

    @pytest.mark.parametrize("name", ["bob", "carol"])
    def test_anyone_else_gets_no_such_page_and_no_tab(self, client, users, name):
        login(client, name)
        resp = client.get("/usage", headers=HOST)
        assert resp.status_code == 404 and "Sign-ins" not in resp.get_data(as_text=True)
        assert "/usage" not in client.get("/assistant", headers=HOST).get_data(as_text=True)

    def test_signed_out_it_asks_for_the_login(self, client, users):
        resp = client.get("/usage", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")

    def test_a_visitor_of_a_public_wiki_is_sent_to_sign_in(self, client, users, monkeypatch):
        monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "view"})
        assert client.get("/page/index", headers=HOST).status_code == 200   # a visitor reads
        resp = client.get("/usage", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")
        assert "/usage" not in client.get("/page/index", headers=HOST).get_data(as_text=True)

    def test_without_users_whoever_is_signed_in_sees_it(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "one password")
        assert client.get("/usage", headers=HOST).status_code == 302
        assert "/usage" not in client.get("/login", headers=HOST).get_data(as_text=True)
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 302
        text = page(client)
        counts = sums(text)
        # One login for everyone: one person, and no table of users.
        assert counts["Sign-ins"] == [1, 1, 1] and counts["People active"] == [1, 1, 1]
        assert last_active(text) is None


class TestWhatIsCounted:

    def test_nothing_yet(self, client, users):
        # The admin's own sign-in and this view are the first.
        counts = sums(page(login(client, "alice")))
        assert counts["Sign-ins"] == [1, 1, 1] and counts["People active"] == [1, 1, 1]
        assert counts["Pages shown"] == counts["Changes to pages"] == counts["Calls by assistants"] == [0, 0, 0]

    def test_views_searches_and_changes_by_people(self, client, users, wiki_dir):
        login(client, "bob")
        for _ in range(3):
            assert client.get("/page/index", headers=HOST).status_code == 200
        assert client.get("/page/private/alice/diary", headers=HOST).status_code != 200   # not shown: not counted
        assert client.get("/api/search?q=welcome", headers=HOST).status_code == 200
        assert client.get("/search?q=welcome", headers=HOST).status_code == 200
        assert client.get("/api/search?q=", headers=HOST).status_code == 200          # nothing asked
        assert client.post("/edit/index", data={"content": "# Home\n\nChanged.\n"}, headers=HOST).status_code == 302
        assert client.post("/api/tasks", json={"title": "Buy milk"}, headers=HOST).status_code in (200, 201)
        counts = sums(page(login(client, "alice")))
        assert counts["Pages shown"] == [3, 3, 3]
        assert counts["Searches"] == [2, 2, 2]
        assert counts["Changes to pages"] == [1, 1, 1]
        assert counts["Changes to tasks"] == [1, 1, 1]
        assert counts["Changes by assistants"] == [0, 0, 0]
        assert counts["Sign-ins"] == [2, 2, 2] and counts["People active"] == [2, 2, 2]

    def test_what_an_assistant_does(self, client, users, wiki_dir):
        tool(client, "read_page", slug="index")
        tool(client, "write_page", slug="from-claude", content="# From Claude\n", summary="Add a page")
        text = page(login(client, "alice"))
        counts = sums(text)
        assert counts["Calls by assistants"] == [2, 2, 2]
        assert counts["Changes by assistants"] == [1, 1, 1] and counts["Changes to pages"] == [0, 0, 0]
        # bob never signed in: active through his assistant.
        assert counts["Sign-ins"] == [1, 1, 1] and counts["People active"] == [2, 2, 2]
        assert last_active(text) == {"alice": TODAY, "bob": TODAY, "carol": ""}

    def test_the_days_table_has_today(self, client, users):
        login(client, "bob")
        client.get("/page/index", headers=HOST)
        text = page(login(client, "alice"))
        days = re.search(r'<table class="usage-table usage-days">(.*?)</table>', text, re.S).group(1)
        row = re.search(rf'<tr><th scope="row"><time datetime="{TODAY}">{TODAY}</time></th>(.*?)</tr>', days).group(1)
        # people, sign-ins, pages shown, searches, page changes, task changes, by assistants, calls
        assert [int(n) for n in re.findall(r"<td>(\d+)</td>", row)] == [2, 2, 1, 0, 0, 0, 0, 0]

    def test_with_git_a_change_is_a_commit(self, git_wiki_dir, client, users):
        login(client, "bob")
        same = (git_wiki_dir / "index.md").read_text()
        assert client.post("/edit/index", data={"content": same}, headers=HOST).status_code == 302   # nothing changed
        assert client.post("/edit/index", data={"content": "# Home\n\nChanged.\n"}, headers=HOST).status_code == 302
        tool(client, "append_to_page", slug="index", content="A line.", summary="Add a line")
        counts = sums(page(login(client, "alice")))
        assert counts["Changes to pages"] == [1, 1, 1] and counts["Changes by assistants"] == [1, 1, 1]


class TestTheFiles:

    def files(self, wiki):
        folder = wiki / ".zone" / "usage"
        return json.loads((folder / "counts.json").read_text()), json.loads((folder / "users.json").read_text())

    def test_counts_hold_numbers_and_users_the_days(self, client, users, wiki_dir):
        login(client, "bob")                          # the first request: written at its end
        counts, _ = self.files(wiki_dir)
        assert counts["days"] == {TODAY: {"logins": 1}} and counts["last_active"] == {}
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", counts["written"])

    def test_they_are_read_at_the_next_start(self, client, users, wiki_dir, monkeypatch):
        login(client, "bob")
        monkeypatch.setattr(settings, "LOCALE", "de")   # Zonewiki starts again
        counts = sums(page(login(client, "alice")))
        assert counts["Sign-ins"] == [2, 2, 2]           # bob's from the file, alice's now

    def test_no_name_title_or_query_is_in_the_counts(self, client, users, wiki_dir):
        folder = wiki_dir / ".zone" / "usage"
        (folder).mkdir(parents=True)
        (folder / "users.json").write_text(json.dumps({"bob": "2020-01-01", "gone": TODAY, "carol": TODAY}))
        login(client, "bob")
        counts, seen = self.files(wiki_dir)
        text = json.dumps(counts)
        assert not any(word in text for word in ("bob", "carol", "alice", "gone", "index", "Home"))
        assert set(counts) == {"days", "last_active", "written"}
        # How many were last active on which day, without who; the day of a
        # user long ago, and of one who is no user, are dropped.
        assert counts["last_active"] == {TODAY: 1} and seen == {"carol": TODAY}

    def test_old_days_are_dropped(self, client, users, wiki_dir):
        folder = wiki_dir / ".zone" / "usage"
        folder.mkdir(parents=True)
        (folder / "counts.json").write_text(json.dumps({"days": {"2020-01-01": {"page_views": 7}, TODAY: {"page_views": 2}}}))
        login(client, "bob")
        counts, _ = self.files(wiki_dir)
        assert counts["days"] == {TODAY: {"page_views": 2, "logins": 1}}

    def test_a_broken_file_counts_from_nothing(self, client, users, wiki_dir):
        folder = wiki_dir / ".zone" / "usage"
        folder.mkdir(parents=True)
        (folder / "counts.json").write_text("{not json")
        assert sums(page(login(client, "alice")))["Sign-ins"] == [1, 1, 1]

    def test_they_stay_out_of_git(self, git_wiki_dir, client, users):
        login(client, "bob")
        assert (git_wiki_dir / ".zone" / "usage" / "counts.json").exists()
        out = subprocess.run(["git", "status", "--porcelain", "--ignored=no"], cwd=git_wiki_dir, env=settings.GIT_ENV,
                             capture_output=True, text=True, check=True).stdout
        assert ".zone" not in out
