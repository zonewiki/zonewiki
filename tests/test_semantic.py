"""Tests for semantic search (src/embed.py) and how server.py uses it: hybrid
ranking in search (search.fuse) and the Related section on page view.

A fake embedder stands in for the model: it maps words to a few "concepts",
so texts sharing no words can still be similar, deterministically and
without fastembed.
"""

import json
import os
import re
import time

import pytest

from tests.conftest import HOST, write_page
import deckstore
import embed
import embed_setup
import search
import pages
import retrieval

# word -> concept axis; other words don't count.
CONCEPTS = {
    "backup": 0, "remote": 0, "push": 0, "copy": 0, "git": 0,
    "car": 1, "repair": 1, "werkstatt": 1, "mechaniker": 1, "bremsen": 1, "golf": 1,
    "sleep": 2, "bed": 2, "tired": 2, "evening": 2, "routine": 2,
    "money": 3, "budget": 3, "rent": 3, "savings": 3, "insurance": 3,
}
DIM = 4


class FakeEmbedder:
    name = "fake:concepts"
    model_name = "fake"

    def __init__(self):
        self.calls: list[str] = []   # every text embedded as a document
        self.fail = False

    def _vec(self, text):
        v = [0.0] * DIM
        for w in search.tokenize(text):
            if w in CONCEPTS:
                v[CONCEPTS[w]] += 1.0
        return v

    def embed_docs(self, texts):
        if self.fail:
            raise RuntimeError("model unavailable")
        self.calls.extend(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        if self.fail:
            raise RuntimeError("model unavailable")
        return self._vec(text)


def make_index(wiki, embedder=None, **kw):
    return embed.SemanticIndex(
        embedder or FakeEmbedder(),
        wiki / ".leaf" / "index" / "embeddings.json",
        lambda: [(s, p) for p, s in pages.iter_pages()],
        retrieval._semantic_text,
        tasks_fn=kw.pop("tasks_fn", lambda: [(d["task_id"], d["excerpt_text"])
                                             for d in deckstore.task_search_docs()]),
        min_score=kw.pop("min_score", 0.5),
        task_min_score=kw.pop("task_min_score", 0.5),
        related_min_score=kw.pop("related_min_score", 0.5),
        # The test pages are short; tests of the thin-page rule pass their own.
        related_min_chars=kw.pop("related_min_chars", 0),
        **kw,
    )


@pytest.fixture()
def semantic(wiki_dir, monkeypatch):
    write_page(wiki_dir, "git-remote", "# Git remote\n\nSet LEAF_GIT_REMOTE, then push to copy every commit.\n")
    write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n")
    write_page(wiki_dir, "evening", "# Evening routine\n\nNo screens late, bed by eleven.\n")
    write_page(wiki_dir, "money", "# Budget\n\nRent and savings.\n")
    idx = make_index(wiki_dir)
    idx.sync()
    monkeypatch.setattr(retrieval, "SEMANTIC", idx)
    return idx


# ── Chunking ─────────────────────────────────────────────────────────────────

class TestChunkPage:
    def test_splits_at_headings_and_keeps_them(self):
        chunks = embed.chunk_page("Intro text.\n\n## Setup\n\nDo this.\n\n### Detail\n\nAnd that.\n")
        assert chunks == ["Intro text.", "Setup\nDo this.", "Setup › Detail\nAnd that."]

    def test_a_section_carries_the_headings_it_sits_under(self):
        text = ("## Problems\n\n### Windows\n\nCrashes.\n\n#### Proxy\n\nSet.\n\n"
                "### Linux\n\nFine.\n\n## Next steps\n\nShip.\n")
        assert [(h, p) for h, _b, _l, p in embed.chunk_sections(text)] == [
            ("Windows", "Problems › Windows"), ("Proxy", "Problems › Windows › Proxy"),
            ("Linux", "Problems › Linux"), ("Next steps", "Next steps")]
        assert embed.chunk_page(text)[0] == "Problems › Windows\nCrashes."

    def test_heading_inside_code_fence_is_not_a_section(self):
        chunks = embed.chunk_page("## Shell\n\n```sh\n# a comment\necho hi\n```\n")
        assert len(chunks) == 1
        assert "# a comment" in chunks[0]

    def test_fence_closes_only_on_its_own_kind(self):
        # Regression: any backtick line ended a fence, so a ```python line
        # inside a ```` block closed it, and a "# comment" after it became a
        # section of its own. The rendered page (mdtext) never split there.
        text = "## Example\n\n````md\n```python\n# not a heading\n```\n````\n\n## Next\n\nText.\n"
        assert [h for h, _b, _l, _p in embed.chunk_sections(text)] == ["Example", "Next"]

    def test_empty_sections_are_skipped(self):
        assert embed.chunk_page("## Empty\n\n## Full\n\nText.\n") == ["Full\nText."]

    def test_long_section_split_at_paragraphs(self):
        body = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(10))
        chunks = embed.chunk_page(f"## Long\n\n{body}", max_chars=400)
        assert len(chunks) > 1
        assert all(c.startswith("Long\n") for c in chunks)
        assert all(len(c) <= 400 + len("Long\n") for c in chunks)
        assert "".join(chunks).count("Paragraph") == 10

    def test_one_huge_paragraph_is_cut(self):
        chunks = embed.chunk_page("x " * 1000, max_chars=300)
        assert len(chunks) > 1 and all(len(c) <= 300 for c in chunks)

    def test_link_targets_dropped(self):
        chunks = embed.chunk_page("See [the docs](https://example.com/page) and ![img](/_assets/a.png).")
        assert "example.com" not in chunks[0] and "_assets" not in chunks[0]
        assert "the docs" in chunks[0]


