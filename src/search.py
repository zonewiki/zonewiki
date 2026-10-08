"""Lexical search ranking for Leaf.

Pure-Python BM25 with light stemming, prefix (partial-word) matching, and an
edit-distance fuzzy fallback for typos. No database, no external dependencies —
just stdlib (re, math, difflib). Used by both page search (server.py) and task
search (deck.py) so results share one comparable relevance scale.

Design notes:
- A "document" is a dict carrying at least a pre-tokenized ``tokens`` list plus
  whatever passthrough fields the caller needs for formatting (slug, title,
  ``excerpt_text``, and optional type/task_id/status). Tokenizing is the
  expensive part, so callers cache ``tokens`` and only rebuild on file change.
- Title terms are boosted by including title tokens ``TITLE_WEIGHT`` extra times
  in the document's token bag, which naturally raises their BM25 term frequency.
"""

import bisect
import difflib
import functools
import html
import math
import re
import unicodedata

# Include title tokens this many extra times so title matches rank higher.
TITLE_WEIGHT = 3

# BM25 tuning — standard defaults (term-frequency saturation / length norm).
_K1 = 1.5
_B = 0.75

# Relevance weight applied to expanded (non-exact) query terms.
_PREFIX_WEIGHT = 0.8
_FUZZY_WEIGHT = 0.5

# Runs of letters and digits in any script (\w without the underscore), so
# "Müller" stays one word instead of splitting into "m" + "ller".
_WORD_RE = re.compile(r"[^\W_]+")


def fold(text: str) -> str:
    """Lowercase and strip accents so "Müller", "MÜLLER" and "muller" match:
    ü→u, é→e, and ß→ss (via casefold). Letters with no ASCII base (e.g. ø,
    Cyrillic) are kept as they are."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def stem(word: str) -> str:
    """Conservative suffix-stripping stemmer so run/runs/running collapse.

    Not a full Porter stemmer — just the common inflections, with doubled-final-
    consonant collapse (running -> runn -> run) so -ing/-ed forms line up with
    their base. Kept deliberately shallow to avoid surprising collisions.

    Porter's guards keep real words intact: the base must contain a vowel
    (string, spring stay whole), "-eed" is not a suffix (speed, agreed), and
    a doubled l/s/z stays doubled (called -> call, passed -> pass), as does
    any double that would leave fewer than three letters (added -> add).
    """
    if len(word) <= 3:
        return word
    for suf in ("ing", "ed"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            base = word[: -len(suf)]
            if suf == "ed" and base.endswith("e"):
                break
            if not any(c in "aeiouy" for c in base):
                break
            if (len(base) >= 4 and base[-1] == base[-2]
                    and base[-1] not in "aeioulsz"):
                base = base[:-1]
            return base
    # Plural handling (Porter step 1a style): databases -> database, ponies ->
    # pony, caress stays caress. Avoids the databases/database mismatch that a
    # blanket "-es" strip caused.
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ss"):
        return word
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


# The "(target)" of a markdown link or image, with an optional "title" and
# one level of parentheses inside the URL (Wikipedia-style). The link text
# stays; the target is left out of the index and excerpts, or every page with
# a /page/… link would match "page".
_LINK_TARGET_RE = re.compile(
    r'\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+"[^"]*")?\)'
)
# Reference-style link definitions: "[id]: https://… "title"".
_LINK_DEF_RE = re.compile(r"(?m)^( {0,3}\[[^\]]+\]:)\s+\S+.*$")


def strip_link_targets(text: str) -> str:
    """Markdown with link/image targets removed, link text kept. Bare URLs
    in the text stay, so a pasted domain can still be found."""
    return _LINK_DEF_RE.sub(r"\1", _LINK_TARGET_RE.sub("]", text))


def tokenize(text: str) -> list[str]:
    """Fold case and accents, split on non-alphanumerics, and stem each token."""
    return [stem(m.group()) for m in _WORD_RE.finditer(fold(text))]


# English and German function words, folded. Left out of a query (never out
# of the index) when it has other words: in a small wiki "the" or "in" is not
# in every page, so BM25 gives them weight, and a question like "when is the
# car going to the mechanic" ranked pages by its filler words. Words that are
# also nouns or names (may, will, it, man, art) are not in the list.
STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i if in into is me my of on or our so than that the their them then there
these they this those to was we were what when where which who whom why with
would you your about after before over under up down out
aber als am an auf aus bei bin bis bist da dann das dass dem den der des die
dies diese dieser doch du ein eine einem einen einer er es fur hab habe hat
hatte ich ihr im in ist ja mich mir mit nach nicht noch nur ob oder sich sie
sind so uber um und uns von vor war waren was welche welcher wenn wer wie wir
wo zu zum zur
""".split())


