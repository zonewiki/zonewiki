"""Tests for Leaf HTTP routes — page view/edit/save, search, upload, auth,
rename with reference updates, conflict detection, and API endpoints.

Run with:
    pytest tests/test_routes.py -v
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_routes_test_")
os.environ.setdefault("WIKI_DATA", _SESSION_TMP)
os.environ["ALLOW_REMOTE"] = "1"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import server
import deck as deck_module
from server import content_hash, section_char_range

HOST = {"Host": "localhost:5000"}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def wiki_dir(tmp_path):
    old_pages = server.PAGES_DIR
    old_wiki = server.WIKI_DIR
    old_deck = deck_module.DECK_FILE
    old_uploads = server.UPLOADS_DIR
    server.PAGES_DIR = tmp_path
    server.WIKI_DIR = tmp_path
    server.UPLOADS_DIR = tmp_path / "_assets"
    server.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    deck_module.DECK_FILE = tmp_path / "_deck.json"
    server.DECK_FILE = tmp_path / "_deck.json"
    server._search_doc_cache.clear()
    server._page_meta_cache.clear()
    deck_module._task_docs_cache["key"] = None
    deck_module._task_docs_cache["docs"] = []
    (tmp_path / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    yield tmp_path
    server.PAGES_DIR = old_pages
    server.WIKI_DIR = old_wiki
    server.UPLOADS_DIR = old_uploads
    deck_module.DECK_FILE = old_deck
    server.DECK_FILE = old_deck


@pytest.fixture()
def client(wiki_dir):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def write_page(wiki_dir: Path, slug: str, content: str) -> Path:
    path = wiki_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ===========================================================================
# Index redirect
# ===========================================================================

class TestIndex:

    def test_root_redirects_to_index_page(self, client):
        resp = client.get("/", headers=HOST)
        assert resp.status_code in (301, 302)
        assert "/page/index" in resp.headers["Location"]


# ===========================================================================
# view_page
# ===========================================================================

class TestViewPage:

    def test_existing_page_returns_200(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n\nHello world.\n")
        resp = client.get("/page/my-page", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "Hello world." in text
        assert "My Page" in text

    def test_missing_page_redirects_to_editor(self, client):
        resp = client.get("/page/does-not-exist", headers=HOST)
        assert resp.status_code in (301, 302)
        assert "/edit/does-not-exist" in resp.headers["Location"]

    def test_slug_with_md_extension_stripped(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n\nContent.\n")
        resp = client.get("/page/my-page.md", headers=HOST)
        # Should redirect or serve the page without .md
        assert resp.status_code in (200, 301, 302)

    def test_index_page_served(self, client):
        resp = client.get("/page/index", headers=HOST)
        assert resp.status_code == 200

    def test_page_title_in_head(self, client, wiki_dir):
        write_page(wiki_dir, "about", "# About Us\n\nInfo.\n")
        resp = client.get("/page/about", headers=HOST)
        assert b"About Us" in resp.data

    def test_edit_link_present(self, client, wiki_dir):
        write_page(wiki_dir, "editable", "# Editable\n\ncontent\n")
        resp = client.get("/page/editable", headers=HOST)
        assert b"/edit/editable" in resp.data

    def test_backlinks_section_appears(self, client, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n\ncontent\n")
        write_page(wiki_dir, "source", "# Source\n\nSee [[target]].\n")
        resp = client.get("/page/target", headers=HOST)
        text = resp.data.decode()
        assert "Backlinks" in text
        assert "source" in text.lower()

    def test_no_backlinks_section_when_empty(self, client, wiki_dir):
        write_page(wiki_dir, "lonely", "# Lonely\n\ncontent\n")
        resp = client.get("/page/lonely", headers=HOST)
        text = resp.data.decode()
        # Backlinks section should not appear
        assert "Backlinks" not in text

    def test_unsafe_slug_returns_400(self, client):
        resp = client.get("/page/../etc/passwd", headers=HOST)
        assert resp.status_code == 400

    def test_frontmatter_rendered_as_meta_not_raw_yaml(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged",
            "---\ntags: work, urgent\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/page/tagged", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "tags: work" not in text
        assert '<p class="page-tldr">Short summary.</p>' in text
        assert 'class="task-tag page-tag"' in text
        assert "Body." in text
        assert "Tagged" in text  # title still derived correctly past the frontmatter

    def test_tags_and_tldr_shown_once_in_meta_row_not_in_body(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged2",
            "---\ntags: work, urgent\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/page/tagged2", headers=HOST)
        text = resp.data.decode()
        # Tags and the collapsed tldr toggle both live in the page-meta-wrapper
        # row (same line as "Last changed"), and appear there exactly once —
        # not duplicated in the article body.
        meta_start = text.index('class="page-meta-wrapper"')
        article_start = text.index('class="page-content"')
        assert 'class="task-tag page-tag"' in text[meta_start:article_start]
        assert text.count('class="task-tag page-tag"') == 2  # "work" + "urgent"
        assert text.count('<p class="page-tldr">') == 1
        assert '<p class="page-tldr">' in text[meta_start:article_start]
        assert '<p class="page-tldr">' not in text[article_start:]
        # Tags and the tldr toggle label sit together in the left group, "Last
        # changed" in the right group.
        left_start = text.index('class="page-meta-left"')
        right_start = text.index('class="page-meta-right"')
        assert left_start < right_start
        assert 'class="task-tag page-tag"' in text[left_start:right_start]
        assert 'class="tldr-label"' in text[left_start:right_start]

    def test_tldr_toggle_is_sibling_of_row_and_text(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged3",
            "---\ntags: work\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/page/tagged3", headers=HOST)
        text = resp.data.decode()
        # The CSS-only reveal needs checkbox → row → text as siblings in that
        # order, so `.tldr-toggle:checked ~ .page-tldr` can match.
        cb = text.index('class="tldr-toggle"')
        row = text.index('class="page-meta-wrapper"')
        para = text.index('<p class="page-tldr">')
        assert cb < row < para
        # The label points at the checkbox by id.
        assert 'id="tldr-toggle"' in text
        assert 'for="tldr-toggle"' in text

    def test_no_tldr_toggle_when_no_tldr(self, client, wiki_dir):
        write_page(wiki_dir, "tagsonly", "---\ntags: work\n---\n\n# T\n\nBody.\n")
        resp = client.get("/page/tagsonly", headers=HOST)
        text = resp.data.decode()
        assert 'class="tldr-toggle"' not in text
        assert 'class="tldr-label"' not in text
        assert 'class="task-tag page-tag"' in text


# ===========================================================================
# edit_page GET
# ===========================================================================

class TestEditPageGet:

    def test_edit_existing_page(self, client, wiki_dir):
        write_page(wiki_dir, "test-edit", "# Test\n\nContent here.\n")
        resp = client.get("/edit/test-edit", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "Content here." in text
        assert 'name="content"' in text

    def test_edit_shows_raw_frontmatter_for_editing(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged",
            "---\ntags: work, urgent\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/edit/tagged", headers=HOST)
        text = resp.data.decode()
        # The textarea holds the raw file content, frontmatter included verbatim,
        # so it can be edited as plain text like everything else.
        assert "tags: work, urgent" in text
        assert "tldr: Short summary." in text

    def test_edit_new_page_shows_empty_editor(self, client):
        resp = client.get("/edit/brand-new-page", headers=HOST)
        assert resp.status_code == 200
        assert b'name="content"' in resp.data

    def test_new_page_textarea_flagged_data_new(self, client):
        resp = client.get("/edit/brand-new-page", headers=HOST)
        assert 'data-new="1"' in resp.data.decode()

    def test_existing_page_textarea_not_flagged_data_new(self, client, wiki_dir):
        write_page(wiki_dir, "test-edit", "# Test\n\nContent here.\n")
        resp = client.get("/edit/test-edit", headers=HOST)
        assert 'data-new="1"' not in resp.data.decode()

    def test_edit_section_loads_section_content(self, client, wiki_dir):
        content = "# Title\n\npreamble\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
        write_page(wiki_dir, "sectioned", content)
        resp = client.get("/edit/sectioned?section=1", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "alpha body" in text
        assert "beta body" not in text

    def test_edit_unsafe_slug_returns_400(self, client):
        resp = client.get("/edit/../etc/passwd", headers=HOST)
        assert resp.status_code == 400

    def test_editor_usable_on_mobile(self, client, wiki_dir):
        """The editor must be served on small screens — no 'not available'
        message, and the toolbar carries a mobile Preview toggle."""
        write_page(wiki_dir, "test-edit", "# Test\n\nContent here.\n")
        resp = client.get("/edit/test-edit", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "not available on small screens" not in text
        assert "editor-mobile-msg" not in text
        assert 'id="mobile-preview-btn"' in text


# ===========================================================================
# save_page POST (/edit/<slug>)
# ===========================================================================

class TestSavePage:

    def test_save_creates_file_and_redirects(self, client, wiki_dir):
        resp = client.post("/edit/new-page", data={"content": "# New\n\nbody\n"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert (wiki_dir / "new-page.md").exists()

    def test_save_updates_existing_file(self, client, wiki_dir):
        path = write_page(wiki_dir, "update-me", "# Old\n\nold body\n")
        client.post("/edit/update-me", data={"content": "# New\n\nnew body\n"}, headers=HOST)
        assert "new body" in path.read_text(encoding="utf-8")

    def test_save_conflict_redirects_to_editor(self, client, wiki_dir):
        content = "# Title\n\noriginal\n"
        write_page(wiki_dir, "conflict-page", content)
        resp = client.post(
            "/edit/conflict-page",
            data={"content": "# Title\n\nupdated\n", "file_hash": "wrong_hash_value"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302)
        assert "conflict" in resp.headers["Location"]

    def test_save_section_replaces_only_section(self, client, wiki_dir):
        content = "# Title\n\npreamble\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
        path = write_page(wiki_dir, "section-save", content)
        rng = section_char_range(content, 1)
        assert rng is not None
        s, e = rng
        resp = client.post("/edit/section-save", data={
            "content": "## Alpha\n\nUPDATED alpha\n",
            "file_hash": content_hash(content),
            "start_char": str(s),
            "end_char": str(e),
        }, headers=HOST)
        assert resp.status_code in (301, 302)
        saved = path.read_text(encoding="utf-8")
        assert "UPDATED alpha" in saved
        assert "beta body" in saved
        assert "preamble" in saved

    def test_save_normalizes_crlf(self, client, wiki_dir):
        resp = client.post(
            "/edit/crlf-page",
            data={"content": "line1\r\nline2\r\n"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302)
        path = wiki_dir / "crlf-page.md"
        assert path.exists()
        assert "\r\n" not in path.read_text(encoding="utf-8")


# ===========================================================================
# api_save POST (/api/save/<slug>)
# ===========================================================================

class TestApiSave:

    def test_saves_new_file(self, client, wiki_dir):
        resp = client.post("/api/save/autosave-page", data={"content": "# Auto\n\nbody\n"}, headers=HOST)
        assert resp.status_code in (200, 204)
        assert (wiki_dir / "autosave-page.md").exists()

    def test_saves_existing_file(self, client, wiki_dir):
        path = write_page(wiki_dir, "auto-existing", "# Old\n\nold\n")
        resp = client.post("/api/save/auto-existing", data={"content": "# New\n\nnew\n"}, headers=HOST)
        assert resp.status_code in (200, 204)
        assert "new" in path.read_text(encoding="utf-8")

    def test_section_save_returns_json(self, client, wiki_dir):
        content = "# T\n\npre\n\n## S\n\nbody\n"
        path = write_page(wiki_dir, "api-section", content)
        rng = section_char_range(content, 1)
        s, e = rng
        resp = client.post("/api/save/api-section", data={
            "content": "## S\n\nupdated body\n",
            "file_hash": content_hash(content),
            "start_char": str(s),
            "end_char": str(e),
        }, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "file_hash" in data
        assert "start_char" in data
        assert "end_char" in data

    def test_section_conflict_returns_409(self, client, wiki_dir):
        content = "# T\n\npre\n\n## S\n\nbody\n"
        write_page(wiki_dir, "conflict-section", content)
        rng = section_char_range(content, 1)
        s, e = rng
        resp = client.post("/api/save/conflict-section", data={
            "content": "## S\n\nnew\n",
            "file_hash": "wrong_hash",
            "start_char": str(s),
            "end_char": str(e),
        }, headers=HOST)
        assert resp.status_code == 409

    def test_full_file_hash_conflict_returns_409(self, client, wiki_dir):
        write_page(wiki_dir, "hash-conflict", "# T\n\nbody\n")
        resp = client.post("/api/save/hash-conflict", data={
            "content": "# T\n\nupdated\n",
            "file_hash": "bad_hash",
        }, headers=HOST)
        assert resp.status_code == 409

    def test_unchanged_content_returns_204_or_json(self, client, wiki_dir):
        content = "# T\n\nbody\n"
        write_page(wiki_dir, "unchanged", content)
        resp = client.post("/api/save/unchanged", data={"content": content}, headers=HOST)
        assert resp.status_code in (200, 204)


# ===========================================================================
# create_page POST (/new)
# ===========================================================================

class TestCreatePage:

    def test_create_page_redirects_to_editor(self, client, wiki_dir):
        resp = client.post("/new", data={"slug": "Brand New Page"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert "brand-new-page" in resp.headers["Location"]

    def test_create_page_writes_file(self, client, wiki_dir):
        client.post("/new", data={"slug": "fresh-page"}, headers=HOST)
        assert (wiki_dir / "fresh-page.md").exists()

    def test_create_page_empty_slug_returns_400(self, client):
        resp = client.post("/new", data={"slug": ""}, headers=HOST)
        assert resp.status_code == 400

    def test_create_existing_page_redirects_to_editor(self, client, wiki_dir):
        write_page(wiki_dir, "existing-page", "# Existing\n\ncontent\n")
        resp = client.post("/new", data={"slug": "Existing Page"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert "existing-page" in resp.headers["Location"]


# ===========================================================================
# delete_page POST (/delete/<slug>)
# ===========================================================================

class TestDeletePage:

    def test_delete_page_removes_file(self, client, wiki_dir):
        path = write_page(wiki_dir, "to-delete", "# Delete Me\n\ncontent\n")
        resp = client.post("/delete/to-delete", headers=HOST)
        assert resp.status_code in (301, 302)
        assert not path.exists()

    def test_delete_redirects_to_next(self, client, wiki_dir):
        write_page(wiki_dir, "gone", "# Gone\n")
        resp = client.post("/delete/gone", data={"next": "/page/other"}, headers=HOST)
        assert resp.headers["Location"] == "/page/other"

    def test_delete_next_pointing_at_deleted_page_falls_back(self, client, wiki_dir):
        write_page(wiki_dir, "gone", "# Gone\n")
        resp = client.post("/delete/gone", data={"next": "/page/gone"}, headers=HOST)
        assert resp.headers["Location"] == "/page/index"

    @pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example"])
    def test_delete_next_rejects_offsite_redirect(self, client, wiki_dir, target):
        # Regression: "//host" and "/\\host" start with "/" but browsers
        # resolve them to another origin (open redirect).
        write_page(wiki_dir, "gone", "# Gone\n")
        resp = client.post("/delete/gone", data={"next": target}, headers=HOST)
        assert resp.headers["Location"] == "/page/index"

    def test_delete_index_returns_400(self, client):
        resp = client.post("/delete/index", headers=HOST)
        assert resp.status_code == 400

    def test_delete_nonexistent_page_returns_404(self, client):
        resp = client.post("/delete/ghost-page", headers=HOST)
        assert resp.status_code == 404


# ===========================================================================
# rename_page POST (/rename/<slug>)
# ===========================================================================

class TestRenamePage:

    def test_rename_moves_file(self, client, wiki_dir):
        write_page(wiki_dir, "old-name", "# Old Name\n\ncontent\n")
        resp = client.post("/rename/old-name", data={"new_name": "new-name"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert (wiki_dir / "new-name.md").exists()
        assert not (wiki_dir / "old-name.md").exists()

    def test_rename_updates_wikilinks_in_other_pages(self, client, wiki_dir):
        write_page(wiki_dir, "old-slug", "# Old Slug\n\ncontent\n")
        ref = write_page(wiki_dir, "referencing", "# Ref\n\nSee [[old-slug]].\n")
        client.post("/rename/old-slug", data={"new_name": "new-slug"}, headers=HOST)
        assert "[[new-slug]]" in ref.read_text(encoding="utf-8")
        assert "[[old-slug]]" not in ref.read_text(encoding="utf-8")

    def test_rename_index_returns_400(self, client):
        resp = client.post("/rename/index", data={"new_name": "homepage"}, headers=HOST)
        assert resp.status_code == 400

    def test_rename_to_existing_slug_returns_redirect_with_error(self, client, wiki_dir):
        write_page(wiki_dir, "page-a", "# A\n\ncontent\n")
        write_page(wiki_dir, "page-b", "# B\n\ncontent\n")
        resp = client.post("/rename/page-a", data={"new_name": "page-b"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert "rename_error" in resp.headers["Location"]

    def test_rename_preserves_content(self, client, wiki_dir):
        write_page(wiki_dir, "preserve-me", "# Preserve\n\nUnique content 99.\n")
        client.post("/rename/preserve-me", data={"new_name": "preserved"}, headers=HOST)
        saved = (wiki_dir / "preserved.md").read_text(encoding="utf-8")
        assert "Unique content 99." in saved

    def test_rename_updates_pipe_alias_wikilinks(self, client, wiki_dir):
        write_page(wiki_dir, "old-page", "# Old\n\ncontent\n")
        ref = write_page(wiki_dir, "source", "# Src\n\nSee [[old-page|My Label]].\n")
        client.post("/rename/old-page", data={"new_name": "new-page"}, headers=HOST)
        text = ref.read_text(encoding="utf-8")
        assert "[[new-page|My Label]]" in text


# ===========================================================================
# api_preview POST
# ===========================================================================

class TestApiPreview:

    def test_preview_returns_rendered_html(self, client):
        resp = client.post(
            "/api/preview",
            data="**bold** text",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        assert b"<strong>bold</strong>" in resp.data

    def test_preview_renders_wikilinks(self, client, wiki_dir):
        write_page(wiki_dir, "linked", "# Linked\n")
        resp = client.post(
            "/api/preview?slug=source",
            data="[[linked]]",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        assert b'href="/page/linked"' in resp.data

    def test_preview_renders_frontmatter_live(self, client):
        resp = client.post(
            "/api/preview",
            data="---\ntags: draft\ntldr: In progress.\n---\n\n# Title\n\nBody.\n",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        text = resp.data.decode()
        assert "tags: draft" not in text
        assert '<p class="page-tldr">In progress.</p>' in text
        assert 'class="task-tag page-tag"' in text

    def test_preview_empty_body(self, client):
        resp = client.post(
            "/api/preview",
            data="",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 200


# ===========================================================================
# api_pages GET
# ===========================================================================

class TestApiPages:

    def test_returns_list_of_slugs(self, client, wiki_dir):
        write_page(wiki_dir, "page-one", "# Page One\n")
        write_page(wiki_dir, "page-two", "# Page Two\n")
        resp = client.get("/api/pages", headers=HOST)
        assert resp.status_code == 200
        slugs = resp.get_json()
        assert "page-one" in slugs
        assert "page-two" in slugs

    def test_returns_json_list(self, client):
        resp = client.get("/api/pages", headers=HOST)
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)


# ===========================================================================
# api_pages_list GET
# ===========================================================================

class TestApiPagesList:

    def test_includes_title_and_mtime(self, client, wiki_dir):
        write_page(wiki_dir, "listable", "# Listable Page\n\nbody\n")
        resp = client.get("/api/pages-list", headers=HOST)
        assert resp.status_code == 200
        pages = resp.get_json()
        slugs = {p["slug"] for p in pages}
        assert "listable" in slugs
        page = next(p for p in pages if p["slug"] == "listable")
        assert page["title"] == "Listable Page"
        assert "mtime" in page

    def test_includes_virtual_special_pages(self, client):
        resp = client.get("/api/pages-list", headers=HOST)
        pages = resp.get_json()
        slugs = {p["slug"] for p in pages}
        assert "_scratch" in slugs
        assert "_daily" in slugs


# ===========================================================================
# search
# ===========================================================================

class TestSearch:

    def test_api_search_returns_results(self, client, wiki_dir):
        write_page(wiki_dir, "searchable", "# Searchable\n\nunique_token_xyz\n")
        resp = client.get("/api/search?q=unique_token_xyz", headers=HOST)
        assert resp.status_code == 200
        results = resp.get_json()
        assert any(r["slug"] == "searchable" for r in results)

    def test_api_search_empty_query(self, client):
        resp = client.get("/api/search?q=", headers=HOST)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_api_search_title_match_ranked_higher(self, client, wiki_dir):
        write_page(wiki_dir, "title-match", "# uniquetoken\n\nbody\n")
        write_page(wiki_dir, "body-only", "# Other\n\nuniquetoken appears once\n")
        resp = client.get("/api/search?q=uniquetoken", headers=HOST)
        results = resp.get_json()
        # title match should come first
        assert results[0]["slug"] == "title-match"

    def test_search_page_returns_200(self, client, wiki_dir):
        write_page(wiki_dir, "findable", "# Findable\n\nfindme_content\n")
        resp = client.get("/search?q=findme_content", headers=HOST)
        assert resp.status_code == 200
        assert b"findme_content" in resp.data

    def test_api_search_excerpt_has_mark(self, client, wiki_dir):
        write_page(wiki_dir, "marked", "# Marked\n\nsome highlighted word\n")
        resp = client.get("/api/search?q=highlighted", headers=HOST)
        results = resp.get_json()
        assert results
        assert "<mark>" in results[0]["excerpt"]

    def test_search_includes_task_results(self, client, wiki_dir):
        client.post("/api/deck", json={"title": "unique_task_token_abc"})
        resp = client.get("/api/search?q=unique_task_token_abc", headers=HOST)
        results = resp.get_json()
        assert any(r.get("type") == "task" for r in results)

    def test_multi_word_query_matches_scattered_terms(self, client, wiki_dir):
        # Both words present but not as a phrase — old substring search scored 0.
        write_page(wiki_dir, "scattered",
                   "# Scattered\n\nthe vector lives here, database is elsewhere\n")
        resp = client.get("/api/search?q=vector+database", headers=HOST)
        results = resp.get_json()
        assert any(r["slug"] == "scattered" for r in results)

    def test_typo_still_finds_page(self, client, wiki_dir):
        write_page(wiki_dir, "dbnotes", "# Database notes\n\nabout the database engine\n")
        resp = client.get("/api/search?q=databse", headers=HOST)  # transposed typo
        results = resp.get_json()
        assert any(r["slug"] == "dbnotes" for r in results)

    def test_word_variation_matches_via_stem(self, client, wiki_dir):
        write_page(wiki_dir, "runlog", "# Run log\n\nI was running in the park today\n")
        resp = client.get("/api/search?q=run", headers=HOST)
        results = resp.get_json()
        assert any(r["slug"] == "runlog" for r in results)

    def test_rarer_term_ranked_higher(self, client, wiki_dir):
        # "notes" is common across pages; "quasar" is rare — the quasar page wins.
        for i in range(4):
            write_page(wiki_dir, f"common{i}", f"# Notes {i}\n\nordinary notes about notes\n")
        write_page(wiki_dir, "rare", "# Notes rare\n\nnotes about the quasar phenomenon\n")
        resp = client.get("/api/search?q=notes+quasar", headers=HOST)
        results = resp.get_json()
        assert results[0]["slug"] == "rare"

    def test_search_page_task_links_to_filtered_deck(self, client, wiki_dir):
        # A task result must open /page/deck with the title prefilled into the
        # deck search filter — not the bare _deck storage slug.
        client.post("/api/deck", json={"title": "buymilk urgent chore"})
        resp = client.get("/search?q=buymilk+urgent+chore", headers=HOST)
        body = resp.get_data(as_text=True)
        assert "/page/_deck" not in body
        assert "/page/deck?search=buymilk+urgent+chore" in body
        assert "filter=open" in body

    def test_search_page_completed_task_links_to_completed_tab(self, client, wiki_dir):
        r = client.post("/api/deck", json={"title": "finishreport tuvwxyz"})
        tid = r.get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        resp = client.get("/search?q=finishreport+tuvwxyz", headers=HOST)
        body = resp.get_data(as_text=True)
        assert "/page/deck?search=finishreport+tuvwxyz" in body
        assert "filter=completed" in body


# ===========================================================================
# api_toggle_checkbox
# ===========================================================================

class TestApiToggleCheckbox:

    def test_toggles_unchecked_to_checked(self, client, wiki_dir):
        path = write_page(wiki_dir, "checkbox-page", "# CB\n\n- [ ] task one\n- [ ] task two\n")
        resp = client.post("/api/toggle-checkbox/checkbox-page", data={"idx": "0"}, headers=HOST)
        assert resp.status_code == 204
        text = path.read_text(encoding="utf-8")
        assert "- [x] task one" in text
        assert "- [ ] task two" in text

    def test_toggles_checked_to_unchecked(self, client, wiki_dir):
        path = write_page(wiki_dir, "toggle-back", "# CB\n\n- [x] done task\n")
        client.post("/api/toggle-checkbox/toggle-back", data={"idx": "0"}, headers=HOST)
        assert "- [ ] done task" in path.read_text(encoding="utf-8")

    def test_returns_404_for_missing_page(self, client):
        resp = client.post("/api/toggle-checkbox/no-such-page", data={"idx": "0"}, headers=HOST)
        assert resp.status_code == 404

    def test_returns_400_for_negative_idx(self, client, wiki_dir):
        write_page(wiki_dir, "bad-idx", "# CB\n\n- [ ] task\n")
        resp = client.post("/api/toggle-checkbox/bad-idx", data={"idx": "-1"}, headers=HOST)
        assert resp.status_code == 400

    @pytest.mark.parametrize("idx", ["abc", "", "1.5"])
    def test_returns_400_for_non_integer_idx(self, client, wiki_dir, idx):
        # Regression: int() on the raw form value raised → 500.
        write_page(wiki_dir, "bad-idx", "# CB\n\n- [ ] task\n")
        resp = client.post("/api/toggle-checkbox/bad-idx", data={"idx": idx}, headers=HOST)
        assert resp.status_code == 400


# ===========================================================================
# api_upload
# ===========================================================================

class TestApiUpload:

    def test_upload_file(self, client, wiki_dir):
        from io import BytesIO
        data = {"file": (BytesIO(b"fake image data"), "test.png")}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 200
        url = resp.get_json()["url"]
        assert url.startswith("/_assets/")
        assert (wiki_dir / "_assets" / "test.png").exists()

    def test_upload_conflict_returns_409(self, client, wiki_dir):
        from io import BytesIO
        (wiki_dir / "_assets" / "existing.png").write_bytes(b"original")
        data = {"file": (BytesIO(b"new data"), "existing.png")}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 409
        assert resp.get_json()["conflict"] is True

    def test_upload_overwrite(self, client, wiki_dir):
        from io import BytesIO
        (wiki_dir / "_assets" / "overwrite.txt").write_bytes(b"old")
        data = {"file": (BytesIO(b"new"), "overwrite.txt"), "overwrite": "1"}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 200

    def test_upload_unique_flag_generates_uuid_name(self, client, wiki_dir):
        from io import BytesIO
        data = {"file": (BytesIO(b"img"), "photo.png"), "unique": "1"}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 200
        url = resp.get_json()["url"]
        # UUID filenames don't match the original name
        assert "photo.png" not in url


# ===========================================================================
# Authentication (LEAF_PASSWORD)
# ===========================================================================

class TestAuth:

    @pytest.fixture()
    def auth_client(self, wiki_dir):
        old_pw = server.LEAF_PASSWORD
        server.LEAF_PASSWORD = "testpassword"
        server.app.config["TESTING"] = True
        with server.app.test_client() as c:
            yield c
        server.LEAF_PASSWORD = old_pw

    def test_unauthenticated_redirects_to_login(self, auth_client):
        resp = auth_client.get("/page/index", headers=HOST)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers["Location"]

    def test_login_with_correct_password(self, auth_client):
        resp = auth_client.post(
            "/login",
            data={"password": "testpassword"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302)
        assert "login" not in resp.headers["Location"]

    def test_login_redirects_to_next(self, auth_client):
        resp = auth_client.post("/login?next=/page/foo",
                                data={"password": "testpassword"}, headers=HOST)
        assert resp.headers["Location"] == "/page/foo"

    @pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example"])
    def test_login_next_rejects_offsite_redirect(self, auth_client, target):
        # Regression: open redirect via protocol-relative next URL.
        resp = auth_client.post("/login", query_string={"next": target},
                                data={"password": "testpassword"}, headers=HOST)
        assert resp.headers["Location"] == "/"

    def test_login_without_password_configured_redirects_home(self, client):
        resp = client.get("/login", headers=HOST)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

    def test_public_assets_served_without_auth(self, auth_client):
        resp = auth_client.get("/public/style.css", headers=HOST)
        assert resp.status_code == 200

    def test_login_with_wrong_password_shows_error(self, auth_client):
        resp = auth_client.post(
            "/login",
            data={"password": "wrongpassword"},
            headers=HOST,
        )
        assert resp.status_code == 200
        assert b"Incorrect" in resp.data

    def test_login_page_accessible_without_auth(self, auth_client):
        resp = auth_client.get("/login", headers=HOST)
        assert resp.status_code == 200

    def test_logout_clears_session(self, auth_client):
        auth_client.post("/login", data={"password": "testpassword"}, headers=HOST)
        resp = auth_client.post("/logout", headers=HOST)
        assert resp.status_code in (301, 302)
        # After logout, protected page should redirect to login
        follow = auth_client.get("/page/index", headers=HOST)
        assert follow.status_code in (301, 302)
        assert "login" in follow.headers["Location"]


# ===========================================================================
# update_references (called during rename)
# ===========================================================================

class TestUpdateReferences:

    def test_returns_list_of_modified_files(self, wiki_dir):
        write_page(wiki_dir, "old-page", "# Old\n\ncontent\n")
        ref = write_page(wiki_dir, "referencer", "# Ref\n\n[[old-page]]\n")
        write_page(wiki_dir, "no-link", "# No Link\n\nbody\n")

        old_pages = server.PAGES_DIR
        server.PAGES_DIR = wiki_dir
        try:
            modified = server.update_references("old-page", "new-page")
        finally:
            server.PAGES_DIR = old_pages

        assert ref in modified
        assert len(modified) == 1  # only the referencing page was modified

    def test_unmodified_pages_not_returned(self, wiki_dir):
        write_page(wiki_dir, "target", "# T\n\ncontent\n")
        write_page(wiki_dir, "no-ref", "# No Ref\n\nbody only\n")

        old_pages = server.PAGES_DIR
        server.PAGES_DIR = wiki_dir
        try:
            modified = server.update_references("target", "new-target")
        finally:
            server.PAGES_DIR = old_pages

        assert modified == []


# ===========================================================================
# API pages — special virtual pages
# ===========================================================================

class TestSpecialPages:

    def test_scratch_index_page_loads(self, client):
        resp = client.get("/page/_scratch", headers=HOST)
        assert resp.status_code == 200
        assert b"Scratch" in resp.data

    def test_daily_index_page_loads(self, client):
        resp = client.get("/page/_daily", headers=HOST)
        assert resp.status_code == 200
        assert b"Daily" in resp.data

    def test_api_scratch_creates_note(self, client, wiki_dir):
        resp = client.post("/api/scratch", data={"title": "My Note"}, headers=HOST)
        assert resp.status_code == 200
        slug = resp.get_json()["slug"]
        assert (wiki_dir / f"{slug}.md").exists()

    def test_api_daily_creates_today_note(self, client, wiki_dir):
        resp = client.post("/api/daily", headers=HOST)
        assert resp.status_code == 200
        slug = resp.get_json()["slug"]
        assert slug.endswith("_daily")
        assert (wiki_dir / f"{slug}.md").exists()

    def test_api_daily_idempotent(self, client, wiki_dir):
        client.post("/api/daily", headers=HOST)
        resp = client.post("/api/daily", headers=HOST)
        assert resp.status_code == 200
        # Should return same slug


# ===========================================================================
# Host header check
# ===========================================================================

class TestHostHeader:

    def test_bad_host_returns_400(self, client):
        old_allow_remote = server.ALLOW_REMOTE
        server.ALLOW_REMOTE = False
        try:
            resp = client.get("/page/index", headers={"Host": "evil.com"})
            assert resp.status_code == 400
        finally:
            server.ALLOW_REMOTE = old_allow_remote

    def test_allow_remote_skips_check(self, client):
        old = server.ALLOW_REMOTE
        server.ALLOW_REMOTE = True
        try:
            resp = client.get("/page/index", headers={"Host": "any.host.example"})
            assert resp.status_code != 400
        finally:
            server.ALLOW_REMOTE = old


# ===========================================================================
# Search result hrefs (server-computed destination URLs)
# ===========================================================================

class TestSearchResultHref:

    def test_page_result_has_href(self, client, wiki_dir):
        write_page(wiki_dir, "gardening", "# Gardening\n\nTomatoes and peppers.\n")
        results = client.get("/api/search?q=tomatoes", headers=HOST).get_json()
        assert results, "expected a search hit"
        assert results[0]["href"] == f"/page/{results[0]['slug']}"

    def test_task_result_href_opens_deck_prefiltered(self, client, wiki_dir):
        client.post("/api/deck", json={"title": "Water the ferns"}, headers=HOST)
        results = client.get("/api/search?q=ferns", headers=HOST).get_json()
        task_hits = [r for r in results if r.get("type") == "task"]
        assert task_hits, "expected a task hit"
        href = task_hits[0]["href"]
        assert href.startswith("/page/deck?")
        assert "search=Water+the+ferns" in href
        assert "filter=open" in href


class TestBacklinkCacheInvalidation:

    def test_backlinks_reflect_on_disk_edits(self, client, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n\nbody\n")
        write_page(wiki_dir, "source", "# Source\n\nno links yet\n")
        resp = client.get("/page/target", headers=HOST)
        assert "No pages link here" in resp.data.decode() or "Source" not in resp.data.decode()
        # Edit on disk (new mtime) — the cached link targets must be re-derived
        import time
        time.sleep(0.01)
        write_page(wiki_dir, "source", "# Source\n\nSee [[Target]].\n")
        resp = client.get("/page/target", headers=HOST)
        assert "Source" in resp.data.decode()


# ===========================================================================
# Linked tasks (deck tasks that wikilink to a page)
# ===========================================================================

class TestTaskBacklinks:

    def test_task_with_wikilink_listed_on_page(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Water [[Garden]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' in html
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        assert "Water Garden" in section

    def test_linked_task_entry_is_a_single_link(self, client, wiki_dir):
        # Regression: the task title's own wikilinks were rendered as <a>
        # inside the outer deck <a> — nested anchors, which browsers split so
        # half the entry went to the deck and half to the page.
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Water [[Garden]] and [x](/page/garden)"},
                    headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        items = re.findall(r"<li>(.*?)</li>", section)
        assert len(items) == 1
        assert items[0].count("<a ") == 1
        assert "Water Garden and x" in items[0]

    def test_wikilink_in_description_and_labelled_link(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Chores",
                                       "description": "see [[garden|the beds]]"}, headers=HOST)
        assert [t["title"] for t in server.find_task_backlinks("garden")] == ["Chores"]

    def test_markdown_page_link_counts(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Read [notes](/page/garden)"}, headers=HOST)
        assert len(server.find_task_backlinks("garden")) == 1

    def test_completed_and_unrelated_tasks_excluded(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        t = client.post("/api/deck", json={"title": "Done [[garden]]"}, headers=HOST).get_json()
        client.put(f"/api/deck/{t['id']}", json={"completed": True}, headers=HOST)
        client.post("/api/deck", json={"title": "About [[kitchen]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' not in html

    def test_task_title_is_escaped(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "<img src=x onerror=alert(1)> [[garden]]"},
                    headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert "<img src=x" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


class TestPlainTaskTitle:

    def test_plain_text_unchanged(self):
        assert server._plain_task_title("a < b & c") == "a < b & c"

    def test_wikilink_and_label(self):
        assert server._plain_task_title("[[My Page]]") == "My Page"
        assert server._plain_task_title("[[my-page|Label]]") == "Label"

    def test_markdown_page_link(self):
        assert server._plain_task_title("see [x](/page/foo) now") == "see x now"

    def test_external_markdown_link_left_as_text(self):
        assert server._plain_task_title("[x](https://e.x)") == "[x](https://e.x)"


# ===========================================================================
# _raw inbox (URL + snippet capture)
# ===========================================================================

class TestRawPage:

    def test_created_on_first_visit(self, client, wiki_dir):
        resp = client.get("/page/_raw", headers=HOST)
        assert resp.status_code == 200
        assert (wiki_dir / "_raw.md").read_text() == "# Raw\n\n"
        assert "<h3>Snippets</h3>" not in resp.get_data(as_text=True)

    def test_append_url(self, client, wiki_dir):
        resp = client.post("/api/raw/append-url",
                           data={"url": "https://example.com", "title": "Ex"}, headers=HOST)
        assert resp.get_json() == {"ok": True}
        assert "- [Ex](https://example.com)\n" in (wiki_dir / "_raw.md").read_text()

    def test_append_url_title_defaults_to_url(self, client, wiki_dir):
        (wiki_dir / "_raw.md").write_text("# Raw", encoding="utf-8")  # no trailing newline
        client.post("/api/raw/append-url", data={"url": "https://e.x"}, headers=HOST)
        assert (wiki_dir / "_raw.md").read_text() == "# Raw\n- [https://e.x](https://e.x)\n"

    def test_append_url_requires_url(self, client):
        assert client.post("/api/raw/append-url", data={}, headers=HOST).status_code == 400

    def test_create_snippet_page_and_link(self, client, wiki_dir):
        resp = client.post("/api/raw/create-page",
                           data={"text": "first line\nsecond"}, headers=HOST).get_json()
        assert resp["title"] == "first line"
        assert resp["slug"].startswith("_raw/")
        snippet = wiki_dir / f"{resp['slug']}.md"
        assert snippet.read_text() == "# first line\n\nfirst line\nsecond\n"
        assert f"- [first line](/page/{resp['slug']})" in (wiki_dir / "_raw.md").read_text()
        html = client.get("/page/_raw", headers=HOST).get_data(as_text=True)
        assert "<h3>Snippets</h3>" in html
        assert f'href="/page/{resp["slug"]}"' in html

    def test_create_snippet_requires_text(self, client):
        assert client.post("/api/raw/create-page", data={"text": "  "}, headers=HOST).status_code == 400

    def test_snippet_pages_hidden_from_page_iteration(self, client, wiki_dir):
        resp = client.post("/api/raw/create-page", data={"text": "x"}, headers=HOST).get_json()
        assert resp["slug"] not in server.all_slugs()
