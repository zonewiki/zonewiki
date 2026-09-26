"""Tests for Leaf markdown rendering helpers.

Covers: render_md, process_wikilinks, _build_toc, _apply_img_sizes,
        _render_task_dates, toggle_nth_checkbox (metadata), page_title,
        to_slug, build_breadcrumb, strip_h1, _render_task_title,
        parse_frontmatter, _render_frontmatter_html.

Run with:
    pytest tests/test_rendering.py -v
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_rendering_test_")
os.environ.setdefault("WIKI_DATA", _SESSION_TMP)
os.environ["ALLOW_REMOTE"] = "1"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import server
from server import (
    _apply_img_sizes,
    _build_toc,
    _render_frontmatter_html,
    _render_tags_pills_html,
    _render_task_dates,
    build_breadcrumb,
    page_title,
    parse_frontmatter,
    process_wikilinks,
    render_md,
    strip_h1,
    to_slug,
    toggle_nth_checkbox,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def wiki_dir(tmp_path):
    old_pages = server.PAGES_DIR
    old_wiki = server.WIKI_DIR
    server.PAGES_DIR = tmp_path
    server.WIKI_DIR = tmp_path
    yield tmp_path
    server.PAGES_DIR = old_pages
    server.WIKI_DIR = old_wiki


def write_page(wiki_dir: Path, slug: str, content: str) -> Path:
    path = wiki_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ===========================================================================
# to_slug
# ===========================================================================

class TestToSlug:

    def test_simple_lowercase(self):
        assert to_slug("hello world") == "hello-world"

    def test_uppercase(self):
        assert to_slug("My Page") == "my-page"

    def test_special_chars_become_hyphens(self):
        assert to_slug("foo & bar") == "foo-bar"

    def test_consecutive_special_chars_single_hyphen(self):
        assert to_slug("a  --  b") == "a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert to_slug("  my-page  ") == "my-page"

    def test_slash_preserved_as_separator(self):
        assert to_slug("journal/my entry") == "journal/my-entry"

    def test_empty_string(self):
        assert to_slug("") == ""

    def test_unicode_replaced(self):
        result = to_slug("café")
        assert result  # should produce something non-empty
        assert "/" not in result


# ===========================================================================
# page_title
# ===========================================================================

class TestPageTitle:

    def test_extracts_h1(self):
        assert page_title("# My Title\n\nBody.", "slug") == "My Title"

    def test_extracts_h1_with_leading_space_in_content(self):
        assert page_title("\n# Title Here\n\nbody", "slug") == "Title Here"

    def test_falls_back_to_slug_display_name(self):
        assert page_title("No heading here.", "my-page") == "My Page"

    def test_strips_trailing_spaces_from_title(self):
        assert page_title("# Title  \n\nbody", "slug") == "Title"

    def test_h2_not_used_as_title(self):
        result = page_title("## Section\n\nbody", "my-slug")
        assert result == "My Slug"

    def test_empty_content_uses_slug(self):
        assert page_title("", "some-page") == "Some Page"


# ===========================================================================
# strip_h1
# ===========================================================================

class TestStripH1:

    def test_removes_first_h1(self):
        result = strip_h1("# Title\n\nBody text.\n")
        assert "# Title" not in result
        assert "Body text." in result

    def test_does_not_remove_h2(self):
        content = "## Section\n\nBody.\n"
        assert strip_h1(content) == content

    def test_strips_only_first_h1(self):
        content = "# First\n\n# Second\n\nBody.\n"
        result = strip_h1(content)
        assert "# First" not in result
        assert "# Second" in result

    def test_empty_content(self):
        assert strip_h1("") == ""

    def test_no_h1(self):
        content = "Just text.\n"
        assert strip_h1(content) == content

    def test_removes_h1_after_frontmatter(self):
        content = "---\ntags: a, b\n---\n\n# Title\n\nBody text.\n"
        result = strip_h1(content)
        assert "# Title" not in result
        assert "Body text." in result
        assert "tags: a, b" in result  # frontmatter itself is untouched

    def test_frontmatter_without_h1_unchanged(self):
        content = "---\ntags: a\n---\n\nBody only.\n"
        assert strip_h1(content) == content


# ===========================================================================
# parse_frontmatter / _render_frontmatter_html
# ===========================================================================

class TestParseFrontmatter:

    def test_no_frontmatter_returns_empty_meta(self):
        meta, body = parse_frontmatter("# Title\n\nBody.\n")
        assert meta == {}
        assert body == "# Title\n\nBody.\n"

    def test_parses_tags_and_tldr(self):
        content = "---\ntags: work, urgent\ntldr: One line summary.\n---\n\n# Title\n\nBody.\n"
        meta, body = parse_frontmatter(content)
        assert meta == {"tags": ["work", "urgent"], "tldr": "One line summary."}
        assert body == "# Title\n\nBody.\n"

    def test_parses_bracketed_tag_list(self):
        meta, _ = parse_frontmatter("---\ntags: [work, urgent]\n---\nBody.\n")
        assert meta["tags"] == ["work", "urgent"]

    def test_quoted_tldr_unquoted(self):
        meta, _ = parse_frontmatter('---\ntldr: "Quoted summary"\n---\nBody.\n')
        assert meta["tldr"] == "Quoted summary"

    def test_unclosed_fence_returns_original_unchanged(self):
        content = "---\ntags: a\n\n# Title\n\nBody.\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_unknown_keys_ignored(self):
        meta, _ = parse_frontmatter("---\ncreated: 2026-01-01\ntags: a\n---\nBody.\n")
        assert meta == {"tags": ["a"]}

    def test_empty_tags_and_tldr_omitted(self):
        meta, _ = parse_frontmatter("---\ntags:\ntldr:\n---\nBody.\n")
        assert meta == {}

    def test_dashes_not_at_start_not_treated_as_frontmatter(self):
        content = "# Title\n\n---\ntags: a\n---\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content


class TestRenderTagsPillsHtml:

    def test_empty_list_renders_nothing(self):
        assert _render_tags_pills_html([]) == ''

    def test_renders_pills_with_search_links(self):
        result = _render_tags_pills_html(["work", "urgent"])
        assert 'class="task-tag page-tag"' in result
        assert 'href="/search?q=work"' in result
        assert 'href="/search?q=urgent"' in result


class TestRenderFrontmatterHtml:

    def test_empty_meta_renders_nothing(self):
        assert _render_frontmatter_html({}) == ''

    def test_tldr_rendered_as_paragraph(self):
        result = _render_frontmatter_html({"tldr": "Summary here"})
        assert '<p class="page-tldr">Summary here</p>' in result

    def test_tags_rendered_as_linked_pills(self):
        result = _render_frontmatter_html({"tags": ["work", "urgent"]})
        assert 'class="task-tag page-tag"' in result
        assert 'href="/search?q=work"' in result
        assert 'href="/search?q=urgent"' in result

    def test_tldr_html_escaped(self):
        result = _render_frontmatter_html({"tldr": "<script>alert(1)</script>"})
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_show_tags_false_omits_tags_keeps_tldr(self):
        result = _render_frontmatter_html(
            {"tldr": "Summary here", "tags": ["work"]}, show_tags=False
        )
        assert '<p class="page-tldr">Summary here</p>' in result
        assert "task-tag" not in result

    def test_show_tags_false_with_only_tags_renders_nothing(self):
        assert _render_frontmatter_html({"tags": ["work"]}, show_tags=False) == ''

    def test_collapsed_by_default(self):
        result = _render_frontmatter_html({"tldr": "Summary here"})
        assert result.startswith('<details class="page-frontmatter">')
        assert '<summary>' in result
        assert ' open' not in result.split('<summary>')[0]  # no `open` attribute

    def test_show_tldr_false_omits_tldr_keeps_tags(self):
        result = _render_frontmatter_html(
            {"tldr": "Summary here", "tags": ["work"]}, show_tldr=False
        )
        assert "page-tldr" not in result
        assert 'class="task-tag page-tag"' in result

    def test_show_tldr_false_with_only_tldr_renders_nothing(self):
        assert _render_frontmatter_html({"tldr": "Summary"}, show_tldr=False) == ''


# ===========================================================================
# process_wikilinks
# ===========================================================================

class TestProcessWikilinks:

    def test_existing_page_link(self, wiki_dir):
        write_page(wiki_dir, "target-page", "# Target Page\n")
        result = process_wikilinks("See [[target-page]]")
        assert 'href="/page/target-page"' in result
        assert 'wikilink-missing' not in result

    def test_missing_page_has_missing_class(self, wiki_dir):
        result = process_wikilinks("See [[nonexistent-page]]")
        assert 'wikilink-missing' in result
        assert 'href="/page/nonexistent-page"' in result

    def test_pipe_alias(self, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n")
        result = process_wikilinks("See [[target|Custom Label]]")
        assert "Custom Label" in result
        assert 'href="/page/target"' in result

    def test_slugifies_page_name(self, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        result = process_wikilinks("See [[My Page]]")
        assert 'href="/page/my-page"' in result

    def test_multiple_wikilinks(self, wiki_dir):
        write_page(wiki_dir, "page-a", "# A\n")
        write_page(wiki_dir, "page-b", "# B\n")
        result = process_wikilinks("[[page-a]] and [[page-b]]")
        assert result.count("href=") == 2

    def test_html_special_chars_in_label_escaped(self, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n")
        result = process_wikilinks("[[target|A & B]]")
        assert "&amp;" in result
        assert "&" not in result.split('class=')[0]


# ===========================================================================
# _apply_img_sizes
# ===========================================================================

class TestApplyImgSizes:

    def test_sets_width_from_alt_pipe(self):
        html = '<img src="/img.png" alt="photo|300">'
        result = _apply_img_sizes(html)
        assert 'width:300px' in result
        assert 'alt="photo"' in result
        assert '|300' not in result

    def test_no_pipe_in_alt_unchanged(self):
        html = '<img src="/img.png" alt="just a photo">'
        result = _apply_img_sizes(html)
        assert result == html

    def test_max_width_always_set(self):
        html = '<img src="/img.png" alt="x|100">'
        assert 'max-width:100%' in _apply_img_sizes(html)

    def test_multiple_images(self):
        html = '<img src="/a.png" alt="a|200"> <img src="/b.png" alt="b|400">'
        result = _apply_img_sizes(html)
        assert 'width:200px' in result
        assert 'width:400px' in result


# ===========================================================================
# _render_task_dates
# ===========================================================================

class TestRenderTaskDates:

    def test_renders_created_date(self):
        html = "task <!--t:2026-01-15T10:00:00-->"
        result = _render_task_dates(html)
        assert "2026-01-15" in result
        assert "task-date-created" in result
        assert "<!--t:" not in result

    def test_renders_created_and_done_dates(self):
        html = "task <!--t:2026-01-15T10:00:00 d:2026-01-16T11:00:00-->"
        result = _render_task_dates(html)
        assert "task-date-created" in result
        assert "task-date-done" in result
        assert "2026-01-16" in result

    def test_renders_done_only_comment(self):
        html = "task <!--d:2026-02-20T09:30:00-->"
        result = _render_task_dates(html)
        assert "task-date-done" in result
        assert "2026-02-20" in result
        assert "<!--d:" not in result

    def test_no_metadata_unchanged(self):
        html = "<li>plain task</li>"
        assert _render_task_dates(html) == html


# ===========================================================================
# _build_toc
# ===========================================================================

class TestBuildToc:

    def test_builds_toc_from_h2(self):
        text = "# Title\n\n## Section One\n\nbody\n\n## Section Two\n\nbody2\n"
        toc = _build_toc(text)
        assert "Section One" in toc
        assert "Section Two" in toc
        assert '<nav class="toc">' in toc

    def test_empty_when_no_headings(self):
        assert _build_toc("Just text.\n") == ""

    def test_h1_not_included(self):
        toc = _build_toc("# Title\n\nBody.\n")
        assert toc == ""

    def test_h3_included_with_class(self):
        text = "## Parent\n\n### Child\n\nbody\n"
        toc = _build_toc(text)
        assert "toc-h3" in toc
        assert "Child" in toc

    def test_h4_not_included(self):
        text = "## Section\n\n#### Deep\n\nbody\n"
        toc = _build_toc(text)
        assert "Deep" not in toc

    def test_anchor_ids_generated(self):
        text = "## My Section\n\nbody\n"
        toc = _build_toc(text)
        assert 'href="#my-section"' in toc

    def test_wikilinks_in_heading_stripped(self):
        text = "## See [[Some Page]]\n\nbody\n"
        toc = _build_toc(text)
        assert "[[" not in toc
        assert "Some Page" in toc


# ===========================================================================
# render_md — integration
# ===========================================================================

class TestRenderMd:

    def test_renders_basic_markdown(self, wiki_dir):
        result = render_md("**bold** text")
        assert "<strong>bold</strong>" in result

    def test_renders_wikilinks(self, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n")
        result = render_md("See [[target]]", slug="source")
        assert 'href="/page/target"' in result

    def test_toc_placeholder_replaced(self, wiki_dir):
        text = "[[toc]]\n\n## Section A\n\nbody\n"
        result = render_md(text)
        assert "[[toc]]" not in result
        assert '<nav class="toc">' in result

    def test_toc_followed_by_hr_not_setext_heading(self, wiki_dir):
        # Regression: [[toc]] followed by a blank line then --- was consuming
        # the newline, leaving the placeholder adjacent to --- and causing
        # markdown-it to render it as a setext h2 instead of <p>.
        text = "[[toc]]\n\n---\n\n## Section A\n\nbody\n"
        result = render_md(text)
        assert '<nav class="toc">' in result
        assert "LEAFTOCPLACEHOLDER" not in result
        assert "LEAFTOCPLACEHOLDER".lower() not in result

    def test_toc_uppercase_variant(self, wiki_dir):
        text = "[[TOC]]\n\n---\n\n## Section A\n\nbody\n"
        result = render_md(text)
        assert '<nav class="toc">' in result
        assert "LEAFTOCPLACEHOLDER" not in result

    def test_image_width_applied(self, wiki_dir):
        text = "![photo|150](/img.png)"
        result = render_md(text)
        assert "width:150px" in result

    def test_task_dates_rendered(self, wiki_dir):
        text = "- [x] done task <!--d:2026-01-10T12:00:00-->"
        result = render_md(text)
        assert "task-date-done" in result

    def test_code_block_not_modified(self, wiki_dir):
        text = "```python\nprint('hello')\n```"
        result = render_md(text)
        assert "<code" in result
        assert "print" in result

    def test_html_enabled(self, wiki_dir):
        result = render_md("<div>raw html</div>")
        assert "<div>" in result

    def test_checkboxes_rendered(self, wiki_dir):
        text = "- [ ] pending\n- [x] done\n"
        result = render_md(text)
        assert 'type="checkbox"' in result

    def test_frontmatter_stripped_from_body_and_rendered_as_meta(self, wiki_dir):
        text = "---\ntags: work\ntldr: Quick summary.\n---\n\n# Title\n\nBody.\n"
        result = render_md(text)
        assert "tags: work" not in result
        assert "tldr:" not in result
        assert '<p class="page-tldr">Quick summary.</p>' in result
        assert 'class="task-tag page-tag"' in result
        assert "<p>Body.</p>" in result

    def test_no_frontmatter_no_meta_block(self, wiki_dir):
        result = render_md("# Title\n\nBody.\n")
        assert "page-frontmatter" not in result

    def test_show_tags_false_keeps_tldr_omits_tags(self, wiki_dir):
        text = "---\ntags: work\ntldr: Quick summary.\n---\n\n# Title\n\nBody.\n"
        result = render_md(text, show_tags=False)
        assert '<p class="page-tldr">Quick summary.</p>' in result
        assert "task-tag" not in result

    def test_show_tags_and_show_tldr_false_suppresses_both(self, wiki_dir):
        text = "---\ntags: work\ntldr: Quick summary.\n---\n\n# Title\n\nBody.\n"
        result = render_md(text, show_tags=False, show_tldr=False)
        assert "page-frontmatter" not in result
        assert "page-tldr" not in result
        assert "task-tag" not in result


# ===========================================================================
# build_breadcrumb
# ===========================================================================

class TestBuildBreadcrumb:

    def test_contains_home_link(self):
        bc = build_breadcrumb("my-page", "My Page")
        assert 'href="/"' in bc or 'class="breadcrumb-home"' in bc

    def test_contains_page_title(self):
        bc = build_breadcrumb("my-page", "My Page")
        assert "My Page" in bc

    def test_nested_slug_shows_ancestor(self):
        bc = build_breadcrumb("journal/my-entry", "My Entry")
        assert "journal" in bc.lower()

    def test_extra_crumbs_rendered(self):
        bc = build_breadcrumb("my-page", "My Page", ("Edit", None))
        assert "Edit" in bc

    def test_extra_crumb_with_url(self):
        bc = build_breadcrumb("my-page", "My Page", ("History", "/history/my-page"))
        assert 'href="/history/my-page"' in bc

    def test_title_escaped(self):
        bc = build_breadcrumb("my-page", "<script>alert(1)</script>")
        assert "<script>" not in bc
        assert "&lt;script&gt;" in bc

    def test_data_attributes_present(self):
        bc = build_breadcrumb("my-page", "My Page")
        assert 'data-slug="my-page"' in bc
        assert 'data-title="My Page"' in bc


# ===========================================================================
# toggle_nth_checkbox with task metadata
# ===========================================================================

class TestToggleCheckboxMetadata:

    def test_checking_adds_done_date(self):
        content = "- [ ] task\n"
        result = toggle_nth_checkbox(content, 0)
        assert "<!--d:" in result

    def test_unchecking_removes_done_date(self):
        content = "- [x] task <!--d:2026-01-10T09:00:00-->\n"
        result = toggle_nth_checkbox(content, 0)
        assert "<!--d:" not in result
        assert "[ ]" in result

    def test_checking_with_created_date_adds_done_date(self):
        content = "- [ ] task <!--t:2026-01-01T08:00:00-->\n"
        result = toggle_nth_checkbox(content, 0)
        assert "<!--t:2026-01-01T08:00:00 d:" in result

    def test_unchecking_preserves_created_date(self):
        content = "- [x] task <!--t:2026-01-01T08:00:00 d:2026-01-10T09:00:00-->\n"
        result = toggle_nth_checkbox(content, 0)
        assert "<!--t:2026-01-01T08:00:00-->" in result
        assert " d:" not in result

    def test_checking_uppercase_x_also_unchecks(self):
        content = "- [X] task\n"
        result = toggle_nth_checkbox(content, 0)
        assert "[ ]" in result

    def test_out_of_range_index_returns_unchanged(self):
        content = "- [ ] only one\n"
        result = toggle_nth_checkbox(content, 5)
        assert result == content


# ===========================================================================
# _render_task_title (accessed via view_page logic)
# ===========================================================================

class TestRenderTaskTitleViaViewPage:
    """Test the _render_task_title helper indirectly by checking render_md
    output doesn't double-escape characters in wikilink labels/slugs."""

    def test_ampersand_in_plain_title_escaped_once(self, wiki_dir):
        # Plain task title with & — should become &amp; exactly once
        text = "plain task with & ampersand"
        result = render_md(text)
        assert "&amp;" in result
        assert "&amp;amp;" not in result

    def test_wikilink_with_special_char_slug(self, wiki_dir):
        # Simulate what process_wikilinks does with a page named "A & B"
        write_page(wiki_dir, "a-b", "# A & B\n")
        result = process_wikilinks("[[A & B]]")
        # Slug should be "a-b" (not "a-amp-b")
        assert 'href="/page/a-b"' in result


