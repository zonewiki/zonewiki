// The settings file's commands (docs/spec/users.md): zonewiki user adds,
// changes and removes users, zonewiki config checks the settings, sets the
// wiki's password and token, and moves settings out of the environment.
// They edit .zone/config in the wiki ZONE_DATA names, keeping its comments,
// and refuse a change that would leave settings Zone doesn't start with; a
// running Zone takes the change within a second and logs it (the file is
// kept out of git). New MCP tokens are shown once and kept as hashes.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"zonewiki/internal/settingsfile"
)

const userUsage = `Usage: zonewiki user COMMAND
  add NAME [--mcp-token]  add a user (asks for the password; --mcp-token makes them a token);
    [--role ROLE]         admin, editor (the default) or viewer; the first user is an admin.
                          The first replaces the wiki's password and MCP token
  passwd NAME             set a user's password (asks for it)
  token NAME              give a user a new MCP token (the old one stops working)
  role NAME ROLE          change a user's role: admin, editor or viewer
  disable NAME            stop a user signing in (their files stay); enable NAME undoes it
  invite NAME             a link for a new user to choose their password, for 7 days (shown once)
    [--role ROLE]         their role: admin, editor (the default) or viewer
  reset NAME              a link for a user to set a new password, for 24 hours (shown once)
  remove NAME             remove a user (their private pages and tasks stay); the last one only
    [--password|--open]   with the wiki's password instead (asks for it), or with no login
  list                    the users, their roles, and whether each has an MCP token`

const configUsage = `Usage: zonewiki config COMMAND
  check            check the settings and show those in effect
  set KEY VALUE    set one of the wiki's settings (hosts, locale, noshare …)
  unset KEY        back to its default (password: no login; mcp-token: /mcp off)
  passwd           set the wiki's password, without users (asks for it)
  token            give the wiki a new MCP token, without users (the old one stops working)`

// newToken: an MCP token, 32 random bytes as hex.
func newToken() string {
	raw := make([]byte, 32)
	_, _ = rand.Read(raw)
	return hex.EncodeToString(raw)
}

// openConfig: the wiki (cfg) and its settings file (empty if there is none).
func openConfig(getenv func(string) string) (*settingsfile.File, error) {
	c, err := loadWiki(getenv)
	if err != nil {
		return nil, err
	}
	cfg = c
	f, err := readConfigFile(configPath())
	if f == nil && err == nil {
		f = settingsfile.Empty()
	}
	return f, err
}

// saveConfig writes f, if the settings it makes are good.
func saveConfig(f *settingsfile.File) error {
	if len(f.Lines) == 0 || !strings.HasPrefix(strings.TrimSpace(f.Lines[0]), "#") {
		header := []string{"# Zone's settings and users (docs/spec/users.md). " +
			"Zone reloads this file when it changes."}
		if len(f.Lines) > 0 && strings.TrimSpace(f.Lines[0]) != "" {
			header = append(header, "")
		}
		f.Lines = append(header, f.Lines...)
	}
	parsed, err := parseConfig(f.Text())
	var s *Settings
	if err == nil {
		s, err = loadSettings(parsed)
	}
	if err == nil {
		err = keepsAdmin(settingsOf(f.Read), s)
	}
	if err != nil {
		return fmt.Errorf("not changed: %w", err)
	}
	// One command at a time, and only onto the file it read: another's
	// change in between (a command, an editor) isn't overwritten.
	if err := os.MkdirAll(filepath.Dir(configPath()), 0o777); err != nil {
		return err
	}
	unlock, err := lockFile(configPath() + ".lock")
	if err != nil {
		return err
	}
	defer unlock()
	b, err := os.ReadFile(configPath())
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if string(b) != f.Read {
		return fmt.Errorf("not changed: %s changed while this command ran. Run it again.", configName())
	}
	return settingsfile.Write(configPath(), f.Text())
}

// settingsOf: the settings text makes; empty ones if it has an error (a
// file Zone doesn't start with has no admin to keep).
func settingsOf(text string) *Settings {
	if f, err := parseConfig(text); err == nil {
		if s, err := loadSettings(f); err == nil {
			return s
		}
	}
	return &Settings{}
}

