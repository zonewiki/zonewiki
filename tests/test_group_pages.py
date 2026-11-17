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
    ("GET /groups/{group}", "GET", "/groups/dev", {}),
    ("GET /groups/{group}", "GET", "/groups/Dev", {}),
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


# ── Share… ───────────────────────────────────────────────────────────────────

def test_share_offers_the_places_a_page_can_go_to(client, wiki):
    """The page menu: a private page goes to a group you are in or to
    everyone, a group's page to a group that includes its own or to everyone;
    each choice is the ordinary rename."""
    (wiki / "private/alice/notes").mkdir(parents=True)
    (wiki / "private/alice/notes/idea.md").write_text("# Idea\n\n![a](/private/alice/_assets/a.png) and [[plans]].\n")
    (wiki / "private/alice/_assets").mkdir()
    (wiki / "private/alice/_assets/a.png").write_bytes(b"hers")
    git(wiki, "add", "-A")
    git(wiki, "commit", "-q", "-m", "Hers")
    sign_in(client, "alice")
    _set_config(wiki, lambda t: t + "\n[group product]\nincludes = dev\n\n[group other]\nincludes = design\n")
    choices = lambda slug: re.findall(r'name="new_name" value="([^"]+)"', client.get(f"/page/{slug}", headers=HOST).get_data(as_text=True))  # noqa: E731
    assert _until(lambda: "groups/product/notes/idea" in choices("private/alice/notes/idea"))
    body = client.get("/page/private/alice/notes/idea", headers=HOST).get_data(as_text=True)
    # The rename dialog's own field aside: her groups, A–Z, then everyone.
    assert choices("private/alice/notes/idea")[:3] == ["groups/dev/notes/idea", "groups/product/notes/idea", "notes/idea"]
    assert 'data-action="privacy">Share…</button>' in body and "Make shared…" not in body
    assert "<strong>@dev</strong>: alice, carol, vera" in body and "<strong>Everyone</strong> who uses this wiki" in body
    assert "moves out of your private folder" in body
    # Into the group: its members see it, with the file it shows.
    assert client.post("/rename/private/alice/notes/idea", data={"from": "page", "new_name": "groups/dev/notes/idea"},
                       headers=HOST).status_code == 302
    assert "/groups/dev/_assets/a.png" in (wiki / "groups/dev/notes/idea.md").read_text()
    assert (wiki / "groups/dev/_assets/a.png").read_bytes() == b"hers" and (wiki / "private/alice/_assets/a.png").exists()
    sign_in(client, "carol")
    assert client.get("/groups/dev/_assets/a.png", headers=HOST).data == b"hers"
    # From the group's folder: the groups that include it, and everyone.
    body = client.get("/page/groups/dev/notes/idea", headers=HOST).get_data(as_text=True)
    assert choices("groups/dev/notes/idea")[:2] == ["groups/product/notes/idea", "notes/idea"]
    assert "moves out of the folder of @dev" in body and "groups/other" not in body
    assert client.post("/rename/groups/dev/notes/idea", data={"from": "page", "new_name": "notes/idea"}, headers=HOST).status_code == 302
    assert "(/_assets/a.png)" in (wiki / "notes/idea.md").read_text() and (wiki / "_assets/a.png").read_bytes() == b"hers"
    # Shared, it goes nowhere else; a viewer, an outsider and a guide's reader are offered nothing.
    assert "Share…" not in client.get("/page/notes/idea", headers=HOST).get_data(as_text=True)
    assert "Share…" not in client.get(f"/page/{GROUP}/AGENTS", headers=HOST).get_data(as_text=True)
    sign_in(client, "vera")
    assert "Share…" not in client.get(f"/page/{DIARY}", headers=HOST).get_data(as_text=True)


