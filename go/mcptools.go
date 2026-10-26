// The MCP tools: reading (search_wiki, search_tasks,
// grep_wiki, read_page, list_pages, check_links, list_tags, history,
// leaf_help, read_guides) and writing pages (write_page (also a scratch
// note), edit_page, replace_in_pages, append_to_page, rename_page (also a
// folder), delete_page, restore_page, file_scratch_note) and deck tasks
// (create_task, update_task: also comments, deletes and tags on several
// tasks). The protocol is in mcp.go.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// ── Arguments ───────────────────────────────────────────────────────────────

// cleanFolder: a folder argument as a slug prefix: "/page/tickets/",
// "tickets/" and "tickets" all become "tickets". "" means the whole wiki.
func cleanFolder(raw any) (string, error) {
	s, ok := raw.(string)
	if !ok {
		return "", toolErr("under must be a string")
	}
	s = pyStrip(s)
	if _, after, found := strings.Cut(s, "/page/"); found {
		s = after
	}
	return strings.Trim(s, "/"), nil
}

func underArg(args *Obj) (string, error) { return cleanFolder(orDefault(args, "under", "")) }

// mcpInt: a whole-number argument (a string of digits too), clamped to 1…top.
func mcpInt(args *Obj, name string, def, top int) (int, error) {
	v := orDefault(args, name, int64(def))
	if s, ok := v.(string); ok && pyStrip(s) != "" && regexp.MustCompile(`^`+pyDigit+`+$`).MatchString(pyStrip(s)) {
		v = int64(pyAtoi(pyStrip(s)))
	}
	n, ok := v.(int64)
	if !ok {
		return 0, toolErr(name + " must be a whole number")
	}
	return int(max(1, min(n, int64(top)))), nil
}

// strArg: a string argument with CRLF line ends as LF; a required one
// non-empty. null is "".
func strArg(args *Obj, name string, required bool) (string, error) {
	v := orDefault(args, name, "")
	if v == nil {
		v = ""
	}
	s, ok := v.(string)
	if !ok {
		return "", toolErr(name + " must be a string")
	}
	s = strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
	if required && pyStrip(s) == "" {
		return "", toolErr(name + " must be a non-empty string")
	}
	return s, nil
}

func boolArg(args *Obj, name string) (bool, error) {
	b, ok := orDefault(args, name, false).(bool)
	if !ok {
		return false, toolErr(name + " must be true or false")
	}
	return b, nil
}

// idsArg: a list of ids (guide ids), a comma- or space-separated string too.
func idsArg(args *Obj, name, msg string) (map[string]bool, error) {
	v := args.Get(name)
	if !truthyValue(v) {
		v = []any{}
	}
	if s, ok := v.(string); ok {
		v = strs(pySplit(strings.ReplaceAll(s, ",", " ")))
	}
	l, ok := v.([]any)
	if !ok {
		return nil, toolErr(msg)
	}
	set := map[string]bool{}
	for _, x := range l {
		s, ok := x.(string)
		if !ok {
			return nil, toolErr(msg)
		}
		set[pyStrip(s)] = true
	}
	return set, nil
}

