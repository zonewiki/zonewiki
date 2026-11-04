// Several people on one page (docs/spec/editor.md, Several editors): who
// wrote a page's uncommitted text, so it is committed in their name before
// someone else's write to the page; the texts the editors started from
// (bases), and the three-way merge of a save onto a page that changed.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/mdtext"
)

// drafts: a page's file, as git names it (wikiRel) → the user whose save
// last wrote it without a commit (the editor's auto-save, a ticked
// checkbox). In memory, as Zone is one process; after a restart an
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

// draftAuthor: who wrote path's uncommitted text ("": unknown, or Zone).
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
// (no commit per pause in typing). Without users there is one author. A
// commit that fails stops the write, as keepPending's does.
func commitOthersDraft(user, slug, path string) error {
	if settings().Users == nil {
		return nil
	}
	if author := draftAuthor(path); author != "" && author != user && isUncommitted(path) {
		if err := gitCommit(author, "Save "+slug, path); err != nil {
			return writeFailed(fmt.Sprintf("nothing was changed: %s's draft of %s couldn't be committed first", author, slug), err)
		}
	}
	return nil
}

// commitOthersDrafts: commitOthersDraft for every page, before a write
// that may change many (a rename re-pointing links, a folder move, an
// append): each other user's draft committed in their name first.
func commitOthersDrafts(user string) error {
	if settings().Users == nil {
		return nil
	}
	draftsMu.Lock()
	var rels []string
	for rel, author := range drafts {
		if author != user {
			rels = append(rels, rel)
		}
	}
	draftsMu.Unlock()
	slices.Sort(rels)
	for _, rel := range rels {
		if err := commitOthersDraft(user, strings.TrimSuffix(rel, ".md"), filepath.Join(cfg.Data, filepath.FromSlash(rel))); err != nil {
			return err
		}
	}
	// What Zone didn't see saved (written on disk, or before a restart) has no
	// known author, but in a private folder the folder says whose it is: one
	// commit per owner.
	byOwner := map[string][]string{}
	for _, f := range gitUncommittedFiles(privateDir) {
		if owner, private := ownerIn(privateDir, f.Filepath); private && owner != "" && owner != user {
			byOwner[owner] = append(byOwner[owner], f.Filepath)
		}
	}
	for _, owner := range sortedKeys(byOwner) {
		files := byOwner[owner]
		msg := commitMessage(files[0])
		if len(files) > 1 {
			msg = fmt.Sprintf("Save %d files in private/%s", len(files), owner)
		}
		paths := make([]string, len(files))
		for i, fp := range files {
			paths[i] = filepath.Join(cfg.Data, filepath.FromSlash(fp))
		}
		if err := gitCommit(owner, msg, paths...); err != nil {
			return writeFailed(fmt.Sprintf("nothing was changed: %s's changes in their private folder couldn't be committed first", owner), err)
		}
	}
	return nil
}

// commitAuthorOf: whom a commit of path's uncommitted text names: who
// wrote it if Zone knows, else fallback.
func commitAuthorOf(path, fallback string) string {
	if author := draftAuthor(path); author != "" && settings().Users != nil {
		return author
	}
	return fallback
}

// Bases: the texts behind recent file hashes (mdtext.ContentHash), so a save that
// sends an older hash can be merged from the text it started from. Two
// lists, so a busy page's auto-saves don't push out the text an idle
// editor started from: the texts handed out (/edit, read_page, a
// conflict's answer) and the texts saves wrote. Each in memory, the most
// recent baseCount, at most baseBytes; a hash in neither (an old tab, a
// restart) can't be merged.
type baseList struct {
	mu    sync.Mutex
	texts map[string]string
	order []string // oldest first
	size  int
}

var (
	givenBases   = &baseList{texts: map[string]string{}}
	writtenBases = &baseList{texts: map[string]string{}}
	baseCount    = 200
	baseBytes    = 20 << 20
)

// keep: text as a base for later merges; returns its hash.
func (l *baseList) keep(text string) string {
	hash := mdtext.ContentHash(text)
	l.mu.Lock()
	defer l.mu.Unlock()
	if _, ok := l.texts[hash]; ok {
		// The most recent again.
		l.order = append(slices.DeleteFunc(l.order, func(h string) bool { return h == hash }), hash)
		return hash
	}
	l.texts[hash] = text
	l.order = append(l.order, hash)
	l.size += len(text)
	for len(l.order) > baseCount || l.size > baseBytes && len(l.order) > 1 {
		old := l.order[0]
		l.order = l.order[1:]
		l.size -= len(l.texts[old])
		delete(l.texts, old)
	}
	return hash
}

func (l *baseList) get(hash string) (string, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	text, ok := l.texts[hash]
	return text, ok
}

// keepBase: a text a save wrote; keepGivenBase: one handed to an editor
// or an assistant. Both return its hash.
func keepBase(text string) string      { return writtenBases.keep(text) }
func keepGivenBase(text string) string { return givenBases.keep(text) }

