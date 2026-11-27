package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"testing/fstest"
	"zonewiki/internal/mdimport"
)

// importWiki: a wiki with git and users: alice in the group dev, bob, and
// vera a viewer; its home page committed.
func importWiki(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	user := func(name, more string) string {
		return "[user " + name + "]\npassword = " + hashPassword(name+"-pw") + "\n" + more
	}
	configWiki(t, user("alice", "groups = dev\n")+user("bob", "")+user("vera", "role = viewer\n"))
	if err := os.WriteFile(filepath.Join(cfg.Data, "index.md"), []byte("# Home\n\nWelcome.\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	cfg.GitEnabled = true
	gitSetup()
}

// The files of a vault: a picture with CR LF and NUL in it, which must
// arrive byte for byte, and a PDF.
var vaultFiles = fstest.MapFS{
	"My Vault/attachments/pic.png": {Data: []byte("\x89PNG\r\n\x1a\n\x00\x01\r\nend")},
	"My Vault/deck.pdf":            {Data: []byte("%PDF-1.4\n")},
}

// vaultPlan: a plan as the converter makes one, into folder, its files'
// names given in place's uploads.
func vaultPlan(folder string) *mdimport.Plan {
	return &mdimport.Plan{
		Pages: []mdimport.Page{
			{Slug: folder + "/garden", Text: "# Garden\n\n![pic](/_assets/pic.png)\n"},
			{Slug: folder + "/deep/notes", Text: "# Notes\n\n[deck](/_assets/deck.pdf)\n"},
		},
		Index: mdimport.Page{Slug: folder + "/index", Text: "# My Vault\n\n- [[" + folder + "/garden]]\n- [[" + folder + "/deep/notes]]\n"},
		Files: []mdimport.File{{Src: "My Vault/attachments/pic.png", Name: "pic.png"}, {Src: "My Vault/deck.pdf", Name: "deck.pdf"}},
	}
}

func headSHA(t *testing.T) string {
	t.Helper()
	return strings.TrimSpace(runGit("rev-parse", "HEAD").Stdout)
}

func dataFile(t *testing.T, rel string) (string, bool) {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(cfg.Data, filepath.FromSlash(rel)))
	return string(b), err == nil
}

// An import is one commit: its pages, the folder's index, the files byte
// for byte and a line on the home page that links the index; what the
// home held that was never in git is committed first, by its own commit.
// Undo takes it all back (the line off the home, which keeps the text
// kept first), and undoing that brings it all back.
func TestImportOpIsOneCommitAndUndoesWhole(t *testing.T) {
	importWiki(t)
	if err := os.WriteFile(filepath.Join(cfg.Data, "index.md"), []byte("# Home\n\nWelcome.\n\n- [[garden]]\n"), 0o666); err != nil {
		t.Fatal(err) // an auto-save never committed
	}
	freshUsage(t)
	before := usedBytes(false)

	kept, err := importOp(asUser("alice"), "my-vault", vaultPlan("my-vault"), vaultFiles, "My Vault", " (test)")
	if err != nil || kept != 1 {
		t.Fatalf("kept %d, %v", kept, err)
	}
	msg, files := headCommit(t)
	want := []string{"_assets/deck.pdf", "_assets/pic.png", "index.md", "my-vault/deep/notes.md", "my-vault/garden.md", "my-vault/index.md"}
	if msg != "Import 2 pages and 2 files from My Vault (test)" || !slices.Equal(files, want) {
		t.Errorf("the commit: %q %v, want %v", msg, files, want)
	}
	if r := runGit("log", "-1", "--format=%s", "HEAD~1"); strings.TrimSpace(r.Stdout) != "Save index" {
		t.Errorf("text kept first: %q", r.Stdout)
	}
	home := "# Home\n\nWelcome.\n\n- [[garden]]\n- [[my-vault/index|My Vault]]\n"
	for rel, text := range map[string]string{
		"index.md": home, "my-vault/garden.md": vaultPlan("my-vault").Pages[0].Text,
		"_assets/pic.png": string(vaultFiles["My Vault/attachments/pic.png"].Data), "_assets/deck.pdf": "%PDF-1.4\n",
	} {
		if got, _ := dataFile(t, rel); got != text {
			t.Errorf("%s: %q, want %q", rel, got, text)
		}
	}
	if b, ok := gitBlob("HEAD", "_assets/pic.png"); !ok || string(b) != string(vaultFiles["My Vault/attachments/pic.png"].Data) {
		t.Errorf("the picture in git: %q", b)
	}
	var size int64
	for _, p := range vaultPlan("my-vault").Pages {
		size += int64(len(p.Text))
	}
	size += int64(len(vaultPlan("my-vault").Index.Text) + len(vaultFiles["My Vault/attachments/pic.png"].Data) + len("%PDF-1.4\n"))
	if got := usedBytes(false); got != before+size {
		t.Errorf("usage %d, want %d", got, before+size)
	}

	// Undo: pages and files gone, the home as before the import.
	u, err := undoChangesOp(asUser("alice"), []string{headSHA(t)}, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(u.gone) != 3 || len(u.filesGone) != 2 || !slices.Equal(u.back, []string{"index"}) {
		t.Errorf("undone: %+v", u)
	}
	for _, rel := range []string{"my-vault/index.md", "my-vault/garden.md", "my-vault/deep/notes.md", "_assets/pic.png", "_assets/deck.pdf"} {
		if _, there := dataFile(t, rel); there {
			t.Errorf("%s is still there", rel)
		}
	}
	if got, _ := dataFile(t, "index.md"); got != "# Home\n\nWelcome.\n\n- [[garden]]\n" {
		t.Errorf("the home after the undo: %q", got)
	}

	// And the undo undone: all back.
	if _, err := undoChangesOp(asUser("alice"), []string{headSHA(t)}, "", nil); err != nil {
		t.Fatal(err)
	}
	if got, _ := dataFile(t, "_assets/pic.png"); got != string(vaultFiles["My Vault/attachments/pic.png"].Data) {
		t.Errorf("the picture back: %q", got)
	}
	if got, _ := dataFile(t, "index.md"); got != home {
		t.Errorf("the home back: %q", got)
	}
	if got, _ := dataFile(t, "my-vault/deep/notes.md"); got != vaultPlan("my-vault").Pages[1].Text {
		t.Errorf("a page back: %q", got)
	}
}

// Into a private folder or a group's: the files go to that place's
// uploads and the line to its home, which is made first, in its own
// commit, when it isn't there yet (so the import's undo never deletes a
// home). Without files the message names only pages.
func TestImportOpIntoAPlace(t *testing.T) {
	importWiki(t)
	plan := vaultPlan("private/alice/vault")
	if _, err := importOp(asUser("alice"), "private/alice/vault", plan, vaultFiles, "", ""); err != nil {
		t.Fatal(err)
	}
	msg, files := headCommit(t)
	if msg != "Import 2 pages and 2 files from vault" || !slices.Contains(files, "private/alice/_assets/pic.png") || !slices.Contains(files, "private/alice/index.md") {
		t.Errorf("the commit: %q %v", msg, files)
	}
	if r := runGit("log", "-1", "--format=%s", "HEAD~1"); strings.TrimSpace(r.Stdout) != "Create private/alice/index" {
		t.Errorf("the home made first: %q", r.Stdout)
	}
	if got, _ := dataFile(t, "private/alice/index.md"); got != privateHomeText+"- [[private/alice/vault/index|vault]]\n" {
		t.Errorf("the private home: %q", got)
	}
	if _, err := undoChangesOp(asUser("alice"), []string{headSHA(t)}, "", nil); err != nil {
		t.Errorf("the import's undo: %v", err)
	}
	if got, _ := dataFile(t, "private/alice/index.md"); got != privateHomeText {
		t.Errorf("the private home after the undo: %q", got)
	}

	plan = vaultPlan("groups/dev/notes")
	plan.Files = nil
	if _, err := importOp(asUser("alice"), "groups/dev/notes", plan, vaultFiles, "Notes", ""); err != nil {
		t.Fatal(err)
	}
	if msg, _ := headCommit(t); msg != "Import 2 pages from Notes" {
		t.Errorf("the commit: %q", msg)
	}
	if got, _ := dataFile(t, "groups/dev/index.md"); got != groupHomeText("dev")+"- [[groups/dev/notes/index|Notes]]\n" {
		t.Errorf("the group's home: %q", got)
	}
}

// An import refused leaves the wiki and git as they were: no page, no
// file (nor the uploads' folder made for them), no commit; files written
// for pages that writePagesOp then refuses are removed again.
func TestImportOpIsRefusedWhole(t *testing.T) {
	importWiki(t)
	commitPage(t, "taken/a.md", "bob", "Update taken/a")
	writePageSaying(t, "_assets/pic.png", "an upload of bob's")
	if err := gitCommit("bob", "Upload pic.png", filepath.Join(cfg.Data, "_assets", "pic.png")); err != nil {
		t.Fatal(err)
	}
	head := headSHA(t)
	status := runGit("status", "--porcelain", "--untracked-files=all").Stdout
	pic, _ := dataFile(t, "_assets/pic.png")

	vault := func(f func(p *mdimport.Plan)) *mdimport.Plan {
		p := vaultPlan("fresh")
		p.Files[0].Name = "picture.png"
		if f != nil {
			f(p)
		}
		return p
	}
	full := func(on bool) {
		s := *settings()
		s.StorageMax = 0
		if on {
			freshUsage(t)
			s.StorageMax = usedBytes(true) + 20
		}
		current.Store(&s)
	}
	for _, c := range []struct {
		name   string
		user   string
		folder string
		plan   *mdimport.Plan
		full   bool
		kind   refusal
	}{
		{"an existing folder", "alice", "taken", vault(nil), false, refusedExists},
		{"another user's private folder", "alice", "private/bob/notes", vault(nil), false, refusedNotFound},
		{"a group's the asker isn't in", "bob", "groups/dev/notes", vault(nil), false, refusedNotFound},
		{"a place's own folder", "alice", "groups/dev", vault(nil), false, refusedInvalid},
		{"a viewer", "vera", "fresh", vault(nil), false, refusedReadOnly},
		{"a full wiki", "alice", "fresh", vault(nil), true, refusedFull},
		{"an upload's name taken meanwhile", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Files[0].Name = "pic.png" }), false, refusedExists},
		{"a file named twice", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Files[1].Name = "Picture.png" }), false, refusedInvalid},
		{"a source that isn't there", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Files[1].Src = "My Vault/gone.pdf" }), false, refusedInvalid},
		{"a page outside the folder", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Pages[1].Slug = "elsewhere" }), false, refusedInvalid},
		{"an index outside the folder", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Index.Slug = "fresh-index" }), false, refusedInvalid},
		// Refused by writePagesOp after the files are written: they go again.
		{"a page in a hidden folder", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Pages[1].Slug = "fresh/_x/notes" }), false, refusedInvalid},
		{"a page named twice", "alice", "fresh", vault(func(p *mdimport.Plan) { p.Pages[1].Slug = "fresh/garden" }), false, refusedInvalid},
	} {
		full(c.full)
		_, err := importOp(asUser(c.user), c.folder, c.plan, vaultFiles, "", "")
		full(false)
		if pe, ok := refusalOf(err); !ok || pe.kind != c.kind {
			t.Errorf("%s: %v, want a refusal of kind %v", c.name, err, c.kind)
		}
		if now := headSHA(t); now != head {
			t.Errorf("%s: committed", c.name)
		}
		if now := runGit("status", "--porcelain", "--untracked-files=all").Stdout; now != status {
			t.Errorf("%s: left %q", c.name, now)
		}
		if got, _ := dataFile(t, "_assets/pic.png"); got != pic {
			t.Errorf("%s: bob's upload overwritten", c.name)
		}
		for _, dir := range []string{"fresh", "private/bob/notes", "groups/dev"} {
			if exists(filepath.Join(cfg.Data, filepath.FromSlash(dir))) {
				t.Errorf("%s: %s made", c.name, dir)
			}
		}
	}
	// The uploads' folder of a place, made for files that then went again,
	// goes too (the group's home, made first as a visit makes it, stays).
	p := vaultPlan("groups/dev/x")
	p.Pages[1].Slug = "groups/dev/x/_y/notes"
	if _, err := importOp(asUser("alice"), "groups/dev/x", p, vaultFiles, "", ""); err == nil {
		t.Error("a page in a hidden folder of a group's: imported")
	}
	if exists(filepath.Join(cfg.Data, "groups", "dev", "_assets")) {
		t.Error("the group's uploads folder was left")
	}
	if exists(filepath.Join(cfg.Data, "groups", "dev", "x")) {
		t.Error("the group's folder was written")
	}
}