def query_words(query: str) -> list[str]:
    """The folded words of a query that count: all but the stopwords, unless
    that leaves nothing ("what is this" still searches)."""
    words = _WORD_RE.findall(fold(query))
    return [w for w in words if w not in STOPWORDS] or words


# ── Query syntax ─────────────────────────────────────────────────────────────
# "exact phrase", -word, -"phrase", #tag and -#tag, the same in the browser
# search and the MCP tools. An unclosed quote is just text.

_QUERY_PART_RE = re.compile(r'(-?)(?:"([^"]+)"|(\S+))')
# edited:2026-09, after:2026-07-01, before:2026: a year, month or day.
_DATE_FILTER_RE = re.compile(r"(?i)(edited|after|before):(\d{4}(?:-\d{2}(?:-\d{2})?)?)")


def squash(text: str) -> str:
    """Folded text with runs of whitespace as one space, for phrase checks."""
    return " ".join(fold(text).split())


class Query:
    """A parsed query. `text` is what ranks and is marked in excerpts (the
    words and phrases, without operators); the rest are filters that a
    document must pass (`accepts`)."""

    def __init__(self, raw: str):
        words, phrases = [], []
        self.phrases: list[str] = []
        self.exclude_phrases: list[str] = []
        self.exclude_stems: set[str] = set()
        self.tags: set[str] = set()
        self.exclude_tags: set[str] = set()
        self.dates: list[tuple[str, str]] = []  # (edited|not-edited|after|before, date prefix)
        for m in _QUERY_PART_RE.finditer(raw or ""):
            negative, quoted, plain = m.group(1) == "-", m.group(2), m.group(3)
            if quoted is not None:
                if not quoted.strip():
                    continue
                if negative:
                    self.exclude_phrases.append(squash(quoted))
                else:
                    self.phrases.append(squash(quoted))
                    phrases.append(quoted.strip())
            elif d := _DATE_FILTER_RE.fullmatch(plain):
                kind = d.group(1).lower()
                if negative:  # -after:X is before:X, -edited:X "not then"
                    kind = {"after": "before", "before": "after", "edited": "not-edited"}[kind]
                self.dates.append((kind, d.group(2)))
            elif plain.startswith("#") and len(plain.strip("#")) > 0:
                (self.exclude_tags if negative else self.tags).add(plain.lstrip("#").lower())
            elif negative:
                self.exclude_stems.update(tokenize(plain))
            else:
                words.append(plain)
        self.text = " ".join(phrases + words)

    @property
    def phrase(self) -> str | None:
        """The query's words as one phrase (squashed), when there are at
        least two: a document containing it is what was meant, so it ranks
        first ("alex again" finds the note saying "Alex again"). Stopwords
        stay in, as they are part of the wording."""
        words = self.text.split()
        return squash(self.text) if len(words) >= 2 else None

    @property
    def has_filters(self) -> bool:
        return bool(self.phrases or self.exclude_phrases or self.exclude_stems
                    or self.tags or self.exclude_tags or self.dates)

    @property
    def ranking_text(self) -> str:
        """What to rank by: the words and phrases, or for a query of only
        tags, the tag words (a page with a tag usually mentions it)."""
        return self.text or " ".join(sorted(self.tags))

    def accepts(self, text: str, tokens, tags, date: str | None = None) -> bool:
        """Whether a document passes the filters: its searchable text, its
        (stemmed) tokens, its tags and its date ("YYYY-MM-DD", for the date
        filters: edited: is a prefix, after: on or after, before: strictly
        before; partial dates compare as prefixes, so before:2026-08 is July
        and earlier)."""
        for kind, value in self.dates:
            if not date:
                return False
            if kind == "edited" and not date.startswith(value):
                return False
            if kind == "not-edited" and date.startswith(value):
                return False
            if kind == "after" and date < value:
                return False
            if kind == "before" and date[:len(value)] >= value:
                return False
        tags = {t.lower() for t in tags or ()}
        if not self.tags <= tags or self.exclude_tags & tags:
            return False
        if self.exclude_stems and self.exclude_stems & set(tokens):
            return False
        if self.phrases or self.exclude_phrases:
            squashed = squash(text)
            if not all(p in squashed for p in self.phrases):
                return False
            if any(p in squashed for p in self.exclude_phrases):
                return False
        return True


