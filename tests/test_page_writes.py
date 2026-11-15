"""Each page write through both doors, the browser's route and the MCP
tool, on the same wiki: the same checks and the same care for text not
yet in git, whichever door it came through (go/pageops.go)."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests import settings
from tests.conftest import HOST, write_page

pytestmark = pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")

TOKEN = "test-token"
DOORS = ["browser", "mcp"]


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "MCP_TOKEN", TOKEN)
    return git_wiki_dir


@pytest.fixture()
def client(wiki, client):
    """The server started with the token set, so its settings commit comes first."""
    return client


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


def commit(wiki, slug, text, message=None):
    write_page(wiki, slug, text)
    git(wiki, "add", "-A")
    git(wiki, "commit", "-q", "-m", message or f"Write {slug}")
    return git(wiki, "rev-parse", "HEAD").strip()


def subjects(wiki):
    return git(wiki, "log", "--format=%s").splitlines()


def in_history(wiki, text):
    """Whether some commit holds text (a line added by it)."""
    return any(line == "+" + text for line in git(wiki, "log", "-p", "--format=").splitlines())


def mcp(client, tool, token=TOKEN, **args):
    """A tool's text, and whether it is an error."""
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    result = resp.get_json()["result"]
    return result["content"][0]["text"], result["isError"]


# Each write through a door: True when it was done, False when refused.

def delete(client, door, slug, token=TOKEN):
    if door == "browser":
        return client.post(f"/delete/{slug}", headers=HOST).status_code == 302
    return not mcp(client, "delete_page", token, slug=slug)[1]


def rename(client, door, slug, new, token=TOKEN):
    if door == "browser":
        resp = client.post(f"/rename/{slug}", data={"new_name": new}, headers=HOST)
        return resp.status_code == 302 and resp.headers["Location"] == f"/page/{new}"
    return not mcp(client, "rename_page", token, slug=slug, new_slug=new)[1]


def restore(client, door, slug, sha, token=TOKEN):
    if door == "browser":
        return client.post(f"/restore/{slug}/{sha}", headers=HOST).status_code == 302
    return not mcp(client, "restore_page", token, slug=slug, version=sha)[1]


def create(client, door, slug, token=TOKEN):
    if door == "browser":
        resp = client.post("/new", data={"slug": slug}, headers=HOST)
        return resp.status_code == 302
    return not mcp(client, "write_page", token, slug=slug, content="# New\n")[1]


@pytest.mark.parametrize("door", DOORS)
class TestTextNotInGit:
    """A page's uncommitted text (an auto-save left, an edit on disk) is
    committed before a write replaces or removes it: it stays in history."""

    def test_delete(self, client, wiki, door):
        commit(wiki, "p", "# P\n\nCommitted.\n")
        write_page(wiki, "p", "# P\n\nOnly on disk.\n")
        assert delete(client, door, "p")
        assert not (wiki / "p.md").exists()
        assert in_history(wiki, "Only on disk.")
        assert git(wiki, "status", "--porcelain") == ""

    def test_restore(self, client, wiki, door):
        v1 = commit(wiki, "p", "# P\n\nv1\n")
        commit(wiki, "p", "# P\n\nv2\n")
        write_page(wiki, "p", "# P\n\nOnly on disk.\n")
        assert restore(client, door, "p", v1)
        assert (wiki / "p.md").read_text() == "# P\n\nv1\n"
        assert in_history(wiki, "Only on disk.")

    def test_rename_commits_it_apart(self, client, wiki, door):
        """The rename's own commit only moves the page."""
        commit(wiki, "p", "# P\n\nCommitted.\n")
        write_page(wiki, "p", "# P\n\nOnly on disk.\n")
        assert rename(client, door, "p", "q")
        assert (wiki / "q.md").read_text() == "# P\n\nOnly on disk.\n"
        assert subjects(wiki)[1] == "Save p"
        stat = git(wiki, "show", "-M", "--format=", "--name-status", "HEAD")
        assert stat.startswith("R100")


    def test_move_a_note_into_the_wiki(self, client, wiki, door):
        note = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        write_page(wiki, note, "# Idea\n\nOnly on disk.\n")
        if door == "browser":
            client.post(f"/promote/{note}", data={"name": "idea"}, headers=HOST)
        else:
            mcp(client, "file_note", slug=note, name="idea")
        assert (wiki / "idea.md").read_text() == "# Idea\n\nOnly on disk.\n"
        assert subjects(wiki)[1] == f"Save {note}"

    def test_append_a_note_to_a_page(self, client, wiki, door):
        commit(wiki, "plans", "# Plans\n")
        note = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        write_page(wiki, note, "# Idea\n\nOnly on disk.\n")
        if door == "browser":
            client.post(f"/append/{note}", data={"page": "plans", "heading": "Idea"}, headers=HOST)
        else:
            mcp(client, "file_note", slug=note, page="plans", heading="Idea")
        assert "Only on disk." in (wiki / "plans.md").read_text()
        assert subjects(wiki)[1] == f"Save {note}"


