"""How Zone's tasks hold at a team's size (docs/spec/testing.md, The bench):
a generated wiki of 100 users, 40 shared lists, 5,000 open tasks and 25,000
closed ones, in git, and what a page view, a list, a write and a search cost
on it, and how the repository grows. Not part of the suites (its name is no test module's): run it with

    ./run.sh test tests/bench_tasks.py -n 0 -s

It prints a table and fails where a budget is passed (BUDGETS, set for the
tools image on a developer's machine; ZONE_BENCH_SIZE=small|team, team by
default). The numbers are of the server alone, over HTTP on loopback: no
browser draws anything.
"""

import json
from pathlib import Path
import os
import random
import statistics
import subprocess
import time
import uuid

import pytest

from tests import settings
from tests.conftest import HOST

SIZES = {"small": (10, 5, 300, 1000), "team": (100, 40, 5000, 25000)}
USERS, LISTS, OPEN, CLOSED = SIZES[os.environ.get("ZONE_BENCH_SIZE", "team")]

# Median milliseconds (or bytes, files) a measure may take at the team's size.
BUDGETS = {
    "a page without tasks (ms)": 40,
    "My tasks (ms)": 150,
    "My tasks (KB)": 400,
    "one list (ms)": 200,
    "All tasks (ms)": 1500,
    "the due count (ms)": 40,
    "a task changed (ms)": 80,
    "a task completed (ms)": 90,
    "My tasks reordered (ms)": 300,
    "a search after a write (ms)": 1500,
    "the repository per 100 writes (MB)": 10,
    "the same, packed (MB)": 1.5,
    "the same, joined (MB)": 0.15,
    "the server's memory (MB)": 210,
    "joining the packs (ms)": 3000,
}

WORDS = ("review the draft of the offer for the customer and send it back with notes call about invoice "
         "plan sprint fix login bug write tests deploy release update docs order parts check budget").split()


def text(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n)).capitalize()


def task(rng, users, lists, closed=False, private=False):
    day = f"2026-{rng.randint(1, 11):02d}-{rng.randint(1, 28):02d}"
    t = {
        "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)), "title": text(rng, rng.randint(3, 8)),
        "description": text(rng, rng.randint(10, 60)) + "\n\n- [ ] " + text(rng, 4) if rng.random() < 0.4 else "",
        "tags": rng.sample(["work", "urgent", "q4", "ops", "idea"], rng.randint(0, 2)),
    }
    if not private:
        t["list"] = rng.choice(lists)
        if rng.random() < 0.7:
            t["assignee"] = rng.choice(users)
    t.update({
        "start_date": day if rng.random() < 0.3 else None, "due_date": day if rng.random() < 0.3 else None,
        "created_at": day + "T09:00:00", "closed_at": day + "T17:00:00" if closed else None,
        "wont_do": False, "keep": False, "last_done": None,
        "sort_order": float(rng.randint(0, 9999)) if rng.random() < 0.5 and not closed else None,
        "recurrence": None, "spawned_from": None,
        "history": [{"at": day + "T09:00", "event": "created", "by": rng.choice(users)}],
        "comments": [{"id": f"{rng.getrandbits(32):08x}", "at": day + "T10:00", "by": rng.choice(users), "text": text(rng, 12)}
                     for _ in range(rng.choice([0, 0, 0, 1, 3]))],
    })
    return t


@pytest.fixture()
def team(git_wiki_dir, monkeypatch):
    """The generated wiki, committed, and its users."""
    rng = random.Random(7)
    users = [f"user{i:03d}" for i in range(USERS)]
    lists = [f"list{i:02d}" for i in range(LISTS)]
    monkeypatch.setattr(settings, "USERS", {u: ("pw", "") for u in users})
    root = git_wiki_dir
    dump = lambda path, data: (path.parent.mkdir(parents=True, exist_ok=True),
                               path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"))
    dump(root / "_tasks/tasks.json", [task(rng, users, lists) for _ in range(OPEN)])
    dump(root / "_tasks/closed.json", [task(rng, users, lists, closed=True) for _ in range(CLOSED)])
    (root / "_tasks/lists.json").write_text(json.dumps(
        {"version": 2, "lists": [{"name": n} for n in lists], "pinned": lists[:3]}), encoding="utf-8")
    for u in users:      # everyone's Inbox
        dump(root / f"private/{u}/_tasks/tasks.json", [task(rng, users, lists, private=True) for _ in range(10)])
    (root / "plain.md").write_text("# Plain\n\nNo task list here.\n", encoding="utf-8")
    git = lambda *args: subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    git("add", "-A")
    git("-c", "user.name=bench", "-c", "user.email=bench@example.org", "commit", "-q", "-m", "The bench's wiki")
    return root, users, lists


def timed(fn, n=7):
    """The median of n runs, in ms, after one to warm up; and the last answer."""
    fn()
    times, out = [], None
    for _ in range(n):
        start = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times), out


