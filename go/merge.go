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
