// Zone's deployment settings: from the environment (.env through run.sh),
// and the wiki's directories, read once at start. The wiki's own settings
// are in its settings file (configfile.go).
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"strings"
	"sync"
	"time"
)

const appName = "Zone"

// legacyAppName: Zone's name before, its commits' author in older wikis.
const legacyAppName = "Leaf"

// Zone's version (semver), bumped by hand when a change is worth a number.
const version = "1.1.0"

// environ: the environment Zone reads its deployment settings from, with
// the names from before Leaf was called Zone (zoneEnv).
var environ = zoneEnv(os.Getenv, logf)

// zoneEnv: getenv, where a ZONE_ variable that isn't set is read by its old
// name, LEAF_…, for one release; warn says once what to call it.
func zoneEnv(getenv func(string) string, warn func(format string, args ...any)) func(string) string {
	var mu sync.Mutex
	warned := map[string]bool{}
	return func(name string) string {
		v := getenv(name)
		rest, ok := strings.CutPrefix(name, "ZONE_")
		if v != "" || !ok {
			return v
		}
		old := "LEAF_" + rest
		if v = getenv(old); v != "" {
			mu.Lock()
			if !warned[old] {
				warned[old] = true
				warn("%s is the name from before Leaf was called Zone: call it %s in .env (the old name still works, for now)", old, name)
			}
			mu.Unlock()
		}
		return v
	}
}

// Config: what is fixed for the process, from the environment. What the
// wiki's settings file can set, and Zone reloads, is Settings
// (configfile.go).
type Config struct {
	Build       string
	HostData    string // the wiki folder as the host sees it
	AllowRemote bool   // listen on the LAN: no Host check unless Hosts is set
	Data        string // the wiki (ZONE_DATA)
	Uploads     string // Data/_assets
	GitEnabled  bool
}

var cfg *Config

// Zone's own folder in the wiki: the settings file (committed), the session
// key, the caches, drafts (not). An older Zone's is .leaf/, moved at start-up
// (migrateNames); Zone reads it while it is the only one there.
const (
	stateDirName       = ".zone"
	legacyStateDirName = ".leaf"
)

// stateDirIn: Zone's folder in the wiki data, .zone/, or .leaf/ while that
// one is still the wiki's (not moved yet, or the move failed): .zone/ has no
// settings file, and either .leaf/ has one or .zone/ is missing or empty. So
// a stray .zone/ never leaves the wiki without its settings, and its login.
func stateDirIn(data string) string {
	dir, old := filepath.Join(data, stateDirName), filepath.Join(data, legacyStateDirName)
	if !isDir(old) || exists(filepath.Join(dir, "config")) {
		return dir
	}
	entries, _ := os.ReadDir(dir)
	if exists(filepath.Join(old, "config")) || len(entries) == 0 {
		return old
	}
	return dir
}

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

// hostName is a Host header's name without its port, lowercase ("[::1]:5000"
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
	if err := staleEnv(getenv); err != nil {
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
		Build:       getenv("ZONE_BUILD"),
		HostData:    strings.TrimRight(strings.TrimSpace(getenv("ZONE_HOST_DATA")), "/"),
		AllowRemote: truthy(getenv("ZONE_ALLOW_REMOTE")),
	}
	if c.Build == "" {
		c.Build = sourceBuild()
	}
	// Required, no default, and it must exist: Zone doesn't make a wiki
	// where a mistyped ZONE_DATA points.
	c.Data = getenv("ZONE_DATA")
	if c.Data == "" {
		return nil, fmt.Errorf("ZONE_DATA isn't set. Set it to your wiki's folder, e.g. ZONE_DATA=~/zonewiki zonewiki")
	}
	if st, err := os.Stat(c.Data); err != nil || !st.IsDir() {
		return nil, fmt.Errorf("no wiki at %s. Create the folder, or set ZONE_DATA to your wiki.", c.Data)
	}
	c.Data, _ = filepath.Abs(c.Data)
	if real, err := filepath.EvalSymlinks(c.Data); err == nil {
		c.Data = real // pages' paths are resolved too (safePath)
	}
	c.Uploads = filepath.Join(c.Data, "_assets")
	_ = os.Mkdir(c.Uploads, 0o777)
	_, err := exec.LookPath("git")
	c.GitEnabled = err == nil
	return c, nil
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
