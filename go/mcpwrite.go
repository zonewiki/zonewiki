// The MCP tools that write pages: write_page (also a
// note), edit_page, append_to_page, rename_page (also a folder),
// delete_page, restore_page, file_note, rename_task_tag (replace_in_pages
// is in mcpreplace.go). Each write
// is a git commit, refused while a guide for the page isn't confirmed
// (checkGuides).
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
)

func unixNano(n int64) time.Time { return time.Unix(0, n) }

func writePage(args jsonObj, base, user string) (string, error) {
	content, err := strArg(args, "content", true)
	if err != nil {
		return "", err
	}
	quicknote, err := boolArg(args, "note")
	if err != nil {
		return "", err
	} else if quicknote {
		return writeQuicknote(args, content, base, user)
	}
	overwrite := jsonobj.True(args["overwrite"])
	raw, err := strArg(args, "slug", true)
	if err != nil {
		return "", err
	}
	existing := mcpPageSlug(raw, user)
	if existing != "" && !overwrite {
		return "", toolErr("Page " + strconv.Quote(existing) + " already exists. Change it with edit_page or append_to_page, " +
			"or set overwrite=true to replace all of it.")
	}
	slug := existing
	if slug == "" {
		if slug, err = newSlug(raw, user); err != nil {
			return "", err
		}
	}
	if err := checkGuides(args, []string{slug}, user); err != nil {
		return "", err
	}
	path := safePath(slug)
	content = trimNewlines(content) + "\n"
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	pending, old, merged := "", "", ""
	if existing == "" {
		if err := createPageOp(user, slug, content, suffix); err != nil {
			return "", err
		}
	} else {
		var kept bool
		old, content, kept, err = changePageOp(user, slug, "Update", suffix, func(old string) (string, error) {
			if baseHash, _ := jsonobj.As[string](args["base_hash"]); baseHash != "" && baseHash != mdtext.ContentHash(old) {
				merged = "\nThe page had changed since you read it; your changes were merged with the others (they touched different lines)."
				return mergeWrite(slug, path, old, content, baseHash, user)
			}
			return content, nil
		})
		if err != nil {
			return "", err
		}
		pending = pendingNote(slug, kept)
	}
	out := "Created page " + slug + ".\n" + pageInfo(slug, base)
	if existing != "" {
		out = "Replaced page " + slug + "." + merged + "\n" + pageInfo(slug, base)
	} else {
		similar := similarPages(slug, mdtext.PageTitle(content, slug), user)
		named := map[string]bool{}
		if len(similar) > 0 {
			out += "\nNote: pages with similar names exist; if this one repeats one of them, " +
				"merge it there (edit_page) and delete_page this one:"
			for _, p := range similar {
				out += "\n- " + p.Slug + " | " + p.Title
				named[p.Slug] = true
			}
		}
		out += closeInContent(slug, content, named, user)
	}
	return out + pending + linkWarning(slug, old, content, user) + guideNote(slug, user), nil
}

