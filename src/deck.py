"""Deck — task management module for Leaf wiki.

Provides:
- Task CRUD operations and storage (_deck.json for active, _deck_completed.json for completed)
- [[deck]] embed HTML generation
- Task backlink resolution
- API route registration via Blueprint
"""

import functools
import html
import json
import os
import re
import subprocess
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


def _read_deck() -> list[dict]:
    """Read active (non-completed) tasks."""
    if not DECK_FILE.exists():
        return []
    try:
        data = json.loads(DECK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _read_completed() -> list[dict]:
    """Read completed tasks."""
    p = _completed_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _read_all() -> list[dict]:
    """Read all tasks (active + completed) — for search and backlinks.

    Holds _LOCK so the active/completed pair is a consistent snapshot rather
    than reading one file before and the other after a concurrent write that
    moves a task between them.
    """
    with _LOCK:
        return _read_deck() + _read_completed()


def _write_deck(tasks: list[dict]) -> None:
    _atomic_write(DECK_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))
    if GIT_ENABLED and _git_commit_fn:
        _git_commit_fn(DECK_FILE, "Update deck")


def _write_deck_no_commit(tasks: list[dict]) -> None:
    _atomic_write(DECK_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))


def _write_both(active: list[dict], completed: list[dict], message: str = "Update deck") -> None:
    """Write both files and commit them together in a single git commit."""
    completed_file = _completed_path()
    _atomic_write(DECK_FILE, json.dumps(active, indent=2, ensure_ascii=False))
    _atomic_write(completed_file, json.dumps(completed, indent=2, ensure_ascii=False))
    if GIT_ENABLED:
        subprocess.run(
            ["git", "add", "--", str(DECK_FILE), str(completed_file)],
            cwd=DECK_FILE.parent, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=DECK_FILE.parent, capture_output=True,
        )


def _validate_date(val: str | None) -> str | None:
    """Return val if it's a valid YYYY-MM-DD string or None, else raise 400."""
    if not val:
        return None
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return val
    except ValueError:
        abort(400, description="Invalid date format, expected YYYY-MM-DD")


