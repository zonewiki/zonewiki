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


class SearchIndex:
    """In-memory BM25 index over a list of pre-tokenized documents."""

    def __init__(self, docs: list[dict], k1: float = _K1, b: float = _B):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self._tf: list[dict[str, int]] = []
        self._len: list[int] = []
        df: dict[str, int] = {}
        for d in docs:
            tf: dict[str, int] = {}
            for t in d.get("tokens", ()):
                tf[t] = tf.get(t, 0) + 1
            self._tf.append(tf)
            self._len.append(sum(tf.values()))
            for t in tf:
                df[t] = df.get(t, 0) + 1
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
        q_tokens = tokenize(query)
        if not q_tokens or self.n == 0:
            return []
        weighted = self._expand(q_tokens)
        if not weighted:
            return []
        scored: list[tuple[float, dict]] = []
        for i, d in enumerate(self.docs):
            tf = self._tf[i]
            dl = self._len[i]
            s = 0.0
            for term, w in weighted:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                s += w * idf * (f * (self.k1 + 1) / denom)
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return scored[:limit]


def _match_spans(text: str, query: str) -> list[tuple[int, int]]:
    """Character spans of the words in ``text`` that the query matched, the
    way the index matches them: same stem, a stem the query is a prefix of,
    or (only when neither occurs) a close fuzzy match. A query of several
    words that appears as a phrase (case and accents ignored) is one span,
    and is the only one, so the excerpt centres on it."""
    words = [(m.start(), m.end(), fold(m.group())) for m in _WORD_RE.finditer(text)]
    q_words = [fold(w) for w in _WORD_RE.findall(query)]
    if not words or not q_words:
        return []
    n = len(q_words)
    if n > 1:
        for i in range(len(words) - n + 1):
            if all(words[i + k][2] == q_words[k] for k in range(n)):
                return [(words[i][0], words[i + n - 1][1])]
    terms = {stem(w) for w in q_words}
    stems = [stem(w[2]) for w in words]

    def exact_or_prefix(s: str) -> bool:
        return any(s == t or (len(t) >= 3 and s.startswith(t)) for t in terms)

    spans = [(w[0], w[1]) for w, s in zip(words, stems) if exact_or_prefix(s)]
    if spans:
        return spans
    fuzzy_terms = [t for t in terms if len(t) >= 4]
    close = {}
    for s in stems:
        if s not in close:
            close[s] = any(
                difflib.SequenceMatcher(None, s, t).ratio() >= 0.8 for t in fuzzy_terms
            )
    return [(w[0], w[1]) for w, s in zip(words, stems) if close[s]]


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
