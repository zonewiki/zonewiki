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
	"zonewiki/internal/jsonobj"
)

// Versions this server speaks; it answers with the client's if listed here,
// else with the newest. The tools-only subset used here is the same in all.
var protocolVersions = []string{"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}

// mcpInstructions is short: Claude Code cuts a server's instructions at
// about 2 KB, so what every session needs comes first and the tools' own
// descriptions carry the details. The wiki's guide follows them
// (instructionsWithGuide).
const mcpInstructions = "Zone is the user's personal wiki: their pages in markdown, plus Tasks, " +
	"their task list, and Notes, quick notes not yet filed into the wiki. Use search_wiki when an answer may be in what the user " +
	"wrote down (it finds tasks too, and matches meaning as well as words, so " +
	"phrase queries the way the notes might), then read_page for a page's full " +
	"text. search_tasks filters tasks by tag and status; grep_wiki finds every " +
	"occurrence of exact text. Cite pages and tasks by their URL. For questions " +
	"about Zone itself, call zone_help.\n\n" +
	"When you research something elsewhere (an issue tracker, chat, mail, code, " +
	"the web) and find a fact that a wiki page on the topic lacks or " +
	"contradicts, offer to add it: name the page and each fact in one line, once " +
	"per answer. Write only when the user agrees.\n\n" +
	"Writing: before creating a page, search for one that covers the topic and " +
	"extend it instead. edit_page and append_to_page change pages, write_page " +
	"creates them, rename_page keeps links working; create_task and update_task " +
	"keep their tasks. Every write is a git commit the user can undo; give each a " +
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

// toolContent: a part of a tool's answer: text, or an image (read_file),
// its bytes base64 in Data.
type toolContent struct {
	Type     string
	Text     string
	Data     string
	MimeType string
}

func (c toolContent) MarshalJSON() ([]byte, error) {
	if c.Type == "image" {
		return json.Marshal(struct {
			Type     string `json:"type"`
			Data     string `json:"data"`
			MimeType string `json:"mimeType"`
		}{c.Type, c.Data, c.MimeType})
	}
	return json.Marshal(struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}{c.Type, c.Text})
}

// textContent: a tool's answer of text alone.
func textContent(text string) []toolContent { return []toolContent{{Type: "text", Text: text}} }

type toolResult struct {
	Content []toolContent `json:"content"`
	IsError bool          `json:"isError"`
}

// knownTool: a tool tools/list shows.
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
func handleMCP(message json.RawMessage, user asker, callTool func(name string, args jsonObj) ([]toolContent, error)) (*rpcResponse, error) {
	msg, ok := jsonobj.Parse(message)
	if v, _ := jsonobj.As[string](msg["jsonrpc"]); !ok || v != "2.0" {
		return rpcError(nil, -32600, "Invalid Request"), nil
	}
	method, ok := jsonobj.As[string](msg["method"])
	id, hasID := msg["id"]
	if !ok {
		return nil, nil // a response to something we never asked
	}
	if !hasID {
		return nil, nil // a notification, e.g. notifications/initialized
	}
	params := jsonObj{}
	if !msg.Null("params") {
		if params, ok = jsonobj.Parse(msg["params"]); !ok {
			return rpcError(id, -32602, "params must be an object"), nil
		}
	}
	switch method {
	case "initialize":
		var res initializeResult
		res.ProtocolVersion = protocolVersions[0]
		if v, _ := jsonobj.As[string](params["protocolVersion"]); slices.Contains(protocolVersions, v) {
			res.ProtocolVersion = v
		}
		res.ServerInfo.Name, res.ServerInfo.Title, res.ServerInfo.Version = "zonewiki", "Zone wiki", version
		res.Instructions = instructionsWithGuide(user)
		return rpcResult(id, res), nil
	case "ping":
		return rpcResult(id, struct{}{}), nil
	case "tools/list":
		return rpcResult(id, map[string]any{"tools": toolList(user)}), nil
	case "tools/call":
		name, isStr := jsonobj.As[string](params["name"])
		if !isStr || !knownTool(name) {
			if !isStr {
				name = string(params["name"])
			}
			return rpcError(id, -32602, "Unknown tool: "+name), nil
		}
		args := jsonObj{}
		if !params.Null("arguments") {
			if args, ok = jsonobj.Parse(params["arguments"]); !ok {
				return rpcError(id, -32602, "arguments must be an object"), nil
			}
		}
		content, err := callTool(name, args)
		isError := false
		if err != nil {
			var te *toolError
			var ce *writeError
			switch {
			case errors.As(err, &te):
				content = textContent(te.msg)
			case errors.As(err, &ce):
				content = textContent(ce.Error())
			default:
				return nil, err
			}
			isError = true
		}
		return rpcResult(id, toolResult{content, isError}), nil
	}
	return rpcError(id, -32601, "Method not found: "+method), nil
}

