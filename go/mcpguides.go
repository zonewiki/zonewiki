// The MCP tools' guides: the wiki's guide and a folder's AGENTS.md or CLAUDE.md,
// which a write must have read (their ids as guides), and read_guides.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
)

// ── Guides ──────────────────────────────────────────────────────────────────
// Folder guides: the user's instructions for the pages in a folder and its
// subfolders, in the files coding assistants read for that (CLAUDE.md,
// AGENTS.md, any case), and for the whole wiki also the page wiki-guide. A
// page write is refused until the model passes the ids of the guides that
// apply; the refusal gives their text and ids. So the model has read them
// before it writes, without the server keeping sessions, and a guide costs
// its tokens once per conversation. An id holds a hash of the guide's text:
// when the user changes a guide, the old id no longer counts.

// guideSlug: the user's guide for keeping this wiki, a page like any other.
// Its text is part of the instructions a client gets when it connects.
const guideSlug = "wiki-guide"

var guideFiles = map[string]bool{"agents.md": true, "claude.md": true}

// A line "@other.md" in a guide stands for that file's text (Claude Code's
// import), relative to the guide's folder, nested this deep at most.
var importRE = regexp.MustCompile(`^@(` + mdtext.NonSpaceClass + `+\.md)$`)

const importDepth = 3

// Longer guides are cut, with a pointer to read_page: in the instructions
// (guideChars), in a refusal or read_guides (folderGuideChars). And the
// folder list in read_guides' description stops after guidedShown, so it
// stays short (clients cut long ones). Variables, for the tests.
var (
	guideChars       = 8000
	folderGuideChars = 8000
	guidedShown      = 25
	backlinksShown   = 50 // how many linking pages read_page names
)

// guideText: a guide file's text with its imports in place. Files in seen
// (already part of another guide) are left out; an import that isn't a page
// of the wiki, or one user may not see, stays as written.
func guideText(path string, user asker, seen map[string]bool, depth int) string {
	seen[path] = true
	text, err := readPageText(path)
	if err != nil {
		return ""
	}
	root := resolvePath(cfg.Data)
	var out []string
	for _, line := range mdtext.SplitLines(text) {
		line = mdtext.LineBody(line)
		if m := importRE.FindStringSubmatch(strings.TrimSpace(line)); m != nil && depth < importDepth {
			target := resolvePath(filepath.Join(filepath.Dir(path), m[1]))
			if rel, ok := relInside(root, target); ok {
				if st, err := os.Stat(target); err == nil && st.Mode().IsRegular() &&
					safePath(strings.TrimSuffix(filepath.ToSlash(rel), ".md")) == target &&
					canSeePath(user, filepath.ToSlash(rel)) {
					if !seen[target] {
						out = append(out, guideText(target, user, seen, depth+1))
					}
					continue
				}
			}
		}
		out = append(out, line)
	}
	return strings.TrimSpace(strings.Join(out, "\n"))
}

type guide struct{ id, slug, text string }

// folderGuides: the guides for a page at slug: the wiki's first, then each
// folder's on the way down to the page's. Those user may see: not the
// guide in another user's private folder.
func folderGuides(slug string, user asker) []guide {
	root := resolvePath(cfg.Data)
	parts := strings.Split(slug, "/")
	parts = parts[:len(parts)-1]
	seen := map[string]bool{}
	var found []guide
	for depth := 0; depth <= len(parts); depth++ {
		folder := filepath.Join(append([]string{root}, parts[:depth]...)...)
		entries, err := os.ReadDir(folder)
		if err != nil {
			break // a folder the write creates: no guides below
		}
		var files []string
		if depth == 0 {
			files = append(files, filepath.Join(root, guideSlug+".md"))
		}
		for _, e := range entries {
			p := filepath.Join(folder, e.Name())
			if st, err := os.Stat(p); err == nil && st.Mode().IsRegular() && guideFiles[strings.ToLower(e.Name())] {
				files = append(files, p)
			}
		}
		for _, p := range files {
			p = resolvePath(p)
			rel, ok := relInside(root, p)
			if !ok {
				continue // a link to a file outside the wiki
			}
			if seen[p] || !canSeePath(user, filepath.ToSlash(rel)) {
				continue
			}
			if text := guideText(p, user, seen, 0); text != "" {
				sum := sha256.Sum256([]byte(text))
				g := strings.TrimSuffix(filepath.ToSlash(rel), filepath.Ext(rel))
				found = append(found, guide{g + ":" + hex.EncodeToString(sum[:])[:8], g, text})
			}
		}
	}
	return found
}

func cutGuide(g guide) string {
	return cutRunes(g.text, folderGuideChars, "\n\n[… cut here: read_page "+strconv.Quote(g.slug)+" for the rest]")
}

