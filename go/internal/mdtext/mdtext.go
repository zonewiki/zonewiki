// Package mdtext is markdown source text: frontmatter, fenced code, code
// and math masking, headings and their anchors, wikilink syntax, splicing a
// section's text, task-list checkboxes, slugs. Pure functions on strings, no files and no
// Zonewiki state. Offsets into a text (sections, splices) count characters
// (runes): the editor sends them back.
package mdtext

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

// Whitespace in a pattern, as unicode.IsSpace counts it (RE2's \s is
// ASCII only, without \v): SpaceClass and its negation NonSpaceClass.
const (
	SpaceClass    = `[\s\v\pZ\x{85}]`
	NonSpaceClass = `[^\s\v\pZ\x{85}]`
)

var WikilinkRE = regexp.MustCompile(`\[\[([^\[\]]+)\]\]`)

// SlugDisplayName is a title from a slug ("my-page" → "My Page").
func SlugDisplayName(slug string) string {
	return TitleWords(strings.ReplaceAll(slug[strings.LastIndex(slug, "/")+1:], "-", " "))
}

// TitleWords: s with each word's first letter in upper (title) case, the
// rest as written ("my page" → "My Page", "my_notes" → "My_Notes", "it's
// 2nd" → "It's 2nd").
func TitleWords(s string) string {
	var b strings.Builder
	start := true
	for _, r := range s {
		if start {
			r = unicode.ToTitle(r)
		}
		b.WriteRune(r)
		start = !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '\'' && r != '’'
	}
	return b.String()
}

var toSlugRE = regexp.MustCompile(`[^a-z0-9_]+`)

// BadNameRune: r has no place in a clean name of a file: a control
// character or a backslash, which git escapes where it lists files without
// -z (package main's cleanPath sees no file so named).
func BadNameRune(r rune) bool { return unicode.IsControl(r) || r == '\\' }

// ToSlug slugifies a name, keeping "/" between folders: each part
// lowercased, runs of other characters a hyphen.
func ToSlug(name string) string {
	var parts []string
	for _, part := range strings.Split(strings.TrimSpace(name), "/") {
		s := strings.Trim(toSlugRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(part)), "-"), "-")
		if s != "" {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, "/")
}

// PageTitle is the first "# " heading in the file (not in frontmatter or
// fenced code: a shell comment in an example never becomes the title), else
// a title from the slug.
func PageTitle(content, slug string) string {
	lines := SplitLines(content)
	start := bodyStart(lines)
	for i, l := range FencedLines(lines) {
		if i >= start && !l.InFence {
			if title, ok := h1Text(LineBody(l.Line)); ok {
				return title
			}
		}
	}
	return SlugDisplayName(slug)
}

// h1Text: the text of "# Title" (^#[ \t]+(\S.*)$), stripped.
func h1Text(line string) (string, bool) {
	if !strings.HasPrefix(line, "#") {
		return "", false
	}
	rest := strings.TrimLeft(line[1:], " \t")
	if len(rest) == len(line)-1 || rest == "" {
		return "", false
	}
	if r, _ := utf8.DecodeRuneInString(rest); unicode.IsSpace(r) {
		return "", false
	}
	return strings.TrimSpace(rest), true
}

// SplitWikilink: a wikilink's inside as page, section, label: `Page`,
// `Page|Label`, `Page#Section`, `Page#Section|Label`, `#Section`. The label
// is the page, "Page › Section", or the section alone, unless given.
func SplitWikilink(inner string) (page, section, label string) {
	target, label, bar := strings.Cut(inner, "|")
	page, section, _ = strings.Cut(target, "#")
	page, section = strings.TrimSpace(page), strings.TrimSpace(section)
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
	return page, section, strings.TrimSpace(label)
}

// SectionAnchor is the heading id a wikilink's #Section points at.
func SectionAnchor(section string) string { return HeadingID(PlainHeading(section)) }

var headingIDRE = regexp.MustCompile(`[^\p{L}\p{N}_\- ]+`)

func HeadingID(text string) string {
	return strings.ReplaceAll(strings.ToLower(strings.TrimSpace(headingIDRE.ReplaceAllString(text, ""))), " ", "-")
}

