"""Extensive tests for section editing — split_sections, _merge_section, and
the /edit and /api/save HTTP routes.

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

# Must be set before importing server so module-level PAGES_DIR picks it up.
_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_test_")
os.environ["WIKI_DATA"] = _SESSION_TMP
os.environ["ALLOW_REMOTE"] = "1"   # skip Host-header check in tests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import server                                       # noqa: E402
from server import split_sections, _merge_section   # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================

def dedent(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


@pytest.fixture()
def wiki_dir(tmp_path):
    """Per-test temp wiki directory — patches server globals in place."""
    pages = tmp_path / "pages"
    pages.mkdir()
    old_pages = server.PAGES_DIR
    old_wiki  = server.WIKI_DIR
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


HOST = {"Host": "localhost:5000"}


# ===========================================================================
# split_sections — unit tests
# ===========================================================================

class TestSplitSections:

    def test_no_headings_returns_single_element(self):
        content = "Just some text\nNo headings here.\n"
        result = split_sections(content)
        assert result == [content]

    def test_h1_does_not_split(self):
        content = "# Title\n\nBody text.\n"
        result = split_sections(content)
        assert result == [content]

    def test_h4_does_not_split(self):
        content = "#### Deep heading\n\nBody.\n"
        result = split_sections(content)
        assert result == [content]

    def test_single_h2_splits_into_preamble_and_one_section(self):
        content = "Preamble text.\n\n## Section A\n\nContent A.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[0] == "Preamble text.\n\n"
        assert result[1] == "## Section A\n\nContent A.\n"

    def test_single_h3_splits_into_preamble_and_one_section(self):
        content = "Preamble.\n\n### Section A\n\nContent A.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("### Section A")

    def test_multiple_h2_headings(self):
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
        assert result[0].strip() == "Preamble."
        assert result[1].startswith("## Alpha")
        assert result[2].startswith("## Beta")
        assert result[3].startswith("## Gamma")

    def test_mixed_h2_and_h3_headings(self):
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
        assert result[1].startswith("## Section 1")
        assert result[2].startswith("### Subsection 1.1")
        assert result[3].startswith("## Section 2")

    def test_heading_at_very_start_gives_empty_preamble(self):
        content = "## First\n\nContent.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[0] == ""
        assert result[1] == "## First\n\nContent.\n"

    def test_h2_inside_backtick_fence_is_ignored(self):
        content = dedent("""
            Preamble.

            ```
            ## Not a heading
            some code
            ```

            ## Real Heading

            Text.
        """)
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real Heading")

    def test_h3_inside_tilde_fence_is_ignored(self):
        content = dedent("""
            Preamble.

            ~~~python
            ### Not a heading
            code
            ~~~

            ## Real

            Text.
        """)
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real")

    def test_h2_inside_longer_backtick_fence(self):
        content = dedent("""
            ```markdown
            ## Fake heading in docs
            ```

            ## Real

            Text.
        """)
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real")

    def test_fence_without_language_identifier(self):
        content = "Preamble.\n\n```\n## Inside fence\n```\n\n## Outside\n\nBody.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Outside")

    def test_unclosed_fence_treats_everything_as_fenced(self):
        """An unclosed fence should suppress all headings inside it."""
        content = "Preamble.\n\n```\n## Heading in unclosed fence\n\n## Another\n"
        result = split_sections(content)
        assert len(result) == 1

    def test_two_independent_fences_heading_between_is_real(self):
        content = dedent("""
            ```
            ## fake 1
            ```

            ## Real

            ```
            ## fake 2
            ```
        """)
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real")

    def test_join_sections_reproduces_original(self):
        """Joining split_sections output must exactly reproduce the original."""
        content = dedent("""
            Preamble text here.

            ## Section One

            Content of section one.

            ### Sub-section

            Sub content.

            ## Section Two

            Content of section two.
        """)
        result = split_sections(content)
        assert "".join(result) == content

    def test_empty_content(self):
        assert split_sections("") == [""]

    def test_only_whitespace(self):
        assert split_sections("   \n\n") == ["   \n\n"]

    def test_h4_before_h2_does_not_prevent_h2_split(self):
        content = "#### h4\n\n## h2\n\nBody.\n"
        result = split_sections(content)
        assert len(result) == 2

    def test_heading_without_space_is_not_split(self):
        """##NoSpace is not a valid Markdown heading — must not split."""
        content = "Preamble.\n\n##NoSpace\n\n## Real\n\nBody.\n"
        result = split_sections(content)
        assert len(result) == 2
        assert result[1].startswith("## Real")

    def test_section_trailing_newline_preserved(self):
        content = "Preamble.\n\n## A\n\ncontent\n\n## B\n\nend\n"
        parts = split_sections(content)
        assert parts[1].endswith("\n")
        assert parts[2].endswith("\n")

    def test_preamble_is_index_zero(self):
        content = "intro\n\n## Sec\n\nbody\n"
        parts = split_sections(content)
        assert parts[0] == "intro\n\n"

    def test_three_sections_correct_indices(self):
        content = "pre\n\n## A\n\na\n\n## B\n\nb\n\n## C\n\nc\n"
        parts = split_sections(content)
        assert len(parts) == 4    # preamble + 3 sections
        assert parts[1].startswith("## A")
        assert parts[2].startswith("## B")
        assert parts[3].startswith("## C")

    def test_section_content_does_not_bleed_into_next(self):
        content = "pre\n\n## A\n\nonly a\n\n## B\n\nonly b\n"
        parts = split_sections(content)
        assert "only a" in parts[1]
        assert "only a" not in parts[2]
        assert "only b" in parts[2]
        assert "only b" not in parts[1]


