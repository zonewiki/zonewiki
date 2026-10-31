"""Markdown rendering as pages show it: wikilinks, image widths, task dates,
frontmatter, sources, code, autolinks, callouts, external links, the
breadcrumb. The text helpers underneath (slugs, titles, frontmatter
parsing, checkbox toggling) have Go tests (go/text_test.go)."""

import re

import pytest

from tests.conftest import HOST, preview, rendered, write_page


class TestWikilinks:

    def test_existing_page_link(self, client, wiki_dir):
        write_page(wiki_dir, "target-page", "# Target Page\n")
        result = preview(client, "See [[target-page]]")
        assert 'href="/page/target-page"' in result
        assert 'wikilink-missing' not in result

    def test_missing_page_has_missing_class(self, client):
        result = preview(client, "See [[nonexistent-page]]")
        assert 'wikilink-missing' in result
        assert 'href="/page/nonexistent-page"' in result

    def test_pipe_alias(self, client, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n")
        result = preview(client, "See [[target|Custom Label]]")
        assert "Custom Label" in result
        assert 'href="/page/target"' in result

    def test_slugifies_page_name(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        assert 'href="/page/my-page"' in preview(client, "See [[My Page]]")

    def test_multiple_wikilinks(self, client, wiki_dir):
        write_page(wiki_dir, "page-a", "# A\n")
        write_page(wiki_dir, "page-b", "# B\n")
        assert preview(client, "[[page-a]] and [[page-b]]").count("href=") == 2

    def test_html_special_chars_in_label_escaped(self, client, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n")
        result = preview(client, "[[target|A & B]]")
        assert ">A &amp; B</a>" in result

    def test_special_char_in_page_name(self, client, wiki_dir):
        write_page(wiki_dir, "a-b", "# A & B\n")
        assert 'href="/page/a-b"' in preview(client, "[[A & B]]")

    def test_toc_is_no_directive(self, client):
        # [[toc]] was removed (the side column lists a page's headings): it is
        # an ordinary wikilink now, to a page named toc.
        result = preview(client, "[[toc]]\n\n## Section A\n\nbody\n")
        assert '<nav class="toc">' not in result
        assert 'href="/page/toc" class="wikilink wikilink-missing"' in result

    def test_section_links(self, client, wiki_dir):
        """[[Page#Section]]: a link to a heading on that page; [[#Section]]
        to one on this page."""
        write_page(wiki_dir, "garden", "# Garden\n")
        out = preview(client, "[[Garden#Raised beds!]] [[Garden#Beds|beds]] [[#Notes]] [[Nowhere#Beds]]\n")
        assert '<a href="/page/garden#raised-beds" class="wikilink">Garden › Raised beds!</a>' in out
        assert '<a href="/page/garden#beds" class="wikilink">beds</a>' in out
        assert '<a href="#notes" class="wikilink">Notes</a>' in out
        assert '<a href="/page/nowhere#beds" class="wikilink wikilink-missing">' in out


class TestImageWidths:
    """![alt|300](url) sizes the image."""

    def test_sets_width_from_alt_pipe(self, client):
        result = preview(client, "![photo|300](/img.png)\n")
        assert 'width:300px' in result
        assert 'alt="photo"' in result
        assert '|300' not in result

    def test_no_pipe_in_alt_unchanged(self, client):
        result = preview(client, "![just a photo](/img.png)\n")
        assert 'alt="just a photo"' in result and "style=" not in result

    def test_max_width_always_set(self, client):
        assert 'max-width:100%' in preview(client, "![x|100](/img.png)\n")

    def test_multiple_images(self, client):
        result = preview(client, "![a|200](/a.png) ![b|400](/b.png)\n")
        assert 'width:200px' in result
        assert 'width:400px' in result
        assert 'class="img-block"' not in result   # two images: not alone

    def test_alone_in_its_paragraph_is_a_block(self, client):
        """An image alone in its paragraph is marked, so wide screens may
        let it grow beyond the text; one inside a sentence is not."""
        assert '<p class="img-block"><img src="/a.png" alt="a" style="max-width:100%;width:200px" /></p>' \
            in preview(client, "![a|200](/a.png)\n")
        out = preview(client, "![Map](/_assets/map.png)\n\nSee ![icon](/_assets/i.png) here.\n")
        assert '<p class="img-block"><img src="/_assets/map.png" alt="Map" /></p>' in out
        assert "<p>See <img" in out


class TestRenderMd:

    def test_renders_basic_markdown(self, client):
        assert "<strong>bold</strong>" in preview(client, "**bold** text")

    def test_task_dates_rendered(self, client):
        result = preview(client, "- [x] done task <!--t:2026-01-15T10:00:00 d:2026-01-16T11:00:00-->")
        assert "task-date-created" in result and "task-date-done" in result
        assert "2026-01-16" in result and "<!--" not in result

    def test_code_block_not_modified(self, client):
        result = preview(client, "```python\nprint('hello')\n```")
        assert "<code" in result
        assert "print" in result

    def test_html_enabled(self, client):
        assert "<div>" in preview(client, "<div>raw html</div>")

    def test_checkboxes_rendered(self, client):
        assert 'type="checkbox"' in preview(client, "- [ ] pending\n- [x] done\n")

    def test_ampersand_escaped_once(self, client):
        result = preview(client, "plain task with & ampersand")
        assert "&amp;" in result
        assert "&amp;amp;" not in result

    def test_frontmatter_stripped_from_body_and_rendered_as_meta(self, client):
        text = "---\ntags: work\ntldr: Quick summary.\n---\n\n# Title\n\nBody.\n"
        result = preview(client, text)
        assert "tags: work" not in result
        assert "tldr:" not in result
        assert '<p class="page-tldr">Quick summary.</p>' in result
        assert 'class="task-tag page-tag"' in result
        assert "<p>Body.</p>" in result

    def test_no_frontmatter_no_meta_block(self, client):
        assert "page-frontmatter" not in preview(client, "# Title\n\nBody.\n")

    def test_page_view_shows_neither_tags_nor_tldr_in_the_text(self, client, wiki_dir):
        # The page's header has them (the meta row).
        text = "---\ntags: work\ntldr: Quick summary.\n---\n\n# Title\n\nBody.\n"
        result = rendered(client, wiki_dir, text)
        assert "page-frontmatter" not in result
        assert "page-tldr" not in result
        assert "task-tag" not in result

    def test_sources_listed_at_the_end_as_links(self, client, wiki_dir):
        text = ("---\nsources:\n  - https://a.example/x\n  - '[Doc](https://b.example)'\n"
                "  - '[[index]]'\n  - <b>raw</b>\n---\n\n# Title\n\nBody.\n")
        result = rendered(client, wiki_dir, text)
        assert result.index("<p>Body.</p>") < result.index('<section class="page-sources">')
        sources = result[result.index('<section class="page-sources">'):]
        assert '<h2 id="sources">Sources</h2>' in sources
        assert '>https://a.example/x</a>' in sources
        assert 'href="https://b.example"' in sources and '>Doc</a>' in sources
        assert 'href="/page/index" class="wikilink"' in sources
        assert "<b>raw</b>" in sources  # inline HTML, as in the body

    def test_sources_heading_id_kept_apart_from_the_pages_own(self, client):
        result = preview(client, "---\nsources: https://a.example\n---\n# T\n\n## Sources\n\nMine.\n")
        assert '<h2 id="sources-1">Sources</h2>' in result

    def test_sources_in_the_side_toc(self, client, wiki_dir):
        write_page(wiki_dir, "src", "---\nsources: https://a.example\n---\n# T\n\n## A\n\n## B\n")
        page = client.get("/page/src", headers=HOST).get_data(as_text=True)
        assert '<li><a href="#sources">Sources</a></li>\n</ul>' in page

    def test_no_sources_no_list(self, client):
        assert "page-sources" not in preview(client, "---\ntags: a\n---\n# T\n\nBody.\n")


def _breadcrumb(page: str) -> str:
    start = page.index('<div class="breadcrumb"')
    return page[start:page.index('<span class="breadcrumb-right">', start) if 'breadcrumb-right' in page else None]


class TestBreadcrumb:

    def test_home_and_title(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        page = client.get("/page/my-page", headers=HOST).get_data(as_text=True)
        bc = _breadcrumb(page)
        assert 'class="breadcrumb-home' in bc
        assert "<span>My Page</span>" in bc
        assert 'data-slug="my-page"' in bc and 'data-title="My Page"' in bc

    def test_nested_slug_shows_ancestor(self, client, wiki_dir):
        write_page(wiki_dir, "journal/my-entry", "# My Entry\n")
        page = client.get("/page/journal/my-entry", headers=HOST).get_data(as_text=True)
        assert 'href="/page/journal">Journal</a>' in page

    def test_extra_crumb_with_url(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        page = client.get("/edit/my-page", headers=HOST).get_data(as_text=True)
        bc = _breadcrumb(page)
        assert '<a href="/page/my-page">My Page</a>' in bc and "Edit" in bc

    def test_title_escaped(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# <script>alert(1)</script>\n")
        page = client.get("/page/my-page", headers=HOST).get_data(as_text=True)
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page


class TestDisabledAttributeStripping:
    """Task-list checkboxes are clickable (no `disabled`). It used to strip
    every ` disabled` in the whole HTML, eating the word from ordinary prose."""

    def test_word_disabled_in_prose_survives(self, client):
        assert "The account is disabled today." in preview(client, "The account is disabled today.\n")

    def test_disabled_attribute_in_raw_html_elsewhere_survives(self, client):
        assert "<button disabled>" in preview(client, '<button disabled>x</button>\n')

    def test_task_checkbox_is_still_enabled(self, client):
        html = preview(client, "- [ ] task\n")
        checkbox = re.search(r'<input[^>]*task-list-item-checkbox[^>]*>', html).group(0)
        assert "disabled" not in checkbox


class TestDirectivesInsideCode:
    """[[wikilinks]] and [[deck]] are pre-processed on the raw text.
    Inside code they must stay literal — they used to be rewritten into HTML
    that then showed up escaped inside the code block (or embedded a whole deck
    app inside a <pre>)."""

    def test_wikilink_in_fenced_code_stays_literal(self, client):
        html = preview(client, "```\nsee [[some page]]\n```\n")
        assert "see [[some page]]" in html
        assert "wikilink" not in html

    def test_wikilink_in_tilde_fence_stays_literal(self, client):
        assert "[[a|b]]" in preview(client, "~~~md\n[[a|b]]\n~~~\n")

    def test_wikilink_in_inline_code_stays_literal(self, client):
        html = preview(client, "Write `[[page]]` to link, e.g. [[page]].\n")
        assert "<code>[[page]]</code>" in html
        assert html.count('class="wikilink') == 1

    def test_double_backtick_inline_code(self, client):
        html = preview(client, "``a ` [[x]]`` and [[y]]\n")
        assert "[[x]]" in html
        assert 'href="/page/y"' in html

    def test_deck_directive_in_code_not_embedded(self, client):
        html = preview(client, "```\n[[deck tag=work]]\n```\n")
        assert "tasks-app" not in html
        assert "[[deck tag=work]]" in html

    def test_directives_outside_code_still_work(self, client):
        html = preview(client, "## A\n\n`code` then [[link]]\n\n```\nx\n```\n\n[[deck]]\n")
        assert 'href="/page/link"' in html
        assert "tasks-app" in html


class TestTextLikeMaskTokens:
    """A page's own text that looks like the private-use tokens masking
    uses (\\ue000N\\ue001 for code, \\ue002N\\ue003 for [[task:…]]) stays as
    written. It used to be taken for a token: the page failed to render
    or showed another code span there."""

    def test_page_renders_with_the_text_as_written(self, client):
        assert "x 5 y" in preview(client, "x 5 y\n")
        html = preview(client, "`code` x 0 y\n")
        assert "x 0 y" in html and html.count("<code>code</code>") == 1

    def test_task_ref_lookalikes_stay(self, client):
        assert "x 5 y" in preview(client, "x 5 y [[task:abcd1234]]\n")
        html = preview(client, "0 [[task:abcd1234]]\n")
        assert html.count("task-ref-missing") == 1 and "0" in html


class TestAutolinks:
    """Bare URLs become links (GFM autolinks) for text the paste prompt never
    saw: files edited on disk, unnamed pastes, URLs inside pasted text."""

    def test_bare_url_www_and_email(self, client):
        html = preview(client, "see https://example.com/a?b=1. and www.x.org, or me@x.org\n")
        assert '<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer">https://example.com/a?b=1</a>.' in html
        assert '<a href="http://www.x.org" target="_blank" rel="noopener noreferrer">www.x.org</a>' in html
        assert '<a href="mailto:me@x.org">me@x.org</a>' in html

    def test_code_and_existing_links_untouched(self, client):
        html = preview(client, "`https://code.org` and [named](https://y.org)\n\n```\nhttps://fenced.org\n```\n")
        assert "<code>https://code.org</code>" in html
        assert html.count("<a ") == 1 and '<a href="https://y.org" target="_blank" rel="noopener noreferrer">named</a>' in html
        assert "https://fenced.org" in html and 'href="https://fenced.org"' not in html

    def test_wikilinks_still_work_next_to_urls(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        html = preview(client, "[[Garden]] https://example.com\n")
        assert 'href="/page/garden"' in html
        assert '<a href="https://example.com" target="_blank" rel="noopener noreferrer">' in html


class TestCallouts:
    @pytest.mark.parametrize("kind", ["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"])
    def test_each_kind(self, client, kind):
        html = preview(client, f"> [!{kind}]\n> Mind **this**.\n")
        assert f'<div class="markdown-alert markdown-alert-{kind.lower()}">' in html
        assert f'<p class="markdown-alert-title">{kind.title()}</p>' in html
        assert "<strong>this</strong>" in html

    def test_plain_quote_stays_a_quote(self, client):
        html = preview(client, "> just a quote\n")
        assert "<blockquote>" in html and "markdown-alert" not in html

    def test_wikilink_inside_callout(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        html = preview(client, "> [!TIP]\n> See [[Garden]].\n")
        assert "markdown-alert-tip" in html and 'href="/page/garden"' in html


class TestExternalLinks:
    """Links to other sites open in a new tab; Zone's own links don't."""

    def test_marked_in_rendered_pages(self, client):
        out = preview(client, "[a](https://example.com) https://x.org www.y.org [p](/page/x) [[Home]] [m](mailto:a@b.c)\n")
        ext = ' target="_blank" rel="noopener noreferrer"'
        assert f'<a href="https://example.com"{ext}>a</a>' in out
        assert f'<a href="https://x.org"{ext}>' in out and f'<a href="http://www.y.org"{ext}>' in out
        assert '<a href="/page/x">p</a>' in out and '<a href="mailto:a@b.c">m</a>' in out
        assert out.count("_blank") == 3

    def test_full_url_to_leaf_itself_stays(self, client):
        out = preview(client, "[me](http://localhost:5000/page/y) [other](http://other:5000/)\n")
        assert '<a href="http://localhost:5000/page/y">me</a>' in out
        assert '<a href="http://other:5000/" target="_blank"' in out
