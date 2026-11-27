"""Changes (/changes): the wiki's commits in the browser, as the MCP tool
history lists them: by day, who and whether through an assistant, what
each changed, the filters, sharing mode. What a user may not see of them
is probed in test_privacy.py, test_group_pages.py and test_group_lists.py.
And who changed a page last, as a search result says it (what a public
wiki's visitor is told of it is in test_public.py).
"""

import html
import os
import re
import subprocess
import time
from io import BytesIO

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
        who = re.search(r'<span class="hist-by">([^<]*)</span>', row)
        items = [(html.unescape(label), html.unescape(href)) for href, label in re.findall(
            r'<(?:a href="([^"]*)"|span)>([^<]*)</(?:a|span)>', row.split('<span class="changes-items">')[1])]
        rows.append((html.unescape(what).strip(), who.group(1) if who else "", "changes-assistant" in row, items))
    return rows


def test_the_wikis_commits_newest_first_with_what_each_changed(client, wiki_dir):
    save(client, "garden", "# Garden\n\nBeds.\n")
    tool(client, "write_page", slug="trips/rome", content="# Rome\n\nPlan.\n", summary="from the mail")
    task = create_task(client, title="Water the beds").get_json()
    rows = changes(client)
    assert [r[0] for r in rows[:3]] == ["Create task", "Create trips/rome: from the mail", "Update garden"]
    assert [r[2] for r in rows[:3]] == [False, True, False]          # the assistant's, marked
    assert all(who == "" for _, who, _, _ in rows)                   # no users: no names
    sha = git(wiki_dir, "log", "-n1", "--format=%H", "--", "trips/rome.md").strip()
    assert rows[1][3] == [("trips/rome", f"/diff/trips/rome/{sha}")]
    assert rows[0][3] == [("Water the beds", f"/tasks?task={task['id']}&view=open")]
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
    assert [m.removesuffix(" (through an assistant)") for m in listed] == [r[0] for r in changes(client)]
    assert listed[0] == "Append to garden (through an assistant)"


# ── The tasks a commit changed ──────────────────────────────────────────────

def test_a_commit_names_the_tasks_it_changed(client, wiki_dir):
    """Each commit of tasks names them in its trailers, as the store wrote
    them (no task file read to list Changes), and Changes shows each by its
    title as it is now, linked to it; the tool history too, with its id."""
    a = create_task(client, title="Water the beds").get_json()
    b = create_task(client, title="Mow").get_json()
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == f"Create task\n\nTask: {b['id']}"
    client.put(f"/api/tasks/{a['id']}", json={"title": "Water the [[garden]] beds"}, headers=HOST)
    client.put(f"/api/tasks/{b['id']}", json={"completed": True}, headers=HOST)
    tool(client, "update_task", id=a["id"], comment="Done half of it.")
    rows = changes(client)
    href = lambda t, view="open": f"/tasks?task={t['id']}&view={view}"
    assert [(r[0], r[3]) for r in rows[:5]] == [
        ("Comment on task", [("Water the garden beds", href(a))]),
        ("Complete task", [("Mow", href(b, "closed"))]),
        ("Update task", [("Water the garden beds", href(a))]),          # its title now, plain
        ("Create task", [("Mow", href(b, "closed"))]),
        ("Create task", [("Water the garden beds", href(a))])]
    listed = tool(client, "history")
    assert f'| Comment on task (through an assistant) | "Water the garden beds" (task {a["id"][:8]})' in listed
    assert "pages and tasks:" in listed
    # A task deleted for good is no task to name: its commits say Tasks.
    assert client.delete(f"/api/tasks/{b['id']}", headers=HOST).status_code == 204
    assert [(r[0], r[3]) for r in changes(client)[:3]] == [
        ("Delete task", [("Tasks", "/tasks")]), ("Comment on task", [("Water the garden beds", href(a))]),
        ("Complete task", [("Tasks", "/tasks")])]
    # So does a commit from before the trailers, made by hand here.
    (wiki_dir / "_tasks/tasks.json").write_text((wiki_dir / "_tasks/tasks.json").read_text().replace("Water", "Wet"))
    git(wiki_dir, "commit", "-q", "-am", "Update task")
    assert changes(client)[0][3] == [("Tasks", "/tasks")]


