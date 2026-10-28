// Several people on one page (docs/spec/editor.md, Several editors): who
// wrote a page's uncommitted text, so it is committed in their name before
// someone else's write to the page.
package main

import (
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
