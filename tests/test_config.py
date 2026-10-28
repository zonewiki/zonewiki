"""The settings file, .leaf/config (docs/spec/users.md): Leaf reloads it
when it changes, so users, passwords and tokens change without a restart;
a broken file keeps the settings from before and says so on /api/health;
Leaf commits the file (its tokens only as hashes), but no route or history
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
    """alice from the settings file, Leaf running; returns the file."""
    monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))
    client.live.ensure()
    return wiki_dir / ".leaf" / "config"


def write(path, text):
    """Rewrites the file as an editor would, and waits until Leaf looks at
    it again (at most once a second, at a request): its next request takes
    the change. (Not polled with logins: wrong ones are throttled.)"""
    path.write_text(text, encoding="utf-8")
    time.sleep(1.1)


def user(name, password, token=""):
    return (f"[user {name}]\npassword = {password_hash(password)}\n"
            + (f"mcp-token = {token_hash(token)}\n" if token else ""))


def until(check, timeout=5.0):
    """check() turns true within timeout (Leaf looks at the file at most
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
    a running Leaf doesn't take that from a reload."""
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
    assert "dave" not in error and "see Leaf's log" in error
    login(client, "alice", "alice-pw")
    assert "dave" in client.get("/api/health", headers=HOST).get_json()["error"]


def test_settings_reload_too(client, config):
    write(config, "locale = de-AT\nhosts = wiki.example.org\n" + config.read_text())
    login(client, "alice", "alice-pw")
    assert until(lambda: 'data-locale="de-AT"' in client.get("/page/index", headers=HOST).data.decode())


def test_a_password_without_users(client, wiki_dir, monkeypatch):
    """The file's password is a hash, from leaf passwd."""
    monkeypatch.setattr(settings, "CONFIG_FILE", f"password = {password_hash('pw')}\n")
    assert client.get("/page/index", headers=HOST).status_code == 302
    assert client.post("/login", data={"password": "nope"}, headers=HOST).status_code == 200
    assert client.post("/login", data={"password": "pw"}, headers=HOST).status_code == 302
    assert signed_in(client)


def test_a_setting_left_in_the_environment_stops_leaf(client, wiki_dir, monkeypatch):
    """Leaf reads the wiki's settings only from the file; one still in .env
    would be ignored (a forgotten LEAF_PASSWORD: an open wiki)."""
    monkeypatch.setattr(settings, "ENV", {"LEAF_PASSWORD": "pw"})
    with pytest.raises(RuntimeError, match="didn't start"):
        client.get("/page/index", headers=HOST)
    log = client.live.log.read_text()
    assert "LEAF_PASSWORD is set in the environment (.env), but Leaf reads the wiki's settings only" in log
    assert "./run.sh config import" in log


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


def test_the_file_is_committed_but_never_shown(client, git_wiki_dir, monkeypatch):
    """Leaf commits the file at start and at each reload (saying what
    changed), and only it of .leaf/; no page route serves it, and Leaf's
    history leaves it out."""
    monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))
    login(client, "alice", "alice-pw")
    config = git_wiki_dir / ".leaf" / "config"
    assert git(git_wiki_dir, "log", "-1", "--format=%an %s", "--", ".leaf/config").strip() == "Leaf Change settings"
    files = git(git_wiki_dir, "ls-files")
    assert ".leaf/config" in files and "secret_key" not in files
    assert "alice-token" not in git(git_wiki_dir, "show", "HEAD:.leaf/config")   # only its hash
    write(config, config.read_text() + user("carol", "carol-pw", "carol-token"))
    client.get("/page/index", headers=HOST)
    assert git(git_wiki_dir, "log", "-1", "--format=%s").strip() == "Change settings: add user carol"
    assert git(git_wiki_dir, "status", "--porcelain").strip() == ""
    for path in ("/page/.leaf/config", "/api/page-preview/.leaf/config", "/edit/.leaf/config",
                 "/_assets/../.leaf/config"):
        resp = client.get(path, headers=HOST)
        assert resp.status_code in (400, 404) and b"pbkdf2" not in resp.data, path
    for path in ("/uncommitted", "/api/uncommitted"):
        body = client.get(path, headers=HOST).data.decode()
        assert ".leaf" not in body and "Change settings" not in body, path
    text = client.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "history", "arguments": {}}},
    ).get_json()["result"]["content"][0]["text"]
    assert ".leaf" not in text and "Change settings" not in text


def test_an_older_wikis_exclude_lets_the_file_in(client, git_wiki_dir, monkeypatch):
    """A wiki an older Leaf set up excluded all of .leaf/."""
    exclude = git_wiki_dir / ".git" / "info" / "exclude"
    assert "/.leaf/*\n!/.leaf/config\n" in exclude.read_text()
    exclude.write_text("/.leaf/\n")
    monkeypatch.setattr(settings, "CONFIG_FILE", "locale = de-AT\n")
    client.get("/page/index", headers=HOST)
    assert exclude.read_text() == "/.leaf/*\n!/.leaf/config\n"
    assert ".leaf/config" in git(git_wiki_dir, "ls-files")


def test_a_new_wikis_first_commit_has_no_secret_key(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "GIT_ENABLED", True)
    monkeypatch.setattr(settings, "CONFIG_FILE", "locale = de-AT\n")
    client.get("/page/index", headers=HOST)
    files = git(wiki_dir, "ls-files")
    assert "index.md" in files and ".leaf/config" in files and "secret_key" not in files
