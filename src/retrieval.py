"""Search: BM25 over pages and deck tasks (search.py), fused with the
optional semantic index (embed.py), for the browser (do_search) and for
LLMs (retrieve, sections rather than pages)."""

from urllib.parse import urlencode

import config
import decksyntax
import deckstore
import pages
import embed
import search
from mdtext import (
    heading_id, is_scratch, page_tags, page_title, parse_frontmatter, plain_heading,
    plain_task_title, strip_h1,
)
from pages import all_backlink_counts, fmt_date, is_noshare, iter_pages, sharing_on, url
from render import task_href, TOC_RE


# Cache of tokenized page documents, keyed by slug -> (mtime, doc, deck key). Avoids
# re-reading and re-tokenizing every file on each search; invalidated per-file
# when the on-disk mtime changes so edits made outside the app stay reflected.
_search_doc_cache: dict[str, tuple[float, dict, tuple]] = {}


def _task_refs_as_titles(content: str) -> str:
    """Page text with each [[task:…]] replaced by its task's title: what
    the reader sees there, and what search and embeddings should match."""
    return deckstore.expand_task_refs(
        content, lambda ref, task: plain_task_title(task.get("title", "")) if task else "")


def _per_page(cache: dict, build, key=None, fresh=lambda value, key: True) -> list:
    """build(slug, content, mtime) for every page, cached per page until its
    file changes (by mtime, so edits made outside the app count), or until
    fresh(value, key) says the value is stale. Entries of pages that are
    gone are dropped. One bad file must not fail every search: a page
    deleted meanwhile is skipped, one that isn't UTF-8 is read with
    stand-ins for the undecodable bytes."""
    out, seen = [], set()
    for md_file, slug in iter_pages():
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            continue
        cached = cache.get(slug)
        if not (cached and cached[0] == mtime and fresh(cached[1], cached[2])):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cached = cache[slug] = (mtime, build(slug, content, mtime), key)
        seen.add(slug)
        out.append(cached[1])
    for stale in [s for s in cache if s not in seen]:
        del cache[stale]
    return out


def _page_search_doc(slug: str, content: str, mtime: float) -> dict:
    """A page as a BM25 document: its text, its title (TITLE_WEIGHT extra
    times) and the words of its slug once, so a page is found by its file
    and folder names too ("robin" finds .../2026-06-18_update-alex-robin)
    without those outranking what the page says."""
    title = page_title(content, slug)
    text = search.strip_link_targets(_task_refs_as_titles(content))
    return {
        "slug": slug,
        "title": title,
        "mtime": mtime,
        "task_refs": bool(decksyntax.TASK_REF_RE.search(content)),
        "tags": page_tags(content),
        "excerpt_text": text,
        "tokens": search.tokenize(text)
        + search.tokenize(title) * search.TITLE_WEIGHT
        + search.tokenize(slug),
    }


def _page_search_docs() -> list[dict]:
    """Every page as a BM25 document (_page_search_doc). A page with
    [[task:…]] is indexed with the tasks' titles, so it is stale when the
    deck changes too."""
    deck_key = deckstore.deck_mtimes()
    return _per_page(_search_doc_cache, _page_search_doc, key=deck_key,
                     fresh=lambda doc, key: not doc["task_refs"] or key == deck_key)


# The BM25 index over the current docs, rebuilt only when a doc changes.
# Both doc caches hand back the same dict objects until their file changes,
# so the docs' identities are the key (the cached index keeps them alive, so
# an id can't be reused while it is the key).
_search_index_cache: dict = {"key": None, "index": None}


def _cached_index(cache: dict, docs: list[dict]) -> search.SearchIndex:
    """The BM25 index over docs, kept in cache until a doc changes."""
    key = tuple(map(id, docs))
    if cache["key"] != key:
        cache["index"] = search.SearchIndex(docs)
        cache["key"] = key
    return cache["index"]


def _search_index() -> search.SearchIndex:
    return _cached_index(_search_index_cache, _page_search_docs() + deckstore.task_search_docs())


def _semantic_text(content: str, slug: str) -> tuple[str, str]:
    """A page as (title, body) for embedding: the tldr and tags lead, the
    frontmatter block and the H1 (the title already) are dropped, and so are
    [[deck …]] and [[toc]] directives. Those are markup, not content: pages
    holding nothing but a deck embed came out as each other's closest match."""
    meta, body = parse_frontmatter(_task_refs_as_titles(content))
    lead = "\n".join(filter(None, [meta.get("tldr", ""), " ".join(meta.get("tags", []))]))
    body = TOC_RE.sub("", decksyntax.DECK_RE.sub("", strip_h1(body)))
    return page_title(content, slug), f"{lead}\n\n{body}" if lead else body


