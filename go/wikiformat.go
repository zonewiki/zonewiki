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

// wikiFormat: the format of the wiki's files this Zone reads and writes. A
// change to what Zone keeps in git that an older Zone would misread raises
// it, with a step at start that takes a wiki of the format before over and
// writes the new number in that step's own commit (writeWikiFormat): so a
// revert of the commit takes back both, and a Zone that knows only an
// older format refuses the wiki instead of misreading it (checkWikiFormat).
const wikiFormat = 1

// firstFormat: the format the steps from before .zone-format leave a wiki
// in (migrateLists' steps, which look at the registries' versions instead).
const firstFormat = 1

// formatFileName: the file at the wiki's root that says which format its
// files are in. It is in git, beside the files it speaks for: a revert, a
// pull or a checkout moves it along with them, which .zone/ (out of git)
// wouldn't; a backup or a copy of the folder has it either way.
const formatFileName = ".zone-format"

func formatPath() string { return filepath.Join(cfg.Data, formatFileName) }

// formatRE: what the file says, a number (and a newline).
var formatRE = regexp.MustCompile(`^[0-9]{1,9}$`)

// readWikiFormat: the format the wiki's files are in; 0 for a wiki without
// .zone-format (from before it, or new), whose files the steps from before
// look through. An error for a file that says anything but a number.
func readWikiFormat() (int, error) {
	b, err := os.ReadFile(formatPath())
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

// checkWikiFormat: the wiki's format, or why Zone won't start on it: a
// file it can't read, or a format a newer Zone wrote, whose files this one
// would misread and write over.
func checkWikiFormat() (int, error) {
	n, err := readWikiFormat()
	if err != nil {
		return 0, fmt.Errorf("%s can't be read (%v). It says which format the wiki's files are in: take it back "+
			"from the history (git checkout -- %s) or, if no newer Zone ran on this wiki, remove it, and Zone looks "+
			"the files through again", formatFileName, err, formatFileName)
	}
	if n > wikiFormat {
		return 0, fmt.Errorf("the wiki's files are in format %d (%s), which a newer Zone wrote; this one (%s) knows "+
			"format %d and would misread them. Start the newer Zone, or revert the commits that raised %s, newest "+
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

// noteWikiFormat: after the steps from before .zone-format took a wiki
// without it (from 0) over, it says firstFormat from then on, in a commit
// of its own; later starts leave those steps out. Not while the lists
// couldn't be set up: the next start tries their steps again.
func noteWikiFormat(from int) {
	if from >= firstFormat || listsUnmigrated {
		return
	}
	// The views' migration's mark, which the file replaces.
	_ = os.Remove(statePath("views-named"))
	path, err := writeWikiFormat(firstFormat)
	if err != nil {
		logf("%s couldn't be written (the next start looks the wiki's files through again): %v", formatFileName, err)
		return
	}
	_ = gitCommit("", "Note the format of the wiki's files", path)
}