def test_a_private_page_of_someone_in_no_group_keeps_make_shared(client, wiki):
    (wiki / "private/bob").mkdir(parents=True)
    (wiki / "private/bob/x.md").write_text("# X\n")
    sign_in(client, "bob")
    body = client.get("/page/private/bob/x", headers=HOST).get_data(as_text=True)
    assert "Make shared…" in body and "Share…" not in body and 'name="new_name" value="x"' in body


def test_a_note_is_filed_into_a_groups_folder(client, wiki):
    """Move to wiki offers the folders of your groups, also one with no
    page in it yet; the note is the group's from then on."""
    sign_in(client, "alice")
    _set_config(wiki, lambda t: t.replace("groups = dev\n", "groups = dev, fresh\n", 1))
    note = client.post("/api/notes", data={"title": "Standup"}, headers=HOST).get_json()["slug"]
    folders = lambda: re.findall(r'<option value="([^"]*)"', client.get(f"/page/{note}", headers=HOST).get_data(as_text=True))  # noqa: E731
    assert _until(lambda: "groups/fresh/" in folders())
    assert "groups/dev/" in folders() and "groups/" not in folders() and "groups/ops/" not in folders()
    resp = client.post(f"/promote/{note}", data={"folder": "groups/fresh", "name": "standup"}, headers=HOST)
    assert resp.status_code == 302 and (wiki / "groups/fresh/standup.md").exists()
    sign_in(client, "carol")            # in dev, not in fresh
    assert client.get("/page/groups/fresh/standup", headers=HOST).status_code == 404
    sign_in(client, "bob")
    assert "groups/" not in " ".join(re.findall(r'<option value="([^"]*)"', client.get(
        "/page/" + client.post("/api/notes", data={"title": "His"}, headers=HOST).get_json()["slug"], headers=HOST).get_data(as_text=True)))


# ── Links and new pages ──────────────────────────────────────────────────────

def test_a_link_is_marked_where_not_every_reader_of_the_page_sees_its_target(client, wiki):
    """For who does see it: a 🔒 and the words that the others see plain
    text. From a shared page to a group's, from a group's to a private one
    or to another group's; not within the group, and not into a group that
    includes it."""
    (wiki / "private/alice").mkdir(parents=True)
    (wiki / "private/alice/own.md").write_text("# Own\n")
    (wiki / "groups/product").mkdir()
    (wiki / "groups/product/wide.md").write_text("# Wide\n")
    (wiki / "groups/ops").mkdir()
    (wiki / "groups/ops/other.md").write_text("# Other\n")
    sign_in(client, "alice")
    _set_config(wiki, lambda t: t.replace("groups = dev\n", "groups = dev, ops\n", 1) + "\n[group product]\nincludes = dev\n")
    links = "[[groups/dev/diary]] [[private/alice/own]] [[groups/product/wide]] [[groups/ops/other]] [[plans]] [[Brand New]]"

    def marks(page):
        html = client.post("/api/preview", query_string={"page": page} if page else {}, data=links, headers=HOST).get_data(as_text=True)
        return {m[0].rsplit("/", 1)[-1].split("?")[0]: (m[1].replace("wikilink", "").strip(), m[2])
                for m in re.findall(r'<a href="/page/([^"]+)" class="([^"]+)"(?: title="([^"]*)")?', html)}
    assert _until(lambda: "wide" in marks("groups/dev/standup") and "other" in marks("groups/dev/standup"))
    plain, private = ("", ""), ("-private", "Links to a private page: others see plain text")
    of = lambda g: ("-private", f"Links to a page of @{g}: others see plain text")  # noqa: E731
    new = ("-missing -shared-new", "Creates a shared page")
    assert marks("plans") == {"diary": of("dev"), "own": private, "wide": of("product"), "other": of("ops"), "plans": plain,
                              "brand-new": ("-missing", "")}
    assert set(marks("").values()) == {plain, ("-missing", "")}              # text on no page (a task's): nothing is marked
    assert marks("groups/dev/standup") == {"diary": plain, "own": private, "wide": plain, "other": of("ops"), "plans": plain,
                                           "brand-new": new}
    assert marks("groups/product/x") == {"diary": of("dev"), "own": private, "wide": plain, "other": of("ops"), "plans": plain,
                                         "brand-new": new}
    assert marks("private/alice/x") == {"diary": plain, "own": plain, "wide": plain, "other": plain, "plans": plain, "brand-new": new}
    # The new page's link says where it was, and the editor offers the page in that folder.
    html = client.post("/api/preview", query_string={"page": "groups/dev/standup"}, data="[[Brand New]]", headers=HOST).get_data(as_text=True)
    assert 'href="/page/brand-new?from=group:dev"' in html
    assert client.get("/page/brand-new?from=group:dev", headers=HOST).headers["Location"] == "/edit/brand-new?from=group:dev"
    body = client.get("/edit/brand-new?from=group:dev", headers=HOST).get_data(as_text=True)
    assert "For a page of @dev, link to <code>groups/dev/brand-new</code> instead" in body
    for url in ("/edit/brand-new?from=group:design", "/edit/brand-new?from=group:", "/edit/plans?from=group:dev",
                "/edit/groups/dev/brand-new?from=group:dev"):                # not her group; no group; a page that is there; the group's already
        assert "shared-new-banner" not in client.get(url, headers=HOST).get_data(as_text=True), url
    assert client.get("/page/brand-new?from=group:Dev%20x", headers=HOST).headers["Location"] == "/edit/brand-new"
    sign_in(client, "bob")              # to who doesn't see them, the targets are words
    html = client.post("/api/preview", data=links, headers=HOST).get_data(as_text=True)
    assert html.count('class="wikilink-hidden"') == 4 and 'href="/page/groups' not in html and 'href="/page/private' not in html


