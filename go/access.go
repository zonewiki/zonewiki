// Who may see and change what (docs/spec/users.md): with users, everything
// is shared but private/<user>/, which only that user sees; anything else
// under private/ (a page in private/ itself, a folder of no user) is seen
// by no one. Without users everyone sees everything, and private/ is an
// ordinary folder. Every route and tool that shows or changes a page asks
// here, so the rule lives in one place.
package main

import (
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

// privateDir is the folder of the users' private folders.
const privateDir = "private"

// ownerIn: a path inside a user's folder below folder (slash-separated,
// relative; "private/alice/x"): (that user, true); below folder but in no
// user's folder ("private/carol/x", "private/alice" itself): ("", true);
// elsewhere: ("", false). Without users nothing is private. rel is judged
// as safePath opens it, cleaned: "private/bob/../alice/x" is alice's.
func ownerIn(folder, rel string) (owner string, private bool) {
	users := settings().Users
	if users == nil {
		return "", false
	}
	rest, found := strings.CutPrefix(strings.ToLower(path.Clean(rel)), folder+"/")
	if !found {
		return "", false
	}
	name, _, inside := strings.Cut(rest, "/")
	if !inside || users[name] == nil {
		return "", true
	}
	return name, true
}

// pageOwner: the user whose private page slug is ("" for a shared page),
// and whether it is private at all (a private page may have no owner).
func pageOwner(slug string) (owner string, private bool) {
	return ownerIn(privateDir, slug)
}

// canSee: user may see the page slug: a shared page, or one of their own.
func canSee(user, slug string) bool {
	owner, private := pageOwner(slug)
	return !private || owner != "" && owner == user
}

// canWrite: user may change (create, edit, rename, delete) the page slug:
// the pages they may see, since everything shared is editable, but for a
// viewer (users.go: roleViewer), who changes only their own folder.
func canWrite(user, slug string) bool {
	return canSee(user, slug) && (!settings().isViewer(user) || ownPrivate(user, slug))
}

// readOnly: user sees the page slug but may not change it: a viewer and a
// shared page. Refusals then say so (403, readOnlyMsg), as the page is
// there for them; one they can't see stays a 404.
func readOnly(user, slug string) bool { return canSee(user, slug) && !canWrite(user, slug) }

// readOnlyMsg: what a viewer is told when they try to change something
// shared.
const readOnlyMsg = "You can read this wiki but not change it: you are a viewer. Ask an admin."

// errReadOnly: a viewer's change to something shared, refused.
var errReadOnly = &apiError{http.StatusForbidden, readOnlyMsg}

// canSeeTask: user may see the task t: a shared one, or one of their own
// private ones.
func canSeeTask(user string, t *Task) bool { return t.Owner == "" || t.Owner == user }

// canWriteTask: user may change the task t: one they see, but a viewer
// only their own private ones.
func canWriteTask(user string, t *Task) bool {
	return canSeeTask(user, t) && (!settings().isViewer(user) || t.Owner == user)
}

// keepsSharedTasks: a write of the tasks by user leaves the shared ones as
// they are on disk, if user is a viewer (the check every task write passes,
// writeOpenLocked and writeBothLocked: so no route or tool can change a
// shared task for a viewer, whatever it does). active in order (the order
// is a change too); completed by id, nil: not part of the write.
func keepsSharedTasks(user string, active, completed []*Task) error {
	if !settings().isViewer(user) {
		return nil
	}
	diskActive, diskCompleted, err := readOwnerStrict("")
	if err != nil {
		return err
	}
	encode := func(tasks []*Task, owner string) []string {
		var out []string
		for _, t := range tasks {
			if t.Owner == owner {
				b, _ := encodeTasks([]*Task{t})
				out = append(out, b)
			}
		}
		return out
	}
	if !slices.Equal(encode(active, ""), encode(diskActive, "")) {
		return errReadOnly
	}
	if completed != nil {
		a, b := encode(completed, ""), encode(diskCompleted, "")
		slices.Sort(a)
		slices.Sort(b)
		if !slices.Equal(a, b) {
			return errReadOnly
		}
	}
	return nil
}

// ownPrivate: slug is a page in user's own private folder (where a new
// task defaults to private).
func ownPrivate(user, slug string) bool {
	owner, _ := pageOwner(slug)
	return owner != "" && owner == user
}

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
	if settings().Users == nil || user == "" {
		return ""
	}
	return privateDir + "/" + user
}

// canSeePath: user may see the file rel (relative to the wiki, slash-
// separated), as git names it in the history: a page by its slug, anything
// else in private/ (a user's uploads and tasks in private/<user>/_assets/
// and _tasks/ too) like a page there. Zone's own files in .zone/ (the
// settings file, with its password hashes; .leaf/, an older Zone's) no one
// sees in Zone.
func canSeePath(user, rel string) bool {
	if strings.HasPrefix(rel, stateDirName+"/") || strings.HasPrefix(rel, legacyStateDirName+"/") {
		return false
	}
	if owner, private := ownerIn(privateDir, rel); private {
		return owner != "" && owner == user
	}
	return true
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
var privateNameRE = regexp.MustCompile(`(^|[^\w./-])(` + privateDir + `/[\w./-]*[\w/])`)

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
