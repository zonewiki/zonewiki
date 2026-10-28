package main

import (
	"strings"
	"testing"
)

func TestPasswordHash(t *testing.T) {
	h := hashPassword("correct horse ✓")
	if !strings.HasPrefix(h, "pbkdf2-sha256:600000:") || strings.Contains(h, "$") {
		t.Fatalf("hash %q", h)
	}
	if !checkPassword("correct horse ✓", h) || checkPassword("correct horse", h) || checkPassword("", h) {
		t.Error("checkPassword")
	}
	if h == hashPassword("correct horse ✓") {
		t.Error("no salt")
	}
	for _, bad := range []string{"", "plain", "pbkdf2-sha256:0:AA:AA", "pbkdf2-sha256:1:!!:AA", "bcrypt:1:AA:AA",
		"pbkdf2-sha256:1::AA"} {
		if _, _, _, err := parseHash(bad); err == nil {
			t.Errorf("%q parses", bad)
		}
		if checkPassword("", bad) {
			t.Errorf("%q checks", bad)
		}
	}
}

func TestAuthenticate(t *testing.T) {
	testWiki(t, nil)
	settings().Users = map[string]*User{"alice": {Name: "alice", PasswordHash: hashPassword("pw")}}
	if u := authenticate(" Alice ", "pw"); u == nil || u.Name != "alice" {
		t.Error("alice")
	}
	if authenticate("alice", "nope") != nil || authenticate("bob", "pw") != nil || authenticate("", "") != nil {
		t.Error("let in")
	}
}
