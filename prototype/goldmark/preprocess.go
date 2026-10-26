// Leaf's text pre-processing before markdown, in Go: what src/render.py's
// render_md does to a page before md.render (frontmatter off, code masked,
// [[deck]] and [[task:…]] swapped for placeholders, [[wikilinks]] as HTML,
// code back). Ported from mdtext.py, render.py, deck.py and decksyntax.py;
// the regexes that Go's RE2 can't run (lookarounds, a backreference) are
// scanners here. Python's string functions differ from Go's in what they
// call whitespace and a line break; pyStrip and splitLines are Python's.
package main

import (
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Wiki is what pre-processing needs to know of the wiki: its pages, and
// those to leave out (sharing mode on and the page not for sharing).
type Wiki struct {
	Slugs  map[string]bool
	Hidden map[string]bool
}

// Frontmatter is parse_frontmatter's metadata: tags, tldr, sources.
type Frontmatter struct {
	Tags    []string `json:"tags,omitempty"`
	Tldr    string   `json:"tldr,omitempty"`
	Sources []string `json:"sources,omitempty"`
}

// Prepared is a page ready for Render: the markdown, its frontmatter, and
// what the placeholders in it stand for (after rendering, deck embeds and
// task chips replace them).
type Prepared struct {
	Meta     Frontmatter `json:"meta"`
	Text     string      `json:"text"`
	Decks    []string    `json:"decks"`     // each [[deck …]]'s parameters, placeholder LEAFDECKPLACEHOLDER_<i>
	TaskRefs []string    `json:"task_refs"` // ids, lowercased, token <i>
}

// Prepare is render_md up to md.render.
func Prepare(content string, wiki *Wiki) Prepared {
	var p Prepared
	p.Meta, content = ParseFrontmatter(content)
	text, stash := MaskCode(content)
	text = deckRE.ReplaceAllStringFunc(text, func(m string) string {
		p.Decks = append(p.Decks, deckRE.FindStringSubmatch(m)[1])
		return "LEAFDECKPLACEHOLDER_" + strconv.Itoa(len(p.Decks)-1)
	})
	text = taskRefRE.ReplaceAllStringFunc(text, func(m string) string {
		p.TaskRefs = append(p.TaskRefs, strings.ToLower(taskRefRE.FindStringSubmatch(m)[1]))
		return "" + strconv.Itoa(len(p.TaskRefs)-1) + ""
	})
	p.Text = UnmaskCode(Wikilinks(text, wiki), stash)
	return p
}

var (
	deckRE    = regexp.MustCompile(`(?i)\[\[deck(?:` + pySpace + `+([^\]]*))?\]\]`) // decksyntax.DECK_RE
	taskRefRE = regexp.MustCompile(`\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]`)  // decksyntax.TASK_REF_RE
)

// ---- Python's notion of whitespace and lines ----

// pySpace is Python's \s in a str pattern.
const pySpace = `[\s\pZ\x{85}\x{1c}-\x{1f}]`

// pyIsSpace is str.isspace: Go's unicode.IsSpace and \x1c–\x1f.
func pyIsSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

func pyStrip(s string) string { return strings.TrimFunc(s, pyIsSpace) }

// splitLines is str.splitlines(keepends=True).
func splitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		end := i + size
		switch r {
		case '\r':
			if end < len(s) && s[end] == '\n' {
				end++
			}
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
		default:
			i = end
			continue
		}
		lines = append(lines, s[start:end])
		start, i = end, end
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

// ---- frontmatter (mdtext.parse_frontmatter) ----

// frontmatterEnd: the index of the line closing a leading --- block, or -1.
func frontmatterEnd(lines []string) int {
	if len(lines) == 0 || pyStrip(lines[0]) != "---" {
		return -1
	}
	for i := 1; i < len(lines); i++ {
		if pyStrip(lines[i]) == "---" {
			return i
		}
	}
	return -1
}

var (
	fmItemRE  = regexp.MustCompile(`^` + pySpace + `*-` + pySpace + `+(.*?)` + pySpace + `*$`)
	fmValueRE = regexp.MustCompile(`(?:\[[^\]]*\]|\([^)]*\)|[^,])+`)
	fmBlockRE = regexp.MustCompile(`^([>|])[+-]?\p{Nd}?[+-]?$`) // Python's \d is any digit
	fmParaRE  = regexp.MustCompile(`\n[ \t]*\n`)
)

// ParseFrontmatter: a leading --- block's tags, tldr and sources, and the
// content without the block (and one blank line after it).
func ParseFrontmatter(content string) (Frontmatter, string) {
	var meta Frontmatter
	lines := strings.Split(content, "\n")
	end := frontmatterEnd(lines)
	if end < 0 {
		return meta, content
	}
	key := ""
	var tldr []string // the tldr's first value and the lines after it
	open := false
	for _, line := range lines[1 : end+1] { // the closing fence ends a tldr still open
		if open {
			if strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t") || pyStrip(line) == "" {
				tldr = append(tldr, line)
				continue
			}
			if text := frontmatterText(tldr[0], tldr[1:]); text != "" {
				meta.Tldr = text
			}
			open = false
		}
		if m := fmItemRE.FindStringSubmatch(line); m != nil {
			if v := strings.Trim(m[1], `"'`); v != "" {
				switch key {
				case "tags":
					meta.Tags = append(meta.Tags, v)
				case "sources":
					meta.Sources = append(meta.Sources, v)
				}
			}
			continue
		}
		k, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key = strings.ToLower(pyStrip(k))
		value = pyStrip(value)
		switch key {
		case "tags", "sources":
			if values := frontmatterValues(value); values != nil {
				if key == "tags" {
					meta.Tags = values
				} else {
					meta.Sources = values
				}
			}
		case "tldr":
			tldr, open = []string{value}, true
		}
	}
	rest := lines[end+1:]
	if len(rest) > 0 && pyStrip(rest[0]) == "" {
		rest = rest[1:]
	}
	return meta, strings.Join(rest, "\n")
}

