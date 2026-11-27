"""A user's mail address (/email, docs/spec/users.md, Mail addresses): set
in the browser with the password again, by an admin on the Users tab, and
mailed nothing until its owner confirmed it by the link Zonewiki sends.
And the mail folder (docs/spec/deployment.md, The mail folder): with
ZONE_MAIL_SPOOL each mail is a file in .zone/outbox/, which these tests
read instead of a mail server."""

import json
import subprocess
import re
import stat
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests import settings
from tests.conftest import HOST, make_list
from tests.test_signup import FakeSMTP
from tests.test_transfer import zonewiki

BASE = "http://wiki.example.org"
USERS = {name: (f"{name}-pw", "") for name in ("alice", "bob", "carol", "dan")}
USER_SETTINGS = {"alice": {"role": "admin", "email": "alice@example.org"}, "carol": {"role": "viewer"},
                 "dan": {"shared": "yes"}}
NAME = re.compile(r"^\d{8}T\d{6}\.\d{9}Z-[0-9a-f]{16}\.json$")
LINK = re.compile(re.escape(BASE) + r"(/email/confirm/\d+\.[\w-]+)")


@pytest.fixture()
def wiki(wiki_dir, monkeypatch):
    """A wiki with users whose deployment puts mail in the mail folder."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {k: dict(v) for k, v in USER_SETTINGS.items()})
    monkeypatch.setattr(settings, "BASE_URL", BASE)
    monkeypatch.setattr(settings, "ENV", {"ZONE_MAIL_SPOOL": "1"})
    return wiki_dir


def login(client, name):
    client.cookies.clear()
    assert client.post("/login", data={"username": name, "password": f"{name}-pw"}, headers=HOST).status_code == 302
    return client


@pytest.fixture()
def bob(client, wiki):
    return login(client, "bob")


def section(wiki, name):
    """A user's keys in the settings file."""
    text = (wiki / ".zone" / "config").read_text()
    m = re.search(rf"^\[user {name}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    return dict(line.split(" = ", 1) for line in m.group(1).splitlines() if " = " in line)


def outbox(wiki, n=None, wait=3.0):
    """The mails in the mail folder, oldest first; with n, once n are there
    (a mail about a task is written in the background)."""
    folder = wiki / ".zone" / "outbox"
    end = time.time() + (wait if n else 0.4 if n == 0 else 0)
    while True:
        files = sorted(folder.iterdir()) if folder.is_dir() else []
        if n is None or (n and len(files) >= n) or time.time() >= end:
            break
        time.sleep(0.02)
    if n is not None:
        assert len(files) == n, [f.name for f in files]
    return [json.loads(f.read_text()) for f in files]


def link(mail):
    return LINK.search(mail["body"]).group(1)


def set_address(client, email, password="bob-pw", **more):
    return client.post("/email", data={"email": email, "password": password, **more}, headers=HOST)


def mail_on(client, kinds="assigned"):
    resp = client.post("/api/tasks/settings", headers=HOST, json={"mine": True, "was": "", "set": {"mail": kinds}})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def my_tasks(client):
    """The My tasks page, which is there once the wiki has a list."""
    if not getattr(client, "has_team", False):
        make_list(client, "team", headers=HOST)
        client.has_team = True
    resp = client.get("/tasks?mine=1", headers=HOST)
    assert resp.status_code == 200
    return resp.data.decode()


def give_bob(client, title):
    """alice gives bob a task; bob is signed in afterwards."""
    login(client, "alice")
    my_tasks(client)
    assert client.post("/api/tasks", json={"list": "team", "title": title, "assignee": "bob"}, headers=HOST).status_code == 201
    login(client, "bob")


class TestTheMailFolder:

    def test_a_mail_is_a_file_with_four_fields_for_its_owner_alone(self, bob, wiki):
        assert set_address(bob, "bob@example.org").status_code == 303
        folder = wiki / ".zone" / "outbox"
        (file,) = list(folder.iterdir())
        assert NAME.match(file.name), file.name
        assert stat.S_IMODE(file.stat().st_mode) == 0o600 and stat.S_IMODE(folder.stat().st_mode) == 0o700
        mail = json.loads(file.read_text())
        assert set(mail) == {"to", "subject", "body", "at"}
        assert mail["to"] == "bob@example.org" and mail["subject"] == "Confirm your mail address for Zonewiki"
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", mail["at"])
        # Nothing is left beside the folder, where a mail is written first.
        assert not [p.name for p in (wiki / ".zone").iterdir() if p.name.endswith(".tmp")]

    def test_the_folder_is_not_in_git(self, client, git_wiki_dir, wiki):
        login(client, "bob")
        set_address(client, "bob@example.org")
        import subprocess
        out = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=wiki, capture_output=True, text=True).stdout
        assert "outbox" not in out.replace("!! .zone/", "")

    def test_at_most_500_wait_and_the_next_is_dropped(self, bob, wiki):
        folder = wiki / ".zone" / "outbox"
        folder.mkdir(parents=True)
        for i in range(500):
            (folder / f"20260101T000000.{i:09d}Z-0123456789abcdef.json").write_text("{}")
        resp = set_address(bob, "bob@example.org")
        assert resp.status_code == 303 and resp.headers["Location"] == "/email?done=failed"
        assert len(list(folder.iterdir())) == 500
        assert "couldn&#39;t send the link" in bob.get("/email?done=failed", headers=HOST).data.decode()
        assert section(wiki, "bob")["email-pending"] == "bob@example.org"     # kept: Send the link again, later

    def test_a_server_and_the_folder_both_set_is_refused_at_start(self, wiki, monkeypatch, tmp_path):
        import os
        import subprocess
        from tests.goserver import binary
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        r = subprocess.run([str(binary()), "--addr", "127.0.0.1:0"], capture_output=True, text=True, timeout=30,
                           env={**env, "ZONE_DATA": str(wiki), "ZONE_MAIL_SPOOL": "1", "ZONE_SMTP_HOST": "smtp.example.org"})
        assert r.returncode == 1 and "ZONE_MAIL_SPOOL and ZONE_SMTP_HOST are both set" in r.stderr

    def test_mail_counts_as_set_up_so_the_boxes_show(self, client, wiki):
        login(client, "alice")                                        # has a confirmed address
        assert 'data-settings-mail="1"' in my_tasks(client)


