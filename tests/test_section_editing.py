"""Extensive tests for section editing — split_sections, _splice_section,
content_hash, section_char_range, and the /edit and /api/save HTTP routes.

Run with:
    pytest tests/test_section_editing.py -v
"""

import os
import sys
import textwrap
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: point the server at a temp WIKI_DATA dir before importing it.
# ---------------------------------------------------------------------------

_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_test_")
os.environ["WIKI_DATA"] = _SESSION_TMP
os.environ["ALLOW_REMOTE"] = "1"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import server                                                    # noqa: E402
from server import (split_sections, _splice_section,             # noqa: E402
                    content_hash, section_char_range)


# ===========================================================================
# Helpers
# ===========================================================================

def dedent(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


@pytest.fixture()
def wiki_dir(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    old_pages, old_wiki = server.PAGES_DIR, server.WIKI_DIR
    server.PAGES_DIR = pages
    server.WIKI_DIR  = pages
    yield pages
    server.PAGES_DIR = old_pages
    server.WIKI_DIR  = old_wiki


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


def section_fields(full_content: str, section_idx: int) -> dict:
    """Return hidden-field dict for a section edit (file_hash, start_char, end_char)."""
    rng = section_char_range(full_content, section_idx)
    assert rng is not None, f"section_char_range returned None for section {section_idx}"
    s, e = rng
    return {
        "file_hash": content_hash(full_content),
        "start_char": str(s),
        "end_char": str(e),
    }


HOST = {"Host": "localhost:5000"}


# ===========================================================================
# split_sections — unit tests (unchanged by refactor)
# ===========================================================================

class TestSplitSections:

    def test_no_headings_returns_single_element(self):
        assert split_sections("Just text\n") == ["Just text\n"]

    def test_h1_does_not_split(self):
        content = "# Title\n\nBody text.\n"
        assert split_sections(content) == [content]

    def test_h4_does_not_split(self):
        assert split_sections("#### Deep heading\n\nBody.\n") == ["#### Deep heading\n\nBody.\n"]

    def test_single_h2(self):
        content = "Preamble text.\n\n## Section A\n\nContent A.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[0] == "Preamble text.\n\n"
        assert result[1] == "## Section A\n\nContent A.\n"

    def test_single_h3(self):
        result = split_sections("Preamble.\n\n### Section A\n\nContent A.\n")
        assert len(result) == 2
        assert result[1].startswith("### Section A")

    def test_multiple_h2(self):
        content = dedent("""
            Preamble.

            ## Alpha

            Content alpha.

            ## Beta

            Content beta.

            ## Gamma

            Content gamma.
        """)
        result = split_sections(content)
        assert len(result) == 4
        assert result[1].startswith("## Alpha")
        assert result[2].startswith("## Beta")
        assert result[3].startswith("## Gamma")

    def test_mixed_h2_h3(self):
        content = dedent("""
            Intro.

            ## Section 1

            Body.

            ### Subsection 1.1

            Sub body.

            ## Section 2

            Body 2.
        """)
        result = split_sections(content)
        assert len(result) == 4

    def test_heading_at_very_start_empty_preamble(self):
        result = split_sections("## First\n\nContent.\n")
        assert result[0] == ""
        assert result[1] == "## First\n\nContent.\n"

    def test_h2_inside_backtick_fence_ignored(self):
        content = "Pre.\n\n```\n## Not a heading\n```\n\n## Real\n\nText.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real")

    def test_h3_inside_tilde_fence_ignored(self):
        content = "Pre.\n\n~~~\n### Not\n~~~\n\n## Real\n\nText.\n"
        assert len(split_sections(content)) == 2

    def test_unclosed_fence_suppresses_all_headings(self):
        content = "Pre.\n\n```\n## Fake\n\n## Also fake\n"
        assert len(split_sections(content)) == 1

    def test_join_reproduces_original(self):
        content = dedent("""
            Preamble.

            ## One

            Content one.

            ### Sub

            Sub content.

            ## Two

            Content two.
        """)
        assert "".join(split_sections(content)) == content

    def test_empty_content(self):
        assert split_sections("") == [""]

    def test_heading_without_space_not_split(self):
        content = "Preamble.\n\n##NoSpace\n\n## Real\n\nBody.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real")

    def test_three_sections_correct_indices(self):
        content = "pre\n\n## A\n\na\n\n## B\n\nb\n\n## C\n\nc\n"
        parts = split_sections(content)
        assert len(parts) == 4
        assert parts[1].startswith("## A")
        assert parts[2].startswith("## B")
        assert parts[3].startswith("## C")


