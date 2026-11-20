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
	return old, kept, notCommitted(gitCommit(user.Name, "Restore "+slug+" to "+short+suffix, path))
}

// createPageOp makes the page slug with content; refused when it
// exists, or can't be made there by user (another user's folder answers
// as missing).
func createPageOp(user asker, slug, content, suffix string) error {
	path := safePath(slug)
	switch {
	case path == "":
		return refuse("“" + slug + "”: pages can't go in folders starting with '_' or '.'")
	case !canWrite(user, slug):
		return notWritable(user, slug, "“"+slug+"”: "+notYoursFolder(slug))
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
	return notCommitted(gitCommit(user.Name, "Create "+slug+suffix, path))
}

// pageNewText: a page and the text it is to hold (replacePagesOp).
type pageNewText struct{ slug, path, text string }

// replacePagesOp writes each page's new text, all in one commit message;
// what the pages held uncommitted is committed first (keepPendingPages:
// what, as the note about it names the pages), so the commit holds only
// the replacement. Every page must be one user may write: one that isn't
// refuses the whole of it, with nothing written. Returns how many pages
// had text kept.
func replacePagesOp(user asker, pages []pageNewText, message, what string) (kept int, err error) {
	paths := make([]string, len(pages))
	for i, p := range pages {
		if _, err := writablePage(user, p.slug); err != nil {
			return 0, err
		}
		paths[i] = p.path
	}
	if kept, err = keepPendingPages(paths, what); err != nil {
		return 0, err
	}
	for _, p := range pages {
		if err := writeText(p.path, p.text); err != nil {
			return kept, err
		}
	}
	return kept, notCommitted(gitCommit(user.Name, message, paths...))
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
	_, _, _, err = changePageOp(user, on, "Link to "+target+" on", "", func(old string) (string, error) {
		// The link keeps its words: [[Garden]] reads Garden, not the folder's path.
		masked, stash := mdtext.MaskCode(old)
		masked = mdtext.WikilinkRE.ReplaceAllStringFunc(masked, func(m string) string {
			l := parseWikilink(m)
			if page := strings.TrimSpace(l.page); !l.hasLabel && !l.hasSection && page != slug && mdtext.ToSlug(page) == slug {
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
	return target, err
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
// it, if not: a folder Zone skips, another user's folder or a group's the
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
			return nil, refuse("Nothing was renamed: a page can't be read (Zone's log says which)")
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

// undoMost: how many changes one undo takes.
const undoMost = 50

// undone: what an undo did: the pages put back as they were (their text
// before the changes, a deleted page brought back) and those taken away
// (made by the changes), by slug; and the tasks put back as they were,
// taken away and brought back, each by its title in quotes (taskUndo.title).
type undone struct {
	back, gone                          []string
	tasksBack, tasksGone, tasksReturned []string
}

func (u *undone) pages() []string { return append(slices.Clone(u.back), u.gone...) }

// undoChangesOp undoes changes of the wiki, commits by their versions:
// every page they changed is as it was before them, and every task, in one
// commit ("Undo <version>: <what it said>", "Undo N changes: …"), which is
// a change like any other and can be undone too. A change is undone whole
// or not at all, and only where that loses nothing and is the user's to do:
//   - it changed only pages and tasks' files (taskfiles.Store.FileOwner;
//     not a list's registry, a task's files, an upload), each one user
//     sees (a change user doesn't see all of is no change to them: refused
//     as missing), each page one they may write;
//   - it moved no page (a rename is undone by renaming back, which asks
//     who may: checkReaders);
//   - no page it changed was changed since by a commit that isn't among
//     those undone, or holds text not committed yet: the undo would take
//     that along;
//   - each task it changed is as the changes left it, and is the user's to
//     put back (taskUndoLocked, mayUndoTask).
//
// check, when given, is asked with the pages before anything is written
// (the MCP tool's guides).
func undoChangesOp(user asker, versions []string, suffix string, check func(slugs []string) error) (*undone, error) {
	if !cfg.GitEnabled {
		return nil, refuseAs(refusedNotFound, "There is no history: git isn't installed where Zone runs.")
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
			if !isPage || safePath(slug) == "" {
				what := "files"
				if isTaskFile(c.Path) {
					what = "a list or a task's files"
				}
				return nil, refuse("Change " + short + " changed " + what + " too, which Undo doesn't put back: only changes of pages and tasks are undone.")
			}
			if !canWrite(user, slug) {
				return nil, notWritable(user, slug, "No change "+strconv.Quote(v)+"; history lists them.")
			}
			if how[c.Path] == nil {
				how[c.Path] = map[string]byte{}
				files = append(files, c.Path)
			}
			how[c.Path][sha] = c.Kind
			kinds += string(c.Kind)
		}
		if strings.Contains(kinds, "A") && strings.Contains(kinds, "D") {
			return nil, refuse("Change " + short + " moved a page: rename it back instead.")
		}
		commits = append(commits, commit{sha, subject})
	}
	// Each page: nothing but these changes since the oldest of them, and
	// what it was before that one.
	type step struct {
		slug, path string
		text       *string // nil: the page is taken away
	}
	var steps []step
	done := &undone{}
	for _, rel := range files {
		slug, path := strings.TrimSuffix(rel, ".md"), filepath.Join(cfg.Data, rel)
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
			return nil, refuseAs(refusedConflict, slug+" has changes not committed yet: commit them first (or undo them in its editor).")
		}
		oldest := last[len(last)-1]
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
