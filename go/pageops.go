// Page operations: each write to pages as one function, which the
// browser's routes and the MCP tools share, so a page is checked, kept and
// committed the same way whichever door the write came through. An
// operation takes the user and does every check itself; a refusal is a
// *pageError, which the routes turn into a status or a dialog's message,
// the tools into a tool error. suffix follows a commit's message (the MCP
// tools' summary and "(MCP)").
package main

import (
	"errors"
	"fmt"
	"path/filepath"
)

// refusal: why an operation refused, for the route's answer.
type refusal int

const (
	refusedInvalid   refusal = iota // can't be done as asked: 400, or the dialog's message
	refusedNotFound                 // no such page, or not one the user may see: 404
	refusedExists                   // a page by the new name is there already
	refusedProtected                // the home page: never deleted or renamed
	refusedUnchanged                // the page already is what the write would make it
)

// pageError: an operation refused, with a message for the user.
type pageError struct {
	kind refusal
	msg  string
}

func (e *pageError) Error() string { return e.msg }

func refuse(msg string) error          { return &pageError{refusedInvalid, msg} }
func refuseAs(k refusal, msg string) error { return &pageError{k, msg} }

// refusalOf: err's refusal, if it is one.
func refusalOf(err error) (*pageError, bool) {
	var pe *pageError
	ok := errors.As(err, &pe)
	return pe, ok
}

// keepPending: before a write that replaces or removes the page at path,
// what it holds uncommitted (an auto-save left, an edit on disk) is
// committed first by whoever wrote it ("Save <slug>"; Zone if not known),
// the writer's own draft too: so the write never loses text that was never
// in git, and its own commit holds only its change. True when it did.
// (A write that continues the text, an auto-save or a checkbox, commits
// only another user's draft: commitOthersDraft.)
func keepPending(path, slug string) bool {
	if path == "" || !exists(path) || !isUncommitted(path) {
		return false
	}
	gitCommit(commitAuthorOf(path, ""), "Save "+slug, path)
	return true
}

// keepPendingPages: keepPending for a write to many pages: those with
// uncommitted changes are committed first, one commit per author, as
// "Save <what>". Returns how many were.
func keepPendingPages(paths []string, what string) int {
	pending := map[string]bool{}
	for _, f := range gitUncommittedFiles() {
		pending[filepath.Join(cfg.Data, f.Filepath)] = true
	}
	// One commit per author, in the order they first come.
	var authors []string
	byAuthor := map[string][]string{}
	n := 0
	for _, p := range paths {
		if pending[p] {
			a := commitAuthorOf(p, "")
			if byAuthor[a] == nil {
				authors = append(authors, a)
			}
			byAuthor[a] = append(byAuthor[a], p)
			n++
		}
	}
	for _, a := range authors {
		gitCommit(a, "Save "+what, byAuthor[a]...)
	}
	return n
}

// pendingNote: what a tool's answer says when keepPending committed text
// the user may still be editing.
func pendingNote(slug string, kept bool) string {
	if !kept {
		return ""
	}
	return "\nNote: " + slug + " had changes that weren't committed yet (the user may be editing it " +
		"in the browser); they were committed first, as \"Save " + slug + "\", and kept."
}

// pendingPagesNote: pendingNote for keepPendingPages.
func pendingPagesNote(n int, what string) string {
	if n == 0 {
		return ""
	}
	return fmt.Sprintf("\nNote: %d of the pages had changes that weren't committed yet (the user may be "+
		"editing them in the browser); they were committed first, as \"Save %s\", and kept.", n, what)
}