class TestWhenToBeStarted:
    """.zone/wake (When to be started, docs/spec/deployment.md): the next
    morning of the wiki while a user wants the morning's mail, for whoever
    starts a Zonewiki that sleeps."""

    def wake(self, wiki):
        path = wiki / ".zone" / "wake"
        return path.read_text() if path.exists() else None

    def test_it_is_there_while_someone_wants_the_mornings_mail(self, client, wiki, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "timezone = Pacific/Kiritimati\n")
        login(client, "alice")                      # her address is confirmed
        my_tasks(client)
        assert self.wake(wiki) is None              # nobody wants it
        mail_on(client, "assigned,digest")
        line = self.wake(wiki)
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:00:00Z\n", line), line
        at = datetime.fromisoformat(line.strip().replace("Z", "+00:00"))
        there = at.astimezone(ZoneInfo("Pacific/Kiritimati"))
        assert (there.hour, there.minute) == (7, 0)                   # the wiki's 7:00, said in UTC
        assert timedelta(hours=-5) < at - datetime.now(timezone.utc) <= timedelta(hours=24)
        assert stat.S_IMODE((wiki / ".zone" / "wake").stat().st_mode) == 0o600

    def test_not_for_the_mail_about_tasks(self, client, wiki):
        login(client, "alice")
        my_tasks(client)
        mail_on(client, "assigned,mentioned,comments")
        assert self.wake(wiki) is None

    def test_an_address_not_confirmed_asks_for_no_start(self, client, wiki):
        login(client, "bob")
        my_tasks(client)
        mail_on(client, "digest")
        set_address(client, "bob@example.org")
        (confirm,) = outbox(wiki, 1)
        assert self.wake(wiki) is None
        assert client.post(link(confirm), headers=HOST).status_code == 303
        for _ in range(100):                        # the settings are taken once the request has ended
            if self.wake(wiki):
                break
            time.sleep(0.05)
        assert self.wake(wiki)


