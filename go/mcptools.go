// The MCP tools: reading (search_wiki, search_tasks,
// grep_wiki, read_page, list_pages, check_links, list_tags, history,
// zone_help, read_guides) and writing pages (write_page (also a
// note), edit_page, replace_in_pages, append_to_page, rename_page (also a
// folder), delete_page, restore_page, file_note) and tasks
// (create_task, update_task: also comments, deletes and tags on several
// tasks). The protocol is in mcp.go.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/textsearch"
)

// ── Arguments ───────────────────────────────────────────────────────────────

// cleanFolder: a folder argument as a slug prefix: "/page/tickets/",
// "tickets/" and "tickets" all become "tickets". "" means the whole wiki.
func cleanFolder(s string) string {
	s = strings.TrimSpace(s)
	if _, after, found := strings.Cut(s, "/page/"); found {
		s = after
	}
	return strings.Trim(s, "/")
}

func underArg(args jsonObj) (string, error) {
	s, err := strArg(args, "under", false)
	return cleanFolder(s), err
}

// underWhere: " under <folder>/" for an answer's text, "" for the whole wiki.
func underWhere(under string) string {
	if under == "" {
		return ""
	}
	return " under " + under + "/"
}

var digitsRE = regexp.MustCompile(`^\d+$`)

// mcpInt: a whole-number argument (a string of digits too), clamped to 1…top.
func mcpInt(args jsonObj, name string, def, top int) (int, error) {
	if !args.has(name) {
		return def, nil
	}
	n, ok := wholeNumber(args[name])
	if s, isStr := as[string](args[name]); isStr && digitsRE.MatchString(strings.TrimSpace(s)) {
		i, err := strconv.Atoi(strings.TrimSpace(s))
		n, ok = i, err == nil
	}
	if !ok {
		return 0, toolErr(name + " must be a whole number")
	}
	return max(1, min(n, top)), nil
}

// strArg: a string argument with CRLF line ends as LF; a required one
// non-empty. Missing or null is "".
func strArg(args jsonObj, name string, required bool) (string, error) {
	s, ok := as[string](args[name])
	if !ok && !args.null(name) {
		return "", toolErr(name + " must be a string")
	}
	s = newlines(s)
	if required && strings.TrimSpace(s) == "" {
		return "", toolErr(name + " must be a non-empty string")
	}
	return s, nil
}

// renamedBoolArg: boolArg by name, or by old, what it was called before
// (an assistant may still send that).
func renamedBoolArg(args jsonObj, name, old string) (bool, error) {
	if args.null(name) && !args.null(old) {
		name = old
	}
	return boolArg(args, name)
}

// boolArg: true or false; missing or null is false.
func boolArg(args jsonObj, name string) (bool, error) {
	b, ok := as[bool](args[name])
	if !ok && !args.null(name) {
		return false, toolErr(name + " must be true or false")
	}
	return b, nil
}

// listArg: a list of strings, or one string split by split; missing or
// null is none.
func listArg(args jsonObj, name, msg string, split func(string) []string) ([]string, error) {
	if args.null(name) {
		return nil, nil
	}
	if s, ok := as[string](args[name]); ok {
		return split(s), nil
	}
	l, ok := as[[]json.RawMessage](args[name])
	out := make([]string, len(l))
	for i, x := range l {
		if out[i], ok = as[string](x); !ok {
			break
		}
	}
	if !ok {
		return nil, toolErr(msg)
	}
	return out, nil
}

// idsArg: a list of ids (guide ids), a comma- or space-separated string too.
func idsArg(args jsonObj, name, msg string) (map[string]bool, error) {
	l, err := listArg(args, name, msg, func(s string) []string { return strings.Fields(strings.ReplaceAll(s, ",", " ")) })
	set := map[string]bool{}
	for _, s := range l {
		set[strings.TrimSpace(s)] = true
	}
	return set, err
}

// cutRunes: s to its first n characters, with tail when it was longer.
func cutRunes(s string, n int, tail string) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n]) + tail
}

func plural(n int, word string) string {
	if n > 1 {
		return word + "s"
	}
	return word
}

// ── Pages ───────────────────────────────────────────────────────────────────

// mcpPageSlug: the existing page a slug-ish string names (a slug,
// "/page/<slug>", a full URL, "<slug>.md", or the page's name, "Git
// Remote"); "" when none, or when user may not see it.
func mcpPageSlug(raw, user string) string {
	raw = strings.TrimSuffix(strings.Trim(slugInput(raw), "/"), ".md")
	for _, slug := range []string{raw, mdtext.ToSlug(raw)} {
		if slug == "" {
			continue
		}
		if p := safePath(slug); p != "" {
			if st, err := os.Stat(p); err == nil && st.Mode().IsRegular() && canSee(user, slug) {
				return slug
			}
		}
	}
	return ""
}

// slugInput: a page argument without what may come with a slug: the
// address up to /page/ and an #anchor.
func slugInput(raw string) string {
	raw = strings.TrimSpace(raw)
	if _, after, found := strings.Cut(raw, "/page/"); found {
		raw = after
	}
	raw, _, _ = strings.Cut(raw, "#")
	return raw
}

// hostFile: a page's file as the host sees it, so a client on the host can
// open or edit it: the host's wiki folder (ZONE_HOST_DATA) plus the slug,
// which is the file's path relative to the wiki.
func hostFile(slug string) string {
	root := cfg.HostData
	if root == "" {
		root = resolvePath(cfg.Data)
	}
	return root + "/" + slug + ".md"
}

func pageInfo(slug, base string) string {
	return "url: " + base + pathURL("page", slug) + "\nfile: " + hostFile(slug)
}

// readPageText: a page's text, undecodable bytes as U+FFFD.
func readPageText(path string) (string, error) {
	text, err := readText(path)
	return strings.ToValidUTF8(text, "�"), err
}

func existingPage(args jsonObj, name, user string) (string, string, error) {
	raw, err := strArg(args, name, true)
	if err != nil {
		return "", "", err
	}
	slug := mcpPageSlug(raw, user)
	if slug == "" {
		return "", "", toolErr("No page " + strconv.Quote(raw) + ". Use search_wiki or list_pages to find its slug.")
	}
	return slug, safePath(slug), nil
}

// newSlug: the slug a new page gets from a name or slug ("Tickets/BUG
// 1234" → "tickets/bug-1234"), refused when it would be outside the wiki or
// in a folder Zone skips (_assets, _tasks, .git …), or in private/ outside
// user's own folder.
func newSlug(raw, user string) (string, error) {
	raw = strings.TrimSuffix(strings.Trim(slugInput(raw), "/"), ".md")
	slug := mdtext.ToSlug(raw)
	if slug == "" || slices.Contains(strings.Split(slug, "/"), "") {
		return "", toolErr(strconv.Quote(raw) + " is not a usable page name")
	}
	if safePath(slug) == "" {
		return "", toolErr(strconv.Quote(slug) + ": pages can't go in folders starting with '_' or '.'")
	}
	if !canWrite(user, slug) {
		return "", toolErr(notYours(slug, user))
	}
	return slug, nil
}

// missingLinks: the links in content, a page at slug, that lead to no
// page: the [[wikilinks]] (as rendering decides it: the name as a slug,
// else slugified) and the markdown links (mdLinkMisses). Each link as
// written → the name or slug it was meant for, to suggest a near match.
func missingLinks(content string, slugs map[string]bool, slug, user string) map[string]string {
	masked, _ := mdtext.MaskCode(content)
	masked = taskRefRE.ReplaceAllString(tasksRE.ReplaceAllString(masked, ""), "")
	missing := map[string]string{}
	for _, m := range mdtext.WikilinkRE.FindAllStringSubmatch(masked, -1) {
		name, _, _ := mdtext.SplitWikilink(m[1])
		if name != "" && !slugs[name] && !slugs[mdtext.ToSlug(name)] {
			missing["[["+name+"]]"] = name
		}
	}
	for k, v := range mdLinkMisses(content, slug, slugs) {
		missing[k] = v
	}
	// A link into a private folder that isn't user's is never reported, whether
	// or not the page is there: "no such page" would tell which ones exist.
	for k, v := range missing {
		if !canSee(user, mdtext.ToSlug(v)) {
			delete(missing, k)
		}
	}
	return missing
}

