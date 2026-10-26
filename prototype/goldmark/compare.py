"""Render every page of a wiki with Leaf (src/render.py) and with the Go
prototype, and compare.

    python prototype/goldmark/compare.py WIKI GO_BINARY OUT_DIR [EXTRA.md …]
    python prototype/goldmark/compare.py --fuzz N WIKI GO_BINARY OUT_DIR

Go gets each page's file as it is. Three checks:

1. Pre-processing (frontmatter, code masking, [[deck]], [[task:…]],
   [[wikilinks]]) must give exactly what render_md hands md.render, and the
   same frontmatter, deck parameters and task refs: it is input text, any
   difference is a bug.
2. Checkboxes: ticking the Nth one toggles the Nth task line mdtext finds,
   so their counts must agree.
3. The HTML, as a reader and Leaf's code see it: text, link targets, heading
   ids, checkboxes, images, the classes Leaf's CSS and JS use. Not the
   markup (<p> in a tight list, <s> or <del>, entities, attribute order,
   footnote ids): goldmark may render differently, nothing may break.

--fuzz N runs check 1 on N random texts made of the syntax pre-processing
looks for, with page "secret" hidden (sharing mode), instead of the wiki's
pages.

WIKI is read only through Leaf's modules; point it at a copy (config.py
creates _assets/ when it is missing). OUT_DIR gets a .diff per page that
differs and summary.txt; it holds your pages' text, keep it out of the repo.
"""

import difflib
import json
import os
import random
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


# What the fuzzer builds texts from: the syntax pre-processing looks for,
# and the characters Python and Go might disagree on.
FUZZ_TOKENS = [
    "a", "b", "Page", "page", "secret", "Notes/My Page", " ", " ", "\t", "\n", "\n", "\r\n", "\r", " ",
    "\x1c", "\xa0", "　", "`", "``", "```", "~~~", "````", "[[", "]]", "[", "]", "(", ")", "|", "#", "!",
    "*", "**", "_", "__", "deck", "DECK", " list=work", "task:", "1a2b3c4d", "abcd-ef", "---", "tags:",
    "tldr:", "sources:", " >", " |", "- ", "  ", '"', "'", ",", ":", "<", ">", "&", "ü", "Ärger", "日本",
    "", "1", "<!--t:2026-06-12T18:35:00-->",
]


# Whole constructs, which random tokens rarely form.
FUZZ_PARTS = [
    "[[page]]", "[[Page|Label]]", "[[secret]]", "[[secret|kept words]]", "[[Notes/My Page#Sec *x* `c`|l]]",
    "[[#Über [[x]] **b**]]", "[[page#A [link](u) ![i](p) [r][1] _u_ __d__]]", "[[ missing page ]]",
    "[[deck]]", "[[deck list=work show=open]]", "[[DECK  view=x]]", "[[task:1a2b3c4d]]", "[[task:ABCD-ef12]]",
    "`[[page]]`", "``a`b``", "```\n[[page]]\n```\n", "~~~~\n~~~\n[[deck]]\n~~~~\n",
    "tags: a, b\n", "tags: [x, \"y\"]\n", "tags:\n  - one\n  - 'two'\n", "sources: [l, with](u), [[p|a, b]]\n",
    "tldr: plain\n  goes on\n", "tldr: >-\n  folded\n\n  para\n", "tldr: |\n    literal\n      more\n",
    "tldr: \"quoted\"\n", "---\n", "\n",
]


def fuzz_text(rng: random.Random) -> str:
    parts = (rng.choice(FUZZ_PARTS) if rng.random() < 0.4 else rng.choice(FUZZ_TOKENS)
             for _ in range(rng.randint(1, 60)))
    text = "".join(parts)
    return ("---\n" + text) if rng.random() < 0.4 else text


