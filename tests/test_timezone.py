"""The wiki's timezone (timezone in the settings file; The wiki's timezone in
docs/spec/users.md): the day, the times Zonewiki shows and the commits it
writes are that zone's, and an admin sets it in the browser (Admin, Wiki)."""

import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests import settings
from tests.conftest import HOST, create_task, write_page
from tests.test_admin import config, login, post, users, admin  # noqa: F401 (fixtures)

# Two zones a day apart at every moment (UTC+14 and UTC-11): a date that is
# today in one never is in the other.
EAST, WEST = "Pacific/Kiritimati", "Pacific/Niue"


def day_in(zone, plus=0):
    return (datetime.now(ZoneInfo(zone)) + timedelta(days=plus)).strftime("%Y-%m-%d")


def due_counts(client):
    return client.get("/api/tasks/due-count", headers=HOST).get_json()


class TestTheDay:

    @pytest.mark.parametrize("zone,today,overdue", [(EAST, 1, 0), (WEST, 0, 0)])
    def test_what_is_due_today_is_the_wikis_day(self, monkeypatch, client, wiki_dir, zone, today, overdue):
        monkeypatch.setattr(settings, "CONFIG_FILE", f"timezone = {zone}\n")
        assert create_task(client, title="Due", due_date=day_in(EAST)).status_code in (200, 201)
        counts = due_counts(client)
        assert (counts["today"], counts["overdue"]) == (today, overdue)

    def test_yesterday_there_is_overdue(self, monkeypatch, client, wiki_dir):
        monkeypatch.setattr(settings, "CONFIG_FILE", f"timezone = {EAST}\n")
        create_task(client, title="Late", due_date=day_in(WEST))   # the day before EAST's
        counts = due_counts(client)
        assert (counts["today"], counts["overdue"]) == (0, 1)

    def test_a_name_that_is_no_timezone_is_refused(self, monkeypatch, client, wiki_dir):
        monkeypatch.setattr(settings, "CONFIG_FILE", "timezone = Mars/Olympus\n")
        with pytest.raises(RuntimeError, match="didn't start"):
            client.get("/page/index", headers=HOST)
        assert "Mars/Olympus isn't a timezone" in client.live.log.read_text()


class TestGit:

    def test_commits_and_history_are_in_the_wikis_time(self, monkeypatch, client, git_wiki_dir):
        monkeypatch.setattr(settings, "CONFIG_FILE", f"timezone = {EAST}\n")
        resp = client.post("/edit/clock", data={"content": "# Clock\n\nTick.\n"}, headers=HOST)
        assert resp.status_code in (200, 302, 303), resp.get_data(as_text=True)
        git = lambda *a: subprocess.run(["git", *a], cwd=git_wiki_dir, capture_output=True, text=True,  # noqa: E731
                                        env=settings.GIT_ENV).stdout.strip()
        assert git("log", "-1", "--format=%ad", "--date=format:%z", "--", "clock.md") == "+1400"
        at = datetime.fromtimestamp(int(git("log", "-1", "--format=%at", "--", "clock.md")), ZoneInfo(EAST))
        page = client.get("/history/clock", headers=HOST).get_data(as_text=True)
        assert at.strftime("%Y-%m-%d %H:%M") in page

    def test_an_older_commit_reads_in_the_wikis_time_too(self, monkeypatch, client, git_wiki_dir):
        """A commit made elsewhere, or before the setting, carries another
        offset: history shows it in the wiki's time all the same."""
        write_page(git_wiki_dir, "old", "# Old\n")
        env = {**settings.GIT_ENV, "GIT_AUTHOR_DATE": "2026-03-01T12:00:00+0000", "GIT_COMMITTER_DATE": "2026-03-01T12:00:00+0000"}
        subprocess.run(["git", "add", "old.md"], cwd=git_wiki_dir, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "Old"], cwd=git_wiki_dir, check=True, env=env)
        monkeypatch.setattr(settings, "CONFIG_FILE", f"timezone = {EAST}\n")
        page = client.get("/history/old", headers=HOST).get_data(as_text=True)
        assert "2026-03-02 02:00" in page and "2026-03-01 12:00" not in page


class TestAdminSetsIt:

    def test_the_wiki_tab_sets_and_unsets_it(self, admin, git_wiki_dir):
        body = admin.get("/admin/wiki", headers=HOST).get_data(as_text=True)
        assert '<a href="/admin/wiki" aria-current="page">Wiki</a>' in body and "no timezone is set" in body
        assert '<a href="/admin/wiki">Wiki</a>' in admin.get("/admin/users", headers=HOST).get_data(as_text=True)
        resp = post(admin, "/admin/wiki/timezone", timezone=f" {EAST} ")
        assert resp.status_code == 303 and resp.headers["Location"] == "/admin/wiki?done=timezone"
        assert f"timezone = {EAST}\n" in config(git_wiki_dir)
        body = admin.get("/admin/wiki?done=timezone", headers=HOST).get_data(as_text=True)
        assert f"The wiki&#39;s timezone is {EAST} now." in body and f'value="{EAST}"' in body
        assert datetime.now(ZoneInfo(EAST)).strftime("%A %-d %B %Y") in body
        # It holds at once: what is due today is that zone's.
        create_task(admin, title="Due", due_date=day_in(EAST))
        assert due_counts(admin)["today"] == 1
        assert post(admin, "/admin/wiki/timezone", timezone="").status_code == 303
        assert "timezone" not in config(git_wiki_dir)
        assert "keeps the server&#39;s" in admin.get("/admin/wiki?done=timezone", headers=HOST).get_data(as_text=True)

    @pytest.mark.parametrize("name", ["Mars/Olympus", "Local", "../etc/passwd", "Europe/Vienna\nrole = admin", "x" * 80])
    def test_what_is_no_timezone_is_refused(self, admin, git_wiki_dir, name):
        before = config(git_wiki_dir)
        resp = post(admin, "/admin/wiki/timezone", timezone=name)
        assert resp.status_code == 400 and "isn&#39;t a timezone Zonewiki knows" in resp.get_data(as_text=True)
        assert config(git_wiki_dir) == before

    def test_anyone_else_gets_a_404_and_nothing_changes(self, client, users, git_wiki_dir):
        login(client, "bob")
        before = config(git_wiki_dir)
        assert client.get("/admin/wiki", headers=HOST).status_code == 404
        assert post(client, "/admin/wiki/timezone", timezone=EAST).status_code == 404
        assert config(git_wiki_dir) == before

    def test_not_from_another_site(self, admin, git_wiki_dir):
        resp = admin.post("/admin/wiki/timezone", data={"timezone": EAST}, headers={**HOST, "Origin": "https://evil.example"})
        assert resp.status_code == 403 and "timezone" not in config(git_wiki_dir)
