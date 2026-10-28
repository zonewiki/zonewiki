// Who may see and change what (docs/spec/users.md): with users, everything
// is shared but private/<user>/, which only that user sees; anything else
// under private/ (a page in private/ itself, a folder of no user) is seen
// by no one. Without users everyone sees everything, and private/ is an
// ordinary folder. Every route and tool that shows or changes a page asks
// here, so the rule lives in one place.
package main

import (
	"os"
	"path"
	"path/filepath"
	"regexp"
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

// canWrite: user may change (create, edit, rename, delete) the page slug;
// the same pages they may see, since everything shared is editable.
func canWrite(user, slug string) bool { return canSee(user, slug) }

// canSeeTask: user may see (and change) the task t: a shared one, or one
// of their own private ones.
func canSeeTask(user string, t *Task) bool { return t.Owner == "" || t.Owner == user }

// mayMakePrivate: user may make the task t their own private one: it is
// already, or it is shared and no one else has worked on it (every history
// event and comment that names someone names user; one that names no one,
// from before users or by Leaf, counts as anyone's). Making a task private
// takes it from everyone else, so not one someone else made or commented on.
func mayMakePrivate(user string, t *Task) bool {
	if privateFolder(user) == "" {
		return false
	}
	if t.Owner != "" {
		return t.Owner == user
	}
	for _, e := range t.History {
		if e.By != "" && e.By != user {
			return false
		}
	}
	for _, c := range t.Comments {
		if c.By != "" && c.By != user {
			return false
		}
	}
	return true
}

// ownComment: user may edit or delete the comment c: their own, or one
// that names no one (from before users, or without users: everyone's).
func ownComment(user string, c Comment) bool { return c.By == "" || c.By == user }

// ownPrivate: slug is a page in user's own private folder (where a new
// task defaults to private).
func ownPrivate(user, slug string) bool {
	owner, _ := pageOwner(slug)
	return owner != "" && owner == user
}

// privateFolder: user's private folder ("private/alice"), "" without
// users (or for Leaf itself, which has no user).
func privateFolder(user string) string {
	if settings().Users == nil || user == "" {
		return ""
	}
	return privateDir + "/" + user
}

// canSeePath: user may see the file rel (relative to the wiki, slash-
// separated), as git names it in the history: a page by its slug, an
// upload in _assets/private/<user>/, a deck file in _deck/users/<user>/,
// anything else in private/ like a page there. Leaf's own files in .leaf/
// (the settings file, with its password hashes) no one sees in Leaf.
func canSeePath(user, rel string) bool {
	if strings.HasPrefix(rel, ".leaf/") {
		return false
	}
	for _, folder := range []string{"_assets/" + privateDir, "_deck/users", privateDir} {
		if owner, private := ownerIn(folder, rel); private {
			return owner != "" && owner == user
		}
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

// privateNameRE: a page's or file's path in private/ (an upload's in
// _assets/private/) as a commit message names it ("Move private/alice/
// 2026-…_idea into the wiki as ideas/x"), at the start of a path, not
// inside one ("notprivate/x", "notes/private/x"): group 2, after group 1.
var privateNameRE = regexp.MustCompile(`(^|[^\w./-])((?:_assets/)?` + privateDir + `/[\w./-]*[\w/])`)

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
