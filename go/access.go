// Who may see and change what (docs/spec/users.md): with users, everything
// is shared but private/<user>/, which only that user sees; anything else
// under private/ (a page in private/ itself, a folder of no user) is seen
// by no one. Without users everyone sees everything, and private/ is an
// ordinary folder. Every route and tool that shows or changes a page asks
// here, so the rule lives in one place.
package main

import (
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"
)

// privateDir is the folder of the users' private folders.
const privateDir = "private"

// A public wiki (ZONE_PUBLIC in the deployment's settings, with users;
// docs/spec/security.md, Public wikis): whoever isn't signed in is a
// visitor, who reads the shared pages (publicView) or edits them too
// (publicEdit). A visitor reaches only the routes in visitorRoutes
// (server.go), sees no task and no private page, and makes no notes, tasks
// or uploads. The checks give such a request the user visitor, which is no
// user's name (a name has no '_'), so every rule here that asks who a user
// is answers for a visitor as for a stranger.
const (
	publicView = "view"
	publicEdit = "edit"
	visitor    = "_visitor"
	// visitorAuthor: who a visitor's commits are by.
	visitorAuthor = "anonymous"
)

// publicMode: how the wiki is public now: publicView, publicEdit, or "" (it
// isn't: ZONE_PUBLIC unset, or no users to tell visitors from).
func publicMode() string {
	if cfg.Public == "" || settings().Users == nil {
		return ""
	}
	return cfg.Public
}

// visitorMsg: what a visitor is told about what only users do.
const visitorMsg = "Sign in to do this: without an account you can only read this wiki."

// keptDirs: the folders whose first level names who sees what is kept in
// them (private/<user>/), as a pattern: what spells such a path in a text
// (an upload's link, a commit message) is made from it.
const keptDirs = privateDir

// place: whose a page or a file is, which its path alone says (where
// something is kept is who sees it): everyone's, one user's, or no one's.
// Every question about who sees what is asked of a place, so a new kind of
// folder (a group's) changes the answers here and nowhere else.
type place struct {
	kind placeKind
	name string // the user whose it is (ofUser), else ""
}

type placeKind int

const (
	ofEveryone placeKind = iota // shared: outside private/, or a wiki without users
	ofUser                      // in private/<user>/
	ofNoOne                     // in private/ but in no user's folder: "private/carol/x", "private/alice" itself
)

// placeOf: the place of the page slug, or of the file rel (slash-separated,
// relative to the wiki): private/<user>/… is that user's, anything else in
// private/ no one's, the rest everyone's. Without users nothing is
// private. It is judged as safePath opens it, cleaned ("private/bob/../
// alice/x" is alice's), and in lower case.
func placeOf(rel string) place {
	users := settings().Users
	if users == nil {
		return place{}
	}
	rest, found := strings.CutPrefix(strings.ToLower(path.Clean(rel)), privateDir+"/")
	if !found {
		return place{}
	}
	name, _, inside := strings.Cut(rest, "/")
	if !inside || users[name] == nil {
		return place{kind: ofNoOne}
	}
	return place{ofUser, name}
}

// shared: everyone's.
func (p place) shared() bool { return p.kind == ofEveryone }

// user: the one user whose place it is, "" for any other place.
func (p place) user() string {
	if p.kind == ofUser {
		return p.name
	}
	return ""
}

// own: it is user's own, and theirs alone.
func (p place) own(user string) bool { return p.kind == ofUser && p.name == user }

// sees: user sees what is kept there.
func (p place) sees(user string) bool { return p.shared() || p.own(user) }

// within: everyone who sees what is kept at p sees what is kept at q too:
// q is the same place or a wider one. What a link's mark asks (does every
// reader of this page see its target), and a move (does it take the page
// from anyone).
func (p place) within(q place) bool {
	return q.shared() || p.kind == ofUser && q == p
}

// canSee: user may see the page slug: a shared page, or one of their own.
func canSee(user, slug string) bool { return placeOf(slug).sees(user) }