# ===========================================================================
# content_hash / section_char_range — unit tests
# ===========================================================================

class TestContentHash:
    def test_deterministic(self):
        assert content_hash("hello") == content_hash("hello")

    def test_different_content_different_hash(self):
        assert content_hash("a") != content_hash("b")

    def test_length_is_16(self):
        assert len(content_hash("some content")) == 16


class TestSectionCharRange:
    def test_returns_none_for_invalid_index(self):
        content = "pre\n\n## A\n\nbody\n"
        assert section_char_range(content, 0) is None
        assert section_char_range(content, 2) is None

    def test_returns_correct_range_for_single_section(self):
        content = "Preamble.\n\n## Sec\n\nbody\n"
        rng = section_char_range(content, 1)
        assert rng is not None
        start, end = rng
        assert content[start:end] == "## Sec\n\nbody\n"

    def test_returns_correct_range_for_middle_section(self):
        content = "pre\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
        rng = section_char_range(content, 2)
        assert rng is not None
        s, e = rng
        section_text = content[s:e]
        assert section_text.startswith("## B")
        assert "b body" in section_text
        assert "a body" not in section_text
        assert "c body" not in section_text

    def test_ranges_cover_full_content(self):
        content = "pre\n\n## A\n\na\n\n## B\n\nb\n"
        parts = split_sections(content)
        # preamble is parts[0]; sections are parts[1:]
        reconstructed = parts[0]
        for i in range(1, len(parts)):
            s, e = section_char_range(content, i)
            reconstructed += content[s:e]
        assert reconstructed == content


# ===========================================================================
# _splice_section — unit tests
# ===========================================================================

