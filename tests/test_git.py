"""Tests for Leaf's git-backed version history — commits on write, history,
revision/diff/restore, deleted pages, and the uncommitted-changes workflow.

Unlike the other test modules, the fixture here initialises a real git repo in
the temporary wiki directory, so every git code path actually executes.

Run with:
    pytest tests/test_git.py -v
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_git_test_")
os.environ.setdefault("WIKI_DATA", _SESSION_TMP)
os.environ["ALLOW_REMOTE"] = "1"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import server
import deck as deck_module

pytestmark = pytest.mark.skipif(not server.GIT_ENABLED, reason="git not installed")

HOST = {"Host": "localhost:5000"}


# ── Fixtures / helpers ────────────────────────────────────────────────────────


@pytest.fixture()
def wiki_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(server, "PAGES_DIR", tmp_path)
    monkeypatch.setattr(server, "UPLOADS_DIR", tmp_path / "_assets")
    monkeypatch.setattr(server, "DECK_FILE", tmp_path / "_deck.json")
    monkeypatch.setattr(server, "GIT_REMOTE", "")
    monkeypatch.setattr(deck_module, "DECK_FILE", tmp_path / "_deck.json")
    monkeypatch.setattr(deck_module, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(deck_module, "GIT_ENABLED", True)
    server._search_doc_cache.clear()
    server._page_meta_cache.clear()
    deck_module._task_docs_cache["key"] = None
    deck_module._task_docs_cache["docs"] = []
    (tmp_path / "_assets").mkdir()
    (tmp_path / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    server.git_setup()
    return tmp_path


@pytest.fixture()
def client(wiki_dir):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def git(wiki_dir: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=wiki_dir, capture_output=True, text=True, check=True,
                       env=server._GIT_ENV)  # no detached maintenance: it would linger as a zombie
    return r.stdout


def commit_messages(wiki_dir: Path) -> list[str]:
    """Commit subjects, newest first."""
    return git(wiki_dir, "log", "--format=%s").splitlines()


def head_sha(wiki_dir: Path) -> str:
    return git(wiki_dir, "rev-parse", "HEAD").strip()


def is_clean(wiki_dir: Path) -> bool:
    return git(wiki_dir, "status", "--porcelain", "--untracked-files=all").strip() == ""


def save(client, slug: str, content: str):
    return client.post(f"/edit/{slug}", data={"content": content}, headers=HOST)


# ===========================================================================
# git_setup
# ===========================================================================


class TestGitSetup:

    def test_initializes_repo_and_imports_existing_files(self, wiki_dir):
        assert (wiki_dir / ".git").is_dir()
        assert (wiki_dir / ".gitignore").exists()
        assert commit_messages(wiki_dir) == ["Initial import"]
        assert "index.md" in git(wiki_dir, "ls-files").splitlines()
        assert is_clean(wiki_dir)

    def test_rerun_is_idempotent(self, wiki_dir):
        server.git_setup()
        assert commit_messages(wiki_dir) == ["Initial import"]

    def test_rerun_restores_missing_identity(self, wiki_dir):
        git(wiki_dir, "config", "--unset", "user.name")
        server.git_setup()
        assert git(wiki_dir, "config", "user.name").strip() == "Leaf"

    def test_git_remote_added_then_updated(self, wiki_dir, monkeypatch):
        monkeypatch.setattr(server, "GIT_REMOTE", "https://example.invalid/a.git")
        server.git_setup()
        assert git(wiki_dir, "remote", "get-url", "origin").strip() == "https://example.invalid/a.git"
        monkeypatch.setattr(server, "GIT_REMOTE", "https://example.invalid/b.git")
        server.git_setup()
        assert git(wiki_dir, "remote", "get-url", "origin").strip() == "https://example.invalid/b.git"


# ===========================================================================
# Write operations commit
# ===========================================================================


class TestWritesCommit:

    def test_done_save_commits(self, client, wiki_dir):
        save(client, "notes", "# Notes\n\nbody\n")
        assert commit_messages(wiki_dir)[0] == "Update notes"
        assert is_clean(wiki_dir)

    def test_done_commits_dirty_file_even_if_content_unchanged(self, client, wiki_dir):
        # Auto-save wrote the file already; Done then posts identical content.
        client.post("/api/save/notes", data={"content": "# Notes\n"}, headers=HOST)
        assert not is_clean(wiki_dir)
        save(client, "notes", "# Notes\n")
        assert commit_messages(wiki_dir)[0] == "Update notes"
        assert is_clean(wiki_dir)

    def test_autosave_does_not_commit(self, client, wiki_dir):
        before = head_sha(wiki_dir)
        resp = client.post("/api/save/index", data={"content": "# Home\n\nchanged\n"}, headers=HOST)
        assert resp.status_code == 204
        assert head_sha(wiki_dir) == before
        assert not is_clean(wiki_dir)

    def test_create_page_commits(self, client, wiki_dir):
        client.post("/new", data={"slug": "Fresh Page"}, headers=HOST)
        assert commit_messages(wiki_dir)[0] == "Create fresh-page"

    def test_scratch_commit(self, client, wiki_dir):
        slug = client.post("/api/scratch", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        assert commit_messages(wiki_dir)[0] == f"Create {slug}"
        assert is_clean(wiki_dir)

    def test_delete_commits(self, client, wiki_dir):
        save(client, "doomed", "# Doomed\n")
        client.post("/delete/doomed", headers=HOST)
        assert commit_messages(wiki_dir)[0] == "Delete doomed"
        assert "doomed.md" not in git(wiki_dir, "ls-files")
        assert is_clean(wiki_dir)

    def test_rename_commits_move_and_updated_references_together(self, client, wiki_dir):
        save(client, "old", "# Old\n")
        save(client, "ref", "# Ref\n\nSee [[old]].\n")
        client.post("/rename/old", data={"new_name": "new"}, headers=HOST)
        assert commit_messages(wiki_dir)[0] == "Rename old to new"
        changed = git(wiki_dir, "show", "--name-status", "--format=", "HEAD").split()
        assert "old.md" in changed and "new.md" in changed and "ref.md" in changed
        assert is_clean(wiki_dir)

    def test_rename_commit_includes_updated_deck_tasks(self, client, wiki_dir):
        save(client, "old", "# Old\n")
        client.post("/api/deck", json={"title": "Task [[old]]"}, headers=HOST)
        client.post("/rename/old", data={"new_name": "new"}, headers=HOST)
        changed = git(wiki_dir, "show", "--name-only", "--format=", "HEAD").split()
        assert "_deck.json" in changed
        assert is_clean(wiki_dir)

    def test_upload_commits(self, client, wiki_dir):
        import io
        client.post("/api/upload", data={"file": (io.BytesIO(b"x"), "a.txt")},
                    content_type="multipart/form-data", headers=HOST)
        assert commit_messages(wiki_dir)[0] == "Upload a.txt"
        assert "_assets/a.txt" in git(wiki_dir, "ls-files").splitlines()

    def test_toggle_checkbox_leaves_change_uncommitted(self, client, wiki_dir):
        save(client, "todo", "# Todo\n\n- [ ] one\n")
        before = head_sha(wiki_dir)
        client.post("/api/toggle-checkbox/todo", data={"idx": 0}, headers=HOST)
        assert "- [x] one" in (wiki_dir / "todo.md").read_text()
        assert head_sha(wiki_dir) == before

    def test_deck_write_commits_deck_file(self, client, wiki_dir):
        client.post("/api/deck", json={"title": "Task"}, headers=HOST)
        assert "_deck.json" in git(wiki_dir, "ls-files").splitlines()
        assert is_clean(wiki_dir)


# ===========================================================================
# History / revision / diff / restore
# ===========================================================================


class TestHistory:

    def test_history_lists_commits_newest_first(self, client, wiki_dir):
        save(client, "p", "# P\n\nv1\n")
        save(client, "p", "# P\n\nv2\n")
        shas = git(wiki_dir, "log", "--format=%H", "--", "p.md").split()
        html = client.get("/history/p", headers=HOST).get_data(as_text=True)
        assert "History — P" in html
        assert html.index(f"/revision/p/{shas[0]}") < html.index(f"/revision/p/{shas[1]}")
        assert f"/diff/p/{shas[0]}" in html

    def test_history_follows_renames(self, client, wiki_dir):
        save(client, "before", "# Page\n\nv1\n")
        client.post("/rename/before", data={"new_name": "after"}, headers=HOST)
        entries = server.git_log(wiki_dir / "after.md")
        assert [e["msg"] for e in entries] == ["Rename before to after", "Update before"]

    def test_git_log_max_count(self, client, wiki_dir):
        save(client, "p", "v1\n")
        save(client, "p", "v2\n")
        assert len(server.git_log(wiki_dir / "p.md", max_count=1)) == 1

    def test_history_of_uncommitted_page_shows_empty_state(self, client, wiki_dir):
        (wiki_dir / "loose.md").write_text("# Loose\n", encoding="utf-8")
        resp = client.get("/history/loose", headers=HOST)
        assert resp.status_code == 200
        assert b"No history yet" in resp.data

    def test_history_of_unknown_page_404(self, client):
        assert client.get("/history/nope", headers=HOST).status_code == 404

    def test_history_of_deleted_page_links_back_to_deleted(self, client, wiki_dir):
        save(client, "gone", "# Gone\n")
        client.post("/delete/gone", headers=HOST)
        resp = client.get("/history/gone", headers=HOST)
        assert resp.status_code == 200
        assert b"/page/_deleted" in resp.data

    def test_page_view_shows_last_changed(self, client, wiki_dir):
        save(client, "p", "# P\n")
        html = client.get("/page/p", headers=HOST).get_data(as_text=True)
        assert "Last changed" in html
        assert "— uncommitted" not in html
        assert "commit-file-form" not in html

    def test_page_view_offers_commit_when_dirty(self, client, wiki_dir):
        save(client, "p", "# P\n")
        (wiki_dir / "p.md").write_text("# P\n\nedited on disk\n", encoding="utf-8")
        html = client.get("/page/p", headers=HOST).get_data(as_text=True)
        assert "— uncommitted" in html
        assert 'data-filepath="p.md"' in html
        # Commit is one more word on the meta line, not a button row of its own.
        meta = html.split('class="page-meta-right"')[1].split("</div></div>")[0]
        assert 'class="commit-link"' in meta and "btn-primary" not in meta


class TestRevision:

    def test_revision_renders_old_content(self, client, wiki_dir):
        save(client, "p", "# P\n\nold text\n")
        old = head_sha(wiki_dir)
        save(client, "p", "# P\n\nnew text\n")
        html = client.get(f"/revision/p/{old}", headers=HOST).get_data(as_text=True)
        assert "old text" in html and "new text" not in html
        assert f'action="/restore/p/{old}"' in html

    def test_revision_accepts_short_sha(self, client, wiki_dir):
        save(client, "p", "# P\n\nold text\n")
        resp = client.get(f"/revision/p/{head_sha(wiki_dir)[:7]}", headers=HOST)
        assert resp.status_code == 200

    @pytest.mark.parametrize("sha", ["HEAD", "abc", "zzzzzzz", "abc1234;rm"])
    def test_revision_rejects_invalid_sha(self, client, sha):
        assert client.get(f"/revision/index/{sha}", headers=HOST).status_code == 400

    def test_revision_unknown_sha_404(self, client):
        assert client.get("/revision/index/" + "0" * 40, headers=HOST).status_code == 404

    def test_revision_of_deletion_commit_shows_parent_content(self, client, wiki_dir):
        save(client, "gone", "# Gone\n\nlast words\n")
        before_delete = head_sha(wiki_dir)
        client.post("/delete/gone", headers=HOST)
        html = client.get(f"/revision/gone/{head_sha(wiki_dir)}", headers=HOST).get_data(as_text=True)
        assert "last words" in html
        # Restore must target the parent (where the file still exists).
        assert f'action="/restore/gone/{before_delete}"' in html


class TestDiff:

    def test_diff_marks_added_and_removed_lines(self, client, wiki_dir):
        save(client, "p", "# P\n\nalpha\n")
        save(client, "p", "# P\n\nbeta\n")
        html = client.get(f"/diff/p/{head_sha(wiki_dir)}", headers=HOST).get_data(as_text=True)
        assert '<span class="diff-del">-alpha</span>' in html
        assert '<span class="diff-add">+beta</span>' in html
        assert 'class="diff-hunk"' in html

    def test_diff_escapes_html(self, client, wiki_dir):
        save(client, "p", "<script>x</script>\n")
        html = client.get(f"/diff/p/{head_sha(wiki_dir)}", headers=HOST).get_data(as_text=True)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html

    def test_diff_rejects_invalid_sha(self, client):
        assert client.get("/diff/index/nothex!", headers=HOST).status_code == 400

    def test_diff_unknown_sha_404(self, client):
        assert client.get("/diff/index/" + "0" * 40, headers=HOST).status_code == 404


class TestRestore:

    def test_restore_writes_old_content_and_commits(self, client, wiki_dir):
        save(client, "p", "# P\n\nv1\n")
        v1 = head_sha(wiki_dir)
        save(client, "p", "# P\n\nv2\n")
        resp = client.post(f"/restore/p/{v1}", headers=HOST)
        assert resp.headers["Location"] == "/page/p"
        assert (wiki_dir / "p.md").read_text() == "# P\n\nv1\n"
        assert commit_messages(wiki_dir)[0] == f"Restore p to {v1[:7]}"
        assert is_clean(wiki_dir)

    def test_restore_rejects_invalid_sha(self, client):
        assert client.post("/restore/index/HEAD", headers=HOST).status_code == 400

    def test_restore_unknown_sha_404(self, client):
        assert client.post("/restore/index/" + "0" * 40, headers=HOST).status_code == 404

    def test_restore_rejects_path_traversal(self, client, wiki_dir):
        resp = client.post(f"/restore/../escape/{head_sha(wiki_dir)}", headers=HOST)
        assert resp.status_code in (400, 404)
        assert not (wiki_dir.parent / "escape.md").exists()


# ===========================================================================
# Deleted pages
# ===========================================================================


class TestDeletedPages:

    def test_empty_state(self, client):
        resp = client.get("/page/_deleted", headers=HOST)
        assert resp.status_code == 200
        assert b"No deleted pages in history." in resp.data

    def test_lists_deleted_page_with_title_and_restore(self, client, wiki_dir):
        save(client, "gone", "# Gone Title\n")
        client.post("/delete/gone", headers=HOST)
        sha = head_sha(wiki_dir)
        html = client.get("/page/_deleted", headers=HOST).get_data(as_text=True)
        assert "Gone Title" in html
        assert f'action="/restore/gone/{sha}"' in html

    def test_newest_deletion_first(self, client, wiki_dir):
        for slug in ("first", "second"):
            save(client, slug, f"# {slug}\n")
        client.post("/delete/first", headers=HOST)
        client.post("/delete/second", headers=HOST)
        assert [p["slug"] for p in server.list_deleted_pages()] == ["second", "first"]

    def test_page_deleted_twice_listed_once(self, client, wiki_dir):
        save(client, "gone", "# Gone\n")
        client.post("/delete/gone", headers=HOST)
        save(client, "gone", "# Gone again\n")
        client.post("/delete/gone", headers=HOST)
        pages = server.list_deleted_pages()
        assert [p["slug"] for p in pages] == ["gone"]
        assert pages[0]["title"] == "Gone again"

    def test_recreated_page_not_listed(self, client, wiki_dir):
        save(client, "gone", "# Gone\n")
        client.post("/delete/gone", headers=HOST)
        save(client, "gone", "# Back\n")
        assert server.list_deleted_pages() == []

    def test_deleted_assets_not_listed(self, client, wiki_dir):
        (wiki_dir / "_assets" / "note.md").write_text("x", encoding="utf-8")
        server.git_commit(wiki_dir / "_assets" / "note.md", "add")
        (wiki_dir / "_assets" / "note.md").unlink()
        server.git_commit(wiki_dir / "_assets" / "note.md", "rm")
        assert server.list_deleted_pages() == []

    def test_restore_from_deleted_list_roundtrip(self, client, wiki_dir):
        save(client, "gone", "# Gone\n\nbody\n")
        client.post("/delete/gone", headers=HOST)
        entry = server.list_deleted_pages()[0]
        # The listed sha is the deletion commit; the revision view resolves to
        # its parent for the actual restore.
        html = client.get(f"/revision/gone/{entry['sha']}", headers=HOST).get_data(as_text=True)
        restore_sha = html.split('action="/restore/gone/')[1].split('"')[0]
        client.post(f"/restore/gone/{restore_sha}", headers=HOST)
        assert (wiki_dir / "gone.md").read_text() == "# Gone\n\nbody\n"
        assert server.list_deleted_pages() == []

    def test_visiting_deleted_page_offers_restore(self, client, wiki_dir):
        save(client, "gone", "# Gone\n")
        client.post("/delete/gone", headers=HOST)
        resp = client.get("/page/gone", headers=HOST)
        assert resp.status_code == 200
        assert b"/restore/gone/" in resp.data or b"/revision/gone/" in resp.data

    def test_deleted_listed_in_pages_list(self, client):
        slugs = [p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()]
        assert "_deleted" in slugs


# ===========================================================================
# Uncommitted changes
# ===========================================================================


class TestUncommitted:

    def test_clean_repo(self, client):
        assert client.get("/api/uncommitted", headers=HOST).get_json() == []
        assert b"No uncommitted changes" in client.get("/uncommitted", headers=HOST).data

    def test_lists_modified_page_and_untracked_asset(self, client, wiki_dir):
        client.post("/api/save/index", data={"content": "# Home\n\nedit\n"}, headers=HOST)
        (wiki_dir / "_assets" / "pic.png").write_bytes(b"png")
        entries = {e["filepath"]: e for e in client.get("/api/uncommitted", headers=HOST).get_json()}
        assert entries["index.md"] == {"slug": "index", "title": "Home", "filepath": "index.md"}
        assert entries["_assets/pic.png"]["slug"] is None
        html = client.get("/uncommitted", headers=HOST).get_data(as_text=True)
        assert "/diff-uncommitted/index" in html
        assert "commit-all-btn" in html

    def test_lists_file_deleted_outside_leaf(self, client, wiki_dir):
        save(client, "p", "# P\n")
        (wiki_dir / "p.md").unlink()
        entries = client.get("/api/uncommitted", headers=HOST).get_json()
        assert entries == [{"slug": "p", "title": "P", "filepath": "p.md"}]

    def test_page_view_from_uncommitted_shows_commit_button(self, client, wiki_dir):
        client.post("/api/save/index", data={"content": "# Home\n\nedit\n"}, headers=HOST)
        html = client.get("/page/index?from=uncommitted", headers=HOST).get_data(as_text=True)
        assert 'data-filepath="index.md"' in html
        assert "← Uncommitted" in html

    def test_diff_uncommitted_modified(self, client, wiki_dir):
        client.post("/api/save/index", data={"content": "# Home\n\nedited\n"}, headers=HOST)
        html = client.get("/diff-uncommitted/index", headers=HOST).get_data(as_text=True)
        assert '<span class="diff-add">+edited</span>' in html
        assert '<span class="diff-del">-Welcome.</span>' in html
        assert 'data-filepath="index.md"' in html

    def test_diff_uncommitted_untracked(self, client, wiki_dir):
        (wiki_dir / "brand-new.md").write_text("# New\n\nhello\n", encoding="utf-8")
        html = client.get("/diff-uncommitted/brand-new", headers=HOST).get_data(as_text=True)
        assert '<span class="diff-add">+hello</span>' in html

    def test_diff_uncommitted_missing_page_404(self, client):
        assert client.get("/diff-uncommitted/nope", headers=HOST).status_code == 404

    def test_commit_file(self, client, wiki_dir):
        client.post("/api/save/index", data={"content": "# Home\n\nedit\n"}, headers=HOST)
        resp = client.post("/api/commit-file", json={"filepath": "index.md"}, headers=HOST)
        assert resp.get_json() == {"success": True}
        assert commit_messages(wiki_dir)[0] == "Save index"
        assert is_clean(wiki_dir)

    def test_commit_file_asset_message(self, client, wiki_dir):
        (wiki_dir / "_assets" / "pic.png").write_bytes(b"png")
        client.post("/api/commit-file", json={"filepath": "_assets/pic.png"}, headers=HOST)
        assert commit_messages(wiki_dir)[0] == "Update _assets/pic.png"

    def test_commit_file_requires_filepath(self, client):
        assert client.post("/api/commit-file", json={}, headers=HOST).status_code == 400

    @pytest.mark.parametrize("filepath", ["index.md", "../outside.md", "missing.md"])
    def test_commit_file_rejects_files_not_in_uncommitted_list(self, client, wiki_dir, filepath):
        before = head_sha(wiki_dir)
        resp = client.post("/api/commit-file", json={"filepath": filepath}, headers=HOST)
        assert resp.status_code == 400
        assert head_sha(wiki_dir) == before

    def test_commit_all_one_commit_per_file(self, client, wiki_dir):
        client.post("/api/save/index", data={"content": "# Home\n\nedit\n"}, headers=HOST)
        (wiki_dir / "other.md").write_text("# Other\n", encoding="utf-8")
        resp = client.post("/api/commit-all", headers=HOST).get_json()
        assert resp["committed"] == 2
        assert sorted(resp["files"]) == ["index.md", "other.md"]
        assert sorted(commit_messages(wiki_dir)[:2]) == ["Save index", "Save other"]
        assert is_clean(wiki_dir)


# ===========================================================================
# Git disabled — graceful degradation
# ===========================================================================


class TestGitDisabled:

    @pytest.fixture()
    def nogit(self, client, monkeypatch):
        monkeypatch.setattr(server, "GIT_ENABLED", False)
        return client

    @pytest.mark.parametrize("url", [
        "/history/index", "/revision/index/abcdef1", "/diff/index/abcdef1",
        "/page/_deleted", "/uncommitted", "/diff-uncommitted/index",
    ])
    def test_git_views_404(self, nogit, url):
        assert nogit.get(url, headers=HOST).status_code == 404

    def test_restore_404(self, nogit):
        assert nogit.post("/restore/index/abcdef1", headers=HOST).status_code == 404

    @pytest.mark.parametrize("url", ["/api/commit-all", "/api/commit-file"])
    def test_commit_endpoints_400(self, nogit, url):
        assert nogit.post(url, json={"filepath": "index.md"}, headers=HOST).status_code == 400

    def test_save_still_works_without_commit(self, nogit, wiki_dir):
        before = head_sha(wiki_dir)
        save(nogit, "p", "# P\n")
        assert (wiki_dir / "p.md").exists()
        assert head_sha(wiki_dir) == before

    def test_page_view_has_no_meta(self, nogit):
        html = nogit.get("/page/index", headers=HOST).get_data(as_text=True)
        assert "Last changed" not in html

    def test_helpers_return_empty(self, nogit, wiki_dir):
        assert server.git_log(wiki_dir / "index.md") == []
        assert server.git_uncommitted_files() == []
        assert server.list_deleted_pages() == []


# ===========================================================================
# Pure helpers
# ===========================================================================


class TestHelpers:

    @pytest.mark.parametrize("delta,expected", [
        (timedelta(seconds=5), "just now"),
        (timedelta(minutes=1, seconds=5), "1 minute ago"),
        (timedelta(minutes=5, seconds=5), "5 minutes ago"),
        (timedelta(hours=1, minutes=1), "1 hour ago"),
        (timedelta(hours=3, minutes=1), "3 hours ago"),
        (timedelta(days=1, minutes=1), "1 day ago"),
        (timedelta(days=10, minutes=1), "10 days ago"),
        (timedelta(days=65), "2 months ago"),
        (timedelta(days=366), "1 year ago"),
        (timedelta(days=800), "2 years ago"),
    ])
    def test_relative_time(self, delta, expected):
        assert server.relative_time(datetime.now() - delta) == expected

    def test_diff_to_html_empty(self):
        assert server._diff_to_html("") == '<span class="diff-ctx">(no changes)</span>'

    def test_diff_to_html_file_headers_are_context(self):
        out = server._diff_to_html("--- a/x\n+++ b/x\n context")
        assert out.count('class="diff-ctx"') == 3

    @pytest.mark.parametrize("filepath,expected", [
        ("notes.md", "Save notes"),
        ("journal/day.md", "Save journal/day"),
        ("_assets/a.png", "Update _assets/a.png"),
    ])
    def test_commit_message(self, filepath, expected):
        assert server._commit_message(filepath) == expected



class TestGitDoesNotDetach:
    def test_maintenance_runs_in_the_foreground(self, wiki_dir):
        # Regression: git (2.47+) detached its auto-maintenance after each
        # commit; with Leaf as the container's PID 1 nobody reaped it, and
        # zombies piled up to the PID limit.
        for key in ("maintenance.autoDetach", "gc.autoDetach"):
            assert server._git("git", "config", "--get", key).stdout.strip() == "false"
