// Several people on one page (docs/spec/editor.md, Several editors): who
// wrote a page's uncommitted text, so it is committed in their name before
// someone else's write to the page; the texts the editors started from
// (bases), and the three-way merge of a save onto a page that changed.
package main

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
)

// drafts: a page's file, as git names it (wikiRel) → the user whose save
// last wrote it without a commit (the editor's auto-save, a ticked
// checkbox). In memory, as Leaf is one process; after a restart an
// uncommitted file's author is unknown.
var (
	draftsMu sync.Mutex
	drafts   = map[string]string{}
)

// noteDraft: user's save wrote path without committing it.
func noteDraft(path, user string) {
	if !cfg.GitEnabled {
		return
	}
	if rel, ok := wikiRel(path); ok {
		draftsMu.Lock()
		drafts[rel] = user
		draftsMu.Unlock()
	}
}

// draftAuthor: who wrote path's uncommitted text ("": unknown, or Leaf).
func draftAuthor(path string) string {
	rel, _ := wikiRel(path)
	draftsMu.Lock()
	defer draftsMu.Unlock()
	return drafts[rel]
}

// forgetDrafts: the files were committed (gitCommit; as git names them).
func forgetDrafts(rels ...string) {
	draftsMu.Lock()
	for _, rel := range rels {
		delete(drafts, rel)
	}
	draftsMu.Unlock()
}

// commitOthersDraft: before user writes, moves or deletes the page at path,
// the draft someone else saved there is committed in their name ("Save
// <slug>"), so it is in git under its author whatever user's write does to
// it, and not in user's commit. One person's own drafts stay uncommitted
// (no commit per pause in typing). Without users there is one author.
func commitOthersDraft(user, slug, path string) {
	if settings().Users == nil {
		return
	}
	if author := draftAuthor(path); author != "" && author != user && isUncommitted(path) {
		gitCommit(author, "Save "+slug, path)
	}
}

// commitAuthorOf: whom a commit of path's uncommitted text names: who
// wrote it if Leaf knows, else fallback.
func commitAuthorOf(path, fallback string) string {
	if author := draftAuthor(path); author != "" && settings().Users != nil {
		return author
	}
	return fallback
}

// bases: the texts behind recent file hashes (contentHash), the ones the
// editor was given (/edit) and every one a save wrote, so a save that
// sends an older hash can be merged from the text it started from. In
// memory, the most recent baseCount, at most baseBytes in all; a hash not
// here (an old tab, a restart) can't be merged.
var (
	basesMu   sync.Mutex
	bases     = map[string]string{}
	baseOrder []string // oldest first
	baseSize  int
	baseCount = 200
	baseBytes = 20 << 20
)

// keepBase: text as a base for later merges; returns its hash.
func keepBase(text string) string {
	hash := contentHash(text)
	basesMu.Lock()
	defer basesMu.Unlock()
	if _, ok := bases[hash]; ok {
		// The most recent again.
		baseOrder = append(slices.DeleteFunc(baseOrder, func(h string) bool { return h == hash }), hash)
		return hash
	}
	bases[hash] = text
	baseOrder = append(baseOrder, hash)
	baseSize += len(text)
	for len(baseOrder) > baseCount || baseSize > baseBytes && len(baseOrder) > 1 {
		old := baseOrder[0]
		baseOrder = baseOrder[1:]
		baseSize -= len(bases[old])
		delete(bases, old)
	}
	return hash
}

// baseText: the text behind hash, if Leaf still has it.
func baseText(hash string) (string, bool) {
	basesMu.Lock()
	defer basesMu.Unlock()
	text, ok := bases[hash]
	return text, ok
}

var errNoMerge = errors.New("no merge without git")

// mergeText merges two texts that both changed base, line by line: mine
// (the save) and theirs (the file as it is now), with git merge-file, as
// git is there anyway. clean: no line changed on both sides; otherwise
// merged has git's conflict markers, mine labelled yours, theirs
// theirName. Without git: errNoMerge.
func mergeText(base, theirs, mine, theirName string) (merged string, clean bool, err error) {
	if !cfg.GitEnabled {
		return "", false, errNoMerge
	}
	dir := filepath.Join(cfg.Data, ".leaf", "tmp")
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return "", false, err
	}
	var files []string
	defer func() {
		for _, f := range files {
			os.Remove(f)
		}
	}()
	for _, text := range []string{mine, base, theirs} {
		f, err := os.CreateTemp(dir, "merge-*")
		if err != nil {
			return "", false, err
		}
		files = append(files, f.Name())
		_, err = f.WriteString(text)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return "", false, err
		}
	}
	r := runGit("merge-file", "-p", "-L", "yours", "-L", "base", "-L", theirName, "--", files[0], files[1], files[2])
	// The exit code is the number of conflicts; negative (255) an error.
	if r.Code < 0 || r.Code > 127 || r.Code == 124 && r.Stdout == "" {
		return "", false, errors.New("git merge-file: " + strings.TrimSpace(r.Stderr))
	}
	return r.Stdout, r.Code == 0, nil
}

// mergeOutcome: an editor's save merged with the file as it is now.
type mergeOutcome struct {
	content    string // the merged file; not clean: with conflict markers
	clean      bool   // no line changed on both sides
	start, end int    // a section edit: the section's range in content
	by         string // whose change it met ("": not known, or no users)
}

// mergeSave merges the editor's save s, made from the text at its hash,
// with the file as it is now: a whole page as it is, a section spliced
// into the text it came from first and merged as a whole page, its new
// range then the section whose heading line is the one the editor's text
// starts with. ok false when there is no merge to make: the file gone, the
// base no longer kept (an old tab, a restart), no git, or (clean) the
// section's heading not there exactly once.
func mergeSave(s *editorSave, user string) (m mergeOutcome, ok bool) {
	if s.current == nil || s.fileHash == "" {
		return m, false
	}
	base, kept := baseText(s.fileHash)
	if !kept {
		return m, false
	}
	mine := s.text
	if s.section {
		var spliced bool
		if mine, _, _, _, spliced = spliceSection(&base, s.fileHash, s.start, s.end, s.text); !spliced {
			return m, false
		}
	}
	m.by = otherAuthor(s.path, user)
	label := m.by
	if label == "" {
		label = "theirs"
	}
	var err error
	if m.content, m.clean, err = mergeText(base, *s.current, mine, label); err != nil {
		return m, false
	}
	if s.section && m.clean {
		if m.start, m.end, ok = headedSection(m.content, firstLine(s.text)); !ok {
			return m, false
		}
	}
	return m, true
}

// otherAuthor: who other than user changed path last, as far as Leaf
// knows: the draft's author, else the last commit's ("": none, or Leaf).
func otherAuthor(path, user string) string {
	if settings().Users == nil {
		return ""
	}
	if a := draftAuthor(path); a != "" {
		if a == user {
			return ""
		}
		return a
	}
	if log := gitLog(path, 1); len(log) > 0 && log[0].Author != user {
		return log[0].Author
	}
	return ""
}

func firstLine(text string) string {
	line, _, _ := strings.Cut(text, "\n")
	return strings.TrimRight(line, " \t")
}

// headedSection: the range (characters) of the one section of content
// whose heading line is heading; ok false if none or several.
func headedSection(content, heading string) (start, end int, ok bool) {
	found := 0
	for i := 1; ; i++ {
		s, e, valid := sectionCharRange(content, i)
		if !valid {
			break
		}
		if firstLine(runeSlice(content, s, e)) == heading {
			start, end = s, e
			found++
		}
	}
	return start, end, found == 1 && heading != ""
}
