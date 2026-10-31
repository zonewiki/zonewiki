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
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
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

func refuse(msg string) error              { return &pageError{refusedInvalid, msg} }
func refuseAs(k refusal, msg string) error { return &pageError{k, msg} }

// refusalOf: err's refusal, if it is one.
func refusalOf(err error) (*pageError, bool) {
	var pe *pageError
	ok := errors.As(err, &pe)
	return pe, ok
}

// pageFailed answers a route whose operation failed: a refusal as its
// status, anything else as a 500.
func pageFailed(w http.ResponseWriter, r *http.Request, err error) {
	pe, ok := refusalOf(err)
	switch {
	case !ok:
		serverError(w, r, err)
	case pe.kind == refusedNotFound:
		http.NotFound(w, r)
	default:
		badRequestPage(w)
	}
}

// writablePage: the file of an existing page user may change; refused as
// missing otherwise, as a page user may not see is.
func writablePage(user, slug string) (string, error) {
	p := ""
	if canWrite(user, slug) {
		p = safePath(slug)
	}
	if p == "" || !exists(p) {
		return "", refuseAs(refusedNotFound, "No page "+strconv.Quote(slug))
	}
	return p, nil
}

// deletePageOp deletes the page, what it held uncommitted committed first
// (keepPending: Deleted Pages brings back its last text). kept: it did.
func deletePageOp(user, slug, suffix string) (kept bool, err error) {
	path, err := writablePage(user, slug)
	if err != nil {
		return false, err
	}
	if isProtected(slug) {
		return false, refuseAs(refusedProtected, slug+" is the home page; it can't be deleted")
	}
	kept = keepPending(path, slug)
	if err := os.Remove(path); err != nil {
		return kept, err
	}
	gitCommit(user, "Delete "+slug+suffix, path)
	return kept, nil
}

// restorePageOp writes the page's text as of version sha (a commit in its
// log; a deleted page comes back), what it held uncommitted committed
// first. old: its text before, nil when it was deleted.
func restorePageOp(user, slug, sha, suffix string) (old *string, kept bool, err error) {
	if !cfg.GitEnabled {
		return nil, false, refuseAs(refusedNotFound, "There is no history: git isn't installed where Zone runs.")
	}
	path := ""
	if canWrite(user, slug) {
		path = safePath(slug)
	}
	if path == "" {
		return nil, false, refuseAs(refusedNotFound, "No page "+strconv.Quote(slug))
	}
	short := runePrefix(sha, 7)
	content, ok := gitShow(sha, versionPath(path, pageLog(user, path), sha))
	if !ok {
		return nil, false, refuseAs(refusedNotFound, "Version "+short+" of "+slug+
			" has no text (the page was deleted then); pick the version before it.")
	}
	if old, err = currentText(path); err != nil {
		return nil, false, err
	}
	if old != nil && *old == content {
		return old, false, refuseAs(refusedUnchanged, slug+" already reads as version "+short+".")
	}
	kept = keepPending(path, slug)
	if err := writeText(path, content); err != nil {
		return old, kept, err
	}
	gitCommit(user, "Restore "+slug+" to "+short+suffix, path)
	return old, kept, nil
}

// checkRename: why user can't rename slug to newSlug, if not; else the
// files of both. The MCP tool asks it before it asks for the guides.
func checkRename(user, slug, newSlug string) (path, newPath string, err error) {
	if path, err = writablePage(user, slug); err != nil {
		return "", "", err
	}
	switch newPath = safePath(newSlug); {
	case isProtected(slug):
		err = refuseAs(refusedProtected, slug+" is the home page; it can't be renamed")
	case newSlug == slug:
		err = refuseAs(refusedUnchanged, "The page is already called "+slug)
	default:
		err = checkTarget(user, slug, newSlug)
	}
	return path, newPath, err
}

// checkTarget: why the page slug can't move to newSlug, a new name for
// it, if not: a folder Zone skips, another user's folder, a page there
// already, or a shared page into a private folder.
func checkTarget(user, slug, newSlug string) error {
	newPath := safePath(newSlug)
	switch {
	case newPath == "":
		return refuse("“" + newSlug + "”: pages can't go in folders starting with '_' or '.'")
	case !canWrite(user, newSlug):
		return refuse("“" + newSlug + "”: that folder is another user's")
	case exists(newPath):
		return refuseAs(refusedExists, "A page named "+strconv.Quote(newSlug)+" already exists")
	}
	if msg := refuseMakingPrivate(map[string]string{slug: newSlug}); msg != "" {
		return refuse(msg)
	}
	return nil
}

// renamePageOp renames the page, links to it following (movePage); what
// it held uncommitted is committed first, so the rename's commit only
// moves it. changed: the files whose links were re-pointed.
func renamePageOp(user, slug, newSlug, suffix string) (changed []string, kept bool, err error) {
	path, newPath, err := checkRename(user, slug, newSlug)
	if err != nil {
		return nil, false, err
	}
	kept = keepPending(path, slug)
	changed, err = movePage(user, path, newPath, slug, newSlug, "Rename "+slug+" to "+newSlug+suffix)
	return changed, kept, err
}

// checkFolderMove: why user can't move folder to newFolder, if not; else
// the files of the pages in it.
func checkFolderMove(user, folder, newFolder string) ([]string, error) {
	switch {
	case newFolder == folder:
		return nil, refuseAs(refusedUnchanged, "The folder is already called "+folder)
	case strings.HasPrefix(newFolder, folder+"/"):
		return nil, refuse(folder + "/ can't move into a folder inside itself")
	case safePath(newFolder+"/page") == "":
		return nil, refuse(strconv.Quote(newFolder) + ": pages can't go in folders starting with '_' or '.'")
	}
	var paths, taken []string
	moved := map[string]string{}
	for _, p := range sortedPages() {
		if !strings.HasPrefix(p.Slug, folder+"/") {
			continue
		}
		n := newFolder + p.Slug[len(folder):]
		if !canWrite(user, p.Slug) || !canWrite(user, n) {
			// private/ itself, say: it holds other users' folders.
			return nil, refuse("Nothing was moved: " + folder + "/ holds pages that aren't yours to move, " +
				"or they would land in a private folder not yours. Move the pages one by one.")
		}
		if np := safePath(n); np != "" && exists(np) {
			taken = append(taken, n)
		}
		paths = append(paths, p.Path)
		moved[p.Slug] = n
	}
	if len(paths) == 0 {
		return nil, refuseAs(refusedNotFound, "No folder "+strconv.Quote(folder))
	}
	if len(taken) > 0 {
		return nil, refuseAs(refusedExists, "These pages already exist, so nothing was moved: "+strings.Join(taken, ", "))
	}
	if msg := refuseMakingPrivate(moved); msg != "" {
		return nil, refuse("Nothing was moved: " + msg)
	}
	return paths, nil
}

// moveFolderOp moves every page in the folder, links following, in one
// commit (moveFolder); what they held uncommitted is committed first.
// Returns the (slug, new slug) moves, the files whose links changed, and
// how many pages had text kept.
func moveFolderOp(user, folder, newFolder, suffix string) (moves [][2]string, changed []string, kept int, err error) {
	paths, err := checkFolderMove(user, folder, newFolder)
	if err != nil {
		return nil, nil, 0, err
	}
	kept = keepPendingPages(paths, folder+"/")
	moves, changed, err = moveFolder(user, folder, newFolder, "Move "+folder+"/ to "+newFolder+"/"+suffix)
	return moves, changed, kept, err
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
