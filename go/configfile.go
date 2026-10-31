// The settings file, .leaf/config in the wiki (docs/spec/users.md,
// docs/spec/deployment.md): the wiki's settings and its users, which Zone
// reloads when the file changes, so adding a user or changing a password
// needs no restart. A small INI, read by Zone's own parser:
//
//	# Zone's settings. Zone reloads this file when it changes.
//	locale = de-AT
//	hosts = wiki.example.org, nas.local
//
//	[user alice]
//	password = pbkdf2-sha256:600000:<salt>:<hash>
//	mcp-token = sha256:<the token's hash>
//
// The environment (.env) holds only what the deployment needs (ZONE_DATA,
// ZONE_ALLOW_REMOTE …: Config, config.go); a wiki setting left there stops
// Zone, pointing to zonewiki config import. The file is in .leaf/, out of
// safePath's reach; it is committed (the rest of .leaf/ isn't), so its MCP
// tokens are kept only as hashes, passwords as PBKDF2 hashes.
package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// configName(): the file as messages name it, relative to the wiki
// (.zone/config, or .leaf/config while Zone reads that one).
func configName() string {
	if cfg == nil {
		return stateDirName + "/config"
	}
	return filepath.Base(stateDirIn(cfg.Data)) + "/config"
}

func configPath() string { return statePath("config") }

// configKeys: the keys outside a [user …] section, in the order `leaf
// config check` shows them, with the environment variables that held them
// until 2026-10 (zonewiki config import reads them; set, they stop Zone).
var configKeys = []struct{ key, env string }{
	{"password", "LEAF_PASSWORD"}, {"mcp-token", "LEAF_MCP_TOKEN"}, {"hosts", "LEAF_HOSTS"},
	{"base-url", "LEAF_BASE_URL"}, {"git-remote", "LEAF_GIT_REMOTE"}, {"noshare", "LEAF_NOSHARE"},
	{"locale", "LEAF_LOCALE"}, {"date-format", "LEAF_DATE_FORMAT"}, {"query-log", "LEAF_QUERY_LOG"},
	{"semantic-min-score", "LEAF_SEMANTIC_MIN_SCORE"},
}

// userKeys: the keys of a [user …] section.
var userKeys = []string{"password", "mcp-token"}

func knownKey(key string) bool {
	for _, k := range configKeys {
		if k.key == key {
			return true
		}
	}
	return false
}

// configFile: the file as read, its lines (kept for rewriting) and what
// they set.
type configFile struct {
	lines []string
	top   map[string]configValue // key → value, outside every section
	users []configUser           // the [user …] sections, in order
	read  string                 // the text as read (a command's check that no one changed it since)
}

type configValue struct {
	value string
	line  int // 1-based
}

type configUser struct {
	name   string
	line   int
	values map[string]configValue
}

func (f *configFile) user(name string) *configUser {
	for i := range f.users {
		if f.users[i].name == name {
			return &f.users[i]
		}
	}
	return nil
}

// lineError: what is wrong on a line of the file.
func lineError(n int, format string, args ...any) error {
	return fmt.Errorf("%s line %d: %s", configName(), n, fmt.Sprintf(format, args...))
}

// sectionName: a line's section header's name ("[user alice]" → "user
// alice"), or false if the line isn't one.
func sectionName(line string) (string, bool) {
	t := strings.TrimSpace(line)
	if !strings.HasPrefix(t, "[") || !strings.HasSuffix(t, "]") {
		return "", false
	}
	return strings.Join(strings.Fields(t[1:len(t)-1]), " "), true
}

// isComment: a blank line, or one starting with "#" ("noshare = #tag" is a
// value, not a comment).
func isComment(line string) bool {
	t := strings.TrimSpace(line)
	return t == "" || strings.HasPrefix(t, "#")
}

