"""What still works by the names from before a rename: Deck is called Tasks,
so /deck, /api/deck and the _deck/ folder (moved at start-up) go on working.
The old [[deck …]] syntax: test_tasks_embed.py."""

import json
import subprocess

from tests.conftest import HOST, create_task


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
