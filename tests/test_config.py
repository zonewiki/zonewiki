"""The settings file, .zone/config (docs/spec/users.md): Zonewiki reloads it
when it changes, so users, passwords and tokens change without a restart;
a broken file keeps the settings from before and says so on /api/health;
Zonewiki commits the file (its tokens only as hashes), but no route or history
view shows it."""

import subprocess
import time

import pytest

from tests import settings
from tests.conftest import HOST
from tests.goserver import password_hash, token_hash

USERS = {"alice": ("alice-pw", "alice-token")}


@pytest.fixture()
def config(wiki_dir, client, monkeypatch):
    """alice from the settings file, Zonewiki running; returns the file."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    client.live.ensure()
    return wiki_dir / ".zone" / "config"


def write(path, text):
    """Rewrites the file as an editor would, and waits until Zonewiki looks at
    it again (at most once a second, at a request): its next request takes
    the change. (Not polled with logins: wrong ones are throttled.)"""
    path.write_text(text, encoding="utf-8")
    time.sleep(1.1)


def user(name, password, token=""):
    return (f"[user {name}]\npassword = {password_hash(password)}\n"
            + (f"mcp-token = {token_hash(token)}\n" if token else ""))


def until(check, timeout=5.0):
    """check() turns true within timeout (Zonewiki looks at the file at most
    once a second)."""
    end = time.monotonic() + timeout
    while True:
        if check():
            return True
        if time.monotonic() > end:
            return False
        time.sleep(0.1)


def login(client, name, password):
    return client.post("/login", data={"username": name, "password": password}, headers=HOST)


def signed_in(client):
    return client.get("/page/index", headers=HOST).status_code == 200


def mcp(client, token):
    return client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_pages", "arguments": {}}})


def test_a_new_user_signs_in_without_a_restart(client, config):
    server = client.live.server
    assert login(client, "carol", "carol-pw").status_code == 200
    write(config, config.read_text() + user("carol", "carol-pw"))
    assert login(client, "carol", "carol-pw").status_code == 302
    assert signed_in(client) and client.live.server is server


def test_a_removed_user_is_signed_out(client, config):
    login(client, "alice", "alice-pw")
    assert signed_in(client)
    write(config, user("bob", "bob-pw"))
    assert until(lambda: not signed_in(client))


def test_removing_every_user_keeps_the_login_until_a_restart(client, config):
    """No users and no password would open the wiki, and private/ with it:
    a running Zonewiki doesn't take that from a reload."""
    login(client, "alice", "alice-pw")
    write(config, "locale = de-AT\n")
    assert until(lambda: client.get("/api/health", headers=HOST).status_code == 503)
    assert "without a login" in client.get("/api/health", headers=HOST).get_json()["error"]
    client.post("/logout", headers=HOST)
    assert client.get("/page/index", headers=HOST).status_code == 302
    assert login(client, "alice", "alice-pw").status_code == 302


def test_a_changed_password(client, config):
    """The new password signs in, the old one not, and a session of the
    old one ends (a stolen cookie with it)."""
    login(client, "alice", "alice-pw")
    assert signed_in(client)
    write(config, user("alice", "new-pw", "alice-token"))
    assert until(lambda: not signed_in(client))
    assert login(client, "alice", "new-pw").status_code == 302
    client.post("/logout", headers=HOST)
    assert login(client, "alice", "alice-pw").status_code == 200


def test_a_changed_mcp_token_takes_effect_at_once(client, config):
    assert mcp(client, "alice-token").status_code == 200
    write(config, user("alice", "alice-pw", "new-token"))
    assert until(lambda: mcp(client, "new-token").status_code == 200)
    assert mcp(client, "alice-token").status_code == 401


def test_a_broken_file_keeps_the_settings_and_fails_health(client, config):
    assert client.get("/api/health", headers=HOST).get_json() == {"ok": True}
    login(client, "alice", "alice-pw")
    good = config.read_text()
    write(config, good + "pasword = x\n")
    assert until(lambda: client.get("/api/health", headers=HOST).status_code == 503)
    error = client.get("/api/health", headers=HOST).get_json()["error"]
    assert "line" in error and "keeps its settings from before" in error
    assert signed_in(client)                                      # alice stays in
    client.post("/logout", headers=HOST)
    assert client.get("/page/index", headers=HOST).status_code == 302   # and no one else gets in
    assert login(client, "alice", "alice-pw").status_code == 302
    write(config, good + "\n")
    assert until(lambda: client.get("/api/health", headers=HOST).status_code == 200)