// newMissing: the links of missingLinks in new that old didn't have, A–Z.
func newMissing(slug, old, new, user string, slugs map[string]bool) []string {
	before := missingLinks(old, slugs, slug, user)
	var added []string
	for k := range missingLinks(new, slugs, slug, user) {
		if _, ok := before[k]; !ok {
			added = append(added, k)
		}
	}
	sort.Strings(added)
	return added
}

// linkWarning: a note on the links a write added that lead nowhere, so the
// model can fix a typo or create the page.
func linkWarning(slug, old, new, user string) string {
	added := newMissing(slug, old, new, user, slugSet())
	if len(added) == 0 {
		return ""
	}
	return "\nNote: these new links point to pages that don't exist (yet): " + strings.Join(added, ", ")
}

var notTitleRE = regexp.MustCompile(`[^a-z0-9]+`)

func normTitle(text string) string {
	return strings.TrimSpace(notTitleRE.ReplaceAllString(strings.ToLower(text), " "))
}

func lastPart(slug string) string { return slug[strings.LastIndex(slug, "/")+1:] }

// pageOrNewSlug: the existing page raw names, else the slug a new page of
// that name would get (a deleted page's, say).
func pageOrNewSlug(raw, user string) (string, error) {
	if slug := mcpPageSlug(raw, user); slug != "" {
		return slug, nil
	}
	return newSlug(raw, user)
}

// notYours: the refusal of a page in private/ that isn't in user's own
// folder (which says nothing of whether it exists).
func notYours(slug, user string) string {
	if own := privateFolder(user); own != "" {
		return strconv.Quote(slug) + ": of private/, only your own folder " + own + "/ is yours to read or write"
	}
	return strconv.Quote(slug) + ": private/ holds the users' private folders, which only their users see"
}

// similarPages: existing pages whose title or file name is close to a new
// page's, so the model notices it may be extending a page rather than
// starting one.
func similarPages(slug, title, user string) []PageRef {
	norm := func(names ...string) []string {
		var out []string
		for _, n := range names {
			if n = normTitle(n); n != "" {
				out = append(out, n)
			}
		}
		return out
	}
	want := norm(title, lastPart(slug))
	var found []PageRef
	for _, p := range visiblePages(user) {
		if p.Slug == slug {
			continue
		}
		otherTitle, _ := pageMeta(p.Path, p.Slug)
		close := false
		for _, b := range norm(otherTitle, lastPart(p.Slug)) {
			similar := textsearch.SimilarTo(b)
			for _, a := range want {
				close = close || similar(a) >= 0.8
			}
		}
		if close {
			found = append(found, PageRef{p.Slug, otherTitle})
		}
	}
	sort.Slice(found, func(i, j int) bool {
		if found[i].Slug != found[j].Slug {
			return found[i].Slug < found[j].Slug
		}
		return found[i].Title < found[j].Title
	})
	return found[:min(5, len(found))]
}

// ── Committing ──────────────────────────────────────────────────────────────
// Every write is its own git commit, its message ending in "(MCP)" so the
// history tells the model's changes from the user's. The model's summary,
// when given, says what changed.

func msgSuffix(args jsonObj) (string, error) {
	s, err := strArg(args, "summary", false)
	if err != nil {
		return "", err
	}
	if s = cutRunes(strings.Join(strings.Fields(s), " "), 200, ""); s != "" {
		return ": " + s + " (MCP)", nil
	}
	return " (MCP)", nil
}