// parseConfig reads the file's text; an unknown key, a section that isn't
// a user's, a key given twice: an error naming its line, as a typo
// shouldn't leave Zone on a default.
func parseConfig(text string) (*configFile, error) {
	read := text
	text = strings.TrimPrefix(strings.ReplaceAll(text, "\r\n", "\n"), "\ufeff")
	f := &configFile{lines: strings.Split(strings.TrimSuffix(text, "\n"), "\n"), top: map[string]configValue{},
		read: read}
	if text == "" {
		f.lines = nil
	}
	values := f.top
	known := knownKey
	for i, line := range f.lines {
		n := i + 1
		if isComment(line) {
			continue
		}
		if name, ok := sectionName(line); ok {
			kind, user, _ := strings.Cut(name, " ")
			if kind != "user" {
				return nil, lineError(n, "[%s] isn't a section Zone knows. Users are [user NAME].", name)
			}
			if !userNameRE.MatchString(user) {
				return nil, lineError(n, "%q isn't a user name. Use lowercase letters, digits and hyphens "+
					"(e.g. alice, mary-ann).", user)
			}
			if u := f.user(user); u != nil {
				return nil, lineError(n, "[user %s] is there twice (line %d).", user, u.line)
			}
			f.users = append(f.users, configUser{name: user, line: n, values: map[string]configValue{}})
			values = f.users[len(f.users)-1].values
			known = func(key string) bool { return slices.Contains(userKeys, key) }
			continue
		}
		key, value, found := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if !found || key == "" {
			return nil, lineError(n, "not a setting (key = value), a [user NAME] or a # comment.")
		}
		if !known(key) {
			if len(f.users) > 0 {
				return nil, lineError(n, "%q isn't a user's setting. A user has %s; the wiki's settings "+
					"go before the first [user …].", key, strings.Join(userKeys, " and "))
			}
			return nil, lineError(n, "%q isn't a setting Zone knows.", key)
		}
		if v, twice := values[key]; twice {
			return nil, lineError(n, "%s is set twice (line %d).", key, v.line)
		}
		values[key] = configValue{strings.TrimSpace(value), n}
	}
	return f, nil
}

// readConfigFile reads the file at path; a missing file is an empty one
// (nil). Zone warns when others may read it: it holds password hashes and
// tokens.
func readConfigFile(path string) (*configFile, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("%s: %w", configName(), err)
	}
	return parseConfig(string(b))
}

// warnReadable: the file is readable by others than its owner.
func warnReadable(path string) {
	if st, err := os.Stat(path); err == nil && st.Mode().Perm()&0o077 != 0 {
		logf("%s can be read by others (mode %o): it holds password hashes and tokens. "+
			"Make it the owner's only: chmod 600 %s", configName(), st.Mode().Perm(), path)
	}
}

// Settings: what the file sets, reloaded when it changes; settings() is
// the current one.
type Settings struct {
	PasswordHash     string           // the login gate without users
	MCPToken         string           // /mcp's token without users, as kept (tokenMatches); "": /mcp is off
	Users            map[string]*User // users.go; nil: no users
	BaseURL          string           // Zone's address as the user's browser knows it
	QueryLog         bool             // keep search_wiki's queries in .leaf/queries.jsonl
	GitRemote        string
	Hosts            map[string]bool // names besides localhost, without port, lowercase
	NoshareTags      map[string]bool
	NoshareFolder    []string
	Locale           string   // a BCP 47 tag for the browser's Intl
	DateFormat       string   // "" or "iso"
	SemanticMinScore *float64 // the semantic search's floor; nil: the model's
}

// authOn: the wiki has a login gate, a password or users.
func (s *Settings) authOn() bool { return s.PasswordHash != "" || s.Users != nil }

var current atomic.Pointer[Settings]

func settings() *Settings { return current.Load() }

