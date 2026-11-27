package main

import (
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// headCommit: the newest commit's subject and the files it changed.
func headCommit(t *testing.T) (string, []string) {
	t.Helper()
	r := runGit("show", "--name-only", "--format=%s", "HEAD")
	if r.Code != 0 {
		t.Fatal(r.Stderr)
	}
	lines := strings.Split(strings.TrimSpace(r.Stdout), "\n")
	var files []string
	for _, l := range lines[1:] {
		if l != "" {
			files = append(files, l)
		}
	}
	slices.Sort(files)
	return lines[0], files
}

// writePagesOp writes new pages and pages there already in one commit,
// a user's private one among them; what a page there already held that
// was never in git is committed first, by its own commit, and the new
// pages' bytes are counted in the wiki's usage.
func TestWritePagesOpWritesAMixedList(t *testing.T) {
	lastCommitsWiki(t, true)
	commitPage(t, "a.md", "bob", "Update a")
	writePageSaying(t, "a.md", "typed, not saved") // an auto-save never committed
	commitPage(t, "b.md", "bob", "Update b")
	freshUsage(t)
	before := usedBytes(false)

	kept, err := writePagesOp(asUser("alice"), pagesWrite{
		pages: []pageWrite{
			{slug: "a", text: "# A\n\nnew a\n"},
			{slug: "new", text: "# New\n", isNew: true},
			{slug: "b", text: "# B\n\nnew b\n"},
			{slug: "private/alice/deep/mine", text: "# Mine\n", isNew: true},
		},
		message: "Import pages (MCP)",
		what:    "pages before the import",
	})
	if err != nil || kept != 1 {
		t.Fatalf("kept %d, %v", kept, err)
	}
	for rel, want := range map[string]string{
		"a.md": "# A\n\nnew a\n", "new.md": "# New\n", "b.md": "# B\n\nnew b\n", "private/alice/deep/mine.md": "# Mine\n",
	} {
		if got := fileText(t, filepath.Join(cfg.Data, filepath.FromSlash(rel))); got != want {
			t.Errorf("%s: %q, want %q", rel, got, want)
		}
	}
	msg, files := headCommit(t)
	if want := []string{"a.md", "b.md", "new.md", "private/alice/deep/mine.md"}; msg != "Import pages (MCP)" || !slices.Equal(files, want) {
		t.Errorf("the commit: %q %v, want %v", msg, files, want)
	}
	if r := runGit("log", "-1", "--format=%s", "HEAD~1", "--", "a.md"); strings.TrimSpace(r.Stdout) != "Save pages before the import" {
		t.Errorf("text kept first: %q", r.Stdout)
	}
	if r := runGit("show", "HEAD~1:a.md"); !strings.Contains(r.Stdout, "typed, not saved") {
		t.Errorf("the kept text: %q", r.Stdout)
	}
	if got, want := usedBytes(false), before+int64(len("# New\n")+len("# Mine\n")); got != want {
		t.Errorf("usage %d, want %d", got, want)
	}
}

// One page that isn't as it was said to be, or not the asker's to write,
// refuses the whole write: no page written, no commit, not even of the
// text kept first; so does a wiki without room for the new pages, which a
// replacement alone never asks.
func TestWritePagesOpIsRefusedWhole(t *testing.T) {
	lastCommitsWiki(t, true)
	commitPage(t, "a.md", "bob", "Update a")
	commitPage(t, "private/bob/b.md", "bob", "Update b")
	writePageSaying(t, "a.md", "typed, not saved")
	head := strings.TrimSpace(runGit("rev-parse", "HEAD").Stdout)
	ok := []pageWrite{{slug: "a", text: "# A\n\nnew\n"}, {slug: "fresh", text: "# Fresh\n", isNew: true}}
	for _, c := range []struct {
		name string
		bad  pageWrite
		kind refusal
	}{
		{"a new page that is there", pageWrite{slug: "a", text: "x", isNew: true}, refusedExists},
		{"a page that isn't there", pageWrite{slug: "missing", text: "x"}, refusedNotFound},
		{"another user's private page", pageWrite{slug: "private/bob/b", text: "x"}, refusedNotFound},
		{"a new page in another user's folder", pageWrite{slug: "private/bob/c", text: "x", isNew: true}, refusedNotFound},
		{"a new page in a hidden folder", pageWrite{slug: "_assets/c", text: "x", isNew: true}, refusedInvalid},
		{"a page named twice", pageWrite{slug: "fresh", text: "again", isNew: true}, refusedInvalid},
	} {
		pages := append(slices.Clone(ok), c.bad)
		_, err := writePagesOp(asUser("alice"), pagesWrite{pages: pages, message: "Write", what: "pages"})
		if pe, isRefusal := refusalOf(err); !isRefusal || pe.kind != c.kind {
			t.Errorf("%s: %v, want a refusal of kind %v", c.name, err, c.kind)
		}
		if exists(filepath.Join(cfg.Data, "fresh.md")) || !strings.Contains(fileText(t, filepath.Join(cfg.Data, "a.md")), "typed, not saved") {
			t.Errorf("%s: written", c.name)
		}
		if now := strings.TrimSpace(runGit("rev-parse", "HEAD").Stdout); now != head {
			t.Errorf("%s: committed", c.name)
		}
	}

	freshUsage(t)
	s := *settings()
	s.StorageMax = usedBytes(true) + 5
	current.Store(&s)
	_, err := writePagesOp(asUser("alice"), pagesWrite{pages: ok, message: "Write", what: "pages"})
	if pe, isRefusal := refusalOf(err); !isRefusal || pe.kind != refusedFull {
		t.Errorf("no room: %v", err)
	}
	if exists(filepath.Join(cfg.Data, "fresh.md")) || !strings.Contains(fileText(t, filepath.Join(cfg.Data, "a.md")), "typed, not saved") {
		t.Error("no room: written")
	}
	// Replacing a page's text adds nothing new: it is never refused for room.
	if kept, err := writePagesOp(asUser("alice"), pagesWrite{pages: ok[:1], message: "Write", what: "pages"}); err != nil || kept != 1 {
		t.Errorf("a replacement in a full wiki: kept %d, %v", kept, err)
	}
}
