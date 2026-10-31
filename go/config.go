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
	"time"
)

const appName = "Zone"

// Zone's version (semver), bumped by hand when a change is worth a number.
const version = "1.1.0"

// Config: what is fixed for the process, from the environment. What the
// wiki's settings file can set, and Zone reloads, is Settings
// (configfile.go).
type Config struct {
	Build       string
	HostData    string // the wiki folder as the host sees it
	AllowRemote bool   // listen on the LAN: no Host check unless Hosts is set
	Data        string // the wiki (LEAF_DATA)
	Uploads     string // Data/_assets
	GitEnabled  bool
}

var cfg *Config

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
	f, err := readConfigFile(filepath.Join(c.Data, ".leaf", "config"))
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
		Build:       getenv("LEAF_BUILD"),
		HostData:    strings.TrimRight(strings.TrimSpace(getenv("LEAF_HOST_DATA")), "/"),
		AllowRemote: truthy(getenv("LEAF_ALLOW_REMOTE")),
	}
	if c.Build == "" {
		c.Build = sourceBuild()
	}
	// Required, no default, and it must exist: Zone doesn't make a wiki
	// where a mistyped LEAF_DATA points.
	c.Data = getenv("LEAF_DATA")
	if c.Data == "" {
		return nil, fmt.Errorf("LEAF_DATA isn't set. Set it to your wiki's folder, e.g. LEAF_DATA=~/leaf leaf")
	}
	if st, err := os.Stat(c.Data); err != nil || !st.IsDir() {
		return nil, fmt.Errorf("no wiki at %s. Create the folder, or set LEAF_DATA to your wiki.", c.Data)
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
// the same string in LEAF_BUILD instead. Shown next to the version.
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
