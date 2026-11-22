// Zone's deployment settings: from the environment (.env through run.sh),
// and the wiki's directories, read once at start. The wiki's own settings
// are in its settings file (configfile.go).
package main

import (
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/debug"
	"strings"
	"time"
)

const appName = "Zone"

// Zone's version (semver), bumped by hand when a change is worth a number:
// before 1.0 the minor one when something people rely on changes, the patch for fixes.
const version = "0.5.0"

// Where this version's source is, linked from the shortcuts dialog. The AGPL has
// whoever runs a changed Zone for others offer them its source: a fork changes this.
const sourceURL = "https://github.com/zonewiki/zonewiki"

// environ: the environment Zone reads its deployment settings from.
var environ = os.Getenv

// Config: what is fixed for the process, from the environment. What the
// wiki's settings file can set, and Zone reloads, is Settings
// (configfile.go).
type Config struct {
	Build       string
	HostData    string // the wiki folder as the host sees it
	AllowRemote bool   // listen on the LAN: no Host check unless Hosts is set
	Published   bool   // open to the network: the container's port (ZONE_BIND, from compose), or --addr beyond loopback
	// PortMapped: compose publishes Zone's port (ZONE_BIND is set), maybe
	// as another one (compose.yaml: ZONE_PORT), so the address Zone listens
	// on isn't the one to open it at (openURL).
	PortMapped bool
	// IdleExit: Zone exits after this long without a request, when it
	// listens on a socket it was handed (--addr fd:3), which whoever
	// handed it starts Zone on again; 0: never (ZONE_IDLE_EXIT).
	IdleExit   time.Duration
	Data       string // the wiki (ZONE_DATA)
	Uploads    string // Data/_assets
	GitEnabled bool
	Init       bool       // ZONE_INIT, --init: the folder becomes a wiki though it holds files already (newWikiError)
	Signup     bool       // ZONE_SIGNUP: people may make an account themselves (signup.go)
	Public     string     // ZONE_PUBLIC: who isn't signed in reads the wiki (publicView) or edits its pages too (publicEdit); "": signs in first
	Mail       mailConfig // ZONE_SMTP_HOST …, ZONE_MAIL_FROM: how Zone sends mail (mail.go); Host "": it sends none
}

var cfg *Config

// Zone's own folder in the wiki: the settings file, the session key, the
// caches, drafts. None of it is in git (excludeState).
const stateDirName = ".zone"

// stateDirIn: Zone's folder in the wiki data.
func stateDirIn(data string) string { return filepath.Join(data, stateDirName) }

// statePath: a file in Zone's folder of the wiki (stateDirIn).
func statePath(parts ...string) string {
	return filepath.Join(append([]string{stateDirIn(cfg.Data)}, parts...)...)
}

func truthy(s string) bool {
	switch strings.ToLower(s) {
	case "1", "true", "yes":
		return true
	}
	return false
}

// hostName is a Host header's name without its port, lowercase ("[::1]:5080"
// → "[::1]", "Wiki.Example.org:443" → "wiki.example.org").
func hostName(host string) string {
	host = strings.ToLower(strings.TrimSpace(host))
	if strings.HasPrefix(host, "[") {
		before, _, _ := strings.Cut(host, "]")
		return before + "]"
	}
	before, _, _ := strings.Cut(host, ":")
	return before
}

// parseNoshare reads noshare (the settings file): "#tag" entries are tags, "folder/"
// entries folders (everything below them); a bare word counts as a tag.
func parseNoshare(spec string) (map[string]bool, []string) {
	tags, folders := map[string]bool{}, []string{}
	for _, item := range strings.Split(spec, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if strings.HasSuffix(item, "/") && !strings.HasPrefix(item, "#") {
			folders = append(folders, strings.ToLower(strings.Trim(item, "/"))+"/")
		} else {
			tags[strings.ToLower(strings.TrimLeft(item, "#"))] = true
		}
	}
	return tags, folders
}

var localeRE = regexp.MustCompile(`^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$`)

