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
    return data


def _read_deck_strict() -> list[dict]:
    return _read_list_strict(DECK_FILE)


def _read_completed_strict() -> list[dict]:
    return _read_list_strict(_completed_path())


def _read_list_lenient(path: Path) -> list[dict]:
    """Read a JSON list, treating a missing or unreadable file as empty.

    Used on read-only paths; write paths use _read_list_strict instead."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
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


def _validate_date(val: str | None) -> str | None:
    """Return val if it's a valid YYYY-MM-DD string or None, else raise 400."""
    if not val:
        return None
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
        "start_date": None,
        "due_date": None,
        "defer_days": None,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "completed_at": None,
        "wont_do": False,
        "focus_date": None,
        "focus_order": None,
        "sort_order": None,
        "recurrence": None,
        "page": None,
        "blocked_by": [],
        "spawned_from": None,
    }
    task.update(overrides)
    return task


def _add_months(base: date, months: int) -> date:
    """Add months to a date, clamping the day to the target month's last day
    (Jan 31 + 1 month = Feb 28/29, not an invalid date and not always the 28th)."""
    month = base.month + months
    year = base.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _rec_step(base: date, rtype: str, interval: int) -> date:
    if rtype == "weekly":
        return base + timedelta(weeks=interval)
    if rtype == "monthly":
        return _add_months(base, interval)
    return base + timedelta(days=interval)  # daily (and any unknown type)


def _spawn_recurring(task: dict) -> dict | None:
    """If task has recurrence, create the next instance and return it.

    ``recurrence.from`` picks the schedule anchor: ``"due"`` (default) steps
    from the previous due date, keeping the series calendar-aligned;
    ``"completion"`` steps from today, for interval habits. In both modes the
    next due date is caught up until it is >= today, so completing a
    long-overdue task never spawns an instance that is already overdue."""
    rec = task.get("recurrence")
    if not rec:
        return None
    rtype = rec.get("type", "daily")
    interval = rec.get("interval", 1)
    today = date.today()

    base = today
    if rec.get("from") != "completion" and task.get("due_date"):
        try:
            base = datetime.strptime(task["due_date"], "%Y-%m-%d").date()
        except ValueError:
            pass

    next_due = _rec_step(base, rtype, interval)
    while next_due < today:
        next_due = _rec_step(next_due, rtype, interval)

    return _new_task(
        title=task["title"],
        description=task.get("description", ""),
        tags=task.get("tags", []),
        due_date=next_due.strftime("%Y-%m-%d"),
        defer_days=task.get("defer_days"),
        sort_order=task.get("sort_order", 0),
        recurrence=task.get("recurrence"),
        page=task.get("page"),
        spawned_from=task["id"],
    )


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
# value the client uses to activate a tab. Lets `[[deck filter=backlog]]` work
# using the labels the user actually sees, not just internal names.
_FILTER_ALIASES = {
    "focus": "focus", "today": "focus",
    "backlog": "open", "open": "open",
    "plan": "due", "upcoming": "due", "due": "due",
    "deferred": "hidden", "hidden": "hidden",
    "completed": "completed", "done": "completed",
}


