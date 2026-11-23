// The format of the wiki's files, in .zone-format at its root (The data
// format in docs/spec/deployment.md).
package main

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"zonewiki/internal/atomicfile"
)

// wikiFormat: the format of the wiki's files this Zonewiki reads and writes. A
// change to what Zonewiki keeps in git that an older Zonewiki would misread raises
// it, with a step at start that takes a wiki of the format before over and
// writes the new number in that step's own commit (writeWikiFormat): so a
// revert of the commit takes back both, and a Zonewiki that knows only an
// older format refuses the wiki instead of misreading it (checkWikiFormat).
const wikiFormat = 1

// formatFileName: the file at the wiki's root that says which format its
// files are in. It is in git, beside the files it speaks for: a revert, a
// pull or a checkout moves it along with them, which .zone/ (out of git)
// wouldn't; a backup or a copy of the folder has it either way.
const formatFileName = ".zone-format"

func formatPath() string { return filepath.Join(cfg.Data, formatFileName) }

// formatRE: what the file says, a number (and a newline).
var formatRE = regexp.MustCompile(`^[0-9]{1,9}$`)

// readWikiFormat: the format the wiki's files are in; 0 for a wiki without
// .zone-format (a new one), which a start notes as this Zonewiki's
// (noteWikiFormat). An error for a file that says anything but a number.
func readWikiFormat() (int, error) { return readFormatFile(formatPath()) }

// readFormatFile: readWikiFormat of the file at path (an import reads the
// one of the wiki it is putting together: transfer.go).
func readFormatFile(path string) (int, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	s := strings.TrimSpace(string(b))
	if !formatRE.MatchString(s) {
		return 0, fmt.Errorf("it says %q, not a number", s)
	}
	n, err := strconv.Atoi(s)
	return n, err
}

// checkWikiFormat: the wiki's format, or why Zonewiki won't start on it: a
// file it can't read, or a format a newer Zonewiki wrote, whose files this one
// would misread and write over.
func checkWikiFormat() (int, error) {
	n, err := readWikiFormat()
	if err != nil {
		return 0, fmt.Errorf("%s can't be read (%v). It says which format the wiki's files are in: take it back "+
			"from the history (git checkout -- %s) or, if no newer Zonewiki ran on this wiki, remove it, and Zonewiki writes "+
			"it again", formatFileName, err, formatFileName)
	}
	if n > wikiFormat {
		return 0, fmt.Errorf("the wiki's files are in format %d (%s), which a newer Zonewiki wrote; this one (%s) knows "+
			"format %d and would misread them. Start the newer Zonewiki, or revert the commits that raised %s, newest "+
			"first (git log -- %s)", n, formatFileName, version, wikiFormat, formatFileName, formatFileName)
	}
	return n, nil
}

// writeWikiFormat writes format n into .zone-format and returns its path,
// for the commit of the step that took the wiki there.
func writeWikiFormat(n int) (string, error) {
	path := formatPath()
	return path, atomicfile.Write(path, strconv.Itoa(n)+"\n")
}

// noteWikiFormat: a wiki without .zone-format (from 0: a new one) says this
// Zonewiki's format from then on, in a commit of its own.
func noteWikiFormat(from int) {
	if from != 0 {
		return
	}
	path, err := writeWikiFormat(wikiFormat)
	if err != nil {
		logf("%s couldn't be written (the next start tries again): %v", formatFileName, err)
		return
	}
	_ = gitCommit("", "Note the format of the wiki's files", path)
}
