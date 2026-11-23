"""Help: Zonewiki's manual in the browser (/help), the spec beside it, the
manual in zone_help, and zonewiki manual DIR (docs/spec/routes.md, Help)."""

import html
import json
import re
import subprocess

import pytest

from tests import settings
from tests.goserver import REPO, binary
from tests.test_mcp import TOKEN, call

MANUAL = REPO / "docs" / "manual"


def pages():
    """The manual's pages by their /help path ("" the contents)."""
    return [""] + sorted(p.stem for p in MANUAL.glob("*.md") if p.name != "README.md")


def ids(text):
    return set(re.findall(r'\bid="([^"]+)"', text))


# ── The manual's own links ───────────────────────────────────────────────────

def test_every_link_in_the_manual_leads_somewhere(client):
    """Every relative link and image of every manual page, as /help shows it,
    leads to a document, a heading in it or a screenshot that is there: a
    renamed heading or page fails here."""
    shown, broken = {}, []

    def get(path):
        if path not in shown:
            resp = client.get(path)
            shown[path] = (resp.status_code, resp.data.decode(errors="replace"))
        return shown[path]

    for name in pages():
        path = "/help/" + name if name else "/help"
        status, text = get(path)
        assert status == 200, path
        article = text.split('<article class="page-content help-content" data-rendered>', 1)[1].split("</article>", 1)[0]
        for attr, link in re.findall(r'\b(href|src)="([^"]+)"', article):
            link = html.unescape(link)
            if re.match(r"[a-z]+:", link) or link.startswith("//"):
                continue
            target, _, fragment = link.partition("#")
            if not target:
                target = path
            if not target.startswith("/help"):
                broken.append(f"{path}: {link} (not rewritten: a file that isn't in docs/?)")
                continue
            status, body = get(target)
            if status != 200:
                broken.append(f"{path}: {link} ({status})")
            elif fragment and fragment not in ids(body):
                broken.append(f"{path}: {link} (no such heading)")
    assert not broken, "\n".join(broken)


def test_every_page_is_in_the_contents():
    readme = (MANUAL / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\(([a-z0-9-]+)\.md\)", readme))
    assert linked == set(pages()[1:])


def test_headings_are_unique_in_each_page():
    """zone_help finds a section by its heading."""
    for p in MANUAL.glob("*.md"):
        text = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)
        headings = re.findall(r"^#{1,6} (.+)$", text, re.M)
        dupes = {h for h in headings if headings.count(h) > 1}
        assert not dupes, f"{p.name}: {dupes}"


# ── /help ────────────────────────────────────────────────────────────────────

def test_the_contents_and_a_page(client):
    resp = client.get("/help")
    text = resp.data.decode()
    assert resp.status_code == 200 and "<title>Zonewiki&#39;s manual" in text
    assert 'href="/help/getting-started"' in text   # README's getting-started.md
    assert 'class="help-next" href="/help/getting-started"' in text
    text = client.get("/help/tasks").data.decode()
    assert "<h1" in text and 'rel="prev"' in text and 'rel="next"' in text
    assert 'class="rail-toc"' in text               # its headings on the left


def test_the_spec_beside_it(client):
    text = client.get("/help/spec").data.decode()
    assert 'href="/help/spec/tasks"' in text and 'href="/help"' in text
    resp = client.get("/help/spec/tasks")
    assert resp.status_code == 200 and "help-pager" not in resp.data.decode()


def test_screenshots(client):
    resp = client.get("/help/screenshots/page.png")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "image/png"
    for path in ("/help/screenshots/make_screenshots.py", "/help/screenshots/nope.png"):
        assert client.get(path).status_code == 404, path
    resp = client.get("/help/screenshots/..%2f..%2fREADME.md")
    assert resp.status_code != 200 and b"# Zonewiki" not in resp.data


@pytest.mark.parametrize("path", ["/help/nope", "/help/README", "/help/spec/nope", "/help/spec/../../README",
                                  "/help/Tasks", "/help/tasks/more"])
def test_what_isnt_there(client, path):
    resp = client.get(path)
    assert resp.status_code != 200 and b"help-content" not in resp.data


def test_without_the_docs(client, monkeypatch):
    monkeypatch.setattr(settings, "DOCS", None)
    resp = client.get("/help")
    assert resp.status_code == 200 and "manual isn" in resp.data.decode()
    assert client.get("/help/tasks").status_code == 404


def test_it_is_linked(client):
    names = [p["title"] for p in client.get("/api/pages-list").get_json()]
    assert "Help" in names
    assert 'href="/help"' in client.get("/page/index").data.decode()   # the shortcuts dialog


# ── zone_help ────────────────────────────────────────────────────────────────

@pytest.fixture()
def mcp(monkeypatch):
    monkeypatch.setattr(settings, "MCP_TOKEN", TOKEN)


def test_zone_help_answers_from_the_manual_first(mcp, client):
    text, err = call(client, "zone_help", query="quick-add a task with a deadline")
    assert not err
    topics = re.findall(r"^topic: (.+)$", text, re.M)
    assert topics[0] == "manual/tasks" and "› Quick-add" in text.splitlines()[0]
    # The first half (rounded up) the manual's, then the spec's.
    assert [t.startswith("manual") for t in topics] == [True] * 3 + [False] * 2
    text, _ = call(client, "zone_help", query="quick-add", limit=1)
    assert re.findall(r"^topic: (.+)$", text, re.M) == ["manual/tasks"]


def test_zone_help_lists_and_reads_the_manual(mcp, client):
    text, _ = call(client, "zone_help")
    assert "manual/tasks" in text and text.index("manual") < text.index("readme")
    text, err = call(client, "zone_help", topic="manual/tasks.md")
    assert not err and text.startswith("topic: manual/tasks\n\n# ")


# ── zonewiki manual DIR ──────────────────────────────────────────────────────

def test_manual_command_writes_fragments(tmp_path):
    out = tmp_path / "docs"
    run = subprocess.run([str(binary()), "manual", "--docs", str(REPO), str(out)],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    listed = json.loads((out / "pages.json").read_text())
    assert listed[0] == {"name": "", "file": "index.html", "title": "Zonewiki's manual", "manual": True}
    files = {e["file"] for e in listed}
    assert {"tasks.html", "spec/index.html", "spec/tasks.html"} <= files
    assert all((out / f).is_file() for f in files)
    assert (out / "screenshots" / "page.png").is_file()
    index = (out / "index.html").read_text()
    assert "<html" not in index and 'href="getting-started.html"' in index and 'href="spec/index.html"' in index
    spec = (out / "spec" / "index.html").read_text()
    assert 'href="tasks.html"' in spec and 'href="../index.html"' in spec
    # Every relative link of a fragment leads to a file that was written.
    for f in files:
        for link in re.findall(r'\b(?:href|src)="([^"#:]+)(?:#[^"]*)?"', (out / f).read_text()):
            assert (out / f).parent.joinpath(html.unescape(link)).resolve().is_file(), (f, link)


def test_manual_command_usage(tmp_path):
    run = subprocess.run([str(binary()), "manual"], capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and "Usage: zonewiki manual" in run.stderr
    run = subprocess.run([str(binary()), "manual", "--docs", str(tmp_path), str(tmp_path / "x")],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 1 and "no docs/manual/README.md" in run.stderr
