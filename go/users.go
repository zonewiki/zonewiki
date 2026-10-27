// Users (docs/spec/users.md): several people on one wiki, each signing in
// with a password of their own and owning a private folder. From .env:
//
//	LEAF_USERS=alice,bob
//	LEAF_USER_ALICE_PASSWORD=pbkdf2-sha256:600000:<salt>:<hash>
//	LEAF_USER_ALICE_MCP_TOKEN=…
//
// Without LEAF_USERS Leaf has no users: one LEAF_PASSWORD (or none) and one
// LEAF_MCP_TOKEN, everything shared.
package main

import (
	"bufio"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
)

// User: one of LEAF_USERS, with the hash of their password and their MCP
// token ("": none, /mcp refuses them).
type User struct {
	Name, PasswordHash, MCPToken string
}

// A user's name is a folder name (private/<name>/) and part of a variable's
// name (LEAF_USER_<NAME>_PASSWORD), so letters, digits and inner hyphens.
var userNameRE = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

// userEnvName: the name as it is in a variable's ("mary-ann" → "MARY_ANN").
func userEnvName(name string) string {
	return strings.ToUpper(strings.ReplaceAll(name, "-", "_"))
}

// loadUsers reads LEAF_USERS and each user's settings; nil without users.
func loadUsers(getenv func(string) string) (map[string]*User, error) {
	spec := strings.TrimSpace(getenv("LEAF_USERS"))
	if spec == "" {
		return nil, nil
	}
	if getenv("LEAF_PASSWORD") != "" {
		return nil, errors.New("LEAF_PASSWORD and LEAF_USERS are both set. With users, each signs in with " +
			"their own password (LEAF_USER_<NAME>_PASSWORD): remove LEAF_PASSWORD.")
	}
	if getenv("LEAF_MCP_TOKEN") != "" {
		return nil, errors.New("LEAF_MCP_TOKEN and LEAF_USERS are both set. With users, each has their own " +
			"token (LEAF_USER_<NAME>_MCP_TOKEN), so an assistant acts as someone: remove LEAF_MCP_TOKEN.")
	}
	users := map[string]*User{}
	tokens := map[string]string{}
	envNames := map[string]string{}
	for _, name := range strings.Split(spec, ",") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		if !userNameRE.MatchString(name) {
			return nil, fmt.Errorf("LEAF_USERS: %q isn't a user name. Use lowercase letters, digits and hyphens "+
				"(e.g. alice, mary-ann).", name)
		}
		env := userEnvName(name)
		if other, taken := envNames[env]; taken {
			return nil, fmt.Errorf("LEAF_USERS: %s is there twice.", other)
		}
		envNames[env] = name
		hash := strings.TrimSpace(getenv("LEAF_USER_" + env + "_PASSWORD"))
		if hash == "" {
			return nil, fmt.Errorf("LEAF_USER_%s_PASSWORD isn't set. Make one with: leaf passwd %s", env, name)
		}
		if _, _, _, err := parseHash(hash); err != nil {
			return nil, fmt.Errorf("LEAF_USER_%s_PASSWORD isn't a password hash (%v). Make one with: leaf passwd %s",
				env, err, name)
		}
		token := strings.TrimSpace(getenv("LEAF_USER_" + env + "_MCP_TOKEN"))
		if other, taken := tokens[token]; taken && token != "" {
			return nil, fmt.Errorf("LEAF_USER_%s_MCP_TOKEN is %s's too. Give each user a token of their own.",
				env, other)
		}
		tokens[token] = name
		users[name] = &User{Name: name, PasswordHash: hash, MCPToken: token}
	}
	if len(users) == 0 {
		return nil, nil
	}
	return users, nil
}

// Passwords are kept as PBKDF2-SHA256 hashes, "pbkdf2-sha256:<iterations>:
// <salt>:<hash>" (base64 without padding): Go's own crypto, no dependency,
// and no "$", which compose would read as a variable in .env.
const (
	hashScheme     = "pbkdf2-sha256"
	hashIterations = 600_000 // OWASP's recommendation for PBKDF2-SHA256
	hashLen        = 32
)

