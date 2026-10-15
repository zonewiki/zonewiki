"""Deck tasks on disk and their model: the _deck/ files (open tasks,
completed, yearly archives) under one lock with atomic writes, recurrence,
history, comments, and what pages and search need from tasks (links to
pages, [[task:…]] references, search documents)."""

import calendar
import functools
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import search
from mdtext import plain_task_title, section_anchor, to_slug
from decksyntax import clean_tags, in_scope, _MD_LINK_RE, TASK_REF_RE, WIKILINK_RE


# Serializes every read-modify-write on the deck JSON files. The Flask dev
# server is threaded, so without this two concurrent task mutations can
# interleave their read → modify → write and lose each other's changes (or, if
# one reads while the other has truncated the file mid-write, silently wipe the
# whole list). All mutating endpoints hold this lock for the full
# read-modify-write-commit sequence. RLock so nested helpers can re-acquire.
_LOCK = threading.RLock()


def serialized(fn):
    """Serialize a view's whole read-modify-write-commit under _LOCK."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


# These are set by init_deck() from server.py at startup
LEAF_DATA: Path = Path(".")


DECK_FILE: Path = Path("_deck/tasks.json")   # the open tasks; the rest sit beside it
DECK_DIR_NAME = "_deck"
GIT_ENABLED: bool = False
_git_commit_fn = None  # type: ignore


def _completed_path() -> Path:
    """Derive the completed-tasks file path from DECK_FILE (same directory)."""
    return DECK_FILE.parent / "completed.json"


def init_deck(wiki_dir: Path, git_enabled: bool, git_commit_fn) -> None:
    """Initialize module-level state from the main server."""
    global LEAF_DATA, DECK_FILE, GIT_ENABLED, _git_commit_fn
    LEAF_DATA = wiki_dir
    DECK_FILE = wiki_dir / DECK_DIR_NAME / "tasks.json"
    GIT_ENABLED = git_enabled
    _git_commit_fn = git_commit_fn
    archive_old_completed()


class DeckCorrupt(Exception):
    """Existing deck file is present but unreadable — refuse to overwrite it."""


def timestamp() -> str:
    """The current time as tasks store it (created_at, completed_at)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _write_tasks(path: Path, tasks: list[dict]) -> None:
    _atomic_write(path, json.dumps(tasks, indent=2, ensure_ascii=False))


def _atomic_write(path: Path, text: str) -> None:
    """Write text by writing a temp file then os.replace-ing it into place.

    os.replace is atomic on the same filesystem, so a concurrent reader always
    sees either the complete old file or the complete new file — never the
    zero-length window that ``open('w')`` exposes between truncate and write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)   # _deck/ in a new wiki
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_list_strict(path: Path) -> list[dict]:
    """Read a JSON list, raising DeckCorrupt if the file exists but is unusable.

    Used on the write path (under _LOCK) so a corrupt or torn file aborts the
    request instead of being silently treated as an empty list and overwritten,
    which would destroy every task. A genuinely empty deck is stored as ``[]``,
    so a zero-length file only ever means a torn/failed write."""
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise DeckCorrupt(str(path))
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise DeckCorrupt(str(path))
    if not isinstance(data, list):
        raise DeckCorrupt(str(path))
    return _normalize_list(data)


def _normalize_list(tasks: list) -> list:
    """Tasks as read from a file, their tags cleaned (clean_tags): a file
    edited by hand may have "local llm" or "#work"."""
    for t in tasks:
        if isinstance(t, dict) and isinstance(t.get("tags"), list):
            t["tags"] = clean_tags(t["tags"])
    return tasks


def read_deck_strict() -> list[dict]:
    return _read_list_strict(DECK_FILE)


def _read_completed_strict() -> list[dict]:
    return _read_list_strict(_completed_path())


def _read_list_lenient(path: Path) -> list[dict]:
    """Read a JSON list, treating a missing or unreadable file as empty.

    Used on read-only paths; write paths use _read_list_strict instead."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_list(data) if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def read_deck() -> list[dict]:
    """Read active (non-completed) tasks."""
    return _read_list_lenient(DECK_FILE)


