# MCP server

Part of Leaf's spec (see `AGENTS.md` for the index).

## MCP server

`POST /mcp` lets an LLM client (any MCP client) read and write the wiki and the deck through tools. Protocol in `src/mcp_server.py` (JSON-RPC, no Flask), route in `server.py` (`mcp_endpoint`), tools in `src/mcp_tools.py` (`mcp_call_tool`). MCP's Streamable HTTP transport, reduced to what tools need: each POST carries one message (or a batch) and is answered with plain JSON; notifications get 202; no event stream (GET → 405), no sessions. `initialize` echoes the client's protocol version when it is in `PROTOCOL_VERSIONS`, else the newest, and sends `instructions` telling the model what Leaf is and how to write to it (search before creating a page, extend rather than duplicate, edit_page/append_to_page over rewriting, a summary per write).

Register `http://localhost:5000/mcp` with your MCP client (HTTP transport) and the header `Authorization: Bearer <LEAF_MCP_TOKEN>`.

A tool failure the model can fix (bad slug, empty query, text not found) is a result with `isError`, not a protocol error.

### Reading

Annotated `readOnlyHint`.

| Tool | Does |
|------|------|
| `search_wiki(query, limit=8, under)` | The most relevant **sections**, full text, best first, each with title › heading, slug, the page's last edit date (`edited:`, so the model can tell current notes from stale ones), a URL with the heading anchor and the page's file on the host (`file:`, see Links). Several may come from one page. Matching deck tasks are results too, ranked with the sections. `under` restricts it to a folder: pages whose slug is that prefix or below it (`in_folder`, case ignored; `tickets`, `tickets/` and `/page/tickets` alike, `_clean_folder`), and no tasks. A folder without pages is a tool error, so the model corrects it. |
| `search_tasks(tag, status="open", query, limit=50)` | Deck tasks filtered by tag (comma = all must match, case-insensitive; a list is its tag) and status (`open`, `done` = completed or won't do, `all`), separate lists included. With a query, ranked by BM25 within the filter; without, open tasks by deadline, then start date, then title, done ones newest first. Starts with "Today is …" so the model can tell overdue from upcoming. |
| `read_page(slug)` | A page's slug, URL and host file (`file:`), then its raw markdown, frontmatter included, plus a list of the deck tasks its `[[task:…]]` references stand for. Accepts a slug, `/page/<slug>`, a full URL, `<slug>.md` or the page name (`_mcp_page_slug`); `safe_path` keeps it inside the wiki. |
| `list_pages(limit=100, under)` | `slug \| title \| edited date`, most recently edited first; `under` lists one folder. |

A task as the tools show it (`_mcp_task_text`) includes `id:`, its 8-character short id (`decksyntax.short_id`), which the task tools take and a page can use as `[[task:…]]`.

### Writing

The wiki is mostly written by LLMs, so they write through Leaf rather than to the files: each write is a git commit (no `/uncommitted` pile), a rename re-points links, and the result tells the model what it would otherwise have to look up. Every write tool takes an optional `summary`; the commit message is the action, then the summary, then `(MCP)` (`_commit_msg`: `Update tickets/bug-1: add findings (MCP)`, `Create deck task (MCP)`), so the history tells the model's changes from the user's. Tools that only add are annotated `destructiveHint: false`, those that change or remove `true`, so a client can ask before those. The token `/mcp` requires (below) guards writes as it guards reads, and git keeps every change undoable.

| Tool | Does |
|------|------|
| `write_page(slug, content, overwrite=false, summary)` | Creates a page. The slug may be a name (`Tickets/BUG 1234` → `tickets/bug-1234`, `to_slug`); folders starting with `_` or `.` are refused (`_new_slug`: Leaf doesn't list pages there). An existing page (`_mcp_page_slug`) is an error that points at edit_page/append_to_page, unless `overwrite`, which replaces its text. Content gets one trailing newline. A new page's result names up to five pages whose title or file name is close (`_similar_pages`: `difflib` ratio ≥ 0.8 on normalised titles and last slug parts), so the model notices when it should extend one instead. |
| `edit_page(slug, old_string, new_string, replace_all=false, summary)` | Exact text replacement, the semantics of a coding agent's edit tool: `old_string` must occur once (else an error with the count) unless `replace_all`; not found is an error pointing at read_page. An empty `new_string` deletes. No read is needed first, so a passage from search_wiki can be edited directly; a concurrent change makes the match fail instead of being overwritten. |
| `append_to_page(slug, content, under_heading, summary)` | Adds at the end of the page, or at the end of the section under a heading (`_append_at`: any level, compared as plain text without case and leading `#`s; the section ends before the next heading of its level or higher, headings in fenced code don't count; subsections belong to it). A missing heading is an error that lists the page's headings. For logs: no read of the page first. |
| `rename_page(slug, new_slug, summary)` | `gitstore.move_page`, as the editor's rename: links on pages and in tasks follow, all in one commit; the result says how many files changed. The home page can't be renamed, an existing target is an error. |
| `delete_page(slug, summary)` | Deletes and commits (restorable from Deleted Pages); names the pages that still link to it. Not the home page. |
| `create_task(title, notes, tags, due, start, repeat, repeat_every, repeat_from, keep, summary)` | A deck task through `deck.create_task`, the same code as `POST /api/deck`. `repeat`: daily/weekly/monthly/yearly/none. |
| `update_task(id, …same fields…, add_tags, remove_tags, status, summary)` | `deck.update_task`, as `PUT /api/deck/<id>`: only the given fields change; an empty `due`/`start`/`notes` clears it. `add_tags`/`remove_tags` work on the current tags (case ignored). `repeat_every`/`repeat_from` alone keep the task's repeat type. `status`: `done` (a keep task goes back to the pool; a repeating one spawns its next occurrence, shown in the result), `wont_do`, `skip` (won't do this occurrence, the series goes on), `open` (reopen; the spawned occurrence goes again). Runs under the deck lock, so add/remove tags see the current tags. |
| `comment_task(id, text, summary)` | `deck.add_comment`: a dated status line. |
| `delete_task(id, summary)` | `deck.delete_task`; the tool's description steers the model to `status` done/wont_do instead. |

Pages are named as in read_page. A task `id` is its id or a prefix of it (`deckstore.find_tasks`: open, completed and archived tasks; an exact id wins; several matches are an error), and a `[[task:…]]` reference or a `/deck?task=…` link work too (`_task_arg`). The deck API's 400s (bad date, empty title, bad interval) come back as tool errors (`_deck_write`).

**Wikilink check**: page writes end with a note naming the `[[wikilinks]]` the write *added* that lead to no page (`_missing_links` before and after, by the renderer's rule: the name as a slug, else slugified; code, `[[toc]]`, `[[deck]]` and `[[task:…]]` aside), so the model fixes a typo or creates the page. Links that were already broken are not repeated.

**Retrieval** (`retrieval.retrieve`) works on sections, not pages: an LLM needs the passages, and the best ones may share a page. BM25 runs over sections (`_page_chunk_docs`, the same `embed.chunk_sections` split, cached per page by mtime; `_chunk_index`), fused with `SEMANTIC.search_chunks` when semantic search is on (sections match by `embed.chunk_key`). Without semantic search it is keyword-only and still works. Deck tasks share that BM25 index (`deckstore.task_search_docs()`, each doc carrying its `task`), so they rank with sections, as in the browser search, and their semantic hits (`SEMANTIC.search_tasks`) join the section hits by similarity. `search_tasks` with a query fuses BM25 and semantic hits within its filter. A task is shown by `_mcp_task_text`: title, a line of facts (status, tags, start, due, repeat, keep, list), the `/deck?task=…` link (`task_href`), its notes and its comments.

**Links** in tool results start with `LEAF_BASE_URL` (e.g. `http://localhost:5000`, Leaf's address as your browser knows it) when set, else with the address the request came in on. A client in a container reaches Leaf as `host.containers.internal`, and links with that host don't open in the browser on the host. **File paths** (`file:`, `_host_file`: search_wiki's sections, read_page) are the host's wiki folder plus `<slug>.md` (a slug is the file's path relative to the wiki, unchanged): `LEAF_HOST_DATA`, which `run.sh` sets to `WIKI_DATA`'s host path, since inside the container the wiki is only `/wiki-data`; unset (no container), `WIKI_DIR` resolved. So a client on the host can open or edit the file directly. Tasks have none.

**Auth**: `/mcp` is exempt from the session login (`check_auth`), since an MCP client can't log in, and checks its own: it needs `LEAF_MCP_TOKEN`, sent as `Authorization: Bearer <token>` (compared in constant time, `_mcp_authorized`). Without a token set, `/mcp` is off: every request gets 403 with a hint to set one, with or without `LEAF_PASSWORD`. The tools write, so an open `/mcp` would let anything that reaches the port change the wiki; a token is never optional. A wrong or missing bearer gets 401 (`WWW-Authenticate: Bearer`). The Host and cross-site Origin checks apply as everywhere.
