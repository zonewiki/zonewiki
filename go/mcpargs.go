// What the MCP tools share: reading their arguments, naming pages, link
// warnings, commit messages, a page's sections.
package main

import (
	"encoding/json"
	"os"
	"path"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
	"zonewiki/internal/jsonobj"
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
	if !args.Has(name) {
		return def, nil
	}
	n, ok := jsonobj.WholeNumber(args[name])
	if s, isStr := jsonobj.As[string](args[name]); isStr && digitsRE.MatchString(strings.TrimSpace(s)) {
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
	s, ok := jsonobj.As[string](args[name])
	if !ok && !args.Null(name) {
		return "", toolErr(name + " must be a string")
	}
	s = newlines(s)
	if required && strings.TrimSpace(s) == "" {
		return "", toolErr(name + " must be a non-empty string")
	}
	return s, nil
}

// queryArg: a search's query, at most textsearch.MaxQueryChars long (a
// search is a few words; textsearch says what a longer one costs).
func queryArg(args jsonObj, required bool) (string, error) {
	q, err := strArg(args, "query", required)
	if err == nil && textsearch.RuneLen(q) > textsearch.MaxQueryChars {
		err = toolErr("query must be at most " + strconv.Itoa(textsearch.MaxQueryChars) + " characters: search with a few words")
	}
	return q, err
}

// boolArg: true or false; missing or null is false.
func boolArg(args jsonObj, name string) (bool, error) {
	b, ok := jsonobj.As[bool](args[name])
	if !ok && !args.Null(name) {
		return false, toolErr(name + " must be true or false")
	}
	return b, nil
}

// listArg: a list of strings, or one string split by split; missing or
// null is none.
func listArg(args jsonObj, name, msg string, split func(string) []string) ([]string, error) {
	if args.Null(name) {
		return nil, nil
	}
	if s, ok := jsonobj.As[string](args[name]); ok {
		return split(s), nil
	}
	l, ok := jsonobj.As[[]json.RawMessage](args[name])
	out := make([]string, len(l))
	for i, x := range l {
		if out[i], ok = jsonobj.As[string](x); !ok {
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
func mcpPageSlug(raw string, user asker) string {
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
	return "url: " + base + pathURL("page", slug) + "\nfile: " + hostFile(slug) + pageSeenBy(slug)
}

// pageSeenBy: a line saying who sees the page slug, when it isn't everyone
// ("" for a shared page): what an assistant must know before it quotes the
// page elsewhere, or links to it from a page more people read.
func pageSeenBy(slug string) string {
	switch p := placeOf(slug); p.kind {
	case ofUser:
		return "\nseen by: only " + p.name + " (a private page, in " + p.folder() + "/)"
	case ofGroup:
		return "\nseen by: the group @" + p.name + " only (" + strings.TrimSuffix(strings.TrimSuffix(groupWho(p.name), " sees"), " see") +
			"; its folder is " + p.folder() + "/). Not for a page or task outside the group"
	}
	return ""
}

// readPageText: a page's text, undecodable bytes as U+FFFD.
func readPageText(path string) (string, error) {
	text, err := readText(path)
	return strings.ToValidUTF8(text, "�"), err
}

func existingPage(args jsonObj, name string, user asker) (string, string, error) {
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

// pageName: the slug a page of a name or slug has ("Tickets/BUG 1234" →
// "tickets/bug-1234"), whether or not it is there; refused when it would be
// outside the wiki or in a folder Zone skips (_assets, _tasks, .git …).
func pageName(raw string) (string, error) {
	raw = strings.TrimSuffix(strings.Trim(slugInput(raw), "/"), ".md")
	slug := mdtext.ToSlug(raw)
	if slug == "" || slices.Contains(strings.Split(slug, "/"), "") {
		return "", toolErr(strconv.Quote(raw) + " is not a usable page name")
	}
	if safePath(slug) == "" {
		return "", toolErr(strconv.Quote(slug) + ": pages can't go in folders starting with '_' or '.'")
	}
	return slug, nil
}

// newSlug: the slug a new page gets from a name or slug (pageName), also
// refused where user may not write: in private/ outside their own folder,
// a guide for anyone but an admin, anything for a viewer.
func newSlug(raw string, user asker) (string, error) {
	slug, err := pageName(raw)
	if err != nil {
		return "", err
	}
	if readOnly(user, slug) {
		return "", toolErr(readOnlyWhy(user, slug))
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
func missingLinks(content string, slugs map[string]bool, slug string, user asker) map[string]string {
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
func newMissing(slug, old, new string, user asker, slugs map[string]bool) []string {
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
func linkWarning(slug, old, new string, user asker) string {
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
// that name would get (a deleted page's, say), for a tool that writes it.
func pageOrNewSlug(raw string, user asker) (string, error) {
	if slug := mcpPageSlug(raw, user); slug != "" {
		return slug, nil
	}
	return newSlug(raw, user)
}

// pageOrSeenSlug: pageOrNewSlug for a tool that only reads (a deleted
// page's history, the guides for a page not yet written): a slug user may
// see, not one they may write, so a viewer or a connection that only reads
// asks too. One they may not see is refused as newSlug refuses it, saying
// nothing of whether it is there.
func pageOrSeenSlug(raw string, user asker) (string, error) {
	if slug := mcpPageSlug(raw, user); slug != "" {
		return slug, nil
	}
	slug, err := pageName(raw)
	if err == nil && !canSee(user, slug) {
		return "", toolErr(notYours(slug, user))
	}
	return slug, err
}

// notYours: the refusal of a page in private/ that isn't in user's own
// folder, or in groups/ and in no folder of a group they are in (which
// says nothing of whether it exists).
func notYours(slug string, user asker) string {
	if dir, _, _ := strings.Cut(strings.ToLower(path.Clean(slug)), "/"); dir == groupsDir && settings().Users != nil {
		if in := groupsFor(user); len(in) > 0 {
			return strconv.Quote(slug) + ": of " + groupsDir + "/, only the folders of the groups you are in are yours to read or write (" +
				groupsDir + "/" + strings.Join(in, "/, "+groupsDir+"/") + "/)"
		}
		return strconv.Quote(slug) + ": " + groupsDir + "/ holds the groups' folders, which only a group's members see, and you are in no group"
	}
	if lacksPrivate(user) {
		return strconv.Quote(slug) + ": private/ holds the users' private folders, and this connection wasn't given yours"
	}
	if own := privateFolder(user); own != "" {
		return strconv.Quote(slug) + ": of private/, only your own folder " + own + "/ is yours to read or write"
	}
	return strconv.Quote(slug) + ": private/ holds the users' private folders, which only their users see"
}

// similarPages: existing pages whose title or file name is close to a new
// page's, so the model notices it may be extending a page rather than
// starting one.
func similarPages(slug, title string, user asker) []PageRef {
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
// Every write is its own git commit, its message's first line ending in
// "(MCP)" for who reads git log. The model's summary, when given, says what
// changed. After that line, a trailer names the connection the write came
// through ("Assistant: Claude", "Assistant: MCP token": commitTrailers),
// which is what tells the history the model's changes from the user's
// (commitMarkOf).

// viaArg: the argument callMCPTool sets to the name of the connection a
// call came through ("" none: an MCP token). No client sets it: what one
// sends there is dropped.
const viaArg = "\x00via"

// mcpMark: what ends the message of a commit a tool makes: " (MCP)", and
// the trailers naming its connection (or the token) and, when the call
// says, where its text is from (source).
func mcpMark(args jsonObj) string {
	via, _ := jsonobj.As[string](args[viaArg])
	source, _ := jsonobj.As[string](args["source"])
	return " " + assistantMark + commitTrailers(via, source)
}

func msgSuffix(args jsonObj) (string, error) {
	s, err := strArg(args, "summary", false)
	if err != nil {
		return "", err
	}
	if s = cutRunes(strings.Join(strings.Fields(s), " "), 200, ""); s != "" {
		return ": " + s + mcpMark(args), nil
	}
	return mcpMark(args), nil
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
