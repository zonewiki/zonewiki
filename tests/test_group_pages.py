"""A group's pages don't leak (docs/spec/users.md, Group pages): groups/dev/
holds the pages of the group dev. alice and carol are in it; bob is an
editor and dana an admin, neither of them in it. With a page (committed,
deleted, uncommitted), an upload and a guide there, bob and dana, signed in
or through their MCP tokens, must not see a word of them anywhere, nor
change them: to them there is no such page.

The probes are those of tests/test_privacy.py, aimed at the group's folder
instead of alice's private one, so a route or tool that gets a probe there
is probed here too."""

import json
import re
import subprocess
import time
from io import BytesIO

import pytest

from tests import settings, test_privacy
from tests.conftest import HOST

SECRET = "ocelotharp"          # in the group's pages only
QUERY = test_privacy.QUERY     # what the probes search for, and a response may echo
GROUP = "groups/dev"
DIARY = f"{GROUP}/diary"
USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol", "dana", "vera")}
OUTSIDERS = ("bob", "dana")


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout.strip()


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    """The group's folder: a diary, a page named like a note, a page with a
    task list, a guide and an upload, committed; an old page, deleted; a
    draft, not committed. The shared page plans links to the diary, which
    links back. vera is a viewer in the group."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}, "carol": {"groups": "dev"},
                                                    "dana": {"role": "admin"}, "vera": {"groups": "dev", "role": "viewer"}})
    w = git_wiki_dir
    (w / GROUP / "_assets").mkdir(parents=True)
    (w / f"{DIARY}.md").write_text(
        f"---\ntags: [{QUERY}-{SECRET}]\n---\n# Diary {SECRET}\n\n{QUERY} {SECRET} with [[plans]].\n\n- [ ] Call\n")
    (w / GROUP / "old.md").write_text(f"# Old {SECRET}\n\n{QUERY} {SECRET}\n")
    (w / GROUP / "2026-10-27_09-00-00_idea.md").write_text(f"# Idea {SECRET}\n\n{QUERY} {SECRET}\n")
    (w / GROUP / "deck.md").write_text(f"# Tasks {SECRET}\n\n[[tasks: list=team]]\n")
    (w / GROUP / "AGENTS.md").write_text(f"# How dev writes {SECRET}\n\nWrite {QUERY} {SECRET} everywhere.\n")
    (w / GROUP / "_assets/photo.png").write_bytes(SECRET.encode())
    (w / "plans.md").write_text(f"# Plans\n\nSee [[{DIARY}]] and [[{DIARY}|the diary]].\n")
    (w / "_tasks").mkdir(exist_ok=True)
    (w / "_tasks/lists.json").write_text('{\n  "version": 3,\n  "lists": [\n    {"name": "team"}\n  ]\n}\n')
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "The group's pages")
    git(w, "rm", "-q", f"{GROUP}/old.md")
    git(w, "commit", "-q", "-m", f"Delete {GROUP}/old")
    (w / GROUP / "draft.md").write_text(f"# Draft {SECRET}\n\n{QUERY} {SECRET}\n")
    return w


def sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def mcp(client, who, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {USERS[who][1]}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return "".join(c.get("text", "") for c in resp.get_json()["result"]["content"])


def rename(client, who, slug, new_slug):
    """rename_page as who's assistant, the guides of both folders read."""
    ids = sorted({g for s in (slug, new_slug) for g in re.findall(r"[\w/-]+:[0-9a-f]{8}", mcp(client, who, "read_guides", slug=s))})
    return mcp(client, who, "rename_page", slug=slug, new_slug=new_slug, guides=ids)


# ── The probes: test_privacy's, aimed at the group's folder ──────────────────