@pytest.mark.parametrize("door", DOORS)
class TestRefused:
    """What neither door does; the wiki is left as it was."""

    def test_home_page_cant_be_deleted_or_renamed(self, client, wiki, door):
        head = subjects(wiki)
        assert not delete(client, door, "index")
        assert not rename(client, door, "index", "home")
        assert (wiki / "index.md").exists() and not (wiki / "home.md").exists()
        assert subjects(wiki) == head

    def test_rename_onto_an_existing_page(self, client, wiki, door):
        commit(wiki, "a", "# A\n")
        commit(wiki, "b", "# B\n")
        assert not rename(client, door, "a", "b")
        assert (wiki / "a.md").read_text() == "# A\n" and (wiki / "b.md").read_text() == "# B\n"

    def test_missing_page(self, client, wiki, door):
        head = subjects(wiki)
        assert not delete(client, door, "nothing")
        assert not rename(client, door, "nothing", "something")
        assert subjects(wiki) == head

    def test_restore_to_the_text_it_has_commits_nothing(self, client, wiki, door):
        v1 = commit(wiki, "p", "# P\n\nv1\n")
        head = subjects(wiki)
        restore(client, door, "p", v1)
        assert subjects(wiki) == head

    def test_restore_to_a_version_without_text(self, client, wiki, door):
        """The commit that deleted the page has no text to go back to."""
        commit(wiki, "p", "# P\n")
        (wiki / "p.md").unlink()
        git(wiki, "commit", "-qam", "Delete p")
        gone = git(wiki, "rev-parse", "HEAD").strip()
        assert not restore(client, door, "p", gone)
        assert not (wiki / "p.md").exists()

    def test_create_over_an_existing_page_keeps_it(self, client, wiki, door):
        commit(wiki, "p", "# P\n\nKept.\n")
        create(client, door, "p")
        assert (wiki / "p.md").read_text() == "# P\n\nKept.\n"


@pytest.mark.parametrize("door", DOORS)
class TestAnotherUsersPrivatePage:
    """alice can't touch bob's private pages, nor learn they exist."""

    @pytest.fixture()
    def bobs(self, client, wiki, monkeypatch):
        monkeypatch.setattr(settings, "MCP_TOKEN", "")
        monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "")})
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        v1 = commit(wiki, "private/bob/diary", "# Diary\n\nv1\n")
        commit(wiki, "private/bob/diary", "# Diary\n\nv2\n")
        return v1

    def test_delete_rename_restore(self, client, wiki, door, bobs):
        head = subjects(wiki)
        assert not delete(client, door, "private/bob/diary", "alice-token")
        assert not rename(client, door, "private/bob/diary", "diary", "alice-token")
        assert not restore(client, door, "private/bob/diary", bobs, "alice-token")
        assert (wiki / "private/bob/diary.md").read_text() == "# Diary\n\nv2\n"
        assert subjects(wiki) == head

    def test_create_in_bobs_folder(self, client, wiki, door, bobs):
        create(client, door, "private/bob/mine", "alice-token")
        assert not (wiki / "private/bob/mine.md").exists()

    def test_rename_into_bobs_folder(self, client, wiki, door, bobs):
        commit(wiki, "p", "# P\n")
        assert not rename(client, door, "p", "private/bob/p", "alice-token")
        assert (wiki / "p.md").exists() and not (wiki / "private/bob/p.md").exists()

    def test_answers_dont_tell_it_exists(self, client, wiki, door, bobs):
        """The same answer as for a page that isn't there."""
        if door == "browser":
            for url in ("/delete/private/bob/diary", "/rename/private/bob/diary", f"/restore/private/bob/diary/{bobs}"):
                assert client.post(url, data={"new_name": "x"}, headers=HOST).status_code == 404, url
        else:
            text, _ = mcp(client, "delete_page", "alice-token", slug="private/bob/diary")
            missing, _ = mcp(client, "delete_page", "alice-token", slug="private/bob/nothing")
            assert text == missing.replace("nothing", "diary")


def test_a_refused_change_commits_nothing(client, wiki):
    """An edit that doesn't apply leaves the page's uncommitted text as it was."""
    commit(wiki, "p", "# P\n\nCommitted.\n")
    write_page(wiki, "p", "# P\n\nOnly on disk.\n")
    head = subjects(wiki)
    text, err = mcp(client, "edit_page", slug="p", old_string="Not there.", new_string="x")
    assert err and "not found" in text
    assert subjects(wiki) == head and git(wiki, "status", "--porcelain").strip() == "M p.md"


class TestOneWriteAtATime:
    """Writes to pages run one at a time (pagesMu), from the read each
    decides on to its commit: none loses another's change."""

    def test_concurrent_appends_keep_every_line(self, client, wiki):
        commit(wiki, "log", "# Log\n")
        lines = [f"Entry {i}." for i in range(16)]
        with ThreadPoolExecutor(len(lines)) as pool:
            results = list(pool.map(lambda line: mcp(client, "append_to_page", slug="log", content=line), lines))
        assert not any(err for _, err in results), results
        text = (wiki / "log.md").read_text()
        assert all(line in text for line in lines), text

    def test_appends_and_ticks_at_once(self, client, wiki):
        """A ticked checkbox (the browser) and appends (MCP) at once."""
        commit(wiki, "todo", "# Todo\n\n- [ ] one\n")

        def tick(_):
            return client.post("/api/toggle-checkbox/todo", data={"idx": "0"}, headers=HOST).status_code

        def append(i):
            return mcp(client, "append_to_page", slug="todo", content=f"Line {i}.")[1]

        with ThreadPoolExecutor(8) as pool:
            ticks = [pool.submit(tick, i) for i in range(4)]
            appends = [pool.submit(append, i) for i in range(8)]
        assert all(t.result() == 200 for t in ticks)
        assert not any(a.result() for a in appends)
        text = (wiki / "todo.md").read_text()
        assert all(f"Line {i}." in text for i in range(8)), text
        assert "- [ ] one" in text  # four ticks: unticked again


@pytest.fixture()
def full(wiki, monkeypatch):
    """A wiki holding more than its storage-max (go/storage.go)."""
    monkeypatch.setattr(settings, "CONFIG_FILE", "storage-max = 10KB\n")
    (wiki / "_assets").mkdir(exist_ok=True)
    (wiki / "_assets" / "big.bin").write_bytes(b"x" * 20_000)
    return wiki


def note(client, door, title, token=TOKEN):
    if door == "browser":
        return client.post("/api/notes", data={"title": title}, headers=HOST).status_code == 200
    return not mcp(client, "write_page", token, note=True, content=f"# {title}\n")[1]


