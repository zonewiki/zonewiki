"""Semantic search: embeddings of page sections, searched by meaning.

Opt-in via LEAF_EMBEDDINGS (see from_env). Each page is split into sections
(chunk_page), each section is turned into a vector by an embedding model, and
a query is compared against every vector (cosine similarity, brute force).
That is plenty fast at wiki scale, so there is no vector database: the
vectors live in memory and in a rebuildable cache file, .leaf/index/embeddings.json
in the wiki directory (kept out of git via .git/info/exclude).

Vectors are content-addressed: a section's vector is keyed by a hash of its
text, so editing one section re-embeds only that section, and a page renamed
or touched without changes re-embeds nothing. Embedding runs in a background
thread (SemanticIndex.start); requests only poke it and query whatever is
ready, so a slow model never blocks a page view.

retrieval.py fuses these results with BM25 (search.fuse) and shows each page's
nearest neighbours as "Related" pages.

The model choice and its download live in embed_setup.py (run at image
build time), so editing this file doesn't re-download the model.
"""

import base64
import hashlib
import json
import math
import operator
import os
import re
import sys
import threading
from array import array
from pathlib import Path
from typing import Callable, Iterable

try:  # comes with fastembed; scoring a query is then one matrix product
    import numpy as np
except ImportError:  # e.g. a local venv without fastembed: plain Python, ~25x slower
    np = None

from embed_setup import DEFAULT_FASTEMBED_MODEL, parse_spec  # noqa: F401 (re-exported)
from mdtext import iter_fenced_lines


# Similarity floors per model, (search, related, tasks):
# - search: a query↔section similarity below this is not a hit. Vector search
#   always has a "nearest" page, so without a floor every query, even
#   gibberish, would fill the results. LEAF_EMBED_MIN_SCORE overrides it.
# - related: pages less similar than this (page↔page, each page the mean of
#   its sections) are not listed as related.
# - tasks: as search, for deck tasks. Their texts are a few words, and short
#   texts let nonsense score higher ("xqzt blorf" reached 0.27 against a task
#   title, 0.17 against page sections), so the floor sits higher.
# Measured on a test wiki: the floor sits above what nonsense queries and
# unrelated pages score, below what the right page scores. Models differ a
# lot (bge-small scores even nonsense at 0.55), so an unlisted model gets a
# guess; tune LEAF_EMBED_MIN_SCORE for it.
THRESHOLDS = {
    "jinaai/jina-embeddings-v2-base-de": (0.25, 0.25, 0.3),
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": (0.4, 0.3, 0.4),  # tasks: not measured
}
DEFAULT_THRESHOLDS = (0.4, 0.4, 0.4)

# A page with less prose than this neither gets a Related list nor appears
# in one; search still finds it. Prose is the letters and digits outside
# links (prose_chars): a page that is only a title, or a hub that is only a
# list of links, has a vector that is little more than its title or an
# average of everything it links to, so its "nearest" pages were noise.
RELATED_MIN_CHARS = 100

# What doesn't count as prose: [[wikilinks]], [labels] (chunks keep a link's
# label, its target is already gone), image alts, and bare URLs.
_LINKISH_RE = re.compile(r"\[\[[^\]]*\]\]|!?\[[^\]]*\]|https?://\S+|www\.\S+")
_PROSE_CHAR_RE = re.compile(r"[^\W_]")


def prose_chars(text: str) -> int:
    """Letters and digits in text outside links (see RELATED_MIN_CHARS)."""
    return len(_PROSE_CHAR_RE.findall(_LINKISH_RE.sub(" ", text)))

# Sections longer than this are split at paragraphs. Small models only read
# the first ~128–512 tokens of an input, so a long section's tail would be lost.
MAX_CHUNK_CHARS = 800

CACHE_VERSION = 1

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK_TARGET_RE = re.compile(r"(!?\[[^\]]*\])\([^)]*\)")


def log(msg: str) -> None:
    print(f"[semantic] {msg}", file=sys.stderr, flush=True)


