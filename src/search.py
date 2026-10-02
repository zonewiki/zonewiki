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
    """
    if len(word) <= 3:
        return word
    for suf in ("ing", "ed"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            base = word[: -len(suf)]
            if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in "aeiou":
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
        self.vocab = list(df)

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
                for v in self.vocab:
                    if v != t and v.startswith(t):
                        bump(v, _PREFIX_WEIGHT)
                        matched = True
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


def make_excerpt(text: str, query: str, before: int = 60, after: int = 90) -> str:
    """Build an HTML-escaped snippet around the first match, bolded via <mark>.

    Prefers the full query phrase if it appears verbatim; otherwise falls back to
    the earliest individual query word (matched as a substring, so partial words
    highlight too). Returns a leading excerpt if nothing matches.
    """
    tl = text.lower()
    ql = query.lower().strip()

    match_at = -1
    match_len = 0
    if ql and ql in tl:
        match_at = tl.find(ql)
        match_len = len(ql)
    else:
        for w in _WORD_RE.findall(ql):
            i = tl.find(w)
            if i != -1 and (match_at == -1 or i < match_at):
                match_at = i
                match_len = len(w)

    if match_at == -1:
        head = text[:150].replace("\n", " ").strip()
        return html.escape(head) + ("…" if len(text) > 150 else "")

    start = max(0, match_at - before)
    end = min(len(text), match_at + match_len + after)
    snippet = text[start:end].replace("\n", " ")
    rel = match_at - start
    pre = html.escape(snippet[:rel])
    mid = html.escape(snippet[rel : rel + match_len])
    post = html.escape(snippet[rel + match_len :])
    return (
        ("…" if start > 0 else "")
        + f"{pre}<mark>{mid}</mark>{post}"
        + ("…" if end < len(text) else "")
    )
