// The OAuth log (docs/spec/mcp.md, The OAuth log): with oauth-log = yes in
// the settings file, Zonewiki writes a line to its log (stderr: the journal,
// ./run.sh logs) for each request an MCP client's connect makes: /mcp, the
// metadata under /.well-known/, the OAuth routes, and the login on the way
// to Allow. It shows what a client sent and what Zonewiki answered, for a
// client that fails to connect, which nothing else shows (a proxy in front
// often keeps no access log). Off by default.
//
// It names no secret: a value is written only for the parameters known to
// carry none (oauthLogShown); a code, a code_verifier, a client_secret, a
// token, a password, a state, and whatever a client sends that Zonewiki
// doesn't know are written as their length alone, the Authorization header
// as its scheme, cookies as their names. Nothing serves the log.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"time"
	"unicode/utf8"
)

// oauthLogged: the request is one a connect makes. The root's /authorize,
// /token and /register are where a client looks that didn't find the
// metadata.
func oauthLogged(r *http.Request) bool {
	switch p := r.URL.Path; {
	case p == "/mcp", strings.HasPrefix(p, "/mcp/"), strings.HasPrefix(p, "/oauth/"), strings.HasPrefix(p, "/.well-known/"),
		p == "/authorize", p == "/token", p == "/register":
		return true
	case p == "/login":
		return strings.HasPrefix(r.URL.Query().Get("next"), "/oauth/authorize")
	}
	return false
}

// oauthLogShown: the parameters (of a query, a form, a JSON body, a
// redirect's address) whose values the log writes: what a client asks and
// is told, none of it a secret. Any other is written as its length.
var oauthLogShown = map[string]bool{
	"response_type": true, "response_mode": true, "client_id": true, "client_name": true,
	"redirect_uri": true, "redirect_uris": true, "code_challenge": true, "code_challenge_method": true,
	"scope": true, "resource": true, "audience": true, "prompt": true,
	"grant_type": true, "grant_types": true, "response_types": true, "token_endpoint_auth_method": true,
	"token_type_hint": true, "application_type": true, "client_uri": true, "logo_uri": true,
	"software_id": true, "software_version": true, "iss": true, "error": true, "error_description": true,
	"next": true, "answer": true, "may": true, // the login's way back; Allow or Deny, and what it may do
	"jsonrpc": true, "method": true, "id": true, // a message to /mcp
}

// oauthLogURLs: the shown parameters that hold an address, whose own
// query is written by the same rule (a login's next holds the question).
var oauthLogURLs = map[string]bool{"redirect_uri": true, "redirect_uris": true, "next": true, "client_uri": true,
	"logo_uri": true, "resource": true, "iss": true}

// oauthLogMost: the longest value written, in bytes; oauthLogBodyMost:
// how much of a body is read to name its fields.
const (
	oauthLogMost     = 300
	oauthLogBodyMost = 64 << 10
)

// oauthLogWrite writes a line (a test reads it instead).
var oauthLogWrite = func(line []byte) { logf("oauth-log %s", line) }

// oauthLogLine: a request and its answer, as the log writes them (JSON:
// a value can't break the line).
type oauthLogLine struct {
	Time    string         `json:"time"`
	Method  string         `json:"method"`
	Path    string         `json:"path"`
	Query   map[string]any `json:"query,omitempty"`
	Headers map[string]any `json:"headers,omitempty"`
	Body    *oauthLogBody  `json:"body,omitempty"`
	Status  int            `json:"status"`
	Answer  map[string]any `json:"answer,omitempty"`
	Millis  int64          `json:"ms"`
}

// oauthLogBody: a request's body: its size, its fields (a form's, a JSON
// object's), and for /mcp the JSON-RPC methods it calls.
type oauthLogBody struct {
	Bytes  int64          `json:"bytes"`
	Fields map[string]any `json:"fields,omitempty"`
	RPC    []string       `json:"rpc,omitempty"`
}

