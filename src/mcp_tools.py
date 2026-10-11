"""The MCP tools: reading (search_wiki, search_tasks, grep_wiki, read_page,
list_pages, check_links, leaf_help) and writing pages (write_page, edit_page, replace_in_pages,
append_to_page, rename_page, delete_page) and deck tasks (create_task, update_task,
comment_task, delete_task). The protocol itself is in mcp_server.py, the
endpoint in server.py."""

import re
from datetime import date
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

from flask import request
from werkzeug.exceptions import HTTPException

import config
import deck
import decksyntax
import deckstore
import manual
import pages
import mcp_server
import retrieval
import search
from gitstore import (git_commit, git_diff, git_log, git_log_all, git_show, git_uncommitted_files,
                      grep_pages, GrepError, is_uncommitted, move_page, SHA_RE,
                      version_index, version_path)
from mdtext import (_INLINE_CODE_RE, is_scratch, iter_fenced_lines, mask_code, page_tags, page_title,
                    plain_heading, split_wikilink, to_slug, WIKILINK_RE)
from pages import (all_backlink_counts, all_slugs, deck_home_slug, find_backlinks, fmt_date, is_protected,
                   iter_pages, page_link_targets, page_meta, safe_path, url)
from render import task_link, TOC_RE
from retrieval import in_folder, passes, retrieve, task_status


def _clean_folder(raw) -> str | None:
    """A folder argument as a slug prefix: '/page/tickets/', 'tickets/' and
    'tickets' all become 'tickets'. Empty means the whole wiki."""
    if not isinstance(raw, str):
        raise mcp_server.ToolError("under must be a string")
    raw = raw.strip()
    if "/page/" in raw:
        raw = raw.split("/page/", 1)[1]
    return raw.strip("/") or None


def _mcp_int(args: dict, name: str, default: int, top: int) -> int:
    value = args.get(name, default)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise mcp_server.ToolError(f"{name} must be a whole number")
    return max(1, min(value, top))


def _mcp_page_slug(raw: str) -> str | None:
    """The existing page a slug-ish string names: a slug, '/page/<slug>', a
    full URL, '<slug>.md', or the page's name ('Git Remote')."""
    raw = raw.strip()
    if "/page/" in raw:
        raw = raw.split("/page/", 1)[1]
    raw = raw.split("#", 1)[0].strip("/")
    if raw.endswith(".md"):
        raw = raw[:-3]
    for slug in (raw, to_slug(raw)):
        path = safe_path(slug) if slug else None
        if path is not None and path.is_file():
            return slug
    return None


def _host_file(slug: str) -> str:
    """A page's file as the host sees it, so a client on the host can open
    or edit it: the host's wiki folder (LEAF_HOST_DATA) plus the slug, which
    is the file's path relative to the wiki."""
    root = config.LEAF_HOST_DATA or str(config.LEAF_DATA.resolve())
    return f"{root}/{slug}.md"