# ===========================================================================
# Regressions from code review
# ===========================================================================

class TestDisabledAttributeStripping:
    """render_md drops `disabled` from task-list checkboxes so they are
    clickable. It used to strip every ` disabled` in the whole HTML, eating the
    word from ordinary prose."""

    def test_word_disabled_in_prose_survives(self, wiki_dir):
        html = render_md("The account is disabled today.\n")
        assert "The account is disabled today." in html

    def test_disabled_attribute_in_raw_html_elsewhere_survives(self, wiki_dir):
        html = render_md('<button disabled>x</button>\n')
        assert "<button disabled>" in html

    def test_task_checkbox_is_still_enabled(self, wiki_dir):
        html = render_md("- [ ] task\n")
        checkbox = re.search(r'<input[^>]*task-list-item-checkbox[^>]*>', html).group(0)
        assert "disabled" not in checkbox


class TestDirectivesInsideCode:
    """[[wikilinks]], [[deck]] and [[toc]] are pre-processed on the raw text.
    Inside code they must stay literal — they used to be rewritten into HTML
    that then showed up escaped inside the code block (or embedded a whole deck
    app inside a <pre>)."""

    def test_wikilink_in_fenced_code_stays_literal(self, wiki_dir):
        html = render_md("```\nsee [[some page]]\n```\n")
        assert "see [[some page]]" in html
        assert "wikilink" not in html

    def test_wikilink_in_tilde_fence_stays_literal(self, wiki_dir):
        html = render_md("~~~md\n[[a|b]]\n~~~\n")
        assert "[[a|b]]" in html

    def test_wikilink_in_inline_code_stays_literal(self, wiki_dir):
        html = render_md("Write `[[page]]` to link, e.g. [[page]].\n")
        assert "<code>[[page]]</code>" in html
        assert html.count('class="wikilink') == 1

    def test_double_backtick_inline_code(self, wiki_dir):
        html = render_md("``a ` [[x]]`` and [[y]]\n")
        assert "[[x]]" in html
        assert 'href="/page/y"' in html

    def test_deck_directive_in_code_not_embedded(self, wiki_dir):
        html = render_md("```\n[[deck tag=work]]\n```\n")
        assert "tasks-app" not in html
        assert "[[deck tag=work]]" in html

    def test_toc_directive_in_code_not_replaced(self, wiki_dir):
        html = render_md("## A\n\n```\n[[toc]]\n```\n")
        assert 'class="toc"' not in html
        assert "[[toc]]" in html

    def test_directives_outside_code_still_work(self, wiki_dir):
        html = render_md("[[toc]]\n\n## A\n\n`code` then [[link]]\n\n```\nx\n```\n\n[[deck]]\n")
        assert 'class="toc"' in html
        assert 'href="/page/link"' in html
        assert "tasks-app" in html


class TestPageTitleIgnoresCode:
    """Regression: page_title took the first "# " line anywhere, so a page
    without an H1 whose code block had a shell comment was titled after it."""

    def test_comment_in_code_block_is_not_a_title(self):
        assert page_title("Intro\n\n```bash\n# install deps\n```\n", "my-page") == "My Page"

    def test_h1_after_code_block_is_found(self):
        assert page_title("```\n# nope\n```\n\n# Real Title\n", "x") == "Real Title"

    def test_h1_after_frontmatter(self):
        assert page_title("---\ntags: a\n---\n# Title\n", "x") == "Title"

    def test_hash_without_space_is_not_heading(self):
        assert page_title("#hashtag\n", "tagged-page") == "Tagged Page"

    def test_heading_does_not_span_lines(self):
        assert page_title("#\nnext line\n", "empty-h") == "Empty H"
