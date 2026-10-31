// The MCP (Model Context Protocol) server: lets an LLM
// client read and write the wiki through tools, at POST /mcp (MCP's
// "Streamable HTTP" transport, answering each request with plain JSON; no
// event stream, no sessions). The tools are in mcptools.go; the tool
// definitions, as clients see them, in mcp_tools.json.
package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"slices"
	"strings"
)

// Versions this server speaks; it answers with the client's if listed here,
// else with the newest. The tools-only subset used here is the same in all.
var protocolVersions = []string{"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}

// mcpInstructions is short: Claude Code cuts a server's instructions at
// about 2 KB, so what every session needs comes first and the tools' own
// descriptions carry the details. The wiki's guide follows them
// (instructionsWithGuide).
const mcpInstructions = "Zone is the user's personal wiki: their notes in markdown, plus the Deck, " +
	"their task list. Use search_wiki when an answer may be in what the user " +
	"wrote down (it finds tasks too, and matches meaning as well as words, so " +
	"phrase queries the way the notes might), then read_page for a page's full " +
	"text. search_tasks filters tasks by tag and status; grep_wiki finds every " +
	"occurrence of exact text. Cite pages and tasks by their URL. For questions " +
	"about Zone itself, call leaf_help.\n\n" +
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

// mcpTools: the tool definitions, in order.
var mcpTools = func() []map[string]any {
	var tools []map[string]any
	if err := json.Unmarshal(mcpToolsJSON, &tools); err != nil {
		panic("mcp_tools.json: " + err.Error())
	}
	return tools
}()

// toolError is a tool failing in a way the model should see (bad slug, …):
// a result with isError, not a protocol error, so the model can correct
// itself.
type toolError struct{ msg string }

func (e *toolError) Error() string { return e.msg }

func toolErr(msg string) error { return &toolError{msg} }

// rpcResponse: a JSON-RPC answer, its id the request's (null when it had
// none that could be read).
type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcFault       `json:"error,omitempty"`
}

type rpcFault struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func rpcError(id json.RawMessage, code int, message string) *rpcResponse {
	return &rpcResponse{JSONRPC: "2.0", ID: id, Error: &rpcFault{code, message}}
}

func rpcResult(id json.RawMessage, result any) *rpcResponse {
	return &rpcResponse{JSONRPC: "2.0", ID: id, Result: result}
}

type initializeResult struct {
	ProtocolVersion string `json:"protocolVersion"`
	Capabilities    struct {
		Tools struct {
			ListChanged bool `json:"listChanged"`
		} `json:"tools"`
	} `json:"capabilities"`
	ServerInfo struct {
		Name    string `json:"name"`
		Title   string `json:"title"`
		Version string `json:"version"`
	} `json:"serverInfo"`
	Instructions string `json:"instructions"`
}

type toolContent struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type toolResult struct {
	Content []toolContent `json:"content"`
	IsError bool          `json:"isError"`
}

func knownTool(name string) bool {
	for _, t := range mcpTools {
		if t["name"] == name {
			return true
		}
	}
	return false
}