def test_a_commit_of_many_tasks_names_some_and_counts_the_rest(client, wiki_dir):
    ids = [create_task(client, title=f"T{i:02d}").get_json()["id"] for i in range(23)]
    assert client.post("/api/tasks/batch-tag", json={"ids": ids, "add": ["many"]}, headers=HOST).status_code == 200
    message = git(wiki_dir, "log", "-n1", "--format=%B")
    assert message.count("\nTask: ") == 20 and message.strip().endswith("More-Tasks: 3")
    items = changes(client)[0][3]
    assert len(items) == 21 and items[-1] == ("other tasks", "/tasks") and all(i[1].startswith("/tasks?task=") for i in items[:20])


def test_sharing_mode_names_no_task_of_a_list_not_for_sharing(client, wiki_dir):
    assert client.post("/tasks/new-list", data={"name": "secret"}, headers=HOST).status_code == 302
    assert client.post("/tasks/new-list", data={"name": "open"}, headers=HOST).status_code == 302
    assert client.post("/api/tasks/settings", json={"list": "secret", "set": {"noshare": "1"}}, headers=HOST).status_code == 200
    create_task(client, title="Hidden thing", list="secret")
    create_task(client, title="Shown thing", list="open")
    assert "Hidden thing" in client.get("/changes", headers=HOST).get_data(as_text=True)
    client.set_cookie("zone-sharing", "1")
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    assert "Shown thing" in body and "Hidden thing" not in body


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
    # A token names no client, and is said to be one.
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Append to garden (MCP)\n\nAssistant: MCP token"
    assert badges(client)[:4] == ["assistant", "through Claude", "through Claude", ""]
    assert [r[0] for r in changes(client)[:2]] == ["Append to garden", "Create task"]           # the trailer is no part of what it says
    # A page's history and the tool's say it too.
    history = client.get("/history/garden", headers=HOST).get_data(as_text=True)
    assert history.count(" · alice through Claude</span>") == 1 and history.count(" · alice</span>") == 1
    assert history.count(" · alice through an assistant</span>") == 1
    listed = tool(client, "history", token="alice-token")
    assert 'Create task (by alice through Claude) | "Water" (task ' in listed
    assert "Append to garden (by alice through an assistant) | garden" in listed
    assert "(by alice through Claude)" in tool(client, "history", token="alice-token", slug="garden")


def test_filter_by_connection_and_by_text_from_outside(client, wiki_dir, connected):
    """Changes' filters by which assistant (its Assistant trailer) and for
    text from elsewhere (its Source trailer), the same at both doors: the
    tool history takes them as the page does, from the same function."""
    save(client, "garden", "# Garden\n")
    tool(client, "append_to_page", token=connected, slug="garden", content="Beds.", source="https://example.org/beds")
    tool(client, "append_to_page", token=connected, slug="garden", content="Paths.")
    tool(client, "append_to_page", token="alice-token", slug="garden", content="Mine.", source="a mail from Bob")
    tool(client, "write_page", token="alice-token", slug="shed", content="# Shed\n")
    assert [r[0] for r in changes(client, via="Claude")] == ["Append to garden", "Append to garden"]
    assert [r[0] for r in changes(client, via="MCP token")] == ["Create shed", "Append to garden"]
    assert [r[0] for r in changes(client, outside="1")] == ["Append to garden", "Append to garden"]
    assert badges(client, via="Claude", outside="1") == ["through Claude"]
    assert changes(client, via="Gemini") == []
    body = client.get("/changes", query_string={"via": "Claude"}, headers=HOST).get_data(as_text=True)
    assert '<option value="Claude" selected>Claude</option>' in body and '<option value="MCP token">MCP token</option>' in body
    assert 'href="/changes" class="changes-all"' in body
    assert 'name="outside" value="1" checked' in client.get("/changes?outside=1", headers=HOST).get_data(as_text=True)
    # The tool history, through the same filters.
    rows = lambda **args: re.findall(r"^\S+ \S+ \| \w{7} \| (.*?) \| ", tool(client, "history", token="alice-token", **args), re.M)
    assert rows(assistant="Claude") == ["Append to garden (by alice through Claude)",
                                        "Append to garden (by alice through Claude; from https://example.org/beds)"]
    assert rows(assistant="MCP token") == ["Create shed (by alice through an assistant)",
                                           "Append to garden (by alice through an assistant; from a mail from Bob)"]
    assert len(rows(assistant="any")) == 4 and len(rows(outside=True)) == 2
    assert tool(client, "history", token="alice-token", assistant="Gemini").startswith("No changes through Gemini.")
    # Connect an assistant leads to each connection's changes; an undo comes back to the list as filtered.
    assert 'href="/changes?user=alice&amp;via=Claude"' in client.get("/assistant", headers=HOST).get_data(as_text=True)
    sha = git(wiki_dir, "log", "-n1", "--format=%H").strip()
    resp = client.post("/changes/undo", data={"sha": sha, "back": "/changes?via=MCP+token&outside=1"}, headers=HOST)
    assert resp.headers["Location"].startswith("/changes?outside=1&undone=") and "via=MCP+token" in resp.headers["Location"]