// frontmatterValues: `a, b` or `[a, b]`; a comma inside [...] or (...)
// doesn't split.
func frontmatterValues(value string) []string {
	if strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]") && !strings.HasPrefix(value, "[[") {
		value = value[1 : len(value)-1]
	}
	var out []string
	for _, v := range fmValueRE.FindAllString(value, -1) {
		if pyStrip(v) != "" {
			out = append(out, strings.Trim(pyStrip(v), `"'`))
		}
	}
	return out
}

// frontmatterText: a text value, the line's `value` and the indented (or
// blank) lines after it: `>` folded, `|` literal, else a folded plain value.
func frontmatterText(value string, more []string) string {
	block := fmBlockRE.FindStringSubmatch(value)
	if block != nil && block[1] == "|" {
		return strings.Trim(dedent(strings.Join(more, "\n")), "\n")
	}
	lines := more
	if block == nil {
		lines = append([]string{value}, more...)
	}
	var paras []string
	for _, p := range fmParaRE.Split(strings.Join(lines, "\n"), -1) {
		if p = strings.Join(strings.FieldsFunc(p, pyIsSpace), " "); p != "" {
			paras = append(paras, p)
		}
	}
	text := strings.Join(paras, "\n")
	if block != nil {
		return text
	}
	return pyStrip(strings.Trim(text, `"'`))
}

// dedent is textwrap.dedent (Python 3.13+): lines of whitespace only
// (str.isspace's) emptied, and the run of spaces and tabs that the smallest
// and the largest of the other lines start with taken off every other line.
func dedent(s string) string {
	lines := strings.Split(s, "\n")
	blank := func(l string) bool { return strings.TrimFunc(l, pyIsSpace) == "" }
	lo, hi, first := "", "", true
	for _, l := range lines {
		if blank(l) {
			continue
		}
		if first || l < lo {
			lo = l
		}
		if first || l > hi {
			hi = l
		}
		first = false
	}
	margin := 0
	for margin < len(lo) && lo[margin] == hi[margin] && (lo[margin] == ' ' || lo[margin] == '\t') {
		margin++
	}
	for i, l := range lines {
		if blank(l) {
			lines[i] = ""
		} else {
			lines[i] = l[margin:]
		}
	}
	return strings.Join(lines, "\n")
}