// oauthLog writes the OAuth log's line for each request oauthLogged names,
// while oauth-log is on.
func oauthLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !oauthLogged(r) {
			next.ServeHTTP(w, r)
			return
		}
		// A change to the file counts from this request on (checks would
		// take it only after): a connect's first request matters most.
		reloadSettings()
		if !settings().OAuthLog {
			next.ServeHTTP(w, r)
			return
		}
		start := time.Now()
		line := &oauthLogLine{Time: now().UTC().Format("2006-01-02T15:04:05.000Z"), Method: r.Method,
			Path: logCut(r.URL.Path), Query: oauthLogValues(r.URL.Query()), Headers: oauthLogHeaders(r)}
		line.Body = oauthLogBodyOf(r)
		rec := &oauthLogWriter{ResponseWriter: w}
		next.ServeHTTP(rec, r)
		if rec.status == 0 {
			rec.status, rec.header = http.StatusOK, w.Header().Clone()
		}
		line.Status, line.Answer, line.Millis = rec.status, oauthLogAnswer(rec), time.Since(start).Milliseconds()
		var b bytes.Buffer
		enc := json.NewEncoder(&b)
		enc.SetEscapeHTML(false) // an address's & stays one
		if enc.Encode(line) == nil {
			oauthLogWrite(bytes.TrimSuffix(b.Bytes(), []byte("\n")))
		}
	})
}

// logCut: s at most oauthLogMost bytes, whole characters.
func logCut(s string) string {
	if len(s) <= oauthLogMost {
		return s
	}
	end := oauthLogMost
	for end > 0 && !utf8.RuneStart(s[end]) {
		end--
	}
	return s[:end] + "…"
}

// logHidden: what the log writes for a value it doesn't show.
func logHidden(v string) string { return fmt.Sprintf("(hidden, %d bytes)", len(v)) }

// oauthLogValue: a parameter's value as the log writes it.
func oauthLogValue(key, v string) string {
	switch {
	case v == "":
		return ""
	case !oauthLogShown[key]:
		return logHidden(v)
	case oauthLogURLs[key]:
		return oauthLogURL(v, 1)
	}
	return logCut(v)
}

// oauthLogValues: a query's or a form's parameters as the log writes them:
// one value as a string, several as a list.
func oauthLogValues(vals url.Values) map[string]any {
	if len(vals) == 0 {
		return nil
	}
	out := map[string]any{}
	for key, vs := range vals {
		shown := make([]string, len(vs))
		for i, v := range vs {
			shown[i] = oauthLogValue(key, v)
		}
		out[logCut(key)] = shown
		if len(shown) == 1 {
			out[logCut(key)] = shown[0]
		}
	}
	return out
}

// oauthLogURL: an address with its query's values by the rule above (a
// redirect's code hidden), its fragment and a user in it hidden. depth:
// how deep the addresses in it are looked into too (a login's
// next=/oauth/authorize?…&redirect_uri=…); deeper, they are hidden.
func oauthLogURL(raw string, depth int) string {
	u, err := url.Parse(raw)
	if err != nil {
		return logHidden(raw)
	}
	if u.User != nil {
		u.User = url.User("hidden")
	}
	frag := ""
	if u.Fragment != "" || u.RawFragment != "" {
		frag = "#" + logHidden(u.EscapedFragment())
	}
	u.Fragment, u.RawFragment = "", ""
	query := u.RawQuery
	u.RawQuery = ""
	if query == "" {
		return logCut(u.String() + frag)
	}
	var pairs []string
	for pair := range strings.SplitSeq(query, "&") {
		k, v, _ := strings.Cut(pair, "=")
		key, err1 := url.QueryUnescape(k)
		value, err2 := url.QueryUnescape(v)
		switch {
		case err1 != nil || err2 != nil:
			pairs = append(pairs, logHidden(pair))
		case oauthLogShown[key] && oauthLogURLs[key] && depth > 0:
			pairs = append(pairs, k+"="+oauthLogURL(value, depth-1))
		case oauthLogShown[key] && !oauthLogURLs[key]:
			pairs = append(pairs, k+"="+v)
		case value == "":
			pairs = append(pairs, pair)
		default:
			pairs = append(pairs, k+"="+logHidden(value))
		}
	}
	return logCut(u.String() + "?" + strings.Join(pairs, "&") + frag)
}