@pytest.mark.parametrize("door", DOORS)
class TestWikiFull:
    """storage-max: nothing new once the wiki would hold more; an edit, a
    restore, a rename or a delete still goes through, so typed text is never
    refused and there is always a way to make room."""

    def test_nothing_new(self, client, full, door):
        client.get("/", headers=HOST)   # started: its settings committed
        head = subjects(full)
        assert not create(client, door, "new-page")
        assert not note(client, door, "Idea")
        assert not (full / "new-page.md").exists() and not list(full.glob("*idea*"))
        assert subjects(full) == head

    def test_the_refusal_says_why(self, client, full, door):
        if door == "browser":
            resp = client.post("/new", data={"slug": "new-page"}, headers=HOST)
            assert resp.status_code == 507
            text = resp.get_data(as_text=True)
        else:
            text, is_error = mcp(client, "write_page", slug="new-page", content="# New\n")
            assert is_error
        assert "The wiki is full:" in text and "of 10 KB used" in text and "Delete pages or files" in text

    def test_edits_restores_renames_and_deletes_go_through(self, client, full, door):
        v1 = commit(full, "p", "# P\n\nv1\n")
        commit(full, "p", "# P\n\nv2\n")
        if door == "browser":
            resp = client.post("/edit/p", data={"content": "# P\n\n" + "more " * 1000}, headers=HOST)
            assert resp.status_code == 302
        else:
            assert not mcp(client, "append_to_page", slug="p", content="more " * 1000)[1]
        assert "more more" in (full / "p.md").read_text()
        assert restore(client, door, "p", v1)
        assert rename(client, door, "p", "q")
        assert delete(client, door, "q")

    def test_room_again_once_deleted(self, client, full, door):
        """A delete through Zone counts at once (one on disk within seconds)."""
        commit(full, "big", "# Big\n\n" + "x" * 20_000)
        assert not create(client, door, "new-page")
        (full / "_assets" / "big.bin").unlink()
        assert delete(client, door, "big")
        assert create(client, door, "new-page")


class TestWrites:
    def test_a_page_keeps_its_permissions(self, client, wiki):
        """Written atomically (a new file renamed into place), with the mode it had."""
        commit(wiki, "p", "# P\n")
        (wiki / "p.md").chmod(0o600)
        assert not mcp(client, "append_to_page", slug="p", content="More.")[1]
        assert "More." in (wiki / "p.md").read_text()
        assert (wiki / "p.md").stat().st_mode & 0o777 == 0o600
        assert not [f for f in wiki.iterdir() if ".tmp-" in f.name]


class TestCommits:
    def test_only_the_writes_files_go_in(self, client, wiki):
        """What is staged by hand stays staged, out of Zone's commit."""
        (wiki / "notes.txt").write_text("by hand\n")
        git(wiki, "add", "notes.txt")
        assert create(client, "browser", "fresh")
        assert git(wiki, "show", "--format=", "--name-only", "HEAD").split() == ["fresh.md"]
        assert git(wiki, "diff", "--cached", "--name-only").split() == ["notes.txt"]

    @pytest.mark.parametrize("door", DOORS)
    def test_a_page_git_ignores_is_written_not_committed(self, client, wiki, door):
        """A folder in .gitignore: its pages are written, not refused."""
        (wiki / ".gitignore").write_text("journal/\n")
        git(wiki, "add", ".gitignore")
        git(wiki, "commit", "-m", "ignore journal")
        head = git(wiki, "rev-parse", "HEAD")
        assert create(client, door, "journal/day")
        assert (wiki / "journal" / "day.md").exists()
        assert git(wiki, "rev-parse", "HEAD") == head
        if door == "browser":
            resp = client.post("/api/save/journal/day", data={"content": "# Day\n\nMore.\n"}, headers=HOST)
            assert resp.status_code == 200, resp.get_data(as_text=True)
        else:
            text, err = mcp(client, "append_to_page", slug="journal/day", content="More.")
            assert not err, text
        assert "More." in (wiki / "journal" / "day.md").read_text()
        assert delete(client, door, "journal/day")
        assert not (wiki / "journal" / "day.md").exists()

    @pytest.mark.parametrize("door", DOORS)
    def test_a_rename_with_links_left_is_done(self, client, wiki, door):
        """A page that can't be read (a dangling link) keeps its links, and the
        rename is done: no error, the MCP tool says what was left."""
        commit(wiki, "p", "# P\n")
        commit(wiki, "ref", "# Ref\n\n[[p]]\n")
        (wiki / "broken.md").symlink_to(wiki / "nowhere.md")
        if door == "browser":
            resp = client.post("/rename/p", data={"new_name": "q"}, headers=HOST)
            assert resp.status_code == 302 and resp.headers["Location"] == "/page/q"
        else:
            text, err = mcp(client, "rename_page", slug="p", new_slug="q")
            assert not err and "not every link" in text, text
        assert (wiki / "q.md").exists() and "[[q]]" in (wiki / "ref.md").read_text()
        assert subjects(wiki)[0].startswith("Rename p to q")

    @pytest.mark.parametrize("door", DOORS)
    def test_a_failed_commit_is_reported(self, client, wiki, door):
        """git can't commit (a stale index.lock): the write says so."""
        commit(wiki, "p", "# P\n")
        (wiki / ".git" / "index.lock").write_text("")
        if door == "browser":
            resp = client.post("/new", data={"slug": "fresh"}, headers=HOST)
            assert resp.status_code == 500
        else:
            text, err = mcp(client, "append_to_page", slug="p", content="More.")
            assert err and "not committed" in text, text

    @pytest.mark.parametrize("door", DOORS)
    def test_text_not_in_git_that_cant_be_committed_stops_the_write(self, client, wiki, door):
        """Deleting it would lose it: nothing is deleted."""
        commit(wiki, "p", "# P\n\nCommitted.\n")
        write_page(wiki, "p", "# P\n\nOnly on disk.\n")
        (wiki / ".git" / "index.lock").write_text("")
        assert not delete(client, door, "p")
        assert (wiki / "p.md").read_text() == "# P\n\nOnly on disk.\n"

    def test_drafts_keep_their_author_when_the_commit_fails(self, client, wiki, monkeypatch):
        """bob's auto-save, committed in his name before alice's write, even
        after a commit that failed in between."""
        monkeypatch.setattr(settings, "MCP_TOKEN", "")
        monkeypatch.setattr(settings, "USERS", {"alice": ("alice-pw", ""), "bob": ("bob-pw", "")})
        commit(wiki, "p", "# P\n\nv1\n")
        client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
        saved = client.post("/api/save/p", data={"content": "# P\n\nbob's\n"}, headers=HOST)
        assert saved.status_code == 200
        lock = wiki / ".git" / "index.lock"
        lock.write_text("")
        assert client.post("/edit/p", data={"content": "# P\n\nbob's\n"}, headers=HOST).status_code == 500
        lock.unlink()
        client.post("/logout", headers=HOST)
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        assert client.post("/delete/p", headers=HOST).status_code == 302
        assert git(wiki, "log", "-2", "--format=%an %s").splitlines() == ["alice Delete p", "bob Save p"]


