// Page operations: each write to pages as one function, which the
// browser's routes and the MCP tools share, so a page is checked, kept and
// committed the same way whichever door the write came through. An
// operation takes the user and does every check itself; a refusal is a
// *refusalError (refusal.go), which the routes turn into a status or a
// dialog's message, the tools into a tool error. suffix follows a commit's message (the MCP
// tools' summary and "(MCP)").
package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"zonewiki/internal/taskfiles"
	"zonewiki/internal/taskmodel"
)

// pagesMu: one write to pages at a time, from the reads it decides on (does
// the page exist, has it changed since the editor loaded it, what text does
// an edit start from) to its commit, as taskFiles' lock is for the task files: two
// writes to one page at once would otherwise lose one of them. The doors
// take it, not the operations, as a route's checks before its operation
// count too: the routes that write pages through writesPages, the MCP tools
// that do in callMCPTool (toolWritesPages in mcpToolTable). Go's mutex isn't reentrant: code
// under it never takes it again. Lock order: pagesMu, then taskFiles' lock (a
// rename's links in tasks), then commitMu.
var pagesMu sync.Mutex

// writesPages runs a route that writes pages under pagesMu. Its body is
// read first, so a slow client doesn't hold every other write up.
func writesPages(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Body != nil {
			body, err := io.ReadAll(r.Body)
			if tooLarge(err) {
				tooLargeError(w, bodyMax)
				return
			}
			if err != nil {
				badRequestPage(w)
				return
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
		}
		pagesMu.Lock()
		defer pagesMu.Unlock()
		h(w, r)
	}
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
	case pe.kind == refusedFull:
		http.Error(w, pe.msg, http.StatusInsufficientStorage)
	case pe.kind == refusedReadOnly:
		http.Error(w, pe.msg, http.StatusForbidden)
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
		return "", notWritable(user, slug, "No page "+strconv.Quote(slug))
	}
	return p, nil
}

// notWritable: the refusal of a change to slug user may not make: a
// viewer's to a shared page says so (refusedReadOnly), anything else
// answers as missing, with msg.
func notWritable(user, slug, msg string) error {
	if readOnly(user, slug) && safePath(slug) != "" {
		return refuseAs(refusedReadOnly, readOnlyWhy(user))
	}
	return refuseAs(refusedNotFound, msg)
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
	if kept, err = keepPending(path, slug); err != nil {
		return false, err
	}
	if err := os.Remove(path); err != nil {
		return kept, err
	}
	forgetUsage()
	return kept, notCommitted(gitCommit(user, "Delete "+slug+suffix, path))
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
		return nil, false, notWritable(user, slug, "No page "+strconv.Quote(slug))
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
	if kept, err = keepPending(path, slug); err != nil {
		return old, false, err
	}
	if err := writeText(path, content); err != nil {
		return old, kept, err
	}
	return old, kept, notCommitted(gitCommit(user, "Restore "+slug+" to "+short+suffix, path))
}

// createPageOp makes the page slug with content; refused when it
// exists, or can't be made there by user (another user's folder answers
// as missing).
func createPageOp(user, slug, content, suffix string) error {
	path := safePath(slug)
	switch {
	case path == "":
		return refuse("“" + slug + "”: pages can't go in folders starting with '_' or '.'")
	case !canWrite(user, slug):
		return notWritable(user, slug, "“"+slug+"”: that folder is another user's")
	case exists(path):
		return refuseAs(refusedExists, "A page named "+strconv.Quote(slug)+" already exists")
	}
	if err := checkRoom(int64(len(content))); err != nil {
		return err
	}
	if err := writeText(path, content); err != nil {
		return err
	}
	added(int64(len(content)))
	return notCommitted(gitCommit(user, "Create "+slug+suffix, path))
}

