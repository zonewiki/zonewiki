// The settings file, .zone/config in the wiki (docs/spec/users.md,
// docs/spec/deployment.md): the wiki's settings and its users, which Zonewiki
// reloads when the file changes, so adding a user or changing a password
// needs no restart. A small INI, read by Zonewiki's own parser:
//
//	# Zonewiki's settings. Zonewiki reloads this file when it changes.
//	locale = de-AT
//	hosts = wiki.example.org, nas.local
//
//	[user alice]
//	password = pbkdf2-sha256:600000:<salt>:<hash>
//	mcp-token = sha256:<the token's hash>
//
// The environment (.env) holds only what the deployment needs (ZONE_DATA,
// ZONE_ALLOW_REMOTE …: Config, config.go). The file is in .zone/, out of
// safePath's reach and, like all of .zone/, out of git: it holds the
// users (account data stays out of git), its MCP tokens only as hashes,
// passwords as PBKDF2 hashes.
package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"maps"
	"net/netip"
	"net/url"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
	"zonewiki/internal/settingsfile"
)

// configName(): the file as messages name it, relative to the wiki.
func configName() string { return stateDirName + "/config" }

// noticeMax: the longest notice, in characters: it is one line at the top of
// every page, cut short on a phone (the whole of it in its title).
const noticeMax = 200

// legalLink: whether the imprint or privacy setting names a place a link can
// lead: an http(s) address with a host, or a path in the wiki (/page/imprint).
// Nothing else (javascript:, //host, a space or control character), though
// the template would defuse it: the file says at once that the link is wrong.
func legalLink(link string) bool {
	if len(link) > 500 || strings.ContainsFunc(link, func(r rune) bool { return r <= ' ' || r == 0x7f || r == '\\' }) {
		return false
	}
	if strings.HasPrefix(link, "/") {
		return !strings.HasPrefix(link, "//")
	}
	u, err := url.Parse(link)
	return err == nil && (u.Scheme == "https" || u.Scheme == "http") && u.Host != "" && u.User == nil
}

func configPath() string { return statePath("config") }

// configKeys: the keys outside a [user …] section, in the order `zonewiki
// config check` shows them.
var configKeys = []string{"password", "mcp-token", "hosts", "base-url", "git-remote", "noshare", "locale",
	"date-format", "query-log", "semantic-min-score", "trusted-proxies", "upload-max", "storage-max",
	"external-content", "notice", "imprint", "privacy", "no-login", "mcp-oauth", "signup-domains", "signup-role", "signup-approval"}

// hostKeys: the settings that say where a wiki runs, not what it is: its
// names, its proxies, its room, its remote, who answers for it. An import
// leaves them in the archive (transfer.go): the host a wiki moves to has
// its own.
var hostKeys = []string{"hosts", "base-url", "git-remote", "trusted-proxies", "storage-max", "imprint", "privacy"}

// usersOnlyKeys: the settings an import with --users-only still takes,
// besides the users and the groups: how the wiki reads, which whoever runs
// it lets its people choose. Every other one changes what the host allows
// (what pages may load, who may sign up, whether there is a login), and is
// the host's to set.
var usersOnlyKeys = []string{"noshare", "locale", "date-format"}

// userKeys: the keys of a [user …] section.
var userKeys = []string{"password", "mcp-token", "role", "disabled", "email", "email-pending", "pending", "groups", "shared"}

func knownKey(key string) bool { return slices.Contains(configKeys, key) }

// settingsKeys: what the file may set (settingsfile.Parse).
// groupKeys: the keys of a [group …] section: the groups it is made of, and
// the users who say who is in it (owners: a group made in the browser,
// groups.go). Any other is an error, so a file from before can't have meant
// anything by one that comes later (readers: docs/spec/users.md, Groups).
//
// was: the names the group went by before a rename (Zonewiki writes it): its
// folder's history under those names is its own, so it may take one of
// them back, which no other group may (groupNameTaken).
var groupKeys = []string{"includes", "owners", "was"}