_RECURRENCE_UNITS ={"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}


def _mcp_task_text(task: dict, base: str) -> str:
    """One deck task for an LLM: title, a line of facts (status, tags, dates,
    repeat, list), its link in the deck, its notes and its comments."""
    status = task_status(task)
    facts = [f"status: {status}" + (f" {task['completed_at'][:10]}" if status != "open" else "")]
    if task.get("tags"):
        facts.append("tags: " + " ".join(f"#{t}" for t in task["tags"]))
    if task.get("start_date"):
        facts.append(f"start: {task['start_date']}")
    if task.get("due_date"):
        facts.append(f"due: {task['due_date']}")
    rec = task.get("recurrence")
    if isinstance(rec, dict) and rec.get("type") in _RECURRENCE_UNITS:
        n = rec.get("interval") or 1
        every = rec["type"] if n == 1 else f"every {n} {_RECURRENCE_UNITS[rec['type']]}s"
        facts.append(f"repeats {every}" + (" after completion" if rec.get("from") == "completion" else ""))
    if task.get("keep"):
        facts.append("keep task (back to the list when done"
                     + (f", last done {task['last_done']})" if task.get("last_done") else ")"))
    list_name = pages.task_list(task)
    if list_name:
        facts.append(f"list: {list_name}")
    url = base + task_link(task, list_name)
    short = decksyntax.short_id(task["id"])
    text = f"Task: {task.get('title', '')}\n{' · '.join(facts)}\nid: {short}\nurl: {url}"
    notes = (task.get("description") or "").strip()
    if notes:
        text += f"\n\n{notes}"
    # Comments: the user's status lines, oldest first, each with its time.
    comments = [c for c in task.get("comments") or [] if isinstance(c, dict) and c.get("text")]
    if comments:
        text += "\n\nComments:\n" + "\n".join(
            f"- {str(c.get('at', ''))[:16].replace('T', ' ')}: {c['text']}" for c in comments)
    return text


def _mcp_task_refs(content: str, base: str) -> str:
    """For read_page: what each [[task:…]] on the page stands for, since the
    raw markdown holds only the id."""
    masked, _ = mask_code(content)
    refs = list(dict.fromkeys(m.group(1).lower() for m in decksyntax.TASK_REF_RE.finditer(masked)))
    if not refs:
        return ""
    tasks = deckstore.resolve_task_refs(refs)
    lines = []
    for ref in refs:
        task = tasks[ref]
        if task is None:
            lines.append(f"- [[task:{ref}]]: no such task (deleted)")
            continue
        facts = [task_status(task)]
        facts += [f"{k} {task[k + '_date']}" for k in ("start", "due") if task.get(k + "_date")]
        url = base + task_link(task, pages.task_list(task))
        lines.append(f"- [[task:{ref}]]: {task.get('title', '')} ({', '.join(facts)}) {url}")
    return "\n\n---\nDeck tasks referenced on this page ([[task:…]]):\n" + "\n".join(lines)


def _mcp_search_tasks(args: dict, base: str) -> str:
    """Deck tasks filtered by tag and status, ranked by the query when there
    is one, else open ones by deadline and start date, done ones newest
    first."""
    query, tag = (args.get(k, "") for k in ("query", "tag"))
    if not all(isinstance(v, str) for v in (query, tag)):
        raise mcp_server.ToolError("query and tag must be strings")
    status = args.get("status", "open")
    if status not in ("open", "done", "all"):
        raise mcp_server.ToolError("status must be open, done or all")
    limit = _mcp_int(args, "limit", 50, 200)
    want = {t.strip().lstrip("#").lower() for t in tag.split(",") if t.strip().lstrip("#")}

    def matches(task: dict) -> bool:
        if status != "all" and (status == "open") == bool(task.get("completed_at")):
            return False
        return want <= {t.lower() for t in task.get("tags") or []}

    parsed = search.Query(query)
    docs = [d for d in deckstore.task_search_docs()
            if matches(d["task"]) and passes(parsed, d)]
    if parsed.text.strip():
        ranked = [d for _s, d in search.SearchIndex(docs).search(parsed.text, limit=max(len(docs), 1))]
        if retrieval.SEMANTIC:
            retrieval.SEMANTIC.poke()
            by_id = {d["task_id"]: d for d in docs}
            hits = [by_id[i] for i, _sc in retrieval.SEMANTIC.search_tasks(parsed.text, known=by_id, limit=len(by_id) or 1)]
            ranked = search.fuse([ranked, hits])
        docs = ranked
    else:
        open_ = sorted((d for d in docs if not d["task"].get("completed_at")),
                       key=lambda d: (d["task"].get("due_date") or "9999",
                                      d["task"].get("start_date") or "9999",
                                      d["task"].get("title", "").lower()))
        done = sorted((d for d in docs if d["task"].get("completed_at")),
                      key=lambda d: d["task"]["completed_at"], reverse=True)
        docs = open_ + done
    if not docs:
        return f"Today is {date.today()}. No matching tasks."
    shown = docs[:limit]
    count = f"{len(docs)} matching tasks" + (f", the first {limit} shown" if len(docs) > limit else "")
    body = "\n\n---\n\n".join(_mcp_task_text(d["task"], base) for d in shown)
    return f"Today is {date.today()}. {count}:\n\n{body}"


# ── Writing ──────────────────────────────────────────────────────────────────
# Every write is its own git commit, its message ending in "(MCP)" so the
# history tells the model's changes from the user's. The model's `summary`,
# when given, says what changed.

def _str_arg(args: dict, name: str, required: bool = True) -> str:
    """A string argument with CRLF line ends as LF; required ones non-empty."""
    value = args.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise mcp_server.ToolError(f"{name} must be a string")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if required and not value.strip():
        raise mcp_server.ToolError(f"{name} must be a non-empty string")
    return value


def _msg_suffix(args: dict) -> str:
    """What follows a write's commit message: the model's summary, then
    "(MCP)"."""
    summary = " ".join(_str_arg(args, "summary", required=False).split())[:200]
    return f": {summary} (MCP)" if summary else " (MCP)"


def _commit_msg(action: str, args: dict) -> str:
    return action + _msg_suffix(args)


def _save_pending(path: Path, slug: str) -> str:
    """Before a write: commit what the page holds that isn't committed yet
    (the browser editor auto-saves without committing; or edits on disk), as
    the user's own "Save <slug>". Else that text would end up in the model's
    commit, or be lost to an overwrite or a delete. Returns a note for the
    model: the user may have the page open."""
    if not path.exists() or not is_uncommitted(path):
        return ""
    git_commit(path, f"Save {slug}")
    return (f"\nNote: {slug} had changes that weren't committed yet (the user may be editing it "
            f"in the browser); they were committed first, as \"Save {slug}\", and kept.")


def _save_pending_pages(paths: list[Path], what: str) -> str:
    """_save_pending for a write to many pages: those with uncommitted
    changes are committed first, together, as the user's "Save <what>"."""
    pending = {config.LEAF_DATA / f["filepath"] for f in git_uncommitted_files()}
    paths = [p for p in paths if p in pending]
    if not paths:
        return ""
    git_commit(paths, f"Save {what}")
    return (f"\nNote: {len(paths)} of the pages had changes that weren't committed yet (the user may be "
            f"editing them in the browser); they were committed first, as \"Save {what}\", and kept.")


def _existing_page(args: dict, name: str = "slug") -> tuple[str, Path]:
    raw = _str_arg(args, name)
    slug = _mcp_page_slug(raw)
    if slug is None:
        raise mcp_server.ToolError(f"No page {raw!r}. Use search_wiki or list_pages to find its slug.")
    return slug, safe_path(slug)


def _new_slug(raw: str) -> str:
    """The slug a new page gets from a name or slug ('Tickets/BUG 1234' →
    'tickets/bug-1234'), refused when it would be outside the wiki or in a
    folder Leaf skips (_assets, _deck, .git …)."""
    raw = raw.strip()
    if "/page/" in raw:
        raw = raw.split("/page/", 1)[1]
    raw = raw.split("#", 1)[0].strip("/")
    if raw.endswith(".md"):
        raw = raw[:-3]
    slug = to_slug(raw)
    if not slug or "" in slug.split("/"):
        raise mcp_server.ToolError(f"{raw!r} is not a usable page name")
    if safe_path(slug) is None:
        raise mcp_server.ToolError(f"{slug!r}: pages can't go in folders starting with '_' or '.'")
    return slug


def _page_info(slug: str, base: str) -> str:
    return f"url: {base}{url('page', slug)}\nfile: {_host_file(slug)}"


def _missing_links(content: str, slugs: set[str]) -> set[str]:
    """The [[wikilinks]] in content whose page doesn't exist (as rendering
    decides it: the name as a slug, else slugified)."""
    masked, _ = mask_code(content)
    for pattern in (TOC_RE, decksyntax.DECK_RE, decksyntax.TASK_REF_RE):
        masked = pattern.sub("", masked)
    missing = set()
    for m in WIKILINK_RE.finditer(masked):
        name = split_wikilink(m.group(1))[0]
        if name and name not in slugs and to_slug(name) not in slugs:
            missing.add(name)
    return missing


def _link_warning(old: str, new: str) -> str:
    """A note on the wikilinks the write added that lead nowhere, so the
    model can fix a typo or create the page."""
    slugs = set(all_slugs())
    added = _missing_links(new, slugs) - _missing_links(old, slugs)
    if not added:
        return ""
    return ("\nNote: these new wikilinks point to pages that don't exist (yet): "
            + ", ".join(f"[[{n}]]" for n in sorted(added)))


def _norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _similar_pages(slug: str, title: str) -> list[tuple[str, str]]:
    """Existing pages whose title or file name is close to a new page's, so
    the model notices it may be extending a page rather than starting one."""
    want = {n for n in (_norm_title(title), _norm_title(slug.rsplit("/", 1)[-1])) if n}
    found = []
    for md_file, other in iter_pages():
        if other == slug:
            continue
        other_title = page_meta(md_file, other)[0]
        have = {n for n in (_norm_title(other_title), _norm_title(other.rsplit("/", 1)[-1])) if n}
        if any(SequenceMatcher(None, a, b).ratio() >= 0.8 for a in want for b in have):
            found.append((other, other_title))
    return sorted(found)[:5]


def _close_in_content(slug: str, content: str, named: set[str]) -> str:
    """For a new page (semantic search only): the existing pages closest to
    it in meaning, which a similar name doesn't catch (a crash analysis
    written up anew when the ticket's page already covers it). Pages it
    links to, or already named for a similar name, are left out."""
    if not retrieval.SEMANTIC:
        return ""
    slugs = set(all_slugs())
    linked = page_link_targets(content, slug, slugs)
    close = retrieval.SEMANTIC.related_to_text(slug, content, known=slugs - {slug},
                                               exclude=linked | named, limit=3)
    if not close:
        return ""
    return ("\nNote: the pages closest to it in content: " + ", ".join(s for s, _ in close)
            + ". If one already covers this topic, merge this page there (edit_page) and "
            "delete_page this one; else consider linking them.")


def _write_page(args: dict, base: str) -> str:
    content = _str_arg(args, "content")
    overwrite = args.get("overwrite", False) is True
    existing = _mcp_page_slug(_str_arg(args, "slug"))
    if existing and not overwrite:
        raise mcp_server.ToolError(
            f"Page {existing!r} already exists. Change it with edit_page or append_to_page, "
            "or set overwrite=true to replace all of it.")
    slug = existing or _new_slug(args["slug"])
    path = safe_path(slug)
    pending = _save_pending(path, slug) if existing else ""
    old = path.read_text(encoding="utf-8") if existing else ""
    content = content.rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, _commit_msg(f"Update {slug}" if existing else f"Create {slug}", args))
    out = f"{'Replaced' if existing else 'Created'} page {slug}.\n{_page_info(slug, base)}"
    if not existing:
        similar = _similar_pages(slug, page_title(content, slug))
        if similar:
            out += ("\nNote: pages with similar names exist; if this one repeats one of them, "
                    "merge it there (edit_page) and delete_page this one:\n"
                    + "\n".join(f"- {s} | {t}" for s, t in similar))
        out += _close_in_content(slug, content, {s for s, _t in similar})
    return out + pending + _link_warning(old, content)