class TestYourAddress:

    def test_the_tab_is_there_where_mail_is(self, bob):
        body = bob.get("/email", headers=HOST).data.decode()
        assert "<h1>Your mail address</h1>" in body and '<a href="/email" aria-current="page">Mail</a>' in body
        assert "You have no mail address here" in body and "Remove my address" not in body
        assert '<a href="/email">Mail</a>' in bob.get("/password", headers=HOST).data.decode()

    def test_no_page_without_mail_or_without_users(self, client, wiki, monkeypatch):
        monkeypatch.setattr(settings, "ENV", {})
        login(client, "bob")
        assert client.get("/email", headers=HOST).status_code == 404
        assert set_address(client, "bob@example.org").status_code == 404
        assert '<a href="/email">Mail</a>' not in client.get("/password", headers=HOST).data.decode()
        monkeypatch.setattr(settings, "ENV", {"ZONE_MAIL_SPOOL": "1"})
        monkeypatch.setattr(settings, "USERS", {})
        monkeypatch.setattr(settings, "USER_SETTINGS", {})
        client.cookies.clear()
        assert client.get("/email", headers=HOST).status_code == 404

    def test_it_takes_the_password_again(self, bob, wiki):
        resp = set_address(bob, "bob@example.org", password="wrong")
        assert resp.status_code == 403 and "Incorrect password: nothing was changed." in resp.data.decode()
        assert "email-pending" not in section(wiki, "bob") and outbox(wiki) == []

    def test_what_is_no_address_is_refused(self, bob, wiki):
        for bad in ("", "bob", "Bob <bob@example.org>", "bob@localhost", "bob@example.org\nBcc: x@example.org"):
            resp = set_address(bob, bad)
            assert resp.status_code == 400 and "That isn&#39;t a mail address" in resp.data.decode(), bad
        assert "email-pending" not in section(wiki, "bob") and outbox(wiki) == []

    def test_an_address_waits_until_its_owner_confirmed_it(self, bob, wiki):
        resp = set_address(bob, "Bob@Example.org")
        assert resp.status_code == 303 and resp.headers["Location"] == "/email?done=sent"
        keys = section(wiki, "bob")
        assert keys["email-pending"] == "bob@example.org" and "email" not in keys
        body = bob.get("/email?done=sent", headers=HOST).data.decode()
        assert "Check your mail: Zonewiki sent a link to confirm the address." in body
        assert "<strong>bob@example.org</strong> isn't confirmed yet" in body and "Send the link again" in body
        (mail,) = outbox(wiki)
        assert "the mail address of the user bob on Zonewiki (http://wiki.example.org)" in mail["body"]
        assert "signed in as bob, and press Confirm" in mail["body"]

        # Opening the link changes nothing: a mail scanner can't confirm.
        page = bob.get(link(mail), headers=HOST)
        assert page.status_code == 200 and "Zonewiki may mail <strong>bob</strong> at <strong>bob@example.org</strong>" in page.data.decode()
        assert "email" not in section(wiki, "bob")

        resp = bob.post(link(mail), headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/email?done=confirmed"
        keys = section(wiki, "bob")
        assert keys["email"] == "bob@example.org" and "email-pending" not in keys
        body = bob.get("/email?done=confirmed", headers=HOST).data.decode()
        assert "Your mail address is confirmed." in body and "<strong>bob@example.org</strong> is confirmed" in body
        # Once: the link is used up.
        assert bob.post(link(mail), headers=HOST).status_code == 404
        assert "This link doesn't work for <strong>bob</strong>" in bob.get(link(mail), headers=HOST).data.decode()

    def test_no_mail_of_any_kind_before_and_the_mail_after(self, client, wiki):
        login(client, "bob")
        mail_on(client, "assigned,mentioned,comments,digest")
        set_address(client, "bob@example.org")
        (confirm,) = outbox(wiki, 1)
        # The boxes aren't there yet: the dialog says where the address is set.
        body = my_tasks(client)
        assert 'data-settings-mailask="1"' in body and 'data-settings-mail="1"' not in body
        give_bob(client, "Before")
        outbox(wiki, 1, wait=0.6)                                     # the link's mail alone
        assert client.post(link(confirm), headers=HOST).status_code == 303
        body = my_tasks(client)
        assert 'data-settings-mail="1"' in body and 'data-settings-mailask="1"' not in body
        give_bob(client, "After")
        # In the wiki's morning the morning's mail may be there too: it goes out once the address is his.
        for _ in range(150):
            about = [m for m in outbox(wiki) if "gave you a task" in m["subject"]]
            if about:
                break
            time.sleep(0.02)
        (mail,) = about
        assert mail["to"] == "bob@example.org" and mail["subject"] == "alice gave you a task: After"
        assert f"{BASE}/tasks?task=" in mail["body"]

    def test_signed_out_the_link_asks_to_sign_in_and_comes_back(self, bob, wiki):
        set_address(bob, "bob@example.org")
        path = link(outbox(wiki, 1)[0])
        bob.cookies.clear()
        resp = bob.get(path, headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=%2Femail%2Fconfirm%2F")
        assert bob.post(path, headers=HOST).status_code in (302, 401, 403)
        assert "email" not in section(wiki, "bob")

    def test_a_link_is_for_its_user_and_its_address_alone(self, client, wiki):
        login(client, "bob")
        set_address(client, "bob@example.org")
        first = link(outbox(wiki, 1)[0])
        # Someone else, signed in, with bob's link: nothing.
        login(client, "alice")
        resp = client.post(first, headers=HOST)
        assert resp.status_code == 404 and "This link doesn't work for <strong>alice</strong>" in resp.data.decode()
        assert section(wiki, "alice")["email"] == "alice@example.org" and "email" not in section(wiki, "bob")
        # bob changes the address: the first link is for one he no longer asks for.
        login(client, "bob")
        set_address(client, "robert@example.org")
        assert client.post(first, headers=HOST).status_code == 404
        assert "email" not in section(wiki, "bob")
        # A token made up, or cut short.
        for bad in ("/email/confirm/99999999999.AAAA", first[:-3], "/email/confirm/x"):
            assert client.post(bad, headers=HOST).status_code == 404, bad
        assert client.post(link(outbox(wiki, 2)[1]), headers=HOST).status_code == 303
        assert section(wiki, "bob")["email"] == "robert@example.org"

    def test_a_new_address_takes_the_confirmed_one_at_once(self, client, wiki):
        login(client, "alice")
        resp = set_address(client, "alice@example.org", password="alice-pw")
        assert resp.headers["Location"] == "/email?done=same" and outbox(wiki) == []
        set_address(client, "alice@example.net", password="alice-pw")
        keys = section(wiki, "alice")
        assert "email" not in keys and keys["email-pending"] == "alice@example.net"

    def test_an_address_another_user_has_is_not_confirmed(self, bob, wiki):
        set_address(bob, "alice@example.org")
        resp = bob.post(link(outbox(wiki, 1)[0]), headers=HOST)
        assert resp.status_code == 409 and "another user of this wiki has the address alice@example.org already" in resp.data.decode()
        assert "email" not in section(wiki, "bob")

    def test_removing_it(self, bob, wiki):
        set_address(bob, "bob@example.org")
        bob.post(link(outbox(wiki, 1)[0]), headers=HOST)
        assert "Remove my address" in bob.get("/email", headers=HOST).data.decode()
        resp = set_address(bob, "", password="wrong", remove="1")
        assert resp.status_code == 403 and section(wiki, "bob")["email"] == "bob@example.org"
        resp = set_address(bob, "", remove="1")
        assert resp.status_code == 303 and resp.headers["Location"] == "/email?done=removed"
        keys = section(wiki, "bob")
        assert "email" not in keys and "email-pending" not in keys

    def test_sending_the_link_again_and_three_a_day_at_most(self, bob, wiki):
        assert bob.post("/email/resend", headers=HOST).status_code == 404      # nothing waits
        set_address(bob, "bob@example.org")
        for _ in range(2):
            resp = bob.post("/email/resend", headers=HOST)
            assert resp.status_code == 303 and resp.headers["Location"] == "/email?done=sent"
        mails = outbox(wiki, 3)
        assert len({link(m) for m in mails}) >= 1 and all(m["to"] == "bob@example.org" for m in mails)
        resp = bob.post("/email/resend", headers=HOST)
        assert resp.headers["Location"] == "/email?done=unsent"
        assert "too many were sent lately" in bob.get("/email?done=unsent", headers=HOST).data.decode()
        # Another address is still bob's link: the day's three are his, whatever the address.
        assert set_address(bob, "robert@example.org").headers["Location"] == "/email?done=unsent"
        outbox(wiki, 3)
        # The count is kept in a file, owner-only: a Zonewiki that slept starts no new count.
        sent = wiki / ".zone" / "confirms"
        assert len(json.loads(sent.read_text())) == 3 and stat.S_IMODE(sent.stat().st_mode) == 0o600
        # The last link sent still confirms the address it was for... which bob replaced: nothing.
        assert bob.post(link(mails[-1]), headers=HOST).status_code == 404

    def test_ten_an_hour_for_the_wiki(self, client, wiki, monkeypatch):
        names = [f"user{i}" for i in range(11)]
        monkeypatch.setattr(settings, "USERS", {name: (f"{name}-pw", "") for name in names})
        monkeypatch.setattr(settings, "USER_SETTINGS", {})
        where = []
        for name in names:
            login(client, name)
            where.append(client.post("/email", data={"email": f"{name}@example.org", "password": f"{name}-pw"},
                                     headers=HOST).headers["Location"])
        assert where == ["/email?done=sent"] * 10 + ["/email?done=unsent"]
        outbox(wiki, 10)

    def test_a_viewer_has_an_address_too(self, client, wiki):
        login(client, "carol")
        resp = client.post("/email", data={"email": "carol@example.org", "password": "carol-pw"}, headers=HOST)
        assert resp.status_code == 303
        assert client.post(link(outbox(wiki, 1)[0]), headers=HOST).status_code == 303
        assert section(wiki, "carol")["email"] == "carol@example.org"

    def test_a_shared_accounts_address_is_the_hosts(self, client, wiki):
        login(client, "dan")
        assert "its mail address is set by whoever runs this wiki, not here" in client.get("/email", headers=HOST).data.decode()
        resp = client.post("/email", data={"email": "dan@example.org", "password": "dan-pw"}, headers=HOST)
        assert resp.status_code == 403 and "email-pending" not in section(wiki, "dan") and outbox(wiki) == []

    def test_through_a_mail_server_as_well(self, client, wiki, monkeypatch):
        smtp = FakeSMTP()
        try:
            monkeypatch.setattr(settings, "ENV", {"ZONE_SMTP_HOST": "127.0.0.1", "ZONE_SMTP_PORT": str(smtp.server_address[1]),
                                                  "ZONE_SMTP_TLS": "none", "ZONE_MAIL_FROM": "wiki@example.org"})
            login(client, "bob")
            assert set_address(client, "bob@example.org").headers["Location"] == "/email?done=sent"
            (to, text), = smtp.mails
            assert to == "bob@example.org" and "Subject: Confirm your mail address for Zonewiki" in text
            assert client.post(LINK.search(text).group(1), headers=HOST).status_code == 303
            assert not (wiki / ".zone" / "outbox").exists()
        finally:
            smtp.shutdown()
            smtp.server_close()


class TestAViewersMorningsMail:
    """A viewer has the morning's mail of the pages they watch, turned on on
    the Mail tab (Z6 of zonewiki-cp's tenant mail plan; The morning's mail in
    docs/spec/tasks.md): their own, beside their watches, no commit."""

    def test_a_viewer_turns_it_on_and_off_on_the_mail_tab(self, client, git_wiki_dir, wiki):
        login(client, "carol")                                   # a viewer
        body = client.get("/email", headers=HOST).get_data(as_text=True)
        assert "<h2>The morning's mail</h2>" in body and "Mail me each morning" in body
        assert "in <a href=\"/tasks?mine=1\">My tasks</a>" not in body   # not where an editor chooses
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_wiki_dir, capture_output=True, text=True, env=settings.GIT_ENV).stdout
        resp = client.post("/email/digest", data={"digest": "on"}, headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/email?done=digest-on"
        assert json.loads((wiki / ".zone" / "watch" / "carol.json").read_text())["digest"] is True
        body = client.get("/email?done=digest-on", headers=HOST).get_data(as_text=True)
        assert "Zonewiki mails you each morning when pages you watch changed." in body
        assert "Stop the morning's mail" in body and "once your address is confirmed" in body
        assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_wiki_dir, capture_output=True, text=True,
                              env=settings.GIT_ENV).stdout == head
        assert client.post("/email/digest", data={"digest": "off"}, headers=HOST).status_code == 303
        assert not (wiki / ".zone" / "watch" / "carol.json").exists()

    def test_with_a_confirmed_address_it_asks_to_be_started(self, client, wiki, monkeypatch):
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "carol": {"role": "viewer", "email": "carol@example.org"}})
        login(client, "carol")
        assert not (wiki / ".zone" / "wake").exists()
        client.post("/email/digest", data={"digest": "on"}, headers=HOST)
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:00:00Z\n", (wiki / ".zone" / "wake").read_text())

    def test_not_for_anyone_but_a_viewer(self, client, wiki):
        login(client, "bob")                                     # an editor: My tasks' settings
        assert client.post("/email/digest", data={"digest": "on"}, headers=HOST).status_code == 404
        assert "Mail me each morning" not in client.get("/email", headers=HOST).get_data(as_text=True)
        assert not (wiki / ".zone" / "watch" / "bob.json").exists()
        client.cookies.clear()
        assert client.post("/email/digest", data={"digest": "on"}, headers=HOST).status_code in (302, 303, 401, 404)

    def test_not_from_another_site(self, client, wiki):
        login(client, "carol")
        resp = client.post("/email/digest", data={"digest": "on"}, headers={**HOST, "Origin": "https://evil.example"})
        assert resp.status_code == 403 and not (wiki / ".zone" / "watch" / "carol.json").exists()


