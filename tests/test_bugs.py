"""Tests for confirmed bugs outside of section editing."""

import pytest
import re

from mdtext import split_sections, toggle_nth_checkbox
from pages import find_backlinks, page_link_targets
from tests.conftest import HOST, section_fields, write_page
import render


# ===========================================================================
# Bug: toggle_nth_checkbox counts [x] / [ ] inside fenced code blocks
# ===========================================================================

class TestToggleCheckboxInCodeBlocks:

    def _cb(self, content, idx):
        return toggle_nth_checkbox(content, idx)

    def test_toggle_works_normally_without_code_blocks(self):
        content = "- [ ] task1\n- [ ] task2\n- [x] task3\n"
        assert "- [x] task1" in self._cb(content, 0)
        assert "- [x] task2" in self._cb(content, 1)
        assert "- [ ] task3" in self._cb(content, 2)

    def test_checkbox_inside_backtick_fence_is_skipped(self):
        # The [x] inside the fence must NOT be counted as a real checkbox.
        content = (
            "- [ ] real task 1\n"
            "```\n"
            "[x] this is code, not a checkbox\n"
            "```\n"
            "- [ ] real task 2\n"
        )
        # idx=1 must toggle real task 2, not the code-block [x]
        result = self._cb(content, 1)
        assert "- [x] real task 2" in result, "task2 was not toggled"
        assert "[x] this is code" in result, "code block was wrongly toggled"

    def test_checkbox_inside_tilde_fence_is_skipped(self):
        content = (
            "- [ ] real task\n"
            "~~~python\n"
            "status = '[x] done'\n"
            "~~~\n"
            "- [ ] another task\n"
        )
        # idx=0 toggles real task, idx=1 toggles another task (not the string in code)
        r0 = self._cb(content, 0)
        assert "- [x] real task" in r0
        r1 = self._cb(content, 1)
        assert "- [x] another task" in r1
        assert "'[x] done'" in r1, "code inside tilde fence should be unchanged"

    def test_unchecked_in_code_block_not_counted(self):
        content = (
            "- [ ] task A\n"
            "```\n"
            "- [ ] fake checkbox\n"
            "```\n"
        )
        # Only 1 real checkbox; idx=0 toggles task A
        result = self._cb(content, 0)
        assert "- [x] task A" in result
        # idx=1 is out of range — content unchanged
        assert self._cb(content, 1) == content

    def test_multiple_code_blocks_only_real_checkboxes_counted(self):
        content = (
            "- [ ] first\n"
            "```\n"
            "[x] fake1\n"
            "[ ] fake2\n"
            "```\n"
            "- [x] second\n"
            "```\n"
            "[ ] fake3\n"
            "```\n"
            "- [ ] third\n"
        )
        # Real checkboxes: first(0), second(1), third(2)
        assert "- [x] first"  in self._cb(content, 0)
        assert "- [ ] second" in self._cb(content, 1)
        assert "- [x] third"  in self._cb(content, 2)
        # idx=3 is out of range
        assert self._cb(content, 3) == content

    def test_toggle_preserves_rest_of_content(self):
        content = "intro\n\n- [ ] task\n\n```\n[x] code\n```\n\noutro\n"
        result = self._cb(content, 0)
        assert "intro\n" in result
        assert "outro\n" in result
        assert "```\n[x] code\n```\n" in result   # code block unchanged


# ===========================================================================
# Bug: split_sections misses setext-style h2 headings
# (lines followed by a line of dashes: "Title\n------")
# ===========================================================================

