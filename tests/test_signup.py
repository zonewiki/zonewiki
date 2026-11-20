"""Sign-up (docs/spec/users.md, Sign-up): with ZONE_SIGNUP and a mail
server in the deployment, users and base-url in the settings, anyone asks
for an account at /signup; Zone mails a link, on whose page they choose a
name and password."""

import json
import re
import socket
import socketserver
import subprocess
import threading

import pytest

from tests import settings
from tests.conftest import HOST

BASE = "http://wiki.example.org"
USERS = {"alice": ("alice-pw", "")}


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP to take a mail, without TLS (ZONE_SMTP_TLS=none)."""

    def handle(self):
        say = lambda line: self.wfile.write(line.encode() + b"\r\n")
        say("220 fake ESMTP")
        rcpt = None
        while line := self.rfile.readline():
            verb = line.decode().strip().split(" ")[0].upper()
            if verb == "RCPT":
                rcpt = re.search(r"<(.*)>", line.decode()).group(1)
                say("250 ok")
            elif verb == "DATA":
                say("354 go on")
                data = []
                while (l := self.rfile.readline()) not in (b".\r\n", b""):
                    data.append(l.decode())
                self.server.mails.append((rcpt, "".join(data)))
                say("250 ok")
            elif verb == "QUIT":
                say("221 bye")
                return
            else:
                say("250 ok")


class FakeSMTP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _SMTPHandler)
        self.mails: list[tuple[str, str]] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def link(self) -> str:
        """The path of the link in the last mail."""
        to, text = self.mails[-1]
        return re.search(re.escape(BASE) + r"(/welcome/[0-9a-f]{64})", text).group(1)


@pytest.fixture()
def smtp():
    s = FakeSMTP()
    yield s
    s.shutdown()
    s.server_close()


def mail_env(port):
    return {"ZONE_SIGNUP": "1", "ZONE_SMTP_HOST": "127.0.0.1", "ZONE_SMTP_PORT": str(port),
            "ZONE_SMTP_TLS": "none", "ZONE_MAIL_FROM": "wiki@example.org"}


@pytest.fixture()
def signup(monkeypatch, wiki_dir, smtp):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin", "email": "alice@example.org"}})
    monkeypatch.setattr(settings, "BASE_URL", BASE)
    monkeypatch.setattr(settings, "ENV", mail_env(smtp.server_address[1]))
    return smtp


def config(wiki):
    return (wiki / ".zone" / "config").read_text()


def section(wiki, name):
    m = re.search(rf"^\[user {name}\]\n(.*?)(?=^\[|\Z)", config(wiki), re.M | re.S)
    return m.group(1) if m else None


def ask(client, email):
    return client.post("/signup", data={"email": email}, headers=HOST)


def make(client, link, name, password="secret-pw", again=None):
    return client.post(link, data={"username": name, "password": password, "again": again or password}, headers=HOST)


class TestOff:

    def test_off_without_the_deployment(self, client, monkeypatch, wiki_dir):
        monkeypatch.setattr(settings, "USERS", dict(USERS))
        monkeypatch.setattr(settings, "BASE_URL", BASE)
        assert client.get("/signup", headers=HOST).status_code == 404
        assert "/signup" not in client.get("/login", headers=HOST).data.decode()

    def test_off_without_base_url(self, client, signup, monkeypatch):
        monkeypatch.setattr(settings, "BASE_URL", "")
        assert client.get("/signup", headers=HOST).status_code == 404
        assert ask(client, "ann@example.org").status_code == 404 and signup.mails == []

    def test_off_without_users(self, client, signup, monkeypatch):
        monkeypatch.setattr(settings, "USERS", {})
        monkeypatch.setattr(settings, "USER_SETTINGS", {})
        assert client.get("/signup", headers=HOST).status_code == 404

    def test_the_deployment_needs_a_mail_server(self, client, monkeypatch, wiki_dir):
        monkeypatch.setattr(settings, "ENV", {"ZONE_SIGNUP": "1"})
        with pytest.raises(RuntimeError):
            client.get("/login", headers=HOST)


class TestSignup:

    def test_the_whole_way(self, client, signup, wiki_dir):
        assert 'href="/signup"' in client.get("/login", headers=HOST).data.decode()
        body = client.get("/signup", headers=HOST).data.decode()
        assert 'name="email"' in body

        resp = ask(client, "Ann@Example.ORG")
        assert resp.status_code == 200 and "Check your mail" in resp.data.decode()
        to, text = signup.mails[-1]
        assert to == "Ann@example.org" and "Subject: Confirm your account on Zone" in text
        link = signup.link()
        assert "ann" not in config(wiki_dir)   # no account before the link is used

        body = client.get(link, headers=HOST).data.decode()
        assert "Ann@example.org" in body and 'name="username"' in body
        resp = make(client, link, "ann")
        assert resp.status_code == 303 and resp.headers["Location"] == "/"
        sec = section(wiki_dir, "ann")
        assert "role = viewer" in sec and "email = Ann@example.org" in sec and "pending" not in sec
        assert client.get("/page/index", headers=HOST).status_code == 200   # signed in

        # Once.
        client.post("/logout", headers=HOST)
        assert client.get(link, headers=HOST).status_code == 404
        # She signs in with it.
        resp = client.post("/login", data={"username": "ann", "password": "secret-pw"}, headers=HOST)
        assert resp.status_code == 302

    def test_a_signed_in_user_goes_home(self, client, signup):
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        resp = client.get("/signup", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/"

    def test_a_name_taken_or_bad_keeps_the_link(self, client, signup, wiki_dir):
        ask(client, "ann@example.org")
        link = signup.link()
        resp = make(client, link, "alice")
        assert "The user name alice is taken" in resp.data.decode()
        resp = make(client, link, "Ann Smith")
        assert "lowercase letters" in resp.data.decode()
        for reserved in ("me", "none"):     # the words for a task's assignee
            assert "be a user name" in make(client, link, reserved).data.decode()
            assert section(wiki_dir, reserved) is None
        resp = make(client, link, "ann", "short")
        assert "at least 8 characters" in resp.data.decode()
        resp = make(client, link, "ann", "secret-pw", "secret-px")
        assert "differ" in resp.data.decode()
        assert section(wiki_dir, "ann") is None
        assert make(client, link, "ann").status_code == 303

    def test_a_removed_users_name_is_taken(self, client, signup, git_wiki_dir):
        """A user removed leaves their private folder, tasks and history: a
        stranger signing up under the name would get them."""
        w = git_wiki_dir
        run = lambda *a: subprocess.run(["git", *a], cwd=w, check=True, capture_output=True, env=settings.GIT_ENV)
        (w / "private/bo").mkdir(parents=True)                  # there now, not committed
        (w / "private/bo/diary.md").write_text("# Diary\n")
        (w / "private/cy").mkdir(parents=True)                  # deleted, in the history
        (w / "private/cy/diary.md").write_text("# Diary\n")
        run("add", "private/cy")
        run("commit", "-qm", "cy's diary")
        run("rm", "-rq", "private/cy")
        run("commit", "-qm", "gone")
        (w / "notes.md").write_text("# Notes\n")                 # a commit by dee, nothing private
        run("add", "notes.md")
        run("commit", "-qm", "Notes", "--author", "dee <dee@zone>")
        ask(client, "ann@example.org")
        link = signup.link()
        for name in ("bo", "cy", "dee"):
            assert f"The user name {name} is taken" in make(client, link, name).data.decode(), name
            assert section(w, name) is None
        assert make(client, link, "de").status_code == 303      # a name only like one is free

    def test_a_removed_users_name_stays_taken_by_what_names_them(self, client, signup, git_wiki_dir, monkeypatch):
        """bob was a member of alice's list secret and carol had a task, and
        neither wrote anything: no folder, no commit. Taken out of the
        settings file by hand, their names are still on the list and the
        task, which a stranger signing up as them would get. dora, removed
        on the Users tab with nothing of hers anywhere, is noted removed."""
        users = {**USERS, "bob": ("bob-pw", ""), "carol": ("carol-pw", ""), "dora": ("dora-pw", "")}
        monkeypatch.setattr(settings, "USERS", users)
        login = lambda name: client.post("/login", data={"username": name, "password": users[name][0]}, headers=HOST)
        assert login("alice").status_code == 302
        assert client.post("/tasks/new-list", data={"name": "secret", "for": "alice, bob"}, headers=HOST).status_code == 302
        assert client.post("/api/tasks", json={"list": "secret", "title": "Layoffs zebrafinch"}, headers=HOST).status_code in (200, 201)
        assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
        resp = client.post("/api/tasks", json={"list": "team", "title": "Call the bank", "assignee": "carol"}, headers=HOST)
        assert resp.status_code in (200, 201), resp.data
        assert client.post("/admin/users/dora/remove", headers=HOST).status_code == 303
        assert json.loads((git_wiki_dir / ".zone/removed-users").read_text()) == ["dora"]
        monkeypatch.setattr(settings, "USERS", {**USERS})       # bob and carol gone by hand
        client.cookies.clear()
        ask(client, "eve@example.org")
        link = signup.link()
        for name in ("bob", "carol", "dora"):
            assert f"The user name {name} is taken" in make(client, link, name).data.decode(), name
            assert section(git_wiki_dir, name) is None
        assert make(client, link, "bobby").status_code == 303
        # An admin may still give such a name again.
        client.cookies.clear()
        assert login("alice").status_code == 302
        resp = client.post("/admin/users/invite", data={"name": "dora", "role": "editor"}, headers=HOST)
        assert resp.status_code == 200 and "/welcome/" in resp.data.decode()

    def test_an_address_with_an_account_gets_its_name(self, client, signup, wiki_dir):
        resp = ask(client, "alice@example.org")
        assert resp.status_code == 200 and "Check your mail" in resp.data.decode()   # the same answer
        to, text = signup.mails[-1]
        assert to == "alice@example.org" and "your user name is alice" in text and "/welcome/" not in text

    def test_bad_addresses(self, client, signup):
        for email in ("ann", "ann@localhost", "Ann <ann@example.org>", "ann@example.org\r\nBcc: x@y.org"):
            resp = ask(client, email)
            assert resp.status_code == 400, email
        assert signup.mails == []

    def test_only_the_domains(self, client, signup, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "signup-domains = example.org, example.net\n")
        assert "example.org, example.net only" in client.get("/signup", headers=HOST).data.decode()
        resp = ask(client, "ann@gmail.com")
        assert resp.status_code == 400 and "example.org or example.net" in resp.data.decode()
        assert ask(client, "ann@example.net").status_code == 200 and len(signup.mails) == 1

    def test_editor(self, client, signup, monkeypatch, wiki_dir):
        monkeypatch.setattr(settings, "CONFIG_FILE", "signup-role = editor\n")
        ask(client, "ann@example.org")
        make(client, signup.link(), "ann")
        assert "role" not in section(wiki_dir, "ann")   # editor, the default

    def test_capped_per_client(self, client, signup):
        for i in range(5):
            assert ask(client, f"a{i}@example.org").status_code == 200
        resp = ask(client, "a5@example.org")
        assert resp.status_code == 429 and len(signup.mails) == 5

    def test_a_new_ask_replaces_the_link(self, client, signup):
        ask(client, "ann@example.org")
        first = signup.link()
        ask(client, "ann@example.org")
        assert signup.link() != first and client.get(first, headers=HOST).status_code == 404

    def test_mail_server_down(self, client, signup, monkeypatch, wiki_dir):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        monkeypatch.setattr(settings, "ENV", mail_env(port))
        resp = ask(client, "ann@example.org")
        assert resp.status_code == 502 and "send the mail: try again later" in resp.data.decode()
        links = wiki_dir / ".zone" / "links"
        assert not links.exists() or "ann@" not in links.read_text()

    def test_link_dead_once_signup_is_off(self, client, signup, monkeypatch):
        ask(client, "ann@example.org")
        link = signup.link()
        monkeypatch.setattr(settings, "ENV", {})
        assert client.get(link, headers=HOST).status_code == 404

    def test_not_in_the_admins_open_links(self, client, signup):
        ask(client, "ann@example.org")
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        body = client.get("/admin/users", headers=HOST).data.decode()
        assert "ann@example.org" not in body and "can sign up at" in body


class TestApproval:

    def test_waits_for_an_admin(self, client, signup, monkeypatch, wiki_dir):
        monkeypatch.setattr(settings, "CONFIG_FILE", "signup-approval = yes\n")
        ask(client, "ann@example.org")
        resp = make(client, signup.link(), "ann")
        assert resp.status_code == 200 and "waits for an admin" in resp.data.decode()
        assert "pending = yes" in section(wiki_dir, "ann")
        assert client.get("/page/index", headers=HOST).status_code == 302   # not signed in

        resp = client.post("/login", data={"username": "ann", "password": "secret-pw"}, headers=HOST)
        assert resp.status_code == 200 and "waits for an admin of this wiki to approve it" in resp.data.decode()

        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        body = client.get("/admin/users", headers=HOST).data.decode()
        assert "Waiting for approval" in body and "ann@example.org" in body and ">Approve</button>" in body
        resp = client.post("/admin/users/ann/enable", headers=HOST)
        assert resp.status_code == 303 and "done=approve" in resp.headers["Location"]
        assert "Approved ann" in client.get(resp.headers["Location"], headers=HOST).data.decode()
        assert "pending" not in section(wiki_dir, "ann")

        client.post("/logout", headers=HOST)
        resp = client.post("/login", data={"username": "ann", "password": "secret-pw"}, headers=HOST)
        assert resp.status_code == 302
