package main

import (
	"bytes"
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

func TestLoadUsers(t *testing.T) {
	wiki := t.TempDir()
	hash := hashPassword("pw")
	env := func(kv ...string) func(string) string {
		m := map[string]string{"LEAF_DATA": wiki}
		for i := 0; i < len(kv); i += 2 {
			m[kv[i]] = kv[i+1]
		}
		return func(k string) string { return m[k] }
	}
	c, err := loadConfig(env("LEAF_USERS", " alice, mary-ann ,", "LEAF_USER_ALICE_PASSWORD", hash,
		"LEAF_USER_ALICE_MCP_TOKEN", " tok ", "LEAF_USER_MARY_ANN_PASSWORD", hash))
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Users) != 2 || c.Users["alice"].MCPToken != "tok" || c.Users["mary-ann"].MCPToken != "" || !c.authOn() {
		t.Fatalf("%+v", c.Users)
	}
	if c, err := loadConfig(env()); err != nil || c.Users != nil || c.authOn() {
		t.Fatal("no users")
	}
	if c, err := loadConfig(env("LEAF_PASSWORD", "pw")); err != nil || !c.authOn() {
		t.Fatal("a password")
	}
	for _, bad := range [][]string{
		{"LEAF_USERS", "Alice", "LEAF_USER_ALICE_PASSWORD", hash},
		{"LEAF_USERS", "al/ice"},
		{"LEAF_USERS", "-a", "LEAF_USER__A_PASSWORD", hash},
		{"LEAF_USERS", "alice"},
		{"LEAF_USERS", "alice", "LEAF_USER_ALICE_PASSWORD", "pw"},
		{"LEAF_USERS", "alice", "LEAF_USER_ALICE_PASSWORD", hash, "LEAF_PASSWORD", "pw"},
		{"LEAF_USERS", "alice", "LEAF_USER_ALICE_PASSWORD", hash, "LEAF_MCP_TOKEN", "t"},
		{"LEAF_USERS", "alice,alice", "LEAF_USER_ALICE_PASSWORD", hash},
		{"LEAF_USERS", "a,b", "LEAF_USER_A_PASSWORD", hash, "LEAF_USER_B_PASSWORD", hash,
			"LEAF_USER_A_MCP_TOKEN", "t", "LEAF_USER_B_MCP_TOKEN", "t"},
	} {
		if _, err := loadConfig(env(bad...)); err == nil {
			t.Errorf("%v: no error", bad)
		}
	}
}

func TestAuthenticate(t *testing.T) {
	testWiki(t, nil)
	cfg.Users = map[string]*User{"alice": {Name: "alice", PasswordHash: hashPassword("pw")}}
	if u := authenticate(" Alice ", "pw"); u == nil || u.Name != "alice" {
		t.Error("alice")
	}
	if authenticate("alice", "nope") != nil || authenticate("bob", "pw") != nil || authenticate("", "") != nil {
		t.Error("let in")
	}
}

func TestPasswd(t *testing.T) {
	var out, errs bytes.Buffer
	if passwdMain([]string{"mary-ann"}, strings.NewReader("s3cret\ns3cret\n"), &out, &errs) != 0 {
		t.Fatal(errs.String())
	}
	line := strings.TrimSpace(out.String())
	hash, found := strings.CutPrefix(line, "LEAF_USER_MARY_ANN_PASSWORD=")
	if !found || !checkPassword("s3cret", hash) {
		t.Errorf("%q", line)
	}
	for _, c := range []struct {
		args  []string
		input string
	}{{nil, "a\na\n"}, {[]string{"Bob"}, "a\na\n"}, {[]string{"bob"}, "a\nb\n"}, {[]string{"bob"}, "\n\n"},
		{[]string{"bob"}, ""}} {
		out.Reset()
		if passwdMain(c.args, strings.NewReader(c.input), &out, &errs) == 0 || out.Len() != 0 {
			t.Errorf("%v %q: accepted", c.args, c.input)
		}
	}
}