class TestSpliceSection:

    def _write(self, tmp_path, content):
        p = tmp_path / "page.md"
        p.write_text(content, encoding="utf-8")
        return p

    def _splice(self, path, section_idx, new_content, full_content=None):
        """Helper: compute hash + range and call _splice_section."""
        if full_content is None:
            full_content = path.read_text(encoding="utf-8")
        fh = content_hash(full_content)
        rng = section_char_range(full_content, section_idx)
        assert rng is not None
        s, e = rng
        return _splice_section(path, fh, s, e, new_content)

    # ---- validity guards ----

    def test_returns_none_for_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.md"
        assert _splice_section(path, "abc", 0, 10, "content") is None

    def test_returns_none_for_wrong_hash(self, tmp_path):
        p = self._write(tmp_path, "pre\n\n## A\n\nbody\n")
        assert _splice_section(p, "wrong_hash", 5, 18, "new") is None

    def test_returns_none_for_out_of_bounds_range(self, tmp_path):
        content = "pre\n\n## A\n\nbody\n"
        p = self._write(tmp_path, content)
        fh = content_hash(content)
        assert _splice_section(p, fh, 0, len(content) + 10, "new") is None

    # ---- basic correctness ----

    def test_splice_only_section(self, tmp_path):
        original = "Preamble.\n\n## Section\n\nOld body.\n"
        p = self._write(tmp_path, original)
        result = self._splice(p, 1, "## Section\n\nNew body.")
        assert result is not None
        merged, new_hash, new_start, new_end = result
        assert "New body." in merged
        assert "Old body." not in merged
        assert merged.startswith("Preamble.")

    def test_splice_middle_section_leaves_others_intact(self, tmp_path):
        original = dedent("""
            Preamble.

            ## Alpha

            Content alpha.

            ## Beta

            Content beta.

            ## Gamma

            Content gamma.
        """)
        p = self._write(tmp_path, original)
        result = self._splice(p, 2, "## Beta\n\nUpdated beta.\n")
        assert result is not None
        merged = result[0]
        assert "Content alpha." in merged
        assert "Updated beta." in merged
        assert "Content beta." not in merged
        assert "Content gamma." in merged

    def test_splice_first_section(self, tmp_path):
        original = "pre\n\n## First\n\nold first\n\n## Second\n\nold second\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 1, "## First\n\nnew first\n")[0]
        assert "new first" in merged
        assert "old first" not in merged
        assert "old second" in merged

    def test_splice_last_section(self, tmp_path):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 2, "## B\n\nnew body b\n")[0]
        assert "new body b" in merged
        assert "body a" in merged

    def test_splice_does_not_duplicate_headings(self, tmp_path):
        original = "pre\n\n## A\n\nbody\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 1, "## A\n\nnew body\n")[0]
        assert merged.count("## A") == 1
        assert merged.count("## B") == 1

    # ---- roundtrip identity ----

    def test_roundtrip_identity(self, tmp_path):
        original = "pre\n\n## Alpha\n\ncontent\n\n## Beta\n\nmore\n"
        p = self._write(tmp_path, original)
        parts = split_sections(original)
        for i in range(1, len(parts)):
            result = self._splice(p, i, parts[i])
            assert result[0] == original, f"Round-trip failed at section {i}"

    def test_roundtrip_three_sections(self, tmp_path):
        original = "pre\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
        p = self._write(tmp_path, original)
        for i in range(1, 4):
            result = self._splice(p, i, split_sections(original)[i])
            assert result[0] == original

    # ---- trailing newline preservation ----

    def test_trailing_newline_preserved(self, tmp_path):
        original = "pre\n\n## A\n\nbody\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 1, "## A\n\nupdated")[0]
        assert merged.endswith("\n")

    # ---- preamble protection ----

    def test_preamble_never_overwritten(self, tmp_path):
        original = "UNIQUE_PREAMBLE\n\n## Sec\n\nbody\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 1, "## Sec\n\nnew body\n")[0]
        assert "UNIQUE_PREAMBLE" in merged

    # ---- no section lost ----

    def test_no_section_lost_when_editing_last(self, tmp_path):
        original = "pre\n\n## One\n\n1\n\n## Two\n\n2\n\n## Three\n\n3\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 3, "## Three\n\nupdated 3\n")[0]
        assert "1" in merged and "2" in merged and "updated 3" in merged

    def test_no_section_lost_when_editing_first(self, tmp_path):
        original = "pre\n\n## One\n\n1\n\n## Two\n\n2\n\n## Three\n\n3\n"
        p = self._write(tmp_path, original)
        merged = self._splice(p, 1, "## One\n\nupdated 1\n")[0]
        assert "updated 1" in merged and "2" in merged and "3" in merged

    # ---- hash-change detection ----

    def test_returns_none_when_file_changed_on_disk(self, tmp_path):
        """If the file is modified between load and save, splice must reject."""
        original = "pre\n\n## A\n\nbody\n"
        p = self._write(tmp_path, original)
        fh = content_hash(original)
        rng = section_char_range(original, 1)
        s, e = rng
        # Simulate another edit changing the file
        p.write_text("pre\n\n## A\n\nSOMEONE ELSE EDITED\n", encoding="utf-8")
        assert _splice_section(p, fh, s, e, "## A\n\nmy edit\n") is None

    # ---- updated anchors returned ----

    def test_returns_updated_hash_and_range(self, tmp_path):
        original = "pre\n\n## A\n\nshort\n\n## B\n\nb\n"
        p = self._write(tmp_path, original)
        result = self._splice(p, 1, "## A\n\nmuch longer replacement content\n")
        merged, new_hash, new_start, new_end = result
        assert new_hash == content_hash(merged)
        assert merged[new_start:new_end].startswith("## A")
        assert "much longer replacement content" in merged[new_start:new_end]

    def test_updated_range_allows_second_splice(self, tmp_path):
        """After a splice, using the returned hash+range for a second splice must work."""
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, original)
        # First splice
        result1 = self._splice(p, 1, "## A\n\nfirst edit\n")
        merged1, h1, s1, e1 = result1
        p.write_text(merged1, encoding="utf-8")
        # Second splice using returned anchors
        result2 = _splice_section(p, h1, s1, e1, "## A\n\nsecond edit\n")
        assert result2 is not None
        merged2 = result2[0]
        assert "second edit" in merged2
        assert "body b" in merged2