// loadConfig reads the settings, the deployment's from the environment and
// the wiki's from its settings file, or says what is wrong with them: a typo shouldn't leave
// Zone running on a default.
func loadConfig(getenv func(string) string) (*Config, *Settings, error) {
	c, err := loadWiki(getenv)
	if err != nil {
		return nil, nil, err
	}
	f, err := readConfigFile(filepath.Join(stateDirIn(c.Data), "config"))
	if err != nil {
		return nil, nil, err
	}
	s, err := loadSettings(f)
	if err != nil {
		return nil, nil, err
	}
	return c, s, nil
}

// loadWiki: the settings fixed for the process (Config), the wiki first.
func loadWiki(getenv func(string) string) (*Config, error) {
	c := &Config{
		Build:       buildOf(getenv),
		HostData:    strings.TrimRight(strings.TrimSpace(getenv("ZONE_HOST_DATA")), "/"),
		AllowRemote: truthy(getenv("ZONE_ALLOW_REMOTE")),
		Published:   published(getenv("ZONE_BIND")),
		PortMapped:  strings.TrimSpace(getenv("ZONE_BIND")) != "",
	}
	// A ZONE_DATA that is set must exist: Zone doesn't make a wiki where a
	// mistyped one points. Unset, the wiki is ~/zonewiki, made if need be.
	if c.Data = getenv("ZONE_DATA"); c.Data == "" {
		data, err := defaultData(getenv)
		if err != nil {
			return nil, err
		}
		c.Data = data
	} else if st, err := os.Stat(c.Data); err != nil || !st.IsDir() {
		return nil, fmt.Errorf("no wiki at %s. Create the folder, or set ZONE_DATA to your wiki.", c.Data)
	}
	c.Data, _ = filepath.Abs(c.Data)
	if real, err := filepath.EvalSymlinks(c.Data); err == nil {
		c.Data = real // pages' paths are resolved too (safePath)
	}
	c.Uploads = filepath.Join(c.Data, "_assets") // made at start, once the folder is checked (wikiFolderError)
	_, err := exec.LookPath("git")
	c.GitEnabled = err == nil
	if c.Mail, err = loadMail(getenv); err != nil {
		return nil, err
	}
	switch p := strings.ToLower(strings.TrimSpace(getenv("ZONE_PUBLIC"))); p {
	case "", "off", "no", "0", "false":
	case publicView, publicEdit:
		c.Public = p
	default:
		return nil, fmt.Errorf("ZONE_PUBLIC is %q: it is view (anyone reads the wiki without signing in), "+
			"edit (anyone edits its pages too) or off", p)
	}
	if v := strings.TrimSpace(getenv("ZONE_IDLE_EXIT")); v != "" && v != "off" && v != "0" {
		if c.IdleExit, err = time.ParseDuration(v); err != nil || c.IdleExit < time.Second {
			return nil, fmt.Errorf("ZONE_IDLE_EXIT is %q: it is how long Zone waits without a request before it exits, "+
				"as 15m or 1h (a second at least), or off", v)
		}
	}
	c.Init = truthy(getenv("ZONE_INIT"))
	if c.Signup = truthy(getenv("ZONE_SIGNUP")); c.Signup && c.Mail.Host == "" {
		return nil, fmt.Errorf("ZONE_SIGNUP needs mail, for the link that confirms an address: " +
			"set ZONE_SMTP_HOST and ZONE_MAIL_FROM (see .env.example)")
	}
	return c, nil
}

// defaultDataName: the wiki's folder in the home folder, where ZONE_DATA
// isn't set.
const defaultDataName = "zonewiki"

// defaultData: the wiki where ZONE_DATA isn't set, ~/zonewiki, made when it
// isn't there (and said so), so that a binary started with nothing set up
// is a wiki at once. Only this one folder is made: the home folder must be
// there, and a ZONE_DATA that names a missing folder is still refused.
func defaultData(getenv func(string) string) (string, error) {
	home := getenv("HOME")
	if home == "" && runtime.GOOS == "windows" {
		home = getenv("USERPROFILE")
	}
	if home == "" {
		return "", fmt.Errorf("ZONE_DATA isn't set, and there is no home folder (HOME) for its default ~/%s. "+
			"Set ZONE_DATA to your wiki's folder.", defaultDataName)
	}
	data := filepath.Join(home, defaultDataName)
	if st, err := os.Stat(data); err == nil {
		if !st.IsDir() {
			return "", fmt.Errorf("ZONE_DATA isn't set, and its default %s is a file, not a folder. "+
				"Set ZONE_DATA to your wiki's folder.", data)
		}
		return data, nil
	}
	if err := os.Mkdir(data, 0o777); err != nil {
		return "", fmt.Errorf("ZONE_DATA isn't set, and its default %s can't be made: %v. "+
			"Set ZONE_DATA to your wiki's folder.", data, err)
	}
	logf("ZONE_DATA isn't set: made a new wiki at %s", data)
	return data, nil
}

