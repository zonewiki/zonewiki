// The MCP tools that write pages: write_page (also a
// scratch note), edit_page, append_to_page, replace_in_pages, rename_page
// (also a folder), delete_page, restore_page, file_scratch_note. Each write
// is a git commit, refused while a guide for the page isn't confirmed
// (checkGuides).
package main

import (
	"errors"
	"fmt"
	"maps"
	"os"
	"regexp"
	"regexp/syntax"
	"strconv"
	"strings"
	"time"
	"unicode"
)

func unixNano(n int64) time.Time { return time.Unix(0, n) }

func writePage(args *Obj, base string) (string, error) {
	content, err := strArg(args, "content", true)
	if err != nil {
		return "", err
	}
	scratch, err := boolArg(args, "scratch")
	if err != nil {
		return "", err
	} else if scratch {
		return writeScratch(args, content, base)
	}
	overwrite := args.Get("overwrite") == true
	raw, err := strArg(args, "slug", true)
	if err != nil {
		return "", err
	}
	existing := mcpPageSlug(raw)
	if existing != "" && !overwrite {
		return "", toolErr("Page " + pyRepr(existing) + " already exists. Change it with edit_page or append_to_page, " +
			"or set overwrite=true to replace all of it.")
	}
	slug := existing
	if slug == "" {
		if slug, err = newSlug(raw); err != nil {
			return "", err
		}
	}
	if err := checkGuides(args, []string{slug}); err != nil {
		return "", err
	}
	path := safePath(slug)
	pending, old := "", ""
	if existing != "" {
		pending = savePending(path, slug)
		if old, err = readText(path); err != nil {
			return "", err
		}
	}
	content = trimNewlines(content) + "\n"
	action := "Create "
	if existing != "" {
		action = "Update "
	}
	msg, err := commitMsg(action+slug, args)
	if err != nil {
		return "", err
	}
	if err := writeText(path, content); err != nil {
		return "", err
	}
	gitCommit(msg, path)
	out := "Created page " + slug + ".\n" + pageInfo(slug, base)
	if existing != "" {
		out = "Replaced page " + slug + ".\n" + pageInfo(slug, base)
	} else {
		similar := similarPages(slug, pageTitle(content, slug))
		named := map[string]bool{}
		if len(similar) > 0 {
			out += "\nNote: pages with similar names exist; if this one repeats one of them, " +
				"merge it there (edit_page) and delete_page this one:"
			for _, p := range similar {
				out += "\n- " + p.Slug + " | " + p.Title
				named[p.Slug] = true
			}
		}
		out += closeInContent(slug, content, named)
	}
	return out + pending + linkWarning(slug, old, content) + guideNote(slug), nil
}

// writeScratch: write_page with scratch, a new scratch note, named by slug
// (any words; Leaf puts the time in front) or else by the content's title.
func writeScratch(args *Obj, content, base string) (string, error) {
	if args.Get("overwrite") == true {
		return "", toolErr("A scratch note is always a new one: overwrite doesn't apply. " +
			"Change an existing note with edit_page.")
	}
	name, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	name = pyStrip(name)
	title := pageTitle(content, "")
	if pyStrip(title) == "" && name == "" {
		return "", toolErr("Give the note a title: start content with '# Title', or set slug.")
	}
	if pyStrip(title) == "" { // the title goes under the frontmatter, if any
		_, body := parseFrontmatter(content)
		content = content[:len(content)-len(body)] + "# " + name + "\n\n" + strings.TrimLeftFunc(body, pyIsSpace)
	}
	// Scratch notes sit at the wiki's root: the wiki's guide applies.
	if err := checkGuides(args, []string{"scratch-note"}); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if name == "" {
		name = title
	}
	slug, err := scratchErr(createScratch(name, &content, suffix))
	if err != nil {
		return "", err
	}
	return "Created scratch note " + slug + " (in Scratch, not yet in the wiki; file_scratch_note " +
		"moves it there or appends it to a page).\n" + pageInfo(slug, base) + linkWarning(slug, "", content), nil
}