// mcpAuthorized: the request carries the wiki's MCP token as a bearer
// token, or with users one user's token (configfile.go): the tools act
// as that user. The tools write (pages, tasks), so /mcp is off until a
// token is set: open, it would let anything that reaches the port change
// the wiki, password or not. c: the connection the token is of (none for
// an MCP token): its name, for what it writes (viaTrailer), and what its
// user gave it less than they have themselves.
func mcpAuthorized(r *http.Request) (user string, c connection, ok bool) {
	auth := r.Header.Get("Authorization")
	if !strings.HasPrefix(auth, "Bearer ") {
		return "", connection{}, false
	}
	given := strings.TrimSpace(auth[len("Bearer "):])
	s := settings()
	if s.Users == nil {
		if tokenMatches(given, s.MCPToken) {
			return "", connection{}, true
		}
		return connectionUser(s, given)
	}
	for _, u := range s.Users {
		if !u.Disabled && tokenMatches(given, u.MCPToken) {
			return u.Name, connection{}, true
		}
	}
	return connectionUser(s, given)
}

// connectionUser: given is the access token of a connection (grants.go),
// whose user may still have one.
func connectionUser(s *Settings, given string) (user string, c connection, ok bool) {
	if !oauthOn(s) {
		return "", connection{}, false
	}
	if g := grantOf(given); g != nil && validGrantUser(s, g.User) {
		return g.User, connection{name: g.Client, readOnly: g.ReadOnly}, true
	}
	return "", connection{}, false
}

// mcpOn: some token is set, so /mcp answers.
func mcpOn() bool {
	s := settings()
	if s.Users == nil {
		return s.MCPToken != ""
	}
	for _, u := range s.Users {
		if u.MCPToken != "" && !u.Disabled {
			return true
		}
	}
	return false
}

// mcpEndpoint: MCP over HTTP, one JSON-RPC message (or a batch) per POST,
// answered with JSON. GET (a server-sent event stream) and DELETE (ending
// a session) aren't offered: there are no notifications and no sessions.
func mcpEndpoint(w http.ResponseWriter, r *http.Request) {
	// With OAuth a client that sends no token is told where to get one (a
	// 401 naming the metadata, RFC 9728), token set or not.
	challenge := "Bearer"
	if s := settings(); oauthOn(s) {
		challenge = `Bearer resource_metadata="` + resourceMetaURL(s, r) + `", scope="` + oauthScope + `"`
	} else if !mcpOn() {
		msg := "MCP is off: make a token at /assistant in the browser (or with ./run.sh config token) and send it as " +
			"'Authorization: Bearer <token>'"
		if settings().Users != nil {
			msg = "MCP is off: a user makes their token at /assistant in the browser (or ./run.sh user token NAME) and sends it as " +
				"'Authorization: Bearer <token>'"
		}
		writeJSON(w, http.StatusForbidden, map[string]string{"error": msg})
		return
	}
	// Wrong tokens count against the client like wrong passwords (their own
	// throttle: a client's MCP mistakes don't lock its browser out). No
	// token at all is no guess: a client finding out how to sign in.
	if r.Header.Get("Authorization") == "" {
		w.Header().Set("WWW-Authenticate", challenge)
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "Unauthorized: wrong or missing bearer token"})
		return
	}
	user, conn := "", connection{}
	ok, wait := mcpThrottle.try(clientKey(r), func() (ok bool) {
		user, conn, ok = mcpAuthorized(r)
		return ok
	})
	if wait > 0 {
		w.Header().Set("Retry-After", itoa(int(wait.Seconds())+1))
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": waitMessage(wait)})
		return
	}
	if !ok {
		w.Header().Set("WWW-Authenticate", challenge+`, error="invalid_token"`)
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "Unauthorized: wrong or missing bearer token"})
		return
	}
	r = withConnection(withUser(r, user), conn)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(r.Body)
	if tooLarge(err) {
		tooLargeError(w, bodyMax)
		return
	}
	if !json.Valid(body) {
		writeJSON(w, http.StatusBadRequest, rpcError(nil, -32700, "Parse error"))
		return
	}
	call := func(name string, args jsonObj) ([]toolContent, error) { return callMCPTool(r, name, args) }
	if batch, ok := jsonobj.As[[]json.RawMessage](body); ok { // a batch (protocol 2025-03-26)
		replies := []*rpcResponse{}
		for _, m := range batch {
			reply, err := handleMCP(m, userOf(r), call)
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
	reply, err := handleMCP(body, userOf(r), call)
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
