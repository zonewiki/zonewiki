// The private home (docs/spec/users.md, Private pages): with users, each
// user's private folder has an index page, private/<user>/index, the hub of
// their own sub-wiki. /private opens it, making it first if it is missing.
package main

import (
	"bytes"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"zonewiki/internal/mdtext"
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
	return slug, notCommitted(gitCommit(user, "Create "+slug, p))
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

// sharedMove: where Make shared would move slug for user: their own private
// page out of their folder (the same path without it). ok false: not offered
// (no users, not their own private page, a private home, a note,
// which Move to wiki takes out of the folder). There is no Make private for
// a shared page: others have seen it, and its links on shared pages would
// have to go; a new page is made private from the start (sharedNewOffer).
func sharedMove(user, slug string) (target string, ok bool) {
	folder := privateFolder(user)
	if folder == "" || isProtected(slug) || isQuicknote(slug) || !canWrite(user, slug) {
		return "", false
	}
	if _, private := pageOwner(slug); !private {
		return "", false
	}
	rest := strings.TrimPrefix(slug, folder+"/")
	return rest, rest != slug && rest != ""
}

// sharedNewOffer: a new page opened from a link on one of the user's private
// pages (?from=private) that would be shared: the private slug a link could
// name instead (the editor's note says so), "" when there is nothing to offer (not from a private
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

// ownUploadRE: a link to a user's private upload (uploadURL), in a page's
// text: group 1 the owner, group 2 the file.
var ownUploadRE = regexp.MustCompile(`/` + privateDir + `/([a-z0-9-]+)/_assets/([^\s)"'<>/]+)`)

// shareUploads: oldSlug moved to newSlug (newPath). A page taken out of a
// user's private folder takes the private uploads it shows with it, since
// the others can't open them: each is copied to _assets/ (the user's other
// pages keep theirs) and the page points at the copy. A name taken by a
// different file there gets a number. Returns the files written, for the
// move's commit.
func shareUploads(oldSlug, newSlug, newPath string) []string {
	owner, _ := pageOwner(oldSlug)
	if newOwner, _ := pageOwner(newSlug); owner == "" || newOwner == owner {
		return nil
	}
	text, err := readText(newPath)
	if err != nil {
		return nil
	}
	var written []string
	masked, stash := mdtext.MaskCode(text)
	masked = ownUploadRE.ReplaceAllStringFunc(masked, func(m string) string {
		sub := ownUploadRE.FindStringSubmatch(m)
		if sub[1] != owner {
			return m
		}
		src := filepath.Join(uploadDir(owner), sub[2])
		data, err := os.ReadFile(src)
		if err != nil {
			return m
		}
		name, ext := sub[2], ""
		if i := strings.LastIndex(name, "."); i > 0 {
			name, ext = name[:i], name[i:]
		}
		dest := filepath.Join(uploadDir(""), sub[2])
		for n := 2; ; n++ {
			if have, err := os.ReadFile(dest); err != nil || bytes.Equal(have, data) {
				break
			}
			dest = filepath.Join(uploadDir(""), name+"-"+strconv.Itoa(n)+ext)
		}
		if !exists(dest) {
			if os.MkdirAll(filepath.Dir(dest), 0o777) != nil || os.WriteFile(dest, data, 0o666) != nil {
				return m
			}
			written = append(written, dest)
		}
		return uploadURL("", filepath.Base(dest))
	})
	if len(written) > 0 || masked != text {
		if n := mdtext.UnmaskCode(masked, stash); n != text && writeText(newPath, n) == nil {
			written = append(written, newPath)
		}
	}
	return written
}