def test_the_name_is_the_connections_not_the_callers(client, wiki_dir, connected):
    """An argument can't name the assistant, and neither can a summary's line breaks."""
    resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "write_page", "arguments": {
            "slug": "shed", "content": "# Shed\n", "\x00via": "Gemini", "summary": "tidy\n\nAssistant: Gemini"}}})
    assert not resp.get_json()["result"]["isError"]
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == \
        "Create shed: tidy Assistant: Gemini (MCP)\n\nAssistant: MCP token"
    assert badges(client)[0] == "assistant"
    # Nor can a client call itself what a token is called.
    from tests import test_oauth
    _, tokens = test_oauth.connect(client, "alice-pw", name="MCP Token")
    tool(client, "append_to_page", token=tokens["access_token"], slug="shed", content="More.")
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip().endswith("\n\nAssistant: An MCP client")


def test_a_client_without_a_name_is_an_assistant(client, wiki_dir, monkeypatch):
    from tests import test_oauth
    monkeypatch.setattr(settings, "MCP_TOKEN", "")
    monkeypatch.setattr(settings, "BASE_URL", test_oauth.BASE)
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", "alice-token")})
    test_oauth.login(client, "alice")
    _, tokens = test_oauth.connect(client, "alice-pw", name="")
    tool(client, "write_page", token=tokens["access_token"], slug="shed", content="# Shed\n")
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Create shed (MCP)\n\nAssistant: An MCP client"
    assert badges(client)[0] == "assistant"


