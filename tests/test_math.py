"""Math in pages over HTTP: what is math is no link, and display math is no
section. The parser and the source-side counts have Go tests
(go/math_test.go); rendering with KaTeX, tests/e2e/test_figures.py."""

import re

from tests.conftest import HOST, preview, section_fields, write_page


def test_wikilinks_in_math_stay_tex(client, wiki_dir):
    write_page(wiki_dir, "e", "# E\n")
    html = preview(client, "Brackets $[[e]]$ and $$[[e]]$$, a link [[e]].\n\n$$\n[[e]]\n$$\n")
    assert html.count('class="wikilink') == 1
    assert '<span class="math math-inline">[[e]]</span>' in html
    assert '<div class="math math-display">[[e]]\n</div>' in html


def test_sections_after_display_math(client, wiki_dir):
    # A ## line inside $$ is no heading, so the ✎ of ## Two edits ## Two.
    text = "# P\n\n## One\n\n$$\n## not a heading\n$$\n\n## Two\n\ntwo body\n"
    write_page(wiki_dir, "p", text)
    page = client.get("/page/p", headers=HOST).get_data(as_text=True)
    edits = re.findall(r'href="/edit/p\?section=(\d+)&amp;h=', page)
    assert edits == ["1", "2"]
    fields = section_fields(client, "p", 2)
    assert text[int(fields["start_char"]):].startswith("## Two")


def test_heading_with_math_keeps_its_anchor(client, wiki_dir):
    write_page(wiki_dir, "h", "# H\n\n## Area $x_1$\n\ntext\n")
    write_page(wiki_dir, "l", "# L\n\n[[h#Area $x_1$]]\n")
    page = client.get("/page/h", headers=HOST).get_data(as_text=True)
    assert '<h2 id="area-x_1">' in page
    link = client.get("/page/l", headers=HOST).get_data(as_text=True)
    assert 'href="/page/h#area-x_1"' in link


def test_heading_with_code_keeps_its_anchor(client, wiki_dir):
    # The page's id and a [[link#…]] to it keep the code's text alike.
    write_page(wiki_dir, "c", "# C\n\n## Run `snake_case` now\n\ntext\n")
    write_page(wiki_dir, "d", "# D\n\n[[c#Run `snake_case` now]]\n")
    page = client.get("/page/c", headers=HOST).get_data(as_text=True)
    assert '<h2 id="run-snake_case-now">' in page
    link = client.get("/page/d", headers=HOST).get_data(as_text=True)
    assert 'href="/page/c#run-snake_case-now"' in link
