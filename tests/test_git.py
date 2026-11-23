"""Tests for Zonewiki's git-backed version history — commits on write, history,
revision/diff/restore, deleted pages, and the uncommitted-changes workflow.

Unlike the other test modules, the wiki here is a real git repo
(git_wiki_dir), so every git code path actually executes.
"""

import html
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import HOST, git_setup, write_page
from tests.goserver import GoServer, _flags, _settings

pytestmark = pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")


@pytest.fixture()
def wiki_dir(git_wiki_dir):
    return git_wiki_dir


def git(wiki_dir: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=wiki_dir, capture_output=True, text=True, check=True,
                       env=settings.GIT_ENV)  # no detached maintenance: it would linger as a zombie
    return r.stdout


def commit_messages(wiki_dir: Path) -> list[str]:
    """Commit subjects, newest first."""
    return git(wiki_dir, "log", "--format=%s").splitlines()


def head_sha(wiki_dir: Path) -> str:
    return git(wiki_dir, "rev-parse", "HEAD").strip()


def is_clean(wiki_dir: Path) -> bool:
    return git(wiki_dir, "status", "--porcelain", "--untracked-files=all").strip() == ""


def deleted_pages(client) -> list[tuple[str, str, str]]:
    """The Deleted Pages list: (slug, title, sha), newest deletion first."""
    page = client.get("/page/_deleted", headers=HOST).get_data(as_text=True)
    return [(slug, html.unescape(title), sha) for sha, title, slug in re.findall(
        r'<a href="/revision/[^"]+/(\w+)" class="deleted-page-title">([^<]*)</a><span class="note-date">([^ ]+) ',
        page)]


def history_labels(client, slug: str) -> list[str]:
    page = client.get(f"/history/{slug}", headers=HOST).get_data(as_text=True)
    return [html.unescape(x) for x in re.findall(r'<span class="hist-what">([^<]*)</span>', page)]


def save(client, slug: str, content: str):
    return client.post(f"/edit/{slug}", data={"content": content}, headers=HOST)


# ===========================================================================
# git_setup
# ===========================================================================


class TestGitSetup:

    def test_initializes_repo_and_imports_existing_files(self, wiki_dir):
        assert (wiki_dir / ".git").is_dir()
        assert (wiki_dir / ".gitignore").exists()
        assert commit_messages(wiki_dir) == ["Note the format of the wiki's files", "Initial import"]
        assert {"index.md", ".zone-format"} <= set(git(wiki_dir, "ls-files").splitlines())
        assert (wiki_dir / ".zone-format").read_text() == "1\n"
        assert is_clean(wiki_dir)

    def test_rerun_is_idempotent(self, wiki_dir, tmp_path_factory):
        git_setup(tmp_path_factory)
        assert commit_messages(wiki_dir) == ["Note the format of the wiki's files", "Initial import"]

    def test_a_wiki_a_newer_zone_wrote_is_refused(self, wiki_dir, tmp_path_factory):
        """A Zonewiki that knows an older format than .zone-format says doesn't
        start, and changes nothing (The data format in deployment.md)."""
        (wiki_dir / ".zone-format").write_text("99\n")
        write_page(wiki_dir, "later", "# Later\n")
        head = head_sha(wiki_dir)
        log = tmp_path_factory.mktemp("go") / "refused.log"
        with pytest.raises(RuntimeError):
            GoServer(_settings(), log, flags=_flags())
        assert "the wiki's files are in format 99 (.zone-format), which a newer Zonewiki wrote" in log.read_text()
        assert head_sha(wiki_dir) == head and (wiki_dir / ".zone-format").read_text() == "99\n"

    def test_rerun_restores_missing_identity(self, wiki_dir, tmp_path_factory):
        git(wiki_dir, "config", "--unset", "user.name")
        git_setup(tmp_path_factory)
        assert git(wiki_dir, "config", "user.name").strip() == "Zonewiki"

    def test_git_remote_added_then_updated(self, wiki_dir, monkeypatch, tmp_path_factory):
        monkeypatch.setattr(settings, "GIT_REMOTE", "https://example.invalid/a.git")
        git_setup(tmp_path_factory)
        assert git(wiki_dir, "remote", "get-url", "origin").strip() == "https://example.invalid/a.git"
        monkeypatch.setattr(settings, "GIT_REMOTE", "https://example.invalid/b.git")
        git_setup(tmp_path_factory)
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
        assert resp.status_code == 200
        assert head_sha(wiki_dir) == before
        assert not is_clean(wiki_dir)

    def test_create_page_commits(self, client, wiki_dir):
        client.post("/new", data={"slug": "Fresh Page"}, headers=HOST)
        assert commit_messages(wiki_dir)[0] == "Create fresh-page"

    def test_quicknote_commit(self, client, wiki_dir):
        slug = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
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

    def test_rename_commit_includes_updated_tasks_tasks(self, client, wiki_dir):
        save(client, "old", "# Old\n")
        client.post("/api/tasks", json={"title": "Task [[old]]"}, headers=HOST)
        client.post("/rename/old", data={"new_name": "new"}, headers=HOST)
        changed = git(wiki_dir, "show", "--name-only", "--format=", "HEAD").split()
        assert "_tasks/tasks.json" in changed
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

    def test_tasks_write_commits_tasks_file(self, client, wiki_dir):
        client.post("/api/tasks", json={"title": "Task"}, headers=HOST)
        assert "_tasks/tasks.json" in git(wiki_dir, "ls-files").splitlines()
        assert is_clean(wiki_dir)


