"""Deck — task management module for Leaf wiki.

Provides:
- Task CRUD operations and storage, in the _deck/ folder: tasks.json (open),
  completed.json (completed this year), completed-YYYY.json (earlier years)
- [[deck]] embed HTML generation
- Task backlink resolution
- API route registration via Blueprint"""

import json
import re
from datetime import date, datetime

from flask import Blueprint, Response, abort, jsonify, request

from mdtext import toggle_nth_checkbox
from pages import deck_home, deck_lists
from templating import render
from decksyntax import (
    clean_list, clean_tags, DECK_RE, embed_view, in_scope, is_true, parse_deck_params,
    tag_param_matches,
)
from deckstore import (
    clean_comments, clean_history, COMMENT_MAX, complete_task, completed_in_year,
    completed_years, DeckCorrupt, keep_done, serialized, log_event, new_comment_id, new_task, timestamp,
    read_completed_all_strict, read_deck, read_deck_strict, sanitize_snapshot,
    spawn_recurring, uncomplete_task, write_both, write_deck,
)


bp = Blueprint("deck", __name__)


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


def _comment_text(val) -> str:
    text = _as_str(val, "text").strip()
    if not text:
        abort(400, description="Comment cannot be empty")
    return text[:COMMENT_MAX]


_DECK_PLACEHOLDER_BASE = "LEAFDECKPLACEHOLDER"


def _scope_arg() -> str | None:
    """The list a completed-count/-years request is for (?list=<name>), or
    None for every task."""
    return clean_list(request.args.get("list")) or None


def build_deck_embed(params: dict, instance_id: int = 0, headless: bool = False) -> str:
    """Render the full interactive deck app HTML for embedding in a wiki page.

    instance_id differentiates multiple embeds on the same page. `headless`
    hides the app and keeps only what its edit modal needs (the editor's
    Make task opens it, window.openDeckEdit); deck.js then skips the deck's
    keyboard shortcuts.
    """
    # `limit=N` turns on the WIP limit (Focus warning, Plan day flags); without
    # it there is none.
    try:
        limit = max(1, min(99, int(params.get("limit", ""))))
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
    # Every declared list: their tags are marked as lists (deck.js), each
    # one's home page title is the list tag's tooltip.
    lists = deck_lists()
    return render(
        "deck_embed.html", tag=params.get("tag", ""), list=clean_list(params.get("list")) or "",
        view=embed_view(params), limit=limit, labels=tab_labels, instance_id=instance_id,
        # Ids get a suffix after a page's first embed, so a page can hold several.
        sfx=f"-{instance_id}" if instance_id > 0 else "", headless=headless,
        # `keep=1`: new tasks in this embed are keep tasks (a shopping list of
        # staples); the edit modal's checkbox still turns it off per task.
        keep="1" if is_true(params.get("keep")) else "",
        # `compact=1`: one line per task until the viewer switches (deck.js).
        compact="1" if is_true(params.get("compact")) else "",
        # `home=1`: this embed is the user's own deck (pages.deck_home); the
        # module bar's New clicks this embed's "+ New".
        home=is_true(params.get("home")),
        lists_json=json.dumps(sorted(lists)),
        homes_json=json.dumps({n: l.get("title") or l.get("slug") or "" for n, l in sorted(lists.items())}))


