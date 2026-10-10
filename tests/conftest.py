"""Shared setup: Leaf imported against a throwaway LEAF_DATA, and a fresh
temporary wiki for each test (wiki_dir, git_wiki_dir, client). The browser
tests (tests/e2e) build on git_wiki_dir too."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Before the import: server.py creates LEAF_DATA and runs git_setup there.
os.environ.setdefault("LEAF_DATA", tempfile.mkdtemp(prefix="leaf_test_"))
os.environ["LEAF_ALLOW_REMOTE"] = "1"   # no Host check against the test client's
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
import deckstore  # noqa: E402
import gitstore  # noqa: E402
import mdtext  # noqa: E402
import pages  # noqa: E402
import retrieval  # noqa: E402
import server  # noqa: E402

HOST = {"Host": "localhost:5000"}


@pytest.fixture()
def wiki_dir(tmp_path, monkeypatch):
    """A fresh wiki (just index.md) that the server and the deck point at,
    with every cache derived from pages or tasks emptied."""
    monkeypatch.setattr(config, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "_assets")
    monkeypatch.setattr(deckstore, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(deckstore, "DECK_FILE", tmp_path / "_deck" / "tasks.json")
    for cache in (retrieval._search_doc_cache, retrieval._chunk_doc_cache,
                  pages._page_meta_cache, pages._noshare_tag_cache):
        cache.clear()
    for cache in (retrieval._search_index_cache, retrieval._chunk_index_cache):
        cache["key"] = None
    deckstore._task_docs_cache.update(key=None, docs=[])
    for throttle in (server.LOGIN_THROTTLE, server.MCP_THROTTLE):
        monkeypatch.setattr(throttle, "_fails", {})
    (tmp_path / "_assets").mkdir()
    (tmp_path / "_deck").mkdir()   # tests write deck files directly
    (tmp_path / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def git_wiki_dir(wiki_dir, monkeypatch):
    """wiki_dir as a git repository, so every git code path runs."""
    monkeypatch.setattr(config, "LEAF_GIT_REMOTE", "")
    monkeypatch.setattr(deckstore, "GIT_ENABLED", True)
    gitstore.git_setup()
    return wiki_dir


@pytest.fixture()
def client(wiki_dir):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def write_page(wiki_dir: Path, slug: str, content: str) -> Path:
    path = wiki_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def create_task(client, **kwargs):
    """POST a task (title "Test task" unless given); returns the response."""
    return client.post("/api/deck", json={"title": "Test task", **kwargs})


def section_fields(full_content: str, section_idx: int) -> dict:
    """The editor's hidden fields for editing one section (file_hash,
    start_char, end_char)."""
    rng = mdtext.section_char_range(full_content, section_idx)
    assert rng is not None, f"no section {section_idx}"
    start, end = rng
    return {"file_hash": mdtext.content_hash(full_content), "start_char": str(start), "end_char": str(end)}
