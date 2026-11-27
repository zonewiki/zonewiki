package mdimport

import (
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/text/unicode/norm"

	"zonewiki/internal/mdtext"
)

// ErrNoNotes: the folder holds no markdown note to import.
var ErrNoNotes = errors.New("the folder has no markdown notes (.md files)")

// skippedDirs: folders an import leaves out, with what is in them: an
// Obsidian vault's settings, its trash, a git repository's insides.
var skippedDirs = map[string]bool{".obsidian": true, ".trash": true, ".git": true}

// note: a markdown file of the folder and the page it becomes.
type note struct {
	src   string // its path in the folder
	slug  string // the page's full slug, under Options.Folder
	title string // its file name without .md: Obsidian's title
	raw   string // its text as read
}

// converter: one Convert's state. Convert runs the notes through it twice:
// first to learn which files the notes link to (collect), so each gets
// its name in the order of the folder's paths, then to write the pages.
type converter struct {
	o        Options
	obsidian bool
	notes    []*note
	bySrc    map[string]*note    // a note by its path
	byPath   map[string]string   // a lowercased path → the path, notes and files alike
	byBase   map[string][]string // a lowercased base name (a note's also without .md) → the paths, sorted
	linked   map[string]bool     // collect: the files a note links to
	names    map[string]string   // a file's path → its name in the uploads ("" for none)
	collect  bool
	report   map[*[]string]map[string]bool
	rep      Report
}

// Convert reads the folder fsys and plans it as pages under o.Folder,
// following the rules in Importing pages in docs/spec/editor.md. It writes
// nothing; the error is fsys's, or ErrNoNotes.
func Convert(fsys fs.FS, o Options) (*Plan, error) {
	if o.Folder == "" {
		return nil, errors.New("mdimport: no folder to import into")
	}
	if o.PagePart == nil {
		o.PagePart = func(string) bool { return true }
	}
	if o.FileName == nil {
		o.FileName = func(want string) string { return want }
	}
	if o.FileURL == nil {
		o.FileURL = url.PathEscape
	}
	c := &converter{o: o, obsidian: o.Obsidian, bySrc: map[string]*note{},
		byPath: map[string]string{}, byBase: map[string][]string{},
		linked: map[string]bool{}, names: map[string]string{}, report: map[*[]string]map[string]bool{}}
	var files []string
	if err := c.walk(fsys, &files); err != nil {
		return nil, err
	}
	if len(c.notes) == 0 {
		return nil, ErrNoNotes
	}
	c.rep.Obsidian = c.obsidian
	sort.Slice(c.notes, func(i, j int) bool { return c.notes[i].src < c.notes[j].src })
	sort.Strings(files)
	for _, ps := range c.byBase {
		sort.Strings(ps)
	}

	c.collect = true
	for _, n := range c.notes {
		c.convertNote(n)
	}
	c.collect = false
	plan := &Plan{}
	for _, f := range files {
		switch {
		case !c.linked[f]:
			c.add(&c.rep.LeftOut, fmt.Sprintf("%s: no note links to it", code(f)))
		default:
			name := c.o.FileName(path.Base(f))
			c.names[f] = name
			if name == "" {
				c.add(&c.rep.LeftOut, fmt.Sprintf("%s: no name a file in the wiki can have", code(f)))
			} else {
				plan.Files = append(plan.Files, File{Src: f, Name: name})
			}
		}
	}
	for _, n := range c.notes {
		plan.Pages = append(plan.Pages, Page{Slug: n.slug, Text: c.convertNote(n)})
	}
	for _, list := range []*[]string{&c.rep.Renamed, &c.rep.Ambiguous, &c.rep.Missing, &c.rep.Lost, &c.rep.LeftOut} {
		sort.Strings(*list)
	}
	plan.Report = c.rep
	plan.Index = Page{Slug: o.Folder + "/index", Text: c.indexText()}
	return plan, nil
}