var settingsKeys = settingsfile.Keys{Top: configKeys, User: userKeys, UserName: userNameRE,
	Group: groupKeys, GroupName: groupNameRE}

// parseConfig reads the settings file's text (settingsfile.Parse).
func parseConfig(text string) (*settingsfile.File, error) {
	return settingsfile.Parse(configName(), text, settingsKeys)
}

// lineError: what is wrong on a line of the file.
func lineError(n int, format string, args ...any) error {
	return settingsfile.LineError(configName(), n, format, args...)
}

// readConfigFile reads the file at path; a missing file is an empty one
// (nil). Zonewiki warns when others may read it: it holds password hashes and
// tokens.
func readConfigFile(path string) (*settingsfile.File, error) {
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
	PasswordHash     string              // the login gate without users
	MCPToken         string              // /mcp's token without users, as kept (tokenMatches); "": /mcp is off
	Users            map[string]*User    // users.go; nil: no users
	Includes         map[string][]string // the groups made of groups ([group product] includes = dev, design): what each includes, A–Z
	partOf           map[string][]string // a group, and every group it is in by way of those (joinGroups; groupIn)
	Owners           map[string][]string // a group's owners ([group apollo] owners = anna), A–Z: they say who is in it (groups.go)
	Was              map[string][]string // the names a group went by before ([group artemis] was = apollo): its own to take back
	BaseURL          string              // Zonewiki's address as the user's browser knows it
	QueryLog         bool                // keep search_wiki's queries in .zone/queries.jsonl
	GitRemote        string
	Hosts            map[string]bool // names besides localhost, without port, lowercase
	NoshareTags      map[string]bool
	NoshareFolder    []string
	Locale           string         // a BCP 47 tag for the browser's Intl
	DateFormat       string         // "" or "iso"
	SemanticMinScore *float64       // the semantic search's floor; nil: the model's
	TrustedProxies   []netip.Prefix // whose X-Forwarded-For names the client (clientKey); unset: loopback
	UploadMax        int64          // the biggest upload in bytes; 0: 100 MB (uploadMax)
	StorageMax       int64          // what the wiki may hold in bytes (storage.go); 0: no limit
	External         []string       // the hosts pages may load images, media and frames from (security.go: externalRE); "*": any
	Notice           string         // a line every page shows at the top, for everyone (layout.html); "": none
	Imprint, Privacy string         // where the wiki's imprint and privacy policy are (legalLink), shown to whoever isn't signed in; "": none
	NoLogin          bool           // no login on purpose, though other devices reach Zonewiki: no first-run page (setup.go)
	SignupDomains    []string       // the mail domains sign-up takes (signup.go), lowercase; none: any
	SignupRole       string         // the role an account made by sign-up gets: roleViewer (default) or roleEditor
	SignupApproval   bool           // an account made by sign-up waits for an admin (pending)
	NoOAuth          bool           // mcp-oauth = no: assistants connect with a static token only (oauth.go)
}

// authOn: the wiki has a login gate, a password or users.
func (s *Settings) authOn() bool { return s.PasswordHash != "" || s.Users != nil }

var current atomic.Pointer[Settings]

func settings() *Settings { return current.Load() }

