package mdimport

import (
	"errors"
	"flag"
	"net/url"
	"os"
	"path"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"testing/fstest"
)

var update = flag.Bool("update", false, "write the golden files in testdata/")

// options: a wiki's rules as the server hands them over, in small: a part
// with ":" or named "con" refused, an upload's name made free with -2,
// "bad.bin" given none.
func options() Options {
	taken := map[string]bool{}
	return Options{
		Folder:   "my-vault",
		PagePart: func(p string) bool { return p != "con" && !strings.Contains(p, ":") },
		FileName: func(want string) string {
			if want == "bad.bin" {
				return ""
			}
			ext := path.Ext(want)
			name := want
			for i := 2; taken[name]; i++ {
				name = strings.TrimSuffix(want, ext) + "-" + strconv.Itoa(i) + ext
			}
			taken[name] = true
			return name
		},
		FileURL: func(name string) string { return "/_assets/" + url.PathEscape(name) },
	}
}

// convert: the plan for a folder of these files, an Obsidian vault when
// obsidian (it gets .obsidian/).
func convert(t *testing.T, obsidian bool, files map[string]string) *Plan {
	t.Helper()
	fsys := fstest.MapFS{}
	for name, text := range files {
		fsys[name] = &fstest.MapFile{Data: []byte(text)}
	}
	if obsidian {
		fsys[".obsidian/app.json"] = &fstest.MapFile{Data: []byte("{}")}
	}
	plan, err := Convert(fsys, options())
	if err != nil {
		t.Fatal(err)
	}
	return plan
}

func page(t *testing.T, plan *Plan, slug string) string {
	t.Helper()
	for _, p := range plan.Pages {
		if p.Slug == slug {
			return p.Text
		}
	}
	var slugs []string
	for _, p := range plan.Pages {
		slugs = append(slugs, p.Slug)
	}
	t.Fatalf("no page %s in %v", slug, slugs)
	return ""
}

func TestPathsBecomeSlugs(t *testing.T) {
	plan := convert(t, false, map[string]string{
		"My Note (2024).md":    "# A\n",
		"Bücher/Lesen.md":      "# B\n",
		"Note.md":              "# C\n",
		"note.md":              "# D\n",
		"日記.md":                "# E\n",
		"con.md":               "# F\n",
		"_templates/Daily.md":  "# G\n",
		".trash/Old.md":        "# H\n",
		".git/HEAD.md":         "# I\n",
		".hidden/Seen.md":      "# J\n",
		"index.md":             "# K\n",
		"sub/index.md":         "# L\n",
		"Note/Child.md":        "# M\n",
		"日本/Tokyo.md":          "# N\n",
		"templates/Weekly.md":  "# O\n",
		"a:b.md":               "# P\n",
		"Board.canvas":         "{}",
		"Data.base":            "x",
		"Sketch.excalidraw.md": "x",
		"Other.excalidraw":     "x",
	})
	var slugs []string
	for _, p := range plan.Pages {
		slugs = append(slugs, p.Slug)
	}
	want := []string{
		"my-vault/hidden/seen", "my-vault/b-cher/lesen", "my-vault/my-note-2024", "my-vault/note",
		"my-vault/note/child", "my-vault/templates/daily", "my-vault/a-b", "my-vault/con-",
		"my-vault/index-2", "my-vault/note-2", "my-vault/sub/index", "my-vault/templates-2/weekly",
		"my-vault/note-2/tokyo", "my-vault/note-3",
	}
	if !reflect.DeepEqual(slugs, want) {
		t.Errorf("slugs:\n%q\nwant\n%q", slugs, want)
	}
	wantRenamed := []string{
		"`con.md` → `my-vault/con-` (not a name a page may have)",
		"`index.md` → `my-vault/index-2` (the folder's index page has the name)",
		"`note.md` → `my-vault/note-2` (another has the name)",
		"`templates/` → `my-vault/templates-2/` (another has the name)",
		"`日本/` → `my-vault/note-2/` (another has the name)",
		"`日記.md` → `my-vault/note-3` (another has the name)",
	}
	if !reflect.DeepEqual(plan.Report.Renamed, wantRenamed) {
		t.Errorf("renamed:\n%q\nwant\n%q", plan.Report.Renamed, wantRenamed)
	}
	wantOut := []string{
		"`Board.canvas`: an Obsidian canvas", "`Data.base`: an Obsidian base",
		"`Other.excalidraw`: an Excalidraw drawing", "`Sketch.excalidraw.md`: an Excalidraw drawing",
	}
	if !reflect.DeepEqual(plan.Report.LeftOut, wantOut) {
		t.Errorf("left out:\n%q\nwant\n%q", plan.Report.LeftOut, wantOut)
	}
	if plan.Report.Obsidian {
		t.Error("a folder without .obsidian/ is no vault")
	}
}