def process_deck_embeds(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Replace all [[deck ...]] directives with placeholders.

    Returns (modified_text, [(placeholder, embed html), …]).
    Supports multiple embeds per page.
    """
    embeds = []
    instance = [0]

    def replacer(m: re.Match) -> str:
        params = parse_deck_params(m.group(1) or "")
        embed_html = build_deck_embed(params, instance_id=instance[0])
        placeholder = f"{_DECK_PLACEHOLDER_BASE}_{instance[0]}"
        embeds.append((placeholder, embed_html))
        instance[0] += 1
        return placeholder

    text = DECK_RE.sub(replacer, text)
    return text, embeds


def inject_deck_embeds(html_str: str, embeds: list[tuple[str, str]]) -> str:
    """Replace deck placeholders in rendered HTML with actual embed HTML.
    Last first: placeholder _1 is a prefix of _10, so in order the 11th
    embed's placeholder would get the second embed's HTML."""
    for placeholder, embed_html in reversed(embeds):
        html_str = html_str.replace(f'<p>{placeholder}</p>', embed_html)
        html_str = html_str.replace(placeholder, embed_html)
    return html_str


@bp.errorhandler(DeckCorrupt)
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
    return jsonify(read_deck())


def _year_arg() -> str:
    """?year=YYYY (a year of the Completed view), default this year."""
    year = request.args.get("year") or str(date.today().year)
    if not re.fullmatch(r"\d{4}", year):
        abort(400, description="year must be YYYY")
    return year


@bp.get("/api/deck/headless")
def api_headless():
    """A hidden deck app, for a page without one that wants its edit modal
    (js/actions.js openHomeTask: c t, the New task button), which starts it
    with deck.js's initDecks."""
    return Response(build_deck_embed({}, headless=True), mimetype="text/html")


@bp.get("/api/deck/completed")
def api_get_deck_completed():
    """Completed tasks of ?year= (default this year; earlier years come from
    their archive file)."""
    return jsonify(completed_in_year(_year_arg()))


@bp.get("/api/deck/completed-years")
def api_deck_completed_years():
    """Years the Completed view can show, of list ?list= or of every task."""
    return jsonify({"years": completed_years(_scope_arg())})


@bp.get("/api/deck/completed-count")
def api_deck_completed_count():
    """How many tasks the Completed view would list (for its link's label):
    this year's, of list ?list=, else of every task."""
    scope = _scope_arg()
    tasks = completed_in_year(str(date.today().year))
    return jsonify({"count": sum(in_scope(t, scope) for t in tasks)})


def deck_counts() -> dict:
    """What the Deck module links show: the open tasks in Focus (DeckCore.
    isFocus: started, or due today or earlier) and, of those, how many are
    overdue or due today. Of the deck's home view (its `list=` and `tag=`)
    when a page declares one, else of every task."""
    today = date.today().isoformat()
    home = deck_home()
    params = home["params"] if home else {}
    scope, tag = clean_list(params.get("list")) or None, params.get("tag", "")
    overdue = today_count = focus = 0
    for t in read_deck():
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
def api_create_deck_item():
    return jsonify(create_task(_json_object())), 201


@serialized
def create_task(data: dict, message: str = "Create deck task") -> dict:
    """A new open task from the fields in data (as the API takes them).
    Invalid fields abort with 400, which the MCP tools turn into errors."""
    if not _as_str(data.get("title", ""), "title").strip():
        abort(400, description="Title cannot be empty")
    task = new_task()
    _apply_task_fields(task, data)
    task["history"] = []          # its first dates are not changes
    log_event(task, "created")
    active = read_deck_strict()
    active.append(task)
    write_deck(active, message)
    return task


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
        task["tags"] = clean_tags(_as_str_list(data["tags"] or [], "tags"))
    if "comments" in data:     # the whole list, for undoing a comment's delete or edit
        if not isinstance(data["comments"], list):
            abort(400, description="comments must be a list")
        task["comments"] = clean_comments(data["comments"])
    if "due_date" in data:
        task["due_date"] = _validate_date(data["due_date"])
        if task["due_date"] != old_due_date:
            log_event(task, "due", old_due_date, task["due_date"])
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
            log_event(task, "start", old_start_date, task["start_date"])
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
            # (spawn_recurring), while neither date changed.
            if (old_rec.get("lead") is not None and task.get("due_date") == old_due_date
                    and task.get("start_date") == old_start_date):
                task["recurrence"]["lead"] = old_rec["lead"]
        else:
            task["recurrence"] = None
    # A date changed by hand defines the gap from now on: forget a remembered
    # one (see spawn_recurring).
    rec = task.get("recurrence")
    if rec and "lead" in rec and (task.get("due_date") != old_due_date
                                  or task.get("start_date") != old_start_date):
        del rec["lead"]
    # Keep and Repeat are two answers to "what happens after done?": a keep
    # task never repeats.
    if task.get("keep"):
        task["recurrence"] = None


@bp.put("/api/deck/<task_id>")
def api_update_deck_item(task_id):
    return jsonify(update_task(task_id, _json_object()))


@serialized
def update_task(task_id: str, data: dict, suffix: str = "") -> dict:
    """Change a task's fields and status (complete, won't do, skip, reopen)
    from data as the API takes it; returns the task. `suffix` goes after the
    commit message (the MCP tools' summary)."""
    active, completed_list, task = _find_for_write(task_id)
    _apply_task_fields(task, data)

    did_skip = bool(data.get("skip") and not task.get("completed_at"))
    if did_skip:
        # Skip this occurrence: archive it as won't-do but DO spawn the next
        # instance — the series continues, unlike a plain won't-do which ends
        # it. On a non-recurring task this degrades to a plain won't-do.
        log_event(task, "skipped")      # before the spawn, which carries the history on
        spawned = spawn_recurring(task)
        task["completed_at"] = timestamp()
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
            task["completed_at"] = timestamp()
            task["wont_do"] = True
            log_event(task, "wontdo")
            active = [t for t in active if t["id"] != task_id]
            completed_list.append(task)
        elif not data["wont_do"] and task.get("completed_at"):
            # Reopen a won't-do task: move completed → active. uncomplete_task
            # also removes the instance a skip spawned (exact spawned_from
            # match), so undoing a skip does not leave a duplicate.
            active = uncomplete_task(task, active)
            completed_list = [t for t in completed_list if t["id"] != task_id]
            active.append(task)
    elif "completed" in data:
        if data["completed"] and task.get("keep") and not task.get("completed_at"):
            keep_done(task)   # stays open, back in the pool
        elif data["completed"] and not task.get("completed_at"):
            # Complete: move active → completed
            spawned = complete_task(task)
            active = [t for t in active if t["id"] != task_id]
            completed_list.append(task)
            if spawned:
                active.append(spawned)
        elif not data["completed"] and task.get("completed_at"):
            # Uncomplete: move completed → active
            active = uncomplete_task(task, active)
            completed_list = [t for t in completed_list if t["id"] != task_id]
            active.append(task)

    # Undo sends the task's old history back with its old fields: it replaces
    # whatever this request logged, so an undone change leaves no entry.
    if "history" in data:
        task["history"] = clean_history(data["history"])

    if did_skip:
        msg = "Skip deck task"
    elif "wont_do" in data:
        msg = "Reopen deck task" if not data["wont_do"] else "Abandon deck task"
    elif "completed" in data:
        msg = "Uncomplete deck task" if not data["completed"] else "Complete deck task"
    else:
        msg = "Update deck task"
    write_both(active, completed_list, msg + suffix)
    return task


def _find_for_write(task_id: str) -> tuple[list[dict], list[dict], dict]:
    """(open tasks, every completed task, the task with that id) for a
    write; 404 when no task has it."""
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    task = next((t for t in active + completed_list if t["id"] == task_id), None)
    if not task:
        abort(404)
    return active, completed_list, task


@bp.post("/api/deck/<task_id>/toggle-check")
def api_toggle_note_check(task_id):
    """Tick or untick the Nth checkbox in a task's notes (0-based, in the
    order render_notes renders them); ticking records when, as on a page
    (mdtext.toggle_nth_checkbox)."""
    idx = _as_int(_json_object().get("idx"), "idx")
    if idx < 0:
        abort(400, description="idx must not be negative")
    return jsonify(toggle_note_check(task_id, idx))


@serialized
def toggle_note_check(task_id: str, idx: int) -> dict:
    _, _, task = _find_for_write(task_id)
    text = task.get("description") or ""
    toggled = toggle_nth_checkbox(text, idx, frontmatter=False)
    if toggled == text:
        return task
    return update_task(task_id, {"description": toggled})


@bp.post("/api/deck/<task_id>/comments")
def api_add_comment(task_id):
    """Add a comment (a status line) to a task, open or done; returns the task."""
    return jsonify(add_comment(task_id, _json_object().get("text"))), 201


@serialized
def add_comment(task_id: str, text, message: str = "Comment on deck task") -> dict:
    text = _comment_text(text)
    active, completed_list, task = _find_for_write(task_id)
    task["comments"] = clean_comments(task.get("comments")) + [{
        "id": new_comment_id(), "at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "text": text}]
    write_both(active, completed_list, message)
    return task


def _task_comment(task: dict, comment_id: str) -> dict:
    task["comments"] = clean_comments(task.get("comments"))
    comment = next((c for c in task["comments"] if c["id"] == comment_id), None)
    if not comment:
        abort(404)
    return comment


@bp.put("/api/deck/<task_id>/comments/<comment_id>")
@serialized
def api_edit_comment(task_id, comment_id):
    """Change a comment's text; its time stays. Returns the task."""
    text = _comment_text(_json_object().get("text"))
    active, completed_list, task = _find_for_write(task_id)
    _task_comment(task, comment_id)["text"] = text
    write_both(active, completed_list, "Edit deck task comment")
    return jsonify(task)


@bp.delete("/api/deck/<task_id>/comments/<comment_id>")
@serialized
def api_delete_comment(task_id, comment_id):
    """Delete a comment; returns the task."""
    active, completed_list, task = _find_for_write(task_id)
    comment = _task_comment(task, comment_id)
    task["comments"] = [c for c in task["comments"] if c is not comment]
    write_both(active, completed_list, "Delete deck task comment")
    return jsonify(task)


@bp.delete("/api/deck/<task_id>")
def api_delete_deck_item(task_id):
    delete_task(task_id)
    return "", 204


@serialized
def delete_task(task_id: str, message: str = "Delete deck task") -> None:
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    active = [t for t in active if t["id"] != task_id]
    completed_list = [t for t in completed_list if t["id"] != task_id]
    write_both(active, completed_list, message)


@bp.post("/api/deck/batch-delete")
@serialized
def api_batch_delete_deck():
    data = _json_object()
    id_set = set(_id_list(data))
    if not id_set:
        abort(400)
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    active = [t for t in active if t["id"] not in id_set]
    completed_list = [t for t in completed_list if t["id"] not in id_set]
    write_both(active, completed_list, "Delete deck tasks")
    return "", 204


@bp.post("/api/deck/restore")
@serialized
def api_restore_deck_items():
    """Re-insert full task snapshots (undo after delete). Snapshots keep their
    original id/created_at/etc.; ids that already exist are skipped."""
    data = _json_object()
    snaps = data.get("tasks", [])
    if not snaps or not isinstance(snaps, list):
        abort(400)
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    existing = {t["id"] for t in active} | {t["id"] for t in completed_list}
    restored = []
    for snap in snaps:
        if not isinstance(snap, dict):
            continue
        task = sanitize_snapshot(snap)
        if task is None or task["id"] in existing:
            continue
        existing.add(task["id"])
        (completed_list if task["completed_at"] else active).append(task)
        restored.append(task)
    if restored:
        write_both(active, completed_list, "Restore deck tasks")
    return jsonify(restored), 201


@bp.post("/api/deck/batch-complete")
@serialized
def api_batch_complete_deck():
    data = _json_object()
    id_set = set(_id_list(data))
    mark_completed = data.get("completed", True)
    if not id_set:
        abort(400)
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    now = timestamp()
    updated = []

    if mark_completed:
        for task in active:
            if task["id"] in id_set and task.get("keep") and not task.get("completed_at"):
                keep_done(task)   # stays open, back in the pool
                updated.append(task)
        moving = [t for t in active if t["id"] in id_set
                  and not t.get("completed_at") and not t.get("keep")]
        moving_ids = {t["id"] for t in moving}
        spawned_tasks = []
        for task in moving:
            spawned = complete_task(task, now)
            if spawned:
                spawned_tasks.append(spawned)
            updated.append(task)
        active = [t for t in active if t["id"] not in moving_ids] + spawned_tasks
        completed_list = completed_list + moving
    else:
        moving = [t for t in completed_list if t["id"] in id_set and t.get("completed_at")]
        moving_ids = {t["id"] for t in moving}
        for task in moving:
            active = uncomplete_task(task, active, protect_ids=id_set)
            updated.append(task)
        completed_list = [t for t in completed_list if t["id"] not in moving_ids]
        active = active + moving

    write_both(active, completed_list, "Complete deck tasks" if mark_completed else "Reopen deck tasks")
    return jsonify(updated)


@bp.post("/api/deck/reorder")
@serialized
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
    active = read_deck_strict()
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
    write_deck(active, "Reorder deck")
    return "", 204


@bp.post("/api/deck/batch-tag")
def api_batch_tag_deck():
    """Add or remove tags from multiple deck items."""
    data = _json_object()
    ids = _id_list(data)
    if not isinstance(data.get("add", []), list) or not isinstance(data.get("remove", []), list):
        abort(400, description="add/remove must be lists of strings")
    return jsonify(tag_tasks(ids, _as_str_list(data.get("add", []), "add"),
                             _as_str_list(data.get("remove", []), "remove")))


@serialized
def tag_tasks(ids: list[str], add: list[str], remove: list[str],
              message: str = "Tag deck tasks") -> list[dict]:
    """Add the tags `add` to the tasks `ids` and take `remove` off them (case
    ignored; a tag a task has keeps its spelling), open and completed alike,
    in one commit; returns the tasks found. The batch-tag route and the MCP
    tag_tasks tool share it."""
    id_set = set(ids)
    add_tags = clean_tags(add)
    remove_tags = {t.lower() for t in clean_tags(remove)}
    if not id_set or not (add_tags or remove_tags):
        abort(400, description="Give task ids and tags to add or remove")
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    updated = []
    for task in active + completed_list:
        if task["id"] not in id_set:
            continue
        current = task.get("tags") or []
        existing_lower = {t.lower() for t in current}
        for tag in add_tags:
            if tag.lower() not in existing_lower:
                current.append(tag)
                existing_lower.add(tag.lower())
        task["tags"] = [t for t in current if t.lower() not in remove_tags]
        updated.append(task)
    write_both(active, completed_list, message)
    return updated


# What batch-update may not do: it changes fields, while completing, won't
# do and skip move tasks between files (batch-complete, PUT).
_STATUS_KEYS = ("completed", "wont_do", "skip")


@bp.post("/api/deck/batch-update")
@serialized
def api_batch_update_deck():
    """Change fields of several tasks in one commit: `tasks` is a list of
    {id, ...fields as PUT takes them}, each task its own fields (start today
    for a selection, or an undo putting back each task's old tags or dates
    with its old history). Ids no longer in the deck are skipped."""
    data = _json_object()
    changes = data.get("tasks")
    if (not isinstance(changes, list) or not changes
            or not all(isinstance(c, dict) and isinstance(c.get("id"), str) for c in changes)):
        abort(400, description="tasks must be a list of objects with an id")
    if any(k in c for c in changes for k in _STATUS_KEYS):
        abort(400, description="batch-update changes fields, not status")
    active = read_deck_strict()
    completed_list = read_completed_all_strict()
    by_id = {t["id"]: t for t in active + completed_list}
    updated = []
    for change in changes:
        task = by_id.get(change["id"])
        if not task:
            continue
        _apply_task_fields(task, change)
        if "history" in change:
            task["history"] = clean_history(change["history"])
        updated.append(task)
    if updated:
        write_both(active, completed_list, "Update deck tasks")
    return jsonify(updated)