// ---- code masking (mdtext.mask_code, iter_fenced_lines) ----

// MaskCode swaps fenced code and inline code spans for <i>, so
// [[…]] in code is never rewritten; UnmaskCode puts them back.
func MaskCode(text string) (string, []string) {
	var stash []string
	token := func(s string) string {
		stash = append(stash, s)
		return "" + strconv.Itoa(len(stash)-1) + ""
	}
	var out strings.Builder
	for _, l := range fencedLines(splitLines(text)) {
		if l.inFence {
			body := strings.TrimRight(l.line, "\n")
			out.WriteString(token(body) + l.line[len(body):])
		} else {
			out.WriteString(maskInlineCode(l.line, token))
		}
	}
	return out.String(), stash
}

var maskTokenRE = regexp.MustCompile("(\\d+)")

func UnmaskCode(text string, stash []string) string {
	return maskTokenRE.ReplaceAllStringFunc(text, func(m string) string {
		i, err := strconv.Atoi(maskTokenRE.FindStringSubmatch(m)[1])
		if err != nil || i >= len(stash) {
			return m // the page's own text, not a token of ours
		}
		return stash[i]
	})
}

type fencedLine struct {
	line    string
	inFence bool // a fence line (``` / ~~~) or one between two
}

// fencedLines is iter_fenced_lines: a closing fence has the opening one's
// character, at least as many.
func fencedLines(lines []string) []fencedLine {
	out := make([]fencedLine, len(lines))
	inFence, marker := false, ""
	for i, line := range lines {
		s := pyStrip(line)
		if !inFence {
			if strings.HasPrefix(s, "```") || strings.HasPrefix(s, "~~~") {
				inFence = true
				marker = strings.Repeat(s[:1], len(s)-len(strings.TrimLeft(s, s[:1])))
			}
			out[i] = fencedLine{line, inFence}
			continue
		}
		if strings.HasPrefix(s, marker) && strings.TrimRight(s, marker[:1]) == "" {
			inFence = false
		}
		out[i] = fencedLine{line, true}
	}
	return out
}

// maskInlineCode is _INLINE_CODE_RE's sub on one line,
// (?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`): a whole run of backticks, then the
// shortest text (no \n, at least a character) up to a run of exactly as many.
func maskInlineCode(line string, token func(string) string) string {
	var out strings.Builder
	i := 0
	for i < len(line) {
		if line[i] != '`' {
			out.WriteByte(line[i])
			i++
			continue
		}
		n := runLen(line, i)
		end := closingRun(line, i+n, n)
		if end < 0 {
			out.WriteString(line[i : i+n])
			i += n
			continue
		}
		out.WriteString(token(line[i:end]))
		i = end
	}
	return out.String()
}

func runLen(s string, i int) int {
	n := 0
	for i+n < len(s) && s[i+n] == '`' {
		n++
	}
	return n
}

// closingRun: the end of the first run of exactly n backticks after at
// least one character from `from`, before any \n; -1 when there is none.
func closingRun(s string, from, n int) int {
	for k := from + 1; k < len(s); k++ {
		if s[k-1] == '\n' {
			return -1
		}
		if s[k] == '`' && s[k-1] != '`' {
			if runLen(s, k) == n {
				return k + n
			}
		}
	}
	return -1
}

// ---- wikilinks (render.process_wikilinks) ----

var wikilinkRE = regexp.MustCompile(`\[\[([^\[\]]+)\]\]`)