func TestAPartTheWikiRefusesGetsAHyphen(t *testing.T) {
	plan := convert(t, false, map[string]string{"con/con.md": "# x\n"})
	if got := plan.Pages[0].Slug; got != "my-vault/con-/con-" {
		t.Errorf("slug %q", got)
	}
}

// A note's name the caller refuses (a folder's guide) gets a hyphen and
// the caller's reason; a folder of that name is no note and keeps it.
func TestANoteNameTheCallerRefusesGetsAHyphen(t *testing.T) {
	fsys := fstest.MapFS{
		"AGENTS.md":        &fstest.MapFile{Data: []byte("# Agents\n")},
		"agents/CLAUDE.md": &fstest.MapFile{Data: []byte("# Claude\n")},
		"agents/plain.md":  &fstest.MapFile{Data: []byte("# Plain\n")},
	}
	o := options()
	o.NoteRefused = func(p string) string {
		if p == "agents" || p == "claude" {
			return "a guide's name"
		}
		return ""
	}
	plan, err := Convert(fsys, o)
	if err != nil {
		t.Fatal(err)
	}
	var slugs []string
	for _, p := range plan.Pages {
		slugs = append(slugs, p.Slug)
	}
	if want := []string{"my-vault/agents-", "my-vault/agents/claude-", "my-vault/agents/plain"}; !reflect.DeepEqual(slugs, want) {
		t.Errorf("slugs %q, want %q", slugs, want)
	}
	if want := []string{"`AGENTS.md` → `my-vault/agents-` (a guide's name)",
		"`agents/CLAUDE.md` → `my-vault/agents/claude-` (a guide's name)"}; !reflect.DeepEqual(plan.Report.Renamed, want) {
		t.Errorf("renamed %q", plan.Report.Renamed)
	}
}

func TestANoteWithoutATitleGetsItsName(t *testing.T) {
	plan := convert(t, false, map[string]string{
		"Plain (1).md":   "Some text.\n",
		"Front.md":       "---\ntags: a\n---\nText.\n",
		"FrontBlank.md":  "---\ntags: a\n---\n\nText.\n",
		"Sub.md":         "## Only a section\n",
		"Code.md":        "```\n# not a heading\n```\n",
		"Empty.md":       "",
		"CRLF.md":        "Line one.\r\nLine two.\r\n",
		"Titled.md":      "Intro.\n\n# Later Title\n",
		"Settext.md":     "Title\n=====\n",
		"Unclosed.md":    "---\nnot frontmatter\n",
		"HashNoSpace.md": "#tag only\n",
	})
	for slug, want := range map[string]string{
		"plain-1":     "# Plain (1)\n\nSome text.\n",
		"front":       "---\ntags: a\n---\n# Front\n\nText.\n",
		"frontblank":  "---\ntags: a\n---\n# FrontBlank\n\nText.\n",
		"sub":         "# Sub\n\n## Only a section\n",
		"code":        "# Code\n\n```\n# not a heading\n```\n",
		"empty":       "# Empty\n",
		"crlf":        "# CRLF\r\n\r\nLine one.\r\nLine two.\r\n",
		"titled":      "Intro.\n\n# Later Title\n",
		"settext":     "# Settext\n\nTitle\n=====\n",
		"unclosed":    "# Unclosed\n\n---\nnot frontmatter\n",
		"hashnospace": "# HashNoSpace\n\n#tag only\n",
	} {
		if got := page(t, plan, "my-vault/"+slug); got != want {
			t.Errorf("%s:\n%q\nwant\n%q", slug, got, want)
		}
	}
}