// checkGuides refuses a write to these pages while a guide for them isn't
// among the ids in args' guides: the error gives the missing guides' text.
func checkGuides(args jsonObj, slugs []string, user asker) error {
	// A page user may read but not change (a viewer's, a guide for anyone
	// but an admin) is refused here already: not after the guides were
	// read and the call sent again.
	for _, slug := range slugs {
		if readOnly(user, slug) && safePath(slug) != "" {
			return toolErr(readOnlyWhy(user, slug))
		}
	}
	given, err := idsArg(args, "guides", "guides must be a list of guide ids")
	if err != nil {
		return err
	}
	var needed []guide
	ids := map[string]bool{}
	for _, slug := range slugs {
		for _, g := range folderGuides(slug, user) {
			if !ids[g.id] {
				ids[g.id] = true
				needed = append(needed, g)
			}
		}
	}
	var missing []guide
	for _, g := range needed {
		if !given[g.id] {
			missing = append(missing, g)
		}
	}
	if len(missing) == 0 {
		return nil
	}
	them := "it"
	if len(missing) > 1 {
		them = "them"
	}
	out := []string{"Nothing was written. The user keeps guides for these pages, with instructions for writing " +
		"them, and this call didn't confirm reading " + them + ". Read below, make the change follow it, then " +
		"call again with guides set to " + jsonobj.Text(sortedKeys(ids)) + ". Pass every guide id you get in " +
		"this conversation with your later writes too; a guide that changes gets a new id."}
	for _, g := range missing {
		out = append(out, "=== guide "+g.slug+" (id "+g.id+") ===\n"+cutGuide(g))
	}
	return toolErr(strings.Join(out, "\n\n"))
}

func guideFolder(g string) string {
	if i := strings.LastIndex(g, "/"); i >= 0 {
		return g[:i]
	}
	return ""
}

// guidedFolders: the folders with a guide of their own, as (folder, the
// guide's title), the root ("") first; those user may see.
func guidedFolders(user asker) [][2]string {
	root := resolvePath(cfg.Data)
	var folders []string
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || !d.IsDir() {
			return nil
		}
		if p != root && (strings.HasPrefix(d.Name(), "_") || strings.HasPrefix(d.Name(), ".")) {
			return filepath.SkipDir
		}
		entries, _ := os.ReadDir(p)
		for _, e := range entries {
			if !e.IsDir() && (guideFiles[strings.ToLower(e.Name())] || p == root && e.Name() == guideSlug+".md") {
				rel, _ := filepath.Rel(root, p)
				folders = append(folders, strings.TrimPrefix(filepath.ToSlash(rel), "."))
				break
			}
		}
		return nil
	})
	var out [][2]string
	for _, folder := range folders {
		page := "page"
		if folder != "" {
			page = folder + "/page"
		}
		for _, g := range folderGuides(page, user) {
			if guideFolder(g.slug) == folder {
				title := cutRunes(mdtext.PageTitle(g.text, ""), 60, "")
				if title == "" {
					title = g.slug
				}
				out = append(out, [2]string{folder, title})
				break
			}
		}
	}
	return out
}

// toolList: the tools for tools/list. read_guides' description names the
// folders that have guides, so the model knows to read one before drafting
// for it. Who changes nothing (a viewer, a connection that only reads) is
// given no tool that writes.
func toolList(user asker) []map[string]any {
	all := mcpTools
	if changesNothing(user) {
		all = slices.DeleteFunc(slices.Clone(all), func(t map[string]any) bool { return writeTool(t["name"].(string)) })
	}
	folders := guidedFolders(user)
	if len(folders) == 0 {
		return all
	}
	var lines []string
	for _, f := range folders[:min(len(folders), guidedShown)] {
		where := "the whole wiki"
		if f[0] != "" {
			where = f[0] + "/"
		}
		lines = append(lines, "- "+where+": "+f[1])
	}
	if len(folders) > guidedShown {
		lines = append(lines, fmt.Sprintf("- … and %d more folders", len(folders)-guidedShown))
	}
	tools := make([]map[string]any, len(all))
	for i, t := range all {
		if t["name"] == "read_guides" {
			t = maps.Clone(t)
			t["description"] = t["description"].(string) + " Guides in this wiki:\n" + strings.Join(lines, "\n")
		}
		tools[i] = t
	}
	return tools
}