def _apply_edit(content: str, slug: str, edit: dict, which: str = "") -> tuple[str, int]:
    """content with one edit applied (old_string → new_string, replace_all),
    and how many places it changed. `which` names the edit in errors."""
    old_string = _str_arg(edit, "old_string")
    new_string = _str_arg(edit, "new_string", required=False)
    if old_string == new_string:
        raise mcp_server.ToolError(f"{which}old_string and new_string are the same: nothing to change")
    count = content.count(old_string)
    if count == 0:
        raise mcp_server.ToolError(
            f"{which}old_string was not found in {slug}. It must match the page exactly, line breaks "
            "and whitespace included; read_page shows the current text.")
    if count > 1 and edit.get("replace_all", False) is not True:
        raise mcp_server.ToolError(
            f"{which}old_string occurs {count} times in {slug}. Include more of the surrounding text "
            "to make it unique, or set replace_all=true to change every one.")
    return content.replace(old_string, new_string), count


def _edit_page(args: dict, base: str) -> str:
    slug, path = _existing_page(args)
    edits = args.get("edits")
    if edits is not None:
        if not isinstance(edits, list) or not edits or not all(isinstance(e, dict) for e in edits):
            raise mcp_server.ToolError("edits must be a non-empty list of {old_string, new_string, replace_all}")
        if "old_string" in args:
            raise mcp_server.ToolError("give either old_string/new_string or edits, not both")
    pending = _save_pending(path, slug)
    content = path.read_text(encoding="utf-8")
    new, counts = content, []
    # All or nothing: each edit applies to the text the ones before it left,
    # and the page is written only when every one of them matched.
    for i, edit in enumerate(edits or [args], 1):
        new, count = _apply_edit(new, slug, edit, f"Edit {i}: " if edits else "")
        counts.append(count)
    path.write_text(new, encoding="utf-8")
    git_commit(path, _commit_msg(f"Update {slug}", args))
    total = sum(counts)
    what = f"{total} occurrences" if total > 1 else "1 occurrence"
    done = f"applied {len(counts)} edits, {what}" if edits else f"replaced {what}"
    return f"Edited page {slug}: {done}.\n{_page_info(slug, base)}" + pending + _link_warning(content, new)


_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


def _section_lines(lines: list[str], heading: str) -> tuple[int, int] | None:
    """The lines of the section under `heading` (any level; its text compared
    without markup and case), from the heading to before the next heading of
    the same or a higher level, subsections included. None when the page
    has no such heading."""
    want = plain_heading(heading.lstrip("#").strip()).lower()
    start = level = None
    end = len(lines)
    for i, line, in_fence in iter_fenced_lines(lines):
        m = None if in_fence else _HEADING_RE.match(line)
        if not m:
            continue
        if start is None:
            if plain_heading(m.group(2)).lower() == want:
                start, level = i, len(m.group(1))
        elif len(m.group(1)) <= level:
            end = i
            break
    return None if start is None else (start, end)


def _no_heading(slug: str, content: str, heading: str) -> str:
    """The error for a heading the page doesn't have, naming those it has."""
    headings = [m.group(2) for _i, line, f in iter_fenced_lines(content.splitlines())
                if not f and (m := _HEADING_RE.match(line))]
    return (f"{slug} has no heading {heading!r}. Its headings: "
            + (", ".join(repr(h) for h in headings) or "none") + ".")


def _append_at(content: str, addition: str, heading: str) -> str | None:
    """content with addition at the end of the section under `heading`. None
    when the page has no such heading."""
    lines = content.splitlines(keepends=True)
    found = _section_lines(lines, heading)
    if found is None:
        return None
    end = found[1]
    body = "".join(lines[:end]).rstrip("\n")
    rest = "".join(lines[end:])
    return body + "\n\n" + addition + "\n" + ("\n" + rest if rest else "")


def _append_to_page(args: dict, base: str) -> str:
    slug, path = _existing_page(args)
    addition = _str_arg(args, "content").strip("\n")
    heading = _str_arg(args, "under_heading", required=False).strip()
    pending = _save_pending(path, slug)
    content = path.read_text(encoding="utf-8")
    if heading:
        new = _append_at(content, addition, heading)
        if new is None:
            raise mcp_server.ToolError(
                _no_heading(slug, content, heading)
                + " To start a new section, put its heading in content and leave out under_heading.")
    else:
        new = content.rstrip("\n") + "\n\n" + addition + "\n"
    path.write_text(new, encoding="utf-8")
    git_commit(path, _commit_msg(f"Append to {slug}", args))
    where = f" under {heading!r}" if heading else ""
    return f"Appended to page {slug}{where}.\n{_page_info(slug, base)}" + pending + _link_warning(content, new)