# ===========================================================================
# HTTP route tests — GET /edit/<slug>?section=N
# ===========================================================================

class TestEditPageGetSection:

    def test_full_edit_shows_all_content(self, client, wiki_dir):
        write_page(wiki_dir, "mypage", "# Title\n\n## A\n\nbody a\n\n## B\n\nbody b\n")
        resp = client.get("/edit/mypage", headers=HOST)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "body a" in html and "body b" in html

    def test_section_1_shows_only_that_section(self, client, wiki_dir):
        write_page(wiki_dir, "sp", "Pre.\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n")
        resp = client.get("/edit/sp?section=1", headers=HOST)
        html = resp.data.decode()
        assert "alpha body" in html
        assert "beta body" not in html

    def test_section_2_shows_second_section(self, client, wiki_dir):
        write_page(wiki_dir, "sp2", "pre\n\n## Alpha\n\nalpha\n\n## Beta\n\nbeta\n")
        resp = client.get("/edit/sp2?section=2", headers=HOST)
        html = resp.data.decode()
        assert "beta" in html
        assert "alpha" not in html or "Alpha" in html  # heading may appear

    def test_invalid_section_falls_back_to_full(self, client, wiki_dir):
        write_page(wiki_dir, "fb", "pre\n\n## A\n\nbody\n")
        resp = client.get("/edit/fb?section=99", headers=HOST)
        assert resp.status_code == 200  # no crash

    def test_section_embeds_hidden_fields(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "hf", content)
        resp = client.get("/edit/hf?section=1", headers=HOST)
        html = resp.data.decode()
        assert 'id="section-file-hash"' in html
        assert 'id="section-start-char"' in html
        assert 'id="section-end-char"' in html
        # Verify the hash value
        assert content_hash(content) in html

    def test_section_preamble_not_shown(self, client, wiki_dir):
        write_page(wiki_dir, "ph", "UNIQUE_PREAMBLE\n\n## A\n\nbody\n")
        resp = client.get("/edit/ph?section=1", headers=HOST)
        assert "UNIQUE_PREAMBLE" not in resp.data.decode()

    def test_section_rename_delete_buttons_hidden(self, client, wiki_dir):
        write_page(wiki_dir, "ns", "pre\n\n## A\n\nbody\n")
        resp = client.get("/edit/ns?section=1", headers=HOST)
        html = resp.data.decode()
        assert "rename-toggle" not in html
        assert "delete-btn" not in html

    def test_conflict_banner_shown_with_query_param(self, client, wiki_dir):
        write_page(wiki_dir, "cf", "# Title\n\nbody\n")
        resp = client.get("/edit/cf?conflict=1", headers=HOST)
        assert "conflict-banner" in resp.data.decode()


# ===========================================================================
# HTTP route tests — POST /edit/<slug> (Done button)
# ===========================================================================