func TestTextThatNeedsNoChangeStaysAsWritten(t *testing.T) {
	texts := []string{
		"# Plain\n\nNo links, *emphasis*, a list:\n\n- one\n- [ ] two\n- [x] three\n\n$$\nx^2\n$$\n\n```mermaid\ngraph TD; A-->B\n```\n",
		"---\ntitle: kept\naliases: [a, b]\ncssclasses: wide\n---\n# Front\r\n\r\nCRLF text   \r\n",
		"# Tags in code\n\n`#not` and\n\n```\n#nor %%this%% ==or this== [[Link]]\n```\n",
		"# Links elsewhere\n\n[site](https://example.org/a#b) [mail](mailto:a@b.c) [here](#section) <https://x.y> [[#Own section]]\n",
		"# Callouts\n\n> [!NOTE]\n> GitHub's kind.\n\n> [!warning]\n> lower case is read too.\n",
	}
	for _, obsidian := range []bool{false, true} {
		for i, text := range texts {
			plan := convert(t, obsidian, map[string]string{"Note.md": text})
			if got := page(t, plan, "my-vault/note"); got != text {
				t.Errorf("obsidian %v, text %d:\n%q\nwant\n%q", obsidian, i, got, text)
			}
		}
	}
}

func TestWikilinks(t *testing.T) {
	plan := convert(t, true, map[string]string{
		"My Note.md":       "# My Note\n\n## Heading\n",
		"a/folder/Deep.md": "# Deep\n",
		"a/x/Dup.md":       "# Dup\n",
		"b/Dup.md":         "# Dup b\n",
		"b/Here.md":        "[[Dup]]\n",
		"img/pic.png":      "PNG",
		"other/pic.png":    "PNG2",
		"files/doc.pdf":    "PDF",
		"files/bad.bin":    "BIN",
		"files/unused.zip": "ZIP",
		"Links.md": strings.Join([]string{
			"# Links",
			"[[My Note]]",
			"[[my note]] [[My Note.md]]",
			"[[My Note#Heading]]",
			"[[My Note|alias]] [[My Note#Heading|both]]",
			"[[My Note#^block1]] [[My Note#^block1|kept alias]]",
			"[[#^own]]",
			"[[folder/Deep]]",
			"[[Dup]]",
			"[[Nowhere]] [[Nowhere|label]]",
			"![[img/pic.png]] ![[img/pic.png|300]] ![[img/pic.png|a cat|200]] ![[img/pic.png|640x480]]",
			"![[other/pic.png]]",
			"![[doc.pdf]] [[doc.pdf]] [[doc.pdf|the doc]]",
			"![[bad.bin]]",
			"![[My Note]] ![[My Note#Heading]]",
			"| a | [[My Note\\|cell]] | ![[img/pic.png\\|100]] |",
			"`[[My Note]]` stays",
			"[[My Note#A#B]]",
		}, "\n") + "\n",
	})
	want := strings.Join([]string{
		"# Links",
		"[[my-vault/my-note|My Note]]",
		"[[my-vault/my-note|my note]] [[my-vault/my-note|My Note.md]]",
		"[[my-vault/my-note#Heading|My Note › Heading]]",
		"[[my-vault/my-note|alias]] [[my-vault/my-note#Heading|both]]",
		"[[my-vault/my-note|My Note]] [[my-vault/my-note|kept alias]]",
		"[[#^own]]",
		"[[my-vault/a/folder/deep|folder/Deep]]",
		"[[my-vault/b/dup|Dup]]",
		"[[Nowhere]] [[Nowhere|label]]",
		"![](/_assets/pic.png) ![|300](/_assets/pic.png) ![a cat|200](/_assets/pic.png) ![|640](/_assets/pic.png)",
		"![](/_assets/pic-2.png)",
		"[doc.pdf](/_assets/doc.pdf) [doc.pdf](/_assets/doc.pdf) [the doc](/_assets/doc.pdf)",
		"![[bad.bin]]",
		"[[my-vault/my-note|My Note]] [[my-vault/my-note#Heading|My Note › Heading]]",
		"| a | [[my-vault/my-note\\|cell]] | ![\\|100](/_assets/pic.png) |",
		"`[[My Note]]` stays",
		"[[my-vault/my-note#B|My Note › B]]",
	}, "\n") + "\n"
	if got := page(t, plan, "my-vault/links"); got != want {
		t.Errorf("links:\n%s\nwant\n%s", got, want)
	}
	if got := page(t, plan, "my-vault/b/here"); got != "# Here\n\n[[my-vault/b/dup|Dup]]\n" {
		t.Errorf("the one in the note's folder: %q", got)
	}
	wantFiles := []File{{"files/doc.pdf", "doc.pdf"}, {"img/pic.png", "pic.png"}, {"other/pic.png", "pic-2.png"}}
	if !reflect.DeepEqual(plan.Files, wantFiles) {
		t.Errorf("files %v, want %v", plan.Files, wantFiles)
	}
	for list, want := range map[string][]string{
		"ambiguous": {
			"`Links.md`: `[[Dup]]` could be `a/x/Dup.md` or `b/Dup.md`; it links to `b/Dup.md`",
			"`b/Here.md`: `[[Dup]]` could be `a/x/Dup.md` or `b/Dup.md`; it links to `b/Dup.md`",
		},
		"missing": {
			"`Links.md`: `[[Nowhere]]` finds no note or file",
			"`Links.md`: `[[Nowhere|label]]` finds no note or file",
		},
		"lost": {
			"`Links.md`: `![[My Note#Heading]]`, an embedded note, is a link",
			"`Links.md`: `![[My Note]]`, an embedded note, is a link",
			"`Links.md`: `[[#^own]]`, a block reference, is a link to nothing",
			"`Links.md`: `[[My Note#^block1]]`, a block reference, links to the page",
			"`Links.md`: `[[My Note#^block1|kept alias]]`, a block reference, links to the page",
		},
		"left out": {
			"`files/bad.bin`: no name a file in the wiki can have",
			"`files/unused.zip`: no note links to it",
		},
	} {
		got := map[string][]string{"ambiguous": plan.Report.Ambiguous, "missing": plan.Report.Missing, "lost": plan.Report.Lost, "left out": plan.Report.LeftOut}[list]
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s:\n%q\nwant\n%q", list, got, want)
		}
	}
}