// canWrite: user may change (create, edit, rename, delete) the page slug:
// the pages they may see, since everything shared is editable, but for a
// viewer (users.go: roleViewer), who changes nothing.
func canWrite(user, slug string) bool {
	return canSee(user, slug) && !settings().isViewer(user) && !adminsOnly(user, slug)
}

// adminsOnly: slug is a shared guide (guidePage), which with users only an
// admin changes, and user isn't one. A guide is what every user's assistant
// is told to follow (mcptools.go: the wiki's guide in its instructions, a
// folder's before a write there), so whoever writes it steers them all: an
// editor, or an assistant that read a prompt injection, could have every
// assistant copy private pages into the open. A guide in a user's own
// private folder is theirs: only their assistant reads it. Without users
// there are no admins, and the wiki's one user writes its guides.
func adminsOnly(user, slug string) bool {
	s := settings()
	if s.Users == nil || s.isAdmin(user) {
		return false
	}
	if !placeOf(slug).shared() {
		return false
	}
	return guidePage(slug)
}

// guidePage: slug is a guide: the wiki's (wiki-guide), a folder's
// (AGENTS.md, CLAUDE.md), or a page one of those pulls in with a line
// "@other.md" (guideText), there or not yet.
func guidePage(slug string) bool {
	clean := path.Clean(slug)
	// In any case: where file names don't tell case apart, Wiki-Guide is wiki-guide.
	if strings.EqualFold(clean, guideSlug) || guideFiles[strings.ToLower(path.Base(clean))+".md"] {
		return true
	}
	p := safePath(slug)
	return p != "" && guideImports()[p]
}

// imports: the files the shared guides pull in, looked up at most once a
// second (a walk of the wiki's folders): canWrite asks for every page of a
// list.
var imports struct {
	sync.Mutex
	at    time.Time
	files map[string]bool
}

func guideImports() map[string]bool {
	imports.Lock()
	defer imports.Unlock()
	if imports.files != nil && time.Since(imports.at) < time.Second {
		return imports.files
	}
	root := resolvePath(cfg.Data)
	files := map[string]bool{}
	var follow func(guide string, depth int)
	follow = func(guide string, depth int) {
		text, err := readText(guide)
		if err != nil || depth >= importDepth {
			return
		}
		for _, line := range mdtext.SplitLines(text) {
			m := importRE.FindStringSubmatch(strings.TrimSpace(mdtext.LineBody(line)))
			if m == nil {
				continue
			}
			target := resolvePath(filepath.Join(filepath.Dir(guide), m[1]))
			if _, ok := relInside(root, target); ok && !files[target] {
				files[target] = true
				follow(target, depth+1)
			}
		}
	}
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			name := d.Name()
			if p != root && (strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".") ||
				filepath.Dir(p) == root && strings.EqualFold(name, privateDir)) {
				return filepath.SkipDir
			}
			return nil
		}
		if guideFiles[strings.ToLower(d.Name())] || filepath.Dir(p) == root && d.Name() == guideSlug+".md" {
			follow(p, 0)
		}
		return nil
	})
	imports.files, imports.at = files, time.Now()
	return files
}

// guideMsg: what an editor is told when they try to change a guide.
const guideMsg = "Only an admin of this wiki can change this page: it is a guide, which every user's assistant " +
	"follows (or a page a guide pulls in). Ask an admin of this wiki."

// readOnlyWhy: why user, who sees slug, may not change it (readOnly): a
// visitor of a wiki that is public to read signs in first, a viewer changes
// nothing, anyone else no guide.
func readOnlyWhy(user string) string {
	if user == visitor && settings().isViewer(user) {
		return visitorMsg
	}
	if settings().isViewer(user) {
		return readOnlyMsg
	}
	return guideMsg
}

// readOnly: user sees the page slug but may not change it: a viewer, or
// anyone but an admin on a guide (adminsOnly; readOnlyWhy says which).
// Refusals then say so (403, readOnlyMsg), as the page is there for them;
// one they can't see stays a 404.
func readOnly(user, slug string) bool { return canSee(user, slug) && !canWrite(user, slug) }