func userMain(args []string, getenv func(string) string, stdin io.Reader, stdout, stderr io.Writer) int {
	c := &userCommand{stdin: stdin, stdout: stdout, stderr: stderr}
	if len(args) == 0 {
		fmt.Fprintln(stderr, userUsage)
		return 2
	}
	if isHelp(args[0]) {
		fmt.Fprintln(stdout, userUsage)
		return 0
	}
	if !c.parse(args[0], args[1:]) {
		fmt.Fprintln(stderr, userUsage)
		return 2
	}
	if c.roleFlag && !slices.Contains(roles, c.role) {
		return c.fail("%q isn't a role. Use admin, editor or viewer.", c.role)
	}
	f, err := openConfig(getenv)
	if err != nil {
		return c.fail("%v", err)
	}
	c.f = f
	if c.cmd == "list" {
		return c.list()
	}
	if !userNameRE.MatchString(c.name) {
		return c.fail("%q isn't a user name. Use lowercase letters, digits and hyphens (e.g. alice, mary-ann).", c.name)
	}
	if c.cmd == "invite" || c.cmd == "reset" {
		return c.link()
	}
	exists := f.User(c.name) != nil
	if links, _ := readLinks(); c.cmd == "add" && linkFor(links, c.name) != nil && linkFor(links, c.name).Kind == "invite" {
		return c.fail("%s has an open invite. Wait for it, or revoke it on the Users page.", c.name)
	}
	switch {
	case c.cmd == "add" && exists:
		return c.fail("%s is a user already. Change the password with: zonewiki user passwd %s", c.name, c.name)
	case c.cmd != "add" && !exists:
		return c.fail("there is no user %s in %s.", c.name, configName())
	}
	switch c.cmd {
	case "add", "passwd":
		return c.setPassword()
	case "token":
		return c.newToken()
	case "role":
		return c.setRole()
	case "disable", "enable":
		return c.setDisabled()
	}
	return c.remove()
}

// userCommand: a zonewiki user command as given (parse), the settings file
// it changes, and where it reads and writes. Each command is a method that
// returns the exit code.
type userCommand struct {
	cmd, name    string
	role         string // --role ROLE, or role's second argument
	roleFlag     bool
	tokenFlag    bool // --mcp-token (add)
	passwordFlag bool // --password (remove)
	openFlag     bool // --open (remove)
	f            *settingsfile.File
	stdin        io.Reader
	stdout       io.Writer
	stderr       io.Writer
}

func (c *userCommand) fail(format string, a ...any) int {
	fmt.Fprintf(c.stderr, "zonewiki user: "+format+"\n", a...)
	return 1
}

// parse reads the command and its arguments; false when they aren't one
// of the usage's forms.
func (c *userCommand) parse(cmd string, args []string) bool {
	c.cmd = cmd
	flag := func(name string) bool { // --name, or -name as Go's flags take it
		is := func(a string) bool { return a == name || a == name[1:] }
		found := slices.ContainsFunc(args, is)
		args = slices.DeleteFunc(args, is)
		return found
	}
	c.tokenFlag, c.passwordFlag, c.openFlag = flag("--mcp-token"), flag("--password"), flag("--open")
	if i := slices.IndexFunc(args, func(a string) bool { return a == "--role" || a == "-role" }); i >= 0 && i+1 < len(args) {
		c.role, c.roleFlag = strings.ToLower(args[i+1]), true
		args = slices.Delete(args, i, i+2)
	}
	wantArgs := 1
	switch cmd {
	case "list":
		wantArgs = 0
	case "role":
		wantArgs = 2
	}
	if !slices.Contains([]string{"add", "passwd", "token", "role", "disable", "enable", "invite", "reset",
		"remove", "list"}, cmd) ||
		len(args) != wantArgs || c.tokenFlag && cmd != "add" || c.roleFlag && cmd != "add" && cmd != "invite" ||
		(c.passwordFlag || c.openFlag) && cmd != "remove" || c.passwordFlag && c.openFlag ||
		slices.ContainsFunc(args, func(a string) bool { return strings.HasPrefix(a, "-") }) {
		return false
	}
	if cmd == "role" {
		c.role, c.roleFlag = strings.ToLower(args[1]), true
	}
	if cmd != "list" {
		c.name = strings.ToLower(strings.TrimSpace(args[0]))
	}
	return true
}