def test_new_page_asks_whom_it_is_for(client, wiki):
    """/new, the form without JavaScript: someone in a group chooses
    everyone, one of their groups or only themselves; the page is made in
    that folder."""
    sign_in(client, "alice")
    form = client.get("/new", headers=HOST).get_data(as_text=True)
    select = re.search(r'<select id="page-for" name="for">(.*?)</select>', form, re.S).group(1)
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "group:dev", "private"] and 'name="private"' not in form
    assert client.post("/new", data={"slug": "Retro Notes", "for": "group:dev"}, headers=HOST).headers["Location"] == "/edit/groups/dev/retro-notes"
    assert (wiki / "groups/dev/retro-notes.md").exists()
    assert client.post("/new", data={"slug": "Mine", "for": "private"}, headers=HOST).headers["Location"] == "/edit/private/alice/mine"
    assert client.post("/new", data={"slug": "Open", "for": ""}, headers=HOST).headers["Location"] == "/edit/open"
    # A group she isn't in, or no group's name: the page is a shared one, as typed.
    assert client.post("/new", data={"slug": "Sneak", "for": "group:ops"}, headers=HOST).headers["Location"] == "/edit/sneak"
    assert not (wiki / "groups/ops").exists()
    sign_in(client, "bob")              # in no group: the Private box, as before
    form = client.get("/new", headers=HOST).get_data(as_text=True)
    assert 'name="private"' in form and 'name="for"' not in form
    assert client.post("/new", data={"slug": "X", "for": "group:dev"}, headers=HOST).headers["Location"] == "/edit/x"
    assert not (wiki / "groups/dev/x.md").exists()


# ── The way in, and the marks ────────────────────────────────────────────────

