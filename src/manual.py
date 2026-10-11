"""Leaf's own documentation, for the MCP tool leaf_help: the README and the
spec (docs/spec/*.md), searched by section with BM25, so an assistant answers
questions about Leaf itself (how to rename a deck's tabs, a setting, the
query syntax) from the docs rather than from guesses."""

import re
from pathlib import Path

import embed
import search
from mdtext import iter_fenced_lines

# How Leaf is tested: nothing a user asks about.
_SKIP = {"testing"}
# A search hit's size: long sections are split at paragraphs into pieces
# of at most this, so a hit is the passage, not a whole long section.
CHUNK_CHARS = 1500

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def docs_root() -> Path | None:
    """The folder holding README.md and docs/spec/: the checkout (src/..),
    or, in the image, the code's own folder (the Containerfile copies them
    next to it). None when neither has them."""
    here = Path(__file__).resolve().parent
    for root in (here.parent, here):
        if (root / "docs" / "spec").is_dir():
            return root
    return None


def topics() -> dict[str, Path]:
    """The docs by topic name: 'readme', then each spec file's name ('deck',
    'editor', …), in that order."""
    root = docs_root()
    if root is None:
        return {}
    found = {}
    if (root / "README.md").is_file():
        found["readme"] = root / "README.md"
    for path in sorted((root / "docs" / "spec").glob("*.md")):
        if path.stem not in _SKIP:
            found[path.stem] = path
    return found


def read(topic: str) -> str:
    return topics()[topic].read_text(encoding="utf-8")


def headings(text: str) -> list[tuple[int, str]]:
    """A doc's headings as (level, text), headings in code fences left out."""
    return [(len(m.group(1)), m.group(2)) for _i, line, in_fence in iter_fenced_lines(text.split("\n"))
            if not in_fence and (m := _HEADING_RE.match(line))]


def title(topic: str, text: str) -> str:
    """A doc's first heading ('Deck', 'Navigation'), else the topic name."""
    found = headings(text)
    return found[0][1] if found else topic


_cache: dict = {"key": None, "index": None}


def _index() -> search.SearchIndex:
    """BM25 over every doc's sections, rebuilt when a doc file changes."""
    paths = topics()
    key = tuple((str(p), p.stat().st_mtime_ns) for p in paths.values())
    if _cache["key"] != key:
        docs = []
        for name, path in paths.items():
            text = path.read_text(encoding="utf-8")
            doc_title = title(name, text)
            for heading, body, _level in embed.chunk_sections(text, CHUNK_CHARS):
                docs.append({
                    "topic": name, "title": doc_title, "heading": heading, "text": body,
                    "tokens": search.tokenize(f"{heading}\n{body}")
                    + search.tokenize(f"{name} {doc_title} {heading}") * search.TITLE_WEIGHT,
                })
        _cache.update(key=key, index=search.SearchIndex(docs))
    return _cache["index"]


def find(query: str, limit: int = 5) -> list[dict]:
    """The doc passages most relevant to the query, best first: dicts with
    topic, title, heading and text."""
    return [d for _score, d in _index().search(query, limit)]