type rule struct {
	re   *regexp.Regexp
	with string
}

var stripLinkRules = []rule{
	{regexp.MustCompile(`\[\[([^\]|]+)\|([^\]]+)\]\]`), "$2"}, // [[page|label]] → label
	{regexp.MustCompile(`\[\[([^\]]+)\]\]`), "$1"},            // [[page]] → page
	{regexp.MustCompile(`!?\[([^\]]*)\]\([^)]*\)`), "$1"},     // [label](url) / ![alt](url)
	{regexp.MustCompile(`\[([^\]]*)\]\[[^\]]*\]`), "$1"},      // [label][ref]
}

var plainHeadingRules = []rule{
	{regexp.MustCompile(`!?\[([^\]]*)\]`), "$1"}, // bare [label]
	{regexp.MustCompile(`\*\*(.+?)\*\*`), "$1"},
	{regexp.MustCompile(`\*(.+?)\*`), "$1"},
	{regexp.MustCompile(`__(.+?)__`), "$1"},
	{regexp.MustCompile(`_(.+?)_`), "$1"},
	{regexp.MustCompile("`[^`]*`"), ""},
}

func applyRules(text string, rules []rule) string {
	for _, r := range rules {
		text = r.re.ReplaceAllString(text, r.with)
	}
	return text
}

// stripLinks: wikilinks and markdown links as their label text.
func stripLinks(text string) string { return applyRules(text, stripLinkRules) }

// PlainHeading: a heading's text without inline markdown and links, for
// its anchor. Code and math stay their text, as the render's heading id
// has them (`a_b` and $a_1$ are no emphasis): masked while the rules run.
func PlainHeading(text string) string {
	var kept []string
	var masked strings.Builder
	at := 0
	for _, s := range literalSpans(text) {
		masked.WriteString(text[at:s.start])
		masked.WriteString("\ue004" + strconv.Itoa(len(kept)) + "\ue005")
		kept = append(kept, s.text)
		at = s.end
	}
	masked.WriteString(text[at:])
	plain := applyRules(stripLinks(masked.String()), plainHeadingRules)
	if len(kept) > 0 {
		plain = keptTokenRE.ReplaceAllStringFunc(plain, func(m string) string {
			i, err := strconv.Atoi(m[3 : len(m)-3])
			if err != nil || i >= len(kept) {
				return m
			}
			return kept[i]
		})
	}
	return strings.TrimSpace(plain)
}

var keptTokenRE = regexp.MustCompile("\ue004\\d+\ue005")

// literalSpan: a code span or inline math in a line, and the text it shows.
type literalSpan struct {
	start, end int
	text       string
}

// literalSpans: a line's code spans and inline math, in order; a code
// span's text without its backticks and, as CommonMark has it, one space
// at each end when both have one, math's its TeX.
func literalSpans(line string) []literalSpan {
	var spans []literalSpan
	for _, c := range InlineCodeSpans(line) {
		n := runLen(line, c[0])
		code := line[c[0]+n : c[1]-n]
		if len(code) > 1 && code[0] == ' ' && code[len(code)-1] == ' ' && strings.Trim(code, " ") != "" {
			code = code[1 : len(code)-1]
		}
		spans = append(spans, literalSpan{c[0], c[1], code})
	}
	for _, m := range InlineMathSpans(line) {
		spans = append(spans, literalSpan{m[0], m[1], line[m[0]+m[2] : m[1]-m[2]]})
	}
	sort.Slice(spans, func(i, j int) bool { return spans[i].start < spans[j].start })
	return spans
}

// Task metadata in markdown: - [ ] task <!--t:2026-06-12T18:35:00-->,
// - [x] task <!--t:2026-06-12T18:35:00 d:2026-06-12T19:00:00-->.
const isoSecond = `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}`

var (
	TaskMetaSrcRE  = regexp.MustCompile(`<!--t:(` + isoSecond + `)(?:` + SpaceClass + `+d:(` + isoSecond + `))?-->`)
	TaskDoneOnlyRE = regexp.MustCompile(`<!--d:(` + isoSecond + `)-->`)
)

// ---- Lines ----

