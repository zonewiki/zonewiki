"""How Zone's tasks hold at a team's size (docs/spec/testing.md, The bench):
a generated wiki of 100 users, 40 shared lists, 5,000 open tasks and 25,000
closed ones, in git, and what a page view, a list, a write and a search cost
on it, how the repository grows, and what a write waits beside a join of
its packs once it has a history; and its pages for some (the last test: ten
groups' folders beside 2,000 shared pages, thirty people searching). Not part of the suites (its name is no test module's): run it with

    ./run.sh test tests/bench_tasks.py -n 0 -s

or, with the run against a race-built server after it, ./run.sh bench (CI's
job). It prints a table and fails where a budget is passed (BUDGETS;
ZONE_BENCH_SIZE=small|team, team by default). The numbers are of the server
alone, over HTTP on loopback: no browser draws anything.

A budget guards a design, not a millisecond: a time is about twice what the
tools image measures on a developer's machine (2026-11-16), so a busy
machine passes and a read that waits for a commit again, or a write that
reads the wiki again, does not. ZONE_BENCH_SLACK multiplies the time budgets
(CI: 3; its runners are slower); sizes and counts are the same anywhere.
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
SLACK = float(os.environ.get("ZONE_BENCH_SLACK", "1"))

BUDGETS = {
    "a page without tasks (ms)": 50,            # measured 19
    "My tasks (ms)": 50,                        # 24
    "My tasks (KB)": 400,
    "one list (ms)": 50,                        # 21
    "All tasks (ms)": 50,                       # 17: the page without its tasks
    "All tasks (KB)": 100,
    "All tasks' tasks, gzipped (ms)": 100,      # 41
    "All tasks' tasks, gzipped (KB)": 700,
    "the due count (ms)": 40,                   # 15
    "a task changed (ms)": 60,                  # 29
    "a task completed (ms)": 70,                # 32
    "My tasks reordered (ms)": 50,              # 16
    "ten tasks tagged at once (ms)": 80,        # 41
    "a search after a write (ms)": 250,         # 118
    "the repository per 100 writes (MB)": 7,    # 3.4
    "the same, packed (MB)": 1.2,               # 0.6
    "the same, joined (MB)": 0.15,
    "the server's memory (MB)": 210,
    "joining the packs (ms)": 3500,             # 1,727
    # Beside writes (test_reads_beside_writes): the 95th percentile, and the
    # slowest. A read never waits for a write's commit, nor for the packing
    # a commit now and then waits for (which is the slowest write).
    "beside writes: a page without tasks, p95 (ms)": 90,     # 42
    "beside writes: the due count, p95 (ms)": 90,            # 43
    "beside writes: My tasks, p95 (ms)": 90,                 # 38
    "beside writes: a task changed, p95 (ms)": 100,          # 49
    "beside writes: a page without tasks, slowest (ms)": 250,
    "beside writes: the due count, slowest (ms)": 250,
    "beside writes: My tasks, slowest (ms)": 250,
    "beside writes: a task changed, slowest (ms)": 2500,
    # Beside a join of the packs (test_a_write_beside_a_join): a write never
    # waits for it, however long the history makes it.
    "beside a join: a task changed, slowest (ms)": 250,
}

# How many commits the history has that a join is measured on (AGED).
AGED = {"small": 2000, "team": 20000}[os.environ.get("ZONE_BENCH_SIZE", "team")]

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
        {"version": 3, "lists": [{"name": n} for n in lists], "pinned": lists[:3]}), encoding="utf-8")
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


def report(got):
    """Prints the measures and fails where one is over its budget."""
    over = []
    for name, value in got.items():
        budget = BUDGETS.get(name)
        if budget is not None and name.endswith("(ms)"):
            budget *= SLACK
        flag = ""
        if budget is not None and value > budget and (USERS, OPEN) == SIZES["team"][:1] + SIZES["team"][2:3]:
            flag = f"   over the budget of {budget}"
            over.append(name)
        print(f"  {name:48s} {value:10.1f}{flag}")
    print()
    # Under the race detector (ZONE_TEST_RACE) the server is several times
    # slower: the run is for what it reports, not for the budgets.
    assert not over or os.environ.get("ZONE_TEST_RACE"), f"over budget: {over}"


READERS, WRITERS, SECONDS, PAUSE = 6, 4, 15, 0.25


def test_reads_beside_writes(team, client):
    """What a reader waits while others write: READERS people who open pages,
    My tasks and whose pages ask for the counts without a pause, and WRITERS
    who each change a task every PAUSE seconds (a hundred people's writes of
    a day in a few minutes), for SECONDS. One client at a time
    (the test above) doesn't see a reader held up by a write's commit."""
    import threading
    from tests.goserver import GoClient
    root, users, lists = team

    def signed_in(user):
        c = GoClient.__new__(GoClient)
        c.live, c.cookies = client.live, {}
        assert c.post("/login", data={"username": user, "password": "pw"}, headers=HOST).status_code == 302
        return c

    clients = [signed_in(u) for u in users[:READERS + WRITERS]]
    for c in clients:                                     # warm: the files parsed, the counts kept
        assert c.get("/tasks?mine=1", headers=HOST).status_code == 200
    times, failed, stop = {}, [], time.monotonic() + SECONDS
    lock = threading.Lock()

    def timed_get(c, name, call):
        start = time.perf_counter()
        resp = call()
        ms = (time.perf_counter() - start) * 1000
        with lock:
            times.setdefault(name, []).append(ms)
            if resp.status_code >= 400:
                failed.append((name, resp.status_code))

    def reader(c, n):
        urls = [("a page without tasks", "/page/plain"), ("the due count", "/api/tasks/due-count?places=1"),
                ("My tasks", "/api/tasks?mine=1")]
        i = n
        while time.monotonic() < stop:
            name, url = urls[i % len(urls)]
            timed_get(c, name, lambda: c.get(url, headers=HOST))
            i += 1

    def writer(c, n):
        mine = [t for t in c.get("/api/tasks?mine=1", headers=HOST).get_json() if not t.get("private")]
        i = 0
        while time.monotonic() < stop:
            t = mine[i % len(mine)]
            timed_get(c, "a task changed", lambda: c.put(f"/api/tasks/{t['id']}", json={"title": f"Changed {n} {i}"}, headers=HOST))
            i += 1
            time.sleep(PAUSE)

    threads = [threading.Thread(target=reader, args=(c, n)) for n, c in enumerate(clients[:READERS])]
    threads += [threading.Thread(target=writer, args=(c, n)) for n, c in enumerate(clients[READERS:])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failed, failed[:5]
    p95 = lambda values: sorted(values)[int(len(values) * 0.95)]
    got = {f"beside writes: {name}, p95 (ms)": p95(values) for name, values in times.items()}
    for name, values in times.items():
        got[f"beside writes: {name}, slowest (ms)"] = max(values)
    print(f"\n\nReads beside writes: {READERS} readers, {WRITERS} writers, {SECONDS} s; "
          + ", ".join(f"{len(v)} × {name}" for name, v in times.items()) + "\n")
    report(got)


def age(root, users, lists, commits):
    """Gives the wiki's repository a history of `commits` changes to 40 files
    of tasks (which its last commit removes: the wiki is as it was), in one
    pack, and 55 small packs after it: more than Zone leaves beside each
    other (packsMost in package gitrepo), so its next pack is followed by a
    join, which reads all of that history."""
    rng = random.Random(11)
    git = lambda *args, **kw: subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, **kw)
    ref = git("symbolic-ref", "HEAD", text=True).stdout.strip().encode()
    files = [[task(rng, users, lists) for _ in range(30)] for _ in range(40)]
    when = 1_780_000_000

    def commit(out, first, change):
        nonlocal when
        when += 1
        out.write(b"commit %s\ncommitter bench <bench@example.org> %d +0000\ndata 6\nChange\n" % (ref, when))
        if first:
            out.write(b"from %s^0\n" % ref)
        out.write(change + b"\n")

    def blob(path, text):
        data = text.encode()
        return b"M 644 inline %s\ndata %d\n%s" % (path.encode(), len(data), data)

    imp = subprocess.Popen(["git", "-C", str(root), "fast-import", "--quiet"], stdin=subprocess.PIPE)
    for c in range(commits):
        i = rng.randrange(len(files))
        rng.choice(files[i])["title"] = text(rng, 6)
        commit(imp.stdin, c == 0, blob(f"_bench/list{i:02d}.json", json.dumps(files[i], indent=2) + "\n"))
    commit(imp.stdin, False, b"D _bench")
    imp.stdin.close()
    assert imp.wait() == 0
    git("repack", "-a", "-d", "-q")
    for n in range(55):
        change = blob("_bench/tick", f"{n}\n") if n < 54 else b"D _bench"
        imp = subprocess.Popen(["git", "-C", str(root), "-c", "fastimport.unpackLimit=0", "fast-import", "--quiet"],
                               stdin=subprocess.PIPE)
        commit(imp.stdin, True, change)
        imp.stdin.close()
        assert imp.wait() == 0
    assert git("status", "--porcelain", text=True).stdout == "", "the wiki's files are as they were"


def test_a_write_beside_a_join(team, client):
    """What a write waits while Zone joins the packs of a repository with a
    history (AGED commits; Zone packs the repository itself, in
    docs/spec/rendering.md): the join reads all of it, for seconds here and
    for minutes after a team's year, and until 2026-11-16 every save of the
    wiki waited for it. One user changes a task again and again; the fiftieth
    commit starts Zone's pack, the join follows, and each write beside it is
    timed."""
    root, users, lists = team
    age(root, users, lists, AGED)
    packs = lambda: len(list((root / ".git/objects/pack").glob("*.pack")))
    before = packs()
    assert client.post("/login", data={"username": users[0], "password": "pw"}, headers=HOST).status_code == 302
    shared = [t for t in client.get("/api/tasks?mine=1", headers=HOST).get_json() if not t.get("private")]
    beside, began, ended, writes = [], None, None, 0
    stop = time.monotonic() + 600
    while time.monotonic() < stop:
        writes += 1
        start = time.perf_counter()
        resp = client.put(f"/api/tasks/{shared[writes % len(shared)]['id']}", json={"title": f"Changed {writes}"}, headers=HOST)
        ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        n = packs()
        if began is None and n > before:
            began = time.monotonic()         # Zone's pack is there: the join is next
        elif began is not None:
            beside.append(ms)
            if n < before:                   # the packs it stands for are gone
                ended = time.monotonic()
                break
    assert ended is not None, f"no join after {writes} writes ({packs()} packs, {before} before)"
    fsck = subprocess.run(["git", "-C", str(root), "fsck", "--full", "--strict"], capture_output=True, text=True)
    assert fsck.returncode == 0, fsck.stderr
    print(f"\n\nA write beside a join: a history of {AGED} commits, joined in {ended - began:.1f} s, "
          f"{len(beside)} writes meanwhile (median {statistics.median(beside):.0f} ms)\n")
    report({"beside a join: a task changed, slowest (ms)": max(beside)})
    assert len(beside) >= 10, f"only {len(beside)} writes went through beside the join: they waited for it, or it was too short to say"


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
    # What a list of every task asks for when it starts: its tasks, gzipped.
    ms, resp = timed(lambda: client.get("/api/tasks", headers={**HOST, "Accept-Encoding": "gzip"}), n=3)
    assert resp.headers.get("Content-Encoding") == "gzip"
    got["All tasks' tasks, gzipped (ms)"], got["All tasks' tasks, gzipped (KB)"] = ms, len(resp.get_data()) / 1024

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

    # A batch: ten open tasks tagged at once, which reads no closed file
    # (after the repository's growth is measured: that is of single writes).
    ten = [t["id"] for t in shared[20:30]]
    assert len(ten) == 10, "the bench's user has thirty shared tasks"
    batches = 0
    def tag():
        nonlocal batches
        batches += 1
        return client.post("/api/tasks/batch-tag", json={"ids": ten, "add": [f"batch{batches}"]}, headers=HOST)
    got["ten tasks tagged at once (ms)"], resp = timed(tag, n=7)
    assert resp.status_code == 200 and len(resp.get_json()) == 10

    # What the Zone process holds after all of that (its resident memory):
    # the tasks it keeps parsed and the search index's, not every closed
    # task of the wiki's years.
    status = Path(f"/proc/{client.live.ensure().proc.pid}/status").read_text()
    got["the server's memory (MB)"] = int(status.split("VmRSS:")[1].split()[0]) / 1024

    print(f"\n\nTasks at a team's size: {USERS} users, {LISTS} lists, {OPEN} open and {CLOSED} closed tasks; "
          f"{sum(p.stat().st_size for p in (root / '_tasks').rglob('tasks.json')) / 1e6:.1f} MB open, "
          f"{sum(p.stat().st_size for p in (root / '_tasks').rglob('closed*.json')) / 1e6:.1f} MB closed, "
          f"in {len(list((root / '_tasks').rglob('*.json')))} files\n")
    report(got)


# ── Pages and groups ─────────────────────────────────────────────────────────

# Groups, shared pages, and pages in each group's folder.
GROUPS, SHARED_PAGES, GROUP_PAGES = {"small": (3, 200, 20), "team": (10, 2000, 200)}[os.environ.get("ZONE_BENCH_SIZE", "team")]
# People who search: more than the server keeps indexes for (maxViews in go/retrieval.go).
SEARCHERS = 30


@pytest.fixture()
def paged(team, monkeypatch):
    """The team's wiki with pages: SHARED_PAGES for everyone, GROUP_PAGES in
    the folder of each of GROUPS groups (300 words each, of a vocabulary of
    5,000), every user in one group and every fifth in a second, one group
    made of three others, and a guide that pulls a page in."""
    root, users, lists = team
    rng = random.Random(13)
    groups = [f"team{i}" for i in range(GROUPS)]
    in_groups = {u: sorted({groups[i % GROUPS]} | ({groups[(i + 3) % GROUPS]} if i % 5 == 0 else set()))
                 for i, u in enumerate(users)}
    monkeypatch.setattr(settings, "USER_SETTINGS", {u: {"groups": ", ".join(g)} for u, g in in_groups.items()})
    monkeypatch.setattr(settings, "GROUPS", {"division": ", ".join(groups[:3])})
    vocabulary = WORDS + ["".join(rng.choice("abcdefghijklmnoprstuvw") for _ in range(rng.randint(4, 9))) for _ in range(5000)]
    page = lambda title, extra="": f"# {title}\n\n" + "\n\n".join(
        " ".join(rng.choice(vocabulary) for _ in range(60)).capitalize() + "." for _ in range(5)) + f"\n\n{extra}\n"
    for i in range(SHARED_PAGES):
        path = root / f"area{i % 20:02d}/page{i:04d}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(page(f"Page {i}", "[[plain]]"), encoding="utf-8")
    for g in groups:
        (root / "groups" / g).mkdir(parents=True)
        for i in range(GROUP_PAGES):
            (root / f"groups/{g}/note{i:03d}.md").write_text(page(f"{g} note {i}", f"Only for {g}: word{g}."), encoding="utf-8")
    (root / "AGENTS.md").write_text("# Guide\n\n@area00/page0000.md\n", encoding="utf-8")
    git = lambda *args: subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    git("add", "-A")
    git("-c", "user.name=bench", "-c", "user.email=bench@example.org", "commit", "-q", "-m", "The bench's pages")
    return root, users, groups, in_groups


BUDGETS.update({
    "a group's page (ms)": 50,                                  # measured 23
    "the page list (ms)": 50,                                   # 24
    "the page list (KB)": 500,
    "a search, the same person again (ms)": 150,                # 71
    # Past the indexes the server keeps (maxViews: 8), a search puts its
    # person's together again: some 45 ms more, and one at a time. With 32
    # kept it was 70 ms in turn and 87 at once, for 230 MB more memory
    # (2026-11-17): what these two guard is that it gets no worse.
    f"a search, {SEARCHERS} people in turn (ms)": 250,          # 118
    f"{SEARCHERS} people searching at once, p95 (ms)": 1200,    # 559
    # A page's view asks whether its reader may change it, which walks the
    # wiki's guides at most once a second (guideImports): it doesn't show.
    "a page beside them, p95 (ms)": 80,                         # 37
    "a page beside them, slowest (ms)": 250,                    # 39
    "the server's memory with pages (MB)": 500,                 # 331
})


def test_pages_and_groups_at_a_teams_size(paged, client):
    """Pages for some at a team's size: what a member's page, the page list
    and a search cost with GROUPS group folders beside the shared pages, and
    what a search costs when more people search than the server keeps
    indexes for (SEARCHERS, in turn and at once): each of them has an index
    of their own, the shared pages' plus their groups' and their private
    ones (Search in docs/spec/users.md, Group pages)."""
    import threading
    from tests.goserver import GoClient
    root, users, groups, in_groups = paged

    def signed_in(user):
        c = GoClient.__new__(GoClient)
        c.live, c.cookies = client.live, {}
        assert c.post("/login", data={"username": user, "password": "pw"}, headers=HOST).status_code == 302
        return c

    people = [signed_in(u) for u in users[:min(SEARCHERS, len(users))]]
    me, mine = people[1], in_groups[users[1]][0]
    other = next(g for g in groups if g not in in_groups[users[1]] and g not in groups[:3])
    got = {}
    search = lambda c, q: c.get(f"/api/search?q={q}", headers=HOST)

    start = time.perf_counter()
    assert search(me, "invoice+budget").status_code == 200
    got["the first search: the index made (ms)"] = (time.perf_counter() - start) * 1000
    # A group's word is found by who is in the group, and by nobody else.
    assert any(r.get("slug", "").startswith(f"groups/{mine}/") for r in search(me, f"word{mine}").get_json())
    assert not [r for r in search(me, f"word{other}").get_json() if r.get("slug", "").startswith(f"groups/{other}/")]
    assert me.get(f"/page/groups/{other}/note000", headers=HOST).status_code == 404

    get = lambda url: (lambda: me.get(url, headers=HOST))
    ms, resp = timed(get(f"/page/groups/{mine}/note000"))
    assert resp.status_code == 200
    got["a group's page (ms)"] = ms
    ms, resp = timed(get("/api/pages-list"))
    assert resp.status_code == 200
    got["the page list (ms)"], got["the page list (KB)"] = ms, len(resp.get_data()) / 1024
    got["a search, the same person again (ms)"], _ = timed(lambda: search(me, "invoice+budget"), n=15)

    # In turn: each search is by another person than the one before, so past
    # the indexes the server keeps every one is put together again.
    for c in people:
        search(c, "invoice+budget")
    times = []
    for _ in range(3):
        for c in people:
            start = time.perf_counter()
            assert search(c, "invoice+budget").status_code == 200
            times.append((time.perf_counter() - start) * 1000)
    got[f"a search, {len(people)} people in turn (ms)"] = statistics.median(times)
    got[f"a search, {len(people)} people in turn, slowest (ms)"] = max(times)

    # At once: everyone searches twice a second (as someone who types), for
    # ten seconds, while one of them opens pages: a page's view asks whether
    # its reader may change it, which walks the wiki's guides once a second.
    times, views, failed, stop = [], [], [], time.monotonic() + 10
    lock = threading.Lock()

    def searcher(c, n):
        time.sleep(n * 0.5 / len(people))
        i = n
        while time.monotonic() < stop:
            start = time.perf_counter()
            resp = search(c, ["invoice+budget", "release+notes", "sprint+plan"][i % 3])
            with lock:
                times.append((time.perf_counter() - start) * 1000)
                if resp.status_code != 200:
                    failed.append(resp.status_code)
            i += 1
            time.sleep(0.5)

    def viewer():
        i = 0
        while time.monotonic() < stop:
            start = time.perf_counter()
            resp = me.get(f"/page/area{i % 20:02d}/page{i % 20:04d}", headers=HOST)
            with lock:
                views.append((time.perf_counter() - start) * 1000)
                if resp.status_code != 200:
                    failed.append(resp.status_code)
            i += 1
            time.sleep(0.1)

    threads = [threading.Thread(target=searcher, args=(c, n)) for n, c in enumerate(people)] + [threading.Thread(target=viewer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failed, failed[:5]
    p95 = lambda values: sorted(values)[int(len(values) * 0.95)]
    got[f"{len(people)} people searching at once, p95 (ms)"] = p95(times)
    got[f"{len(people)} people searching at once, slowest (ms)"] = max(times)
    got["a page beside them, p95 (ms)"] = p95(views)
    got["a page beside them, slowest (ms)"] = max(views)
    status = Path(f"/proc/{client.live.ensure().proc.pid}/status").read_text()
    got["the server's memory with pages (MB)"] = int(status.split("VmRSS:")[1].split()[0]) / 1024

    print(f"\n\nPages and groups at a team's size: {len(users)} users, {GROUPS} groups, {SHARED_PAGES} shared pages and "
          f"{GROUP_PAGES} in each group's folder; {len(people)} people search ({len(times)} searches at once in 10 s, "
          f"{len(views)} page views beside them)\n")
    report(got)