def test_an_upload_with_the_token_is_an_assistants(client, wiki_dir):
    """The file routes taking the MCP token are an assistant's door too: its
    upload is marked as upload_file's is (until 2026-11-20 it was a person's)."""
    resp = client.post("/api/upload", data={"file": (BytesIO(b"x"), "scan.png")},
                       headers={**HOST, "Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, resp.data
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Upload scan.png (MCP)\n\nAssistant: MCP token"
    assert badges(client)[0] == "assistant"
    client.post("/api/upload", data={"file": (BytesIO(b"y"), "mine.png")}, headers=HOST)
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Upload mine.png" and badges(client)[0] == ""


def test_what_an_assistant_wrote_is_told_by_its_trailer_alone(client, wiki_dir):
    """Regression (the audit of 2026-11-19): a commit whose first line ended
    in "(MCP)" was an assistant's, so a page a person named "notes (MCP)"
    made their save read as one, on Changes, in its History, in the tool
    history and for Undo all. The trailer each assistant's commit carries
    says it, and nothing else does."""
    save(client, "notes%20(MCP)", "# Notes\n")
    tool(client, "append_to_page", slug="notes (MCP)", content="More.")
    tool(client, "append_to_page", slug="notes (MCP)", content="Even more.")
    rows = changes(client)
    assert [(r[0], r[2]) for r in rows[:3]] == [
        ("Append to notes (MCP)", True), ("Append to notes (MCP)", True), ("Update notes (MCP)", False)]
    assert changes(client, assistant="1")[-1][0] == "Append to notes (MCP)"   # the person's isn't among them
    history = client.get("/history/notes%20(MCP)", headers=HOST).get_data(as_text=True)
    assert history.count("through an assistant") == 2
    listed = tool(client, "history")
    assert "| Update notes (MCP) | notes (MCP)" in listed and "| Append to notes (MCP) (through an assistant) |" in listed
    # Undo all takes the assistant's run, not the person's save before it.
    run = offers(client)[0][2]
    assert len(run) == 2
    # A commit made by hand whose first line ends so is no assistant's either;
    # one with the trailer says what its author wrote, as its author does.
    for name, message, by in (("a", "Add a (MCP)", ""), ("b", "Add b\n\nAssistant: Claude", "through Claude")):
        (wiki_dir / f"{name}.md").write_text("# X\n")
        git(wiki_dir, "add", f"{name}.md")
        git(wiki_dir, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", message)
        assert badges(client)[0] == by


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
    assert rows["Rename shed to barn"] == [] and rows["Update shed"] == [] and rows["Initial import"] == []
    assert len(rows["Create task"]) == 1                                  # a task's change is undone too


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


def test_undo_of_tasks_is_offered_where_it_can_be_done(client, wiki_dir):
    """Changes offers Undo for a change of tasks while no later change in the
    wiki's latest commits names one of its tasks (their trailers: no task
    file read), whoever changed other tasks in the same files meanwhile."""
    a = create_task(client, title="Water").get_json()
    b = create_task(client, title="Mow").get_json()
    client.put(f"/api/tasks/{a['id']}", json={"title": "Water the beds"}, headers=HOST)
    client.put(f"/api/tasks/{b['id']}", json={"title": "Mow the lawn"}, headers=HOST)
    listed = offers(client)
    assert [(what, bool(one)) for what, one, _ in listed[:4]] == [
        ("Update task", True), ("Update task", True), ("Create task", False), ("Create task", False)]
    # Undone from the list, it says which tasks are as before.
    resp = client.post("/changes/undo", data={"sha": listed[1][1]}, headers=HOST)
    said = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "Undone: the task “Water” as before." in html.unescape(said)
    # A change of tasks by hand, which names none, is offered no Undo.
    (wiki_dir / "_tasks/tasks.json").write_text((wiki_dir / "_tasks/tasks.json").read_text().replace("Mow the lawn", "Mow"))
    git(wiki_dir, "commit", "-q", "-am", "By hand")
    assert offers(client)[0] == ("By hand", [], [])
    assert offers(client)[3][1] == []                                    # nor the change it may have changed again


def test_an_assistants_run_of_task_changes_is_undone_together(client, wiki_dir):
    tool(client, "create_task", title="Order mulch")
    tid = re.search(r"Task: ([0-9a-f-]{36})", git(wiki_dir, "log", "-n1", "--format=%B")).group(1)
    tool(client, "update_task", id=tid, due="2030-03-01")
    tool(client, "update_task", id=tid, comment="From the newsletter.")
    rows = offers(client)
    assert rows[0][0] == "Comment on task" and len(rows[0][2]) == 3
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    assert "Every page, task and file they changed goes back" in body
    resp = client.post("/changes/undo", data={"sha": rows[0][2]}, headers=HOST)
    said = html.unescape(client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True))
    assert "Undone: the task “Order mulch” deleted again." in said
    assert tid not in (wiki_dir / "_tasks/tasks.json").read_text()


def test_an_undo_of_many_pages_names_a_few(client, wiki_dir):
    """An undo that takes back many pages (an import) names three of them
    and counts the rest: the sentence goes in the redirect's URL, which a
    proxy refuses past a few KB, and into an assistant's context, whose
    lines of the pages put back stop at twenty."""
    for i in range(25):
        (wiki_dir / f"p{i:02d}.md").write_text(f"# P{i}\n")
    git(wiki_dir, "add", "-A")
    git(wiki_dir, "commit", "-qm", "Many pages")
    sha = git(wiki_dir, "rev-parse", "HEAD").strip()
    resp = client.post("/changes/undo", data={"sha": sha}, headers=HOST)
    assert resp.status_code == 303 and len(resp.headers["Location"]) < 300, resp.headers["Location"]
    said = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "Undone: p00, p01, p02 and 22 more deleted again." in said
    text = tool(client, "undo_changes", versions=offers(client)[0][1])     # the undo undone: all back
    assert text.startswith("Undone: p00, p01, p02 and 22 more as before.")
    assert text.count("\nurl: ") == 20 and text.endswith("\n… and 5 more pages (history lists them)"), text
    assert all((wiki_dir / f"p{i:02d}.md").exists() for i in range(25))


def test_an_upload_is_offered_undo_and_said(client, wiki_dir):
    """An upload's change is undone as a page's (undoChangesOp): offered on
    its row, and said by its path, from the browser and to an assistant."""
    client.post("/api/upload", data={"file": (BytesIO(b"\x89PNG\r\n"), "scan.png")}, headers=HOST)
    top = offers(client)[0]
    assert top[0] == "Upload scan.png" and top[1]
    resp = client.post("/changes/undo", data={"sha": top[1]}, headers=HOST)
    said = html.unescape(client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True))
    assert "Undone: the file _assets/scan.png deleted again." in said
    assert not (wiki_dir / "_assets/scan.png").exists()
    undo_sha = offers(client)[0][1]
    assert undo_sha                                                     # the undo, which can be undone
    text = tool(client, "undo_changes", versions=undo_sha)
    assert text.startswith("Undone: the file _assets/scan.png as before.")
    assert (wiki_dir / "_assets/scan.png").read_bytes() == b"\x89PNG\r\n"


def test_her_private_upload_is_offered_to_her_alone(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "MCP_TOKEN", "")
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", ""), "bob": ("bob-pw", "")})
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    client.post("/api/upload", data={"file": (BytesIO(b"x"), "secret.png"), "page": "private/alice/diary"}, headers=HOST)
    client.post("/api/upload", data={"file": (BytesIO(b"y"), "map.png"), "page": "garden"}, headers=HOST)
    rows = offers(client)
    assert [(what, bool(one)) for what, one, _ in rows[:2]] == [("Upload map.png", True), ("Upload private/alice/_assets/secret.png", True)]
    client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    assert "secret.png" not in body
    assert [(what, bool(one)) for what, one, _ in offers(client)[:1]] == [("Upload map.png", True)]   # a shared one he may upload too


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
    assert re.fullmatch(r"Create task \(MCP\)\n\nAssistant: MCP token\nSource: a mail from Bob\nTask: [0-9a-f-]{36}",
                        git(wiki_dir, "log", "-n1", "--skip=1", "--format=%B").strip())
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    sources = [html.unescape(s) for s in re.findall(r'<span class="changes-source"[^>]*>from ([^<]*)</span>', body)]
    assert sources == ["a mail from Bob", "https://example.org/garden-news?id=7"]
    assert 'href="https://example.org' not in body                       # said, not linked: it is the assistant's word
    history = html.unescape(client.get("/history/garden", headers=HOST).get_data(as_text=True))
    assert history.count(" · from https://example.org/garden-news?id=7</span>") == 1 and history.count(" · from ") == 1
    listed = tool(client, "history", token="alice-token")
    assert "(by alice through Claude; from https://example.org/garden-news?id=7) | garden" in listed
    assert '(by alice through an assistant; from a mail from Bob) | "Order mulch" (task ' in listed
    assert "Append to garden (by alice through an assistant) | garden" in listed


def make_shared_list(client):
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    return "team"


def test_a_source_is_one_line_and_only_an_assistants(client, wiki_dir, connected):
    tool(client, "write_page", token="alice-token", slug="shed", content="# Shed\n",
         source="a page\n\nAssistant: Gemini\nSource: elsewhere " + "x" * 400)
    message = git(wiki_dir, "log", "-n1", "--format=%B").strip()
    first, _, trailers = message.partition("\n\n")
    assert first == "Create shed (MCP)" and trailers.startswith("Assistant: MCP token\nSource: a page Assistant: Gemini Source: elsewhere xxx")
    source = trailers.split("\n")[1]
    assert trailers.count("\n") == 1 and len(source) == len("Source: ") + 200
    assert badges(client)[0] == "assistant"                              # no connection named by it
    # A commit made by hand with the trailer says nothing.
    (wiki_dir / "byhand.md").write_text("# By hand\n")
    git(wiki_dir, "add", "byhand.md")
    git(wiki_dir, "-c", "user.name=alice", "-c", "user.email=a@x", "commit", "-q", "-m", "Add byhand\n\nSource: nowhere")
    assert "nowhere" not in client.get("/changes", headers=HOST).get_data(as_text=True)
    # Without one, none.
    tool(client, "append_to_page", token="alice-token", slug="shed", content="More.", source="  ")
    assert git(wiki_dir, "log", "-n1", "--format=%B").strip() == "Append to shed (MCP)\n\nAssistant: MCP token"


def test_no_name_starts_a_commit_messages_next_line(client, wiki_dir):
    """A name is put into a commit message as it is ("Update <slug>"), and a
    line break in it started the message's next lines, where git reads
    trailers: a page saved as "notes (MCP)\\n\\nAssistant: Claude" showed in
    Changes as written by an assistant through Claude. No route takes such a
    name: not for a page, an upload or a list."""
    from io import BytesIO
    from urllib.parse import quote
    head = git(wiki_dir, "log", "-n1", "--format=%H").strip()
    forged = "notes (MCP)\n\nAssistant: Claude"
    for slug in (forged, "notes\rx", "notes\x1ex", "a\\b"):
        assert save(client, quote(slug), "# Notes\n").status_code == 400
        assert client.get(f"/page/{quote(slug)}", headers=HOST).status_code == 400
    resp = client.post("/api/upload", data={"file": (BytesIO(b"x"), "photo\n\nAssistant: Claude.png")}, headers=HOST)
    assert resp.status_code == 400
    resp = client.post("/tasks/new-list", data={"name": "chores\x1ex"}, headers=HOST)      # a line break becomes a "-"
    assert "newlist_error" in resp.headers["Location"]
    assert git(wiki_dir, "log", "--format=%B", f"{head}..HEAD").count("Assistant") == 0
    assert not (wiki_dir / forged).with_suffix(".md").exists() and "through Claude" not in client.get("/changes", headers=HOST).get_data(as_text=True)


# ── Who changed a page last, in search ──────────────────────────────────────

def metas(client, q):
    """The line under each page result of /api/search, by slug."""
    return {r["slug"]: r["meta"] for r in client.get("/api/search", query_string={"q": q}, headers=HOST).get_json()
            if r.get("type") != "task"}


def by_hand(wiki_dir, slug, text, author):
    """A page written and committed in a terminal, not by Zonewiki."""
    (wiki_dir / f"{slug}.md").write_text(text)
    git(wiki_dir, "add", f"{slug}.md")
    git(wiki_dir, "-c", f"user.name={author}", "-c", f"user.email={author}@x", "commit", "-q", "-m", f"Add {slug}")


EDITED = r"edited \d{4}-\d\d-\d\d"


def test_a_search_result_says_who_changed_its_page_last(client, wiki_dir, connected):
    """A page result says who made its last change: a user, a user's
    assistant (and which, where its connection named one), whoever committed
    in a terminal, before Zonewiki's first search or after it; nobody for a
    page changed since its last commit. In the search dialog's results,
    on /search and in search_wiki."""
    by_hand(wiki_dir, "old-notes", "# Old notes\n\nwombat\n", "carol")       # before the first search
    save(client, "garden", "# Garden\n\nwombat beds\n")
    tool(client, "write_page", token=connected, slug="shed", content="# Shed\n\nwombat tools\n")
    tool(client, "write_page", token="alice-token", slug="pond", content="# Pond\n\nwombat fish\n")
    meta = metas(client, "wombat")
    assert re.fullmatch(EDITED + " by alice", meta["garden"]), meta
    assert re.fullmatch(EDITED + r" by alice through Claude", meta["shed"]), meta
    assert re.fullmatch(EDITED + " by alice's assistant", meta["pond"]), meta
    assert re.fullmatch(EDITED + " by carol", meta["old-notes"]), meta
    by_hand(wiki_dir, "new-notes", "# New notes\n\nwombat\n", "dave")        # after it
    assert metas(client, "wombat")["new-notes"].endswith(" by dave")
    page = client.get("/search", query_string={"q": "wombat"}, headers=HOST).get_data(as_text=True)
    assert re.search(r'<span class="result-meta">' + EDITED + ' by alice</span>', page)
    found = tool(client, "search_wiki", token="alice-token", query="wombat tools")
    assert re.search(r"slug: shed\nedited: \d{4}-\d\d-\d\d by alice through Claude\n", found), found
    # Changed on disk since its commit: whose that change is isn't known.
    (wiki_dir / "garden.md").write_text("# Garden\n\nwombat beds, and a path\n")
    later = time.time() + 5
    os.utime(wiki_dir / "garden.md", (later, later))
    assert re.fullmatch(EDITED, metas(client, "wombat")["garden"])


def test_without_users_only_whether_an_assistant_did(client, wiki_dir):
    save(client, "garden", "# Garden\n\nwombat\n")
    tool(client, "write_page", slug="shed", content="# Shed\n\nwombat\n")
    by_hand(wiki_dir, "notes", "# Notes\n\nwombat\n", "carol")
    meta = metas(client, "wombat")
    assert re.fullmatch(EDITED, meta["garden"]) and re.fullmatch(EDITED, meta["notes"]), meta
    assert re.fullmatch(EDITED + " by an assistant", meta["shed"]), meta
    assert "by an assistant" in tool(client, "search_wiki", query="wombat")


def test_sharing_mode_says_who_as_changes_does(client, wiki_dir, monkeypatch):
    """Sharing mode leaves out the pages not for sharing, and names who
    changed the others, as Changes and a page's history do."""
    monkeypatch.setattr(settings, "MCP_TOKEN", "")
    monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", "")})
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    save(client, "garden", "# Garden\n\nwombat\n")
    save(client, "pay", "---\ntags: [noshare]\n---\n# Pay\n\nwombat\n")
    client.set_cookie("zone-sharing", "1")
    meta = metas(client, "wombat")
    assert "pay" not in meta and meta["garden"].endswith(" by alice")
