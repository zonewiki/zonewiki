"""Watching pages (docs/spec/navigation.md, Watching pages): a user watches
a page or a folder and is told when someone else changes it: a count with
the tasks' counts, the changes in Changes (Only what I watch), a row marked
new until they look. What they watch is theirs, in .zone/watch/<user>.json:
no commit, nothing in the page. What a user may not see of it is probed
here too, and for each new route in test_privacy.py and test_roles.py."""

import json
import re

import pytest

from tests import settings
from tests.conftest import HOST
from tests.test_git import commit_messages, save
from tests.test_task_notify import alice, login, smtp  # noqa: F401  (fixtures)


@pytest.fixture()
def wiki_dir(git_wiki_dir):
    return git_wiki_dir


def watch(client, on=True, back="", **what):
    return client.post("/watch", data={**what, "on": "1" if on else "0", "back": back}, headers=HOST)


def count(client):
    return client.get("/api/tasks/due-count", headers=HOST).get_json().get("watched", 0)


def bell(client):
    """The number on the top bar's bell: what is new for who is signed in."""
    return client.get("/api/tasks/due-count", headers=HOST).get_json().get("updates", 0)


def updates_rows(client):
    """Updates' Pages you watch: (the page's title, who changed it, where it leads)."""
    html = client.get("/updates", headers=HOST).get_data(as_text=True)
    return [(title, by, href) for href, title, by in re.findall(
        r'<li class="updates-row updates-watch"><a class="updates-row-page" href="([^"]*)">([^<]*)</a> '
        r'<span class="updates-row-what"><strong>([^<]*)</strong> changed it</span>', html)]


def watched_rows(client, **query):
    """The rows of the watched changes: (what, new, [page])."""
    resp = client.get("/changes", query_string={"watched": "1", **query}, headers=HOST)
    assert resp.status_code == 200
    rows = []
    for cls, row in re.findall(r'<li class="hist-row changes-row( changes-row-new)?">(.*?)</li>', resp.get_data(as_text=True), re.S):
        what = re.search(r'<span class="changes-what">(?:<span class="changes-new"[^>]*>new</span> )?([^<]*)', row).group(1)
        pages = re.findall(r'<a href="/diff/[^"]*">([^<]*)</a>', row)
        rows.append((what.strip(), bool(cls), pages))
    return rows


def kept(wiki_dir, name):
    path = wiki_dir / ".zone/watch" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def watching(wiki_dir, name):
    """What name watches, None for nothing: their file stays while they have a mail turned on (test_email.py)."""
    return (kept(wiki_dir, name) or {}).get("watch") or None


def as_user(client, name, slug, text):
    """name saves the page; alice is signed in afterwards."""
    login(client, name)
    assert save(client, slug, text).status_code in (200, 302)
    login(client, "alice")


