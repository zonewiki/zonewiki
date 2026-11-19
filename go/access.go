// Who may see and change what (docs/spec/users.md): with users, a folder is
// one user's (private/<user>/, which only that user sees), a group's
// (groups/<group>/, which whoever is in the group sees) or everyone's
// (anything else); what else lies under private/ or groups/ (a page in
// private/ itself, a folder of no user, of no group's name) is seen by no
// one. Without users everyone sees everything, and private/ and groups/ are
// ordinary folders. Every route and tool that shows or changes a page asks
// here (place, canSee, canWrite, canSeePath), so the rule lives in one place.
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
	"unicode"
	"unicode/utf8"
	askerpkg "zonewiki/internal/asker"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"
)

// asker: who is asking (package asker): a user, or an assistant's
// connection that was given less than its user has. Every function that
// answers who may see or change something takes one, so a limit a
// connection has holds wherever the question is asked; a function that
// only needs to know who someone is (a commit's author, whose Inbox) takes
// the name, asker.Name.
type asker = askerpkg.Asker

// asUser: the user name themselves, with all they may: what Zone asks
// about a user it knows by name (whom a task is given to, who gets a mail),
// never about a request, whose asker is userOf(r).
func asUser(name string) asker { return askerpkg.User(name) }

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

// groupsDir is the folder of the groups' folders: groups/<group>/ holds
// the pages (and their uploads) of the group's members (docs/spec/users.md,
// Group pages).
const groupsDir = "groups"

// keptDirs: the folders whose first level names who sees what is kept in
// them (private/<user>/, groups/<group>/), as a pattern: what spells such a
// path in a text (an upload's link, a commit message) is made from it.
const keptDirs = privateDir + "|" + groupsDir

// place: whose a page or a file is, which its path alone says (where
// something is kept is who sees it): everyone's, one user's, a group's, or
// no one's. Every question about who sees what is asked of a place, so the
// answers are here and nowhere else.
type place struct {
	kind placeKind
	name string // the user (ofUser), the group (ofGroup) or the task (ofTask, its id) whose it is, else ""
}

type placeKind int

const (
	ofEveryone placeKind = iota // shared: outside private/ and groups/, or a wiki without users
	ofUser                      // in private/<user>/
	ofGroup                     // in groups/<group>/: its members', whoever they are now (nobody, too)
	ofNoOne                     // in private/ but in no user's folder, or in groups/ but in no group's: "private/carol/x", "private/alice" itself, "groups/x"
	ofTask                      // a task's files, in _tasks/files/<id>/: whoever sees the task (taskPlace; no page is there, so placeOf never says it)
)

// taskFilesDir: the folder of the tasks' files, a folder a task
// (_tasks/files/<id>/_assets/<file>): a file attached to a task is kept by
// the task, not by its list or its owner, so it is seen by who sees the
// task wherever the task goes (another list, a list narrowed or renamed, a
// private task shared) and nothing has to move with it.
const taskFilesDir = taskfiles.DirName + "/files"

// taskFolderRE: a task's id as a folder's name: a uuid as Zone makes them, or
// what an import brought, as long as it is one plain word.
var taskFolderRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)

// taskPlace: the place of the files of the task id.
func taskPlace(id string) place { return place{ofTask, id} }

// seesTaskFiles: user sees the files of the task id: who sees the task. A
// task there isn't (yet: its dialog is open and its first file is pasted;
// or no more: deleted) has files only who knows its id finds, and no
// visitor: an id is 122 random bits, said to nobody but the task's readers.
func seesTaskFiles(user asker, id string) bool {
	if user.Name == visitor || !taskFolderRE.MatchString(id) {
		return false
	}
	if s := settings(); s.Users != nil {
		if u := s.Users[user.Name]; u == nil || u.Disabled {
			return false
		}
	}
	if t := taskFiles.Find(id); t != nil {
		return canSeeTask(user, t)
	}
	return true
}

