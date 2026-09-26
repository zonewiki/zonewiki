"""Deck — task management module for Leaf wiki.

Provides:
- Task CRUD operations and storage (_deck.json for active, _deck_completed.json for completed)
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
DECK_FILE: Path = Path("_deck.json")
GIT_ENABLED: bool = False
_git_commit_fn = None  # type: ignore


def _completed_path() -> Path:
    """Derive the completed-tasks file path from DECK_FILE (same directory)."""
    return DECK_FILE.parent / "_deck_completed.json"


def init_deck(wiki_dir: Path, git_enabled: bool, git_commit_fn) -> None:
    """Initialize module-level state from the main server."""
    global WIKI_DIR, DECK_FILE, GIT_ENABLED, _git_commit_fn
    WIKI_DIR = wiki_dir
    DECK_FILE = wiki_dir / "_deck.json"
    GIT_ENABLED = git_enabled
    _git_commit_fn = git_commit_fn
    _migrate_completed_split()
    _migrate_deck_files()
    _archive_old_completed()


def _migrate_completed_split() -> None:
    """One-time migration: move completed tasks from _deck.json into _deck_completed.json."""
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
    notes already link to it."""
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
            _git_commit_fn(changed, "Normalize deck files (date model, tags, page links)")


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


def _archive_glob() -> list[Path]:
    """Per-year archive files, e.g. _deck_completed-2025.json (sorted)."""
    return sorted(DECK_FILE.parent.glob("_deck_completed-*.json"))


def _read_archived() -> list[dict]:
    """Completed tasks rolled into per-year archives — searched, not shown."""
    tasks: list[dict] = []
    for path in _archive_glob():
        tasks.extend(_read_list_lenient(path))
    return tasks