// SplitLines: s's lines, each with its line end, which is any of \n, \r\n,
// \r, \v, \f, the separators U+001C–U+001E, NEL, U+2028 and U+2029.
func SplitLines(s string) []string {
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

// LineBody is a line from SplitLines without its line break.
func LineBody(line string) string {
	for _, br := range []string{"\r\n", "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\u0085", " ", " "} {
		if strings.HasSuffix(line, br) {
			return line[:len(line)-len(br)]
		}
	}
	return line
}

// ---- fenced code and masking ----

type FencedLine struct {
	Line    string
	InFence bool // a fence line (``` / ~~~ / $$) or one between two
}

// FencedLines is iter_fenced_lines: a closing fence has the opening one's
// character, at least as many. Display math counts as a fence too, as
// math.go parses it: from a $$ line to a line ending in $$, or $$tex$$ on
// its own line.
func FencedLines(lines []string) []FencedLine {
	out := make([]FencedLine, len(lines))
	inFence, marker := false, ""
	for i, line := range lines {
		s := strings.TrimSpace(line)
		if !inFence {
			if strings.HasPrefix(s, "```") || strings.HasPrefix(s, "~~~") {
				marker = strings.Repeat(s[:1], len(s)-len(strings.TrimLeft(s, s[:1])))
				// A backtick after a ``` is inline code (```x``` y), no fence.
				inFence = s[0] != '`' || !strings.Contains(s[len(marker):], "`")
			} else if opens, whole := MathFence(line); opens {
				inFence, marker = true, "$$"
			} else if whole {
				out[i] = FencedLine{line, true}
				continue
			}
			out[i] = FencedLine{line, inFence}
			continue
		}
		if marker == "$$" {
			inFence = !MathFenceEnd(line)
		} else if strings.HasPrefix(s, marker) && strings.TrimRight(s, marker[:1]) == "" {
			inFence = false
		}
		out[i] = FencedLine{line, true}
	}
	return out
}

// MaskCode swaps fenced code, inline code spans, display math and inline
// math with a [[ in it (math.go) for <i>, so [[…]] in them is
// never rewritten (math in a link's text stays, for the link); UnmaskCode puts them back. Text that
// looks like a token is stashed too, so it comes back as written.
func MaskCode(text string) (string, []string) {
	var stash []string
	token := func(s string) string {
		stash = append(stash, s)
		return "" + strconv.Itoa(len(stash)-1) + ""
	}
	var out strings.Builder
	for _, l := range FencedLines(SplitLines(text)) {
		if l.InFence {
			body := strings.TrimRight(l.Line, "\n")
			out.WriteString(token(body) + l.Line[len(body):])
		} else {
			out.WriteString(maskInlineCode(l.Line, token))
		}
	}
	return out.String(), stash
}

var maskTokenRE = regexp.MustCompile("(\\d+)")

func UnmaskCode(text string, stash []string) string {
	return maskTokenRE.ReplaceAllStringFunc(text, func(m string) string {
		i, err := strconv.Atoi(maskTokenRE.FindStringSubmatch(m)[1])
		if err != nil || i >= len(stash) {
			return m
		}
		return stash[i]
	})
}

// maskInlineCode is _MASKED_RE's sub on one line: text that looks like one
// of our tokens, and inline code, (?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`): a
// whole run of backticks, the shortest text (no \n, at least a character)
// up to a run of exactly as many.
func maskInlineCode(line string, token func(string) string) string {
	var out strings.Builder
	i := 0
	for i < len(line) {
		if n := lookalikeLen(line[i:], '', ''); n > 0 {
			out.WriteString(token(line[i : i+n]))
			i += n
			continue
		}
		if line[i] == '\\' && i+1 < len(line) && line[i+1] == '$' {
			out.WriteString(line[i : i+2])
			i += 2
			continue
		}
		if line[i] == '$' {
			if n, _ := InlineMath(line[i:]); n > 0 && strings.Contains(line[i:i+n], "[[") {
				out.WriteString(token(line[i : i+n]))
				i += n
				continue
			}
		}
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

// InlineCodeSpans: the byte ranges of a line's inline code, as maskInlineCode finds them.
func InlineCodeSpans(line string) [][2]int {
	var spans [][2]int
	for i := 0; i < len(line); {
		if line[i] != '`' {
			i++
			continue
		}
		n := runLen(line, i)
		if end := closingRun(line, i+n, n); end >= 0 {
			spans = append(spans, [2]int{i, end})
			i = end
		} else {
			i += n
		}
	}
	return spans
}

// lookalikeLen: the length of open, digits, close at the start of s;
// 0 when s doesn't start so.
func lookalikeLen(s string, open, close rune) int {
	r, n := utf8.DecodeRuneInString(s)
	if r != open {
		return 0
	}
	digits := 0
	for n < len(s) {
		r, size := utf8.DecodeRuneInString(s[n:])
		if r == close && digits > 0 {
			return n + size
		}
		if r < '0' || r > '9' {
			return 0
		}
		digits++
		n += size
	}
	return 0
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

// ---- checkboxes ----

// TaskLine: a checkbox where the parser found it (taskItems in
// go/checkboxes.go): its line (SplitLines) and the bytes of its mark there.
type TaskLine struct{ Index, Start, End int }

// TaskLineKeys: a key per task-list item of a page (items: where they are,
// in rendered order): its line without the checkbox's mark and the tick's
// stamps, hashed short. A tick from a page view loaded before the page
// changed finds its item by it (apiToggleCheckbox), also once it has been
// ticked.
func TaskLineKeys(content string, items []TaskLine) []string {
	lines := SplitLines(content)
	var keys []string
	for _, it := range items {
		body := strings.TrimRight(lines[it.Index], "\n")
		body = body[:it.Start] + " " + body[it.End:]
		body = TaskDoneOnlyRE.ReplaceAllString(TaskMetaSrcRE.ReplaceAllString(body, ""), "")
		keys = append(keys, ContentHash(strings.TrimSpace(body))[:8])
	}
	return keys
}

// ToggleCheckbox ticks or unticks the checkbox it, recording when it was
// done (at).
func ToggleCheckbox(content string, it TaskLine, at time.Time) string {
	lines := SplitLines(content)
	line := lines[it.Index]
	body := strings.TrimRight(line, "\n")
	eol := line[len(body):]
	if eol != "" {
		eol = "\n"
	}
	checking := body[it.Start:it.End] == " "
	mark := " "
	if checking {
		mark = "x"
	}
	body = body[:it.Start] + mark + body[it.End:]
	stamp := at.Format("2006-01-02T15:04:05")
	meta := TaskMetaSrcRE.FindStringSubmatchIndex(body)
	if checking {
		if meta != nil {
			body = body[:meta[0]] + "<!--t:" + body[meta[2]:meta[3]] + " d:" + stamp + "-->" + body[meta[1]:]
		} else if done := TaskDoneOnlyRE.FindStringIndex(body); done != nil {
			body = body[:done[0]] + "<!--d:" + stamp + "-->" + body[done[1]:]
		} else {
			body = TrimRightSpace(body) + " <!--d:" + stamp + "-->"
		}
	} else if meta != nil {
		body = body[:meta[0]] + "<!--t:" + body[meta[2]:meta[3]] + "-->" + body[meta[1]:]
	} else {
		body = TrimRightSpace(TaskDoneOnlyRE.ReplaceAllString(body, ""))
	}
	lines[it.Index] = body + eol
	return strings.Join(lines, "")
}

// TrimRightSpace: s without trailing whitespace.
func TrimRightSpace(s string) string { return strings.TrimRightFunc(s, unicode.IsSpace) }

// ---- sections ----

// ContentHash is a short hex hash of text, for change detection.
func ContentHash(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])[:16]
}

// RuneSlice is s[a:b] in characters.
func RuneSlice(s string, a, b int) string {
	r := []rune(s)
	return string(r[a:b])
}

// SpliceSection replaces content[start:end] (characters), guarded by a
// hash check; ok false when the file is gone (nil), changed, or the range
// is off. The range's trailing line breaks stay, so the next heading isn't
// glued onto the new text.
func SpliceSection(full *string, fileHash string, start, end int, newContent string) (merged, hash string, newStart, newEnd int, ok bool) {
	if full == nil || ContentHash(*full) != fileHash {
		return "", "", 0, 0, false
	}
	r := []rune(*full)
	if start < 0 || start > end || end > len(r) {
		return "", "", 0, 0, false
	}
	orig := string(r[start:end])
	trailing := orig[len(strings.TrimRight(orig, "\r\n")):]
	replacement := strings.TrimRight(newContent, "\r\n") + trailing
	merged = string(r[:start]) + replacement + string(r[end:])
	return merged, ContentHash(merged), start, start + utf8.RuneCountInString(replacement), true
}

var h1LineRE = regexp.MustCompile(`^#` + SpaceClass + `+`)

// StripH1 removes the first H1 line, so it isn't shown twice under the
// page header. A --- right under a title on a page's first line stays no
// frontmatter: the title's line stays, empty.
func StripH1(content string) string {
	lines := strings.Split(content, "\n")
	for i := bodyStart(lines); i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) != "" {
			if h1LineRE.MatchString(lines[i]) && i == 0 && len(lines) > 1 && strings.TrimSpace(lines[1]) == "---" {
				lines[0] = ""
			} else if h1LineRE.MatchString(lines[i]) {
				lines = append(lines[:i], lines[i+1:]...)
			}
			break
		}
	}
	return strings.Join(lines, "\n")
}