def test_health_needs_no_login(client, config):
    assert client.get("/api/health", headers=HOST).status_code == 200


def test_health_tells_only_who_may_read_the_wiki_what_is_wrong(client, config):
    """The error may name users: without a session, only that there is one."""
    write(config, config.read_text() + "[user dave]\n")
    assert until(lambda: client.get("/api/health", headers=HOST).status_code == 503)
    error = client.get("/api/health", headers=HOST).get_json()["error"]
    assert "dave" not in error and "see Zonewiki's log" in error
    login(client, "alice", "alice-pw")
    assert "dave" in client.get("/api/health", headers=HOST).get_json()["error"]


def test_settings_reload_too(client, config):
    write(config, "locale = de-AT\nhosts = wiki.example.org\n" + config.read_text())
    login(client, "alice", "alice-pw")
    assert until(lambda: 'data-locale="de-AT"' in client.get("/page/index", headers=HOST).data.decode())


def test_a_password_without_users(client, wiki_dir, monkeypatch):
    """The file's password is a hash, from zonewiki config passwd."""
    monkeypatch.setattr(settings, "CONFIG_FILE", f"password = {password_hash('pw')}\n")
    assert client.get("/page/index", headers=HOST).status_code == 302
    assert client.post("/login", data={"password": "nope"}, headers=HOST).status_code == 200
    assert client.post("/login", data={"password": "pw"}, headers=HOST).status_code == 302
    assert signed_in(client)


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


def test_the_file_is_kept_out_of_git_and_never_shown(client, git_wiki_dir, monkeypatch):
    """Zonewiki keeps all of .zone/ out of git, the settings file too (account data
    stays out of git); a change goes to the log, names and keys only; no page
    route serves it, and Zonewiki's history leaves it out."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    login(client, "alice", "alice-pw")
    config = git_wiki_dir / ".zone" / "config"
    files = git(git_wiki_dir, "ls-files")
    assert ".zone/" not in files and git(git_wiki_dir, "log", "--all", "--format=%H", "--", ".zone/config") == ""
    write(config, config.read_text() + user("carol", "carol-pw", "carol-token"))
    client.get("/page/index", headers=HOST)
    assert until(lambda: "Change settings: add user carol" in client.live.log.read_text())
    assert "carol-pw" not in client.live.log.read_text() and "pbkdf2" not in client.live.log.read_text()
    assert git(git_wiki_dir, "status", "--porcelain").strip() == ""
    for path in ("/page/.zone/config", "/api/page-preview/.zone/config", "/edit/.zone/config",
                 "/_assets/../.zone/config", "/page/.leaf/config", "/_assets/../.leaf/config"):
        resp = client.get(path, headers=HOST)
        assert resp.status_code in (400, 404) and b"pbkdf2" not in resp.data, path
    for path in ("/uncommitted", "/api/uncommitted"):
        body = client.get(path, headers=HOST).data.decode()
        assert ".zone" not in body, path


def test_a_clone_without_its_users_doesnt_start_open(client, git_wiki_dir, monkeypatch):
    """A wiki whose history has users' commits but no settings file (a clone
    of its git remote) won't start without a login; an empty file says so on
    purpose."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    login(client, "alice", "alice-pw")
    client.post("/api/notes", data={"title": "Alices"}, headers=HOST)   # a commit by alice
    monkeypatch.setattr(settings, "USERS", {})
    monkeypatch.setattr(settings, "NO_LOGIN", False)   # no settings file at all
    with pytest.raises(RuntimeError, match="didn't start"):
        client.get("/page/index", headers=HOST)
    log = client.live.log.read_text()
    assert "this wiki had users (alice made changes" in log and "create an empty .zone/config" in log
    client.live.stop()
    (git_wiki_dir / ".zone" / "config").write_text("")
    monkeypatch.setattr(settings, "CONFIG_FILE", "# no login, on purpose\n")
    # It starts; reached from other devices (as the tests do) it asks for a login first.
    assert client.get("/page/index", headers=HOST).headers["Location"] == "/setup"
    monkeypatch.setattr(settings, "CONFIG_FILE", "no-login = yes\n")
    assert client.get("/page/index", headers=HOST).status_code == 200


def test_a_new_wikis_first_commit_has_no_secret_key(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "GIT_ENABLED", True)
    monkeypatch.setattr(settings, "CONFIG_FILE", "locale = de-AT\n")
    client.get("/page/index", headers=HOST)
    files = git(wiki_dir, "ls-files")
    assert "index.md" in files and ".zone/" not in files