func TestShortestPathWinsOutsideTheFolder(t *testing.T) {
	plan := convert(t, false, map[string]string{
		"deep/er/Dup.md": "# 1\n",
		"x/Dup.md":       "# 2\n",
		"c/From.md":      "# From\n[[Dup]]\n",
	})
	if got := page(t, plan, "my-vault/c/from"); got != "# From\n[[my-vault/x/dup|Dup]]\n" {
		t.Errorf("%q", got)
	}
}

// TestAMacsNamesAreFound: a name a Mac wrote decomposed ("u" and a
// combining mark) is found by a link written composed, and slugified as one.
func TestAMacsNamesAreFound(t *testing.T) {
	plan := convert(t, false, map[string]string{
		"Bücher.md": "Mine\n",
		"From.md":    "# From\n[[Bücher]] [x](B%C3%BCcher.md)\n",
	})
	if got := page(t, plan, "my-vault/from"); got != "# From\n[[my-vault/b-cher|Bücher]] [[my-vault/b-cher|x]]\n" {
		t.Errorf("%q", got)
	}
	if got := page(t, plan, "my-vault/b-cher"); got != "# Bücher\n\nMine\n" {
		t.Errorf("%q", got)
	}
}

func TestMarkdownLinks(t *testing.T) {
	plan := convert(t, false, map[string]string{
		"Other Note.md":   "# Other\n\n## Part two\n",
		"a/Dup.md":        "# a\n",
		"Dup.md":          "# root\n",
		"a/Sib.md":        "# sib\n",
		"a/img/pic.png":   "PNG",
		"a/files/a b.pdf": "PDF",
		"a/Links.md": strings.Join([]string{
			"# Links",
			"[x](../Other%20Note.md) [y](<../Other Note.md>) [z](/Other%20Note.md#Part%20two)",
			"[](../Other%20Note.md)",
			"[sib](Sib.md) [sib2](./Sib)",
			"[dup](Dup.md)",
			"![a pic](img/pic.png) ![w|300](img/pic.png \"title\")",
			"[the file](files/a%20b.pdf) [page](files/a%20b.pdf#page=2)",
			"[gone](missing.md) [gone2](../nowhere/x.pdf)",
			"[ext](https://a.example/b.md) [mail](mailto:x@y.z) [anchor](#here) [ftp](ftp://h/x.md)",
			"![embed](Sib.md) [blk](Sib.md#^abc)",
		}, "\n") + "\n",
	})
	want := strings.Join([]string{
		"# Links",
		"[[my-vault/other-note|x]] [[my-vault/other-note|y]] [[my-vault/other-note#Part two|z]]",
		"[[my-vault/other-note|Other Note]]",
		"[[my-vault/a/sib|sib]] [[my-vault/a/sib|sib2]]",
		"[[my-vault/dup|dup]]",
		"![a pic](/_assets/pic.png) ![w|300](/_assets/pic.png \"title\")",
		"[the file](/_assets/a%20b.pdf) [page](/_assets/a%20b.pdf#page=2)",
		"[gone](missing.md) [gone2](../nowhere/x.pdf)",
		"[ext](https://a.example/b.md) [mail](mailto:x@y.z) [anchor](#here) [ftp](ftp://h/x.md)",
		"[[my-vault/a/sib|embed]] [[my-vault/a/sib|blk]]",
	}, "\n") + "\n"
	if got := page(t, plan, "my-vault/a/links"); got != want {
		t.Errorf("links:\n%s\nwant\n%s", got, want)
	}
	wantAmb := []string{"`a/Links.md`: `[dup](Dup.md)` could be `Dup.md` or `a/Dup.md`; it links to `Dup.md`"}
	if !reflect.DeepEqual(plan.Report.Ambiguous, wantAmb) {
		t.Errorf("ambiguous %q", plan.Report.Ambiguous)
	}
	if len(plan.Report.Missing) != 2 || len(plan.Report.Lost) != 2 {
		t.Errorf("missing %q, lost %q", plan.Report.Missing, plan.Report.Lost)
	}
}

