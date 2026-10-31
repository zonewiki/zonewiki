package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A ZONE_ variable that isn't set is read by its name from before Leaf was
// called Zone, with one warning per name; the new name wins when both are set.
func TestZoneEnvReadsTheOldNames(t *testing.T) {
	vars := map[string]string{"LEAF_DATA": "/old", "ZONE_BUILD": "new", "LEAF_BUILD": "old", "LEAF_PASSWORD": "pw"}
	var warnings []string
	get := zoneEnv(func(k string) string { return vars[k] },
		func(format string, args ...any) { warnings = append(warnings, fmt.Sprintf(format, args...)) })
	if got := get("ZONE_DATA"); got != "/old" {
		t.Errorf("ZONE_DATA = %q, want LEAF_DATA's", got)
	}
	_ = get("ZONE_DATA")
	if got := get("ZONE_BUILD"); got != "new" {
		t.Errorf("ZONE_BUILD = %q, want its own", got)
	}
	if got := get("ZONE_ALLOW_REMOTE"); got != "" {
		t.Errorf("ZONE_ALLOW_REMOTE = %q, set under neither name", got)
	}
	if got := get("LEAF_PASSWORD"); got != "pw" {
		t.Errorf("LEAF_PASSWORD = %q: other names are read as they are", got)
	}
	if len(warnings) != 1 || !strings.HasPrefix(warnings[0], "LEAF_DATA is the name from before") ||
		!strings.Contains(warnings[0], "ZONE_DATA") {
		t.Errorf("warnings %q, want one for LEAF_DATA", warnings)
	}
}

// Zone's folder is .zone/, or an older Zone's .leaf/ while that one is still
// the wiki's: a stray .zone/ never takes the settings file's place.
func TestStateDirIn(t *testing.T) {
	wiki := t.TempDir()
	in := func(want string, why string) {
		t.Helper()
		if got := filepath.Base(stateDirIn(wiki)); got != want {
			t.Errorf("%s: %s, want %s", why, got, want)
		}
	}
	mk := func(rel string) {
		_ = os.MkdirAll(filepath.Dir(filepath.Join(wiki, rel)), 0o777)
		_ = os.WriteFile(filepath.Join(wiki, rel), []byte("x"), 0o600)
	}
	in(".zone", "neither")
	mk(".leaf/secret_key")
	in(".leaf", ".leaf/ alone")
	_ = os.Mkdir(filepath.Join(wiki, ".zone"), 0o777)
	in(".leaf", "an empty .zone/")
	mk(".zone/tmp/x")
	in(".zone", ".zone/ with files, neither has settings")
	mk(".leaf/config")
	in(".leaf", "only .leaf/ has settings")
	mk(".zone/config")
	in(".zone", "both have settings")
	_ = os.RemoveAll(filepath.Join(wiki, ".leaf"))
	in(".zone", ".zone/ alone")
}