func readGuides(args jsonObj, base string, user asker) (string, error) {
	raw, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	raw = strings.TrimSuffix(slugInput(raw), ".md")
	folder := strings.Trim(raw, "/")
	// A folder is one by the pages user sees in it, never by what is on
	// disk: whether another user's private folder, a group's they aren't
	// in, or a folder outside the wiki is there is none of their business.
	if folder != "" && safePath(folder+"/page") == "" {
		return "", toolErr(strconv.Quote(folder) + " is no page or folder of this wiki")
	}
	var slug string
	switch {
	case folder == "":
		slug = "page"
	case strings.HasSuffix(raw, "/") || slices.ContainsFunc(visiblePages(user), func(p PageFile) bool {
		return strings.HasPrefix(p.Slug, folder+"/")
	}):
		slug = folder + "/page"
	default:
		if slug, err = pageOrSeenSlug(folder, user); err != nil {
			return "", err
		}
	}
	if !canSee(user, slug) {
		return "", toolErr(notYours(folder+"/", user))
	}
	known, err := idsArg(args, "guides", "guides must be a list of guide ids")
	if err != nil {
		return "", err
	}
	found := folderGuides(slug, user)
	where := "the wiki"
	if folder != "" && slug == folder+"/page" {
		where = folder + "/"
	} else if folder != "" {
		where = slug
	}
	if len(found) == 0 {
		return "No guides apply to " + where + ": write as the conversation and the wiki suggest.", nil
	}
	ids := make([]any, len(found))
	for i, g := range found {
		ids[i] = g.id
	}
	out := []string{"Guides for " + where + ", the whole wiki's first. Follow them; where they disagree, the more " +
		"specific one wins. Pass guides=" + jsonobj.Text(ids) + " with your writes."}
	for _, g := range found {
		if known[g.id] {
			out = append(out, "=== guide "+g.slug+" (id "+g.id+"): you have it ===")
		} else {
			out = append(out, "=== guide "+g.slug+" (id "+g.id+") ===\n"+cutGuide(g))
		}
	}
	return strings.Join(out, "\n\n"), nil
}

// guideIDLine: for read_page of a whole guide, its id, as reading it counts
// as reading the guide. Not when it imports other pages, which the model
// hasn't seen then.
func guideIDLine(slug, content string, user asker) string {
	for _, g := range folderGuides(slug, user) {
		if g.slug == slug {
			if g.text != strings.TrimSpace(content) {
				return "guide: yes; it imports other pages, read_guides shows it whole\n"
			}
			under := guideFolder(slug)
			if under == "" {
				under = "the wiki"
			}
			return "guide id: " + g.id + " (pass it as guides with writes under " + under + ")\n"
		}
	}
	return ""
}

// guideNote: after a write to a guide, its new id, which the next writes pass.
func guideNote(slug string, user asker) string {
	for _, g := range folderGuides(slug, user) {
		if g.slug == slug {
			return "\nNote: " + slug + " is a guide; its id is now " + g.id + ": pass that one from now on."
		}
	}
	return ""
}

// instructionsWithGuide: the server's instructions for initialize, plus the
// wiki's guide, or, without one, a nudge to start it.
func instructionsWithGuide(user asker) string {
	return instructionsFor(mcpInstructions + groupsNote(user))
}

// groupsNote: for a user who is in a group, where its pages are and who
// sees them, said with the instructions: an assistant that files a page
// must know that the folder decides who reads it.
func groupsNote(user asker) string {
	in := groupsFor(user)
	if len(in) == 0 {
		return ""
	}
	folders := make([]string, len(in))
	for i, g := range in {
		folders[i] = groupsDir + "/" + g + "/"
	}
	return "\n\nWhere a page is kept is who sees it: " + privateDir + "/" + user.Name + "/ only this user, " +
		strings.Join(folders, ", ") + " the people in that group, anywhere else everyone who uses the wiki. " +
		"File what is meant for a group in its folder, and don't copy from a narrower place into a wider one unless the user says so; " +
		"a page moves to more people; back to fewer only by an admin, or by whoever shared it while nobody else has changed it."
}

// instructionsFor: base (the server's instructions, with what holds for
// this user) and the wiki's guide, or the nudge to start one.
func instructionsFor(base string) string {
	guide := ""
	if p := safePath(guideSlug); p != "" {
		if text, err := readPageText(p); err == nil {
			guide = strings.TrimSpace(text)
		}
	}
	if guide == "" {
		return base + "\n\nThis wiki has no guide yet. When the user tells you " +
			"how they want it kept (where things go, which tags, page layouts, what stays " +
			"private), offer to write that down on the page 'wiki-guide': it is shown to " +
			"you at the start of every session."
	}
	guide = cutRunes(guide, guideChars, "\n\n[… cut here: read_page \"wiki-guide\" for the rest]")
	// Some clients cut the instructions (Claude Code at about 2 KB), and so
	// the guide; the instructions point to read_guides, which gives it whole.
	return base + "\n\nThe user's guide for this wiki (the page 'wiki-guide'; " +
		"read_guides gives it too if this is cut). Follow it; where it differs from the advice " +
		"above, it wins. When the user settles a new convention, offer to add it there.\n\n" + guide
}