// staleEnv: the wiki's settings still in the environment, where Zone no
// longer reads them; Zone stops rather than run without them (a forgotten
// LEAF_PASSWORD would leave the wiki open).
func staleEnv(getenv func(string) string) error {
	var moved []string
	for _, k := range configKeys {
		if strings.TrimSpace(getenv(k.env)) != "" {
			moved = append(moved, k.env)
		}
	}
	if strings.TrimSpace(getenv("LEAF_USERS")) != "" {
		moved = append(moved, "LEAF_USERS")
	}
	if len(moved) > 0 {
		verb := "are"
		if len(moved) == 1 {
			verb = "is"
		}
		return fmt.Errorf("%s %s set in the environment (.env), but Zone reads the wiki's settings only from "+
			"%s now. Move them there with ./run.sh config import (zonewiki config import), then delete them from .env.",
			strings.Join(moved, ", "), verb, configName())
	}
	if getenv("LEAF_SECRET_KEY") != "" {
		return errors.New("LEAF_SECRET_KEY is set in the environment (.env), but Zone no longer reads it: it " +
			"keeps its own key in .leaf/secret_key. Delete it from .env (everyone signs in once more).")
	}
	return nil
}

// loadSettings: the settings the file f (nil: none) makes, or what is
// wrong with them.
func loadSettings(f *configFile) (*Settings, error) {
	if f == nil {
		f = &configFile{top: map[string]configValue{}}
	}
	// get: a setting, and how a message names it (".leaf/config line 3:
	// locale = ").
	get := func(key string) (value, where string, set bool) {
		v, set := f.top[key]
		return v.value, fmt.Sprintf("%s line %d: %s = ", configName(), v.line, key), set
	}
	s := &Settings{Hosts: map[string]bool{}}
	var where string
	var set bool
	if s.PasswordHash, where, set = get("password"); set {
		if _, _, _, err := parseHash(s.PasswordHash); err != nil {
			return nil, fmt.Errorf("%s… isn't a password hash (%v). Set the password with: zonewiki config passwd",
				where, err)
		}
	}
	if s.MCPToken, where, set = get("mcp-token"); set && !isTokenHash(s.MCPToken) {
		return nil, fmt.Errorf("%s… isn't a token's hash (sha256:<64 hex digits>). Make a token with: "+
			"zonewiki config token", where)
	}
	baseURL, _, _ := get("base-url")
	s.BaseURL = strings.TrimRight(baseURL, "/")
	queryLog, where, _ := get("query-log")
	s.QueryLog = truthy(queryLog)
	if !s.QueryLog && !slices.Contains([]string{"", "0", "false", "no"}, strings.ToLower(queryLog)) {
		return nil, fmt.Errorf("%s%s isn't yes or no.", where, queryLog)
	}
	s.GitRemote, _, _ = get("git-remote")
	hosts, _, _ := get("hosts")
	for _, h := range strings.Split(hosts, ",") {
		if strings.TrimSpace(h) != "" {
			s.Hosts[hostName(h)] = true
		}
	}
	// Not set: the default; set but empty: nothing hidden.
	noshare, _, set := get("noshare")
	if !set {
		noshare = "#noshare"
	}
	s.NoshareTags, s.NoshareFolder = parseNoshare(noshare)
	locale, where, _ := get("locale")
	if s.Locale = locale; s.Locale != "" && !localeRE.MatchString(s.Locale) {
		return nil, fmt.Errorf("%s%s isn't a locale. Use a tag like de-AT or en-GB.", where, s.Locale)
	}
	dateFormat, where, _ := get("date-format")
	if s.DateFormat = strings.ToLower(dateFormat); s.DateFormat != "" && s.DateFormat != "iso" {
		return nil, fmt.Errorf("%s%s isn't known. Use iso, or leave it unset.", where, s.DateFormat)
	}
	if score, where, set := get("semantic-min-score"); set && score != "" {
		f, err := strconv.ParseFloat(score, 64)
		if err != nil || f < -1 || f > 1 {
			return nil, fmt.Errorf("%s%s isn't a similarity between -1 and 1 (e.g. 0.4).", where, score)
		}
		s.SemanticMinScore = &f
	}
	var err error
	if s.Users, err = fileUsers(f); err != nil {
		return nil, err
	}
	if s.Users != nil {
		// With users each has their own password and token: a shared one
		// would make the user unknown.
		for _, key := range []string{"password", "mcp-token"} {
			if v, ok := f.top[key]; ok {
				return nil, lineError(v.line, "%s is the wiki's, but there are users: each has their own. "+
					"Remove this line.", key)
			}
		}
	}
	return s, nil
}

