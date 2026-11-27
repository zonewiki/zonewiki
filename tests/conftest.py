"""Shared setup: a fresh temporary wiki for each test (wiki_dir,
git_wiki_dir) and Zonewiki's server on it (client: tests/goserver.py). The
browser tests (tests/e2e) build on git_wiki_dir too."""

import hashlib
import html
import io
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from tests import settings
from tests.goserver import GoClient, GoServer, binary

HOST = {"Host": "localhost:5000"}


def pytest_configure(config):
    # Not an xdist worker: build the server once for all (without Go, the
    # tests that need it fail; run.sh's tools image has it).
    if not hasattr(config, "workerinput") and shutil.which("go"):
        binary()


@pytest.fixture()
def wiki_dir(tmp_path, monkeypatch):
    """A fresh wiki (just index.md) that the server points at."""
    monkeypatch.setattr(settings, "ZONE_DATA", tmp_path)
    (tmp_path / "_assets").mkdir()
    (tmp_path / "_tasks").mkdir()   # tests write tasks files directly
    (tmp_path / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def git_wiki_dir(wiki_dir, monkeypatch, tmp_path_factory):
    """wiki_dir as a git repository, as Zonewiki sets one up when it starts."""
    monkeypatch.setattr(settings, "GIT_REMOTE", "")
    monkeypatch.setattr(settings, "GIT_ENABLED", True)
    git_setup(tmp_path_factory)
    return wiki_dir


def git_setup(tmp_path_factory) -> None:
    """What Zonewiki does to the wiki when it starts (git init, the first
    commit, the task files): a server started and stopped."""
    from tests.goserver import _flags, _settings
    GoServer(_settings(), tmp_path_factory.mktemp("go") / "setup.log", flags=_flags()).stop()


@pytest.fixture()
def client(wiki_dir, tmp_path_factory):
    with GoClient(tmp_path_factory.mktemp("go") / "server.log") as c:
        c.live.ensure()   # its start-up (git, the task files) before the test
        yield c


def write_page(wiki_dir: Path, slug: str, content: str) -> Path:
    path = wiki_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def create_task(client, **kwargs):
    """POST a task (title "Test task" unless given); returns the response."""
    return client.post("/api/tasks", json={"title": "Test task", **kwargs})


def make_list(client, name: str, private: bool = False, pin: bool = False, **kwargs):
    """Make a task list as New list… does (private: the user's own), but not
    pinned (the dialog pins the list it makes: pin=True), so a test pins
    what it means to; returns the response, a redirect to the list."""
    data = {"name": name, "pin": "1" if pin else "0", **({"private": "1"} if private else {})}
    return client.post("/tasks/new-list", data=data, **kwargs)


_EMBED_RE = re.compile(r"\[\[tasks:\s*([^\]]*)\]\]", re.I)


def list_page(client, wiki_dir: Path, slug: str, content: str, **kwargs) -> Path:
    """write_page, and the lists the page's [[tasks: list=…]] embeds show,
    made through the routes the browser uses: each list if it isn't there
    yet (private for a page in private/), with the embed's list settings
    (view, keep, limit, plan, labels, new) when it was made here, and pinned
    as a pin= in the embed says (pin=1; pin=default: pinned first). A list is no longer
    declared by a page nor pinned by one: pin= in the text means nothing to
    Zonewiki, it tells this helper what the test wants. Code is skipped."""
    path = write_page(wiki_dir, slug, content)
    kwargs.setdefault("headers", HOST)
    text = re.sub(r"```.*?```|`[^`\n]*`", "", content, flags=re.S)
    for m in _EMBED_RE.finditer(text):
        p = dict(w.split("=", 1) for w in m.group(1).split() if "=" in w)
        name = p.get("list", "").strip("#").lower()
        if not name:
            continue
        made = client.post("/tasks/new-list", data={"name": name, "pin": "0", **({"private": "1"} if slug.startswith("private/") else {})}, **kwargs)
        own = {k: p[k] for k in ("view", "keep", "limit", "plan", "labels", "new") if k in p}
        if own and "newlist_error" not in made.headers.get("Location", ""):
            assert client.post("/api/tasks/settings", json={"list": name, "set": own}, **kwargs).status_code == 200
        if p.get("pin"):
            pin = "first" if p["pin"].lower() == "default" else "1"
            assert client.post("/tasks/pin-list", data={"list": name, "pin": pin}, **kwargs).status_code == 302
    return path


def notes_zip(files: dict | None = None) -> bytes:
    """A zip of a folder of notes, for Import pages (POST /import): its
    files by name, text or bytes; a small Obsidian vault zipped as its
    folder ("My Vault/…", with .obsidian/) when none are given."""
    if files is None:
        files = {"My Vault/Garden.md": "# Garden\n\nSee [[Roses]] and ![[pic.png]].\n",
                 "My Vault/Roses.md": "Red ones.\n", "My Vault/pic.png": b"\x89PNG\r\n\x1a\n",
                 "My Vault/.obsidian/app.json": "{}"}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def content_hash(text: str) -> str:
    """The editor's hash of a page's text (file_hash), for conflict checks."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def section_fields(client, slug: str, section_idx: int) -> dict:
    """The editor's hidden fields for editing one section (file_hash,
    start_char, end_char), as its page has them."""
    page = client.get(f"/edit/{slug}", query_string={"section": section_idx}, headers=HOST)
    assert page.status_code == 200, f"no section {section_idx}"
    fields = dict(re.findall(r'<input type="hidden" name="(file_hash|start_char|end_char)"[^>]* value="([^"]*)"',
                             page.get_data(as_text=True)))
    assert "start_char" in fields, f"no section {section_idx}"
    return {k: html.unescape(v) for k, v in fields.items()}


def rendered(client, wiki_dir: Path, content: str, slug: str = "render-test") -> str:
    """The HTML of a page with this text, its article only."""
    write_page(wiki_dir, slug, content)
    page = client.get(f"/page/{slug}", headers=HOST).get_data(as_text=True)
    start = page.index("<article")
    return page[page.index(">", start) + 1:page.rindex("</article>")]


def preview(client, text: str) -> str:
    """Markdown as Zonewiki renders it (the editor's preview: no page around it)."""
    return client.post("/api/preview", data=text.encode("utf-8"), headers=HOST).get_data(as_text=True)


def _link_section(client, slug: str, section_id: str) -> str:
    page = client.get(f"/page/{slug}", headers=HOST).get_data(as_text=True)
    start = page.find(f'id="{section_id}"')
    return "" if start < 0 else page[start:page.index("</section>", start)]


def backlinks(client, slug: str) -> list[str]:
    """The pages a page's Backlinks section lists, by slug."""
    return re.findall(r'<a href="/page/([^"#?]+)"', _link_section(client, slug, "backlinks"))


def task_backlinks(client, slug: str) -> list[str]:
    """The tasks a page's Linked tasks section lists, by title."""
    return [html.unescape(re.sub(r"<[^>]+>", "", t)) for t in
            re.findall(r"<a [^>]*>(.*?)</a>", _link_section(client, slug, "task-backlinks"))]
