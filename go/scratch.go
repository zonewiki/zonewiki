// Scratch notes: quick notes with a timestamped name,
// kept in the Scratch module until moved into the wiki or appended to a
// page. The web routes and the MCP tools share these, so both make the same
// files and commits; suffix follows a commit's message (the MCP tools'
// summary and "(MCP)").
package main

import (
	"errors"
	"os"
	"regexp"
	"time"
)

// scratchError: a move or append refused, with a message for the user.
type scratchError struct{ msg string }

func (e *scratchError) Error() string { return e.msg }

func refuse(msg string) error { return &scratchError{msg} }

// noteDate: the day a scratch note was created (YYYY-MM-DD).
func noteDate(slug string) string {
	m := scratchRE.FindStringSubmatch(slug)
	if m == nil {
		return ""
	}
	return runePrefix(m[1], 10)
}

// appendHeading: the section heading a scratch note gets on the page it is
// appended to: its title and its day.
func appendHeading(slug, title string) string { return title + " (" + noteDate(slug) + ")" }

// createScratch makes a scratch note named after title (a / in it is a
// hyphen), with content, or just `# title`; returns its slug. Two notes of
// one name in the same second: the second gets the next second's stamp.
func createScratch(title string, content *string, suffix string) (string, error) {
	if title = pyStrip(title); title == "" {
		title = "Untitled"
	}
	name := toSlug(regexp.MustCompile("/").ReplaceAllString(title, "-"))
	if name == "" {
		name = "untitled"
	}
	at := now()
	var slug, path string
	for {
		slug = at.Format("2006-01-02_15-04-05") + "_" + name
		if path = safePath(slug); path == "" {
			return "", refuse("'" + title + "' can't name a scratch note")
		}
		if !exists(path) {
			break
		}
		at = at.Add(time.Second)
	}
	text := "# " + title + "\n\n"
	if content != nil {
		text = trimRightNewlines(*content) + "\n"
	}
	if err := writeText(path, text); err != nil {
		return "", err
	}
	gitCommit("Create "+slug+suffix, path)
	return slug, nil
}

func trimRightNewlines(s string) string {
	for len(s) > 0 && s[len(s)-1] == '\n' {
		s = s[:len(s)-1]
	}
	return s
}

func notePath(slug string) (string, error) {
	p := ""
	if isScratch(slug) {
		p = safePath(slug)
	}
	if p == "" || !exists(p) {
		return "", refuse("There is no scratch note “" + slug + "”")
	}
	return p, nil
}

// moveScratch moves a scratch note into the wiki (Move to wiki): a rename
// to folder/name, links following, with its own commit message. Returns
// the new slug.
func moveScratch(slug, folder, name string, keepDate bool, suffix string) (string, error) {
	oldPath, err := notePath(slug)
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
	if isScratch(newSlug) {
		return "", refuse("That name would still be a scratch note")
	}
	newPath := safePath(newSlug)
	if newPath == "" {
		return "", refuse("“" + newSlug + "”: pages can't go in folders starting with '_' or '.'")
	}
	if exists(newPath) {
		return "", refuse("A page named “" + newSlug + "” already exists")
	}
	if _, err := movePage(oldPath, newPath, slug, newSlug, "Promote "+slug+" to "+newSlug+suffix); err != nil {
		return "", err
	}
	return newSlug, nil
}

var (
	headingTextRE = regexp.MustCompile(`(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$`)
	breakersRE    = regexp.MustCompile(`[\[\]|]`)
)

// appendScratch appends a scratch note to the page target as a `## heading`
// section (its tags into the page's frontmatter) and deletes the note;
// links to the note point at that section, all in one commit. Returns the
// section's anchor.
func appendScratch(slug, target, heading, suffix string) (string, error) {
	oldPath, err := notePath(slug)
	if err != nil {
		return "", err
	}
	// [ ] | would break the wikilinks that now point at the section.
	heading = pyStrip(breakersRE.ReplaceAllString(heading, ""))
	targetPath := ""
	if target != "" {
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
	if err := writeText(targetPath, trimRightNewlines(pageText)+"\n\n"+section); err != nil {
		return "", err
	}
	if err := os.Remove(oldPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	refs := updateReferences(slug, target, heading)
	gitCommit("Append "+slug+" to "+target+suffix, append([]string{oldPath, targetPath}, refs...)...)
	return anchor, nil
}
