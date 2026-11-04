package main

// A page's sections, as the section editor cuts them and the ✎ buttons count
// them. Both go by the markdown parser's headings (sectionHeading), so the
// Nth ✎ opens the Nth section, whatever else the page holds. The ranges
// count characters, as the editor does.

import (
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/text"

	"zonewiki/internal/mdtext"
)

// sectionHeading: a heading that starts a section: a ## or ### directly in
// the page, not one in a quote, a list or a footnote, where a section's text
// would start halfway through that block.
func sectionHeading(n *ast.Heading) bool {
	return (n.Level == 2 || n.Level == 3) && n.Parent() != nil && n.Parent().Kind() == ast.KindDocument
}

// pageHeading: a heading directly in a text, as the parser reads it: its
// level and text, the offset of the line it starts on and of what follows it.
type pageHeading struct {
	level       int
	text        string
	start, body int
}

var atxLineRE = regexp.MustCompile(`^ {0,3}#{1,6}(?:[ \t\r\n]|$)`)

// headingsIn: the headings of markdown without frontmatter, not those in a
// quote, a list or a footnote.
func headingsIn(body string) []pageHeading {
	src := []byte(body)
	m := blockRenderers.Get().(*mdRenderer)
	defer blockRenderers.Put(m)
	m.transformer.env = &Env{}
	doc := m.md.Parser().Parse(text.NewReader(src))
	m.transformer.env = nil
	lineEnd := func(at int) int {
		if i := strings.IndexByte(body[at:], '\n'); i >= 0 {
			return at + i + 1
		}
		return len(body)
	}
	var found []pageHeading
	for n := doc.FirstChild(); n != nil; n = n.NextSibling() {
		h, ok := n.(*ast.Heading)
		if !ok {
			continue
		}
		// Pos is where the heading's text or marks start, past any indent.
		pos := max(h.Pos(), 0)
		start := strings.LastIndexByte(body[:pos], '\n') + 1
		var words []string
		last := pos
		for i := range h.Lines().Len() {
			line := h.Lines().At(i)
			words = append(words, strings.TrimSpace(string(line.Value(src))))
			last = max(last, line.Stop-1)
		}
		after := lineEnd(last)
		if !atxLineRE.MatchString(body[start:]) { // setext: its underline too
			after = lineEnd(after)
		}
		found = append(found, pageHeading{h.Level, strings.Join(words, " "), start, after})
	}
	return found
}

// pageHeadings: the headings of a page; offsets are into content. The
// frontmatter is no markdown: a page's render leaves it out too.
func pageHeadings(content string) []pageHeading {
	_, body := mdtext.ParseFrontmatter(content)
	offset := len(content) - len(body)
	found := headingsIn(body)
	for i := range found {
		found[i].start += offset
		found[i].body += offset
	}
	return found
}

// startsSection: pageHeadings has only headings directly in the page, so its
// level tells (sectionHeading, for the parser's nodes).
func (h pageHeading) startsSection() bool { return h.level == 2 || h.level == 3 }

// pageSections cuts content at its sections: [preamble, section1, section2,
// …]. The preamble holds the frontmatter; the parts join to content again.
func pageSections(content string) []string {
	var sections []string
	cut := 0
	for _, h := range pageHeadings(content) {
		if h.startsSection() {
			sections = append(sections, content[cut:h.start])
			cut = h.start
		}
	}
	return append(sections, content[cut:])
}

// sectionRange: the start and end (characters) of section idx (1-based; 0,
// the preamble, is no section); ok false for an invalid index.
func sectionRange(content string, idx int) (start, end int, ok bool) {
	sections := pageSections(content)
	if idx < 1 || idx > len(sections)-1 {
		return 0, 0, false
	}
	for _, s := range sections[:idx] {
		start += utf8.RuneCountInString(s)
	}
	return start, start + utf8.RuneCountInString(sections[idx]), true
}

// findSection: the section meant by a number from an older look at the page
// and by the key of its heading line then (headingKey): that number if its
// heading still has the key, else the one section whose heading has it (a
// section added above moves the numbers). ok false if none, or several and
// the number isn't one of them. Idx 0 goes by the key alone.
func findSection(content string, idx int, key string) (found, start, end int, ok bool) {
	at, matches := 0, 0
	for i, section := range pageSections(content) {
		size := utf8.RuneCountInString(section)
		if i > 0 && headingKey(firstLine(section)) == key {
			if i == idx {
				return i, at, at + size, true
			}
			found, start, end = i, at, at+size
			matches++
		}
		at += size
	}
	return found, start, end, matches == 1
}

// spliceIntoPage: content with text in the place of the section that had
// heading at number idx. ok false, and text added at the page's end, when
// that section can't be told: nothing of the page is replaced then.
func spliceIntoPage(content string, idx int, heading, text string) (merged string, ok bool) {
	_, start, end, found := findSection(content, idx, headingKey(heading))
	if !found {
		return strings.TrimRight(content, "\r\n") + "\n\n" + strings.TrimRight(text, "\r\n") + "\n", false
	}
	merged, _, _, _, _ = mdtext.SpliceSection(&content, mdtext.ContentHash(content), start, end, text)
	return merged, true
}

// sectionKeys: a key per section of a page, for its ✎ link, by which a link
// from a page view loaded before the page changed still finds its section
// (editPage). The headings are the page's (pageHeadings).
func sectionKeys(content string, headings []pageHeading) []string {
	var keys []string
	for _, h := range headings {
		if h.startsSection() {
			keys = append(keys, headingKey(firstLine(content[h.start:])))
		}
	}
	return keys
}

// headingKey: a short hash of a section's heading line (firstLine). A section
// always has a heading line, so the key of an empty one matches none.
func headingKey(line string) string { return mdtext.ContentHash(line)[:8] }
