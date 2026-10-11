"""MCP (Model Context Protocol) server: lets an LLM client
read and write the wiki through tools. Served by server.py at POST /mcp (MCP's
"Streamable HTTP" transport, answering each request with plain JSON; no
event stream, no sessions). The tools search, read and write pages and deck
tasks; each write is a git commit.

This module is the protocol only (JSON-RPC in, JSON-RPC out); server.py
does authentication and implements the tools through call_tool.

Clients register http://localhost:5000/mcp with the HTTP transport.
"""

from typing import Callable

# Versions this server speaks; it answers with the client's if listed here,
# else with the newest. The tools-only subset used here is the same in all.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Leaf is the user's personal wiki: their notes, in markdown, plus the Deck, "
    "their task list. Use search_wiki when a question may be answered by something "
    "the user wrote down (it finds tasks too), then read_page for a page's full "
    "text (with heading, just one section of a long page). Search finds by meaning as well as by keyword, so phrase queries the "
    "way the notes might. For questions about their tasks (what's open for a "
    "project, what's due, what they finished) use search_tasks, which filters by "
    "tag and status. To find every occurrence of exact text (a ticket id, a name, "
    "a TODO), not the most relevant sections, use grep_wiki, which takes a regex. "
    "read_page shows which pages link to a page; list_pages counts the links and, "
    "with orphans, lists pages nothing links to; check_links reports wikilinks that "
    "lead nowhere; list_tags shows the tags in use. history shows recent changes "
    "and a page's versions (restore_page goes back to one). Cite pages and tasks "
    "by their URL.\n\n"
    "For questions about Leaf itself (how to do something in it, its markdown "
    "syntax, the deck's options, settings, setup, the MCP), call leaf_help: it "
    "searches Leaf's documentation. Answer from it, not from guesses.\n\n"
    "You can also write: the wiki is meant to be kept up to date by you. Before "
    "creating a page, search for one that already covers the topic and extend "
    "it instead. Change pages with edit_page (exact text replacement, like a "
    "code editor) or append_to_page (adds text without reading the page "
    "first; under_heading adds to that section); write_page creates a page "
    "or, with overwrite, replaces all of it. rename_page keeps links working. "
    "Tasks: create_task, update_task (fields, done, won't do), tag_tasks "
    "(add or remove tags on several tasks at once), comment_task for a status line. Every write is a git commit the user can undo; give "
    "each a short summary, which becomes its commit message."
)

_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
# Writes that only add (a page, a section, a task, a comment), and those that
# change or remove what is there.
_ADDS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
_CHANGES = {**_ADDS, "destructiveHint": True}

_SUMMARY = {"type": "string", "description": (
    "What this change does, in a few words (e.g. 'add findings from BUG-1234'); "
    "it becomes the git commit message.")}
_PAGE_SLUG = {"type": "string",
              "description": "The page's slug as search_wiki or list_pages give it, e.g. 'git-remote' or 'journal/2026-09'."}
_TASK_ID = {"type": "string",
            "description": "The task's id as search_tasks or search_wiki show it (the 8-character short id is enough)."}
_TASK_FIELDS = {
    "notes": {"type": "string", "description": "The task's notes (markdown). Replaces the notes there are."},
    "tags": {"type": "array", "items": {"type": "string"},
             "description": "The task's tags, without '#'. Replaces its tags; a list's name as a tag files it in that list."},
    "due": {"type": "string", "description": "Deadline, YYYY-MM-DD; empty to clear."},
    "start": {"type": "string", "description": "When to start (it shows in Focus from then), YYYY-MM-DD; empty to clear."},
    "repeat": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly", "none"],
               "description": "Repeat the task; done, the next occurrence is created."},
    "repeat_every": {"type": "integer", "minimum": 1, "description": "Repeat every N days/weeks/months/years (default 1)."},
    "repeat_from": {"type": "string", "enum": ["due", "completion"],
                    "description": "Next occurrence counted from the due date (default) or from when it was done."},
    "keep": {"type": "boolean",
             "description": "A keep task: done, it goes back to the list instead of being archived (shopping list staples)."},
}

