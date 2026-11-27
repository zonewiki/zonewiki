package main

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/settingsfile"
)

func zoneSettings(t *testing.T, text string) (*Settings, error) {
	t.Helper()
	f, err := settingsfile.Parse("config", text, settingsKeys)
	if err != nil {
		t.Fatal(err)
	}
	return loadSettings(f)
}

// The setting timezone names a zone of the IANA database, which the binary
// carries; anything else is refused when the settings are read.
func TestTimezoneSetting(t *testing.T) {
	s, err := zoneSettings(t, "timezone = Europe/Vienna\n")
	if err != nil || s.Zone == nil || s.Zone.String() != "Europe/Vienna" {
		t.Fatalf("%v, %v", s, err)
	}
	if s, err := zoneSettings(t, ""); err != nil || s.Zone != nil || s.Timezone != "" {
		t.Errorf("unset: %+v, %v", s, err)
	}
	for _, bad := range []string{"Mars/Olympus", "Local", "Europe/Vienna x", "+02:00"} {
		if _, err := zoneSettings(t, "timezone = "+bad+"\n"); err == nil || !strings.Contains(err.Error(), "isn't a timezone") {
			t.Errorf("%q: %v", bad, err)
		}
	}
}

// With a timezone, the wiki's clock, its day and the days it reads are that
// zone's, whatever the process's is; without, the process's.
func TestTheWikisDayIsItsTimezones(t *testing.T) {
	old, oldNow := current.Load(), now
	t.Cleanup(func() { current.Store(old); now = oldNow })
	// 23:30 UTC on the 25th: the 26th already in Vienna and in Kiritimati,
	// still the 25th in New York.
	at := time.Date(2026, 11, 25, 23, 30, 0, 0, time.UTC)
	now = func() time.Time { return at.In(zone()) }
	for name, day := range map[string]string{"Europe/Vienna": "2026-11-26", "Pacific/Kiritimati": "2026-11-26", "America/New_York": "2026-11-25", "UTC": "2026-11-25"} {
		s, err := zoneSettings(t, "timezone = "+name+"\n")
		if err != nil {
			t.Fatal(err)
		}
		current.Store(s)
		if zone().String() != name || isoDay(today()) != day || today().Location().String() != name {
			t.Errorf("%s: zone %s, today %s in %s", name, zone(), isoDay(today()), today().Location())
		}
		if d, ok := parseDay(day); !ok || !d.Equal(today()) {
			t.Errorf("%s: %s read as %s, today is %s", name, day, d, today())
		}
		if got := fmtDate(at); got != day {
			t.Errorf("%s: a time shown as the day %s, not %s", name, got, day)
		}
		if env := zoneEnv(); len(env) != 1 || env[0] != "TZ="+name {
			t.Errorf("%s: git's environment %v", name, env)
		}
	}
	s, _ := zoneSettings(t, "")
	current.Store(s)
	if zone() != time.Local || zoneEnv() != nil {
		t.Errorf("unset: zone %s, git's environment %v", zone(), zoneEnv())
	}
}

// The wiki's zone is asked in one place (zone, serverZone in security.go):
// code that reads time.Local or calls .Local() shows the process's time
// where the wiki's is meant.
func TestTheZoneIsAskedInOnePlace(t *testing.T) {
	local := regexp.MustCompile(`\btime\.Local\b|\.Local\(\)`)
	files, _ := filepath.Glob("*.go")
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") || name == "security.go" {
			continue
		}
		src, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		for i, line := range strings.Split(string(src), "\n") {
			if code, _, _ := strings.Cut(line, "//"); local.MatchString(code) {
				t.Errorf("%s:%d asks for the process's timezone: use zone() (the wiki's), or serverZone()\n\t%s", name, i+1, strings.TrimSpace(line))
			}
		}
	}
}
