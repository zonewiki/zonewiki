// The settings file's commands (docs/spec/users.md): zonewiki user adds,
// changes and removes users, zonewiki config checks the settings, sets the
// wiki's password and token, and moves settings out of the environment.
// They edit .zone/config in the wiki ZONE_DATA names, keeping its comments,
// and refuse a change that would leave settings Zone doesn't start with; a
// running Zone takes the change within a second and commits it (else Zone
// at its next start): git stays with the one process. New MCP tokens are
// shown once and kept as hashes.
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
                          the first replaces the wiki's password and MCP token
  passwd NAME             set a user's password (asks for it)
  token NAME              give a user a new MCP token (the old one stops working)
  remove NAME             remove a user (their private pages and tasks stay); the last one only
    [--password|--open]   with the wiki's password instead (asks for it), or with no login
  list                    the users, and whether each has an MCP token`

const configUsage = `Usage: zonewiki config COMMAND
  check            check the settings and show those in effect
  set KEY VALUE    set one of the wiki's settings (hosts, locale, noshare …)
  unset KEY        back to its default (password: no login; mcp-token: /mcp off)
  passwd           set the wiki's password, without users (asks for it)
  token            give the wiki a new MCP token, without users (the old one stops working)
  import           move the wiki's settings from the environment (.env, before 2026-10) into the settings file`

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
	if err == nil {
		_, err = loadSettings(parsed)
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

func userMain(args []string, getenv func(string) string, stdin io.Reader, stdout, stderr io.Writer) int {
	fail := func(format string, a ...any) int {
		fmt.Fprintf(stderr, "zonewiki user: "+format+"\n", a...)
		return 1
	}
	if len(args) == 0 {
		fmt.Fprintln(stderr, userUsage)
		return 2
	}
	if isHelp(args[0]) {
		fmt.Fprintln(stdout, userUsage)
		return 0
	}
	cmd, args := args[0], args[1:]
	flag := func(name string) bool { // --name, or -name as Go's flags take it
		is := func(a string) bool { return a == name || a == name[1:] }
		found := slices.ContainsFunc(args, is)
		args = slices.DeleteFunc(args, is)
		return found
	}
	tokenFlag, passwordFlag, openFlag := flag("--mcp-token"), flag("--password"), flag("--open")
	wantArgs := 1
	if cmd == "list" {
		wantArgs = 0
	}
	if !slices.Contains([]string{"add", "passwd", "token", "remove", "list"}, cmd) || len(args) != wantArgs ||
		tokenFlag && cmd != "add" || (passwordFlag || openFlag) && cmd != "remove" || passwordFlag && openFlag {
		fmt.Fprintln(stderr, userUsage)
		return 2
	}
	f, err := openConfig(getenv)
	if err != nil {
		return fail("%v", err)
	}
	if cmd == "list" {
		if len(f.Users) == 0 {
			fmt.Fprintln(stdout, "No users. Add one with: zonewiki user add NAME")
		}
		for _, u := range f.Users {
			fmt.Fprintln(stdout, userLine(u.Name, u.Values["mcp-token"].Value != ""))
		}
		return 0
	}
	name := strings.ToLower(strings.TrimSpace(args[0]))
	if !userNameRE.MatchString(name) {
		return fail("%q isn't a user name. Use lowercase letters, digits and hyphens (e.g. alice, mary-ann).", name)
	}
	exists := f.User(name) != nil
	switch {
	case cmd == "add" && exists:
		return fail("%s is a user already. Change the password with: zonewiki user passwd %s", name, name)
	case cmd != "add" && !exists:
		return fail("there is no user %s in %s.", name, configName())
	}
	switch cmd {
	case "add", "passwd":
		password, ok := askPassword(stdin, stderr)
		if !ok {
			return 1
		}
		f.Set(name, "password", hashPassword(password))
		// The first user: the wiki's own password and token give way (with
		// users each has their own).
		var replaced []string
		for _, key := range []string{"password", "mcp-token"} {
			if _, ok := f.Top[key]; ok && cmd == "add" {
				f.Unset("", key)
				replaced = append(replaced, key)
			}
		}
		var token string
		if tokenFlag {
			token = newToken()
			f.Set(name, "mcp-token", hashToken(token))
		}
		if err := saveConfig(f); err != nil {
			return fail("%v", err)
		}
		if cmd == "add" {
			fmt.Fprintf(stdout, "Added %s to %s.\n", name, configName())
		} else {
			fmt.Fprintf(stdout, "Changed %s's password.\n", name)
		}
		if token != "" {
			fmt.Fprintf(stdout, "%s's MCP token (shown only now): %s\n", name, token)
		}
		if slices.Contains(replaced, "password") {
			fmt.Fprintln(stdout, "The wiki's password no longer works: everyone signs in as a user now.")
		}
		if slices.Contains(replaced, "mcp-token") {
			fmt.Fprintf(stdout, "The wiki's MCP token no longer works: each user has their own "+
				"(zonewiki user token %s).\n", name)
		}
	case "token":
		token := newToken()
		f.Set(name, "mcp-token", hashToken(token))
		if err := saveConfig(f); err != nil {
			return fail("%v", err)
		}
		fmt.Fprintf(stdout, "%s's new MCP token (shown only now; the old one no longer works): %s\n", name, token)
	case "remove":
		last := len(f.Users) == 1
		switch {
		case last && !passwordFlag && !openFlag:
			return fail("%s is the last user. Without users the wiki has no login and private/ is an "+
				"ordinary folder everyone sees. Remove them with --password to set the wiki's password "+
				"instead, or with --open for no login.", name)
		case !last && (passwordFlag || openFlag):
			return fail("--password and --open are for the last user; there are others.")
		}
		f.RemoveUser(name)
		if passwordFlag {
			password, ok := askPassword(stdin, stderr)
			if !ok {
				return 1
			}
			f.Set("", "password", hashPassword(password))
		}
		if err := saveConfig(f); err != nil {
			return fail("%v", err)
		}
		fmt.Fprintf(stdout, "Removed %s from %s.\n", name, configName())
		switch {
		case openFlag:
			fmt.Fprintln(stdout, "No users and no password now: the wiki has no login, and private/ is an "+
				"ordinary folder. A running Zone takes this only at its next start (restart it).")
		case passwordFlag:
			fmt.Fprintln(stdout, "No users now: the wiki's password signs in, and private/ is an ordinary folder.")
		}
		var kept []string
		if dir := privateDir + "/" + name; isDir(filepath.Join(cfg.Data, dir)) {
			kept = append(kept, dir+"/")
		}
		if len(kept) > 0 && !last {
			fmt.Fprintf(stdout, "Their files stay in the wiki, seen by no one: %s. Move or delete them there.\n",
				strings.Join(kept, ", "))
		} else if len(kept) > 0 {
			fmt.Fprintf(stdout, "Their files stay in the wiki: %s.\n", strings.Join(kept, ", "))
		}
	}
	return 0
}

func userLine(name string, token bool) string {
	if token {
		return "  " + name + " (MCP token)"
	}
	return "  " + name
}

func configMain(args []string, getenv func(string) string, stdin io.Reader, stdout, stderr io.Writer) int {
	if isHelp(firstOf(args)) {
		fmt.Fprintln(stdout, configUsage)
		return 0
	}
	wantArgs := map[string]int{"check": 0, "passwd": 0, "token": 0, "import": 0, "set": 2, "unset": 1}
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
		if err == nil {
			err = staleEnv(getenv)
		}
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
	case "import":
		return importEnv(f, getenv, stdout, stderr)
	}
	return 0
}

