package main

import (
	"crypto/sha256"
	"encoding/base64"
	"net/url"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestOAuthClientID(t *testing.T) {
	old := sessionKey
	sessionKey = []byte("a key for the test")
	defer func() { sessionKey = old }()
	c := oauthClient{Name: "Claude", Redirects: []string{"https://claude.ai/api/mcp/auth_callback"}}
	id := c.id()
	got, ok := clientOf(id)
	if !ok || got.Name != c.Name || len(got.Redirects) != 1 || got.Redirects[0] != c.Redirects[0] {
		t.Fatalf("clientOf(%q) = %+v, %v", id, got, ok)
	}
	evil := oauthClient{Name: "Claude", Redirects: []string{"https://evil.example/cb"}}.id()
	forged := evil[:strings.LastIndex(evil, ".")] + id[strings.LastIndex(id, "."):]
	for _, bad := range []string{"", "zc1.", "zc1.x.y", id[:len(id)-1], forged, strings.TrimPrefix(id, clientIDPrefix)} {
		if _, ok := clientOf(bad); ok {
			t.Errorf("clientOf(%q) is a client", bad)
		}
	}
	sessionKey = []byte("another wiki's key")
	if _, ok := clientOf(id); ok {
		t.Error("a client id signed with another key is a client")
	}
}

func TestOAuthRedirects(t *testing.T) {
	for uri, want := range map[string]bool{
		"https://claude.ai/api/mcp/auth_callback":        true,
		"http://localhost:3118/callback":                 true,
		"http://127.0.0.1/callback":                      true,
		"http://[::1]:8080/cb":                           true,
		"http://claude.ai/cb":                            false,
		"http://localhost.evil.example/cb":               false,
		"https://claude.ai/cb#x":                         false,
		"https://user@claude.ai/cb":                      false,
		"cursor://x/cb":                                  false,
		"javascript:alert(1)":                            false,
		"https:///cb":                                    false,
		"":                                               false,
		"https://a.example/" + strings.Repeat("x", 1000): false,
	} {
		if got := validRedirect(uri); got != want {
			t.Errorf("validRedirect(%q) = %v, want %v", uri, got, want)
		}
	}
	c := oauthClient{Redirects: []string{"https://claude.ai/cb", "http://localhost:3118/callback?x=1"}}
	for uri, want := range map[string]bool{
		"https://claude.ai/cb":                  true,
		"https://claude.ai/cb/":                 false,
		"https://claude.ai:444/cb":              false, // only this computer's port is free
		"https://claude.ai/cb?x=1":              false,
		"http://localhost:3118/callback?x=1":    true,
		"http://localhost:60000/callback?x=1":   true,
		"http://localhost/callback?x=1":         true,
		"http://localhost:60000/callback":       false,
		"http://localhost:60000/other?x=1":      false,
		"http://127.0.0.1:3118/callback?x=1":    false,
		"https://localhost:3118/callback?x=1":   false,
		"http://localhost:3118/callback?x=1#f":  false,
		"http://evil.example:3118/callback?x=1": false,
	} {
		if got := c.allows(uri); got != want {
			t.Errorf("allows(%q) = %v, want %v", uri, got, want)
		}
	}
}

func TestPKCE(t *testing.T) {
	verifier := strings.Repeat("a", 43)
	sum := sha256.Sum256([]byte(verifier))
	challenge := base64.RawURLEncoding.EncodeToString(sum[:])
	if !pkceMatches(verifier, challenge) {
		t.Error("the verifier doesn't match its challenge")
	}
	for _, bad := range []string{"", "short", strings.Repeat("b", 43), strings.Repeat("a", 129), challenge} {
		if pkceMatches(bad, challenge) {
			t.Errorf("pkceMatches(%q) is true", bad)
		}
	}
}

func TestAuthCodes(t *testing.T) {
	at := time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)
	old := now
	now = func() time.Time { return at }
	defer func() { now = old }()
	code := newCode(authCode{user: "alice"})
	if c, ok, _ := takeCode(code); !ok || c.user != "alice" {
		t.Fatalf("takeCode = %+v, %v", c, ok)
	}
	// Used again while its connection is made: spendCode says so.
	if _, ok, copied := takeCode(code); ok || copied != "" {
		t.Error("a code works twice")
	}
	if spendCode(code, "g1") {
		t.Error("a code used again while its connection was made isn't told")
	}
	// Used again after: the connection made with it, for the caller to end,
	// also after the code's minute.
	other := newCode(authCode{user: "alice"})
	takeCode(other)
	if !spendCode(other, "g2") {
		t.Error("a code used once is told as copied")
	}
	at = at.Add(codeLifetime)
	if _, ok, copied := takeCode(other); ok || copied != "g2" {
		t.Errorf("a code used again gives %q", copied)
	}
	late := newCode(authCode{user: "alice"})
	at = at.Add(codeLifetime)
	if _, ok, _ := takeCode(late); ok {
		t.Error("a code works after a minute")
	}
	if _, ok, _ := takeCode(""); ok {
		t.Error("no code is a code")
	}
}

