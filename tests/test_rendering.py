"""Tests for Leaf markdown rendering helpers.

Covers: render_md, process_wikilinks, image widths,
        _render_task_dates, toggle_nth_checkbox (metadata), page_title,
        to_slug, build_breadcrumb, strip_h1, _render_task_title,
        parse_frontmatter, _render_frontmatter_html.
"""

import re

import pytest

from mdtext import page_title, parse_frontmatter, strip_h1, to_slug, toggle_nth_checkbox
from render import _render_frontmatter_html, render_tags_pills_html, _render_task_dates, process_wikilinks, render_md
from views import build_breadcrumb
from tests.conftest import write_page
import mdtext
import pages
import render


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

    def test_parses_sources_inline_without_splitting_links(self):
        meta, _ = parse_frontmatter(
            "---\nsources: https://a.example/x, [Doc, v2](https://b.example), [[people/alex|Alex, A.]]\n---\nBody.\n")
        assert meta["sources"] == ["https://a.example/x", "[Doc, v2](https://b.example)",
                                   "[[people/alex|Alex, A.]]"]

    def test_parses_block_lists(self):
        meta, _ = parse_frontmatter(
            "---\ntags:\n  - work\n  - 'urgent'\nsources:\n- https://a.example/x?q=1:2\n"
            "- [[index]]\ntldr: Short.\n- stray item\n---\nBody.\n")
        assert meta == {"tags": ["work", "urgent"], "sources": ["https://a.example/x?q=1:2", "[[index]]"],
                        "tldr": "Short."}

    def test_single_wikilink_source_not_taken_for_a_bracketed_list(self):
        meta, _ = parse_frontmatter("---\nsources: [[index]]\n---\nBody.\n")
        assert meta["sources"] == ["[[index]]"]

    def test_dashes_not_at_start_not_treated_as_frontmatter(self):
        content = "# Title\n\n---\ntags: a\n---\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content


class TestRenderTagsPillsHtml:

    def test_empty_list_renders_nothing(self):
        assert render_tags_pills_html([]) == ''

    def test_renders_pills_with_search_links(self):
        result = render_tags_pills_html(["work", "urgent"])
        assert 'class="task-tag page-tag"' in result
        assert 'href="/search?q=%23work"' in result
        assert 'href="/search?q=%23urgent"' in result

    def test_tag_with_a_space_stays_a_word_search(self):
        assert 'href="/search?q=my+tag"' in render_tags_pills_html(["my tag"])


class TestRenderFrontmatterHtml:

    def test_empty_meta_renders_nothing(self):
        assert _render_frontmatter_html({}) == ''

    def test_tldr_rendered_as_paragraph(self):
        result = _render_frontmatter_html({"tldr": "Summary here"})
        assert '<p class="page-tldr">Summary here</p>' in result

    def test_tags_rendered_as_linked_pills(self):
        result = _render_frontmatter_html({"tags": ["work", "urgent"]})
        assert 'class="task-tag page-tag"' in result
        assert 'href="/search?q=%23work"' in result
        assert 'href="/search?q=%23urgent"' in result

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
# Image widths: ![alt|300](url)
# ===========================================================================

class TestImageWidths:

    def test_sets_width_from_alt_pipe(self, wiki_dir):
        result = render_md("![photo|300](/img.png)\n")
        assert 'width:300px' in result
        assert 'alt="photo"' in result
        assert '|300' not in result

    def test_no_pipe_in_alt_unchanged(self, wiki_dir):
        result = render_md("![just a photo](/img.png)\n")
        assert 'alt="just a photo"' in result and "style=" not in result

    def test_max_width_always_set(self, wiki_dir):
        assert 'max-width:100%' in render_md("![x|100](/img.png)\n")

    def test_multiple_images(self, wiki_dir):
        result = render_md("![a|200](/a.png) ![b|400](/b.png)\n")
        assert 'width:200px' in result
        assert 'width:400px' in result
        assert 'class="img-block"' not in result   # two images: not alone

    def test_alone_in_its_paragraph_is_a_block(self, wiki_dir):
        assert '<p class="img-block"><img src="/a.png" alt="a" style="max-width:100%;width:200px" /></p>' \
            in render_md("![a|200](/a.png)\n")


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
# render_md — integration
# ===========================================================================

