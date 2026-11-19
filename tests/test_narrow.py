"""Who may move a page to other readers (docs/spec/users.md): to more
people whoever may write, asked first; to fewer an admin, or whoever shared
the page while nobody else has changed it, asked first; anyone else is
refused. Through Rename (the page's dialog and the editor's), a folder's
move and an assistant's rename_page."""

import re
import subprocess
from urllib.parse import parse_qs, urlparse

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol", "dana", "erin")}


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True, env=settings.GIT_ENV).stdout.strip()


def sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def mcp(client, who, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {USERS[who][1]}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return "".join(c["text"] for c in resp.get_json()["result"]["content"])


def rename(client, slug, new, **more):
    return client.post(f"/rename/{slug}", data={"new_name": new, **more}, headers=HOST)


def asked(resp):
    """What a rename asks before it is made ("" when it asks nothing)."""
    return parse_qs(urlparse(resp.headers.get("Location", "")).query).get("rename_ask", [""])[0]


def refused(resp):
    return parse_qs(urlparse(resp.headers.get("Location", "")).query).get("rename_error", [""])[0]


@pytest.fixture()
def wiki(client, git_wiki_dir, monkeypatch):
    """alice, bob and erin are editors, dana an admin; alice, carol and dana
    are in dev. plans and the folder notes/ are shared, by nobody in
    particular (committed before anyone signed in)."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}, "carol": {"groups": "dev"},
                                                    "dana": {"role": "admin", "groups": "dev"}})
    w = git_wiki_dir
    (w / "plans.md").write_text("# Plans\n\nThe first version.\n")
    (w / "notes").mkdir()
    (w / "notes/a.md").write_text("# A\n")
    (w / "notes/b.md").write_text("# B\n\nSee [[plans]].\n")
    git(w, "add", "-A")
    git(w, "commit", "-qm", "Shared pages")
    return w


def test_an_admin_moves_a_page_to_fewer_people_and_is_asked_first(client, wiki):
    sign_in(client, "dana")
    resp = rename(client, "plans", "groups/dev/plans")
    said = asked(resp)
    # Who loses it, by name, and who has it then.
    assert "bob, erin will no longer see “plans”" in said and "@dev (alice, carol, dana)" in said, said
    assert (wiki / "plans.md").exists() and not (wiki / "groups/dev/plans.md").exists()
    # The dialog comes back open with the question, and the form confirms that name.
    body = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert 'name="confirmed" value="groups/dev/plans"' in body and "Yes, move it" in body and "will no longer see" in body
    # A yes to another name is no yes to this one.
    assert asked(rename(client, "plans", "groups/dev/plans", confirmed="groups/dev/other"))
    assert asked(rename(client, "plans", "groups/dev/plans", confirmed="share"))        # Share…'s is for more people
    assert (wiki / "plans.md").exists()
    resp = rename(client, "plans", "groups/dev/plans", confirmed="groups/dev/plans")
    assert resp.headers["Location"] == "/page/groups/dev/plans"
    assert (wiki / "groups/dev/plans.md").exists() and not (wiki / "plans.md").exists()
    assert "[[groups/dev/plans]]" in (wiki / "notes/b.md").read_text()                   # links follow
    # Into her own folder too, from the group's.
    assert "alice, carol will no longer see" in asked(rename(client, "groups/dev/plans", "private/dana/plans"))
    assert rename(client, "groups/dev/plans", "private/dana/plans", confirmed="private/dana/plans").headers["Location"] == "/page/private/dana/plans"


def test_an_admin_moves_only_where_they_may_write(client, wiki, monkeypatch):
    """An admin who is not in a group doesn't see its folder: no page goes there by them."""
    config = wiki / ".zone" / "config"
    sign_in(client, "dana")
    client.get("/page/index", headers=HOST)
    config.write_text(config.read_text().replace("[user erin]\n", "[user erin]\ngroups = ops\n"))
    for _ in range(50):
        if "ops" in client.get("/groups", headers=HOST).get_data(as_text=True):
            break
    assert "aren't in" in refused(rename(client, "plans", "groups/ops/plans", confirmed="groups/ops/plans"))
    assert (wiki / "plans.md").exists()


@pytest.mark.parametrize("new", ["groups/dev/plans", "private/alice/plans"])
def test_an_editor_takes_no_page_from_the_others(client, wiki, new):
    sign_in(client, "alice")
    for more in ({}, {"confirmed": new}, {"from": "page", "confirmed": new}):
        said = refused(rename(client, "plans", new, **more))
        assert "is seen by fewer people" in said and "Ask an admin" in said and f"“{new}”" in said, said     # names what was typed
    out = mcp(client, "alice", "rename_page", slug="plans", new_slug=new, narrow=True)
    assert "Not renamed" in out and "Ask an admin" in out
    out = mcp(client, "alice", "rename_page", slug="notes/", new_slug=new.rsplit("/", 1)[0] + "/notes", narrow=True)
    assert "Nothing was moved" in out and "Ask an admin" in out
    assert (wiki / "plans.md").exists() and (wiki / "notes/a.md").exists() and git(wiki, "status", "--porcelain") == ""


def test_who_shared_a_page_takes_it_back_while_nobody_else_changed_it(client, wiki):
    sign_in(client, "alice")
    client.post("/api/save/private/alice/draft", data={"content": "# Draft\n\nHers.\n"}, headers=HOST)
    # To more people: Rename says who will see it and asks (Share… says it itself).
    said = asked(rename(client, "private/alice/draft", "draft"))
    assert "will be seen and changed by everyone who uses this wiki" in said and "Now only alice sees it" in said, said
    assert rename(client, "private/alice/draft", "draft", confirmed="draft").headers["Location"] == "/page/draft"
    # A minute later: back, asked first, to where it was or to a group she is in.
    assert "bob, carol, dana and 1 others will no longer see “draft”" in asked(rename(client, "draft", "private/alice/draft")) or \
        "will no longer see “draft”" in asked(rename(client, "draft", "private/alice/draft"))
    assert asked(rename(client, "draft", "groups/dev/draft"))
    assert rename(client, "draft", "private/alice/draft", confirmed="private/alice/draft").headers["Location"] == "/page/private/alice/draft"
    # Shared again and changed by bob: no longer hers alone to take.
    assert rename(client, "private/alice/draft", "draft", confirmed="share").status_code == 302
    sign_in(client, "bob")
    assert client.post("/api/save/draft", data={"content": "# Draft\n\nHers, and bob's.\n"}, headers=HOST).status_code == 200
    sign_in(client, "alice")
    assert "Ask an admin" in refused(rename(client, "draft", "private/alice/draft", confirmed="private/alice/draft"))
    assert (wiki / "draft.md").exists()
    # Nor is a page someone else shared, untouched as it is.
    sign_in(client, "bob")
    assert "Ask an admin" in refused(rename(client, "plans", "private/bob/plans", confirmed="private/bob/plans"))


def test_what_those_left_out_keep(client, wiki):
    """The question says they keep the page's versions up to now: they do
    (its history is read by its path), and nothing of what comes after."""
    sign_in(client, "dana")
    assert rename(client, "plans", "groups/dev/plans", confirmed="groups/dev/plans").status_code == 302
    client.post("/api/save/groups/dev/plans", data={"content": "# Plans\n\nOnly dev reads this now.\n"}, headers=HOST)
    sign_in(client, "bob")
    assert client.get("/page/groups/dev/plans", headers=HOST).status_code == 404
    first = git(wiki, "log", "--format=%h", "--diff-filter=A", "--", "plans.md").split()[-1]
    assert "The first version" in client.get(f"/revision/plans/{first}", headers=HOST).get_data(as_text=True)
    for path in ("/history/plans", "/page/plans", "/page/_deleted", "/api/search?q=reads", f"/revision/groups/dev/plans/{first}"):
        body = client.get(path, headers=HOST, follow_redirects=True).get_data(as_text=True)
        assert "Only dev reads this now" not in body, path
    assert "[[groups/dev/plans]]" in (wiki / "notes/b.md").read_text()
    assert 'class="wikilink-hidden"' in client.get("/page/notes/b", headers=HOST).get_data(as_text=True)   # the link is words to him


def test_an_admins_assistant_is_asked_too(client, wiki):
    out = mcp(client, "dana", "rename_page", slug="plans", new_slug="groups/dev/plans")
    assert "Not moved" in out and "bob, erin will no longer see" in out and "narrow=true" in out and (wiki / "plans.md").exists()
    out = mcp(client, "dana", "rename_page", slug="plans", new_slug="groups/dev/plans", narrow=True)
    assert "Renamed page" in out and (wiki / "groups/dev/plans.md").exists()
    # A folder: every page of it, asked once.
    out = mcp(client, "dana", "rename_page", slug="notes/", new_slug="groups/dev/notes")
    assert "Not moved" in out and "2 pages" in out and (wiki / "notes/a.md").exists()
    out = mcp(client, "dana", "rename_page", slug="notes/", new_slug="groups/dev/notes", narrow=True)
    assert (wiki / "groups/dev/notes/a.md").exists() and not (wiki / "notes").exists(), out
    # To more people an assistant moves as before: its user asked for it.
    out = mcp(client, "dana", "rename_page", slug="groups/dev/plans", new_slug="plans")
    assert "Renamed page" in out and (wiki / "plans.md").exists()


def test_a_page_moved_to_fewer_people_takes_its_files_there_not_to_everyone(client, wiki):
    """A group's page taken into a private folder (or into another group's)
    copies the files it shows to that folder's _assets/, never to the
    shared one, where every user (and a visitor) could open them."""
    (wiki / "groups/dev/_assets").mkdir(parents=True)
    (wiki / "groups/dev/_assets/photo.png").write_bytes(b"dev's photo")
    (wiki / "groups/dev/plans.md").write_text("# Plans\n\n![photo](/groups/dev/_assets/photo.png)\n")
    git(wiki, "add", "-A")
    git(wiki, "commit", "-qm", "dev's plans")
    sign_in(client, "dana")
    assert rename(client, "groups/dev/plans", "private/dana/plans", confirmed="private/dana/plans").headers["Location"] == "/page/private/dana/plans"
    assert not (wiki / "_assets/photo.png").exists()
    assert (wiki / "private/dana/_assets/photo.png").read_bytes() == b"dev's photo"
    assert "/private/dana/_assets/photo.png" in (wiki / "private/dana/plans.md").read_text()
    assert client.get("/private/dana/_assets/photo.png", headers=HOST).data == b"dev's photo"
    assert "private/dana/_assets/photo.png" in git(wiki, "show", "--name-only", "--format=", "HEAD")    # in the move's commit
    sign_in(client, "bob")
    assert client.get("/_assets/photo.png", headers=HOST).status_code == 404
    assert client.get("/private/dana/_assets/photo.png", headers=HOST).status_code == 404


def test_a_rename_within_a_place_asks_nothing(client, wiki):
    sign_in(client, "alice")
    assert rename(client, "plans", "plans-2026").headers["Location"] == "/page/plans-2026"
    assert rename(client, "notes/a", "archive/a").headers["Location"] == "/page/archive/a"