def _rename_page(args: dict, base: str) -> str:
    slug, path = _existing_page(args)
    if is_protected(slug):
        raise mcp_server.ToolError(f"{slug} is the home page; it can't be renamed")
    new_slug = _new_slug(_str_arg(args, "new_slug"))
    if new_slug == slug:
        raise mcp_server.ToolError(f"The page is already called {slug}")
    new_path = safe_path(new_slug)
    if new_path.exists():
        raise mcp_server.ToolError(f"A page {new_slug!r} already exists")
    pending = _save_pending(path, slug)
    changed = move_page(path, new_path, slug, new_slug, _commit_msg(f"Rename {slug} to {new_slug}", args))
    refs = f" Links to it were updated in {len(changed)} files." if changed else ""
    return f"Renamed page {slug} to {new_slug}.{refs}\n{_page_info(new_slug, base)}" + pending


def _delete_page(args: dict, base: str) -> str:
    slug, path = _existing_page(args)
    if is_protected(slug):
        raise mcp_server.ToolError(f"{slug} is the home page; it can't be deleted")
    linking = find_backlinks(slug)
    pending = _save_pending(path, slug)
    path.unlink()
    git_commit(path, _commit_msg(f"Delete {slug}", args))
    out = f"Deleted page {slug}."
    if config.GIT_ENABLED:
        out += " It can be restored from Deleted Pages (git history)."
    if linking:
        out += ("\nThese pages still link to it: "
                + ", ".join(b["slug"] for b in linking))
    return out + pending


_TASK_LINK_RE = re.compile(r"task[:=]([0-9a-f-]{4,36})")


def _task_arg(args: dict) -> dict:
    """The task `id` names: its id or a prefix of it (the 8-character short
    id the tools show), a [[task:…]] reference or a /deck?task=… link."""
    return _find_task(_str_arg(args, "id"))


def _find_task(raw: str) -> dict:
    """The task a reference names (see _task_arg)."""
    raw = raw.strip().lower()
    m = _TASK_LINK_RE.search(raw)
    ref = m.group(1) if m else raw
    if not re.fullmatch(r"[0-9a-f][0-9a-f-]{3,35}", ref):
        raise mcp_server.ToolError(f"{raw!r} is not a task id; search_tasks shows each task's id")
    found = deckstore.find_tasks(ref)
    if not found:
        raise mcp_server.ToolError(f"No task with id {ref!r}. search_tasks shows each task's id.")
    if len(found) > 1:
        raise mcp_server.ToolError(f"Several tasks have ids starting with {ref!r}; give more of the id")
    return found[0]


_REPEATS = ("daily", "weekly", "monthly", "yearly")


def _task_fields(args: dict, task: dict | None = None) -> dict:
    """The deck API's fields from the tools' arguments (only those given)."""
    data = {}
    for arg, field in (("title", "title"), ("notes", "description"), ("due", "due_date"),
                       ("start", "start_date")):
        if arg in args:
            data[field] = _str_arg(args, arg, required=False).strip() or None
    if "title" in data and not data["title"]:
        raise mcp_server.ToolError("title must be a non-empty string")
    if "description" in data:
        data["description"] = data["description"] or ""
    if "keep" in args:
        if not isinstance(args["keep"], bool):
            raise mcp_server.ToolError("keep must be true or false")
        data["keep"] = args["keep"]
    if "tags" in args:
        data["tags"] = args["tags"] if args["tags"] is not None else []
    if task is not None and ("add_tags" in args or "remove_tags" in args):
        tags = list(data.get("tags", task.get("tags") or []))
        lower = {t.lower() for t in tags}
        tags += [t for t in _tag_list(args, "add_tags") if t.lower() not in lower]
        drop = {t.lower() for t in _tag_list(args, "remove_tags")}
        data["tags"] = [t for t in tags if t.lower() not in drop]
    if any(k in args for k in ("repeat", "repeat_every", "repeat_from")):
        rec = (task or {}).get("recurrence") or {}
        repeat = args.get("repeat", rec.get("type") or "none")
        if repeat not in (*_REPEATS, "none"):
            raise mcp_server.ToolError("repeat must be daily, weekly, monthly, yearly or none")
        data["rec_type"] = repeat if repeat != "none" else None
        data["rec_interval"] = args.get("repeat_every", rec.get("interval") or 1)
        data["rec_from"] = args.get("repeat_from", rec.get("from") or "due")
        if data["rec_from"] not in ("due", "completion"):
            raise mcp_server.ToolError("repeat_from must be due or completion")
    return data


def _deck_write(fn, *args):
    """Run one of deck.py's task writes, its 400s (bad date, empty title …)
    as tool errors the model can correct."""
    try:
        return fn(*args)
    except HTTPException as e:
        raise mcp_server.ToolError(e.description or str(e))
    except deckstore.DeckCorrupt:
        raise mcp_server.ToolError("The deck's storage can't be read right now; try again")


def _create_task(args: dict, base: str) -> str:
    _str_arg(args, "title")
    task = _deck_write(deck.create_task, _task_fields(args), _commit_msg("Create deck task", args))
    return "Created.\n\n" + _mcp_task_text(task, base)


_STATUS = {"done": {"completed": True}, "open": {"completed": False},
           "wont_do": {"wont_do": True}, "skip": {"skip": True}}


@deckstore.serialized
def _update_task(args: dict, base: str) -> str:
    task = _task_arg(args)
    data = _task_fields(args, task)
    if "status" in args:
        if args["status"] not in _STATUS:
            raise mcp_server.ToolError("status must be open, done, wont_do or skip")
        data.update(_STATUS[args["status"]])
    if not data:
        raise mcp_server.ToolError("Nothing to change: give the fields to set, or a status")
    task = _deck_write(deck.update_task, task["id"], data, _msg_suffix(args))
    out = "Updated.\n\n" + _mcp_task_text(task, base)
    spawned = [t for t in deckstore.read_deck() if t.get("spawned_from") == task["id"]
               and not t.get("completed_at")]
    if args.get("status") in ("done", "skip") and spawned:
        out += "\n\nNext occurrence:\n\n" + _mcp_task_text(spawned[0], base)
    return out


def _tag_list(args: dict, name: str) -> list[str]:
    """A list-of-tags argument (a comma-separated string works too), each
    without its leading #."""
    val = args.get(name) or []
    if isinstance(val, str):
        val = val.split(",")
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        raise mcp_server.ToolError(f"{name} must be a list of tags")
    return [v.strip().lstrip("#") for v in val if v.strip().lstrip("#")]