// newWikiError: why Zone doesn't make the wiki's folder a wiki without
// being asked (--init, ZONE_INIT): it isn't one yet (no .zone-format) and
// holds files, which a start would make the wiki's pages and, with git,
// commit, all of them. That is what a folder of notes wants once, and
// what a mistyped ZONE_DATA (the home folder, a Documents folder, a
// project's checkout) must never get. A .git doesn't make a folder a
// wiki: a checkout and a home folder kept in git have one, and Zone would
// commit its pages into that repository. An empty folder needs nothing, nor
// one with only what Zone's own commands write before a first start
// (.zone/, the settings file) or an older start made (_assets/). Asked at
// start before Zone writes anything in the folder.
func newWikiError() error {
	if cfg.Init {
		return nil
	}
	if _, err := os.Lstat(formatPath()); err == nil {
		return nil
	}
	entries, err := os.ReadDir(cfg.Data)
	if err != nil {
		return nil // what can't be read fails where it is read
	}
	var names []string
	for _, e := range entries {
		if e.Name() == stateDirName || e.Name() == "_assets" || e.Name() == ".git" {
			continue // an empty repository holds no files either
		}
		name := e.Name()
		if e.IsDir() {
			name += "/"
		}
		names = append(names, name)
	}
	if len(names) == 0 {
		return nil
	}
	shown := strings.Join(names[:min(len(names), 3)], ", ")
	if len(names) > 3 {
		shown += fmt.Sprintf(" and %d more", len(names)-3)
	}
	history := "the first commit of its history"
	if isDir(filepath.Join(cfg.Data, ".git")) {
		history = "Zone commits its changes to the git repository there"
	}
	return fmt.Errorf("%s holds files (%s) but isn't a wiki yet. Zone would make it one: its files become the wiki's "+
		"pages and %s. If that is what you want, start Zone once with --init "+
		"(or ZONE_INIT=1, in .env for the container); else set ZONE_DATA to an empty folder or to your wiki.",
		cfg.Data, shown, history)
}

// published: ZONE_BIND, the address compose publishes Zone's port on
// (compose.build.yaml and compose.yaml hand it in), isn't this
// machine alone: other devices reach Zone. Not set (Zone run by hand):
// not known, so no.
func published(bind string) bool {
	bind = strings.Trim(strings.TrimSpace(bind), "[]")
	if bind == "" || strings.EqualFold(bind, "localhost") {
		return false
	}
	addr, err := netip.ParseAddr(bind)
	return err != nil || !addr.IsLoopback()
}

// buildOf: the build shown next to the version: ZONE_BUILD when the
// environment names it (the container image), else what go build recorded.
func buildOf(getenv func(string) string) string {
	if build := getenv("ZONE_BUILD"); build != "" {
		return build
	}
	return sourceBuild()
}

// sourceBuild is the build Zone runs from, as "<short hash> <commit date>"
// ("+dirty" when built with uncommitted changes), from what go build
// recorded. The container image is built without .git, so run.sh passes
// the same string in ZONE_BUILD instead. Shown next to the version.
func sourceBuild() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "unknown"
	}
	var rev, at string
	dirty := false
	for _, s := range info.Settings {
		switch s.Key {
		case "vcs.revision":
			rev = s.Value
		case "vcs.time":
			at = s.Value
		case "vcs.modified":
			dirty = s.Value == "true"
		}
	}
	if len(rev) < 7 {
		return "unknown"
	}
	build := rev[:7]
	if t, err := time.Parse(time.RFC3339, at); err == nil {
		build += " " + t.Local().Format("2006-01-02 15:04")
	}
	if dirty {
		build += " +dirty"
	}
	return build
}
