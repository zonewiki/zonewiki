// Who may see and change what (docs/spec/users.md): with users, everything
// is shared but private/<user>/, which only that user sees; anything else
// under private/ (a page in private/ itself, a folder of no user) is seen
// by no one. Without users everyone sees everything, and private/ is an
// ordinary folder. Every route and tool that shows or changes a page asks
// here, so the rule lives in one place.
package main

import (
	"io/fs"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/mdtext"
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
	if _, private := pageOwner(slug); private {
		return false
	}
	return guidePage(slug)
}

// guidePage: slug is a guide: the wiki's (wiki-guide), a folder's
// (AGENTS.md, CLAUDE.md), or a page one of those pulls in with a line
// "@other.md" (guideText), there or not yet.
func guidePage(slug string) bool {
	clean := path.Clean(slug)
	if clean == guideSlug || guideFiles[strings.ToLower(path.Base(clean))+".md"] {
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
// viewer changes nothing, anyone else no guide.
func readOnlyWhy(user string) string {
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
var errReadOnly = &apiError{http.StatusForbidden, readOnlyMsg}

// canSeeTask: user may see the task t: a shared one, or one of their own
// private ones.
func canSeeTask(user string, t *Task) bool { return t.Owner == "" || t.Owner == user }

// mayWriteTasks: user may write the task files: anyone but a viewer. Every
// task write passes it (writeOpenLocked, writeBothLocked), so no route or
// tool changes a task for a viewer, whatever it does.
func mayWriteTasks(user string) error {
	if settings().isViewer(user) {
		return errReadOnly
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