# ── Renaming a tag or list ──────────────────────────────────────

def tasks_file(wiki, name="tasks.json"):
    return json.loads((wiki / "_tasks" / name).read_text())


def stored_task(title, tags, closed_at=None):
    return {"id": f"{abs(hash(title)):032x}"[:32], "title": title, "description": "", "tags": tags,
            "start_date": None, "due_date": None, "created_at": "2001-01-01T10:00:00",
            "closed_at": closed_at, "wont_do": False, "keep": False, "last_done": None,
            "sort_order": None, "recurrence": None, "spawned_from": None, "history": [], "comments": []}


def rename_tag(client, door, tag, new, merge=False):
    """True when done, False when refused; the browser's refusal is a redirect to the dialog."""
    if door == "browser":
        data = {"tag": tag, "new_name": new, **({"merge": "1"} if merge else {})}
        resp = client.post("/tasks/rename", data=data, headers=HOST)
        assert resp.status_code == 302
        return "rename_error" not in resp.headers["Location"]
    return not mcp(client, "rename_task_tag", tag=tag, new_tag=new, merge=merge)[1]


@pytest.fixture()
def shop(client, wiki):
    """A tag #shop: on open, completed and archived tasks, in a page's
    embeds and links; and a list of that name with a task in it, which is
    no tag: a tag's rename leaves it alone, its own rename the tag."""
    (wiki / "_tasks/tasks.json").write_text(json.dumps([
        stored_task("Milk", ["shop", "weekly"]),
        {**stored_task("See list", ["other"]), "description": "On [the tag](/tasks?tag=shop) and [the list](/tasks?list=shop)."}]))
    (wiki / "_tasks/closed.json").write_text(json.dumps([stored_task("Eggs", ["Shop"], "2099-01-01T10:00:00")]))
    (wiki / "_tasks/closed-2001.json").write_text(json.dumps([{**stored_task("Old", ["shop"], "2001-01-01T10:00:00"), "list": "shop"}]))
    assert client.post("/tasks/new-list", data={"name": "shop", "pin": "0"}, headers=HOST).headers["Location"] == "/tasks?list=shop"
    commit(wiki, "lists", "# Lists\n\n[[tasks: list=shop view=open]]\n\n[[tasks: tag=shop,-x new=shop]]\n\n"
                          "[Go](/tasks?tag=shop), [the list](/tasks?list=shop&view=open), #shop\n\n`[[tasks: list=shop tag=shop]]`\n")
    return wiki


@pytest.mark.parametrize("door", DOORS)
def test_rename_tag_changes_it_everywhere_in_one_commit(client, shop, door):
    before = git(shop, "rev-parse", "HEAD").strip()
    assert rename_tag(client, door, "shop", "food")
    assert tasks_file(shop)[0]["tags"] == ["food", "weekly"]
    assert "(/tasks?tag=food) and [the list](/tasks?list=shop)" in tasks_file(shop)[1]["description"]
    assert tasks_file(shop, "closed.json")[0]["tags"] == ["food"]
    old = tasks_file(shop, "closed-2001.json")[0]
    assert old["tags"] == ["food"] and old["list"] == "shop"
    text = (shop / "lists.md").read_text()
    # The list of that name is no tag: it, its embed and its link stay.
    assert "[[tasks: list=shop view=open]]" in text and "[[tasks: tag=food,-x new=food]]" in text
    assert "(/tasks?tag=food), [the list](/tasks?list=shop&view=open), #shop" in text
    assert "`[[tasks: list=shop tag=shop]]`" in text   # prose and code stay
    assert len(git(shop, "log", "--format=%H", f"{before}..HEAD").split()) == 1
    assert git(shop, "status", "--porcelain", "--", "lists.md", "_tasks").strip() == ""
    page = client.get("/tasks?tag=food", headers=HOST).get_data(as_text=True)
    assert 'id="tag-rename-dialog"' in page and "Rename tag #food" in page
    assert 'data-place="list:shop"' in page


@pytest.mark.parametrize("door", DOORS)
def test_rename_tag_refuses_a_tag_no_one_has(client, shop, door):
    assert not rename_tag(client, door, "nosuch", "food")
    assert not rename_tag(client, door, "shop", "")
    assert not rename_tag(client, door, "shop", "a,b")
    assert "[[tasks: tag=shop,-x new=shop]]" in (shop / "lists.md").read_text()


