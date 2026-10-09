# MCP server

Part of Leaf's spec (see `AGENTS.md` for the index).

## MCP server

`POST /mcp` lets an LLM client (any MCP client) read the wiki and the deck through tools. Protocol in `src/mcp_server.py` (JSON-RPC, no Flask), route in `server.py` (`mcp_endpoint`), tools in `src/mcp_tools.py` (`mcp_call_tool`). MCP's Streamable HTTP transport, reduced to what tools need: each POST carries one message (or a batch) and is answered with plain JSON; notifications get 202; no event stream (GET → 405), no sessions. `initialize` echoes the client's protocol version when it is in `PROTOCOL_VERSIONS`, else the newest, and sends `instructions` telling the model what Leaf is.

Register `http://localhost:5000/mcp` with your MCP client (HTTP transport) and the header `Authorization: Bearer <LEAF_MCP_TOKEN>`.

Tools, all read-only (`readOnlyHint`). A tool failure the model can fix (bad slug, empty query) is a result with `isError`, not a protocol error:

| Tool | Does |
|------|------|
| `search_wiki(query, limit=8, under)` | The most relevant **sections**, full text, best first, each with title › heading, slug, the page's last edit date (`edited:`, so the model can tell current notes from stale ones), a URL with the heading anchor and the page's file on the host (`file:`, see Links). Several may come from one page. Matching deck tasks are results too, ranked with the sections. `under` restricts it to a folder: pages whose slug is that prefix or below it (`in_folder`, case ignored; `tickets`, `tickets/` and `/page/tickets` alike, `_clean_folder`), and no tasks. A folder without pages is a tool error, so the model corrects it. |
| `search_tasks(tag, status="open", query, limit=50)` | Deck tasks filtered by tag (comma = all must match, case-insensitive; a list is its tag) and status (`open`, `done` = completed or won't do, `all`), separate lists included. With a query, ranked by BM25 within the filter; without, open tasks by deadline, then start date, then title, done ones newest first. Starts with "Today is …" so the model can tell overdue from upcoming. |
| `read_page(slug)` | A page's slug, URL and host file (`file:`), then its raw markdown, frontmatter included, plus a list of the deck tasks its `[[task:…]]` references stand for. Accepts a slug, `/page/<slug>`, a full URL, `<slug>.md` or the page name (`_mcp_page_slug`); `safe_path` keeps it inside the wiki. |
| `list_pages(limit=100, under)` | `slug \| title \| edited date`, most recently edited first; `under` lists one folder. |

**Retrieval** (`retrieval.retrieve`) works on sections, not pages: an LLM needs the passages, and the best ones may share a page. BM25 runs over sections (`_page_chunk_docs`, the same `embed.chunk_sections` split, cached per page by mtime; `_chunk_index`), fused with `SEMANTIC.search_chunks` when semantic search is on (sections match by `embed.chunk_key`). Without semantic search it is keyword-only and still works. Deck tasks share that BM25 index (`deckstore.task_search_docs()`, each doc carrying its `task`), so they rank with sections, as in the browser search, and their semantic hits (`SEMANTIC.search_tasks`) join the section hits by similarity. `search_tasks` with a query fuses BM25 and semantic hits within its filter. A task is shown by `_mcp_task_text`: title, a line of facts (status, tags, start, due, repeat, keep, list), the `/deck?task=…` link (`task_href`), its notes and its comments.

**Links** in tool results start with `LEAF_BASE_URL` (e.g. `http://localhost:5000`, Leaf's address as your browser knows it) when set, else with the address the request came in on. A client in a container reaches Leaf as `host.containers.internal`, and links with that host don't open in the browser on the host. **File paths** (`file:`, `_host_file`: search_wiki's sections, read_page) are the host's wiki folder plus `<slug>.md` (a slug is the file's path relative to the wiki, unchanged): `LEAF_HOST_DATA`, which `run.sh` sets to `WIKI_DATA`'s host path, since inside the container the wiki is only `/wiki-data`; unset (no container), `WIKI_DIR` resolved. So a client on the host can open or edit the file directly. Tasks have none.

**Auth**: `/mcp` is exempt from the session login (`check_auth`), since an MCP client can't log in, and checks its own: with `LEAF_MCP_TOKEN` set, every request needs `Authorization: Bearer <token>`; without one, `/mcp` is as open as the pages are, so a wiki with `LEAF_PASSWORD` keeps it closed (401) until a token is set. The Host and cross-site Origin checks apply as everywhere.
