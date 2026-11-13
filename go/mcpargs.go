// What the MCP tools share: reading their arguments, naming pages, link
// warnings, commit messages, a page's sections.
package main

import (
	"encoding/json"
	"os"
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
	if readOnly(user, slug) {
		return "", toolErr(readOnlyWhy(user))
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