class TestSemanticText:
    def test_deck_and_toc_directives_dropped(self):
        # Pages holding only a deck embed were each other's closest match.
        title, body = retrieval._semantic_text(
            "# Leaf\n\n[[toc]]\n\nNotes [[Deck tag=leaf view=open]] here.\n\n[[deck]]\n", "leaf")
        assert "deck" not in body.lower() and "toc" not in body
        assert "Notes  here." in body

    def test_only_a_deck_embed_leaves_no_body(self):
        assert retrieval._semantic_text("# Leaf\n\n[[Deck tag=leaf]]\n", "leaf")[1].strip() == ""

    def test_frontmatter_and_h1_dropped_tldr_and_tags_lead(self):
        title, body = retrieval._semantic_text(
            "---\ntags: [cars, repair]\ntldr: Notes on the car\n---\n# Golf\n\nBody.\n", "golf")
        assert title == "Golf"
        assert body.startswith("Notes on the car\ncars repair")
        assert "# Golf" not in body and "---" not in body


# ── Fusion and excerpts ─────────────────────────────────────────────────────

class TestFuse:
    def test_item_in_both_lists_wins(self):
        a, b, c = {"n": "a"}, {"n": "b"}, {"n": "c"}
        assert search.fuse([[a, b], [c, b]])[0] is b

    def test_ties_keep_first_list_order(self):
        a, b = {"n": "a"}, {"n": "b"}
        assert search.fuse([[a], [b]]) == [a, b]

    def test_no_duplicates(self):
        a = {"n": "a"}
        assert search.fuse([[a], [a]]) == [a]

    def test_custom_key(self):
        assert search.fuse([["x", "y"], ["y"]], key=str) == ["y", "x"]


# ── The index ────────────────────────────────────────────────────────────────