def _read_completed() -> list[dict]:
    """Read completed tasks."""
    return _read_list_lenient(_completed_path())


_ARCHIVE_NAME_RE = re.compile(r"completed-(\d{4})\.json")


def _archive_glob() -> list[Path]:
    """Per-year archive files, e.g. completed-2025.json (sorted)."""
    return sorted(p for p in DECK_FILE.parent.glob("completed-*.json")
                  if _ARCHIVE_NAME_RE.fullmatch(p.name))


def _archive_path(year: str) -> Path:
    return DECK_FILE.parent / f"completed-{year}.json"


def _completed_year(task: dict) -> str:
    """The year a completed task belongs to ("" when its date is unusable)."""
    year = (task.get("completed_at") or "")[:4]
    return year if re.fullmatch(r"\d{4}", year) else ""


def past_year(task: dict, current: str | None = None) -> str | None:
    """The task's completion year when it is before this one (it belongs in
    that year's archive), else None (the current completed file)."""
    year = _completed_year(task)
    return year if year and year < (current or str(date.today().year)) else None


def read_completed_all_strict() -> list[dict]:
    """Every completed task, this year's file and the yearly archives, for
    the write path: a task from an earlier year can be edited, reopened or
    deleted like any other. _write_completed puts each back by year. The
    archives come first, so tasks moving into one land after its own."""
    tasks = []
    for path in _archive_glob():
        tasks.extend(_read_list_strict(path))
    return tasks + _read_list_strict(_completed_path())


def _write_completed(completed: list[dict]) -> list[Path]:
    """Write completed tasks split by year: this year's into completed.json,
    earlier years' into completed-YYYY.json. Only files whose content
    changes are written; returns their paths (for the commit)."""
    current = str(date.today().year)
    by_path: dict[Path, list[dict]] = {_completed_path(): []}
    for path in _archive_glob():
        by_path[path] = []
    for t in completed:
        year = past_year(t, current)
        by_path.setdefault(_archive_path(year) if year else _completed_path(), []).append(t)
    changed = []
    for path, tasks in by_path.items():
        try:
            if json.loads(path.read_text(encoding="utf-8")) == tasks:
                continue
        except (OSError, ValueError):
            pass
        _write_tasks(path, tasks)
        changed.append(path)
    return changed


def completed_in_year(year: str) -> list[dict]:
    """The Completed view of one year: tasks completed in it (this year's
    view also takes tasks without a usable date)."""
    current = str(date.today().year)
    with _LOCK:
        tasks = _read_completed()
        if year != current:
            tasks = tasks + _read_list_lenient(_archive_path(year))
    if year == current:
        return [t for t in tasks if not past_year(t, current)]
    return [t for t in tasks if _completed_year(t) == year]


def completed_years(scope: str | None) -> list[str]:
    """Years with completed tasks in scope (see in_scope), newest first; this
    year is always included."""
    current = str(date.today().year)
    years = {current}
    with _LOCK:
        for t in _read_completed():
            if in_scope(t, scope) and past_year(t, current):
                years.add(_completed_year(t))
        for path in _archive_glob():
            if any(in_scope(t, scope) for t in _read_list_lenient(path)):
                years.add(_ARCHIVE_NAME_RE.fullmatch(path.name).group(1))
    return sorted(years, reverse=True)


def _read_archived() -> list[dict]:
    """Completed tasks rolled into per-year archives (search; the Completed
    view shows them by year)."""
    tasks: list[dict] = []
    for path in _archive_glob():
        tasks.extend(_read_list_lenient(path))
    return tasks


def archive_old_completed() -> None:
    """Roll completed tasks from previous years into completed-YYYY.json.

    Runs at startup so the current completed file stays small; every write
    also files tasks by year (_write_completed), so this only matters after
    a year change without writes. Skips silently if a file is unreadable —
    archiving is an optimization, never worth failing startup over."""
    with _LOCK:
        try:
            completed = read_completed_all_strict()
        except DeckCorrupt:
            return
        changed = _write_completed(completed)
        if changed and GIT_ENABLED and _git_commit_fn:
            _git_commit_fn(changed, "Archive completed deck tasks")