func firstOf(args []string) string {
	if len(args) == 0 {
		return ""
	}
	return args[0]
}

// importEnv moves the wiki's settings from the environment, where Zone
// read them until 2026-10, into the file: a plain LEAF_PASSWORD and the MCP
// tokens as their hashes (the clients keep their tokens), LEAF_USERS and
// each user's LEAF_USER_<NAME>_… as sections.
func importEnv(f *settingsfile.File, getenv func(string) string, stdout, stderr io.Writer) int {
	var moved []string
	for _, k := range configKeys {
		value := strings.TrimSpace(getenv(k.env))
		if value == "" {
			continue
		}
		switch k.key {
		case "password":
			value = hashPassword(value)
		case "mcp-token":
			value = hashToken(value)
		}
		f.Set("", k.key, value)
		moved = append(moved, k.env)
	}
	if strings.TrimSpace(getenv("LEAF_USERS")) != "" {
		users, err := envUsers(getenv)
		if err != nil {
			fmt.Fprintf(stderr, "zonewiki config: %v\n", err)
			return 1
		}
		moved = append(moved, "LEAF_USERS")
		for _, u := range users {
			env := "LEAF_USER_" + strings.ToUpper(strings.ReplaceAll(u.Name, "-", "_"))
			f.Set(u.Name, "password", u.PasswordHash)
			moved = append(moved, env+"_PASSWORD")
			if u.MCPToken != "" {
				f.Set(u.Name, "mcp-token", hashToken(u.MCPToken))
				moved = append(moved, env+"_MCP_TOKEN")
			}
		}
	}
	if getenv("LEAF_SECRET_KEY") != "" {
		moved = append(moved, "LEAF_SECRET_KEY (no longer used: Zone keeps its key in .zone/secret_key)")
	}
	if len(moved) == 0 {
		fmt.Fprintf(stdout, "Nothing to import: the environment sets none of the settings %s holds.\n", configName())
		return 0
	}
	if err := saveConfig(f); err != nil {
		fmt.Fprintf(stderr, "zonewiki config: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "Moved into %s. Now delete these from .env and restart Zone "+
		"(until then it refuses to start):\n", configName())
	for _, name := range moved {
		fmt.Fprintln(stdout, "  "+name)
	}
	return 0
}

// envUsers: the users as the environment held them (LEAF_USERS=alice,bob,
// and per user LEAF_USER_<NAME>_PASSWORD=<hash>, LEAF_USER_<NAME>_MCP_TOKEN=
// <token>, the name upper case with "_" for "-"), in order.
func envUsers(getenv func(string) string) ([]*User, error) {
	var users []*User
	seen := map[string]bool{}
	for _, name := range strings.Split(getenv("LEAF_USERS"), ",") {
		name = strings.ToLower(strings.TrimSpace(name))
		if name == "" || seen[name] {
			continue
		}
		if !userNameRE.MatchString(name) {
			return nil, fmt.Errorf("LEAF_USERS: %q isn't a user name.", name)
		}
		seen[name] = true
		env := "LEAF_USER_" + strings.ToUpper(strings.ReplaceAll(name, "-", "_"))
		hash := strings.TrimSpace(getenv(env + "_PASSWORD"))
		if _, _, _, err := parseHash(hash); err != nil {
			return nil, fmt.Errorf("%s_PASSWORD isn't a password hash (%v): set %s's password afterwards "+
				"with zonewiki user passwd %s", env, err, name, name)
		}
		users = append(users, &User{Name: name, PasswordHash: hash,
			MCPToken: strings.TrimSpace(getenv(env + "_MCP_TOKEN"))})
	}
	if len(users) == 0 {
		return nil, errors.New("LEAF_USERS names no one.")
	}
	return users, nil
}

// showSettings prints the settings in effect and where each comes from,
// hashes masked.
func showSettings(w io.Writer, f *settingsfile.File, s *Settings) {
	fmt.Fprintf(w, "Settings in effect (%s, else Zone's default):\n", configName())
	for _, k := range configKeys {
		v, set := f.Top[k.key]
		value, from := v.Value, fmt.Sprintf("line %d", v.Line)
		if !set {
			value, from = "", "default"
		}
		switch {
		case k.key == "noshare" && !set:
			value = "#noshare"
		case value == "":
			value = "(none)"
		case k.key == "password" || k.key == "mcp-token":
			value = "(set)"
		}
		fmt.Fprintf(w, "  %-18s %s  [%s]\n", k.key, value, from)
	}
	if s.Users == nil {
		fmt.Fprintf(w, "  %-18s (none)\n", "users")
		return
	}
	var names []string
	for _, name := range sortedKeys(s.Users) {
		names = append(names, strings.TrimSpace(userLine(name, s.Users[name].MCPToken != "")))
	}
	fmt.Fprintf(w, "  %-18s %s\n", "users", strings.Join(names, ", "))
}
