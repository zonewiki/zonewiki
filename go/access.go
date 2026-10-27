// Who may see and change what (docs/spec/users.md): with users, everything
// is shared but private/<user>/, which only that user sees; anything else
// under private/ (a page in private/ itself, a folder of no user) is seen
// by no one. Without users everyone sees everything, and private/ is an
// ordinary folder. Every route and tool that shows or changes a page asks
// here, so the rule lives in one place.
package main

import (
	"os"
	"path/filepath"
	"strings"
)

// privateDir is the folder of the users' private folders.
const privateDir = "private"

// ownerIn: a path inside a user's folder below folder (slash-separated,
// relative; "private/alice/x"): (that user, true); below folder but in no
// user's folder ("private/carol/x", "private/alice" itself): ("", true);
// elsewhere: ("", false). Without users nothing is private.
func ownerIn(folder, rel string) (owner string, private bool) {
	if cfg.Users == nil {
		return "", false
	}
	rest, found := strings.CutPrefix(strings.ToLower(rel), folder+"/")
	if !found {
		return "", false
	}
	name, _, inside := strings.Cut(rest, "/")
	if !inside || cfg.Users[name] == nil {
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

// canSeePath: user may see the file rel (relative to the wiki, slash-
// separated), as git names it in the history: a page by its slug, an
// upload in _assets/private/<user>/, a deck file in _deck/users/<user>/,
// anything else in private/ like a page there.
func canSeePath(user, rel string) bool {
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
	if cfg.Users == nil {
		return nil
	}
	entries, _ := os.ReadDir(filepath.Join(cfg.Data, privateDir))
	var stray []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		if !e.IsDir() || cfg.Users[strings.ToLower(e.Name())] == nil {
			stray = append(stray, privateDir+"/"+e.Name())
		}
	}
	return stray
}
