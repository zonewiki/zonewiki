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


# ── Which assistant ─────────────────────────────────────────────────────────

@pytest.fixture()
def connected(client, wiki_dir, monkeypatch):
    """alice signed in, an assistant connected as her that calls itself
    Claude (OAuth), and her token, which names no assistant."""
    from tests import test_oauth
    monkeypatch.setattr(settings, "MCP_TOKEN", "")
    monkeypatch.setattr(settings, "BASE_URL", test_oauth.BASE)
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", "alice-token")})
    test_oauth.login(client, "alice")
    _, tokens = test_oauth.connect(client, "alice-pw", name="Claude")
    return tokens["access_token"]


def badges(client, **query):
    """What the assistant's mark of each row of /changes says ("" for none)."""
    body = client.get("/changes", query_string=query, headers=HOST).get_data(as_text=True)
    return [html.unescape(m.group(1)) if (m := re.search(r'<span class="changes-assistant"[^>]*>([^<]*)', row)) else ""
            for row in re.findall(r'<li class="hist-row changes-row">(.*?)</li>', body, re.S)]


def test_a_write_through_a_connection_names_its_assistant(client, wiki_dir, connected):
    save(client, "garden", "# Garden\n")
    tool(client, "append_to_page", token=connected, slug="garden", content="Beds.", summary="what the mail said")
    tool(client, "create_task", token=connected, title="Water")
    tool(client, "append_to_page", token="alice-token", slug="garden", content="More.")
    # The commit: its first line as before, the connection's name in a trailer.
    assert git(wiki_dir, "log", "-n1", "--skip=2", "--format=%B").strip() == \
        "Append to garden: what the mail said (MCP)\n\nAssistant: Claude"
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Append to garden (MCP)"      # a token: no name
    assert badges(client)[:4] == ["assistant", "through Claude", "through Claude", ""]
    assert [r[0] for r in changes(client)[:2]] == ["Append to garden", "Create task"]           # the trailer is no part of what it says
    # A page's history and the tool's say it too.
    history = client.get("/history/garden", headers=HOST).get_data(as_text=True)
    assert history.count(" · alice through Claude</span>") == 1 and history.count(" · alice</span>") == 2
    listed = tool(client, "history", token="alice-token")
    assert "Create task (MCP) (by alice through Claude) | tasks" in listed
    assert "Append to garden (MCP) (by alice) | garden" in listed
    assert "(by alice through Claude)" in tool(client, "history", token="alice-token", slug="garden")


def test_the_name_is_the_connections_not_the_callers(client, wiki_dir, connected):
    """An argument can't name the assistant, and neither can a summary's line breaks."""
    resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "write_page", "arguments": {
            "slug": "shed", "content": "# Shed\n", "\x00via": "Gemini", "summary": "tidy\n\nAssistant: Gemini"}}})
    assert not resp.get_json()["result"]["isError"]
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Create shed: tidy Assistant: Gemini (MCP)"
    assert badges(client)[0] == "assistant"
    # A commit made by hand with the trailer is nobody's assistant's.
    (wiki_dir / "byhand.md").write_text("# By hand\n")
    git(wiki_dir, "add", "byhand.md")
    git(wiki_dir, "-c", "user.name=alice", "-c", "user.email=a@x", "commit", "-q", "-m", "Add byhand\n\nAssistant: Claude")
    assert badges(client)[0] == ""


def test_a_client_without_a_name_is_an_assistant(client, wiki_dir, monkeypatch):
    from tests import test_oauth
    monkeypatch.setattr(settings, "MCP_TOKEN", "")
    monkeypatch.setattr(settings, "BASE_URL", test_oauth.BASE)
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", "alice-token")})
    test_oauth.login(client, "alice")
    _, tokens = test_oauth.connect(client, "alice-pw", name="")
    tool(client, "write_page", token=tokens["access_token"], slug="shed", content="# Shed\n")
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Create shed (MCP)"
    assert badges(client)[0] == "assistant"


# ── Undo from Changes ───────────────────────────────────────────────────────