@pytest.mark.parametrize("door", DOORS)
def test_rename_tag_onto_a_name_in_use_merges_only_when_asked(client, shop, door):
    assert not rename_tag(client, door, "shop", "weekly")
    assert tasks_file(shop)[0]["tags"] == ["shop", "weekly"]
    assert rename_tag(client, door, "shop", "weekly", merge=True)
    assert tasks_file(shop)[0]["tags"] == ["weekly"]
    assert tasks_file(shop, "closed.json")[0]["tags"] == ["weekly"]
    assert "Merge tag #shop into #weekly" in git(shop, "log", "-1", "--format=%s")


@pytest.mark.parametrize("door", DOORS)
def test_rename_tag_keeps_text_not_yet_in_git(client, shop, door):
    (shop / "lists.md").write_text((shop / "lists.md").read_text() + "\nA draft line\n")
    assert rename_tag(client, door, "shop", "food")
    text = (shop / "lists.md").read_text()
    assert "A draft line" in text and "tag=food" in text
    assert git(shop, "status", "--porcelain", "--", "lists.md").strip() == ""
    subjects = git(shop, "log", "-2", "--format=%s", "--", "lists.md").split("\n")
    assert subjects[0].startswith("Rename tag") and subjects[1].startswith("Save ")
    assert "A draft line" in git(shop, "show", "HEAD~1:lists.md")


@pytest.mark.parametrize("door", DOORS)
def test_a_corrupt_tasks_file_stops_a_tag_rename_with_nothing_written(client, shop, door):
    (shop / "_tasks/closed.json").write_text("{not json")
    before = (shop / "lists.md").read_text(), (shop / "_tasks/tasks.json").read_text()
    if door == "browser":
        assert client.post("/tasks/rename", data={"tag": "shop", "new_name": "food"}, headers=HOST).status_code == 500
    else:
        assert rename_tag(client, door, "shop", "food") is False
    assert ((shop / "lists.md").read_text(), (shop / "_tasks/tasks.json").read_text()) == before


def test_rename_tag_changing_only_the_case_is_no_merge(client, shop):
    assert rename_tag(client, "mcp", "weekly", "Weekly")
    assert tasks_file(shop)[0]["tags"] == ["shop", "Weekly"]
    assert not rename_tag(client, "mcp", "Weekly", "Weekly")


def test_rename_tag_works_on_a_plain_tag(client, shop):
    assert rename_tag(client, "browser", "weekly", "every-week")
    assert tasks_file(shop)[0]["tags"] == ["shop", "every-week"]


def test_rename_tag_to_its_own_name_changes_nothing(client, shop):
    head = git(shop, "rev-parse", "HEAD")
    resp = client.post("/tasks/rename", data={"tag": "weekly", "new_name": "#weekly"}, headers=HOST)
    assert resp.status_code == 302 and "rename_error" not in resp.headers["Location"]
    assert git(shop, "rev-parse", "HEAD") == head


def test_rename_tag_to_its_own_name_evens_out_the_spelling(client, shop):
    assert rename_tag(client, "browser", "shop", "shop")
    assert tasks_file(shop, "closed.json")[0]["tags"] == ["shop"]   # it was #Shop


