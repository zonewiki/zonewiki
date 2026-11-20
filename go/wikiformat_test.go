package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func writeFormatFile(t *testing.T, text string) {
	t.Helper()
	if err := os.WriteFile(formatPath(), []byte(text), 0o666); err != nil {
		t.Fatal(err)
	}
}

// What .zone-format says: a number, or nothing (a wiki from before it). A
// file that says anything else, or a format a newer Zone wrote, is refused.
func TestWikiFormatRead(t *testing.T) {
	testWiki(t, nil)
	if n, err := checkWikiFormat(); n != 0 || err != nil {
		t.Errorf("without the file: %d, %v", n, err)
	}
	for text, want := range map[string]int{"1\n": 1, " 1 \r\n": 1, "0\n": 0, strconv.Itoa(wikiFormat): wikiFormat} {
		writeFormatFile(t, text)
		if n, err := checkWikiFormat(); n != want || err != nil {
			t.Errorf("%q: %d, %v", text, n, err)
		}
	}
	for _, text := range []string{"", "x", "1 2", "1\n2\n", "-1", "+1", "1.0", "format = 1", "9999999999"} {
		writeFormatFile(t, text)
		if _, err := checkWikiFormat(); err == nil || !strings.Contains(err.Error(), "can't be read") {
			t.Errorf("%q: %v", text, err)
		}
	}
	writeFormatFile(t, strconv.Itoa(wikiFormat+1)+"\n")
	_, err := checkWikiFormat()
	if err == nil || !strings.Contains(err.Error(), fmt.Sprintf("format %d (.zone-format), which a newer Zone wrote", wikiFormat+1)) {
		t.Errorf("a newer format: %v", err)
	}
}

// A start of a wiki without .zone-format runs the steps from before it,
// then writes the file, in a commit of its own; a start that finds it
// changes nothing.
func TestWikiFormatNoted(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	start := func() {
		t.Helper()
		format, err := checkWikiFormat()
		if err != nil {
			t.Fatal(err)
		}
		setUpTasks(format)
		noteWikiFormat(format)
	}
	start()
	if got := fileText(t, formatPath()); got != strconv.Itoa(firstFormat)+"\n" {
		t.Errorf(".zone-format says %q", got)
	}
	if got := strings.TrimSpace(runGit("log", "--format=%s", "--", formatFileName).Stdout); got != "Note the format of the wiki's files" {
		t.Errorf("its commits: %q", got)
	}
	if got := runGit("status", "--porcelain", "--untracked-files=all").Stdout; got != "" {
		t.Errorf("not committed: %q", got)
	}
	head := runGit("rev-parse", "HEAD").Stdout
	start()
	if runGit("rev-parse", "HEAD").Stdout != head {
		t.Errorf("a start that found the file committed: %s", runGit("log", "--format=%s", "-3").Stdout)
	}
}

// A wiki that has .zone-format is past the steps from before it: a page's
// list= and pin= are an embed's, as in a wiki whose registry says so, and
// no start takes them over; without the file they are taken over.
func TestWikiFormatLeavesTheOldStepsOut(t *testing.T) {
	page := "# Shop\n\n[[tasks: list=shop view=focus pin=1]]\n"
	testWiki(t, map[string]string{"shop": page})
	migrateLists(false, firstFormat)
	if listsUnmigrated || exists(taskFiles.ListsPath("")) || fileText(t, safePath("shop")) != page {
		t.Errorf("a wiki of format %d was taken over: registry %v, page %q", firstFormat,
			exists(taskFiles.ListsPath("")), fileText(t, safePath("shop")))
	}
	migrateLists(false, 0)
	if listsUnmigrated || !exists(taskFiles.ListsPath("")) || strings.Contains(fileText(t, safePath("shop")), "pin=") {
		t.Errorf("a wiki without the file wasn't taken over: registry %v, page %q",
			exists(taskFiles.ListsPath("")), fileText(t, safePath("shop")))
	}
}

// The views' old names are rewritten in a wiki without a registry also when
// Zone's folder has the mark a start before .zone-format left there
// (views-named, out of git): the pages it spoke for may be back in their
// old text since, by a revert. The start that notes the format removes it.
func TestViewsRenamedDespiteAnOldMark(t *testing.T) {
	testWiki(t, map[string]string{"index": "# Home\n\n[[tasks: view=focus]]\n"})
	mark := statePath("views-named")
	if err := os.MkdirAll(filepath.Dir(mark), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(mark, []byte("1\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	migrateLists(false, 0)
	if got := fileText(t, safePath("index")); !strings.Contains(got, "[[tasks: view=today]]") {
		t.Errorf("the page: %q", got)
	}
	noteWikiFormat(0)
	if exists(mark) {
		t.Error("the old mark is still there")
	}
	if !exists(formatPath()) {
		t.Error("no .zone-format")
	}
}

// A start whose lists couldn't be set up writes no .zone-format: the next
// start runs their steps again.
func TestWikiFormatNotNotedWhileListsUnmigrated(t *testing.T) {
	testWiki(t, map[string]string{"shop": "# Shop\n\n[[tasks: list=shop pin=1]]\n"})
	t.Cleanup(func() { listsUnmigrated = false })
	if err := os.MkdirAll(taskFiles.Dir(""), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(taskFiles.OpenPath(""), []byte("[ half"), 0o666); err != nil {
		t.Fatal(err)
	}
	migrateLists(false, 0)
	noteWikiFormat(0)
	if !listsUnmigrated || exists(formatPath()) {
		t.Errorf("unmigrated %v, .zone-format written %v", listsUnmigrated, exists(formatPath()))
	}
}