// pyRepr is Python's repr() of a string: 'text', quotes and unprintable
// characters escaped.
func pyRepr(s string) string {
	quote := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		quote = `"`
	}
	var b strings.Builder
	b.WriteString(quote)
	for _, r := range s {
		switch {
		case r == '\\' || string(r) == quote:
			b.WriteString(`\` + string(r))
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case unicode.IsPrint(r):
			b.WriteRune(r)
		case r < 0x100:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r < 0x10000:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			fmt.Fprintf(&b, `\U%08x`, r)
		}
	}
	b.WriteString(quote)
	return b.String()
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
// Remote"); "" when none.
func mcpPageSlug(raw string) string {
	raw = pyStrip(raw)
	if _, after, found := strings.Cut(raw, "/page/"); found {
		raw = after
	}
	raw, _, _ = strings.Cut(raw, "#")
	raw = strings.TrimSuffix(strings.Trim(raw, "/"), ".md")
	for _, slug := range []string{raw, toSlug(raw)} {
		if slug == "" {
			continue
		}
		if p := safePath(slug); p != "" {
			if st, err := os.Stat(p); err == nil && st.Mode().IsRegular() {
				return slug
			}
		}
	}
	return ""
}

// hostFile: a page's file as the host sees it, so a client on the host can
// open or edit it: the host's wiki folder (LEAF_HOST_DATA) plus the slug,
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

func existingPage(args *Obj, name string) (string, string, error) {
	raw, err := strArg(args, name, true)
	if err != nil {
		return "", "", err
	}
	slug := mcpPageSlug(raw)
	if slug == "" {
		return "", "", toolErr("No page " + pyRepr(raw) + ". Use search_wiki or list_pages to find its slug.")
	}
	return slug, safePath(slug), nil
}

// newSlug: the slug a new page gets from a name or slug ("Tickets/BUG
// 1234" → "tickets/bug-1234"), refused when it would be outside the wiki or
// in a folder Leaf skips (_assets, _deck, .git …).
func newSlug(raw string) (string, error) {
	raw = pyStrip(raw)
	if _, after, found := strings.Cut(raw, "/page/"); found {
		raw = after
	}
	raw, _, _ = strings.Cut(raw, "#")
	raw = strings.TrimSuffix(strings.Trim(raw, "/"), ".md")
	slug := toSlug(raw)
	if slug == "" || slices_contains(strings.Split(slug, "/"), "") {
		return "", toolErr(pyRepr(raw) + " is not a usable page name")
	}
	if safePath(slug) == "" {
		return "", toolErr(pyRepr(slug) + ": pages can't go in folders starting with '_' or '.'")
	}
	return slug, nil
}

func slices_contains(l []string, s string) bool {
	for _, x := range l {
		if x == s {
			return true
		}
	}
	return false
}

// missingLinks: the links in content, a page at slug, that lead to no
// page: the [[wikilinks]] (as rendering decides it: the name as a slug,
// else slugified) and the markdown links (mdLinkMisses). Each link as
// written → the name or slug it was meant for, to suggest a near match.
func missingLinks(content string, slugs map[string]bool, slug string) map[string]string {
	masked, _ := maskCode(content)
	masked = taskRefRE.ReplaceAllString(deckRE.ReplaceAllString(masked, ""), "")
	missing := map[string]string{}
	for _, m := range wikilinkRE.FindAllStringSubmatch(masked, -1) {
		name, _, _ := splitWikilink(m[1])
		if name != "" && !slugs[name] && !slugs[toSlug(name)] {
			missing["[["+name+"]]"] = name
		}
	}
	for k, v := range mdLinkMisses(content, slug, slugs) {
		missing[k] = v
	}
	return missing
}

// newMissing: the links of missingLinks in new that old didn't have, A–Z.
func newMissing(slug, old, new string, slugs map[string]bool) []string {
	before := missingLinks(old, slugs, slug)
	var added []string
	for k := range missingLinks(new, slugs, slug) {
		if _, ok := before[k]; !ok {
			added = append(added, k)
		}
	}
	sort.Strings(added)
	return added
}

// linkWarning: a note on the links a write added that lead nowhere, so the
// model can fix a typo or create the page.
func linkWarning(slug, old, new string) string {
	added := newMissing(slug, old, new, slugSet())
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

// similarPages: existing pages whose title or file name is close to a new
// page's, so the model notices it may be extending a page rather than
// starting one.
func similarPages(slug, title string) []PageRef {
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
	for _, p := range iterPages() {
		if p.Slug == slug {
			continue
		}
		otherTitle, _ := pageMeta(p.Path, p.Slug)
		close := false
		for _, b := range norm(otherTitle, lastPart(p.Slug)) {
			rb := []rune(b)
			b2j, junk := buildB2J(rb)
			for _, a := range want {
				close = close || ratio([]rune(a), rb, b2j, junk) >= 0.8
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

func msgSuffix(args *Obj) (string, error) {
	s, err := strArg(args, "summary", false)
	if err != nil {
		return "", err
	}
	if s = cutRunes(strings.Join(pySplit(s), " "), 200, ""); s != "" {
		return ": " + s + " (MCP)", nil
	}
	return " (MCP)", nil
}

func commitMsg(action string, args *Obj) (string, error) {
	s, err := msgSuffix(args)
	return action + s, err
}

// savePending: before a write, commit what the page holds that isn't
// committed yet (the browser editor auto-saves without committing; or
// edits on disk), as the user's own "Save <slug>". Else that text would
// end up in the model's commit, or be lost to an overwrite or a delete.
// Returns a note for the model: the user may have the page open.
func savePending(path, slug string) string {
	if path == "" || !exists(path) || !isUncommitted(path) {
		return ""
	}
	gitCommit("Save "+slug, path)
	return "\nNote: " + slug + " had changes that weren't committed yet (the user may be editing it " +
		"in the browser); they were committed first, as \"Save " + slug + "\", and kept."
}

// savePendingPages: savePending for a write to many pages: those with
// uncommitted changes are committed first, together, as "Save <what>".
func savePendingPages(paths []string, what string) string {
	pending := map[string]bool{}
	for _, f := range gitUncommittedFiles() {
		pending[filepath.Join(cfg.Data, f.Filepath)] = true
	}
	var save []string
	for _, p := range paths {
		if pending[p] {
			save = append(save, p)
		}
	}
	if len(save) == 0 {
		return ""
	}
	gitCommit("Save "+what, save...)
	return fmt.Sprintf("\nNote: %d of the pages had changes that weren't committed yet (the user may be "+
		"editing them in the browser); they were committed first, as \"Save %s\", and kept.", len(save), what)
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
var importRE = regexp.MustCompile(`^@(` + pyNonSpace + `+\.md)$`)

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
// of the wiki stays as written.
func guideText(path string, seen map[string]bool, depth int) string {
	seen[path] = true
	text, err := readPageText(path)
	if err != nil {
		return ""
	}
	root := resolvePath(cfg.Data)
	var out []string
	for _, line := range splitLines(text) {
		line = lineBody(line)
		if m := importRE.FindStringSubmatch(pyStrip(line)); m != nil && depth < importDepth {
			target := resolvePath(filepath.Join(filepath.Dir(path), m[1]))
			if rel, err := filepath.Rel(root, target); err == nil && rel != ".." && !strings.HasPrefix(rel, "../") {
				if st, err := os.Stat(target); err == nil && st.Mode().IsRegular() &&
					safePath(strings.TrimSuffix(filepath.ToSlash(rel), ".md")) == target {
					if !seen[target] {
						out = append(out, guideText(target, seen, depth+1))
					}
					continue
				}
			}
		}
		out = append(out, line)
	}
	return pyStrip(strings.Join(out, "\n"))
}

type guide struct{ id, slug, text string }

// folderGuides: the guides for a page at slug: the wiki's first, then each
// folder's on the way down to the page's.
func folderGuides(slug string) []guide {
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
			rel, err := filepath.Rel(root, p)
			if err != nil || rel == ".." || strings.HasPrefix(rel, "../") {
				continue // a link to a file outside the wiki
			}
			if seen[p] {
				continue
			}
			if text := guideText(p, seen, 0); text != "" {
				sum := sha256.Sum256([]byte(text))
				g := strings.TrimSuffix(filepath.ToSlash(rel), filepath.Ext(rel))
				found = append(found, guide{g + ":" + hex.EncodeToString(sum[:])[:8], g, text})
			}
		}
	}
	return found
}

func cutGuide(g guide) string {
	return cutRunes(g.text, folderGuideChars, "\n\n[… cut here: read_page "+pyRepr(g.slug)+" for the rest]")
}

// checkGuides refuses a write to these pages while a guide for them isn't
// among the ids in args' guides: the error gives the missing guides' text.
func checkGuides(args *Obj, slugs []string) error {
	given, err := idsArg(args, "guides", "guides must be a list of guide ids")
	if err != nil {
		return err
	}
	var needed []guide
	ids := map[string]bool{}
	for _, slug := range slugs {
		for _, g := range folderGuides(slug) {
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
		"call again with guides set to " + dumpsLine(strs(sortedKeys(ids))) + ". Pass every guide id you get in " +
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
// guide's title), the root ("") first.
func guidedFolders() [][2]string {
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
		for _, g := range folderGuides(page) {
			if guideFolder(g.slug) == folder {
				title := cutRunes(pageTitle(g.text, ""), 60, "")
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
func toolList() []any {
	folders := guidedFolders()
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
	tools := make([]any, len(mcpTools))
	for i, t := range mcpTools {
		if t.(*Obj).Get("name") == "read_guides" {
			t = t.(*Obj).Copy()
			t.(*Obj).Set("description", t.(*Obj).Str("description")+" Guides in this wiki:\n"+strings.Join(lines, "\n"))
		}
		tools[i] = t
	}
	return tools
}

func readGuides(args *Obj, base string) (string, error) {
	raw, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	raw = pyStrip(raw)
	if _, after, found := strings.Cut(raw, "/page/"); found {
		raw = after
	}
	raw, _, _ = strings.Cut(raw, "#")
	raw = strings.TrimSuffix(raw, ".md")
	folder := strings.Trim(raw, "/")
	var slug string
	switch {
	case strings.HasSuffix(raw, "/") || folder != "" && isDir(filepath.Join(cfg.Data, folder)):
		slug = folder + "/page"
	case folder == "":
		slug = "page"
	default:
		if slug = mcpPageSlug(folder); slug == "" {
			if slug, err = newSlug(folder); err != nil {
				return "", err
			}
		}
	}
	known, err := idsArg(args, "guides", "guides must be a list of guide ids")
	if err != nil {
		return "", err
	}
	found := folderGuides(slug)
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
		"specific one wins. Pass guides=" + dumpsLine(ids) + " with your writes."}
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
func guideIDLine(slug, content string) string {
	for _, g := range folderGuides(slug) {
		if g.slug == slug {
			if g.text != pyStrip(content) {
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
func guideNote(slug string) string {
	for _, g := range folderGuides(slug) {
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
			guide = pyStrip(text)
		}
	}
	if guide == "" {
		return mcpInstructions + "\n\nThis wiki has no guide yet. When the user tells you " +
			"how they want it kept (where things go, which tags, page layouts, what stays " +
			"private), offer to write that down on the page 'wiki-guide': it is shown to " +
			"you at the start of every session."
	}
	guide = cutRunes(guide, guideChars, "\n\n[… cut here: read_page 'wiki-guide' for the rest]")
	// Some clients cut the instructions (Claude Code at about 2 KB), and so
	// the guide; the instructions point to read_guides, which gives it whole.
	return mcpInstructions + "\n\nThe user's guide for this wiki (the page 'wiki-guide'; " +
		"read_guides gives it too if this is cut). Follow it; where it differs from the advice " +
		"above, it wins. When the user settles a new convention, offer to add it there.\n\n" + guide
}

// ── Sections ────────────────────────────────────────────────────────────────

var mcpHeadingRE = regexp.MustCompile(`^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$`)

// headingMatch: a line (without its "\n") as a heading: level and text.
func headingMatch(line string) (int, string, bool) {
	m := mcpHeadingRE.FindStringSubmatch(strings.TrimSuffix(line, "\n"))
	if m == nil {
		return 0, "", false
	}
	return len(m[1]), m[2], true
}

// sectionLines: the lines of the section under heading (any level; its
// text compared without markup and case), from the heading to before the
// next heading of the same or a higher level, subsections included.
func sectionLines(lines []string, heading string) (int, int, bool) {
	want := strings.ToLower(plainHeading(pyStrip(strings.TrimLeft(heading, "#"))))
	start, level, end := -1, 0, len(lines)
	for i, l := range fencedLines(lines) {
		if l.inFence {
			continue
		}
		n, text, ok := headingMatch(l.line)
		if !ok {
			continue
		}
		if start < 0 {
			if strings.ToLower(plainHeading(text)) == want {
				start, level = i, n
			}
		} else if n <= level {
			end = i
			break
		}
	}
	return start, end, start >= 0
}

// noHeading: the error for a heading the page doesn't have, naming those
// it has.
func noHeading(slug, content, heading string) string {
	lines := splitLines(content)
	for i := range lines {
		lines[i] = lineBody(lines[i])
	}
	var names []string
	for _, l := range fencedLines(lines) {
		if _, text, ok := headingMatch(l.line); ok && !l.inFence {
			names = append(names, pyRepr(text))
		}
	}
	list := strings.Join(names, ", ")
	if list == "" {
		list = "none"
	}
	return slug + " has no heading " + pyRepr(heading) + ". Its headings: " + list + "."
}

func trimNewlines(s string) string { return strings.TrimRight(s, "\n") }

// ── Deck tasks ──────────────────────────────────────────────────────────────

var recurrenceUnits = map[string]string{"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}

func shortID(id string) string { return runePrefix(id, 8) }

// mcpTaskText: one deck task for an LLM: title, a line of facts (status,
// tags, dates, repeat, list), its link in the deck, its notes and comments.
func mcpTaskText(t *Task, base string) string {
	status := taskStatus(t)
	fact := "status: " + status
	if status != "open" {
		fact += " " + runePrefix(t.CompletedAt, 10)
	}
	facts := []string{fact}
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
	list := taskList(t, nil)
	if list != "" {
		facts = append(facts, "list: "+list)
	}
	text := "Task: " + t.Title + "\n" + strings.Join(facts, " · ") +
		"\nid: " + shortID(t.ID) + "\nurl: " + base + taskLink(t, list)
	if notes := pyStrip(t.Description); notes != "" {
		text += "\n\n" + notes
	}
	// Comments: the user's status lines, oldest first, each with its time.
	var comments []string
	for _, c := range t.Comments {
		if c.Text != "" {
			at := strings.ReplaceAll(runePrefix(c.At, 16), "T", " ")
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
func mcpTaskRefs(content, base string) string {
	masked, _ := maskCode(content)
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
	tasks := resolveTaskRefs(refs)
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
			base+taskLink(t, taskList(t, nil)))
	}
	return "\n\n---\nDeck tasks referenced on this page ([[task:…]]):\n" + strings.Join(lines, "\n")
}

func searchTasks(args *Obj, base string) (string, error) {
	query, ok1 := orDefault(args, "query", "").(string)
	tag, ok2 := orDefault(args, "tag", "").(string)
	if !ok1 || !ok2 {
		return "", toolErr("query and tag must be strings")
	}
	status := orDefault(args, "status", "open")
	if status != "open" && status != "done" && status != "all" {
		return "", toolErr("status must be open, done or all")
	}
	limit, err := mcpInt(args, "limit", 50, 200)
	if err != nil {
		return "", err
	}
	want := map[string]bool{}
	for _, t := range strings.Split(tag, ",") {
		if t = strings.ToLower(strings.TrimLeft(pyStrip(t), "#")); t != "" {
			want[t] = true
		}
	}
	q := ParseQuery(query)
	var docs []*SearchDoc
	for _, d := range taskSearchDocs() {
		if status != "all" && (status == "open") != d.Task.isOpen() {
			continue
		}
		have := taskTagsLower(d.Task)
		all := true
		for w := range want {
			all = all && have[w]
		}
		if all && passes(q, d, nil) {
			docs = append(docs, d)
		}
	}
	if pyStrip(q.Text) != "" {
		tokens := make([][]string, len(docs))
		for i, d := range docs {
			tokens[i] = d.Tokens
		}
		var ranked []*SearchDoc
		for _, h := range NewIndex(tokens).Search(q.Text, max(len(docs), 1)) {
			ranked = append(ranked, docs[h.Doc])
		}
		if semantic != nil {
			semantic.poke()
			byID := map[string]*SearchDoc{}
			for _, d := range docs {
				byID[d.TaskID] = d
			}
			var hits []*SearchDoc
			for _, h := range semantic.searchTasks(q.Text, docKeys(byID), max(len(byID), 1)) {
				hits = append(hits, byID[h.ID])
			}
			ranked = Fuse([][]*SearchDoc{ranked, hits})
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
		body = append(body, mcpTaskText(d.Task, base))
	}
	return "Today is " + day + ". " + count + ":\n\n" + strings.Join(body, "\n\n---\n\n"), nil
}

var (
	taskLinkRE = regexp.MustCompile(`task[:=]([0-9a-f-]{4,36})`)
	taskIDRE   = regexp.MustCompile(`^[0-9a-f][0-9a-f-]{3,35}$`)
)

// findTask: the task a reference names: its id or a prefix of it (the
// 8-character short id the tools show), a [[task:…]] reference or a
// /deck?task=… link.
func findTask(raw string) (*Task, error) {
	raw = strings.ToLower(pyStrip(raw))
	ref := raw
	if m := taskLinkRE.FindStringSubmatch(raw); m != nil {
		ref = m[1]
	}
	if !taskIDRE.MatchString(ref) {
		return nil, toolErr(pyRepr(raw) + " is not a task id; search_tasks shows each task's id")
	}
	found := findTasks(ref)
	if len(found) == 0 {
		return nil, toolErr("No task with id " + pyRepr(ref) + ". search_tasks shows each task's id.")
	}
	if len(found) > 1 {
		return nil, toolErr("Several tasks have ids starting with " + pyRepr(ref) + "; give more of the id")
	}
	return found[0], nil
}

// dateArg: a date argument, YYYY-MM-DD or a word quick-add knows
// ("tomorrow", "fri", "+3d"), resolved with Leaf's today; nil for none.
func dateArg(args *Obj, name string) (any, error) {
	s, err := strArg(args, name, false)
	if err != nil || pyStrip(s) == "" {
		return nil, err
	}
	day := parseDateWord(pyStrip(s), today())
	if day == "" {
		return nil, toolErr(name + " must be a date: YYYY-MM-DD, today, tomorrow, a weekday or +3d/+2w")
	}
	return day, nil
}

// tagListArg: a list-of-tags argument (a comma-separated string works too),
// each without its leading #.
func tagListArg(args *Obj, name string) ([]string, error) {
	v := args.Get(name)
	if !truthyValue(v) {
		v = []any{}
	}
	if s, ok := v.(string); ok {
		v = strs(strings.Split(s, ","))
	}
	l, ok := v.([]any)
	if !ok {
		return nil, toolErr(name + " must be a list of tags")
	}
	var out []string
	for _, x := range l {
		s, ok := x.(string)
		if !ok {
			return nil, toolErr(name + " must be a list of tags")
		}
		if s = strings.TrimLeft(pyStrip(s), "#"); s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// taskFields: the deck API's fields from the tools' arguments (only those
// given); task, when updating one, for add_tags/remove_tags and repeat.
func taskFields(args *Obj, task *Task) (*Obj, error) {
	data := NewObj()
	for _, f := range [][2]string{{"title", "title"}, {"notes", "description"}} {
		if args.Has(f[0]) {
			s, err := strArg(args, f[0], false)
			if err != nil {
				return nil, err
			}
			data.Set(f[1], orNone(pyStrip(s)))
		}
	}
	for _, f := range [][2]string{{"due", "due_date"}, {"start", "start_date"}} {
		if args.Has(f[0]) {
			day, err := dateArg(args, f[0])
			if err != nil {
				return nil, err
			}
			data.Set(f[1], day)
		}
	}
	if data.Has("title") && !data.Truthy("title") {
		return nil, toolErr("title must be a non-empty string")
	}
	if data.Has("description") {
		data.Set("description", firstTruthy(data.Get("description"), ""))
	}
	if args.Has("keep") {
		keep, ok := args.Get("keep").(bool)
		if !ok {
			return nil, toolErr("keep must be true or false")
		}
		data.Set("keep", keep)
	}
	if args.Has("tags") {
		data.Set("tags", firstNonNil(args.Get("tags"), []any{}))
	}
	if task != nil && (args.Has("add_tags") || args.Has("remove_tags")) {
		var tags []string
		if data.Has("tags") {
			l, _ := data.Get("tags").([]any)
			for _, t := range l {
				tags = append(tags, pyStr(t))
			}
		} else {
			tags = append([]string{}, task.Tags...)
		}
		lower := map[string]bool{}
		for _, t := range tags {
			lower[strings.ToLower(t)] = true
		}
		add, err := tagListArg(args, "add_tags")
		if err != nil {
			return nil, err
		}
		for _, t := range add {
			if !lower[strings.ToLower(t)] {
				tags = append(tags, t)
			}
		}
		remove, err := tagListArg(args, "remove_tags")
		if err != nil {
			return nil, err
		}
		drop := map[string]bool{}
		for _, t := range remove {
			drop[strings.ToLower(t)] = true
		}
		kept := []string{}
		for _, t := range tags {
			if !drop[strings.ToLower(t)] {
				kept = append(kept, t)
			}
		}
		data.Set("tags", strs(kept))
	}
	if args.Has("repeat") || args.Has("repeat_every") || args.Has("repeat_from") {
		var rec any = "none"
		interval, from := any(int64(1)), any("due")
		if task != nil && task.Recurrence != nil {
			rec, interval, from = task.Recurrence.Type, int64(task.Recurrence.Interval), task.Recurrence.From
		}
		repeat := orDefault(args, "repeat", rec)
		switch repeat {
		case "daily", "weekly", "monthly", "yearly":
			data.Set("rec_type", repeat)
		case "none":
			data.Set("rec_type", nil)
		default:
			return nil, toolErr("repeat must be daily, weekly, monthly, yearly or none")
		}
		data.Set("rec_interval", orDefault(args, "repeat_every", interval))
		from = orDefault(args, "repeat_from", from)
		if from != "due" && from != "completion" {
			return nil, toolErr("repeat_from must be due or completion")
		}
		data.Set("rec_from", from)
	}
	return data, nil
}

// firstTruthy is Python's a or b.
func firstTruthy(a, b any) any {
	if truthyValue(a) {
		return a
	}
	return b
}

func isString(v any) bool { _, ok := v.(string); return ok }

func firstNonNil(v, d any) any {
	if v == nil {
		return d
	}
	return v
}

// deckWrite: a deck write's refusals (a bad date, an empty title …) as
// tool errors the model can correct.
func deckWrite[T any](v T, err error) (T, error) {
	var ae *apiError
	switch {
	case errors.Is(err, errDeckCorrupt):
		return v, toolErr("The deck's storage can't be read right now; try again")
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
func tagHints(task *Task) string {
	var others []*SearchDoc
	for _, d := range taskSearchDocs() {
		if d.TaskID != task.ID {
			others = append(others, d)
		}
	}
	have := taskTagsLower(task)
	text := ParseQuery(task.Title + " " + task.Description).Text
	counts := map[string]int{}
	var order []string
	if pyStrip(text) != "" {
		tokens := make([][]string, len(others))
		for i, d := range others {
			tokens[i] = d.Tokens
		}
		for _, h := range NewIndex(tokens).Search(text, similarTasks) {
			for _, tag := range others[h.Doc].Task.Tags {
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
	for _, p := range iterPages() {
		if content, err := readPageText(p.Path); err == nil {
			for _, t := range pageTags(content) {
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

// homeTags: a new task's tags, the deck home's new-task tags (its list, its
// single tags, as its "+ New" and c t give them) before the ones given, so
// the task shows in the user's deck. Not when the given tags already name a
// declared list: the task is filed there.
func homeTags(tags any) any {
	if !truthyValue(tags) {
		tags = []any{}
	}
	if s, ok := tags.(string); ok {
		tags = strs(strings.Split(s, ","))
	}
	l, ok := tags.([]any)
	if !ok {
		return tags // createTask refuses it
	}
	home := lookupDeckHome()
	if home == nil {
		return l
	}
	lists := deckLists()
	for _, t := range l {
		if _, ok := lists[strings.ToLower(strings.TrimLeft(pyStr(t), "#"))]; ok {
			return l
		}
	}
	return strs(cleanTags(append(strs(newTaskTags(home.Params)), l...)))
}

func createTaskTool(args *Obj, base string) (string, error) {
	if _, err := strArg(args, "title", true); err != nil {
		return "", err
	}
	data, err := taskFields(args, nil)
	if err != nil {
		return "", err
	}
	data.Set("tags", homeTags(data.Get("tags")))
	msg, err := commitMsg("Create deck task", args)
	if err != nil {
		return "", err
	}
	task, err := deckWrite(createTask(data, msg))
	if err != nil {
		return "", err
	}
	return "Created (today is " + isoDay(today()) + ").\n\n" + mcpTaskText(task, base) + tagHints(task), nil
}

var taskStatusFields = map[string]*Obj{
	"done": NewObj("completed", true), "open": NewObj("completed", false),
	"wont_do": NewObj("wont_do", true), "skip": NewObj("skip", true),
}

// otherArgs: the arguments besides those named, A–Z.
func otherArgs(args *Obj, allowed ...string) []string {
	var other []string
	for _, k := range args.Keys() {
		if !slices_contains(allowed, k) {
			other = append(other, k)
		}
	}
	sort.Strings(other)
	return other
}

// updateTaskTool: one task's fields, status and comment; status deleted
// deletes it; ids instead of id tags several tasks (tagTasksTool).
func updateTaskTool(args *Obj, base string) (string, error) {
	if args.Has("ids") {
		if args.Has("id") {
			return "", toolErr("Give id or ids, not both")
		}
		if other := otherArgs(args, "ids", "add_tags", "remove_tags", "summary"); len(other) > 0 {
			return "", toolErr("With ids only add_tags and remove_tags work, not " + strings.Join(other, ", ") +
				"; change the rest one task (id) at a time")
		}
		return tagTasksTool(args)
	}
	raw, err := strArg(args, "id", true)
	if err != nil {
		return "", err
	}
	task, err := findTask(raw)
	if err != nil {
		return "", err
	}
	status := args.Get("status")
	if status == "deleted" {
		if other := otherArgs(args, "id", "status", "summary"); len(other) > 0 {
			return "", toolErr("status deleted takes nothing else, not " + strings.Join(other, ", "))
		}
		msg, err := commitMsg("Delete deck task", args)
		if err != nil {
			return "", err
		}
		if _, err := deckWrite(struct{}{}, deleteTask(task.ID, msg)); err != nil {
			return "", err
		}
		return "Deleted task " + shortID(task.ID) + ": " + task.Title, nil
	}
	var comment *string
	if args.Has("comment") {
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
	if args.Has("status") {
		fields, ok := taskStatusFields[pyStr(status)]
		if !ok || !isString(status) {
			return "", toolErr("status must be open, done, wont_do, skip or deleted")
		}
		for _, k := range fields.Keys() {
			data.Set(k, fields.Get(k))
		}
	}
	if data.Len() == 0 && comment == nil {
		return "", toolErr("Nothing to change: give the fields to set, a status or a comment")
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if data.Len() > 0 {
		if task, err = deckWrite(updateTask(task.ID, data, suffix)); err != nil {
			return "", err
		}
	}
	if comment != nil {
		if task, err = deckWrite(addComment(task.ID, *comment, "Comment on deck task"+suffix)); err != nil {
			return "", err
		}
	}
	head := "Comment added."
	if data.Len() > 0 && comment != nil {
		head = "Updated, comment added."
	} else if data.Len() > 0 {
		head = "Updated."
	}
	out := head + "\n\n" + mcpTaskText(task, base)
	if status == "done" || status == "skip" {
		for _, t := range readDeck() {
			if t.SpawnedFrom == task.ID && t.isOpen() {
				out += "\n\nNext occurrence:\n\n" + mcpTaskText(t, base)
				break
			}
		}
	}
	return out, nil
}

// More ids than this in one call is likely a mistake (a whole deck retagged).
const tagTasksMax = 200

func tagTasksTool(args *Obj) (string, error) {
	ids, ok := args.Get("ids").([]any)
	if !ok || len(ids) == 0 {
		return "", toolErr("ids must be a non-empty list of task ids")
	}
	for _, id := range ids {
		if !isString(id) {
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
	for _, raw := range ids {
		t, err := findTask(raw.(string)) // an unknown id fails the whole call
		if err != nil {
			return "", err
		}
		if _, ok := before[t.ID]; !ok {
			found = append(found, t.ID)
			before[t.ID] = append([]string{}, t.Tags...)
		}
	}
	msg, err := commitMsg("Tag deck tasks", args)
	if err != nil {
		return "", err
	}
	tasks, err := deckWrite(tagTasks(found, add, remove, msg))
	if err != nil {
		return "", err
	}
	changed := 0
	var lines []string
	for _, t := range tasks {
		tags := append([]string{}, t.Tags...)
		if strings.Join(tags, "\x00") != strings.Join(before[t.ID], "\x00") || len(tags) != len(before[t.ID]) {
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
func mcpPageLinks(slug, content string) string {
	var linking []string
	for _, b := range findBacklinks(slug) {
		linking = append(linking, b.Slug)
	}
	shown := strings.Join(linking[:min(len(linking), backlinksShown)], ", ")
	if shown == "" {
		shown = "no other page"
	}
	if len(linking) > backlinksShown {
		shown += fmt.Sprintf(", … and %d more", len(linking)-backlinksShown)
	}
	out := fmt.Sprintf("\n\n---\nLinked from (%d): %s", len(linking), shown)
	slugs := slugSet()
	if missing := missingLinks(content, slugs, slug); len(missing) > 0 {
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
		if related := semantic.related(slug, slugs, linked, 5); len(related) > 0 {
			out += "\nRelated, not linked (close in meaning): " + joinIDs(related)
		}
	}
	return out
}

func readPage(args *Obj, base string) (string, error) {
	raw, ok := args.Get("slug").(string)
	if !ok || pyStrip(raw) == "" {
		return "", toolErr("slug must be a non-empty string")
	}
	slug := mcpPageSlug(raw)
	if slug == "" {
		return "", toolErr("No page " + pyRepr(raw) + ". Use search_wiki or list_pages to find its slug.")
	}
	content, err := readPageText(safePath(slug))
	if err != nil {
		return "", err
	}
	head := "slug: " + slug + "\n" + pageInfo(slug, base) + "\n"
	heading, err := strArg(args, "heading", false)
	if err != nil {
		return "", err
	}
	if heading = pyStrip(heading); heading != "" {
		lines := splitLines(content)
		start, end, ok := sectionLines(lines, heading)
		if !ok {
			return "", toolErr(noHeading(slug, content, heading) + " Leave out heading for the whole page.")
		}
		// Line numbers as grep_wiki gives them, so the two fit together.
		head += fmt.Sprintf("section: lines %d–%d of %d\n", start+1, end, len(lines))
		content = trimNewlines(strings.Join(lines[start:end], "")) + "\n"
	} else {
		head += guideIDLine(slug, content)
	}
	return head + "\n" + content + mcpTaskRefs(content, base) + mcpPageLinks(slug, content), nil
}

// isOrphan: no other page links to it. The home page, the deck's home and
// scratch notes are reached otherwise (the module switcher), and the guide
// is read by the assistant, so they never are.
func isOrphan(slug string, counts map[string]int, home string) bool {
	return counts[slug] == 0 && !isProtected(slug) && slug != home && slug != guideSlug && !isScratch(slug)
}

func listPages(args *Obj, base string) (string, error) {
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
	onlyScratch, err := boolArg(args, "scratch")
	if err != nil {
		return "", err
	}
	counts := allBacklinkCounts()
	home := ""
	if h := lookupDeckHome(); h != nil {
		home = h.Slug
	}
	type row struct {
		mtime       int64
		slug, title string
	}
	var rows []row
	for _, p := range iterPages() {
		if !inFolder(p.Slug, under) || orphans && !isOrphan(p.Slug, counts, home) || onlyScratch && !isScratch(p.Slug) {
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
	where := ""
	if under != "" {
		where = " under " + under + "/"
	}
	if len(rows) == 0 {
		switch {
		case onlyScratch:
			return "No scratch notes" + where + ".", nil
		case orphans:
			return "Every page" + where + " is linked from another page.", nil
		case under != "":
			return "No pages under " + pyRepr(under) + ".", nil
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
	if onlyScratch {
		what = "Scratch notes"
	} else if orphans {
		what = "Pages no other page links to (the home page, the deck's home and scratch notes aside)"
	}
	return what + where + ", most recently edited first (" + shown + "); " +
		"slug | title | last edit | pages linking to it:\n" + strings.Join(lines, "\n"), nil
}

func checkLinks(args *Obj, base string) (string, error) {
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	slugs := slugSet()
	all := allSlugs()
	var out []string
	total := 0
	for _, p := range sortedPages() {
		if !inFolder(p.Slug, under) {
			continue
		}
		content, err := readPageText(p.Path)
		if err != nil {
			continue
		}
		missing := missingLinks(content, slugs, p.Slug)
		if len(missing) == 0 {
			continue
		}
		var links []string
		for _, link := range sortedKeys(missing) {
			name := missing[link]
			if strings.HasPrefix(link, "[[") {
				name = toSlug(name)
			}
			if near := closeMatches(name, all, 1, 0.8); len(near) > 0 {
				link += " (maybe [[" + near[0] + "]]?)"
			}
			links = append(links, link)
		}
		total += len(links)
		out = append(out, p.Slug+": "+strings.Join(links, ", "))
	}
	where := ""
	if under != "" {
		where = " under " + under + "/"
	}
	if len(out) == 0 {
		return "No broken links" + where + ".", nil
	}
	return fmt.Sprintf("%d links%s point to pages that don't exist, on %d pages "+
		"(fix the link, or create the page):\n", total, where, len(out)) + strings.Join(out, "\n"), nil
}

// How much of a long line grep_wiki and replace_in_pages show.
const grepLineChars = 300

func cutLine(line string) string { return cutRunes(pyStrip(line), grepLineChars, " …") }

func grepWiki(args *Obj, base string) (string, error) {
	pattern, ok := args.Get("pattern").(string)
	if !ok || pyStrip(pattern) == "" {
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
	found, err := grepPages(pattern, ignoreCase)
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
	where := ""
	if under != "" {
		where = " under " + under + "/"
	}
	if len(hits) == 0 {
		return "No line" + where + " matches " + pyRepr(pattern) + ".", nil
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
// deck's files as tasks.
func fileLabel(fp string) string {
	if strings.HasPrefix(fp, "_deck/") {
		return "deck tasks"
	}
	return strings.TrimSuffix(fp, ".md")
}

func noGit() error { return toolErr("There is no history: git isn't installed where Leaf runs.") }

func history(args *Obj, base string) (string, error) {
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
	raw, version = pyStrip(raw), strings.ToLower(pyStrip(version))
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
		day, _ := since.(string)
		return recentChanges(day, under, limit), nil
	}
	slug := mcpPageSlug(raw) // a deleted page has a history too
	if slug == "" {
		if slug, err = newSlug(raw); err != nil {
			return "", err
		}
	}
	path := safePath(slug)
	entries := gitLog(path, 0)
	if len(entries) == 0 {
		return "", toolErr("No history for " + pyRepr(raw) + ". Use list_pages to find its slug.")
	}
	if version != "" {
		return pageChange(slug, path, entries, version)
	}
	var rows []string
	for _, e := range entries[:min(limit, len(entries))] {
		rows = append(rows, fmt.Sprintf("%s | %s | +%d -%d | %s", runePrefix(e.Sha, 7), e.Time, e.Added, e.Deleted, e.Msg))
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
		return "", toolErr(pyRepr(version) + " is not a version of " + slug + "; history lists them.")
	}
	e := entries[i]
	paths := []string{versionPath(path, entries, e.Sha)}
	if i+1 < len(entries) {
		paths = append(paths, filepath.Join(cfg.Data, filepath.FromSlash(entries[i+1].Path)))
	}
	diff, _ := gitDiff(e.Sha, paths...)
	if diff == "" {
		diff = "(no changes)"
	}
	return slug + ", version " + runePrefix(e.Sha, 7) + " of " + e.Time + ": " + e.Msg + "\n\n" + diff, nil
}

func recentChanges(since, under string, limit int) string {
	entries := gitLogAll(since, under, limit)
	var out, pending []string
	for _, f := range gitUncommittedFiles() {
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
	where, when := "", ""
	if under != "" {
		where = " under " + under + "/"
	}
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
			if l := fileLabel(f); !slices_contains(labels, l) {
				labels = append(labels, l)
			}
		}
		rows = append(rows, e.Time+" | "+runePrefix(e.Sha, 7)+" | "+e.Msg+" | "+strings.Join(labels, ", "))
	}
	theLimit := ""
	if len(rows) == limit {
		theLimit = ", the limit"
	}
	out = append(out, fmt.Sprintf("Changes%s%s, newest first (%d%s); when | version | what (\"(MCP)\": written by "+
		"an assistant) | pages:\n", where, when, len(rows), theLimit)+strings.Join(rows, "\n"))
	return strings.Join(out, "\n\n")
}

func listTags(args *Obj, base string) (string, error) {
	counts := map[string]*[3]int{} // tag → pages, open tasks, done tasks
	count := func(tag string, column int) {
		if counts[tag] == nil {
			counts[tag] = &[3]int{}
		}
		counts[tag][column]++
	}
	for _, p := range iterPages() {
		content, err := readPageText(p.Path)
		if err != nil {
			continue
		}
		seen := map[string]bool{}
		for _, t := range pageTags(content) {
			if !seen[t] {
				seen[t] = true
				count(t, 0)
			}
		}
	}
	for _, t := range allTasks() {
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
	lists := deckLists()
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

// leafHelp: Leaf's documentation (manual.go), passages for a query, a doc
// or one of its sections, or the list of docs with their headings.
func leafHelp(args *Obj, base string) (string, error) {
	var query, topic, heading string
	for _, a := range []struct {
		name string
		v    *string
	}{{"query", &query}, {"topic", &topic}, {"heading", &heading}} {
		s, err := strArg(args, a.name, false)
		if err != nil {
			return "", err
		}
		*a.v = pyStrip(s)
	}
	topic = strings.TrimSuffix(strings.ToLower(topic), ".md")
	docs := manualTopics()
	if len(docs) == 0 {
		return "", toolErr("Leaf's documentation isn't installed with this server.")
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
			return "", toolErr("No topic " + pyRepr(topic) + ". Topics: " + strings.Join(names, ", ") + ".")
		}
		text, err := readText(path)
		if err != nil {
			return "", err
		}
		if heading == "" {
			return "topic: " + topic + "\n\n" + text, nil
		}
		lines := splitLines(text)
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
			return "Nothing in Leaf's documentation matches. Try other words, or leave out query for the list of topics.", nil
		}
		var out []string
		for i, d := range hits {
			if d.heading != "" {
				out = append(out, fmt.Sprintf("[%d] %s › %s\ntopic: %s\nheading: %s\n\n%s", i+1, d.title, d.heading, d.topic, d.heading, d.text))
			} else {
				out = append(out, fmt.Sprintf("[%d] %s\ntopic: %s\n\n%s", i+1, d.title, d.topic, d.text))
			}
		}
		return strings.Join(out, "\n\n---\n\n") + "\n\nThe whole section: leaf_help with its topic and heading.", nil
	}
	out := []string{"Leaf's documentation: the README (what Leaf is, its features, setup) and the spec, " +
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

func searchWiki(args *Obj, base string) (string, error) {
	query, ok := args.Get("query").(string)
	if !ok || pyStrip(query) == "" {
		return "", toolErr("query must be a non-empty string")
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	if under != "" && !anyPageIn(under) {
		return "", toolErr("No pages under " + pyRepr(under) + ". list_pages shows the slugs, " +
			"whose folders are the part before the last '/'.")
	}
	limit, err := mcpInt(args, "limit", 8, 20)
	if err != nil {
		return "", err
	}
	scratch, err := boolArg(args, "scratch")
	if err != nil {
		return "", err
	}
	hits := retrieve(query, limit, under, scratch)
	logQuery(query, under, hits)
	if len(hits) == 0 {
		return "No matching sections or tasks. Try other words, or list_pages to browse.", nil
	}
	var out []string
	for i, d := range hits {
		if d.isTask() {
			out = append(out, fmt.Sprintf("[%d] ", i+1)+mcpTaskText(d.Task, base))
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

func anyPageIn(folder string) bool {
	for _, p := range iterPages() {
		if inFolder(p.Slug, folder) {
			return true
		}
	}
	return false
}

// ── Dispatch ────────────────────────────────────────────────────────────────

var mcpToolFuncs = map[string]func(args *Obj, base string) (string, error){
	"search_wiki": searchWiki, "search_tasks": searchTasks, "grep_wiki": grepWiki, "read_page": readPage,
	"list_pages": listPages, "check_links": checkLinks, "history": history, "list_tags": listTags,
	"leaf_help": leafHelp, "read_guides": readGuides,
	"write_page": writePage, "edit_page": editPageTool, "replace_in_pages": replaceInPages,
	"append_to_page": appendToPage, "file_scratch_note": fileScratchNote, "rename_page": renamePageTool,
	"delete_page": deletePageTool, "restore_page": restorePage, "create_task": createTaskTool,
	"update_task": updateTaskTool,
}

// callMCPTool runs a tool for the request r: links in its answer use
// LEAF_BASE_URL, else the address the client reached Leaf at.
func callMCPTool(r *http.Request, name string, args *Obj) (string, error) {
	base := cfg.BaseURL
	if base == "" {
		scheme := "http"
		if r.TLS != nil {
			scheme = "https"
		}
		base = scheme + "://" + r.Host
	}
	f, ok := mcpToolFuncs[name]
	if !ok {
		return "", toolErr("Unknown tool: " + name)
	}
	return f(args, base)
}

func joinIDs(hits []idScore) string {
	ids := make([]string, len(hits))
	for i, h := range hits {
		ids[i] = h.ID
	}
	return strings.Join(ids, ", ")
}