def tool(client, name, token="alice-token", **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    result = resp.get_json()["result"]
    return result["content"][0]["text"], result["isError"]


def test_watch_and_unwatch_are_the_users_and_no_commit(alice, wiki_dir):
    save(alice, "roadmap", "# Roadmap\n\nQ1.\n")
    before = commit_messages(wiki_dir)
    page = alice.get("/page/roadmap", headers=HOST).get_data(as_text=True)
    assert 'action="/watch"' in page and ">Watch</button>" in page
    resp = watch(alice, page="roadmap")
    assert resp.status_code == 303 and resp.headers["Location"] == "/page/roadmap"
    assert list(kept(wiki_dir, "alice")["watch"]) == ["roadmap"]
    assert ">Unwatch</button>" in alice.get("/page/roadmap", headers=HOST).get_data(as_text=True)
    assert commit_messages(wiki_dir) == before                   # nothing in the wiki changed
    assert "roadmap" not in (wiki_dir / "roadmap.md").read_text().replace("# Roadmap", "")
    assert watching(wiki_dir, "bob") is None                         # hers alone
    # Listed with the watched changes, where it is unwatched too.
    listed = alice.get("/changes?watched=1", headers=HOST).get_data(as_text=True)
    assert "You watch 1 thing" in listed and '<a href="/page/roadmap">roadmap</a>' in listed
    assert watch(alice, on=False, page="roadmap", back="/changes?watched=1").headers["Location"] == "/changes?watched=1"
    assert watching(wiki_dir, "alice") is None                       # gone with the last watch
    assert ">Watch</button>" in alice.get("/page/roadmap", headers=HOST).get_data(as_text=True)
    # Back only to Zonewiki.
    assert watch(alice, page="roadmap", back="https://evil.example/").headers["Location"] == "/page/roadmap"


def test_someone_elses_change_is_news_until_you_look(alice, wiki_dir):
    save(alice, "roadmap", "# Roadmap\n\nQ1.\n")
    save(alice, "other", "# Other\n")
    watch(alice, page="roadmap")
    assert count(alice) == 0                                     # what was there before is no news
    save(alice, "roadmap", "# Roadmap\n\nQ1, Q2.\n")
    assert count(alice) == 0                                     # her own change is none either
    as_user(alice, "bob", "other", "# Other\n\nMore.\n")
    assert count(alice) == 0                                     # a page she doesn't watch
    as_user(alice, "bob", "roadmap", "# Roadmap\n\nQ1, Q2, Q3.\n")
    assert count(alice) == 1
    rows = watched_rows(alice)
    assert rows[0] == ("Update roadmap", True, ["roadmap"])      # bob's, marked new
    assert all(pages == ["roadmap"] for _, _, pages in rows)     # only the page she watches
    assert [new for _, new, _ in rows[1:]] == [False] * (len(rows) - 1)
    # The top bar's bell says so on every page, and Updates lists it: who, and what the change did.
    home = alice.get("/page/index", headers=HOST).get_data(as_text=True)
    assert '<span class="top-updates-count menu-count">1</span>' in home
    assert 'class="menu-watched">Watched pages</a>' in home and "top-user-new" not in home     # no count of its own
    assert updates_rows(alice) == [("Roadmap", "bob", "/page/roadmap")]
    assert bell(alice) == 1
    # bob has no news of his own change, and watches nothing.
    login(alice, "bob")
    assert count(alice) == 0 and watched_rows(alice) == []
    login(alice, "alice")
    # She opens the page: read.
    assert alice.get("/page/roadmap", headers=HOST).status_code == 200
    assert count(alice) == 0
    assert watched_rows(alice)[0] == ("Update roadmap", False, ["roadmap"])
    home = alice.get("/page/index", headers=HOST).get_data(as_text=True)
    assert '<span class="top-updates-count menu-count" hidden></span>' in home
    assert updates_rows(alice) == [] and bell(alice) == 0
    assert "Nothing you watch changed since you last looked." in alice.get("/updates", headers=HOST).get_data(as_text=True)
    # Changed again, twice: one page with news; her own change after it takes the news away.
    as_user(alice, "bob", "roadmap", "# Roadmap\n\nQ1 to Q4.\n")
    as_user(alice, "bob", "roadmap", "# Roadmap\n\nQ1 to Q5.\n")
    assert count(alice) == 1
    assert [new for _, new, _ in watched_rows(alice)[:3]] == [True, True, False]
    save(alice, "roadmap", "# Roadmap\n\nAll of it.\n")
    assert count(alice) == 0
    assert not any(new for _, new, _ in watched_rows(alice))


def test_the_diff_of_the_last_change_and_mark_all_read(alice, wiki_dir):
    for slug in ("a", "b"):
        save(alice, slug, f"# {slug}\n")
        watch(alice, page=slug)
        as_user(alice, "bob", slug, f"# {slug}\n\nOne.\n")
        as_user(alice, "bob", slug, f"# {slug}\n\nTwo.\n")
    assert count(alice) == 2
    listed = alice.get("/changes?watched=1", headers=HOST).get_data(as_text=True)
    assert 'id="watch-read-btn"' in listed
    diffs = re.findall(r'<a href="(/diff/a/[0-9a-f]+)">a</a>', listed)
    # What an older change did is no look at the page as it is; the last one's is.
    assert alice.get(diffs[1], headers=HOST).status_code == 200
    assert count(alice) == 2
    assert alice.get(diffs[0], headers=HOST).status_code == 200
    assert count(alice) == 1
    resp = alice.post("/watch/read", data={"back": "/changes?watched=1"}, headers=HOST)
    assert resp.status_code == 303 and resp.headers["Location"] == "/changes?watched=1"
    assert count(alice) == 0
    listed = alice.get("/changes?watched=1", headers=HOST).get_data(as_text=True)
    assert 'id="watch-read-btn"' not in listed and "changes-row-new" not in listed


def test_a_watched_folder_has_its_pages_and_those_made_later(alice, wiki_dir):
    save(alice, "projects/leaf", "# Leaf\n")
    # A folder's changes offer to watch it.
    listed = alice.get("/changes?under=projects", headers=HOST).get_data(as_text=True)
    assert "Watch this folder" in listed
    assert watch(alice, folder="projects", back="/changes?under=projects").headers["Location"] == "/changes?under=projects"
    assert list(kept(wiki_dir, "alice")["watch"]) == ["projects/"]
    assert "You watch <code>projects/</code>" in alice.get("/changes?under=projects", headers=HOST).get_data(as_text=True)
    # The page says it is watched with its folder.
    assert "You watch the folder projects/" in alice.get("/page/projects/leaf", headers=HOST).get_data(as_text=True)
    assert count(alice) == 0
    as_user(alice, "bob", "projects/leaf", "# Leaf\n\nGrows.\n")
    as_user(alice, "bob", "projects/new/tree", "# Tree\n")       # made later, in a folder inside it
    as_user(alice, "bob", "elsewhere", "# Elsewhere\n")
    assert count(alice) == 2
    assert {tuple(pages) for _, new, pages in watched_rows(alice) if new} == {("projects/leaf",), ("projects/new/tree",)}
    # Within a folder of the watched one, the watched changes are that folder's.
    assert [pages for _, _, pages in watched_rows(alice, under="projects/new")] == [["projects/new/tree"]]
    assert alice.get("/page/projects/leaf", headers=HOST).status_code == 200
    assert count(alice) == 1
    watch(alice, on=False, folder="projects")
    assert count(alice) == 0 and watching(wiki_dir, "alice") is None


def test_a_watch_follows_a_rename_and_a_folders_move(alice, wiki_dir):
    save(alice, "roadmap", "# Roadmap\n")
    save(alice, "projects/leaf", "# Leaf\n")
    watch(alice, page="roadmap")
    watch(alice, page="projects/leaf")
    watch(alice, folder="projects")
    login(alice, "bob")
    assert alice.post("/rename/roadmap", data={"new_name": "plans/roadmap"}, headers=HOST).status_code == 302
    text, err = tool(alice, "rename_page", token="bob-token", slug="projects/", new_slug="work")
    assert not err, text
    login(alice, "alice")
    assert sorted(kept(wiki_dir, "alice")["watch"]) == ["plans/roadmap", "work/", "work/leaf"]
    # The move itself is bob's change of the pages: news, where they are now.
    assert count(alice) == 2
    assert alice.get("/page/plans/roadmap", headers=HOST).status_code == 200
    assert alice.get("/page/work/leaf", headers=HOST).status_code == 200
    assert count(alice) == 0
    as_user(alice, "bob", "plans/roadmap", "# Roadmap\n\nMoved and changed.\n")
    assert count(alice) == 1


def test_nothing_of_a_page_you_may_not_see(alice, wiki_dir):
    login(alice, "bob")
    save(alice, "private/bob/diary", "# Diary\n")
    save(alice, "plan", "# Plan\n")
    login(alice, "alice")
    # A page she doesn't see, and one that isn't there, answer alike.
    assert watch(alice, page="private/bob/diary").status_code == 404
    assert watch(alice, page="private/bob/nothing").status_code == 404
    assert watch(alice, page="no-such-page").status_code == 404
    assert watch(alice, page="../etc/passwd").status_code == 404
    assert watch(alice, folder="_tasks").status_code == 404
    assert watching(wiki_dir, "alice") is None
    # A folder is watched whatever is in it, and tells nothing of what she doesn't see.
    assert watch(alice, folder="private/bob").status_code == 303
    watch(alice, page="plan")
    as_user(alice, "bob", "private/bob/diary", "# Diary\n\nDear diary.\n")
    assert count(alice) == 0 and [pages for _, _, pages in watched_rows(alice)] == [["plan"]]
    listed = alice.get("/changes?watched=1", headers=HOST).get_data(as_text=True)
    assert "private/bob" not in listed and "diary" not in listed
    # A watched page moved to where she doesn't see it: no news of it, nor that it went.
    login(alice, "bob")
    assert alice.post("/rename/plan", data={"new_name": "private/bob/plan", "confirmed": "private/bob/plan"},
                      headers=HOST).status_code == 302
    save(alice, "private/bob/plan", "# Plan\n\nSecret now.\n")
    login(alice, "alice")
    assert count(alice) == 0
    listed = alice.get("/changes?watched=1", headers=HOST).get_data(as_text=True)
    assert "private/bob" not in listed and "Secret" not in listed
    text, err = tool(alice, "history", watched=True)
    assert not err and "private/bob" not in text


def test_a_viewer_watches_and_a_wiki_without_users_has_no_watching(alice, wiki_dir, client, monkeypatch):
    save(alice, "handbook", "# Handbook\n")
    login(alice, "carol")                                        # a viewer: it changes nothing of the wiki
    assert watch(alice, page="handbook").status_code == 303
    as_user(alice, "bob", "handbook", "# Handbook\n\nNew rule.\n")
    login(alice, "carol")
    assert count(alice) == 1
    assert alice.post("/watch/read", headers=HOST).status_code == 303
    assert count(alice) == 0
    # Without users there is nobody else: no Watch, no route, no filter.
    monkeypatch.setattr(settings, "USERS", {})
    monkeypatch.setattr(settings, "USER_SETTINGS", {})
    monkeypatch.setattr(settings, "PASSWORD", "pw")
    assert client.post("/login", data={"password": "pw"}, headers=HOST).status_code == 302
    page = client.get("/page/handbook", headers=HOST).get_data(as_text=True)
    assert 'action="/watch"' not in page and "menu-watched" not in page
    assert watch(client, page="handbook").status_code == 404
    assert client.post("/watch/read", headers=HOST).status_code == 404
    listed = client.get("/changes?watched=1", headers=HOST).get_data(as_text=True)
    assert "Only what I watch" not in listed and "Update handbook" in listed      # the filter is ignored


def test_an_assistant_watches_for_its_user_and_its_changes_are_news(alice, wiki_dir):
    save(alice, "roadmap", "# Roadmap\n")
    text, err = tool(alice, "history", watched=True)
    assert not err and text.startswith("The user watches nothing")
    text, err = tool(alice, "watch_page", slug="roadmap")
    assert not err and text.startswith("The user watches the page roadmap now")
    text, err = tool(alice, "watch_page", slug="notes/")
    assert not err and "the folder notes/" in text
    assert sorted(kept(wiki_dir, "alice")["watch"]) == ["notes/", "roadmap"]
    assert tool(alice, "watch_page", slug="private/bob/diary")[1]
    assert tool(alice, "watch_page", slug="no-such-page")[1]
    # What her own assistant writes is news to her: she sees what it did.
    text, err = tool(alice, "append_to_page", slug="roadmap", content="Q2 is planned.", summary="plan")
    assert not err, text
    assert count(alice) == 1
    text, err = tool(alice, "history", watched=True)
    assert not err and "to what the user watches" in text
    row = next(line for line in text.splitlines() if "roadmap" in line and " | " in line)
    assert row.endswith("| roadmap | new") and "new: a page the user watches" in text
    # The tool reading the page is no look of hers; her opening it is.
    assert not tool(alice, "read_page", slug="roadmap")[1]
    assert count(alice) == 1
    assert alice.get("/page/roadmap", headers=HOST).status_code == 200
    assert count(alice) == 0
    assert " | new" not in tool(alice, "history", watched=True)[0]
    text, err = tool(alice, "watch_page", slug="roadmap", watch=False)
    assert not err and text == "The user no longer watches the page roadmap."
    # A viewer's connection changes nothing, what they watch included: they watch in the browser.
    text, err = tool(alice, "watch_page", token="carol-token", slug="roadmap")
    assert err


def test_sharing_mode_shows_no_count(alice, wiki_dir):
    save(alice, "roadmap", "# Roadmap\n")
    watch(alice, page="roadmap")
    as_user(alice, "bob", "roadmap", "# Roadmap\n\nMore.\n")
    assert count(alice) == 1
    alice.set_cookie("zone-sharing", "1")
    assert count(alice) == 0
    assert "top-updates" not in alice.get("/page/index", headers=HOST).get_data(as_text=True)   # no bell in sharing mode


def test_updates_lists_the_watched_pages_and_mark_all_read_reads_them(alice, wiki_dir):
    """Updates has the watched pages someone else changed, the latest first,
    each with what the change did; Mark all read reads them, and who watches
    nothing is told how to."""
    page = lambda: alice.get("/updates", headers=HOST).get_data(as_text=True)
    assert '<h2 id="updates-watched-h">Pages you watch</h2>' in page() and "You watch no page." in page()
    save(alice, "roadmap", "# Roadmap\n")
    save(alice, "projects/plan", "# Plan\n")
    watch(alice, page="roadmap")
    watch(alice, folder="projects")
    assert "Nothing you watch changed since you last looked." in page()
    as_user(alice, "bob", "roadmap", "# Roadmap\n\nQ1.\n")
    as_user(alice, "bob", "projects/plan", "# Plan\n\nStep 1.\n")
    assert sorted(updates_rows(alice)) == [("Plan", "bob", "/page/projects/plan"), ("Roadmap", "bob", "/page/roadmap")]
    assert bell(alice) == 2 and count(alice) == 2
    html = page()
    assert re.search(r'<a class="updates-row-diff" href="/diff/roadmap/[0-9a-f]{7}">What changed</a>', html)
    assert '<a href="/changes?watched=1">Every change to what you watch</a>' in html
    assert 'id="updates-read-btn" title="Mark every update as read">' in html          # shown from the first paint
    # bob, who watches nothing and made the changes, has none of it.
    login(alice, "bob")
    assert updates_rows(alice) == [] and bell(alice) == 0
    login(alice, "alice")
    before = commit_messages(wiki_dir)
    assert alice.post("/updates/read", headers=HOST).status_code == 204
    assert updates_rows(alice) == [] and bell(alice) == 0 and count(alice) == 0
    assert commit_messages(wiki_dir) == before
    assert list(kept(wiki_dir, "alice")["seen"]) == ["projects/plan", "roadmap"]