// scratchErr: a scratch note's refusal as a tool error.
func scratchErr(s string, err error) (string, error) {
	var se *scratchError
	if errors.As(err, &se) {
		return "", toolErr(se.msg)
	}
	return s, err
}

// fileScratchNote: the scratch note's Move to wiki (name, folder,
// keep_date) or Append to page (page, heading), as in the browser.
func fileScratchNote(args *Obj, base string) (string, error) {
	slug, path, err := existingPage(args, "slug")
	if err != nil {
		return "", err
	}
	if !isScratch(slug) {
		return "", toolErr(slug + " is not a scratch note. rename_page moves a page, append_to_page adds text to one.")
	}
	page, err := strArg(args, "page", false)
	if err != nil {
		return "", err
	}
	name, err := strArg(args, "name", false)
	if err != nil {
		return "", err
	}
	page, name = pyStrip(page), pyStrip(name)
	if (page == "") == (name == "") {
		return "", toolErr("Give either name (to move the note into the wiki as its own page) " +
			"or page (to append it to that page as a section), not both.")
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if page != "" {
		target := mcpPageSlug(page)
		if target == "" {
			return "", toolErr("No page " + pyRepr(page) + ". Use search_wiki or list_pages to find its slug.")
		}
		heading, err := strArg(args, "heading", false)
		if err != nil {
			return "", err
		}
		if heading = pyStrip(heading); heading == "" {
			text, err := readText(path)
			if err != nil {
				return "", err
			}
			heading = appendHeading(slug, pageTitle(text, slug))
		}
		if err := checkGuides(args, []string{target}); err != nil {
			return "", err
		}
		pending := savePending(path, slug) + savePending(safePath(target), target)
		anchor, err := scratchErr(appendScratch(slug, target, heading, suffix))
		if err != nil {
			return "", err
		}
		return "Appended scratch note " + slug + " to " + target + " as the section “" + heading + "”, and deleted " +
			"the note; links to it now point at that section.\nurl: " + base + pathURL("page", target) + "#" + anchor +
			"\nfile: " + hostFile(target) + pending, nil
	}
	keepDate, err := boolArg(args, "keep_date")
	if err != nil {
		return "", err
	}
	folder, err := strArg(args, "folder", false)
	if err != nil {
		return "", err
	}
	date := ""
	if keepDate {
		date = noteDate(slug)
	}
	if s := promotedSlug(folder, name, date); s != "" {
		if err := checkGuides(args, []string{s}); err != nil {
			return "", err
		}
	}
	pending := savePending(path, slug)
	moved, err := scratchErr(moveScratch(slug, folder, name, keepDate, suffix))
	if err != nil {
		return "", err
	}
	return "Moved scratch note " + slug + " into the wiki as " + moved + "; links to it were updated. " +
		"No page links to it yet: add a link where it belongs (edit_page).\n" + pageInfo(moved, base) + pending, nil
}

// applyEdit: content with one edit applied (old_string → new_string,
// replace_all), and how many places it changed. which names the edit in
// errors.
func applyEdit(content, slug string, edit *Obj, which string) (string, int, error) {
	old, err := strArg(edit, "old_string", true)
	if err != nil {
		return "", 0, err
	}
	repl, err := strArg(edit, "new_string", false)
	if err != nil {
		return "", 0, err
	}
	if old == repl {
		return "", 0, toolErr(which + "old_string and new_string are the same: nothing to change")
	}
	count := strings.Count(content, old)
	if count == 0 {
		return "", 0, toolErr(which + "old_string was not found in " + slug + ". It must match the page exactly, line breaks " +
			"and whitespace included; read_page shows the current text.")
	}
	if count > 1 && edit.Get("replace_all") != true {
		return "", 0, toolErr(fmt.Sprintf("%sold_string occurs %d times in %s. Include more of the surrounding text "+
			"to make it unique, or set replace_all=true to change every one.", which, count, slug))
	}
	return strings.ReplaceAll(content, old, repl), count, nil
}

func editPageTool(args *Obj, base string) (string, error) {
	slug, path, err := existingPage(args, "slug")
	if err != nil {
		return "", err
	}
	if err := checkGuides(args, []string{slug}); err != nil {
		return "", err
	}
	edits := []*Obj{args}
	several := args.Get("edits") != nil
	if several {
		l, ok := args.Get("edits").([]any)
		if !ok || len(l) == 0 {
			return "", toolErr("edits must be a non-empty list of {old_string, new_string, replace_all}")
		}
		edits = nil
		for _, e := range l {
			o, ok := e.(*Obj)
			if !ok {
				return "", toolErr("edits must be a non-empty list of {old_string, new_string, replace_all}")
			}
			edits = append(edits, o)
		}
		if args.Has("old_string") {
			return "", toolErr("give either old_string/new_string or edits, not both")
		}
	}
	pending := savePending(path, slug)
	content, err := readText(path)
	if err != nil {
		return "", err
	}
	// All or nothing: each edit applies to the text the ones before it left,
	// and the page is written only when every one of them matched.
	text, total := content, 0
	for i, e := range edits {
		which := ""
		if several {
			which = fmt.Sprintf("Edit %d: ", i+1)
		}
		var n int
		if text, n, err = applyEdit(text, slug, e, which); err != nil {
			return "", err
		}
		total += n
	}
	msg, err := commitMsg("Update "+slug, args)
	if err != nil {
		return "", err
	}
	if err := writeText(path, text); err != nil {
		return "", err
	}
	gitCommit(msg, path)
	what := "1 occurrence"
	if total > 1 {
		what = fmt.Sprintf("%d occurrences", total)
	}
	done := "replaced " + what
	if several {
		done = fmt.Sprintf("applied %d edits, %s", len(edits), what)
	}
	return "Edited page " + slug + ": " + done + ".\n" + pageInfo(slug, base) + pending +
		linkWarning(slug, content, text) + guideNote(slug), nil
}

// appendAt: content with addition at the end of the section under heading;
// ok false when the page has no such heading.
func appendAt(content, addition, heading string) (string, bool) {
	lines := splitLines(content)
	_, end, ok := sectionLines(lines, heading)
	if !ok {
		return "", false
	}
	out := trimNewlines(strings.Join(lines[:end], "")) + "\n\n" + addition + "\n"
	if rest := strings.Join(lines[end:], ""); rest != "" {
		out += "\n" + rest
	}
	return out, true
}

func appendToPage(args *Obj, base string) (string, error) {
	slug, path, err := existingPage(args, "slug")
	if err != nil {
		return "", err
	}
	addition, err := strArg(args, "content", true)
	if err != nil {
		return "", err
	}
	addition = strings.Trim(addition, "\n")
	heading, err := strArg(args, "under_heading", false)
	if err != nil {
		return "", err
	}
	heading = pyStrip(heading)
	if err := checkGuides(args, []string{slug}); err != nil {
		return "", err
	}
	pending := savePending(path, slug)
	content, err := readText(path)
	if err != nil {
		return "", err
	}
	text := trimNewlines(content) + "\n\n" + addition + "\n"
	where := ""
	if heading != "" {
		var ok bool
		if text, ok = appendAt(content, addition, heading); !ok {
			return "", toolErr(noHeading(slug, content, heading) +
				" To start a new section, put its heading in content and leave out under_heading.")
		}
		where = " under " + pyRepr(heading)
	}
	msg, err := commitMsg("Append to "+slug, args)
	if err != nil {
		return "", err
	}
	if err := writeText(path, text); err != nil {
		return "", err
	}
	gitCommit(msg, path)
	return "Appended to page " + slug + where + ".\n" + pageInfo(slug, base) + pending +
		linkWarning(slug, content, text) + guideNote(slug), nil
}

// folderArg: the folder rename_page's slug names, if it names one: a
// trailing "/" ("projects/"), or no page by that name but pages below it.
// "" for a page.
func folderArg(raw string) string {
	folder, _ := cleanFolder(raw)
	if folder == "" || !strings.HasSuffix(pyStrip(raw), "/") && mcpPageSlug(raw) != "" {
		return ""
	}
	for _, p := range iterPages() {
		if strings.HasPrefix(p.Slug, folder+"/") {
			return folder
		}
	}
	return ""
}

// A list of moved pages in rename_page's result stops after this many.
const movesShown = 20

func renameFolder(folder string, args *Obj, base string) (string, error) {
	raw, err := strArg(args, "new_slug", true)
	if err != nil {
		return "", err
	}
	newFolder, err := newSlug(raw)
	if err != nil {
		return "", err
	}
	switch {
	case newFolder == folder:
		return "", toolErr("The folder is already called " + folder)
	case strings.HasPrefix(newFolder, folder+"/"):
		return "", toolErr(folder + "/ can't move into a folder inside itself")
	case safePath(newFolder+"/page") == "":
		return "", toolErr(pyRepr(newFolder) + ": pages can't go in folders starting with '_' or '.'")
	}
	var paths, taken []string
	for _, p := range sortedPages() {
		if strings.HasPrefix(p.Slug, folder+"/") {
			paths = append(paths, p.Path)
			n := newFolder + p.Slug[len(folder):]
			if np := safePath(n); np != "" && exists(np) {
				taken = append(taken, n)
			}
		}
	}
	if len(taken) > 0 {
		return "", toolErr("These pages already exist, so nothing was moved: " + strings.Join(taken, ", "))
	}
	if err := checkGuides(args, []string{folder + "/page", newFolder + "/page"}); err != nil {
		return "", err
	}
	msg, err := commitMsg("Move "+folder+"/ to "+newFolder+"/", args)
	if err != nil {
		return "", err
	}
	pending := savePendingPages(paths, folder+"/")
	moves, changed, err := moveFolder(folder, newFolder, msg)
	if err != nil {
		return "", err
	}
	head := fmt.Sprintf("Moved folder %s/ to %s/: %d pages.", folder, newFolder, len(moves))
	if len(changed) > 0 {
		head += fmt.Sprintf(" Links to them were updated in %d files.", len(changed))
	}
	out := []string{head}
	for _, m := range moves[:min(len(moves), movesShown)] {
		out = append(out, "- "+m[0]+" → "+m[1])
	}
	if len(moves) > movesShown {
		out = append(out, fmt.Sprintf("… and %d more", len(moves)-movesShown))
	}
	return strings.Join(out, "\n") + pending, nil
}

func renamePageTool(args *Obj, base string) (string, error) {
	raw, err := strArg(args, "slug", true)
	if err != nil {
		return "", err
	}
	if folder := folderArg(raw); folder != "" {
		return renameFolder(folder, args, base)
	}
	slug, path, err := existingPage(args, "slug")
	if err != nil {
		return "", err
	}
	if isProtected(slug) {
		return "", toolErr(slug + " is the home page; it can't be renamed")
	}
	rawNew, err := strArg(args, "new_slug", true)
	if err != nil {
		return "", err
	}
	newS, err := newSlug(rawNew)
	if err != nil {
		return "", err
	}
	if newS == slug {
		return "", toolErr("The page is already called " + slug)
	}
	newPath := safePath(newS)
	if exists(newPath) {
		return "", toolErr("A page " + pyRepr(newS) + " already exists")
	}
	if err := checkGuides(args, []string{slug, newS}); err != nil {
		return "", err
	}
	msg, err := commitMsg("Rename "+slug+" to "+newS, args)
	if err != nil {
		return "", err
	}
	pending := savePending(path, slug)
	changed, err := movePage(path, newPath, slug, newS, msg)
	if err != nil {
		return "", err
	}
	refs := ""
	if len(changed) > 0 {
		refs = fmt.Sprintf(" Links to it were updated in %d files.", len(changed))
	}
	return "Renamed page " + slug + " to " + newS + "." + refs + "\n" + pageInfo(newS, base) + pending, nil
}

func deletePageTool(args *Obj, base string) (string, error) {
	slug, path, err := existingPage(args, "slug")
	if err != nil {
		return "", err
	}
	if isProtected(slug) {
		return "", toolErr(slug + " is the home page; it can't be deleted")
	}
	if err := checkGuides(args, []string{slug}); err != nil {
		return "", err
	}
	msg, err := commitMsg("Delete "+slug, args)
	if err != nil {
		return "", err
	}
	linking := findBacklinks(slug)
	pending := savePending(path, slug)
	if err := os.Remove(path); err != nil {
		return "", err
	}
	gitCommit(msg, path)
	out := "Deleted page " + slug + "."
	if cfg.GitEnabled {
		out += " It can be restored from Deleted Pages (git history)."
	}
	if len(linking) > 0 {
		var slugs []string
		for _, b := range linking {
			slugs = append(slugs, b.Slug)
		}
		out += "\nThese pages still link to it: " + strings.Join(slugs, ", ")
	}
	return out + pending, nil
}

func restorePage(args *Obj, base string) (string, error) {
	if !cfg.GitEnabled {
		return "", noGit()
	}
	raw, err := strArg(args, "slug", true)
	if err != nil {
		return "", err
	}
	version, err := strArg(args, "version", true)
	if err != nil {
		return "", err
	}
	version = strings.ToLower(pyStrip(version))
	slug := mcpPageSlug(raw)
	if slug == "" {
		if slug, err = newSlug(raw); err != nil {
			return "", err
		}
	}
	path := safePath(slug)
	entries := gitLog(path, 0)
	i := versionOf(entries, version)
	if i < 0 {
		return "", toolErr(pyRepr(version) + " is not a version of " + slug + "; history(slug) lists them.")
	}
	sha := entries[i].Sha
	content, ok := gitShow(sha, versionPath(path, entries, sha))
	if !ok {
		return "", toolErr("Version " + runePrefix(sha, 7) + " of " + slug + " has no text (the page was deleted then); " +
			"pick the version before it.")
	}
	existed := exists(path)
	pending, old := savePending(path, slug), ""
	if existed {
		if old, err = readText(path); err != nil {
			return "", err
		}
		if old == content {
			return "", toolErr(slug + " already reads as version " + runePrefix(sha, 7) + ".")
		}
	}
	if err := checkGuides(args, []string{slug}); err != nil {
		return "", err
	}
	msg, err := commitMsg("Restore "+slug+" to "+runePrefix(sha, 7), args)
	if err != nil {
		return "", err
	}
	if err := writeText(path, content); err != nil {
		return "", err
	}
	gitCommit(msg, path)
	what := "Brought back the deleted page"
	if existed {
		what = "Restored"
	}
	return what + " " + slug + " as of version " + runePrefix(sha, 7) + " (" + entries[i].Time + ").\n" +
		pageInfo(slug, base) + pending + linkWarning(slug, old, content) + guideNote(slug), nil
}

// ── replace_in_pages ────────────────────────────────────────────────────────
// A regex is Go's (RE2): no lookarounds or backreferences, but it can't
// backtrack for hours, so it runs in the server (Python needed a process
// of its own with a timeout). The replacement takes Python's \1 and
// \g<name>, as before.

// changedLine is a line replace changed: its number, before and after,
// and how many matches.
type changedLine struct {
	number   int
	old, new string
	matches  int
}

// replaceLines: content with re replaced line by line, code (fenced
// blocks, inline spans) left alone unless includeCode, and the lines that
// changed.
func replaceLines(content string, re *regexp.Regexp, repl func(body string, m []int) (string, error),
	includeCode bool) (string, []changedLine, error) {
	var out strings.Builder
	var changed []changedLine
	for i, l := range fencedLines(splitLines(content)) {
		body := strings.TrimRight(l.line, "\r\n")
		if l.inFence && !includeCode {
			out.WriteString(l.line)
			continue
		}
		var code [][2]int
		if !includeCode {
			code = inlineCodeSpans(body)
		}
		var b strings.Builder
		last, n := 0, 0
	matches:
		for _, m := range re.FindAllStringSubmatchIndex(body, -1) {
			// A match touching inline code (an empty one: starting in it) stays.
			for _, c := range code {
				if c[0] < max(m[1], m[0]+1) && m[0] < c[1] {
					continue matches
				}
			}
			r, err := repl(body, m)
			if err != nil {
				return "", nil, err
			}
			b.WriteString(body[last:m[0]] + r)
			last = m[1]
			n++
		}
		b.WriteString(body[last:])
		if n > 0 && b.String() != body {
			changed = append(changed, changedLine{i + 1, body, b.String(), n})
		}
		out.WriteString(b.String() + l.line[len(body):])
	}
	return out.String(), changed, nil
}

// templatePart is a piece of a replacement: text, or a group (group ≥ 0).
type templatePart struct {
	text  string
	group int
}

var pyEscapes = map[byte]string{'a': "\a", 'b': "\b", 'f': "\f", 'n': "\n", 'r': "\r", 't': "\t", 'v': "\v", '\\': `\`}

func isOctal(c byte) bool { return c >= '0' && c <= '7' }

// parseTemplate reads a replacement as Python's re does: \1 or \g<1> a
// group by number, \g<name> by name, \n and the like, an unknown escape of
// a letter an error, of anything else kept as written.
func parseTemplate(t string, re *regexp.Regexp) ([]templatePart, error) {
	var parts []templatePart
	text := func(s string) { parts = append(parts, templatePart{s, -1}) }
	group := func(n int) error {
		if n > re.NumSubexp() {
			return fmt.Errorf("invalid group reference %d", n)
		}
		parts = append(parts, templatePart{"", n})
		return nil
	}
	for i := 0; i < len(t); i++ {
		if t[i] != '\\' {
			text(t[i : i+1])
			continue
		}
		if i+1 == len(t) {
			return nil, errors.New("bad escape (end of pattern)")
		}
		c := t[i+1]
		i++
		switch {
		case c == 'g':
			if i+1 >= len(t) || t[i+1] != '<' {
				return nil, errors.New("missing <")
			}
			end := strings.IndexByte(t[i+2:], '>')
			if end < 0 {
				return nil, errors.New("missing >, unterminated name")
			}
			name := t[i+2 : i+2+end]
			i += 2 + end
			if n, err := strconv.Atoi(name); err == nil && n >= 0 {
				if err := group(n); err != nil {
					return nil, err
				}
			} else if n := re.SubexpIndex(name); n >= 0 {
				parts = append(parts, templatePart{"", n})
			} else {
				return nil, fmt.Errorf("unknown group name %s", pyRepr(name))
			}
		case c == '0':
			n := 0
			for k := 0; k < 2 && i+1 < len(t) && isOctal(t[i+1]); k++ {
				i++
				n = n*8 + int(t[i]-'0')
			}
			text(string(rune(n)))
		case c >= '1' && c <= '9':
			if i+2 < len(t) && isOctal(c) && isOctal(t[i+1]) && isOctal(t[i+2]) {
				n := int(c-'0')*64 + int(t[i+1]-'0')*8 + int(t[i+2]-'0')
				if n > 0o377 {
					return nil, fmt.Errorf(`octal escape value \%s outside of range 0-0o377`, t[i:i+3])
				}
				text(string(rune(n)))
				i += 2
				continue
			}
			n := int(c - '0')
			if i+1 < len(t) && t[i+1] >= '0' && t[i+1] <= '9' {
				n = n*10 + int(t[i+1]-'0')
				i++
			}
			if err := group(n); err != nil {
				return nil, err
			}
		case pyEscapes[c] != "":
			text(pyEscapes[c])
		case c < 0x80 && unicode.IsLetter(rune(c)):
			return nil, fmt.Errorf(`bad escape \%c`, c)
		default:
			text(`\` + string(c))
		}
	}
	return parts, nil
}

func expand(parts []templatePart, body string, m []int) string {
	var b strings.Builder
	for _, p := range parts {
		if p.group < 0 {
			b.WriteString(p.text)
		} else if m[2*p.group] >= 0 { // a group that didn't take part is ""
			b.WriteString(body[m[2*p.group]:m[2*p.group+1]])
		}
	}
	return b.String()
}

// pyPattern: a Python regex as RE2 takes it, where the syntax differs but
// the meaning doesn't: \Z (the end) is RE2's \z.
func pyPattern(p string) string {
	var b strings.Builder
	for i := 0; i < len(p); i++ {
		if p[i] == '\\' && i+1 < len(p) {
			if p[i+1] == 'Z' {
				b.WriteString(`\z`)
			} else {
				b.WriteString(p[i : i+2])
			}
			i++
			continue
		}
		b.WriteByte(p[i])
	}
	return b.String()
}

type replaceHit struct {
	path, slug, old, new string
	changed              []changedLine
}

func replaceInPages(args *Obj, base string) (string, error) {
	find, err := strArg(args, "find", true)
	if err != nil {
		return "", err
	}
	replace, err := strArg(args, "replace", false)
	if err != nil {
		return "", err
	}
	if strings.Contains(find, "\n") {
		return "", toolErr("find is matched within a line, so it can't contain a line break. " +
			"For a passage spanning lines, use edit_page on each page.")
	}
	isRegex, err := boolArg(args, "regex")
	if err != nil {
		return "", err
	}
	includeCode, err := boolArg(args, "include_code")
	if err != nil {
		return "", err
	}
	dryRun, ok := orDefault(args, "dry_run", true).(bool)
	if !ok {
		return "", toolErr("dry_run must be true or false")
	}
	pattern := regexp.QuoteMeta(find)
	if isRegex {
		pattern = pyPattern(find)
	}
	if _, err := regexp.Compile(pattern); err != nil {
		msg := "find is not a valid regex: " + strings.TrimPrefix(err.Error(), "error parsing regexp: ")
		var perr *syntax.Error
		if errors.As(err, &perr) && (perr.Code == syntax.ErrInvalidPerlOp || perr.Code == syntax.ErrInvalidEscape) {
			msg += " (no lookarounds or backreferences here: match more and keep it with a group, \\1)"
		}
		return "", toolErr(msg)
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	if under != "" && !anyPageIn(under) {
		return "", toolErr("No pages under " + pyRepr(under) + ". list_pages shows the slugs.")
	}
	maxPages, err := mcpInt(args, "max_pages", 50, 1000)
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
	if ignoreCase {
		pattern = "(?i)" + pattern
	}
	re := regexp.MustCompile(pattern)
	var parts []templatePart // parsed at the first match, as Python does
	parsed := false
	repl := func(body string, m []int) (string, error) {
		if !isRegex {
			return replace, nil
		}
		if !parsed {
			if parts, err = parseTemplate(replace, re); err != nil {
				return "", toolErr("replace: " + err.Error() + ` (groups are \1 or \g<name>; a literal backslash is \\)`)
			}
			parsed = true
		}
		return expand(parts, body, m), nil
	}
	var hits []replaceHit
	total := 0
	for _, p := range sortedPages() {
		if !inFolder(p.Slug, under) {
			continue
		}
		content, err := readText(p.Path)
		if err != nil {
			return "", err
		}
		text, changed, err := replaceLines(content, re, repl, includeCode)
		if err != nil {
			return "", err
		}
		if len(changed) > 0 {
			hits = append(hits, replaceHit{p.Path, p.Slug, content, text, changed})
			for _, c := range changed {
				total += c.matches
			}
		}
	}
	where, code := "", ""
	if under != "" {
		where = " under " + under + "/"
	}
	if !includeCode {
		code = " outside code"
	}
	if len(hits) == 0 {
		return "Nothing" + where + " matches " + pyRepr(find) + code + ".", nil
	}
	counted := fmt.Sprintf("%d %s on %d %s", total, plural(total, "occurrence"), len(hits), plural(len(hits), "page"))
	if dryRun {
		var out []string
		shown, lines := 0, 0
		for _, h := range hits {
			lines += len(h.changed)
		}
		for _, h := range hits {
			if shown >= limit {
				break
			}
			out = append(out, "", h.slug)
			for _, c := range h.changed[:min(len(h.changed), limit-shown)] {
				out = append(out, fmt.Sprintf("  %d: - %s", c.number, cutLine(c.old)), fmt.Sprintf("  %d: + %s", c.number, cutLine(c.new)))
				shown++
			}
		}
		head := "Dry run, nothing written: would replace " + counted + where + code
		if shown < lines {
			head += fmt.Sprintf("; the first %d of %d changed lines shown (raise limit to see more)", shown, lines)
		}
		head += ". Call again with dry_run=false to write them, in one commit."
		if len(hits) > maxPages {
			head += fmt.Sprintf(" That is more pages than max_pages (%d): raise it, or narrow find or under.", maxPages)
		}
		return head + "\n" + strings.Join(out, "\n"), nil
	}
	if len(hits) > maxPages {
		return "", toolErr(fmt.Sprintf("%s would change %d pages, more than max_pages (%d); nothing was "+
			"written. Check with dry_run, then raise max_pages, or narrow find or under.", pyRepr(find), len(hits), maxPages))
	}
	var slugs, paths []string
	for _, h := range hits {
		slugs = append(slugs, h.slug)
		paths = append(paths, h.path)
	}
	if err := checkGuides(args, slugs); err != nil {
		return "", err
	}
	on := hits[0].slug
	if len(hits) > 1 {
		on = fmt.Sprintf("%d pages", len(hits))
	}
	msg, err := commitMsg("Replace "+pyRepr(cutRunes(find, 60, "…"))+" on "+on, args)
	if err != nil {
		return "", err
	}
	pending := savePendingPages(paths, fmt.Sprintf("%d pages", len(hits)))
	for _, h := range hits {
		if err := writeText(h.path, h.new); err != nil {
			return "", err
		}
	}
	gitCommit(msg, paths...)
	all := slugSet()
	brokenSet := map[string]bool{}
	for _, h := range hits {
		for _, l := range newMissing(h.slug, h.old, h.new, all) {
			brokenSet[l] = true
		}
	}
	out := "Replaced " + counted + where + code + ", in one commit: " + strings.Join(slugs, ", ")
	if len(brokenSet) > 0 {
		out += "\nNote: these new links point to pages that don't exist (yet): " + strings.Join(sortedKeys(brokenSet), ", ")
	}
	return out + pending, nil
}

// closeInContent: for a new page (semantic search only), the existing pages
// closest to it in meaning, which a similar name doesn't catch (a crash
// analysis written up anew when the ticket's page already covers it).
// Pages it links to, or already named for a similar name, are left out.
func closeInContent(slug, content string, named map[string]bool) string {
	if semantic == nil {
		return ""
	}
	known := slugSet()
	delete(known, slug)
	exclude := maps.Clone(named)
	for s := range pageLinkTargets(content, slug) {
		exclude[s] = true
	}
	close := semantic.relatedToText(slug, content, known, exclude, 3)
	if len(close) == 0 {
		return ""
	}
	return "\nNote: the pages closest to it in content: " + joinIDs(close) +
		". If one already covers this topic, merge this page there (edit_page) and " +
		"delete_page this one; else consider linking them."
}