// oauthLogRequestHeaders: the request's headers the log writes as they are.
var oauthLogRequestHeaders = []string{"User-Agent", "Origin", "Content-Type", "Accept", "MCP-Protocol-Version"}

// authSchemes: the schemes an Authorization header names, which the log
// writes; anything else could be the credential itself.
var authSchemes = []string{"basic", "bearer", "dpop", "digest", "negotiate", "token"}

// oauthLogHeaders: the request's headers as the log writes them.
func oauthLogHeaders(r *http.Request) map[string]any {
	h := map[string]any{"Host": logCut(r.Host)}
	for _, name := range oauthLogRequestHeaders {
		if v := r.Header.Values(name); len(v) > 0 {
			h[name] = logCut(strings.Join(v, ", "))
		}
	}
	if v := r.Header.Get("Referer"); v != "" {
		h["Referer"] = oauthLogURL(v, 2)
	}
	if v := r.Header.Get("Mcp-Session-Id"); v != "" {
		h["Mcp-Session-Id"] = logHidden(v)
	}
	if v := r.Header.Get("Authorization"); v != "" {
		scheme, credential, _ := strings.Cut(strings.TrimSpace(v), " ")
		if slices.Contains(authSchemes, strings.ToLower(scheme)) {
			h["Authorization"] = scheme + " " + logHidden(strings.TrimSpace(credential))
		} else {
			h["Authorization"] = logHidden(v)
		}
	}
	if cookies := r.Cookies(); len(cookies) > 0 {
		var names []string
		for _, c := range cookies {
			names = append(names, logCut(c.Name))
		}
		h["Cookie"] = "names: " + strings.Join(names, ", ")
	}
	return h
}

// oauthLogBodyOf: the body's size and fields, read up to oauthLogBodyMost
// and put back for the route.
func oauthLogBodyOf(r *http.Request) *oauthLogBody {
	if r.Body == nil || r.Body == http.NoBody {
		return nil
	}
	head, err := io.ReadAll(io.LimitReader(r.Body, oauthLogBodyMost))
	r.Body = readCloser{io.MultiReader(bytes.NewReader(head), r.Body), r.Body}
	if err != nil || len(head) == 0 {
		return nil
	}
	b := &oauthLogBody{Bytes: r.ContentLength}
	if b.Bytes < 0 {
		b.Bytes = int64(len(head))
	}
	if mt, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type")); mt == "application/x-www-form-urlencoded" {
		if vals, err := url.ParseQuery(string(head)); err == nil {
			b.Fields = oauthLogValues(vals)
		}
		return b
	}
	var v any
	if json.Unmarshal(head, &v) != nil {
		return b // not JSON, or longer than what was read: its size alone
	}
	if obj, ok := v.(map[string]any); ok {
		b.Fields = map[string]any{}
		for key, value := range obj {
			b.Fields[logCut(key)] = oauthLogJSON(key, value)
		}
	}
	if r.URL.Path == "/mcp" {
		b.RPC = rpcCalls(v)
	}
	return b
}

// readCloser: a body read again from what was read first and the rest.
type readCloser struct {
	io.Reader
	io.Closer
}

// oauthLogJSON: a JSON field's value as the log writes it: a shown one's
// strings by oauthLogValue, any other as its kind and length.
func oauthLogJSON(key string, v any) any {
	if !oauthLogShown[key] {
		if s, ok := v.(string); ok {
			return oauthLogValue(key, s)
		}
		return fmt.Sprintf("(hidden, %s)", jsonKind(v))
	}
	switch v := v.(type) {
	case string:
		return oauthLogValue(key, v)
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = oauthLogJSON(key, item)
		}
		return out
	case map[string]any:
		return "(object)"
	}
	return v // a number, true, false, null
}

func jsonKind(v any) string {
	switch v.(type) {
	case map[string]any:
		return "object"
	case []any:
		return "list"
	case nil:
		return "null"
	case bool:
		return "boolean"
	case float64:
		return "number"
	}
	return "string"
}