# More ids than this in one call is likely a mistake (a whole deck retagged).
_TAG_TASKS_MAX = 200


def _tag_tasks(args: dict, base: str) -> str:
    ids = args.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise mcp_server.ToolError("ids must be a non-empty list of task ids")
    if len(ids) > _TAG_TASKS_MAX:
        raise mcp_server.ToolError(f"At most {_TAG_TASKS_MAX} tasks per call")
    add, remove = _tag_list(args, "add_tags"), _tag_list(args, "remove_tags")
    if not add and not remove:
        raise mcp_server.ToolError("Give add_tags or remove_tags")
    found = {t["id"]: t for t in map(_find_task, ids)}   # an unknown id fails the whole call
    before = {i: list(t.get("tags") or []) for i, t in found.items()}
    tasks = _deck_write(deck.tag_tasks, list(found), add, remove, _commit_msg("Tag deck tasks", args))
    changed = [t for t in tasks if (t.get("tags") or []) != before.get(t["id"])]
    lines = [f"- {t.get('title', '')} ({decksyntax.short_id(t['id'])}): "
             + (" ".join(f"#{g}" for g in t.get("tags") or []) or "no tags") for t in tasks]
    head = f"Tags changed on {len(changed)} of {len(tasks)} task{'s' if len(tasks) != 1 else ''}"
    return head + (" (the others had them so already)" if len(changed) < len(tasks) else "") + \
        ".\n\n" + "\n".join(lines)


def _comment_task(args: dict, base: str) -> str:
    task = _task_arg(args)
    task = _deck_write(deck.add_comment, task["id"], _str_arg(args, "text"),
                       _commit_msg("Comment on deck task", args))
    return "Comment added.\n\n" + _mcp_task_text(task, base)


def _delete_task(args: dict, base: str) -> str:
    task = _task_arg(args)
    _deck_write(deck.delete_task, task["id"], _commit_msg("Delete deck task", args))
    return f"Deleted task {decksyntax.short_id(task['id'])}: {task.get('title', '')}"


# How many linking pages read_page names before "… and N more".
_BACKLINKS_SHOWN = 50


def _page_links(slug: str, content: str) -> str:
    """For read_page: the pages that link to it and its wikilinks that lead
    nowhere, so the model sees where a page sits without reading others."""
    linking = [b["slug"] for b in find_backlinks(slug)]
    shown = ", ".join(linking[:_BACKLINKS_SHOWN]) or "no other page"
    if len(linking) > _BACKLINKS_SHOWN:
        shown += f", … and {len(linking) - _BACKLINKS_SHOWN} more"
    out = f"\n\n---\nLinked from ({len(linking)}): {shown}"
    slugs = set(all_slugs())
    missing = _missing_links(content, slugs)
    if missing:
        out += "\nBroken wikilinks (no such page): " + ", ".join(f"[[{n}]]" for n in sorted(missing))
    if retrieval.SEMANTIC:
        # As the page view's Related: pages close in meaning that aren't
        # linked either way, the connections not written down yet.
        retrieval.SEMANTIC.poke()
        linked = set(linking) | page_meta(safe_path(slug), slug)[1]
        related = retrieval.SEMANTIC.related(slug, known=slugs, exclude=linked)
        if related:
            out += "\nRelated, not linked (close in meaning): " + ", ".join(s for s, _ in related)
    return out


def _bool_arg(args: dict, name: str) -> bool:
    value = args.get(name, False)
    if not isinstance(value, bool):
        raise mcp_server.ToolError(f"{name} must be true or false")
    return value


def _is_orphan(slug: str, counts: dict[str, int], home: str | None) -> bool:
    """No other page links to it. The home page, the deck's home and scratch
    notes are reached otherwise (the module switcher), and the guide is
    read by the assistant, so they never are."""
    return (not counts.get(slug) and not is_protected(slug) and slug not in (home, GUIDE_SLUG)
            and not is_scratch(slug))


def _list_pages(args: dict, base: str) -> str:
    limit = _mcp_int(args, "limit", 100, 1000)
    under = _clean_folder(args.get("under", ""))
    orphans = _bool_arg(args, "orphans")
    counts = all_backlink_counts()
    home = deck_home_slug()
    pages = []
    for md_file, slug in iter_pages():
        if not in_folder(slug, under) or (orphans and not _is_orphan(slug, counts, home)):
            continue
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            continue
        pages.append((mtime, slug, page_meta(md_file, slug)[0]))
    pages.sort(reverse=True)
    where = f" under {under}/" if under else ""
    if not pages:
        if orphans:
            return f"Every page{where} is linked from another page."
        return f"No pages under {under!r}." if under else "The wiki has no pages."
    lines = [f"{slug} | {title} | edited {fmt_date(mtime)} | {counts.get(slug, 0)}"
             for mtime, slug, title in pages[:limit]]
    shown = f"{len(lines)} of {len(pages)}" if len(lines) < len(pages) else f"all {len(pages)}"
    what = ("Pages no other page links to (the home page, the deck's home and scratch notes aside)"
            if orphans else "Pages")
    return (f"{what}{where}, most recently edited first ({shown}); "
            "slug | title | last edit | pages linking to it:\n" + "\n".join(lines))


def _check_links(args: dict, base: str) -> str:
    under = _clean_folder(args.get("under", ""))
    slugs = set(all_slugs())
    found = []
    for md_file, slug in sorted(iter_pages(), key=lambda p: p[1]):
        if not in_folder(slug, under):
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if missing := _missing_links(content, slugs):
            found.append((slug, sorted(missing)))
    where = f" under {under}/" if under else ""
    if not found:
        return f"No broken wikilinks{where}."
    out = []
    for slug, names in found:
        links = []
        for name in names:
            near = get_close_matches(to_slug(name), slugs, n=1, cutoff=0.8)
            links.append(f"[[{name}]]" + (f" (maybe [[{near[0]}]]?)" if near else ""))
        out.append(f"{slug}: " + ", ".join(links))
    total = sum(len(names) for _s, names in found)
    return (f"{total} wikilinks{where} point to pages that don't exist, on {len(found)} pages "
            "(fix the link, or create the page):\n" + "\n".join(out))


# How much of a long line grep_wiki shows.
_GREP_LINE_CHARS = 300