// loadSettings: the settings the file f (nil: none) makes, or what is
// wrong with them.
func loadSettings(f *settingsfile.File) (*Settings, error) {
	if f == nil {
		f = settingsfile.Empty()
	}
	// get: a setting, and how a message names it (".zone/config line 3:
	// locale = ").
	get := func(key string) (value, where string, set bool) {
		v, set := f.Top[key]
		return v.Value, fmt.Sprintf("%s line %d: %s = ", configName(), v.Line, key), set
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
	noLogin, where, _ := get("no-login")
	s.NoLogin = truthy(noLogin)
	if !s.NoLogin && !slices.Contains([]string{"", "0", "false", "no"}, strings.ToLower(noLogin)) {
		return nil, fmt.Errorf("%s%s isn't yes or no.", where, noLogin)
	}
	approval, where, _ := get("signup-approval")
	s.SignupApproval = truthy(approval)
	if !s.SignupApproval && !slices.Contains([]string{"", "0", "false", "no"}, strings.ToLower(approval)) {
		return nil, fmt.Errorf("%s%s isn't yes or no.", where, approval)
	}
	role, where, _ := get("signup-role")
	switch s.SignupRole = strings.ToLower(role); s.SignupRole {
	case "":
		s.SignupRole = roleViewer
	case roleViewer, roleEditor:
	default:
		return nil, fmt.Errorf("%s%s isn't a role sign-up gives: viewer or editor (an admin makes admins).", where, role)
	}
	domains, where, _ := get("signup-domains")
	for _, d := range strings.Split(domains, ",") {
		if d = strings.ToLower(strings.TrimPrefix(strings.TrimSpace(d), "@")); d == "" {
			continue
		}
		if !domainRE.MatchString(d) {
			return nil, fmt.Errorf("%s%s isn't a mail domain (e.g. example.org).", where, d)
		}
		s.SignupDomains = append(s.SignupDomains, d)
	}
	mcpOAuth, where, set := get("mcp-oauth")
	s.NoOAuth = set && slices.Contains([]string{"0", "false", "no"}, strings.ToLower(mcpOAuth))
	if !s.NoOAuth && !truthy(mcpOAuth) && mcpOAuth != "" {
		return nil, fmt.Errorf("%s%s isn't yes or no.", where, mcpOAuth)
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
	notice, where, _ := get("notice")
	if s.Notice = strings.TrimSpace(notice); utf8.RuneCountInString(s.Notice) > noticeMax {
		return nil, fmt.Errorf("%s… is %d characters: a notice is one line of at most %d.", where, utf8.RuneCountInString(s.Notice), noticeMax)
	}
	for _, l := range []struct {
		key string
		to  *string
	}{{"imprint", &s.Imprint}, {"privacy", &s.Privacy}} {
		link, where, _ := get(l.key)
		if *l.to = strings.TrimSpace(link); *l.to != "" && !legalLink(*l.to) {
			return nil, fmt.Errorf("%s%s isn't a link. Use an address like https://example.org/%s, or a path in the wiki like /page/%s.",
				where, *l.to, l.key, l.key)
		}
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
	proxies, where, set := get("trusted-proxies")
	if !set {
		proxies = "127.0.0.0/8, ::1"
	}
	for _, p := range strings.Split(proxies, ",") {
		if p = strings.TrimSpace(p); p == "" {
			continue
		}
		prefix, err := netip.ParsePrefix(p)
		if !strings.Contains(p, "/") {
			var addr netip.Addr
			if addr, err = netip.ParseAddr(p); err == nil {
				prefix = netip.PrefixFrom(addr, addr.BitLen())
			}
		}
		if err != nil {
			return nil, fmt.Errorf("%s%s isn't an IP address or a network (e.g. 172.17.0.1 or 172.16.0.0/12).", where, p)
		}
		s.TrustedProxies = append(s.TrustedProxies, prefix.Masked())
	}
	for _, size := range []struct {
		key string
		to  *int64
	}{{"upload-max", &s.UploadMax}, {"storage-max", &s.StorageMax}} {
		if v, where, set := get(size.key); set && v != "" {
			n, err := parseSize(v)
			if err != nil {
				return nil, fmt.Errorf("%s%s isn't a size. Use bytes, or KB, MB, GB, TB (e.g. 25MB or 5GB).", where, v)
			}
			*size.to = n
		}
	}
	external, where, _ := get("external-content")
	for _, h := range strings.Split(external, ",") {
		if h = strings.ToLower(strings.TrimSpace(h)); h == "" {
			continue
		}
		if !externalRE.MatchString(h) {
			return nil, fmt.Errorf("%s%s isn't a host pages may load from. Use names like upload.wikimedia.org or "+
				"*.example.org, or * for any.", where, h)
		}
		s.External = append(s.External, h)
	}
	var err error
	if s.Users, err = fileUsers(f); err != nil {
		return nil, err
	}
	if s.Includes, s.Owners, s.Was, err = fileGroups(f); err != nil {
		return nil, err
	}
	s.joinGroups()
	if s.Users != nil {
		// With users each has their own password and token: a shared one
		// would make the user unknown.
		for _, key := range []string{"password", "mcp-token"} {
			if v, ok := f.Top[key]; ok {
				return nil, lineError(v.Line, "%s is the wiki's, but there are users: each has their own. "+
					"Remove this line.", key)
			}
		}
	}
	return s, nil
}

// fileUsers: the file's [user …] sections as users; nil without any.
func fileUsers(f *settingsfile.File) (map[string]*User, error) {
	if len(f.Users) == 0 {
		return nil, nil
	}
	users := map[string]*User{}
	tokens := map[string]string{}
	for _, u := range f.Users {
		// No user is called by a word for a task's assignee: the one place
		// every way into the file passes (the commands, the admin page, an
		// invite taken, a sign-up, an edit by hand).
		if reservedNames[u.Name] {
			return nil, lineError(u.Line, "%s", reservedNameMsg(u.Name))
		}
		hash, ok := u.Values["password"]
		if !ok || hash.Value == "" {
			return nil, lineError(u.Line, "[user %s] has no password. Set one with: zonewiki user passwd %s",
				u.Name, u.Name)
		}
		if _, _, _, err := parseHash(hash.Value); err != nil {
			return nil, lineError(hash.Line, "%s's password isn't a password hash (%v). Set one with: "+
				"zonewiki user passwd %s", u.Name, err, u.Name)
		}
		token, hasToken := u.Values["mcp-token"]
		if hasToken && !isTokenHash(token.Value) {
			return nil, lineError(token.Line, "%s's mcp-token isn't a token's hash (sha256:<64 hex digits>). "+
				"Make a token with: zonewiki user token %s", u.Name, u.Name)
		}
		if other, taken := tokens[token.Value]; taken && token.Value != "" {
			return nil, lineError(token.Line, "%s's mcp-token is %s's too. Give each user a token of their "+
				"own: zonewiki user token %s", u.Name, other, u.Name)
		}
		tokens[token.Value] = u.Name
		role := roleEditor
		if v, ok := u.Values["role"]; ok {
			if role = strings.ToLower(v.Value); !slices.Contains(roles, role) {
				return nil, lineError(v.Line, "%s's role %s isn't one Zonewiki knows: admin, editor or viewer.",
					u.Name, v.Value)
			}
		}
		disabled := u.Values["disabled"]
		if !slices.Contains([]string{"", "0", "false", "no", "1", "true", "yes"}, strings.ToLower(disabled.Value)) {
			return nil, lineError(disabled.Line, "%s's disabled = %s isn't yes or no.", u.Name, disabled.Value)
		}
		pending := u.Values["pending"]
		if !slices.Contains([]string{"", "0", "false", "no", "1", "true", "yes"}, strings.ToLower(pending.Value)) {
			return nil, lineError(pending.Line, "%s's pending = %s isn't yes or no.", u.Name, pending.Value)
		}
		shared := u.Values["shared"]
		if !slices.Contains([]string{"", "0", "false", "no", "1", "true", "yes"}, strings.ToLower(shared.Value)) {
			return nil, lineError(shared.Line, "%s's shared = %s isn't yes or no.", u.Name, shared.Value)
		}
		// Whoever knows a shared account's password is many people: none
		// of them administers the wiki.
		if truthy(shared.Value) && role == roleAdmin {
			return nil, lineError(shared.Line, "%s is shared and an admin: an account several people sign in to can't be an admin.", u.Name)
		}
		email := u.Values["email"]
		if email.Value != "" && !validEmail(email.Value) {
			return nil, lineError(email.Line, "%s's email %s isn't a mail address.", u.Name, email.Value)
		}
		waiting := u.Values["email-pending"]
		if waiting.Value != "" && !validEmail(waiting.Value) {
			return nil, lineError(waiting.Line, "%s's email-pending %s isn't a mail address.", u.Name, waiting.Value)
		}
		groups, bad := parseGroups(u.Values["groups"].Value)
		if bad != "" {
			return nil, lineError(u.Values["groups"].Line, "%s's group %s isn't a group's name: %s (groups = dev, hiring).", u.Name, bad, groupNameRule)
		}
		// Waiting for approval, a user is disabled too: every check that
		// keeps a disabled user out keeps them out.
		users[u.Name] = &User{Name: u.Name, PasswordHash: hash.Value, MCPToken: token.Value, Role: role,
			Disabled: truthy(disabled.Value) || truthy(pending.Value), Pending: truthy(pending.Value),
			Email: strings.ToLower(email.Value), EmailPending: strings.ToLower(waiting.Value), Groups: groups, Shared: truthy(shared.Value)}
	}
	return users, nil
}

// fileGroups: the file's [group …] sections: what each group includes and
// who its owners are (nil without any). A group that includes itself, by
// whatever way round, is an error naming the line: who is in it would have
// no answer. An owner who is no user (removed since) is none, and no error:
// removing a user doesn't have to know the groups.
func fileGroups(f *settingsfile.File) (includes, owners, was map[string][]string, err error) {
	if len(f.Groups) == 0 {
		return nil, nil, nil, nil
	}
	includes, owners, was = map[string][]string{}, map[string][]string{}, map[string][]string{}
	for _, g := range f.Groups {
		v := g.Values["includes"]
		named, bad := parseGroups(v.Value)
		if bad != "" {
			return nil, nil, nil, lineError(v.Line, "[group %s] includes %s, which isn't a group's name: %s (includes = dev, design).", g.Name, bad, groupNameRule)
		}
		includes[g.Name] = named
		if v, set := g.Values["owners"]; set {
			names, bad := parseNames(v.Value)
			if bad != "" {
				return nil, nil, nil, lineError(v.Line, "[group %s] has the owner %s, which isn't a user's name (owners = anna, bo).",
					g.Name, bad)
			}
			if len(names) > 0 {
				owners[g.Name] = names
			}
		}
		if v, set := g.Values["was"]; set {
			names, bad := parseGroups(v.Value)
			if bad != "" {
				return nil, nil, nil, lineError(v.Line, "[group %s] was %s, which isn't a group's name (was = apollo).", g.Name, bad)
			}
			if len(names) > 0 {
				was[g.Name] = names
			}
		}
	}
	for _, g := range f.Groups {
		if round := includesItself(includes, g.Name); round != nil {
			line := g.Values["includes"].Line
			if len(round) == 2 {
				return nil, nil, nil, lineError(line, "[group %s] includes itself.", g.Name)
			}
			return nil, nil, nil, lineError(line, "[group %s] includes itself, by way of %s: a group is made of other groups.",
				g.Name, strings.Join(round[1:len(round)-1], ", "))
		}
	}
	return includes, owners, was, nil
}

// includesItself: the way from group back to itself through what the groups
// include (group, …, group), nil if there is none.
func includesItself(includes map[string][]string, group string) []string {
	seen := map[string]bool{}
	var walk func(g string, way []string) []string
	walk = func(g string, way []string) []string {
		for _, in := range includes[g] {
			if in == group {
				return append(way, in)
			}
			if !seen[in] {
				seen[in] = true
				if round := walk(in, append(way, in)); round != nil {
					return round
				}
			}
		}
		return nil
	}
	return walk(group, []string{group})
}

// MCP tokens: in the settings file only as "sha256:" and the token's
// SHA-256 in hex. A token is 32 random bytes, so its hash tells whoever
// has the file (a backup, an export) nothing.
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
// A file with an error keeps the settings Zonewiki has, so a broken edit
// never locks everyone out or opens the wiki; health() reports it.
var (
	reloadMu    sync.Mutex
	reloadEvery = time.Second
	lastLook    time.Time
	lastStat    os.FileInfo // the file as last read; nil: there was none
	settingsErr atomic.Pointer[string]
)

// startSettings: the settings Zonewiki starts with, and the file's state for
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
	if s := takeSettings(); s != nil {
		betweenRequests(s)
	}
}

// A request sees one set of settings from its checks to its answer: else
// one whose session was checked against the users before a change could
// be judged by the ones after (a switch from users to a password makes
// private/ an ordinary folder, for a user who no longer has a login). So
// the checks count the requests running (beginRequest, endRequest), and a
// change takes effect only when none is (betweenRequests): at once if none
// is, else when the last one ends, at most drainWait later. Until then the
// requests go on with the settings before, nothing waits for the change.
var (
	flightMu   sync.Mutex
	flightDone = sync.NewCond(&flightMu)
	inFlight   int
	waiting    *Settings // a change waiting for the requests running; the newest
	waits      int       // the waits begun (betweenRequests): which one is current
	drainWait  = 10 * time.Second
)

func beginRequest() {
	flightMu.Lock()
	inFlight++
	flightMu.Unlock()
}

// endRequest: the last request to end puts a change waiting in effect
// itself, under flightMu, so no request begins in between: one that began
// before a goroutine got to it would hold the change up again (an admin's
// next page, right after the one that demoted them, still saw them admin).
func endRequest() {
	flightMu.Lock()
	inFlight--
	var old, s *Settings
	if inFlight == 0 {
		if waiting != nil {
			s, waiting = waiting, nil
			old = current.Swap(s)
		}
		flightDone.Broadcast()
	}
	flightMu.Unlock()
	if s != nil {
		tookSettings(old, s)
	}
}

// betweenRequests puts s in effect once no request is running: now if
// none is, else when the last one ends (endRequest). A goroutine sees to
// it that a request that doesn't end holds it up at most drainWait (it
// then sees the change, as the log says). A change while one waits
// replaces it.
func betweenRequests(s *Settings) {
	flightMu.Lock()
	if inFlight == 0 && waiting == nil {
		old := current.Swap(s)
		flightMu.Unlock()
		tookSettings(old, s)
		return
	}
	already := waiting != nil
	waiting = s
	if !already {
		waits++
	}
	wait := waits
	flightMu.Unlock()
	if already {
		return
	}
	go func() {
		timer := time.AfterFunc(drainWait, func() {
			flightMu.Lock()
			flightDone.Broadcast()
			flightMu.Unlock()
		})
		defer timer.Stop()
		deadline := time.Now().Add(drainWait)
		flightMu.Lock()
		// Until endRequest took the change: then this wait is over, also
		// when a later change already started a wait of its own.
		for waiting != nil && waits == wait && inFlight > 0 && time.Now().Before(deadline) {
			flightDone.Wait()
		}
		if waiting == nil || waits != wait {
			flightMu.Unlock()
			return
		}
		if inFlight > 0 {
			logf("a request still running after %s sees the settings change", drainWait)
		}
		s := waiting
		waiting = nil
		old := current.Swap(s)
		flightMu.Unlock()
		tookSettings(old, s)
	}()
}

// tookSettings: what a change in effect does besides (the remote, the
// noshare cache), and its line in the log.
func tookSettings(old, s *Settings) {
	logf("reloaded %s", configName())
	applySettings(old, s)
	by := ""
	if a := settingsAuthor.Swap(nil); a != nil && *a != "" {
		by = " (by " + *a + ")" // a change on the Users tab (changeSettings)
	}
	// The file isn't in git (excludeState): the log is its history, names
	// and keys only, never a value.
	logf("%s%s", settingsChange(old, s), by)
}

// latestSettings: the settings in effect, or the change waiting for the
// requests running.
func latestSettings() *Settings {
	flightMu.Lock()
	defer flightMu.Unlock()
	if waiting != nil {
		return waiting
	}
	return current.Load()
}

// takeSettings: a changed file's settings, to put in effect; nil if the
// file is as it was or has an error.
func takeSettings() (s *Settings) {
	reloadMu.Lock()
	defer reloadMu.Unlock()
	if time.Since(lastLook) < reloadEvery {
		return nil
	}
	lastLook = time.Now()
	path := configPath()
	st, err := os.Stat(path)
	if err != nil {
		st = nil
	}
	if st == nil && lastStat == nil || st != nil && lastStat != nil && os.SameFile(st, lastStat) &&
		st.ModTime().Equal(lastStat.ModTime()) && st.Size() == lastStat.Size() {
		return nil
	}
	if st == nil {
		// Gone (or between an editor's delete and write): keep what Zonewiki
		// has, as taking it away could open the wiki.
		lastStat = nil
		settingsFailed(fmt.Errorf("%s was removed: Zonewiki keeps the settings it had until it is back, or a restart", configName()))
		return nil
	}
	lastStat = st
	f, err := readConfigFile(path)
	if err == nil {
		s, err = loadSettings(f)
	}
	if old := latestSettings(); err == nil && old.authOn() && !s.authOn() {
		// The users (or the password) gone: everyone could get in, and
		// private/ would be an ordinary folder. Only on purpose, a restart.
		err = fmt.Errorf("%s sets no users and no password now, which leaves the wiki without a login "+
			"(and private/ open to everyone): Zonewiki takes that only at its next start", configName())
	}
	if err != nil {
		settingsFailed(err)
		return nil
	}
	settingsErr.Store(nil)
	warnReadable(path)
	return s
}

// settingsChange: what changed, for the log ("Change settings: add user
// carol, alice's password, locale"), names and keys only.
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
		if was != nil && was.Role != now.Role {
			changes = append(changes, name+"'s role")
		}
		if was != nil && !slices.Equal(was.Groups, now.Groups) {
			changes = append(changes, name+"'s groups")
		}
		if was != nil && was.Shared != now.Shared {
			if now.Shared {
				changes = append(changes, name+" is shared")
			} else {
				changes = append(changes, name+" is no longer shared")
			}
		}
		if was != nil && was.Disabled != now.Disabled {
			switch {
			case was.Pending && !now.Pending && !now.Disabled:
				changes = append(changes, "approve "+name)
			case now.Disabled:
				changes = append(changes, "disable "+name)
			default:
				changes = append(changes, "enable "+name)
			}
		}
	}
	for _, g := range sortedKeys(old.Includes) {
		if _, still := s.Includes[g]; !still && len(old.Includes[g]) > 0 {
			changes = append(changes, g+" includes no group")
		}
	}
	for _, g := range sortedKeys(s.Includes) {
		if now := s.Includes[g]; !slices.Equal(old.Includes[g], now) {
			if len(now) == 0 {
				changes = append(changes, g+" includes no group")
			} else {
				changes = append(changes, g+" includes "+strings.Join(now, ", "))
			}
		}
	}
	for _, g := range sortedKeys(old.Owners) {
		if len(s.Owners[g]) == 0 {
			changes = append(changes, g+" has no owner")
		}
	}
	for _, g := range sortedKeys(s.Owners) {
		if now := s.Owners[g]; !slices.Equal(old.Owners[g], now) {
			changes = append(changes, g+"'s owners: "+strings.Join(now, ", "))
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
		{"trusted-proxies", !slices.Equal(old.TrustedProxies, s.TrustedProxies)},
		{"upload-max", old.UploadMax != s.UploadMax}, {"storage-max", old.StorageMax != s.StorageMax},
		{"external-content", !slices.Equal(old.External, s.External)},
		{"notice", old.Notice != s.Notice},
		{"imprint", old.Imprint != s.Imprint}, {"privacy", old.Privacy != s.Privacy},
		{"no-login", old.NoLogin != s.NoLogin},
		{"signup-domains", !slices.Equal(old.SignupDomains, s.SignupDomains)},
		{"signup-role", old.SignupRole != s.SignupRole}, {"signup-approval", old.SignupApproval != s.SignupApproval},
		{"mcp-oauth", old.NoOAuth != s.NoOAuth},
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
	msg := err.Error() + " (Zonewiki keeps its settings from before)"
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
	endFormerAdminsLinks(s)
}
