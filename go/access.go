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

// seesTaskFiles: user sees the files of the task id: who sees the task.
// Before there is a task of that id (its dialog is open and a file is
// pasted for the id it made up) they are who put the first one there's
// alone (unsavedBy), as theirs as their private place is; once there is
// none any more (deleted for good: the files stay, for the undo) no one's.
// Without users everyone's, as everything is.
func seesTaskFiles(user asker, id string) bool {
	if user.Name == visitor || !taskFolderRE.MatchString(id) {
		return false
	}
	s := settings()
	if s.Users == nil {
		return true
	}
	if u := s.Users[user.Name]; u == nil || u.Disabled {
		return false
	}
	if t := taskFiles.Find(id); t != nil {
		// An id in another case is no task's (its folder is another, or on a
		// file system that ignores case the task's: no way in either way).
		return t.ID == id && canSeeTask(user, t)
	}
	return unsavedBy(id) == user.Name && user.HasPrivate()
}

// keepsUnsaved: user may put the first file in the folder of a task no one
// has made yet (claimUnsaved): a user of the wiki who may write, whose
// private place is there for them, as such files are theirs alone until
// the task is made.
func keepsUnsaved(user asker) bool {
	if privateFolder(user) == "" || !user.HasPrivate() || changesNothing(user) {
		return false
	}
	u := settings().Users[user.Name]
	return u != nil && !u.Disabled
}