def test_a_groups_home_is_made_by_a_member_and_stays(client, wiki):
    """/groups/dev opens groups/dev/index, made on the first visit of
    someone in the group who may write; to anyone else it is a 404 that
    makes nothing; the home is not renamed or deleted."""
    for who in OUTSIDERS + ("vera",):       # vera is in the group, and a viewer: she makes nothing
        sign_in(client, who)
        assert client.get("/groups/dev", headers=HOST).status_code == 404
        assert client.get("/groups", headers=HOST).status_code == (302 if who == "vera" else 404)
    assert not (wiki / GROUP / "index.md").exists()
    sign_in(client, "carol")
    resp = client.get("/groups/dev", headers=HOST)
    assert resp.status_code == 302 and resp.headers["Location"] == "/page/groups/dev/index"
    text = (wiki / GROUP / "index.md").read_text()
    assert text.startswith("# dev\n") and "whoever is in the group @dev" in text and "## Links" in text
    assert git(wiki, "log", "-n1", "--format=%an|%s") == "carol|Create groups/dev/index"
    assert client.get("/groups/dev", headers=HOST).headers["Location"] == "/page/groups/dev/index"     # there: nothing made
    assert git(wiki, "log", "-n1", "--format=%s") == "Create groups/dev/index"
    assert client.get("/groups", headers=HOST).headers["Location"] == "/groups/dev"                    # her one group
    body = client.get("/page/groups/dev/index", headers=HOST).get_data(as_text=True)
    assert "Rename…" not in body and "Share…" not in body
    assert client.post("/delete/groups/dev/index", headers=HOST).status_code == 400
    assert client.post("/rename/groups/dev/index", data={"new_name": "groups/dev/start"}, headers=HOST).status_code == 400
    assert "can't be" in rename(client, "carol", "groups/dev/index", "groups/dev/start") and (wiki / GROUP / "index.md").exists()
    sign_in(client, "vera")                 # now it is there, she reads it
    assert client.get("/groups/dev", headers=HOST).headers["Location"] == "/page/groups/dev/index"
    for bad in ("/groups/nobody", "/groups/Dev", "/groups/dev%2F..", "/groups/_x"):
        assert client.get(bad, headers=HOST).status_code in (400, 404), bad


def crumbs(html):
    crumb = re.search(r'<div class="breadcrumb".*?</div>', html, re.S).group(0)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", crumb.split("</nav>", 1)[1])).strip()


def test_the_place_is_named_not_the_path(client, wiki):
    """The mark beside the title, the breadcrumb, the page list, a search
    result and the switcher say whose a page is: a group's (👥 and the
    group), yours alone (🔒), or the wiki's (nothing)."""
    (wiki / "private/alice/notes").mkdir(parents=True)
    (wiki / "private/alice/notes/own.md").write_text(f"# Own\n\n{QUERY}\n")
    sign_in(client, "alice")
    body = client.get(f"/page/{DIARY}", headers=HOST).get_data(as_text=True)
    assert ('<a class="lock group-mark" href="/groups/dev" title="@dev: alice, carol and vera see this page" '
            'aria-label="A page of the group dev">👥 <span class="group-mark-name">dev</span></a>') in body and 'title="Private: only you see this page"' not in body
    assert crumbs(body).startswith(f"› 👥 dev › Diary {SECRET}")                 # then the backlinks' chip
    assert '<a href="/groups/dev">👥 dev</a>' in body
    assert crumbs(client.get(f"/page/{GROUP}/deck", headers=HOST).get_data(as_text=True)).startswith("› 👥 dev › Tasks")
    body = client.get("/page/private/alice/notes/own", headers=HOST).get_data(as_text=True)
    assert crumbs(body).startswith("› 🔒 Private › Notes › Own") and '<a href="/private">🔒 Private</a>' in body
    assert "group-mark" not in body and 'title="Private: only you see this page"' in body
    plans = client.get("/page/plans", headers=HOST).get_data(as_text=True)
    assert crumbs(plans).startswith("› Plans") and "group-mark" not in plans
    listed = {p["slug"]: (p.get("private"), p.get("group")) for p in client.get("/api/pages-list", headers=HOST).get_json()}
    assert listed[DIARY] == (False, "dev") and listed["private/alice/notes/own"] == (True, "") and listed["plans"] == (False, "")
    results = client.get("/search", query_string={"q": QUERY}, headers=HOST).get_data(as_text=True)
    assert "👥 groups/dev" in results and "🔒 private/alice/notes" in results
    # The switcher: Groups, between Wiki and Private; on a group's page it is where you are.
    nav = lambda html: re.findall(r'data-module="(\w+)"[^>]*?(aria-current="page")?[ >]', html.split('class="breadcrumb-home', 1)[1].split("</nav>", 1)[0])  # noqa: E731
    body = client.get(f"/page/{DIARY}", headers=HOST).get_data(as_text=True)
    assert [m for m, _ in nav(body)] == ["wiki", "groups", "private", "tasks", "notes"]
    assert 'href="/groups/dev" class="module-link" data-module="groups" title="@dev: alice, carol and vera see its pages" aria-current="page"' in body
    assert 'data-module="groups"' in plans and 'data-module="groups" title="@dev: alice, carol and vera see its pages" aria-current' not in plans
    # In several groups, Groups goes to the list of them.
    _set_config(wiki, lambda t: t.replace("groups = dev\n", "groups = dev, ops\n", 1) + "\n[group product]\nincludes = dev\n")
    assert _until(lambda: 'href="/groups" class="module-link" data-module="groups" title="Your groups: dev, ops, product"' in client.get(
        "/page/plans", headers=HOST).get_data(as_text=True))
    body = client.get("/groups", headers=HOST).get_data(as_text=True)
    assert re.findall(r'<a href="/groups/([\w-]+)"><span class="group-name">', body) == ["dev", "ops", "product"]
    assert "alice, carol, vera" in body and "(whoever is in dev)" in body and "5 pages" in body and "no page yet" in body
    assert 'data-module="groups" title="Your groups: dev, ops, product" aria-current="page"' in body
    # Who is in no group has no Groups, and a page of a group's name tells them nothing.
    sign_in(client, "bob")
    body = client.get("/page/plans", headers=HOST).get_data(as_text=True)
    assert 'data-module="groups"' not in body and "👥" not in body
    assert "👥" not in client.get("/search", query_string={"q": QUERY}, headers=HOST).get_data(as_text=True)