// Wikilinks: [[Page]], [[Page|Label]], [[Page#Section]], [[#Section]] as
// links; to a page missing from the wiki with class wikilink-missing, to a
// page left out in sharing mode as its label only.
func Wikilinks(text string, wiki *Wiki) string {
	return wikilinkRE.ReplaceAllStringFunc(text, func(m string) string {
		page, section, label := SplitWikilink(m[2 : len(m)-2])
		anchor := ""
		if section != "" {
			anchor = "#" + pyEscape(SectionAnchor(section))
		}
		label = pyEscape(label)
		if page == "" {
			if anchor == "" {
				anchor = "#"
			}
			return `<a href="` + anchor + `" class="wikilink">` + label + `</a>`
		}
		slug := page
		if !wiki.Slugs[page] {
			slug = ToSlug(page)
		}
		if wiki.Hidden[slug] {
			return `<span class="wikilink-hidden">` + label + `</span>`
		}
		css := "wikilink"
		if !wiki.Slugs[slug] {
			css += " wikilink-missing"
		}
		return `<a href="` + pageURL("page", slug) + anchor + `" class="` + css + `">` + label + `</a>`
	})
}

// SplitWikilink: a wikilink's inside as page, section, label; the label is
// the page, "Page › Section", or the section alone, unless given after |.
func SplitWikilink(inner string) (page, section, label string) {
	target, label, bar := strings.Cut(inner, "|")
	page, section, _ = strings.Cut(target, "#")
	page, section = pyStrip(page), pyStrip(section)
	if !bar {
		switch {
		case page != "" && section != "":
			label = page + " › " + section
		case section != "":
			label = section
		default:
			label = page
		}
	}
	return page, section, pyStrip(label)
}

var toSlugRE = regexp.MustCompile(`[^a-z0-9_]+`)

// ToSlug: each part between / lowercased, runs of other characters a hyphen.
func ToSlug(name string) string {
	var parts []string
	for _, part := range strings.Split(pyStrip(name), "/") {
		s := strings.Trim(toSlugRE.ReplaceAllString(strings.ToLower(pyStrip(part)), "-"), "-")
		if s != "" {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, "/")
}

// SectionAnchor is mdtext.section_anchor: the id of the heading a
// #Section points at, from its text with the markdown taken off.
func SectionAnchor(section string) string {
	return headingID(plainHeading(section))
}

var headingIDRE = regexp.MustCompile(`[^\p{L}\p{N}_\- ]+`)

func headingID(text string) string {
	return strings.ReplaceAll(strings.ToLower(pyStrip(headingIDRE.ReplaceAllString(text, ""))), " ", "-")
}

var plainHeadingRules = []struct {
	re   *regexp.Regexp
	with string
}{
	{regexp.MustCompile(`\[\[([^\]|]+)\|([^\]]+)\]\]`), "$2"}, // [[page|label]]
	{regexp.MustCompile(`\[\[([^\]]+)\]\]`), "$1"},            // [[page]]
	{regexp.MustCompile(`!?\[([^\]]*)\]\([^)]*\)`), "$1"},     // [label](url), ![alt](url)
	{regexp.MustCompile(`\[([^\]]*)\]\[[^\]]*\]`), "$1"},      // [label][ref]
	{regexp.MustCompile(`!?\[([^\]]*)\]`), "$1"},              // bare [label]
	{regexp.MustCompile(`\*\*(.+?)\*\*`), "$1"},
	{regexp.MustCompile(`\*(.+?)\*`), "$1"},
	{regexp.MustCompile(`__(.+?)__`), "$1"},
	{regexp.MustCompile(`_(.+?)_`), "$1"},
	{regexp.MustCompile("`[^`]*`"), ""},
}

func plainHeading(text string) string {
	for _, r := range plainHeadingRules {
		text = r.re.ReplaceAllString(text, r.with)
	}
	return pyStrip(text)
}

// pyEscape is html.escape: & < > " and '.
var pyEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;")

func pyEscape(s string) string { return pyEscaper.Replace(s) }

// pageURL is pages.url: each part percent-encoded as urllib's quote does
// (letters, digits, _.-~ and / stay).
func pageURL(parts ...string) string {
	var b strings.Builder
	for _, p := range parts {
		b.WriteByte('/')
		for i := 0; i < len(p); i++ {
			c := p[i]
			if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.IndexByte("_.-~/", c) >= 0 {
				b.WriteByte(c)
			} else {
				b.WriteString("%" + strings.ToUpper(strconv.FormatInt(int64(c)>>4, 16)) + strings.ToUpper(strconv.FormatInt(int64(c)&15, 16)))
			}
		}
	}
	return b.String()
}