// importOptions checks the folder as the import does, and gives the
// converter the server's rules: a file's name free in the place's uploads
// and not given twice in one import, numbered otherwise; the link to it
// the place's, percent-encoded; a page's part as a slug takes it.
func TestImportOptions(t *testing.T) {
	importWiki(t)
	writePageSaying(t, "private/alice/_assets/pic.png", "there")
	if _, err := importOptions(asUser("alice"), "index"); err != nil {
		t.Errorf("a folder of the page index's name: %v", err) // a page, not a folder
	}
	// A folder holding only empty folders (an import undone) clashes with nothing.
	if err := os.MkdirAll(filepath.Join(cfg.Data, "undone/sub"), 0o777); err != nil {
		t.Fatal(err)
	}
	if _, err := importOptions(asUser("alice"), "undone"); err != nil {
		t.Errorf("an empty folder: %v", err)
	}
	writePageSaying(t, "undone/sub/note", "x")
	if _, err := importOptions(asUser("alice"), "undone"); err == nil {
		t.Error("a folder with a page in it: no refusal")
	}
	for folder, kind := range map[string]refusal{
		"private/bob/x": refusedNotFound, "_hidden/x": refusedInvalid, "a//b": refusedInvalid, "../x": refusedInvalid,
		"private/alice": refusedInvalid, "private/alice/_assets": refusedInvalid,
	} {
		if _, err := importOptions(asUser("alice"), folder); err == nil {
			t.Errorf("%s: no refusal", folder)
		} else if pe, ok := refusalOf(err); !ok || pe.kind != kind {
			t.Errorf("%s: %v, want %v", folder, err, kind)
		}
	}
	if _, err := importOptions(asUser("vera"), "x"); err == nil {
		t.Error("a viewer's import: no refusal")
	}

	o, err := importOptions(asUser("alice"), "private/alice/vault")
	if err != nil {
		t.Fatal(err)
	}
	if o.Folder != "private/alice/vault" || o.PagePart("a:b") || !o.PagePart("notes") {
		t.Errorf("folder %q, page parts", o.Folder)
	}
	if o.NoteRefused("agents") == "" || o.NoteRefused("claude") == "" || o.NoteRefused("notes") != "" {
		t.Error("a note named as a folder's guide isn't refused, or another is")
	}
	for _, c := range [][2]string{
		{"pic.png", "pic-2.png"},     // there in alice's uploads
		{"sub/pic.png", "pic-3.png"}, // its base name, given once already
		{"PIC-3.png", "PIC-3-2.png"}, // given, in another case
		{"My Deck (v2).pdf", "My Deck (v2).pdf"},
		{".hidden", ""},
		{"", ""},
	} {
		if got := o.FileName(c[0]); got != c[1] {
			t.Errorf("FileName(%q) = %q, want %q", c[0], got, c[1])
		}
	}
	if got := o.FileURL("My Deck (v2).pdf"); got != "/private/alice/_assets/My%20Deck%20%28v2%29.pdf" {
		t.Errorf("FileURL: %q", got)
	}
	shared, err := importOptions(asUser("bob"), "vault")
	if err != nil {
		t.Fatal(err)
	}
	if got := shared.FileName("pic.png"); got != "pic.png" {
		t.Errorf("a shared import's name: %q", got) // alice's private picture is no clash
	}
	if got := shared.FileURL("pic.png"); got != "/_assets/pic.png" {
		t.Errorf("a shared import's URL: %q", got)
	}
}

func TestWithLine(t *testing.T) {
	for text, want := range map[string]string{
		"":                    "- L\n",
		"# Home\n\n":          "# Home\n\n- L\n",
		"# Home":              "# Home\n\n- L\n",
		"# Home\n\nText.\n":   "# Home\n\nText.\n\n- L\n",
		"# Home\n\n- a\n":     "# Home\n\n- a\n- L\n",
		"# Home\n\n- a\n\n\n": "# Home\n\n- a\n\n\n- L\n",
	} {
		if got := withLine(text, "- L"); got != want {
			t.Errorf("withLine(%q) = %q, want %q", text, got, want)
		}
	}
	if got := importLine("a/my-vault", "a/my-vault/index", "My [Vault] | x\n"); got != "- [[a/my-vault/index|My Vault  x]]" {
		t.Errorf("importLine: %q", got)
	}
	if got := importLine("a/my-vault", "a/my-vault/index", ""); got != "- [[a/my-vault/index|my-vault]]" {
		t.Errorf("importLine without a name: %q", got)
	}
}