# ===========================================================================
# _merge_section — unit tests
# ===========================================================================

class TestMergeSection:

    def _write(self, tmp_path, content):
        p = tmp_path / "page.md"
        p.write_text(content, encoding="utf-8")
        return p

    # ---- validity guards ----

    def test_returns_none_for_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.md"
        assert _merge_section(path, 1, "content") is None

    def test_returns_none_for_section_zero(self, tmp_path):
        p = self._write(tmp_path, "pre\n\n## A\n\nbody\n")
        assert _merge_section(p, 0, "## A\n\nnew body\n") is None

    def test_returns_none_for_out_of_range_section(self, tmp_path):
        p = self._write(tmp_path, "pre\n\n## A\n\nbody\n")
        assert _merge_section(p, 2, "## A\n\nnew\n") is None

    def test_returns_none_for_file_with_no_sections(self, tmp_path):
        """File with no ## headings has only a preamble; section=1 is invalid."""
        p = self._write(tmp_path, "No headings here.\n")
        assert _merge_section(p, 1, "new content\n") is None

    def test_returns_none_for_negative_section(self, tmp_path):
        p = self._write(tmp_path, "pre\n\n## A\n\nbody\n")
        assert _merge_section(p, -1, "content") is None

    # ---- basic merge correctness ----

    def test_merge_only_section(self, tmp_path):
        original = "Preamble.\n\n## Section\n\nOld body.\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## Section\n\nNew body.")
        assert result is not None
        assert "New body." in result
        assert "Old body." not in result
        assert result.startswith("Preamble.")

    def test_merge_middle_section_leaves_others_intact(self, tmp_path):
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
        result = _merge_section(p, 2, "## Beta\n\nUpdated beta.\n")
        assert result is not None
        assert "Content alpha." in result
        assert "Updated beta." in result
        assert "Content beta." not in result
        assert "Content gamma." in result

    def test_merge_first_section(self, tmp_path):
        original = "pre\n\n## First\n\nold first\n\n## Second\n\nold second\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## First\n\nnew first\n")
        assert result is not None
        assert "new first" in result
        assert "old first" not in result
        assert "old second" in result

    def test_merge_last_section(self, tmp_path):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 2, "## B\n\nnew body b\n")
        assert result is not None
        assert "new body b" in result
        # Old content replaced — "## B\n\nbody b\n" must not appear verbatim
        assert "## B\n\nbody b\n" not in result
        assert "body a" in result

    def test_merge_does_not_duplicate_headings(self, tmp_path):
        original = "pre\n\n## A\n\nbody\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## A\n\nnew body\n")
        assert result.count("## A") == 1
        assert result.count("## B") == 1

    # ---- roundtrip identity ----

    def test_roundtrip_split_merge_identity(self, tmp_path):
        original = "pre\n\n## Alpha\n\ncontent\n\n## Beta\n\nmore\n"
        p = self._write(tmp_path, original)
        sections = split_sections(original)
        for i in range(1, len(sections)):
            result = _merge_section(p, i, sections[i])
            assert result == original, f"Round-trip failed at section {i}"

    def test_roundtrip_three_sections_identity(self, tmp_path):
        original = "pre\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
        p = self._write(tmp_path, original)
        sections = split_sections(original)
        for i in range(1, len(sections)):
            result = _merge_section(p, i, sections[i])
            assert result == original, f"Round-trip failed at section {i}"

    # ---- trailing newline preservation ----

    def test_trailing_newline_preserved_when_new_content_has_none(self, tmp_path):
        original = "pre\n\n## A\n\nbody\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## A\n\nupdated")   # no trailing newline
        assert result is not None
        assert result.endswith("\n"), "Trailing newline must be preserved"

    def test_merge_exact_trailing_newline_restoration(self, tmp_path):
        original = "pre\n\n## Sec\n\ncontent\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## Sec\n\nnew content")
        assert result == "pre\n\n## Sec\n\nnew content\n"

    def test_trailing_newlines_of_last_replaced_section_kept(self, tmp_path):
        """When span > 1, trailing newlines from the *last* replaced section are kept."""
        disk = "pre\n\n## A-sub1\n\npart1\n\n## A-sub2\n\npart2\n\n## B\n\nb\n"
        p = self._write(tmp_path, disk)
        result = _merge_section(p, 1, "## A\n\nnew a", section_count=3)
        assert result is not None
        # The trailing newline of the last replaced section (A-sub2) should be kept
        # and the content after (## B) should immediately follow
        assert "new a" in result
        assert "## B" in result

    # ---- preamble protection ----

    def test_preamble_never_overwritten(self, tmp_path):
        original = "UNIQUE_PREAMBLE_MARKER\n\n## Sec\n\nbody\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## Sec\n\nnew body\n")
        assert result is not None
        assert "UNIQUE_PREAMBLE_MARKER" in result

    # ---- section_count / span logic ----

    def test_span_is_one_when_disk_matches_section_count(self, tmp_path):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, original)
        section_count = len(split_sections(original))   # 3
        result = _merge_section(p, 1, "## A\n\nnew a\n", section_count)
        assert result is not None
        assert result.count("## B") == 1
        assert "new a" in result

    def test_span_expands_to_absorb_one_extra_autosave_section(self, tmp_path):
        """Auto-save added one extra heading; Done must collapse it back."""
        section_count = 3   # preamble + A + B when editor opened
        # Disk after auto-save expanded A into A1+A2
        disk = "pre\n\n## A1\n\npart1\n\n## A2\n\npart2\n\n## B\n\nbody b\n"
        p = self._write(tmp_path, disk)
        result = _merge_section(p, 1, "## A-final\n\nfinal body\n", section_count)
        assert result is not None
        secs = split_sections(result)
        assert len(secs) == 3
        assert "A-final" in result
        assert "A1" not in result
        assert "A2" not in result
        assert "body b" in result

    def test_span_expands_to_absorb_two_extra_autosave_sections(self, tmp_path):
        """Auto-save added two extra headings; Done must collapse all of them."""
        section_count = 3   # preamble + A + B
        disk = (
            "pre\n\n"
            "## A-part1\n\ncontent1\n\n"
            "## A-part2\n\ncontent2\n\n"
            "## A-part3\n\ncontent3\n\n"
            "## B\n\nbody b\n"
        )
        p = self._write(tmp_path, disk)
        # disk has 5 sections; span = 1 + (5-3) = 3 → replaces sections[1:4]
        result = _merge_section(p, 1, "## A\n\nfull rewrite\n", section_count)
        assert result is not None
        secs = split_sections(result)
        assert len(secs) == 3
        assert "full rewrite" in result
        assert "content1" not in result
        assert "content2" not in result
        assert "content3" not in result
        assert "body b" in result

    def test_section_count_none_uses_span_one_regardless(self, tmp_path):
        """With no section_count, span is always 1."""
        disk = "pre\n\n## X\n\nx\n\n## Y\n\ny\n\n## Z\n\nz\n"
        p = self._write(tmp_path, disk)
        result = _merge_section(p, 1, "## X\n\nnew x\n", section_count=None)
        assert result is not None
        secs = split_sections(result)
        assert len(secs) == 4
        assert "new x" in result
        assert "y" in result
        assert "z" in result

    def test_no_section_lost_when_editing_last_section(self, tmp_path):
        original = "pre\n\n## One\n\nbody one\n\n## Two\n\nbody two\n\n## Three\n\nbody three\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 3, "## Three\n\nupdated three\n")
        assert result is not None
        assert "body one" in result
        assert "body two" in result
        assert "updated three" in result

    def test_no_section_lost_when_editing_first_section(self, tmp_path):
        original = "pre\n\n## One\n\nbody one\n\n## Two\n\nbody two\n\n## Three\n\nbody three\n"
        p = self._write(tmp_path, original)
        result = _merge_section(p, 1, "## One\n\nupdated one\n")
        assert result is not None
        assert "updated one" in result
        assert "body two" in result
        assert "body three" in result

    # ---- span boundary edge cases ----

    def test_span_clamped_when_last_section_edited_with_extra_disk_sections(self, tmp_path):
        """end = min(section + span, len(sections)) — must not go out of bounds."""
        # section_count=3, disk has 4 sections, editing section 3 (last original section)
        disk = "pre\n\n## A\n\na\n\n## B\n\nb\n\n## C\n\nc\n"
        p = self._write(tmp_path, disk)
        result = _merge_section(p, 3, "## C\n\nnew c\n", section_count=3)
        assert result is not None
        assert "new c" in result
        assert "## A" in result
        assert "## B" in result

    def test_regression_section_after_span_not_discarded(self, tmp_path):
        """Merging a section whose span reaches near-end must not discard
        sections after the span."""
        # 5 sections on disk; section_count=4; editing section 1
        # span = 1 + (5-4) = 2; end = min(1+2, 5) = 3
        # sections[1:3] = [A, A-extra]; B and C must survive
        disk = (
            "pre\n\n"
            "## A\n\na\n\n"
            "## A-extra\n\nextra\n\n"
            "## B\n\nb\n\n"
            "## C\n\nc\n"
        )
        p = self._write(tmp_path, disk)
        result = _merge_section(p, 1, "## A\n\nupdated a\n", section_count=4)
        assert result is not None
        secs = split_sections(result)
        assert len(secs) == 4   # preamble + A + B + C
        assert "updated a" in result
        assert "A-extra" not in result
        assert "## B" in result
        assert "## C" in result