def test_filing_a_note_names_your_places_first(client, wiki):
    (wiki / "projects").mkdir()
    (wiki / "projects/x.md").write_text("# X\n")
    sign_in(client, "alice")
    client.get("/private", headers=HOST)
    note = client.post("/api/notes", data={"title": "Standup"}, headers=HOST).get_json()["slug"]
    body = client.get(f"/page/{note}", headers=HOST).get_data(as_text=True)
    options = re.findall(r'<option value="([^"]*)"(?: label="([^"]*)")?>', body.split('id="promote-folders">', 1)[1].split("</datalist>", 1)[0])
    assert options == [("groups/dev/", "👥 dev"), ("private/alice/", "🔒 Private"), ("projects/", "")]


def test_the_admin_page_names_the_folders_no_one_sees(client, wiki):
    """An admin sees no page of a group they aren't in, but which folders
    there are that nobody sees, and which groups have pages."""
    (wiki / "groups/ghosts").mkdir()
    (wiki / "groups/ghosts/x.md").write_text("# X\n")
    (wiki / "private/gone").mkdir(parents=True)
    (wiki / "private/gone/y.md").write_text("# Y\n")
    sign_in(client, "dana")
    body = client.get("/admin/users", headers=HOST).get_data(as_text=True)
    assert "<strong>Seen by no one:</strong> <code>groups/ghosts</code>, <code>private/gone</code>" in body
    assert re.search(r'<td class="user-name">dev <span class="user-fact"[^>]*>pages</span></td>', body) and SECRET not in body
    # Emptying a group says what stays.
    for who in ("alice", "carol"):
        client.post(f"/admin/users/{who}/groups", data={"groups": ""}, headers=HOST)
    resp = client.post("/admin/users/vera/groups", data={"groups": ""}, headers=HOST)
    body = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "Nobody is in dev now: its pages stay in the wiki, in groups/dev/, seen by no one." in body
    assert "<code>groups/dev</code>" in body