class SearchIndex:
    """In-memory BM25 index over a list of pre-tokenized documents."""

    def __init__(self, docs: list[dict], k1: float = _K1, b: float = _B):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self._len: list[int] = []
        # Inverted index: term -> [(doc index, term frequency)], so a search
        # scores only the docs containing its terms, not every doc.
        self._postings: dict[str, list[tuple[int, int]]] = {}
        df: dict[str, int] = {}
        for i, d in enumerate(docs):
            tf: dict[str, int] = {}
            for t in d.get("tokens", ()):
                tf[t] = tf.get(t, 0) + 1
            self._len.append(sum(tf.values()))
            for t, f in tf.items():
                df[t] = df.get(t, 0) + 1
                self._postings.setdefault(t, []).append((i, f))
        self.n = len(docs)
        self.avgdl = (sum(self._len) / self.n) if self.n else 0.0
        self.df = df
        self.idf = {
            t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }
        self.vocab = sorted(df)

    def _expand(self, q_tokens: list[str]) -> list[tuple[str, float]]:
        """Map query tokens to (term, weight) pairs, keeping the best weight.

        Exact matches weigh 1.0; prefix (partial-word) matches 0.8; and if a
        token has no exact/prefix hit, close edit-distance matches 0.5 so typos
        still land. Only the strongest weight per resolved term is kept.
        """
        best: dict[str, float] = {}

        def bump(term: str, weight: float) -> None:
            if weight > best.get(term, 0.0):
                best[term] = weight

        for t in q_tokens:
            matched = False
            if t in self.df:
                bump(t, 1.0)
                matched = True
            if len(t) >= 3:
                # The vocabulary is sorted: words starting with t are one run.
                i = bisect.bisect_left(self.vocab, t)
                while i < len(self.vocab) and self.vocab[i].startswith(t):
                    if self.vocab[i] != t:
                        bump(self.vocab[i], _PREFIX_WEIGHT)
                        matched = True
                    i += 1
            if not matched and len(t) >= 4:
                for c in difflib.get_close_matches(t, self.vocab, n=3, cutoff=0.8):
                    bump(c, _FUZZY_WEIGHT)
        return list(best.items())

    def search(self, query: str, limit: int = 15) -> list[tuple[float, dict]]:
        """Return up to ``limit`` (score, doc) pairs ranked most-relevant first."""
        q_tokens = [stem(w) for w in query_words(query)]
        if not q_tokens or self.n == 0:
            return []
        weighted = self._expand(q_tokens)
        if not weighted:
            return []
        scores: dict[int, float] = {}
        for term, w in weighted:
            idf = self.idf.get(term, 0.0)
            for i, f in self._postings.get(term, ()):
                denom = f + self.k1 * (1 - self.b + self.b * self._len[i] / self.avgdl)
                scores[i] = scores.get(i, 0.0) + w * idf * (f * (self.k1 + 1) / denom)
        # Ties keep the docs' order, as when every doc was scored in turn.
        ranked = sorted((i for i, s in scores.items() if s > 0), key=lambda i: (-scores[i], i))
        return [(scores[i], self.docs[i]) for i in ranked[:limit]]


@functools.lru_cache(maxsize=1 << 16)
def _fold_stem(word: str) -> tuple[str, str]:
    """A word's folded form and stem; words repeat across pages, so cached."""
    folded = fold(word)
    return folded, stem(folded)


@functools.lru_cache(maxsize=4096)
def _analyze(text: str):
    """A text's words as (start, end, folded), their stems, the distinct stems
    sorted (prefix matches by bisection) and grouped by length (fuzzy
    candidates). Cached: the same page or section text is matched against
    query after query, and splitting and stemming it was most of a search's
    time."""
    words, stems = [], []
    for m in _WORD_RE.finditer(text):
        folded, st = _fold_stem(m.group())
        words.append((m.start(), m.end(), folded))
        stems.append(st)
    distinct = sorted(set(stems))
    by_len: dict[int, list[str]] = {}
    for st in distinct:
        by_len.setdefault(len(st), []).append(st)
    return words, stems, distinct, by_len