class TestAnAdminSetsIt:

    def test_someones_address_waits_for_them(self, client, wiki):
        login(client, "alice")
        body = client.get("/admin/users", headers=HOST).data.decode()
        assert 'action="/admin/users/bob/email"' in body and 'value="alice@example.org" aria-label="Mail address of alice"' in body
        resp = client.post("/admin/users/bob/email", data={"email": "Bob@example.org"}, headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/admin/users?done=email-sent&user=bob"
        keys = section(wiki, "bob")
        assert keys["email-pending"] == "bob@example.org" and "email" not in keys
        body = client.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "Sent bob a link to confirm the address: no mail goes to it until they did." in body
        assert 'value="bob@example.org" aria-label="Mail address of bob"' in body and "not confirmed yet" in body
        (mail,) = outbox(wiki, 1)
        # The admin can't confirm it for them.
        assert client.post(link(mail), headers=HOST).status_code == 404
        login(client, "bob")
        assert "<strong>bob@example.org</strong> isn't confirmed yet" in client.get("/email", headers=HOST).data.decode()
        assert client.post(link(mail), headers=HOST).status_code == 303
        assert section(wiki, "bob")["email"] == "bob@example.org"

    def test_their_own_too_and_the_same_again_changes_nothing(self, client, wiki):
        login(client, "alice")
        client.post("/admin/users/alice/email", data={"email": "alice@example.org"}, headers=HOST)
        assert section(wiki, "alice")["email"] == "alice@example.org" and outbox(wiki) == []
        client.post("/admin/users/alice/email", data={"email": "alice@example.net"}, headers=HOST)
        keys = section(wiki, "alice")
        assert keys["email-pending"] == "alice@example.net" and "email" not in keys

    def test_taking_it_out_and_what_is_no_address(self, client, wiki):
        login(client, "alice")
        resp = client.post("/admin/users/bob/email", data={"email": "not an address"}, headers=HOST)
        assert resp.status_code == 400 and "isn&#39;t a mail address" in resp.data.decode()
        client.post("/admin/users/bob/email", data={"email": "bob@example.org"}, headers=HOST)
        resp = client.post("/admin/users/bob/email", data={"email": ""}, headers=HOST)
        assert resp.headers["Location"] == "/admin/users?done=email-removed&user=bob"
        assert "email-pending" not in section(wiki, "bob")
        assert client.post("/admin/users/nobody/email", data={"email": "x@example.org"}, headers=HOST).status_code == 404

    def test_only_an_admin(self, bob, wiki):
        assert bob.post("/admin/users/alice/email", data={"email": "bob@example.org"}, headers=HOST).status_code == 404
        assert section(wiki, "alice")["email"] == "alice@example.org"


class TestOnTheHost:

    def test_the_command_confirms(self, bob, wiki):
        set_address(bob, "bob@example.org")
        r = zonewiki("user", "email", "bob", data=wiki)
        assert "bob@example.org (not confirmed by bob yet" in r.stdout.decode()
        assert "bob@example.org not confirmed" in zonewiki("user", "list", data=wiki).stdout.decode()
        assert zonewiki("user", "email", "bob", "bob@example.org", data=wiki).returncode == 0
        keys = section(wiki, "bob")
        assert keys["email"] == "bob@example.org" and "email-pending" not in keys
        assert zonewiki("user", "email", "bob", "--remove", data=wiki).returncode == 0
        assert "email" not in section(wiki, "bob")

    def test_remove_takes_an_address_that_waits(self, bob, wiki):
        set_address(bob, "bob@example.org")
        assert zonewiki("user", "email", "bob", "--remove", data=wiki).returncode == 0
        assert "email-pending" not in section(wiki, "bob")

    def test_a_pending_address_that_is_none_is_refused_by_the_file(self, wiki, client):
        login(client, "bob")
        config = wiki / ".zone" / "config"
        config.write_text(config.read_text().replace("[user bob]\n", "[user bob]\nemail-pending = nonsense\n"))
        r = zonewiki("config", "check", data=wiki)
        assert r.returncode == 1 and "bob's email-pending nonsense isn't a mail address" in r.stderr