// handleMCP answers one JSON-RPC message. Notifications and responses (no
// id, or no method) get nil: the transport answers those with 202 and no
// body. callTool runs a tool, its error a toolError for the model; any
// other error (a failed write) is returned, for a 500.
func handleMCP(message json.RawMessage, user string, callTool func(name string, args jsonObj) (string, error)) (*rpcResponse, error) {
	msg, ok := parseObj(message)
	if v, _ := as[string](msg["jsonrpc"]); !ok || v != "2.0" {
		return rpcError(nil, -32600, "Invalid Request"), nil
	}
	method, ok := as[string](msg["method"])
	id, hasID := msg["id"]
	if !ok {
		return nil, nil // a response to something we never asked
	}
	if !hasID {
		return nil, nil // a notification, e.g. notifications/initialized
	}
	params := jsonObj{}
	if !msg.null("params") {
		if params, ok = parseObj(msg["params"]); !ok {
			return rpcError(id, -32602, "params must be an object"), nil
		}
	}
	switch method {
	case "initialize":
		var res initializeResult
		res.ProtocolVersion = protocolVersions[0]
		if v, _ := as[string](params["protocolVersion"]); slices.Contains(protocolVersions, v) {
			res.ProtocolVersion = v
		}
		res.ServerInfo.Name, res.ServerInfo.Title, res.ServerInfo.Version = "leaf", "Zone wiki", version
		res.Instructions = instructionsWithGuide()
		return rpcResult(id, res), nil
	case "ping":
		return rpcResult(id, struct{}{}), nil
	case "tools/list":
		return rpcResult(id, map[string]any{"tools": toolList(user)}), nil
	case "tools/call":
		name, isStr := as[string](params["name"])
		if !isStr || !knownTool(name) {
			if !isStr {
				name = string(params["name"])
			}
			return rpcError(id, -32602, "Unknown tool: "+name), nil
		}
		args := jsonObj{}
		if !params.null("arguments") {
			if args, ok = parseObj(params["arguments"]); !ok {
				return rpcError(id, -32602, "arguments must be an object"), nil
			}
		}
		text, err := callTool(name, args)
		isError := false
		if err != nil {
			var te *toolError
			if !errors.As(err, &te) {
				return nil, err
			}
			text, isError = te.msg, true
		}
		return rpcResult(id, toolResult{[]toolContent{{"text", text}}, isError}), nil
	}
	return rpcError(id, -32601, "Method not found: "+method), nil
}

// mcpAuthorized: the request carries the wiki's MCP token as a bearer
// token, or with users one user's token (configfile.go): the tools act
// as that user. The tools write (pages, tasks), so /mcp is off until a
// token is set: open, it would let anything that reaches the port change
// the wiki, password or not.
func mcpAuthorized(r *http.Request) (user string, ok bool) {
	auth := r.Header.Get("Authorization")
	if !strings.HasPrefix(auth, "Bearer ") {
		return "", false
	}
	given := strings.TrimSpace(auth[len("Bearer "):])
	s := settings()
	if s.Users == nil {
		return "", tokenMatches(given, s.MCPToken)
	}
	for _, u := range s.Users {
		if tokenMatches(given, u.MCPToken) {
			return u.Name, true
		}
	}
	return "", false
}

// mcpOn: some token is set, so /mcp answers.
func mcpOn() bool {
	s := settings()
	if s.Users == nil {
		return s.MCPToken != ""
	}
	for _, u := range s.Users {
		if u.MCPToken != "" {
			return true
		}
	}
	return false
}

// mcpEndpoint: MCP over HTTP, one JSON-RPC message (or a batch) per POST,
// answered with JSON. GET (a server-sent event stream) and DELETE (ending
// a session) aren't offered: there are no notifications and no sessions.
func mcpEndpoint(w http.ResponseWriter, r *http.Request) {
	if !mcpOn() {
		msg := "MCP is off: make a token with ./run.sh config token and send it as " +
			"'Authorization: Bearer <token>'"
		if settings().Users != nil {
			msg = "MCP is off: give a user a token with ./run.sh user token NAME and send it as " +
				"'Authorization: Bearer <token>'"
		}
		writeJSON(w, http.StatusForbidden, map[string]string{"error": msg})
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
	user, ok := mcpAuthorized(r)
	if !ok {
		mcpThrottle.failed(key)
		w.Header().Set("WWW-Authenticate", "Bearer")
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "Unauthorized: wrong or missing bearer token"})
		return
	}
	mcpThrottle.succeeded(key)
	r = withUser(r, user)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, _ := io.ReadAll(r.Body)
	if !json.Valid(body) {
		writeJSON(w, http.StatusBadRequest, rpcError(nil, -32700, "Parse error"))
		return
	}
	call := func(name string, args jsonObj) (string, error) { return callMCPTool(r, name, args) }
	if batch, ok := as[[]json.RawMessage](body); ok { // a batch (protocol 2025-03-26)
		replies := []*rpcResponse{}
		for _, m := range batch {
			reply, err := handleMCP(m, user, call)
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
	reply, err := handleMCP(body, user, call)
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