def _read_all() -> list[dict]:
    """Read all tasks (active + completed) — for search and backlinks.

    Holds _LOCK so the active/completed pair is a consistent snapshot rather
    than reading one file before and the other after a concurrent write that
    moves a task between them.
    """
    with _LOCK:
        return read_deck() + _read_completed()


def all_tasks() -> list[dict]:
    """Every task: open, completed and archived (completed-YYYY.json)."""
    return _read_all() + _read_archived()


def write_deck(tasks: list[dict], message: str = "Update deck") -> None:
    _write_tasks(DECK_FILE, tasks)
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn([DECK_FILE], message)


def write_both(active: list[dict], completed: list[dict], message: str = "Update deck") -> None:
    """Write the open tasks and every completed task (all of them, as read by
    read_completed_all_strict; filed by year) in a single git commit."""
    _write_tasks(DECK_FILE, active)
    changed = _write_completed(completed)
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn([DECK_FILE, *changed], message)


def new_task(**overrides) -> dict:
    """Return a task dict with every field at its default, plus overrides.

    Single source of truth for the task schema — create, recurring spawn, and
    field updates all build on this skeleton."""
    task = {
        "id": str(uuid.uuid4()),
        "title": "",
        "description": "",
        "tags": [],
        "due_date": None,
        "created_at": timestamp(),
        "completed_at": None,
        "wont_do": False,
        "start_date": None,
        "sort_order": None,
        "recurrence": None,
        "spawned_from": None,
        "keep": False,
        "last_done": None,
        "history": [],
        "comments": [],
    }
    task.update(overrides)
    return task


