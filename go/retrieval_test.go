package main

import (
	"os"
	"path/filepath"
	"testing"
)

// Search and retrieval (retrieval.go): what the API tests can't reach.

// A page whose file vanishes or can't be read between listing and reading is
// left out of the search, not a failed search.
func TestUnreadablePagesAreSkipped(t *testing.T) {
	testWiki(t, map[string]string{"fine": "# Fine\n\nzzqword here\n"})
	// A dangling link fails the stat, a link to a folder the read.
	if err := os.Symlink(filepath.Join(cfg.Data, "nowhere.md"), filepath.Join(cfg.Data, "gone.md")); err != nil {
		t.Fatal(err)
	}
	_ = os.Mkdir(filepath.Join(cfg.Data, "folder"), 0o777)
	if err := os.Symlink(filepath.Join(cfg.Data, "folder"), filepath.Join(cfg.Data, "unread.md")); err != nil {
		t.Fatal(err)
	}
	var slugs []string
	for _, d := range pageSearchDocs() {
		slugs = append(slugs, d.Slug)
	}
	if len(slugs) != 1 || slugs[0] != "fine" {
		t.Errorf("pages searched: %q", slugs)
	}
}

func TestChunkSections(t *testing.T) {
	text := "Intro [link](http://x) text\n\n# Top\n\nOne\n\n```\n# not a heading\n```\n\n## Sub ##\n\nTwo\n\n#### Deep\n\nThree\n\n## Empty\n"
	got := chunkSections(text, maxChunkChars)
	want := []chunkSection{
		{"", "Intro [link] text", 0, ""},
		{"Top", "One\n\n```\n# not a heading\n```", 1, "Top"},
		{"Sub", "Two", 2, "Top › Sub"},
		{"Deep", "Three", 4, "Top › Sub › Deep"},
	}
	if len(got) != len(want) {
		t.Fatalf("got %+v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("%d: got %+v, want %+v", i, got[i], want[i])
		}
	}
	if a := sectionAnchorOf("Deep", 4); a != "" {
		t.Errorf("an h4 has no anchor: %q", a)
	}
}

func TestSplitLong(t *testing.T) {
	long := ""
	for i := 0; i < 30; i++ {
		long += "wörd "
	}
	eight := "wörd wörd wörd wörd wörd wörd wörd wörd"
	got := splitLong("short\n\n  \n"+long, 40) // Python's: embed._split_long
	want := []string{"short", eight, eight, eight, "wörd wörd wörd wörd wörd wörd"}
	if len(got) != len(want) {
		t.Fatalf("got %q", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("%d: got %q, want %q", i, got[i], want[i])
		}
	}
}
