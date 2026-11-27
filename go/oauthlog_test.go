package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
)

// logged: the OAuth log's lines for the requests made while it runs, read
// back as JSON.
func logged(t *testing.T) *[]map[string]any {
	t.Helper()
	var lines []map[string]any
	set(t, &oauthLogWrite, func(b []byte) {
		var m map[string]any
		if err := json.Unmarshal(b, &m); err != nil {
			t.Errorf("not JSON: %s", b)
		}
		if strings.Contains(string(b), "\n") {
			t.Errorf("more than a line: %q", b)
		}
		lines = append(lines, m)
	})
	return &lines
}

// The secrets of the requests below: none of them may be written.
var logSecrets = []string{"the-code-1234", "the-verifier-5678", "the-client-secret", "the-access-token",
	"the-refresh-token", "the-password", "the-session-cookie", "the-state-9", "the-fragment-token", "bare-credential"}

func TestOAuthLogNamesNoSecret(t *testing.T) {
	configWiki(t, "oauth-log = yes\n")
	lines := logged(t)
	var bodies []string
	h := oauthLog(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body) // the route gets the whole body
		bodies = append(bodies, string(b))
		switch r.URL.Path {
		case "/oauth/token":
			w.Header().Set("Set-Cookie", "zone_session=the-session-cookie; HttpOnly")
			oauthError(w, http.StatusBadRequest, "invalid_grant", "The code is wrong, used or older than a minute.")
		case "/oauth/authorize":
			w.Header().Set("Refresh", "0; url=https://chatgpt.com/cb?code=the-code-1234&state=the-state-9&iss=https%3A%2F%2Fwiki.example.org")
			w.Write([]byte("<p>Connected</p>"))
		case "/login":
			http.Redirect(w, r, "/oauth/authorize?client_id=zc1.x&state=the-state-9&code_challenge=abc", http.StatusFound)
		case "/mcp":
			w.Header().Set("WWW-Authenticate", `Bearer resource_metadata="https://wiki.example.org/.well-known/oauth-protected-resource/mcp", scope="mcp"`)
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "Unauthorized: wrong or missing bearer token"})
		default:
			w.Header().Set("Location", "https://chatgpt.com/cb?code=the-code-1234#access_token=the-fragment-token")
			w.WriteHeader(http.StatusFound)
		}
	}))
	form := "grant_type=authorization_code&code=the-code-1234&code_verifier=the-verifier-5678&client_id=zc1.abc" +
		"&client_secret=the-client-secret&redirect_uri=https%3A%2F%2Fchatgpt.com%2Fcb&resource=https%3A%2F%2Fwiki.example.org%2Fmcp" +
		"&refresh_token=the-refresh-token"
	requests := []*http.Request{
		httptest.NewRequest("POST", "/oauth/token", strings.NewReader(form)),
		httptest.NewRequest("POST", "/oauth/authorize", strings.NewReader("answer=allow&password=the-password&state=the-state-9&may=read")),
		httptest.NewRequest("POST", "/login?next=%2Foauth%2Fauthorize%3Fclient_id%3Dzc1.x%26state%3Dthe-state-9", strings.NewReader("username=bob&password=the-password")),
		httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"openai-mcp","version":"1.0.0"},"capabilities":{}}}`)),
		httptest.NewRequest("GET", "/oauth/authorize?response_type=code&client_id=zc1.x&state=the-state-9&code_challenge=abc&code=the-code-1234&access_token=the-access-token", nil),
		httptest.NewRequest("GET", "/.well-known/openid-configuration", nil),
	}
	requests[0].Header.Set("Content-Type", "application/x-www-form-urlencoded")
	requests[0].Header.Set("Authorization", "Basic the-client-secret")
	requests[0].Header.Set("Cookie", "zone_session=the-session-cookie")
	requests[1].Header.Set("Content-Type", "application/x-www-form-urlencoded")
	requests[1].Header.Set("Referer", "https://wiki.example.org/oauth/authorize?client_id=zc1.x&state=the-state-9")
	requests[2].Header.Set("Content-Type", "application/x-www-form-urlencoded")
	requests[3].Header.Set("Content-Type", "application/json")
	requests[3].Header.Set("Authorization", "Bearer the-access-token")
	requests[3].Header.Set("MCP-Protocol-Version", "2025-06-18")
	requests[3].Header.Set("User-Agent", "openai-mcp/1.0.0")
	requests[3].Header.Set("Origin", "https://chatgpt.com")
	requests[4].Header.Set("Authorization", "bare-credential")
	for _, r := range requests {
		h.ServeHTTP(httptest.NewRecorder(), r)
	}
	if len(*lines) != len(requests) {
		t.Fatalf("%d lines for %d requests", len(*lines), len(requests))
	}
	if bodies[0] != form {
		t.Errorf("the route got %q", bodies[0])
	}
	all, _ := json.Marshal(*lines)
	for _, secret := range logSecrets {
		if strings.Contains(string(all), secret) {
			t.Errorf("the log names %s: %s", secret, all)
		}
	}
	text := func(i int) string {
		var b strings.Builder
		enc := json.NewEncoder(&b)
		enc.SetEscapeHTML(false)
		_ = enc.Encode((*lines)[i])
		return b.String()
	}
	for i, want := range []string{
		// what helps: the parameters that hold no secret, the secrets' lengths, the error
		`"grant_type":"authorization_code"`, `"code":"(hidden, 13 bytes)"`, `"code_verifier":"(hidden, 17 bytes)"`,
		`"resource":"https://wiki.example.org/mcp"`, `"Authorization":"Basic (hidden, 17 bytes)"`,
		`"Cookie":"names: zone_session"`, `"Set-Cookie":"names: zone_session"`, `"error":"invalid_grant"`, `"status":400`,
	} {
		if !strings.Contains(text(0), want) {
			t.Errorf("token %d: no %s in %s", i, want, text(0))
		}
	}
	for _, want := range []string{`"answer":"allow"`, `"may":"read"`, `"password":"(hidden, 12 bytes)"`,
		`code=(hidden, 13 bytes)`, `state=(hidden, 11 bytes)`, `iss=https://wiki.example.org`, `"Refresh":"0; url=https://chatgpt.com/cb?`,
		`"Referer":"https://wiki.example.org/oauth/authorize?client_id=zc1.x&state=(hidden, 11 bytes)"`} {
		if !strings.Contains(text(1), want) {
			t.Errorf("authorize: no %s in %s", want, text(1))
		}
	}
	for _, want := range []string{`"next":"/oauth/authorize?client_id=zc1.x&state=(hidden, 11 bytes)"`,
		`"username":"(hidden, 3 bytes)"`, `"Location":"/oauth/authorize?client_id=zc1.x&state=(hidden, 11 bytes)&code_challenge=abc"`} {
		if !strings.Contains(text(2), want) {
			t.Errorf("login: no %s in %s", want, text(2))
		}
	}
	for _, want := range []string{`"rpc":["initialize protocolVersion=2025-06-18 clientInfo=openai-mcp/1.0.0"]`,
		`"params":"(hidden, object)"`, `"method":"initialize"`, `"MCP-Protocol-Version":"2025-06-18"`,
		`"Origin":"https://chatgpt.com"`, `"Authorization":"Bearer (hidden, 16 bytes)"`, `"status":401`,
		`"WWW-Authenticate":"Bearer resource_metadata=\"https://wiki.example.org/.well-known/oauth-protected-resource/mcp\", scope=\"mcp\""`} {
		if !strings.Contains(text(3), want) {
			t.Errorf("mcp: no %s in %s", want, text(3))
		}
	}
	for _, want := range []string{`"response_type":"code"`, `"code_challenge":"abc"`, `"access_token":"(hidden, 16 bytes)"`,
		`"Authorization":"(hidden, 15 bytes)"`} {
		if !strings.Contains(text(4), want) {
			t.Errorf("authorize asked: no %s in %s", want, text(4))
		}
	}
	for _, want := range []string{`"path":"/.well-known/openid-configuration"`, `"status":302`,
		`"Location":"https://chatgpt.com/cb?code=(hidden, 13 bytes)#(hidden, 31 bytes)"`} {
		if !strings.Contains(text(5), want) {
			t.Errorf("openid-configuration: no %s in %s", want, text(5))
		}
	}
}