TOOLS = [
    {
        "name": "search_wiki",
        "title": "Search the wiki",
        "description": (
            "Search the user's wiki and return the most relevant page sections with "
            "their full text, best first, each with its page, the page's last edit "
            "date (to tell current notes from stale ones) and a link. Several "
            "sections may come from the same page. Deck tasks that match are "
            "results too, with their status, tags, dates and notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": (
                    'What to look for: words or a question. Also: "exact phrase", -word to '
                    'leave out, #tag for pages (frontmatter tags) and tasks with a tag, -#tag, '
                    'and edited:2026-09 / after:2026-07-01 / before:2026-08 for when a page was '
                    'last edited (a task: its last activity).')},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8,
                          "description": "How many sections to return."},
                "under": {"type": "string",
                          "description": "Only pages in this folder and its subfolders, without tasks, e.g. 'tickets' or 'projects' (the slug part before a '/')."},
            },
            "required": ["query"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "search_tasks",
        "title": "Search tasks",
        "description": (
            "List the user's deck tasks, filtered by tag and status, each with "
            "status, tags, start and due dates, repeat, notes and a link. Without a "
            "query, open tasks come by deadline, then start date; done ones newest "
            "first. With a query, by relevance. Starts with today's date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string",
                        "description": "Only tasks with this tag; several comma-separated tags must all match, e.g. 'leaf' or 'work,urgent'."},
                "status": {"type": "string", "enum": ["open", "done", "all"], "default": "open",
                           "description": "open, done (completed or won't do), or all."},
                "query": {"type": "string", "description": (
                    'Words to look for in title, notes and tags; "exact phrase", -word and '
                    'edited:/after:/before: (last activity, e.g. after:2026-09-01) work too.')},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "grep_wiki",
        "title": "Find lines in the wiki",
        "description": (
            "Find every line of every page that matches a regular expression, as "
            "page slug, line number and line: for exact text and all its occurrences "
            "(ticket ids, names, TODOs, an old term to replace), where search_wiki "
            "gives only the most relevant sections. Perl-style regex (\\d, \\b, "
            "(?:…), lookarounds); escape . ( [ and such to match them literally. "
            "Pages only, not deck tasks."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The regex, e.g. 'BUG-\\d{4,5}' or '^- \\[ \\]'."},
                "ignore_case": {"type": "boolean", "default": False},
                "under": {"type": "string",
                          "description": "Only pages in this folder and its subfolders, e.g. 'tickets'."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50,
                          "description": "How many lines to show; the total is always given."},
            },
            "required": ["pattern"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "read_page",
        "title": "Read a page",
        "description": ("Return a wiki page's full markdown, frontmatter included, by its slug, "
                        "followed by the pages that link to it and its wikilinks that lead nowhere. "
                        "With heading, only that section (subsections included): cheaper for long pages."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string",
                         "description": "The page's slug as search_wiki or list_pages give it, e.g. 'git-remote' or 'journal/2026-09'."},
                "heading": {"type": "string",
                            "description": "A heading's text (any level, without '#'): return only its section."},
            },
            "required": ["slug"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "list_pages",
        "title": "List pages",
        "description": ("List the wiki's pages (slug, title, last edited, how many pages link "
                        "to it), most recently edited first; with `under`, one folder's; with "
                        "`orphans`, only pages no other page links to. Slugs show the folder structure."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                "under": {"type": "string",
                          "description": "Only pages in this folder and its subfolders, e.g. 'tickets' or 'projects' (the slug part before a '/')."},
                "orphans": {"type": "boolean", "default": False, "description": (
                    "Only pages no other page links to (the home page, the deck's home "
                    "and scratch notes aside): candidates to link from somewhere.")},
            },
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "check_links",
        "title": "Find broken wikilinks",
        "description": ("List every [[wikilink]] in the wiki that points to a page that doesn't "
                        "exist, by page, with the existing page it was probably meant for."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "under": {"type": "string",
                          "description": "Only pages in this folder and its subfolders, e.g. 'tickets'."},
            },
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "list_tags",
        "title": "List tags",
        "description": ("List the tags in use, on pages (frontmatter tags:) and tasks, with how "
                        "often each occurs. Check it before tagging, to reuse a tag instead of "
                        "inventing a near-duplicate."),
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": _READ_ONLY,
    },
    {
        "name": "history",
        "title": "Show history",
        "description": (
            "The wiki's git history. Without slug: recent changes across the wiki "
            "(what, when, which pages; '(MCP)' marks an assistant's), plus pages "
            "with changes not committed yet. With slug: that page's versions. With "
            "slug and version: what that version changed, as a diff. restore_page "
            "goes back to a version."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "A page, also a deleted one."},
                "version": {"type": "string", "description": "A version (commit) as history lists it, e.g. '1a2b3c4'."},
                "since": {"type": "string", "description": "Recent changes from this day on, YYYY-MM-DD."},
                "under": {"type": "string", "description": "Recent changes in this folder only, e.g. 'tickets'."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "leaf_help",
        "title": "Leaf's documentation",
        "description": (
            "Search Leaf's own documentation (its README and its spec) for how Leaf works: "
            "features, markdown syntax ([[deck …]] options, callouts, wikilinks, templates), "
            "the editor, search syntax, keyboard shortcuts, settings, setup, the MCP. Not the "
            "user's notes: search_wiki finds those. With query, the best passages; with "
            "topic (and heading), that doc or one section; with neither, the list of topics "
            "and their headings. The spec is written for Leaf's developers: answer the user "
            "in terms of what they type or click, leaving out function and file names."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to look for, in words the docs would use, e.g. 'deck tab labels' or 'rename page'."},
                "topic": {"type": "string",
                          "description": "A doc to read, as the results name it: 'readme', 'deck', 'editor', 'navigation', …"},
                "heading": {"type": "string",
                            "description": "With topic: a heading's text, to read only its section."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5,
                          "description": "How many passages a query returns."},
            },
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "write_page",
        "title": "Create a page",
        "description": (
            "Create a wiki page with the given markdown (start it with '# Title'). "
            "Fails if the page exists, unless overwrite is true, which replaces all "
            "of it; to change part of a page use edit_page or append_to_page. Tells "
            "you about pages with similar names and wikilinks that lead nowhere."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": (
                    "The new page's slug or name, folders separated by '/', e.g. "
                    "'tickets/bug-1234' ('Tickets/BUG 1234' becomes that).")},
                "content": {"type": "string", "description": "The page's full markdown."},
                "overwrite": {"type": "boolean", "default": False,
                              "description": "Replace an existing page's whole text."},
                "summary": _SUMMARY,
            },
            "required": ["slug", "content"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "edit_page",
        "title": "Edit a page",
        "description": (
            "Replace exact text in a page: old_string must match the page's markdown "
            "exactly (whitespace and line breaks included) and occur once, unless "
            "replace_all is true. Copy old_string from read_page or a search_wiki "
            "section. An empty new_string deletes the text. For several changes to "
            "one page, pass edits instead: applied in order, all or none, one commit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": _PAGE_SLUG,
                "old_string": {"type": "string", "description": "The text to replace."},
                "new_string": {"type": "string", "description": "The text to put there."},
                "replace_all": {"type": "boolean", "default": False,
                                "description": "Replace every occurrence of old_string."},
                "edits": {"type": "array", "description": (
                    "Instead of old_string/new_string: several replacements, each on the "
                    "text the ones before it left. If one doesn't match, nothing is changed."),
                    "items": {"type": "object", "properties": {
                        "old_string": {"type": "string"}, "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False}},
                        "required": ["old_string", "new_string"]}},
                "summary": _SUMMARY,
            },
            "required": ["slug"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "replace_in_pages",
        "title": "Find and replace across pages",
        "description": (
            "Replace text on every page where it occurs, in one commit: a term, a name, "
            "a ticket prefix, a link. Matched within a line; code (fenced blocks, inline "
            "code) is left alone unless include_code. Pages only, not tasks. By default a "
            "dry run that shows each line before and after and writes nothing: check it, "
            "then call again with dry_run=false. For changes to one page, use edit_page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "find": {"type": "string", "description": "The text to find, within a line."},
                "replace": {"type": "string", "description": (
                    "What to put there; empty deletes. With regex, \\1 or \\g<name> are the groups.")},
                "regex": {"type": "boolean", "default": False,
                          "description": "find is a regular expression (Python syntax)."},
                "ignore_case": {"type": "boolean", "default": False},
                "include_code": {"type": "boolean", "default": False,
                                 "description": "Replace in code blocks and inline code too."},
                "under": {"type": "string", "description": "Only pages in this folder, e.g. 'tickets'."},
                "dry_run": {"type": "boolean", "default": True,
                            "description": "Only show what would change. false writes it."},
                "max_pages": {"type": "integer", "default": 50, "description": (
                    "Refuse to write when more pages would change: a guard against a too broad find.")},
                "limit": {"type": "integer", "default": 50,
                          "description": "How many changed lines a dry run shows."},
                "summary": _SUMMARY,
            },
            "required": ["find", "replace"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "append_to_page",
        "title": "Append to a page",
        "description": (
            "Add markdown to the end of a page, or with under_heading to the end of "
            "that heading's section (before the next heading of its level), without "
            "reading the page first. For logs and adding to a section."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": _PAGE_SLUG,
                "content": {"type": "string", "description": "The markdown to add."},
                "under_heading": {"type": "string", "description": (
                    "The heading whose section gets the text, e.g. 'Findings' (any level, "
                    "case ignored). Leave it out to add at the end of the page.")},
                "summary": _SUMMARY,
            },
            "required": ["slug", "content"],
        },
        "annotations": _ADDS,
    },
    {
        "name": "rename_page",
        "title": "Rename or move a page",
        "description": ("Rename a page or move it to another folder. Every link to it, on "
                        "pages and in tasks, is changed to the new name. A folder ('projects/', "
                        "with the slash) moves with all its pages and subfolders, in one commit."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": (
                    _PAGE_SLUG["description"] + " Or a folder, with a trailing slash: 'journal/'.")},
                "new_slug": {"type": "string", "description": (
                    "The new slug, e.g. 'projects/leaf/mcp'; for a folder, the folder's new path.")},
                "summary": _SUMMARY,
            },
            "required": ["slug", "new_slug"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "delete_page",
        "title": "Delete a page",
        "description": ("Delete a page (it stays in the git history, restorable from Deleted "
                        "Pages). Says which pages still link to it."),
        "inputSchema": {
            "type": "object",
            "properties": {"slug": _PAGE_SLUG, "summary": _SUMMARY},
            "required": ["slug"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "restore_page",
        "title": "Restore a page version",
        "description": ("Set a page back to an earlier version (history lists them), or bring "
                        "back a deleted page. A new commit: the versions after it stay in the history."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The page, also a deleted one."},
                "version": {"type": "string", "description": "The version to go back to, e.g. '1a2b3c4'."},
                "summary": _SUMMARY,
            },
            "required": ["slug", "version"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "create_task",
        "title": "Create a task",
        "description": "Add a task to the user's deck. Returns it with its id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What to do, in a few words."},
                **_TASK_FIELDS,
                "summary": _SUMMARY,
            },
            "required": ["title"],
        },
        "annotations": _ADDS,
    },
    {
        "name": "update_task",
        "title": "Update a task",
        "description": (
            "Change a deck task: only the fields given change. status: done (a "
            "repeating task gets its next occurrence), wont_do (abandon it), skip "
            "(won't do this occurrence, the series goes on), open (reopen)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _TASK_ID,
                "title": {"type": "string"},
                **_TASK_FIELDS,
                "add_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to add."},
                "remove_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to remove."},
                "status": {"type": "string", "enum": ["open", "done", "wont_do", "skip"]},
                "summary": _SUMMARY,
            },
            "required": ["id"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "tag_tasks",
        "title": "Tag several tasks",
        "description": (
            "Add tags to several deck tasks and/or remove tags from them, in one change "
            "(one commit the user can undo). Case is ignored; a tag a task has keeps its "
            "spelling. For one task, update_task's add_tags/remove_tags do the same. "
            "list_tags shows the tags in use: reuse those."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"},
                        "description": "The tasks: ids (or their 8-character prefixes) as search_tasks shows them."},
                "add_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to add."},
                "remove_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to remove."},
                "summary": _SUMMARY,
            },
            "required": ["ids"],
        },
        "annotations": _CHANGES,
    },
    {
        "name": "comment_task",
        "title": "Comment on a task",
        "description": "Add a comment (a dated status line) to a deck task, open or done.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _TASK_ID,
                "text": {"type": "string", "description": "The comment."},
                "summary": _SUMMARY,
            },
            "required": ["id", "text"],
        },
        "annotations": _ADDS,
    },
    {
        "name": "delete_task",
        "title": "Delete a task",
        "description": ("Delete a deck task for good (only in the git history after). To end "
                        "a task use update_task with status done or wont_do instead."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": _TASK_ID, "summary": _SUMMARY},
            "required": ["id"],
        },
        "annotations": _CHANGES,
    },
]


class ToolError(Exception):
    """A tool failed in a way the model should see (bad slug, …). Returned
    as a result with isError, not as a protocol error, so the model can
    correct itself."""


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def handle(message, call_tool: Callable[[str, dict], str], version: str,
           instructions: Callable[[], str] = lambda: INSTRUCTIONS) -> dict | None:
    """Answer one JSON-RPC message. Notifications and responses (no id, or
    no method) get None: the transport answers those with 202 and no body.
    `instructions` gives initialize's instructions (asked for only then)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    id_ = message.get("id")
    if not isinstance(method, str):
        return None  # a response to something we never asked
    if "id" not in message:
        return None  # notification, e.g. notifications/initialized
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(id_, -32602, "params must be an object")

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(id_, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "leaf", "title": "Leaf wiki", "version": version},
            "instructions": instructions(),
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not any(t["name"] == name for t in TOOLS):
            return _error(id_, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _error(id_, -32602, "arguments must be an object")
        try:
            text, is_error = call_tool(name, args), False
        except ToolError as e:
            text, is_error = str(e), True
        return _result(id_, {"content": [{"type": "text", "text": text}], "isError": is_error})
    return _error(id_, -32601, f"Method not found: {method}")