def test_rename_tag_dialog_comes_back_offering_the_merge(client, shop):
    resp = client.post("/tasks/rename", data={"tag": "shop", "new_name": "weekly"}, headers=HOST)
    assert "rename_merge=1" in resp.headers["Location"]
    assert resp.headers["Location"].startswith("/tasks?tag=shop&")
    page = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    dialog = page.split('<dialog id="tag-rename-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert " data-open>" in dialog.split(">", 1)[0] + ">"
    assert 'name="merge"' in dialog and "already in use" in dialog and 'value="weekly"' in dialog


# ── A new list (the Tasks page's ⋯ menu) ─────────────────────────

def new_list(client, name, **more):
    """The list's URL when made, None when the dialog comes back with a refusal."""
    resp = client.post("/tasks/new-list", data={"name": name, **more}, headers=HOST)
    assert resp.status_code == 302
    return None if "newlist_error" in resp.headers["Location"] else resp.headers["Location"]


def test_new_list_makes_a_list_and_no_page(client, wiki):
    before = git(wiki, "rev-parse", "HEAD").strip()
    assert new_list(client, " #Shop  Runs ") == "/tasks?list=shop-runs"   # the list's page; it is pinned
    assert not (wiki / "lists").exists()
    assert (wiki / "_tasks/lists.json").read_text() == '{\n  "version": 2,\n  "lists": [\n    {"name": "shop-runs"}\n  ],\n  "pinned": ["shop-runs"]\n}\n'
    assert git(wiki, "log", "--format=%s", f"{before}..HEAD").splitlines() == ["Create list #shop-runs"]
    assert git(wiki, "show", "--name-only", "--format=", "HEAD").split() == ["_tasks/lists.json"]
    page = client.get("/tasks?list=shop-runs", headers=HOST).get_data(as_text=True)
    assert 'data-place="list:shop-runs"' in page                     # an entry in the list switcher
    assert "There is no list" not in page
    assert "tasks-made-first" not in page and "New tasks go into this list" not in page     # a pin takes no new task
    # A task made there is in the list.
    assert 'data-init-list="shop-runs"' in page


def test_new_list_of_a_tags_name_takes_no_task(client, shop):
    assert new_list(client, "weekly") == "/tasks?list=weekly"        # a tag of Milk's
    milk = tasks_file(shop)[0]
    assert milk["tags"] == ["shop", "weekly"] and "list" not in milk  # a tag is a tag: the tasks stay as they are


def test_new_list_refuses_a_list_there_is_and_no_name(client, shop):
    head = git(shop, "rev-parse", "HEAD")
    resp = client.post("/tasks/new-list", data={"name": "Shop"}, headers=HOST)
    assert "newlist_error" in resp.headers["Location"]
    page = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert 'id="list-new-dialog" class="modal" data-params="newlist_error newlist_name" data-open' in page    # the dialog again, saying why
    assert "There is a list #shop already" in page and 'value="Shop"' in page
    for name in ("", "  ", "#", "a,b", "x=y", "[x]", "a/b", "inbox", " #Inbox ", "none"):
        assert new_list(client, name) is None, name
    # inbox and none name the Inbox, the tasks in no list: the dialog says so.
    resp = client.post("/tasks/new-list", data={"name": "Inbox"}, headers=HOST)
    page = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "A list can&#39;t be called inbox: that names the Inbox, the tasks in no list" in page
    assert git(shop, "rev-parse", "HEAD") == head


def test_a_list_made_where_the_page_stays_answers_its_ref(client, wiki):
    """POST /api/tasks/lists (New list… in the dialog that moves tasks):
    the list as New list… makes it, answered as JSON: pinned, last, the
    first list too (a pin is a pill of the switcher, and takes no new task)."""
    before = git(wiki, "rev-parse", "HEAD").strip()
    resp = client.post("/api/tasks/lists", json={"name": " #Shop  Runs "}, headers=HOST)
    assert resp.status_code == 201 and resp.get_json() == {"ref": "shop-runs", "private": False, "pinned": True}
    registry = wiki / "_tasks/lists.json"
    assert [l["name"] for l in json.loads(registry.read_text())["lists"]] == ["shop-runs"]
    assert json.loads(registry.read_text())["pinned"] == ["shop-runs"]
    assert git(wiki, "log", "--format=%s", f"{before}..HEAD").splitlines() == ["Create list #shop-runs"]
    assert git(wiki, "show", "--name-only", "--format=", "HEAD").split() == ["_tasks/lists.json"]
    resp = client.post("/api/tasks/lists", json={"name": "work"}, headers=HOST)
    assert resp.status_code == 201 and resp.get_json() == {"ref": "work", "private": False, "pinned": True}
    assert json.loads(registry.read_text())["pinned"] == ["shop-runs", "work"]


def test_a_list_made_where_the_page_stays_says_why_not(client, shop):
    head = git(shop, "rev-parse", "HEAD")
    resp = client.post("/api/tasks/lists", json={"name": "Shop"}, headers=HOST)
    assert resp.status_code == 400 and resp.get_json() == {"error": "There is a list #shop already"}
    for body in ({}, {"name": ""}, {"name": "  "}, {"name": "a,b"}, {"name": 3}, {"name": "x", "private": "yes"},
                 {"name": "x", "private": True},        # a private list needs users
                 {"name": "inbox"}, {"name": "None"}):  # the Inbox's names
        resp = client.post("/api/tasks/lists", json=body, headers=HOST)
        assert resp.status_code == 400 and resp.get_json()["error"], body
    assert client.post("/api/tasks/lists", data="[]", headers=HOST).status_code == 400
    assert git(shop, "rev-parse", "HEAD") == head


def test_new_list_is_no_page(client, wiki):
    commit(wiki, "lists/shop", "# Shopping notes\n")
    assert new_list(client, "shop") == "/tasks?list=shop"            # a page of that name is a page
    assert (wiki / "lists/shop.md").read_text() == "# Shopping notes\n"
    # Without users there is nobody for a list to be private to.
    assert new_list(client, "mine", private="1") is None


def test_the_tasks_page_has_the_menu_with_new_list(client, shop):
    for url in ("/tasks", "/tasks?list=shop", "/tasks?list=nolist"):
        page = client.get(url, headers=HOST).get_data(as_text=True)
        assert 'class="btn page-menu-btn"' in page and 'data-dialog="list-new-dialog"' in page, url
    assert 'id="list-new-private"' not in page                      # no users: no Private box


def test_a_lists_actions_are_a_lists_alone(client, shop):
    page = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
    for item in ("list-rename-btn", "list-pin-btn", "list-delete-btn"):
        assert item in page, item
    assert "list-share-btn" not in page and "tag-rename-btn" not in page    # a shared list; no tag here
    for url in ("/tasks", "/tasks?list=nolist"):
        page = client.get(url, headers=HOST).get_data(as_text=True)
        assert "list-rename-btn" not in page and "list-delete-btn" not in page and "tag-rename-btn" not in page, url
    page = client.get("/tasks?tag=weekly", headers=HOST).get_data(as_text=True)
    assert "tag-rename-btn" in page and "list-rename-btn" not in page


# ── A list's rename, delete and pin (its ⋯ menu on /tasks?list=) ──────────────

def list_op(client, op, **data):
    """Where the browser goes after a list's operation; the refusal's message when its dialog comes back."""
    resp = client.post(f"/tasks/{op}-list", data=data, headers=HOST)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return resp.headers["Location"]


def test_rename_list_changes_it_everywhere_in_one_commit(client, shop):
    client.post("/api/tasks", json={"title": "Bread", "list": "shop", "tags": ["shop"]}, headers=HOST)
    list_op(client, "pin", list="shop", pin="1")
    before = git(shop, "rev-parse", "HEAD").strip()
    assert list_op(client, "rename", list="shop", new_name=" #Food ") == "/tasks?list=food"
    bread = tasks_file(shop)[-1]
    assert bread["list"] == "food" and bread["tags"] == ["shop"]      # the tag of that name is no list
    assert tasks_file(shop)[0]["tags"] == ["shop", "weekly"]
    assert "(/tasks?tag=shop) and [the list](/tasks?list=food)" in tasks_file(shop)[1]["description"]
    assert tasks_file(shop, "closed-2001.json")[0]["list"] == "food"  # archived ones too
    text = (shop / "lists.md").read_text()
    assert "[[tasks: list=food view=open]]" in text and "[[tasks: tag=shop,-x new=shop]]" in text
    assert "(/tasks?tag=shop), [the list](/tasks?list=food&view=open), #shop" in text
    assert "`[[tasks: list=shop tag=shop]]`" in text   # code stays
    registry = (shop / "_tasks/lists.json").read_text()
    assert '{"name": "food"}' in registry and '"pinned": ["food"]' in registry and "shop" not in registry
    assert git(shop, "log", "--format=%s", f"{before}..HEAD").splitlines() == ["Rename list #shop to #food"]
    assert git(shop, "status", "--porcelain", "--", "lists.md", "_tasks").strip() == ""
    page = client.get("/tasks?list=food", headers=HOST).get_data(as_text=True)
    assert "List: food" in page and 'data-place="list:shop"' not in page and 'data-place="list:food"' in page
    assert "There is no list" in client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)