func hashPassword(password string) string {
	salt := make([]byte, 16)
	_, _ = rand.Read(salt)
	key, _ := pbkdf2.Key(sha256.New, password, salt, hashIterations, hashLen)
	b64 := base64.RawStdEncoding.EncodeToString
	return fmt.Sprintf("%s:%d:%s:%s", hashScheme, hashIterations, b64(salt), b64(key))
}

func parseHash(h string) (iterations int, salt, key []byte, err error) {
	parts := strings.Split(h, ":")
	if len(parts) != 4 || parts[0] != hashScheme {
		return 0, nil, nil, errors.New("not " + hashScheme + ":<iterations>:<salt>:<hash>")
	}
	if iterations, err = strconv.Atoi(parts[1]); err != nil || iterations < 1 {
		return 0, nil, nil, errors.New("bad iteration count")
	}
	salt, err1 := base64.RawStdEncoding.DecodeString(parts[2])
	key, err2 := base64.RawStdEncoding.DecodeString(parts[3])
	if err1 != nil || err2 != nil || len(salt) == 0 || len(key) == 0 {
		return 0, nil, nil, errors.New("bad base64")
	}
	return iterations, salt, key, nil
}

// checkPassword: password is the one hash was made from.
func checkPassword(password, hash string) bool {
	iterations, salt, key, err := parseHash(hash)
	if err != nil {
		return false
	}
	got, err := pbkdf2.Key(sha256.New, password, salt, iterations, len(key))
	return err == nil && subtle.ConstantTimeCompare(got, key) == 1
}

// A wrong user name costs the same time as a wrong password, so timing
// doesn't tell which names exist.
var dummyHash = hashPassword("")

// authenticate: the user name and password belong together; the user, or nil.
func authenticate(name, password string) *User {
	u := cfg.Users[strings.ToLower(strings.TrimSpace(name))]
	if u == nil {
		checkPassword(password, dummyHash)
		return nil
	}
	if !checkPassword(password, u.PasswordHash) {
		return nil
	}
	return u
}

// passwdMain: "leaf passwd NAME" asks for a password (twice) and prints the
// line for .env. The password comes from stdin, so it isn't in the shell's
// history or ps; main turns the terminal's echo off (echoOff).
func passwdMain(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) != 1 || !userNameRE.MatchString(args[0]) {
		fmt.Fprintln(stderr, "Usage: leaf passwd NAME (lowercase letters, digits and hyphens)")
		return 2
	}
	in := bufio.NewReader(stdin)
	read := func(prompt string) (string, bool) {
		fmt.Fprint(stderr, prompt)
		line, err := in.ReadString('\n')
		fmt.Fprintln(stderr)
		if err != nil && line == "" {
			return "", false
		}
		return strings.TrimRight(line, "\r\n"), true
	}
	password, ok := read("Password: ")
	if !ok || password == "" {
		fmt.Fprintln(stderr, "No password given.")
		return 1
	}
	if again, ok := read("Again: "); !ok || again != password {
		fmt.Fprintln(stderr, "The passwords differ.")
		return 1
	}
	fmt.Fprintf(stdout, "LEAF_USER_%s_PASSWORD=%s\n", userEnvName(args[0]), hashPassword(password))
	return 0
}

// echoOff stops a terminal on stdin from showing what is typed, with stty
// (no terminal package: Leaf keeps to its few dependencies); the returned
// function turns it back on. Not a terminal (a pipe, run.sh's container):
// nothing to do.
func echoOff() func() {
	st, err := os.Stdin.Stat()
	if err != nil || st.Mode()&os.ModeCharDevice == 0 {
		return func() {}
	}
	stty := func(arg string) {
		cmd := exec.Command("stty", arg)
		cmd.Stdin = os.Stdin
		_ = cmd.Run()
	}
	stty("-echo")
	return func() { stty("echo") }
}
