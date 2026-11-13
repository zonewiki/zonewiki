package main

import (
	"crypto/sha256"
	"encoding/base64"
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
	if c, ok := takeCode(code); !ok || c.user != "alice" {
		t.Fatalf("takeCode = %+v, %v", c, ok)
	}
	if _, ok := takeCode(code); ok {
		t.Error("a code works twice")
	}
	late := newCode(authCode{user: "alice"})
	at = at.Add(codeLifetime)
	if _, ok := takeCode(late); ok {
		t.Error("a code works after a minute")
	}
	if _, ok := takeCode(""); ok {
		t.Error("no code is a code")
	}
}