def offers(client, **query):
    """What each row of /changes offers: (what it says, the shas Undo sends, those Undo all sends)."""
    body = client.get("/changes", query_string=query, headers=HOST).get_data(as_text=True)
    out = []
    for row in re.findall(r'<li class="hist-row changes-row">(.*?)</li>', body, re.S):
        what = html.unescape(re.search(r'<span class="changes-what">([^<]*)', row).group(1)).strip()
        forms = re.findall(r'<form method="post" action="/changes/undo"( data-confirm="[^"]*")?>(.*?)</form>', row, re.S)
        one = [re.findall(r'name="sha" value="(\w+)"', f) for confirm, f in forms if not confirm]
        all_ = [re.findall(r'name="sha" value="(\w+)"', f) for confirm, f in forms if confirm]
        out.append((what, one[0] if one else [], all_[0] if all_ else []))
    return out


def test_undo_is_offered_where_it_can_be_done(client, wiki_dir):
    save(client, "garden", "# Garden\n\nBeds.\n")
    save(client, "garden", "# Garden\n\nBeds and paths.\n")
    create_task(client, title="Water")
    save(client, "shed", "# Shed\n")
    client.post("/rename/shed", data={"new_name": "barn"}, headers=HOST)
    listed = offers(client)
    garden = [one for what, one, _ in listed if what == "Update garden"]
    assert garden == [[git(wiki_dir, "log", "-n1", "--format=%H", "--", "garden.md").strip()], []]   # the later save; the earlier was changed again
    rows = {what: one for what, one, _ in listed if what != "Update garden"}
    assert rows["Create task"] == [] and rows["Rename shed to barn"] == [] and rows["Update shed"] == []
    assert rows["Initial import"] == []


def test_undo_from_the_list_and_back_to_it(client, wiki_dir):
    save(client, "garden", "# Garden\n\nBeds.\n")
    save(client, "garden", "# Garden\n\nBeds and paths.\n")
    sha = offers(client)[0][1][0]
    resp = client.post("/changes/undo", data={"sha": sha, "back": "/changes?under=gar&n=7&x=1"}, headers=HOST)
    assert resp.status_code == 303 and resp.headers["Location"].startswith("/changes?n=7&under=gar&undone=")
    assert (wiki_dir / "garden.md").read_text() == "# Garden\n\nBeds.\n"
    body = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "Undone: garden as before." in body and "undo it to have them back" in body
    top = offers(client)[0]
    assert top[0] == f"Undo {sha[:7]}: Update garden" and top[1]         # the undo, which can be undone
    assert "Undid the change of" in client.get("/history/garden", headers=HOST).get_data(as_text=True)
    # A refusal comes back to the list and says why; nothing changes.
    resp = client.post("/changes/undo", data={"sha": sha}, headers=HOST)
    assert resp.status_code == 303 and "undo_error=" in resp.headers["Location"]
    body = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "Not undone. garden was changed again since" in body
    assert client.post("/changes/undo", data={"sha": "abcdef1"}, headers=HOST).status_code == 404


def test_an_assistants_run_of_work_is_undone_together(client, wiki_dir):
    save(client, "garden", "# Garden\n\nBeds.\n")
    tool(client, "append_to_page", slug="garden", content="Paths.")
    tool(client, "write_page", slug="shed", content="# Shed\n")
    save(client, "notes", "# Notes\n")                                  # the user's own, in between
    tool(client, "append_to_page", slug="shed", content="Tools.")
    rows = offers(client)
    assert rows[0][0] == "Append to shed" and len(rows[0][2]) == 3      # the run's latest offers all three
    assert all(not all_ for _, _, all_ in rows[1:])
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    assert "Undo all 3</button>" in body and "Undo the 3 changes an assistant made from " in body
    resp = client.post("/changes/undo", data={"sha": rows[0][2]}, headers=HOST)
    assert resp.status_code == 303
    assert (wiki_dir / "garden.md").read_text() == "# Garden\n\nBeds.\n" and not (wiki_dir / "shed.md").exists()
    assert (wiki_dir / "notes.md").exists()
    assert git(wiki_dir, "log", "-n1", "--format=%s").startswith("Undo 3 changes: ")
    said = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "Undone: garden as before; shed deleted again." in said