def _spawn_recurring(task: dict) -> dict | None:
    """If task has recurrence, create the next instance and return it."""
    rec = task.get("recurrence")
    if not rec:
        return None
    rtype = rec.get("type", "daily")
    interval = rec.get("interval", 1)
    base_date = task.get("due_date")
    if base_date:
        try:
            base = datetime.strptime(base_date, "%Y-%m-%d")
        except ValueError:
            base = datetime.now()
    else:
        base = datetime.now()

    if rtype == "daily":
        next_due = base + timedelta(days=interval)
    elif rtype == "weekly":
        next_due = base + timedelta(weeks=interval)
    elif rtype == "monthly":
        month = base.month + interval
        year = base.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(base.day, 28)
        next_due = base.replace(year=year, month=month, day=day)
    else:
        next_due = base + timedelta(days=interval)

    new_task = {
        "id": str(uuid.uuid4()),
        "title": task["title"],
        "description": task.get("description", ""),
        "tags": task.get("tags", []),
        "start_date": None,
        "due_date": next_due.strftime("%Y-%m-%d"),
        "defer_days": task.get("defer_days"),
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "completed_at": None,
        "wont_do": False,
        "focus_date": None,
        "sort_order": task.get("sort_order", 0),
        "recurrence": task.get("recurrence"),
        "page": task.get("page"),
        "blocked_by": [],
    }
    return new_task


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
    "today": "today",
    "backlog": "open", "open": "open",
    "upcoming": "due", "due": "due",
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

    init_tag = html.escape(tag_val)
    init_page = html.escape(page_val)
    init_filter = html.escape(filt)

    # Use instance suffix for unique IDs when multiple embeds exist
    sfx = f"-{instance_id}" if instance_id > 0 else ""

    return f"""<div class="tasks-app" id="tasks-app{sfx}" data-init-tag="{init_tag}" data-init-page="{init_page}" data-init-filter="{init_filter}" data-init-new="" data-instance="{instance_id}">
  <div class="deck-header">
    <div class="tasks-tabs" id="tasks-tabs{sfx}">
      <button class="tab active" data-filter="open">Backlog</button>
      <button class="tab" data-filter="today">Today</button>
      <button class="tab" data-filter="due">Upcoming</button>
      <button class="tab" data-filter="hidden">Deferred</button>
      <button class="tab" data-filter="completed">Completed</button>
    </div>
    <button class="btn btn-primary" id="task-add-btn{sfx}">+ New Item</button>
  </div>
  <div class="tasks-toolbar">
    <input type="text" id="task-filter-search{sfx}" placeholder="Search tasks…" autocomplete="off">
    <input type="text" id="task-filter-tag{sfx}" placeholder="tag, tag…" title="Comma = all tags (AND) · pipe a|b = any tag (OR)" autocomplete="off">
    <select id="task-sort{sfx}">
      <option value="manual">Manual order</option>
      <option value="created_at">Created</option>
    </select>
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


def find_task_backlinks(slug: str, to_slug_fn) -> list[dict]:
    """Return deck tasks whose title or description contain a wikilink to slug."""
    results = []
    _MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(/page/([^)]+)\)')
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


def task_search_docs() -> list[dict]:
    """Return all tasks as search documents for the shared BM25 index.

    Documents carry pre-tokenized ``tokens`` (title boosted by TITLE_WEIGHT) plus
    passthrough fields (type/task_id/status) that server.do_search formats into
    results. Ranking and excerpting happen in the unified index, not here."""
    docs = []
    for task in _read_all():
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
    return docs


# ── API Routes ────────────────────────────────────────────────────────────────


@bp.errorhandler(_DeckCorrupt)
def _handle_deck_corrupt(exc):
    # The existing deck file could not be read. Refuse the write (rather than
    # silently overwriting it with a partial list) and tell the client to retry.
    return jsonify({"error": "deck storage temporarily unavailable, please retry"}), 503


@bp.get("/api/deck")
def api_get_deck():
    return jsonify(_read_deck())


@bp.get("/api/deck/completed")
def api_get_deck_completed():
    return jsonify(_read_completed())


@bp.get("/api/deck/due-count")
def api_tasks_due_count():
    today = date.today().isoformat()
    overdue = today_count = 0
    for t in _read_deck():
        if not t.get("due_date"):
            continue
        if t["due_date"] < today:
            overdue += 1
        elif t["due_date"] == today:
            today_count += 1
    return jsonify({"count": overdue + today_count, "overdue": overdue, "today": today_count})


@bp.post("/api/deck")
@_locked
def api_create_deck_item():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        abort(400)
    defer_days = data.get("defer_days")
    if defer_days is not None:
        defer_days = max(0, int(defer_days))
    blocked_by = data.get("blocked_by", [])
    if isinstance(blocked_by, str):
        blocked_by = [b.strip() for b in blocked_by.split(",") if b.strip()]
    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": data.get("description", "").strip(),
        "tags": [t.strip() for t in data.get("tags", []) if t.strip()],
        "start_date": _validate_date(data.get("start_date")),
        "due_date": _validate_date(data.get("due_date")),
        "defer_days": defer_days if defer_days is not None else None,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "completed_at": None,
        "wont_do": False,
        "focus_date": _validate_date(data.get("focus_date")),
        "sort_order": None,
        "recurrence": None,
        "page": (data.get("page") or "").strip() or None,
        "blocked_by": blocked_by,
    }
    rec_type = data.get("rec_type")
    if rec_type in ("daily", "weekly", "monthly"):
        task["recurrence"] = {
            "type": rec_type,
            "interval": max(1, int(data.get("rec_interval", 1))),
        }
    active = _read_deck_strict()
    active.append(task)
    _write_deck(active)
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
    if "page" in data:
        task["page"] = (data["page"] or "").strip() or None
    if "focus_date" in data:
        task["focus_date"] = _validate_date(data["focus_date"])
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

    if "wont_do" in data:
        if data["wont_do"] and not task.get("completed_at"):
            # Won't do: move active → completed, marked as abandoned.
            # Deliberately does NOT spawn a recurring instance — skipping an
            # occurrence should not perpetuate the series.
            task["completed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            task["wont_do"] = True
            active = [t for t in active if t["id"] != task_id]
            completed_list.append(task)
        elif not data["wont_do"] and task.get("completed_at"):
            # Reopen a won't-do task: move completed → active
            task["completed_at"] = None
            task["wont_do"] = False
            completed_list = [t for t in completed_list if t["id"] != task_id]
            active.append(task)
    elif "completed" in data:
        if data["completed"] and not task.get("completed_at"):
            # Complete: move active → completed
            task["completed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            task["wont_do"] = False
            spawned = _spawn_recurring(task)
            active = [t for t in active if t["id"] != task_id]
            completed_list.append(task)
            if spawned:
                active.append(spawned)
        elif not data["completed"] and task.get("completed_at"):
            # Uncomplete: move completed → active
            if task.get("recurrence"):
                completed_at = task["completed_at"]
                active = [t for t in active if not (
                    t.get("title") == task["title"] and
                    t.get("recurrence") == task.get("recurrence") and
                    not t.get("completed_at") and
                    t.get("created_at", "") >= completed_at
                )]
            task["completed_at"] = None
            task["wont_do"] = False
            completed_list = [t for t in completed_list if t["id"] != task_id]
            active.append(task)
    elif in_completed:
        # Non-completion update on a completed task — save in place
        pass  # task is already mutated in completed_list

    _write_both(active, completed_list)
    return jsonify(task)


@bp.delete("/api/deck/<task_id>")
@_locked
def api_delete_deck_item(task_id):
    active = _read_deck_strict()
    completed_list = _read_completed_strict()
    _write_both(
        [t for t in active if t["id"] != task_id],
        [t for t in completed_list if t["id"] != task_id],
    )
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
    _write_both(
        [t for t in active if t["id"] not in id_set],
        [t for t in completed_list if t["id"] not in id_set],
    )
    return "", 204


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
        for task in moving:
            task["completed_at"] = now
            spawned = _spawn_recurring(task)
            if spawned:
                active.append(spawned)
            updated.append(task)
        active = [t for t in active if t["id"] not in id_set or t.get("completed_at") is None and False]
        # Rebuild: keep active tasks not being moved
        active = [t for t in active if t["id"] not in {t2["id"] for t2 in moving}]
        completed_list = completed_list + moving
    else:
        moving = [t for t in completed_list if t["id"] in id_set and t.get("completed_at")]
        for task in moving:
            if task.get("recurrence"):
                completed_at = task["completed_at"]
                active = [t for t in active if not (
                    t["id"] not in id_set and
                    t.get("title") == task["title"] and
                    t.get("recurrence") == task.get("recurrence") and
                    not t.get("completed_at") and
                    t.get("created_at", "") >= completed_at
                )]
            task["completed_at"] = None
            updated.append(task)
        completed_list = [t for t in completed_list if t["id"] not in {t2["id"] for t2 in moving}]
        active = active + moving

    _write_both(active, completed_list)
    return jsonify(updated)


@bp.post("/api/deck/reorder")
@_locked
def api_reorder_deck():
    data = request.get_json(force=True)
    order = data.get("order", [])
    if not order:
        abort(400)
    active = _read_deck_strict()
    id_map = {t["id"]: t for t in active}
    for i, task_id in enumerate(order):
        if task_id in id_map:
            id_map[task_id]["sort_order"] = i
    _write_deck_no_commit(active)
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
    _write_both(active, completed_list)
    return jsonify(updated)