// writeDraft writes the editor's text to the page (current: what it holds,
// nil for none; the same text isn't written again): an auto-save or a
// ticked checkbox stays a draft, not committed; Done's save (commit)
// commits it as "Update <slug>", also when only the file was dirty.
// Either continues the writer's text, so only another user's draft there
// is committed first (commitOthersDraft), not the writer's own. A page's
// first save makes it, so it needs room (checkRoom); an edit never does.
func writeDraft(user, slug, path string, current *string, text string, commit bool) error {
	if !canWrite(user, slug) || safePath(slug) != path {
		return notWritable(user, slug, "No page "+strconv.Quote(slug))
	}
	if current == nil {
		if err := checkRoom(int64(len(text))); err != nil {
			return err
		}
	}
	if err := commitOthersDraft(user, slug, path); err != nil {
		return err
	}
	if current == nil || *current != text {
		if err := writeText(path, text); err != nil {
			return err
		}
		if current == nil {
			added(int64(len(text)))
		}
	}
	if commit {
		return notCommitted(gitCommit(user, "Update "+slug, path))
	} else {
		noteDraft(path, user)
	}
	return nil
}

// changePageOp writes the page's text as change makes it from the text it
// has, in one commit "<action> <slug><suffix>"; what it held uncommitted is
// committed first (keepPending), so the commit holds only this change. A
// refusal from change leaves everything as it was. Returns the text before
// and after.
func changePageOp(user, slug, action, suffix string, change func(old string) (string, error)) (old, text string, kept bool, err error) {
	path, err := writablePage(user, slug)
	if err != nil {
		return "", "", false, err
	}
	if old, err = readText(path); err != nil {
		return "", "", false, err
	}
	if text, err = change(old); err != nil {
		return old, "", false, err
	}
	if kept, err = keepPending(path, slug); err != nil {
		return old, "", false, err
	}
	if err := writeText(path, text); err != nil {
		return old, text, kept, err
	}
	return old, text, kept, notCommitted(gitCommit(user, action+" "+slug+suffix, path))
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
	case readOnly(user, newSlug):
		return refuseAs(refusedReadOnly, readOnlyWhy(user))
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
	if kept, err = keepPending(path, slug); err != nil {
		return nil, false, err
	}
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
	if kept, err = keepPendingPages(paths, folder+"/"); err != nil {
		return nil, nil, 0, err
	}
	moves, changed, err = moveFolder(user, folder, newFolder, "Move "+folder+"/ to "+newFolder+"/"+suffix)
	return moves, changed, kept, err
}

// tagPlan: what renaming a tag would change.
type tagPlan struct {
	old, name string // old lowercase, name as it will be written
	merges    bool   // the new name is in use (the two become one)
	owners    []string
	pages     []tagPage
}

// tagPage: a page whose text the rename changes.
type tagPage struct {
	PageFile
	text string
}

// visibleSlugs: the pages of the plan user may see.
func (p *tagPlan) visibleSlugs(user string) []string {
	var out []string
	for _, page := range p.pages {
		if canSee(user, page.Slug) {
			out = append(out, page.Slug)
		}
	}
	return out
}

// tagChange: a task's tags, title and description with the tag renamed;
// changed: any differs.
func tagChange(t *Task, old, name string) (changed bool) {
	tags, tagged := renamedTags(t.Tags, old, name)
	t.Tags, changed = tags, tagged
	for _, field := range []*string{&t.Title, &t.Description} {
		if n := renameTagInText(*field, old, name); n != *field {
			*field, changed = n, true
		}
	}
	return changed
}