# None unless LEAF_EMBEDDINGS is set: then search is hybrid (BM25 fused with
# vector similarity) and pages list their nearest neighbours as Related.
SEMANTIC: embed.SemanticIndex | None = None


def semantic_from_env() -> embed.SemanticIndex | None:
    """The semantic index LEAF_EMBEDDINGS asks for, its worker not started
    (None: keywords only)."""
    return embed.from_env(
        config.LEAF_DATA, lambda: [(s, p) for p, s in iter_pages()], _semantic_text,
        # A task is embedded as its search text: title, notes and #tags.
        tasks_fn=lambda: [(d["task_id"], d["excerpt_text"]) for d in deckstore.task_search_docs()])


def start_semantic() -> None:
    """Set up the semantic index LEAF_EMBEDDINGS asks for and start its
    worker (server.py, once the deck is set up: the worker reads tasks)."""
    global SEMANTIC
    SEMANTIC = semantic_from_env()
    if SEMANTIC:
        SEMANTIC.start()


# How deep each ranking goes into the fusion; the fused list is cut to 15.
FUSE_DEPTH = 30


def _by_similarity(*hit_lists: list[tuple[dict, float]]) -> list[dict]:
    """One semantic ranking from several (doc, similarity) lists, e.g. page
    sections and tasks: all come from the same model, so their cosine
    similarities compare directly."""
    return [d for d, _s in sorted((h for hits in hit_lists for h in hits), key=lambda h: -h[1])]


def _doc_text(d: dict) -> str:
    """A page, section or task doc's searchable text."""
    return d["excerpt_text"] if "excerpt_text" in d else f"{d['title']}\n{d['heading']}\n{d['text']}"


def _doc_date(d: dict) -> str | None:
    """A doc's day for the date filters: a page's (or section's) last edit,
    a task's last activity."""
    return fmt_date(d["mtime"]) if "mtime" in d else d.get("edited")


def _accepts(query: search.Query, d: dict) -> bool:
    """Whether a page, section or task doc passes the query's filters."""
    return query.accepts(_doc_text(d), d["tokens"], d.get("tags"), _doc_date(d))


def passes(query: search.Query, d: dict, keep=None) -> bool:
    """Whether a doc takes part in a search: it passes `keep` (when given)
    and the query's filters."""
    return (keep is None or keep(d)) and (not query.has_filters or _accepts(query, d))


def _has_phrase(d: dict, phrase: str) -> bool:
    # The squashed text is cached on the doc, which lives until its file changes.
    if "_squashed" not in d:
        d["_squashed"] = search.squash(_doc_text(d))
    return phrase in d["_squashed"]


def _phrase_first(ranked: list[dict], query: search.Query, index: search.SearchIndex,
                  keep=None) -> list[dict]:
    """ranked with the docs containing the query as a phrase moved to the
    front (in BM25 order), including ones the ranking had cut off: a page
    saying exactly what was typed beats one that has the words apart."""
    phrase = query.phrase
    if not phrase:
        return ranked
    hits = [d for _s, d in index.search(query.ranking_text, limit=len(index.docs))
            if passes(query, d, keep) and _has_phrase(d, phrase)]
    if not hits:
        return ranked
    first = {id(d) for d in hits}
    return hits + [d for d in ranked if id(d) not in first]


def _bm25(index: search.SearchIndex, query: search.Query, depth: int, keep=None) -> list[dict]:
    """The best `depth` docs by BM25 for the query's ranking text, among those
    that pass its filters (and `keep`, when given)."""
    text = query.ranking_text
    if not query.has_filters and keep is None:
        return [d for _s, d in index.search(text, limit=depth)]
    return [d for _s, d in index.search(text, limit=len(index.docs))
            if passes(query, d, keep)][:depth]


def _ranked_docs(query: search.Query, index: search.SearchIndex, keep=None,
                 limit: int = 15) -> list[dict]:
    """The `limit` best docs for the query. Keyword ranking only, unless semantic
    search is on and ready: then BM25 and vector similarity are fused
    (search.fuse). Docs that fail the query's filters, or `keep` when
    given, take no part."""
    q = query.ranking_text
    if not q.strip():
        if not query.dates:
            return []
        # Only date filters ("edited:2026-09"): everything that passes,
        # newest first. There are no words to rank by.
        docs = [d for d in index.docs if passes(query, d, keep)]
        return sorted(docs, key=lambda d: _doc_date(d) or "", reverse=True)[:limit]
    # Each ranking goes as deep as the fused list must be long.
    depth = max(FUSE_DEPTH, limit) if SEMANTIC else limit
    bm25 = _bm25(index, query, depth, keep)
    if not SEMANTIC:
        return _phrase_first(bm25, query, index, keep)[:limit]
    SEMANTIC.poke()
    allowed = [d for d in index.docs if passes(query, d, keep)]
    pages = {d["slug"]: d for d in allowed if d.get("type") != "task"}
    tasks = {d["task_id"]: d for d in allowed if d.get("type") == "task"}
    hits = SEMANTIC.search(q, known=pages, limit=depth)
    task_hits = SEMANTIC.search_tasks(q, known=tasks, limit=depth)
    if not hits and not task_hits:
        return _phrase_first(bm25, query, index, keep)[:limit]
    semantic = _by_similarity([(pages[slug], sc) for slug, sc, _t in hits],
                              [(tasks[i], sc) for i, sc in task_hits])
    return _phrase_first(search.fuse([bm25, semantic[:depth]]), query, index, keep)[:limit]