// add puts a line in a list of the report once (not while collecting).
func (c *converter) add(list *[]string, line string) {
	if c.collect {
		return
	}
	if c.report[list] == nil {
		c.report[list] = map[string]bool{}
	}
	if !c.report[list][line] {
		c.report[list][line] = true
		*list = append(*list, line)
	}
}

// code: s as a markdown code span. Report lines are markdown.
func code(s string) string {
	if strings.Contains(s, "`") {
		return "`` " + s + " ``"
	}
	return "`" + s + "`"
}

// ---- the folder ----

// walk reads the folder: its notes get their slugs, its other files are
// listed (files), what is left out is reported.
func (c *converter) walk(fsys fs.FS, files *[]string) error {
	dirSlug := map[string]string{".": ""}
	taken := map[string]bool{"n\x00\x00index": true} // the folder's index page
	return fs.WalkDir(fsys, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if p == "." {
			return nil
		}
		base := path.Base(p)
		parent := path.Dir(p)
		if d.IsDir() {
			if skippedDirs[base] {
				if base == ".obsidian" {
					c.obsidian = true
				}
				return fs.SkipDir
			}
			part, why := c.part(base, "d\x00"+parent+"\x00", taken)
			dirSlug[p] = joinSlug(dirSlug[parent], part)
			if why != "" {
				c.add(&c.rep.Renamed, fmt.Sprintf("%s → %s (%s)", code(p+"/"), code(c.o.Folder+"/"+dirSlug[p]+"/"), why))
			}
			return nil
		}
		lower := key(base)
		switch {
		case strings.HasPrefix(base, "."):
			return nil // .DS_Store and the like
		case !d.Type().IsRegular():
			c.add(&c.rep.LeftOut, fmt.Sprintf("%s: not a plain file", code(p)))
			return nil
		case strings.HasSuffix(lower, ".excalidraw.md"), strings.HasSuffix(lower, ".excalidraw"):
			c.add(&c.rep.LeftOut, fmt.Sprintf("%s: an Excalidraw drawing", code(p)))
			return nil
		case strings.HasSuffix(lower, ".canvas"):
			c.add(&c.rep.LeftOut, fmt.Sprintf("%s: an Obsidian canvas", code(p)))
			return nil
		case strings.HasSuffix(lower, ".base"):
			c.add(&c.rep.LeftOut, fmt.Sprintf("%s: an Obsidian base", code(p)))
			return nil
		}
		c.byPath[key(p)] = p
		c.byBase[lower] = append(c.byBase[lower], p)
		if !strings.HasSuffix(lower, ".md") {
			*files = append(*files, p)
			return nil
		}
		title := norm.NFC.String(base[:len(base)-len(".md")])
		c.byBase[key(title)] = append(c.byBase[key(title)], p)
		raw, err := fs.ReadFile(fsys, p)
		if err != nil {
			return err
		}
		part, why := c.part(title, "n\x00"+dirSlug[parent]+"\x00", taken)
		n := &note{src: p, slug: c.o.Folder + "/" + joinSlug(dirSlug[parent], part), title: title, raw: string(raw)}
		if why != "" {
			c.add(&c.rep.Renamed, fmt.Sprintf("%s → %s (%s)", code(p), code(n.slug), why))
		}
		c.notes = append(c.notes, n)
		c.bySrc[p] = n
		return nil
	})
}

// key: a path as links find it: case ignored, and composed (NFC), since
// a Mac writes "ü" in a file's name as "u" and a combining mark.
func key(p string) string { return strings.ToLower(norm.NFC.String(p)) }

func joinSlug(dir, part string) string {
	if dir == "" {
		return part
	}
	return dir + "/" + part
}

