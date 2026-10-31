"""Each page write through both doors, the browser's route and the MCP
tool, on the same wiki: the same checks and the same care for text not
yet in git, whichever door it came through (go/pageops.go)."""

import subprocess

import pytest

from tests import settings
from tests.conftest import HOST, write_page

pytestmark = pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")

TOKEN = "test-token"
DOORS = ["browser", "mcp"]


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "LEAF_MCP_TOKEN", TOKEN)
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


def until_step(request, door, step):
    """The browser's route still does it its own way (docs/plans/operations-layer.md)."""
    if door == "browser":
        request.applymarker(pytest.mark.xfail(strict=True, reason=f"the route, until step {step}"))


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

    def test_rename_commits_it_apart(self, client, wiki, door, request):
        """The rename's own commit only moves the page."""
        until_step(request, door, 4)
        commit(wiki, "p", "# P\n\nCommitted.\n")
        write_page(wiki, "p", "# P\n\nOnly on disk.\n")
        assert rename(client, door, "p", "q")
        assert (wiki / "q.md").read_text() == "# P\n\nOnly on disk.\n"
        assert subjects(wiki)[1] == "Save p"
        stat = git(wiki, "show", "-M", "--format=", "--name-status", "HEAD")
        assert stat.startswith("R100")


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
        monkeypatch.setattr(settings, "LEAF_MCP_TOKEN", "")
        monkeypatch.setattr(settings, "LEAF_USERS", {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "")})
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
