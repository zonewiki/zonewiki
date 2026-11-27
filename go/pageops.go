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
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"zonewiki/internal/mdimport"
	"zonewiki/internal/mdtext"
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
	case pe.kind == refusedFull, pe.kind == refusedReadOnly, pe.kind == refusedConflict, pe.kind == refusedTooLarge:
		http.Error(w, pe.msg, pe.kind.status()) // the status that says its kind (refusal.status)
	default:
		badRequestPage(w)
	}
}

// writablePage: the file of an existing page user may change; refused as
// missing otherwise, as a page user may not see is.
func writablePage(user asker, slug string) (string, error) {
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
func notWritable(user asker, slug, msg string) error {
	if readOnly(user, slug) && safePath(slug) != "" {
		return refuseAs(refusedReadOnly, readOnlyWhy(user, slug))
	}
	return refuseAs(refusedNotFound, msg)
}

// deletePageOp deletes the page, what it held uncommitted committed first
// (keepPending: Deleted Pages brings back its last text). kept: it did.
func deletePageOp(user asker, slug, suffix string) (kept bool, err error) {
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
	return kept, notCommitted(gitCommit(user.Name, "Delete "+slug+suffix, path))
}

// restorePageOp writes the page's text as of version sha (a commit in its
// log; a deleted page comes back), what it held uncommitted committed
// first. old: its text before, nil when it was deleted.
func restorePageOp(user asker, slug, sha, suffix string) (old *string, kept bool, err error) {
	if !cfg.GitEnabled {
		return nil, false, refuseAs(refusedNotFound, "There is no history: git isn't installed where Zonewiki runs.")
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
	return old, kept, notCommitted(gitCommit(user.Name, "Restore "+slug+" to "+short+suffix, path))
}

// createPageOp makes the page slug with content; refused when it
// exists, or can't be made there by user (another user's folder answers
// as missing): writePagesOp with one new page.
func createPageOp(user asker, slug, content, suffix string) error {
	_, err := writePagesOp(user, pagesWrite{
		pages:   []pageWrite{{slug: slug, text: content, isNew: true}},
		message: "Create " + slug + suffix,
	})
	return err
}

// pageNewText: a page and the text it is to hold (replacePagesOp).
type pageNewText struct{ slug, text string }

// replacePagesOp writes each page's new text, all in one commit message:
// writePagesOp with pages that are all there already. what names the
// pages in the note about text kept first (keepPendingPages). Returns how
// many pages had text kept.
func replacePagesOp(user asker, pages []pageNewText, message, what string) (kept int, err error) {
	w := pagesWrite{message: message, what: what}
	for _, p := range pages {
		w.pages = append(w.pages, pageWrite{slug: p.slug, text: p.text})
	}
	return writePagesOp(user, w)
}

// pageWrite: a page writePagesOp writes, the text it is to hold, and
// whether it is to be new (not there yet) or there already.
type pageWrite struct {
	slug, text string
	isNew      bool
}

// pagesWrite: what writePagesOp writes, in one commit.
type pagesWrite struct {
	pages   []pageWrite
	message string   // the commit's message, its suffix included
	what    string   // names the pages in "Save <what>", the commit of text kept first
	also    []string // other files to commit along, written by the caller (an import's uploads)
}

// writePagesOp writes pages, each new or there already, in one commit:
// the rules of making and of replacing a page, said once. A new page must
// have a path (safePath), be user's to write (canWrite) and not be there
// yet; one there already must be user's to write (writablePage). One page
// that isn't as it was said to be, or is named twice, refuses the whole of
// it, with nothing written; so does a wiki without room for the new pages'
// text (checkRoom; replacing a page's text never asks). What the pages
// there already held uncommitted is committed first (keepPendingPages:
// what, as the note about it names the pages), so the commit holds only
// this write; also's files go into it too. Returns how many pages had text
// kept.
func writePagesOp(user asker, w pagesWrite) (kept int, err error) {
	paths := make([]string, len(w.pages))
	var there []string
	var room int64
	seen := map[string]bool{}
	for i, p := range w.pages {
		if p.isNew {
			path := safePath(p.slug)
			switch {
			case path == "":
				return 0, refuse("“" + p.slug + "”: pages can't go in folders starting with '_' or '.'")
			case !canWrite(user, p.slug):
				return 0, notWritable(user, p.slug, "“"+p.slug+"”: "+notYoursFolder(p.slug))
			case exists(path):
				return 0, refuseAs(refusedExists, "A page named "+strconv.Quote(p.slug)+" already exists")
			}
			paths[i] = path
			room += int64(len(p.text))
		} else {
			path, err := writablePage(user, p.slug)
			if err != nil {
				return 0, err
			}
			paths[i] = path
			there = append(there, path)
		}
		if seen[paths[i]] {
			return 0, refuse("“" + p.slug + "” is named twice")
		}
		seen[paths[i]] = true
	}
	if err := checkRoom(room); err != nil {
		return 0, err
	}
	if len(there) > 0 {
		if kept, err = keepPendingPages(there, w.what); err != nil {
			return 0, err
		}
	}
	for i, p := range w.pages {
		if err := writeText(paths[i], p.text); err != nil {
			return kept, err
		}
		if p.isNew {
			added(int64(len(p.text)))
		}
	}
	return kept, notCommitted(gitCommit(user.Name, w.message, append(paths, w.also...)...))
}

// setAsideOp: the editor's text mine, which lost to theirs (what the page
// holds: a conflict's Reload theirs), is committed as user's and the page
// put back as it is, so what was typed is in the page's history and not
// lost: two commits, "Set aside an edit of <slug>" and "Reload <slug>".
// What the page held that was never in git is committed first. A commit
// that fails stops it there: putting theirs back would lose the text.
func setAsideOp(user asker, slug, mine, theirs string) error {
	path, err := writablePage(user, slug)
	if err != nil {
		return err
	}
	if _, err := keepPending(path, slug); err != nil {
		return err
	}
	if err := writeText(path, mine); err != nil {
		return err
	}
	if err := gitCommit(user.Name, "Set aside an edit of "+slug, path); err != nil {
		return err
	}
	if err := writeText(path, theirs); err != nil {
		return err
	}
	if err := gitCommit(user.Name, "Reload "+slug, path); err != nil {
		return err
	}
	dropDraft(user.Name, path)
	return nil
}

// keepMineOp: the editor's Keep mine after a conflict: whatever the page
// holds is replaced, but never what was never in git, which is committed
// first (keepPending); then the text is the user's draft, as any save
// (writeDraft).
func keepMineOp(user asker, slug, path string, current *string, text string) error {
	if _, err := keepPending(path, slug); err != nil {
		return err
	}
	return writeDraft(user, slug, path, current, text, false)
}

// linkNewOp: the page slug, which isn't there yet and would be a shared one,
// is to be made in the folder of the page on instead (on: one of user's
// private pages, or a page of a group they are in, with a link to slug: the
// link that led to the new page's editor). Every link to slug on that page
// points at the page in the folder from now (one commit); the page itself
// is made when its editor first saves. Returns that page's slug.
func linkNewOp(user asker, on, slug string) (target string, err error) {
	on, slug = mdtext.ToSlug(on), mdtext.ToSlug(slug)
	p := placeOf(on)
	if on == "" || slug == "" || !canSee(user, on) || p.shared() {
		return "", refuseAs(refusedNotFound, "No page "+strconv.Quote(on))
	}
	if target, _ = sharedNewOffer(user, slug, newFrom(p)); target == "" {
		return "", refuse("There is nothing to make there: the page is there already, or it isn't a shared page's name")
	}
	if !canWrite(user, target) {
		return "", notWritable(user, target, "No page "+strconv.Quote(target))
	}
	return target, pointLinksOp(user, on, slug, target)
}

// relinkOp: the missing page slug, opened from a link on the page on, was
// meant to be target, a page of the same name user sees elsewhere
// (sameNamePages: the editor's Did you mean). Every link to slug on that
// page points at target from now (one commit).
func relinkOp(rq *Req, on, slug, target string) error {
	on, slug, target = mdtext.ToSlug(on), mdtext.ToSlug(slug), mdtext.ToSlug(target)
	if on == "" || slug == "" || !canSee(rq.user, on) {
		return refuseAs(refusedNotFound, "No page "+strconv.Quote(on))
	}
	if p := safePath(slug); p == "" || exists(p) || !slices.Contains(sameNamePages(rq, slug, maxSameName), target) {
		return refuse("“" + target + "” isn't a page of the name “" + slug + "” was looking for")
	}
	return pointLinksOp(rq.user, on, slug, target)
}

// maxSameName: how many pages of a missing page's name the editor offers.
const maxSameName = 5

// pointLinksOp: every link to slug on the page on points at target from now,
// in one commit "Link to <target> on <on>". A link keeps its words:
// [[Garden]] reads Garden, not the new path. Refused when on has no link
// to slug.
func pointLinksOp(user asker, on, slug, target string) error {
	_, _, _, err := changePageOp(user, on, "Link to "+target+" on", "", func(old string) (string, error) {
		masked, stash := mdtext.MaskCode(old)
		masked = mdtext.WikilinkRE.ReplaceAllStringFunc(masked, func(m string) string {
			l := parseWikilink(m)
			if page := strings.TrimSpace(l.page); !l.hasLabel && !l.hasSection && mdtext.ToSlug(page) == slug {
				l.label, l.hasLabel = page, true
				return l.String()
			}
			return m
		})
		text := rewritePageLinks(mdtext.UnmaskCode(masked, stash), slug, target, on, "")
		if text == old {
			return "", refuse("“" + on + "” has no link to “" + slug + "”")
		}
		return text, nil
	})
	return err
}

// writeDraft writes the editor's text to the page (current: what it holds,
// nil for none; the same text isn't written again): an auto-save or a
// ticked checkbox stays a draft, not committed; Done's save (commit)
// commits it as "Update <slug>", also when only the file was dirty.
// Either continues the writer's text, so only another user's draft there
// is committed first (commitOthersDraft), not the writer's own. A page's
// first save makes it, so it needs room (checkRoom); an edit never does.
func writeDraft(user asker, slug, path string, current *string, text string, commit bool) error {
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
		return notCommitted(gitCommit(user.Name, "Update "+slug, path))
	} else {
		noteDraft(path, user.Name)
	}
	return nil
}

// changePageOp writes the page's text as change makes it from the text it
// has, in one commit "<action> <slug><suffix>"; what it held uncommitted is
// committed first (keepPending), so the commit holds only this change. A
// refusal from change leaves everything as it was. Returns the text before
// and after.
func changePageOp(user asker, slug, action, suffix string, change func(old string) (string, error)) (old, text string, kept bool, err error) {
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
	return old, text, kept, notCommitted(gitCommit(user.Name, action+" "+slug+suffix, path))
}

// checkRename: why user can't rename slug to newSlug, if not; else the
// files of both. The MCP tool asks it before it asks for the guides.
func checkRename(user asker, slug, newSlug string, sure moveSure) (path, newPath string, err error) {
	if path, err = writablePage(user, slug); err != nil {
		return "", "", err
	}
	switch newPath = safePath(newSlug); {
	case isProtected(slug):
		err = refuseAs(refusedProtected, slug+" is the home page; it can't be renamed")
	case newSlug == slug:
		err = refuseAs(refusedUnchanged, "The page is already called "+slug)
	default:
		err = checkTarget(user, slug, newSlug, sure)
	}
	return path, newPath, err
}

// checkTarget: why the page slug can't move to newSlug, a new name for
// it, if not: a folder Zonewiki skips, another user's folder or a group's the
// user isn't in, a page there already, or a move to other readers that
// isn't the user's to make, or that the door hasn't asked about yet
// (checkReaders: an askError then).
func checkTarget(user asker, slug, newSlug string, sure moveSure) error {
	newPath := safePath(newSlug)
	switch {
	case newPath == "":
		return refuse("“" + newSlug + "”: pages can't go in folders starting with '_' or '.'")
	case readOnly(user, newSlug):
		return refuseAs(refusedReadOnly, readOnlyWhy(user, newSlug))
	case !canWrite(user, newSlug):
		return refuse("“" + newSlug + "”: " + notYoursFolder(newSlug))
	case exists(newPath):
		return refuseAs(refusedExists, "A page named "+strconv.Quote(newSlug)+" already exists")
	}
	return checkReaders(user, map[string]string{slug: newSlug}, sure)
}

// renamePageOp renames the page, links to it following (movePage); what
// it held uncommitted is committed first, so the rename's commit only
// moves it. changed: the files whose links were re-pointed, of those user
// sees (the links on everyone's pages follow, but what user is told of
// counts nothing they may not see).
func renamePageOp(user asker, slug, newSlug, suffix string, sure moveSure) (changed []string, kept bool, err error) {
	path, newPath, err := checkRename(user, slug, newSlug, sure)
	if err != nil {
		return nil, false, err
	}
	if kept, err = keepPending(path, slug); err != nil {
		return nil, false, err
	}
	changed, err = movePage(user, path, newPath, slug, newSlug, "Rename "+slug+" to "+newSlug+suffix)
	return seenFiles(user, changed), kept, err
}

// checkFolderMove: why user can't move folder to newFolder, if not; else
// the files of the pages in it.
func checkFolderMove(user asker, folder, newFolder string, sure moveSure) ([]string, error) {
	switch {
	case newFolder == folder:
		return nil, refuseAs(refusedUnchanged, "The folder is already called "+folder)
	case strings.HasPrefix(newFolder, folder+"/"):
		return nil, refuse(folder + "/ can't move into a folder inside itself")
	case safePath(newFolder+"/page") == "":
		return nil, refuse(strconv.Quote(newFolder) + ": pages can't go in folders starting with '_' or '.'")
	case safePath(folder+"/page") == "":
		return nil, refuseAs(refusedNotFound, "No folder "+strconv.Quote(folder)) // a name with a line break, as safePath
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
				"or they would land in a private folder not yours, or in the folder of a group you aren't in. Move the pages one by one.")
		}
		np := safePath(n)
		if np == "" {
			return nil, refuse("Nothing was moved: " + n + " can't be a page (a folder on the way is a link, or a name the files can't have)")
		}
		if exists(np) {
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
	if err := checkReaders(user, moved, sure); err != nil {
		if pe, ok := refusalOf(err); ok {
			return nil, refuse("Nothing was moved: " + pe.msg)
		}
		return nil, err
	}
	return paths, nil
}

// moveFolderOp moves every page in the folder, links following, in one
// commit (moveFolder); what they held uncommitted is committed first.
// Returns the (slug, new slug) moves, the files whose links changed of
// those user sees (as renamePageOp), and how many pages had text kept.
func moveFolderOp(user asker, folder, newFolder, suffix string, sure moveSure) (moves [][2]string, changed []string, kept int, err error) {
	paths, err := checkFolderMove(user, folder, newFolder, sure)
	if err != nil {
		return nil, nil, 0, err
	}
	if kept, err = keepPendingPages(paths, folder+"/"); err != nil {
		return nil, nil, 0, err
	}
	moves, changed, err = moveFolder(user, folder, newFolder, "Move "+folder+"/ to "+newFolder+"/"+suffix)
	return moves, seenFiles(user, changed), kept, err
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
func (p *tagPlan) visibleSlugs(user asker) []string {
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
func checkTagRename(user asker, tag, newName string, merge bool) (*tagPlan, error) {
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
		for _, t := range f.tasks {
			// By the file's owner (a task read from its file doesn't say whose it is).
			tags, visible := taskTagsLower(t), ownerPlace(f.owner).sees(user)
			seen = seen || visible && tags[old]
			shared = shared || !privateOwner(f.owner) && tags[old]
			taken = taken || visible && tags[strings.ToLower(name)]
		}
	}
	pages := iterPages()
	texts := make([]string, len(pages))
	for i, p := range pages {
		text, err := readText(p.Path)
		if err != nil {
			logf("rename of tag #%s: %v", old, err)
			return nil, refuse("Nothing was renamed: a page can't be read (Zonewiki's log says which)")
		}
		texts[i] = text
		// A group's page shows shared tasks, as a shared page does: only a
		// user's own page makes a tag theirs alone.
		private := placeOf(p.Slug).kind == ofUser || placeOf(p.Slug).kind == ofNoOne
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
		plan.owners = nil // a tag of their own alone: their own tasks, if those are there for them
		if user.HasPrivate() {
			plan.owners = []string{user.Name}
		}
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
func renameTagOp(user asker, tag, newName string, merge bool, suffix string) (*tagRename, error) {
	if err := mayWriteTasks(user); err != nil { // a tag's rename writes tasks: whoever may write them
		return nil, err
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
			if seesFile(user, f.Path) { // not another user's, nor a list's for some user is none of
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
		failed = errors.Join(failed, notCommitted(gitCommit(user.Name, msg, written...)))
	}
	return done, failed
}

// importInto: where an import into a folder goes (checkImportFolder): the
// folder, the home page of its place, which gets the line that links the
// folder's index, and the place whose uploads take the files.
type importInto struct {
	folder, home string
	up           place
}

// checkImportFolder: folder is one user may import notes into (the
// markdown import, importOp): a folder of pages they may write (the shared
// wiki, their private folder, a group's they are in; a viewer or a
// read-only connection nowhere), inside its place but not the place's own
// folder (private/alice itself holds her home), whose uploads they may add
// to (uploadPlace), and not there yet, so nothing an import writes clashes
// with what is there. One that isn't theirs answers as missing, as
// notWritable does.
func checkImportFolder(user asker, folder string) (importInto, error) {
	for _, part := range strings.Split(folder, "/") {
		if part == "" || part == "." || part == ".." {
			return importInto{}, refuse(strconv.Quote(folder) + " can't be a folder's name: name one like my-notes or projects/my-notes")
		}
	}
	probe := folder + "/index"
	file := safePath(probe)
	if file == "" {
		if msg := nameRefused(folder); msg != "" {
			return importInto{}, refuse(msg)
		}
		return importInto{}, refuse("“" + folder + "”: pages can't go in folders starting with '_' or '.'")
	}
	if changesNothing(user) {
		return importInto{}, refuseAs(refusedReadOnly, noChangeMsg(user))
	}
	if !canWrite(user, probe) {
		return importInto{}, notWritable(user, probe, "“"+folder+"”: "+notYoursFolder(folder))
	}
	up, ok := uploadPlace(user, probe)
	if !ok {
		return importInto{}, refuseAs(refusedNotFound, "“"+folder+"”: "+notYoursFolder(folder))
	}
	to := importInto{folder: folder, home: "index", up: up}
	switch {
	case up.user() != "":
		to.home = privateHomeSlug(user)
	case up.group() != "":
		to.home = groupHomeSlug(up.group())
	}
	if strings.EqualFold(folder, up.folder()) {
		return importInto{}, refuse("“" + folder + "” holds the place's other pages: import into a new folder inside it")
	}
	if _, err := os.Lstat(filepath.Dir(file)); !errors.Is(err, os.ErrNotExist) && !emptyFolder(filepath.Dir(file)) {
		return importInto{}, refuseAs(refusedExists, "A folder named “"+folder+"” is there already: import into a new one, so nothing clashes")
	}
	return to, nil
}

// emptyFolder: dir is a folder (not a link) that holds no file, only
// empty folders if any: what an undone import or deleted pages leave,
// where nothing can clash.
func emptyFolder(dir string) bool {
	if st, err := os.Lstat(dir); err != nil || !st.IsDir() {
		return false
	}
	empty := true
	_ = filepath.WalkDir(dir, func(_ string, d fs.DirEntry, err error) error {
		if err != nil || !d.IsDir() {
			empty = false
			return fs.SkipAll
		}
		return nil
	})
	return empty
}

// importOptions: what the converter needs to plan an import into folder
// for user (mdimport.Options), the folder checked first (checkImportFolder):
// a page's part as safePath takes it (pagePart), a note's name not a
// folder's guide's (AGENTS.md, CLAUDE.md: guidePage), a file's name one an
// upload can have (uploadName) that is free in the place's uploads and not
// given to another file of this import (freeImportName), and its link the
// place's (uploadURL).
func importOptions(user asker, folder string) (mdimport.Options, error) {
	to, err := checkImportFolder(user, folder)
	if err != nil {
		return mdimport.Options{}, err
	}
	given := map[string]bool{}
	return mdimport.Options{
		Folder:   folder,
		PagePart: pagePart,
		// A note named AGENTS.md or CLAUDE.md would become a folder's
		// guide: an admin's, made on purpose, never by an import.
		NoteRefused: func(part string) string {
			if guideFiles[part+".md"] {
				return "a page of that name would be a guide for assistants"
			}
			return ""
		},
		FileName: func(want string) string {
			name := freeImportName(to.up, uploadName(want), given)
			if name != "" {
				given[strings.ToLower(name)] = true
			}
			return name
		},
		FileURL: func(name string) string { return uploadURL(to.up, name) },
	}, nil
}

// importNameTries: how many names freeImportName tries for a file.
const importNameTries = 1000

// freeImportName: name, or with a number before its extension (deck-2.pdf,
// deck-3.pdf …), as freeUploadName gives it: free in the place's uploads
// (uploadPath, no file there) and not given (by its lower case: two names
// that differ only in case are one file where file names ignore it). ""
// for name "" or none found.
func freeImportName(up place, name string, given map[string]bool) string {
	if name == "" {
		return ""
	}
	ext := suffix(name)
	stem := strings.TrimSuffix(name, ext)
	for n := 1; n <= importNameTries; n++ {
		try := name
		if n > 1 {
			try = stem + "-" + strconv.Itoa(n) + ext
		}
		if given[strings.ToLower(try)] {
			continue
		}
		if p, ok := uploadPath(up, try); ok {
			if _, err := os.Lstat(p); errors.Is(err, os.ErrNotExist) {
				return try
			}
		}
	}
	return ""
}

// importOp writes an import's plan (the converter's, of the folder src)
// into folder, which isn't there yet (checkImportFolder): the files into
// the place's uploads, then the pages and the folder's index as new pages,
// and a line on the place's home page that links the index (every new page
// needs a link, and the index links the others), in one commit, "Import
// 214 pages and 87 files from my-vault" (from: what was imported; the
// folder's name if ""). Undo takes it back whole: the pages and files
// away, the line off the home. The home's text not yet in git is
// committed first (writePagesOp); a home not there yet is made first, in
// its own commit, as a visit makes it, so the import's undo never deletes
// a home (it stays, should writePagesOp then refuse the pages).
//
// It is refused whole, with nothing written, for a page outside folder, a
// file's name that is no upload's, named twice or taken now (the plan's
// names are in its pages' links, so one uploaded meanwhile refuses the
// import rather than being overwritten or the file renamed), a source that
// is no file, and a wiki without room for the files and the pages together
// (checkRoom). Files written for pages writePagesOp then refuses are
// removed again. Returns how many pages had text kept (the home).
func importOp(user asker, folder string, plan *mdimport.Plan, src fs.FS, from, suffix string) (kept int, err error) {
	to, err := checkImportFolder(user, folder)
	if err != nil {
		return 0, err
	}
	if plan == nil || plan.Index.Slug == "" {
		return 0, refuse("There is nothing to import")
	}
	var pages []pageWrite
	var room int64
	for _, p := range append(slices.Clone(plan.Pages), plan.Index) {
		if !strings.HasPrefix(p.Slug, folder+"/") {
			return 0, refuse("Nothing was imported: “" + p.Slug + "” isn't in " + folder + "/")
		}
		pages = append(pages, pageWrite{slug: p.Slug, text: p.Text, isNew: true})
		room += int64(len(p.Text))
	}
	// The uploads' lock (after pagesMu and the task files' lock, before
	// commitMu) from the names' check to the commit: no upload takes one
	// meanwhile.
	uploadMu.Lock()
	defer uploadMu.Unlock()
	type upload struct{ src, dest string }
	var files []upload
	names := map[string]bool{}
	for _, f := range plan.Files {
		dest, ok := uploadPath(to.up, f.Name)
		if !ok || names[strings.ToLower(f.Name)] {
			return 0, refuse("Nothing was imported: " + strconv.Quote(f.Name) + " can't be an upload's name there")
		}
		names[strings.ToLower(f.Name)] = true
		if _, err := os.Lstat(dest); !errors.Is(err, os.ErrNotExist) {
			return 0, refuseAs(refusedExists, "Nothing was imported: a file named "+strconv.Quote(f.Name)+
				" was uploaded there meanwhile. Import again.")
		}
		if !fs.ValidPath(f.Src) {
			return 0, refuse("Nothing was imported: " + strconv.Quote(f.Src) + " is no file of what was imported")
		}
		st, err := fs.Stat(src, f.Src)
		if err != nil || !st.Mode().IsRegular() {
			return 0, refuse("Nothing was imported: " + strconv.Quote(f.Src) + " is no file of what was imported")
		}
		files = append(files, upload{f.Src, dest})
		room += st.Size()
	}
	if err := checkRoom(room); err != nil {
		return 0, err
	}
	home, err := importHome(user, to)
	if err != nil {
		return 0, err
	}
	dir := uploadDir(to.up)
	madeDir := !exists(dir)
	var written []string
	removeFiles := func() {
		for _, p := range written {
			_ = os.Remove(p)
		}
		if madeDir {
			_ = os.Remove(dir) // only when empty: nothing else was put there
		}
		forgetUsage()
	}
	for _, f := range files {
		n, err := copyNewFile(src, f.src, f.dest)
		if err != nil {
			removeFiles()
			return 0, err
		}
		written = append(written, f.dest)
		added(n)
	}
	pages = append(pages, pageWrite{slug: to.home, text: withLine(home, importLine(folder, plan.Index.Slug, from))})
	if from == "" {
		from = baseName(folder)
	}
	message := "Import " + counted(len(plan.Pages), "page", "pages")
	if len(files) > 0 {
		message += " and " + counted(len(files), "file", "files")
	}
	kept, err = writePagesOp(user, pagesWrite{pages: pages, message: message + " from " + from + suffix, what: to.home, also: written})
	if err != nil && !writtenAnyway(err) {
		removeFiles()
	}
	return kept, err
}

// importHome: the text of the import's home page, made first when it
// isn't there, as a visit makes it (ensurePrivateHome, groupRoute,
// ensureHomePage), in its own commit "Create <slug>".
func importHome(user asker, to importInto) (string, error) {
	file := safePath(to.home)
	if file == "" || !canWrite(user, to.home) {
		return "", notWritable(user, to.home, "No page "+strconv.Quote(to.home))
	}
	if !exists(file) {
		text := "# Home\n\n"
		switch {
		case to.up.user() != "":
			text = privateHomeText
		case to.up.group() != "":
			text = groupHomeText(to.up.group())
		}
		if err := writeText(file, text); err != nil {
			return "", err
		}
		added(int64(len(text)))
		if err := notCommitted(gitCommit(user.Name, "Create "+to.home, file)); err != nil {
			return "", err
		}
	}
	text, err := currentText(file)
	if err != nil {
		return "", err
	}
	if text == nil {
		return "", errors.New(to.home + " is gone")
	}
	return *text, nil
}

// importLine: the line on the home page that links an import's index
// ("- [[my-vault/index|My Vault]]"): its words what was imported (from),
// without what would end the link, or else the folder's name.
func importLine(folder, index, from string) string {
	label := strings.TrimSpace(strings.Map(func(r rune) rune {
		if strings.ContainsRune("[]|", r) || r < ' ' || r == 0x7f {
			return -1
		}
		return r
	}, from))
	if label == "" {
		label = baseName(folder)
	}
	return "- [[" + index + "|" + label + "]]"
}

// withLine: text with line added at its end: a list's next item after a
// list, after a blank line after anything else.
func withLine(text, line string) string {
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	last := strings.TrimSpace(lines[len(lines)-1])
	if last != "" && !strings.HasPrefix(last, "- ") && !strings.HasSuffix(text, "\n\n") {
		text += "\n"
	}
	return text + line + "\n"
}

// copyNewFile copies the file name of src to dest, which must not be
// there (an upload is never overwritten); returns how many bytes. A copy
// that fails leaves no file.
func copyNewFile(src fs.FS, name, dest string) (int64, error) {
	in, err := src.Open(name)
	if err != nil {
		return 0, err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dest), 0o777); err != nil {
		return 0, err
	}
	out, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o666)
	if err != nil {
		return 0, err
	}
	n, err := io.Copy(out, in)
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		_ = os.Remove(dest)
		return 0, err
	}
	return n, nil
}

// undoMost: how many changes one undo takes.
const undoMost = 50

// undone: what an undo did: the pages put back as they were (their text
// before the changes, a deleted page brought back) and those taken away
// (made by the changes), by slug; the uploads likewise, by their paths in
// the wiki; and the tasks put back as they were, taken away and brought
// back, each by its title in quotes (taskUndo.title).
type undone struct {
	back, gone                          []string
	filesBack, filesGone                []string
	tasksBack, tasksGone, tasksReturned []string
}

func (u *undone) pages() []string { return append(slices.Clone(u.back), u.gone...) }

// undoChangesOp undoes changes of the wiki, commits by their versions:
// every page they changed is as it was before them, and every task, in one
// commit ("Undo <version>: <what it said>", "Undo N changes: …"), which is
// a change like any other and can be undone too. Its uploads too: one it
// added is taken away, one it replaced or deleted written back, byte for
// byte (gitBlob). A change is undone whole or not at all, and only where
// that loses nothing and is the user's to do:
//   - it changed only pages, uploads (uploadOf) and tasks' files
//     (taskfiles.Store.FileOwner; not a list's registry, a task's files),
//     each one user sees (a change user doesn't see all of is no change to
//     them: refused as missing), each page one they may write, each upload
//     one they may upload there (mayUndoUpload);
//   - it moved no page (a rename is undone by renaming back, which asks
//     who may: checkReaders);
//   - no page or upload it changed was changed since by a commit that
//     isn't among those undone, or holds what isn't committed yet: the undo
//     would take that along;
//   - what the uploads written back add fits the wiki (checkRoom);
//   - each task it changed is as the changes left it, and is the user's to
//     put back (taskUndoLocked, mayUndoTask).
//
// check, when given, is asked with the pages before anything is written
// (the MCP tool's guides).
func undoChangesOp(user asker, versions []string, suffix string, check func(slugs []string) error) (*undone, error) {
	if !cfg.GitEnabled {
		return nil, refuseAs(refusedNotFound, "There is no history: git isn't installed where Zonewiki runs.")
	}
	if len(versions) == 0 || len(versions) > undoMost {
		return nil, refuse(fmt.Sprintf("Name one change to undo, or up to %d", undoMost))
	}
	// The changes, and for each page which of them changed it and how; for
	// each change its task files.
	type commit struct{ sha, subject string }
	var commits []commit
	how := map[string]map[string]byte{} // file → commit → A, M or D
	var files []string
	uploads := map[string]place{}            // of files, the uploads, with their places
	taskChanges := map[string][]fileChange{} // commit → the task files it changed
	for _, v := range versions {
		none := refuseAs(refusedNotFound, "No change "+strconv.Quote(v)+"; history lists them.")
		sha, subject, ok := commitOf(strings.ToLower(strings.TrimSpace(v)))
		if !ok {
			return nil, none
		}
		if slices.ContainsFunc(commits, func(c commit) bool { return c.sha == sha }) {
			continue
		}
		changes := commitChanges(sha)
		for _, c := range changes {
			if !canSeePath(user, c.Path) {
				return nil, none
			}
		}
		short := runePrefix(sha, 7)
		if len(changes) == 0 {
			return nil, refuse("Change " + short + " changed no page: there is nothing to undo.")
		}
		kinds := ""
		for _, c := range changes {
			if _, isTasks := taskFiles.FileOwner(c.Path); isTasks {
				taskChanges[sha] = append(taskChanges[sha], c)
				continue
			}
			slug, isPage := strings.CutSuffix(c.Path, ".md")
			if p, _, isUpload := uploadOf(c.Path); isUpload {
				if changesNothing(user) && canSeePath(user, c.Path) { // one they don't see: no change of theirs, below
					return nil, refuseAs(refusedReadOnly, noChangeMsg(user))
				}
				if !mayUndoUpload(user, c.Path) {
					return nil, none
				}
				uploads[c.Path] = p
			} else if !isPage || safePath(slug) == "" {
				what := "files"
				if isTaskFile(c.Path) {
					what = "a list or a task's files"
				}
				return nil, refuse("Change " + short + " changed " + what + " too, which Undo doesn't put back: only changes of pages, uploads and tasks are undone.")
			} else if !canWrite(user, slug) {
				return nil, notWritable(user, slug, "No change "+strconv.Quote(v)+"; history lists them.")
			}
			if how[c.Path] == nil {
				how[c.Path] = map[string]byte{}
				files = append(files, c.Path)
			}
			how[c.Path][sha] = c.Kind
			if _, isUpload := uploads[c.Path]; !isUpload {
				kinds += string(c.Kind) // a page moved: an upload's A and D are no move
			}
		}
		if strings.Contains(kinds, "A") && strings.Contains(kinds, "D") {
			return nil, refuse("Change " + short + " moved a page: rename it back instead.")
		}
		commits = append(commits, commit{sha, subject})
	}
	// Each page and upload: nothing but these changes since the oldest of
	// them, and what it was before that one.
	type step struct {
		slug, path string
		text       *string // nil: the page or upload is taken away
		upload     string  // an upload: its path in the wiki, and its
		newest     string  // latest commit, one of these changes
	}
	var steps []step
	var grows int64 // what the uploads written back add to the wiki
	done := &undone{}
	for _, rel := range files {
		slug, path := strings.TrimSuffix(rel, ".md"), filepath.Join(cfg.Data, rel)
		p, isUpload := uploads[rel]
		if isUpload {
			slug = rel // what the refusals name
			var ok bool
			if path, ok = uploadPath(p, baseName(rel)); !ok {
				return nil, refuse(rel + " can't be put back: it is no file of its folder's uploads now.")
			}
		}
		ours := how[rel]
		last := lastCommitsOf(rel, len(ours))
		for _, sha := range last {
			if _, is := ours[sha]; !is {
				return nil, refuseAs(refusedConflict, slug+" was changed again since: undoing would take that along. Its history has every version.")
			}
		}
		if len(last) != len(ours) {
			return nil, refuseAs(refusedConflict, slug+": its history doesn't hold these changes anymore.")
		}
		if isUncommitted(path) {
			if isUpload {
				return nil, refuseAs(refusedConflict, rel+" was changed on disk and that isn't committed yet: undoing would take that along.")
			}
			return nil, refuseAs(refusedConflict, slug+" has changes not committed yet: commit them first (or undo them in its editor).")
		}
		oldest := last[len(last)-1]
		if isUpload {
			// The bytes, as they are and as they were: no text is read.
			current, err := os.ReadFile(path)
			there := err == nil
			if err != nil && !errors.Is(err, os.ErrNotExist) {
				return nil, err
			}
			s := step{slug: rel, path: path, upload: rel, newest: last[0]}
			if ours[oldest] == 'A' { // added by these changes: gone again
				if !there {
					continue
				}
				steps = append(steps, s)
				done.filesGone = append(done.filesGone, rel)
				continue
			}
			before, ok := gitBlob(oldest+"^", rel)
			if !ok {
				return nil, refuseAs(refusedConflict, rel+": the file before the change can't be read.")
			}
			if there && bytes.Equal(current, before) {
				continue
			}
			text := string(before)
			s.text = &text
			steps = append(steps, s)
			grows += int64(len(before) - len(current))
			done.filesBack = append(done.filesBack, rel)
			continue
		}
		current, err := currentText(path)
		if err != nil {
			return nil, err
		}
		if ours[oldest] == 'A' { // made by these changes: gone again
			if current == nil {
				continue
			}
			if isProtected(slug) {
				return nil, refuseAs(refusedProtected, slug+" is the home page; it can't be deleted")
			}
			steps = append(steps, step{slug: slug, path: path})
			done.gone = append(done.gone, slug)
			continue
		}
		before, ok := gitShow(oldest+"^", path)
		if !ok {
			return nil, refuseAs(refusedConflict, slug+": its text before the change can't be read.")
		}
		if current != nil && *current == before {
			continue
		}
		steps = append(steps, step{slug: slug, path: path, text: &before})
		done.back = append(done.back, slug)
	}
	// The tasks: each as before the first of these changes that changed it,
	// read from the history now (taskUndoPlan); whether it still is as they
	// left it is asked under the task files' lock (taskUndoLocked).
	var plan []*taskUndo
	if len(taskChanges) > 0 {
		var shas []string
		for _, c := range commits {
			if taskChanges[c.sha] != nil {
				shas = append(shas, c.sha)
			}
		}
		var err error
		if plan, err = taskUndoPlan(shas, taskChanges); err != nil {
			return nil, err
		}
	}
	if len(steps) == 0 && len(plan) == 0 {
		if len(taskChanges) > 0 {
			return nil, refuseAs(refusedUnchanged, "The pages and tasks already are as they were before.")
		}
		return nil, refuseAs(refusedUnchanged, "The pages already are as they were before.")
	}
	if check != nil {
		if err := check(done.pages()); err != nil {
			return nil, err
		}
	}
	if err := checkRoom(grows); err != nil {
		return nil, err
	}
	message := "Undo " + runePrefix(commits[0].sha, 7) + ": " + commits[0].subject
	if len(commits) > 1 {
		shas := make([]string, len(commits))
		for i, c := range commits {
			shas[i] = runePrefix(c.sha, 7)
		}
		message = fmt.Sprintf("Undo %d changes: %s", len(commits), strings.Join(shas, " "))
	}
	var tw *taskUndoWrite
	if len(plan) > 0 {
		// The task files' lock before the first page is written (pagesMu,
		// then taskFiles.Lock, then commitMu): the tasks are checked
		// exactly, and the pages go into the tasks' commit.
		taskFiles.Lock()
		defer taskFiles.Unlock()
		var err error
		if tw, err = taskUndoLocked(user, plan); err != nil {
			return nil, err
		}
		if len(steps) == 0 && len(tw.back)+len(tw.gone)+len(tw.returned) == 0 {
			return nil, refuseAs(refusedUnchanged, "The pages and tasks already are as they were before.")
		}
		done.tasksBack, done.tasksGone, done.tasksReturned = tw.back, tw.gone, tw.returned
	}
	if len(done.filesBack)+len(done.filesGone) > 0 {
		// An upload isn't written under pagesMu but under uploadMu (after
		// the task files' lock, before commitMu, as storeUpload takes it
		// before its commit): each still as these changes left it, so one
		// uploaded meanwhile isn't overwritten.
		uploadMu.Lock()
		defer uploadMu.Unlock()
		for _, s := range steps {
			if s.upload == "" {
				continue
			}
			if last := lastCommitsOf(s.upload, 1); len(last) != 1 || last[0] != s.newest || isUncommitted(s.path) {
				return nil, refuseAs(refusedConflict, s.upload+" was changed again since: undoing would take that along. Its history has every version.")
			}
		}
	}
	paths := make([]string, len(steps))
	for i, s := range steps {
		paths[i] = s.path
		if s.text == nil {
			if err := os.Remove(s.path); err != nil {
				return nil, err
			}
			continue
		}
		if err := writeText(s.path, *s.text); err != nil {
			return nil, err
		}
	}
	if len(steps) > 0 {
		forgetUsage()
	}
	if tw != nil {
		return done, taskFiles.WriteBothLocked(user, tw.active, tw.closed, message+suffix, paths...)
	}
	return done, notCommitted(gitCommit(user.Name, message+suffix, paths...))
}

// keepPending: before a write that replaces or removes the page at path,
// what it holds uncommitted (an auto-save left, an edit on disk) is
// committed first by whoever wrote it ("Save <slug>"; Zonewiki if not known),
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
// assistant reads from an MCP tool (handleMCP). written: the change itself
// is made, on disk, and what failed is only its commit (notCommitted).
type writeError struct {
	msg     string
	err     error
	written bool
}

func (e *writeError) Error() string { return e.msg + ": " + e.err.Error() }
func (e *writeError) Unwrap() error { return e.err }

func writeFailed(msg string, err error) error { return &writeError{msg: msg, err: err} }

// notCommitted: a write's own commit failed (gitCommit): the change is on
// disk, in the Uncommitted list, but not in the history.
func notCommitted(err error) error {
	if err == nil {
		return nil
	}
	return &writeError{msg: "the change is written but not committed", err: err, written: true}
}

// writtenAnyway: err says a change that is made, with something left to
// do about it (its commit: notCommitted), not one that wasn't made. What a
// write of several steps asks before it undoes the steps before: a step
// that is made is not undone for its commit.
func writtenAnyway(err error) bool {
	var we *writeError
	return errors.As(err, &we) && we.written
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
