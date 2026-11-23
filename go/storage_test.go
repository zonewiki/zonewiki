package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseSize(t *testing.T) {
	for s, want := range map[string]int64{
		"300": 300, "300 bytes": 300, "1b": 1, "512KB": 512 << 10, "25MB": 25 << 20, "25 mb": 25 << 20,
		"5GB": 5 << 30, "1.5GB": 3 << 29, "2TB": 2 << 40, " 100MB ": 100 << 20,
	} {
		if got, err := parseSize(s); err != nil || got != want {
			t.Errorf("%q: %d, %v; want %d", s, got, err, want)
		}
	}
	for _, s := range []string{"", "MB", "0", "0.1", "-5MB", "5 PB", "5M", "lots", "1e9", "99999999TB"} {
		if got, err := parseSize(s); err == nil {
			t.Errorf("%q: %d, want an error", s, got)
		}
	}
}

func TestFormatSize(t *testing.T) {
	for n, want := range map[int64]string{
		512: "512 bytes", 1 << 10: "1 KB", 100 << 20: "100 MB", 3 << 29: "1.5 GB", 5 << 30: "5 GB",
		5<<30 - 1<<20: "5.0 GB", 2 << 40: "2 TB",
	} {
		if got := formatSize(n); got != want {
			t.Errorf("%d: %q, want %q", n, got, want)
		}
	}
}

func TestSizeSettings(t *testing.T) {
	load := func(text string) (*Settings, error) {
		t.Helper()
		f, err := parseConfig(text)
		if err != nil {
			t.Fatal(err)
		}
		return loadSettings(f)
	}
	s, err := load("upload-max = 25MB\nstorage-max = 5GB\n")
	if err != nil || s.UploadMax != 25<<20 || s.uploadMax() != 25<<20 || s.StorageMax != 5<<30 {
		t.Fatalf("%v %+v", err, s)
	}
	// Not set, or empty: 100 MB uploads, no limit on the wiki.
	for _, text := range []string{"", "upload-max =\nstorage-max =\n"} {
		if s, err := load(text); err != nil || s.uploadMax() != 100<<20 || s.StorageMax != 0 {
			t.Errorf("%q: %v %+v", text, err, s)
		}
	}
	for text, want := range map[string]string{
		"upload-max = big":    ".zone/config line 1: upload-max = big isn't a size",
		"\nstorage-max = 5PB": ".zone/config line 2: storage-max = 5PB isn't a size. Use bytes, or KB, MB, GB, TB",
		"storage-max = 0":     "storage-max = 0 isn't a size",
	} {
		if _, err := load(text); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: %v, want %q", text, err, want)
		}
	}
	defaults, _ := load("")
	if got := settingsChange(defaults, s); got != "Change settings: upload-max, storage-max" {
		t.Errorf("%q", got)
	}
}

// freshUsage: the next usedBytes walks the wiki (the figure is the
// process's, not a test's).
func freshUsage(t *testing.T) {
	forgetUsage()
	t.Cleanup(forgetUsage)
}

func TestUsageCountsWhatTheUserHas(t *testing.T) {
	testWiki(t, map[string]string{"index": "12345", "notes/a": "123"})
	freshUsage(t)
	for name, size := range map[string]int{
		"_assets/photo.png": 100, "_tasks/tasks.json": 10, "private/alice/b.md": 7, ".obsidian/app.json": 2,
		// Not the user's: git's history, Zonewiki's own folder.
		".git/objects/pack/x.pack": 1000, ".zone/index/cache": 5000,
	} {
		p := filepath.Join(cfg.Data, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, make([]byte, size), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	// testWiki's settings file is in .zone/ too.
	if got, want := usedBytes(false), int64(5+3+100+10+7+2); got != want {
		t.Errorf("%d bytes, want %d", got, want)
	}
	// A figure a minute old at most, plus what was added since.
	if err := os.WriteFile(filepath.Join(cfg.Data, "c.md"), make([]byte, 50), 0o666); err != nil {
		t.Fatal(err)
	}
	added(50)
	if got := usedBytes(false); got != 177 {
		t.Errorf("%d bytes, want 177", got)
	}
}

func TestCheckRoom(t *testing.T) {
	testWiki(t, map[string]string{"index": strings.Repeat("x", 900)})
	freshUsage(t)
	if err := checkRoom(1 << 30); err != nil {
		t.Errorf("no storage-max: %v", err)
	}
	s := *settings()
	s.StorageMax = 1000
	current.Store(&s)
	if err := checkRoom(100); err != nil {
		t.Errorf("900 + 100 of 1000: %v", err)
	}
	err := checkRoom(101)
	if pe, ok := refusalOf(err); !ok || pe.kind != refusedFull ||
		pe.msg != "The wiki is full: 900 bytes of 1000 bytes used. Delete pages or files to make room." {
		t.Errorf("900 + 101 of 1000: %v", err)
	}
	// Past the limit, what adds nothing still fits.
	s.StorageMax = 500
	if err := checkRoom(0); err != nil {
		t.Errorf("900 + 0 of 500: %v", err)
	}
	if err := checkRoom(-100); err != nil {
		t.Errorf("900 - 100 of 500: %v", err)
	}
	s.StorageMax = 1000
	// A file deleted on disk: a figure that says full is walked again, but
	// not on every check.
	if err := os.Remove(filepath.Join(cfg.Data, "index.md")); err != nil {
		t.Fatal(err)
	}
	if err := checkRoom(101); err == nil {
		t.Error("walked again at once")
	}
	oldRecheck := usageRecheck
	usageRecheck = 0
	t.Cleanup(func() { usageRecheck = oldRecheck })
	if err := checkRoom(101); err != nil {
		t.Errorf("after the delete: %v", err)
	}
}