// placeOf: the place of the page slug, or of the file rel (slash-separated,
// relative to the wiki): private/<user>/… is that user's, groups/<group>/…
// that group's, anything else in private/ or groups/ no one's, the rest
// everyone's. A group's folder is its group's whether or not anyone is in
// the group: so what is shared never depends on who is in which group, and
// emptying a group publishes nothing. Without users nothing is private. It
// is judged as safePath opens it, cleaned ("private/bob/../alice/x" is
// alice's), and in lower case.
func placeOf(rel string) place {
	users := settings().Users
	if users == nil {
		return place{}
	}
	dir, rest, found := strings.Cut(strings.ToLower(path.Clean(rel)), "/")
	if !found || dir != privateDir && dir != groupsDir {
		return place{}
	}
	name, _, inside := strings.Cut(rest, "/")
	switch {
	case !inside:
		return place{kind: ofNoOne}
	case dir == privateDir && users[name] != nil:
		return place{ofUser, name}
	case dir == groupsDir && groupNameRE.MatchString(name):
		return place{ofGroup, name}
	}
	return place{kind: ofNoOne}
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

// group: the group whose place it is, "" for any other place.
func (p place) group() string {
	if p.kind == ofGroup {
		return p.name
	}
	return ""
}

// folder: the place's folder ("private/alice", "groups/dev"), "" for the
// shared pages (the wiki itself) and for no one's.
func (p place) folder() string {
	switch p.kind {
	case ofUser:
		return privateDir + "/" + p.name
	case ofGroup:
		return groupsDir + "/" + p.name
	case ofTask:
		return taskFilesDir + "/" + p.name
	}
	return ""
}

// own: it is user's own, and theirs alone.
func (p place) own(user asker) bool {
	return p.kind == ofUser && p.name == user.Name && user.HasPrivate()
}

// sees: user sees what is kept there: everyone what is shared, a user their
// own, a group's members what is the group's (an admin who is none of them
// doesn't, as an admin sees no private page; nor a visitor, who is in no
// group).
func (p place) sees(user asker) bool {
	switch p.kind {
	case ofEveryone:
		return true
	case ofUser:
		return p.own(user)
	case ofGroup:
		return inGroup(user, p.name)
	case ofTask:
		return seesTaskFiles(user, p.name)
	}
	return false
}

// inGroup: the group's place is user's: they are in the group, and an asker
// that was given only some of its user's groups was given this one. Every
// question "is this group's theirs" is asked here (and groupsFor lists
// them), never of the settings with an asker's name: that would give a
// narrowed asker its user's groups back.
func inGroup(user asker, group string) bool {
	return user.HasGroup(group) && settings().memberOf(user.Name, group)
}

// groupsFor: the groups whose places are user's (inGroup), A–Z.
func groupsFor(user asker) []string {
	var out []string
	for _, g := range settings().groupsOf(user.Name) {
		if user.HasGroup(g) {
			out = append(out, g)
		}
	}
	return out
}

// theirs: p is a user's or a group's place that user sees: their own
// folder, or one of a group they are in (not the shared wiki, which is
// everyone's, nor a place that is no one's). Where an upload of theirs is
// kept with its page, and which uploads they are served.
func (p place) theirs(user asker) bool {
	return (p.kind == ofUser || p.kind == ofGroup) && p.sees(user)
}

// within: everyone who sees what is kept at p sees what is kept at q too:
// q is the same place or a wider one. What a link's mark asks (does every
// reader of this page see its target), and a move (does it take the page
// from anyone). Between groups it goes by how they are made, not by who
// happens to be in them: dev is within product while product includes dev,
// and two groups of the same people are two.
func (p place) within(q place) bool {
	switch {
	case q.shared():
		return true
	case p.kind == ofUser:
		return q == p || q.kind == ofGroup && settings().memberOf(p.name, q.name)
	case p.kind == ofGroup:
		return q.kind == ofGroup && (q.name == p.name || settings().groupIn(p.name, q.name))
	}
	return false
}

// canSee: user may see the page slug: a shared page, one of their own, or
// one of a group they are in.
func canSee(user asker, slug string) bool { return placeOf(slug).sees(user) }

// canWrite: user may change (create, edit, rename, delete) the page slug:
// the pages they may see, since everything shared is editable, but for a
// viewer (users.go: roleViewer), who changes nothing.
func canWrite(user asker, slug string) bool {
	return canSee(user, slug) && !changesNothing(user) && !adminsOnly(user, slug)
}

// changesNothing: user reads and changes nothing, whatever it is: a viewer
// (the user's role), or an asker that was let only read (an assistant's
// read-only connection). The one place that says so: canWrite and
// mayWriteTasks ask it, so every write does.
func changesNothing(user asker) bool {
	return user.ReadOnly || settings().isViewer(user.Name)
}

// noChangeMsg: what user, who changes nothing, is told when they try: a
// visitor signs in first, a viewer is one by their role, and otherwise it
// is a connection that was let only read.
func noChangeMsg(user asker) string {
	switch {
	case user.Name == visitor:
		return visitorMsg
	case settings().isViewer(user.Name):
		return readOnlyMsg
	}
	return readOnlyConnectionMsg
}

// adminsOnly: slug is a shared guide (guidePage), which with users only an
// admin changes, and user isn't one. A guide is what every user's assistant
// is told to follow (mcptools.go: the wiki's guide in its instructions, a
// folder's before a write there), so whoever writes it steers them all: an
// editor, or an assistant that read a prompt injection, could have every
// assistant copy private pages into the open. A guide in a user's own
// private folder is theirs: only their assistant reads it. Without users
// there are no admins, and the wiki's one user writes its guides.
func adminsOnly(user asker, slug string) bool {
	s := settings()
	if s.Users == nil || s.isAdmin(user.Name) {
		return false
	}
	// A group's guide steers the assistant of every member, as a shared one
	// does everyone's: an admin's too, and of those only one who is in the
	// group sees it at all (canSee).
	if p := placeOf(slug); !p.shared() && p.kind != ofGroup {
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
			// Not private/: a guide there is its user's. groups/ is walked: a
			// group's guides, and what they pull in, are admins' too.
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

// groupGuideMsg: the same of a group's guide, which only an admin who is in
// the group changes.
const groupGuideMsg = "Only an admin of this wiki who is in the group can change this page: it is a guide, which the " +
	"assistant of everyone in the group follows (or a page a guide pulls in). Ask an admin to join the group."

// readOnlyWhy: why user, who sees slug, may not change it (readOnly): a
// visitor of a wiki that is public to read signs in first, a viewer changes
// nothing, nor does a connection that only reads, anyone else no guide.
func readOnlyWhy(user asker, slug string) string {
	if changesNothing(user) {
		return noChangeMsg(user)
	}
	if placeOf(slug).kind == ofGroup {
		return groupGuideMsg
	}
	return guideMsg
}

// readOnly: user sees the page slug but may not change it: a viewer, or
// anyone but an admin on a guide (adminsOnly; readOnlyWhy says which).
// Refusals then say so (403, readOnlyMsg), as the page is there for them;
// one they can't see stays a 404.
func readOnly(user asker, slug string) bool { return canSee(user, slug) && !canWrite(user, slug) }

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
func canSeeTask(user asker, t *Task) bool {
	if user.Name == visitor {
		return false
	}
	if t.Owner != "" {
		return t.Owner == user.Name && user.HasPrivate()
	}
	return taskFiles.SeesList(user, t.List)
}

// canSeeList: user may see the list l and the tasks in it: their own
// private one, or a shared list, unless it is for some (ListInfo.For) and
// they are none of them: to anyone else such a list is no list, as another
// user's private one. A visitor sees no list. What lists, counts or names a
// list asks (taskLists gives only these).
func canSeeList(user asker, l ListInfo) bool {
	if user.Name == visitor {
		return false
	}
	if l.Owner != "" {
		return l.Owner == user.Name && user.HasPrivate()
	}
	return len(l.For) == 0 || listMember(user, l.For)
}

// listMember: user is one of those a list is for: named, or in a group it
// names (@dev: Groups in docs/spec/users.md). A user who can't use the
// wiki (disabled, removed) is none; nor is an admin by being admin, as an
// admin sees no private page.
func listMember(user asker, who []string) bool {
	s := settings()
	if s.Users == nil {
		return true // without users everyone sees everything: a list for some from when there were users, too
	}
	if u := s.Users[user.Name]; u == nil || u.Disabled {
		return false
	}
	for _, w := range who {
		if group, ok := strings.CutPrefix(w, "@"); ok && inGroup(user, group) || w == user.Name {
			return true
		}
	}
	return false
}

// listMembers: the users a list for who is for, A–Z.
func listMembers(who []string) []string {
	var out []string
	for _, name := range sortedKeys(settings().Users) {
		if listMember(asUser(name), who) {
			out = append(out, name)
		}
	}
	return out
}

// canWriteList: user may change the tasks in the list l (make, change, move
// in or out): whoever sees it and may write tasks at all. The list itself
// (rename, delete, its settings) is canManageList's. One place to ask, so
// that members or roles per list change this and canSeeList, and nothing
// else.
func canWriteList(user asker, l ListInfo) bool {
	return canSeeList(user, l) && mayWriteTasks(user) == nil
}

// canManageList: user may change the list itself: rename it, delete it,
// set its settings. A private list is its user's; a shared one, with users,
// its owners' (who made it: ListInfo.Owners) and every admin's. Whoever may
// write its tasks (canWriteList) and is neither leaves the list as it is:
// among a hundred people a list isn't everyone's to rename or delete.
// Without users, and in a wiki that has no admin (from before roles),
// whoever may write may manage, as before.
func canManageList(user asker, l ListInfo) bool {
	if !canWriteList(user, l) {
		return false
	}
	s := settings()
	if l.Private() || s.Users == nil || !s.hasAdmin() {
		return true
	}
	return slices.Contains(l.Owners, user.Name) || s.isAdmin(user.Name)
}

// mayWriteTasks: user may write the task files: anyone but a viewer and an
// asker that only reads (changesNothing). Every
// task write passes it (writeOpenLocked, writeBothLocked), so no route or
// tool changes a task for a viewer, whatever it does.
func mayWriteTasks(user asker) error {
	if user.Name == visitor {
		return refuseAs(refusedReadOnly, visitorMsg)
	}
	if changesNothing(user) {
		return refuseAs(refusedReadOnly, noChangeMsg(user))
	}
	return nil
}

// ownPrivate: slug is a page in user's own private folder (where a new
// task defaults to private).
func ownPrivate(user asker, slug string) bool { return placeOf(slug).own(user) }

// inQuicknote: slug is a note in user's Notes: any note without
// users or for Zone itself (user ""), with users only one in their own folder
// (a note from before users, at the top level, is a page like any other).
func inQuicknote(user asker, slug string) bool {
	if !isQuicknote(slug) {
		return false
	}
	return privateFolder(user) == "" || ownPrivate(user, slug)
}

// privateFolder: user's private folder ("private/alice"), "" without
// users (or for Zone itself, which has no user).
func privateFolder(user asker) string {
	if settings().Users == nil || user.Name == "" || user.Name == visitor {
		return ""
	}
	return privateDir + "/" + user.Name
}

// ownsTasks: the task files of owner (a user's name, as the store files
// them: private/<owner>/_tasks/) are user's own, and there for them.
func ownsTasks(user asker, owner string) bool {
	return owner != "" && owner == user.Name && user.HasPrivate()
}

// lacksPrivate: user is a user of this wiki whose private place isn't there
// for it (an assistant's connection that wasn't given it). Not the same as
// a wiki without users, where nothing is private and privateFolder is "":
// here the Inbox, the notes and the private lists are, and aren't its.
func lacksPrivate(user asker) bool { return privateFolder(user) != "" && !user.HasPrivate() }

// noPrivateMsg: what such an asker is told when it would make or change
// something private.
const noPrivateMsg = "This connection wasn't given your private pages, notes and tasks, so it can make none: " +
	"what it made there it couldn't see again. Use a shared page or a shared list (a task without a list goes to the private Inbox)."

// canSeePath: user may see the file rel (relative to the wiki, slash-
// separated), as git names it in the history: a page by its slug, anything
// else in private/ (a user's uploads and tasks in private/<user>/_assets/
// and _tasks/ too) like a page there. What lies in a hidden folder no one
// sees in Zone: its own files in .zone/ (the settings file, with its
// password hashes), and whatever else the history holds in one.
func canSeePath(user asker, rel string) bool {
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
	// A task's files: who sees the task.
	if rest, ok := strings.CutPrefix(rel, taskFilesDir+"/"); ok {
		if id, _, inFolder := strings.Cut(rest, "/"); inFolder && !seesTaskFiles(user, id) {
			return false
		}
	}
	// A visitor sees no task, so no task file's history either.
	return user.Name != visitor || !strings.HasPrefix(rel, taskfiles.DirName+"/")
}

// namesList: msg names the list name as commit messages do ("#hiring"), as
// a whole word ("#hiring-2" is another list).
func namesList(msg, name string) bool {
	for rest := msg; ; {
		i := strings.Index(rest, "#"+name)
		if i < 0 {
			return false
		}
		rest = rest[i+1+len(name):]
		if r, _ := utf8.DecodeRuneInString(rest); rest == "" || !(unicode.IsLetter(r) || unicode.IsDigit(r) || r == '-' || r == '_') {
			return true
		}
	}
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

// strayGroups: what lies in groups/ that no one sees, for a warning at
// start (and for a wiki that had a folder groups/ before group folders:
// docs/spec/users.md, Group pages): the folder of a group nobody is in, a
// folder no group can be named as, a page put in groups/ itself.
func strayGroups() []string {
	s := settings()
	if s.Users == nil {
		return nil
	}
	entries, _ := os.ReadDir(filepath.Join(cfg.Data, groupsDir))
	var stray []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		if !e.IsDir() || len(s.groupMembers(strings.ToLower(e.Name()))) == 0 {
			stray = append(stray, groupsDir+"/"+e.Name())
		}
	}
	return stray
}

// visiblePages: the pages user may see, as iterPages lists them.
func visiblePages(user asker) []PageFile {
	var out []PageFile
	for _, p := range iterPages() {
		if canSee(user, p.Slug) {
			out = append(out, p)
		}
	}
	return out
}

// visibleSlugs: the slugs of visiblePages, as a set.
func visibleSlugs(user asker) map[string]bool {
	out := map[string]bool{}
	for _, p := range visiblePages(user) {
		out[p.Slug] = true
	}
	return out
}

// seenFolders: the folders whose pages user sees, for what opens the files
// itself rather than through visiblePages (grep_wiki's git grep, which is
// told these and no other): "" for the wiki's top (a page there is
// everyone's), each folder at the top that is everyone's, and in private/
// and groups/ the folders of the places user sees (placeOf, which judges
// in lower case: a hand-made Private/Alice/ is alice's, and only alice's).
// Slash-separated, relative to the wiki, as they are on disk. Zone's own
// folders (_…, .…) are none of them, and the caller leaves them out below
// these too. Listed from what is there: a folder made since isn't searched,
// rather than searched unasked.
func seenFolders(user asker) []string {
	out := []string{""}
	top, _ := os.ReadDir(cfg.Data)
	for _, e := range top {
		name := e.Name()
		if !e.IsDir() || strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".") {
			continue
		}
		if placeOf(name + "/page").shared() {
			out = append(out, name)
			continue
		}
		// private/ or groups/: its folders are places, each its own.
		inside, _ := os.ReadDir(filepath.Join(cfg.Data, name))
		for _, f := range inside {
			if folder := name + "/" + f.Name(); f.IsDir() && placeOf(folder+"/page").sees(user) {
				out = append(out, folder)
			}
		}
	}
	return out
}

// privateNameRE: a page's or file's path in private/ as a commit message
// names it ("Move private/alice/2026-…_idea into the wiki as ideas/x"), at
// the start of a path, not inside one ("notprivate/x", "notes/private/x"):
// group 2, after group 1.
// In any case: placeOf judges a path in lower case, so "Private/alice/x" (a
// folder made by hand) is alice's too, and is named to no one else either.
var privateNameRE = regexp.MustCompile(`(?i)(^|[^\w./-])((?:` + keptDirs + `)/[\w./-]*[\w/])`)

// hideNames: a commit message for user, the paths in it user may not see
// as "a private page": a commit that moved a page out of a private folder,
// or rewrote links in one, is in the shared page's history too.
//
// Nor does it name a list for some to who is none of its members: a commit
// that narrowed one ("List #hiring is for @hiring, anna"), pinned it for
// everyone or renamed it writes the registry everyone reads, so it is in
// everyone's history; to anyone else it says only that a list changed.
func hideNames(user asker, msg string) string {
	if settings().Users == nil {
		return msg
	}
	if strings.Contains(msg, "#") {
		for _, name := range taskFiles.ForSome() {
			if namesList(msg, name) && !taskFiles.SeesList(user, name) {
				return "A list for some changed"
			}
		}
	}
	if low := strings.ToLower(msg); !strings.Contains(low, privateDir+"/") && !strings.Contains(low, groupsDir+"/") {
		return msg
	}
	var out strings.Builder
	last := 0
	for _, m := range privateNameRE.FindAllStringSubmatchIndex(msg, -1) {
		name := msg[m[4]:m[5]]
		out.WriteString(msg[last:m[4]])
		switch {
		case canSeePath(user, strings.TrimSuffix(name, ".md")):
			out.WriteString(name)
		case strings.HasPrefix(strings.ToLower(name), groupsDir+"/"):
			out.WriteString("a group's page")
		default:
			out.WriteString("a private page")
		}
		last = m[5]
	}
	return out.String() + msg[last:]
}
