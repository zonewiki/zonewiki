"""Unit tests for the BM25 search ranking module (src/search.py).

These exercise the ranking primitives directly (no Flask), covering the three
pain points the rewrite targets: multi-word queries, ranking quality, and
typo/word-variation tolerance.

Run with:
    pytest tests/test_search.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import search


def _doc(slug, title, text):
    return {
        "slug": slug,
        "title": title,
        "excerpt_text": text,
        "tokens": search.tokenize(text) + search.tokenize(title) * search.TITLE_WEIGHT,
    }


def _ranked_slugs(docs, query, limit=15):
    index = search.SearchIndex(docs)
    return [d["slug"] for _s, d in index.search(query, limit=limit)]


# ── Stemming / tokenizing ──────────────────────────────────────────────────


class TestStem:
    def test_plural_collapses(self):
        assert search.stem("databases") == search.stem("database")

    def test_ing_collapses_to_base(self):
        # running -> runn -> run so it lines up with "run"
        assert search.stem("running") == search.stem("run") == "run"

    def test_ed_collapses(self):
        assert search.stem("stopped") == search.stem("stop") == "stop"

    def test_ies_to_y(self):
        assert search.stem("studies") == search.stem("study")

    def test_short_words_untouched(self):
        assert search.stem("cat") == "cat"
        assert search.stem("is") == "is"

    def test_double_s_not_stripped(self):
        assert search.stem("class") == "class"

    def test_doubled_l_s_z_stay(self):
        # Regression: called -> "cal" and passed -> "pas" no longer matched
        # call/pass exactly.
        for base, forms in [("call", ["called", "calling"]),
                            ("pass", ["passed", "passes"]),
                            ("fill", ["filled"]), ("buzz", ["buzzing"])]:
            for f in forms:
                assert search.stem(f) == search.stem(base) == base

    def test_double_kept_when_base_would_be_two_letters(self):
        assert search.stem("added") == search.stem("add") == "add"

    def test_vowelless_base_is_not_a_stem(self):
        # Regression: string -> "str" while strings -> "string".
        assert search.stem("string") == search.stem("strings") == "string"
        assert search.stem("spring") == "spring"

    def test_eed_is_not_a_suffix(self):
        assert search.stem("speed") == search.stem("speeds") == "speed"

    def test_inflection_matches_base_at_full_weight(self):
        docs = [_doc("a", "A", "We called him"), _doc("b", "B", "call me maybe")]
        index = search.SearchIndex(docs)
        scores = {d["slug"]: sc for sc, d in index.search("call")}
        assert scores["a"] == pytest.approx(scores["b"], rel=0.2)

    def test_tokenize_splits_and_stems(self):
        assert search.tokenize("Running Tests, quickly!") == ["run", "test", "quickly"]


class TestStripLinkTargets:
    def test_inline_link_keeps_text(self):
        assert search.strip_link_targets("See [Foo](/page/foo).") == "See [Foo]."

    def test_image_with_title(self):
        assert search.strip_link_targets('![pic](/_assets/a.png "A")') == "![pic]"

    def test_url_with_parentheses(self):
        text = "[W](https://en.wikipedia.org/wiki/A_(b)) after"
        assert search.strip_link_targets(text) == "[W] after"

    def test_reference_definition(self):
        assert search.strip_link_targets('[ref]: https://x.org "T"') == "[ref]:"

    def test_bare_url_and_wikilink_kept(self):
        text = "go to https://example.com or [[some page|Label]]"
        assert search.strip_link_targets(text) == text


class TestUnicode:
    def test_umlaut_word_stays_whole(self):
        # Regression: [a-z0-9]+ split "Müller" into "m" + "ller".
        assert search.tokenize("Müller") == ["muller"]

    def test_accents_and_case_fold(self):
        assert search.tokenize("Café MÜLLER") == search.tokenize("cafe muller")

    def test_sharp_s_folds_to_ss(self):
        assert search.tokenize("Straße") == search.tokenize("strasse")

    def test_underscore_still_separates(self):
        assert search.tokenize("foo_bar") == ["foo", "bar"]

    def test_partial_umlaut_word_matches(self):
        docs = [_doc("a", "A", "Notizen zur Größe"), _doc("b", "B", "other text")]
        assert _ranked_slugs(docs, "grö") == ["a"]
        assert _ranked_slugs(docs, "gro") == ["a"]

    def test_fragment_no_longer_matches(self):
        # "Straße" used to index a stray "e" that any query "e" hit.
        docs = [_doc("a", "A", "Die Straße"), _doc("b", "B", "other text")]
        assert _ranked_slugs(docs, "e") == []

    def test_unaccented_query_finds_accented_word(self):
        docs = [_doc("a", "A", "Ein Besuch im Café"), _doc("b", "B", "other text")]
        assert _ranked_slugs(docs, "cafe") == ["a"]


# ── Ranking ─────────────────────────────────────────────────────────────────


class TestRanking:
    def test_title_match_ranks_above_body(self):
        docs = [
            _doc("body", "Other", "the token appears once here"),
            _doc("title", "token", "unrelated body content"),
        ]
        assert _ranked_slugs(docs, "token")[0] == "title"

    def test_multi_word_query_matches_scattered_terms(self):
        # The exact phrase never appears, but both words do — old substring
        # search would score this 0.
        docs = [
            _doc("a", "Alpha", "the vector lives here and the database elsewhere"),
            _doc("b", "Beta", "nothing relevant at all"),
        ]
        ranked = _ranked_slugs(docs, "vector database")
        assert ranked == ["a"]

    def test_rarer_term_outranks_common_term(self):
        # "the" is in every doc (common, low idf); "quasar" is rare (high idf).
        # A doc matching the rare term should win.
        docs = [
            _doc(f"common{i}", f"Doc {i}", "the the the the the ordinary text")
            for i in range(5)
        ]
        docs.append(_doc("rare", "Doc rare", "the quasar text"))
        ranked = _ranked_slugs(docs, "the quasar")
        assert ranked[0] == "rare"

    def test_no_match_returns_empty(self):
        docs = [_doc("a", "Alpha", "some content")]
        assert _ranked_slugs(docs, "zzzznomatch") == []

    def test_empty_query_returns_empty(self):
        docs = [_doc("a", "Alpha", "some content")]
        assert _ranked_slugs(docs, "   ") == []


# ── Typo / partial-word tolerance ────────────────────────────────────────────


class TestFuzzy:
    def test_typo_finds_close_term(self):
        docs = [
            _doc("db", "Database notes", "everything about the database engine"),
            _doc("x", "Other", "unrelated"),
        ]
        # "databse" (transposed) should still find the database page.
        assert _ranked_slugs(docs, "databse") == ["db"]

    def test_prefix_partial_word_matches(self):
        docs = [
            _doc("db", "Notes", "the database engine internals"),
            _doc("x", "Other", "unrelated content"),
        ]
        # Typing a truncated word should still match via prefix expansion.
        assert _ranked_slugs(docs, "datab") == ["db"]

    def test_prefix_expansion_finds_exactly_the_words_with_that_prefix(self):
        docs = [_doc("a", "A", "databank"), _doc("b", "B", "datum dat"),
                _doc("c", "C", "database"), _doc("d", "D", "update")]
        assert sorted(_ranked_slugs(docs, "data")) == ["a", "c"]

    def test_word_variation_matches_via_stem(self):
        docs = [
            _doc("r", "Notes", "I was running in the park"),
            _doc("x", "Other", "unrelated content"),
        ]
        assert _ranked_slugs(docs, "run") == ["r"]


# ── Excerpt building ──────────────────────────────────────────────────────────


class TestExcerpt:
    def test_marks_full_phrase_when_present(self):
        ex = search.make_excerpt("see the findme_content marker here", "findme_content")
        assert "<mark>findme_content</mark>" in ex

    def test_marks_first_query_word_when_phrase_absent(self):
        ex = search.make_excerpt("alpha beta gamma delta", "gamma epsilon")
        assert "<mark>gamma</mark>" in ex

    def test_escapes_html(self):
        ex = search.make_excerpt("a <script> tag near findme here", "findme")
        assert "&lt;script&gt;" in ex
        assert "<script>" not in ex

    def test_no_match_returns_leading_text(self):
        ex = search.make_excerpt("some leading content", "nomatchword")
        assert "some leading content" in ex
        assert "<mark>" not in ex

    def test_marks_inflected_form(self):
        # Regression: "running" matched "runs" by stem but highlighted nothing.
        ex = search.make_excerpt("He runs every morning", "running")
        assert "<mark>runs</mark>" in ex

    def test_whole_words_only(self):
        # Regression: "art" marked the inside of "restart".
        ex = search.make_excerpt("restart the art show", "art")
        assert ex == "restart the <mark>art</mark> show"

    def test_marks_prefix_match(self):
        ex = search.make_excerpt("set up the database", "data")
        assert "<mark>database</mark>" in ex

    def test_marks_typo_match(self):
        ex = search.make_excerpt("notes on the kubernetes cluster", "kubernets")
        assert "<mark>kubernetes</mark>" in ex

    def test_marks_accented_word_for_plain_query(self):
        ex = search.make_excerpt("Termin mit Herrn Müller", "muller")
        assert "<mark>Müller</mark>" in ex

    def test_phrase_ignores_case(self):
        ex = search.make_excerpt("one two. Alpha Beta here", "alpha beta")
        assert "<mark>Alpha Beta</mark>" in ex

    def test_marks_every_match_in_window(self):
        ex = search.make_excerpt("a cat and another cat", "cat")
        assert ex.count("<mark>cat</mark>") == 2

    def test_window_centres_on_first_match(self):
        text = "x " * 200 + "needle" + " y" * 200
        ex = search.make_excerpt(text, "needle")
        assert ex.startswith("…") and ex.endswith("…")
        assert "<mark>needle</mark>" in ex