def repo_mb(root):
    out = subprocess.run(["du", "-sk", str(root / ".git")], check=True, capture_output=True, text=True).stdout
    return int(out.split()[0]) / 1024


def test_the_tasks_at_a_teams_size(team, client):
    root, users, lists = team
    me = users[0]
    assert client.post("/login", data={"username": me, "password": "pw"}, headers=HOST).status_code == 302
    get = lambda url: (lambda: client.get(url, headers=HOST))
    got = {}

    ms, resp = timed(get("/page/plain"))
    assert resp.status_code == 200
    got["a page without tasks (ms)"] = ms
    ms, resp = timed(get("/tasks?mine=1"))
    got["My tasks (ms)"], got["My tasks (KB)"] = ms, len(resp.get_data()) / 1024
    ms, resp = timed(get(f"/tasks?list={lists[0]}"))
    got["one list (ms)"], got["one list (KB)"] = ms, len(resp.get_data()) / 1024
    ms, resp = timed(get("/tasks"), n=3)
    got["All tasks (ms)"], got["All tasks (KB)"] = ms, len(resp.get_data()) / 1024
    got["the due count (ms)"], _ = timed(get("/api/tasks/due-count?places=1"))

    mine = client.get("/api/tasks?mine=1", headers=HOST).get_json()
    shared = [t for t in mine if not t.get("private")]
    assert len(shared) > 10, "the bench's user has tasks assigned"
    # The wiki as joined (Repo.Join) is what the growth is measured from.
    join = ["git", "-C", str(root), "repack", "-a", "-d", "-l", "-q", "-f", "--window=50"]
    subprocess.run(join, check=True, capture_output=True)
    before = repo_mb(root)
    writes = 0

    ids = iter([t["id"] for t in shared])
    def change():
        nonlocal writes
        writes += 1
        return client.put(f"/api/tasks/{shared[0]['id']}", json={"title": f"Changed {writes}"}, headers=HOST)
    got["a task changed (ms)"], resp = timed(change, n=15)
    assert resp.status_code == 200

    def complete():
        nonlocal writes
        writes += 1
        return client.put(f"/api/tasks/{next(ids)}", json={"completed": True}, headers=HOST)
    next(ids)                                   # not the one being changed
    got["a task completed (ms)"], resp = timed(complete, n=7)
    assert resp.status_code == 200

    order = [t["id"] for t in mine[:40]]
    def reorder():
        nonlocal writes
        writes += 1
        rng_order = order[writes % 7:] + order[:writes % 7]
        return client.post("/api/tasks/reorder", json={"mine": True, "order": rng_order}, headers=HOST)
    got["My tasks reordered (ms)"], resp = timed(reorder, n=7)
    assert resp.status_code == 204

    def search():
        change()
        return client.get("/search?q=invoice+budget", headers=HOST)
    got["a search after a write (ms)"], resp = timed(search, n=3)
    assert resp.status_code == 200
    got["the repository per 100 writes (MB)"] = (repo_mb(root) - before) / writes * 100
    # Packed, as Zone packs every 50 commits (Repo.Pack): the versions of a file as differences.
    subprocess.run(["git", "-C", str(root), "repack", "-d", "-l", "-q"], check=True, capture_output=True)
    got["the same, packed (MB)"] = (repo_mb(root) - before) / writes * 100
    # Joined, as Zone joins its packs after a few of them: the differences computed anew.
    start = time.perf_counter()
    subprocess.run(join, check=True, capture_output=True)
    got["joining the packs (ms)"] = (time.perf_counter() - start) * 1000
    got["the same, joined (MB)"] = (repo_mb(root) - before) / writes * 100

    # What the Zone process holds after all of that (its resident memory):
    # the tasks it keeps parsed and the search index's, not every closed
    # task of the wiki's years.
    status = Path(f"/proc/{client.live.ensure().proc.pid}/status").read_text()
    got["the server's memory (MB)"] = int(status.split("VmRSS:")[1].split()[0]) / 1024

    print(f"\n\nTasks at a team's size: {USERS} users, {LISTS} lists, {OPEN} open and {CLOSED} closed tasks; "
          f"{sum(p.stat().st_size for p in (root / '_tasks').rglob('tasks.json')) / 1e6:.1f} MB open, "
          f"{sum(p.stat().st_size for p in (root / '_tasks').rglob('closed*.json')) / 1e6:.1f} MB closed, "
          f"in {len(list((root / '_tasks').rglob('*.json')))} files\n")
    over = []
    for name, value in got.items():
        budget = BUDGETS.get(name)
        flag = ""
        if budget is not None and value > budget and (USERS, OPEN) == SIZES["team"][:1] + SIZES["team"][2:3]:
            flag = f"   over the budget of {budget}"
            over.append(name)
        print(f"  {name:38s} {value:10.1f}{flag}")
    print()
    assert not over, f"over budget: {over}"
