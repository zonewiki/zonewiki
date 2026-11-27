"""How a markdown import holds at a big vault's size (Importing pages in
docs/spec/editor.md; The bench in docs/spec/testing.md): a generated
Obsidian vault of 5,000 notes in 50 folders and 500 pictures, zipped,
uploaded through Import pages, imported, searched, and undone; what each
step costs, how the repository and the server's memory grow, and how the
zip's limits (go/imports.go) stand to it. Not part of the suites (its
name is no test module's): run it with

    ./run.sh test tests/bench_import.py -n 0 -s

or with the tasks' bench, ./run.sh bench (CI's job). It prints a table and
fails where a budget is passed (BUDGETS; ZONE_BENCH_SIZE=small|team, team by
default). A budget is about twice what the tools image measures on a
developer's machine (2026-11-26), multiplied by ZONE_BENCH_SLACK.
"""

import io
import os
import random
import re
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import HOST

# Notes, folders, pictures and a picture's KB (random bytes, as a photo's
# are to zip). limit: near the zip's limit of entries (importMaxEntries),
# its pictures small to stay under upload-max; not run by CI.
NOTES, FOLDERS, FILES, FILE_KB = {"small": (500, 10, 50, 60), "team": (5000, 50, 500, 60),
                                  "limit": (18000, 180, 1800, 20)}[os.environ.get("ZONE_BENCH_SIZE", "team")]

SLACK = float(os.environ.get("ZONE_BENCH_SLACK", "1"))

# Milliseconds, megabytes and counts a measure may take at the team's size
# (measured on 2026-11-26 in the comment). At the limit's size (18,000
# notes, 1,800 pictures: 20,000 entries) the same run measured 11 s, 19 s,
# 15 s for the first search, 13.5 s and 19.5 s for the undos, 347 MB.
BUDGETS = {
    "the upload and its report (ms)": 6500,       # 3,113
    "the report (KB)": 40,                        # 20: lines are capped, not one a note
    "Import (ms)": 9000,                          # 4,575
    "the repository grew by (MB)": 60,            # 51.7: the pictures' 30 MB do not pack
    "the folder's index, first view (ms)": 700,   # 350
    "the home page after it (ms)": 50,            # 14
    "the first search after it (ms)": 5500,       # 2,688: 5,000 pages indexed
    "a search after that (ms)": 100,              # 30
    "the page list (ms)": 100,                    # 42
    "the server's memory, at most (MB)": 320,     # 157
    "the server's memory, now (MB)": 300,         # 142
    "Undo of the import (ms)": 2500,              # 1,232: one git log and status for all (was 13 s a file each)
    "Undo of that undo: all back (ms)": 9000,     # 4,349
}

WORDS = ("garden roses compost spring plan seeds water soil tomato harvest notes meeting draft review offer "
         "customer invoice budget sprint release docs idea project travel book recipe bread").split()


def words(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n))


def vault(rng):
    """The vault's files by their paths in the zip: notes linking each other
    by their names (Obsidian's way), some by a heading, with tags,
    callouts, a task list and pictures embedded; every picture is
    embedded once at least."""
    names = [f"Note {i:04d} {rng.choice(WORDS).title()}" for i in range(NOTES)]
    folders = [f"Area {i // 10:02d}/Topic {i:02d}" for i in range(FOLDERS)]
    pictures = [f"Attachments/picture-{i:03d}.png" for i in range(FILES)]
    files = {".obsidian/app.json": b"{}"}
    for i, name in enumerate(names):
        parts = []
        if rng.random() < 0.5:
            parts.append(f"---\ntags: [{rng.choice(WORDS)}]\naliases: [n{i}]\n---\n")
        parts.append(words(rng, 30).capitalize() + f" #{rng.choice(WORDS)}.\n")
        for _ in range(rng.randint(2, 5)):
            parts.append(f"\n## {words(rng, 3).title()}\n\n" + words(rng, rng.randint(40, 90)).capitalize() + ".\n")
        links = " ".join(f"[[{rng.choice(names)}]]" for _ in range(rng.randint(3, 8)))
        parts.append(f"\nSee {links} and [[{rng.choice(names)}#Plan|the plan]].\n")
        if rng.random() < 0.2:
            parts.append(f"\n> [!tip] {words(rng, 2)}\n> {words(rng, 12)}\n")
        if rng.random() < 0.3:
            parts.append(f"\n- [ ] {words(rng, 4)}\n- [x] {words(rng, 4)}\n")
        if i < FILES or rng.random() < 0.1:
            parts.append(f"\n![[{Path(pictures[i % FILES]).name}|400]]\n")
        files[f"{folders[i % FOLDERS]}/{name}.md"] = "".join(parts).encode()
    for p in pictures:
        files[p] = rng.randbytes(FILE_KB * 1024)
    return files


def zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(zipfile.ZipInfo("Big Vault/" + name, date_time=(2026, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
    return out.getvalue()


def repo_mb(root):
    out = subprocess.run(["du", "-sk", str(root / ".git")], check=True, capture_output=True, text=True).stdout
    return int(out.split()[0]) / 1024


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                          env=settings.GIT_ENV).stdout


def report(got):
    """Prints the measures and fails where one is over its budget."""
    over = []
    for name, value in got.items():
        budget = BUDGETS.get(name)
        if budget is not None and name.endswith("(ms)"):
            budget *= SLACK
        flag = ""
        if budget is not None and value > budget and NOTES == 5000:
            flag = f"   over the budget of {budget}"
            over.append(name)
        print(f"  {name:48s} {value:10.1f}{flag}")
    print()
    assert not over or os.environ.get("ZONE_TEST_RACE"), f"over budget: {over}"


def stamp():
    return time.perf_counter()


def ms(since):
    return (time.perf_counter() - since) * 1000


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"anna": ("pw", "")})
    return git_wiki_dir


def test_a_big_vault(wiki, client):
    rng = random.Random(11)
    files = vault(rng)
    data = zipped(files)
    got = {
        "the zip (MB)": len(data) / 1e6,
        "the vault unpacked (MB)": sum(len(b) for b in files.values()) / 1e6,
        "its entries": len(files) + FOLDERS + FOLDERS // 10 + 3,
    }
    client.post("/login", data={"username": "anna", "password": "pw"}, headers=HOST)
    server = client.live.ensure().proc.pid
    before = repo_mb(wiki)

    start = stamp()
    resp = client.post("/import", data={"file": (io.BytesIO(data), "Big Vault.zip")}, headers=HOST)
    got["the upload and its report (ms)"] = ms(start)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200, body[:3000]
    got["the report (KB)"] = len(body.encode()) / 1024
    ids = set(re.findall(r'name="id" value="([0-9a-f]{32})"', body))
    assert len(ids) == 1, body[:3000]

    start = stamp()
    resp = client.post("/import/apply", data={"id": ids.pop()}, headers=HOST)
    got["Import (ms)"] = ms(start)
    assert resp.status_code == 303 and resp.headers["Location"] == "/page/big-vault/index", resp.get_data(as_text=True)[:3000]
    sha = git(wiki, "rev-parse", "HEAD").strip()
    assert git(wiki, "log", "-1", "--format=%s") == f"Import {NOTES} pages and {FILES} files from Big Vault\n"
    got["files in its commit"] = len(git(wiki, "show", "--name-only", "--format=", sha).split())
    got["the repository grew by (MB)"] = repo_mb(wiki) - before

    start = stamp()
    resp = client.get("/page/big-vault/index", headers=HOST)
    got["the folder's index, first view (ms)"] = ms(start)
    assert resp.status_code == 200
    start = stamp()
    assert client.get("/page/index", headers=HOST).status_code == 200
    got["the home page after it (ms)"] = ms(start)
    start = stamp()
    resp = client.get("/search?q=compost+harvest", headers=HOST)
    got["the first search after it (ms)"] = ms(start)
    assert resp.status_code == 200 and "big-vault/" in resp.get_data(as_text=True)
    start = stamp()
    client.get("/search?q=invoice+budget", headers=HOST)
    got["a search after that (ms)"] = ms(start)
    start = stamp()
    pages = client.get("/api/pages-list", headers=HOST).get_json()
    got["the page list (ms)"] = ms(start)
    assert len(pages) > NOTES

    status = Path(f"/proc/{server}/status").read_text()
    got["the server's memory, at most (MB)"] = int(status.split("VmHWM:")[1].split()[0]) / 1024
    got["the server's memory, now (MB)"] = int(status.split("VmRSS:")[1].split()[0]) / 1024

    start = stamp()
    resp = client.post("/changes/undo", data={"sha": [sha]}, headers=HOST)
    got["Undo of the import (ms)"] = ms(start)
    assert resp.status_code == 303 and "undone" in resp.headers["Location"], resp.headers.get("Location")
    assert not (wiki / "big-vault").exists() or not any((wiki / "big-vault").rglob("*.md"))

    undo = git(wiki, "rev-parse", "HEAD").strip()
    start = stamp()
    resp = client.post("/changes/undo", data={"sha": [undo]}, headers=HOST)
    got["Undo of that undo: all back (ms)"] = ms(start)
    assert resp.status_code == 303 and "undone" in resp.headers["Location"], resp.headers.get("Location")
    assert len(list((wiki / "big-vault").rglob("*.md"))) == NOTES + 1

    print(f"\n\nAn import of {NOTES} notes in {FOLDERS} folders and {FILES} pictures of {FILE_KB} KB\n")
    report(got)
