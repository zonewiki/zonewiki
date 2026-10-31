// Notes: quick notes with a timestamped name,
// kept in Notes module until moved into the wiki or appended to a
// page. The web routes and the MCP tools share these, so both make the same
// files and commits; suffix follows a commit's message (the MCP tools'
// summary and "(MCP)").
package main

import (
	"errors"
	"os"
	"regexp"
	"strings"
	"time"
)

// noteDate: the day a note was created (YYYY-MM-DD).
func noteDate(slug string) string {
	m := quicknoteRE.FindStringSubmatch(slug)
	if m == nil {
		return ""
	}
	return runePrefix(m[1], 10)
}

// appendHeading: the section heading a note gets on the page it is
// appended to: its title and its day.
func appendHeading(slug, title string) string { return title + " (" + noteDate(slug) + ")" }

// createQuicknote makes a note named after title (a / in it is a
// hyphen), with content, or just `# title`; returns its slug. With users
// it goes in the user's private folder: notes are your own. Two notes of
// one name in the same second: the second gets the next second's stamp.
func createQuicknote(user, title string, content *string, suffix string) (string, error) {
	if title = strings.TrimSpace(title); title == "" {
		title = "Untitled"
	}
	name := toSlug(strings.ReplaceAll(title, "/", "-"))
	if name == "" {
		name = "untitled"
	}
	folder := privateFolder(user)
	if folder != "" {
		folder += "/"
	}
	at := now()
	var slug, path string
	for {
		slug = folder + at.Format("2006-01-02_15-04-05") + "_" + name
		if path = safePath(slug); path == "" {
			return "", refuse("'" + title + "' can't name a note")
		}
		if !exists(path) {
			break
		}
		at = at.Add(time.Second)
	}
	text := "# " + title + "\n\n"
	if content != nil {
		text = trimNewlines(*content) + "\n"
	}
	if err := writeText(path, text); err != nil {
		return "", err
	}
	gitCommit(user, "Create "+slug+suffix, path)
	return slug, nil
}

// notePath: the file of a note user may change.
func notePath(user, slug string) (string, error) {
	p := ""
	if isQuicknote(slug) && canWrite(user, slug) {
		p = safePath(slug)
	}
	if p == "" || !exists(p) {
		return "", refuse("There is no note “" + slug + "”")
	}
	return p, nil
}

// moveQuicknote moves a note into the wiki (Move to wiki): a rename
// to folder/name, links following, with its own commit message. Returns
// the new slug.
func moveQuicknote(user, slug, folder, name string, keepDate bool, suffix string) (string, error) {
	oldPath, err := notePath(user, slug)
	if err != nil {
		return "", err
	}
	date := ""
	if keepDate {
		date = noteDate(slug)
	}
	newSlug := promotedSlug(folder, name, date)
	if newSlug == "" {
		return "", refuse("Give the page a name")
	}
	if isQuicknote(newSlug) {
		return "", refuse("That name would still be a note")
	}
	newPath := safePath(newSlug)
	if newPath == "" {
		return "", refuse("“" + newSlug + "”: pages can't go in folders starting with '_' or '.'")
	}
	if !canWrite(user, newSlug) {
		return "", refuse("“" + newSlug + "”: that folder is another user's")
	}
	if exists(newPath) {
		return "", refuse("A page named “" + newSlug + "” already exists")
	}
	// A shared note (one from before users) can't be taken into a private folder.
	if msg := refuseMakingPrivate(map[string]string{slug: newSlug}); msg != "" {
		return "", refuse(msg)
	}
	if _, err := movePage(user, oldPath, newPath, slug, newSlug, "Promote "+slug+" to "+newSlug+suffix); err != nil {
		return "", err
	}
	return newSlug, nil
}

var headingTextRE = regexp.MustCompile(`(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$`)

// appendQuicknote appends a note to the page target as a `## heading`
// section (its tags into the page's frontmatter) and deletes the note;
// links to the note point at that section, all in one commit. Returns the
// section's anchor.
func appendQuicknote(user, slug, target, heading, suffix string) (string, error) {
	oldPath, err := notePath(user, slug)
	if err != nil {
		return "", err
	}
	commitOthersDrafts(user) // the page's, and those whose links to the note follow
	// [ ] | would break the wikilinks that now point at the section.
	heading = strings.TrimSpace(wikilinkBreakers.ReplaceAllString(heading, ""))
	targetPath := ""
	if target != "" && canWrite(user, target) {
		targetPath = safePath(target)
	}
	if targetPath == "" || !exists(targetPath) {
		if target == "" {
			return "", refuse("Choose a page")
		}
		return "", refuse("There is no page “" + target + "”")
	}
	if target == slug {
		return "", refuse("Choose another page than this note")
	}
	if heading == "" {
		return "", refuse("Give the section a heading")
	}
	pageText, err := readText(targetPath)
	if err != nil {
		return "", err
	}
	anchor := sectionAnchor(heading)
	masked, _ := maskCode(pageText)
	for _, m := range headingTextRE.FindAllStringSubmatch(masked, -1) {
		if sectionAnchor(m[1]) == anchor {
			return "", refuse("“" + target + "” already has a section “" + heading + "”")
		}
	}
	note, err := readText(oldPath)
	if err != nil {
		return "", err
	}
	section, tags := noteAsSection(note, heading)
	pageText = addFrontmatterTags(pageText, tags)
	if err := writeText(targetPath, trimNewlines(pageText)+"\n\n"+section); err != nil {
		return "", err
	}
	if err := os.Remove(oldPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	refs := updateReferences(slug, target, heading)
	gitCommit(user, "Append "+slug+" to "+target+suffix, append([]string{oldPath, targetPath}, refs...)...)
	return anchor, nil
}