// checkTagRename: why user can't rename the tag, if not; else what it changes.
// A tag on anything shared (a task, an embed on a page) is one name for all,
// so the rename reaches every user's private tasks and pages too, as a page
// rename's links do. A tag only user sees stays theirs: another user's
// private tag of that name is another tag. A tag user doesn't see at all is
// refused as one that doesn't exist, and what user is told counts only what
// they see, so it says nothing about another user's private files.
func checkTagRename(user, tag, newName string, merge bool) (*tagPlan, error) {
	olds, names := taskmodel.CleanTags([]string{tag}), taskmodel.CleanTags([]string{newName})
	if len(olds) == 0 {
		return nil, refuse("Which tag? The name is empty")
	}
	if len(names) == 0 {
		return nil, refuse("The new name is empty")
	}
	if strings.ContainsAny(names[0], "[],|=") {
		return nil, refuse(strconv.Quote(names[0]) + ": a tag's name can't contain [ ] , | or =")
	}
	old, name := strings.ToLower(olds[0]), names[0]
	notFound := refuseAs(refusedNotFound, "No tag #"+old)
	type fileTasks struct {
		owner string
		tasks []*Task
	}
	var files []fileTasks
	taskFiles.Lock()
	for _, f := range taskFiles.Files(taskFiles.EveryOwner()) {
		tasks, err := taskfiles.ReadStrict(f.Path)
		if err != nil {
			taskFiles.Unlock()
			return nil, err
		}
		files = append(files, fileTasks{f.Owner, tasks})
	}
	taskFiles.Unlock()
	seen, shared, taken := false, false, false
	for _, f := range files {
		visible := f.owner == "" || f.owner == user
		for _, t := range f.tasks {
			tags := taskTagsLower(t)
			seen = seen || visible && tags[old]
			shared = shared || f.owner == "" && tags[old]
			taken = taken || visible && tags[strings.ToLower(name)]
		}
	}
	pages := iterPages()
	texts := make([]string, len(pages))
	for i, p := range pages {
		text, err := readText(p.Path)
		if err != nil {
			logf("rename of tag #%s: %v", old, err)
			return nil, refuse("Nothing was renamed: a page can't be read (Zone's log says which)")
		}
		texts[i] = text
		private := !placeOf(p.Slug).shared()
		for _, params := range pageTasksEmbeds(text) {
			if embedUsesTag(params, old) {
				seen = seen || canSee(user, p.Slug)
				shared = shared || !private
			}
		}
	}
	if !seen {
		return nil, notFound
	}
	plan := &tagPlan{old: old, name: name, owners: []string{""}}
	if privateFolder(user) != "" {
		plan.owners = []string{user}
	}
	if shared {
		plan.owners = taskFiles.EveryOwner()
	}
	plan.merges = taken && strings.ToLower(name) != old
	if plan.merges && !merge {
		return nil, refuseAs(refusedExists, "#"+name+" is already in use. Renaming #"+old+" to it merges the two: their tasks then carry one tag.")
	}
	for i, p := range pages {
		if shared || ownPrivate(user, p.Slug) {
			if n := renameTagInText(texts[i], old, name); n != texts[i] {
				plan.pages = append(plan.pages, tagPage{p, n})
			}
		}
	}
	changes := len(plan.pages) > 0
	for _, f := range files {
		if slices.Contains(plan.owners, f.owner) {
			for _, t := range f.tasks {
				changes = tagChange(t, old, name) || changes
			}
		}
	}
	if !changes {
		return nil, refuseAs(refusedUnchanged, "The tag is already called #"+name)
	}
	return plan, nil
}

// tagRename: what a rename did, as far as its user may see: the tasks
// changed and the pages' slugs.
type tagRename struct {
	Name   string
	Merged bool
	Tasks  int
	Pages  []string
}