def aimed(value):
    """A probe's path or argument, with alice's private folder replaced by
    the group's (also where it is reached through another folder, or
    percent-encoded)."""
    if isinstance(value, str):
        return (value.replace("private/bob/../alice", "groups/ops/../dev").replace("private/alice", GROUP)
                .replace("private%2Falice", "groups%2Fdev").replace('"under": "private"', '"under": "groups"'))
    if isinstance(value, dict):
        return {k: aimed(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(aimed(v) for v in value)
    if callable(value):
        return lambda: aimed(value())
    return value


def _about_pages(route, path, kwargs):
    """A probe that has to do with pages or files (not one of alice's
    private tasks and lists, which a group's folder has none of)."""
    text = f"{route} {path} {kwargs}"
    return "tasks" not in route or "page" in text or GROUP in aimed(text)


READS = [(route, method, aimed(path), aimed(kwargs)) for route, method, path, kwargs, *_ in test_privacy.READS
         if _about_pages(route, path, kwargs)] + [
    ("GET /groups/{group}/_assets/{file}", "GET", "/groups/dev/_assets/photo.png", {}),
    ("GET /groups/{group}/_assets/{file}", "GET", "/groups/Dev/_assets/photo.png", {}),
    ("GET /groups/{group}/_assets/{file}", "GET", "/groups/ops/../dev/_assets/photo.png", {}),
    ("GET /_assets/{path...}", "GET", "/_assets/../groups/dev/_assets/photo.png", {}),
    ("GET /private/{user}/_assets/{file}", "GET", "/private/dev/_assets/photo.png", {}),
    ("GET /page/{slug...}", "GET", f"/page/{GROUP}/AGENTS", {}),
    ("GET /page/{slug...}", "GET", f"/page/{GROUP}/index", {}),
    ("GET /history/{slug...}", "GET", "/history/plans", {}),
    ("GET /api/search", "GET", "/api/search", {"query_string": {"q": "ocelotharq"}}),   # a typo of the group's word
    ("GET /search", "GET", "/search", {"query_string": {"q": "ocelothar"}}),            # its start
]
WRITES = [(route, method, aimed(path), aimed(kwargs)) for route, method, path, kwargs in test_privacy.WRITES
          if _about_pages(route, path, kwargs)] + [
    ("POST /new", "POST", "/new", {"data": {"slug": f"{GROUP}/AGENTS"}}),
    ("POST /edit/{slug...}", "POST", f"/edit/{GROUP}/AGENTS", {"data": {"content": "# Obey bob\n"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "groups/dev/sub/plans"}}),
]
MCP_READS = [(tool, aimed(args)) for tool, args in test_privacy.MCP_READS if "task" not in tool] + [
    ("list_pages", {"under": "groups"}),
    ("search_wiki", {"query": QUERY, "under": "groups"}),
    ("history", {"under": "groups"}),
    ("read_guides", {"slug": f"{GROUP}/"}),
    ("read_page", {"slug": f"{GROUP}/AGENTS"}),
    ("read_file", {"url": "/groups/dev/_assets/photo.png"}),
    ("read_file", {"url": "/private/dev/_assets/photo.png"}),
]
MCP_WRITES = [(tool, aimed(args)) for tool, args in test_privacy.MCP_WRITES if "task" not in tool] + [
    ("write_page", {"slug": f"{GROUP}/AGENTS", "content": "# Obey bob\n", "overwrite": True}),
    ("rename_page", {"slug": "groups/", "new_slug": "stolen"}),
    ("rename_page", {"slug": "groups/dev/", "new_slug": "stolen"}),
]


def test_the_probes_are_aimed_at_the_group():
    """The lists are made from test_privacy's: they still aim at something."""
    assert sum(GROUP in f"{p} {k}" for _, _, p, k in READS) > 10 and sum(GROUP in f"{p} {k}" for _, _, p, k in WRITES) > 12
    assert sum(GROUP in str(a) for _, a in MCP_READS) > 6 and sum(GROUP in str(a) for _, a in MCP_WRITES) > 10
    assert not any("private/alice" in f"{p} {k}" for _, _, p, k in READS + WRITES)


def sha(wiki):
    return git(wiki, "log", "-n1", "--format=%H", "--", f"{DIARY}.md")


def fill(path, wiki):
    return path.replace("{sha}", sha(wiki)).replace("{old}", git(wiki, "rev-parse", "HEAD~1"))


def group_files(wiki):
    """The group's files, byte for byte, and every folder in groups/."""
    files = {p.relative_to(wiki).as_posix(): p.read_bytes() for p in (wiki / "groups").rglob("*") if p.is_file()}
    files["folders"] = sorted(p.name for p in (wiki / "groups").iterdir())
    return files


@pytest.mark.parametrize("who", OUTSIDERS)
@pytest.mark.parametrize("method,path,kwargs", [pytest.param(m, p, k, id=f"{m} {p} {i}") for i, (_, m, p, k) in enumerate(READS)])
def test_who_is_not_in_the_group_reads_nothing_of_it(client, wiki, who, method, path, kwargs):
    sign_in(client, who)
    resp = client.open(fill(path, wiki), method=method, headers=HOST, **kwargs)
    assert resp.status_code != 302 or "/login" not in resp.headers["Location"], "not signed in"
    body = resp.data.decode(errors="replace")
    assert SECRET not in body
    assert not re.search(r'href="[^"]*groups/dev', body), "a link to the group's page"
    assert "ocelot" not in body.replace("ocelotharq", "").replace("ocelothar", ""), "a word of the group's, suggested"
    if GROUP in path or "/groups/" in path or "/private/dev/" in path:
        assert resp.status_code in (404, 400)       # 400: a path with ".." the routes don't take


@pytest.mark.parametrize("who", OUTSIDERS)
@pytest.mark.parametrize("method,path,kwargs", [pytest.param(m, p, k, id=f"{m} {p} {i}") for i, (_, m, p, k) in enumerate(WRITES)])
def test_who_is_not_in_the_group_changes_nothing_of_it(client, wiki, who, method, path, kwargs):
    sign_in(client, who)
    if "{bobs}" in path:   # their own note, aiming at the group's pages from there
        path = path.replace("{bobs}", client.post("/api/notes", data={"title": "Mine"}, headers=HOST).get_json()["slug"])
    kwargs = {k: v() if callable(v) else v for k, v in kwargs.items()}
    before, log = group_files(wiki), git(wiki, "log", "--format=%H")
    resp = client.open(fill(path, wiki), method=method, headers=HOST, **kwargs)
    assert group_files(wiki) == before
    assert SECRET not in resp.data.decode(errors="replace")
    for commit in git(wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split():
        # Zone's own commit of what lay uncommitted there is no change of theirs.
        stat, author = git(wiki, "show", "--stat", "--format=", commit), git(wiki, "show", "-s", "--format=%an", commit)
        assert GROUP not in stat or author not in OUTSIDERS, stat


@pytest.mark.parametrize("who", OUTSIDERS)
@pytest.mark.parametrize("tool,args", [pytest.param(t, a, id=f"{t} {i}") for i, (t, a) in enumerate(MCP_READS)])
def test_their_assistants_read_nothing_of_it(client, wiki, who, tool, args):
    text = mcp(client, who, tool, **{k: fill(v, wiki) if isinstance(v, str) else v for k, v in args.items()})
    # The slug asked for, and the shared page's own links, may name the diary.
    named = text.replace(args.get("slug", "\0"), "").replace(args.get("url", "\0"), "").replace(f"[[{DIARY}", "")
    assert SECRET not in text and "groups/dev" not in named
    assert sha(wiki)[:7] not in text or tool == "history" and not args, "the diary's versions"


@pytest.mark.parametrize("who", OUTSIDERS)
@pytest.mark.parametrize("tool,args", [pytest.param(t, a, id=f"{t} {i}") for i, (t, a) in enumerate(MCP_WRITES)])
def test_their_assistants_change_nothing_of_it(client, wiki, who, tool, args):
    if "{bobs}" in args.values():
        mine = mcp(client, who, "write_page", note=True, slug="Mine", content="Mine\n")
        args = {k: re.search(r"note (\S+)", mine)[1] if v == "{bobs}" else v for k, v in args.items()}
    args = {k: fill(v, wiki) if isinstance(v, str) else v for k, v in args.items()}
    before, log = group_files(wiki), git(wiki, "log", "--format=%H")
    text = mcp(client, who, tool, **args)
    assert group_files(wiki) == before, text
    assert SECRET not in text
    for commit in git(wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split():
        stat, author = git(wiki, "show", "--stat", "--format=", commit), git(wiki, "show", "-s", "--format=%an", commit)
        assert GROUP not in stat or author not in OUTSIDERS, stat


def test_a_visitor_and_a_stranger_see_none_of_it(client, wiki):
    client.post("/logout", headers=HOST)
    for path in (f"/page/{DIARY}", "/groups/dev/_assets/photo.png", "/api/search?q=" + QUERY):
        resp = client.get(path, headers=HOST)
        assert resp.status_code in (302, 401, 404) and SECRET not in resp.get_data(as_text=True)


# ── Its members ──────────────────────────────────────────────────────────────

def test_its_members_see_it_and_change_it(client, wiki):
    sign_in(client, "carol")
    page = client.get(f"/page/{DIARY}", headers=HOST)
    assert page.status_code == 200 and SECRET in page.get_data(as_text=True)
    assert 'href="/page/groups/dev/diary"' in client.get("/page/plans", headers=HOST).get_data(as_text=True)
    assert "Diary" in client.get("/api/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)
    assert DIARY in [p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()]
    assert client.get("/groups/dev/_assets/photo.png", headers=HOST).data == SECRET.encode()
    assert client.get("/groups/dev/_assets/photo.png", headers={**HOST, "Authorization": "Bearer carol-token"}).data == SECRET.encode()
    assert "The group" in client.get(f"/history/{DIARY}", headers=HOST).get_data(as_text=True)
    assert "groups/dev/draft" in client.get("/uncommitted", headers=HOST).get_data(as_text=True)
    assert "groups/dev/old" in client.get("/page/_deleted", headers=HOST).get_data(as_text=True)
    # What alice wrote, carol changes: a commit in her name.
    resp = client.post(f"/edit/{DIARY}", data={"content": f"# Diary {SECRET}\n\nCarol was here.\n"}, headers=HOST)
    assert resp.status_code in (200, 302)
    assert "Carol was here" in (wiki / f"{DIARY}.md").read_text()
    assert client.post("/new", data={"slug": f"{GROUP}/retro"}, headers=HOST).status_code == 302
    assert SECRET in mcp(client, "carol", "read_page", slug=DIARY)
    assert SECRET in mcp(client, "carol", "search_wiki", query=SECRET)
    assert "Uploaded" in mcp(client, "carol", "upload_file", filename="notes.txt", text="hers", page=DIARY)
    assert (wiki / GROUP / "_assets/notes.txt").read_text() == "hers"
    out = mcp(client, "carol", "write_page", slug=f"{GROUP}/howto", content="# How to\n\nBy carol.\n",
              guides=[g for g in re.findall(r"[\w/-]+:[0-9a-f]{8}", mcp(client, "carol", "read_guides", slug=f"{GROUP}/howto"))])
    assert (wiki / GROUP / "howto.md").exists(), out
    sign_in(client, "alice")
    assert "Carol was here" in client.get(f"/page/{DIARY}", headers=HOST).get_data(as_text=True)


def test_a_viewer_in_the_group_reads_and_changes_nothing(client, wiki):
    sign_in(client, "vera")
    assert SECRET in client.get(f"/page/{DIARY}", headers=HOST).get_data(as_text=True)
    before = group_files(wiki)
    assert client.post(f"/edit/{DIARY}", data={"content": "# Vera\n"}, headers=HOST).status_code == 403
    assert client.post("/new", data={"slug": f"{GROUP}/veras"}, headers=HOST).status_code in (302, 403)
    assert group_files(wiki) == before


def test_an_upload_for_a_groups_page_is_the_groups(client, wiki):
    sign_in(client, "alice")
    resp = client.post("/api/upload", data={"file": (BytesIO(b"plan"), "plan.png"), "page": DIARY}, headers=HOST)
    assert resp.get_json() == {"url": "/groups/dev/_assets/plan.png"}
    assert (wiki / GROUP / "_assets/plan.png").read_bytes() == b"plan" and not (wiki / "_assets/plan.png").exists()
    assert git(wiki, "log", "-n1", "--format=%an|%s") == "alice|Upload groups/dev/_assets/plan.png"
    assert client.get("/groups/dev/_assets/plan.png", headers=HOST).data == b"plan"
    assert client.get("/groups/dev/sub/plan.png", headers=HOST).status_code == 404
    # A file for a page of a group she isn't in goes with the shared ones, as for another user's page.
    resp = client.post("/api/upload", data={"file": (BytesIO(b"x"), "x.png"), "page": "groups/ops/y"}, headers=HOST)
    assert resp.get_json() == {"url": "/_assets/x.png"}
    for who in OUTSIDERS:
        sign_in(client, who)
        assert client.get("/groups/dev/_assets/plan.png", headers=HOST).status_code == 404
        token = {**HOST, "Authorization": f"Bearer {USERS[who][1]}"}
        client.post("/logout", headers=HOST)
        assert client.get("/groups/dev/_assets/plan.png", headers=token).status_code == 404
        resp = client.post("/api/upload", data={"file": (BytesIO(b"theirs"), "plan.png"), "page": DIARY}, headers=token)
        assert "/groups/" not in resp.get_data(as_text=True)
        assert (wiki / GROUP / "_assets/plan.png").read_bytes() == b"plan"
        assert "No such file" in mcp(client, who, "read_file", url="/groups/dev/_assets/plan.png")
    assert "plan.png (image/png" in mcp(client, "carol", "read_file", url="/groups/dev/_assets/plan.png")
    assert "only who is in @dev" in mcp(client, "carol", "upload_file", filename="c.txt", text="c", page=DIARY)


def _set_config(wiki, change):
    config = wiki / ".zone" / "config"
    config.write_text(change(config.read_text()))


def _until(check):
    for _ in range(60):       # the settings are taken within the second
        if check():
            return True
        time.sleep(0.1)
    return check()


def test_who_leaves_the_group_no_longer_sees_it_and_who_joins_does(client, wiki):
    sign_in(client, "alice")
    assert client.get(f"/page/{DIARY}", headers=HOST).status_code == 200
    _set_config(wiki, lambda t: t.replace("[user alice]\n", "[user alice]\n# was in dev\n", 1).replace("groups = dev\n", "", 1))
    assert _until(lambda: client.get(f"/page/{DIARY}", headers=HOST).status_code == 404)       # no restart
    assert "Diary" not in client.get("/api/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)
    assert client.get("/groups/dev/_assets/photo.png", headers=HOST).status_code == 404
    sign_in(client, "bob")
    _set_config(wiki, lambda t: t.replace("[user bob]\n", "[user bob]\ngroups = dev\n"))
    assert _until(lambda: client.get(f"/page/{DIARY}", headers=HOST).status_code == 200)
    assert "Diary" in client.get("/api/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)
    # Disabled, a member is none: their token reads nothing.
    _set_config(wiki, lambda t: t.replace("[user carol]\n", "[user carol]\ndisabled = yes\n"))
    assert _until(lambda: SECRET not in mcp_text(client, "carol", "read_page", slug=DIARY))


def mcp_text(client, who, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {USERS[who][1]}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return resp.get_data(as_text=True)


def test_a_group_made_of_groups_has_a_folder_too(client, wiki, monkeypatch):
    """groups/product/ is for whoever is in product, by whatever way:
    alice is in dev, which product includes."""
    (wiki / "groups/product").mkdir()
    (wiki / "groups/product/roadmap.md").write_text(f"# Roadmap {SECRET}\n")
    sign_in(client, "alice")            # the server is up: it takes the change below
    _set_config(wiki, lambda t: t.replace("[user bob]\n", "[user bob]\ngroups = design\n") + "\n[group product]\nincludes = dev, design\n")
    assert _until(lambda: client.get("/page/groups/product/roadmap", headers=HOST).status_code == 200), client.live.log.read_text()[-1500:]
    sign_in(client, "bob")              # in design: in product, not in dev
    assert client.get("/page/groups/product/roadmap", headers=HOST).status_code == 200
    assert client.get(f"/page/{DIARY}", headers=HOST).status_code == 404
    sign_in(client, "dana")
    assert client.get("/page/groups/product/roadmap", headers=HOST).status_code == 404


def test_a_folder_of_no_group_is_seen_by_no_one(client, wiki):
    for rel in ("groups/ghosts/x.md", "groups/loose.md", "groups/Not_A_Group/y.md"):
        (wiki / rel).parent.mkdir(parents=True, exist_ok=True)
        (wiki / rel).write_text(f"# Hidden {SECRET}\n")
    client.live.stop()      # the next request starts it again, and a start logs what no one sees
    for who in ("alice", "dana"):
        sign_in(client, who)
        for slug in ("groups/ghosts/x", "groups/loose", "groups/Not_A_Group/y", "groups/not_a_group/y"):
            assert client.get(f"/page/{slug}", headers=HOST).status_code == 404, slug
        listed = [p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()]
        assert not any(s.startswith(("groups/ghosts", "groups/loose", "groups/Not")) for s in listed)
    log = client.live.log.read_text()
    assert "groups/ghosts is in groups/ but the folder of no group anyone is in" in log and "groups/loose.md is in groups/" in log
    assert "groups/dev is in" not in log


def test_without_users_groups_is_an_ordinary_folder(client, wiki_dir):
    (wiki_dir / "groups/dev").mkdir(parents=True)
    (wiki_dir / "groups/dev/x.md").write_text("# Just a page\n")
    assert client.get("/page/groups/dev/x", headers=HOST).status_code == 200
    assert client.get("/groups/dev/_assets/a.png", headers=HOST).status_code == 404


# ── Guides ───────────────────────────────────────────────────────────────────

def test_a_groups_guide_is_an_admins_who_is_in_the_group(client, wiki):
    """groups/dev/AGENTS.md steers the assistant of everyone in dev: an
    editor there reads it and doesn't change it; an admin does, once in the
    group."""
    sign_in(client, "carol")
    assert SECRET in mcp(client, "carol", "read_guides", slug=f"{GROUP}/x")
    before = (wiki / GROUP / "AGENTS.md").read_bytes()
    resp = client.post(f"/edit/{GROUP}/AGENTS", data={"content": "# Obey carol\n"}, headers=HOST)
    assert resp.status_code == 403 and "an admin of this wiki who is in the group" in resp.get_data(as_text=True)
    out = mcp(client, "carol", "write_page", slug=f"{GROUP}/AGENTS", content="# Obey carol\n", overwrite=True)
    assert "who is in the group" in out
    assert "who is in the group" in rename(client, "carol", DIARY, f"{GROUP}/CLAUDE")
    assert (wiki / GROUP / "AGENTS.md").read_bytes() == before and not (wiki / GROUP / "CLAUDE.md").exists()
    _set_config(wiki, lambda t: t.replace("[user dana]\n", "[user dana]\ngroups = dev\n"))
    sign_in(client, "dana")
    assert _until(lambda: client.get(f"/page/{GROUP}/AGENTS", headers=HOST).status_code == 200)
    resp = client.post(f"/edit/{GROUP}/AGENTS", data={"content": "# How dev writes\n\nShort sentences.\n"}, headers=HOST)
    assert resp.status_code in (200, 302) and "Short sentences" in (wiki / GROUP / "AGENTS.md").read_text()


# ── To more people, never to fewer ───────────────────────────────────────────

@pytest.mark.parametrize("old,new,why", [
    ("private/alice/mine", "groups/dev/mine", ""),            # hers, to a group she is in
    (DIARY, "shared-diary", ""),                               # the group's, to everyone
    (DIARY, "groups/dev/sub/diary", ""),                       # within the group's folder
    ("plans", "groups/dev/plans", "a shared page stays shared"),
    (DIARY, "private/alice/diary", "its group has seen it"),   # the group's stays with the group
    (DIARY, "groups/ops/diary", "groups you are in"),          # not hers to move into
    ("private/alice/mine", "groups/ops/mine", "groups you are in"),
])
def test_a_page_moves_to_more_people_never_to_fewer(client, wiki, old, new, why):
    ok = not why
    (wiki / "private/alice").mkdir(parents=True)
    (wiki / "private/alice/mine.md").write_text("# Mine\n")
    git(wiki, "add", "-A")
    git(wiki, "commit", "-q", "-m", "Hers")
    sign_in(client, "alice")
    resp = client.post(f"/rename/{old}", data={"new_name": new}, headers=HOST)
    assert (wiki / f"{new}.md").exists() == ok and (wiki / f"{old}.md").exists() != ok, resp.get_data(as_text=True)[:300]
    if not ok:
        out = rename(client, "alice", old, new)
        assert why in out and not (wiki / f"{new}.md").exists() and (wiki / f"{old}.md").exists(), out


def test_a_group_s_page_moves_to_a_group_that_includes_it(client, wiki):
    sign_in(client, "alice")
    _set_config(wiki, lambda t: t.replace("groups = dev\n", "groups = dev, ops\n", 1) + "\n[group product]\nincludes = dev\n")   # alice's
    moved = wiki / "groups/product/diary.md"
    out = []
    assert _until(lambda: moved.exists() or (out.append(rename(client, "alice", DIARY, "groups/product/diary")), moved.exists())[1]), out[-1]
    # Back is to fewer people; so is a group that merely has her in it too.
    out = rename(client, "alice", "groups/product/diary", DIARY)
    assert "its group has seen it" in out and (wiki / "groups/product/diary.md").exists()
    out = rename(client, "alice", "groups/product/diary", "groups/ops/diary")
    assert "its group has seen it" in out and not (wiki / "groups/ops").exists()


# ── History ──────────────────────────────────────────────────────────────────

def test_the_history_names_no_page_of_the_group_to_anyone_else(client, wiki):
    before = sha(wiki)
    sign_in(client, "alice")
    assert client.post(f"/rename/{DIARY}", data={"new_name": "open-diary"}, headers=HOST).status_code == 302
    sign_in(client, "bob")
    body = client.get("/history/open-diary", headers=HOST).get_data(as_text=True)
    assert "groups/dev" not in body and "a group&#39;s page" in body
    assert "groups/dev" not in mcp(client, "bob", "history", slug="open-diary")
    # The versions from when it was the group's are the group's.
    assert before[:7] not in body
    sign_in(client, "carol")
    assert "groups/dev/diary" in client.get("/history/open-diary", headers=HOST).get_data(as_text=True)


def test_a_write_of_someone_else_commits_what_lay_there_as_zones(client, wiki):
    """The draft in the group's folder has no known author. bob's rename of
    a shared page re-points the diary's link in a commit of his: what lay
    uncommitted in the group's folder is committed first, as Zone's, so his
    commit carries nothing he doesn't see."""
    sign_in(client, "bob")
    assert client.post("/rename/plans", data={"new_name": "plan"}, headers=HOST).status_code == 302
    assert "[[plan]]" in (wiki / f"{DIARY}.md").read_text()
    log = git(wiki, "log", "--format=%an|%s", "-n3")
    assert f"|Save {GROUP}/draft" in log and f"bob|Save {GROUP}/draft" not in log
    assert "draft.md" not in git(wiki, "show", "--stat", "--format=", "HEAD")