// save writes the settings file; false (said) when it can't.
func (c *userCommand) save() bool {
	if err := saveConfig(c.f); err != nil {
		c.fail("%v", err)
		return false
	}
	return true
}

func (c *userCommand) list() int {
	if len(c.f.Users) == 0 {
		fmt.Fprintln(c.stdout, "No users. Add one with: zonewiki user add NAME")
	}
	s := settingsOf(c.f.Read)
	for _, u := range c.f.Users {
		if user := s.Users[u.Name]; user != nil {
			fmt.Fprintln(c.stdout, userLine(user))
		} else {
			fmt.Fprintln(c.stdout, "  "+u.Name)
		}
	}
	return 0
}

// link: invite NAME and reset NAME, a link shown once.
func (c *userCommand) link() int {
	role := c.role
	if role == "" {
		role = roleEditor
	}
	token, err := makeLink(settingsOf(c.f.Read), c.cmd, c.name, role, "")
	if err != nil {
		return c.fail("%v", err)
	}
	if c.cmd == "invite" {
		fmt.Fprintf(c.stdout, "An invite for %s, as %s, for 7 days. Send them this link (shown only now; "+
			"it works once):\n", c.name, withArticle(role))
	} else {
		fmt.Fprintf(c.stdout, "A link for %s to set a new password, for 24 hours. Send them this link "+
			"(shown only now; it works once):\n", c.name)
	}
	base := settingsOf(c.f.Read).BaseURL
	fmt.Fprintln(c.stdout, linkURL(base, nil, token))
	if base == "" {
		fmt.Fprintln(c.stdout, "Put Zone's address in front of it (or set base-url: zonewiki config set base-url https://…).")
	}
	return 0
}

// setPassword: add NAME and passwd NAME.
func (c *userCommand) setPassword() int {
	add := c.cmd == "add"
	password, ok := askPassword(c.stdin, c.stderr)
	if !ok {
		return 1
	}
	c.f.Set(c.name, "password", hashPassword(password))
	// The first user is an admin, unless told otherwise.
	role := c.role
	firstAdmin := add && len(c.f.Users) == 0 && !c.roleFlag
	if firstAdmin {
		role = roleAdmin
	}
	if add && role != "" && role != roleEditor {
		c.f.Set(c.name, "role", role)
	}
	// The first user: the wiki's own password and token give way (with
	// users each has their own).
	var replaced []string
	for _, key := range []string{"password", "mcp-token"} {
		if _, ok := c.f.Top[key]; ok && add {
			c.f.Unset("", key)
			replaced = append(replaced, key)
		}
	}
	var token string
	if c.tokenFlag {
		token = newToken()
		c.f.Set(c.name, "mcp-token", hashToken(token))
	}
	if !c.save() {
		return 1
	}
	if firstAdmin {
		fmt.Fprintf(c.stdout, "Added %s to %s, as an admin: the first user manages the others.\n", c.name, configName())
	} else if add {
		fmt.Fprintf(c.stdout, "Added %s to %s.\n", c.name, configName())
	} else {
		fmt.Fprintf(c.stdout, "Changed %s's password.\n", c.name)
	}
	if token != "" {
		fmt.Fprintf(c.stdout, "%s's MCP token (shown only now): %s\n", c.name, token)
	}
	if slices.Contains(replaced, "password") {
		fmt.Fprintln(c.stdout, "The wiki's password no longer works: everyone signs in as a user now.")
	}
	if slices.Contains(replaced, "mcp-token") {
		fmt.Fprintf(c.stdout, "The wiki's MCP token no longer works: each user has their own "+
			"(zonewiki user token %s).\n", c.name)
	}
	return 0
}