def main() -> None:
    args, fuzz = sys.argv[1:], 0
    if args[0] == "--fuzz":
        fuzz, args = int(args[1]), args[2:]
    wiki, binary, out = Path(args[0]), args[1], Path(args[2])
    extra = [Path(p) for p in args[3:]]
    os.environ["LEAF_DATA"] = str(wiki)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import deck
    import deckstore
    import render
    from decksyntax import DECK_RE
    from mdtext import _task_item_lines, mask_code, parse_frontmatter, unmask_code
    from pages import all_slugs, hidden_page, iter_pages

    slugs = set(all_slugs())
    hidden = {s for s in slugs if hidden_page(s)}
    if fuzz:
        slugs |= {"page", "notes/my-page", "secret"}
        hidden = {"secret"}
        render.hidden_page = lambda slug: slug in hidden

    def prepare(content: str) -> dict:
        """render_md up to md.render, and what it found on the way."""
        meta, text = parse_frontmatter(content)
        text, stash = mask_code(text)
        decks = [m.group(1) or "" for m in DECK_RE.finditer(text)]
        text, _ = deck.process_deck_embeds(text)
        text, refs = render._mask_task_refs(text)
        return {"meta": meta, "text": unmask_code(render.process_wikilinks(text, slugs), stash),
                "decks": decks, "task_refs": refs}

    jobs, boxes = [], {}  # boxes: the checkboxes mdtext toggles by number, per page
    if fuzz:
        rng = random.Random(fuzz)
        pages = [(f"fuzz-{i}", "fuzz", fuzz_text(rng)) for i in range(fuzz)]
    else:
        pages = [(str(path), slug, path.read_text(encoding="utf-8"))
                 for path, slug in [*sorted(iter_pages()), *((p, p.stem) for p in extra)]]
    for id_, slug, content in pages:
        chips = {} if fuzz else render._section_task_chips(deckstore.find_task_backlinks(slug))
        boxes[id_] = len(_task_item_lines(content.splitlines(keepends=True)))
        jobs.append({"id": id_, "content": content, "env": {"edit_slug": slug, "section_chips": chips}})

    wiki_line = json.dumps({"slugs": sorted(slugs), "hidden": sorted(hidden)})
    run = subprocess.run([binary], input=wiki_line + "\n" + "".join(json.dumps(j) + "\n" for j in jobs),
                         stdout=subprocess.PIPE, text=True, check=True)
    go = {r["id"]: r for r in map(json.loads, run.stdout.splitlines())}

    out.mkdir(parents=True, exist_ok=True)
    for old in [*out.glob("*.diff"), *out.glob("*.prep.json")]:
        old.unlink()
    prep_wrong, box_wrong, same, lines = [], [], 0, Counter()
    for j in jobs:
        id_, content, g = j["id"], j["content"], go[j["id"]]
        name = id_.replace(str(wiki) + "/", "").replace("/", "__")
        py = prepare(content)
        gp = {k: g["prepared"][k] or [] for k in ("decks", "task_refs")}
        gp["meta"], gp["text"] = {k: v for k, v in g["prepared"]["meta"].items() if v}, g["prepared"]["text"]
        if py != gp:
            prep_wrong.append(id_)
            (out / f"{name}.prep.json").write_text(
                json.dumps({"content": content, "python": py, "go": gp}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        if fuzz:
            continue
        if g["html"].count('class="task-list-item-checkbox"') != boxes[id_]:
            box_wrong.append(id_)
        env = {"edit_slug": j["env"]["edit_slug"], "sections": 0, "section_chips": j["env"]["section_chips"]}
        a, b = canon(render.md.render(py["text"], env)), canon(g["html"])
        if a == b:
            same += 1
            continue
        diff = list(difflib.unified_diff(a, b, "markdown-it", "goldmark", lineterm="", n=2))
        lines[id_] = sum(1 for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---"))
        (out / f"{name}.diff").write_text("\n".join(diff) + "\n", encoding="utf-8")

    report = [f"{len(prep_wrong)}/{len(jobs)} {'texts' if fuzz else 'pages'} pre-processed differently"
              + "".join(f"\n       {p}" for p in prep_wrong[:20])]
    if not fuzz:
        report.append(f"{len(box_wrong)} pages where goldmark's checkboxes are not mdtext's task lines"
                      + "".join(f"\n       {p}" for p in box_wrong))
        report.append(f"{same}/{len(jobs)} pages render the same (text, links, ids, checkboxes, Leaf's classes)")
        report.append(f"{len(lines)} differ, {sum(lines.values())} changed lines in all")
        report += [f"{n:5}  {p}" for p, n in lines.most_common()]
    (out / "summary.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[:4]))


if __name__ == "__main__":
    main()
