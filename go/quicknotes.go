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
	p, err := writablePage(user, slug)
	if err != nil {
		return "", refuseAs(refusedNotFound, "There is no note “"+slug+"”")
	}
	if !isQuicknote(slug) {
		return "", refuse(slug + " is not a note")
	}
	return p, nil
}

// moveQuicknote moves a note into the wiki (Move to wiki): a rename
// to folder/name, links following, with its own commit message, what the
// note held uncommitted committed first (keepPending). Returns the new
// slug, and whether it kept any.
func moveQuicknote(user, slug, folder, name string, keepDate bool, suffix string) (string, bool, error) {
	oldPath, err := notePath(user, slug)
	if err != nil {
		return "", false, err
	}
	date := ""
	if keepDate {
		date = noteDate(slug)
	}
	newSlug := promotedSlug(folder, name, date)
	if newSlug == "" {
		return "", false, refuse("Give the page a name")
	}
	if isQuicknote(newSlug) {
		return "", false, refuse("That name would still be a note")
	}
	// A shared note (one from before users) can't be taken into a private folder.
	if err := checkTarget(user, slug, newSlug); err != nil {
		return "", false, err
	}
	kept := keepPending(oldPath, slug)
	if _, err := movePage(user, oldPath, safePath(newSlug), slug, newSlug, "Promote "+slug+" to "+newSlug+suffix); err != nil {
		return "", kept, err
	}
	return newSlug, kept, nil
}

var headingTextRE = regexp.MustCompile(`(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$`)

// appendQuicknote appends a note to the page target as a `## heading`
// section (its tags into the page's frontmatter) and deletes the note;
// links to the note point at that section, all in one commit; what the
// note held uncommitted is committed first (keepPending). Returns the
// section's anchor, and whether it kept any.
func appendQuicknote(user, slug, target, heading, suffix string) (anchor string, kept bool, err error) {
	oldPath, err := notePath(user, slug)
	if err != nil {
		return "", false, err
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
			return "", false, refuse("Choose a page")
		}
		return "", false, refuse("There is no page “" + target + "”")
	}
	if target == slug {
		return "", false, refuse("Choose another page than this note")
	}
	if heading == "" {
		return "", false, refuse("Give the section a heading")
	}
	pageText, err := readText(targetPath)
	if err != nil {
		return "", false, err
	}
	anchor = sectionAnchor(heading)
	masked, _ := maskCode(pageText)
	for _, m := range headingTextRE.FindAllStringSubmatch(masked, -1) {
		if sectionAnchor(m[1]) == anchor {
			return "", false, refuse("“" + target + "” already has a section “" + heading + "”")
		}
	}
	kept = keepPending(oldPath, slug)
	note, err := readText(oldPath)
	if err != nil {
		return "", false, err
	}
	section, tags := noteAsSection(note, heading)
	pageText = addFrontmatterTags(pageText, tags)
	if err := writeText(targetPath, trimNewlines(pageText)+"\n\n"+section); err != nil {
		return "", false, err
	}
	if err := os.Remove(oldPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", false, err
	}
	refs := updateReferences(slug, target, heading)
	gitCommit(user, "Append "+slug+" to "+target+suffix, append([]string{oldPath, targetPath}, refs...)...)
	return anchor, kept, nil
}