def _match_spans(text: str, query: str) -> list[tuple[int, int]]:
    """Character spans of the words in ``text`` that the query matched, the
    way the index matches them: same stem, a stem the query is a prefix of,
    or (only when neither occurs) a close fuzzy match. A query of several
    words that appears as a phrase (case and accents ignored) is one span,
    and is the only one, so the excerpt centres on it."""
    words, stems, distinct, by_len = _analyze(text)
    q_words = [fold(w) for w in _WORD_RE.findall(query)]
    if not words or not q_words:
        return []
    n = len(q_words)
    if n > 1:
        for i in range(len(words) - n + 1):
            if all(words[i + k][2] == q_words[k] for k in range(n)):
                return [(words[i][0], words[i + n - 1][1])]
    terms = {stem(w) for w in query_words(query)}
    hits: set[str] = set()
    for t in terms:
        i = bisect.bisect_left(distinct, t)
        if i < len(distinct) and distinct[i] == t:
            hits.add(t)
        if len(t) >= 3:  # stems the term is a prefix of: one sorted run
            while i < len(distinct) and distinct[i].startswith(t):
                hits.add(distinct[i])
                i += 1
    if not hits:
        # Close fuzzy matches (ratio >= 0.8). That needs a length within
        # 2/3 to 3/2 of the term's, so only those stems are candidates.
        for t in terms:
            if len(t) >= 4:
                lo, hi = -(-2 * len(t) // 3), 3 * len(t) // 2
                cands = [c for n in range(lo, hi + 1) for c in by_len.get(n, ())]
                hits.update(difflib.get_close_matches(t, cands, n=len(cands) or 1, cutoff=0.8))
    return [(w[0], w[1]) for w, s in zip(words, stems) if s in hits]


def matched_words(text: str, query: str, limit: int = 10) -> list[str]:
    """The distinct words (or the one phrase) of text that the query matched,
    as they are written there, whitespace squashed, at most `limit`: what a
    page opened from a search highlights."""
    seen: dict[str, str] = {}
    for a, b in _match_spans(text, query):
        word = " ".join(text[a:b].split())
        seen.setdefault(word.casefold(), word)
        if len(seen) >= limit:
            break
    return list(seen.values())


def matches(text: str, query: str) -> bool:
    """Whether text contains a word the query matches, the way the index and
    the excerpt match words (stem, prefix, else fuzzy)."""
    return bool(_match_spans(text, query))


def make_excerpt(text: str, query: str, before: int = 60, after: int = 90) -> str:
    """Build an HTML-escaped snippet around the first match, bolded via <mark>.

    Matches are whole words found as the index finds them (see _match_spans):
    "running" marks "runs", a typo marks the word it stands for, and "art"
    does not mark the inside of "start". Every match inside the window is
    marked. Returns a leading excerpt if nothing matches.
    """
    spans = _match_spans(text, query)
    if not spans:
        head = text[:150].replace("\n", " ").strip()
        return html.escape(head) + ("…" if len(text) > 150 else "")

    first_start, first_end = spans[0]
    start = max(0, first_start - before)
    end = min(len(text), first_end + after)
    out, pos = [], start
    for a, b in spans:
        if a < start or b > end:
            continue
        out.append(html.escape(text[pos:a]))
        out.append(f"<mark>{html.escape(text[a:b])}</mark>")
        pos = b
    out.append(html.escape(text[pos:end]))
    snippet = "".join(out).replace("\n", " ")
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


# Reciprocal rank fusion: each list adds 1/(RRF_K + rank) for every item in it.
# It needs only the ranks, so BM25 scores and cosine similarities, which are
# on unrelated scales, can be merged without normalising either.
RRF_K = 60


def fuse(rankings: list[list], key=id) -> list:
    """Merge ranked lists (best first) into one by reciprocal rank fusion.
    Items are identified by key(item); an item high in several lists beats
    one high in a single list. Ties keep the order items were first seen in,
    so the first list (BM25) wins them."""
    scores: dict = {}
    items: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            k = key(item)
            scores[k] = scores.get(k, 0.0) + 1.0 / (RRF_K + rank + 1)
            items.setdefault(k, item)
    return sorted(items.values(), key=lambda item: -scores[key(item)])
