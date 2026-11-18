"""Changes (/changes): the wiki's commits in the browser, as the MCP tool
history lists them: by day, who and whether through an assistant, what
each changed, the filters, sharing mode. What a user may not see of them
is probed in test_privacy.py, test_group_pages.py and test_list_members.py.
"""

import html
import re
import subprocess

import pytest

from tests import settings
from tests.conftest import HOST, create_task
from tests.test_git import git, save

TOKEN = "test-token"


@pytest.fixture()
def wiki_dir(git_wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "MCP_TOKEN", TOKEN)
    return git_wiki_dir


def tool(client, name, token=TOKEN, **args):
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": name, "arguments": args}},
                       headers={**HOST, "Authorization": f"Bearer {token}"})
    result = resp.get_json()["result"]
    assert not result["isError"], result
    return result["content"][0]["text"]


def changes(client, **query):
    """The rows of /changes: (what, who, by an assistant, [(label, href)])."""
    resp = client.get("/changes", query_string=query, headers=HOST)
    assert resp.status_code == 200
    rows = []
    for row in re.findall(r'<li class="hist-row changes-row">(.*?)</li>', resp.get_data(as_text=True), re.S):
        what = re.search(r'<span class="changes-what">([^<]*)', row).group(1)
        who = re.search(r'<span class="hist-by"> · ([^<]*)</span>', row)
        items = [(html.unescape(label), href) for href, label in re.findall(
            r'<(?:a href="([^"]*)"|span)>([^<]*)</(?:a|span)>', row.split('<span class="changes-items">')[1])]
        rows.append((html.unescape(what).strip(), who.group(1) if who else "", "changes-assistant" in row, items))
    return rows


def test_the_wikis_commits_newest_first_with_what_each_changed(client, wiki_dir):
    save(client, "garden", "# Garden\n\nBeds.\n")
    tool(client, "write_page", slug="trips/rome", content="# Rome\n\nPlan.\n", summary="from the mail")
    create_task(client, title="Water the beds")
    rows = changes(client)
    assert [r[0] for r in rows[:3]] == ["Create task", "Create trips/rome: from the mail", "Update garden"]
    assert [r[2] for r in rows[:3]] == [False, True, False]          # the assistant's, marked
    assert all(who == "" for _, who, _, _ in rows)                   # no users: no names
    sha = git(wiki_dir, "log", "-n1", "--format=%H", "--", "trips/rome.md").strip()
    assert rows[1][3] == [("trips/rome", f"/diff/trips/rome/{sha}")]
    assert rows[0][3] == [("Tasks", "/tasks")]
    save(client, "tasks", "# A page called tasks\n")                 # a page, not the task files
    assert changes(client)[0][3][0][1].startswith("/diff/tasks/")
    # A page's link opens what that commit changed on it.
    assert "Plan." in client.get(rows[1][3][0][1], headers=HOST).get_data(as_text=True)
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    assert '<h2 class="changes-day">Today</h2>' in body and "(MCP)" not in body
    assert 'name="user"' not in body                                 # no users: no filter by user


def test_the_same_list_as_the_assistants_history(client, wiki_dir):
    """One function for both doors: every commit the tool lists is on the page."""
    save(client, "garden", "# Garden\n")
    tool(client, "append_to_page", slug="garden", content="More.")
    listed = re.findall(r"^\S+ \S+ \| \w{7} \| (.*?) \| ", tool(client, "history"), re.M)
    assert [m.removesuffix("(MCP)").strip() for m in listed] == [r[0] for r in changes(client)]