// What a connection may do and see, as the form on Allow and Change… sets
// it (chooseReach), and which change gives it more (beyond): what its user
// may take away without the password and give only with it.
func TestAConnectionsReach(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "carol")
	settings().Users["alice"].In = []string{"dev", "ops"}
	settings().Users["carol"].Role = roleViewer
	alice := asUser("alice")
	groups := func(g ...string) *[]string {
		g = append([]string{}, g...)
		return &g
	}
	form := func(may string, see ...string) url.Values {
		return url.Values{"may": {may}, "choose": {"1"}, "see": see}
	}
	everything := less{}
	for _, c := range []struct {
		name string
		had  less
		form url.Values
		want less
	}{
		{"nothing posted: as it is", less{ReadOnly: true, Groups: groups("dev")}, nil, less{ReadOnly: true, Groups: groups("dev")}},
		{"all ticked on Allow: every group, also later", everything, form("write", "private", "group:dev", "group:ops"), everything},
		{"one left out", everything, form("write", "private", "group:dev"), less{Groups: groups("dev")}},
		{"none", everything, form("read"), less{ReadOnly: true, NoPrivate: true, Groups: groups()}},
		{"a group she isn't in is no group", everything, form("write", "private", "group:dev", "group:hr"), less{Groups: groups("dev")}},
		{"the one it lacked ticked again: every group", less{Groups: groups("dev")}, form("write", "private", "group:dev", "group:ops"), everything},
		// It had both of hers (and one she has left): all ticked, nothing
		// new, so it isn't handed the groups she joins later.
		{"all ticked, none new", less{Groups: groups("dev", "ops", "qa")}, form("write", "private", "group:dev", "group:ops"), less{Groups: groups("dev", "ops")}},
	} {
		_, got := chooseReach(settings(), alice, c.had, c.form)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: %+v, want %+v", c.name, got, c.want)
		}
	}
	// A viewer's only reads, whatever the form says.
	if f, got := chooseReach(settings(), asUser("carol"), everything, form("write", "private")); !got.ReadOnly || !f.Viewer || !f.ReadOnly {
		t.Errorf("a viewer's connection: %+v", got)
	}
	// Without users only what it may do is asked.
	settings().Users = nil
	if f, got := chooseReach(settings(), asker{}, everything, url.Values{"may": {"read"}, "see": {"private"}}); f.Users || got != (less{ReadOnly: true}) {
		t.Errorf("without users: %+v %+v", f, got)
	}

	for _, c := range []struct {
		name    string
		had, to less
		gives   bool
	}{
		{"the same", less{Groups: groups("dev")}, less{Groups: groups("dev")}, false},
		{"to only read", everything, less{ReadOnly: true}, false},
		{"read and change again", less{ReadOnly: true}, everything, true},
		{"without the private place", everything, less{NoPrivate: true}, false},
		{"the private place again", less{NoPrivate: true}, everything, true},
		{"fewer groups", everything, less{Groups: groups("dev")}, false},
		{"no group", less{Groups: groups("dev")}, less{Groups: groups()}, false},
		{"a group it hadn't", less{Groups: groups("dev")}, less{Groups: groups("dev", "ops")}, true},
		{"every group, where it had some", less{Groups: groups("dev", "ops")}, everything, true},
		{"mixed: only read, with the private place", less{NoPrivate: true}, less{ReadOnly: true}, true},
	} {
		if got := c.to.beyond(c.had); got != c.gives {
			t.Errorf("%s: beyond %v, want %v", c.name, got, c.gives)
		}
	}
}