class TestRenderMd:

    def test_renders_basic_markdown(self, wiki_dir):
        result = render_md("**bold** text")
        assert "<strong>bold</strong>" in result

    def test_renders_wikilinks(self, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n")
        result = render_md("See [[target]]")
        assert 'href="/page/target"' in result

    def test_toc_is_no_directive(self, wiki_dir):
        # [[toc]] was removed (the side column lists a page's headings): it is
        # an ordinary wikilink now, to a page named toc.
        result = render_md("[[toc]]\n\n## Section A\n\nbody\n")
        assert '<nav class="toc">' not in result
        assert 'href="/page/toc" class="wikilink wikilink-missing"' in result

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

    def test_sources_listed_at_the_end_as_links(self, wiki_dir):
        write_page(wiki_dir, "index", "# Index\n")
        text = ("---\nsources:\n  - https://a.example/x\n  - '[Doc](https://b.example)'\n"
                "  - '[[index]]'\n  - <b>raw</b>\n---\n\n# Title\n\nBody.\n")
        result = render_md(text, show_tags=False, show_tldr=False)
        assert result.index("<p>Body.</p>") < result.index('<section class="page-sources">')
        sources = result[result.index('<section class="page-sources">'):]
        assert '<h2 id="sources">Sources</h2>' in sources
        assert '>https://a.example/x</a>' in sources
        assert 'href="https://b.example"' in sources and '>Doc</a>' in sources
        assert 'href="/page/index" class="wikilink"' in sources
        assert "<b>raw</b>" in sources  # inline HTML, as in the body

    def test_sources_heading_id_kept_apart_from_the_pages_own(self, wiki_dir):
        result = render_md("---\nsources: https://a.example\n---\n# T\n\n## Sources\n\nMine.\n")
        assert '<h2 id="sources-1">Sources</h2>' in result

    def test_sources_in_the_side_toc(self, wiki_dir):
        from views import rail_toc_items
        toc = rail_toc_items(render_md("---\nsources: https://a.example\n---\n# T\n\n## A\n\n## B\n"))
        assert toc.endswith('<li><a href="#sources">Sources</a></li>\n</ul>')

    def test_no_sources_no_list(self, wiki_dir):
        assert "page-sources" not in render_md("---\ntags: a\n---\n# T\n\nBody.\n")

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
    """[[wikilinks]] and [[deck]] are pre-processed on the raw text.
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

    def test_directives_outside_code_still_work(self, wiki_dir):
        html = render_md("## A\n\n`code` then [[link]]\n\n```\nx\n```\n\n[[deck]]\n")
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


class TestAutolinks:
    """Bare URLs become links (GFM autolinks) for text the paste prompt never
    saw: files edited on disk, unnamed pastes, URLs inside pasted text."""

    def test_bare_url_www_and_email(self, wiki_dir):
        html = render.render_md("see https://example.com/a?b=1. and www.x.org, or me@x.org\n")
        assert '<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer">https://example.com/a?b=1</a>.' in html
        assert '<a href="http://www.x.org" target="_blank" rel="noopener noreferrer">www.x.org</a>' in html
        assert '<a href="mailto:me@x.org">me@x.org</a>' in html

    def test_code_and_existing_links_untouched(self, wiki_dir):
        html = render.render_md("`https://code.org` and [named](https://y.org)\n\n```\nhttps://fenced.org\n```\n")
        assert "<code>https://code.org</code>" in html
        assert html.count("<a ") == 1 and '<a href="https://y.org" target="_blank" rel="noopener noreferrer">named</a>' in html
        assert "https://fenced.org" in html and 'href="https://fenced.org"' not in html

    def test_wikilinks_still_work_next_to_urls(self, wiki_dir):
        (wiki_dir / "garden.md").write_text("# Garden\n", encoding="utf-8")
        html = render.render_md("[[Garden]] https://example.com\n")
        assert 'href="/page/garden"' in html
        assert '<a href="https://example.com" target="_blank" rel="noopener noreferrer">' in html


class TestCallouts:
    @pytest.mark.parametrize("kind", ["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"])
    def test_each_kind(self, wiki_dir, kind):
        html = render.render_md(f"> [!{kind}]\n> Mind **this**.\n")
        assert f'<div class="markdown-alert markdown-alert-{kind.lower()}">' in html
        assert f'<p class="markdown-alert-title">{kind.title()}</p>' in html
        assert "<strong>this</strong>" in html

    def test_plain_quote_stays_a_quote(self, wiki_dir):
        html = render.render_md("> just a quote\n")
        assert "<blockquote>" in html and "markdown-alert" not in html

    def test_wikilink_inside_callout(self, wiki_dir):
        (wiki_dir / "garden.md").write_text("# Garden\n", encoding="utf-8")
        html = render.render_md("> [!TIP]\n> See [[Garden]].\n")
        assert "markdown-alert-tip" in html and 'href="/page/garden"' in html


class TestImageParagraphs:
    """An image alone in its paragraph is marked, so wide screens may let it
    grow beyond the text; one inside a sentence is not."""

    def test_alone_is_marked(self):
        from render import render_md
        html_out = render_md("![Map](/_assets/map.png)\n\nSee ![icon](/_assets/i.png) here.\n")
        assert '<p class="img-block"><img src="/_assets/map.png" alt="Map" /></p>' in html_out
        assert "<p>See <img" in html_out

    def test_sized_image_alone_is_marked_too(self):
        from render import render_md
        assert '<p class="img-block"><img ' in render_md("![a|300](/a.png)\n")


class TestWikilinkSections:
    """[[Page#Section]]: a link to a heading on that page; [[#Section]] to
    one on this page."""

    def test_split(self):
        from mdtext import split_wikilink
        assert split_wikilink("Garden") == ("Garden", "", "Garden")
        assert split_wikilink("Garden|the garden") == ("Garden", "", "the garden")
        assert split_wikilink("Garden#Beds") == ("Garden", "Beds", "Garden › Beds")
        assert split_wikilink(" Garden # Raised beds | beds") == ("Garden", "Raised beds", "beds")
        assert split_wikilink("#Beds") == ("", "Beds", "Beds")

    def test_rendered_links(self, wiki_dir):
        (wiki_dir / "garden.md").write_text("# Garden\n")
        out = render.render_md("[[Garden#Raised beds!]] [[Garden#Beds|beds]] [[#Notes]] [[Nowhere#Beds]]\n")
        assert '<a href="/page/garden#raised-beds" class="wikilink">Garden › Raised beds!</a>' in out
        assert '<a href="/page/garden#beds" class="wikilink">beds</a>' in out
        assert '<a href="#notes" class="wikilink">Notes</a>' in out
        assert '<a href="/page/nowhere#beds" class="wikilink wikilink-missing">' in out

    def test_section_link_is_a_backlink_and_survives_a_rename(self):
        assert pages.page_link_targets("[[Garden#Beds]] [[#Here]]", "x") == {"garden"}
        assert pages.rewrite_page_links("[[garden#Beds|b]] [[Garden#Paths]] [[#garden]]", "garden", "yard") == \
            "[[yard#Beds|b]] [[yard#Paths]] [[#garden]]"
        assert mdtext.plain_task_title("Weed [[Garden#Beds]] and [[Yard|the yard]]") == "Weed Garden › Beds and the yard"


class TestExternalLinks:
    """Links to other sites open in a new tab; Leaf's own links don't."""

    def test_marked_in_rendered_pages(self):
        from render import render_md
        out = render_md("[a](https://example.com) https://x.org www.y.org [p](/page/x) [[Home]] [m](mailto:a@b.c)\n")
        ext = ' target="_blank" rel="noopener noreferrer"'
        assert f'<a href="https://example.com"{ext}>a</a>' in out
        assert f'<a href="https://x.org"{ext}>' in out and f'<a href="http://www.y.org"{ext}>' in out
        assert '<a href="/page/x">p</a>' in out and '<a href="mailto:a@b.c">m</a>' in out
        assert out.count("_blank") == 3

    def test_full_url_to_leaf_itself_stays(self):
        import server
        with server.app.test_request_context("/page/x", headers={"Host": "localhost:5000"}):
            out = render.render_md("[me](http://localhost:5000/page/y) [other](http://other:5000/)\n")
        assert '<a href="http://localhost:5000/page/y">me</a>' in out
        assert '<a href="http://other:5000/" target="_blank"' in out


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