// part: a file's or folder's name as a part of a slug, and why it isn't
// the name slugified ("" when it is). Notes and folders each have their
// own names in a folder (a page and a folder may share one); space is the
// key of those taken.
func (c *converter) part(name, space string, taken map[string]bool) (string, string) {
	s := strings.TrimLeft(mdtext.ToSlug(norm.NFC.String(name)), "_-")
	why := ""
	if s == "" {
		s, why = "note", "no letter a page's name keeps"
	}
	if !c.o.PagePart(s) {
		s, why = s+"-", "not a name a page may have"
	} else if c.o.NoteRefused != nil && strings.HasPrefix(space, "n\x00") {
		if r := c.o.NoteRefused(s); r != "" {
			s, why = s+"-", r
		}
	}
	if taken[space+s] {
		why = "another has the name"
		if space == "n\x00\x00" && s == "index" {
			why = "the folder's index page has the name"
		}
		base := s
		for i := 2; taken[space+s]; i++ {
			s = base + "-" + strconv.Itoa(i)
		}
	}
	taken[space+s] = true
	return s, why
}

// ---- links: which file a link means ----

// resolve: the path in the folder a link from n means, by the first rule
// that finds one (case ignored, .md optional): the path from the folder's
// root; the path from n's folder (markdown links, and ./ or ../ ones);
// the path's end; the base name. Several found by one rule: the one in
// n's folder, else the shortest path, else the first by name, reported.
func (c *converter) resolve(n *note, target string, md bool, written string) (string, bool) {
	t := strings.TrimSpace(strings.ReplaceAll(norm.NFC.String(target), `\`, "/"))
	if t == "" {
		return "", false
	}
	rooted := strings.HasPrefix(t, "/")
	lt := strings.ToLower(path.Clean(strings.TrimLeft(t, "/")))
	dir := key(path.Dir(n.src))
	exact := func(p string) string {
		if hit, ok := c.byPath[p]; ok {
			return hit
		}
		return c.byPath[p+".md"]
	}
	var relative string
	if !rooted && (md || strings.HasPrefix(t, "./") || strings.HasPrefix(t, "../")) {
		if j := path.Join(dir, lt); j != ".." && !strings.HasPrefix(j, "../") {
			relative = exact(j)
		}
	}
	if hit := exact(lt); hit != "" && !strings.HasPrefix(lt, "../") {
		if relative != "" && relative != hit {
			c.ambiguous(n, written, []string{hit, relative}, hit)
		}
		return hit, true
	}
	if relative != "" {
		return relative, true
	}
	base := path.Base(lt)
	var ends, bases []string
	for _, p := range c.byBase[base] {
		lp := key(p)
		if strings.Contains(lt, "/") && (strings.HasSuffix(lp, "/"+lt) || strings.HasSuffix(lp, "/"+lt+".md")) {
			ends = append(ends, p)
		}
		bases = append(bases, p)
	}
	for _, found := range [][]string{ends, bases} {
		if len(found) > 0 {
			return c.choose(n, written, found), true
		}
	}
	return "", false
}

// choose: one of several paths a link finds (sorted by name), reported
// when there are several.
func (c *converter) choose(n *note, written string, found []string) string {
	if len(found) == 1 {
		return found[0]
	}
	chosen := ""
	for _, p := range found {
		if path.Dir(p) == path.Dir(n.src) {
			chosen = p
			break
		}
	}
	if chosen == "" {
		chosen = found[0]
		for _, p := range found[1:] {
			if len(p) < len(chosen) {
				chosen = p
			}
		}
	}
	c.ambiguous(n, written, found, chosen)
	return chosen
}

func (c *converter) ambiguous(n *note, written string, found []string, chosen string) {
	var names []string
	for _, p := range found {
		names = append(names, code(p))
	}
	c.add(&c.rep.Ambiguous, fmt.Sprintf("%s: %s could be %s; it links to %s", code(n.src), code(written), strings.Join(names, " or "), code(chosen)))
}

// fileURL: the URL a link to the folder's file src takes, false when the
// file has no name in the uploads.
func (c *converter) fileURL(src string) (string, bool) {
	if c.collect {
		c.linked[src] = true
		return "", true
	}
	name := c.names[src]
	if name == "" {
		return "", false
	}
	return c.o.FileURL(name), true
}

// ---- links: what they become ----

var (
	// linkRE: a wikilink or an embed (1, 2), or a markdown link or image
	// (3 the !, 4 its text, 5 its target, 6 a title after it).
	linkRE = regexp.MustCompile(`(!?)\[\[([^\[\]\n]+)\]\]|(!?)\[([^\[\]\n]*)\]\((<[^<>\n]+>|[^()\s]+)([ \t]+"[^"\n]*")?\)`)
	// schemeRE: a link to elsewhere (http:, mailto:, …) or another host.
	schemeRE   = regexp.MustCompile(`^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)`)
	imageExts  = map[string]bool{".png": true, ".jpg": true, ".jpeg": true, ".gif": true, ".svg": true, ".webp": true, ".bmp": true, ".avif": true}
	embedWidth = regexp.MustCompile(`^(?:(.*)\|)?\s*(\d+)(?:x\d+)?\s*$`)
)

func isImage(p string) bool { return imageExts[strings.ToLower(path.Ext(p))] }

// links rewrites every link in text (code masked) to its new target.
func (c *converter) links(n *note, text string) string {
	return linkRE.ReplaceAllStringFunc(text, func(m string) string {
		g := linkRE.FindStringSubmatch(m)
		if g[2] != "" {
			return c.wikilink(n, m, g[1] == "!", g[2])
		}
		return c.mdLink(n, m, g[3] == "!", g[4], g[5], g[6])
	})
}

// wikilink: [[Note]], [[Note#Section|alias]], and in a vault ![[embed]].
func (c *converter) wikilink(n *note, m string, embed bool, inner string) string {
	if embed && !c.obsidian {
		return m
	}
	inTable := strings.Contains(inner, `\|`) // a wikilink in a table's row
	target, alias, hasAlias := strings.Cut(strings.ReplaceAll(inner, `\|`, "|"), "|")
	name, section, _ := strings.Cut(target, "#")
	name, section, alias = strings.TrimSpace(name), strings.TrimSpace(section), strings.TrimSpace(alias)
	if name == "" { // [[#Section]]: this note's own
		if strings.HasPrefix(section, "^") {
			c.add(&c.rep.Lost, fmt.Sprintf("%s: %s, a block reference, is a link to nothing", code(n.src), code(m)))
		}
		return m
	}
	src, ok := c.resolve(n, name, false, m)
	if !ok {
		c.add(&c.rep.Missing, fmt.Sprintf("%s: %s finds no note or file", code(n.src), code(m)))
		return m
	}
	out := ""
	if to := c.bySrc[src]; to != nil {
		if i := strings.LastIndex(section, "#"); i >= 0 { // Obsidian's [[Note#A#B]]: B under A
			section = strings.TrimSpace(section[i+1:])
		}
		if strings.HasPrefix(section, "^") {
			c.add(&c.rep.Lost, fmt.Sprintf("%s: %s, a block reference, links to the page", code(n.src), code(m)))
			section = ""
		}
		if embed {
			c.add(&c.rep.Lost, fmt.Sprintf("%s: %s, an embedded note, is a link", code(n.src), code(m)))
		}
		label := name
		if hasAlias && alias != "" {
			label = alias
		} else if section != "" {
			label = name + " › " + section
		}
		slug := to.slug
		if section != "" {
			slug += "#" + section
		}
		out = mdtext.PageWikilink(slug, label)
	} else {
		u, ok := c.fileURL(src)
		if !ok {
			return m
		}
		switch {
		case embed && isImage(src):
			alt := alias
			if w := embedWidth.FindStringSubmatch(alias); w != nil {
				alt = strings.TrimSpace(w[1]) + "|" + w[2]
			}
			out = "![" + alt + "](" + u + ")"
		default:
			label := name
			if hasAlias && alias != "" {
				label = alias
			}
			out = "[" + label + "](" + u + ")"
		}
	}
	if inTable {
		out = strings.ReplaceAll(out, "|", `\|`)
	}
	return out
}

// mdLink: [text](target "title") and ![alt](target) to a note or a file
// of the folder; links elsewhere and to an anchor stay.
func (c *converter) mdLink(n *note, m string, image bool, text, dest, title string) string {
	d := strings.TrimSuffix(strings.TrimPrefix(dest, "<"), ">")
	if d == "" || strings.HasPrefix(d, "#") || schemeRE.MatchString(d) {
		return m
	}
	p, frag, _ := strings.Cut(d, "#")
	if u, err := url.PathUnescape(p); err == nil {
		p = u
	}
	if u, err := url.PathUnescape(frag); err == nil {
		frag = u
	}
	src, ok := c.resolve(n, p, true, m)
	if !ok {
		c.add(&c.rep.Missing, fmt.Sprintf("%s: %s finds no note or file", code(n.src), code(m)))
		return m
	}
	if to := c.bySrc[src]; to != nil {
		if strings.HasPrefix(frag, "^") {
			c.add(&c.rep.Lost, fmt.Sprintf("%s: %s, a block reference, links to the page", code(n.src), code(m)))
			frag = ""
		}
		if image {
			c.add(&c.rep.Lost, fmt.Sprintf("%s: %s, an embedded note, is a link", code(n.src), code(m)))
		}
		slug := to.slug
		if frag != "" {
			slug += "#" + frag
		}
		label := text
		if strings.TrimSpace(label) == "" {
			label = to.title
		}
		return mdtext.PageWikilink(slug, label)
	}
	u, ok := c.fileURL(src)
	if !ok {
		return m
	}
	if frag != "" {
		u += "#" + url.PathEscape(frag)
	}
	bang := ""
	if image {
		bang = "!"
	}
	return bang + "[" + text + "](" + u + title + ")"
}

// ---- a note ----

// convertNote: n's text as its page holds it.
func (c *converter) convertNote(n *note) string {
	masked, stash := mdtext.MaskCode(n.raw)
	lines := mdtext.SplitLines(masked)
	end := frontmatterEnd(lines)
	front, body := strings.Join(lines[:end+1], ""), strings.Join(lines[end+1:], "")
	var tags []string
	if c.obsidian {
		body = commentRE.ReplaceAllString(commentLinesRE.ReplaceAllString(body, ""), "")
		tags = inlineTags(body)
		body = callouts(body)
		body = blockIDs(body)
		body = highlightRE.ReplaceAllString(body, "<mark>$1</mark>")
		c.lost(n, body)
	}
	text := mdtext.UnmaskCode(c.links(n, front)+c.links(n, body), stash)
	if !hasH1(text) {
		text = withTitle(text, n.title)
	}
	if len(tags) > 0 {
		text = mdtext.AddFrontmatterTags(text, tags)
	}
	return text
}

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

var h1RE = regexp.MustCompile(`^#[ \t]+\S`)

// hasH1: text has a "# " heading outside its frontmatter and code, which
// the wiki takes for the page's title.
func hasH1(text string) bool {
	lines := mdtext.SplitLines(text)
	start := frontmatterEnd(lines) + 1
	for i, l := range mdtext.FencedLines(lines) {
		if i >= start && !l.InFence && h1RE.MatchString(mdtext.LineBody(l.Line)) {
			return true
		}
	}
	return false
}

// withTitle: text with "# title" on top, after its frontmatter.
func withTitle(text, title string) string {
	eol := "\n"
	if strings.Contains(text, "\r\n") {
		eol = "\r\n"
	}
	lines := mdtext.SplitLines(text)
	end := frontmatterEnd(lines)
	front, rest := strings.Join(lines[:end+1], ""), strings.Join(lines[end+1:], "")
	if front != "" && !strings.HasSuffix(front, "\n") && !strings.HasSuffix(front, "\r") {
		front += eol
	}
	head := "# " + title + eol
	if rest != "" && strings.TrimSpace(mdtext.LineBody(mdtext.SplitLines(rest)[0])) != "" {
		head += eol
	}
	return front + head + rest
}

// ---- Obsidian's own syntax ----

var (
	// commentLinesRE: a comment on lines of its own, which go with it;
	// commentRE: one in a line, with a space before it.
	commentLinesRE = regexp.MustCompile(`(?m)^[ \t]*%%(?s:.*?)%%[ \t]*(?:\r\n|\n|\r|\z)`)
	commentRE      = regexp.MustCompile(`(?s)[ \t]?%%.*?%%`)
	highlightRE    = regexp.MustCompile(`==([^\s=](?:[^\n]*?[^\s=])?)==`)
	// tagRE: #tag after a space or a line's start, as Obsidian has it.
	tagRE       = regexp.MustCompile(`(?:^|\s)#([\p{L}\p{N}_/\-]+)`)
	digitsRE    = regexp.MustCompile(`^[\d/]*$`)
	calloutRE   = regexp.MustCompile(`^((?:[ \t]*>)+[ \t]?)\[!([A-Za-z0-9_-]+)\]([+-]?)[ \t]*(.*)$`)
	blockIDRE   = regexp.MustCompile(`^(.*\S)[ \t]+\^[A-Za-z0-9-]+[ \t]*$`)
	blockLineRE = regexp.MustCompile(`^[ \t]*\^[A-Za-z0-9-]+[ \t]*$`)
)

// inlineTags: the #tags in text, each once (case ignored), as written.
func inlineTags(text string) []string {
	var tags []string
	seen := map[string]bool{}
	for _, m := range tagRE.FindAllStringSubmatch(text, -1) {
		tag := strings.Trim(m[1], "/")
		if digitsRE.MatchString(tag) || seen[strings.ToLower(tag)] {
			continue
		}
		seen[strings.ToLower(tag)] = true
		tags = append(tags, tag)
	}
	return tags
}

// calloutKinds: Obsidian's callouts on Zonewiki's five kinds.
var calloutKinds = map[string]string{
	"note": "NOTE", "abstract": "NOTE", "summary": "NOTE", "tldr": "NOTE", "info": "NOTE", "todo": "NOTE",
	"question": "NOTE", "help": "NOTE", "faq": "NOTE", "example": "NOTE", "quote": "NOTE", "cite": "NOTE",
	"tip": "TIP", "hint": "TIP", "success": "TIP", "check": "TIP", "done": "TIP",
	"important": "IMPORTANT",
	"warning":   "WARNING", "attention": "WARNING",
	"caution": "CAUTION", "failure": "CAUTION", "fail": "CAUTION", "missing": "CAUTION",
	"danger": "CAUTION", "error": "CAUTION", "bug": "CAUTION",
}

// callouts: Obsidian's callout lines as Zonewiki reads them: the kind one
// of its five, a title a bold line of its own (the kind's name when it
// isn't one of the five), the fold sign dropped.
func callouts(text string) string {
	var out strings.Builder
	for _, line := range mdtext.SplitLines(text) {
		body := mdtext.LineBody(line)
		g := calloutRE.FindStringSubmatch(body)
		if g == nil {
			out.WriteString(line)
			continue
		}
		eol := line[len(body):]
		kind, title := strings.ToLower(g[2]), strings.TrimSpace(g[4])
		zone := calloutKinds[kind]
		if zone == "" {
			zone = "NOTE"
		}
		if strings.ToLower(zone) != kind && title == "" {
			title = mdtext.TitleWords(kind)
		}
		if title == "" && g[3] == "" {
			out.WriteString(line)
			continue
		}
		nl := eol
		if nl == "" {
			nl = "\n"
		}
		parts := []string{g[1] + "[!" + zone + "]"}
		if title != "" {
			parts = append(parts, g[1]+"**"+title+"**", strings.TrimRight(g[1], " \t"))
		}
		out.WriteString(strings.Join(parts, nl) + eol)
	}
	return out.String()
}

// blockIDs: text without Obsidian's block ids (" ^id" at a line's end, or
// "^id" on a line of its own), which the wiki would show.
func blockIDs(text string) string {
	var out strings.Builder
	for _, line := range mdtext.SplitLines(text) {
		body := mdtext.LineBody(line)
		switch {
		case blockLineRE.MatchString(body):
			out.WriteString(line[len(body):])
		case blockIDRE.MatchString(body):
			out.WriteString(blockIDRE.ReplaceAllString(body, "$1") + line[len(body):])
		default:
			out.WriteString(line)
		}
	}
	return out.String()
}

var (
	dataviewBlockRE = regexp.MustCompile("(?m)^[ \t]*(?:`{3,}|~{3,})[ \t]*dataview(?:js)?\\b")
	inlineFieldRE   = regexp.MustCompile(`(?m)^[ \t]*(?:[-*+][ \t]+(?:\[.\][ \t]+)?)?[\p{L}\p{N}_][\p{L}\p{N}_ -]*::(?:[ \t]|$)|[\[(][\p{L}\p{N}_][\p{L}\p{N}_ -]*::[^\])\n]*[\])]`)
	tasksPluginRE   = regexp.MustCompile(`(?m)^[ \t]*(?:>[ \t]*)*[-*+][ \t]+\[.\][^\n]*(?:📅|⏳|🛫|✅|❌|🔁|⏫|🔼|🔽|⏬|➕|🆔|⛔)`)
)

// lost: Dataview's and the Tasks plugin's syntax in n (body: its text,
// code masked), which stays as written, counted in the report.
func (c *converter) lost(n *note, body string) {
	var what []string
	count := func(k int, one, many string) {
		if k == 1 {
			what = append(what, "1 "+one)
		} else if k > 1 {
			what = append(what, strconv.Itoa(k)+" "+many)
		}
	}
	count(len(dataviewBlockRE.FindAllString(n.raw, -1)), "Dataview query", "Dataview queries")
	count(len(inlineFieldRE.FindAllString(body, -1)), "Dataview field (key:: value)", "Dataview fields (key:: value)")
	count(len(tasksPluginRE.FindAllString(body, -1)), "Tasks-plugin task", "Tasks-plugin tasks")
	if len(what) > 0 {
		c.add(&c.rep.Lost, fmt.Sprintf("%s: %s, as written", code(n.src), strings.Join(what, ", ")))
	}
}

// ---- the index page ----

// indexText: the folder's index page: a title, a list of every page
// imported, the report.
func (c *converter) indexText() string {
	name := c.o.Name
	if name == "" {
		name = mdtext.SlugDisplayName(c.o.Folder)
	}
	var b strings.Builder
	b.WriteString("# " + name + "\n\n")
	kind := "a folder of markdown notes"
	if c.obsidian {
		kind = "an Obsidian vault"
	}
	pages := "pages"
	if len(c.notes) == 1 {
		pages = "page"
	}
	fmt.Fprintf(&b, "Imported from %s: %d %s.\n\n## Pages\n\n", kind, len(c.notes), pages)
	srcs := make([]string, 0, len(c.notes))
	for _, n := range c.notes {
		srcs = append(srcs, n.src)
	}
	sort.Strings(srcs)
	for _, s := range srcs {
		n := c.bySrc[s]
		b.WriteString("- " + mdtext.PageWikilink(n.slug, strings.TrimSuffix(n.src, path.Ext(n.src))) + "\n")
	}
	for _, sec := range []struct {
		title string
		lines []string
	}{
		{"Renamed", c.rep.Renamed},
		{"Links with several targets", c.rep.Ambiguous},
		{"Links to nothing", c.rep.Missing},
		{"Lost on the way", c.rep.Lost},
		{"Left out", c.rep.LeftOut},
	} {
		if len(sec.lines) == 0 {
			continue
		}
		b.WriteString("\n## " + sec.title + "\n\n")
		for _, l := range sec.lines {
			b.WriteString("- " + l + "\n")
		}
	}
	return b.String()
}
