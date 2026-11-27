"""The search's kind and place filters (Kinds and places in
docs/spec/search.md): is:page, is:task, is:note and in:everyone,
in:private, in:<group>, with their - forms, the same in /api/search,
/search, search_wiki and search_tasks, and the search modal's chips and
Where menu that write them.

alice and carol are in the group dev, bob in no group. The word kestrel is
in a shared page, a top-level note, alice's private page and private note,
the group's page, a shared task, alice's private task and a task of the
group's list."""

import re

import pytest

from tests import settings
from tests.conftest import HOST, write_page

WORD = "kestrel"
USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol")}
# The slugs of the pages that say WORD, by what they are.
PAGES = {
    "shared": "birds",
    "note": f"2026-10-27_09-00-00_{WORD}-note",
    "private": "private/alice/hide",
    "private-note": f"private/alice/2026-10-28_09-00-00_{WORD}-mine",
    "group": "groups/dev/spec",
}


def sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def mcp(client, who, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {USERS[who][1]}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return "".join(c.get("text", "") for c in resp.get_json()["result"]["content"])


def search(client, q, **more):
    resp = client.get("/api/search", query_string={"q": q, "limit": 100, **more}, headers=HOST)
    assert resp.status_code == 200
    return resp.get_json()


def found(results):
    """The pages (by slug) and tasks (by title) among results."""
    return {r["title"].removeprefix("[Task] ") if r.get("type") == "task" else r["slug"] for r in results}


TASKS = {"shared": f"Ring {WORD} shared", "private": f"Ring {WORD} mine", "group": f"Ring {WORD} for dev"}


@pytest.fixture()
def wiki(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}, "carol": {"groups": "dev"}})
    for what, slug in PAGES.items():
        write_page(wiki_dir, slug, f"# {what.title()} {WORD}\n\nThe {WORD} hovers ({what}).\n")
    sign_in(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    assert client.post("/tasks/new-list", data={"name": "devlist", "for": "group:dev"}, headers=HOST).status_code == 302
    # In no list a task is in alice's Inbox, hers alone.
    for what, extra in (("shared", {"list": "team"}), ("private", {}), ("group", {"list": "devlist"})):
        assert client.post("/api/tasks", json={"title": TASKS[what], **extra}, headers=HOST).status_code in (200, 201)
    return wiki_dir


ALL_OF_ALICE = set(PAGES.values()) | set(TASKS.values())


def test_without_filters_alice_finds_all_of_it(client, wiki):
    assert found(search(client, WORD)) == ALL_OF_ALICE


@pytest.mark.parametrize("q, want", [
    (f"{WORD} is:task", set(TASKS.values())),
    (f"{WORD} is:note", {PAGES["note"], PAGES["private-note"]}),
    (f"{WORD} is:page", {PAGES["shared"], PAGES["private"], PAGES["group"]}),
    (f"{WORD} is:page is:note", set(PAGES.values())),     # several kinds: either
    (f"{WORD} -is:task", set(PAGES.values())),
    (f"{WORD} IS:Task", set(TASKS.values())),             # any case
])
def test_is_keeps_a_kind(client, wiki, q, want):
    assert found(search(client, q)) == want


@pytest.mark.parametrize("q, want", [
    (f"{WORD} in:everyone", {PAGES["shared"], PAGES["note"], TASKS["shared"]}),
    (f"{WORD} in:private", {PAGES["private"], PAGES["private-note"], TASKS["private"]}),
    (f"{WORD} in:dev", {PAGES["group"], TASKS["group"]}),
    (f"{WORD} in:dev in:private", {PAGES["group"], TASKS["group"], PAGES["private"], PAGES["private-note"], TASKS["private"]}),
    (f"{WORD} -in:dev", ALL_OF_ALICE - {PAGES["group"], TASKS["group"]}),
    (f"{WORD} in:dev is:task", {TASKS["group"]}),
    (f"{WORD} in:nosuchgroup", set()),
])
def test_in_keeps_a_place(client, wiki, q, want):
    assert found(search(client, q)) == want


def test_a_filter_only_narrows_what_one_sees(client, wiki):
    """bob is in no group: in:dev finds nothing of the group's, in:private
    nothing of alice's, and neither says that they are there."""
    sign_in(client, "bob")
    assert found(search(client, f"{WORD} in:dev")) == set()
    assert found(search(client, "in:dev")) == set()
    assert found(search(client, f"{WORD} in:private")) == set()
    assert found(search(client, f"{WORD} is:note")) == {PAGES["note"]}
    assert found(search(client, f"{WORD} -in:everyone")) == set()
    for q in (f"{WORD} in:dev", f"{WORD} in:private"):
        assert "groups/dev" not in mcp(client, "bob", "search_wiki", query=q)
        text = mcp(client, "bob", "search_tasks", query=q, status="all")
        assert TASKS["group"] not in text and TASKS["private"] not in text


def test_filters_alone_list_newest_first(client, wiki):
    """No words: a kind or place lists everything that passes, as a date
    filter alone does; only exclusions find nothing."""
    assert found(search(client, "is:task")) == set(TASKS.values())
    assert found(search(client, "in:dev")) == {PAGES["group"], TASKS["group"]}
    assert found(search(client, "is:note in:private")) == {PAGES["private-note"]}
    assert search(client, "-is:task") == []


def test_operators_are_not_words(client, wiki):
    """They rank nothing and mark nothing: a shared page saying "in:dev" is
    not what in:dev finds, and an excerpt marks the words alone."""
    write_page(wiki, "plain", "# Plain\n\nis:task in:dev, written out\n")
    assert "plain" not in found(search(client, "in:dev"))
    assert "plain" not in found(search(client, "is:task"))
    excerpts = [r["excerpt"] for r in search(client, f"{WORD} in:dev")]
    assert excerpts and all(re.findall(r"<mark>([^<]*)</mark>", e) for e in excerpts)
    assert all(m.lower() == WORD for e in excerpts for m in re.findall(r"<mark>([^<]*)</mark>", e))


def test_the_search_page_and_unlinked_take_them_too(client, wiki):
    html = client.get("/search", query_string={"q": f"{WORD} in:dev"}, headers=HOST).get_data(as_text=True)
    assert PAGES["group"] in html and "/page/birds" not in html
    unlinked = found(search(client, f"{WORD} is:note", unlinked=1))
    assert unlinked == {PAGES["note"], PAGES["private-note"]}


def test_the_assistants_tools_take_them(client, wiki):
    text = mcp(client, "alice", "search_wiki", query=f"{WORD} in:dev")
    assert "groups/dev/spec" in text and "birds" not in text and "hide" not in text
    text = mcp(client, "alice", "search_tasks", query="in:private", status="all")
    assert TASKS["private"] in text and TASKS["shared"] not in text and TASKS["group"] not in text
    assert WORD not in mcp(client, "alice", "search_tasks", query=f"{WORD} is:page", status="all")


def test_without_users_everything_is_everyones(client, wiki_dir):
    write_page(wiki_dir, "birds", f"# Birds\n\nA {WORD}.\n")
    write_page(wiki_dir, f"2026-10-27_09-00-00_{WORD}", f"# Note\n\nA {WORD}.\n")
    assert found(search(client, f"{WORD} in:everyone")) == {"birds", f"2026-10-27_09-00-00_{WORD}"}
    assert found(search(client, f"{WORD} in:private")) == set()
    assert found(search(client, f"{WORD} is:note")) == {f"2026-10-27_09-00-00_{WORD}"}


def test_the_page_list_says_which_pages_are_notes(client, wiki):
    entries = {p["slug"]: p for p in client.get("/api/pages-list", headers=HOST).get_json()}
    assert entries[PAGES["note"]]["note"] and entries[PAGES["private-note"]]["note"]
    assert not entries[PAGES["shared"]]["note"] and not entries[PAGES["group"]]["note"]


def test_the_where_menu_lists_ones_own_groups(client, wiki):
    """The search modal's Where menu: Anywhere, Everyone's, Private and the
    groups that are alice's; none of them for bob, who is in no group."""
    html = client.get("/page/index", headers=HOST).get_data(as_text=True)
    menu = re.search(r'<select id="modal-where".*?</select>', html, re.S).group(0)
    assert re.findall(r'<option value="([^"]*)"', menu) == ["", "everyone", "private", "dev"]
    assert 'data-kind="task"' in html and 'data-kind="note"' in html
    sign_in(client, "bob")
    html = client.get("/page/index", headers=HOST).get_data(as_text=True)
    menu = re.search(r'<select id="modal-where".*?</select>', html, re.S).group(0)
    assert re.findall(r'<option value="([^"]*)"', menu) == ["", "everyone", "private"]


def test_no_where_menu_without_users(client, wiki_dir):
    html = client.get("/page/index", headers=HOST).get_data(as_text=True)
    assert 'id="modal-where"' not in html
    assert 'data-kind="page"' in html and 'data-kind="task"' in html
