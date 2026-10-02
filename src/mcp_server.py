"""MCP (Model Context Protocol) server: lets an LLM client
read the wiki through tools. Served by server.py at POST /mcp (MCP's
"Streamable HTTP" transport, answering each request with plain JSON; no
event stream, no sessions). Read-only: the tools search and read pages.

This module is the protocol only (JSON-RPC in, JSON-RPC out); server.py
does authentication and implements the tools through call_tool.

Clients register http://localhost:5000/mcp with the HTTP transport.
"""

from typing import Callable

# Versions this server speaks; it answers with the client's if listed here,
# else with the newest. The tools-only subset used here is the same in all.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Leaf is the user's personal wiki: their notes, in markdown. Use search_wiki "
    "when a question may be answered by something the user wrote down, then "
    "read_page for a page's full text. Search finds by meaning as well as by "
    "keyword, so phrase queries the way the notes might. Cite pages by their URL."
)

_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "search_wiki",
        "title": "Search the wiki",
        "description": (
            "Search the user's wiki and return the most relevant sections with "
            "their full text, best first, each with its page and a link. Several "
            "sections may come from the same page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for: words or a question."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8,
                          "description": "How many sections to return."},
            },
            "required": ["query"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "read_page",
        "title": "Read a page",
        "description": "Return a wiki page's full markdown, frontmatter included, by its slug.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string",
                         "description": "The page's slug as search_wiki or list_pages give it, e.g. 'git-remote' or 'journal/2026-09'."},
            },
            "required": ["slug"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "list_pages",
        "title": "List pages",
        "description": "List the wiki's pages (slug, title, last edited), most recently edited first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            },
        },
        "annotations": _READ_ONLY,
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


def handle(message, call_tool: Callable[[str, dict], str], version: str) -> dict | None:
    """Answer one JSON-RPC message. Notifications and responses (no id, or
    no method) get None: the transport answers those with 202 and no body."""
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
            "instructions": INSTRUCTIONS,
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
