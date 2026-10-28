package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func mergeWiki(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
}

func TestMergeText(t *testing.T) {
	mergeWiki(t)
	base := "# Plans\n\n## One\n\nFirst.\n\n## Two\n\nSecond.\n"
	for _, c := range []struct {
		name, base, theirs, mine, want string
		clean                          bool
	}{
		{"different sections", base,
			strings.Replace(base, "First.", "First, by Bob.", 1),
			strings.Replace(base, "Second.", "Second, by Alice.", 1),
			"# Plans\n\n## One\n\nFirst, by Bob.\n\n## Two\n\nSecond, by Alice.\n", true},
		{"the same change on both sides", base,
			strings.Replace(base, "First.", "Same.", 1), strings.Replace(base, "First.", "Same.", 1),
			strings.Replace(base, "First.", "Same.", 1), true},
		{"mine only", base, base, base + "\nMore.\n", base + "\nMore.\n", true},
		{"no final newline", "a\nb\nc", "A\nb\nc", "a\nb\nC", "A\nb\nC", true},
		{"empty base", "", "", "New.\n", "New.\n", true},
		{"the same line", base,
			strings.Replace(base, "First.", "Bob's.", 1), strings.Replace(base, "First.", "Alice's.", 1),
			"# Plans\n\n## One\n\n<<<<<<< yours\nAlice's.\n=======\nBob's.\n>>>>>>> bob\n\n## Two\n\nSecond.\n", false},
	} {
		got, clean, err := mergeText(c.base, c.theirs, c.mine, "bob")
		if err != nil || got != c.want || clean != c.clean {
			t.Errorf("%s: %q %v %v", c.name, got, clean, err)
		}
	}
	// Its temporary files go.
	if entries, _ := os.ReadDir(filepath.Join(cfg.Data, ".leaf", "tmp")); len(entries) != 0 {
		t.Errorf("left %d files", len(entries))
	}
	cfg.GitEnabled = false
	if _, _, err := mergeText(base, base, base, "bob"); err != errNoMerge {
		t.Errorf("without git: %v", err)
	}
}

func TestBases(t *testing.T) {
	oldCount, oldBytes := baseCount, baseBytes
	t.Cleanup(func() { baseCount, baseBytes = oldCount, oldBytes })
	baseCount, baseBytes = 3, 100
	a := keepBase("a")
	if got, ok := baseText(a); !ok || got != "a" || a != contentHash("a") {
		t.Fatal("kept")
	}
	keepBase("b")
	keepBase("c")
	keepBase("a") // the most recent again
	keepBase("d") // b goes, the oldest
	if _, ok := baseText(contentHash("b")); ok {
		t.Error("b kept past the count")
	}
	for _, s := range []string{"a", "c", "d"} {
		if _, ok := baseText(contentHash(s)); !ok {
			t.Errorf("%s gone", s)
		}
	}
	keepBase(strings.Repeat("x", 100)) // past the bytes: all but it go
	if _, ok := baseText(contentHash("d")); ok {
		t.Error("kept past the bytes")
	}
	if _, ok := baseText(contentHash(strings.Repeat("x", 100))); !ok {
		t.Error("the newest gone")
	}
	if _, ok := baseText("0123456789abcdef"); ok {
		t.Error("an unknown hash")
	}
}
