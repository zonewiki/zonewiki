"""Deck — task management module for Leaf wiki.

Provides:
- Task CRUD operations and storage, in the _deck/ folder: tasks.json (open),
  completed.json (completed this year), completed-YYYY.json (earlier years)
- [[deck]] embed HTML generation
- Task backlink resolution
- API route registration via Blueprint
"""

import calendar
import functools
import html
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, jsonify, request

import search

# ── Blueprint ─────────────────────────────────────────────────────────────────

bp = Blueprint("deck", __name__)

# Serializes every read-modify-write on the deck JSON files. The Flask dev
# server is threaded, so without this two concurrent task mutations can
# interleave their read → modify → write and lose each other's changes (or, if
# one reads while the other has truncated the file mid-write, silently wipe the
# whole list). All mutating endpoints hold this lock for the full
# read-modify-write-commit sequence. RLock so nested helpers can re-acquire.
_LOCK = threading.RLock()


def _locked(fn):
    """Serialize a view's whole read-modify-write-commit under _LOCK."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper

# These are set by init_deck() from server.py
WIKI_DIR: Path = Path(".")
DECK_FILE: Path = Path("_deck/tasks.json")   # the open tasks; the rest sit beside it
DECK_DIR_NAME = "_deck"
GIT_ENABLED: bool = False
_git_commit_fn = None  # type: ignore


def _completed_path() -> Path:
    """Derive the completed-tasks file path from DECK_FILE (same directory)."""
    return DECK_FILE.parent / "completed.json"


def init_deck(wiki_dir: Path, git_enabled: bool, git_commit_fn) -> None:
    """Initialize module-level state from the main server."""
    global WIKI_DIR, DECK_FILE, GIT_ENABLED, _git_commit_fn
    WIKI_DIR = wiki_dir
    DECK_FILE = wiki_dir / DECK_DIR_NAME / "tasks.json"
    GIT_ENABLED = git_enabled
    _git_commit_fn = git_commit_fn
    _move_into_deck_dir()
    _migrate_completed_split()
    _migrate_deck_files()
    _archive_old_completed()


# Before the _deck/ folder, the files sat in the wiki's root.
_OLD_ROOT_FILES_RE = re.compile(r"_deck(?:_completed(?:-(\d{4}))?)?\.json")


def _move_into_deck_dir() -> None:
    """One-time move of the deck files from the wiki's root into _deck/
    (_deck.json → tasks.json, _deck_completed.json → completed.json,
    _deck_completed-YYYY.json → completed-YYYY.json), in one commit, so git
    sees renames and each file keeps its history. A file whose new name is
    taken already stays where it is."""
    moves = []
    for old in sorted(WIKI_DIR.glob("_deck*.json")):
        m = _OLD_ROOT_FILES_RE.fullmatch(old.name)
        if not m:
            continue
        name = ("tasks.json" if old.name == "_deck.json"
                else f"completed-{m.group(1)}.json" if m.group(1) else "completed.json")
        new = DECK_FILE.parent / name
        if new.exists():
            print(f"[deck] {old.name} not moved: {new} exists", flush=True)
            continue
        moves.append((old, new))
    if not moves:
        return
    DECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    for old, new in moves:
        os.replace(old, new)
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn([p for move in moves for p in move], "Move deck files into _deck/")


def _migrate_completed_split() -> None:
    """One-time migration: move completed tasks from tasks.json into completed.json."""
    completed_file = _completed_path()
    if completed_file.exists() or not DECK_FILE.exists():
        return
    try:
        data = json.loads(DECK_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return
    except (json.JSONDecodeError, ValueError):
        return
    completed = [t for t in data if t.get("completed_at")]
    active = [t for t in data if not t.get("completed_at")]
    if not completed:
        _atomic_write(completed_file, "[]")
        return
    _atomic_write(DECK_FILE, json.dumps(active, indent=2, ensure_ascii=False))
    _atomic_write(completed_file, json.dumps(completed, indent=2, ensure_ascii=False))


# ── Storage ───────────────────────────────────────────────────────────────────


class _DeckCorrupt(Exception):
    """Existing deck file is present but unreadable — refuse to overwrite it."""


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
    """Read a JSON list, raising _DeckCorrupt if the file exists but is unusable.

    Used on the write path (under _LOCK) so a corrupt or torn file aborts the
    request instead of being silently treated as an empty list and overwritten,
    which would destroy every task. A genuinely empty deck is stored as ``[]``,
    so a zero-length file only ever means a torn/failed write."""
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise _DeckCorrupt(str(path))
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise _DeckCorrupt(str(path))
    if not isinstance(data, list):
        raise _DeckCorrupt(str(path))
    return _normalize_list(data)


def _clean_tags(tags) -> list[str]:
    """Tags as the filter field can match them: one word each (whitespace
    inside becomes "-", so "local llm" → "local-llm"), no leading "#", no
    empties, no case-insensitive duplicates (the first spelling wins). The
    filter splits on spaces, so a tag with a space could never be found."""
    out, seen = [], set()
    for tag in tags or []:
        tag = re.sub(r"\s+", "-", str(tag).strip().lstrip("#").strip()).strip("-")
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out


def _clean_list(name) -> str | None:
    """A list name as stored and matched: trimmed, lowercase, one word
    (whitespace becomes "-", like tags), no leading "#". Empty means no list."""
    name = re.sub(r"\s+", "-", str(name or "").strip().lstrip("#").strip().lower()).strip("-")
    return name or None


def _normalize_task(task: dict) -> bool:
    """Upgrade a task in place to the current format; True if it changed.

    Older decks stored the start date as ``focus_date``, a derivation rule
    ``defer_days`` (start N days before due) and ``recurrence.anchor`` (which
    date a cycle advances). The start date is now ``start_date``; the rule is
    gone because every derived date was already stored, and recurring spawns
    carry the start→due gap instead; the anchor is implied by which dates the
    task has. Tags are cleaned (_clean_tags): older decks allowed spaces.
    The ``page`` field (a linked page slug) is gone: a wikilink in the notes
    does the same, so the slug becomes ``[[slug]]`` there unless the title or
    notes already link to it. ``focus_order`` (a place in Focus's own order,
    the Lineup) is gone: Focus uses Open's order."""
    changed = False
    if "page" in task:
        slug = task.pop("page")
        changed = True
        if isinstance(slug, str) and slug.strip():
            slug = slug.strip()
            text = f"{task.get('title') or ''}\n{task.get('description') or ''}"
            linked = re.search(r"\[\[\s*" + re.escape(slug) + r"\s*(\||\]\])", text, re.I) \
                or f"/page/{slug})" in text
            if not linked:
                desc = (task.get("description") or "").rstrip()
                task["description"] = f"{desc}\n\n[[{slug}]]" if desc else f"[[{slug}]]"
    if "focus_date" in task:
        task.setdefault("start_date", task["focus_date"])
        del task["focus_date"]
        changed = True
    if "defer_days" in task:
        del task["defer_days"]
        changed = True
    rec = task.get("recurrence")
    if isinstance(rec, dict) and "anchor" in rec:
        del rec["anchor"]
        changed = True
    tags = task.get("tags")
    if isinstance(tags, list) and _clean_tags(tags) != tags:
        task["tags"] = _clean_tags(tags)
        changed = True
    if "focus_order" in task:
        del task["focus_order"]
        changed = True
    return changed


