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
	"zonewiki/internal/mdtext"
)

// quicknoteRE: a note's slug, at the wiki's top level or at the top
// of a user's private folder (isQuicknote checks the user); the first group
// is its timestamp.
var quicknoteRE = regexp.MustCompile(`^(?:` + privateDir + `/[a-z0-9-]+/)?(\d{4}-\d{2}-\d{2}_` +
	`\d{2}-\d{2}-\d{2})_(.+)$`)

// isQuicknote: slug is a note: at the top level, or (with users) at
// the top of a user's private folder.
func isQuicknote(slug string) bool {
	if !quicknoteRE.MatchString(slug) {
		return false
	}
	if !strings.HasPrefix(slug, privateDir+"/") {
		return true
	}
	return placeOf(slug).user() != ""
}

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
func createQuicknote(user asker, title string, content *string, suffix string) (string, error) {
	if title = strings.TrimSpace(title); title == "" {
		title = "Untitled"
	}
	name := mdtext.ToSlug(strings.ReplaceAll(title, "/", "-"))
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
		// A note is a page in its user's folder, and made by who may write
		// there: not a viewer, nor an asker that wasn't given the folder.
		if !canWrite(user, slug) {
			if lacksPrivate(user) {
				return "", refuse(noPrivateMsg)
			}
			return "", notWritable(user, slug, "'"+title+"' can't be made a note")
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
	if err := checkRoom(int64(len(text))); err != nil {
		return "", err
	}
	if err := writeText(path, text); err != nil {
		return "", err
	}
	added(int64(len(text)))
	return slug, notCommitted(gitCommit(user.Name, "Create "+slug+suffix, path))
}

// notePath: the file of a note user may change.
func notePath(user asker, slug string) (string, error) {
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
func moveQuicknote(user asker, slug, folder, name string, keepDate bool, suffix string) (string, bool, error) {
	oldPath, err := notePath(user, slug)
	if err != nil {
		return "", false, err
	}
	date := ""
	if keepDate {
		date = noteDate(slug)
	}
	newSlug := mdtext.PromotedSlug(folder, name, date)
	if newSlug == "" {
		return "", false, refuse("Give the page a name")
	}
	if isQuicknote(newSlug) {
		return "", false, refuse("That name would still be a note")
	}
	// Move to wiki says where the note goes, and that is what was asked:
	// nothing more to ask (a shared note, one from before users, goes into a
	// private folder only by who may take a page from others: checkReaders).
	if err := checkTarget(user, slug, newSlug, sure); err != nil {
		return "", false, err
	}
	kept, err := keepPending(oldPath, slug)
	if err != nil {
		return "", false, err
	}
	_, err = movePage(user, oldPath, safePath(newSlug), slug, newSlug, "Promote "+slug+" to "+newSlug+suffix)
	if _, ok := err.(*linksLeft); err != nil && !ok {
		return "", kept, err
	}
	return newSlug, kept, err // moved, links left or not
}

var headingTextRE = regexp.MustCompile(`(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$`)

// appendQuicknote appends a note to the page target as a `## heading`
// section (its tags into the page's frontmatter) and deletes the note;
// links to the note point at that section, all in one commit; what the
// note and the page held uncommitted is committed first (keepPending), by
// either door, so the commit holds only the append. Returns the section's
// anchor, and whether it kept any of the note's (kept) and of the page's
// (keptPage).
func appendQuicknote(user asker, slug, target, heading, suffix string) (anchor string, kept, keptPage bool, err error) {
	oldPath, err := notePath(user, slug)
	if err != nil {
		return "", false, false, err
	}
	// The page's, and those whose links to the note follow.
	if err := commitOthersDrafts(user); err != nil {
		return "", false, false, err
	}
	// [ ] | would break the wikilinks that now point at the section.
	heading = strings.TrimSpace(mdtext.WikilinkBreakers.ReplaceAllString(heading, ""))
	targetPath := ""
	if target != "" && canWrite(user, target) {
		targetPath = safePath(target)
	}
	if targetPath == "" || !exists(targetPath) {
		if target == "" {
			return "", false, false, refuse("Choose a page")
		}
		return "", false, false, refuse("There is no page “" + target + "”")
	}
	if target == slug {
		return "", false, false, refuse("Choose another page than this note")
	}
	if heading == "" {
		return "", false, false, refuse("Give the section a heading")
	}
	pageText, err := readText(targetPath)
	if err != nil {
		return "", false, false, err
	}
	anchor = mdtext.SectionAnchor(heading)
	masked, stash := mdtext.MaskCode(pageText) // no headings in code; a heading's own code counts
	for _, m := range headingTextRE.FindAllStringSubmatch(masked, -1) {
		if mdtext.SectionAnchor(mdtext.UnmaskCode(m[1], stash)) == anchor {
			return "", false, false, refuse("“" + target + "” already has a section “" + heading + "”")
		}
	}
	if keptPage, err = keepPending(targetPath, target); err != nil {
		return "", false, false, err
	}
	if kept, err = keepPending(oldPath, slug); err != nil {
		return "", false, false, err
	}
	note, err := readText(oldPath)
	if err != nil {
		return "", false, false, err
	}
	section, tags := mdtext.NoteAsSection(note, heading)
	pageText = mdtext.AddFrontmatterTags(pageText, tags)
	if err := writeText(targetPath, trimNewlines(pageText)+"\n\n"+section); err != nil {
		return "", false, false, err
	}
	if err := os.Remove(oldPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", false, false, err
	}
	refs, linkErr := updateReferences(slug, target, heading)
	if err := notCommitted(gitCommit(user.Name, "Append "+slug+" to "+target+suffix, append([]string{oldPath, targetPath}, refs...)...)); err != nil {
		return anchor, kept, keptPage, err
	}
	return anchor, kept, keptPage, linkErr
}