// renameTagOp renames a tag: on tasks (open, closed, archived), in
// [[tasks: …]] embeds (tag=, new=) and in links to /tasks (?tag=), in one
// commit. Not a list, which is no tag (renameListOp). merge: go on
// when the new name is in use. Every tasks file is read before anything is
// written, so one that can't be read changes nothing; text not yet in git is
// committed first. Not the tags in pages' frontmatter: those sort pages.
func renameTagOp(user, tag, newName string, merge bool, suffix string) (*tagRename, error) {
	if settings().isViewer(user) {
		return nil, refuseAs(refusedReadOnly, readOnlyMsg)
	}
	plan, err := checkTagRename(user, tag, newName, merge)
	if err != nil {
		return nil, err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	done := &tagRename{Name: plan.name, Merged: plan.merges, Pages: plan.visibleSlugs(user)}
	type taskWrite struct {
		path  string
		tasks []*Task
	}
	var writes []taskWrite
	for _, f := range taskFiles.Files(plan.owners) {
		tasks, err := taskfiles.ReadStrict(f.Path)
		if err != nil {
			return nil, err
		}
		n := 0
		for _, t := range tasks {
			if tagChange(t, plan.old, plan.name) {
				n++
			}
		}
		if n > 0 {
			writes = append(writes, taskWrite{f.Path, tasks})
			if f.Owner == "" || f.Owner == user {
				done.Tasks += n
			}
		}
	}
	paths := make([]string, len(plan.pages))
	for i, p := range plan.pages {
		paths[i] = p.Path
	}
	if _, err := keepPendingPages(paths, "pages before renaming #"+plan.old); err != nil {
		return nil, err
	}
	var written []string
	var failed error
	for _, p := range plan.pages {
		if failed = writeText(p.Path, p.text); failed != nil {
			break
		}
		written = append(written, p.Path)
	}
	for _, w := range writes {
		if failed != nil {
			break
		}
		taskFiles.Hold()
		if failed = taskfiles.Write(w.path, w.tasks); failed == nil {
			written = append(written, w.path)
		}
	}
	msg := "Rename tag #" + plan.old + " to #" + plan.name + suffix
	if plan.merges {
		msg = "Merge tag #" + plan.old + " into #" + plan.name + suffix
	}
	if len(written) > 0 {
		failed = errors.Join(failed, notCommitted(gitCommit(user, msg, written...)))
	}
	return done, failed
}

// keepPending: before a write that replaces or removes the page at path,
// what it holds uncommitted (an auto-save left, an edit on disk) is
// committed first by whoever wrote it ("Save <slug>"; Zone if not known),
// the writer's own draft too: so the write never loses text that was never
// in git, and its own commit holds only its change. True when it did.
// (A write that continues the text, an auto-save or a checkbox, commits
// only another user's draft: commitOthersDraft.) A commit that fails
// stops the write: the text would be lost.
func keepPending(path, slug string) (bool, error) {
	if path == "" || !exists(path) || !isUncommitted(path) {
		return false, nil
	}
	if err := gitCommit(commitAuthorOf(path, ""), "Save "+slug, path); err != nil {
		return false, writeFailed(fmt.Sprintf("nothing was changed: %s has text not in git yet, which couldn't be committed first", slug), err)
	}
	return true, nil
}

// writeError: a write that failed part way: its own commit failed, or one
// that had to come first (keepPending), or a rename's links couldn't all be
// re-pointed; msg says what that left. A 500 from a route, a tool error the
// assistant reads from an MCP tool (handleMCP).
type writeError struct {
	msg string
	err error
}

func (e *writeError) Error() string { return e.msg + ": " + e.err.Error() }
func (e *writeError) Unwrap() error { return e.err }

func writeFailed(msg string, err error) error { return &writeError{msg, err} }

// notCommitted: a write's own commit failed (gitCommit): the change is on
// disk, in the Uncommitted list, but not in the history.
func notCommitted(err error) error {
	if err == nil {
		return nil
	}
	return writeFailed("the change is written but not committed", err)
}

// keepPendingPages: keepPending for a write to many pages: those with
// uncommitted changes are committed first, one commit per author, as
// "Save <what>". Returns how many were; a commit that fails stops the write.
func keepPendingPages(paths []string, what string) (int, error) {
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
		if err := gitCommit(a, "Save "+what, byAuthor[a]...); err != nil {
			return 0, writeFailed("nothing was changed: pages with text not in git yet couldn't be committed first", err)
		}
	}
	return n, nil
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