func (c *userCommand) newToken() int {
	token := newToken()
	c.f.Set(c.name, "mcp-token", hashToken(token))
	if !c.save() {
		return 1
	}
	fmt.Fprintf(c.stdout, "%s's new MCP token (shown only now; the old one no longer works): %s\n", c.name, token)
	return 0
}

func (c *userCommand) setRole() int {
	if c.role == roleEditor {
		c.f.Unset(c.name, "role") // the default
	} else {
		c.f.Set(c.name, "role", c.role)
	}
	if !c.save() {
		return 1
	}
	fmt.Fprintf(c.stdout, "%s is %s now.\n", c.name, withArticle(c.role))
	return 0
}

// setDisabled: disable NAME and enable NAME.
func (c *userCommand) setDisabled() int {
	disable := c.cmd == "disable"
	if disable {
		c.f.Set(c.name, "disabled", "yes")
	} else {
		c.f.Unset(c.name, "disabled")
		c.f.Unset(c.name, "pending") // approved
	}
	if !c.save() {
		return 1
	}
	if disable {
		fmt.Fprintf(c.stdout, "Disabled %s: they can't sign in, and their MCP token no longer works. "+
			"Their files stay. Undo it with: zonewiki user enable %s\n", c.name, c.name)
	} else {
		fmt.Fprintf(c.stdout, "Enabled %s: they can sign in again.\n", c.name)
	}
	return 0
}

func (c *userCommand) remove() int {
	last := len(c.f.Users) == 1
	switch {
	case last && !c.passwordFlag && !c.openFlag:
		return c.fail("%s is the last user. Without users the wiki has no login and private/ is an "+
			"ordinary folder everyone sees. Remove them with --password to set the wiki's password "+
			"instead, or with --open for no login.", c.name)
	case !last && (c.passwordFlag || c.openFlag):
		return c.fail("--password and --open are for the last user; there are others.")
	}
	c.f.RemoveUser(c.name)
	if c.passwordFlag {
		password, ok := askPassword(c.stdin, c.stderr)
		if !ok {
			return 1
		}
		c.f.Set("", "password", hashPassword(password))
	}
	if c.openFlag {
		c.f.Set("", "no-login", "yes") // on purpose: no first-run page (setup.go)
	}
	if !c.save() {
		return 1
	}
	fmt.Fprintf(c.stdout, "Removed %s from %s.\n", c.name, configName())
	revokeGrantsOf(c.name) // their assistants' connections (grants.go)
	switch {
	case c.openFlag:
		fmt.Fprintln(c.stdout, "No users and no password now: the wiki has no login, and private/ is an "+
			"ordinary folder. A running Zone takes this only at its next start (restart it).")
	case c.passwordFlag:
		fmt.Fprintln(c.stdout, "No users now: the wiki's password signs in, and private/ is an ordinary folder.")
	}
	dir := privateDir + "/" + c.name
	if !isDir(filepath.Join(cfg.Data, dir)) {
		return 0
	}
	if last {
		fmt.Fprintf(c.stdout, "Their files stay in the wiki: %s/.\n", dir)
	} else {
		fmt.Fprintf(c.stdout, "Their files stay in the wiki, seen by no one: %s/. Move or delete them there.\n", dir)
	}
	return 0
}

// userLine: a user as zonewiki user list shows them ("  alice (admin, MCP
// token)"); an editor's role goes unsaid, as it is the default.
func userLine(u *User) string {
	var facts []string
	if u.Role != roleEditor && u.Role != "" {
		facts = append(facts, u.Role)
	}
	if u.MCPToken != "" {
		facts = append(facts, "MCP token")
	}
	switch {
	case u.Pending:
		facts = append(facts, "waiting for approval: zonewiki user enable "+u.Name)
	case u.Disabled:
		facts = append(facts, "disabled")
	}
	if u.Email != "" {
		facts = append(facts, u.Email)
	}
	if len(facts) == 0 {
		return "  " + u.Name
	}
	return "  " + u.Name + " (" + strings.Join(facts, ", ") + ")"
}