// fileUsers: the file's [user …] sections as users; nil without any.
func fileUsers(f *configFile) (map[string]*User, error) {
	if len(f.users) == 0 {
		return nil, nil
	}
	users := map[string]*User{}
	tokens := map[string]string{}
	for _, u := range f.users {
		hash, ok := u.values["password"]
		if !ok || hash.value == "" {
			return nil, lineError(u.line, "[user %s] has no password. Set one with: zonewiki user passwd %s",
				u.name, u.name)
		}
		if _, _, _, err := parseHash(hash.value); err != nil {
			return nil, lineError(hash.line, "%s's password isn't a password hash (%v). Set one with: "+
				"zonewiki user passwd %s", u.name, err, u.name)
		}
		token, hasToken := u.values["mcp-token"]
		if hasToken && !isTokenHash(token.value) {
			return nil, lineError(token.line, "%s's mcp-token isn't a token's hash (sha256:<64 hex digits>). "+
				"Make a token with: zonewiki user token %s", u.name, u.name)
		}
		if other, taken := tokens[token.value]; taken && token.value != "" {
			return nil, lineError(token.line, "%s's mcp-token is %s's too. Give each user a token of their "+
				"own: zonewiki user token %s", u.name, other, u.name)
		}
		tokens[token.value] = u.name
		users[u.name] = &User{Name: u.name, PasswordHash: hash.value, MCPToken: token.value}
	}
	return users, nil
}

// MCP tokens: in the settings file (committed) only as "sha256:" and the
// token's SHA-256 in hex. A token is 32 random bytes,
// so its hash tells whoever has the repository nothing.
const tokenHashPrefix = "sha256:"

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return tokenHashPrefix + hex.EncodeToString(sum[:])
}

func isTokenHash(kept string) bool {
	h, ok := strings.CutPrefix(kept, tokenHashPrefix)
	_, err := hex.DecodeString(h)
	return ok && len(h) == 64 && err == nil && strings.ToLower(h) == h
}

// tokenMatches: given is the token kept (its hash; a test's may be plain);
// never an empty one.
func tokenMatches(given, kept string) bool {
	if given == "" || kept == "" {
		return false
	}
	if isTokenHash(kept) {
		return subtle.ConstantTimeCompare([]byte(hashToken(given)), []byte(kept)) == 1
	}
	return sameSecret(given, kept)
}

// Reloading: the request checks call reloadSettings, which looks at the
// file at most once a second (no watcher: no dependency, no goroutine).
// A file with an error keeps the settings Zone has, so a broken edit
// never locks everyone out or opens the wiki; health() reports it.
var (
	reloadMu    sync.Mutex
	reloadEvery = time.Second
	lastLook    time.Time
	lastStat    os.FileInfo // the file as last read; nil: there was none
	settingsErr atomic.Pointer[string]
)

// startSettings: the settings Zone starts with, and the file's state for
// reloads.
func startSettings(s *Settings) {
	current.Store(s)
	settingsErr.Store(nil)
	reloadMu.Lock()
	lastStat, _ = os.Stat(configPath())
	lastLook = time.Now()
	reloadMu.Unlock()
	if lastStat != nil {
		warnReadable(configPath())
	}
}

// settingsError: why the file's last change wasn't taken ("": it was).
func settingsError() string {
	if e := settingsErr.Load(); e != nil {
		return *e
	}
	return ""
}

func reloadSettings() {
	// The git work after the lock: the other requests wait only for the
	// look at the file, not for a commit.
	if old, s := takeSettings(); s != nil {
		applySettings(old, s)
		commitSettings(settingsChange(old, s))
	}
}

