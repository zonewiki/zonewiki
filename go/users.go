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
	"unicode"
)

// User: one of the users, with the hash of their password and their MCP
// token as kept (its hash, tokenMatches; "": none, /mcp refuses them), their
// role and whether they are disabled (role, disabled in their section).
type User struct {
	Name, PasswordHash, MCPToken string
	Role                         string   // roleAdmin, roleEditor or roleViewer
	Disabled                     bool     // can't sign in; sessions and token refused
	Pending                      bool     // made by sign-up, waiting for an admin's approval (Disabled too)
	Email                        string   // their mail address, lowercase; "": none known
	Groups                       []string // the groups their section names (groups = dev, hiring), A–Z
	// In: every group they are in, A–Z: those their section names, and the
	// groups made of those ([group product] includes = dev: joinGroups).
	In []string
}

// Groups (docs/spec/users.md): who belongs together is said once, for
// whatever is for some and not for all (a task list for a group, a group's
// folder of pages): on the users (groups in a user's section), or by the
// groups a group is made of ([group product] includes = dev, design: who is
// in dev or in design is in product, and so on through what those include).
// A group is its name; one nobody is in has no members, and is no error.
var groupNameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)

// groupNameRule: what a group's name is, as every refusal of one says it.
const groupNameRule = "lowercase letters, digits and hyphens, at most 40"

// parseGroups: the groups a groups value names (commas or spaces between
// them, a leading @ allowed), cleaned, A–Z, each once; the first word that
// is no group's name as bad.
func parseGroups(value string) (groups []string, bad string) {
	for _, w := range strings.FieldsFunc(value, func(r rune) bool { return r == ',' || unicode.IsSpace(r) }) {
		name := strings.ToLower(strings.TrimPrefix(w, "@"))
		if !groupNameRE.MatchString(name) {
			return nil, w
		}
		if !slices.Contains(groups, name) {
			groups = append(groups, name)
		}
	}
	slices.Sort(groups)
	return groups, ""
}

// parseNames: the users a value names (commas or spaces between them, a
// leading @ allowed), lowercase, A–Z, each once; the first word that is no
// user's name as bad. Whether there is such a user is the caller's.
func parseNames(value string) (names []string, bad string) {
	for _, w := range strings.FieldsFunc(value, func(r rune) bool { return r == ',' || unicode.IsSpace(r) }) {
		name := strings.ToLower(strings.TrimPrefix(w, "@"))
		if !userNameRE.MatchString(name) {
			return nil, w
		}
		if !slices.Contains(names, name) {
			names = append(names, name)
		}
	}
	slices.Sort(names)
	return names, ""
}

// ownerOf: user is an owner of group (owners in its section), and can use
// the wiki.
func (s *Settings) ownerOf(user, group string) bool {
	u := s.Users[user]
	return u != nil && !u.Disabled && slices.Contains(s.Owners[group], user)
}

// managesGroup: user says who is in group, and what it is called: its
// owners, and every admin (who sets any user's groups anyway). Not a
// viewer, who changes nothing.
func (s *Settings) managesGroup(user, group string) bool {
	return (s.ownerOf(user, group) || s.isAdmin(user)) && !s.isViewer(user)
}

// directMembers: the users whose section names group (not those in it by
// way of a group it is made of), disabled ones too, A–Z: what an owner's
// field lists and sets.
func (s *Settings) directMembers(group string) []string {
	var out []string
	for _, name := range sortedKeys(s.Users) {
		if slices.Contains(s.Users[name].Groups, group) {
			out = append(out, name)
		}
	}
	return out
}

// groupsOf: the groups user is in, by whatever way (User.In), A–Z: none for
// who is no user or can't use the wiki (disabled). What asks "which groups
// are theirs" asks here, as memberOf answers for one.
func (s *Settings) groupsOf(user string) []string {
	if u := s.Users[user]; u != nil && !u.Disabled {
		return u.In
	}
	return nil
}

// memberOf: user is in group (named on them, or by way of a group it is
// made of), and can use the wiki (not disabled).
func (s *Settings) memberOf(user, group string) bool {
	return slices.Contains(s.groupsOf(user), group)
}

// joinGroups works out, once for these settings, which groups each user is
// in (User.In): canSee and a list's members ask for every page and task.
func (s *Settings) joinGroups() {
	// madeOf: the groups that include a group, directly.
	madeOf := map[string][]string{}
	for _, g := range sortedKeys(s.Includes) {
		for _, in := range s.Includes[g] {
			madeOf[in] = append(madeOf[in], g)
		}
	}
	// through: groups, and every group that includes one of them, by
	// whatever way.
	through := func(groups []string) []string {
		in := slices.Clone(groups)
		for i := 0; i < len(in); i++ { // in grows while it is walked
			for _, g := range madeOf[in[i]] {
				if !slices.Contains(in, g) {
					in = append(in, g)
				}
			}
		}
		slices.Sort(in)
		return in
	}
	for _, u := range s.Users {
		u.In = through(u.Groups)
	}
	s.partOf = map[string][]string{}
	for g := range madeOf {
		s.partOf[g] = through([]string{g})
	}
}

// groupIn: the group outer includes the group inner, directly or by way of
// others, so whoever is in inner is in outer, now and whoever joins (a
// group is not in itself).
func (s *Settings) groupIn(inner, outer string) bool {
	return inner != outer && slices.Contains(s.partOf[inner], outer)
}

// groups: every group there is, A–Z: those a user is in, and those the
// settings file has a section for.
func (s *Settings) groups() []string {
	out := sortedKeys(s.Includes)
	for _, u := range s.Users {
		for _, g := range u.In {
			if !slices.Contains(out, g) {
				out = append(out, g)
			}
		}
	}
	slices.Sort(out)
	return out
}

// groupMembers: the users in group who can use the wiki, A–Z.
func (s *Settings) groupMembers(group string) []string {
	var out []string
	for _, name := range sortedKeys(s.Users) {
		if s.memberOf(name, group) {
			out = append(out, name)
		}
	}
	return out
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

// isViewer: name is a viewer (changes nothing): a user of that role, or a
// visitor of a wiki that is public to read only (access.go).
func (s *Settings) isViewer(name string) bool {
	if name == visitor {
		return cfg.Public != publicEdit
	}
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