class TestSplitSectionsSetextHeadings:

    def test_setext_h2_is_split_point(self):
        content = "Preamble.\n\nSection One\n-----------\n\nContent 1.\n"
        parts = split_sections(content)
        assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}: {parts}"
        assert parts[0].strip() == "Preamble."
        assert parts[1].startswith("Section One")

    def test_two_setext_h2_headings(self):
        content = (
            "Preamble.\n\n"
            "Section One\n-----------\n\nContent 1.\n\n"
            "Section Two\n-----------\n\nContent 2.\n"
        )
        parts = split_sections(content)
        assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}"
        assert parts[1].startswith("Section One")
        assert parts[2].startswith("Section Two")

    def test_setext_join_reproduces_original(self):
        content = (
            "Preamble.\n\n"
            "Heading A\n---------\n\nBody A.\n\n"
            "Heading B\n---------\n\nBody B.\n"
        )
        parts = split_sections(content)
        assert "".join(parts) == content

    def test_horizontal_rule_without_preceding_text_is_not_a_heading(self):
        """--- after an empty line is a thematic break, not a setext heading."""
        content = "## Real\n\nbody\n\n---\n\nmore body\n"
        parts = split_sections(content)
        assert len(parts) == 2   # only the real ## heading
        assert "more body" in parts[1]

    def test_setext_heading_inside_code_fence_is_ignored(self):
        content = (
            "Preamble.\n\n"
            "```\n"
            "Not a heading\n"
            "-------------\n"
            "```\n\n"
            "## Real\n\nbody\n"
        )
        parts = split_sections(content)
        assert len(parts) == 2
        assert parts[1].startswith("## Real")

    def test_setext_section_edit_route_uses_correct_index(self, client, wiki_dir):
        """Regression: clicking a setext-section's edit button must load that section,
        not silently discard the edit."""
        content = (
            "Preamble.\n\n"
            "Alpha Section\n-------------\n\nalpha body\n\n"
            "Beta Section\n------------\n\nbeta body\n"
        )
        write_page(wiki_dir, "setext_page", content)
        # The GET /edit route should serve section 1 correctly
        resp = client.get("/edit/setext_page?section=1", headers=HOST)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alpha body" in html
        assert "beta body" not in html

    def test_setext_section_save_does_not_silently_drop(self, client, wiki_dir):
        """Setext sections must save correctly with the new splice approach."""
        content = (
            "Preamble.\n\n"
            "Alpha\n-----\n\nalpha body\n\n"
            "Beta\n----\n\nbeta body\n"
        )
        page = write_page(wiki_dir, "setext_save", content)
        fields = section_fields(content, 1)
        fields["content"] = "Alpha\n-----\n\nUPDATED alpha body\n"
        resp = client.post("/edit/setext_save", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "UPDATED alpha body" in saved, "Edit was silently dropped (data loss bug)"
        assert "beta body" in saved


# ===========================================================================
# Bug: split_sections misses indented ATX headings (up to 3 leading spaces)
# ===========================================================================

class TestSplitSectionsIndentedHeadings:

    def test_one_space_before_h2_is_split_point(self):
        content = "pre\n\n ## Indented One\n\nbody\n"
        parts = split_sections(content)
        assert len(parts) == 2
        assert parts[1].startswith(" ## Indented One") or \
               parts[1].startswith("## Indented One")  # either representation ok

    def test_two_spaces_before_h2_is_split_point(self):
        content = "pre\n\n  ## Two Spaces\n\nbody\n"
        parts = split_sections(content)
        assert len(parts) == 2

    def test_three_spaces_before_h2_is_split_point(self):
        content = "pre\n\n   ## Three Spaces\n\nbody\n"
        parts = split_sections(content)
        assert len(parts) == 2

    def test_four_spaces_before_h2_is_code_not_heading(self):
        """4+ spaces = indented code block in CommonMark; must NOT be a split point."""
        content = "pre\n\n    ## Four Spaces — code\n\nbody\n"
        parts = split_sections(content)
        assert len(parts) == 1   # no heading split

    def test_indented_h2_join_reproduces_original(self):
        content = "pre\n\n  ## A\n\nbody a\n\n  ## B\n\nbody b\n"
        parts = split_sections(content)
        assert "".join(parts) == content

    def test_indented_section_save_does_not_drop(self, client, wiki_dir):
        """Indented ATX heading: edit must reach the correct section."""
        content = "preamble\n\n  ## Section A\n\nbody a\n\n  ## Section B\n\nbody b\n"
        page = write_page(wiki_dir, "indented_page", content)
        fields = section_fields(content, 1)
        fields["content"] = "  ## Section A\n\nUPDATED body a\n"
        resp = client.post("/edit/indented_page", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "UPDATED body a" in saved, "Edit was silently dropped"
        assert "body b" in saved


# ===========================================================================
# Bug: rename_page crashes when new slug is in a new subdirectory
# ===========================================================================

class TestRenamePageMkdir:

    def test_rename_to_existing_flat_slug_works(self, client, wiki_dir):
        write_page(wiki_dir, "flat-old", "# Old\n\ncontent\n")
        resp = client.post(
            "/rename/flat-old",
            data={"new_name": "flat-new"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        assert (wiki_dir / "flat-new.md").exists()
        assert not (wiki_dir / "flat-old.md").exists()

    def test_rename_to_new_subdirectory_does_not_crash(self, client, wiki_dir):
        """Renaming my-page to journal/my-page must create the journal/ dir."""
        write_page(wiki_dir, "my-page", "# My Page\n\ncontent\n")
        resp = client.post(
            "/rename/my-page",
            data={"new_name": "journal/my-page"},
            headers=HOST,
        )
        # Must redirect, not 500
        assert resp.status_code in (301, 302, 303)
        new_file = wiki_dir / "journal" / "my-page.md"
        assert new_file.exists(), "File was not moved to the new subdirectory"
        assert not (wiki_dir / "my-page.md").exists()

    def test_rename_to_deeply_nested_subdirectory(self, client, wiki_dir):
        write_page(wiki_dir, "src", "# Src\n\ncontent\n")
        resp = client.post(
            "/rename/src",
            data={"new_name": "a/b/c/src"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        assert (wiki_dir / "a" / "b" / "c" / "src.md").exists()

    def test_rename_within_same_subdirectory(self, client, wiki_dir):
        write_page(wiki_dir, "notes/old-note", "# Old\n\ncontent\n")
        resp = client.post(
            "/rename/notes/old-note",
            data={"new_name": "notes/new-note"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        assert (wiki_dir / "notes" / "new-note.md").exists()
        assert not (wiki_dir / "notes" / "old-note.md").exists()

    def test_rename_preserves_content(self, client, wiki_dir):
        write_page(wiki_dir, "preserve", "# Title\n\nUnique content 12345.\n")
        client.post(
            "/rename/preserve",
            data={"new_name": "subdir/preserve"},
            headers=HOST,
        )
        new_file = wiki_dir / "subdir" / "preserve.md"
        assert new_file.exists()
        assert "Unique content 12345." in new_file.read_text(encoding="utf-8")


# ===========================================================================
# Bug: backlinks not detected when link is a bare slug (no .md extension)
# e.g. [Ivo](ivo-petrenko) — not a wikilink, not a .md link
# ===========================================================================

class TestBareSlugBacklinks:

    def test_bare_slug_link_detected_as_target(self, wiki_dir):
        """[Label](slug) with no extension must appear in page_link_targets."""
        targets = page_link_targets(
            "- [Ivo](ivo-petrenko)\n",
            "state",
            known_slugs={"ivo-petrenko"},
        )
        assert "ivo-petrenko" in targets

    def test_bare_slug_backlink_shows_in_find_backlinks(self, wiki_dir):
        """find_backlinks must return the source page when it contains [Label](slug)."""
        write_page(wiki_dir, "ivo-petrenko", "# Ivo Petrenko\n")
        write_page(wiki_dir, "state", "# State\n\n- [Ivo](ivo-petrenko)\n")

        backlinks = find_backlinks("ivo-petrenko")

        slugs = [b["slug"] for b in backlinks]
        assert "state" in slugs, f"Expected 'state' in backlinks, got {slugs}"

    def test_bare_slug_in_list_item(self, wiki_dir):
        """Bare slug inside a list item must be detected."""
        targets = page_link_targets(
            "## Team\n\n- Kim\n  - [Ivo](ivo-petrenko)\n    - [ ] task\n",
            "state",
            known_slugs={"ivo-petrenko"},
        )
        assert "ivo-petrenko" in targets

    def test_external_url_not_counted_as_backlink(self, wiki_dir):
        """External http links must never be treated as page targets."""
        targets = page_link_targets(
            "[link](https://example.com/ivo-petrenko)\n",
            "state",
            known_slugs={"ivo-petrenko"},
        )
        assert "ivo-petrenko" not in targets

    def test_bare_slug_not_in_known_slugs_is_ignored(self, wiki_dir):
        """A bare link whose target page doesn't exist must not appear in targets."""
        targets = page_link_targets(
            "[Ghost](nonexistent-page)\n",
            "state",
            known_slugs={"ivo-petrenko"},
        )
        assert "nonexistent-page" not in targets

    def test_wikilink_still_works_alongside_bare_link(self, wiki_dir):
        """Both [[wikilink]] and bare slug links must be detected in the same page."""
        targets = page_link_targets(
            "[[Signals]]\n[Ivo](ivo-petrenko)\n",
            "state",
            known_slugs={"signals", "ivo-petrenko"},
        )
        assert "signals" in targets
        assert "ivo-petrenko" in targets


# ===========================================================================
# Bug: toggle_nth_checkbox counted any "[x]"/"[ ]" on a line, not just
# task-list items, so its index disagreed with the rendered checkboxes and a
# click toggled the wrong line.
# ===========================================================================

class TestToggleCheckboxOnlyTaskItems:

    def test_bracket_in_prose_is_not_a_checkbox(self):
        content = "Press [x] to close.\n\n- [ ] task\n"
        out = toggle_nth_checkbox(content, 0)
        assert out.startswith("Press [x] to close.\n")
        assert "- [x] task" in out

    def test_second_bracket_on_a_task_line_is_not_a_checkbox(self):
        content = "- [ ] a [x] b\n- [ ] c\n"
        out = toggle_nth_checkbox(content, 1)
        assert out.startswith("- [ ] a [x] b\n")
        assert "- [x] c" in out

    def test_plain_list_item_with_brackets_is_not_a_checkbox(self):
        content = "- see [ ] here\n- [ ] real\n"
        assert "- [x] real" in toggle_nth_checkbox(content, 0)

    def test_frontmatter_brackets_ignored(self):
        content = "---\ntags: [a, b]\ntldr: [x] marks\n---\n# T\n\n- [ ] one\n"
        assert "- [x] one" in toggle_nth_checkbox(content, 0)

    @pytest.mark.parametrize("line", ["* [ ] star", "+ [ ] plus", "1. [ ] num",
                                      "2) [ ] paren", "  - [ ] nested", "> - [ ] quoted"])
    def test_task_item_variants_are_counted(self, line):
        out = toggle_nth_checkbox(f"{line}\n", 0)
        assert "[x]" in out

    def test_index_matches_rendered_checkboxes(self, wiki_dir):
        """The Nth rendered checkbox must be the Nth line the toggle counts."""
        content = (
            "Intro with [x] and [ ] in prose.\n\n"
            "- [ ] first\n"
            "- plain [ ] item\n"
            "  - [x] second (nested)\n"
            "\n```\n- [ ] in code\n```\n\n"
            "1. [ ] third\n"
            "> - [ ] fourth\n"
            "- [ ] fifth [x] trailing\n"
        )
        html = render.render_md(content)
        rendered = re.findall(r'<input[^>]*task-list-item-checkbox[^>]*>', html)
        assert len(rendered) == 5
        labels = ["first", "second", "third", "fourth", "fifth"]
        for i, label in enumerate(labels):
            out = toggle_nth_checkbox(content, i)
            changed = [a for a, b in zip(content.splitlines(), out.splitlines()) if a != b]
            assert len(changed) == 1 and label in changed[0], (i, changed)