def test_filters(client, wiki_dir):
    save(client, "garden", "# Garden\n")
    tool(client, "write_page", slug="trips/rome", content="# Rome\n")
    save(client, "trips/oslo", "# Oslo\n")
    assert [r[0] for r in changes(client, assistant="1")] == ["Create trips/rome"]
    assert [r[0] for r in changes(client, under="trips")] == ["Update trips/oslo", "Create trips/rome"]
    assert [r[0] for r in changes(client, under="/page/trips/", assistant="1")] == ["Create trips/rome"]
    (wiki_dir / "old.md").write_text("# Old\n")
    git(wiki_dir, "add", "old.md")
    then = "2001-02-03T10:00:00"
    subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "Add old", f"--date={then}"],
                   cwd=wiki_dir, check=True, env={**settings.GIT_ENV, "GIT_COMMITTER_DATE": then})
    assert "Add old" in [r[0] for r in changes(client)]
    assert "Add old" not in [r[0] for r in changes(client, since="2020-01-01")]
    assert "Add old" in [r[0] for r in changes(client, since="not a day")]     # ignored
    body = client.get("/changes", query_string={"under": "trips", "assistant": "1"}, headers=HOST).get_data(as_text=True)
    assert 'name="under" value="trips"' in body and 'name="assistant" value="1" checked' in body
    assert 'href="/changes" class="changes-all"' in body
    empty = client.get("/changes", query_string={"under": "nowhere"}, headers=HOST).get_data(as_text=True)
    assert "No changes like that." in empty
    assert '<h2 class="changes-day">2001-02-03</h2>' in client.get("/changes", headers=HOST).get_data(as_text=True)


def test_show_more(client, wiki_dir):
    for i in range(4):
        save(client, f"p{i}", f"# P{i}\n")
    body = client.get("/changes", query_string={"n": "2", "under": ""}, headers=HOST).get_data(as_text=True)
    assert len(changes(client, n="2")) == 2 and 'href="/changes?n=4"' in body
    assert "Show more" not in client.get("/changes", headers=HOST).get_data(as_text=True)   # all of them shown
    assert len(changes(client, n="-3")) == 1 and len(changes(client, n="x")) >= 4


def test_by_user_and_through_whose_assistant(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "MCP_TOKEN", "")                   # with users each has their own
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token")})
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    save(client, "garden", "# Garden\n")
    tool(client, "write_page", token="bob-token", slug="shed", content="# Shed\n")
    tool(client, "append_to_page", token="alice-token", slug="garden", content="More.")
    assert [(r[0], r[1], r[2]) for r in changes(client)[:3]] == [
        ("Append to garden", "alice", True), ("Create shed", "bob", True), ("Update garden", "alice", False)]
    assert [r[0] for r in changes(client, user="bob")] == ["Create shed"]
    assert [r[0] for r in changes(client, user="alice", assistant="1")] == ["Append to garden"]
    assert len(changes(client, user="nobody")) >= 3                  # no user's name: everyone's
    body = client.get("/changes", query_string={"user": "bob"}, headers=HOST).get_data(as_text=True)
    assert '<option value="bob" selected>bob</option>' in body and '<option value="alice">alice</option>' in body
    # Connect an assistant leads to what one's own assistants changed.
    assert 'href="/changes?assistant=1&amp;user=alice"' in client.get("/assistant", headers=HOST).get_data(as_text=True)


def test_sharing_mode_leaves_out_what_touched_a_page_not_for_sharing(client, wiki_dir):
    save(client, "garden", "# Garden\n")
    save(client, "pay", "---\ntags: [noshare]\n---\n# Pay\n")
    assert "Update pay" in [r[0] for r in changes(client)]
    client.set_cookie("zone-sharing", "1")
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    assert "Update garden" in body and "pay" not in body.split('<div class="sheet">')[1]


def test_where_it_is_found(client, wiki_dir):
    listed = client.get("/api/pages-list", headers=HOST).get_json()
    assert [p["title"] for p in listed[:2]] == ["Changes", "Deleted Pages"] and listed[0]["href"] == "/changes"
    assert 'href="/changes"' in client.get("/history/index", headers=HOST).get_data(as_text=True)
    assert 'href="/changes?assistant=1"' in client.get("/assistant", headers=HOST).get_data(as_text=True)


def test_without_git_there_is_no_such_page(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "GIT_ENABLED", False)
    assert client.get("/changes", headers=HOST).status_code == 404
    assert "Changes" not in [p["title"] for p in client.get("/api/pages-list", headers=HOST).get_json()]
    assert "/changes" not in client.get("/assistant", headers=HOST).get_data(as_text=True)
