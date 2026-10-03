import json
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

    def test_viewport_resizes_with_on_screen_keyboard(self, client, wiki_dir):
        # Regression: without interactive-widget=resizes-content, Android
        # Chrome's keyboard only shrinks the visual viewport, so the
        # full-screen deck editor kept its height and its Save/Cancel bar sat
        # behind the keyboard.
        write_page(wiki_dir, "my-page", "# My Page\n")
        text = client.get("/page/my-page", headers=HOST).data.decode()
        assert ('<meta name="viewport" content="width=device-width, initial-scale=1, '
                'interactive-widget=resizes-content">') in text

    def test_phone_action_buttons_on_views_not_in_editor(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/deck"):
            body = client.get(url, headers=HOST).data.decode()
            assert 'class="action-note"' in body and 'class="action-search"' in body
        # The editor has its own toolbar and the keyboard owns the bottom edge.
        assert 'class="action-buttons"' not in client.get("/edit/my-page", headers=HOST).data.decode()

    def test_pages_load_nothing_from_other_hosts(self, client, wiki_dir):
        # No CDN: highlight.js is vendored and served from /public/.
        write_page(wiki_dir, "my-page", "# My Page\n\n```py\nx = 1\n```\n")
        for url in ("/page/my-page", "/edit/my-page", "/deck", "/search?q=page"):
            head = client.get(url, headers=HOST).data.decode()
            assert not re.search(r'<(?:script|link)\b[^>]*(?:src|href)="(?:https?:)?//', head), url
        for f in ("highlight.min.js", "github.min.css", "github-dark.min.css"):
            r = client.get(f"/public/vendor/highlight/{f}", headers=HOST)
            assert r.status_code == 200 and len(r.data) > 1000, f

    def test_editor_has_formatting_bar_page_view_not(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        body = client.get("/edit/my-page", headers=HOST).data.decode()
        assert 'id="format-bar" role="toolbar"' in body
        for fmt in ("bold", "italic", "ul", "ol", "task", "link", "wikilink", "table", "attach"):
            assert f'data-fmt="{fmt}"' in body
        # Icon-only buttons need a name for screen readers.
        assert body.count('class="fmt-btn"') == body.count('class="fmt-btn" data-fmt="') \
            == len(re.findall(r'class="fmt-btn" data-fmt="\w+" title="[^"]+" aria-label="[^"]+"', body))
        assert 'id="format-bar"' not in client.get("/page/my-page", headers=HOST).data.decode()

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
            "/api/preview",
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
        assert "_daily" not in slugs   # daily notes were removed

    def test_special_pages_come_first_and_are_marked(self, client, wiki_dir):
        write_page(wiki_dir, "aaa", "# AAA\n")
        pages = client.get("/api/pages-list", headers=HOST).get_json()
        special = [p["title"] for p in pages if p.get("special")]
        assert special[:2] == ["Deck", "Scratch"]
        assert [p["title"] for p in pages[:len(special)]] == special
        assert not next(p for p in pages if p["slug"] == "aaa").get("special")


# ===========================================================================
# search
# ===========================================================================

class TestIterPages:
    def test_skips_underscore_and_hidden_folders(self, wiki_dir):
        write_page(wiki_dir, "top", "# Top\n")
        write_page(wiki_dir, "a/b/deep", "# Deep\n")
        for skipped in ("_assets/x", "_index/y", ".git/objects/z", "a/_drafts/w", "a/.hidden/v"):
            write_page(wiki_dir, skipped, "# no\n")
        assert sorted(slug for _p, slug in server.iter_pages()) == ["a/b/deep", "index", "top"]
        assert all(p.is_file() and p.suffix == ".md" for p, _s in server.iter_pages())


class TestSearch:

    def test_api_search_returns_results(self, client, wiki_dir):
        write_page(wiki_dir, "searchable", "# Searchable\n\nunique_token_xyz\n")
        resp = client.get("/api/search?q=unique_token_xyz", headers=HOST)
        assert resp.status_code == 200
        results = resp.get_json()
        assert any(r["slug"] == "searchable" for r in results)

    def test_result_links_to_the_matching_section(self, client, wiki_dir):
        write_page(wiki_dir, "long", "# Long notes\n\nIntro.\n\n## Setup\n\nInstall it.\n\n"
                                     "## Backing **up** the [[data]]\n\nRun git push nightly.\n")
        r = client.get("/api/search?q=nightly", headers=HOST).get_json()[0]
        assert r["href"] == "/page/long?hl=nightly#backing-up-the-data"
        assert "<mark>nightly</mark>" in r["excerpt"] and r["excerpt"].startswith("Backing")

    def test_best_section_wins(self, client, wiki_dir):
        write_page(wiki_dir, "multi", "# Multi\n\n## One\n\ngit once, and lots of other words here.\n\n"
                                      "## Two\n\ngit git git.\n")
        assert client.get("/api/search?q=git", headers=HOST).get_json()[0]["href"] == "/page/multi?hl=git#two"

    def test_title_only_match_opens_the_top(self, client, wiki_dir):
        write_page(wiki_dir, "zebra", "# Zebra\n\n## Food\n\nGrass.\n\n## Home\n\nAfrica.\n")
        assert client.get("/api/search?q=zebra", headers=HOST).get_json()[0]["href"] == "/page/zebra?hl=Zebra"

    def test_match_before_the_first_heading_opens_the_top(self, client, wiki_dir):
        write_page(wiki_dir, "intro", "# Intro\n\nThe quokka lives here.\n\n## Later\n\nNothing.\n")
        assert client.get("/api/search?q=quokka", headers=HOST).get_json()[0]["href"] == "/page/intro?hl=quokka"

    def test_no_anchor_for_headings_without_an_id(self, client, wiki_dir):
        # Only ## and ### headings get ids (anchors_plugin max_level=3).
        write_page(wiki_dir, "deep", "# Deep\n\n## Top\n\nx\n\n#### Fourth\n\nThe narwhal.\n")
        r = client.get("/api/search?q=narwhal", headers=HOST).get_json()[0]
        assert r["href"] == "/page/deep?hl=narwhal" and r["excerpt"].startswith("Fourth")

    def test_search_page_links_to_the_section(self, client, wiki_dir):
        write_page(wiki_dir, "long", "# Long\n\n## Setup\n\nInstall the wombat.\n")
        assert b'href="/page/long?hl=wombat#setup"' in client.get("/search?q=wombat", headers=HOST).data

    def test_exact_phrase(self, client, wiki_dir):
        write_page(wiki_dir, "near", "# Near\n\nThe push to git happened.\n")
        write_page(wiki_dir, "exact", "# Exact\n\nWe git push daily.\n")
        slugs = [r["slug"] for r in client.get('/api/search?q="git push"', headers=HOST).get_json()]
        assert slugs == ["exact"]

    def test_phrase_marked_in_excerpt(self, client, wiki_dir):
        write_page(wiki_dir, "trip", "# Trip\n\nTwo weeks of holiday in Italy.\n")
        r = client.get('/api/search?q="holiday in italy"', headers=HOST).get_json()[0]
        assert "<mark>holiday in Italy</mark>" in r["excerpt"]

    def test_exclude_word(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n\ngit and docker.\n")
        write_page(wiki_dir, "b", "# B\n\ngit only.\n")
        slugs = [r["slug"] for r in client.get("/api/search?q=git+-dockers", headers=HOST).get_json()]
        assert slugs == ["b"]

    def test_tag_filter_pages_and_tasks(self, client, wiki_dir):
        write_page(wiki_dir, "tagged", "---\ntags: [Leaf, ui]\n---\n# Tagged\n\nThe date picker.\n")
        write_page(wiki_dir, "untagged", "# Untagged\n\nThe date picker again.\n")
        client.post("/api/deck", json={"title": "Fix date picker", "tags": ["leaf"]}, headers=HOST)
        client.post("/api/deck", json={"title": "Date picker elsewhere"}, headers=HOST)
        results = client.get("/api/search?q=date+picker+%23leaf", headers=HOST).get_json()
        assert {r["title"] for r in results} == {"Tagged", "[Task] Fix date picker"}
        results = client.get("/api/search?q=date+picker+-%23leaf", headers=HOST).get_json()
        assert {r["title"] for r in results} == {"Untagged", "[Task] Date picker elsewhere"}

    def test_tag_only_query(self, client, wiki_dir):
        write_page(wiki_dir, "tagged", "---\ntags: leaf\n---\n# Tagged\n\nNotes.\n")
        write_page(wiki_dir, "mentions", "# Mentions\n\nA leaf fell.\n")
        assert [r["slug"] for r in client.get("/api/search?q=%23leaf", headers=HOST).get_json()] == ["tagged"]

    def test_only_exclusions_find_nothing(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n\ngit.\n")
        assert client.get("/api/search?q=-git", headers=HOST).get_json() == []

    def test_exact_phrase_ranks_first(self, client, wiki_dir):
        # Regression: "alex again" put a note with both words apart (and
        # more often) above the one saying "Alex again".
        write_page(wiki_dir, "prep", "# Alex prep\n\nAlex, Alex and Alex. Ask again, again.\n")
        write_page(wiki_dir, "situation", "# Situation\n\n## Context\n\nCalm.\n\n"
                                          "## The ask\n\nThen Alex   again pushed back.\n")
        results = client.get("/api/search?q=alex+again", headers=HOST).get_json()
        assert [r["slug"] for r in results] == ["situation", "prep"]
        assert results[0]["href"] == "/page/situation?hl=Alex+again#the-ask"
        assert "<mark>Alex   again</mark>" in results[0]["excerpt"]

    def test_single_word_has_no_phrase_boost(self, client, wiki_dir):
        write_page(wiki_dir, "once", "# Once\n\nzebra.\n")
        write_page(wiki_dir, "often", "# Zebra\n\nzebra zebra.\n")
        assert client.get("/api/search?q=zebra", headers=HOST).get_json()[0]["slug"] == "often"

    def test_unlinked_only(self, client, wiki_dir):
        write_page(wiki_dir, "hub", "# Hub\n\nSee [[linked]]. Zebra.\n")
        write_page(wiki_dir, "linked", "# Linked\n\nZebra.\n")
        write_page(wiki_dir, "orphan", "# Orphan\n\nZebra.\n")
        client.post("/api/deck", json={"title": "Zebra task"}, headers=HOST)
        all_ = {r["title"] for r in client.get("/api/search?q=zebra", headers=HOST).get_json()}
        assert all_ == {"Hub", "Linked", "Orphan", "[Task] Zebra task"}
        only = [r["slug"] for r in client.get("/api/search?q=zebra&unlinked=1", headers=HOST).get_json()]
        assert sorted(only) == ["hub", "orphan"]   # nothing links to hub either; no tasks

    def test_result_meta_tells_alike_titles_apart(self, client, wiki_dir):
        import os
        path = write_page(wiki_dir, "conversations/2026-07-08_alex/prep", "# Alex Debrief\n\nquokka\n")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        write_page(wiki_dir, "quokka", "# Quokka\n\nquokka\n")
        open_ = client.post("/api/deck", json={"title": "quokka open"}, headers=HOST).get_json()
        done = client.post("/api/deck", json={"title": "quokka done", "list": "zoo"}, headers=HOST).get_json()
        client.put(f"/api/deck/{done['id']}", json={"completed": True}, headers=HOST)
        meta = {r["title"]: r["meta"] for r in client.get("/api/search?q=quokka", headers=HOST).get_json()}
        assert meta["Alex Debrief"] == "conversations/2026-07-08_alex · edited 2001-09-09"
        assert meta["Quokka"].startswith("edited 20")
        assert meta["[Task] quokka open"] == "Deck · open"
        assert re.fullmatch(r"Deck › zoo · done \d{4}-\d\d-\d\d", meta["[Task] quokka done"])
        assert open_["id"]
        page = client.get("/search?q=quokka", headers=HOST).get_data(as_text=True)
        assert '<span class="result-meta">conversations/2026-07-08_alex · edited 2001-09-09</span>' in page

    def test_tag_pill_lists_exactly_the_tagged(self, client, wiki_dir):
        # Regression: a pill searched the tag as a word, so it listed every
        # page mentioning "ops", tagged or not.
        write_page(wiki_dir, "tagged", "---\ntags: [ops]\n---\n# Runbook\n\nRestart steps.\n")
        write_page(wiki_dir, "mentions", "# Notes\n\nAsk the ops team.\n")
        client.post("/api/deck", json={"title": "Rotate keys", "tags": ["ops"]}, headers=HOST)
        page = client.get("/page/tagged", headers=HOST).get_data(as_text=True)
        href = re.search(r'class="task-tag page-tag" href="([^"]+)"', page).group(1)
        assert href == "/search?q=%23ops"
        results = client.get(href, headers=HOST).get_data(as_text=True)
        assert "Runbook" in results and "Rotate keys" in results and "Notes" not in results

    def test_limit(self, client, wiki_dir):
        for i in range(20):
            write_page(wiki_dir, f"walrus-{i}", f"# Walrus {i}\n\nwalrus\n")
        get = lambda qs: client.get(f"/api/search?q=walrus{qs}", headers=HOST).get_json()  # noqa: E731
        assert len(get("")) == 15
        assert len(get("&limit=16")) == 16
        assert len(get("&limit=1000")) == 20      # clamped to 100, only 20 exist
        assert len(get("&limit=0")) == 1          # at least one
        assert len(get("&limit=lots")) == 15      # not a number: the default

    def test_search_page_shows_more_with_a_count(self, client, wiki_dir):
        for i in range(20):
            write_page(wiki_dir, f"walrus-{i}", f"# Walrus {i}\n\nwalrus\n")
        page = client.get("/search?q=walrus", headers=HOST).get_data(as_text=True)
        assert page.count('class="search-result-card"') == 20
        assert '<p class="search-count">20 results</p>' in page
        for i in range(20, 60):
            write_page(wiki_dir, f"walrus-{i}", f"# Walrus {i}\n\nwalrus\n")
        page = client.get("/search?q=walrus&unlinked=1", headers=HOST).get_data(as_text=True)
        assert page.count('class="search-result-card"') == 50
        assert "The first 50 results · unlinked pages only" in page

    def test_date_filters(self, client, wiki_dir):
        import os
        for slug, day in [("july", 1_783_000_000), ("sept", 1_789_000_000), ("oct", 1_791_000_000)]:
            os.utime(write_page(wiki_dir, slug, f"# {slug}\n\nyak notes\n"), (day, day))
        # july: 2026-07-02, sept: 2026-09-10, oct: 2026-10-02 (UTC; any zone keeps the months)
        slugs = lambda q: sorted(r["slug"] for r in client.get(  # noqa: E731
            "/api/search", query_string={"q": q}, headers=HOST).get_json())
        assert slugs("yak edited:2026-09") == ["sept"]
        assert slugs("yak after:2026-09") == ["oct", "sept"]
        assert slugs("yak before:2026-09") == ["july"]
        assert slugs("yak -edited:2026-09") == ["july", "oct"]

    def test_only_dates_list_newest_first(self, client, wiki_dir):
        import os
        for slug, day in [("july", 1_783_000_000), ("sept", 1_789_000_000), ("oct", 1_791_000_000)]:
            os.utime(write_page(wiki_dir, slug, f"# {slug}\n\nnotes\n"), (day, day))
        os.utime(wiki_dir / "index.md", (1_000_000_000, 1_000_000_000))  # the fixture's, 2001
        results = client.get("/api/search", query_string={"q": "after:2026-07"}, headers=HOST).get_json()
        assert [r["slug"] for r in results] == ["oct", "sept", "july"]

    def test_tasks_dated_by_last_activity(self, client, wiki_dir):
        from datetime import date
        task = client.post("/api/deck", json={"title": "yak shaving"}, headers=HOST).get_json()
        today = date.today().isoformat()
        hits = client.get("/api/search", query_string={"q": f"yak edited:{today}"}, headers=HOST).get_json()
        assert [r.get("task_id") for r in hits] == [task["id"]]
        assert client.get("/api/search", query_string={"q": "yak before:2000"}, headers=HOST).get_json() == []

    def test_found_by_file_and_folder_names(self, client, wiki_dir):
        write_page(wiki_dir, "messages/2026-06-18_roof-update-alex-robin",
                   "# Roof repair status\n\nRevised timeline.\n")
        slugs = [r["slug"] for r in client.get("/api/search?q=robin", headers=HOST).get_json()]
        assert slugs == ["messages/2026-06-18_roof-update-alex-robin"]
        assert client.get("/api/search?q=messages", headers=HOST).get_json()  # folder name

    def test_file_name_does_not_outrank_content(self, client, wiki_dir):
        write_page(wiki_dir, "zebra-notes", "# Notes\n\nNothing here.\n")
        write_page(wiki_dir, "savanna", "# Savanna\n\nThe zebra grazes. A zebra runs.\n")
        slugs = [r["slug"] for r in client.get("/api/search?q=zebra", headers=HOST).get_json()]
        assert slugs == ["savanna", "zebra-notes"]

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

    def test_link_targets_are_not_indexed(self, client, wiki_dir):
        # Regression: every page with a /page/… link matched "page".
        write_page(wiki_dir, "linker", "# Linker\n\nSee [the notes](/page/zzqtarget).\n")
        results = client.get("/api/search?q=zzqtarget", headers=HOST).get_json()
        assert not any(r["slug"] == "linker" for r in results)
        results = client.get("/api/search?q=notes", headers=HOST).get_json()
        assert any(r["slug"] == "linker" for r in results)

    def test_bare_urls_stay_searchable(self, client, wiki_dir):
        write_page(wiki_dir, "bare", "# Bare\n\nsee https://zzqdomain.example/x\n")
        results = client.get("/api/search?q=zzqdomain", headers=HOST).get_json()
        assert any(r["slug"] == "bare" for r in results)

    def test_task_link_targets_are_not_indexed(self, client, wiki_dir):
        client.post("/api/deck", json={"title": "Read", "description": "[doc](https://zzqhost.example)"})
        results = client.get("/api/search?q=zzqhost", headers=HOST).get_json()
        assert results == []

    def test_task_found_by_tag(self, client, wiki_dir):
        # Regression: tasks were indexed by title and notes only, so a page's
        # tag pill (/search?q=<tag>) never listed the tasks with that tag.
        client.post("/api/deck", json={"title": "Call Bob", "tags": ["zzqtag"]})
        results = client.get("/api/search?q=zzqtag", headers=HOST).get_json()
        assert [r["title"] for r in results] == ["[Task] Call Bob"]
        assert "<mark>zzqtag</mark>" in results[0]["excerpt"]

    def test_non_utf8_page_does_not_break_search(self, client, wiki_dir):
        # Regression: one undecodable file made every search a 500.
        (wiki_dir / "latin1.md").write_bytes("# Latin\n\nzzqword Gr\xf6\xdfe\n".encode("latin-1"))
        write_page(wiki_dir, "fine", "# Fine\n\nzzqword here\n")
        resp = client.get("/api/search?q=zzqword", headers=HOST)
        assert resp.status_code == 200
        assert {r["slug"] for r in resp.get_json()} == {"latin1", "fine"}

    def test_page_vanishing_mid_search_is_skipped(self, client, wiki_dir, monkeypatch):
        write_page(wiki_dir, "gone", "# Gone\n\nzzqword\n")
        write_page(wiki_dir, "fine", "# Fine\n\nzzqword here\n")
        real = Path.read_text

        def flaky(self, *a, **kw):
            if self.name == "gone.md":
                raise FileNotFoundError(self)
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", flaky)
        resp = client.get("/api/search?q=zzqword", headers=HOST)
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.get_json()] == ["fine"]

    def test_index_is_reused_until_something_changes(self, client, wiki_dir):
        path = write_page(wiki_dir, "cached", "# Cached\n\nzzqold\n")
        first = server._search_index()
        assert server._search_index() is first
        path.write_text("# Cached\n\nzzqnew\n", encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        assert server._search_index() is not first
        assert [r["slug"] for r in client.get("/api/search?q=zzqnew", headers=HOST).get_json()] == ["cached"]
        assert client.get("/api/search?q=zzqold", headers=HOST).get_json() == []

    def test_new_task_and_new_page_reach_the_cached_index(self, client, wiki_dir):
        client.get("/api/search?q=warm", headers=HOST)
        client.post("/api/deck", json={"title": "zzqtask"})
        assert client.get("/api/search?q=zzqtask", headers=HOST).get_json()
        write_page(wiki_dir, "later", "# Later\n\nzzqpage\n")
        assert client.get("/api/search?q=zzqpage", headers=HOST).get_json()

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

    def test_search_page_task_links_to_the_task_by_id(self, client, wiki_dir):
        # A task result must open the deck (/deck) at that task (by id) — not the
        # bare _deck storage slug, and not a title search.
        tid = client.post("/api/deck", json={"title": "buymilk urgent chore"}).get_json()["id"]
        resp = client.get("/search?q=buymilk+urgent+chore", headers=HOST)
        body = resp.get_data(as_text=True)
        assert "/page/_deck" not in body
        assert f'href="/deck?task={tid}&amp;view=open"' in body

    def test_search_page_completed_task_links_to_completed_tab(self, client, wiki_dir):
        r = client.post("/api/deck", json={"title": "finishreport tuvwxyz"})
        tid = r.get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        resp = client.get("/search?q=finishreport+tuvwxyz", headers=HOST)
        body = resp.get_data(as_text=True)
        assert f'href="/deck?task={tid}&amp;view=completed"' in body


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



class TestServeUpload:
    """Uploads must not run scripts with Leaf's origin (stored XSS)."""

    def put(self, wiki_dir, name, data=b"x"):
        (wiki_dir / "_assets" / name).write_bytes(data)
        return f"/_assets/{name}"

    def test_image_is_inline(self, client, wiki_dir):
        resp = client.get(self.put(wiki_dir, "pic.png"), headers=HOST)
        assert resp.status_code == 200
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_pdf_is_inline(self, client, wiki_dir):
        resp = client.get(self.put(wiki_dir, "doc.pdf"), headers=HOST)
        assert "attachment" not in resp.headers.get("Content-Disposition", "")

    def test_html_is_a_download(self, client, wiki_dir):
        url = self.put(wiki_dir, "evil.html", b"<script>alert(1)</script>")
        resp = client.get(url, headers=HOST)
        assert resp.headers["Content-Disposition"].startswith("attachment")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_unknown_type_is_a_download(self, client, wiki_dir):
        resp = client.get(self.put(wiki_dir, "blob.xyzzy"), headers=HOST)
        assert resp.headers["Content-Disposition"].startswith("attachment")

    def test_svg_is_inline_but_sandboxed(self, client, wiki_dir):
        url = self.put(wiki_dir, "draw.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
        resp = client.get(url, headers=HOST)
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.headers["Content-Security-Policy"].startswith("sandbox")

    def test_upload_rejects_dot_names(self, client, wiki_dir):
        from io import BytesIO
        for name in ("..", ".git", ".hidden"):
            data = {"file": (BytesIO(b"x"), "a.png"), "filename": name, "overwrite": "1"}
            resp = client.post("/api/upload", data=data,
                               content_type="multipart/form-data", headers=HOST)
            assert resp.status_code == 400, name

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

    def test_login_page_has_no_action_buttons(self, auth_client):
        assert 'class="action-buttons"' not in auth_client.get("/login", headers=HOST).data.decode()

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

    def test_home_screen_app_files_served_without_auth(self, auth_client):
        # Browsers may fetch the manifest and icons without the session cookie.
        manifest = auth_client.get("/public/manifest.json", headers=HOST)
        assert manifest.status_code == 200
        data = json.loads(manifest.data)
        assert data["name"] == "Leaf" and data["start_url"] == "/"
        for icon in data["icons"]:
            resp = auth_client.get(icon["src"], headers=HOST)
            assert resp.status_code == 200, icon["src"]
            assert resp.mimetype == icon["type"]
        assert auth_client.get("/public/apple-touch-icon.png", headers=HOST).status_code == 200

    def test_pages_link_the_manifest_and_touch_icon(self, auth_client):
        body = auth_client.get("/login", headers=HOST).get_data(as_text=True)
        assert '<link rel="manifest" href="/public/manifest.json">' in body
        assert '<link rel="apple-touch-icon" href="/public/apple-touch-icon.png">' in body

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

    def test_api_scratch_creates_note(self, client, wiki_dir):
        resp = client.post("/api/scratch", data={"title": "My Note"}, headers=HOST)
        assert resp.status_code == 200
        slug = resp.get_json()["slug"]
        assert (wiki_dir / f"{slug}.md").exists()

    def test_daily_notes_are_gone(self, client, wiki_dir):
        # The feature was removed: no endpoint, no shortcut, no index. An old
        # daily note on disk is just an ordinary page.
        assert client.post("/api/daily", headers=HOST).status_code in (404, 405)
        (wiki_dir / "2026-01-01_daily.md").write_text("# 2026-01-01\n\nold\n", encoding="utf-8")
        resp = client.get("/page/2026-01-01_daily", headers=HOST)
        assert resp.status_code == 200 and b"old" in resp.data
        # The shortcuts help (in every page's shell) no longer offers it.
        assert "daily" not in resp.get_data(as_text=True).lower().replace("2026-01-01_daily", "")


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
        assert results[0]["href"] == f"/page/{results[0]['slug']}?hl=Tomatoes"

    def test_page_result_carries_the_matched_words(self, client, wiki_dir):
        # The page highlights what matched, as written there: stems, typos
        # and phrases included, each once, tasks without.
        write_page(wiki_dir, "run", "# Notes\n\nShe runs. He ran. RUNS again.\n")
        client.post("/api/deck", json={"title": "running shoes"}, headers=HOST)
        results = client.get("/api/search?q=running", headers=HOST).get_json()
        page = next(r for r in results if r["slug"] == "run")
        assert page["href"] == "/page/run?hl=runs"
        task = next(r for r in results if r.get("type") == "task")
        assert "hl=" not in task["href"]
        phrase = client.get("/api/search?q=runs+again", headers=HOST).get_json()[0]
        assert phrase["href"] == "/page/run?hl=RUNS+again"

    def test_task_result_href_opens_deck_at_the_task(self, client, wiki_dir):
        # Regression: the href prefilled the title into the deck's filter
        # field, where a "#word" in the title reads as a tag filter.
        tid = client.post("/api/deck", json={"title": "Water the ferns #3"},
                          headers=HOST).get_json()["id"]
        results = client.get("/api/search?q=ferns", headers=HOST).get_json()
        task_hits = [r for r in results if r.get("type") == "task"]
        assert task_hits, "expected a task hit"
        assert task_hits[0]["href"] == f"/deck?task={tid}&view=open"


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

    def test_linked_task_links_to_the_task_by_id(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        tid = client.post("/api/deck", json={"title": "Water [[Garden]]"},
                          headers=HOST).get_json()["id"]
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        assert f'href="/deck?task={tid}&amp;view=open"' in section

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


# ===========================================================================
# Rename keeps every kind of reference working
# ===========================================================================

class TestRenameUpdatesAllReferences:
    """Backlinks count markdown links, and deck tasks link pages too — a
    rename used to update only [[wikilinks]] in pages, so every other link to
    the page broke. It also rewrote wikilinks inside code examples."""

    def rename(self, client, old, new):
        resp = client.post(f"/rename/{old}", data={"new_name": new}, headers=HOST)
        assert resp.status_code == 302

    def test_markdown_links_rewritten(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        ref = write_page(wiki_dir, "ref", (
            "[bare](old) [ext](old.md) [abs](/page/old) [anchor](old#part)\n"
            "[other](older) [web](https://example.com/old) [img](/_assets/old.png)\n"))
        self.rename(client, "old", "new")
        assert ref.read_text() == (
            "[bare](new) [ext](new.md) [abs](/page/new) [anchor](new#part)\n"
            "[other](older) [web](https://example.com/old) [img](/_assets/old.png)\n")

    def test_relative_links_across_directories(self, client, wiki_dir):
        write_page(wiki_dir, "notes/old", "# Old\n")
        sibling = write_page(wiki_dir, "notes/sib", "[s](old) [s2](./old.md)\n")
        cousin = write_page(wiki_dir, "journal/day", "[c](../notes/old)\n")
        self.rename(client, "notes/old", "archive/new")
        assert sibling.read_text() == "[s](../archive/new) [s2](../archive/new.md)\n"
        assert cousin.read_text() == "[c](../archive/new)\n"
        assert server.find_backlinks("archive/new") and len(server.find_backlinks("archive/new")) == 2

    def test_backlinks_survive_rename(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        write_page(wiki_dir, "ref", "# Ref\n\n[x](old.md) and [[old]]\n")
        assert [b["slug"] for b in server.find_backlinks("old")] == ["ref"]
        self.rename(client, "old", "new")
        assert [b["slug"] for b in server.find_backlinks("new")] == ["ref"]

    def test_links_inside_code_untouched(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        ref = write_page(wiki_dir, "ref", (
            "[[old]] and `[[old]]` and [x](old)\n\n"
            "```\n[[old]] [x](old)\n```\n"))
        self.rename(client, "old", "new")
        assert ref.read_text() == (
            "[[new]] and `[[old]]` and [x](new)\n\n"
            "```\n[[old]] [x](old)\n```\n")

    def test_deck_tasks_follow_rename(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        linked = client.post("/api/deck", json={
            "title": "Do [[old]] and [[Old|label]]", "description": "see [x](/page/old)",
            }, headers=HOST).get_json()
        done = client.post("/api/deck", json={"title": "Done [[old]]"},
                           headers=HOST).get_json()
        client.put(f"/api/deck/{done['id']}", json={"completed": True}, headers=HOST)
        other = client.post("/api/deck", json={"title": "[[older]]"},
                            headers=HOST).get_json()
        self.rename(client, "old", "new")
        tasks = {t["id"]: t for t in client.get("/api/deck", headers=HOST).get_json()}
        assert tasks[linked["id"]]["title"] == "Do [[new]] and [[new|label]]"
        assert tasks[linked["id"]]["description"] == "see [x](/page/new)"
        assert tasks[other["id"]]["title"] == "[[older]]"
        completed = client.get("/api/deck/completed", headers=HOST).get_json()
        assert completed[0]["title"] == "Done [[new]]"
        html = client.get("/page/new", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' in html

    def test_rename_without_deck_files_does_not_create_them(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        self.rename(client, "old", "new")
        assert not (wiki_dir / "_deck.json").exists()


# ===========================================================================
# /deck — find the page that embeds the deck
# ===========================================================================

class TestDeckPage:
    """/deck is a built-in page with one full main deck. It used to redirect
    to whichever page embedded [[deck]], so where the due badge and task links
    landed depended on the wiki's pages (and, before that, /page/deck opened
    the editor in a wiki without deck.md)."""

    def get(self, client, qs=""):
        resp = client.get("/deck" + qs, headers=HOST)
        assert resp.status_code == 200
        return resp.get_data(as_text=True)

    def test_renders_one_main_deck(self, client, wiki_dir):
        body = self.get(client)
        assert body.count('class="tasks-app"') == 1
        assert 'data-init-list=""' in body
        assert 'data-init-tag=""' in body
        assert 'data-init-view=""' in body   # ?view= / session / Today decide
        assert '<script src="/public/deck.js" defer></script>' in body
        assert "<h1>Deck</h1>" in body

    def test_does_not_depend_on_pages(self, client, wiki_dir):
        # Embeds elsewhere (even a deck.md) are ordinary pages now.
        write_page(wiki_dir, "deck", "# My deck\n\n[[deck tag=work]]\n\n[[deck view=plan]]\n")
        body = self.get(client, "?task=abc&view=today")
        assert body.count('class="tasks-app"') == 1
        assert "My deck" not in body
        assert 'data-init-tag=""' in body

    def test_list(self, client, wiki_dir):
        body = self.get(client, "?list=Groceries&task=abc")
        assert 'data-init-list="groceries"' in body
        assert '<a href="/deck">Deck</a>' in body
        assert "<h1>groceries</h1>" in body

    def test_list_name_escaped(self, client, wiki_dir):
        body = self.get(client, "?list=<b>x")
        assert "<b>x" not in body

    def test_in_search_modal_list(self, client, wiki_dir):
        pages = client.get("/api/pages-list", headers=HOST).get_json()
        deck = [p for p in pages if p.get("href") == "/deck"]
        assert len(deck) == 1 and deck[0]["title"] == "Deck"

    def test_linked_task_in_list_links_to_list(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        tid = client.post("/api/deck", json={"title": "Seeds for [[Garden]]", "list": "shop"},
                          headers=HOST).get_json()["id"]
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert f'href="/deck?task={tid}&amp;view=open&amp;list=shop"' in body

    def test_due_badge_and_task_links_use_it(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Water [[Garden]]"}, headers=HOST)
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'href="/deck?view=focus"' in body
        assert "/page/deck" not in body


# ===========================================================================
# Cross-site request forgery
# ===========================================================================

class TestCrossSiteWrites:
    """Regression: any website could post a form to localhost:5000 and delete
    or overwrite pages (or deck tasks); the Host check alone let it through."""

    EVIL = {**HOST, "Origin": "https://evil.example"}
    SAME = {**HOST, "Origin": "http://localhost:5000"}

    def test_forged_delete_and_edit_are_refused(self, client, wiki_dir):
        write_page(wiki_dir, "victim", "# Victim\n\nkeep me\n")
        assert client.post("/delete/victim", headers=self.EVIL).status_code == 403
        resp = client.post("/edit/victim", data={"content": "<script>x</script>"}, headers=self.EVIL)
        assert resp.status_code == 403
        assert (wiki_dir / "victim.md").read_text(encoding="utf-8") == "# Victim\n\nkeep me\n"

    def test_forged_deck_writes_are_refused(self, client, wiki_dir):
        # text/plain: a cross-site fetch/form can send it without a preflight.
        tid = client.post("/api/deck", json={"title": "Keep"}, headers=HOST).get_json()["id"]
        resp = client.post("/api/deck", data='{"title": "Spam"}', content_type="text/plain", headers=self.EVIL)
        assert resp.status_code == 403
        assert client.delete(f"/api/deck/{tid}", headers=self.EVIL).status_code == 403
        assert client.put(f"/api/deck/{tid}", json={"title": "x"}, headers=self.EVIL).status_code == 403
        assert [t["title"] for t in client.get("/api/deck", headers=HOST).get_json()] == ["Keep"]

    def test_null_origin_is_refused(self, client, wiki_dir):
        # A sandboxed iframe on an attacker's page posts with Origin: null.
        write_page(wiki_dir, "victim", "# Victim\n")
        assert client.post("/delete/victim", headers={**HOST, "Origin": "null"}).status_code == 403
        assert (wiki_dir / "victim.md").exists()

    def test_same_origin_and_originless_writes_work(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n")
        write_page(wiki_dir, "b", "# B\n")
        assert client.post("/delete/a", headers=self.SAME).status_code == 302
        assert client.post("/delete/b", headers=HOST).status_code == 302   # curl, scripts
        assert client.post("/api/deck", json={"title": "Ok"}, headers=self.SAME).status_code == 201

    def test_reads_are_not_affected(self, client, wiki_dir):
        write_page(wiki_dir, "r", "# R\n")
        assert client.get("/page/r", headers=self.EVIL).status_code == 200


# ── Leaf version ──────────────────────────────────────────────────────────────


class TestVersion:
    def test_version_is_semver(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", server.VERSION)

    def test_shown_in_shortcuts_modal_and_api(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(server, "VERSION", "1.2.3")
        monkeypatch.setattr(server, "LEAF_BUILD", "abc1234 2026-09-26 12:00")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert '<p class="leaf-version">Leaf 1.2.3 · abc1234 2026-09-26 12:00</p>' in body
        assert '<meta name="generator" content="Leaf 1.2.3">' in body
        assert client.get("/api/version", headers=HOST).get_json() == {
            "version": "1.2.3", "build": "abc1234 2026-09-26 12:00"}

    def test_unknown_build_shows_version_only(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(server, "VERSION", "1.2.3")
        monkeypatch.setattr(server, "LEAF_BUILD", "unknown")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert '<p class="leaf-version">Leaf 1.2.3</p>' in body

    def test_build_escaped(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(server, "LEAF_BUILD", "<b>")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert "· &lt;b&gt;</p>" in body

    @pytest.mark.skipif(not server.GIT_ENABLED, reason="git not installed")
    def test_source_build_from_git(self, tmp_path):
        import subprocess
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                            *args], check=True, capture_output=True, env=server._GIT_ENV)
        git("init", "-q")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("x\n")
        git("add", "-A")
        git("commit", "-q", "-m", "c")
        v = server._source_build(tmp_path)
        assert re.fullmatch(r"[0-9a-f]{7,} \d{4}-\d\d-\d\d \d\d:\d\d", v), v
        (tmp_path / "src" / "a.py").write_text("y\n")
        assert server._source_build(tmp_path) == v + " +dirty"
        (tmp_path / "README").write_text("outside src\n")
        (tmp_path / "src" / "a.py").write_text("x\n")
        assert server._source_build(tmp_path) == v   # only src/ counts

    def test_source_build_without_git_repo(self, tmp_path):
        assert server._source_build(tmp_path) == "unknown"