def _normalize_list(tasks: list) -> list:
    for t in tasks:
        if isinstance(t, dict):
            _normalize_task(t)
    return tasks


def _migrate_deck_files() -> None:
    """One-time rewrite of every deck file into the current format
    (_normalize_task: date model, clean tags), so the files on disk (and in
    git) stop carrying the old shapes. Reads normalize too, so this is
    tidiness, not correctness. A no-op once the files are current."""
    with _LOCK:
        changed = []
        for path in [DECK_FILE, _completed_path(), *_archive_glob()]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, list):
                continue
            if any([_normalize_task(t) for t in data if isinstance(t, dict)]):
                _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
                changed.append(path)
        if changed and GIT_ENABLED and _git_commit_fn:
            _git_commit_fn(changed, "Normalize deck files (date model, tags, page links, Lineup)")


def _read_deck_strict() -> list[dict]:
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


def _read_deck() -> list[dict]:
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


def _past_year(task: dict, current: str | None = None) -> str | None:
    """The task's completion year when it is before this one (it belongs in
    that year's archive), else None (the current completed file)."""
    year = _completed_year(task)
    return year if year and year < (current or str(date.today().year)) else None


def _read_completed_all_strict() -> list[dict]:
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
        year = _past_year(t, current)
        by_path.setdefault(_archive_path(year) if year else _completed_path(), []).append(t)
    changed = []
    for path, tasks in by_path.items():
        try:
            if json.loads(path.read_text(encoding="utf-8")) == tasks:
                continue
        except (OSError, ValueError):
            pass
        _atomic_write(path, json.dumps(tasks, indent=2, ensure_ascii=False))
        changed.append(path)
    return changed


def _completed_in_year(year: str) -> list[dict]:
    """The Completed view of one year: tasks completed in it (this year's
    view also takes tasks without a usable date)."""
    current = str(date.today().year)
    with _LOCK:
        tasks = _read_completed()
        if year != current:
            tasks = tasks + _read_list_lenient(_archive_path(year))
    if year == current:
        return [t for t in tasks if not _past_year(t, current)]
    return [t for t in tasks if _completed_year(t) == year]


def completed_years(scope: str | None) -> list[str]:
    """Years with completed tasks in scope (see in_scope), newest first; this
    year is always included."""
    current = str(date.today().year)
    years = {current}
    with _LOCK:
        for t in _read_completed():
            if in_scope(t, scope) and _past_year(t, current):
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


def _archive_old_completed() -> None:
    """Roll completed tasks from previous years into completed-YYYY.json.

    Runs at startup so the current completed file stays small; every write
    also files tasks by year (_write_completed), so this only matters after
    a year change without writes. Skips silently if a file is unreadable —
    archiving is an optimization, never worth failing startup over."""
    with _LOCK:
        try:
            completed = _read_completed_all_strict()
        except _DeckCorrupt:
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
        return _read_deck() + _read_completed()


def _write_deck(tasks: list[dict], message: str = "Update deck") -> None:
    _atomic_write(DECK_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn([DECK_FILE], message)


def _write_both(active: list[dict], completed: list[dict], message: str = "Update deck") -> None:
    """Write the open tasks and every completed task (all of them, as read by
    _read_completed_all_strict; filed by year) in a single git commit."""
    _atomic_write(DECK_FILE, json.dumps(active, indent=2, ensure_ascii=False))
    changed = _write_completed(completed)
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn([DECK_FILE, *changed], message)


def _json_object() -> dict:
    """The request's JSON body, which must be an object (else 400)."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        abort(400, description="Request body must be a JSON object")
    return data


def _as_str(val, name: str) -> str:
    if not isinstance(val, str):
        abort(400, description=f"{name} must be a string")
    return val


def _as_int(val, name: str) -> int:
    """int() that accepts numbers and numeric strings, else 400."""
    if isinstance(val, bool):
        abort(400, description=f"{name} must be an integer")
    try:
        return int(val)
    except (TypeError, ValueError):
        abort(400, description=f"{name} must be an integer")


def _as_str_list(val, name: str) -> list[str]:
    """A list of strings; a single string is split on commas."""
    if isinstance(val, str):
        val = val.split(",")
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        abort(400, description=f"{name} must be a list of strings")
    return [v.strip() for v in val if v.strip()]


def _id_list(data: dict) -> list[str]:
    """The body's `ids` — a list of task-id strings (else 400)."""
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        abort(400, description="ids must be a list of task ids")
    return ids


def _as_order(val, name: str):
    if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):
        abort(400, description=f"{name} must be a number")
    return val


def _validate_date(val: str | None) -> str | None:
    """Return val if it's a valid YYYY-MM-DD string or None, else raise 400."""
    if not val:
        return None
    if not isinstance(val, str):
        abort(400, description="Invalid date format, expected YYYY-MM-DD")
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return val
    except ValueError:
        abort(400, description="Invalid date format, expected YYYY-MM-DD")


