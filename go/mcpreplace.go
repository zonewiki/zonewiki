// The MCP tool replace_in_pages: a regex replacement over pages, with
// Python's template syntax.
package main

import (
	"errors"
	"fmt"
	"maps"
	"regexp"
	"regexp/syntax"
	"strconv"
	"strings"
	"unicode"
	"zonewiki/internal/mdtext"
)

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
	for i, l := range mdtext.FencedLines(mdtext.SplitLines(content)) {
		body := strings.TrimRight(l.Line, "\r\n")
		if l.InFence && !includeCode {
			out.WriteString(l.Line)
			continue
		}
		var code [][2]int
		if !includeCode {
			code = mdtext.InlineCodeSpans(body)
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
		out.WriteString(b.String() + l.Line[len(body):])
	}
	return out.String(), changed, nil
}

// templatePart is a piece of a replacement: text, or a group (group ≥ 0).
type templatePart struct {
	text  string
	group int
}

var templateEscapes = map[byte]string{'a': "\a", 'b': "\b", 'f': "\f", 'n': "\n", 'r': "\r", 't': "\t", 'v': "\v", '\\': `\`}

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
				return nil, fmt.Errorf("unknown group name %s", strconv.Quote(name))
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
		case templateEscapes[c] != "":
			text(templateEscapes[c])
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

// replaceSpec: replace_in_pages' arguments, read.
type replaceSpec struct {
	find, replace        string
	isRegex, includeCode bool
	dryRun               bool // the default: nothing written
	under                string
	maxPages, limit      int
	re                   *regexp.Regexp
}

// replaceArgs reads replace_in_pages' arguments, or says what is wrong
// with them.
func replaceArgs(args jsonObj, user string) (*replaceSpec, error) {
	spec := &replaceSpec{dryRun: true}
	var err error
	if spec.find, err = strArg(args, "find", true); err != nil {
		return nil, err
	}
	if spec.replace, err = strArg(args, "replace", false); err != nil {
		return nil, err
	}
	if strings.Contains(spec.find, "\n") {
		return nil, toolErr("find is matched within a line, so it can't contain a line break. " +
			"For a passage spanning lines, use edit_page on each page.")
	}
	if spec.isRegex, err = boolArg(args, "regex"); err != nil {
		return nil, err
	}
	if spec.includeCode, err = boolArg(args, "include_code"); err != nil {
		return nil, err
	}
	if args.has("dry_run") {
		var ok bool
		if spec.dryRun, ok = as[bool](args["dry_run"]); !ok {
			return nil, toolErr("dry_run must be true or false")
		}
	}
	pattern := regexp.QuoteMeta(spec.find)
	if spec.isRegex {
		pattern = pyPattern(spec.find)
	}
	if _, err := regexp.Compile(pattern); err != nil {
		msg := "find is not a valid regex: " + strings.TrimPrefix(err.Error(), "error parsing regexp: ")
		var perr *syntax.Error
		if errors.As(err, &perr) && (perr.Code == syntax.ErrInvalidPerlOp || perr.Code == syntax.ErrInvalidEscape) {
			msg += " (no lookarounds or backreferences here: match more and keep it with a group, \\1)"
		}
		return nil, toolErr(msg)
	}
	if spec.under, err = underArg(args); err != nil {
		return nil, err
	}
	if spec.under != "" && !anyPageIn(spec.under, user) {
		return nil, toolErr("No pages under " + strconv.Quote(spec.under) + ". list_pages shows the slugs.")
	}
	if spec.maxPages, err = mcpInt(args, "max_pages", 50, 1000); err != nil {
		return nil, err
	}
	if spec.limit, err = mcpInt(args, "limit", 50, 500); err != nil {
		return nil, err
	}
	ignoreCase, err := boolArg(args, "ignore_case")
	if err != nil {
		return nil, err
	}
	if ignoreCase {
		pattern = "(?i)" + pattern
	}
	spec.re = regexp.MustCompile(pattern)
	return spec, nil
}

// hits: the pages under spec.under that user may write and the replacement
// changes, each with its new text, and how many occurrences in all.
func (spec *replaceSpec) hits(user string) ([]replaceHit, int, error) {
	var parts []templatePart // parsed at the first match, as Python does
	parsed := false
	repl := func(body string, m []int) (string, error) {
		if !spec.isRegex {
			return spec.replace, nil
		}
		if !parsed {
			var err error
			if parts, err = parseTemplate(spec.replace, spec.re); err != nil {
				return "", toolErr("replace: " + err.Error() + ` (groups are \1 or \g<name>; a literal backslash is \\)`)
			}
			parsed = true
		}
		return expand(parts, body, m), nil
	}
	var hits []replaceHit
	total := 0
	for _, p := range sortedPages() {
		if !inFolder(p.Slug, spec.under) || !canWrite(user, p.Slug) {
			continue
		}
		content, err := readText(p.Path)
		if err != nil {
			return nil, 0, err
		}
		text, changed, err := replaceLines(content, spec.re, repl, spec.includeCode)
		if err != nil {
			return nil, 0, err
		}
		if len(changed) > 0 {
			hits = append(hits, replaceHit{p.Path, p.Slug, content, text, changed})
			for _, c := range changed {
				total += c.matches
			}
		}
	}
	return hits, total, nil
}

// dryRunReport: what a dry run answers: the changed lines, at most
// spec.limit of them. what: "<counted><where><code>", as the write says it.
func (spec *replaceSpec) dryRunReport(hits []replaceHit, what string) string {
	var out []string
	shown, lines := 0, 0
	for _, h := range hits {
		lines += len(h.changed)
	}
	for _, h := range hits {
		if shown >= spec.limit {
			break
		}
		out = append(out, "", h.slug)
		for _, c := range h.changed[:min(len(h.changed), spec.limit-shown)] {
			out = append(out, fmt.Sprintf("  %d: - %s", c.number, cutLine(c.old)), fmt.Sprintf("  %d: + %s", c.number, cutLine(c.new)))
			shown++
		}
	}
	head := "Dry run, nothing written: would replace " + what
	if shown < lines {
		head += fmt.Sprintf("; the first %d of %d changed lines shown (raise limit to see more)", shown, lines)
	}
	head += ". Call again with dry_run=false to write them, in one commit."
	if len(hits) > spec.maxPages {
		head += fmt.Sprintf(" That is more pages than max_pages (%d): raise it, or narrow find or under.", spec.maxPages)
	}
	return head + "\n" + strings.Join(out, "\n")
}

func replaceInPages(args jsonObj, base, user string) (string, error) {
	spec, err := replaceArgs(args, user)
	if err != nil {
		return "", err
	}
	hits, total, err := spec.hits(user)
	if err != nil {
		return "", err
	}
	where, code := underWhere(spec.under), ""
	if !spec.includeCode {
		code = " outside code"
	}
	if len(hits) == 0 {
		return "Nothing" + where + " matches " + strconv.Quote(spec.find) + code + ".", nil
	}
	counted := fmt.Sprintf("%d %s on %d %s", total, plural(total, "occurrence"), len(hits), plural(len(hits), "page"))
	if spec.dryRun {
		return spec.dryRunReport(hits, counted+where+code), nil
	}
	if len(hits) > spec.maxPages {
		return "", toolErr(fmt.Sprintf("%s would change %d pages, more than max_pages (%d); nothing was "+
			"written. Check with dry_run, then raise max_pages, or narrow find or under.", strconv.Quote(spec.find), len(hits), spec.maxPages))
	}
	var slugs, paths []string
	for _, h := range hits {
		slugs = append(slugs, h.slug)
		paths = append(paths, h.path)
	}
	if err := checkGuides(args, slugs, user); err != nil {
		return "", err
	}
	on := hits[0].slug
	if len(hits) > 1 {
		on = fmt.Sprintf("%d pages", len(hits))
	}
	msg, err := commitMsg("Replace "+strconv.Quote(cutRunes(spec.find, 60, "…"))+" on "+on, args)
	if err != nil {
		return "", err
	}
	what := fmt.Sprintf("%d pages", len(hits))
	keptPages, err := keepPendingPages(paths, what)
	if err != nil {
		return "", err
	}
	pending := pendingPagesNote(keptPages, what)
	for _, h := range hits {
		if err := writeText(h.path, h.new); err != nil {
			return "", err
		}
	}
	if err := notCommitted(gitCommit(user, msg, paths...)); err != nil {
		return "", err
	}
	all := slugSet()
	brokenSet := map[string]bool{}
	for _, h := range hits {
		for _, l := range newMissing(h.slug, h.old, h.new, user, all) {
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
func closeInContent(slug, content string, named map[string]bool, user string) string {
	if semantic == nil {
		return ""
	}
	known := visibleSlugs(user)
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