// readOnlyMsg: what a viewer is told when they try to change something
// shared.
const readOnlyMsg = "You can read this wiki but not change it: you are a viewer. Ask an admin of this wiki."

// errReadOnly: a viewer's change to something shared, refused.
var errReadOnly = refuseAs(refusedReadOnly, readOnlyMsg)

// canSeeTask: user may see the task t: one of their own private ones, or a
// shared one, unless its list is for some and they are none of them (the
// list's folder says for whom: taskFiles.SeesList, which the store's own
// readers ask too; this is for what holds every user's tasks, the search
// index and a task found by its id).
// A visitor sees no task.
func canSeeTask(user string, t *Task) bool {
	if user == visitor {
		return false
	}
	if t.Owner != "" {
		return t.Owner == user
	}
	return taskFiles.SeesList(user, t.List)
}

// canSeeList: user may see the list l and the tasks in it: their own
// private one, or a shared list, unless it is for some (ListInfo.For) and
// they are none of them: to anyone else such a list is no list, as another
// user's private one. A visitor sees no list. What lists, counts or names a
// list asks (taskLists gives only these).
func canSeeList(user string, l ListInfo) bool {
	if user == visitor {
		return false
	}
	if l.Owner != "" {
		return l.Owner == user
	}
	return len(l.For) == 0 || listMember(user, l.For)
}

// listMember: user is one of those a list is for: named, or in a group it
// names (@dev: Groups in docs/spec/users.md). A user who can't use the
// wiki (disabled, removed) is none; nor is an admin by being admin, as an
// admin sees no private page.
func listMember(user string, who []string) bool {
	s := settings()
	if u := s.Users[user]; u == nil || u.Disabled {
		return false
	}
	for _, w := range who {
		if group, ok := strings.CutPrefix(w, "@"); ok && s.memberOf(user, group) || w == user {
			return true
		}
	}
	return false
}

// listMembers: the users a list for who is for, A–Z.
func listMembers(who []string) []string {
	var out []string
	for _, name := range sortedKeys(settings().Users) {
		if listMember(name, who) {
			out = append(out, name)
		}
	}
	return out
}

// canWriteList: user may change the list l (rename, delete, its settings)
// and the tasks in it (make, change, move in or out): whoever sees it and
// may write tasks at all. One place to ask, so that members or roles per
// list change this and canSeeList, and nothing else.
func canWriteList(user string, l ListInfo) bool {
	return canSeeList(user, l) && mayWriteTasks(user) == nil
}

// canManageList: user may change the list itself: rename it, delete it,
// set its settings. A private list is its user's; a shared one, with users,
// its owners' (who made it: ListInfo.Owners) and every admin's. Whoever may
// write its tasks (canWriteList) and is neither leaves the list as it is:
// among a hundred people a list isn't everyone's to rename or delete.
// Without users, and in a wiki that has no admin (from before roles),
// whoever may write may manage, as before.
func canManageList(user string, l ListInfo) bool {
	if !canWriteList(user, l) {
		return false
	}
	s := settings()
	if l.Private() || s.Users == nil || !s.hasAdmin() {
		return true
	}
	return slices.Contains(l.Owners, user) || s.isAdmin(user)
}

// mayWriteTasks: user may write the task files: anyone but a viewer. Every
// task write passes it (writeOpenLocked, writeBothLocked), so no route or
// tool changes a task for a viewer, whatever it does.
func mayWriteTasks(user string) error {
	if user == visitor {
		return refuseAs(refusedReadOnly, visitorMsg)
	}
	if settings().isViewer(user) {
		return errReadOnly
	}
	return nil
}

// ownPrivate: slug is a page in user's own private folder (where a new
// task defaults to private).
func ownPrivate(user, slug string) bool { return placeOf(slug).own(user) }