def _new_task(**overrides) -> dict:
    """Return a task dict with every field at its default, plus overrides.

    Single source of truth for the task schema — create, recurring spawn, and
    field updates all build on this skeleton."""
    task = {
        "id": str(uuid.uuid4()),
        "title": "",
        "description": "",
        "tags": [],
        "due_date": None,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
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


def _spawn_recurring(task: dict) -> dict | None:
    """If task has recurrence, create the next instance and return it.

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
    if not rec:
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
    spawned = _new_task(
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
    _log(spawned, "repeat", None, spawned["due_date"] or spawned["start_date"])
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


def _log(task: dict, event: str, frm=None, to=None) -> None:
    entry = {"at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "event": event}
    if event in _DATED_EVENTS:
        entry["from"], entry["to"] = frm, to
    task["history"] = (task.get("history") or [])[-(HISTORY_LIMIT - 1):] + [entry]


def _clean_history(val) -> list[dict]:
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


def _new_comment_id() -> str:
    return uuid.uuid4().hex[:8]


def _comment_text(val) -> str:
    text = _as_str(val, "text").strip()
    if not text:
        abort(400, description="Comment cannot be empty")
    return text[:COMMENT_MAX]


def _clean_comments(val) -> list[dict]:
    """Validate a comment list from a request or snapshot; bad entries go."""
    out, seen = [], set()
    for c in val if isinstance(val, list) else []:
        if not (isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip()
                and isinstance(c.get("at"), str)):
            continue
        cid = c.get("id") if isinstance(c.get("id"), str) and c["id"] else _new_comment_id()
        if cid in seen:
            cid = _new_comment_id()
        seen.add(cid)
        out.append({"id": cid, "at": c["at"][:16], "text": c["text"].strip()[:COMMENT_MAX]})
    return out


def _keep_done(task: dict) -> None:
    """Complete a keep task. It is never archived: done means "back to the
    pool". It returns to Open with no start date (so it leaves Focus), its
    deadline is dropped (it was this round's), and the day is
    recorded in ``last_done``. Undo is a plain update restoring those fields."""
    _log(task, "done")
    task["last_done"] = date.today().isoformat()
    task["start_date"] = None
    task["due_date"] = None


def _complete_task(task: dict, now: str | None = None) -> dict | None:
    """Stamp task as completed; return the spawned recurring instance, if any."""
    task["completed_at"] = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    task["wont_do"] = False
    _log(task, "done")
    return _spawn_recurring(task)


def _uncomplete_task(task: dict, active: list[dict],
                     protect_ids: frozenset = frozenset()) -> list[dict]:
    """Clear task's completion state and return active with the open recurring
    instance that was spawned at completion time removed. Instances carry
    spawned_from (the completed task's id) for an exact match; instances from
    before that field existed fall back to the old heuristic (title, recurrence,
    creation after completion). protect_ids are never removed."""
    if task.get("recurrence"):
        exact = [t for t in active
                 if t.get("spawned_from") == task["id"] and not t.get("completed_at")]
        if exact:
            drop = {t["id"] for t in exact} - set(protect_ids)
            active = [t for t in active if t["id"] not in drop]
        else:
            completed_at = task["completed_at"]
            active = [t for t in active if t["id"] in protect_ids or not (
                t.get("title") == task["title"] and
                t.get("recurrence") == task.get("recurrence") and
                not t.get("completed_at") and
                t.get("created_at", "") >= completed_at
            )]
    task["completed_at"] = None
    task["wont_do"] = False
    _log(task, "reopened")
    return active


# ── [[deck]] embed ────────────────────────────────────────────────────────────

_DECK_RE = re.compile(
    r'\[\[deck(?:\s+([^\]]*))?\]\]', re.IGNORECASE
)

_DECK_PLACEHOLDER_BASE = "LEAFDECKPLACEHOLDER"


def _parse_tasks_params(raw: str) -> dict:
    """Parse key=value pairs from [[deck key=val ...]]."""
    params: dict = {}
    if not raw:
        return params
    for token in raw.split():
        if "=" in token:
            k, v = token.split("=", 1)
            params[k.strip().lower()] = v.strip()
    return params


# Maps friendly tab-label names (and the internal names) to the data-filter
# value the client uses to activate a tab. Lets `[[deck view=all]]` work
# using the labels the user actually sees, not just internal names. "backlog"
# is the tab's former label, kept so existing embeds keep working.
_FILTER_ALIASES = {
    "focus": "focus", "today": "focus",   # "today": the tab's label for a while
    "all": "open", "backlog": "open", "open": "open",   # "all": the label before "Open"
    "plan": "due", "upcoming": "due", "due": "due",
    "completed": "completed", "done": "completed",
}


# Separate lists. A list is a tag that a page declares with [[deck list=<tag>]]:
# that page shows the tasks carrying the tag, and marks the tag as a list
# everywhere. Otherwise a list tag is a tag: the main deck shows its tasks like
# any other. The server scans pages for the declarations (server._deck_lists) and
# installs the lookup here, since deck.py doesn't read pages itself.
# LISTS_FN() returns {name: {"slug": declaring page, "title": its title,
# "params": its embed's params}}; names are lowercase.
LISTS_FN = lambda: {}


# The deck's home: the page whose [[deck … home=1]] embed is the user's own
# deck, where the Deck module link goes (server._deck_home installs the
# lookup). HOME_FN() returns {"slug", "title", "params"} or None (then the
# Deck link goes to /deck).
HOME_FN = lambda: None


def is_true(value) -> bool:
    """A yes/no embed param (`keep=1`, `home=yes`)."""
    return str(value or "").strip().lower() in ("1", "true", "yes")


def tag_param_matches(task: dict, raw: str) -> bool:
    """Whether a task passes an embed's `tag=` param, as deck.js reads it
    (DeckCore.tagParamToQuery): `a|b` any of them, else comma-separated tags
    that all must be there, a leading `-` excluding one."""
    raw = (raw or "").strip()
    if not raw:
        return True
    tags = {t.lower() for t in task.get("tags") or []}
    if "|" in raw:
        return bool(tags & {t.strip().lstrip("#").lower() for t in raw.split("|") if t.strip()})
    for t in (t.strip().lstrip("#").lower() for t in raw.split(",")):
        if t.startswith("-"):
            if t[1:] in tags:
                return False
        elif t and t not in tags:
            return False
    return True


def list_names() -> set[str]:
    """Every declared list name."""
    return set(LISTS_FN())


def task_list(task: dict, names: set[str] | None = None) -> str | None:
    """The separate list a task belongs to: the first (A–Z) of its tags that
    is a declared list, or None."""
    names = list_names() if names is None else names
    hits = sorted({t.lower() for t in task.get("tags") or []} & names)
    return hits[0] if hits else None


def in_scope(task: dict, scope: str | None) -> bool:
    """Whether a task belongs to a deck view: scope is a list name (tasks
    tagged with it) or None for every task (the main deck, tag embeds)."""
    if not scope:
        return True
    return scope in {t.lower() for t in task.get("tags") or []}


def _scope_arg() -> str | None:
    """The list a completed-count/-years request is for (?list=<name>), or
    None for every task. A leftover ?all=1 changes nothing."""
    return _clean_list(request.args.get("list")) or None


def _build_deck_embed(params: dict, instance_id: int = 0, headless: bool = False) -> str:
    """Render the full interactive deck app HTML for embedding in a wiki page.

    instance_id differentiates multiple embeds on the same page. `headless`
    hides the app and keeps only what its edit modal needs (the editor's
    Make task opens it, window.openDeckEdit); deck.js then skips the deck's
    keyboard shortcuts.
    """
    tag_val = params.get("tag", "")
    list_val = _clean_list(params.get("list")) or ""
    # `view=` picks the tab; `filter=` is its old name, still accepted.
    raw_filter = (params.get("view") or params.get("filter", "")).strip().lower()
    filt = _FILTER_ALIASES.get(raw_filter, raw_filter)
    # `limit=N` turns on the WIP limit (Focus warning, Plan day flags); without
    # it there is none. `capacity=` is its old name, still accepted.
    try:
        limit = max(1, min(99, int(params.get("limit") or params.get("capacity", ""))))
    except ValueError:
        limit = ""  # no limit
    # `labels=Catalogue,Plan,Shopping`: this embed's own names for the tabs, in
    # on-screen order (Open, Plan, Focus). Only the words change; views and
    # links (`view=open` …) stay the same. An empty entry keeps the default;
    # `_` stands for a space, since params are split on whitespace.
    tab_labels = ["Open", "Plan", "Focus"]
    for i, name in enumerate(params.get("labels", "").split(",")[:3]):
        name = name.replace("_", " ").strip()[:30]
        if name:
            tab_labels[i] = name
    open_label, plan_label, focus_label = (html.escape(n) for n in tab_labels)
    # `keep=1`: new tasks in this embed are keep tasks (a shopping list of
    # staples); the edit modal's checkbox still turns it off per task.
    keep = "1" if is_true(params.get("keep")) else ""
    # `home=1`: this embed is the user's own deck (HOME_FN); the module bar's
    # New clicks this embed's "+ New".
    home_attr = ' data-home="1"' if is_true(params.get("home")) else ""
    init_tag = html.escape(tag_val)
    init_filter = html.escape(filt)
    # Every declared list: their tags are marked as lists (deck.js).
    # Each one's home page title is for the list tag's tooltip.
    lists = LISTS_FN()
    lists_json = html.escape(json.dumps(sorted(lists)))
    homes_json = html.escape(json.dumps(
        {n: l.get("title") or l.get("slug") or "" for n, l in sorted(lists.items())}))

    # Use instance suffix for unique IDs when multiple embeds exist
    sfx = f"-{instance_id}" if instance_id > 0 else ""

    hide = ' data-headless="1" style="display:none"' if headless else ""
    return f"""<div class="tasks-app" id="tasks-app{sfx}"{hide} data-init-tag="{init_tag}" data-init-list="{html.escape(list_val)}" data-lists="{lists_json}" data-list-homes="{homes_json}" data-init-view="{init_filter}" data-init-limit="{limit}" data-init-keep="{keep}" data-init-new="" data-instance="{instance_id}"{home_attr}>
  <div class="deck-header">
    <div class="view-tabs" id="tasks-tabs{sfx}" role="tablist" aria-label="View">
      <button type="button" role="tab" data-filter="open" aria-selected="false">{open_label}</button>
      <button type="button" role="tab" data-filter="due" aria-selected="false">{plan_label}</button>
      <button type="button" role="tab" data-filter="focus" aria-selected="false">{focus_label}</button>
    </div>
    <div class="tasks-toolbar">
      <input type="text" id="task-filter-search{sfx}" placeholder="Search… #tag" title="Text searches title and notes · #tag filters by tag · #a #b = both tags · #a|b = either · -#tag = without" autocomplete="off">
      <button class="btn btn-primary" id="task-add-btn{sfx}" aria-label="New task" title="New task (n)">+<span class="task-add-label"> New</span></button>
    </div>
  </div>
  <ul class="tasks-list" id="tasks-list{sfx}"></ul>
  <div class="deck-footer">
    <button type="button" class="deck-completed-link" id="task-completed-link{sfx}">✓ Completed</button>
  </div>
</div>
<dialog id="task-edit-modal{sfx}" class="task-edit-modal">
  <form id="task-edit-form{sfx}" class="task-edit-form">
    <input type="hidden" id="task-edit-id{sfx}" name="id">
    <div class="task-field">
      <label for="task-edit-title{sfx}">Title</label>
      <input type="text" id="task-edit-title{sfx}" name="title" required autocomplete="off">
      <div class="task-quick-preview" id="task-edit-quick{sfx}" hidden></div>
    </div>
    <div class="task-field task-edit-batch" id="task-edit-batch{sfx}" style="display:none">
      <label id="task-edit-batch-label{sfx}">Tasks</label>
      <ul class="task-edit-batch-list" id="task-edit-batch-list{sfx}"></ul>
      <span class="field-hint">One task per line; the fields below apply to all of them.</span>
    </div>
    <div class="task-field">
      <label for="task-edit-desc{sfx}">Notes</label>
      <textarea id="task-edit-desc{sfx}" name="description" rows="6"></textarea>
    </div>
    <div class="task-field task-comments" id="task-edit-comments{sfx}" hidden>
      <label for="task-edit-comment{sfx}">Comments<span class="task-edit-links-summary" id="task-edit-comments-count{sfx}"></span></label>
      <div class="task-comment-add">
        <input type="text" id="task-edit-comment{sfx}" placeholder="Add a status line" autocomplete="off" maxlength="2000">
        <button type="button" class="btn" id="task-edit-comment-add{sfx}">Add</button>
      </div>
      <ul class="task-comment-list" id="task-edit-comment-list{sfx}"></ul>
    </div>
    <div class="task-field">
      <label for="task-edit-tags{sfx}">Tags <span class="field-hint">(Tab completes, comma adds)</span></label>
      <input type="text" id="task-edit-tags{sfx}" name="tags" autocomplete="off">
    </div>
    <div class="task-field-row task-row-dates">
      <div class="task-field">
        <label>Due date</label>
        <input type="hidden" id="task-edit-due{sfx}" name="due_date">
        <button type="button" class="date-trigger" data-date-trigger="task-edit-due{sfx}" data-placeholder="Set date">Set date</button>
      </div>
      <div class="task-field">
        <label>Start</label>
        <input type="hidden" id="task-edit-start{sfx}" name="start_date">
        <button type="button" class="date-trigger" data-date-trigger="task-edit-start{sfx}" data-placeholder="Set date">Set date</button>
      </div>
      <div class="task-field task-field-lead" title="Start this many days before the due date. Just another way to set the start date; recurring tasks keep this gap.">
        <label for="task-edit-lead{sfx}">Days before due</label>
        <input type="number" id="task-edit-lead{sfx}" min="0" placeholder="—">
      </div>
    </div>
    <div class="task-date-warning" id="task-edit-date-warn{sfx}" hidden>
      <span id="task-edit-date-warn-text{sfx}"></span>
      <button type="button" class="btn" id="task-edit-date-warn-fix{sfx}"></button>
    </div>
    <div class="task-field-row task-row-repeat">
      <div class="task-field">
        <label for="task-edit-rec-type{sfx}">Repeat</label>
        <select id="task-edit-rec-type{sfx}" name="rec_type">
          <option value="">—</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="yearly">Yearly</option>
        </select>
      </div>
      <div class="task-field">
        <label for="task-edit-rec-interval{sfx}">Every N</label>
        <input type="number" id="task-edit-rec-interval{sfx}" name="rec_interval" min="1" value="1">
      </div>
      <div class="task-field task-field-check" title="Repeat from the completion date instead of the due (or start) date">
        <label for="task-edit-rec-from{sfx}">From done</label>
        <span class="check-wrap"><input type="checkbox" id="task-edit-rec-from{sfx}" name="rec_from"></span>
      </div>
    </div>
    <label class="task-edit-keep" title="Ticking it off sends it back to Open instead of archiving it, e.g. a staple on a shopping list">
      <input type="checkbox" id="task-edit-keep{sfx}" name="keep"> Keep after done <span class="field-hint">(back to Open when ticked off)</span>
    </label>
    <div class="task-edit-extras">
    <details class="task-edit-links task-edit-history" id="task-edit-history{sfx}" hidden>
      <summary>History<span class="task-edit-links-summary" id="task-edit-history-count{sfx}"></span></summary>
      <ul class="task-history" id="task-edit-history-list{sfx}"></ul>
    </details>
    </div>
    <div class="task-edit-actions">
      <button type="submit" class="btn btn-primary">Save</button>
      <button type="button" class="btn" id="task-edit-cancel{sfx}">Cancel</button>
      <button type="button" class="btn btn-danger" id="task-edit-delete{sfx}" style="display:none">Delete</button>
    </div>
  </form>
</dialog>
<dialog id="task-delete-modal{sfx}" class="task-edit-modal scratch-modal">
  <div class="scratch-label">Delete task?</div>
  <div id="task-delete-title{sfx}" class="task-delete-title"></div>
  <div class="task-edit-actions">
    <button type="button" class="btn btn-danger" id="task-delete-confirm{sfx}">Delete</button>
    <button type="button" class="btn" id="task-delete-cancel{sfx}">Cancel</button>
  </div>
</dialog>
<dialog id="task-deadline-modal{sfx}" class="task-edit-modal scratch-modal">
  <div class="scratch-label">Move the deadline too?</div>
  <div id="task-deadline-text{sfx}" class="task-delete-title"></div>
  <div class="task-edit-actions">
    <button type="button" class="btn btn-primary" id="task-deadline-confirm{sfx}">Move deadline</button>
    <button type="button" class="btn" id="task-deadline-cancel{sfx}">Cancel</button>
  </div>
</dialog>
<script src="/public/deck-core.js" defer></script>
<script src="/public/deck.js" defer></script>"""


def process_deck_embeds(text: str) -> tuple[str, list[str]]:
    """Replace all [[deck ...]] directives with placeholders.

    Returns (modified_text, list_of_embed_htmls).
    Supports multiple embeds per page.
    """
    embeds = []
    instance = [0]

    def replacer(m: re.Match) -> str:
        params = _parse_tasks_params(m.group(1) or "")
        embed_html = _build_deck_embed(params, instance_id=instance[0])
        placeholder = f"{_DECK_PLACEHOLDER_BASE}_{instance[0]}"
        embeds.append((placeholder, embed_html))
        instance[0] += 1
        return placeholder

    text = _DECK_RE.sub(replacer, text)
    return text, embeds


def inject_deck_embeds(html_str: str, embeds: list[tuple[str, str]]) -> str:
    """Replace deck placeholders in rendered HTML with actual embed HTML."""
    for placeholder, embed_html in embeds:
        html_str = html_str.replace(f'<p>{placeholder}</p>', embed_html)
        html_str = html_str.replace(placeholder, embed_html)
    return html_str


# ── Task backlinks ────────────────────────────────────────────────────────────

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(/page/([^)]+)\)')


def rename_page_refs(old_slug: str, new_slug: str, rewrite_text) -> list[Path]:
    """Follow a page rename in the deck: rewrite links in task titles and
    descriptions (via rewrite_text, the server's link rewriter). Writes only files that changed and does not commit — returns
    their paths so the caller includes them in the rename commit."""
    with _LOCK:
        changed = []
        files = [(DECK_FILE, _read_deck_strict()),
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
                _atomic_write(path, json.dumps(tasks, indent=2, ensure_ascii=False))
                changed.append(path)
        return changed


def find_task_backlinks(slug: str, to_slug_fn) -> list[dict]:
    """Return deck tasks whose title or description contain a wikilink to slug."""
    results = []
    for task in _read_all():
        if task.get("completed_at"):
            continue
        text = (task.get("title") or "") + "\n" + (task.get("description") or "")
        found = False
        for m in WIKILINK_RE.finditer(text):
            inner = m.group(1)
            page_name = inner.split("|", 1)[0].strip() if "|" in inner else inner.strip()
            if page_name == slug or to_slug_fn(page_name) == slug:
                found = True
                break
        if not found:
            for m in _MD_LINK_RE.finditer(text):
                if m.group(2) == slug:
                    found = True
                    break
        if found:
            results.append({"id": task["id"], "title": task.get("title", ""),
                            "tags": task.get("tags") or [], "list": task_list(task)})
    return results


# ── Task references ([[task:<id>]] in pages) ─────────────────────────────────

# A page refers to one task as [[task:<id>]]: the first SHORT_ID_LEN
# characters of its id (like a short git hash), or the whole id. The server
# renders it as a live chip (server._task_ref_html).
TASK_REF_RE = re.compile(r"\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]")
SHORT_ID_LEN = 8


def short_id(task_id: str) -> str:
    return task_id[:SHORT_ID_LEN]


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
        active = _read_deck()
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


def expand_task_refs(text: str, fmt) -> str:
    """Replace every [[task:…]] in text with fmt(ref, task_or_None)."""
    refs = [m.group(1).lower() for m in TASK_REF_RE.finditer(text)]
    if not refs:
        return text
    tasks = resolve_task_refs(refs)
    return TASK_REF_RE.sub(lambda m: fmt(m.group(1).lower(), tasks[m.group(1).lower()]), text)


# ── Search integration ────────────────────────────────────────────────────────


_task_docs_cache: dict = {"key": None, "docs": []}


def _deck_mtimes() -> tuple:
    """Cache key: paths + mtimes of every deck file, archives included."""
    def mt(p: Path):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None
    paths = [DECK_FILE, _completed_path()] + _archive_glob()
    return tuple((str(p), mt(p)) for p in paths)


def _last_activity(task: dict) -> str | None:
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
    passthrough fields (type/task_id/status) that server.do_search formats into
    results. Ranking and excerpting happen in the unified index, not here.

    Tokenizing every task on each search request is wasted work, so results are
    cached against the deck files' mtimes (same pattern as the page-token cache
    in server.py)."""
    key = _deck_mtimes()
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
            "title": f"[Task] {title}",
            "excerpt_text": text,
            "tokens": search.tokenize(text)
            + search.tokenize(title) * search.TITLE_WEIGHT,
            "type": "task",
            "task_id": task["id"],
            "status": "done" if task.get("completed_at") else "open",
            "year": _past_year(task) if task.get("completed_at") else None,
            "tags": [t.lower() for t in task.get("tags") or []],  # for #tag queries
            "edited": _last_activity(task),  # for edited:/after:/before: queries
            "task": task,  # the whole task, for the MCP tools' dates and tags
        })
    _task_docs_cache["key"] = key
    _task_docs_cache["docs"] = docs
    return docs


# ── API Routes ────────────────────────────────────────────────────────────────


@bp.errorhandler(_DeckCorrupt)
def _handle_deck_corrupt(exc):
    # The existing deck file could not be read. Refuse the write (rather than
    # silently overwriting it with a partial list) and tell the client to retry.
    return jsonify({"error": "deck storage temporarily unavailable, please retry"}), 503


@bp.errorhandler(400)
def _handle_bad_request(exc):
    # JSON body so deck.js can surface the reason (e.g. an invalid field)
    # instead of Flask's HTML error page.
    return jsonify({"error": exc.description}), 400


@bp.get("/api/deck")
def api_get_deck():
    return jsonify(_read_deck())


def _year_arg() -> str:
    """?year=YYYY (a year of the Completed view), default this year."""
    year = request.args.get("year") or str(date.today().year)
    if not re.fullmatch(r"\d{4}", year):
        abort(400, description="year must be YYYY")
    return year


@bp.get("/api/deck/completed")
def api_get_deck_completed():
    """Completed tasks of ?year= (default this year; earlier years come from
    their archive file)."""
    return jsonify(_completed_in_year(_year_arg()))


@bp.get("/api/deck/completed-years")
def api_deck_completed_years():
    """Years the Completed view can show, of list ?list= or of every task."""
    return jsonify({"years": completed_years(_scope_arg())})


@bp.get("/api/deck/completed-count")
def api_deck_completed_count():
    """How many tasks the Completed view would list (for its link's label):
    this year's, of list ?list=, else of every task."""
    scope = _scope_arg()
    tasks = _completed_in_year(str(date.today().year))
    return jsonify({"count": sum(in_scope(t, scope) for t in tasks)})


def deck_counts() -> dict:
    """What the Deck module links show: the open tasks in Focus (DeckCore.
    isFocus: started, or due today or earlier) and, of those, how many are
    overdue or due today. Of the deck's home view (its `list=` and `tag=`)
    when a page declares one, else of every task."""
    today = date.today().isoformat()
    home = HOME_FN()
    params = home["params"] if home else {}
    scope, tag = _clean_list(params.get("list")) or None, params.get("tag", "")
    overdue = today_count = focus = 0
    for t in _read_deck():
        if not (in_scope(t, scope) and tag_param_matches(t, tag)):
            continue
        due, start = t.get("due_date"), t.get("start_date")
        if (start and start <= today) or (due and due <= today):
            focus += 1
        if due and due < today:
            overdue += 1
        elif due == today:
            today_count += 1
    return {"count": overdue + today_count, "overdue": overdue, "today": today_count, "focus": focus}


@bp.get("/api/deck/due-count")
def api_tasks_due_count():
    """deck_counts(): `count` (due today or overdue), `overdue`, `today`,
    `focus` (tasks in Focus)."""
    return jsonify(deck_counts())


@bp.post("/api/deck")
@_locked
def api_create_deck_item():
    data = _json_object()
    if not _as_str(data.get("title", ""), "title").strip():
        abort(400, description="Title cannot be empty")
    task = _new_task()
    _apply_task_fields(task, data)
    task["history"] = []          # its first dates are not changes
    _log(task, "created")
    active = _read_deck_strict()
    active.append(task)
    _write_deck(active, "Create deck task")
    return jsonify(task), 201


def _apply_task_fields(task: dict, data: dict) -> None:
    """Apply non-completion field updates from request data onto task in place."""
    old_due_date = task.get("due_date")
    if "title" in data:
        title = _as_str(data["title"], "title").strip()
        if not title:
            abort(400, description="Title cannot be empty")
        task["title"] = title
    if "description" in data:
        task["description"] = _as_str(data["description"] or "", "description").strip()
    if "tags" in data:
        task["tags"] = _clean_tags(_as_str_list(data["tags"] or [], "tags"))
    if "comments" in data:     # the whole list, for undoing a comment's delete or edit
        if not isinstance(data["comments"], list):
            abort(400, description="comments must be a list")
        task["comments"] = _clean_comments(data["comments"])
    if "due_date" in data:
        task["due_date"] = _validate_date(data["due_date"])
        if task["due_date"] != old_due_date:
            _log(task, "due", old_due_date, task["due_date"])
    if "sort_order" in data:
        task["sort_order"] = _as_order(data["sort_order"], "sort_order")
    if "keep" in data:
        task["keep"] = bool(data["keep"])
    if "last_done" in data:
        task["last_done"] = _validate_date(data["last_done"])
    old_start_date = task.get("start_date")
    old_rec = task.get("recurrence") or {}
    if "start_date" in data:
        task["start_date"] = _validate_date(data["start_date"])
        if task["start_date"] != old_start_date:
            _log(task, "start", old_start_date, task["start_date"])
    if "rec_type" in data:
        rec_type = data["rec_type"]
        if rec_type in ("daily", "weekly", "monthly", "yearly"):
            task["recurrence"] = {
                "type": rec_type,
                "interval": max(1, _as_int(data.get("rec_interval", 1), "rec_interval")),
                "from": "completion" if data.get("rec_from") == "completion" else "due",
            }
            # Re-saving the edit modal re-sends the recurrence; keep the
            # series' day of month unless the type or its primary date (due,
            # else start) actually changed — moving that date re-anchors it.
            old_primary = old_due_date or old_start_date
            new_primary = task.get("due_date") or task.get("start_date")
            if (old_rec.get("day") and old_rec.get("type") == rec_type
                    and new_primary == old_primary):
                task["recurrence"]["day"] = old_rec["day"]
            # Likewise the remembered start→due gap of a clamped start
            # (_spawn_recurring), while neither date changed.
            if (old_rec.get("lead") is not None and task.get("due_date") == old_due_date
                    and task.get("start_date") == old_start_date):
                task["recurrence"]["lead"] = old_rec["lead"]
        else:
            task["recurrence"] = None
    # A date changed by hand defines the gap from now on: forget a remembered
    # one (see _spawn_recurring).
    rec = task.get("recurrence")
    if rec and "lead" in rec and (task.get("due_date") != old_due_date
                                  or task.get("start_date") != old_start_date):
        del rec["lead"]
    # Keep and Repeat are two answers to "what happens after done?": a keep
    # task never repeats.
    if task.get("keep"):
        task["recurrence"] = None


@bp.put("/api/deck/<task_id>")
@_locked
def api_update_deck_item(task_id):
    data = _json_object()
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()

    task = next((t for t in active if t["id"] == task_id), None)
    in_completed = task is None
    if in_completed:
        task = next((t for t in completed_list if t["id"] == task_id), None)
    if not task:
        abort(404)

    _apply_task_fields(task, data)

    did_skip = bool(data.get("skip") and not task.get("completed_at"))
    if did_skip:
        # Skip this occurrence: archive it as won't-do but DO spawn the next
        # instance — the series continues, unlike a plain won't-do which ends
        # it. On a non-recurring task this degrades to a plain won't-do.
        _log(task, "skipped")      # before the spawn, which carries the history on
        spawned = _spawn_recurring(task)
        task["completed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        task["wont_do"] = True
        active = [t for t in active if t["id"] != task_id]
        completed_list.append(task)
        if spawned:
            active.append(spawned)
    elif "wont_do" in data:
        if data["wont_do"] and not task.get("completed_at"):
            # Won't do: move active → completed, marked as abandoned.
            # Deliberately does NOT spawn a recurring instance — abandoning a
            # task should not perpetuate the series (use skip for that).
            task["completed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            task["wont_do"] = True
            _log(task, "wontdo")
            active = [t for t in active if t["id"] != task_id]
            completed_list.append(task)
        elif not data["wont_do"] and task.get("completed_at"):
            # Reopen a won't-do task: move completed → active. _uncomplete_task
            # also removes the instance a skip spawned (exact spawned_from
            # match), so undoing a skip does not leave a duplicate.
            active = _uncomplete_task(task, active)
            completed_list = [t for t in completed_list if t["id"] != task_id]
            active.append(task)
    elif "completed" in data:
        if data["completed"] and task.get("keep") and not task.get("completed_at"):
            _keep_done(task)   # stays open, back in the pool
        elif data["completed"] and not task.get("completed_at"):
            # Complete: move active → completed
            spawned = _complete_task(task)
            active = [t for t in active if t["id"] != task_id]
            completed_list.append(task)
            if spawned:
                active.append(spawned)
        elif not data["completed"] and task.get("completed_at"):
            # Uncomplete: move completed → active
            active = _uncomplete_task(task, active)
            completed_list = [t for t in completed_list if t["id"] != task_id]
            active.append(task)

    # Undo sends the task's old history back with its old fields: it replaces
    # whatever this request logged, so an undone change leaves no entry.
    if "history" in data:
        task["history"] = _clean_history(data["history"])

    if did_skip:
        msg = "Skip deck task"
    elif "wont_do" in data:
        msg = "Reopen deck task" if not data["wont_do"] else "Abandon deck task"
    elif "completed" in data:
        msg = "Uncomplete deck task" if not data["completed"] else "Complete deck task"
    else:
        msg = "Update deck task"
    _write_both(active, completed_list, msg)
    return jsonify(task)


def _find_for_write(task_id: str) -> tuple[list[dict], list[dict], dict]:
    """(open tasks, every completed task, the task with that id) for a
    write; 404 when no task has it."""
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()
    task = next((t for t in active + completed_list if t["id"] == task_id), None)
    if not task:
        abort(404)
    return active, completed_list, task


@bp.post("/api/deck/<task_id>/comments")
@_locked
def api_add_comment(task_id):
    """Add a comment (a status line) to a task, open or done; returns the task."""
    text = _comment_text(_json_object().get("text"))
    active, completed_list, task = _find_for_write(task_id)
    task["comments"] = _clean_comments(task.get("comments")) + [{
        "id": _new_comment_id(), "at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "text": text}]
    _write_both(active, completed_list, "Comment on deck task")
    return jsonify(task), 201


def _task_comment(task: dict, comment_id: str) -> dict:
    task["comments"] = _clean_comments(task.get("comments"))
    comment = next((c for c in task["comments"] if c["id"] == comment_id), None)
    if not comment:
        abort(404)
    return comment


@bp.put("/api/deck/<task_id>/comments/<comment_id>")
@_locked
def api_edit_comment(task_id, comment_id):
    """Change a comment's text; its time stays. Returns the task."""
    text = _comment_text(_json_object().get("text"))
    active, completed_list, task = _find_for_write(task_id)
    _task_comment(task, comment_id)["text"] = text
    _write_both(active, completed_list, "Edit deck task comment")
    return jsonify(task)


@bp.delete("/api/deck/<task_id>/comments/<comment_id>")
@_locked
def api_delete_comment(task_id, comment_id):
    """Delete a comment; returns the task."""
    active, completed_list, task = _find_for_write(task_id)
    comment = _task_comment(task, comment_id)
    task["comments"] = [c for c in task["comments"] if c is not comment]
    _write_both(active, completed_list, "Delete deck task comment")
    return jsonify(task)


@bp.delete("/api/deck/<task_id>")
@_locked
def api_delete_deck_item(task_id):
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()
    active = [t for t in active if t["id"] != task_id]
    completed_list = [t for t in completed_list if t["id"] != task_id]
    _write_both(active, completed_list, "Delete deck task")
    return "", 204


@bp.post("/api/deck/batch-delete")
@_locked
def api_batch_delete_deck():
    data = _json_object()
    ids = _id_list(data)
    if not ids or not isinstance(ids, list):
        abort(400)
    id_set = set(ids)
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()
    active = [t for t in active if t["id"] not in id_set]
    completed_list = [t for t in completed_list if t["id"] not in id_set]
    _write_both(active, completed_list, "Delete deck tasks")
    return "", 204


def _sanitize_snapshot(snap: dict) -> dict | None:
    """Rebuild a task from an untrusted snapshot, keeping only known fields.

    Returns None for snapshots without a usable title. Dates are validated;
    invalid optional values degrade to their defaults rather than aborting,
    so one bad field doesn't lose the rest of an undo."""
    title = str(snap.get("title") or "").strip()
    if not title:
        return None
    _normalize_task(snap)   # before reading fields: it may rewrite the notes
    task = _new_task(title=title)
    task["description"] = str(snap.get("description") or "")
    task["tags"] = _clean_tags(snap.get("tags") if isinstance(snap.get("tags"), list) else [])
    for field in ("due_date", "start_date", "last_done"):
        val = snap.get(field)
        if isinstance(val, str):
            try:
                datetime.strptime(val, "%Y-%m-%d")
                task[field] = val
            except ValueError:
                pass
    if isinstance(snap.get("id"), str) and snap["id"]:
        task["id"] = snap["id"]
    for field in ("created_at", "completed_at", "spawned_from"):
        if isinstance(snap.get(field), str) and snap[field]:
            task[field] = snap[field]
    if isinstance(snap.get("sort_order"), int):
        task["sort_order"] = snap["sort_order"]
    task["wont_do"] = bool(snap.get("wont_do"))
    task["keep"] = bool(snap.get("keep"))
    task["history"] = _clean_history(snap.get("history"))
    task["comments"] = _clean_comments(snap.get("comments"))
    rec = snap.get("recurrence")
    if isinstance(rec, dict) and rec.get("type") in ("daily", "weekly", "monthly", "yearly"):
        task["recurrence"] = {
            "type": rec["type"],
            "interval": rec["interval"] if isinstance(rec.get("interval"), int)
                        and not isinstance(rec["interval"], bool) and rec["interval"] >= 1 else 1,
            "from": "completion" if rec.get("from") == "completion" else "due",
        }
        if isinstance(rec.get("day"), int) and 1 <= rec["day"] <= 31:
            task["recurrence"]["day"] = rec["day"]
        if isinstance(rec.get("lead"), int) and not isinstance(rec["lead"], bool) and rec["lead"] >= 0:
            task["recurrence"]["lead"] = rec["lead"]
    if task["keep"]:
        task["recurrence"] = None
    return task


@bp.post("/api/deck/restore")
@_locked
def api_restore_deck_items():
    """Re-insert full task snapshots (undo after delete). Snapshots keep their
    original id/created_at/etc.; ids that already exist are skipped."""
    data = _json_object()
    snaps = data.get("tasks", [])
    if not snaps or not isinstance(snaps, list):
        abort(400)
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()
    existing = {t["id"] for t in active} | {t["id"] for t in completed_list}
    restored = []
    for snap in snaps:
        if not isinstance(snap, dict):
            continue
        task = _sanitize_snapshot(snap)
        if task is None or task["id"] in existing:
            continue
        existing.add(task["id"])
        (completed_list if task["completed_at"] else active).append(task)
        restored.append(task)
    if restored:
        _write_both(active, completed_list, "Restore deck tasks")
    return jsonify(restored), 201


@bp.post("/api/deck/batch-complete")
@_locked
def api_batch_complete_deck():
    data = _json_object()
    ids = _id_list(data)
    mark_completed = data.get("completed", True)
    if not ids or not isinstance(ids, list):
        abort(400)
    id_set = set(ids)
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    updated = []

    if mark_completed:
        for task in active:
            if task["id"] in id_set and task.get("keep") and not task.get("completed_at"):
                _keep_done(task)   # stays open, back in the pool
                updated.append(task)
        moving = [t for t in active if t["id"] in id_set
                  and not t.get("completed_at") and not t.get("keep")]
        moving_ids = {t["id"] for t in moving}
        spawned_tasks = []
        for task in moving:
            spawned = _complete_task(task, now)
            if spawned:
                spawned_tasks.append(spawned)
            updated.append(task)
        active = [t for t in active if t["id"] not in moving_ids] + spawned_tasks
        completed_list = completed_list + moving
    else:
        moving = [t for t in completed_list if t["id"] in id_set and t.get("completed_at")]
        moving_ids = {t["id"] for t in moving}
        for task in moving:
            active = _uncomplete_task(task, active, protect_ids=id_set)
            updated.append(task)
        completed_list = [t for t in completed_list if t["id"] not in moving_ids]
        active = active + moving

    _write_both(active, completed_list, "Complete deck tasks" if mark_completed else "Reopen deck tasks")
    return jsonify(updated)


@bp.post("/api/deck/reorder")
@_locked
def api_reorder_deck():
    data = _json_object()
    order = data.get("order", [])
    updates = data.get("updates", [])
    if not isinstance(order, list) or not isinstance(updates, list) \
            or not all(isinstance(u, dict) for u in updates):
        abort(400, description="order must be a list of ids, updates a list of objects")
    if not order and not updates:
        abort(400)
    field = "sort_order"
    active = _read_deck_strict()
    id_map = {t["id"]: t for t in active}
    for i, task_id in enumerate(order):
        if task_id in id_map:
            id_map[task_id][field] = i
    for u in updates:
        task_id = u.get("id")
        val = u.get(field)
        # null clears the field: the task leaves the manual order (the
        # Sorted section of Open and Focus).
        if val is None and task_id in id_map:
            id_map[task_id][field] = None
            continue
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if task_id in id_map:
            id_map[task_id][field] = val
    _write_deck(active, "Reorder deck")
    return "", 204
@bp.post("/api/deck/batch-tag")
@_locked
def api_batch_tag_deck():
    """Add or remove tags from multiple deck items."""
    data = _json_object()
    ids = _id_list(data)
    if not isinstance(data.get("add", []), list) or not isinstance(data.get("remove", []), list):
        abort(400, description="add/remove must be lists of strings")
    add_tags = _clean_tags(_as_str_list(data.get("add", []), "add"))
    remove_tags = [t.lower() for t in _clean_tags(_as_str_list(data.get("remove", []), "remove"))]
    if not ids or not isinstance(ids, list):
        abort(400)
    if not add_tags and not remove_tags:
        abort(400)
    id_set = set(ids)
    active = _read_deck_strict()
    completed_list = _read_completed_all_strict()
    updated = []
    for task in active + completed_list:
        if task["id"] not in id_set:
            continue
        current = task.get("tags") or []
        if add_tags:
            existing_lower = {t.lower() for t in current}
            for tag in add_tags:
                if tag.lower() not in existing_lower:
                    current.append(tag)
                    existing_lower.add(tag.lower())
        if remove_tags:
            current = [t for t in current if t.lower() not in remove_tags]
        task["tags"] = current
        updated.append(task)
    _write_both(active, completed_list, "Tag deck tasks")
    return jsonify(updated)