// withArticle: "an admin", "an editor", "a viewer".
func withArticle(role string) string {
	if role == roleViewer {
		return "a " + role
	}
	return "an " + role
}

func configMain(args []string, getenv func(string) string, stdin io.Reader, stdout, stderr io.Writer) int {
	if isHelp(firstOf(args)) {
		fmt.Fprintln(stdout, configUsage)
		return 0
	}
	wantArgs := map[string]int{"check": 0, "passwd": 0, "token": 0, "set": 2, "unset": 1}
	if n, ok := wantArgs[firstOf(args)]; !ok || len(args)-1 < n || n < 2 && len(args)-1 != n {
		fmt.Fprintln(stderr, configUsage)
		return 2
	}
	fail := func(format string, a ...any) int {
		fmt.Fprintf(stderr, "zonewiki config: "+format+"\n", a...)
		return 1
	}
	f, err := openConfig(getenv)
	if err != nil {
		return fail("%v", err)
	}
	switch args[0] {
	case "check":
		s, err := loadSettings(f)
		if err != nil {
			return fail("%v", err)
		}
		showSettings(stdout, f, s)
	case "set", "unset":
		key := args[1]
		switch {
		case key == "password" && args[0] == "set":
			return fail("the password is set with: zonewiki config passwd")
		case key == "mcp-token" && args[0] == "set":
			return fail("the MCP token is made with: zonewiki config token")
		case !knownKey(key):
			return fail("%q isn't a setting Zone knows.", key)
		}
		if args[0] == "set" {
			value := strings.Join(args[2:], " ")
			if strings.ContainsAny(value, "\r\n") {
				return fail("a value is one line.")
			}
			f.Set("", key, value)
		} else {
			f.Unset("", key)
		}
		if err := saveConfig(f); err != nil {
			return fail("%v", err)
		}
		if args[0] == "unset" && key == "password" && len(f.Users) == 0 {
			fmt.Fprintln(stdout, "The wiki has no login now. A running Zone takes this only at its next "+
				"start (restart it).")
		}
	case "passwd", "token":
		if len(f.Users) > 0 {
			return fail("with users, each has their own: zonewiki user %s NAME", args[0])
		}
		if args[0] == "passwd" {
			password, ok := askPassword(stdin, stderr)
			if !ok {
				return 1
			}
			f.Set("", "password", hashPassword(password))
			if err := saveConfig(f); err != nil {
				return fail("%v", err)
			}
			fmt.Fprintln(stdout, "Changed the wiki's password.")
			return 0
		}
		token := newToken()
		f.Set("", "mcp-token", hashToken(token))
		if err := saveConfig(f); err != nil {
			return fail("%v", err)
		}
		fmt.Fprintf(stdout, "The wiki's new MCP token (shown only now; an old one no longer works): %s\n", token)
	}
	return 0
}

func firstOf(args []string) string {
	if len(args) == 0 {
		return ""
	}
	return args[0]
}

// showSettings prints the settings in effect and where each comes from,
// hashes masked.
func showSettings(w io.Writer, f *settingsfile.File, s *Settings) {
	fmt.Fprintf(w, "Settings in effect (%s, else Zone's default):\n", configName())
	for _, k := range configKeys {
		v, set := f.Top[k]
		value, from := v.Value, fmt.Sprintf("line %d", v.Line)
		if !set {
			value, from = "", "default"
		}
		switch {
		case k == "noshare" && !set:
			value = "#noshare"
		case value == "":
			value = "(none)"
		case k == "password" || k == "mcp-token":
			value = "(set)"
		}
		fmt.Fprintf(w, "  %-18s %s  [%s]\n", k, value, from)
	}
	if s.Users == nil {
		fmt.Fprintf(w, "  %-18s (none)\n", "users")
		return
	}
	var names []string
	for _, name := range sortedKeys(s.Users) {
		names = append(names, strings.TrimSpace(userLine(s.Users[name])))
	}
	fmt.Fprintf(w, "  %-18s %s\n", "users", strings.Join(names, ", "))
}
