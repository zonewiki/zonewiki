"""Semantic search in the tests, without a model: the server embeds with a
fake that maps words to a few "concepts" (go/embedtest.go), so texts sharing
no words can still be similar, deterministically. A test turns it on with
monkeypatch.setattr(settings, "SEMANTIC", make_index()); the server
restarts with it."""

# word -> concept axis; other words don't count.
CONCEPTS = {
    "backup": 0, "remote": 0, "push": 0, "copy": 0, "git": 0,
    "car": 1, "repair": 1, "werkstatt": 1, "mechaniker": 1, "bremsen": 1, "golf": 1,
    "sleep": 2, "bed": 2, "tired": 2, "evening": 2, "routine": 2,
    "money": 3, "budget": 3, "rent": 3, "savings": 3, "insurance": 3,
}
DIM = 4
# The least prose a page needs to take part in Related (go/embed.go).
RELATED_MIN_CHARS = 100


class FakeEmbedder:
    name = "fake:concepts"
    model_name = "fake"
    concepts, dim = CONCEPTS, DIM

    def __init__(self):
        self.fail = False   # every embedding fails ("model unavailable")


class FakeIndex:
    """The semantic index the server builds: its embedder and floors. It
    syncs (embeds the wiki) when the server starts, once sync() was called:
    each sync() restarts the server, so it sees the wiki as it is now."""

    def __init__(self, embedder=None, min_score=0.5, task_min_score=0.5,
                 related_min_score=0.5, related_min_chars=0):
        self.embedder = embedder or FakeEmbedder()
        self.min_score, self.task_min_score = min_score, task_min_score
        self.related_min_score = related_min_score
        # The test pages are short; tests of the thin-page rule pass their own.
        self.related_min_chars = related_min_chars
        self.ready, self.generation = False, 0

    def sync(self):
        self.ready = True
        self.generation += 1

    def flag(self) -> dict:
        """go/embedtest.go's --semantic-test."""
        e = self.embedder
        return {"name": e.name, "model_name": e.model_name, "concepts": e.concepts, "dim": e.dim,
                "fail": e.fail, "min_score": self.min_score, "related_min_score": self.related_min_score,
                "task_min_score": self.task_min_score, "related_min_chars": self.related_min_chars,
                "synced": self.ready, "generation": self.generation}


def make_index(**kw) -> FakeIndex:
    return FakeIndex(**kw)