def _best_sections(query: search.Query, slugs: set[str]) -> dict[str, dict]:
    """For each page in slugs, the section a result should open: one whose
    own text has the query as a phrase, else the best BM25-scored section
    whose own text has a query word, else, for a page found by meaning, its
    most similar section. A page matched only by its title gets none (every
    section would match equally), so its link opens the top of the page."""
    q = query.ranking_text
    index = _chunk_index()
    ranked = [d for _s, d in index.search(q, limit=len(index.docs))
              if d.get("type") != "task" and d.get("slug") in slugs]
    best: dict[str, dict] = {}
    if query.phrase:
        for d in ranked:
            if d["slug"] not in best and query.phrase in search.squash(f"{d['heading']}\n{d['text']}"):
                best[d["slug"]] = d
    for d in ranked:
        if d["slug"] not in best and search.matches(f"{d['heading']}\n{d['text']}", q):
            best[d["slug"]] = d
    if SEMANTIC and len(best) < len(slugs):
        rest = slugs - set(best)
        by_key = {d["key"]: d for d in index.docs if d.get("type") != "task" and d["slug"] in rest}
        for key, _sc in SEMANTIC.search_chunks(q, known=rest, limit=len(by_key) or 1):
            if key in by_key:
                best.setdefault(by_key[key]["slug"], by_key[key])
    return best


def _result_meta(d: dict) -> str:
    """The small line under a result's title, telling apart results whose
    titles look alike: a page's folder and last edit, a task's list and
    status ("Deck · done 2026-05-01")."""
    if d.get("type") == "task":
        task = d["task"]
        status = task_status(task)
        if status != "open":
            status += f" {task['completed_at'][:10]}"
        list_name = pages.task_list(task)
        where = f"Deck › {list_name}" if list_name else "Deck"
        return f"{where} · {status}"
    folder = d["slug"].rpartition("/")[0]
    edited = f"edited {fmt_date(d['mtime'])}"
    return f"{folder} · {edited}" if folder else edited


SEARCH_LIMIT = 15       # results in the search modal (/api/search default)
SEARCH_PAGE_LIMIT = 50  # results on the /search page ("All results")
SEARCH_MAX_LIMIT = 100
SCRATCH_FILTER_LIMIT = 1000   # the Scratch page's filter lists every match


def do_search(raw: str, unlinked: bool = False, limit: int = SEARCH_LIMIT,
              scratch: bool = False) -> list[dict]:
    """Search results for the browser, at most `limit`. `unlinked` keeps
    only pages no other page links to (the search modal's "Unlinked
    only"), no tasks. `scratch` keeps only scratch notes (the Scratch
    page's filter)."""
    if not raw.strip():
        return []
    query = search.Query(raw)
    q = query.ranking_text  # what excerpts mark: the words, without operators
    tests = []
    if unlinked:
        linked = {slug for slug, n in all_backlink_counts().items() if n > 0}
        tests.append(lambda d: d.get("type") != "task" and d["slug"] not in linked)
    if scratch:
        tests.append(lambda d: d.get("type") != "task" and is_scratch(d["slug"]))
    if sharing_on():
        tests.append(lambda d: d.get("type") == "task" or not is_noshare(d["slug"]))
    keep = (lambda d: all(t(d) for t in tests)) if tests else None
    docs = _ranked_docs(query, _search_index(), keep, limit)
    sections = _best_sections(query, {d["slug"] for d in docs if d.get("type") != "task"})
    results = []
    names = pages.list_names()
    for d in docs:
        section = sections.get(d["slug"]) if d.get("type") != "task" else None
        text = f"{section['heading']}\n{section['text']}".strip() if section else d["excerpt_text"]
        r = {
            "slug": d["slug"],
            "title": d["title"],
            "excerpt": search.make_excerpt(text, q),
        }
        for k in ("type", "task_id", "status", "year"):
            if d.get(k) is not None:
                r[k] = d[k]
        if d.get("type") == "task" and (list_name := pages.task_list(d["task"], names)):
            r["list"] = list_name
        r["meta"] = _result_meta(d)
        r["href"] = _result_href(r)
        if d.get("type") != "task":
            # The page highlights what matched (js/search.js); before the #anchor.
            words = search.matched_words(d["excerpt_text"], q)
            if words:
                r["href"] += "?" + urlencode([("hl", w) for w in words])
        if section:
            r["href"] += section["anchor"]
        results.append(r)
    return results