// baseText: the text behind hash, if Zone still has it.
func baseText(hash string) (string, bool) {
	if text, ok := givenBases.get(hash); ok {
		return text, true
	}
	return writtenBases.get(hash)
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
	dir := statePath("tmp")
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
	// The exit code is the number of conflicts, whose markers the text then
	// has; anything else (255, a timeout, a git that didn't start) an error.
	if r.Code != 0 && (r.Code > 127 || !strings.Contains(r.Stdout, "<<<<<<< ")) {
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
	// The save's own text as a whole page (kept as a base) and, for a
	// section, its range there: an editor typed on meanwhile saves from it
	// next, and that save merges again.
	sentHash           string
	sentStart, sentEnd int
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
		if mine, _, m.sentStart, m.sentEnd, spliced = mdtext.SpliceSection(&base, s.fileHash, s.start, s.end, s.text); !spliced {
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
		if _, m.start, m.end, ok = findSection(m.content, 0, headingKey(firstLine(s.text))); !ok {
			return m, false
		}
	}
	if m.clean {
		m.sentHash = keepBase(mine)
	}
	return m, true
}

// otherAuthor: who other than user changed path last, as far as Zone
// knows: the draft's author, else the last commit's ("": none, or Zone).
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
	return strings.TrimRight(line, " \t\r")
}

// Leaving a page in conflict: the editor's save on pagehide can't show a
// banner, and writing over the other's change would lose it. So its text
// is kept for its user, in .zone/drafts/<user>/<slug>.json (not a page,
// not in git; "_" without users), with the text it started from, and the
// user's next /edit of the page offers the banner's choices. A save of the
// page by the user takes it away; after leftDraftDays it goes on its own.
type leftDraft struct {
	Content string    `json:"content"` // the whole page as the user left it
	Base    string    `json:"base"`    // the text the editor started from ("": not known)
	At      time.Time `json:"at"`
}

const leftDraftDays = 30

func leftDraftsDir() string { return statePath("drafts") }

// leftDraftPath: the file of user's draft of the page at path.
func leftDraftPath(user, path string) string {
	if user == "" {
		user = "_"
	}
	rel, _ := wikiRel(path)
	return filepath.Join(leftDraftsDir(), user, filepath.FromSlash(strings.TrimSuffix(rel, ".md")+".json"))
}

func leaveDraft(user, path, content, base string) {
	p := leftDraftPath(user, path)
	b, _ := json.Marshal(leftDraft{Content: content, Base: base, At: time.Now()})
	err := os.MkdirAll(filepath.Dir(p), 0o700)
	if err == nil {
		err = os.WriteFile(p, b, 0o600)
	}
	if err != nil {
		logf("could not keep the draft left in %s: %v", p, err)
	}
}

// draftLeft: user's draft of the page at path, if there is one (and not
// too old: then it goes).
func draftLeft(user, path string) (*leftDraft, bool) {
	p := leftDraftPath(user, path)
	b, err := os.ReadFile(p)
	if err != nil {
		return nil, false
	}
	var d leftDraft
	if json.Unmarshal(b, &d) != nil || time.Since(d.At) > leftDraftDays*24*time.Hour {
		os.Remove(p)
		return nil, false
	}
	return &d, true
}

// dropDraft: user saved the page at path; a draft they left is done.
func dropDraft(user, path string) { os.Remove(leftDraftPath(user, path)) }

// cleanLeftDrafts takes out the drafts older than leftDraftDays (at start).
func cleanLeftDrafts() {
	_ = filepath.WalkDir(leftDraftsDir(), func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			if info, err := d.Info(); err == nil && time.Since(info.ModTime()) > leftDraftDays*24*time.Hour {
				os.Remove(p)
			}
		}
		return nil
	})
}

// mergeWrite: write_page's content for a page that changed since the
// model read it (base_hash, from read_page), merged as the editor's saves
// are; an error the model sees when the lines clash (with them) or there
// is nothing to merge from.
func mergeWrite(slug, path, current, content, baseHash, user string) (string, error) {
	again := " Read it again with read_page and write from that, or change the lines you mean with edit_page."
	base, kept := baseText(baseHash)
	if !kept {
		return "", toolErr("Nothing written: " + slug + " changed since you read it (hash " + baseHash +
			"), and Zone no longer has that version to merge from." + again)
	}
	label := otherAuthor(path, user)
	if label == "" {
		label = "theirs"
	}
	merged, clean, err := mergeText(base, current, content, label)
	if err != nil {
		return "", toolErr("Nothing written: " + slug + " changed since you read it, and Zone can't merge (" +
			err.Error() + ")." + again)
	}
	if !clean {
		return "", toolErr("Nothing written: " + slug + " changed since you read it, in lines you changed too. " +
			"Where they clash (yours, then the page's now):\n\n" + conflictBlocks(merged) + "\n" + again)
	}
	return merged, nil
}

// conflictBlocks: the conflict markers' blocks of a merged text, nothing
// around them, separated by "…".
func conflictBlocks(text string) string {
	var blocks []string
	var block []string
	for _, line := range strings.SplitAfter(text, "\n") {
		switch {
		case strings.HasPrefix(line, "<<<<<<< "):
			block = []string{line}
		case block != nil:
			block = append(block, line)
			if strings.HasPrefix(line, ">>>>>>> ") {
				blocks = append(blocks, strings.Join(block, ""))
				block = nil
			}
		}
	}
	return strings.Join(blocks, "…\n")
}