// ---- frontmatter ----

// frontmatterEnd: the index of the line closing a leading --- block, or -1.
func frontmatterEnd(lines []string) int {
	if len(lines) == 0 || strings.TrimSpace(lines[0]) != "---" {
		return -1
	}
	for i := 1; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "---" {
			return i
		}
	}
	return -1
}

// bodyStart: the index of the first line after a leading frontmatter block.
func bodyStart(lines []string) int { return frontmatterEnd(lines) + 1 }

// Frontmatter is what a page's leading --- block says: tags, tldr, sources.
type Frontmatter struct {
	Tags    []string
	Tldr    string
	Sources []string
}

var (
	fmItemRE  = regexp.MustCompile(`^` + SpaceClass + `*-` + SpaceClass + `+(.*?)` + SpaceClass + `*$`)
	fmValueRE = regexp.MustCompile(`(?:\[[^\]]*\]|\([^)]*\)|[^,])+`)
	fmBlockRE = regexp.MustCompile(`^([>|])[+-]?\d?[+-]?$`)
	fmParaRE  = regexp.MustCompile(`\n[ \t]*\n`)
)

// ParseFrontmatter: a leading --- block's tags, tldr and sources, and the
// content without the block (and one blank line after it). Without a block
// (or one never closed) the content is as it was.
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
			if strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t") || strings.TrimSpace(line) == "" {
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
		key = strings.ToLower(strings.TrimSpace(k))
		value = strings.TrimSpace(value)
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
	if len(rest) > 0 && strings.TrimSpace(rest[0]) == "" {
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
		if strings.TrimSpace(v) != "" {
			out = append(out, strings.Trim(strings.TrimSpace(v), `"'`))
		}
	}
	return out
}