// rpcCalls: the JSON-RPC methods a message to /mcp (or a batch) calls,
// with what initialize says of the client and a tool call's tool: nothing
// of the arguments.
func rpcCalls(v any) []string {
	messages, ok := v.([]any)
	if !ok {
		messages = []any{v}
	}
	var calls []string
	for _, m := range messages {
		msg, _ := m.(map[string]any)
		method, _ := msg["method"].(string)
		if method == "" {
			continue
		}
		params, _ := msg["params"].(map[string]any)
		call := method
		switch method {
		case "initialize":
			if version, ok := params["protocolVersion"].(string); ok {
				call += " protocolVersion=" + version
			}
			if info, ok := params["clientInfo"].(map[string]any); ok {
				name, _ := info["name"].(string)
				version, _ := info["version"].(string)
				call += " clientInfo=" + strings.TrimSuffix(name+"/"+version, "/")
			}
		case "tools/call":
			if name, ok := params["name"].(string); ok {
				call += " " + name
			}
		}
		calls = append(calls, logCut(call))
	}
	return calls
}

// oauthLogWriter: the answer's status, its headers as sent, and the start
// of an error's body (an OAuth error's code and description).
type oauthLogWriter struct {
	http.ResponseWriter
	status  int
	header  http.Header
	errBody []byte
}

func (o *oauthLogWriter) WriteHeader(status int) {
	if o.status == 0 {
		o.status, o.header = status, o.ResponseWriter.Header().Clone()
	}
	o.ResponseWriter.WriteHeader(status)
}

func (o *oauthLogWriter) Write(b []byte) (int, error) {
	if o.status == 0 {
		o.WriteHeader(http.StatusOK)
	}
	if o.status >= 400 && len(o.errBody) < 4<<10 {
		o.errBody = append(o.errBody, b[:min(len(b), 4<<10-len(o.errBody))]...)
	}
	return o.ResponseWriter.Write(b)
}

func (o *oauthLogWriter) Unwrap() http.ResponseWriter { return o.ResponseWriter }

// oauthLogResponseHeaders: the answer's headers the log writes as they are.
var oauthLogResponseHeaders = []string{"WWW-Authenticate", "Content-Type", "Retry-After", "Allow",
	"Access-Control-Allow-Origin"}

// oauthLogAnswer: the answer's headers that tell a client where to go next,
// its addresses' codes hidden, and an error's code and description (never
// a success's body: it holds the tokens).
func oauthLogAnswer(o *oauthLogWriter) map[string]any {
	a := map[string]any{}
	for _, name := range oauthLogResponseHeaders {
		if v := o.header.Values(name); len(v) > 0 {
			a[name] = logCut(strings.Join(v, ", "))
		}
	}
	if v := o.header.Get("Location"); v != "" {
		a["Location"] = oauthLogURL(v, 2)
	}
	if v := o.header.Get("Refresh"); v != "" {
		wait, to, found := strings.Cut(v, "url=")
		if found {
			a["Refresh"] = wait + "url=" + oauthLogURL(to, 2)
		} else {
			a["Refresh"] = logCut(v)
		}
	}
	if cookies := (&http.Response{Header: o.header}).Cookies(); len(cookies) > 0 {
		var names []string
		for _, c := range cookies {
			names = append(names, logCut(c.Name))
		}
		a["Set-Cookie"] = "names: " + strings.Join(names, ", ")
	}
	var e struct {
		Error       any    `json:"error"`
		Description string `json:"error_description"`
	}
	if mt, _, _ := mime.ParseMediaType(o.header.Get("Content-Type")); mt == "application/json" &&
		o.header.Get("Content-Encoding") == "" && json.Unmarshal(o.errBody, &e) == nil {
		if s, ok := e.Error.(string); ok && s != "" {
			a["error"] = logCut(s)
		}
		if e.Description != "" {
			a["error_description"] = logCut(e.Description)
		}
	}
	if len(a) == 0 {
		return nil
	}
	return a
}
