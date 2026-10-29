// The private home (docs/spec/users.md, Private pages): with users, each
// user's private folder has an index page, private/<user>/index, the hub of
// their own sub-wiki. /private opens it, making it first if it is missing.
package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// privateHomeText is what a new private home holds.
const privateHomeText = "# Private\n\nPages in your private folder are hidden from the other users of this wiki.\n\n## Links\n\n"

// privateHomeSlug: user's private home ("private/alice/index"), "" without users.
func privateHomeSlug(user string) string {
	if folder := privateFolder(user); folder != "" {
		return folder + "/index"
	}
	return ""
}

// isPrivateHome: slug is some user's private home (never renamed or deleted,
// like the wiki's own home). Without users private/ is an ordinary folder.
func isPrivateHome(slug string) bool {
	owner, _ := pageOwner(slug)
	return owner != "" && slug == privateHomeSlug(owner)
}

// ensurePrivateHome makes user's private home when it is missing (committed
// in their name); returns its slug.
func ensurePrivateHome(user string) (string, error) {
	slug := privateHomeSlug(user)
	if slug == "" {
		return "", nil
	}
	p := safePath(slug)
	if p == "" || exists(p) {
		return slug, nil
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		return "", err
	}
	if err := writeText(p, privateHomeText); err != nil {
		return "", err
	}
	gitCommit(user, "Create "+slug, p)
	return slug, nil
}

// privateRoute: GET /private opens the signed-in user's private home; 404
// without users (there is nothing private then).
func privateRoute(w http.ResponseWriter, r *http.Request) {
	slug, err := ensurePrivateHome(userOf(r))
	if slug == "" && err == nil {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		serverError(w, r, err)
		return
	}
	http.Redirect(w, r, pathURL("page", slug), http.StatusFound)
}

// privacyMove: where Make private / Make shared would move slug for user: a
// shared page into their folder (toPrivate), their own private page out of
// it (the same path without the folder). ok false: not offered (no users,
// another user's page, a home page, a scratch note, which Move to wiki takes
// out of the folder).
func privacyMove(user, slug string) (target string, toPrivate, ok bool) {
	folder := privateFolder(user)
	if folder == "" || isProtected(slug) || isScratch(slug) || !canWrite(user, slug) {
		return "", false, false
	}
	if _, private := pageOwner(slug); private {
		rest := strings.TrimPrefix(slug, folder+"/")
		return rest, false, rest != slug && rest != ""
	}
	return folder + "/" + slug, true, true
}

// sharedNewOffer: a new page opened from a link on one of the user's private
// pages (?from=private) that would be shared: the private slug it can be
// made as instead, "" when there is nothing to offer (not from a private
// page, no users, the page is private already, or that private page exists).
func sharedNewOffer(user, slug string, fromPrivate bool) string {
	folder := privateFolder(user)
	if folder == "" || !fromPrivate {
		return ""
	}
	if _, private := pageOwner(slug); private {
		return ""
	}
	target := folder + "/" + slug
	if p := safePath(target); p == "" || exists(p) {
		return ""
	}
	return target
}