def _build_deck_embed(params: dict, page_slug: str = "", instance_id: int = 0) -> str:
    """Render the full interactive deck app HTML for embedding in a wiki page.

    instance_id differentiates multiple embeds on the same page.
    """
    tag_val = params.get("tag", "")
    page_val = params.get("page", "")
    if page_val == "self" and page_slug:
        page_val = page_slug
    raw_filter = params.get("filter", "").strip().lower()
    filt = _FILTER_ALIASES.get(raw_filter, raw_filter)
    try:
        capacity = max(1, min(99, int(params.get("capacity", ""))))
    except ValueError:
        capacity = ""  # client falls back to its default

    init_tag = html.escape(tag_val)
    init_page = html.escape(page_val)
    init_filter = html.escape(filt)

    # Use instance suffix for unique IDs when multiple embeds exist
    sfx = f"-{instance_id}" if instance_id > 0 else ""

    return f"""<div class="tasks-app" id="tasks-app{sfx}" data-init-tag="{init_tag}" data-init-page="{init_page}" data-init-filter="{init_filter}" data-init-capacity="{capacity}" data-init-new="" data-instance="{instance_id}">
  <div class="deck-header">
    <select class="tab-select" id="tasks-tabs{sfx}" aria-label="View">
      <option value="focus">Focus</option>
      <option value="due">Plan</option>
      <option value="open">Backlog</option>
      <option value="hidden">Deferred</option>
      <option value="completed">Completed</option>
    </select>
    <div class="tasks-toolbar">
      <input type="text" id="task-filter-search{sfx}" placeholder="Search…" autocomplete="off">
      <input type="text" id="task-filter-tag{sfx}" placeholder="tag…" title="Comma = all tags (AND) · pipe a|b = any tag (OR)" autocomplete="off">
      <select id="task-sort{sfx}">
        <option value="manual">Manual order</option>
        <option value="created_at">Created</option>
      </select>
      <button class="btn btn-primary" id="task-add-btn{sfx}">+ New</button>
    </div>
  </div>
  <ul class="tasks-list" id="tasks-list{sfx}"></ul>
</div>
<dialog id="task-edit-modal{sfx}" class="task-edit-modal">
  <form id="task-edit-form{sfx}" class="task-edit-form">
    <input type="hidden" id="task-edit-id{sfx}" name="id">
    <div class="task-field">
      <label for="task-edit-title{sfx}">Title</label>
      <input type="text" id="task-edit-title{sfx}" name="title" required autocomplete="off">
    </div>
    <div class="task-field">
      <label for="task-edit-desc{sfx}">Notes</label>
      <textarea id="task-edit-desc{sfx}" name="description" rows="6"></textarea>
    </div>
    <div class="task-field">
      <label for="task-edit-tags{sfx}">Tags <span class="field-hint">(comma-separated)</span></label>
      <input type="text" id="task-edit-tags{sfx}" name="tags" autocomplete="off">
    </div>
    <div class="task-field-row">
      <div class="task-field">
        <label for="task-edit-due{sfx}">Due date</label>
        <input type="date" id="task-edit-due{sfx}" name="due_date">
        <div class="task-date-shortcuts">
          <button type="button" data-due="today">Today</button>
          <button type="button" data-due="tomorrow">Tmrw</button>
          <button type="button" data-due="+3">+3d</button>
          <button type="button" data-due="next-week">Mon</button>
          <button type="button" data-due="clear">✕</button>
        </div>
      </div>
      <div class="task-field">
        <label for="task-edit-rec-type{sfx}">Repeat</label>
        <select id="task-edit-rec-type{sfx}" name="rec_type">
          <option value="">—</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </div>
      <div class="task-field" id="task-rec-interval-field{sfx}" style="display:none">
        <label for="task-edit-rec-interval{sfx}">Every N</label>
        <input type="number" id="task-edit-rec-interval{sfx}" name="rec_interval" min="1" value="1">
      </div>
      <div class="task-field task-field-center" id="task-rec-from-field{sfx}" style="display:none" title="Repeat from the completion date instead of the due date">
        <label for="task-edit-rec-from{sfx}">From done</label>
        <input type="checkbox" id="task-edit-rec-from{sfx}" name="rec_from">
      </div>
      <div class="task-field">
        <label for="task-edit-focus{sfx}">Plan for</label>
        <input type="date" id="task-edit-focus{sfx}" name="focus_date">
        <div class="task-date-shortcuts">
          <button type="button" data-focus="today">Today</button>
          <button type="button" data-focus="tmrw">Tmrw</button>
          <button type="button" data-focus="mon">Mon</button>
          <button type="button" data-focus="clear">✕</button>
        </div>
      </div>
    </div>
    <div class="task-field-row hide-until-row">
      <div class="task-field">
        <label for="task-edit-start{sfx}">Hide until</label>
        <input type="date" id="task-edit-start{sfx}" name="start_date">
      </div>
      <div class="task-field task-field-center">
        <label for="task-edit-defer-due{sfx}">Due date</label>
        <input type="checkbox" id="task-edit-defer-due{sfx}">
      </div>
      <div class="task-field">
        <label for="task-edit-defer-days{sfx}">N days before due</label>
        <input type="number" id="task-edit-defer-days{sfx}" name="defer_days" min="1" placeholder="e.g. 3">
      </div>
    </div>
    <div class="task-field-row">
      <div class="task-field">
        <label>Links</label>
        <div class="links-picker" id="links-picker{sfx}">
          <div class="link-chips" id="link-chips{sfx}"></div>
          <div class="link-add-row">
            <select id="link-type{sfx}" class="link-type-select">
              <option value="blocked_by">blocked by</option>
              <option value="blocking">blocking</option>
            </select>
            <input type="text" class="link-search" id="link-search{sfx}" placeholder="Search tasks…" autocomplete="off">
          </div>
        </div>
      </div>
    </div>
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


def process_deck_embeds(text: str, slug: str = "") -> tuple[str, list[str]]:
    """Replace all [[deck ...]] directives with placeholders.

    Returns (modified_text, list_of_embed_htmls).
    Supports multiple embeds per page.
    """
    embeds = []
    instance = [0]

    def replacer(m: re.Match) -> str:
        params = _parse_tasks_params(m.group(1) or "")
        embed_html = _build_deck_embed(params, page_slug=slug, instance_id=instance[0])
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
            results.append({"id": task["id"], "title": task.get("title", "")})
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
    # JSON body so deck.js can surface the reason (e.g. dependency cycles)
    # instead of Flask's HTML error page.
    return jsonify({"error": exc.description}), 400


@bp.get("/api/deck")
def api_get_deck():
    return jsonify(_read_deck())


@bp.get("/api/deck/completed")
def api_get_deck_completed():
    return jsonify(_read_completed())


def _is_deferred(task: dict, today: str) -> bool:
    """True when the task is hidden until a later date — either an absolute
    start_date or defer_days before its due_date (most restrictive wins).
    An explicit plan overrides deferral: focus_date <= today keeps the task
    visible. Mirror of isDeferred() in deck-core.js; keep the two in sync."""
    if task.get("completed_at"):
        return False
    if task.get("focus_date") and task["focus_date"] <= today:
        return False
    if task.get("start_date") and task["start_date"] > today:
        return True
    if task.get("defer_days") is not None and task.get("due_date"):
        due_dt = datetime.fromisoformat(task["due_date"] + "T00:00:00")
        show_from = (due_dt - timedelta(days=task["defer_days"])).date().isoformat()
        if show_from > today:
            return True
    return False


@bp.get("/api/deck/due-count")
def api_tasks_due_count():
    today = date.today().isoformat()
    overdue = today_count = 0
    tasks = _read_deck()
    active_ids = {t["id"] for t in tasks}
    for t in tasks:
        # Skip tasks not actionable right now: deferred ones are not visible in
        # Backlog/Focus/Plan, and blocked ones can't be worked on anyway.
        if not t.get("due_date") or _is_deferred(t, today):
            continue
        if any(b in active_ids for b in t.get("blocked_by") or []):
            continue
        if t["due_date"] < today:
            overdue += 1
        elif t["due_date"] == today:
            today_count += 1
    return jsonify({"count": overdue + today_count, "overdue": overdue, "today": today_count})


def _has_blocking_cycle(start_id: str, tasks: list[dict]) -> bool:
    """True when following blocked_by edges from start_id reaches start_id
    again. A cycle would make every task in it permanently non-actionable
    (each waiting on the other), so writes that create one are rejected."""
    graph = {t["id"]: t.get("blocked_by") or [] for t in tasks}
    seen = set()
    stack = list(graph.get(start_id, []))
    while stack:
        tid = stack.pop()
        if tid == start_id:
            return True
        if tid not in seen:
            seen.add(tid)
            stack.extend(graph.get(tid, []))
    return False


def _check_no_cycle(task_id: str, data: dict, active: list[dict]) -> None:
    if ("blocked_by" in data or "blocking" in data) and _has_blocking_cycle(task_id, active):
        abort(400, description="Rejected: this dependency would create a cycle")


def _reconcile_blocking(task_id: str, blocking_ids: list, active: list[dict]) -> None:
    """Make task_id block exactly blocking_ids: add it to those tasks'
    blocked_by and remove it everywhere else. Server-side so the whole
    edit lands in one write instead of one request per affected task."""
    wanted = {b for b in blocking_ids if isinstance(b, str)}
    for t in active:
        if t["id"] == task_id:
            continue
        blocked = t.get("blocked_by") or []
        if t["id"] in wanted and task_id not in blocked:
            t["blocked_by"] = blocked + [task_id]
        elif t["id"] not in wanted and task_id in blocked:
            t["blocked_by"] = [b for b in blocked if b != task_id]


@bp.post("/api/deck")
@_locked
def api_create_deck_item():
    data = request.get_json(force=True)
    if not data.get("title", "").strip():
        abort(400)
    task = _new_task()
    _apply_task_fields(task, data)
    active = _read_deck_strict()
    active.append(task)
    if isinstance(data.get("blocking"), list):
        _reconcile_blocking(task["id"], data["blocking"], active)
    _check_no_cycle(task["id"], data, active)
    _write_deck(active, "Create deck task")
    return jsonify(task), 201


def _apply_task_fields(task: dict, data: dict) -> None:
    """Apply non-completion field updates from request data onto task in place."""
    if "title" in data:
        title = data["title"].strip()
        if not title:
            abort(400, description="Title cannot be empty")
        task["title"] = title
    if "description" in data:
        task["description"] = data["description"].strip()
    if "tags" in data:
        task["tags"] = [t.strip() for t in data["tags"] if t.strip()]
    if "due_date" in data:
        task["due_date"] = _validate_date(data["due_date"])
    if "start_date" in data:
        task["start_date"] = _validate_date(data["start_date"])
    if "defer_days" in data:
        dd = data["defer_days"]
        task["defer_days"] = max(0, int(dd)) if dd is not None else None
    if "sort_order" in data:
        task["sort_order"] = data["sort_order"]
    if "focus_order" in data:
        task["focus_order"] = data["focus_order"]
    if "page" in data:
        task["page"] = (data["page"] or "").strip() or None
    if "focus_date" in data:
        new_focus_date = _validate_date(data["focus_date"])
        old_focus_date = task.get("focus_date")
        # Reset focus_order when the task leaves or re-enters the focus view
        # (null→value or value→null). Rescheduling (value→value) keeps the order.
        if (old_focus_date is None) != (new_focus_date is None):
            task["focus_order"] = None
        task["focus_date"] = new_focus_date
    if "blocked_by" in data:
        blocked = data["blocked_by"]
        if isinstance(blocked, str):
            blocked = [b.strip() for b in blocked.split(",") if b.strip()]
        task["blocked_by"] = blocked if blocked else []
    if "rec_type" in data:
        rec_type = data["rec_type"]
        if rec_type in ("daily", "weekly", "monthly"):
            task["recurrence"] = {
                "type": rec_type,
                "interval": max(1, int(data.get("rec_interval", 1))),
                "from": "completion" if data.get("rec_from") == "completion" else "due",
            }
        else:
            task["recurrence"] = None


@bp.put("/api/deck/<task_id>")
@_locked
def api_update_deck_item(task_id):
    data = request.get_json(force=True)
    active = _read_deck_strict()
    completed_list = _read_completed_strict()

    task = next((t for t in active if t["id"] == task_id), None)
    in_completed = task is None
    if in_completed:
        task = next((t for t in completed_list if t["id"] == task_id), None)
    if not task:
        abort(404)

    _apply_task_fields(task, data)
    if isinstance(data.get("blocking"), list):
        _reconcile_blocking(task_id, data["blocking"], active)
    _check_no_cycle(task_id, data, active)

    if data.get("skip") and not task.get("completed_at"):
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

    if data.get("skip") and not task.get("completed_at"):
        msg = "Skip deck task"
    elif "wont_do" in data:
        msg = "Reopen deck task" if not data["wont_do"] else "Abandon deck task"
    elif "completed" in data:
        msg = "Uncomplete deck task" if not data["completed"] else "Complete deck task"
    else:
        msg = "Update deck task"
    _write_both(active, completed_list, msg)
    return jsonify(task)


def _strip_blocked_refs(dead_ids: set, *task_lists: list[dict]) -> None:
    """Remove deleted task ids from every remaining task's blocked_by, so
    deletion never leaves dangling dependency references."""
    for tasks in task_lists:
        for t in tasks:
            if t.get("blocked_by") and any(b in dead_ids for b in t["blocked_by"]):
                t["blocked_by"] = [b for b in t["blocked_by"] if b not in dead_ids]


@bp.delete("/api/deck/<task_id>")
@_locked
def api_delete_deck_item(task_id):
    active = _read_deck_strict()
    completed_list = _read_completed_strict()
    active = [t for t in active if t["id"] != task_id]
    completed_list = [t for t in completed_list if t["id"] != task_id]
    _strip_blocked_refs({task_id}, active, completed_list)
    _write_both(active, completed_list, "Delete deck task")
    return "", 204


@bp.post("/api/deck/batch-delete")
@_locked
def api_batch_delete_deck():
    data = request.get_json(force=True)
    ids = data.get("ids", [])
    if not ids or not isinstance(ids, list):
        abort(400)
    id_set = set(ids)
    active = _read_deck_strict()
    completed_list = _read_completed_strict()
    active = [t for t in active if t["id"] not in id_set]
    completed_list = [t for t in completed_list if t["id"] not in id_set]
    _strip_blocked_refs(id_set, active, completed_list)
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
    task = _new_task(title=title)
    task["description"] = str(snap.get("description") or "")
    task["tags"] = [str(t).strip() for t in snap.get("tags") or [] if str(t).strip()]
    for field in ("start_date", "due_date", "focus_date"):
        val = snap.get(field)
        if isinstance(val, str):
            try:
                datetime.strptime(val, "%Y-%m-%d")
                task[field] = val
            except ValueError:
                pass
    if isinstance(snap.get("defer_days"), int):
        task["defer_days"] = max(0, snap["defer_days"])
    if isinstance(snap.get("id"), str) and snap["id"]:
        task["id"] = snap["id"]
    for field in ("created_at", "completed_at", "spawned_from", "page"):
        if isinstance(snap.get(field), str) and snap[field]:
            task[field] = snap[field]
    if isinstance(snap.get("sort_order"), int):
        task["sort_order"] = snap["sort_order"]
    task["wont_do"] = bool(snap.get("wont_do"))
    rec = snap.get("recurrence")
    if isinstance(rec, dict) and rec.get("type") in ("daily", "weekly", "monthly"):
        task["recurrence"] = {
            "type": rec["type"],
            "interval": max(1, int(rec.get("interval") or 1)),
            "from": "completion" if rec.get("from") == "completion" else "due",
        }
    blocked = snap.get("blocked_by")
    if isinstance(blocked, list):
        task["blocked_by"] = [b for b in blocked if isinstance(b, str)]
    return task


@bp.post("/api/deck/restore")
@_locked
def api_restore_deck_items():
    """Re-insert full task snapshots (undo after delete). Snapshots keep their
    original id/created_at/etc.; ids that already exist are skipped."""
    data = request.get_json(force=True)
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
        # Re-add reverse dependency edges stripped when the task was deleted.
        if isinstance(snap.get("blocking"), list):
            _reconcile_blocking(task["id"], snap["blocking"], active)
        restored.append(task)
    if restored:
        _write_both(active, completed_list, "Restore deck tasks")
    return jsonify(restored), 201


@bp.post("/api/deck/batch-complete")
@_locked
def api_batch_complete_deck():
    data = request.get_json(force=True)
    ids = data.get("ids", [])
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
    data = request.get_json(force=True)
    order = data.get("order", [])
    updates = data.get("updates", [])
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
    data = request.get_json(force=True)
    ids = data.get("ids", [])
    add_tags = [t.strip() for t in data.get("add", []) if t.strip()]
    remove_tags = [t.strip().lower() for t in data.get("remove", []) if t.strip()]
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