// takeSettings: a changed file's settings, now in effect, and those they
// replace; nil if the file is as it was or has an error.
func takeSettings() (old, s *Settings) {
	reloadMu.Lock()
	defer reloadMu.Unlock()
	if time.Since(lastLook) < reloadEvery {
		return nil, nil
	}
	lastLook = time.Now()
	path := configPath()
	st, err := os.Stat(path)
	if err != nil {
		st = nil
	}
	if st == nil && lastStat == nil || st != nil && lastStat != nil && os.SameFile(st, lastStat) &&
		st.ModTime().Equal(lastStat.ModTime()) && st.Size() == lastStat.Size() {
		return nil, nil
	}
	if st == nil {
		// Gone (or between an editor's delete and write): keep what Zone
		// has, as taking it away could open the wiki.
		lastStat = nil
		settingsFailed(fmt.Errorf("%s was removed: Zone keeps the settings it had until it is back, or a restart", configName()))
		return nil, nil
	}
	lastStat = st
	f, err := readConfigFile(path)
	if err == nil {
		s, err = loadSettings(f)
	}
	old = settings()
	if err == nil && old.authOn() && !s.authOn() {
		// The users (or the password) gone: everyone could get in, and
		// private/ would be an ordinary folder. Only on purpose, a restart.
		err = fmt.Errorf("%s sets no users and no password now, which leaves the wiki without a login "+
			"(and private/ open to everyone): Zone takes that only at its next start", configName())
	}
	if err != nil {
		settingsFailed(err)
		return nil, nil
	}
	current.Store(s)
	settingsErr.Store(nil)
	logf("reloaded %s", configName())
	warnReadable(path)
	return old, s
}

// commitSettings commits the settings file, if it is there and changed:
// at start and at each reload, by Zone, so a change made by hand or with
// zonewiki user (another process, which leaves git to Zone) is in the history.
func commitSettings(message string) {
	if settingsError() == "" && exists(configPath()) {
		gitCommit("", message, configPath())
	}
}

// settingsChange: a commit message saying what changed ("Change settings:
// add user carol, alice's password, locale"), names and keys only.
func settingsChange(old, s *Settings) string {
	var changes []string
	for _, name := range sortedKeys(old.Users) {
		if s.Users[name] == nil {
			changes = append(changes, "remove user "+name)
		}
	}
	for _, name := range sortedKeys(s.Users) {
		was, now := old.Users[name], s.Users[name]
		switch {
		case was == nil:
			changes = append(changes, "add user "+name)
		case was.PasswordHash != now.PasswordHash:
			changes = append(changes, name+"'s password")
		}
		if was != nil && was.MCPToken != now.MCPToken {
			changes = append(changes, name+"'s MCP token")
		}
	}
	for _, c := range []struct {
		key     string
		changed bool
	}{
		{"password", old.PasswordHash != s.PasswordHash},
		{"mcp-token", old.MCPToken != s.MCPToken},
		{"hosts", !maps.Equal(old.Hosts, s.Hosts)}, {"base-url", old.BaseURL != s.BaseURL},
		{"git-remote", old.GitRemote != s.GitRemote},
		{"noshare", !maps.Equal(old.NoshareTags, s.NoshareTags) || !slices.Equal(old.NoshareFolder, s.NoshareFolder)},
		{"locale", old.Locale != s.Locale}, {"date-format", old.DateFormat != s.DateFormat},
		{"query-log", old.QueryLog != s.QueryLog},
		{"semantic-min-score", !sameFloat(old.SemanticMinScore, s.SemanticMinScore)},
	} {
		if c.changed {
			changes = append(changes, c.key)
		}
	}
	if len(changes) == 0 {
		return "Change settings"
	}
	return "Change settings: " + strings.Join(changes, ", ")
}

func sameFloat(a, b *float64) bool { return a == nil && b == nil || a != nil && b != nil && *a == *b }

func settingsFailed(err error) {
	msg := err.Error() + " (Zone keeps its settings from before)"
	settingsErr.Store(&msg)
	logf("%s", msg)
}

// applySettings: what changes with the settings beyond reading them: the
// git remote, the noshare cache.
func applySettings(old, s *Settings) {
	if s.GitRemote != old.GitRemote {
		setGitRemote()
	}
	noshareMu.Lock()
	clear(noshareCache)
	noshareMu.Unlock()
}

