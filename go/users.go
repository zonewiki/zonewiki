// Users (docs/spec/users.md): several people on one wiki, each signing in
// with a password of their own and owning a private folder: the settings
// file's [user …] sections (configfile.go). Without them Zone has no users:
// one password (or none) and one MCP token, everything shared.
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

// User: one of the users, with the hash of their password and their MCP
// token as kept (its hash, tokenMatches; "": none, /mcp refuses them).
type User struct {
	Name, PasswordHash, MCPToken string
}

// A user's name is a folder name (private/<name>/), so letters, digits and
// inner hyphens.
var userNameRE = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

// Passwords are kept as PBKDF2-SHA256 hashes, "pbkdf2-sha256:<iterations>:
// <salt>:<hash>" (base64 without padding): Go's own crypto, no dependency.
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
	u := settings().Users[strings.ToLower(strings.TrimSpace(name))]
	if u == nil {
		checkPassword(password, dummyHash)
		return nil
	}
	if !checkPassword(password, u.PasswordHash) {
		return nil
	}
	return u
}

// askPassword asks for a new password twice; false (and why, on stderr) if
// none was given or the two differ.
func askPassword(stdin io.Reader, stderr io.Writer) (string, bool) {
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
		return "", false
	}
	if again, ok := read("Again: "); !ok || again != password {
		fmt.Fprintln(stderr, "The passwords differ.")
		return "", false
	}
	return password, true
}

// echoOff stops a terminal on stdin from showing what is typed, with stty
// (no terminal package: Zone keeps to its few dependencies); the returned
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
