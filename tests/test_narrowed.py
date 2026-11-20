"""A connection given less than its user sees (docs/spec/mcp.md, Connections
given less): not their private place, or only some of their groups. The
leak tests' probes (test_privacy.py, test_group_pages.py,
test_list_members.py), which hold a secret word in each kind of place and
ask every tool for it as someone else, are asked here as the owner
themselves, through a connection that wasn't given the place: the answers
must be a stranger's.
"""

import json
import re
from urllib.parse import urlencode

import pytest

from tests import settings, test_group_pages, test_list_members, test_oauth, test_privacy
from tests.conftest import HOST
from tests.test_oauth import asking, allow, exchange, register

pytestmark = pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")


def connect(client, name, password, see=None, may="write"):
    """name signs in and lets an assistant in that sees of their places only
    those in see ("private", "group:dev"; None: no choice sent, all of
    them): its access token."""
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": password}, headers=HOST).status_code == 302
    client_id = register(client)
    form = {**asking(client_id), "may": may}
    if see is not None:
        form.update(choose="1", see=see)
    resp, code = allow(client, form, password)
    assert resp.status_code == 200 and code, resp.data
    tokens = exchange(client, client_id, code).get_json()
    client.post("/logout", headers=HOST)
    return tokens["access_token"]


def call(client, access, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {access}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert resp.status_code == 200
    return "".join(c.get("text", "") for c in resp.get_json()["result"]["content"])


def ids(probes):
    return [pytest.param(t, a, id=f"{t} {i}") for i, (t, a) in enumerate(probes)]


def own_note(args):
    """A probe that starts from the asker's own note, which a connection
    without the private place can't make."""
    return "{bobs}" in args.values()


# ── Without the private place ───────────────────────────────────────────────

P = test_privacy


@pytest.fixture()
def private_wiki(git_wiki_dir, monkeypatch):
    return P.wiki.__wrapped__(git_wiki_dir, monkeypatch) if hasattr(P.wiki, "__wrapped__") else None


@pytest.fixture()
def wiki(request):
    return request.getfixturevalue("private_wiki")


@pytest.fixture()
def without_private(client, private_wiki):
    return connect(client, "alice", "alice-pw", see=[])


@pytest.mark.parametrize("tool,args", ids(P.MCP_READS))
def test_without_the_private_place_it_reads_nothing_private(client, private_wiki, without_private, tool, args):
    text = call(client, without_private, tool, **args)
    named = text.replace(args.get("slug", "\0"), "").replace(f"[[{P.DIARY}", "")
    assert P.SECRET not in text and "private/alice" not in named
    assert P.TASK_ID[:8] not in text.replace(f"[[task:{P.TASK_ID[:8]}]]", "") and "secretlist" not in text
    assert P.sha(private_wiki)[:7] not in text or tool == "history" and not args, "the diary's versions"


@pytest.mark.parametrize("tool,args", ids([p for p in P.MCP_WRITES if not own_note(p[1])]))
def test_without_the_private_place_it_changes_nothing_private(client, private_wiki, without_private, tool, args):
    args = {k: P.fill(v, private_wiki) if isinstance(v, str) else v for k, v in args.items()}
    before, log = P._alices_files(private_wiki), P.git(private_wiki, "log", "--format=%H")
    text = call(client, without_private, tool, **args)
    assert P._alices_files(private_wiki) == before, text
    for commit in P.git(private_wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split():
        assert "private/alice" not in P.git(private_wiki, "show", "--stat", "--format=", commit)
    assert P.SECRET not in text


def test_it_makes_nothing_private_and_says_why(client, private_wiki, without_private):
    """No note, no private page, no task in the Inbox: none of them would be seen by it again."""
    before = P._alices_files(private_wiki)
    for tool, args in (("write_page", {"note": True, "slug": "An idea", "content": "x\n"}),
                       ("write_page", {"slug": "private/alice/new", "content": "# New\n"}),
                       ("create_task", {"title": "Into the Inbox"}),
                       ("create_task_list", {"name": "mine", "private": True})):
        resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {without_private}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).get_json()["result"]
        assert resp["isError"], (tool, resp)
    assert P._alices_files(private_wiki) == before
    # What is shared it reads and writes as alice does.
    assert "Plans" in call(client, without_private, "read_page", slug="plans")
    call(client, without_private, "write_page", slug="shed", content="# Shed\n")
    assert (private_wiki / "shed.md").exists() and P.git(private_wiki, "log", "-n1", "--format=%an") == "alice"


def errors(client, access, tool, **args):
    """A tool's text and whether it is an error; a failure inside Zone (a 500) fails the test."""
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {access}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert resp.status_code == 200, (tool, resp.data)
    result = resp.get_json()["result"]
    return "".join(c.get("text", "") for c in result["content"]), result["isError"]


def test_ordinary_work_on_what_is_shared_goes_on(client, private_wiki, without_private):
    """Every tool does its shared work for a connection without the private
    place, and none fails inside Zone for what it can't reach."""
    a = without_private
    before = P._alices_files(private_wiki)
    for tool, args in (("search_wiki", {"query": "plans"}), ("search_tasks", {}), ("search_tasks", {"list": "team"}),
                       ("list_pages", {}), ("list_tags", {}), ("history", {}), ("check_links", {}), ("read_guides", {}),
                       ("grep_wiki", {"pattern": "Plans"}), ("zone_help", {"query": "tasks"}),
                       ("create_task", {"title": "Order chairs", "list": "team"}),
                       ("create_task_list", {"name": "errands"}),
                       ("create_task", {"title": "Buy stamps", "list": "errands", "assignee": "me"}),
                       ("write_page", {"slug": "shed", "content": "# Shed\n\n#errand\n"}),
                       ("append_to_page", {"slug": "shed", "content": "Tools."}),
                       ("upload_file", {"filename": "plan.txt", "text": "a plan", "page": "shed"}),
                       ("rename_page", {"slug": "shed", "new_slug": "barn"}),
                       ("rename_task_list", {"list": "errands", "new_name": "chores"}),
                       ("delete_page", {"slug": "barn"})):
        text, refused = errors(client, a, tool, **args)
        assert not refused, (tool, text)
        assert P.SECRET not in text and "secretlist" not in text, tool
    # Its tasks are found again, and changed; into the Inbox they don't go.
    found, _ = errors(client, a, "search_tasks", query="stamps")
    task = re.search(r"\bid: (\S+)", found)[1]
    assert not errors(client, a, "update_task", id=task, status="done")[1]
    text, refused = errors(client, a, "update_task", id=task, list="")
    assert refused, text
    text, refused = errors(client, a, "update_task", id=task, list="inbox")
    assert refused and "wasn't given your private" in text, text
    assert P._alices_files(private_wiki) == before
    # Her private upload isn't served to its token, and an upload for a private page of hers isn't kept with it.
    auth = {**HOST, "Authorization": f"Bearer {a}"}
    assert client.get("/private/alice/_assets/photo.png", headers=auth).status_code == 404
    assert client.get("/private/alice/_assets/photo.png", headers={**HOST, "Authorization": "Bearer alice-token"}).status_code == 200
    errors(client, a, "upload_file", filename="smuggled.txt", text="x", page=P.DIARY)
    assert not (private_wiki / "private/alice/_assets/smuggled.txt").exists()


def test_the_same_user_with_everything_reads_it(client, private_wiki):
    """The probes do find the secret for a connection that was given the place."""
    full = connect(client, "alice", "alice-pw")
    assert P.SECRET in call(client, full, "read_page", slug=P.DIARY)
    assert P.SECRET in call(client, full, "search_wiki", query=P.QUERY)
    chosen_all = connect(client, "alice", "alice-pw", see=["private"])
    assert P.SECRET in call(client, chosen_all, "read_page", slug=P.DIARY)


# ── Without a group ─────────────────────────────────────────────────────────

G = test_group_pages


@pytest.fixture()
def group_wiki(git_wiki_dir, monkeypatch):
    return G.wiki.__wrapped__(git_wiki_dir, monkeypatch)


@pytest.fixture()
def without_the_group(client, group_wiki):
    return connect(client, "alice", "alice-pw", see=["private"])     # alice is in dev: not given


@pytest.mark.parametrize("tool,args", ids(G.MCP_READS))
def test_without_the_group_it_reads_nothing_of_it(client, group_wiki, without_the_group, tool, args):
    text = call(client, without_the_group, tool, **{k: G.fill(v, group_wiki) if isinstance(v, str) else v for k, v in args.items()})
    named = text.replace(args.get("slug", "\0"), "").replace(args.get("url", "\0"), "").replace(f"[[{G.DIARY}", "")
    assert G.SECRET not in text and "groups/dev" not in named
    assert G.sha(group_wiki)[:7] not in text or tool == "history" and not args, "the diary's versions"


@pytest.mark.parametrize("tool,args", ids([p for p in G.MCP_WRITES if not own_note(p[1])] ))
def test_without_the_group_it_changes_nothing_of_it(client, group_wiki, without_the_group, tool, args):
    args = {k: G.fill(v, group_wiki) if isinstance(v, str) else v for k, v in args.items()}
    before, log = G.group_files(group_wiki), G.git(group_wiki, "log", "--format=%H")
    text = call(client, without_the_group, tool, **args)
    assert G.group_files(group_wiki) == before, text
    assert G.SECRET not in text
    for commit in G.git(group_wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split():
        assert G.GROUP not in G.git(group_wiki, "show", "--stat", "--format=", commit)


def test_given_the_group_it_reads_it_and_is_told_of_no_other(client, group_wiki, monkeypatch):
    monkeypatch.setattr(settings, "USER_SETTINGS", {**settings.USER_SETTINGS, "alice": {"groups": "dev, ops"}})
    only_dev = connect(client, "alice", "alice-pw", see=["group:dev"])
    assert G.SECRET in call(client, only_dev, "read_page", slug=G.DIARY)
    (group_wiki / "groups/ops").mkdir(parents=True)
    (group_wiki / "groups/ops/runbook.md").write_text("# Runbook quollfeather\n")
    assert "quollfeather" not in call(client, only_dev, "search_wiki", query="quollfeather")
    listed = call(client, only_dev, "list_pages")
    assert "groups/dev/" in listed and "groups/ops" not in listed
    # A group it wasn't given is none of its groups, in what it is told too.
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {only_dev}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).get_json()["result"]
    assert "groups/dev/" in resp["instructions"] and "ops" not in resp["instructions"]
    # All of them chosen: every group, also one joined later.
    every = connect(client, "alice", "alice-pw", see=["private", "group:dev", "group:ops"])
    assert "quollfeather" in call(client, every, "search_wiki", query="quollfeather")


# ── A list for a group ──────────────────────────────────────────────────────

L = test_list_members


@pytest.fixture()
def list_wiki(client, git_wiki_dir, monkeypatch):
    return L.wiki.__wrapped__(client, git_wiki_dir, monkeypatch)


def test_without_the_group_a_list_for_it_is_no_list(client, list_wiki):
    """carol sees the list hiring by her group; her connection without the group doesn't."""
    narrowed = connect(client, "carol", "carol-pw", see=["private"])
    for tool, args in [("search_tasks", {"query": L.SECRET}), ("search_tasks", {"list": L.LIST}), ("search_wiki", {"query": L.SECRET}),
                       ("list_tags", {}), ("read_page", {"slug": "plans"}), ("history", {}),
                       ("update_task", {"id": L.IDS["open"], "title": "Taken", "summary": "x"}),
                       ("update_task", {"id": L.IDS["shared"], "list": L.LIST, "summary": "x"}),
                       ("create_task", {"title": "Smuggled", "list": L.LIST, "summary": "x"}),
                       ("update_task", {"id": L.IDS["shared"], "after": L.IDS["open"], "summary": "x"}),
                       ("create_task", {"title": "Smuggled", "list": "team", "before": L.IDS["open"], "summary": "x"}),
                       ("rename_task_list", {"list": L.LIST, "new_name": "taken", "summary": "x"})]:
        before = L._its_files(list_wiki)
        out = call(client, narrowed, tool, **args)
        assert not any(m in out for m in L.MARKS), (tool, out)
        assert not re.search(rf"#{L.LIST}\b|list[:=]{L.LIST}\b", out) or L.LIST in json.dumps(args) or tool == "read_page", tool
        assert L._its_files(list_wiki) == before, tool
    given = connect(client, "carol", "carol-pw", see=["private", "group:hiring"])
    assert L.SECRET in call(client, given, "search_tasks", query=L.SECRET)
    # alice is named on the list herself: hers whatever groups her connection has.
    named = connect(client, "alice", "alice-pw", see=[])
    assert L.SECRET in call(client, named, "search_tasks", list=L.LIST)


# ── The question and the page ───────────────────────────────────────────────

def test_the_question_offers_the_places_and_the_page_says_what_was_given(client, group_wiki):
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    client_id = register(client)
    body = client.get("/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST).data.decode()
    assert 'name="see" value="private" checked' in body and 'name="see" value="group:dev" checked' in body
    assert 'name="choose" value="1"' in body and "The shared wiki" in body
    # A wrong password keeps what was chosen.
    resp, code = allow(client, {**asking(client_id), "choose": "1", "see": ["group:dev"]}, "wrong")
    kept = resp.data.decode()
    assert code is None and 'value="private">' in kept and 'value="group:dev" checked' in kept
    resp, code = allow(client, {**asking(client_id), "choose": "1", "see": ["private"], "may": "read"}, "alice-pw")
    assert "It sees only: the shared wiki; your private pages, notes and tasks." in resp.data.decode()
    exchange(client, client_id, code)
    grant = test_oauth.grants(group_wiki)[-1]
    assert (grant["no_private"], grant["groups"], grant["read_only"]) == (False, [], True)
    page = client.get("/assistant", headers=HOST).data.decode()
    assert 'Only read<span class="assistant-sees">Sees only: the shared wiki; your private pages, notes and tasks</span>' in page
    connect(client, "alice", "alice-pw", see=["group:dev"])
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    # dev is every group she is in: all of her groups, also one she joins later.
    assert "Sees only: the shared wiki; your groups</span>" in client.get("/assistant", headers=HOST).data.decode()
    # A group named that isn't hers is given no one.
    forged = connect(client, "alice", "alice-pw", see=["private", "group:ops", "group:dev"])
    assert test_oauth.grants(group_wiki)[-1]["groups"] is None and forged


def test_without_users_there_is_nothing_to_choose(client, git_wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "PASSWORD", "one password")
    client.post("/login", data={"password": "one password"}, headers=HOST)
    body = client.get("/oauth/authorize?" + urlencode(asking(register(client))), headers=HOST).data.decode()
    assert "What may it do?" in body and "What may it see?" not in body
