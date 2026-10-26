"""Golden files for the Go port's pure functions: Python's answers on many
inputs, which go/golden_test.go checks Go against. The inputs are every
string literal in tests/*.py (the cases Leaf's tests care about) and some
edge cases of our own. Rerun after changing one of these functions in
Python (while both exist):

    LEAF_DATA=$(mktemp -d) .venv/bin/python go/testdata/golden.py
"""

import ast
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import decksyntax  # noqa: E402
import mdtext  # noqa: E402
import pages  # noqa: E402

NOW = datetime(2026, 1, 2, 3, 4, 5)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


mdtext.datetime = FixedDatetime   # toggle_nth_checkbox stamps the time

EXTRA = [
    "", "\n", "# Title", "#Title", "#\tTab title", "#  nbsp", "  # indented",
    "---\ntags: a\n---\n# T\n", "---\n# not title\n---\nbody", "```\n# code\n```\n# Real",
    "## A\ntext\n### B\n## C\n", "Setext\n---\nbody\n", "- item\n---\n", "a\r\nb\rc d",
    "`[[x]]` [[y]] ``a`b`` 1", "- [ ] a\n- [x] b\n  - [ ] c\n1. [ ] d\n> - [X] e\n- [ ]\n",
    "[l](../a/b.md) [m](/page/x#h) [n](c) [o](http://x) ![i](d.md) [p](e.png) [q](f/)",
    "[[Page#Sec|Label]] [[#Only]] [[ spaced | label ]] [[a|b|c]]",
    "Über **bold** _em_ `code` [link](u) [[w|label]]", "ÄÖÜ ß straße", "tab\there", "x\x1cy",
]

TODAY = date(2026, 3, 18)   # a Wednesday


def literals() -> list[str]:
    found = set(EXTRA)
    for f in sorted((REPO / "tests").glob("**/*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 3000:
                found.add(node.value)
    return sorted(found)


def main() -> None:
    texts = literals()
    cases = []
    for t in texts:
        sections = mdtext.split_sections(t)
        meta, body = mdtext.parse_frontmatter(t)
        masked, stash = mdtext.mask_code(t)
        ranges = [mdtext.section_char_range(t, i) for i in range(len(sections) + 1)]
        toggles = [mdtext.toggle_nth_checkbox(t, i) for i in range(4)]
        notes_toggles = [mdtext.toggle_nth_checkbox(t, i, frontmatter=False) for i in range(2)]
        slug = "folder/sub/page"
        cases.append({
            "text": t,
            "split_sections": sections,
            "section_char_range": [list(r) if r else None for r in ranges],
            "parse_frontmatter": [{k: meta[k] for k in sorted(meta)}, body],
            "mask_code": [masked, stash],
            "page_title": mdtext.page_title(t, "some-slug"),
            "strip_h1": mdtext.strip_h1(t),
            "toggle": toggles,
            "toggle_notes": notes_toggles,
            "to_slug": mdtext.to_slug(t),
            "section_anchor": mdtext.section_anchor(t),
            "plain_task_title": mdtext.plain_task_title(t),
            "slug_display_name": mdtext.slug_display_name(t),
            "page_link_targets": sorted(pages.page_link_targets(t, slug)),
            "md_link_misses": pages.md_link_misses(t, slug, {"folder/a", "x"}),
            "rewrite_page_links": pages.rewrite_page_links(t, "folder/a", "other/b", slug),
            "rewrite_to_section": pages.rewrite_page_links(t, "x", "y", slug, "New Heading"),
            "rebase_links": pages.rebase_links(t, slug, "moved/page", {"folder/a": "z/a"}),
            "add_frontmatter_tags": mdtext.add_frontmatter_tags(t, ["new", "A"]),
            "note_as_section": list(mdtext.note_as_section(t, "Heading")),
            "promoted_slug": mdtext.promoted_slug("Folder X", t, "2026-01-02"),
            "page_wikilink": mdtext.page_wikilink("s", t),
            "is_scratch": mdtext.is_scratch(t),
            "clean_tags": decksyntax.clean_tags([t, "#" + t]),
            "clean_list": decksyntax.clean_list(t),
            "parse_date_word": decksyntax.parse_date_word(t, TODAY),
            "deck_params": [list(p.items()) for p in (decksyntax.parse_deck_params(m.group(1) or "")
                                                      for m in decksyntax.DECK_RE.finditer(t))],
        })
    out = Path(__file__).parent / "golden.json.gz"
    data = json.dumps({"now": NOW.isoformat(), "today": TODAY.isoformat(), "cases": cases},
                      ensure_ascii=False, separators=(",", ":"))
    out.write_bytes(gzip.compress(data.encode("utf-8"), mtime=0))
    print(f"{len(cases)} cases → {out}")


if __name__ == "__main__":
    main()
