"""What still works by the names from before a rename: Deck is called Tasks,
so /deck, /api/deck and the _deck/ folder (moved at start-up) go on working;
Scratch is called Notes, so /page/_scratch, /api/scratch, ?scratch=1 and
MCP's file_scratch_note and scratch arguments do too; Leaf is called Zone,
so MCP's leaf_help and the LEAF_… environment variables do too (run.sh's
side: test_run_sh.py). The old [[deck …]] syntax: test_tasks_embed.py."""

import json
import subprocess
import urllib.request

import pytest

from tests import settings
from tests.conftest import HOST, create_task, write_page
from tests.goserver import GoServer, _flags, _settings
from tests.test_mcp import TOKEN, call, rpc


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True).stdout


def task_file(title, task_id="abcd1234"):
    return json.dumps([{"id": task_id, "title": title, "tags": [], "created_at": "2026-01-01T09:00:00"}]) + "\n"


# ── /deck and /api/deck ─────────────────────────────────────────────────────────

class TestOldPaths:
    def test_deck_redirects_to_tasks_with_its_query(self, client):
        resp = client.get("/deck", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/tasks"
        resp = client.get("/deck?list=shop&task=abcd1234", headers=HOST)
        assert resp.headers["Location"] == "/tasks?list=shop&task=abcd1234"

    def test_the_tasks_module_links_to_tasks(self, client):
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert 'href="/tasks" class="module-link" data-module="tasks"' in body
        assert 'href="/deck"' not in body

    def test_api_deck_answers_as_api_tasks(self, client):
        tid = client.post("/api/deck", json={"title": "Old client"}, headers=HOST).get_json()["id"]
        assert [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()] == ["Old client"]
        assert client.put(f"/api/deck/{tid}", json={"title": "Renamed"}, headers=HOST).status_code == 200
        assert [t["title"] for t in client.get("/api/deck", headers=HOST).get_json()] == ["Renamed"]
        assert client.get("/api/deck/due-count", headers=HOST).get_json()["count"] == 0
        assert client.delete(f"/api/deck/{tid}", headers=HOST).status_code == 204
        assert client.get("/api/tasks", headers=HOST).get_json() == []

    def test_api_deck_keeps_the_methods_and_the_checks(self, client):
        assert client.get("/api/deck/nope", headers=HOST).status_code in (404, 405)
        evil = {**HOST, "Origin": "https://evil.example"}
        assert client.post("/api/deck", json={"title": "Spam"}, headers=evil).status_code == 403
        assert client.get("/api/tasks", headers=HOST).get_json() == []


# ── _deck/ → _tasks/ ────────────────────────────────────────────────────────────

class TestTasksFolderMove:
    def _old_wiki(self, wiki, *private):
        """A wiki as an older Zone left it: the tasks in _deck/, committed."""
        (wiki / "_tasks").rmdir()
        (wiki / "_deck").mkdir()
        (wiki / "_deck" / "tasks.json").write_text(task_file("Shared one"))
        for user in private:
            (wiki / "private" / user / "_deck").mkdir(parents=True)
            (wiki / "private" / user / "_deck" / "tasks.json").write_text(task_file(f"{user}'s", "eeee1111"))
        git(wiki, "add", "-A")
        git(wiki, "commit", "-qm", "Tasks in _deck/")

    def test_start_up_moves_deck_to_tasks_in_one_commit(self, git_wiki_dir, client):
        self._old_wiki(git_wiki_dir, "alice")
        client.live.stop()
        assert [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()] == ["Shared one"]
        assert not (git_wiki_dir / "_deck").exists()
        assert "Shared one" in (git_wiki_dir / "_tasks" / "tasks.json").read_text()
        assert not (git_wiki_dir / "private/alice/_deck").exists()
        assert "alice's" in (git_wiki_dir / "private/alice/_tasks/tasks.json").read_text()
        # (Then Zone writes the hand-made file in its own format, a commit of its own.)
        [move] = git(git_wiki_dir, "log", "--format=%H", "--fixed-strings",
                     "--grep=Move _deck/ to _tasks/: Deck is called Tasks").split()
        moved = git(git_wiki_dir, "show", "--name-status", "--format=", move).split("\n")
        assert "R100\t_deck/tasks.json\t_tasks/tasks.json" in moved
        assert "R100\tprivate/alice/_deck/tasks.json\tprivate/alice/_tasks/tasks.json" in moved
        assert git(git_wiki_dir, "status", "--porcelain") == ""

    def test_the_move_happens_once(self, git_wiki_dir, client):
        self._old_wiki(git_wiki_dir)
        client.live.stop()
        client.live.ensure()
        head = git(git_wiki_dir, "rev-parse", "HEAD")
        client.live.stop()
        client.live.ensure()
        assert git(git_wiki_dir, "rev-parse", "HEAD") == head
        create_task(client, title="After")
        assert "After" in (git_wiki_dir / "_tasks" / "tasks.json").read_text()
        assert not (git_wiki_dir / "_deck").exists()

    def test_a_tasks_folder_already_there_wins_and_deck_stays(self, git_wiki_dir, client):
        self._old_wiki(git_wiki_dir)
        (git_wiki_dir / "_tasks").mkdir()
        (git_wiki_dir / "_tasks" / "tasks.json").write_text(task_file("New one", "bbbb2222"))
        client.live.stop()
        assert [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()] == ["New one"]
        assert "Shared one" in (git_wiki_dir / "_deck" / "tasks.json").read_text()
        assert "_deck/ and _tasks/ are both there" in client.live.log.read_text()

    def test_without_git_the_folder_moves_too(self, wiki_dir, client):
        client.live.stop()
        (wiki_dir / "_tasks").rmdir()
        (wiki_dir / "_deck").mkdir()
        (wiki_dir / "_deck" / "tasks.json").write_text(task_file("Plain files"))
        assert [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()] == ["Plain files"]
        assert (wiki_dir / "_tasks" / "tasks.json").exists() and not (wiki_dir / "_deck").exists()


# ── /page/_scratch, /api/scratch, MCP's old names ───────────────────────────────

class TestOldNotesNames:
    NOTE = "2026-09-02_10-00-00_groceries"

    def test_page_scratch_redirects_to_notes_with_its_query(self, client):
        resp = client.get("/page/_scratch", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/notes"
        assert client.get("/page/_scratch?q=milk", headers=HOST).headers["Location"] == "/notes?q=milk"

    def test_the_notes_module_links_to_notes(self, client):
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert 'href="/notes" class="action-module module-link" data-module="notes"' in body
        assert "/page/_scratch" not in body
        assert '<body data-module="notes">' in client.get("/notes", headers=HOST).get_data(as_text=True)

    def test_api_scratch_still_makes_a_note(self, client, wiki_dir):
        slug = client.post("/api/scratch", data={"title": "Old client"}, headers=HOST).get_json()["slug"]
        assert slug.endswith("_old-client") and (wiki_dir / f"{slug}.md").exists()

    def test_search_still_takes_scratch_1(self, client, wiki_dir):
        write_page(wiki_dir, self.NOTE, "# Groceries\n\nmilk\n")
        write_page(wiki_dir, "dairy", "# Dairy\n\nmilk\n")
        for param in ("notes", "scratch"):
            hits = client.get("/api/search", query_string={"q": "milk", param: "1"}, headers=HOST).get_json()
            assert [h["slug"] for h in hits] == [self.NOTE], param


class TestOldCookie:
    def test_sharing_mode_from_the_leaf_sharing_cookie(self, client):
        client.set_cookie("leaf-sharing", "1")
        assert 'class="sharing"' in client.get("/page/index", headers=HOST).get_data(as_text=True)


class TestOldMcpNames:
    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_MCP_TOKEN", TOKEN)

    def test_tools_list_names_only_file_note(self, client):
        names = [t["name"] for t in rpc(client, "tools/list").get_json()["result"]["tools"]]
        assert "file_note" in names and "file_scratch_note" not in names

    def test_scratch_arguments_and_file_scratch_note_still_work(self, client, git_wiki_dir):
        text, err = call(client, "write_page", slug="Alex prep", content="Roadmap", scratch=True)
        assert not err and "Created note" in text
        note = next(p.stem for p in git_wiki_dir.glob("*_alex-prep.md"))
        assert note in call(client, "list_pages", scratch=True)[0]
        assert note in call(client, "search_wiki", query="roadmap", scratch=True)[0]
        text, err = call(client, "file_scratch_note", slug=note, name="Alex prep")
        assert not err and (git_wiki_dir / "alex-prep.md").exists(), text

    def test_leaf_help_answers_as_zone_help(self, client):
        names = [t["name"] for t in rpc(client, "tools/list").get_json()["result"]["tools"]]
        assert "zone_help" in names and "leaf_help" not in names
        new, old = (call(client, tool, query="tab labels") for tool in ("zone_help", "leaf_help"))
        assert old == new and not new[1]


# ── LEAF_… environment variables ────────────────────────────────────────────────

def test_zone_starts_from_the_old_variable_names_and_says_to_rename_them(wiki_dir, tmp_path):
    write_page(wiki_dir, "old-env", "# Old env\n")
    env = {k.replace("ZONE_", "LEAF_", 1) if k in ("ZONE_DATA", "ZONE_BUILD") else k: v
           for k, v in _settings().items()}
    assert "ZONE_DATA" not in env and env["LEAF_DATA"] == str(wiki_dir)
    log = tmp_path / "server.log"
    server = GoServer(env, log, flags=_flags())
    try:
        req = urllib.request.Request(f"http://{server.host}:{server.port}/page/old-env", headers=HOST)
        assert "Old env" in urllib.request.urlopen(req).read().decode()
    finally:
        server.stop()
    text = log.read_text()
    assert "LEAF_DATA is the name from before Leaf was called Zone: call it ZONE_DATA in .env" in text
    assert text.count("LEAF_DATA is the name") == 1