def _archive_old_completed() -> None:
    """Roll completed tasks from previous years into _deck_completed-YYYY.json.

    Runs at startup so the hot completed file (what the Completed tab loads)
    stays small forever. Archived tasks remain in the search index via
    _read_archived(). Skips silently if the hot file is unreadable — archiving
    is an optimization, never worth failing startup over."""
    with _LOCK:
        completed = _read_completed()
        current_year = str(date.today().year)
        keep, old_by_year = [], {}
        for t in completed:
            year = (t.get("completed_at") or "")[:4]
            if year and year < current_year:
                old_by_year.setdefault(year, []).append(t)
            else:
                keep.append(t)
        if not old_by_year:
            return
        changed = []
        for year, tasks in old_by_year.items():
            path = DECK_FILE.parent / f"_deck_completed-{year}.json"
            merged = _read_list_lenient(path) + tasks
            _atomic_write(path, json.dumps(merged, indent=2, ensure_ascii=False))
            changed.append(path)
        _atomic_write(_completed_path(), json.dumps(keep, indent=2, ensure_ascii=False))
        if GIT_ENABLED and _git_commit_fn:
            _git_commit_fn(changed + [_completed_path()], "Archive completed deck tasks")


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
    """Write both files and commit them together in a single git commit."""
    completed_file = _completed_path()
    _atomic_write(DECK_FILE, json.dumps(active, indent=2, ensure_ascii=False))
    _atomic_write(completed_file, json.dumps(completed, indent=2, ensure_ascii=False))
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn([DECK_FILE, completed_file], message)


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
        "focus_order": None,
        "sort_order": None,
        "recurrence": None,
        "list": None,
        "spawned_from": None,
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
    them ("start 3 days before due" carries over to every instance)."""
    rec = task.get("recurrence")
    if not rec:
        return None
    today = date.today()
    due, start = _parse_date(task.get("due_date")), _parse_date(task.get("start_date"))
    primary = due or start
    anchored = primary is not None and rec.get("from") != "completion"
    next_date, next_rec = _next_occurrence(rec, primary if anchored else today, anchored, today)
    if due:
        new_due = next_date
        new_start = next_date - (due - start) if start else None
    elif start:
        new_due, new_start = None, next_date
    else:
        # No date at all: the series gets a due date (API-created only; the
        # edit modal requires a date before it offers Repeat).
        new_due, new_start = next_date, None
    return _new_task(
        title=task["title"],
        description=task.get("description", ""),
        tags=task.get("tags", []),
        due_date=new_due.isoformat() if new_due else None,
        start_date=new_start.isoformat() if new_start else None,
        sort_order=task.get("sort_order", 0),
        recurrence=next_rec,
        list=task.get("list"),
        spawned_from=task["id"],
    )


def _parse_date(val) -> date | None:
    try:
        return datetime.strptime(val, "%Y-%m-%d").date() if val else None
    except (TypeError, ValueError):
        return None


def _complete_task(task: dict, now: str | None = None) -> dict | None:
    """Stamp task as completed; return the spawned recurring instance, if any."""
    task["completed_at"] = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    task["wont_do"] = False
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
# value the client uses to activate a tab. Lets `[[deck filter=all]]` work
# using the labels the user actually sees, not just internal names. "backlog"
# is the tab's former label, kept so existing embeds keep working.
_FILTER_ALIASES = {
    "focus": "focus", "today": "focus",
    "all": "open", "backlog": "open", "open": "open",   # "all": the label before "Open"
    "plan": "due", "upcoming": "due", "due": "due",
    "completed": "completed", "done": "completed",
}


def known_lists() -> list[str]:
    """Names of every list some task (open or completed) belongs to."""
    return sorted({t["list"] for t in _read_all() if t.get("list")})


def in_list(task: dict, name: str | None) -> bool:
    """Whether task belongs to the view of list `name` (None: the main deck,
    which holds exactly the tasks that are in no list)."""
    return (task.get("list") or None) == (name or None)


def _build_deck_embed(params: dict, instance_id: int = 0) -> str:
    """Render the full interactive deck app HTML for embedding in a wiki page.

    instance_id differentiates multiple embeds on the same page.
    """
    tag_val = params.get("tag", "")
    list_val = _clean_list(params.get("list")) or ""
    raw_filter = params.get("filter", "").strip().lower()
    filt = _FILTER_ALIASES.get(raw_filter, raw_filter)
    try:
        capacity = max(1, min(99, int(params.get("capacity", ""))))
    except ValueError:
        capacity = ""  # client falls back to its default
    # `unplanned=1`/`unplanned=0` pins the Open tab's "Unplanned only" filter on
    # or off for this embed. Omitted, the client uses the stored preference,
    # which defaults off — so pinning it on gives a planning-queue embed.
    raw_unplanned = params.get("unplanned", "").strip().lower()
    if raw_unplanned in ("1", "true", "yes"):
        unplanned = "1"
    elif raw_unplanned in ("0", "false", "no"):
        unplanned = "0"
    else:
        unplanned = ""

    init_tag = html.escape(tag_val)
    init_filter = html.escape(filt)
    # Every list name in use, for the edit modal's List suggestions.
    list_options = "".join(f'<option value="{html.escape(n)}">'
                           for n in known_lists())

    # Use instance suffix for unique IDs when multiple embeds exist
    sfx = f"-{instance_id}" if instance_id > 0 else ""

    return f"""<div class="tasks-app" id="tasks-app{sfx}" data-init-tag="{init_tag}" data-init-list="{html.escape(list_val)}" data-init-filter="{init_filter}" data-init-capacity="{capacity}" data-init-unplanned="{unplanned}" data-init-new="" data-instance="{instance_id}">
  <div class="deck-header">
    <div class="view-tabs" id="tasks-tabs{sfx}" role="tablist" aria-label="View">
      <button type="button" role="tab" data-filter="open" aria-selected="false">Open</button>
      <button type="button" role="tab" data-filter="due" aria-selected="false">Plan</button>
      <button type="button" role="tab" data-filter="focus" aria-selected="false">Focus</button>
    </div>
    <div class="tasks-toolbar">
      <input type="text" id="task-filter-search{sfx}" placeholder="Search… #tag" title="Text searches title and notes · #tag filters by tag · #a #b = both tags · #a|b = either" autocomplete="off">
      <select id="task-sort{sfx}" title="Sort order" aria-label="Sort order">
        <option value="manual">Manual</option>
        <option value="created_at">Created</option>
      </select>
      <label class="task-unplanned-toggle" id="task-unplanned-wrap{sfx}" title="Show only tasks with no start date — the queue still awaiting a decision about when to do them. Uncheck to see every open task.">
        <input type="checkbox" id="task-unplanned{sfx}"> Unplanned
      </label>
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
    <div class="task-field">
      <label for="task-edit-desc{sfx}">Notes</label>
      <textarea id="task-edit-desc{sfx}" name="description" rows="6"></textarea>
    </div>
    <div class="task-field">
      <label for="task-edit-tags{sfx}">Tags <span class="field-hint">(comma-separated, no spaces)</span></label>
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
    <details class="task-edit-links" id="task-edit-links{sfx}">
      <summary>List<span class="task-edit-links-summary" id="task-edit-links-summary{sfx}"></span></summary>
      <div class="task-field" title="A task in a list shows only where [[deck list=…]] embeds that list, not in the main deck">
        <label for="task-edit-list{sfx}">List <span class="field-hint">(separate list; empty = main deck)</span></label>
        <input type="text" id="task-edit-list{sfx}" name="list" list="task-edit-lists{sfx}" autocomplete="off" placeholder="—">
        <datalist id="task-edit-lists{sfx}">{list_options}</datalist>
      </div>
    </details>
    <div class="task-edit-created" id="task-edit-created{sfx}" style="display:none"></div>
    <div class="task-field" id="task-edit-inbox-field{sfx}" style="display:none">
      <label><input type="checkbox" id="task-edit-inbox{sfx}"> Move to New (remove from manual order)</label>
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
        for path, tasks in ((DECK_FILE, _read_deck_strict()),
                            (_completed_path(), _read_completed_strict())):
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
                            "list": task.get("list")})
    return results


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
        text = f"{title} {desc}".strip()
        docs.append({
            "slug": "_deck",
            "title": f"[Task] {title}",
            "excerpt_text": text,
            "tokens": search.tokenize(text)
            + search.tokenize(title) * search.TITLE_WEIGHT,
            "type": "task",
            "task_id": task["id"],
            "status": "done" if task.get("completed_at") else "open",
            "list": task.get("list"),
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


@bp.get("/api/deck/completed")
def api_get_deck_completed():
    return jsonify(_read_completed())


@bp.get("/api/deck/completed-count")
def api_deck_completed_count():
    """How many tasks the Completed view would list (for its link's label):
    those of list ?list=, or without one for the main deck."""
    name = _clean_list(request.args.get("list"))
    return jsonify({"count": sum(in_list(t, name) for t in _read_completed())})


@bp.get("/api/deck/due-count")
def api_tasks_due_count():
    today = date.today().isoformat()
    overdue = today_count = 0
    tasks = _read_deck()
    for t in tasks:
        # Tasks in a separate list stay on their list's page, out of the badge.
        if not t.get("due_date") or t.get("list"):
            continue
        if t["due_date"] < today:
            overdue += 1
        elif t["due_date"] == today:
            today_count += 1
    return jsonify({"count": overdue + today_count, "overdue": overdue, "today": today_count})


@bp.post("/api/deck")
@_locked
def api_create_deck_item():
    data = _json_object()
    if not _as_str(data.get("title", ""), "title").strip():
        abort(400, description="Title cannot be empty")
    task = _new_task()
    _apply_task_fields(task, data)
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
    if "due_date" in data:
        task["due_date"] = _validate_date(data["due_date"])
    if "sort_order" in data:
        task["sort_order"] = _as_order(data["sort_order"], "sort_order")
    if "focus_order" in data:
        task["focus_order"] = _as_order(data["focus_order"], "focus_order")
    if "list" in data:
        task["list"] = _clean_list(_as_str(data["list"] or "", "list"))
    old_start_date = task.get("start_date")
    old_rec = task.get("recurrence") or {}
    if "start_date" in data:
        task["start_date"] = _validate_date(data["start_date"])
    # Reset focus_order when the task leaves or re-enters the focus view
    # (null→value or value→null). Rescheduling (value→value) keeps the order.
    if (old_start_date is None) != (task.get("start_date") is None):
        task["focus_order"] = None
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
        else:
            task["recurrence"] = None


@bp.put("/api/deck/<task_id>")
@_locked
def api_update_deck_item(task_id):
    data = _json_object()
    active = _read_deck_strict()
    completed_list = _read_completed_strict()

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
        if data["completed"] and not task.get("completed_at"):
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


@bp.delete("/api/deck/<task_id>")
@_locked
def api_delete_deck_item(task_id):
    active = _read_deck_strict()
    completed_list = _read_completed_strict()
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
    completed_list = _read_completed_strict()
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
    for field in ("due_date", "start_date"):
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
    if isinstance(snap.get("list"), str):
        task["list"] = _clean_list(snap["list"])
    if isinstance(snap.get("sort_order"), int):
        task["sort_order"] = snap["sort_order"]
    task["wont_do"] = bool(snap.get("wont_do"))
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
    completed_list = _read_completed_strict()
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
    completed_list = _read_completed_strict()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    updated = []

    if mark_completed:
        moving = [t for t in active if t["id"] in id_set and not t.get("completed_at")]
        spawned_tasks = []
        for task in moving:
            spawned = _complete_task(task, now)
            if spawned:
                spawned_tasks.append(spawned)
            updated.append(task)
        active = [t for t in active if t["id"] not in id_set] + spawned_tasks
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
    field = data.get("field", "sort_order")
    if field not in ("sort_order", "focus_order"):
        abort(400)
    active = _read_deck_strict()
    id_map = {t["id"]: t for t in active}
    for i, task_id in enumerate(order):
        if task_id in id_map:
            id_map[task_id][field] = i
    for u in updates:
        task_id = u.get("id")
        val = u.get(field)
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
    completed_list = _read_completed_strict()
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