# ===========================================================================
# History / revision / diff / restore
# ===========================================================================


class TestHomePage:

    def test_a_wiki_without_index_gets_one(self, client, wiki_dir, tmp_path_factory):
        (wiki_dir / "index.md").unlink()
        git(wiki_dir, "commit", "-qam", "no home")
        git_setup(tmp_path_factory)   # a start
        assert (wiki_dir / "index.md").read_text() == "# Home\n\n"
        assert commit_messages(wiki_dir)[0] == "Create index"
        assert client.get("/page/index", headers=HOST).status_code == 200

    def test_an_existing_index_is_left_alone(self, wiki_dir, tmp_path_factory):
        before = head_sha(wiki_dir)
        git_setup(tmp_path_factory)
        assert (wiki_dir / "index.md").read_text() == "# Home\n\nWelcome.\n"
        assert head_sha(wiki_dir) == before


class TestTasksFolder:

    def test_a_new_wiki_gets_the_folder_on_first_write(self, client, wiki_dir):
        (wiki_dir / "_tasks").rmdir()   # as in a wiki without tasks yet
        client.post("/api/tasks", json={"title": "First"}, headers=HOST)
        assert (wiki_dir / "_tasks" / "tasks.json").exists()
        assert not list(wiki_dir.glob("_deck*.json"))