def test_rename_list_refuses_and_says_why_in_its_dialog(client, shop):
    client.post("/tasks/new-list", data={"name": "market", "pin": "0"}, headers=HOST)
    head = git(shop, "rev-parse", "HEAD")
    for name, why in (("market", "There is a list #market already"), ("a,b", "can&#39;t contain"), ("  ", "needs a name")):
        back = list_op(client, "rename", list="shop", new_name=name)
        assert back.startswith("/tasks?list=shop&rename_error="), name
        page = client.get(back, headers=HOST).get_data(as_text=True)
        dialog = page.split('<dialog id="list-rename-dialog"', 1)[1].split("</dialog>", 1)[0]
        assert " data-open" in dialog.split(">", 1)[0] and why in dialog, name
    assert list_op(client, "rename", list="shop", new_name="Shop") == "/tasks?list=shop"     # its own name: nothing to do
    assert client.post("/tasks/rename-list", data={"list": "nosuch", "new_name": "x"}, headers=HOST).status_code == 404
    assert git(shop, "rev-parse", "HEAD") == head


def test_an_assistant_renames_a_list_as_the_dialog_does(client, shop):
    """rename_task_list: the same operation through the MCP door."""
    client.post("/api/tasks", json={"title": "Bread", "list": "shop"}, headers=HOST)
    before = git(shop, "rev-parse", "HEAD").strip()
    text, err = mcp(client, "rename_task_list", list="shop", new_name="#Food", summary="it is food")
    assert not err, text
    assert text.startswith("Renamed the list #shop to #food, in one commit: 2 tasks, 1 page (lists).") and "/tasks?list=food" in text
    assert tasks_file(shop)[-1]["list"] == "food" and tasks_file(shop, "closed-2001.json")[0]["list"] == "food"
    assert "[[tasks: list=food view=open]]" in (shop / "lists.md").read_text()
    assert '{"name": "food"}' in (shop / "_tasks/lists.json").read_text()
    subjects = git(shop, "log", "--format=%s", f"{before}..HEAD").splitlines()
    assert subjects == ["Rename list #shop to #food: it is food (MCP)"]
    # Refusals are tool errors, and nothing is written.
    head = git(shop, "rev-parse", "HEAD")
    for args, why in (({"list": "nosuch", "new_name": "x"}, "No list #nosuch"),
                      ({"list": "food", "new_name": "food"}, "already called #food"),
                      ({"list": "food", "new_name": "a,b"}, "can't contain"),
                      ({"list": "food"}, "new_name")):
        text, err = mcp(client, "rename_task_list", **args)
        assert err and why in text, args
    assert git(shop, "rev-parse", "HEAD") == head


def test_rename_list_under_a_guide_needs_its_id(client, shop):
    commit(shop, "wiki-guide", "# Guide\n\nWrite well.\n")
    text, err = mcp(client, "rename_task_list", list="shop", new_name="food")
    assert err and text.startswith("Nothing was written") and '{"name": "shop"}' in (shop / "_tasks/lists.json").read_text()
    assert "list=shop view=open" in (shop / "lists.md").read_text()
    ids = json.loads(text.split("guides set to ", 1)[1].split("]", 1)[0] + "]")
    text, err = mcp(client, "rename_task_list", list="shop", new_name="food", guides=ids)
    assert not err and "list=food view=open" in (shop / "lists.md").read_text()


def test_rename_list_keeps_text_not_yet_in_git(client, shop):
    (shop / "lists.md").write_text((shop / "lists.md").read_text() + "\nA draft line\n")
    assert list_op(client, "rename", list="shop", new_name="food") == "/tasks?list=food"
    text = (shop / "lists.md").read_text()
    assert "A draft line" in text and "list=food view=open" in text
    assert git(shop, "status", "--porcelain", "--", "lists.md").strip() == ""
    subjects = git(shop, "log", "-2", "--format=%s", "--", "lists.md").split("\n")
    assert subjects[0].startswith("Rename list") and subjects[1].startswith("Save ")
    assert "A draft line" in git(shop, "show", "HEAD~1:lists.md")


def test_a_corrupt_tasks_file_stops_a_list_rename_with_nothing_written(client, shop):
    (shop / "_tasks/closed.json").write_text("{not json")
    before = (shop / "lists.md").read_text(), (shop / "_tasks/lists.json").read_text()
    assert client.post("/tasks/rename-list", data={"list": "shop", "new_name": "food"}, headers=HOST).status_code == 500
    assert ((shop / "lists.md").read_text(), (shop / "_tasks/lists.json").read_text()) == before