// frontmatterText: a text value, the line's value and the indented (or
// blank) lines after it: `>` folded, `|` literal, else a folded plain value.
func frontmatterText(value string, more []string) string {
	block := fmBlockRE.FindStringSubmatch(value)
	if block != nil && block[1] == "|" {
		return strings.Trim(Dedent(strings.Join(more, "\n")), "\n")
	}
	lines := more
	if block == nil {
		lines = append([]string{value}, more...)
	}
	var paras []string
	for _, p := range fmParaRE.Split(strings.Join(lines, "\n"), -1) {
		if p = strings.Join(strings.Fields(p), " "); p != "" {
			paras = append(paras, p)
		}
	}
	text := strings.Join(paras, "\n")
	if block != nil {
		return text
	}
	return strings.TrimSpace(strings.Trim(text, `"'`))
}

// Dedent removes the indentation all lines share: lines of whitespace only
// emptied, and the run of spaces and tabs that the smallest and the largest
// of the other lines start with taken off every other line.
func Dedent(s string) string {
	lines := strings.Split(s, "\n")
	blank := func(l string) bool { return strings.TrimSpace(l) == "" }
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

// PageTags: a page's frontmatter tags, lowercased, for #tag queries.
func PageTags(content string) []string {
	meta, _ := ParseFrontmatter(content)
	out := make([]string, len(meta.Tags))
	for i, t := range meta.Tags {
		out[i] = strings.ToLower(t)
	}
	return out
}

// AddFrontmatterTags adds the tags content's frontmatter lacks (case
// ignored) to its `tags:`, adding the line, or the block, when there is none.
func AddFrontmatterTags(content string, tags []string) string {
	meta, _ := ParseFrontmatter(content)
	have := map[string]bool{}
	for _, h := range meta.Tags {
		have[strings.ToLower(h)] = true
	}
	var fresh []string
	for _, t := range tags {
		if !have[strings.ToLower(t)] {
			fresh = append(fresh, t)
		}
	}
	if len(fresh) == 0 {
		return content
	}
	line := "tags: " + strings.Join(append(append([]string{}, meta.Tags...), fresh...), ", ")
	lines := strings.Split(content, "\n")
	end := frontmatterEnd(lines)
	if end < 0 {
		return "---\n" + line + "\n---\n" + content
	}
	for i := 1; i < end; i++ {
		k, _, _ := strings.Cut(lines[i], ":")
		if strings.ToLower(strings.TrimSpace(k)) == "tags" {
			j := i + 1 // a block list's `- tag` lines: the new line has them
			for j < end && fmItemRE.MatchString(lines[j]) {
				j++
			}
			lines = append(append(append([]string{}, lines[:i]...), line), lines[j:]...)
			return strings.Join(lines, "\n")
		}
	}
	lines = append([]string{lines[0], line}, lines[1:]...)
	return strings.Join(lines, "\n")
}

// ---- links and titles ----

var plainMdRE = regexp.MustCompile(`\[([^\[\]]*)\]\(([^()\s]+)\)`)

// PlainTaskTitle: a task title with its links reduced to their labels, as
// tasks.js plainTitle does (for a title inside one link: no nested <a>).
func PlainTaskTitle(title string) string {
	title = WikilinkRE.ReplaceAllStringFunc(title, func(m string) string {
		_, _, label := SplitWikilink(m[2 : len(m)-2])
		return label
	})
	return plainMdRE.ReplaceAllStringFunc(title, func(m string) string {
		g := plainMdRE.FindStringSubmatch(m)
		if g[1] != "" {
			return g[1]
		}
		return g[2]
	})
}

// PromotedSlug: where a note moved to the wiki goes, `folder/name`,
// with `date_` before the name when the date is kept. The name makes no
// folders of its own (a / in it is a hyphen). Mirrored by PromotedSlug in
// js/pagemenu.js.
func PromotedSlug(folder, name, date string) string {
	name = ToSlug(strings.ReplaceAll(name, "/", "-"))
	if name == "" {
		return ""
	}
	if date != "" {
		name = date + "_" + name
	}
	var parts []string
	for _, p := range []string{ToSlug(folder), name} {
		if p != "" {
			parts = append(parts, p)
		}
	}
	return strings.Join(parts, "/")
}

var WikilinkBreakers = regexp.MustCompile(`[\[\]|]`)

// PageWikilink is `[[slug|label]]`, with what would break a wikilink taken
// out of the label. Mirrored by the Move to wiki dialog in js/pagemenu.js.
func PageWikilink(slug, label string) string {
	label = strings.TrimSpace(WikilinkBreakers.ReplaceAllString(label, ""))
	if label == "" {
		label = slug
	}
	return "[[" + slug + "|" + label + "]]"
}

var headingLevelRE = regexp.MustCompile(`^(#{1,6})[ \t]`)

// NoteAsSection: a note's text as a `## heading` section of another
// page, and its frontmatter tags. Its title goes (the heading replaces it),
// a tldr becomes an italic first paragraph, its headings move down so the
// highest is a ###, its sources become a list at the end.
func NoteAsSection(content, heading string) (string, []string) {
	meta, body := ParseFrontmatter(content)
	lines := strings.Split(strings.Trim(StripH1(body), "\n"), "\n")
	minLevel := 0
	for _, l := range FencedLines(lines) {
		if m := headingLevelRE.FindStringSubmatch(l.Line); m != nil && !l.InFence {
			if minLevel == 0 || len(m[1]) < minLevel {
				minLevel = len(m[1])
			}
		}
	}
	if shift := max(0, 3-minLevel); minLevel > 0 && shift > 0 {
		for i, l := range FencedLines(lines) {
			if m := headingLevelRE.FindStringSubmatch(l.Line); m != nil && !l.InFence {
				lines[i] = strings.Repeat("#", min(6, len(m[1])+shift)) + l.Line[len(m[1]):]
			}
		}
	}
	parts := []string{"## " + heading}
	if meta.Tldr != "" {
		var it []string
		for _, line := range strings.Split(meta.Tldr, "\n") {
			if strings.TrimSpace(line) != "" {
				it = append(it, "_"+strings.TrimSpace(line)+"_")
			}
		}
		parts = append(parts, strings.Join(it, "\n"))
	}
	if text := strings.Trim(strings.Join(lines, "\n"), "\n"); text != "" {
		parts = append(parts, text)
	}
	if len(meta.Sources) > 0 {
		items := make([]string, len(meta.Sources))
		for i, s := range meta.Sources {
			items[i] = "- " + s
		}
		parts = append(parts, "Sources:\n\n"+strings.Join(items, "\n"))
	}
	return strings.Join(parts, "\n\n") + "\n", meta.Tags
}

// Math, as the parser (math.go) finds it: FencedLines counts a $$ block
// as a fence, MaskCode masks inline math as it masks code.

// InlineMath: the length of the $…$ or $$…$$ at the start of s and of its
// delimiter; 0, 0 when s doesn't start with one. Pandoc's rules, so prices
// stay text: the opening $ has no space after it, the closing $ none before
// it and no digit after it ("$5 and $10", "$5–$10" are no math); $$…$$
// needs only its closing $$. On one line; a backslash keeps the next
// character from closing it (\$).
func InlineMath(s string) (n, open int) {
	open = 1
	if len(s) > 1 && s[1] == '$' {
		open = 2
	}
	body := s[open:]
	if len(body) == 0 || isSpaceByte(body[0]) || body[0] == '$' {
		return 0, 0
	}
	for i := 0; i < len(body); i++ {
		switch body[i] {
		case '\\':
			i++
		case '\n', '\r':
			return 0, 0
		case '$':
			if open == 2 {
				if i+1 >= len(body) || body[i+1] != '$' {
					continue
				}
			} else if isSpaceByte(body[i-1]) || i+1 < len(body) && body[i+1] >= '0' && body[i+1] <= '9' {
				continue
			}
			return open + i + open, open
		}
	}
	return 0, 0
}

// InlineMathSpans: where a line's inline math is, [start, end, delimiter
// length], as the parser finds it: not in code, not at an escaped \$.
func InlineMathSpans(line string) [][3]int {
	var spans [][3]int
	code := InlineCodeSpans(line)
	for i := 0; i < len(line); i++ {
		if len(code) > 0 && i >= code[0][0] {
			i = code[0][1] - 1
			code = code[1:]
			continue
		}
		switch line[i] {
		case '\\':
			i++
		case '$':
			if n, open := InlineMath(line[i:]); n > 0 {
				spans = append(spans, [3]int{i, i + n, open})
				i += n - 1
				for len(code) > 0 && code[0][0] < i {
					code = code[1:]
				}
			}
		}
	}
	return spans
}

func isSpaceByte(b byte) bool { return b == ' ' || b == '\t' || b == '\n' || b == '\r' }

// MathFence: a $$ line that opens display math (only $$, up to three
// spaces before it), or one that is display math by itself ($$tex$$);
// the parser and mdtext.FencedLines both go by it.
func MathFence(line string) (opens, whole bool) {
	if strings.HasPrefix(line, "    ") || strings.HasPrefix(strings.TrimLeft(line, " "), "\t") {
		return false, false
	}
	s := strings.TrimSpace(line)
	if s == "$$" {
		return true, false
	}
	return false, len(s) > 4 && strings.HasPrefix(s, "$$") && strings.HasSuffix(s, "$$")
}

// MathFenceEnd: a line that closes display math (it ends in $$).
func MathFenceEnd(line string) bool {
	return strings.HasSuffix(strings.TrimRight(line, " \t\r\n"), "$$")
}
