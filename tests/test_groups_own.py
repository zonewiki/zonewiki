"""Groups made in the browser (docs/spec/users.md, Groups): whoever may
write makes a group of named people on /groups and is its owner; its owners
say who is in it and what it is called. Who is in a group stays said on the
users (groups in their sections), so its folder and the lists for it work
as for a group an admin made. A rename takes the folder, the links to its
pages, the lists for it and the settings along.

alice, bob and carol are editors, dana an admin, vera a viewer; carol is in
dev, a group dana made on the host (no owners), which has a page."""

import html
import re
import subprocess
import time

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol", "dana", "vera")}


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout.strip()


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"carol": {"groups": "dev"}, "dana": {"role": "admin"}, "vera": {"role": "viewer"}})
    monkeypatch.setattr(settings, "GROUPS", {"product": "dev, design"})
    w = git_wiki_dir
    (w / "groups/dev").mkdir(parents=True)
    (w / "groups/dev/notes.md").write_text("# Dev notes\n\nonlydev\n")
    (w / "groups/ghosts").mkdir()
    (w / "groups/ghosts/old.md").write_text("# Left behind\n")
    (w / "plans.md").write_text("# Plans\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "The wiki")
    return w


def sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def config(wiki):
    return (wiki / ".zone/config").read_text()


def section(wiki, header):
    """The lines of a section of the settings file, without its password and token."""
    body = config(wiki).split(f"[{header}]\n", 1)[1].split("\n[", 1)[0]
    return [line for line in body.splitlines() if line and not line.startswith(("password", "mcp-token"))]


def until(check):
    for _ in range(60):       # a change of the settings waits for the requests running
        if check():
            return True
        time.sleep(0.1)
    return check()


def page(client, path="/groups"):
    return client.get(path, headers=HOST).get_data(as_text=True)


def make(client, name, people=""):
    return client.post("/groups", data={"name": name, "people": people}, headers=HOST)


def test_whoever_may_write_makes_a_group(client, wiki):
    sign_in(client, "alice")
    body = page(client)
    assert "You are in no group." in body and 'action="/groups"' in body
    assert 'class="menu-groups">Groups</a>' in body                      # the user menu's way here
    resp = make(client, "Apollo", "bob")
    assert resp.status_code == 303 and resp.headers["Location"] == "/groups?done=made&group=apollo"
    assert section(wiki, "user alice") == ["groups = apollo"] and section(wiki, "user bob") == ["groups = apollo"]
    assert section(wiki, "group apollo") == ["owners = alice"]
    body = page(client, resp.headers["Location"])
    assert "Made the group apollo" in body and "alice, bob" in body and "owned by alice" in body
    assert 'action="/groups/apollo/people"' in body and 'name="people" value="alice, bob"' in body
    # Its folder is theirs from the next request, as any group's.
    assert until(lambda: client.get("/groups/apollo", headers=HOST).status_code == 302)
    assert client.post("/api/save/groups/apollo/roadmap", data={"content": "# Roadmap\n\nonlyapollo\n"}, headers=HOST).status_code == 200
    sign_in(client, "bob")
    assert "onlyapollo" in page(client, "/page/groups/apollo/roadmap")
    body = page(client)
    assert "👥 apollo" in body and "owned by alice" in body and 'action="/groups/apollo/people"' not in body   # in it, not its owner
    sign_in(client, "carol")
    assert client.get("/page/groups/apollo/roadmap", headers=HOST).status_code == 404
    assert "apollo" not in page(client) and "👥 dev" in page(client)
    assert 'action="/groups/dev/people"' not in page(client)             # dev has no owner: an admin's


@pytest.mark.parametrize("name,people,says", [
    ("dev", "", "is taken"),              # a group there is
    ("product", "", "is taken"),          # one made of groups
    ("design", "", "is taken"),           # one a group is said to include, though nobody is in it
    ("ghosts", "", "is taken"),           # a folder in groups/ nobody sees: it would be the new group's
    ("hiring", "", "is taken"),           # a list is for it
    ("Bad Name", "", "a group's name"),
    ("_assets", "", "a group's name"),
    ("fine", "nobody-here", "not a user"),
    ("fine", "bob, x!y", "not a user"),
])
def test_a_group_is_not_made_of_a_name_that_is_taken(client, wiki, name, people, says):
    sign_in(client, "dana")
    assert client.post("/tasks/new-list", data={"name": "hires", "for": "@hiring, dana"}, headers=HOST).status_code == 302
    sign_in(client, "alice")
    before = config(wiki)
    resp = make(client, name, people)
    assert resp.status_code in (400, 409) and says in html.unescape(resp.get_data(as_text=True)), resp.status_code
    assert config(wiki) == before


def test_a_viewer_makes_no_group_and_a_wiki_without_users_has_none(client, wiki, monkeypatch):
    sign_in(client, "vera")
    before = config(wiki)
    assert make(client, "mine").status_code == 403
    body = page(client)
    assert "You are in no group." in body and 'action="/groups"' not in body
    assert config(wiki) == before
    monkeypatch.setattr(settings, "USERS", {})
    for method, path in (("GET", "/groups"), ("POST", "/groups"), ("POST", "/groups/dev/people"), ("POST", "/groups/dev/rename")):
        assert client.open(path, method=method, data={"name": "x"}, headers=HOST).status_code == 404


def test_its_owners_say_who_is_in_it(client, wiki):
    sign_in(client, "alice")
    make(client, "apollo", "bob")
    people = lambda **data: client.post("/groups/apollo/people", data=data, headers=HOST)
    resp = people(people="alice, carol", owners="alice, carol")
    assert resp.status_code == 303
    body = page(client, resp.headers["Location"])
    assert "apollo (owners: alice, carol): alice, carol." in body and "No longer in it: bob. What is for @apollo they no longer see." in body
    assert section(wiki, "user bob") == [] and section(wiki, "user carol") == ["groups = apollo, dev"]
    assert section(wiki, "group apollo") == ["owners = alice, carol"]
    # A group keeps an owner, and its people are users.
    before = config(wiki)
    assert "keeps an owner" in people(people="alice", owners="").get_data(as_text=True)
    assert "not a user" in people(people="alice, zed", owners="alice").get_data(as_text=True)
    assert config(wiki) == before
    # Who doesn't own it changes nothing, member or not; nor a group that has no owner.
    for who in ("bob", "vera"):
        sign_in(client, who)
        assert people(people=who, owners=who).status_code in (403, 404)
    sign_in(client, "alice")
    assert client.post("/groups/dev/people", data={"people": "alice", "owners": "alice"}, headers=HOST).status_code == 404
    assert client.post("/groups/nosuch/people", data={"people": "alice", "owners": "alice"}, headers=HOST).status_code == 404
    assert config(wiki) == before
    # The other owner does; an owner who takes themselves out is out.
    sign_in(client, "carol")
    assert until(lambda: 'action="/groups/apollo/people"' in page(client))
    assert people(people="carol, bob", owners="carol").status_code == 303
    assert section(wiki, "user alice") == [] and section(wiki, "group apollo") == ["owners = carol"]
    sign_in(client, "alice")
    assert until(lambda: "apollo" not in page(client))
    # An admin says it of any group, and may leave one without an owner: it is an admin's then.
    sign_in(client, "dana")
    assert "apollo" not in page(client)                                  # not in it: not among her groups
    assert "owned by carol" in page(client, "/admin/users")
    assert people(people="bob", owners="").status_code == 303
    assert "[group apollo]" not in config(wiki) and section(wiki, "user bob") == ["groups = apollo"]
    assert client.post("/groups/dev/people", data={"people": "carol, alice", "owners": "alice"}, headers=HOST).status_code == 303
    assert section(wiki, "group dev") == ["owners = alice"] and section(wiki, "group product") == ["includes = dev, design"]


def test_a_removed_user_owns_nothing(client, wiki):
    sign_in(client, "alice")
    make(client, "apollo", "bob")
    sign_in(client, "dana")
    assert until(lambda: "owned by alice" in page(client, "/admin/users"))
    assert client.post("/admin/users/alice/remove", headers=HOST).status_code == 303
    assert "[user alice]" not in config(wiki) and "[group apollo]" not in config(wiki)
    assert section(wiki, "user bob") == ["groups = apollo"]              # the group is there, an admin's now


@pytest.fixture()
def apollo(client, wiki):
    """alice's group apollo with bob in it: a roadmap that shows a file and
    links to a page beside it, a file no page shows, a shared page and a
    task that link to the roadmap, a list for the group, and a group made
    of it."""
    sign_in(client, "alice")
    make(client, "apollo", "bob")
    assert until(lambda: client.get("/groups/apollo", headers=HOST).status_code == 302)      # its home, as Zone writes it
    save = lambda slug, text: client.post(f"/api/save/{slug}", data={"content": text}, headers=HOST)
    assert save("groups/apollo/notes", "# Notes\n").status_code == 200
    assert save("groups/apollo/roadmap", "# Roadmap\n\n![plan](/groups/apollo/_assets/plan.png) [[groups/apollo/notes]]\n").status_code == 200
    (wiki / "groups/apollo/_assets").mkdir()
    (wiki / "groups/apollo/_assets/plan.png").write_bytes(b"plan")
    (wiki / "groups/apollo/_assets/spare.png").write_bytes(b"spare")
    assert save("plans", "# Plans\n\nSee [[groups/apollo/roadmap]].\n").status_code == 200
    assert client.post("/tasks/new-list", data={"name": "launch", "for": "@apollo"}, headers=HOST).status_code == 302
    task = client.post("/api/tasks", json={"title": "Read [[groups/apollo/roadmap]]", "list": "launch"}, headers=HOST).get_json()
    sign_in(client, "dana")
    assert client.post("/admin/groups/includes", data={"group": "program", "includes": "apollo"}, headers=HOST).status_code == 303
    git(wiki, "add", "-A")
    git(wiki, "commit", "-q", "-m", "The group's files")
    sign_in(client, "alice")
    return task["id"]


def test_a_rename_takes_what_is_the_groups_along(client, wiki, apollo):
    resp = client.post("/groups/apollo/rename", data={"name": "Artemis"}, headers=HOST)
    assert resp.status_code == 303 and resp.headers["Location"] == "/groups?done=renamed&group=artemis"
    # The settings: the users, its section, the group that includes it.
    assert section(wiki, "user alice") == ["groups = artemis"] and section(wiki, "user bob") == ["groups = artemis"]
    assert section(wiki, "group artemis") == ["owners = alice"] and "[group apollo]" not in config(wiki)
    assert section(wiki, "group program") == ["includes = artemis"]
    # The folder, with the file its page shows and the one none shows; the links.
    assert not (wiki / "groups/apollo").exists(), sorted(p.as_posix() for p in (wiki / "groups").rglob("*"))
    assert sorted(p.relative_to(wiki / "groups/artemis").as_posix() for p in (wiki / "groups/artemis").rglob("*") if p.is_file()) == [
        "_assets/plan.png", "_assets/spare.png", "index.md", "notes.md", "roadmap.md"]
    assert (wiki / "groups/artemis/roadmap.md").read_text() == "# Roadmap\n\n![plan](/groups/artemis/_assets/plan.png) [[groups/artemis/notes]]\n"
    assert (wiki / "plans.md").read_text() == "# Plans\n\nSee [[groups/artemis/roadmap]].\n"
    assert (wiki / "groups/artemis/index.md").read_text().startswith("# artemis\n") and "@artemis" in (wiki / "groups/artemis/index.md").read_text()
    assert git(wiki, "status", "--porcelain") == ""                      # all of it committed
    assert {line.split("|")[0] for line in git(wiki, "log", "-n3", "--format=%an|%s").splitlines()} == {"alice"}
    # Its people see it as before, under the new name: the pages, the file, the list and its task.
    for who in ("alice", "bob"):
        sign_in(client, who)
        assert until(lambda: client.get("/page/groups/artemis/roadmap", headers=HOST).status_code == 200)
        assert client.get("/groups/artemis/_assets/plan.png", headers=HOST).data == b"plan"
        assert "For @artemis" in page(client, "/tasks?list=launch")
        task = next(t for t in client.get("/api/tasks?list=launch", headers=HOST).get_json() if t["id"] == apollo)
        assert task["title"] == "Read [[groups/artemis/roadmap]]"
        assert client.get("/page/groups/apollo/roadmap", headers=HOST).status_code == 404
        assert "The group is called artemis now" in page(client, "/groups?done=renamed&group=artemis")
    # And nobody else, as before.
    sign_in(client, "carol")
    assert client.get("/page/groups/artemis/roadmap", headers=HOST).status_code == 404
    assert client.get("/groups/artemis/_assets/plan.png", headers=HOST).status_code == 404
    assert client.get("/tasks?list=launch", headers=HOST).status_code in (302, 404) or "For @artemis" not in page(client, "/tasks?list=launch")
    # The old name is free again.
    assert make(client, "apollo").status_code == 303


def test_a_rename_is_its_owners_and_an_admins(client, wiki, apollo):
    files = lambda: {p.relative_to(wiki).as_posix(): p.read_bytes() for p in wiki.rglob("*")
                     if p.is_file() and not p.relative_to(wiki).as_posix().startswith((".git/", ".zone/"))}
    before, head, settings_before = files(), git(wiki, "rev-parse", "HEAD"), config(wiki)
    rename = lambda group, name, **more: client.post(f"/groups/{group}/rename", data={"name": name, **more}, headers=HOST)
    # A name that is taken, or the same, or none: nothing moves.
    for name, says in (("dev", "is taken"), ("ghosts", "is taken"), ("program", "is taken"), ("apollo", "already"), ("No Name", "a group's name")):
        resp = rename("apollo", name)
        assert resp.status_code in (400, 409) and says in html.unescape(resp.get_data(as_text=True)), name
    # Who doesn't own it: a member, someone else, a viewer; and a group without an owner.
    for who in ("bob", "carol", "vera"):
        sign_in(client, who)
        assert rename("apollo", "theirs").status_code in (403, 404)
    sign_in(client, "carol")
    assert rename("dev", "theirs").status_code == 404                    # carol is in dev, which is an admin's
    assert rename("nosuch", "theirs").status_code == 404
    assert files() == before and git(wiki, "rev-parse", "HEAD") == head and config(wiki) == settings_before
    # An admin renames any group, from the admin page, and sees nothing of its pages by it.
    sign_in(client, "dana")
    body = page(client, "/admin/users")
    assert 'action="/groups/apollo/rename"' in body and 'action="/groups/dev/rename"' in body
    resp = rename("dev", "core", **{"from": "admin"})
    assert resp.status_code == 303 and resp.headers["Location"] == "/admin/users?done=renamed&group=core"
    assert "The group is called core now" in page(client, resp.headers["Location"])
    assert section(wiki, "user carol") == ["groups = core"] and section(wiki, "group product") == ["includes = core, design"]
    assert (wiki / "groups/core/notes.md").read_text() == "# Dev notes\n\nonlydev\n" and not (wiki / "groups/dev").exists()
    assert client.get("/page/groups/core/notes", headers=HOST).status_code == 404
    assert "Dev notes" not in page(client, "/search?q=onlydev")
    resp = rename("core", "apollo", **{"from": "admin"})                 # refused on the admin page, which says why
    assert resp.status_code == 409 and "is taken" in resp.get_data(as_text=True) and "Admin" in resp.get_data(as_text=True)
    sign_in(client, "carol")
    assert until(lambda: "onlydev" in page(client, "/page/groups/core/notes"))


def test_who_is_in_a_group_says_by_which_way_and_a_large_one_is_counted(client, wiki, monkeypatch):
    """/groups names the people a group is named on, then each group it
    includes with its people; past eight it says how many, and how."""
    who = lambda body, g: " ".join(re.sub(r"<[^>]+>", "", body.split(f'data-group="{g}"', 1)[1].split(
        '<span class="group-who">', 1)[1].split('<span class="group-pages">', 1)[0]).split())
    monkeypatch.setattr(settings, "USER_SETTINGS", {"carol": {"groups": "dev, product"}, "bob": {"groups": "design"},
                                                    "alice": {"groups": "dev"}, "dana": {"role": "admin"}})
    body = page(sign_in(client, "carol"))
    assert who(body, "dev") == "alice, carol"
    assert who(body, "product") == "carol, and everyone in design (bob) and dev (alice, carol)"
    many = {f"u{n}": (f"u{n}-pw", f"u{n}-token") for n in range(8)}
    monkeypatch.setattr(settings, "USERS", {**USERS, **many})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"carol": {"groups": "dev, product"}, "dana": {"role": "admin"},
                                                    **{name: {"groups": "dev"} for name in many}})
    body = page(sign_in(client, "carol"))
    assert who(body, "dev") == "9 people" and "u3" not in body.split('data-group="dev"', 1)[1].split("</li>", 1)[0].split("<details", 1)[0]
    assert who(body, "product") == "9 people (everyone in design and dev, and 1 named on it)"