// placeOf: the place of the page slug, or of the file rel (slash-separated,
// relative to the wiki): private/<user>/… is that user's, groups/<group>/…
// that group's, anything else in private/ or groups/ no one's, the rest
// everyone's. A group's folder is its group's whether or not anyone is in
// the group: so what is shared never depends on who is in which group, and
// emptying a group publishes nothing. Without users nothing is private. It
// is judged as safePath opens it, cleaned ("private/bob/../alice/x" is
// alice's), and in any case: where file names don't tell case apart,
// "Private/alice" is private/alice, and so is "PRIVATE" or "groupſ" (ſ is
// an s to a case-insensitive volume, which folds case as EqualFold does).
func placeOf(rel string) place {
	s := settings()
	if s == nil || s.Users == nil { // no settings yet: a text function's test
		return place{}
	}
	users := s.Users
	dir, rest, found := strings.Cut(path.Clean(rel), "/")
	switch {
	case !found:
		return place{}
	case strings.EqualFold(dir, privateDir):
		dir = privateDir
	case strings.EqualFold(dir, groupsDir):
		dir = groupsDir
	default:
		return place{}
	}
	name, _, inside := strings.Cut(strings.ToLower(rest), "/")
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

// ownerPlace: the place of the tasks and lists of owner, as the task store
// files them (Store.Dir): "" everyone's, a user's name their private place,
// "@<group>" (taskfiles.GroupOwner) the group's: where a task is kept is
// who sees it, as for a page (placeOf). Without users nothing is anyone's.
func ownerPlace(owner string) place {
	if settings().Users == nil || owner == "" {
		return place{}
	}
	if g, ok := taskfiles.GroupOf(owner); ok {
		if groupNameRE.MatchString(g) {
			return place{ofGroup, g}
		}
		return place{kind: ofNoOne}
	}
	return place{ofUser, owner}
}

// privateOwner: owner is a user's: their tasks and lists are theirs alone
// (a group's are shared, with its members).
func privateOwner(owner string) bool {
	_, group := taskfiles.GroupOf(owner)
	return owner != "" && !group
}

// canSeeTask: user may see the task t: who sees its place (ownerPlace):
// everyone a shared one, a user their own, a group's members the group's
// (an admin who is none of them doesn't, as an admin sees no private page).
// The store reads only the places a user sees (Store.Owners); this is for
// what holds every user's tasks, the search index and a task found by its
// id. A visitor sees no task.
func canSeeTask(user asker, t *Task) bool {
	return user.Name != visitor && ownerPlace(t.Owner).sees(user)
}

// canSeeList: user may see the list l and the tasks in it: who sees its
// place, as for its tasks. To anyone else it is no list. A visitor sees no
// list. What lists, counts or names a list asks (taskLists gives only these).
func canSeeList(user asker, l ListInfo) bool {
	return user.Name != visitor && ownerPlace(l.Owner).sees(user)
}

// listMembers: the users who see a group's list l and can be given its
// tasks, A–Z: the group's members; none for any other list (a shared one is
// everyone's, a private one its user's alone).
func listMembers(l ListInfo) []string {
	g := l.Group()
	if g == "" {
		return nil
	}
	var out []string
	for _, name := range sortedKeys(settings().Users) {
		if u := settings().Users[name]; !u.Disabled && inGroup(asUser(name), g) {
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
// password hashes), and whatever else the history holds in one. Nor does
// anyone see what isn't a clean path (cleanPath): a slip in reading git's
// output hides a file rather than showing it.
func canSeePath(user asker, rel string) bool {
	if !cleanPath(rel) || strings.HasPrefix(rel, ".") && strings.Contains(rel, "/") || strings.Contains(rel, "/.") {
		return false
	}
	if p := placeOf(rel); !p.shared() {
		return p.sees(user)
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

// seesFile: user may see the file at path (in the wiki, as the code holds
// it): canSeePath. What a write tells its user it changed, where it changed
// files of others too (a rename's links on another user's private page, a
// tag in a list for some), is counted with it.
func seesFile(user asker, path string) bool {
	rel, ok := relInside(cfg.Data, path)
	return ok && canSeePath(user, filepath.ToSlash(rel))
}

// seenFiles: of files (paths in the wiki), those user sees (seesFile).
func seenFiles(user asker, files []string) []string {
	var out []string
	for _, f := range files {
		if seesFile(user, f) {
			out = append(out, f)
		}
	}
	return out
}

// cleanPath: rel is a path as git names a file with -z (a folder: with a
// "/" after it): relative, no part between its slashes empty, "." or "..",
// no control character or backslash in it (mdtext.BadNameRune) and no
// double quote at its start. Without -z git quotes a name that holds a byte
// outside ASCII, a quote, a backslash or a control character: in double
// quotes, with backslashes ("private/alice/K\303\274ndigung.pdf"), and that
// text, read as a path, starts with no private folder: Changes showed
// other users' private files to everyone (2026-11).
func cleanPath(rel string) bool {
	if rel == "" || strings.HasPrefix(rel, "/") || strings.HasPrefix(rel, `"`) {
		return false
	}
	for _, r := range rel {
		if mdtext.BadNameRune(r) {
			return false
		}
	}
	for _, part := range strings.Split(strings.TrimSuffix(rel, "/"), "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
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
// group 2, after group 1. Its letters are any script's ("Gehälter"). A
// group's and a task's files likewise ("Upload
// _tasks/files/<id>/_assets/scan.png": the task's id and the file's name).
// In any case: placeOf judges a path in lower case, so "Private/alice/x" (a
// folder made by hand) is alice's too, and is named to no one else either.
var privateNameRE = regexp.MustCompile(`(?i)(^|[^` + nameClass + `./-])((?:` + keptDirs + `|` + taskFilesDir + `)/[` + nameClass + `./-]*[` + nameClass + `/])`)

// nameClass: the letters, digits and marks of a path's name in a commit
// message (inside a regex's brackets), with "_".
const nameClass = `\p{L}\p{N}\p{M}_`

// nameGoesOn: after a path privateNameRE found in a commit message, at a
// space, the name goes on: an upload's name may hold spaces ("Upload
// private/alice/_assets/Arztbrief März.pdf"), so the words after it are
// its until one that goes on with Zone's message (" to ", " (MCP)" …).
func nameGoesOn(rest string) bool {
	if !strings.HasPrefix(rest, " ") || len(rest) < 2 {
		return false
	}
	for _, w := range []string{"to ", "into ", "as ", "and ", "in ", "from ", "("} {
		if strings.HasPrefix(rest[1:], w) {
			return false
		}
	}
	return true
}

// pathRune: r may be part of a path's name, as privateNameRE reads one.
func pathRune(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsNumber(r) || unicode.IsMark(r) || strings.ContainsRune("_./-", r)
}

// namesKeptDir: msg may name a path in a private or a group's folder, or a
// task's file.
func namesKeptDir(msg string) bool {
	low := strings.ToLower(msg)
	return strings.Contains(low, privateDir+"/") || strings.Contains(low, groupsDir+"/") || strings.Contains(low, taskFilesDir+"/")
}

// taskFile: rel is a task's file (_tasks/files/<id>/…), whose place is the
// task's (seesTaskFiles), not the folder's.
func taskFile(rel string) bool { return strings.HasPrefix(strings.ToLower(rel), taskFilesDir+"/") }

// hiddenAs: what a commit message says for a path user may not see.
func hiddenAs(name string) string {
	switch {
	case strings.HasPrefix(strings.ToLower(name), groupsDir+"/"):
		return "a group's page"
	case taskFile(name):
		return "a task's file"
	}
	return "a private page"
}

// hideNames: a commit message for user, the paths in it user may not see
// as "a private page" ("a group's page", "a task's file"): a commit that
// moved a page out of a private folder, or rewrote links in one, is in the
// shared page's history too. hidden: the names of the commit's files user
// may not see (canSeePath), as git gives them, which are taken out whole
// whatever they hold (a space, a comma, a parenthesis); then any other path
// in a private or a group's folder, or of a task's files (privateNameRE,
// its name going on past a space as nameGoesOn says).
//
// Nor does it name a group's list to who is none of the group: a commit
// about one that writes something everyone sees too (a list moved to the
// group, renamed in the pages that show it) is in everyone's history; to
// anyone else it says only that a group's list changed.
func hideNames(user asker, msg string, hidden []string) string {
	if settings().Users == nil {
		return msg
	}
	if strings.Contains(msg, "#") {
		for name, group := range groupListNames() {
			if namesList(msg, name) && !inGroup(user, group) {
				return "A group's list changed"
			}
		}
	}
	if !namesKeptDir(msg) {
		return msg
	}
	// Longest first, so that a name that starts another doesn't take part of it.
	hidden = slices.Clone(hidden)
	slices.SortFunc(hidden, func(a, b string) int { return len(b) - len(a) })
	for _, f := range hidden {
		if !placeOf(f).shared() || taskFile(f) {
			for _, name := range []string{f, strings.TrimSuffix(f, ".md")} {
				msg = replaceName(msg, name, hiddenAs(f))
			}
		}
	}
	var out strings.Builder
	last := 0
	for _, m := range privateNameRE.FindAllStringSubmatchIndex(msg, -1) {
		if m[4] < last {
			continue // in the words of a name before it
		}
		name, end := msg[m[4]:m[5]], m[5]
		out.WriteString(msg[last:m[4]])
		if canSeePath(user, strings.TrimSuffix(name, ".md")) {
			out.WriteString(name)
		} else {
			for nameGoesOn(msg[end:]) {
				next := strings.IndexByte(msg[end+1:], ' ')
				if next < 0 {
					next = len(msg) - end - 1
				}
				end += 1 + next
			}
			out.WriteString(hiddenAs(name))
		}
		last = end
	}
	return out.String() + msg[last:]
}

// replaceName: msg with name, where it stands as a whole (no letter, digit,
// ".", "-", "_" or "/" just before or after it), as as.
func replaceName(msg, name, as string) string {
	if name == "" {
		return msg
	}
	var out strings.Builder
	for {
		i := strings.Index(msg, name)
		if i < 0 {
			break
		}
		before, _ := utf8.DecodeLastRuneInString(msg[:i])
		after, _ := utf8.DecodeRuneInString(msg[i+len(name):])
		if (i == 0 || !pathRune(before)) && (i+len(name) == len(msg) || !pathRune(after)) {
			out.WriteString(msg[:i] + as)
		} else {
			out.WriteString(msg[:i+len(name)])
		}
		msg = msg[i+len(name):]
	}
	return out.String() + msg
}