def _add_months(base: date, months: int, day: int | None = None) -> date:
    """Add months to a date, clamping the day to the target month's last day
    (Jan 31 + 1 month = Feb 28/29, not an invalid date and not always the 28th).
    `day` overrides base.day as the wanted day of month, so a series anchored
    on the 31st returns to the 31st after passing through a shorter month."""
    month = base.month + months
    year = base.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(day or base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# The largest "every N": far beyond any real series, and small enough
# that stepping a date never leaves the calendar (year 9999).
REC_INTERVAL_MAX = 999


def _rec_step(base: date, rtype: str, interval: int, day: int | None = None) -> date:
    if rtype == "weekly":
        return base + timedelta(weeks=interval)
    if rtype == "monthly":
        return _add_months(base, interval, day)
    if rtype == "yearly":
        # Reuse month arithmetic so Feb-29 clamps to Feb-28 in non-leap years.
        return _add_months(base, 12 * interval, day)
    return base + timedelta(days=interval)  # daily (and any unknown type)


def _next_occurrence(rec: dict, base: date, anchored: bool, today: date) -> tuple[date, dict]:
    """Step a recurrence from base and catch up to >= today.

    Monthly/yearly series remember their day of month in ``recurrence.day``:
    stepping from the previous (possibly clamped) date alone would drift —
    Jan 31 → Feb 28 → Mar 28 → … — so the anchor day is taken from the first
    date the series stepped from and carried on every spawned copy. When the
    base is the completion day (``from: completion``) there is no anchor.
    Returns (next date, recurrence dict for the spawned instance)."""
    rtype = rec.get("type", "daily")
    interval = rec.get("interval", 1)
    rec = dict(rec)
    day = None
    if rtype in ("monthly", "yearly") and anchored:
        day = rec.get("day") or base.day
        rec["day"] = day
    nxt = _rec_step(base, rtype, interval, day)
    while nxt < today:
        nxt = _rec_step(nxt, rtype, interval, day)
    return nxt, rec


def spawn_recurring(task: dict, active: list[dict] = ()) -> dict | None:
    """If task has recurrence, create the next instance and return it.
    None too when `active` (the open tasks) still has the one it spawned
    before: reopening kept it (uncomplete_task), so completing again must
    not add a second.

    The series is driven by the task's primary date — the due date if it has
    one, else the start date. ``recurrence.from`` picks the base for stepping
    it: ``"due"`` (default) steps from that date; ``"completion"`` steps from
    today. The next date is caught up until >= today. When the task has both
    dates, the start date moves with the due date, keeping the gap between
    them ("start 3 days before due" carries over to every instance).

    The new start is never before today: the next instance can't have been
    available before the previous one was done (a daily task with a 2-day
    gap would otherwise come back "since 2 days ago"). A clamped start would
    shrink the gap for good, so the series then remembers it in
    ``recurrence.lead`` (like ``day``) and the next spawn uses that."""
    rec = task.get("recurrence")
    if not rec or any(t.get("spawned_from") == task["id"] and not t.get("completed_at")
                      for t in active):
        return None
    today = date.today()
    due, start = _parse_date(task.get("due_date")), _parse_date(task.get("start_date"))
    primary = due or start
    anchored = primary is not None and rec.get("from") != "completion"
    next_date, next_rec = _next_occurrence(rec, primary if anchored else today, anchored, today)
    next_rec.pop("lead", None)
    if due:
        new_due = next_date
        new_start = None
        if start:
            lead = rec.get("lead", (due - start).days)
            new_start = next_date - timedelta(days=lead)
            if new_start < today:
                new_start = today
                next_rec["lead"] = lead
    elif start:
        new_due, new_start = None, next_date
    else:
        # No date at all: the series gets a due date (API-created only; the
        # edit modal requires a date before it offers Repeat).
        new_due, new_start = next_date, None
    spawned = new_task(
        title=task["title"],
        description=task.get("description", ""),
        tags=task.get("tags", []),
        due_date=new_due.isoformat() if new_due else None,
        start_date=new_start.isoformat() if new_start else None,
        sort_order=task.get("sort_order", 0),
        recurrence=next_rec,
        spawned_from=task["id"],
    )
    # The next occurrence carries the series' history on (with the "done" or
    # "skipped" just logged), then notes its own date.
    spawned["history"] = list(task.get("history") or [])
    spawned["comments"] = list(task.get("comments") or [])   # the series' log too
    log_event(spawned, "repeat", None, spawned["due_date"] or spawned["start_date"])
    return spawned


def _parse_date(val) -> date | None:
    try:
        return datetime.strptime(val, "%Y-%m-%d").date() if val else None
    except (TypeError, ValueError):
        return None


# A task's history: what happened to it and when, newest last, capped.
# Events: created, start / due (with from/to dates), done, reopened, wontdo,
# skipped, repeat (a recurring task's next occurrence, `to` = its date; the
# occurrence carries the series' history on, so it reads as one). Undo sends the old history back with the old fields, so an undone
# change leaves no trace (_apply_history).
HISTORY_EVENTS = ("created", "start", "due", "done", "reopened", "wontdo", "skipped", "repeat")


_DATED_EVENTS = ("start", "due", "repeat")
HISTORY_LIMIT = 30


def log_event(task: dict, event: str, frm=None, to=None) -> None:
    entry = {"at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "event": event}
    if event in _DATED_EVENTS:
        entry["from"], entry["to"] = frm, to
    task["history"] = (task.get("history") or [])[-(HISTORY_LIMIT - 1):] + [entry]


def clean_history(val) -> list[dict]:
    """Validate a history list from a request or snapshot; bad entries go."""
    out = []
    for e in val if isinstance(val, list) else []:
        if not (isinstance(e, dict) and e.get("event") in HISTORY_EVENTS
                and isinstance(e.get("at"), str)):
            continue
        entry = {"at": e["at"][:16], "event": e["event"]}
        if e["event"] in _DATED_EVENTS:
            for k in ("from", "to"):
                v = e.get(k)
                entry[k] = v if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None
        out.append(entry)
    return out[-HISTORY_LIMIT:]


# A task's comments: short status lines the user adds ("called Bob, waiting
# for an answer"), oldest first, each {id, at, text}. Unlike the history
# they are never capped, and undo of other changes leaves them alone; they
# are added, edited and deleted through their own routes. A recurring
# task's next occurrence carries them on, like the history.
COMMENT_MAX = 2000


def new_comment_id() -> str:
    return uuid.uuid4().hex[:8]


def clean_comments(val) -> list[dict]:
    """Validate a comment list from a request or snapshot; bad entries go."""
    out, seen = [], set()
    for c in val if isinstance(val, list) else []:
        if not (isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip()
                and isinstance(c.get("at"), str)):
            continue
        cid = c.get("id") if isinstance(c.get("id"), str) and c["id"] else new_comment_id()
        if cid in seen:
            cid = new_comment_id()
        seen.add(cid)
        out.append({"id": cid, "at": c["at"][:16], "text": c["text"].strip()[:COMMENT_MAX]})
    return out


def keep_done(task: dict) -> None:
    """Complete a keep task. It is never archived: done means "back to the
    pool". It returns to Open with no start date (so it leaves Focus), its
    deadline is dropped (it was this round's), and the day is
    recorded in ``last_done``. Undo is a plain update restoring those fields."""
    log_event(task, "done")
    task["last_done"] = date.today().isoformat()
    task["start_date"] = None
    task["due_date"] = None


def complete_task(task: dict, now: str | None = None, active: list[dict] = ()) -> dict | None:
    """Stamp task as completed; return the spawned recurring instance, if any
    (see spawn_recurring for `active`)."""
    task["completed_at"] = now or timestamp()
    task["wont_do"] = False
    log_event(task, "done")
    return spawn_recurring(task, active)


def _untouched_spawn(spawn: dict, task: dict) -> bool:
    """Whether the occurrence task spawned is still as the spawn made it:
    its history ends with the spawn's "repeat" (a date change would follow
    it), and its title, notes, tags and comments are task's. Only such a
    spawn may go when task is reopened; one with your edits stays."""
    history = spawn.get("history") or []
    comments = task.get("comments") or []
    return (bool(history) and history[-1].get("event") == "repeat"
            and not spawn.get("keep")
            and spawn.get("title") == task.get("title")
            and (spawn.get("description") or "") == (task.get("description") or "")
            and (spawn.get("tags") or []) == (task.get("tags") or [])
            and all(c in comments for c in spawn.get("comments") or []))


def uncomplete_task(task: dict, active: list[dict],
                     protect_ids: frozenset = frozenset()) -> list[dict]:
    """Clear task's completion state and return active without the open
    recurring instance spawned when it was completed (the one whose
    spawned_from is its id), unless it was changed since (_untouched_spawn):
    then it stays, and completing task again spawns no second one
    (spawn_recurring). protect_ids are never removed."""
    if task.get("recurrence"):
        drop = {t["id"] for t in active
                if t.get("spawned_from") == task["id"] and not t.get("completed_at")
                and _untouched_spawn(t, task)} - set(protect_ids)
        active = [t for t in active if t["id"] not in drop]
    task["completed_at"] = None
    task["wont_do"] = False
    log_event(task, "reopened")
    return active


def rename_page_refs(old_slug: str, new_slug: str, rewrite_text) -> list[Path]:
    """Follow a page rename in the deck: rewrite links in task titles and
    descriptions (via rewrite_text, the server's link rewriter). Writes only files that changed and does not commit — returns
    their paths so the caller includes them in the rename commit."""
    with _LOCK:
        changed = []
        files = [(DECK_FILE, read_deck_strict()),
                 (_completed_path(), _read_completed_strict())]
        files += [(p, _read_list_strict(p)) for p in _archive_glob()]
        for path, tasks in files:
            dirty = False
            for t in tasks:
                for field in ("title", "description"):
                    if t.get(field):
                        new_text = rewrite_text(t[field])
                        if new_text != t[field]:
                            t[field] = new_text
                            dirty = True
            if dirty:
                _write_tasks(path, tasks)
                changed.append(path)
        return changed


def find_task_backlinks(slug: str) -> list[dict]:
    """Return deck tasks whose title or description contain a link to slug
    (a wikilink, or a markdown link to /page/<slug>). A link to one of its
    sections (`[[Page#Section]]`, `/page/<slug>#anchor`) gives the task a
    `section`: the section as written, and `anchor`, its heading id
    (mdtext.section_anchor), for the first such link."""
    results = []
    for task in _read_all():
        if task.get("completed_at"):
            continue
        text = (task.get("title") or "") + "\n" + (task.get("description") or "")
        found, section, anchor = False, "", ""
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).partition("|")[0]
            page_name, _, sec = (x.strip() for x in target.partition("#"))
            if page_name and (page_name == slug or to_slug(page_name) == slug):
                found = True
                if sec and not section:
                    section, anchor = sec, section_anchor(sec)
        for m in _MD_LINK_RE.finditer(text):
            page, _, frag = m.group(2).partition("#")
            if page == slug:
                found = True
                if frag and not section:
                    section, anchor = frag.replace("-", " "), frag
        if found:
            results.append({"id": task["id"], "title": task.get("title", ""),
                            "tags": task.get("tags") or [],
                            "section": section, "anchor": anchor})
    return results


def resolve_task_refs(refs) -> dict[str, dict | None]:
    """Map each reference (an id prefix, lowercased) to its task, None when
    no task has that id. An open task wins over a completed one with the
    same prefix. A completed recurring task stands for its series: the
    reference follows `spawned_from` to the occurrence that is current now,
    so ticking it on a page and reloading shows the next one."""
    refs = {r.lower() for r in refs}
    if not refs:
        return {}
    with _LOCK:
        active = read_deck()
        done = _read_completed() + _read_archived()
    successor = {t["spawned_from"]: t for t in active + done if t.get("spawned_from")}
    out = {}
    for ref in refs:
        task = (next((t for t in active if t["id"].lower().startswith(ref)), None)
                or next((t for t in done if t["id"].lower().startswith(ref)), None))
        seen = set()
        while task and task.get("completed_at") and task["id"] in successor \
                and task["id"] not in seen:
            seen.add(task["id"])
            task = successor[task["id"]]
        out[ref] = task
    return out


def find_tasks(prefix: str) -> list[dict]:
    """Every task (open, completed, archived) whose id starts with prefix
    (lowercase); only the one with exactly that id when there is one."""
    with _LOCK:
        tasks = read_deck() + _read_completed() + _read_archived()
    found = [t for t in tasks if t["id"].lower().startswith(prefix)]
    return [t for t in found if t["id"].lower() == prefix] or found


def expand_task_refs(text: str, fmt) -> str:
    """Replace every [[task:…]] in text with fmt(ref, task_or_None)."""
    refs = [m.group(1).lower() for m in TASK_REF_RE.finditer(text)]
    if not refs:
        return text
    tasks = resolve_task_refs(refs)
    return TASK_REF_RE.sub(lambda m: fmt(m.group(1).lower(), tasks[m.group(1).lower()]), text)


_task_docs_cache: dict = {"key": None, "docs": []}


def deck_mtimes() -> tuple:
    """Cache key: paths + mtimes of every deck file, archives included."""
    def mt(p: Path):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None
    paths = [DECK_FILE, _completed_path()] + _archive_glob()
    return tuple((str(p), mt(p)) for p in paths)


def last_activity(task: dict) -> str | None:
    """The day a task last changed, as far as it records: the latest of its
    completion, history events, comments and creation ("YYYY-MM-DD")."""
    stamps = [task.get("completed_at"), task.get("created_at")]
    stamps += [e.get("at") for e in task.get("history") or [] if isinstance(e, dict)]
    stamps += [c.get("at") for c in task.get("comments") or [] if isinstance(c, dict)]
    days = [str(t)[:10] for t in stamps if t and re.match(r"\d{4}-\d{2}-\d{2}", str(t))]
    return max(days) if days else None


def task_search_docs() -> list[dict]:
    """Return all tasks as search documents for the shared BM25 index.

    Documents carry pre-tokenized ``tokens`` (title boosted by TITLE_WEIGHT) plus
    passthrough fields (type/task_id/status) that retrieval.do_search formats into
    results. Ranking and excerpting happen in the unified index, not here.

    Tokenizing every task on each search request is wasted work, so results are
    cached against the deck files' mtimes (same pattern as the page-token cache
    in retrieval.py)."""
    key = deck_mtimes()
    if _task_docs_cache["key"] == key:
        return _task_docs_cache["docs"]
    docs = []
    for task in _read_all() + _read_archived():
        title = task.get("title", "")
        desc = task.get("description", "")
        # Tags as "#tag" words, so a page's tag pill (/search?q=<tag>) finds
        # the tasks with that tag too, and the excerpt can show the match.
        tags = " ".join(f"#{t}" for t in task.get("tags") or [])
        comments = " ".join(c.get("text", "") for c in task.get("comments") or [] if isinstance(c, dict))
        text = search.strip_link_targets(f"{title} {desc} {comments} {tags}".strip())
        docs.append({
            "slug": "_deck",
            "title": f"[Task] {plain_task_title(title)}",
            "excerpt_text": text,
            "tokens": search.tokenize(text)
            + search.tokenize(plain_task_title(title)) * search.TITLE_WEIGHT,
            "type": "task",
            "task_id": task["id"],
            "status": "done" if task.get("completed_at") else "open",
            "year": past_year(task) if task.get("completed_at") else None,
            "tags": [t.lower() for t in task.get("tags") or []],  # for #tag queries
            "edited": last_activity(task),  # for edited:/after:/before: queries
            "task": task,  # the whole task, for the MCP tools' dates and tags
        })
    _task_docs_cache["key"] = key
    _task_docs_cache["docs"] = docs
    return docs


def clean_recurrence(rec) -> dict | None:
    """A recurrence from a snapshot or an undo, its known fields validated
    (day and lead included); None when it names no repeat type."""
    if not (isinstance(rec, dict) and rec.get("type") in ("daily", "weekly", "monthly", "yearly")):
        return None
    out = {
        "type": rec["type"],
        "interval": rec["interval"] if isinstance(rec.get("interval"), int)
                    and not isinstance(rec["interval"], bool)
                    and 1 <= rec["interval"] <= REC_INTERVAL_MAX else 1,
        "from": "completion" if rec.get("from") == "completion" else "due",
    }
    if isinstance(rec.get("day"), int) and not isinstance(rec["day"], bool) and 1 <= rec["day"] <= 31:
        out["day"] = rec["day"]
    if isinstance(rec.get("lead"), int) and not isinstance(rec["lead"], bool) and rec["lead"] >= 0:
        out["lead"] = rec["lead"]
    return out


def sanitize_snapshot(snap: dict) -> dict | None:
    """Rebuild a task from an untrusted snapshot, keeping only known fields.

    Returns None for snapshots without a usable title. Dates are validated;
    invalid optional values degrade to their defaults rather than aborting,
    so one bad field doesn't lose the rest of an undo."""
    title = str(snap.get("title") or "").strip()
    if not title:
        return None
    task = new_task(title=title)
    task["description"] = str(snap.get("description") or "")
    task["tags"] = clean_tags(snap.get("tags") if isinstance(snap.get("tags"), list) else [])
    for field in ("due_date", "start_date", "last_done"):
        if isinstance(snap.get(field), str) and _parse_date(snap[field]):
            task[field] = snap[field]
    if isinstance(snap.get("id"), str) and snap["id"]:
        task["id"] = snap["id"]
    for field in ("created_at", "completed_at", "spawned_from"):
        if isinstance(snap.get(field), str) and snap[field]:
            task[field] = snap[field]
    # Fractional too: a move between two tasks (reorder `updates`) may leave one.
    if isinstance(snap.get("sort_order"), (int, float)) and not isinstance(snap["sort_order"], bool):
        task["sort_order"] = snap["sort_order"]
    task["wont_do"] = bool(snap.get("wont_do"))
    task["keep"] = bool(snap.get("keep"))
    task["history"] = clean_history(snap.get("history"))
    task["comments"] = clean_comments(snap.get("comments"))
    task["recurrence"] = clean_recurrence(snap.get("recurrence"))
    if task["keep"]:
        task["recurrence"] = None
    return task