def _grep_wiki(args: dict, base: str) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise mcp_server.ToolError("pattern must be a non-empty string")
    under = _clean_folder(args.get("under", ""))
    limit = _mcp_int(args, "limit", 50, 500)
    try:
        hits = [h for h in grep_pages(pattern, _bool_arg(args, "ignore_case")) if in_folder(h[0], under)]
    except GrepError as e:
        raise mcp_server.ToolError(f"grep_wiki: {e}")
    where = f" under {under}/" if under else ""
    if not hits:
        return f"No line{where} matches {pattern!r}."
    out, last = [], None
    for slug, number, line in hits[:limit]:
        if slug != last:
            if out:
                out.append("")
            out.append(slug)
            last = slug
        line = line.strip()
        if len(line) > _GREP_LINE_CHARS:
            line = line[:_GREP_LINE_CHARS] + " …"
        out.append(f"  {number}: {line}")
    pages = len({h[0] for h in hits})
    head = f"{len(hits)} matching lines on {pages} pages{where}"
    if len(hits) > limit:
        head += f", the first {limit} shown (raise limit, or narrow the pattern or folder)"
    if not config.GIT_ENABLED:
        head += "; git isn't installed, so the pattern was matched as plain text"
    return head + ":\n" + "\n".join(out)


def _replace_lines(content: str, pattern: re.Pattern, repl, include_code: bool) -> tuple[str, list]:
    """content with pattern replaced line by line, code (fenced blocks,
    inline spans) left alone unless include_code, and the lines that
    changed as (line number, old, new, matches)."""
    out, changed = [], []
    for i, line, in_fence in iter_fenced_lines(content.splitlines(keepends=True)):
        body = line.rstrip("\r\n")
        if in_fence and not include_code:
            out.append(line)
            continue
        code = [] if include_code else [m.span() for m in _INLINE_CODE_RE.finditer(body)]
        parts, last, n = [], 0, 0
        for m in pattern.finditer(body):
            # A match touching inline code (an empty one: starting in it) stays.
            if any(s < max(m.end(), m.start() + 1) and m.start() < e for s, e in code):
                continue
            parts += [body[last:m.start()], repl(m)]
            last = m.end()
            n += 1
        new = "".join(parts) + body[last:]
        if n and new != body:
            changed.append((i + 1, body, new, n))
        out.append(new + line[len(body):])
    return "".join(out), changed


def _cut(line: str) -> str:
    line = line.strip()
    return line[:_GREP_LINE_CHARS] + " …" if len(line) > _GREP_LINE_CHARS else line


def _replace_in_pages(args: dict, base: str) -> str:
    find = _str_arg(args, "find")
    replace = _str_arg(args, "replace", required=False)
    if "\n" in find:
        raise mcp_server.ToolError("find is matched within a line, so it can't contain a line break. "
                                   "For a passage spanning lines, use edit_page on each page.")
    regex, include_code = _bool_arg(args, "regex"), _bool_arg(args, "include_code")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise mcp_server.ToolError("dry_run must be true or false")
    try:
        pattern = re.compile(find if regex else re.escape(find),
                             re.IGNORECASE if _bool_arg(args, "ignore_case") else 0)
    except re.error as e:
        raise mcp_server.ToolError(f"find is not a valid regex: {e}")

    def repl(m: re.Match) -> str:
        if not regex:
            return replace
        try:
            return m.expand(replace)
        except (re.error, IndexError) as e:
            raise mcp_server.ToolError(f"replace: {e} (groups are \\1 or \\g<name>; a literal backslash is \\\\)")

    under = _clean_folder(args.get("under", ""))
    if under and not any(in_folder(slug, under) for _p, slug in iter_pages()):
        raise mcp_server.ToolError(f"No pages under {under!r}. list_pages shows the slugs.")
    max_pages = _mcp_int(args, "max_pages", 50, 1000)
    limit = _mcp_int(args, "limit", 50, 500)
    hits = []
    for path, slug in sorted(iter_pages(), key=lambda p: p[1]):
        if not in_folder(slug, under):
            continue
        content = path.read_text(encoding="utf-8")
        new, changed = _replace_lines(content, pattern, repl, include_code)
        if changed:
            hits.append((path, slug, content, new, changed))
    where = f" under {under}/" if under else ""
    code = "" if include_code else " outside code"
    if not hits:
        return f"Nothing{where} matches {find!r}{code}."
    total = sum(n for *_x, changed in hits for *_l, n in changed)
    counted = f"{total} occurrence{'s' if total > 1 else ''} on {len(hits)} page{'s' if len(hits) > 1 else ''}"
    if dry_run:
        out, shown = [], 0
        for _path, slug, _old, _new, changed in hits:
            if shown >= limit:
                break
            out += ["", slug]
            for number, old_line, new_line, _n in changed[:limit - shown]:
                out += [f"  {number}: - {_cut(old_line)}", f"  {number}: + {_cut(new_line)}"]
                shown += 1
        lines = sum(len(c) for *_x, c in hits)
        head = f"Dry run, nothing written: would replace {counted}{where}{code}"
        if shown < lines:
            head += f"; the first {shown} of {lines} changed lines shown (raise limit to see more)"
        head += ". Call again with dry_run=false to write them, in one commit."
        if len(hits) > max_pages:
            head += f" That is more pages than max_pages ({max_pages}): raise it, or narrow find or under."
        return head + "\n" + "\n".join(out)
    if len(hits) > max_pages:
        raise mcp_server.ToolError(
            f"{find!r} would change {len(hits)} pages, more than max_pages ({max_pages}); nothing was "
            "written. Check with dry_run, then raise max_pages, or narrow find or under.")
    pending = _save_pending_pages([h[0] for h in hits], f"{len(hits)} pages")
    for path, _slug, _old, new, _changed in hits:
        path.write_text(new, encoding="utf-8")
    label = find if len(find) <= 60 else find[:60] + "…"
    on = f"{len(hits)} pages" if len(hits) > 1 else hits[0][1]
    git_commit([h[0] for h in hits], _commit_msg(f"Replace {label!r} on {on}", args))
    slugs = set(all_slugs())
    broken = sorted(set().union(*(_missing_links(new, slugs) - _missing_links(old, slugs)
                                  for _p, _s, old, new, _c in hits)))
    out = f"Replaced {counted}{where}{code}, in one commit: " + ", ".join(h[1] for h in hits)
    if broken:
        out += ("\nNote: these new wikilinks point to pages that don't exist (yet): "
                + ", ".join(f"[[{n}]]" for n in broken))
    return out + pending