# ===========================================================================
# HTTP route tests — GET /edit/<slug>?section=N
# ===========================================================================

class TestEditPageGetSection:

    def test_get_edit_without_section_shows_full_content(self, client, wiki_dir):
        content = "# Title\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "mypage", content)
        resp = client.get("/edit/mypage", headers=HOST)
        assert resp.status_code == 200
        html_text = resp.data.decode()
        assert "body a" in html_text
        assert "body b" in html_text

    def test_get_edit_section_1_shows_only_that_sections_content(self, client, wiki_dir):
        content = "Preamble.\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
        write_page(wiki_dir, "sectpage", content)
        resp = client.get("/edit/sectpage?section=1", headers=HOST)
        assert resp.status_code == 200
        html_text = resp.data.decode()
        assert "alpha body" in html_text
        assert "beta body" not in html_text

    def test_get_edit_section_2_shows_only_second_section(self, client, wiki_dir):
        content = "pre\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
        write_page(wiki_dir, "sectpage2", content)
        resp = client.get("/edit/sectpage2?section=2", headers=HOST)
        assert resp.status_code == 200
        html_text = resp.data.decode()
        assert "beta body" in html_text
        assert "alpha body" not in html_text

    def test_get_edit_section_0_falls_back_to_full_edit(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "fallback1", content)
        resp = client.get("/edit/fallback1?section=0", headers=HOST)
        assert resp.status_code == 200
        html_text = resp.data.decode()
        assert "body" in html_text   # full content visible

    def test_get_edit_out_of_range_section_falls_back_to_full(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "fallback2", content)
        resp = client.get("/edit/fallback2?section=99", headers=HOST)
        assert resp.status_code == 200   # must not crash

    def test_get_edit_section_embeds_section_count_in_form_action(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "sectcount", content)
        resp = client.get("/edit/sectcount?section=1", headers=HOST)
        html_text = resp.data.decode()
        # preamble + A + B = 3 sections; form action must include section_count=3
        assert "section_count=3" in html_text

    def test_get_edit_section_preamble_not_shown_in_textarea(self, client, wiki_dir):
        content = "UNIQUE_PREAMBLE_TEXT\n\n## A\n\nbody\n"
        write_page(wiki_dir, "preamblehide", content)
        resp = client.get("/edit/preamblehide?section=1", headers=HOST)
        html_text = resp.data.decode()
        assert "UNIQUE_PREAMBLE_TEXT" not in html_text

    def test_get_edit_section_rename_and_delete_buttons_hidden(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "nosidebar", content)
        resp = client.get("/edit/nosidebar?section=1", headers=HOST)
        html_text = resp.data.decode()
        assert "rename-toggle" not in html_text
        assert "delete-btn" not in html_text