class TestSavePageSection:

    def test_section_save_merges_correctly(self, client, wiki_dir):
        original = "pre\n\n## Alpha\n\nold alpha\n\n## Beta\n\nold beta\n"
        page = write_page(wiki_dir, "ss", original)
        fields = section_fields(original, 1)
        fields["content"] = "## Alpha\n\nnew alpha\n"
        resp = client.post("/edit/ss", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "new alpha" in saved
        assert "old alpha" not in saved
        assert "old beta" in saved

    def test_section_2_preserves_section_1(self, client, wiki_dir):
        original = "pre\n\n## One\n\none body\n\n## Two\n\ntwo body\n"
        page = write_page(wiki_dir, "ss2", original)
        fields = section_fields(original, 2)
        fields["content"] = "## Two\n\nupdated two\n"
        client.post("/edit/ss2", data=fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "one body" in saved
        assert "updated two" in saved

    def test_full_page_save_no_section_fields(self, client, wiki_dir):
        page = write_page(wiki_dir, "fs", "pre\n\n## A\n\nbody\n")
        new_content = "pre\n\n## A\n\nupdated body\n"
        client.post("/edit/fs", data={"content": new_content}, headers=HOST)
        assert page.read_text(encoding="utf-8") == new_content

    def test_hash_mismatch_redirects_with_conflict(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "hm", original)
        fields = section_fields(original, 1)
        fields["content"] = "## A\n\nnew body\n"
        fields["file_hash"] = "wrong_hash_value"
        resp = client.post("/edit/hm", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        assert "conflict=1" in resp.headers.get("Location", "")

    def test_preserves_preamble(self, client, wiki_dir):
        original = "KEEP_THIS_PREAMBLE\n\n## Sec\n\nold body\n"
        page = write_page(wiki_dir, "kp", original)
        fields = section_fields(original, 1)
        fields["content"] = "## Sec\n\nnew body\n"
        client.post("/edit/kp", data=fields, headers=HOST)
        assert "KEEP_THIS_PREAMBLE" in page.read_text(encoding="utf-8")


# ===========================================================================
# HTTP route tests — POST /api/save/<slug> (auto-save)
# ===========================================================================

class TestApiSaveSection:

    def test_section_save_merges_correctly(self, client, wiki_dir):
        original = "pre\n\n## X\n\nold x\n\n## Y\n\nold y\n"
        page = write_page(wiki_dir, "as", original)
        fields = section_fields(original, 1)
        fields["content"] = "## X\n\nnew x\n"
        resp = client.post("/api/save/as", data=fields, headers=HOST)
        assert resp.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "new x" in saved
        assert "old y" in saved

    def test_returns_updated_anchors(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "ua", original)
        fields = section_fields(original, 1)
        fields["content"] = "## A\n\nlonger body for section a\n"
        resp = client.post("/api/save/ua", data=fields, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "file_hash" in data
        assert "start_char" in data
        assert "end_char" in data
        assert data["file_hash"] != content_hash(original)  # content changed

    def test_second_autosave_uses_returned_anchors(self, client, wiki_dir):
        """Simulates two consecutive auto-saves, the second using anchors from the first."""
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "chain", original)

        # First auto-save
        fields1 = section_fields(original, 1)
        fields1["content"] = "## A\n\nedit 1\n"
        resp1 = client.post("/api/save/chain", data=fields1, headers=HOST)
        assert resp1.status_code == 200
        anchors = resp1.get_json()

        # Second auto-save using returned anchors
        fields2 = {
            "content": "## A\n\nedit 2\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        resp2 = client.post("/api/save/chain", data=fields2, headers=HOST)
        assert resp2.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "edit 2" in saved
        assert "body b" in saved  # B must not be lost!

    def test_full_save_returns_204(self, client, wiki_dir):
        page = write_page(wiki_dir, "full", "old\n")
        resp = client.post("/api/save/full", data={"content": "new\n"}, headers=HOST)
        assert resp.status_code == 204

    def test_hash_mismatch_returns_409(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "c409", original)
        fields = section_fields(original, 1)
        fields["content"] = "## A\n\nnew\n"
        fields["file_hash"] = "wrong_hash_value"
        resp = client.post("/api/save/c409", data=fields, headers=HOST)
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "file_changed"

    def test_unchanged_content_returns_same_anchors(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "noop", original)
        fields = section_fields(original, 1)
        rng = section_char_range(original, 1)
        sec_text = original[rng[0]:rng[1]]
        fields["content"] = sec_text.rstrip('\n')
        resp = client.post("/api/save/noop", data=fields, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["file_hash"] == content_hash(original)

    def test_repeated_autosave_is_idempotent(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "idem", original)
        fields = section_fields(original, 1)
        rng = section_char_range(original, 1)
        fields["content"] = original[rng[0]:rng[1]].rstrip('\n')
        for _ in range(5):
            client.post("/api/save/idem", data=fields, headers=HOST)
        assert page.read_text(encoding="utf-8") == original


# ===========================================================================
# Regression — the "lost section" scenarios
# ===========================================================================

class TestLostSectionRegression:

    def test_last_section_not_lost_after_editing_middle(self, client, wiki_dir):
        original = (
            "# Page\n\nIntro.\n\n"
            "## First\n\nFirst body.\n\n"
            "## Second\n\nSecond body.\n\n"
            "## Third\n\nThird body.\n"
        )
        page = write_page(wiki_dir, "lt1", original)
        fields = section_fields(original, 2)
        fields["content"] = "## Second\n\nUpdated second body.\n"
        client.post("/edit/lt1", data=fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "Third body." in saved
        assert "Updated second body." in saved
        assert "First body." in saved

    def test_section_not_lost_with_autosave_then_done(self, client, wiki_dir):
        """Auto-save fires, then Done. No section must disappear."""
        original = "preamble\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
        page = write_page(wiki_dir, "al", original)

        # Auto-save
        fields = section_fields(original, 2)
        fields["content"] = "## B\n\nb body modified\n"
        resp = client.post("/api/save/al", data=fields, headers=HOST)
        assert resp.status_code == 200
        anchors = resp.get_json()

        # Done using updated anchors
        done_fields = {
            "content": "## B\n\nb body final\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        client.post("/edit/al", data=done_fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "a body" in saved
        assert "b body final" in saved
        assert "c body" in saved, "Section C was lost!"

    def test_autosave_adding_heading_does_not_lose_subsequent_sections(self, client, wiki_dir):
        """User adds a new ## heading in the editor, auto-save fires, then Done.
        The subsequent sections must survive."""
        original = "preamble\n\n## Old\n\nold content\n\n## After\n\nafter content\n"
        page = write_page(wiki_dir, "ah", original)

        # Auto-save: user typed a new heading in their section
        fields = section_fields(original, 1)
        fields["content"] = "## Old-Part1\n\npart1\n\n## Old-Part2\n\npart2\n"
        resp = client.post("/api/save/ah", data=fields, headers=HOST)
        assert resp.status_code == 200
        after_autosave = page.read_text(encoding="utf-8")
        assert "after content" in after_autosave, "Section 'After' lost during auto-save!"

        # Done: user merges back to single heading
        anchors = resp.get_json()
        done_fields = {
            "content": "## Old-Merged\n\nmerged content\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        client.post("/edit/ah", data=done_fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "merged content" in saved
        assert "after content" in saved, "Section 'After' was lost after Done!"

    def test_multiple_autosaves_then_done(self, client, wiki_dir):
        """Multiple auto-saves followed by Done — all sections must survive."""
        original = "preamble\n\n## S1\n\ns1\n\n## S2\n\ns2\n\n## S3\n\ns3\n"
        page = write_page(wiki_dir, "ms", original)

        # First auto-save
        fields = section_fields(original, 1)
        fields["content"] = "## S1\n\ns1 draft\n"
        resp = client.post("/api/save/ms", data=fields, headers=HOST)
        anchors = resp.get_json()

        # Second auto-save
        fields2 = {
            "content": "## S1\n\ns1 final draft\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        resp2 = client.post("/api/save/ms", data=fields2, headers=HOST)
        anchors2 = resp2.get_json()

        # Done
        done_fields = {
            "content": "## S1\n\ns1 final\n",
            "file_hash": anchors2["file_hash"],
            "start_char": str(anchors2["start_char"]),
            "end_char": str(anchors2["end_char"]),
        }
        client.post("/edit/ms", data=done_fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        secs = split_sections(saved)
        assert len(secs) == 4
        assert "s1 final" in saved
        assert "s2" in saved, "S2 was lost!"
        assert "s3" in saved, "S3 was lost!"