def _read_page(args: dict, base: str) -> str:
    raw = args.get("slug")
    if not isinstance(raw, str) or not raw.strip():
        raise mcp_server.ToolError("slug must be a non-empty string")
    slug = _mcp_page_slug(raw)
    if slug is None:
        raise mcp_server.ToolError(f"No page {raw!r}. Use search_wiki or list_pages to find its slug.")
    content = safe_path(slug).read_text(encoding="utf-8", errors="replace")
    head = f"slug: {slug}\nurl: {base}{url('page', slug)}\nfile: {_host_file(slug)}\n"
    heading = _str_arg(args, "heading", required=False).strip()
    if heading:
        lines = content.splitlines(keepends=True)
        found = _section_lines(lines, heading)
        if found is None:
            raise mcp_server.ToolError(_no_heading(slug, content, heading) + " Leave out heading for the whole page.")
        start, end = found
        # Line numbers as grep_wiki gives them, so the two fit together.
        head += f"section: lines {start + 1}–{end} of {len(lines)}\n"
        content = "".join(lines[start:end]).rstrip("\n") + "\n"
    return f"{head}\n{content}" + _mcp_task_refs(content, base) + _page_links(slug, content)


def _date_arg(args: dict, name: str) -> str | None:
    value = _str_arg(args, name, required=False).strip()
    if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise mcp_server.ToolError(f"{name} must be a date, YYYY-MM-DD")
    return value or None


def _file_label(filepath: str) -> str:
    """A changed file as the model knows it: a page by its slug, the deck's
    files as tasks."""
    if filepath.startswith("_deck/"):
        return "deck tasks"
    return filepath[:-3] if filepath.endswith(".md") else filepath


def _history(args: dict, base: str) -> str:
    if not config.GIT_ENABLED:
        raise mcp_server.ToolError("There is no history: git isn't installed where Leaf runs.")
    limit = _mcp_int(args, "limit", 20, 200)
    raw = _str_arg(args, "slug", required=False).strip()
    version = _str_arg(args, "version", required=False).strip().lower()
    if version and not raw:
        raise mcp_server.ToolError("version needs the page's slug too")
    if not raw:
        return _recent_changes(_date_arg(args, "since"), _clean_folder(args.get("under", "")), limit)
    slug = _mcp_page_slug(raw) or _new_slug(raw)       # a deleted page has a history too
    path = safe_path(slug)
    entries = git_log(path)
    if not entries:
        raise mcp_server.ToolError(f"No history for {raw!r}. Use list_pages to find its slug.")
    if version:
        return _page_change(slug, path, entries, version)
    rows = [f"{e['sha'][:7]} | {e['time']} | +{e['added']} -{e['deleted']} | {e['msg']}"
            for e in entries[:limit]]
    shown = f"{len(rows)} of {len(entries)}" if len(rows) < len(entries) else f"all {len(entries)}"
    state = "" if path.exists() else " (deleted: restore_page brings back a version)"
    if path.exists() and is_uncommitted(path):
        state = " (it has changes not committed yet, newer than the first version listed)"
    return (f"Versions of {slug}{state}, newest first ({shown}); version | when | lines added and "
            "removed | what (\"(MCP)\": written by an assistant). history with a version shows "
            "what it changed:\n" + "\n".join(rows))


def _page_change(slug: str, path: Path, entries: list[dict], version: str) -> str:
    """What one version changed on the page, as a diff."""
    i = version_index(entries, version) if SHA_RE.match(version) else None
    if i is None:
        raise mcp_server.ToolError(f"{version!r} is not a version of {slug}; history lists them.")
    before = [config.LEAF_DATA / entries[i + 1]["path"]] if i + 1 < len(entries) else []
    diff = git_diff(entries[i]["sha"], version_path(path, entries, entries[i]["sha"]), *before) or "(no changes)"
    e = entries[i]
    return f"{slug}, version {e['sha'][:7]} of {e['time']}: {e['msg']}\n\n{diff}"


def _recent_changes(since: str | None, under: str | None, limit: int) -> str:
    entries = git_log_all(since, under, limit)
    pending = [f for f in git_uncommitted_files() if not under or in_folder(_file_label(f["filepath"]), under)]
    out = []
    if pending:
        out.append("Not committed yet (edited in the browser or on disk): "
                   + ", ".join(f"{_file_label(f['filepath'])} ({f['rel']})" if f["rel"] else
                               f"{_file_label(f['filepath'])} (deleted)" for f in pending))
    where = f" under {under}/" if under else ""
    when = f" since {since}" if since else ""
    if not entries:
        out.append(f"No changes{where}{when}.")
        return "\n\n".join(out)
    rows = [f"{e['time']} | {e['sha'][:7]} | {e['msg']} | "
            + ", ".join(dict.fromkeys(_file_label(f) for f in e["files"])) for e in entries]
    out.append(f"Changes{where}{when}, newest first ({len(rows)}{', the limit' if len(rows) == limit else ''}); "
               "when | version | what (\"(MCP)\": written by an assistant) | pages:\n" + "\n".join(rows))
    return "\n\n".join(out)