// inQuicknote: slug is a note in user's Notes: any note without
// users or for Zone itself (user ""), with users only one in their own folder
// (a note from before users, at the top level, is a page like any other).
func inQuicknote(user, slug string) bool {
	if !isQuicknote(slug) {
		return false
	}
	return privateFolder(user) == "" || ownPrivate(user, slug)
}

// privateFolder: user's private folder ("private/alice"), "" without
// users (or for Zone itself, which has no user).
func privateFolder(user string) string {
	if settings().Users == nil || user == "" || user == visitor {
		return ""
	}
	return privateDir + "/" + user
}

// canSeePath: user may see the file rel (relative to the wiki, slash-
// separated), as git names it in the history: a page by its slug, anything
// else in private/ (a user's uploads and tasks in private/<user>/_assets/
// and _tasks/ too) like a page there. What lies in a hidden folder no one
// sees in Zone: its own files in .zone/ (the settings file, with its
// password hashes), and whatever else the history holds in one.
func canSeePath(user, rel string) bool {
	if strings.HasPrefix(rel, ".") && strings.Contains(rel, "/") || strings.Contains(rel, "/.") {
		return false
	}
	if p := placeOf(rel); !p.shared() {
		return p.sees(user)
	}
	// A list for some: its folder's files (the tasks, the list's entry) are
	// its members', also what it left there when it was deleted or renamed.
	if rest, ok := strings.CutPrefix(rel, taskfiles.DirName+"/"+taskfiles.ListsDirName+"/"); ok {
		if name, _, inFolder := strings.Cut(rest, "/"); inFolder && !taskFiles.SeesList(user, name) {
			return false
		}
	}
	// A visitor sees no task, so no task file's history either.
	return user != visitor || !strings.HasPrefix(rel, taskfiles.DirName+"/")
}

// strayPrivate: what lies in private/ outside every user's folder (seen by
// no one), for a warning at start: a user's folder whose user was removed,
// or a page put in private/ itself.
func strayPrivate() []string {
	if settings().Users == nil {
		return nil
	}
	entries, _ := os.ReadDir(filepath.Join(cfg.Data, privateDir))
	var stray []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		if !e.IsDir() || settings().Users[strings.ToLower(e.Name())] == nil {
			stray = append(stray, privateDir+"/"+e.Name())
		}
	}
	return stray
}

// visiblePages: the pages user may see, as iterPages lists them.
func visiblePages(user string) []PageFile {
	var out []PageFile
	for _, p := range iterPages() {
		if canSee(user, p.Slug) {
			out = append(out, p)
		}
	}
	return out
}

// visibleSlugs: the slugs of visiblePages, as a set.
func visibleSlugs(user string) map[string]bool {
	out := map[string]bool{}
	for _, p := range visiblePages(user) {
		out[p.Slug] = true
	}
	return out
}

// privateNameRE: a page's or file's path in private/ as a commit message
// names it ("Move private/alice/2026-…_idea into the wiki as ideas/x"), at
// the start of a path, not inside one ("notprivate/x", "notes/private/x"):
// group 2, after group 1.
var privateNameRE = regexp.MustCompile(`(^|[^\w./-])((?:` + keptDirs + `)/[\w./-]*[\w/])`)

// hideNames: a commit message for user, the paths in it user may not see
// as "a private page": a commit that moved a page out of a private folder,
// or rewrote links in one, is in the shared page's history too.
func hideNames(user, msg string) string {
	if settings().Users == nil || !strings.Contains(msg, privateDir+"/") {
		return msg
	}
	var out strings.Builder
	last := 0
	for _, m := range privateNameRE.FindAllStringSubmatchIndex(msg, -1) {
		name := msg[m[4]:m[5]]
		out.WriteString(msg[last:m[4]])
		if canSeePath(user, strings.TrimSuffix(name, ".md")) {
			out.WriteString(name)
		} else {
			out.WriteString("a private page")
		}
		last = m[5]
	}
	return out.String() + msg[last:]
}