def test_delete_list_moves_its_tasks_and_keeps_the_pages(client, shop):
    client.post("/tasks/new-list", data={"name": "market", "pin": "0"}, headers=HOST)
    client.post("/api/tasks", json={"title": "Bread", "list": "shop"}, headers=HOST)
    list_op(client, "pin", list="shop", pin="first")
    before = git(shop, "rev-parse", "HEAD").strip()
    assert list_op(client, "delete", list="shop", to="shop").startswith("/tasks?list=shop&delete_error=")
    assert list_op(client, "delete", list="shop", to="market") == "/tasks?list=market"
    assert tasks_file(shop)[-1]["list"] == "market" and tasks_file(shop, "closed-2001.json")[0]["list"] == "market"
    registry = (shop / "_tasks/lists.json").read_text()
    assert '{"name": "shop"}' not in registry and '"pinned": []' in registry and "default" not in registry
    assert git(shop, "log", "--format=%s", f"{before}..HEAD").splitlines() == ["Delete list #shop"]
    assert "[[tasks: list=shop view=open]]" in (shop / "lists.md").read_text()   # the page keeps its embed,
    assert "There is no list <code>#shop</code>" in client.get("/page/lists", headers=HOST).get_data(as_text=True)   # which says so
    # To no list: its tasks stay, in none.
    assert list_op(client, "delete", list="market", to="") == "/tasks"
    assert "list" not in tasks_file(shop)[-1] and tasks_file(shop)[-1]["title"] == "Bread"


def test_delete_list_closes_its_open_tasks_when_asked(client, shop):
    """Delete…'s "Close its open tasks as won't do": the list's open tasks
    go to Closed undone, in the same commit, and can be reopened; a task
    elsewhere and one closed already stay as they are."""
    for title in ("Bread", "Jam"):
        client.post("/api/tasks", json={"title": title, "list": "shop"}, headers=HOST)
    done = client.post("/api/tasks", json={"title": "Done", "list": "shop"}, headers=HOST).get_json()["id"]
    client.put(f"/api/tasks/{done}", json={"completed": True}, headers=HOST)
    page = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
    assert 'id="list-delete-close" value="1"> Close its 2 open tasks as won\'t do' in page.replace("&#39;", "'")
    before = git(shop, "rev-parse", "HEAD").strip()
    assert list_op(client, "delete", list="shop", to="", close="1") == "/tasks"
    assert git(shop, "log", "--format=%s", f"{before}..HEAD").splitlines() == ["Delete list #shop"]
    assert [t["title"] for t in tasks_file(shop)] == ["Milk", "See list"]         # in no list all along
    closed = {t["title"]: t for t in tasks_file(shop, "closed.json")}
    for title in ("Bread", "Jam"):
        t = closed[title]
        assert t["wont_do"] is True and t["closed_at"] and "list" not in t and t["history"][-1]["event"] == "wontdo"
    assert closed["Done"]["wont_do"] is False and "list" not in closed["Done"]
    # Reopened from Closed, as any task closed undone.
    assert client.put(f"/api/tasks/{closed['Bread']['id']}", json={"completed": False}, headers=HOST).status_code == 200
    assert "Bread" in [t["title"] for t in tasks_file(shop)]


def test_delete_list_without_open_tasks_offers_no_closing(client, shop):
    client.post("/tasks/new-list", data={"name": "empty", "pin": "0"}, headers=HOST)
    assert "list-delete-close" not in client.get("/tasks?list=empty", headers=HOST).get_data(as_text=True)


def test_pinning_a_list_is_a_commit_of_the_registry(client, shop):
    before = git(shop, "rev-parse", "HEAD").strip()
    assert list_op(client, "pin", list="shop", pin="1") == "/tasks?list=shop"
    registry = shop / "_tasks/lists.json"
    assert '"pinned": ["shop"]\n' in registry.read_text() and "default" not in registry.read_text()
    # Move first: the list goes to the front (pinned, if it wasn't), where a new task goes.
    assert client.post("/tasks/new-list", data={"name": "work"}, headers=HOST).status_code == 302
    assert list_op(client, "pin", list="work", pin="first") == "/tasks?list=work"
    assert '"pinned": ["work", "shop"]\n' in registry.read_text()
    assert list_op(client, "pin", list="shop", pin="first") == "/tasks?list=shop"
    assert '"pinned": ["shop", "work"]\n' in registry.read_text()
    assert list_op(client, "pin", list="shop", pin="0") == "/tasks?list=shop"
    assert '"pinned": ["work"]\n' in registry.read_text()
    assert git(shop, "log", "--format=%s", f"{before}..HEAD").splitlines() == [
        "Unpin list #shop", "Pin list #shop first", "Pin list #work first", "Create list #work", "Pin list #shop"]
    assert client.post("/tasks/pin-list", data={"list": "shop", "pin": "2"}, headers=HOST).status_code == 400
    assert client.post("/tasks/pin-list", data={"list": "nosuch", "pin": "1"}, headers=HOST).status_code == 404


def test_rename_tag_under_a_guide_needs_its_id(client, shop):
    commit(shop, "wiki-guide", "# Guide\n\nWrite well.\n")
    text, err = mcp(client, "rename_task_tag", tag="shop", new_tag="food")
    assert err and text.startswith("Nothing was written") and tasks_file(shop)[0]["tags"][0] == "shop"



def test_a_registry_naming_its_default_list_keeps_it_first(client, shop):
    """Until 2026-11 "default" named the list a new task goes to, whatever
    the order of the pins: it reads as the first pin (which takes no new
    task any more: it is the first pill), and the next write drops the key
    and keeps the order."""
    assert client.post("/tasks/new-list", data={"name": "work"}, headers=HOST).status_code == 302
    registry = shop / "_tasks/lists.json"
    registry.write_text(registry.read_text().replace("\n}\n", ',\n  "pinned": ["shop", "work"],\n  "default": "work"\n}\n'))
    html = client.get("/tasks", headers=HOST).get_data(as_text=True)
    assert "data-tasks-list" not in html
    assert html.index('data-place="list:work"') < html.index('data-place="list:shop"')
    assert client.post("/tasks/new-list", data={"name": "home"}, headers=HOST).status_code == 302
    text = registry.read_text()
    assert '"pinned": ["work", "shop", "home"]' in text and "default" not in text   # the new list pinned, last
