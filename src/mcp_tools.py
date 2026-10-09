"""The MCP tools (search_wiki, search_tasks, read_page, list_pages); the
protocol itself is in mcp_server.py, the endpoint in server.py."""

from datetime import date

from flask import request

import config
import deck as deck_module
import mcp_server
import retrieval
import search
from mdtext import _mask_code, to_slug
from pages import fmt_date, iter_pages, _page_meta, safe_path, url
from render import _task_link
from retrieval import _in_folder, _passes, retrieve, _task_status


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


_RECURRENCE_UNITS = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}


def _mcp_task_text(task: dict, base: str) -> str:
    """One deck task for an LLM: title, a line of facts (status, tags, dates,
    repeat, list), its link in the deck, its notes and its comments."""
    status = _task_status(task)
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
    list_name = deck_module.task_list(task)
    if list_name:
        facts.append(f"list: {list_name}")
    url = base + _task_link(task, list_name)
    text = f"Task: {task.get('title', '')}\n{' · '.join(facts)}\nurl: {url}"
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
    masked, _ = _mask_code(content)
    refs = list(dict.fromkeys(m.group(1).lower() for m in deck_module.TASK_REF_RE.finditer(masked)))
    if not refs:
        return ""
    tasks = deck_module.resolve_task_refs(refs)
    lines = []
    for ref in refs:
        task = tasks[ref]
        if task is None:
            lines.append(f"- [[task:{ref}]]: no such task (deleted)")
            continue
        facts = [_task_status(task)]
        facts += [f"{k} {task[k + '_date']}" for k in ("start", "due") if task.get(k + "_date")]
        url = base + _task_link(task, deck_module.task_list(task))
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
    docs = [d for d in deck_module.task_search_docs()
            if matches(d["task"]) and _passes(parsed, d)]
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


def _mcp_call_tool(name: str, args: dict) -> str:
    base = config.LEAF_BASE_URL or request.host_url.rstrip("/")
    if name == "search_wiki":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise mcp_server.ToolError("query must be a non-empty string")
        under = _clean_folder(args.get("under", ""))
        if under and not any(_in_folder(slug, under) for _p, slug in iter_pages()):
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
                       f"url: {base}{url('page', d['slug'])}{anchor}\n\n{d['text']}")
        return "\n\n---\n\n".join(out)
    if name == "search_tasks":
        return _mcp_search_tasks(args, base)
    if name == "read_page":
        raw = args.get("slug")
        if not isinstance(raw, str) or not raw.strip():
            raise mcp_server.ToolError("slug must be a non-empty string")
        slug = _mcp_page_slug(raw)
        if slug is None:
            raise mcp_server.ToolError(f"No page {raw!r}. Use search_wiki or list_pages to find its slug.")
        content = safe_path(slug).read_text(encoding="utf-8", errors="replace")
        return f"slug: {slug}\nurl: {base}{url('page', slug)}\n\n{content}" + _mcp_task_refs(content, base)
    if name == "list_pages":
        limit = _mcp_int(args, "limit", 100, 1000)
        under = _clean_folder(args.get("under", ""))
        pages = []
        for md_file, slug in iter_pages():
            if not _in_folder(slug, under):
                continue
            try:
                mtime = md_file.stat().st_mtime
            except OSError:
                continue
            pages.append((mtime, slug, _page_meta(md_file, slug)[0]))
        pages.sort(reverse=True)
        lines = [f"{slug} | {title} | edited {fmt_date(mtime)}" for mtime, slug, title in pages[:limit]]
        if not pages:
            return f"No pages under {under!r}." if under else "The wiki has no pages."
        shown = f"{len(lines)} of {len(pages)}" if len(lines) < len(pages) else f"all {len(pages)}"
        where = f" under {under}/" if under else ""
        return f"Pages{where}, most recently edited first ({shown}); slug | title | last edit:\n" + "\n".join(lines)
    raise mcp_server.ToolError(f"Unknown tool: {name}")