func TestObsidianSyntax(t *testing.T) {
	text := strings.Join([]string{
		"---",
		"tags:",
		"  - journal",
		"aliases: [mine]",
		"---",
		"# Note",
		"",
		"Tags #idea, #Idea again, #project/alpha/ and (#paren) not#this, #123, #2024/11, #y2024 #tag-with_dash",
		"See https://x.example/#frag and [l](https://x.example/#frag2) `#code`",
		"## Heading not a tag",
		"",
		"This is ==very important== text %%a remark%% here. ^para1",
		"A == B == C stays.",
		"%%",
		"hidden #hiddentag",
		"%%",
		"Table cell ^",
		"^blockline",
		"",
		"> [!tip]- Remember this",
		"> Folded.",
		"",
		"> [!example]",
		"> An example.",
		"",
		"> [!INFO]+",
		"> Open.",
		"",
		"> [!custom-kind] Mine",
		"> Custom.",
		"",
		"> > [!bug] nested",
		"> > deep",
		"",
		"```dataview",
		"LIST FROM #project",
		"```",
		"",
		"status:: active",
		"- [ ] task 📅 2026-12-01 🔁 every week",
		"- [ ] plain task",
		"Inline [due:: tomorrow] field.",
	}, "\n")
	want := strings.Join([]string{
		"---",
		"tags: journal, idea, project/alpha, y2024, tag-with_dash",
		"aliases: [mine]",
		"---",
		"# Note",
		"",
		"Tags #idea, #Idea again, #project/alpha/ and (#paren) not#this, #123, #2024/11, #y2024 #tag-with_dash",
		"See https://x.example/#frag and [l](https://x.example/#frag2) `#code`",
		"## Heading not a tag",
		"",
		"This is <mark>very important</mark> text here.",
		"A == B == C stays.",
		"Table cell ^",
		"",
		"",
		"> [!TIP]",
		"> **Remember this**",
		">",
		"> Folded.",
		"",
		"> [!NOTE]",
		"> **Example**",
		">",
		"> An example.",
		"",
		"> [!NOTE]",
		"> **Info**",
		">",
		"> Open.",
		"",
		"> [!NOTE]",
		"> **Mine**",
		">",
		"> Custom.",
		"",
		"> > [!CAUTION]",
		"> > **nested**",
		"> >",
		"> > deep",
		"",
		"```dataview",
		"LIST FROM #project",
		"```",
		"",
		"status:: active",
		"- [ ] task 📅 2026-12-01 🔁 every week",
		"- [ ] plain task",
		"Inline [due:: tomorrow] field.",
	}, "\n")
	plan := convert(t, true, map[string]string{"Note.md": text})
	if got := page(t, plan, "my-vault/note"); got != want {
		t.Errorf("obsidian:\n%s\nwant\n%s", got, want)
	}
	wantLost := []string{"`Note.md`: 1 Dataview query, 2 Dataview fields (key:: value), 1 Tasks-plugin task, as written"}
	if !reflect.DeepEqual(plan.Report.Lost, wantLost) {
		t.Errorf("lost %q", plan.Report.Lost)
	}
	if !plan.Report.Obsidian {
		t.Error("a vault")
	}
	// In a plain folder the same text stays as written.
	plain := convert(t, false, map[string]string{"Note.md": text})
	if got := page(t, plain, "my-vault/note"); got != text {
		t.Errorf("plain folder:\n%s", got)
	}
	if len(plain.Report.Lost) != 0 {
		t.Errorf("lost in a plain folder: %q", plain.Report.Lost)
	}
}