class TestSemanticIndex:
    def test_finds_by_meaning(self, semantic):
        hits = semantic.search("car repair")
        assert hits[0][0] == "golf"
        assert hits[0][2].startswith("Werkstatt")

    def test_min_score_drops_weak_hits(self, semantic):
        assert semantic.search("xqzt blorf") == []

    def test_known_filters_pages(self, semantic):
        assert semantic.search("car repair", known={"money"}) == []

    def test_not_ready_before_first_sync(self, wiki_dir):
        idx = make_index(wiki_dir)
        assert not idx.ready
        assert idx.search("car") == [] and idx.related("index") == []

    def test_unchanged_pages_are_not_reembedded(self, semantic, wiki_dir):
        semantic.embedder.calls.clear()
        assert semantic.sync() is False
        assert semantic.embedder.calls == []

    def test_editing_one_section_reembeds_only_it(self, wiki_dir):
        path = write_page(wiki_dir, "long", "# Long\n\n## One\n\nFirst.\n\n## Two\n\nSecond.\n")
        idx = make_index(wiki_dir)
        idx.sync()
        idx.embedder.calls.clear()
        path.write_text("# Long\n\n## One\n\nFirst.\n\n## Two\n\nChanged.\n", encoding="utf-8")
        _bump_mtime(path)
        assert idx.sync() is True
        assert idx.embedder.calls == ["Long\nTwo\nChanged."]

    def test_deleted_page_dropped(self, semantic, wiki_dir):
        (wiki_dir / "golf.md").unlink()
        semantic.sync()
        assert semantic.search("car repair") == []
        assert "golf" not in semantic._page_vecs

    def test_cache_survives_restart(self, semantic, wiki_dir):
        fresh = make_index(wiki_dir)
        assert fresh.ready
        assert fresh.search("car repair")[0][0] == "golf"
        assert fresh.sync() is False
        assert fresh.embedder.calls == []

    def test_cache_of_an_older_chunking_rechunks_every_page_and_keeps_its_vectors(self, semantic, wiki_dir):
        cache = wiki_dir / ".leaf" / "index" / "embeddings.json"
        data = json.loads(cache.read_text(encoding="utf-8"))
        data["chunking"] = embed.CHUNKING - 1
        data["pages"]["golf"]["chunks"] = [["stale-key", "Werkstatt\nTermin beim Mechaniker"]]
        cache.write_text(json.dumps(data), encoding="utf-8")
        fresh = make_index(wiki_dir)
        assert not fresh.ready
        fresh.sync()
        assert fresh.embedder.calls == []   # the texts are unchanged: their vectors reused
        keys = {d["key"] for d in retrieval._page_chunk_docs()}
        hits = fresh.search_chunks("car repair")
        assert hits and all(k in keys for k, _s in hits)

    def test_cache_of_another_model_ignored(self, semantic, wiki_dir):
        other = FakeEmbedder()
        other.name = "fake:other"
        fresh = make_index(wiki_dir, other)
        assert not fresh.ready
        fresh.sync()
        assert other.calls  # everything embedded again

    def test_corrupt_cache_ignored(self, wiki_dir):
        cache = wiki_dir / ".leaf" / "index" / "embeddings.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("{not json", encoding="utf-8")
        idx = make_index(wiki_dir)
        assert not idx.ready
        idx.sync()
        assert json.loads(cache.read_text())["model"] == "fake:concepts"

    def test_embedding_failure_keeps_old_vectors_and_retries(self, semantic, wiki_dir):
        semantic.embedder.fail = True
        path = write_page(wiki_dir, "garage", "# Garage\n\nMechaniker und Bremsen.\n")
        semantic.sync()
        assert "golf" in semantic._page_vecs and "garage" not in semantic._pages
        semantic.embedder.fail = False
        _bump_mtime(path)
        semantic.sync()
        assert {s for s, _sc, _t in semantic.search("car repair")} == {"golf", "garage"}

    def test_query_failure_returns_nothing(self, semantic):
        semantic.embedder.fail = True
        semantic._queries.clear()
        assert semantic.search("car repair") == []

    def test_related_by_meaning(self, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        assert [s for s, _ in semantic.related("golf")] == ["brakes"]
        assert semantic.related("golf", exclude={"brakes"}) == []

    def test_related_to_a_text_not_indexed_yet(self, semantic, wiki_dir):
        text = "# Brakes\n\nBremsen beim Mechaniker.\n"
        assert [s for s, _ in semantic.related_to_text("brakes", text)] == ["golf"]
        before = len(semantic.embedder.calls)
        semantic.related_to_text("brakes", text)
        assert len(semantic.embedder.calls) == before + 1      # its section, not the whole wiki
        assert [s for s, _ in semantic.related_to_text("x", "# Sleep\n\nTired in bed.\n")] == ["evening"]
        assert semantic.related_to_text("x", "# Notes\n\nNothing in particular.\n") == []
        semantic.embedder.fail = True
        assert semantic.related_to_text("brakes", text) == []

    def test_thin_pages_take_no_part_in_related(self, wiki_dir):
        long = "Werkstatt beim Mechaniker, Bremsen quietschen, Termin am Dienstag. " * 3
        write_page(wiki_dir, "golf", f"# Golf\n\n{long}\n")
        write_page(wiki_dir, "brakes", f"# Brakes\n\n{long}\n")
        write_page(wiki_dir, "garage", "# Garage\n\nMechaniker.\n")   # thin, same topic
        idx = make_index(wiki_dir, related_min_chars=100)
        idx.sync()
        assert [s for s, _ in idx.related("golf")] == ["brakes"]    # garage is not offered
        assert idx.related("garage") == []                         # and gets no list
        assert idx.search("car repair")                            # but search finds them all
        assert "garage" in {s for s, _sc, _t in idx.search("car repair")}

    def test_link_hubs_take_no_part_in_related(self, wiki_dir):
        long = "Werkstatt beim Mechaniker, Bremsen quietschen, Termin am Dienstag. " * 3
        write_page(wiki_dir, "golf", f"# Golf\n\n{long}\n")
        write_page(wiki_dir, "brakes", f"# Brakes\n\n{long}\n")
        hub = "\n".join(f"- [[Werkstatt Mechaniker {i}]]\n- [Bremsen {i}](golf-{i}.md)" for i in range(10))
        write_page(wiki_dir, "index", f"# Home\n\n{hub}\n- https://example.com/werkstatt/mechaniker\n")
        idx = make_index(wiki_dir, related_min_chars=100)
        idx.sync()
        assert idx.related("index") == []
        assert [s for s, _ in idx.related("golf")] == ["brakes"]

    def test_prose_chars(self):
        assert embed.prose_chars("- [[People and Teams]]\n- [Projects]\n- ![img]\n- https://x.org/a") == 0
        assert embed.prose_chars("See [[State]] for the plan, ok?") == len("Seefortheplanok")
        assert embed.prose_chars("Werkstatt_2026") == len("Werkstatt2026")

    def test_default_cut_off(self, wiki_dir):
        assert make_index(wiki_dir, related_min_chars=embed.RELATED_MIN_CHARS).related_min_chars == 100

    def test_worker_picks_up_changes(self, wiki_dir):
        idx = make_index(wiki_dir)
        idx.start()
        _wait(lambda: idx.ready)
        write_page(wiki_dir, "golf", "# Golf\n\nWerkstatt.\n")
        idx.poke()
        _wait(lambda: idx.search("car repair"))
        assert idx.search("car repair")[0][0] == "golf"


def _bump_mtime(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while not cond():
        assert time.time() < end, "timed out"
        time.sleep(0.02)


class TestTaskVectors:
    TASKS = {"t1": "Book the Werkstatt", "t2": "Buy milk", "t3": "Evening routine: bed early"}

    def index(self, wiki_dir, tasks, **kw):
        return make_index(wiki_dir, tasks_fn=lambda: list(tasks.items()), **kw)

    def test_tasks_found_by_meaning(self, wiki_dir):
        idx = self.index(wiki_dir, self.TASKS)
        idx.sync()
        assert [i for i, _s in idx.search_tasks("car repair")] == ["t1"]
        assert idx.search_tasks("xqzt") == []
        assert idx.search_tasks("car repair", known={"t2"}) == []

    def test_only_changed_tasks_reembedded(self, wiki_dir):
        tasks = dict(self.TASKS)
        idx = self.index(wiki_dir, tasks)
        idx.sync()
        idx.embedder.calls.clear()
        tasks["t2"] = "Buy oat milk"
        del tasks["t3"]
        assert idx.sync() is True
        assert idx.embedder.calls == ["Buy oat milk"]
        assert set(idx._tasks) == {"t1", "t2"}

    def test_unchanged_tasks_cost_nothing(self, wiki_dir):
        idx = self.index(wiki_dir, self.TASKS)
        idx.sync()
        idx.embedder.calls.clear()
        assert idx.sync() is False and idx.embedder.calls == []

    def test_cached_across_restarts(self, wiki_dir):
        self.index(wiki_dir, self.TASKS).sync()
        fresh = self.index(wiki_dir, self.TASKS)
        assert fresh.sync() is False and fresh.embedder.calls == []
        assert [i for i, _s in fresh.search_tasks("car repair")] == ["t1"]

    def test_own_floor(self, wiki_dir):
        idx = self.index(wiki_dir, {"t1": "Werkstatt and milk and bed"}, task_min_score=0.9)
        idx.sync()
        assert idx.search_tasks("car repair") == []    # similar, but under the task floor
        assert idx.task_min_score == 0.9 and idx.min_score == 0.5

    def test_failed_task_retried(self, wiki_dir):
        idx = self.index(wiki_dir, self.TASKS)
        idx.embedder.fail = True
        idx.sync()
        assert idx.search_tasks("car repair") == []
        idx.embedder.fail = False
        idx._queries.clear()
        idx.sync()
        assert [i for i, _s in idx.search_tasks("car repair")] == ["t1"]


class TestConfig:
    def test_spec_parsing(self):
        assert embed.embedder_from_spec("") is None
        assert embed.embedder_from_spec("off") is None
        e = embed.embedder_from_spec("fastembed")
        assert e.model_name == embed.DEFAULT_FASTEMBED_MODEL
        assert embed.embedder_from_spec("1").model_name == embed.DEFAULT_FASTEMBED_MODEL
        assert embed.embedder_from_spec("fastembed:BAAI/bge-small-en-v1.5").model_name == "BAAI/bge-small-en-v1.5"
        with pytest.raises(ValueError):
            embed.embedder_from_spec("ollama:nomic-embed-text")   # removed: fastembed only
        with pytest.raises(ValueError):
            embed.embedder_from_spec("bogus:x")

    def test_thresholds_per_model(self, wiki_dir):
        e = FakeEmbedder()
        e.model_name = embed.DEFAULT_FASTEMBED_MODEL
        idx = embed.SemanticIndex(e, wiki_dir / "c.json", lambda: [], retrieval._semantic_text)
        assert (idx.min_score, idx.related_min_score, idx.task_min_score) == \
            embed.THRESHOLDS[embed.DEFAULT_FASTEMBED_MODEL]

    def test_setup_parses_like_the_server(self):
        assert embed_setup.parse_spec("") is None
        assert embed_setup.parse_spec("fastembed") == embed.DEFAULT_FASTEMBED_MODEL
        assert embed_setup.parse_spec(" fastembed: BAAI/bge-small-en-v1.5 ") == "BAAI/bge-small-en-v1.5"

    @pytest.mark.parametrize("spec", ["", "off"])
    def test_setup_installs_nothing_when_off(self, spec, monkeypatch):
        monkeypatch.setenv("LEAF_EMBEDDINGS", spec)
        monkeypatch.setattr(embed_setup.subprocess, "run",
                            lambda *a, **k: pytest.fail("nothing to install"))
        embed_setup.prepare()

    def test_scoring_without_numpy_matches(self, semantic, monkeypatch):
        # The plain-Python fallback (no NumPy, e.g. a local venv) scores the same.
        with_np = semantic.search("car repair")
        monkeypatch.setattr(embed, "np", None)
        semantic._rows_gen = -1          # rebuild rows without matrices
        semantic._scores.clear()
        assert [(s, round(sc, 5)) for s, sc, _t in semantic.search("car repair")] == \
            [(s, round(sc, 5)) for s, sc, _t in with_np]

    def test_setup_downloads_the_model(self, monkeypatch):
        import sys
        import types
        calls = []
        fake = types.ModuleType("fastembed")

        class TextEmbedding:
            def __init__(self, name, cache_dir=None):
                calls.append(("load", name, cache_dir))

            def query_embed(self, texts):
                calls.append(("embed", texts))
                return iter([[0.0]])

        fake.TextEmbedding = TextEmbedding
        monkeypatch.setitem(sys.modules, "fastembed", fake)
        monkeypatch.setattr(embed_setup.subprocess, "run", lambda cmd, **k: calls.append(("pip", cmd[-1])))
        monkeypatch.setenv("LEAF_EMBEDDINGS", "fastembed:BAAI/bge-small-en-v1.5")
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/opt/fastembed")
        embed_setup.prepare()
        assert calls == [("pip", "fastembed"), ("load", "BAAI/bge-small-en-v1.5", "/opt/fastembed"),
                         ("embed", ["warm up"])]

    def test_containerfile_copies_only_the_setup_before_the_download(self):
        # The model layer must not depend on embed.py, or every change to the
        # search code downloads the model again.
        from pathlib import Path
        text = (Path(__file__).parent.parent / "Containerfile").read_text()
        before = text[:text.index("RUN python3 embed_setup.py")]
        assert "COPY src/embed_setup.py ." in before
        assert "COPY src/ ." not in before and "embed.py ." not in before

    def test_off_by_default(self, monkeypatch, wiki_dir):
        monkeypatch.delenv("LEAF_EMBEDDINGS", raising=False)
        assert embed.from_env(wiki_dir, lambda: [], retrieval._semantic_text) is None

    def test_startup_starts_the_worker(self, monkeypatch, wiki_dir):
        # The index LEAF_EMBEDDINGS asks for is set up at startup and its
        # worker started (retrieval.start_semantic, called by server.py).
        started = []

        class Index:
            def start(self):
                started.append(self)
        monkeypatch.setattr(embed, "from_env", lambda *a, **k: Index())
        monkeypatch.setattr(retrieval, "SEMANTIC", None)
        retrieval.start_semantic()
        assert started == [retrieval.SEMANTIC]

    def test_bad_setting_is_not_fatal(self, monkeypatch, wiki_dir):
        monkeypatch.setenv("LEAF_EMBEDDINGS", "bogus:x")
        assert embed.from_env(wiki_dir, lambda: [], retrieval._semantic_text) is None

    def test_cache_dir_excluded_from_git(self, wiki_dir):
        (wiki_dir / ".git" / "info").mkdir(parents=True)
        (wiki_dir / ".git" / "info" / "exclude").write_text("# *~", encoding="utf-8")
        embed.exclude_from_git(wiki_dir)
        embed.exclude_from_git(wiki_dir)
        assert (wiki_dir / ".git" / "info" / "exclude").read_text() == "# *~\n/.leaf/\n"

    def test_no_git_no_exclude(self, wiki_dir):
        embed.exclude_from_git(wiki_dir)
        assert not (wiki_dir / ".git").exists()


# ── Server: hybrid search and Related ────────────────────────────────────────

def _search(client, q):
    return client.get("/api/search", query_string={"q": q}, headers=HOST).get_json()


class TestHybridSearch:
    def test_semantic_only_hit_with_section_excerpt(self, client, semantic):
        results = _search(client, "car repair")
        assert results[0]["slug"] == "golf"
        assert results[0]["excerpt"].startswith("Werkstatt")
        assert results[0]["href"] == "/page/golf#werkstatt"   # its most similar section

    def test_matching_both_ways_beats_keyword_only(self, client, semantic, wiki_dir):
        # All three contain "golf"; only "sport" isn't about cars, so it is
        # the one page found by keyword alone and ranks last.
        write_page(wiki_dir, "sport", "# Sport\n\nGolf on Sundays, then an evening routine and early bed when tired.\n")
        write_page(wiki_dir, "zz-car", "# Car notes\n\nWerkstatt, Mechaniker, golf.\n")
        semantic.sync()
        slugs = [r["slug"] for r in _search(client, "golf")]
        assert set(slugs) == {"golf", "sport", "zz-car"}
        assert slugs[-1] == "sport"

    def test_nonsense_finds_nothing(self, client, semantic):
        assert _search(client, "xqzt blorf") == []

    def test_tasks_still_found_by_keyword(self, client, semantic):
        client.post("/api/deck", json={"title": "Call the mechaniker"}, headers=HOST)
        results = _search(client, "mechaniker")
        assert any(r.get("type") == "task" for r in results)
        assert any(r["slug"] == "golf" for r in results)

    def test_deleted_page_not_returned_before_resync(self, client, semantic, wiki_dir):
        (wiki_dir / "golf.md").unlink()
        assert all(r["slug"] != "golf" for r in _search(client, "car repair"))

    def test_without_semantic_keywords_only(self, client, semantic, monkeypatch):
        monkeypatch.setattr(retrieval, "SEMANTIC", None)
        assert _search(client, "car repair") == []
        assert _search(client, "werkstatt")[0]["slug"] == "golf"

    def test_task_found_by_meaning(self, client, semantic):
        client.post("/api/deck", json={"title": "Book the Werkstatt"}, headers=HOST)
        semantic.sync()
        results = _search(client, "car repair")
        assert any(r.get("type") == "task" and r["title"] == "[Task] Book the Werkstatt" for r in results)

    def test_keyword_section_beats_the_similar_one(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nMechaniker, Bremsen.\n\n"
                                     "## Papers\n\nThe insurance letter is in the drawer.\n")
        semantic.sync()
        r = next(r for r in _search(client, "drawer") if r["slug"] == "golf")
        assert r["href"] == "/page/golf?hl=drawer#papers"

    def test_filters_apply_to_meaning_hits(self, client, semantic):
        assert _search(client, "car repair")[0]["slug"] == "golf"
        assert _search(client, "car repair -bremsen") == []      # golf mentions Bremsen
        assert _search(client, 'car repair "no such phrase"') == []

    def test_search_page_uses_it_too(self, client, semantic):
        html = client.get("/search", query_string={"q": "car repair"}, headers=HOST).get_data(as_text=True)
        assert "/page/golf" in html


def _related(client, slug):
    html = client.get(f"/page/{slug}", headers=HOST).get_data(as_text=True)
    start = html.find('id="related"')
    if start < 0:
        return None
    section = html[start:html.find("</section>", start)]
    return re.findall(r'href="/page/([^"]+)"', section)


class TestRelated:
    def test_lists_similar_pages(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        assert _related(client, "golf") == ["brakes"]

    def test_title_shown(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brake pads\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        html = client.get("/page/golf", headers=HOST).get_data(as_text=True)
        assert ">Brake pads</a>" in html

    def test_linked_pages_left_out(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        write_page(wiki_dir, "garage", "# Garage\n\nWerkstatt Mechaniker. See [[golf]].\n")
        _bump_mtime(write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nMechaniker, Bremsen. [[brakes]]\n"))
        semantic.sync()
        # brakes is linked from golf, garage links to golf: nothing left.
        assert _related(client, "golf") is None

    def test_absent_when_nothing_similar(self, client, semantic):
        assert _related(client, "evening") is None

    def test_absent_on_a_page_that_is_only_a_deck_embed(self, client, wiki_dir, monkeypatch):
        long = "Werkstatt beim Mechaniker, Bremsen quietschen, Termin am Dienstag. " * 3
        write_page(wiki_dir, "golf", f"# Golf\n\n{long}\n")
        write_page(wiki_dir, "car-tasks", "# Golf tasks\n\n[[deck tag=golf]]\n")
        idx = make_index(wiki_dir, related_min_chars=embed.RELATED_MIN_CHARS)
        idx.sync()
        monkeypatch.setattr(retrieval, "SEMANTIC", idx)
        assert _related(client, "car-tasks") is None
        assert _related(client, "golf") is None  # car-tasks isn't offered either

    def test_absent_without_semantic(self, client, semantic, wiki_dir, monkeypatch):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        monkeypatch.setattr(retrieval, "SEMANTIC", None)
        assert _related(client, "golf") is None