# Like search, but it returns sections rather than pages, with their full
# text: an LLM needs the passages, and the best ones may share a page. BM25
# runs over sections here, fused with semantic section hits when that is on.

_chunk_doc_cache: dict[str, tuple[float, list[dict], None]] = {}


def _section_anchor(heading: str, level: int) -> str:
    """'#<id>' of a section's heading as the rendered page has it, or "" when
    there is none: no heading, or one deeper than anchors_plugin's max_level
    (3), whose heading carries no id."""
    if not heading or level > 3:
        return ""
    return f"#{heading_id(plain_heading(heading))}"


def _page_chunks(slug: str, content: str, mtime: float) -> list[dict]:
    """A page's sections (embed.chunk_sections) as BM25 documents. A
    section's `key` is the one its vector has in the semantic index."""
    title, body = _semantic_text(content, slug)
    tags = page_tags(content)
    chunks = []
    for heading, text, level, path in embed.chunk_sections(body) or [("", "", 0, "")]:
        chunk = embed.chunk_text(path, text)
        chunks.append({
            "slug": slug, "title": title, "heading": heading, "path": path, "text": text,
            "anchor": _section_anchor(heading, level), "tags": tags,
            "mtime": mtime, "key": embed.chunk_key(title, chunk),
            "tokens": search.tokenize(f"{heading}\n{text}") + search.tokenize(title) * search.TITLE_WEIGHT
            + search.tokenize(slug),
        })
    return chunks


def _page_chunk_docs() -> list[dict]:
    """Every page's sections as BM25 documents, cached per page by mtime."""
    return [d for chunks in _per_page(_chunk_doc_cache, _page_chunks) for d in chunks]


_chunk_index_cache: dict = {"key": None, "index": None}


def _chunk_index() -> search.SearchIndex:
    """BM25 over page sections and deck tasks (a task is one result, like a
    section), as the browser search ranks pages and tasks together."""
    return _cached_index(_chunk_index_cache, _page_chunk_docs() + deckstore.task_search_docs())


def in_folder(slug: str, folder: str | None) -> bool:
    """Whether slug is the page `folder` or below it (case ignored)."""
    if not folder:
        return True
    slug, folder = slug.lower(), folder.lower()
    return slug == folder or slug.startswith(folder + "/")


def retrieve(raw: str, limit: int = 8, under: str | None = None) -> list[dict]:
    """The sections and tasks most relevant to the query, best first: section
    dicts (slug, title, heading, text) and task docs (type "task", with the
    task). The query syntax applies (search.Query). With `under`, only
    sections of pages in that folder, and no tasks."""
    query = search.Query(raw)
    q = query.ranking_text
    if not q.strip():
        return []
    index = _chunk_index()
    keep = (lambda d: d.get("type") != "task" and in_folder(d["slug"], under)) if under else None
    ranked = _bm25(index, query, FUSE_DEPTH, keep)
    if SEMANTIC:
        SEMANTIC.poke()
        sections = [d for d in index.docs if d.get("type") != "task" and in_folder(d["slug"], under)
                    and passes(query, d)]
        by_key = {d["key"]: d for d in sections}
        hits = [(by_key[k], sc) for k, sc in SEMANTIC.search_chunks(
            q, known={d["slug"] for d in sections}, limit=FUSE_DEPTH) if k in by_key]
        task_hits = []
        if not under:
            tasks = {d["task_id"]: d for d in index.docs if d.get("type") == "task" and passes(query, d)}
            task_hits = [(tasks[i], sc) for i, sc in SEMANTIC.search_tasks(q, known=tasks, limit=FUSE_DEPTH)]
        if hits or task_hits:
            ranked = search.fuse([ranked, _by_similarity(hits, task_hits)[:FUSE_DEPTH]])
    return _phrase_first(ranked, query, index, keep)[:limit]


def task_status(task: dict) -> str:
    if not task.get("completed_at"):
        return "open"
    return "won't do" if task.get("wont_do") else "done"


def _result_href(r: dict) -> str:
    """Destination URL for a search result. Task results open the deck page on
    the tab matching the task's state, with that one task selected and
    highlighted (`?task=<id>`, handled by deck.js) rather than the bare _deck
    slug. By id, not title: titles can repeat or contain #words."""
    if r.get("type") == "task":
        tab = "completed" if r.get("status") == "done" else "open"
        return task_href(r["task_id"], tab, r.get("list"), r.get("year"))
    return url("page", r['slug'])