// writeQuicknote: write_page with quicknote, a new note, named by slug
// (any words; Zone puts the time in front) or else by the content's title.
func writeQuicknote(args jsonObj, content, base, user string) (string, error) {
	if jsonobj.True(args["overwrite"]) {
		return "", toolErr("A note is always a new one: overwrite doesn't apply. " +
			"Change an existing note with edit_page.")
	}
	name, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	name = strings.TrimSpace(name)
	title := mdtext.PageTitle(content, "")
	if strings.TrimSpace(title) == "" && name == "" {
		return "", toolErr("Give the note a title: start content with '# Title', or set slug.")
	}
	if strings.TrimSpace(title) == "" { // the title goes under the frontmatter, if any
		_, body := mdtext.ParseFrontmatter(content)
		content = content[:len(content)-len(body)] + "# " + name + "\n\n" + strings.TrimLeftFunc(body, unicode.IsSpace)
	}
	// Notes sit at the wiki's root, with users in the user's
	// private folder: the guides of that folder apply.
	where := "note"
	if own := privateFolder(user); own != "" {
		where = own + "/" + where
	}
	if err := checkGuides(args, []string{where}, user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if name == "" {
		name = title
	}
	slug, err := createQuicknote(user, name, &content, suffix)
	if err != nil {
		return "", err
	}
	return "Created note " + slug + " (in Notes, not yet in the wiki; file_note " +
		"moves it there or appends it to a page).\n" + pageInfo(slug, base) + linkWarning(slug, "", content, user), nil
}

// fileNote: the note's Move to wiki (name, folder,
// keep_date) or Append to page (page, heading), as in the browser.
func fileNote(args jsonObj, base, user string) (string, error) {
	slug, path, err := existingPage(args, "slug", user)
	if err != nil {
		return "", err
	}
	if !isQuicknote(slug) {
		return "", toolErr(slug + " is not a note. rename_page moves a page, append_to_page adds text to one.")
	}
	page, err := strArg(args, "page", false)
	if err != nil {
		return "", err
	}
	name, err := strArg(args, "name", false)
	if err != nil {
		return "", err
	}
	page, name = strings.TrimSpace(page), strings.TrimSpace(name)
	if (page == "") == (name == "") {
		return "", toolErr("Give either name (to move the note into the wiki as its own page) " +
			"or page (to append it to that page as a section), not both.")
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if page != "" {
		target := mcpPageSlug(page, user)
		if target == "" {
			return "", toolErr("No page " + strconv.Quote(page) + ". Use search_wiki or list_pages to find its slug.")
		}
		heading, err := strArg(args, "heading", false)
		if err != nil {
			return "", err
		}
		if heading = strings.TrimSpace(heading); heading == "" {
			text, err := readText(path)
			if err != nil {
				return "", err
			}
			heading = appendHeading(slug, mdtext.PageTitle(text, slug))
		}
		if err := checkGuides(args, []string{target}, user); err != nil {
			return "", err
		}
		keptTarget, err := keepPending(safePath(target), target)
		if err != nil {
			return "", err
		}
		pending := pendingNote(target, keptTarget)
		anchor, kept, err := appendQuicknote(user, slug, target, heading, suffix)
		left, err := partly(err)
		if err != nil {
			return "", err
		}
		pending = pendingNote(slug, kept) + pending
		return "Appended note " + slug + " to " + target + " as the section “" + heading + "”, and deleted " +
			"the note; links to it now point at that section.\nurl: " + base + pathURL("page", target) + "#" + anchor +
			"\nfile: " + hostFile(target) + pending + left, nil
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
	if s := mdtext.PromotedSlug(folder, name, date); s != "" {
		if err := checkGuides(args, []string{s}, user); err != nil {
			return "", err
		}
	}
	moved, kept, err := moveQuicknote(user, slug, folder, name, keepDate, suffix)
	left, err := partly(err)
	if err != nil {
		return "", err
	}
	pending := pendingNote(slug, kept) + left
	return "Moved note " + slug + " into the wiki as " + moved + "; links to it were updated. " +
		"No page links to it yet: add a link where it belongs (edit_page).\n" + pageInfo(moved, base) + pending, nil
}

// applyEdit: content with one edit applied (old_string → new_string,
// replace_all), and how many places it changed. which names the edit in
// errors.
func applyEdit(content, slug string, edit jsonObj, which string) (string, int, error) {
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
	if count > 1 && !jsonobj.True(edit["replace_all"]) {
		return "", 0, toolErr(fmt.Sprintf("%sold_string occurs %d times in %s. Include more of the surrounding text "+
			"to make it unique, or set replace_all=true to change every one.", which, count, slug))
	}
	return strings.ReplaceAll(content, old, repl), count, nil
}

func editPageTool(args jsonObj, base, user string) (string, error) {
	slug, _, err := existingPage(args, "slug", user)
	if err != nil {
		return "", err
	}
	if err := checkGuides(args, []string{slug}, user); err != nil {
		return "", err
	}
	edits := []jsonObj{args}
	several := !args.Null("edits")
	if several {
		l, ok := jsonobj.As[[]json.RawMessage](args["edits"])
		if !ok || len(l) == 0 {
			return "", toolErr("edits must be a non-empty list of {old_string, new_string, replace_all}")
		}
		edits = nil
		for _, e := range l {
			o, ok := jsonobj.As[jsonObj](e)
			if !ok {
				return "", toolErr("edits must be a non-empty list of {old_string, new_string, replace_all}")
			}
			edits = append(edits, o)
		}
		if args.Has("old_string") {
			return "", toolErr("give either old_string/new_string or edits, not both")
		}
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	// All or nothing: each edit applies to the text the ones before it left,
	// and the page is written only when every one of them matched.
	total := 0
	content, text, kept, err := changePageOp(user, slug, "Update", suffix, func(text string) (string, error) {
		for i, e := range edits {
			which := ""
			if several {
				which = fmt.Sprintf("Edit %d: ", i+1)
			}
			var n int
			var err error
			if text, n, err = applyEdit(text, slug, e, which); err != nil {
				return "", err
			}
			total += n
		}
		return text, nil
	})
	if err != nil {
		return "", err
	}
	pending := pendingNote(slug, kept)
	what := "1 occurrence"
	if total > 1 {
		what = fmt.Sprintf("%d occurrences", total)
	}
	done := "replaced " + what
	if several {
		done = fmt.Sprintf("applied %d edits, %s", len(edits), what)
	}
	return "Edited page " + slug + ": " + done + ".\n" + pageInfo(slug, base) + pending +
		linkWarning(slug, content, text, user) + guideNote(slug, user), nil
}

// appendAt: content with addition at the end of the section under heading;
// ok false when the page has no such heading.
func appendAt(content, addition, heading string) (string, bool) {
	lines := mdtext.SplitLines(content)
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

func appendToPage(args jsonObj, base, user string) (string, error) {
	slug, _, err := existingPage(args, "slug", user)
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
	heading = strings.TrimSpace(heading)
	if err := checkGuides(args, []string{slug}, user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	where := ""
	content, text, kept, err := changePageOp(user, slug, "Append to", suffix, func(content string) (string, error) {
		if heading == "" {
			return trimNewlines(content) + "\n\n" + addition + "\n", nil
		}
		text, ok := appendAt(content, addition, heading)
		if !ok {
			return "", toolErr(noHeading(slug, content, heading) +
				" To start a new section, put its heading in content and leave out under_heading.")
		}
		where = " under " + strconv.Quote(heading)
		return text, nil
	})
	if err != nil {
		return "", err
	}
	pending := pendingNote(slug, kept)
	return "Appended to page " + slug + where + ".\n" + pageInfo(slug, base) + pending +
		linkWarning(slug, content, text, user) + guideNote(slug, user), nil
}

// folderArg: the folder rename_page's slug names, if it names one: a
// trailing "/" ("projects/"), or no page by that name but pages below it.
// "" for a page.
func folderArg(raw, user string) string {
	folder := cleanFolder(raw)
	if folder == "" || !strings.HasSuffix(strings.TrimSpace(raw), "/") && mcpPageSlug(raw, user) != "" {
		return ""
	}
	for _, p := range visiblePages(user) {
		if strings.HasPrefix(p.Slug, folder+"/") {
			return folder
		}
	}
	return ""
}

// A list of moved pages in rename_page's result stops after this many.
const movesShown = 20

func renameFolder(folder string, args jsonObj, base, user string) (string, error) {
	raw, err := strArg(args, "new_slug", true)
	if err != nil {
		return "", err
	}
	newFolder, err := newSlug(raw, user)
	if err != nil {
		return "", err
	}
	asked, err := moveArg(args)
	if err != nil {
		return "", err
	}
	if _, err := checkFolderMove(user, folder, newFolder, asked); err != nil {
		return "", narrowHint(err)
	}
	if err := checkGuides(args, []string{folder + "/page", newFolder + "/page"}, user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	moves, changed, kept, err := moveFolderOp(user, folder, newFolder, suffix, asked)
	left, err := partly(err)
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
	return strings.Join(out, "\n") + pendingPagesNote(kept, folder+"/") + left, nil
}

// renameTaskTag: the Rename… of /tasks?list=<name>, a list or a plain tag.
func renameTaskTag(args jsonObj, base, user string) (string, error) {
	tag, err := strArg(args, "tag", true)
	if err != nil {
		return "", err
	}
	name, err := strArg(args, "new_tag", true)
	if err != nil {
		return "", err
	}
	merge, err := boolArg(args, "merge")
	if err != nil {
		return "", err
	}
	plan, err := checkTagRename(user, tag, name, merge)
	if err != nil {
		return "", err
	}
	if err := checkGuides(args, plan.visibleSlugs(user), user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	done, err := renameTagOp(user, tag, name, merge, suffix)
	if err != nil {
		return "", err
	}
	verb := "Renamed tag #" + plan.old + " to #" + done.Name
	if done.Merged {
		verb = "Merged tag #" + plan.old + " into #" + done.Name
	}
	out := fmt.Sprintf("%s, in one commit: %d %s", verb, done.Tasks, plural(done.Tasks, "task"))
	if len(done.Pages) > 0 {
		out += fmt.Sprintf(", %d %s (%s)", len(done.Pages), plural(len(done.Pages), "page"), strings.Join(done.Pages, ", "))
	}
	return out + ".", nil
}

// moveArg: what a rename_page has been asked already: a move to more people
// is what its user asked for; one to fewer wants narrow=true, said by who
// knows whom it takes the page from (the refusal names them).
func moveArg(args jsonObj) (moveSure, error) {
	narrow, err := boolArg(args, "narrow")
	return moveSure{wider: true, narrower: narrow}, err
}

// narrowHint: a move that asks first (askError), as a tool says it.
func narrowHint(err error) error {
	if ask := (*askError)(nil); errors.As(err, &ask) {
		return toolErr("Not moved: " + ask.msg + " To move it all the same, ask the user, then call again with narrow=true.")
	}
	return err
}

func renamePageTool(args jsonObj, base, user string) (string, error) {
	raw, err := strArg(args, "slug", true)
	if err != nil {
		return "", err
	}
	if folder := folderArg(raw, user); folder != "" {
		return renameFolder(folder, args, base, user)
	}
	slug, _, err := existingPage(args, "slug", user)
	if err != nil {
		return "", err
	}
	rawNew, err := strArg(args, "new_slug", true)
	if err != nil {
		return "", err
	}
	newS, err := newSlug(rawNew, user)
	if err != nil {
		return "", err
	}
	asked, err := moveArg(args)
	if err != nil {
		return "", err
	}
	if _, _, err := checkRename(user, slug, newS, asked); err != nil {
		if pe, ok := refusalOf(err); ok {
			return "", toolErr("Not renamed: " + pe.msg)
		}
		return "", narrowHint(err)
	}
	if err := checkGuides(args, []string{slug, newS}, user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	changed, kept, err := renamePageOp(user, slug, newS, suffix, asked)
	left, err := partly(err)
	if err != nil {
		return "", err
	}
	refs := ""
	if len(changed) > 0 {
		refs = fmt.Sprintf(" Links to it were updated in %d files.", len(changed))
	}
	return "Renamed page " + slug + " to " + newS + "." + refs + "\n" + pageInfo(newS, base) + pendingNote(slug, kept) + left, nil
}

func deletePageTool(args jsonObj, base, user string) (string, error) {
	slug, _, err := existingPage(args, "slug", user)
	if err != nil {
		return "", err
	}
	if isProtected(slug) { // before the guides: they'd be asked for in vain
		return "", toolErr(slug + " is the home page; it can't be deleted")
	}
	if err := checkGuides(args, []string{slug}, user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	var linking []PageRef
	for _, b := range findBacklinks(slug) {
		if canSee(user, b.Slug) {
			linking = append(linking, b)
		}
	}
	kept, err := deletePageOp(user, slug, suffix)
	if err != nil {
		return "", err
	}
	pending := pendingNote(slug, kept)
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

func restorePage(args jsonObj, base, user string) (string, error) {
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
	version = strings.ToLower(strings.TrimSpace(version))
	slug, err := pageOrNewSlug(raw, user)
	if err != nil {
		return "", err
	}
	entries := pageLog(user, safePath(slug))
	i := versionOf(entries, version)
	if i < 0 {
		return "", toolErr(strconv.Quote(version) + " is not a version of " + slug + "; history(slug) lists them.")
	}
	if err := checkGuides(args, []string{slug}, user); err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	sha := entries[i].Sha
	old, kept, err := restorePageOp(user, slug, sha, suffix)
	if err != nil {
		return "", err
	}
	what, before := "Brought back the deleted page", ""
	if old != nil {
		what, before = "Restored", *old
	}
	content, _ := readText(safePath(slug))
	return what + " " + slug + " as of version " + runePrefix(sha, 7) + " (" + entries[i].Time + ").\n" +
		pageInfo(slug, base) + pendingNote(slug, kept) + linkWarning(slug, before, content, user) + guideNote(slug, user), nil
}