def _restore_page(args: dict, base: str) -> str:
    if not config.GIT_ENABLED:
        raise mcp_server.ToolError("There is no history: git isn't installed where Leaf runs.")
    raw = _str_arg(args, "slug")
    version = _str_arg(args, "version").strip().lower()
    slug = _mcp_page_slug(raw) or _new_slug(raw)
    path = safe_path(slug)
    entries = git_log(path)
    i = version_index(entries, version) if SHA_RE.match(version) else None
    if i is None:
        raise mcp_server.ToolError(f"{version!r} is not a version of {slug}; history(slug) lists them.")
    sha = entries[i]["sha"]
    content = git_show(sha, version_path(path, entries, sha))
    if content is None:
        raise mcp_server.ToolError(f"Version {sha[:7]} of {slug} has no text (the page was deleted then); "
                                   "pick the version before it.")
    existed = path.exists()
    pending = _save_pending(path, slug)
    old = path.read_text(encoding="utf-8") if existed else ""
    if existed and old == content:
        raise mcp_server.ToolError(f"{slug} already reads as version {sha[:7]}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, _commit_msg(f"Restore {slug} to {sha[:7]}", args))
    what = "Restored" if existed else "Brought back the deleted page"
    return (f"{what} {slug} as of version {sha[:7]} ({entries[i]['time']}).\n{_page_info(slug, base)}"
            + pending + _link_warning(old, content))


def _list_tags(args: dict, base: str) -> str:
    counts: dict[str, list[int]] = {}             # tag → [pages, open tasks, done tasks]
    for md_file, _slug in iter_pages():
        try:
            tags = page_tags(md_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for tag in set(tags):
            counts.setdefault(tag, [0, 0, 0])[0] += 1
    for task in deckstore.all_tasks():
        column = 1 if task_status(task) == "open" else 2
        for tag in {t.lower() for t in task.get("tags") or []}:
            counts.setdefault(tag, [0, 0, 0])[column] += 1
    if not counts:
        return "No tags yet: neither pages (frontmatter tags:) nor tasks have any."
    lists = {n.lower() for n in pages.list_names()}
    rows = sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
    lines = [f"{tag}{' (a list)' if tag in lists else ''} | {p} | {o} | {d}" for tag, (p, o, d) in rows]
    return ("Tags in use, most used first; reuse these rather than inventing near-duplicates. "
            "tag | pages | open tasks | done tasks:\n" + "\n".join(lines))


def _leaf_help(args: dict, base: str) -> str:
    """Leaf's documentation (manual.py): passages for a query, a doc or one
    of its sections, or the list of docs with their headings."""
    query = _str_arg(args, "query", required=False).strip()
    topic = _str_arg(args, "topic", required=False).strip().lower().removesuffix(".md")
    heading = _str_arg(args, "heading", required=False).strip()
    docs = manual.topics()
    if not docs:
        raise mcp_server.ToolError("Leaf's documentation isn't installed with this server.")
    if topic:
        if topic not in docs:
            raise mcp_server.ToolError(f"No topic {topic!r}. Topics: {', '.join(docs)}.")
        text = manual.read(topic)
        if not heading:
            return f"topic: {topic}\n\n{text}"
        lines = text.splitlines(keepends=True)
        found = _section_lines(lines, heading)
        if found is None:
            raise mcp_server.ToolError(_no_heading(topic, text, heading) + " Leave out heading for the whole doc.")
        return f"topic: {topic}\n\n" + "".join(lines[found[0]:found[1]]).rstrip("\n") + "\n"
    if heading:
        raise mcp_server.ToolError("heading needs a topic.")
    if query:
        hits = manual.find(query, _mcp_int(args, "limit", 5, 20))
        if not hits:
            return "Nothing in Leaf's documentation matches. Try other words, or leave out query for the list of topics."
        return "\n\n---\n\n".join(
            f"[{i}] {d['title']} › {d['heading']}\ntopic: {d['topic']}\nheading: {d['heading']}\n\n{d['text']}"
            if d["heading"] else f"[{i}] {d['title']}\ntopic: {d['topic']}\n\n{d['text']}"
            for i, d in enumerate(hits, 1)) + "\n\nThe whole section: leaf_help with its topic and heading."
    out = ["Leaf's documentation: the README (what Leaf is, its features, setup) and the spec, "
           "one doc per area. Read one with topic, a section with topic and heading."]
    for name in docs:
        found = manual.headings(manual.read(name))
        out.append(f"\n{name}: " + (found[0][1] if found else name))
        out.extend(f"  {'  ' * (level - 2)}{text}" for level, text in found[1:] if level <= 3)
    return "\n".join(out)


_READ_TOOLS = {"grep_wiki": _grep_wiki, "read_page": _read_page, "list_pages": _list_pages,
               "check_links": _check_links, "history": _history, "list_tags": _list_tags,
               "leaf_help": _leaf_help}

_WRITE_TOOLS = {
    "write_page": _write_page, "edit_page": _edit_page, "replace_in_pages": _replace_in_pages,
    "append_to_page": _append_to_page,
    "rename_page": _rename_page, "delete_page": _delete_page,
    "create_task": _create_task, "update_task": _update_task, "tag_tasks": _tag_tasks,
    "comment_task": _comment_task, "delete_task": _delete_task, "restore_page": _restore_page,
}


# The user's guide for keeping this wiki: their conventions (where things go,
# tags, page layouts, what stays private), a page like any other. Its text is
# part of the instructions a client gets when it connects, so the model knows
# it before it writes, in every client.
GUIDE_SLUG = "wiki-guide"
# More than this is cut, with a pointer to read_page: the instructions are
# in the model's context for the whole session.
_GUIDE_CHARS = 8000


def mcp_instructions() -> str:
    """The server's instructions for initialize, plus the wiki's guide, or,
    without one, a nudge to start it."""
    path = safe_path(GUIDE_SLUG)
    try:
        guide = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        guide = ""
    if not guide:
        return (mcp_server.INSTRUCTIONS + "\n\nThis wiki has no guide yet. When the user tells you "
                "how they want it kept (where things go, which tags, page layouts, what stays "
                f"private), offer to write that down on the page {GUIDE_SLUG!r}: it is shown to "
                "you at the start of every session.")
    if len(guide) > _GUIDE_CHARS:
        guide = guide[:_GUIDE_CHARS] + f"\n\n[… cut here: read_page {GUIDE_SLUG!r} for the rest]"
    return (mcp_server.INSTRUCTIONS + f"\n\nThe user's guide for this wiki (the page {GUIDE_SLUG!r}). "
            "Follow it; where it differs from the advice above, it wins. When the user settles a "
            "new convention, offer to add it there.\n\n" + guide)


def mcp_call_tool(name: str, args: dict) -> str:
    base = config.LEAF_BASE_URL or request.host_url.rstrip("/")
    if name == "search_wiki":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise mcp_server.ToolError("query must be a non-empty string")
        under = _clean_folder(args.get("under", ""))
        if under and not any(in_folder(slug, under) for _p, slug in iter_pages()):
            raise mcp_server.ToolError(f"No pages under {under!r}. list_pages shows the slugs, "
                                       "whose folders are the part before the last '/'.")
        hits = retrieve(query, _mcp_int(args, "limit", 8, 20), under)
        if not hits:
            return "No matching sections or tasks. Try other words, or list_pages to browse."
        out = []
        for i, d in enumerate(hits, 1):
            if d.get("type") == "task":
                out.append(f"[{i}] " + _mcp_task_text(d["task"], base))
                continue
            anchor = d["anchor"]
            where = f"{d['title']} › {d['heading']}" if d["heading"] else d["title"]
            out.append(f"[{i}] {where}\nslug: {d['slug']}\nedited: {fmt_date(d['mtime'])}\n"
                       f"url: {base}{url('page', d['slug'])}{anchor}\n"
                       f"file: {_host_file(d['slug'])}\n\n{d['text']}")
        return "\n\n---\n\n".join(out)
    if name == "search_tasks":
        return _mcp_search_tasks(args, base)
    if name in _READ_TOOLS:
        return _READ_TOOLS[name](args, base)
    if name in _WRITE_TOOLS:
        return _WRITE_TOOLS[name](args, base)
    raise mcp_server.ToolError(f"Unknown tool: {name}")
