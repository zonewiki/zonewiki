"""Render every page of a wiki with Leaf's markdown-it (src/render.py's md)
and with the goldmark prototype, and compare what a reader and Leaf's code
see: the text, link targets, heading ids, checkboxes, images and the classes
Leaf's CSS and JS use. Not the markup itself (<p> or not in a tight list,
<s> or <del>, entities, attribute order, footnote ids): the goldmark port may
render differently, it must not render anything broken.

Both get the same input: the page after Leaf's pre-processing (frontmatter
off, [[wikilinks]] as HTML, [[deck]] and [[task:…]] masked), as render_md
hands it to md.render, with the page view's env (✎ per section, the ☑ N
chips of tasks linking to a section).

    python prototype/goldmark/compare.py WIKI GO_BINARY OUT_DIR [EXTRA.md …]

WIKI is read only through Leaf's modules; point it at a copy (config.py
creates _assets/ when it is missing). OUT_DIR gets one .diff per page that
differs (in canonical tokens, see canon) and summary.txt; it holds your pages' text, so keep it out of the repo.
"""

import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

# Classes Leaf's CSS or JS select on; other classes are the renderer's own.
LEAF_CLASS = re.compile(r"^(task-list-item(-checkbox)?|markdown-alert(-\w+)?|img-block|section-\w+|"
                        r"task-date(-\w+)?|task-ref(-\w+)?|wikilink(-\w+)?|tasks-app)$")
KEEP_ATTRS = {"a": ("href", "target"), "img": ("src", "alt", "style"), "input": ("type", "checked"),
              "h1": ("id",), "h2": ("id",), "h3": ("id",)}
SAME_TAG = {"del": "s", "section": "div"}
SKIP_TAGS = {"p", "hr", "sup", "thead", "tbody"}  # layout only, or footnote markup


class Canon(HTMLParser):
    """HTML as a list of tokens: <tag attr=…> for the tags and attributes
    that matter, the text between them with whitespace collapsed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.text = [], ""

    def flush(self):
        t = " ".join(self.text.split())
        if t:
            self.out.append(t)
        self.text = ""

    def handle_starttag(self, tag, attrs):
        tag = SAME_TAG.get(tag, tag)
        a = dict(attrs)
        if tag in SKIP_TAGS or (a.get("href") or "").startswith("#fn") or "footnote" in (a.get("class") or ""):
            return
        self.flush()
        keep = [f'{k}="{unquote(a[k]) if k in ("href", "src") else a[k]}"'
                for k in KEEP_ATTRS.get(tag, ()) if k in a]
        classes = [c for c in (a.get("class") or "").split() if LEAF_CLASS.match(c)]
        if classes:
            keep.append(f'class="{" ".join(sorted(classes))}"')
        self.out.append(f"<{' '.join([tag, *keep])}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = SAME_TAG.get(tag, tag)
        if tag not in SKIP_TAGS:
            self.flush()
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.text += data


def canon(html: str) -> list[str]:
    c = Canon()
    c.feed(html)
    c.close()
    c.flush()
    # Footnote back-links: markdown-it's ↩︎ text, goldmark's the same as entities.
    return [t for t in c.out if t not in ("<a>", "</a>") and t.strip("↩︎ ")]


def main() -> None:
    wiki, binary, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    extra = [Path(p) for p in sys.argv[4:]]
    os.environ["LEAF_DATA"] = str(wiki)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import deck
    import deckstore
    import render
    from mdtext import _task_item_lines, mask_code, parse_frontmatter, unmask_code
    from pages import iter_pages

    def md_input(content: str) -> str:
        """render_md's text, up to md.render."""
        _, text = parse_frontmatter(content)
        text, stash = mask_code(text)
        text, _ = deck.process_deck_embeds(text)
        text, _ = render._mask_task_refs(text)
        return unmask_code(render.process_wikilinks(text), stash)

    jobs = []
    boxes = {}  # the checkboxes mdtext toggles by number, per page
    for path, slug in [*sorted(iter_pages()), *((p, p.stem) for p in extra)]:
        chips = render._section_task_chips(deckstore.find_task_backlinks(slug))
        content = path.read_text(encoding="utf-8")
        boxes[str(path)] = len(_task_item_lines(content.splitlines(keepends=True)))
        jobs.append({"id": str(path), "text": md_input(content),
                     "env": {"edit_slug": slug, "section_chips": chips}})

    py = {j["id"]: render.md.render(j["text"], {"edit_slug": j["env"]["edit_slug"], "sections": 0,
                                                  "section_chips": j["env"]["section_chips"]})
          for j in jobs}
    run = subprocess.run([binary], input="".join(json.dumps(j) + "\n" for j in jobs),
                         stdout=subprocess.PIPE, text=True, check=True)
    go = {r["id"]: r["html"] for r in map(json.loads, run.stdout.splitlines())}

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.diff"):
        old.unlink()
    same, lines = 0, Counter()
    report = []
    for j in jobs:
        a, b = canon(py[j["id"]]), canon(go[j["id"]])
        if a == b:
            same += 1
            continue
        diff = list(difflib.unified_diff(a, b, "markdown-it", "goldmark", lineterm="", n=2))
        changed = sum(1 for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---"))
        lines[j["id"]] = changed
        name = j["id"].replace(str(wiki) + "/", "").replace("/", "__")
        (out / f"{name}.diff").write_text("\n".join(diff) + "\n", encoding="utf-8")
    # Ticking the Nth checkbox toggles the Nth task line mdtext finds: the
    # counts must agree, or a click ticks the wrong item.
    wrong = [j["id"] for j in jobs if go[j["id"]].count('class="task-list-item-checkbox"') != boxes[j["id"]]]
    report.append(f"{len(wrong)} pages where goldmark's checkboxes are not mdtext's task lines"
                  + "".join(f"\n       {p}" for p in wrong))
    report.append(f"{same}/{len(jobs)} pages render the same (text, links, ids, checkboxes, Leaf's classes)")
    report.append(f"{len(lines)} differ, {sum(lines.values())} changed lines in all")
    report += [f"{n:5}  {p}" for p, n in lines.most_common()]
    (out / "summary.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[:3 + len(wrong)]))


if __name__ == "__main__":
    main()