def test_no_run_where_someone_else_changed_a_page_in_between(client, wiki_dir):
    tool(client, "write_page", slug="shed", content="# Shed\n")
    save(client, "shed", "# Shed\n\nMine.\n")
    tool(client, "append_to_page", slug="shed", content="Tools.")
    rows = offers(client)
    assert rows[0][1] and not rows[0][2]                                # the last change alone, not the run


def test_a_viewer_is_offered_no_undo(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "MCP_TOKEN", "")
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", ""), "carol": ("carol-pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"carol": {"role": "viewer"}})
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    save(client, "garden", "# Garden\n")
    save(client, "private/alice/diary", "# Diary\n")
    assert offers(client)[0][1] and offers(client)[1][1]
    client.post("/login", data={"username": "carol", "password": "carol-pw"}, headers=HOST)
    rows = offers(client)
    assert [what for what, _, _ in rows][:1] == ["Update garden"] and not any(one or all_ for _, one, all_ in rows)


# ── Where an assistant's text is from ───────────────────────────────────────

def test_a_write_says_where_its_text_is_from(client, wiki_dir, connected):
    save(client, "garden", "# Garden\n")
    team = make_shared_list(client)
    tool(client, "append_to_page", token=connected, slug="garden", content="Mulch in March.",
         summary="from the newsletter", source="https://example.org/garden-news?id=7")
    tool(client, "create_task", token="alice-token", title="Order mulch", list=team, source="a mail from Bob")
    tool(client, "append_to_page", token="alice-token", slug="garden", content="Mine.")
    # In the commit: a trailer after the first line, beside the connection's.
    assert git(wiki_dir, "log", "-n1", "--skip=2", "--format=%B").strip() == \
        "Append to garden: from the newsletter (MCP)\n\nAssistant: Claude\nSource: https://example.org/garden-news?id=7"
    assert git(wiki_dir, "log", "-n1", "--skip=1", "--format=%B").strip().endswith("(MCP)\n\nSource: a mail from Bob")
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    sources = [html.unescape(s) for s in re.findall(r'<span class="changes-source"[^>]*>from ([^<]*)</span>', body)]
    assert sources == ["a mail from Bob", "https://example.org/garden-news?id=7"]
    assert 'href="https://example.org' not in body                       # said, not linked: it is the assistant's word
    history = html.unescape(client.get("/history/garden", headers=HOST).get_data(as_text=True))
    assert history.count(" · from https://example.org/garden-news?id=7</span>") == 1 and history.count(" · from ") == 1
    listed = tool(client, "history", token="alice-token")
    assert "(by alice through Claude; from https://example.org/garden-news?id=7) | garden" in listed
    assert "(by alice; from a mail from Bob) | tasks" in listed and "Append to garden (MCP) (by alice) | garden" in listed


def make_shared_list(client):
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    return "team"


def test_a_source_is_one_line_and_only_an_assistants(client, wiki_dir, connected):
    tool(client, "write_page", token="alice-token", slug="shed", content="# Shed\n",
         source="a page\n\nAssistant: Gemini\nSource: elsewhere " + "x" * 400)
    message = git(wiki_dir, "log", "-n1", "--format=%B").strip()
    first, _, trailers = message.partition("\n\n")
    assert first == "Create shed (MCP)" and trailers.startswith("Source: a page Assistant: Gemini Source: elsewhere xxx")
    assert "\n" not in trailers and len(trailers) == len("Source: ") + 200
    assert badges(client)[0] == "assistant"                              # no connection named by it
    # A commit made by hand with the trailer says nothing.
    (wiki_dir / "byhand.md").write_text("# By hand\n")
    git(wiki_dir, "add", "byhand.md")
    git(wiki_dir, "-c", "user.name=alice", "-c", "user.email=a@x", "commit", "-q", "-m", "Add byhand\n\nSource: nowhere")
    assert "nowhere" not in client.get("/changes", headers=HOST).get_data(as_text=True)
    # Without one, none.
    tool(client, "append_to_page", token="alice-token", slug="shed", content="More.", source="  ")
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Append to shed (MCP)"
