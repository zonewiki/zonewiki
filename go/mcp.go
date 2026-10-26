// The MCP (Model Context Protocol) server (src/mcp_server.py): lets an LLM
// client read and write the wiki through tools, at POST /mcp (MCP's
// "Streamable HTTP" transport, answering each request with plain JSON; no
// event stream, no sessions). The tools are in mcptools.go; the tool
// definitions, as clients see them, in mcp_tools.json.
package main

import (
	_ "embed"
	"errors"
	"io"
	"net/http"
	"strings"
)

// Versions this server speaks; it answers with the client's if listed here,
// else with the newest. The tools-only subset used here is the same in all.
var protocolVersions = []string{"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}

// mcpInstructions is short: Claude Code cuts a server's instructions at
// about 2 KB, so what every session needs comes first and the tools' own
// descriptions carry the details. The wiki's guide follows them
// (instructionsWithGuide).
const mcpInstructions = "Leaf is the user's personal wiki: their notes in markdown, plus the Deck, " +
	"their task list. Use search_wiki when an answer may be in what the user " +
	"wrote down (it finds tasks too, and matches meaning as well as words, so " +
	"phrase queries the way the notes might), then read_page for a page's full " +
	"text. search_tasks filters tasks by tag and status; grep_wiki finds every " +
	"occurrence of exact text. Cite pages and tasks by their URL. For questions " +
	"about Leaf itself, call leaf_help.\n\n" +
	"When you research something elsewhere (an issue tracker, chat, mail, code, " +
	"the web) and find a fact that a wiki page on the topic lacks or " +
	"contradicts, offer to add it: name the page and each fact in one line, once " +
	"per answer. Write only when the user agrees.\n\n" +
	"Writing: before creating a page, search for one that covers the topic and " +
	"extend it instead. edit_page and append_to_page change pages, write_page " +
	"creates them, rename_page keeps links working; create_task and update_task " +
	"keep the Deck. Every write is a git commit the user can undo; give each a " +
	"short summary, its commit message. The user's guides (for the wiki, and a " +
	"folder's CLAUDE.md or AGENTS.md) say where things go and how to write them: " +
	"call read_guides before drafting, and pass their ids as guides when writing."

//go:embed mcp_tools.json
var mcpToolsJSON []byte

// mcpTools: the tool definitions, in order, as *Obj (key order kept).
var mcpTools = func() []any {
	v, err := loads(mcpToolsJSON)
	if err != nil {
		panic("mcp_tools.json: " + err.Error())
	}
	return v.([]any)
}()

// toolError is a tool failing in a way the model should see (bad slug, …):
// a result with isError, not a protocol error, so the model can correct
// itself.
type toolError struct{ msg string }

func (e *toolError) Error() string { return e.msg }

func toolErr(msg string) error { return &toolError{msg} }

func rpcError(id any, code int, message string) *Obj {
	return NewObj("jsonrpc", "2.0", "id", id, "error", NewObj("code", code, "message", message))
}

func rpcResult(id any, result *Obj) *Obj {
	return NewObj("jsonrpc", "2.0", "id", id, "result", result)
}

func knownTool(name any) bool {
	for _, t := range mcpTools {
		if t.(*Obj).Get("name") == name {
			return true
		}
	}
	return false
}

// handleMCP answers one JSON-RPC message. Notifications and responses (no
// id, or no method) get nil: the transport answers those with 202 and no
// body. callTool runs a tool, its error a toolError for the model; any
// other error (a failed write) is returned, for a 500.
func handleMCP(message any, callTool func(name string, args *Obj) (string, error)) (*Obj, error) {
	msg, ok := message.(*Obj)
	if !ok || msg.Get("jsonrpc") != "2.0" {
		return rpcError(nil, -32600, "Invalid Request"), nil
	}
	method, ok := msg.Get("method").(string)
	id := msg.Get("id")
	if !ok {
		return nil, nil // a response to something we never asked
	}
	if !msg.Has("id") {
		return nil, nil // a notification, e.g. notifications/initialized
	}
	params := NewObj()
	if truthyValue(msg.Get("params")) {
		if params, ok = msg.Get("params").(*Obj); !ok {
			return rpcError(id, -32602, "params must be an object"), nil
		}
	}
	switch method {
	case "initialize":
		protocol := protocolVersions[0]
		for _, v := range protocolVersions {
			if params.Get("protocolVersion") == v {
				protocol = v
			}
		}
		return rpcResult(id, NewObj(
			"protocolVersion", protocol,
			"capabilities", NewObj("tools", NewObj("listChanged", false)),
			"serverInfo", NewObj("name", "leaf", "title", "Leaf wiki", "version", version),
			"instructions", instructionsWithGuide())), nil
	case "ping":
		return rpcResult(id, NewObj()), nil
	case "tools/list":
		return rpcResult(id, NewObj("tools", toolList())), nil
	case "tools/call":
		name := params.Get("name")
		args := NewObj()
		if truthyValue(params.Get("arguments")) {
			args, ok = params.Get("arguments").(*Obj)
		}
		if !knownTool(name) {
			return rpcError(id, -32602, "Unknown tool: "+pyStr(name)), nil
		}
		if !ok {
			return rpcError(id, -32602, "arguments must be an object"), nil
		}
		text, err := callTool(name.(string), args)
		isError := false
		if err != nil {
			var te *toolError
			if !errors.As(err, &te) {
				return nil, err
			}
			text, isError = te.msg, true
		}
		return rpcResult(id, NewObj("content", []any{NewObj("type", "text", "text", text)}, "isError", isError)), nil
	}
	return rpcError(id, -32601, "Method not found: "+method), nil
}

// mcpAuthorized: the request carries LEAF_MCP_TOKEN as a bearer token. The
// tools write (pages, tasks), so /mcp is off until a token is set: open, it
// would let anything that reaches the port change the wiki, password or not.
func mcpAuthorized(r *http.Request) bool {
	auth := r.Header.Get("Authorization")
	return cfg.MCPToken != "" && strings.HasPrefix(auth, "Bearer ") &&
		sameSecret(pyStrip(auth[len("Bearer "):]), cfg.MCPToken)
}

// mcpEndpoint: MCP over HTTP, one JSON-RPC message (or a batch) per POST,
// answered with JSON. GET (a server-sent event stream) and DELETE (ending
// a session) aren't offered: there are no notifications and no sessions.
func mcpEndpoint(w http.ResponseWriter, r *http.Request) {
	if cfg.MCPToken == "" {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "MCP is off: set LEAF_MCP_TOKEN in .env and send it " +
			"as 'Authorization: Bearer <token>'"})
		return
	}
	// Wrong tokens count against the client like wrong passwords (their own
	// throttle: a client's MCP mistakes don't lock its browser out).
	key := clientKey(r)
	if wait := mcpThrottle.wait(key); wait > 0 {
		w.Header().Set("Retry-After", itoa(int(wait.Seconds())+1))
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": waitMessage(wait)})
		return
	}
	if !mcpAuthorized(r) {
		mcpThrottle.failed(key)
		w.Header().Set("WWW-Authenticate", "Bearer")
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "Unauthorized: wrong or missing bearer token"})
		return
	}
	mcpThrottle.succeeded(key)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, _ := io.ReadAll(r.Body)
	message, err := loads(body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, rpcError(nil, -32700, "Parse error"))
		return
	}
	call := func(name string, args *Obj) (string, error) { return callMCPTool(r, name, args) }
	if batch, ok := message.([]any); ok { // a batch (protocol 2025-03-26)
		replies := []any{}
		for _, m := range batch {
			reply, err := handleMCP(m, call)
			if err != nil {
				serverError(w, r, err)
				return
			}
			if reply != nil {
				replies = append(replies, reply)
			}
		}
		if len(replies) == 0 {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		writeJSON(w, http.StatusOK, replies)
		return
	}
	reply, err := handleMCP(message, call)
	if err != nil {
		serverError(w, r, err)
		return
	}
	if reply != nil {
		writeJSON(w, http.StatusOK, reply)
		return
	}
	w.WriteHeader(http.StatusAccepted)
}
