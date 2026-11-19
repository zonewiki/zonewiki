// The private home (docs/spec/users.md, Private pages): with users, each
// user's private folder has an index page, private/<user>/index, the hub of
// their own sub-wiki. /private opens it, making it first if it is missing.
package main

import (
	"bytes"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"zonewiki/internal/mdtext"
)

// privateHomeText is what a new private home holds.
const privateHomeText = "# Private\n\nPages in your private folder are hidden from the other users of this wiki.\n\n## Links\n\n"

// privateHomeSlug: user's private home ("private/alice/index"), "" without users.
func privateHomeSlug(user asker) string {
	if folder := privateFolder(user); folder != "" {
		return folder + "/index"
	}
	return ""
}

// isPrivateHome: slug is some user's private home (never renamed or deleted,
// like the wiki's own home). Without users private/ is an ordinary folder.
func isPrivateHome(slug string) bool {
	owner := placeOf(slug).user()
	return owner != "" && slug == privateHomeSlug(asUser(owner))
}

// ensurePrivateHome makes user's private home when it is missing (committed
// in their name); returns its slug.
func ensurePrivateHome(user asker) (string, error) {
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
	return slug, notCommitted(gitCommit(user.Name, "Create "+slug, p))
}

// privateRoute: GET /private opens the signed-in user's private home; 404
// without users (there is nothing private then). Only a visit that makes
// the home takes pagesMu, as a page write: the others don't wait for one.
func privateRoute(w http.ResponseWriter, r *http.Request) {
	user := userOf(r)
	if changesNothing(user) {
		http.NotFound(w, r) // a viewer makes nothing, so has no private home
		return
	}
	if slug := privateHomeSlug(user); slug != "" && safePath(slug) != "" && exists(safePath(slug)) {
		http.Redirect(w, r, pathURL("page", slug), http.StatusFound)
		return
	}
	pagesMu.Lock()
	slug, err := ensurePrivateHome(user)
	pagesMu.Unlock()
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

// groupHomeText is what a new group's home holds.
func groupHomeText(group string) string {
	return "# " + group + "\n\nPages in this folder are seen and changed by whoever is in the group @" + group +
		", and by no one else in this wiki.\n\n## Links\n\n"
}

// groupHomeSlug: the home of a group's folder ("groups/dev/index").
func groupHomeSlug(group string) string { return groupsDir + "/" + group + "/index" }

// isGroupHome: slug is a group's home (never renamed or deleted, like the
// wiki's own home and a private one). Without users groups/ is an ordinary
// folder.
func isGroupHome(slug string) bool {
	g := placeOf(slug).group()
	return g != "" && strings.EqualFold(path.Clean(slug), groupHomeSlug(g))
}

// groupRoute: GET /groups/<group> opens the group's home, groups/<group>/
// index, and makes it on the first visit of someone in the group who may
// write (committed in their name); 404 to anyone who isn't in the group,
// as for a group that isn't, and without users. Only a visit that makes
// the home takes pagesMu, as a page write.
func groupRoute(w http.ResponseWriter, r *http.Request) {
	user, group := userOf(r), r.PathValue("group")
	slug := groupHomeSlug(group)
	if p := placeOf(slug); p.group() != group || !p.sees(user) {
		http.NotFound(w, r)
		return
	}
	file := safePath(slug)
	if file == "" || !exists(file) {
		if !canWrite(user, slug) {
			http.NotFound(w, r) // a viewer makes nothing: no home until someone else made it
			return
		}
		pagesMu.Lock()
		var err error
		if !exists(file) {
			if err = os.MkdirAll(filepath.Dir(file), 0o777); err == nil {
				if err = writeText(file, groupHomeText(group)); err == nil {
					err = notCommitted(gitCommit(user.Name, "Create "+slug, file))
				}
			}
		}
		pagesMu.Unlock()
		if err != nil {
			serverError(w, r, err)
			return
		}
	}
	http.Redirect(w, r, pathURL("page", slug), http.StatusFound)
}

// pagesIn: the pages in folder the request may see.
func pagesIn(rq *Req, folder string) []PageFile {
	var out []PageFile
	for _, p := range rq.pageFiles() {
		if strings.HasPrefix(p.Slug, folder+"/") && !hiddenPage(rq, p.Slug) {
			out = append(out, p)
		}
	}
	return out
}

// placeMark: the mark a page in a place that isn't everyone's carries beside
// its name, and what it says: 🔒 for the user's own private page, 👥 and
// the group for a group's ("@dev: anna, bo and carol see this page"; a large
// group is said by its number).
func placeMark(p place) (icon, title string) {
	switch p.kind {
	case ofUser:
		return "🔒", "Private: only you see this page"
	case ofGroup:
		return "👥", "@" + p.name + ": " + groupWho(p.name) + " this page"
	}
	return "", ""
}

// groupTitle: what the 👥 beside the title of a group's page says ("" for
// any other page).
func groupTitle(slug string) string {
	if p := placeOf(slug); p.kind == ofGroup {
		_, title := placeMark(p)
		return title
	}
	return ""
}

// groupWho: who is in a group, as a sentence's start: "anna, bo and carol
// see", "anna sees", "14 people (dev, design) see", "nobody sees".
func groupWho(group string) string {
	s := settings()
	who := s.groupMembers(group)
	switch {
	case len(who) == 0:
		return "nobody sees"
	case len(who) == 1:
		return who[0] + " sees"
	case len(who) > 8:
		made := ""
		if in := s.Includes[group]; len(in) > 0 {
			made = " (" + strings.Join(in, ", ") + ")"
		}
		return itoa(len(who)) + " people" + made + " see"
	}
	return strings.Join(who[:len(who)-1], ", ") + " and " + who[len(who)-1] + " see"
}

// shareTarget: a place a page can be shared to, as the page menu's dialog
// offers it: the name it would have there, and who sees it then.
type shareTarget struct {
	Slug  string   // where the page goes ("groups/dev/notes/x", "notes/x")
	Group string   // the group it is then for; "": everyone
	Who   []string // the group's members now
}

// shareTargets: where the page menu's Share… would move slug for user, to
// more people (docs/spec/users.md: to more people, never to fewer): their
// own private page into the folder of each group they are in, a group's
// page into that of each group that includes its own, and either out to
// everyone, each time the same path below the folder. None: not offered (no
// users, a shared page, someone else's, a viewer's, a guide not theirs to
// change, a private home, or a note, which Move to wiki takes out of the
// folder). There is no way back: others have seen the page then, and its
// links on their pages would have to go; a new page is made where it
// belongs from the start (sharedNewOffer).
func shareTargets(user asker, slug string) []shareTarget {
	s := settings()
	from := placeOf(slug)
	u := s.Users[user.Name]
	if u == nil || from.folder() == "" || !from.sees(user) || isProtected(slug) || isQuicknote(slug) || !canWrite(user, slug) {
		return nil
	}
	rest := strings.TrimPrefix(slug, from.folder()+"/")
	if rest == slug || rest == "" {
		return nil
	}
	var out []shareTarget
	offer := func(t shareTarget) {
		// Not onto a page that is there, nor where they may not write (a guide's name).
		if p := safePath(t.Slug); p != "" && !exists(p) && canWrite(user, t.Slug) {
			out = append(out, t)
		}
	}
	for _, g := range groupsFor(user) {
		if to := (place{ofGroup, g}); to != from && from.within(to) {
			offer(shareTarget{to.folder() + "/" + rest, g, s.groupMembers(g)})
		}
	}
	offer(shareTarget{Slug: rest})
	return out
}

// newFrom: how a link to a new page says where it was (?from=…): "private"
// on one of the user's private pages, "group:<name>" on a group's.
func newFrom(source place) string {
	if g := source.group(); g != "" {
		return "group:" + g
	}
	return "private"
}

// newFromRE: a ?from= a link to a new page carries.
var newFromRE = regexp.MustCompile(`^(?:private|group:[a-z0-9][a-z0-9-]{0,39})$`)

// sharedNewOffer: a new page opened from a link on one of the user's private
// pages (?from=private) or on a page of a group they are in
// (?from=group:<name>) that would be shared: the slug in that folder a link
// could name instead (the editor's note says so), and the group it would be
// for ("" for the user's own). "" when there is nothing to offer (not from
// such a page, no users, the page isn't a shared one, or that page exists).
func sharedNewOffer(user asker, slug, from string) (target, group string) {
	folder := ""
	if g, ok := strings.CutPrefix(from, "group:"); ok && newFromRE.MatchString(from) && inGroup(user, g) {
		folder, group = groupsDir+"/"+g, g
	} else if from == "private" {
		folder = privateFolder(user)
	}
	if folder == "" || !placeOf(slug).shared() {
		return "", ""
	}
	target = folder + "/" + slug
	if p := safePath(target); p == "" || exists(p) {
		return "", ""
	}
	return target, group
}

// ownUploadRE: a link to an upload kept in a user's or a group's folder
// (uploadURL), in a page's text: group 1 the folder of folders (private,
// groups), group 2 the user or group, group 3 the file.
var ownUploadRE = regexp.MustCompile(`/(` + keptDirs + `)/([a-z0-9-]+)/_assets/([^\s)"'<>/]+)`)

// shareUploads: oldSlug moved to newSlug (newPath) out of a user's private
// folder or a group's, to another place. It takes the uploads it shows with
// it, since those who see it there may not open them where they were: each
// file kept where the page was is copied to where it is now (_assets/, a
// group's or a user's; the other pages there keep theirs) and the page
// points at the copy. A file goes only where the page goes, never to more
// people: a page taken back into a private folder or into a group's (a move
// to fewer, by an admin or whoever shared it) takes its files there too.
// A name taken by a different file there gets a number. Returns the files
// written, for the move's commit.
func shareUploads(oldSlug, newSlug, newPath string) []string {
	from, to := placeOf(oldSlug), placeOf(newSlug)
	if from.folder() == "" || to == from || to.kind == ofNoOne {
		return nil // a place no one sees: the files stay where they are, seen by no more people than before
	}
	text, err := readText(newPath)
	if err != nil {
		return nil
	}
	var written []string
	masked, stash := mdtext.MaskCode(text)
	masked = ownUploadRE.ReplaceAllStringFunc(masked, func(m string) string {
		sub := ownUploadRE.FindStringSubmatch(m)
		if sub[1]+"/"+sub[2] != from.folder() {
			return m
		}
		file, err := url.PathUnescape(sub[3]) // the link has the name percent-encoded (uploadURL)
		if err != nil || strings.ContainsAny(file, `/\`) {
			return m
		}
		src := filepath.Join(uploadDir(from), file)
		data, err := os.ReadFile(src)
		if err != nil {
			return m
		}
		name, ext := file, ""
		if i := strings.LastIndex(name, "."); i > 0 {
			name, ext = name[:i], name[i:]
		}
		dest := filepath.Join(uploadDir(to), file)
		for n := 2; ; n++ {
			if have, err := os.ReadFile(dest); err != nil || bytes.Equal(have, data) {
				break
			}
			dest = filepath.Join(uploadDir(to), name+"-"+strconv.Itoa(n)+ext)
		}
		if !exists(dest) {
			if os.MkdirAll(filepath.Dir(dest), 0o777) != nil || os.WriteFile(dest, data, 0o666) != nil {
				return m
			}
			written = append(written, dest)
		}
		return uploadURL(to, filepath.Base(dest))
	})
	if len(written) > 0 || masked != text {
		if n := mdtext.UnmaskCode(masked, stash); n != text && writeText(newPath, n) == nil {
			written = append(written, newPath)
		}
	}
	return written
}
