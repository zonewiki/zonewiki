package main

import (
	"fmt"
	"os"
	"os/exec"
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

// What .zone-format says: a number, or nothing (a new wiki). A
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

// A start of a wiki without .zone-format (a new one) writes the file, in a
// commit of its own, and takes nothing over: a page's list= and pin= of
// before lists were places, its view=focus and a registry's version stay as
// written. A start that finds the file changes nothing.
func TestWikiFormatNoted(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	page := "# Shop\n\n[[tasks: list=shop view=focus pin=1]]\n"
	testWiki(t, map[string]string{"shop": page})
	cfg.GitEnabled = true
	gitSetup()
	reg := `{"version": 1, "lists": [{"name": "work", "view": "plan"}]}`
	writeRegistry(t, "", reg)
	runGit("add", "-A")
	runGit("commit", "-q", "-m", "A wiki")
	start := func() {
		t.Helper()
		format, err := checkWikiFormat()
		if err != nil {
			t.Fatal(err)
		}
		setUpTasks()
		noteWikiFormat(format)
	}
	start()
	if got := fileText(t, formatPath()); got != strconv.Itoa(wikiFormat)+"\n" {
		t.Errorf(".zone-format says %q", got)
	}
	if got := strings.TrimSpace(runGit("log", "--format=%s", "--", formatFileName).Stdout); got != "Note the format of the wiki's files" {
		t.Errorf("its commits: %q", got)
	}
	if got := runGit("status", "--porcelain", "--untracked-files=all").Stdout; got != "" {
		t.Errorf("not committed: %q", got)
	}
	if fileText(t, safePath("shop")) != page || fileText(t, taskFiles.ListsPath("")) != reg {
		t.Errorf("taken over: page %q, registry %q", fileText(t, safePath("shop")), fileText(t, taskFiles.ListsPath("")))
	}
	head := runGit("rev-parse", "HEAD").Stdout
	start()
	if runGit("rev-parse", "HEAD").Stdout != head {
		t.Errorf("a start that found the file committed: %s", runGit("log", "--format=%s", "-3").Stdout)
	}
}
