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
	"slices"
	"strconv"
	"strings"
)

// User: one of the users, with the hash of their password and their MCP
// token as kept (its hash, tokenMatches; "": none, /mcp refuses them), their
// role and whether they are disabled (role, disabled in their section).
type User struct {
	Name, PasswordHash, MCPToken string
	Role                         string // roleAdmin, roleEditor or roleViewer
	Disabled                     bool   // can't sign in; sessions and token refused
	Pending                      bool   // made by sign-up, waiting for an admin's approval (Disabled too)
	Email                        string // their mail address, lowercase; "": none known
}

// The roles (docs/spec/users.md): an admin manages the users and does what
// an editor does; an editor changes everything shared (every user before
// roles); a viewer reads what is shared and changes nothing.
const (
	roleAdmin  = "admin"
	roleEditor = "editor"
	roleViewer = "viewer"
)

var roles = []string{roleAdmin, roleEditor, roleViewer}

// isAdmin: name is an admin, and not disabled.
func (s *Settings) isAdmin(name string) bool {
	u := s.Users[name]
	return u != nil && !u.Disabled && u.Role == roleAdmin
}

// isViewer: name is a viewer (changes nothing).
func (s *Settings) isViewer(name string) bool {
	u := s.Users[name]
	return u != nil && u.Role == roleViewer
}

// reservedNames: what no new user is called: where a task's assignee is
// named, "me" is whoever is signed in and "none" no one (docs/spec/users.md,
// Assigning tasks).
var reservedNames = map[string]bool{"me": true, "none": true}

// reservedNameMsg: why name can't be a new user's.
func reservedNameMsg(name string) string {
	return `"` + name + `" can't be a user name: for a task's assignee, "me" means whoever is signed in and "none" no one. Choose another.`
}

// assignable: a task can be assigned to name: a user who isn't disabled or
// waiting for approval, isn't a viewer (who couldn't complete it), and
// isn't called by a reserved name.
func (s *Settings) assignable(name string) bool {
	u := s.Users[name]
	return u != nil && !u.Disabled && !u.Pending && u.Role != roleViewer && !reservedNames[name]
}

// notAssignable: why a task can't be assigned to name.
func (s *Settings) notAssignable(name string) string {
	u := s.Users[name]
	switch {
	case u == nil:
		return `"` + name + `" is not a user of this wiki`
	case reservedNames[name]:
		return `The user "` + name + `" can't be given tasks: the name is the word for ` +
			map[string]string{"me": "whoever is signed in", "none": "no one"}[name]
	case u.Pending:
		return name + "'s account is waiting for an admin's approval"
	case u.Disabled:
		return name + "'s account is disabled"
	}
	return name + " is a viewer, and a viewer can't change tasks"
}

// assignableUsers: the names of the users a task can be assigned to, A–Z.
func (s *Settings) assignableUsers() []string {
	names := []string{}
	for name := range s.Users {
		if s.assignable(name) {
			names = append(names, name)
		}
	}
	slices.Sort(names)
	return names
}

// hasAdmin: some user is an admin, and not disabled.
func (s *Settings) hasAdmin() bool {
	for name := range s.Users {
		if s.isAdmin(name) {
			return true
		}
	}
	return false
}

// keepsAdmin: the change from old to s doesn't leave a wiki that had an
// admin without one (removing, demoting or disabling the last): nil, or
// why not. Without users there is no one to manage, so taking the last
// user out (with the wiki's password or none) is no such change.
func keepsAdmin(old, s *Settings) error {
	if old.hasAdmin() && s.Users != nil && !s.hasAdmin() {
		return errors.New("that leaves the wiki without an admin. Make another user admin first " +
			"(zonewiki user role NAME admin).")
	}
	return nil
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

// authenticate: the user name and password belong together; the user, or
// nil. A disabled user is returned too: the login says they are disabled
// (only to whoever knows their password).
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