class TestHistory:

    def test_history_lists_commits_newest_first(self, client, wiki_dir):
        save(client, "p", "# P\n\nv1\n")
        save(client, "p", "# P\n\nv2\n")
        shas = git(wiki_dir, "log", "--format=%H", "--", "p.md").split()
        html = client.get("/history/p", headers=HOST).get_data(as_text=True)
        assert "History — P" in html
        # The newest is the page as it is now: marked Current, opening the page.
        rows = html.split('<ol class="hist-list">', 1)[1].split("</ol>", 1)[0].split("<li ")[1:]
        assert 'class="hist-row hist-current"' in rows[0] and 'href="/page/p"' in rows[0]
        assert '<span class="hist-badge">Current</span>' in rows[0]
        assert f"/revision/p/{shas[1]}" in rows[1] and "Current" not in rows[1]
        assert f"/diff/p/{shas[0]}" in rows[0] and f"/diff/p/{shas[1]}" in rows[1]
        # What each save did, in words, and how much it changed.
        assert '<span class="hist-what">Edited</span>' in rows[0]
        assert '<span class="hist-add">+1</span> <span class="hist-del">−1</span>' in rows[0]

    def test_history_names_what_each_save_did(self, client, wiki_dir):
        save(client, "draft", "# Talk\n\nv1\n")
        client.post("/rename/draft", data={"new_name": "talk"}, headers=HOST)
        old = head_sha(wiki_dir)
        save(client, "talk", "# Talk\n\nv2\n")
        client.post(f"/restore/talk/{old}", headers=HOST)
        html = client.get("/history/talk", headers=HOST).get_data(as_text=True)
        what = re.findall(r'<span class="hist-what">([^<]*)</span>', html)
        assert what[0].startswith("Restored the version of 20")
        assert what[1:] == ["Edited", "Renamed from “draft”", "Edited"]

    def test_promote_commits_and_names_it_in_history(self, client, wiki_dir):
        note = "2026-10-08_14-03-11_notes"
        save(client, note, "# Notes\n\nv1\n")
        save(client, "ref", f"# Ref\n\nSee [[{note}]].\n")
        save(client, "hub", "# Hub\n")
        client.post(f"/promote/{note}", data={"name": "Notes", "link_from": "hub"}, headers=HOST)
        assert commit_messages(wiki_dir)[0] == f"Promote {note} to notes"
        changed = git(wiki_dir, "show", "--name-status", "--format=", "HEAD").split()
        assert f"{note}.md" in changed and "notes.md" in changed and "ref.md" in changed
        assert "hub.md" not in changed                  # linked by pasting, in the editor
        assert is_clean(wiki_dir)
        html = client.get("/history/notes", headers=HOST).get_data(as_text=True)
        what = re.findall(r'<span class="hist-what">([^<]*)</span>', html)
        assert what == ["Moved from Notes", "Edited"]

    def test_append_commits_and_names_it_in_history(self, client, wiki_dir):
        note = "2026-10-08_14-03-11_notes"
        save(client, note, "# Notes\n\nv1\n")
        save(client, "garden", "# Garden\n")
        save(client, "ref", f"# Ref\n\nSee [[{note}]].\n")
        client.post(f"/append/{note}", data={"page": "garden", "heading": "Notes"}, headers=HOST)
        assert commit_messages(wiki_dir)[0] == f"Append {note} to garden"
        changed = git(wiki_dir, "show", "--name-status", "--format=", "HEAD").split()
        assert f"{note}.md" in changed and "garden.md" in changed and "ref.md" in changed
        assert is_clean(wiki_dir)
        html = client.get("/history/garden", headers=HOST).get_data(as_text=True)
        assert re.findall(r'<span class="hist-what">([^<]*)</span>', html) == ["Note appended", "Edited"]
        # The note is in Deleted Pages, so it can be restored.
        assert note in client.get("/page/_deleted", headers=HOST).get_data(as_text=True)

    def test_history_with_edits_not_committed_yet(self, client, wiki_dir):
        # Edits on disk since the last commit: a "Now" row is the current
        # one, and the newest commit is an old version like the rest.
        save(client, "p", "# P\n\nv1\n")
        sha = head_sha(wiki_dir)
        (wiki_dir / "p.md").write_text("# P\n\nedited on disk\n", encoding="utf-8")
        html = client.get("/history/p", headers=HOST).get_data(as_text=True)
        rows = html.split('<ol class="hist-list">', 1)[1].split("<li ")[1:]
        assert "Not committed yet" in rows[0] and 'href="/page/p"' in rows[0]
        assert f'href="/revision/p/{sha}"' in rows[1] and "Current" not in rows[1]

    def test_history_follows_renames(self, client, wiki_dir):
        save(client, "before", "# Page\n\nv1\n")
        client.post("/rename/before", data={"new_name": "after"}, headers=HOST)
        assert history_labels(client, "after") == ["Renamed from “before”", "Edited"]

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

    def test_last_changed_follows_changes_made_outside_zone(self, client, wiki_dir):
        """What git says of a page is kept between views: a change on disk
        and a commit in a terminal still show at the next view."""
        save(client, "p", "# P\n")
        view = lambda: client.get("/page/p", headers=HOST).get_data(as_text=True)
        assert "commit-file-form" not in view() and "commit-file-form" not in view()
        (wiki_dir / "p.md").write_text("# P\n\nEdited in an editor.\n", encoding="utf-8")
        assert "commit-file-form" in view() and "commit-file-form" in view()
        git(wiki_dir, "commit", "-q", "-m", "by hand", "--", "p.md")
        html = view()
        assert "Last changed" in html and "commit-file-form" not in html

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

    def test_revision_says_where_it_stands_and_steps_through(self, client, wiki_dir):
        save(client, "p", "# P\n\nv1\n")
        v1 = head_sha(wiki_dir)
        save(client, "p", "# P\n\nv2\n")
        v2 = head_sha(wiki_dir)
        save(client, "p", "# P\n\nv3\n")
        v3 = head_sha(wiki_dir)
        html = client.get(f"/revision/p/{v2}", headers=HOST).get_data(as_text=True)
        assert "· 1 version back" in html
        assert f'<a href="/revision/p/{v1}" class="btn">← Older</a>' in html
        assert '<a href="/page/p" class="btn">Current →</a>' in html     # the next is the page now
        assert f'href="/diff/p/{v2}"' in html
        # Restoring asks first, and is a plain button, not the main action.
        form = html.split('class="version-restore"', 1)[1].split("</form>", 1)[0]
        assert "data-confirm=" in form and 'class="btn">Restore this version' in form
        html = client.get(f"/revision/p/{v1}", headers=HOST).get_data(as_text=True)
        assert "· 2 versions back" in html and '<span class="btn" aria-disabled="true">← Older</span>' in html
        assert f'<a href="/revision/p/{v2}" class="btn">Newer →</a>' in html
        # The current version can't be restored: it is the page.
        html = client.get(f"/revision/p/{v3}", headers=HOST).get_data(as_text=True)
        assert "The current version" in html and "/restore/" not in html

    def test_versions_from_before_a_rename(self, client, wiki_dir):
        # A version saved under the page's old name is found under it:
        # viewing, its changes and restoring it used to 404.
        save(client, "draft", "# Talk\n\nold words\n")
        old = head_sha(wiki_dir)
        client.post("/rename/draft", data={"new_name": "talk"}, headers=HOST)
        renamed = head_sha(wiki_dir)
        save(client, "talk", "# Talk\n\nnew words\n")
        resp = client.get(f"/revision/talk/{old}", headers=HOST)
        assert resp.status_code == 200 and "old words" in resp.get_data(as_text=True)
        assert client.get(f"/diff/talk/{old}", headers=HOST).status_code == 200
        # The rename itself changed no text: not the whole page as new.
        html = client.get(f"/diff/talk/{renamed}", headers=HOST).get_data(as_text=True)
        assert "diff-add" not in html and "(no changes)" in html
        assert client.post(f"/restore/talk/{old}", headers=HOST).status_code == 302
        assert "old words" in (wiki_dir / "talk.md").read_text()

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
        assert '<span class="diff-hunk">⋯</span>' in html
        # No git file headers; it says which save and offers that version.
        assert "diff --git" not in html and "+++ b/" not in html
        assert f'href="/revision/p/{head_sha(wiki_dir)}"' in html and "/restore/" not in html

    def test_diff_escapes_html(self, client, wiki_dir):
        save(client, "p", "<script>x</script>\n")
        html = client.get(f"/diff/p/{head_sha(wiki_dir)}", headers=HOST).get_data(as_text=True)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html

    def test_diff_rejects_invalid_sha(self, client):
        assert client.get("/diff/index/nothex!", headers=HOST).status_code == 400

    def test_diff_unknown_sha_404(self, client):
        assert client.get("/diff/index/" + "0" * 40, headers=HOST).status_code == 404

    def test_diffs_hidden_in_sharing_mode(self, client, wiki_dir, monkeypatch):
        # Regression: history and revisions said "Hidden page", but a diff
        # showed what a page not for sharing said.
        monkeypatch.setattr(settings, "NOSHARE_TAGS", frozenset({"noshare"}))
        save(client, "secret", "---\ntags: noshare\n---\n# Secret\n\nold words\n")
        save(client, "secret", "---\ntags: noshare\n---\n# Secret\n\nnew words\n")
        client.post("/api/save/secret", data={"content": "---\ntags: noshare\n---\n# Secret\n\nunsaved words\n"},
                    headers=HOST)
        client.set_cookie("zone-sharing", "1")
        for url in (f"/diff/secret/{head_sha(wiki_dir)}", "/diff-uncommitted/secret"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            assert "Hidden page" in body and "words" not in body, url


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
        assert [p[0] for p in deleted_pages(client)] == ["second", "first"]

    def test_page_deleted_twice_listed_once(self, client, wiki_dir):
        save(client, "gone", "# Gone\n")
        client.post("/delete/gone", headers=HOST)
        save(client, "gone", "# Gone again\n")
        client.post("/delete/gone", headers=HOST)
        assert [p[:2] for p in deleted_pages(client)] == [("gone", "Gone again")]

    def test_recreated_page_not_listed(self, client, wiki_dir):
        save(client, "gone", "# Gone\n")
        client.post("/delete/gone", headers=HOST)
        save(client, "gone", "# Back\n")
        assert deleted_pages(client) == []

    def test_deleted_assets_not_listed(self, client, wiki_dir):
        (wiki_dir / "_assets" / "note.md").write_text("x", encoding="utf-8")
        git(wiki_dir, "add", "_assets/note.md")
        git(wiki_dir, "commit", "-qm", "add")
        git(wiki_dir, "rm", "-q", "_assets/note.md")
        git(wiki_dir, "commit", "-qm", "rm")
        assert deleted_pages(client) == []

    def test_restore_from_deleted_list_roundtrip(self, client, wiki_dir):
        save(client, "gone", "# Gone\n\nbody\n")
        client.post("/delete/gone", headers=HOST)
        sha = deleted_pages(client)[0][2]
        # The listed sha is the deletion commit; the revision view resolves to
        # its parent for the actual restore.
        page = client.get(f"/revision/gone/{sha}", headers=HOST).get_data(as_text=True)
        restore_sha = page.split('action="/restore/gone/')[1].split('"')[0]
        client.post(f"/restore/gone/{restore_sha}", headers=HOST)
        assert (wiki_dir / "gone.md").read_text() == "# Gone\n\nbody\n"
        assert deleted_pages(client) == []

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
        header = html.split('<header class="page-header">', 1)[1].split("</header>", 1)[0]
        assert 'id="commit-all-btn"' in header     # with the title, not loose under the list

    def test_lists_file_deleted_outside_zone(self, client, wiki_dir):
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

    def test_file_names_with_spaces_and_umlauts(self, client, wiki_dir):
        # Regression: git quotes such names in plain --porcelain output
        # ("_assets/my \303\274.pdf"), so the list showed the quoted name and
        # committing it failed.
        (wiki_dir / "_assets" / "my ü.pdf").write_bytes(b"pdf")
        entries = client.get("/api/uncommitted", headers=HOST).get_json()
        assert [e["filepath"] for e in entries] == ["_assets/my ü.pdf"]
        resp = client.post("/api/commit-file", json={"filepath": "_assets/my ü.pdf"}, headers=HOST)
        assert resp.get_json() == {"success": True}
        assert is_clean(wiki_dir)

    def test_commit_file_without_json_body(self, client):
        resp = client.post("/api/commit-file", data="filepath=index.md", headers=HOST)
        assert resp.status_code == 400

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
        monkeypatch.setattr(settings, "GIT_ENABLED", False)
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

    def test_no_history_no_deleted_pages(self, nogit, wiki_dir):
        assert history_labels(nogit, "index") == []
        assert deleted_pages(nogit) == []


def test_secret_key_folder_is_kept_out_of_git(client, wiki_dir):
    assert (wiki_dir / ".zone" / "secret_key").exists()
    assert ".zone" not in git(wiki_dir, "status", "--porcelain", "--untracked-files=all")