func commitMsg(action string, args jsonObj) (string, error) {
	s, err := msgSuffix(args)
	return action + s, err
}

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
func guideText(path, user string, seen map[string]bool, depth int) string {
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
func folderGuides(slug, user string) []guide {
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
func checkGuides(args jsonObj, slugs []string, user string) error {
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
		"call again with guides set to " + jsonText(sortedKeys(ids)) + ". Pass every guide id you get in " +
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
func guidedFolders(user string) [][2]string {
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
// for it.
func toolList(user string) []map[string]any {
	folders := guidedFolders(user)
	if len(folders) == 0 {
		return mcpTools
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
	tools := make([]map[string]any, len(mcpTools))
	for i, t := range mcpTools {
		if t["name"] == "read_guides" {
			t = maps.Clone(t)
			t["description"] = t["description"].(string) + " Guides in this wiki:\n" + strings.Join(lines, "\n")
		}
		tools[i] = t
	}
	return tools
}

func readGuides(args jsonObj, base, user string) (string, error) {
	raw, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	raw = strings.TrimSuffix(slugInput(raw), ".md")
	folder := strings.Trim(raw, "/")
	var slug string
	switch {
	case strings.HasSuffix(raw, "/") || folder != "" && isDir(filepath.Join(cfg.Data, folder)):
		slug = folder + "/page"
	case folder == "":
		slug = "page"
	default:
		if slug, err = pageOrNewSlug(folder, user); err != nil {
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
		"specific one wins. Pass guides=" + jsonText(ids) + " with your writes."}
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
func guideIDLine(slug, content, user string) string {
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
func guideNote(slug, user string) string {
	for _, g := range folderGuides(slug, user) {
		if g.slug == slug {
			return "\nNote: " + slug + " is a guide; its id is now " + g.id + ": pass that one from now on."
		}
	}
	return ""
}

// instructionsWithGuide: the server's instructions for initialize, plus the
// wiki's guide, or, without one, a nudge to start it.
func instructionsWithGuide() string {
	guide := ""
	if p := safePath(guideSlug); p != "" {
		if text, err := readPageText(p); err == nil {
			guide = strings.TrimSpace(text)
		}
	}
	if guide == "" {
		return mcpInstructions + "\n\nThis wiki has no guide yet. When the user tells you " +
			"how they want it kept (where things go, which tags, page layouts, what stays " +
			"private), offer to write that down on the page 'wiki-guide': it is shown to " +
			"you at the start of every session."
	}
	guide = cutRunes(guide, guideChars, "\n\n[… cut here: read_page \"wiki-guide\" for the rest]")
	// Some clients cut the instructions (Claude Code at about 2 KB), and so
	// the guide; the instructions point to read_guides, which gives it whole.
	return mcpInstructions + "\n\nThe user's guide for this wiki (the page 'wiki-guide'; " +
		"read_guides gives it too if this is cut). Follow it; where it differs from the advice " +
		"above, it wins. When the user settles a new convention, offer to add it there.\n\n" + guide
}

// ── Sections ────────────────────────────────────────────────────────────────

// sectionLines: the lines of the section under heading (any level; its
// text compared without markup and case), from the heading to before the
// next heading of the same or a higher level, subsections included. The
// lines are a whole text, split with their line breaks (mdtext.SplitLines).
func sectionLines(lines []string, heading string) (int, int, bool) {
	want := strings.ToLower(mdtext.PlainHeading(strings.TrimSpace(strings.TrimLeft(heading, "#"))))
	lineAt := func(offset int) int {
		for i, l := range lines {
			if offset -= len(l); offset < 0 {
				return i
			}
		}
		return len(lines)
	}
	headings := pageHeadings(strings.Join(lines, ""))
	for i, h := range headings {
		if strings.ToLower(mdtext.PlainHeading(h.text)) != want {
			continue
		}
		for _, next := range headings[i+1:] {
			if next.level <= h.level {
				return lineAt(h.start), lineAt(next.start), true
			}
		}
		return lineAt(h.start), len(lines), true
	}
	return -1, len(lines), false
}

// noHeading: the error for a heading the page doesn't have, naming those
// it has.
func noHeading(slug, content, heading string) string {
	var names []string
	for _, h := range pageHeadings(content) {
		names = append(names, strconv.Quote(h.text))
	}
	list := strings.Join(names, ", ")
	if list == "" {
		list = "none"
	}
	return slug + " has no heading " + strconv.Quote(heading) + ". Its headings: " + list + "."
}

func trimNewlines(s string) string { return strings.TrimRight(s, "\n") }

// ── Tasks ──────────────────────────────────────────────────────────────

var recurrenceUnits = map[string]string{"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}

func shortID(id string) string { return runePrefix(id, shortIDLen) }

// mcpTaskText: one task for user's LLM: title, a line of facts
// (status, private, tags, dates, repeat, list), its link in the tasks, its
// notes and comments.
func mcpTaskText(t *Task, base, user string) string {
	status := taskStatus(t)
	fact := "status: " + status
	if status != "open" {
		fact += " " + runePrefix(t.CompletedAt, 10)
	}
	facts := []string{fact}
	if t.Owner != "" {
		facts = append(facts, "private")
	}
	if len(t.Tags) > 0 {
		var tags []string
		for _, tag := range t.Tags {
			tags = append(tags, "#"+tag)
		}
		facts = append(facts, "tags: "+strings.Join(tags, " "))
	}
	if t.StartDate != "" {
		facts = append(facts, "start: "+t.StartDate)
	}
	if t.DueDate != "" {
		facts = append(facts, "due: "+t.DueDate)
	}
	if rec := t.Recurrence; rec != nil {
		if unit, ok := recurrenceUnits[rec.Type]; ok {
			every := rec.Type
			if rec.Interval != 1 {
				every = "every " + strconv.Itoa(rec.Interval) + " " + unit + "s"
			}
			if rec.From == "completion" {
				every += " after completion"
			}
			facts = append(facts, "repeats "+every)
		}
	}
	if t.Keep {
		keep := "keep task (back to the list when done"
		if t.LastDone != "" {
			keep += ", last done " + t.LastDone
		}
		facts = append(facts, keep+")")
	}
	list := taskList(t, taskLists(user))
	if list != "" {
		facts = append(facts, "list: "+list)
	}
	text := "Task: " + t.Title + "\n" + strings.Join(facts, " · ") +
		"\nid: " + shortID(t.ID) + "\nurl: " + base + taskLink(t, list)
	if notes := strings.TrimSpace(t.Description); notes != "" {
		text += "\n\n" + notes
	}
	// Comments: the user's status lines, oldest first, each with its time
	// (and with users, who wrote it).
	var comments []string
	for _, c := range t.Comments {
		if c.Text != "" {
			at := strings.ReplaceAll(runePrefix(c.At, 16), "T", " ")
			if c.By != "" {
				at += " (" + c.By + ")"
			}
			comments = append(comments, "- "+at+": "+c.Text)
		}
	}
	if len(comments) > 0 {
		text += "\n\nComments:\n" + strings.Join(comments, "\n")
	}
	return text
}

// mcpTaskRefs: for read_page, what each [[task:…]] on the page stands for,
// since the raw markdown holds only the id.
func mcpTaskRefs(content, base, user string) string {
	masked, _ := mdtext.MaskCode(content)
	var refs []string
	seen := map[string]bool{}
	for _, m := range taskRefRE.FindAllStringSubmatch(masked, -1) {
		if ref := strings.ToLower(m[1]); !seen[ref] {
			seen[ref] = true
			refs = append(refs, ref)
		}
	}
	if len(refs) == 0 {
		return ""
	}
	tasks := resolveTaskRefs(user, refs)
	lists := taskLists(user)
	var lines []string
	for _, ref := range refs {
		t := tasks[ref]
		if t == nil {
			lines = append(lines, "- [[task:"+ref+"]]: no such task (deleted)")
			continue
		}
		facts := []string{taskStatus(t)}
		if t.StartDate != "" {
			facts = append(facts, "start "+t.StartDate)
		}
		if t.DueDate != "" {
			facts = append(facts, "due "+t.DueDate)
		}
		lines = append(lines, "- [[task:"+ref+"]]: "+t.Title+" ("+strings.Join(facts, ", ")+") "+
			base+taskLink(t, taskList(t, lists)))
	}
	return "\n\n---\nTasks referenced on this page ([[task:…]]):\n" + strings.Join(lines, "\n")
}

func searchTasks(args jsonObj, base, user string) (string, error) {
	query, err1 := strArg(args, "query", false)
	tag, err2 := strArg(args, "tag", false)
	if err1 != nil || err2 != nil {
		return "", toolErr("query and tag must be strings")
	}
	status := "open"
	if args.has("status") {
		status, _ = as[string](args["status"])
	}
	if status != "open" && status != "done" && status != "all" {
		return "", toolErr("status must be open, done or all")
	}
	limit, err := mcpInt(args, "limit", 50, 200)
	if err != nil {
		return "", err
	}
	want := map[string]bool{}
	for _, t := range strings.Split(tag, ",") {
		if t = strings.ToLower(strings.TrimLeft(strings.TrimSpace(t), "#")); t != "" {
			want[t] = true
		}
	}
	q := textsearch.ParseQuery(query)
	var docs []*SearchDoc
	for _, d := range taskSearchDocs() {
		if !canSeeTask(user, d.Task) || status != "all" && (status == "open") != d.Task.isOpen() {
			continue
		}
		have := taskTagsLower(d.Task)
		all := true
		for w := range want {
			all = all && have[w]
		}
		if all && (!q.HasFilters() || q.Accepts(d.text(), d.Tags, d.date()) && !q.HoldsExcluded(d.Tokens())) {
			docs = append(docs, d)
		}
	}
	if strings.TrimSpace(q.Text) != "" {
		ranked := newDocIndex(docs).search(q.Text, max(len(docs), 1))
		if semantic != nil {
			semantic.poke()
			byID := map[string]*SearchDoc{}
			for _, d := range docs {
				byID[d.TaskID] = d
			}
			ranked = textsearch.Fuse([][]*SearchDoc{ranked, bySimilarity(taskHits(q.Text, byID, max(len(byID), 1)))})
		}
		docs = ranked
	} else {
		var open, done []*SearchDoc
		for _, d := range docs {
			if d.Task.isOpen() {
				open = append(open, d)
			} else {
				done = append(done, d)
			}
		}
		or9 := func(s string) string { return firstNonEmpty(s, "9999") }
		sort.SliceStable(open, func(i, j int) bool {
			a, b := open[i].Task, open[j].Task
			if x, y := or9(a.DueDate), or9(b.DueDate); x != y {
				return x < y
			}
			if x, y := or9(a.StartDate), or9(b.StartDate); x != y {
				return x < y
			}
			return strings.ToLower(a.Title) < strings.ToLower(b.Title)
		})
		sort.SliceStable(done, func(i, j int) bool {
			return done[i].Task.CompletedAt > done[j].Task.CompletedAt
		})
		docs = append(open, done...)
	}
	day := isoDay(today())
	if len(docs) == 0 {
		return "Today is " + day + ". No matching tasks.", nil
	}
	count := fmt.Sprintf("%d matching tasks", len(docs))
	if len(docs) > limit {
		count += fmt.Sprintf(", the first %d shown", limit)
	}
	var body []string
	for _, d := range docs[:min(limit, len(docs))] {
		body = append(body, mcpTaskText(d.Task, base, user))
	}
	return "Today is " + day + ". " + count + ":\n\n" + strings.Join(body, "\n\n---\n\n"), nil
}

var (
	taskLinkRE = regexp.MustCompile(`task[:=]([0-9a-f-]{4,36})`)
	taskIDRE   = regexp.MustCompile(`^[0-9a-f][0-9a-f-]{3,35}$`)
)

// findTask: the task a reference names: its id or a prefix of it (the
// 8-character short id the tools show), a [[task:…]] reference or a
// /tasks?task=… link; of the tasks user sees.
func findTask(raw, user string) (*Task, error) {
	raw = strings.ToLower(strings.TrimSpace(raw))
	ref := raw
	if m := taskLinkRE.FindStringSubmatch(raw); m != nil {
		ref = m[1]
	}
	if !taskIDRE.MatchString(ref) {
		return nil, toolErr(strconv.Quote(raw) + " is not a task id; search_tasks shows each task's id")
	}
	found := findTasks(user, ref)
	if len(found) == 0 {
		return nil, toolErr("No task with id " + strconv.Quote(ref) + ". search_tasks shows each task's id.")
	}
	if len(found) > 1 {
		return nil, toolErr("Several tasks have ids starting with " + strconv.Quote(ref) + "; give more of the id")
	}
	return found[0], nil
}

// dateArg: a date argument, YYYY-MM-DD or a word quick-add knows
// ("tomorrow", "fri", "+3d"), resolved with Zone's today; "" for none.
func dateArg(args jsonObj, name string) (string, error) {
	s, err := strArg(args, name, false)
	if err != nil || strings.TrimSpace(s) == "" {
		return "", err
	}
	day := parseDateWord(strings.TrimSpace(s), today())
	if day == "" {
		return "", toolErr(name + " must be a date: YYYY-MM-DD, today, tomorrow, a weekday or +3d/+2w")
	}
	return day, nil
}

// tagListArg: a list-of-tags argument (a comma-separated string works too),
// each without its leading #.
func tagListArg(args jsonObj, name string) ([]string, error) {
	l, err := listArg(args, name, name+" must be a list of tags", splitComma)
	var out []string
	for _, s := range l {
		if s = strings.TrimLeft(strings.TrimSpace(s), "#"); s != "" {
			out = append(out, s)
		}
	}
	return out, err
}

func splitComma(s string) []string { return strings.Split(s, ",") }

// taskFields: the tasks API's fields from the tools' arguments (only those
// given); task, when updating one, for add_tags/remove_tags and repeat.
func taskFields(args jsonObj, task *Task) (jsonObj, error) {
	data := jsonObj{}
	for _, f := range [][2]string{{"title", "title"}, {"notes", "description"}} {
		if args.has(f[0]) {
			s, err := strArg(args, f[0], false)
			if err != nil {
				return nil, err
			}
			data.set(f[1], strings.TrimSpace(s))
		}
	}
	for _, f := range [][2]string{{"due", "due_date"}, {"start", "start_date"}} {
		if args.has(f[0]) {
			day, err := dateArg(args, f[0])
			if err != nil {
				return nil, err
			}
			data.set(f[1], nullable(day))
		}
	}
	if title, _ := as[string](data["title"]); data.has("title") && title == "" {
		return nil, toolErr("title must be a non-empty string")
	}
	for _, f := range []string{"keep", "private"} {
		if args.has(f) {
			b, ok := as[bool](args[f])
			if !ok {
				return nil, toolErr(f + " must be true or false")
			}
			data.set(f, b)
		}
	}
	if args.has("tags") {
		data["tags"] = args["tags"]
	}
	if task != nil && (args.has("add_tags") || args.has("remove_tags")) {
		tags := append([]string{}, task.Tags...)
		if data.has("tags") {
			tags = textList(data["tags"])
		}
		add, err := tagListArg(args, "add_tags")
		if err != nil {
			return nil, err
		}
		remove, err := tagListArg(args, "remove_tags")
		if err != nil {
			return nil, err
		}
		data.set("tags", retag(tags, add, remove))
	}
	if args.has("repeat") || args.has("repeat_every") || args.has("repeat_from") {
		repeat, interval, from := "none", json.RawMessage("1"), "due"
		if task != nil && task.Recurrence != nil {
			repeat, from = task.Recurrence.Type, task.Recurrence.From
			interval = json.RawMessage(strconv.Itoa(task.Recurrence.Interval))
		}
		if args.has("repeat") {
			repeat, _ = as[string](args["repeat"])
		}
		switch repeat {
		case "daily", "weekly", "monthly", "yearly":
			data.set("rec_type", repeat)
		case "none":
			data.set("rec_type", nil)
		default:
			return nil, toolErr("repeat must be daily, weekly, monthly, yearly or none")
		}
		if args.has("repeat_every") {
			interval = args["repeat_every"]
		}
		data["rec_interval"] = interval
		if args.has("repeat_from") {
			from, _ = as[string](args["repeat_from"])
		}
		if from != "due" && from != "completion" {
			return nil, toolErr("repeat_from must be due or completion")
		}
		data.set("rec_from", from)
	}
	return data, nil
}

// tasksWrite: a task list write's refusals (a bad date, an empty title …) as
// tool errors the model can correct.
func tasksWrite[T any](v T, err error) (T, error) {
	var ae *apiError
	switch {
	case errors.Is(err, errTasksCorrupt):
		return v, toolErr("The tasks' storage can't be read right now; try again")
	case errors.As(err, &ae):
		return v, toolErr(ae.msg)
	}
	return v, err
}

// The tasks closest to a new one whose tags are suggested, and how many.
const (
	similarTasks  = 10
	tagsSuggested = 5
)

// tagHints: for a task just created, the tags on the tasks most like it
// (by its title and notes; open and done ones), most common first, which
// it lacks, so the model can offer them; and its tags no other task or page
// uses, which may be near-duplicates of existing ones.
func tagHints(task *Task, user string) string {
	var others []*SearchDoc
	for _, d := range taskSearchDocs() {
		if d.TaskID != task.ID && canSeeTask(user, d.Task) {
			others = append(others, d)
		}
	}
	have := taskTagsLower(task)
	text := textsearch.ParseQuery(task.Title + " " + task.Description).Text
	counts := map[string]int{}
	var order []string
	if strings.TrimSpace(text) != "" {
		for _, d := range newDocIndex(others).search(text, similarTasks) {
			for _, tag := range d.Task.Tags {
				tag = strings.ToLower(tag)
				if have[tag] {
					continue
				}
				if counts[tag] == 0 {
					order = append(order, tag)
				}
				counts[tag]++
				have[tag] = true // once per task
			}
			have = taskTagsLower(task)
		}
	}
	out := ""
	if len(order) > 0 {
		// Most common first; ties: the order the closest tasks named them.
		sort.SliceStable(order, func(i, j int) bool { return counts[order[i]] > counts[order[j]] })
		var hints []string
		for _, t := range order[:min(len(order), tagsSuggested)] {
			hints = append(hints, fmt.Sprintf("#%s (%d)", t, counts[t]))
		}
		out += "\n\nTags on similar tasks (offer the fitting ones; update_task's add_tags adds them): " +
			strings.Join(hints, ", ")
	}
	used := map[string]bool{}
	for _, d := range others {
		for t := range taskTagsLower(d.Task) {
			used[t] = true
		}
	}
	for _, p := range visiblePages(user) {
		if content, err := readPageText(p.Path); err == nil {
			for _, t := range mdtext.PageTags(content) {
				used[t] = true
			}
		}
	}
	var fresh []string
	for t := range have {
		if !used[t] {
			fresh = append(fresh, "#"+t)
		}
	}
	sort.Strings(fresh)
	if len(fresh) > 0 && len(used) > 0 {
		out += "\n\nNew tags, used nowhere else yet: " + strings.Join(fresh, ", ") +
			". If one means an existing tag (list_tags), use that instead."
	}
	return out
}

// homeTags: a new task's tags, the default list's new-task tags (its list, its
// single tags, as its "+ New" and c t give them) before the ones given, so
// the task shows in user's tasks. Not when the given tags already name a
// declared list: the task is filed there.
func homeTags(tags json.RawMessage, user string) json.RawMessage {
	l, ok := as[[]string](tags)
	if s, isStr := as[string](tags); isStr {
		l, ok = splitComma(s), true
	}
	if !ok && !isNull(tags) {
		return tags // createTask refuses it
	}
	home := lookupDefaultList(user)
	if home == nil {
		return tags
	}
	lists := taskLists(user)
	for _, t := range l {
		if _, ok := lists[strings.ToLower(strings.TrimLeft(t, "#"))]; ok {
			return tags
		}
	}
	b, _ := json.Marshal(cleanTags(append(newTaskTags(home.Params), l...)))
	return b
}

func createTaskTool(args jsonObj, base, user string) (string, error) {
	if _, err := strArg(args, "title", true); err != nil {
		return "", err
	}
	data, err := taskFields(args, nil)
	if err != nil {
		return "", err
	}
	data["tags"] = homeTags(data["tags"], user)
	// As c t in the browser: private when the user's default list is theirs alone.
	if h := lookupDefaultList(user); !data.has("private") && h != nil && ownPrivate(user, h.Slug) {
		data.set("private", true)
	}
	msg, err := commitMsg("Create task", args)
	if err != nil {
		return "", err
	}
	task, err := tasksWrite(createTask(user, data, msg))
	if err != nil {
		return "", err
	}
	return "Created (today is " + isoDay(today()) + ").\n\n" + mcpTaskText(task, base, user) + tagHints(task, user), nil
}

// taskStatusFields: a status as the tasks API's field and its value.
var taskStatusFields = map[string]struct {
	field string
	value bool
}{"done": {"completed", true}, "open": {"completed", false}, "wont_do": {"wont_do", true}, "skip": {"skip", true}}

// otherArgs: the arguments besides those named, A–Z.
func otherArgs(args jsonObj, allowed ...string) []string {
	var other []string
	for k := range args {
		if !slices.Contains(allowed, k) {
			other = append(other, k)
		}
	}
	sort.Strings(other)
	return other
}

// updateTaskTool: one task's fields, status and comment; status deleted
// deletes it; ids instead of id tags several tasks (tagTasksTool).
func updateTaskTool(args jsonObj, base, user string) (string, error) {
	if args.has("ids") {
		if args.has("id") {
			return "", toolErr("Give id or ids, not both")
		}
		if other := otherArgs(args, "ids", "add_tags", "remove_tags", "summary"); len(other) > 0 {
			return "", toolErr("With ids only add_tags and remove_tags work, not " + strings.Join(other, ", ") +
				"; change the rest one task (id) at a time")
		}
		return tagTasksTool(args, user)
	}
	raw, err := strArg(args, "id", true)
	if err != nil {
		return "", err
	}
	task, err := findTask(raw, user)
	if err != nil {
		return "", err
	}
	status, _ := as[string](args["status"])
	if status == "deleted" {
		if other := otherArgs(args, "id", "status", "summary"); len(other) > 0 {
			return "", toolErr("status deleted takes nothing else, not " + strings.Join(other, ", "))
		}
		msg, err := commitMsg("Delete task", args)
		if err != nil {
			return "", err
		}
		if _, err := tasksWrite(struct{}{}, deleteTask(user, task.ID, msg)); err != nil {
			return "", err
		}
		return "Deleted task " + shortID(task.ID) + ": " + task.Title, nil
	}
	var comment *string
	if args.has("comment") {
		c, err := strArg(args, "comment", true)
		if err != nil {
			return "", err
		}
		comment = &c
	}
	data, err := taskFields(args, task)
	if err != nil {
		return "", err
	}
	if args.has("status") {
		f, ok := taskStatusFields[status]
		if !ok {
			return "", toolErr("status must be open, done, wont_do, skip or deleted")
		}
		data.set(f.field, f.value)
	}
	if len(data) == 0 && comment == nil {
		return "", toolErr("Nothing to change: give the fields to set, a status or a comment")
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if len(data) > 0 {
		if task, err = tasksWrite(updateTask(user, task.ID, data, suffix)); err != nil {
			return "", err
		}
	}
	if comment != nil {
		if task, err = tasksWrite(addComment(user, task.ID, *comment, "Comment on task"+suffix)); err != nil {
			return "", err
		}
	}
	head := "Comment added."
	if len(data) > 0 && comment != nil {
		head = "Updated, comment added."
	} else if len(data) > 0 {
		head = "Updated."
	}
	out := head + "\n\n" + mcpTaskText(task, base, user)
	if status == "done" || status == "skip" {
		for _, t := range openTasks(user) {
			if t.SpawnedFrom == task.ID && t.isOpen() {
				out += "\n\nNext occurrence:\n\n" + mcpTaskText(t, base, user)
				break
			}
		}
	}
	return out, nil
}

// More ids than this in one call is likely a mistake (a whole tasks retagged).
const tagTasksMax = 200

func tagTasksTool(args jsonObj, user string) (string, error) {
	ids, ok := as[[]json.RawMessage](args["ids"])
	if !ok || len(ids) == 0 {
		return "", toolErr("ids must be a non-empty list of task ids")
	}
	for _, id := range ids {
		if _, ok := as[string](id); !ok {
			return "", toolErr("ids must be a non-empty list of task ids")
		}
	}
	if len(ids) > tagTasksMax {
		return "", toolErr(fmt.Sprintf("At most %d tasks per call", tagTasksMax))
	}
	add, err := tagListArg(args, "add_tags")
	if err != nil {
		return "", err
	}
	remove, err := tagListArg(args, "remove_tags")
	if err != nil {
		return "", err
	}
	if len(add) == 0 && len(remove) == 0 {
		return "", toolErr("Give add_tags or remove_tags")
	}
	var found []string
	before := map[string][]string{}
	for _, id := range ids {
		raw, _ := as[string](id)
		t, err := findTask(raw, user) // an unknown id fails the whole call
		if err != nil {
			return "", err
		}
		if _, ok := before[t.ID]; !ok {
			found = append(found, t.ID)
			before[t.ID] = append([]string{}, t.Tags...)
		}
	}
	msg, err := commitMsg("Tag tasks", args)
	if err != nil {
		return "", err
	}
	tasks, err := tasksWrite(tagTasks(user, found, add, remove, msg))
	if err != nil {
		return "", err
	}
	changed := 0
	var lines []string
	for _, t := range tasks {
		tags := t.Tags
		if !slices.Equal(tags, before[t.ID]) {
			changed++
		}
		shown := "no tags"
		if len(tags) > 0 {
			shown = "#" + strings.Join(tags, " #")
		}
		lines = append(lines, "- "+t.Title+" ("+shortID(t.ID)+"): "+shown)
	}
	s := ""
	if len(tasks) != 1 {
		s = "s"
	}
	head := fmt.Sprintf("Tags changed on %d of %d task%s", changed, len(tasks), s)
	if changed < len(tasks) {
		head += " (the others had them so already)"
	}
	return head + ".\n\n" + strings.Join(lines, "\n"), nil
}

// ── Reading ─────────────────────────────────────────────────────────────────

// mcpPageLinks: for read_page, the pages that link to it and its links that
// lead nowhere, so the model sees where a page sits without reading others.
// Of the pages user may see; a link to one they may not isn't broken.
func mcpPageLinks(slug, content, user string) string {
	var linking []string
	for _, b := range findBacklinks(slug) {
		if canSee(user, b.Slug) {
			linking = append(linking, b.Slug)
		}
	}
	shown := strings.Join(linking[:min(len(linking), backlinksShown)], ", ")
	if shown == "" {
		shown = "no other page"
	}
	if len(linking) > backlinksShown {
		shown += fmt.Sprintf(", … and %d more", len(linking)-backlinksShown)
	}
	out := fmt.Sprintf("\n\n---\nLinked from (%d): %s", len(linking), shown)
	if missing := missingLinks(content, slugSet(), slug, user); len(missing) > 0 {
		out += "\nBroken links (no such page): " + strings.Join(sortedKeys(missing), ", ")
	}
	if semantic != nil {
		// As the page view's Related: pages close in meaning that aren't
		// linked either way, the connections not written down yet.
		semantic.poke()
		_, targets := pageMeta(safePath(slug), slug)
		linked := maps.Clone(targets)
		for _, s := range linking {
			linked[s] = true
		}
		if related := semantic.related(slug, visibleSlugs(user), linked, 5); len(related) > 0 {
			out += "\nRelated, not linked (close in meaning): " + joinIDs(related)
		}
	}
	return out
}

func readPage(args jsonObj, base, user string) (string, error) {
	raw, ok := as[string](args["slug"])
	if !ok || strings.TrimSpace(raw) == "" {
		return "", toolErr("slug must be a non-empty string")
	}
	slug := mcpPageSlug(raw, user)
	if slug == "" {
		return "", toolErr("No page " + strconv.Quote(raw) + ". Use search_wiki or list_pages to find its slug.")
	}
	content, err := readPageText(safePath(slug))
	if err != nil {
		return "", err
	}
	// The whole page's hash, whatever part is read: write_page's base_hash.
	head := "slug: " + slug + "\n" + pageInfo(slug, base) + "\nhash: " + keepGivenBase(content) + "\n"
	heading, err := strArg(args, "heading", false)
	if err != nil {
		return "", err
	}
	if heading = strings.TrimSpace(heading); heading != "" {
		lines := mdtext.SplitLines(content)
		start, end, ok := sectionLines(lines, heading)
		if !ok {
			return "", toolErr(noHeading(slug, content, heading) + " Leave out heading for the whole page.")
		}
		// Line numbers as grep_wiki gives them, so the two fit together.
		head += fmt.Sprintf("section: lines %d–%d of %d\n", start+1, end, len(lines))
		content = trimNewlines(strings.Join(lines[start:end], "")) + "\n"
	} else {
		head += guideIDLine(slug, content, user)
	}
	return head + "\n" + content + mcpTaskRefs(content, base, user) + mcpPageLinks(slug, content, user), nil
}

// isOrphan: no other page links to it. The home page, the default list's page and
// notes are reached otherwise (the module switcher), and the guide
// is read by the assistant, so they never are.
func isOrphan(slug string, counts map[string]int, home string) bool {
	return counts[slug] == 0 && !isProtected(slug) && slug != home && slug != guideSlug && !isQuicknote(slug)
}

func listPages(args jsonObj, base, user string) (string, error) {
	limit, err := mcpInt(args, "limit", 100, 1000)
	if err != nil {
		return "", err
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	orphans, err := boolArg(args, "orphans")
	if err != nil {
		return "", err
	}
	onlyQuicknote, err := renamedBoolArg(args, "notes", "scratch")
	if err != nil {
		return "", err
	}
	counts := allBacklinkCounts(&Req{user: user})
	home := ""
	if h := lookupDefaultList(user); h != nil {
		home = h.Slug
	}
	type row struct {
		mtime       int64
		slug, title string
	}
	var rows []row
	for _, p := range visiblePages(user) {
		if !inFolder(p.Slug, under) || orphans && !isOrphan(p.Slug, counts, home) || onlyQuicknote && !inQuicknote(user, p.Slug) {
			continue
		}
		st, err := os.Stat(p.Path)
		if err != nil {
			continue
		}
		title, _ := pageMeta(p.Path, p.Slug)
		rows = append(rows, row{st.ModTime().UnixNano(), p.Slug, title})
	}
	sort.Slice(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if a.mtime != b.mtime {
			return a.mtime > b.mtime
		}
		if a.slug != b.slug {
			return a.slug > b.slug
		}
		return a.title > b.title
	})
	where := underWhere(under)
	if len(rows) == 0 {
		switch {
		case onlyQuicknote:
			return "No notes" + where + ".", nil
		case orphans:
			return "Every page" + where + " is linked from another page.", nil
		case under != "":
			return "No pages under " + strconv.Quote(under) + ".", nil
		}
		return "The wiki has no pages.", nil
	}
	var lines []string
	for _, r := range rows[:min(limit, len(rows))] {
		lines = append(lines, fmt.Sprintf("%s | %s | edited %s | %d", r.slug, r.title,
			fmtDate(unixNano(r.mtime)), counts[r.slug]))
	}
	shown := fmt.Sprintf("all %d", len(rows))
	if len(lines) < len(rows) {
		shown = fmt.Sprintf("%d of %d", len(lines), len(rows))
	}
	what := "Pages"
	if onlyQuicknote {
		what = "Notes"
	} else if orphans {
		what = "Pages no other page links to (the home page, the Tasks home and notes aside)"
	}
	return what + where + ", most recently edited first (" + shown + "); " +
		"slug | title | last edit | pages linking to it:\n" + strings.Join(lines, "\n"), nil
}

func checkLinks(args jsonObj, base, user string) (string, error) {
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	// A link to a page user may not see isn't broken, nor suggested.
	slugs := slugSet()
	var all []string
	for _, s := range allSlugs() {
		if canSee(user, s) {
			all = append(all, s)
		}
	}
	var out []string
	total := 0
	for _, p := range sortedPages() {
		if !inFolder(p.Slug, under) || !canSee(user, p.Slug) {
			continue
		}
		content, err := readPageText(p.Path)
		if err != nil {
			continue
		}
		missing := missingLinks(content, slugs, p.Slug, user)
		if len(missing) == 0 {
			continue
		}
		var links []string
		for _, link := range sortedKeys(missing) {
			name := missing[link]
			if strings.HasPrefix(link, "[[") {
				name = mdtext.ToSlug(name)
			}
			if near := textsearch.CloseMatches(name, all, 1, 0.8); len(near) > 0 {
				link += " (maybe [[" + near[0] + "]]?)"
			}
			links = append(links, link)
		}
		total += len(links)
		out = append(out, p.Slug+": "+strings.Join(links, ", "))
	}
	where := underWhere(under)
	if len(out) == 0 {
		return "No broken links" + where + ".", nil
	}
	return fmt.Sprintf("%d links%s point to pages that don't exist, on %d pages "+
		"(fix the link, or create the page):\n", total, where, len(out)) + strings.Join(out, "\n"), nil
}

// How much of a long line grep_wiki and replace_in_pages show.
const grepLineChars = 300

func cutLine(line string) string { return cutRunes(strings.TrimSpace(line), grepLineChars, " …") }

func grepWiki(args jsonObj, base, user string) (string, error) {
	pattern, ok := as[string](args["pattern"])
	if !ok || strings.TrimSpace(pattern) == "" {
		return "", toolErr("pattern must be a non-empty string")
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	limit, err := mcpInt(args, "limit", 50, 500)
	if err != nil {
		return "", err
	}
	ignoreCase, err := boolArg(args, "ignore_case")
	if err != nil {
		return "", err
	}
	found, err := grepPages(pattern, ignoreCase, visibleSlugs(user))
	var ge *grepError
	if errors.As(err, &ge) {
		return "", toolErr("grep_wiki: " + ge.msg)
	}
	var hits []GrepHit
	pages := map[string]bool{}
	for _, h := range found {
		if inFolder(h.Slug, under) {
			hits = append(hits, h)
			pages[h.Slug] = true
		}
	}
	where := underWhere(under)
	if len(hits) == 0 {
		return "No line" + where + " matches " + strconv.Quote(pattern) + ".", nil
	}
	var out []string
	last := ""
	for _, h := range hits[:min(limit, len(hits))] {
		if h.Slug != last {
			if len(out) > 0 {
				out = append(out, "")
			}
			out = append(out, h.Slug)
			last = h.Slug
		}
		out = append(out, fmt.Sprintf("  %d: %s", h.Number, cutLine(h.Line)))
	}
	head := fmt.Sprintf("%d matching lines on %d pages%s", len(hits), len(pages), where)
	if len(hits) > limit {
		head += fmt.Sprintf(", the first %d shown (raise limit, or narrow the pattern or folder)", limit)
	}
	if !cfg.GitEnabled {
		head += "; git isn't installed, so the pattern was matched as plain text"
	}
	return head + ":\n" + strings.Join(out, "\n"), nil
}

// fileLabel: a changed file as the model knows it, a page by its slug, the
// task files as tasks (in _deck/ in the history from before the move).
func fileLabel(fp string) string {
	for _, dir := range []string{tasksDirName, legacyTasksDirName} {
		if strings.HasPrefix(fp, dir+"/") || strings.Contains(fp, "/"+dir+"/") {
			return "tasks"
		}
	}
	return strings.TrimSuffix(fp, ".md")
}

func noGit() error { return toolErr("There is no history: git isn't installed where Zone runs.") }

func history(args jsonObj, base, user string) (string, error) {
	if !cfg.GitEnabled {
		return "", noGit()
	}
	limit, err := mcpInt(args, "limit", 20, 200)
	if err != nil {
		return "", err
	}
	raw, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	version, err := strArg(args, "version", false)
	if err != nil {
		return "", err
	}
	raw, version = strings.TrimSpace(raw), strings.ToLower(strings.TrimSpace(version))
	if version != "" && raw == "" {
		return "", toolErr("version needs the page's slug too")
	}
	if raw == "" {
		since, err := dateArg(args, "since")
		if err != nil {
			return "", err
		}
		under, err := underArg(args)
		if err != nil {
			return "", err
		}
		return recentChanges(since, under, limit, user), nil
	}
	slug, err := pageOrNewSlug(raw, user) // a deleted page has a history too
	if err != nil {
		return "", err
	}
	path := safePath(slug)
	entries := pageLog(user, path)
	if len(entries) == 0 {
		return "", toolErr("No history for " + strconv.Quote(raw) + ". Use list_pages to find its slug.")
	}
	if version != "" {
		return pageChange(slug, path, entries, version)
	}
	var rows []string
	for _, e := range entries[:min(limit, len(entries))] {
		rows = append(rows, fmt.Sprintf("%s | %s | +%d -%d | %s", runePrefix(e.Sha, 7), e.Time, e.Added, e.Deleted,
			e.Msg+byAuthor(e.Author)))
	}
	shown := fmt.Sprintf("all %d", len(entries))
	if len(rows) < len(entries) {
		shown = fmt.Sprintf("%d of %d", len(rows), len(entries))
	}
	state := ""
	if !exists(path) {
		state = " (deleted: restore_page brings back a version)"
	} else if isUncommitted(path) {
		state = " (it has changes not committed yet, newer than the first version listed)"
	}
	return "Versions of " + slug + state + ", newest first (" + shown + "); version | when | lines added and " +
		"removed | what (\"(MCP)\": written by an assistant). history with a version shows " +
		"what it changed:\n" + strings.Join(rows, "\n"), nil
}

// byAuthor: " (by alice)" after a commit's message; nothing for Zone's own.
func byAuthor(author string) string {
	if author == "" {
		return ""
	}
	return " (by " + author + ")"
}

func versionOf(entries []GitEntry, version string) int {
	if !shaRE.MatchString(version) {
		return -1
	}
	return versionIndex(entries, version)
}

// pageChange: what one version changed on the page, as a diff.
func pageChange(slug, path string, entries []GitEntry, version string) (string, error) {
	i := versionOf(entries, version)
	if i < 0 {
		return "", toolErr(strconv.Quote(version) + " is not a version of " + slug + "; history lists them.")
	}
	e := entries[i]
	diff, _ := versionDiff(path, entries, e.Sha)
	if diff == "" {
		diff = "(no changes)"
	}
	return slug + ", version " + runePrefix(e.Sha, 7) + " of " + e.Time + ": " + e.Msg + "\n\n" + diff, nil
}

// recentChanges: the wiki's latest commits, as user sees them: only the
// files they may see, without a commit that changed none of those.
func recentChanges(since, under string, limit int, user string) string {
	entries := gitLogAll(since, under, limit, func(path string) bool { return canSeePath(user, path) })
	var out, pending []string
	for _, f := range gitUncommittedFiles() {
		if !canSeePath(user, f.Filepath) {
			continue
		}
		if label := fileLabel(f.Filepath); under == "" || inFolder(label, under) {
			if f.Rel != "" {
				pending = append(pending, label+" ("+f.Rel+")")
			} else {
				pending = append(pending, label+" (deleted)")
			}
		}
	}
	if len(pending) > 0 {
		out = append(out, "Not committed yet (edited in the browser or on disk): "+strings.Join(pending, ", "))
	}
	where, when := underWhere(under), ""
	if since != "" {
		when = " since " + since
	}
	if len(entries) == 0 {
		return strings.Join(append(out, "No changes"+where+when+"."), "\n\n")
	}
	var rows []string
	for _, e := range entries {
		var labels []string
		for _, f := range e.Files {
			if l := fileLabel(f); !slices.Contains(labels, l) {
				labels = append(labels, l)
			}
		}
		rows = append(rows, e.Time+" | "+runePrefix(e.Sha, 7)+" | "+hideNames(user, e.Msg)+byAuthor(e.Author)+" | "+
			strings.Join(labels, ", "))
	}
	theLimit := ""
	if len(rows) == limit {
		theLimit = ", the limit"
	}
	out = append(out, fmt.Sprintf("Changes%s%s, newest first (%d%s); when | version | what (\"(MCP)\": written by "+
		"an assistant) | pages:\n", where, when, len(rows), theLimit)+strings.Join(rows, "\n"))
	return strings.Join(out, "\n\n")
}

func listTags(args jsonObj, base, user string) (string, error) {
	counts := map[string]*[3]int{} // tag → pages, open tasks, done tasks
	count := func(tag string, column int) {
		if counts[tag] == nil {
			counts[tag] = &[3]int{}
		}
		counts[tag][column]++
	}
	for _, p := range visiblePages(user) {
		content, err := readPageText(p.Path)
		if err != nil {
			continue
		}
		seen := map[string]bool{}
		for _, t := range mdtext.PageTags(content) {
			if !seen[t] {
				seen[t] = true
				count(t, 0)
			}
		}
	}
	for _, t := range allTasks(user) {
		column := 2
		if taskStatus(t) == "open" {
			column = 1
		}
		for tag := range taskTagsLower(t) {
			count(tag, column)
		}
	}
	if len(counts) == 0 {
		return "No tags yet: neither pages (frontmatter tags:) nor tasks have any.", nil
	}
	lists := taskLists(user)
	tags := sortedKeys(counts)
	sum := func(t string) int { c := counts[t]; return c[0] + c[1] + c[2] }
	sort.SliceStable(tags, func(i, j int) bool { return sum(tags[i]) > sum(tags[j]) })
	var lines []string
	for _, t := range tags {
		c := counts[t]
		list := ""
		if _, ok := lists[t]; ok {
			list = " (a list)"
		}
		lines = append(lines, fmt.Sprintf("%s%s | %d | %d | %d", t, list, c[0], c[1], c[2]))
	}
	return "Tags in use, most used first; reuse these rather than inventing near-duplicates. " +
		"tag | pages | open tasks | done tasks:\n" + strings.Join(lines, "\n"), nil
}

// zoneHelp: Zone's documentation (manual.go), passages for a query, a doc
// or one of its sections, or the list of docs with their headings.
func zoneHelp(args jsonObj, base, user string) (string, error) {
	var query, topic, heading string
	for _, a := range []struct {
		name string
		v    *string
	}{{"query", &query}, {"topic", &topic}, {"heading", &heading}} {
		s, err := strArg(args, a.name, false)
		if err != nil {
			return "", err
		}
		*a.v = strings.TrimSpace(s)
	}
	topic = strings.TrimSuffix(strings.ToLower(topic), ".md")
	docs := manualTopics()
	if len(docs) == 0 {
		return "", toolErr("Zone's documentation isn't installed with this server.")
	}
	var names []string
	paths := map[string]string{}
	for _, d := range docs {
		names = append(names, d.name)
		paths[d.name] = d.path
	}
	if topic != "" {
		path, ok := paths[topic]
		if !ok {
			return "", toolErr("No topic " + strconv.Quote(topic) + ". Topics: " + strings.Join(names, ", ") + ".")
		}
		text, err := readText(path)
		if err != nil {
			return "", err
		}
		if heading == "" {
			return "topic: " + topic + "\n\n" + text, nil
		}
		lines := mdtext.SplitLines(text)
		start, end, ok := sectionLines(lines, heading)
		if !ok {
			return "", toolErr(noHeading(topic, text, heading) + " Leave out heading for the whole doc.")
		}
		return "topic: " + topic + "\n\n" + trimNewlines(strings.Join(lines[start:end], "")) + "\n", nil
	}
	if heading != "" {
		return "", toolErr("heading needs a topic.")
	}
	if query != "" {
		limit, err := mcpInt(args, "limit", 5, 20)
		if err != nil {
			return "", err
		}
		hits := manualFind(query, limit)
		if len(hits) == 0 {
			return "Nothing in Zone's documentation matches. Try other words, or leave out query for the list of topics.", nil
		}
		var out []string
		for i, d := range hits {
			if d.heading != "" {
				out = append(out, fmt.Sprintf("[%d] %s › %s\ntopic: %s\nheading: %s\n\n%s", i+1, d.title, d.heading, d.topic, d.heading, d.text))
			} else {
				out = append(out, fmt.Sprintf("[%d] %s\ntopic: %s\n\n%s", i+1, d.title, d.topic, d.text))
			}
		}
		return strings.Join(out, "\n\n---\n\n") + "\n\nThe whole section: zone_help with its topic and heading.", nil
	}
	out := []string{"Zone's documentation: the README (what Zone is, its features, setup) and the spec, " +
		"one doc per area. Read one with topic, a section with topic and heading."}
	for _, d := range docs {
		text, _ := readText(d.path)
		found := manualHeadings(text)
		title := d.name
		if len(found) > 0 {
			title = found[0].text
		}
		out = append(out, "\n"+d.name+": "+title)
		for _, h := range found[min(1, len(found)):] {
			if h.level <= 3 {
				out = append(out, "  "+strings.Repeat("  ", max(0, h.level-2))+h.text)
			}
		}
	}
	return strings.Join(out, "\n"), nil
}

func searchWiki(args jsonObj, base, user string) (string, error) {
	query, ok := as[string](args["query"])
	if !ok || strings.TrimSpace(query) == "" {
		return "", toolErr("query must be a non-empty string")
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	if under != "" && !anyPageIn(under, user) {
		return "", toolErr("No pages under " + strconv.Quote(under) + ". list_pages shows the slugs, " +
			"whose folders are the part before the last '/'.")
	}
	limit, err := mcpInt(args, "limit", 8, 20)
	if err != nil {
		return "", err
	}
	quicknote, err := renamedBoolArg(args, "notes", "scratch")
	if err != nil {
		return "", err
	}
	hits := retrieve(&Req{user: user}, query, limit, under, quicknote)
	logQuery(query, under, hits)
	if len(hits) == 0 {
		return "No matching sections or tasks. Try other words, or list_pages to browse.", nil
	}
	var out []string
	for i, d := range hits {
		if d.isTask() {
			out = append(out, fmt.Sprintf("[%d] ", i+1)+mcpTaskText(d.Task, base, user))
			continue
		}
		where := d.Title
		if d.Path != "" {
			where += " › " + d.Path
		}
		out = append(out, fmt.Sprintf("[%d] %s\nslug: %s\nedited: %s\nurl: %s%s%s\nfile: %s\n\n%s", i+1, where, d.Slug,
			fmtDate(d.Mtime), base, pathURL("page", d.Slug), d.Anchor, hostFile(d.Slug), d.Text))
	}
	return strings.Join(out, "\n\n---\n\n"), nil
}

func anyPageIn(folder, user string) bool {
	for _, p := range visiblePages(user) {
		if inFolder(p.Slug, folder) {
			return true
		}
	}
	return false
}

// ── Dispatch ────────────────────────────────────────────────────────────────

var mcpToolFuncs = map[string]func(args jsonObj, base, user string) (string, error){
	"search_wiki": searchWiki, "search_tasks": searchTasks, "grep_wiki": grepWiki, "read_page": readPage,
	"list_pages": listPages, "check_links": checkLinks, "history": history, "list_tags": listTags,
	"zone_help": zoneHelp, "read_guides": readGuides,
	"write_page": writePage, "edit_page": editPageTool, "replace_in_pages": replaceInPages,
	"append_to_page": appendToPage, "file_note": fileNote, "rename_page": renamePageTool,
	"delete_page": deletePageTool, "restore_page": restorePage, "rename_task_tag": renameTaskTag, "create_task": createTaskTool,
	"update_task": updateTaskTool,
}

// mcpToolAliases: tools by the names they had before, for assistants that
// still call them so; tools/list shows only the new ones.
var mcpToolAliases = map[string]string{"file_scratch_note": "file_note", "leaf_help": "zone_help"}

// callMCPTool runs a tool for the request r, as its token's user: links in
// its answer use base-url (the settings file), else the address the client reached Zone at.
func callMCPTool(r *http.Request, name string, args jsonObj) (string, error) {
	base := settings().BaseURL
	if base == "" {
		scheme := "http"
		if r.TLS != nil {
			scheme = "https"
		}
		base = scheme + "://" + r.Host
	}
	if n, ok := mcpToolAliases[name]; ok {
		name = n
	}
	f, ok := mcpToolFuncs[name]
	if !ok {
		return "", toolErr("Unknown tool: " + name)
	}
	if pageTools[name] {
		pagesMu.Lock()
		defer pagesMu.Unlock()
	}
	return f(args, base, userOf(r))
}

// pageTools: the tools that write pages, each run under pagesMu (as the
// routes that do are, writesPages).
var pageTools = map[string]bool{"write_page": true, "edit_page": true, "replace_in_pages": true,
	"append_to_page": true, "file_note": true, "rename_page": true, "delete_page": true, "restore_page": true,
	"rename_task_tag": true}

func joinIDs(hits []idScore) string {
	ids := make([]string, len(hits))
	for i, h := range hits {
		ids[i] = h.ID
	}
	return strings.Join(ids, ", ")
}