func TestTheCallerSaysItIsAVault(t *testing.T) {
	o := options()
	o.Obsidian = true
	plan, err := Convert(fstest.MapFS{"N.md": {Data: []byte("# N\n==hi==\n")}}, o)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Pages[0].Text != "# N\n<mark>hi</mark>\n" || !plan.Report.Obsidian {
		t.Errorf("%q %v", plan.Pages[0].Text, plan.Report.Obsidian)
	}
}

func TestNoNotes(t *testing.T) {
	_, err := Convert(fstest.MapFS{"a.png": {Data: []byte("x")}, ".obsidian/app.json": {Data: []byte("{}")}}, options())
	if !errors.Is(err, ErrNoNotes) {
		t.Errorf("err %v", err)
	}
}

func TestTheIndexPage(t *testing.T) {
	o := options()
	o.Name = "My Vault"
	plan, err := Convert(fstest.MapFS{
		"b.md":   {Data: []byte("# B\n[[Nowhere]]\n")},
		"A/a.md": {Data: []byte("# A\n")},
	}, o)
	if err != nil {
		t.Fatal(err)
	}
	want := "# My Vault\n\nImported from a folder of markdown notes: 2 pages.\n\n## Pages\n\n" +
		"- [[my-vault/a/a|A/a]]\n- [[my-vault/b|b]]\n\n## Links to nothing\n\n- `b.md`: `[[Nowhere]]` finds no note or file\n"
	if plan.Index.Slug != "my-vault/index" || plan.Index.Text != want {
		t.Errorf("index %s:\n%s\nwant\n%s", plan.Index.Slug, plan.Index.Text, want)
	}
	if plan.Pages[0].Slug != "my-vault/a/a" {
		t.Errorf("pages by path: %v", plan.Pages)
	}
	o.Name = ""
	plan, _ = Convert(fstest.MapFS{"b.md": {Data: []byte("# B\n")}}, o)
	if !strings.HasPrefix(plan.Index.Text, "# My Vault\n\nImported from a folder of markdown notes: 1 page.\n") {
		t.Errorf("a name from the folder: %q", plan.Index.Text)
	}
}

// TestSampleFolders: the vault and the plain folder in testdata/ against
// their golden files (go test -run TestSampleFolders -update writes them).
func TestSampleFolders(t *testing.T) {
	for _, name := range []string{"vault", "plain"} {
		plan, err := Convert(os.DirFS("testdata/"+name), options())
		if err != nil {
			t.Fatal(err)
		}
		var b strings.Builder
		for _, p := range plan.Pages {
			b.WriteString("=== page " + p.Slug + "\n" + p.Text)
		}
		for _, f := range plan.Files {
			b.WriteString("=== file " + f.Src + " → " + f.Name + "\n")
		}
		b.WriteString("=== index " + plan.Index.Slug + "\n" + plan.Index.Text)
		golden := "testdata/" + name + ".golden"
		if *update {
			if err := os.WriteFile(golden, []byte(b.String()), 0o644); err != nil {
				t.Fatal(err)
			}
		}
		want, err := os.ReadFile(golden)
		if err != nil {
			t.Fatal(err)
		}
		if b.String() != string(want) {
			t.Errorf("%s differs from %s:\n%s", name, golden, b.String())
		}
	}
}

// A name in the report stays one code span whatever backticks it holds,
// so a file named to break out of it can't add markdown or HTML there.
func TestCodeSpanHoldsAnyBackticks(t *testing.T) {
	for in, want := range map[string]string{
		"plain.md":        "`plain.md`",
		"a`b.md":          "`` a`b.md ``",
		"a``b<x>.md":      "``` a``b<x>.md ```",
		"`start and end`": "`` `start and end` ``",
	} {
		if got := code(in); got != want {
			t.Errorf("code(%q) = %q, want %q", in, got, want)
		}
	}
}