# ===========================================================================
# HTTP route tests — POST /edit/<slug>?section=N&section_count=M
# ===========================================================================

class TestSavePageSection:

    def test_post_edit_section_merges_and_leaves_other_sections(self, client, wiki_dir):
        original = "pre\n\n## Alpha\n\nold alpha\n\n## Beta\n\nold beta\n"
        page = write_page(wiki_dir, "savesect", original)
        resp = client.post(
            "/edit/savesect?section=1&section_count=3",
            data={"content": "## Alpha\n\nnew alpha\n"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "new alpha" in saved
        assert "old alpha" not in saved
        assert "old beta" in saved   # must not be lost!

    def test_post_edit_section_2_preserves_section_1(self, client, wiki_dir):
        original = "pre\n\n## One\n\none body\n\n## Two\n\ntwo body\n"
        page = write_page(wiki_dir, "savesect2", original)
        client.post(
            "/edit/savesect2?section=2&section_count=3",
            data={"content": "## Two\n\nupdated two\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        assert "one body" in saved
        assert "updated two" in saved

    def test_post_edit_full_page_no_section_param(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody\n"
        page = write_page(wiki_dir, "fullsave", original)
        new_content = "pre\n\n## A\n\nupdated body\n"
        client.post(
            "/edit/fullsave",
            data={"content": new_content},
            headers=HOST,
        )
        assert page.read_text(encoding="utf-8") == new_content

    def test_post_edit_invalid_section_redirects_gracefully(self, client, wiki_dir):
        """Section out of range → redirect, no crash."""
        content = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "invalidsect", content)
        resp = client.post(
            "/edit/invalidsect?section=99&section_count=3",
            data={"content": "## A\n\nnew\n"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)

    def test_post_edit_section_with_autosave_expansion(self, client, wiki_dir):
        """Regression: auto-save expanded the section on disk; Done must not lose
        the section that follows."""
        # Disk state after auto-save: A was expanded to A-sub1 + A-sub2
        disk_state = "pre\n\n## A-sub1\n\npart1\n\n## A-sub2\n\npart2\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "autoexp", disk_state)
        resp = client.post(
            "/edit/autoexp?section=1&section_count=3",
            data={"content": "## A-final\n\nfinal body\n"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        secs = split_sections(saved)
        assert len(secs) == 3   # preamble + A-final + B
        assert "A-final" in saved
        assert "A-sub1" not in saved
        assert "A-sub2" not in saved
        assert "body b" in saved   # B must NOT be lost

    def test_post_edit_preserves_preamble(self, client, wiki_dir):
        original = "PRESERVE_THIS_PREAMBLE\n\n## Sec\n\nold body\n"
        page = write_page(wiki_dir, "keeppreable", original)
        client.post(
            "/edit/keeppreable?section=1&section_count=2",
            data={"content": "## Sec\n\nnew body\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        assert "PRESERVE_THIS_PREAMBLE" in saved


# ===========================================================================
# HTTP route tests — POST /api/save/<slug>?section=N&section_count=M
# ===========================================================================

class TestApiSaveSection:

    def test_api_save_section_merges_correctly(self, client, wiki_dir):
        original = "pre\n\n## X\n\nold x\n\n## Y\n\nold y\n"
        page = write_page(wiki_dir, "apisave", original)
        resp = client.post(
            "/api/save/apisave?section=1&section_count=3",
            data={"content": "## X\n\nnew x\n"},
            headers=HOST,
        )
        assert resp.status_code == 204
        saved = page.read_text(encoding="utf-8")
        assert "new x" in saved
        assert "old y" in saved   # must not be lost!

    def test_api_save_without_section_replaces_full_file(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody\n"
        page = write_page(wiki_dir, "apisavefull", original)
        new_content = "completely new content\n"
        resp = client.post(
            "/api/save/apisavefull",
            data={"content": new_content},
            headers=HOST,
        )
        assert resp.status_code == 204
        assert page.read_text(encoding="utf-8") == new_content

    def test_api_save_invalid_section_returns_204_gracefully(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody\n"
        page = write_page(wiki_dir, "apigrace", content)
        resp = client.post(
            "/api/save/apigrace?section=99&section_count=3",
            data={"content": "## Bogus\n\nstuff\n"},
            headers=HOST,
        )
        assert resp.status_code == 204
        assert page.read_text(encoding="utf-8") == content  # unchanged

    def test_api_save_no_write_when_content_unchanged(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "noop", original)
        mtime_before = page.stat().st_mtime
        resp = client.post(
            "/api/save/noop?section=1&section_count=3",
            data={"content": "## A\n\nbody a\n"},
            headers=HOST,
        )
        assert resp.status_code == 204
        assert page.stat().st_mtime == mtime_before   # file not touched

    def test_api_save_repeated_autosave_is_idempotent(self, client, wiki_dir):
        """Multiple auto-saves with same content must not corrupt the file."""
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "idempotent", original)
        for _ in range(5):
            client.post(
                "/api/save/idempotent?section=1&section_count=3",
                data={"content": "## A\n\nbody a\n"},
                headers=HOST,
            )
        saved = page.read_text(encoding="utf-8")
        assert saved == original

    def test_api_save_then_post_edit_roundtrip(self, client, wiki_dir):
        """Auto-save followed by Done must produce coherent output."""
        original = "pre\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
        page = write_page(wiki_dir, "roundtrip", original)

        # Auto-save: user added extra heading in section 1
        client.post(
            "/api/save/roundtrip?section=1&section_count=3",
            data={"content": "## Alpha-part1\n\npart1\n\n## Alpha-part2\n\npart2\n"},
            headers=HOST,
        )
        mid_state = page.read_text(encoding="utf-8")
        assert "Alpha-part1" in mid_state
        assert "beta body" in mid_state       # Beta must survive auto-save!

        # Done: user simplified back to a single section
        client.post(
            "/edit/roundtrip?section=1&section_count=3",
            data={"content": "## Alpha\n\nfinal alpha\n"},
            headers=HOST,
        )
        final = page.read_text(encoding="utf-8")
        secs = split_sections(final)
        assert len(secs) == 3          # preamble + Alpha + Beta
        assert "final alpha" in final
        assert "Alpha-part1" not in final
        assert "beta body" in final    # Beta must absolutely not be lost

    def test_api_save_section_count_mismatch_recovers(self, client, wiki_dir):
        """Disk has more sections than section_count; trailing sections must survive."""
        disk_expanded = "pre\n\n## A1\n\na1\n\n## A2\n\na2\n\n## B\n\nb\n\n## C\n\nc\n"
        page = write_page(wiki_dir, "mismatch", disk_expanded)
        # section_count=4 means original had preamble + A + B + C
        resp = client.post(
            "/api/save/mismatch?section=1&section_count=4",
            data={"content": "## A\n\nnew a\n"},
            headers=HOST,
        )
        assert resp.status_code == 204
        saved = page.read_text(encoding="utf-8")
        secs = split_sections(saved)
        assert len(secs) == 4   # preamble + A + B + C
        assert "new a" in saved
        assert "## B" in saved   # B must not be lost
        assert "## C" in saved   # C must not be lost


# ===========================================================================
# Regression — the "lost section" scenarios
# ===========================================================================

class TestLostSectionRegression:
    """Targeted regression tests reproducing the bug where a section disappeared
    after saving."""

    def test_last_section_not_lost_after_editing_middle(self, client, wiki_dir):
        original = (
            "# My Wiki Page\n\n"
            "Intro text.\n\n"
            "## First Section\n\nFirst body.\n\n"
            "## Second Section\n\nSecond body.\n\n"
            "## Third Section\n\nThird body.\n"
        )
        page = write_page(wiki_dir, "losttest", original)
        client.post(
            "/edit/losttest?section=2&section_count=4",
            data={"content": "## Second Section\n\nUpdated second body.\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        assert "Third Section" in saved, "Third section was lost after editing Second!"
        assert "Third body." in saved
        assert "Updated second body." in saved
        assert "First body." in saved

    def test_first_section_not_lost_after_editing_second(self, client, wiki_dir):
        original = (
            "## A\n\nbody a\n\n"
            "## B\n\nbody b\n\n"
            "## C\n\nbody c\n"
        )
        page = write_page(wiki_dir, "losttesta", original)
        # split_sections produces ["", "## A...", "## B...", "## C..."] → section_count=4
        client.post(
            "/edit/losttesta?section=2&section_count=4",
            data={"content": "## B\n\nnew body b\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        assert "body a" in saved
        assert "new body b" in saved
        assert "body c" in saved

    def test_section_not_lost_with_autosave_before_done(self, client, wiki_dir):
        """The realistic flow: auto-save fires, then user clicks Done.
        No section must disappear."""
        original = "preamble\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
        page = write_page(wiki_dir, "autolost", original)

        # Auto-save
        client.post(
            "/api/save/autolost?section=2&section_count=4",
            data={"content": "## B\n\nb body modified\n"},
            headers=HOST,
        )
        # Done
        client.post(
            "/edit/autolost?section=2&section_count=4",
            data={"content": "## B\n\nb body final\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        assert "a body" in saved
        assert "b body final" in saved
        assert "c body" in saved, "Section C was lost after Done!"

    def test_adding_heading_in_section_then_saving_does_not_lose_subsequent_sections(
        self, client, wiki_dir
    ):
        """User adds a new ## heading inside a section then hits Done.
        The sections that follow must still be present."""
        original = "preamble\n\n## Old\n\nold content\n\n## After\n\nafter content\n"
        page = write_page(wiki_dir, "addheading", original)

        # Auto-save: user typed a new heading inside section 1
        client.post(
            "/api/save/addheading?section=1&section_count=3",
            data={"content": "## Old-Part1\n\npart1\n\n## Old-Part2\n\npart2\n"},
            headers=HOST,
        )
        after_autosave = page.read_text(encoding="utf-8")
        assert "after content" in after_autosave, "Section 'After' lost during auto-save!"

        # Done: user merges back to single heading
        client.post(
            "/edit/addheading?section=1&section_count=3",
            data={"content": "## Old-Merged\n\nmerged content\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        assert "merged content" in saved
        assert "after content" in saved, "Section 'After' was lost after Done!"

    def test_multiple_autosaves_then_done_all_sections_present(self, client, wiki_dir):
        """Realistic multi-save session: several auto-saves, then Done."""
        original = "preamble\n\n## Sec1\n\ns1\n\n## Sec2\n\ns2\n\n## Sec3\n\ns3\n"
        page = write_page(wiki_dir, "multisave", original)

        # First auto-save: edit sec1, content unchanged
        client.post(
            "/api/save/multisave?section=1&section_count=4",
            data={"content": "## Sec1\n\ns1 draft\n"},
            headers=HOST,
        )
        # Second auto-save: added a heading
        client.post(
            "/api/save/multisave?section=1&section_count=4",
            data={"content": "## Sec1a\n\ns1a\n\n## Sec1b\n\ns1b\n"},
            headers=HOST,
        )
        # Third auto-save: removed the extra heading again
        client.post(
            "/api/save/multisave?section=1&section_count=4",
            data={"content": "## Sec1\n\ns1 final draft\n"},
            headers=HOST,
        )
        # Done
        client.post(
            "/edit/multisave?section=1&section_count=4",
            data={"content": "## Sec1\n\ns1 final\n"},
            headers=HOST,
        )
        saved = page.read_text(encoding="utf-8")
        secs = split_sections(saved)
        assert len(secs) == 4, f"Expected 4 sections, got {len(secs)}: {secs}"
        assert "s1 final" in saved
        assert "s2" in saved, "Sec2 was lost!"
        assert "s3" in saved, "Sec3 was lost!"