# ── Chunking ─────────────────────────────────────────────────────────────────

def _split_long(text: str, max_chars: int) -> list[str]:
    """Split text at blank lines into pieces of at most max_chars (a single
    longer paragraph is cut at whitespace)."""
    pieces, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > max_chars:
            cut = para.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if cur and len(cur) + 2 + len(para) > max_chars:
            pieces.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def chunk_page(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split a page body into sections at its headings (outside code fences),
    long sections further at paragraphs. Each chunk starts with its heading,
    so a hit's excerpt shows where in the page it is. Link targets are
    dropped (the link text stays), as in the BM25 index."""
    return [f"{heading}\n{body}" if heading else body
            for heading, body, _level in chunk_sections(text, max_chars)]


def chunk_key(title: str, chunk: str) -> str:
    """A chunk's identity: the hash its vector is stored under. Retrieval
    (retrieval.py) uses it to match keyword hits with semantic ones."""
    return _hash(f"{title}\n{chunk}") if chunk else _hash(title)


def chunk_sections(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[tuple[str, str, int]]:
    """chunk_page's pieces as (heading, body, heading level); before the first
    heading that is ("", body, 0)."""
    sections: list[list[str]] = [[]]
    for _i, line, in_fence in iter_fenced_lines(text.split("\n")):
        if not in_fence and _HEADING_RE.match(line):
            sections.append([])
        sections[-1].append(line)
    chunks = []
    for lines in sections:
        heading, level = "", 0
        m = _HEADING_RE.match(lines[0]) if lines else None
        if m:
            heading, level = m.group(2), len(m.group(1))
            lines = lines[1:]
        body = _LINK_TARGET_RE.sub(r"\1", "\n".join(lines)).strip()
        if not body:
            continue
        for piece in _split_long(body, max_chars):
            chunks.append((heading, piece, level))
    return chunks


# ── Vectors ──────────────────────────────────────────────────────────────────

def _normalize(vec: Iterable[float]) -> array:
    v = array("f", vec)
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return array("f", (x / norm for x in v))


def _dot(a: array, b: array) -> float:
    return sum(map(operator.mul, a, b))


def _mean(vecs: list[array]) -> array | None:
    if not vecs:
        return None
    acc = [0.0] * len(vecs[0])
    for v in vecs:
        for i, x in enumerate(v):
            acc[i] += x
    return _normalize(acc)


def _encode(vec: array) -> str:
    return base64.b64encode(vec.tobytes()).decode("ascii")


def _decode(text: str) -> array:
    v = array("f")
    v.frombytes(base64.b64decode(text))
    return v


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ── Embedders ────────────────────────────────────────────────────────────────
# An embedder has a `name` (cache files are per model) and two methods:
# embed_docs(texts) and embed_query(text), returning plain float sequences.
# Some models want different prefixes for the two; fastembed adds them.

class FastEmbedder:
    """A local ONNX model via fastembed. Loaded on first use (seconds)."""

    def __init__(self, model: str = DEFAULT_FASTEMBED_MODEL):
        self.name = f"fastembed:{model}"
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(self.model_name,
                                            cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
            return self._model

    def embed_docs(self, texts: list[str]) -> list:
        return [list(v) for v in self._load().passage_embed(texts)]

    def embed_query(self, text: str) -> list:
        return list(next(iter(self._load().query_embed([text]))))


def embedder_from_spec(spec: str):
    """`fastembed` or `fastembed:<model>` (embed_setup.parse_spec); None when
    empty or off."""
    model = parse_spec(spec)
    return FastEmbedder(model) if model else None


# ── The index ────────────────────────────────────────────────────────────────

class SemanticIndex:
    """Section vectors of every page, kept fresh by a background worker.

    pages_fn() lists the current pages as (slug, path); prepare(content, slug)
    turns a file into (title, body text). Both come from retrieval.py. A page's
    sections are embedded as "title\\nsection", so each carries its page's
    topic along. tasks_fn(), when given, lists deck tasks as (id, text); each
    task is one vector, found by search_tasks.
    """

    def __init__(self, embedder, cache_path: Path,
                 pages_fn: Callable[[], Iterable[tuple[str, Path]]],
                 prepare: Callable[[str, str], tuple[str, str]],
                 tasks_fn: Callable[[], Iterable[tuple[str, str]]] | None = None,
                 min_score: float | None = None,
                 related_min_score: float | None = None,
                 related_min_chars: int = RELATED_MIN_CHARS,
                 task_min_score: float | None = None):
        self.embedder = embedder
        self.cache_path = cache_path
        self.pages_fn = pages_fn
        self.prepare = prepare
        self.tasks_fn = tasks_fn
        search_floor, related_floor, task_floor = THRESHOLDS.get(
            getattr(embedder, "model_name", ""), DEFAULT_THRESHOLDS)
        self.min_score = search_floor if min_score is None else min_score
        self.related_min_score = related_floor if related_min_score is None else related_min_score
        self.related_min_chars = related_min_chars
        self.task_min_score = task_floor if task_min_score is None else task_min_score
        self._lock = threading.Lock()      # guards the state below
        self._sync_lock = threading.Lock()  # one sync at a time
        # slug -> {"mtime", "chunks": [[hash, text], …]}
        self._pages: dict[str, dict] = {}
        self._vecs: dict[str, array] = {}   # chunk hash -> unit vector
        self._page_vecs: dict[str, array] = {}  # slug -> mean of its chunks
        self._tasks: dict[str, str] = {}        # task id -> hash of its text
        self._queries: dict[str, array] = {}    # small cache of query vectors
        # Scoring: rows and (with NumPy) matrices per index generation, and
        # the scores of recent queries, so one search scores each vector once.
        self._gen = 0
        self._rows_gen = -1
        self._chunk_rows: list[tuple[str, str, str]] = []  # (slug, hash, text)
        self._task_rows: list[tuple[str, str]] = []        # (task id, hash)
        self._chunk_mat = self._task_mat = None
        self._scores: dict[str, tuple[int, list[float], list[float]]] = {}
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._failed = False
        self.ready = False
        self._load_cache()

    # Persistence

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("version") != CACHE_VERSION or data.get("model") != self.embedder.name:
            return
        try:
            self._vecs = {h: _decode(v) for h, v in data["vectors"].items()}
            self._pages = data["pages"]
            self._tasks = dict(data.get("tasks") or {})
        except (KeyError, TypeError, ValueError):
            self._vecs, self._pages, self._tasks = {}, {}, {}
            return
        self._page_vecs = {s: v for s, p in self._pages.items()
                           if (v := self._mean_of(p)) is not None}
        self.ready = bool(self._pages)

    def _save_cache(self) -> None:
        data = {
            "version": CACHE_VERSION,
            "model": self.embedder.name,
            "pages": self._pages,
            "tasks": self._tasks,
            "vectors": {h: _encode(v) for h, v in self._vecs.items()},
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as e:
            log(f"could not write {self.cache_path}: {e}")

    def _mean_of(self, page: dict) -> array | None:
        return _mean([self._vecs[h] for h, _t in page["chunks"] if h in self._vecs])

    # Keeping up with the pages

    def sync(self) -> bool:
        """Bring the vectors in line with the pages on disk: embed new and
        changed sections, drop deleted pages. Returns whether anything changed."""
        with self._sync_lock:
            return self._sync()

    def _sync(self) -> bool:
        current = {}
        for slug, path in self.pages_fn():
            try:
                current[slug] = (path, path.stat().st_mtime)
            except OSError:
                continue
        pages = {s: p for s, p in self._pages.items() if s in current}
        changed = len(pages) != len(self._pages)
        todo: dict[str, str] = {}   # hash -> text to embed
        for slug, (path, mtime) in current.items():
            old = pages.get(slug)
            if old and old["mtime"] == mtime:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            title, body = self.prepare(content, slug)
            chunks = [[chunk_key(title, c), c] for c in chunk_page(body)]
            if not chunks and title:
                chunks = [[chunk_key(title, ""), ""]]
            for h, c in chunks:
                if h not in self._vecs:
                    todo[h] = f"{title}\n{c}".strip()
            pages[slug] = {"mtime": mtime, "chunks": chunks}
            changed = True
        # Tasks are short, so they are simply re-hashed each time: a changed
        # task gets a new hash and only that one is embedded.
        tasks: dict[str, str] = {}
        for task_id, text in (self.tasks_fn() if self.tasks_fn else ()):
            if text.strip():
                tasks[task_id] = h = _hash(text)
                if h not in self._vecs:
                    todo[h] = text
        changed = changed or tasks != self._tasks
        new_vecs: dict[str, array] = {}
        if todo:
            hashes = list(todo)
            try:
                for i in range(0, len(hashes), 64):
                    batch = hashes[i:i + 64]
                    for h, v in zip(batch, self.embedder.embed_docs([todo[h] for h in batch])):
                        new_vecs[h] = _normalize(v)
                if self._failed:
                    log("embedding works again")
                    self._failed = False
            except Exception as e:  # model missing, broken, …: keep BM25 working
                if not self._failed:
                    log(f"embedding failed, search falls back to keywords: {e}")
                    self._failed = True
                # Pages whose sections couldn't be embedded are retried next time.
                for slug, p in list(pages.items()):
                    if any(h not in self._vecs and h not in new_vecs for h, _ in p["chunks"]):
                        if slug in self._pages:
                            pages[slug] = self._pages[slug]
                        else:
                            del pages[slug]
        if not changed and not new_vecs:
            return False
        # A task whose embedding failed keeps its hash and is retried next time.
        used = {h for p in pages.values() for h, _ in p["chunks"]} | set(tasks.values())
        vecs = {h: v for h, v in {**self._vecs, **new_vecs}.items() if h in used}
        page_vecs = {}
        for slug, p in pages.items():
            if slug in self._page_vecs and self._pages.get(slug) is p:
                page_vecs[slug] = self._page_vecs[slug]
            else:
                m = _mean([vecs[h] for h, _ in p["chunks"] if h in vecs])
                if m is not None:
                    page_vecs[slug] = m
        with self._lock:
            self._pages, self._vecs, self._page_vecs, self._tasks = pages, vecs, page_vecs, tasks
            self._gen += 1
            self.ready = True
        self._save_cache()
        return True

    def start(self) -> None:
        """Run sync in a daemon thread, now and after every poke()."""
        if self._worker:
            return

        def run():
            first = True
            while True:
                try:
                    self.sync()
                    if first:
                        sections = sum(len(p["chunks"]) for p in self._pages.values())
                        log(f"{len(self._pages)} pages ({sections} sections) and "
                            f"{len(self._tasks)} tasks indexed")
                except Exception as e:
                    log(f"sync failed: {e}")
                first = False
                self._wake.wait()
                self._wake.clear()

        self._worker = threading.Thread(target=run, name="leaf-semantic", daemon=True)
        self._worker.start()

    def poke(self) -> None:
        """Ask the worker to check for changed pages (non-blocking)."""
        self._wake.set()

    # Queries

    def _query_vec(self, query: str) -> array | None:
        key = query.strip().lower()
        v = self._queries.get(key)
        if v is None:
            try:
                v = _normalize(self.embedder.embed_query(query))
            except Exception as e:
                if not self._failed:
                    log(f"query embedding failed, search falls back to keywords: {e}")
                    self._failed = True
                return None
            if len(self._queries) >= 128:
                self._queries.clear()
            self._queries[key] = v
        return v

    def _scored(self, query: str):
        """(chunk rows, their scores, task rows, their scores) for the query,
        scoring every vector once per query and index generation: a search
        asks several times (pages, tasks, sections for the links)."""
        key = query.strip().lower()
        with self._lock:
            gen, pages, vecs, tasks = self._gen, self._pages, self._vecs, self._tasks
            if self._rows_gen != gen:
                self._chunk_rows = [(slug, h, text) for slug, p in pages.items()
                                    for h, text in p["chunks"] if h in vecs]
                self._task_rows = [(i, h) for i, h in tasks.items() if h in vecs]
                if np is not None:
                    self._chunk_mat = np.array([vecs[h] for _s, h, _t in self._chunk_rows] or
                                               np.zeros((0, 0)), dtype=np.float32)
                    self._task_mat = np.array([vecs[h] for _i, h in self._task_rows] or
                                              np.zeros((0, 0)), dtype=np.float32)
                self._rows_gen, self._scores = gen, {}
            chunk_rows, task_rows = self._chunk_rows, self._task_rows
            chunk_mat, task_mat = self._chunk_mat, self._task_mat
            cached = self._scores.get(key)
        if cached and cached[0] == gen:
            return chunk_rows, cached[1], task_rows, cached[2]
        q = self._query_vec(query)
        if q is None:
            return [], [], [], []
        if np is not None:
            qv = np.asarray(q, dtype=np.float32)
            chunk_scores = (chunk_mat @ qv).tolist() if len(chunk_rows) else []
            task_scores = (task_mat @ qv).tolist() if len(task_rows) else []
        else:
            chunk_scores = [_dot(q, vecs[h]) for _s, h, _t in chunk_rows]
            task_scores = [_dot(q, vecs[h]) for _i, h in task_rows]
        with self._lock:
            if len(self._scores) >= 32:
                self._scores.clear()
            self._scores[key] = (gen, chunk_scores, task_scores)
        return chunk_rows, chunk_scores, task_rows, task_scores

    def _chunk_scores(self, query: str, known: Iterable[str] | None):
        """(slug, chunk key, score, text) of every section at least min_score
        similar to the query, in no particular order."""
        if not self.ready or not query.strip():
            return []
        rows, scores, _tr, _ts = self._scored(query)
        known = set(known) if known is not None else None
        return [(slug, h, sc, text) for (slug, h, text), sc in zip(rows, scores)
                if sc >= self.min_score and (known is None or slug in known)]

    def search(self, query: str, known: Iterable[str] | None = None,
               limit: int = 30) -> list[tuple[str, float, str]]:
        """Pages whose best section is at least min_score similar to the query,
        best first, as (slug, score, that section's text)."""
        best: dict[str, tuple[float, str]] = {}
        for slug, _h, s, text in self._chunk_scores(query, known):
            if s > best.get(slug, (-1.0, ""))[0]:
                best[slug] = (s, text)
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:limit]
        return [(slug, s, text) for slug, (s, text) in ranked]

    def search_tasks(self, query: str, known: Iterable[str] | None = None,
                     limit: int = 30) -> list[tuple[str, float]]:
        """(task id, score) of the tasks at least task_min_score similar to
        the query, best first."""
        if not self.ready or not query.strip():
            return []
        _cr, _cs, rows, scores = self._scored(query)
        known = set(known) if known is not None else None
        hits = [(sc, task_id) for (task_id, _h), sc in zip(rows, scores)
                if sc >= self.task_min_score and (known is None or task_id in known)]
        hits.sort(key=lambda x: -x[0])
        return [(task_id, s) for s, task_id in hits[:limit]]

    def search_chunks(self, query: str, known: Iterable[str] | None = None,
                      limit: int = 30) -> list[tuple[str, float]]:
        """(key, score) of the sections most similar to the query, best first;
        several may come from one page (retrieval for an LLM wants passages)."""
        hits = sorted(self._chunk_scores(query, known), key=lambda x: -x[2])
        return [(h, s) for _slug, h, s, _t in hits[:limit]]

    def _has_content(self, page: dict | None) -> bool:
        return bool(page) and sum(prose_chars(t) for _h, t in page["chunks"]) >= self.related_min_chars

    def related(self, slug: str, known: Iterable[str] | None = None,
                exclude: Iterable[str] = (), limit: int = 5) -> list[tuple[str, float]]:
        """The pages most similar to slug (by their mean vectors), best first.
        Pages with little prose (related_min_chars) take no part, either way."""
        with self._lock:
            pages, page_vecs = self._pages, self._page_vecs
        me = page_vecs.get(slug)
        if me is None or not self._has_content(pages.get(slug)):
            return []
        return self._nearest(me, pages, page_vecs, set(exclude) | {slug}, known, limit)

    def related_to_text(self, slug: str, content: str, known: Iterable[str] | None = None,
                        exclude: Iterable[str] = (), limit: int = 5) -> list[tuple[str, float]]:
        """related() for a page's text, which the index may not have yet (a
        page just written; the worker catches up later): its sections are
        embedded now, those the index knows reused. [] for little prose, or
        when embedding fails."""
        if not self.ready:
            return []
        title, body = self.prepare(content, slug)
        chunks = chunk_page(body)
        if sum(prose_chars(c) for c in chunks) < self.related_min_chars:
            return []
        with self._lock:
            pages, vecs, page_vecs = self._pages, self._vecs, self._page_vecs
        keyed = [(chunk_key(title, c), f"{title}\n{c}".strip()) for c in chunks]
        missing = [(h, t) for h, t in keyed if h not in vecs]
        try:
            new = {h: _normalize(v) for (h, _t), v in
                   zip(missing, self.embedder.embed_docs([t for _h, t in missing]) if missing else [])}
        except Exception as e:
            log(f"embedding failed, no related pages for {slug}: {e}")
            return []
        me = _mean([vecs[h] if h in vecs else new[h] for h, _t in keyed])
        if me is None:
            return []
        return self._nearest(me, pages, page_vecs, set(exclude) | {slug}, known, limit)

    def _nearest(self, me: array, pages: dict, page_vecs: dict, skip: set[str],
                 known: Iterable[str] | None, limit: int) -> list[tuple[str, float]]:
        known = set(known) if known is not None else None
        scored = []
        for other, v in page_vecs.items():
            if other in skip or (known is not None and other not in known):
                continue
            if not self._has_content(pages.get(other)):
                continue
            s = _dot(me, v)
            if s >= self.related_min_score:
                scored.append((other, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]


def exclude_from_git(wiki_dir: Path, entry: str = "/.leaf/") -> None:
    """List the .leaf dir (cache, secret key) in .git/info/exclude (not .gitignore, which would
    itself show up as an uncommitted change)."""
    info = wiki_dir / ".git" / "info"
    if not info.parent.is_dir():
        return
    exclude = info / "exclude"
    try:
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if entry not in text.split("\n"):
            info.mkdir(exist_ok=True)
            exclude.write_text(text + ("" if not text or text.endswith("\n") else "\n")
                               + entry + "\n", encoding="utf-8")
    except OSError as e:
        log(f"could not update {exclude}: {e}")


def from_env(wiki_dir: Path, pages_fn, prepare, tasks_fn=None) -> SemanticIndex | None:
    """The SemanticIndex LEAF_EMBEDDINGS asks for, or None (keywords only).
    A missing dependency or a bad setting is logged, not fatal."""
    try:
        embedder = embedder_from_spec(os.environ.get("LEAF_EMBEDDINGS", ""))
    except ValueError as e:
        log(str(e))
        return None
    if embedder is None:
        return None
    try:
        import fastembed  # noqa: F401
    except ImportError:
        log("LEAF_EMBEDDINGS is set but fastembed is not installed "
            "(pip install fastembed); using keyword search only")
        return None
    min_score = None
    if os.environ.get("LEAF_EMBED_MIN_SCORE"):
        try:
            min_score = float(os.environ["LEAF_EMBED_MIN_SCORE"])
        except ValueError:
            log("LEAF_EMBED_MIN_SCORE is not a number; using the default")
    exclude_from_git(wiki_dir)
    log(f"semantic search on, model {embedder.name}")
    return SemanticIndex(embedder, wiki_dir / ".leaf" / "index" / "embeddings.json",
                         pages_fn, prepare, tasks_fn=tasks_fn, min_score=min_score)