// Off by default, and only for a connect's routes.
func TestOAuthLogIsOffAndNarrow(t *testing.T) {
	configWiki(t, "")
	lines := logged(t)
	h := oauthLog(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/oauth/token", strings.NewReader("code=x")))
	if len(*lines) != 0 {
		t.Errorf("off, but %v", *lines)
	}
	settings().OAuthLog = true
	for path, want := range map[string]bool{
		"/mcp": true, "/oauth/register": true, "/.well-known/oauth-authorization-server/mcp": true, "/token": true,
		"/login?next=%2Foauth%2Fauthorize%3Fx%3D1": true, "/login": false, "/login?next=%2Fpage%2Fx": false,
		"/page/index": false, "/api/tasks": false, "/assistant": false,
	} {
		*lines = nil
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", path, nil))
		if got := len(*lines) == 1; got != want {
			t.Errorf("%s: logged %v", path, got)
		}
	}
}

func TestOAuthLogSetting(t *testing.T) {
	for text, want := range map[string]bool{"": false, "oauth-log = yes\n": true, "oauth-log = no\n": false, "oauth-log = 1\n": true} {
		f, err := parseConfig(text)
		if err != nil {
			t.Fatal(err)
		}
		s, err := loadSettings(f)
		if err != nil || s.OAuthLog != want {
			t.Errorf("%q: %v, %v", text, s.OAuthLog, err)
		}
	}
	f, _ := parseConfig("oauth-log = sometimes\n")
	if _, err := loadSettings(f); err == nil || !strings.Contains(err.Error(), "line 1: oauth-log = sometimes isn't yes or no") {
		t.Errorf("sometimes: %v", err)
	}
	on, _ := parseConfig("oauth-log = yes\n")
	off, _ := parseConfig("")
	a, _ := loadSettings(off)
	b, _ := loadSettings(on)
	if got := settingsChange(a, b); got != "Change settings: oauth-log" {
		t.Errorf("change: %q", got)
	}
	if !slices.Contains(hostKeys, "oauth-log") {
		t.Error("oauth-log is the host's: an import leaves it out")
	}
}