// Writing the file (`zonewiki user`, `zonewiki config`): whole, to a temporary file
// in .leaf/ renamed over it (a reload never reads half a file), owner-only,
// keeping the comments and the order of the lines it doesn't change.

// sectionRange: the lines of a section (user "": the top, before the first
// section), from its header (or 0) to the next header; -1 if it isn't there.
func (f *configFile) sectionRange(user string) (start, end int) {
	start = -1
	if user == "" {
		start = 0
	}
	for i, line := range f.lines {
		name, ok := sectionName(line)
		if !ok {
			continue
		}
		if start >= 0 {
			return start, i
		}
		if name == "user "+user {
			start = i
		}
	}
	if start < 0 {
		return -1, -1
	}
	return start, len(f.lines)
}

// set gives key a value in user's section (""; the top), replacing its line
// or adding one after the section's last setting; a new section goes at
// the end.
func (f *configFile) set(user, key, value string) {
	line := key + " = " + value
	start, end := f.sectionRange(user)
	if start < 0 {
		if len(f.lines) > 0 && strings.TrimSpace(f.lines[len(f.lines)-1]) != "" {
			f.lines = append(f.lines, "")
		}
		f.lines = append(f.lines, "[user "+user+"]", line)
		return
	}
	at := start
	if user != "" {
		at++
	}
	for i := start; i < end; i++ {
		if isComment(f.lines[i]) {
			continue
		}
		if k, _, _ := strings.Cut(f.lines[i], "="); strings.TrimSpace(k) == key {
			f.lines[i] = line
			return
		}
		if _, header := sectionName(f.lines[i]); !header {
			at = i + 1
		}
	}
	if user == "" && at == 0 {
		// Below the comments the file starts with.
		for at < end && strings.HasPrefix(strings.TrimSpace(f.lines[at]), "#") {
			at++
		}
	}
	if at < len(f.lines) {
		if _, header := sectionName(f.lines[at]); header {
			f.lines = slices.Insert(f.lines, at, "") // the wiki's settings, then the users
		}
	}
	f.lines = slices.Insert(f.lines, at, line)
}

// unset takes key's line out of user's section ("": the top).
func (f *configFile) unset(user, key string) {
	start, end := f.sectionRange(user)
	for i := max(start, 0); start >= 0 && i < end; i++ {
		if k, _, found := strings.Cut(f.lines[i], "="); found && !isComment(f.lines[i]) && strings.TrimSpace(k) == key {
			f.lines = slices.Delete(f.lines, i, i+1)
			return
		}
	}
}

// removeUser takes user's section out, with the comments right above it
// (they are about it; not those the file starts with), but not those right
// above the next section.
func (f *configFile) removeUser(user string) {
	start, end := f.sectionRange(user)
	if start < 0 {
		return
	}
	above := start
	for above > 0 && strings.HasPrefix(strings.TrimSpace(f.lines[above-1]), "#") {
		above--
	}
	if above > 0 {
		start = above
	}
	if end < len(f.lines) {
		for end > start+1 && strings.HasPrefix(strings.TrimSpace(f.lines[end-1]), "#") {
			end--
		}
	}
	for end > start+1 && strings.TrimSpace(f.lines[end-1]) == "" {
		end--
	}
	// One blank line between what was before and after it, not two.
	for start > 0 && strings.TrimSpace(f.lines[start-1]) == "" {
		start--
	}
	f.lines = slices.Delete(f.lines, start, end)
	if start > 0 && start < len(f.lines) && strings.TrimSpace(f.lines[start]) != "" {
		f.lines = slices.Insert(f.lines, start, "")
	}
}

func (f *configFile) text() string {
	if len(f.lines) == 0 {
		return ""
	}
	return strings.Join(f.lines, "\n") + "\n"
}

// writeConfig writes text as the file at path, owner-only.
func writeConfig(path, text string) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".config-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err = tmp.Chmod(0o600); err == nil {
		_, err = tmp.WriteString(text)
	}
	if err == nil {
		err = tmp.Sync() // on disk before it replaces the file
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
