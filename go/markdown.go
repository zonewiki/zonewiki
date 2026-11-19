// Markdown to HTML on goldmark: goldmark's own output where Zone doesn't care
// (footnotes, deflists, entities, URL escaping), Zone's rules where a
// feature depends on them: checkboxes the page can tick (counted as mdtext
// counts them), callouts, heading ids that [[Page#Section]] points at, ✎
// and ☑ N per section, image width and img-block, external links in a new
// tab, task-date chips, math (math.go). docs/spec/rendering.md has the
// differences.
package main

import (
	"bytes"
	"fmt"
	gohtml "html"
	"regexp"
	"slices"
	"strings"
	"sync"
	"zonewiki/internal/mdtext"

	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/extension"
	"github.com/yuin/goldmark/parser"
	"github.com/yuin/goldmark/renderer"
	"github.com/yuin/goldmark/renderer/html"
	"github.com/yuin/goldmark/text"
	"github.com/yuin/goldmark/util"
)

// Env is what a render needs besides the text: the page being viewed
// (EditSlug: ✎ per section), the ☑ N chips by heading id, and the
// request's Host (links to it are not external).
type Env struct {
	EditSlug     string
	SectionKeys  []string
	SectionChips map[string]string
	Host         string
	HeadingIDs   headingIDs // what a heading's id becomes
	// Wikilink: a [[link]]'s HTML around its label, inner what is between
	// the brackets; nil: wikilinks stay text.
	Wikilink func(inner string) (open, label, close string)
	// LineBreaks: a line's end inside a paragraph is a line break (a task's
	// comment); without it lines in a row join, as on a page.
	LineBreaks bool
}

// headingIDs: how headings carry their ids: as id (a page), as data-pid
// (the search modal's preview, over a page with its own ids), or not at
// all (task notes and comments, inside a page).
type headingIDs int

const (
	headingAsID headingIDs = iota
	headingPID
	headingNoID
)

// typographer: curly quotes, dashes and the ellipsis; not « » for << >>.
var typographer = extension.NewTypographer(extension.WithTypographicSubstitutions(
	extension.TypographicSubstitutions{extension.LeftAngleQuote: []byte("&lt;&lt;"), extension.RightAngleQuote: []byte("&gt;&gt;")}))

// markdown renders src; inline renders it as one paragraph's inline
// content (no headings, lists, quotes …).
func markdown(src string, env *Env, inline bool) string {
	pool := &blockRenderers
	if inline {
		pool = &inlineRenderers
	}
	m := pool.Get().(*mdRenderer)
	defer pool.Put(m)
	m.renderer.sections = 0
	m.renderer.env, m.transformer.env = env, env
	var buf bytes.Buffer
	if err := m.md.Convert([]byte(encodeUploadLinks(src)), &buf); err != nil {
		logf("markdown: %v", err)
	}
	m.renderer.env, m.transformer.env = nil, nil
	// Escaped text never holds a "<!--", so only raw HTML's comments match.
	out := taskDates(buf.String())
	if inline {
		out = strings.TrimSuffix(strings.TrimPrefix(strings.TrimSpace(out), "<p>"), "</p>")
	}
	return out
}

// mdRenderer: a goldmark set up with Zone's rules, built once and reused
// (a sync.Pool each for block and inline rendering): its transformer and
// renderer are bound to one render's Env at a time, and the renderer
// counts the sections it has numbered, so a render has one to itself.
type mdRenderer struct {
	md          goldmark.Markdown
	transformer *zoneTransformer
	renderer    *zoneRenderer
}

var (
	blockRenderers  = sync.Pool{New: func() any { return newMDRenderer(false) }}
	inlineRenderers = sync.Pool{New: func() any { return newMDRenderer(true) }}
)

func newMDRenderer(inline bool) *mdRenderer {
	m := &mdRenderer{transformer: &zoneTransformer{}, renderer: &zoneRenderer{}}
	opts := []goldmark.Option{
		goldmark.WithExtensions(extension.Table, extension.Strikethrough,
			extension.NewLinkify(extension.WithLinkifyURLRegexp(urlRE)),
			extension.Footnote, extension.DefinitionList, typographer, mathExtension{block: !inline}, wikilinkExtension{}),
		goldmark.WithParserOptions(
			parser.WithParagraphTransformers(
				util.Prioritized(alertTransformer{}, 100),
				util.Prioritized(taskTransformer{}, 200),
			),
			parser.WithASTTransformers(util.Prioritized(m.transformer, 1000)),
		),
		goldmark.WithRendererOptions(
			html.WithUnsafe(), html.WithXHTML(),
			renderer.WithNodeRenderers(util.Prioritized(m.renderer, 100)),
		),
	}
	if inline {
		opts = append([]goldmark.Option{goldmark.WithParser(parser.NewParser(
			parser.WithBlockParsers(util.Prioritized(parser.NewParagraphParser(), 1000)),
			parser.WithInlineParsers(parser.DefaultInlineParsers()...),
			parser.WithParagraphTransformers(parser.DefaultParagraphTransformers()...),
		))}, opts...)
	}
	m.md = goldmark.New(opts...)
	return m
}

// urlRE: goldmark's linkify pattern, but the host needs no dot, so a bare
// http://localhost:5000 or http://nas:5000 in a self-hosted wiki is a link.
var urlRE = regexp.MustCompile(`^(?:http|https|ftp)://[-a-zA-Z0-9@:%._\+~#=]{1,256}(?:\.[a-z]+)?(?::\d+)?(?:[/#?][-a-zA-Z0-9@:%_+.~#$!?&/=\(\);,'">\^{}\[\]` + "`" + `]*)?`)

// alertTransformer: a blockquote whose first line is [!NOTE], [!TIP],
// [!IMPORTANT], [!WARNING] or [!CAUTION], alone on it, is a callout; the
// marker line goes. It looks at the line before inlines are parsed.
type alertTransformer struct{}

var alertTypes = map[string]bool{"NOTE": true, "TIP": true, "IMPORTANT": true, "WARNING": true, "CAUTION": true}

func (alertTransformer) Transform(p *ast.Paragraph, reader text.Reader, pc parser.Context) {
	bq, ok := p.Parent().(*ast.Blockquote)
	if !ok || bq.FirstChild() != p {
		return
	}
	first := p.Lines().At(0)
	line := strings.TrimSpace(string(first.Value(reader.Source())))
	if !strings.HasPrefix(line, "[!") || !strings.HasSuffix(line, "]") {
		return
	}
	kind := strings.ToUpper(line[2 : len(line)-1])
	if !alertTypes[kind] {
		return
	}
	bq.SetAttributeString("zone-alert", kind)
	rest := text.NewSegments()
	for i := 1; i < p.Lines().Len(); i++ {
		rest.Append(p.Lines().At(i))
	}
	if rest.Len() == 0 {
		bq.RemoveChild(bq, p)
		return
	}
	p.SetLines(rest)
}

// taskTransformer: a list item whose first paragraph starts "[ ]", "[x]" or
// "[X]" and then whitespace and text gets a checkbox. Exactly the lines
// mdtext.TaskItemLines counts: ticking the Nth checkbox toggles the Nth
// match in the file. (goldmark's tasklist also takes "- [ ]" alone.)
type taskTransformer struct{}

func (taskTransformer) Transform(p *ast.Paragraph, reader text.Reader, pc parser.Context) {
	li, ok := p.Parent().(*ast.ListItem)
	if !ok || li.FirstChild() != p {
		return
	}
	first := p.Lines().At(0)
	v := first.Value(reader.Source())
	if len(v) < 4 || v[0] != '[' || v[2] != ']' || !strings.ContainsRune(" xX", rune(v[1])) ||
		v[3] != ' ' && v[3] != '\t' || len(bytes.TrimSpace(v[3:])) == 0 {
		return
	}
	// On the item: a tight list swaps the paragraph for a new TextBlock.
	li.SetAttributeString("zone-task", v[1] != ' ')
	p.Lines().Set(0, first.WithStart(first.Start+3))
}

var kindCheckbox = ast.NewNodeKind("ZoneCheckbox")

type checkbox struct {
	ast.BaseInline
	Checked bool
}

func (n *checkbox) Kind() ast.NodeKind         { return kindCheckbox }
func (n *checkbox) Dump(src []byte, level int) { ast.DumpHelper(n, src, level, nil, nil) }

// zoneTransformer, after parsing: checkboxes, heading ids, images, links.
type zoneTransformer struct{ env *Env }

var imgWidthRE = regexp.MustCompile(`\|(\d+)$`)

func (t *zoneTransformer) Transform(doc *ast.Document, reader text.Reader, pc parser.Context) {
	src := reader.Source()
	slugs := map[string]bool{}
	var unsafe []ast.Node
	var blocked []*ast.Image
	defer func() {
		// An image the page may not load (blockedImage) is a link to it.
		for _, img := range blocked {
			link := ast.NewLink()
			link.Destination = img.Destination
			link.Title = []byte("An image on another site: not loaded (the external-content setting)")
			link.SetAttributeString("class", "external-image")
			link.SetAttributeString("target", "_blank")
			link.SetAttributeString("rel", "noopener noreferrer")
			for c := img.FirstChild(); c != nil; {
				next := c.NextSibling()
				link.AppendChild(link, c)
				c = next
			}
			if link.ChildCount() == 0 {
				link.AppendChild(link, ast.NewString(util.EscapeHTML(img.Destination)))
			}
			parent := img.Parent()
			parent.ReplaceChild(parent, img, link)
		}
	}()
	defer func() {
		// Such a link is its text.
		for _, n := range unsafe {
			parent := n.Parent()
			for c := n.FirstChild(); c != nil; {
				next := c.NextSibling()
				parent.InsertBefore(parent, n, c)
				c = next
			}
			parent.RemoveChild(parent, n)
		}
	}()
	ast.Walk(doc, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if !entering {
			return ast.WalkContinue, nil
		}
		switch n := n.(type) {
		case *ast.ListItem:
			if checked, ok := n.AttributeString("zone-task"); ok {
				n.RemoveAttributes()
				n.SetAttributeString("class", "task-list-item")
				p := n.FirstChild()
				p.InsertBefore(p, p.FirstChild(), &checkbox{Checked: checked.(bool)})
			}
		case *ast.Paragraph:
			// An image alone in its paragraph may grow beyond the text width.
			if img, ok := n.FirstChild().(*ast.Image); ok && n.ChildCount() == 1 && !t.blockedImage(img) {
				n.SetAttributeString("class", "img-block")
			}
		case *ast.Image:
			if t.blockedImage(n) {
				blocked = append(blocked, n)
			}
			// ![alt|300](url): 300px wide, never wider than the text.
			if last, ok := n.LastChild().(*ast.Text); ok {
				if m := imgWidthRE.FindSubmatchIndex(last.Segment.Value(src)); m != nil {
					width := string(last.Segment.Value(src)[m[2]:m[3]])
					last.Segment = last.Segment.WithStop(last.Segment.Start + m[0])
					n.SetAttributeString("style", "max-width:100%;width:"+width+"px")
				}
			}
		case *ast.Link:
			if unsafeLink(n.Destination) {
				unsafe = append(unsafe, n)
			}
			t.external(n, n.Destination)
		case *ast.AutoLink:
			t.external(n, n.URL(src))
		case *ast.Heading:
			if n.Level <= 3 {
				n.SetAttributeString("id", uniqueSlug(mdtext.HeadingID(headingTitle(n, src)), slugs))
			}
		case *ast.Text:
			if t.env.LineBreaks && n.SoftLineBreak() {
				n.SetHardLineBreak(true)
			}
		}
		return ast.WalkContinue, nil
	})
}

// unsafeLink: a URL Zone refuses to link (javascript:, vbscript:,
// data: but an image's). The page policy wouldn't run a script anyway.
func unsafeLink(href []byte) bool {
	h := strings.ToLower(strings.TrimSpace(string(href)))
	switch {
	case strings.HasPrefix(h, "javascript:"), strings.HasPrefix(h, "vbscript:"):
		return true
	case strings.HasPrefix(h, "data:"):
		for _, ok := range []string{"data:image/gif;", "data:image/png;", "data:image/jpeg;", "data:image/webp;"} {
			if strings.HasPrefix(h, ok) {
				return false
			}
		}
		return true
	}
	return false
}

// blockedImage: an image on another site the page policy won't load
// (pageCSP: only Zone's own, and the external-content setting's hosts). It
// is shown as a link to it, not as a broken image.
func (t *zoneTransformer) blockedImage(n *ast.Image) bool {
	host, ok := urlHost(string(n.Destination))
	return ok && !(t.env.Host != "" && strings.EqualFold(host, t.env.Host)) && !settings().loadsFrom(host)
}

// urlHost: the host of an http(s) or scheme-less ("//host/…") URL; false
// for any other (a path of Zone's own, data:).
func urlHost(href string) (string, bool) {
	rest, ok := strings.CutPrefix(href, "//")
	if !ok {
		scheme, after, _ := strings.Cut(href, ":")
		if !strings.EqualFold(scheme, "http") && !strings.EqualFold(scheme, "https") {
			return "", false
		}
		rest = strings.TrimLeft(after, "/")
	}
	host := rest
	for _, sep := range []string{"/", "?", "#", "\\"} {
		host, _, _ = strings.Cut(host, sep)
	}
	if i := strings.LastIndex(host, "@"); i >= 0 {
		host = host[i+1:]
	}
	return host, host != ""
}

// external: an http(s) link to another site opens in a new tab; one to the
// host the request came in on (Zone itself) does not. Not url.Parse: it
// refuses some URLs a browser follows (a ` in the host, say).
func (t *zoneTransformer) external(n ast.Node, href []byte) {
	scheme, rest, _ := strings.Cut(string(href), ":")
	if !strings.EqualFold(scheme, "http") && !strings.EqualFold(scheme, "https") {
		return
	}
	host, _, _ := strings.Cut(strings.TrimPrefix(rest, "//"), "/")
	host, _, _ = strings.Cut(host, "?")
	host, _, _ = strings.Cut(host, "#")
	if t.env.Host != "" && strings.EqualFold(host, t.env.Host) {
		return
	}
	n.SetAttributeString("target", "_blank")
	n.SetAttributeString("rel", "noopener noreferrer")
}

// headingTitle: the heading's text, its code spans' too, not an image's alt.
func headingTitle(h ast.Node, src []byte) string {
	var b strings.Builder
	ast.Walk(h, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if !entering {
			return ast.WalkContinue, nil
		}
		switch n := n.(type) {
		case *ast.Image:
			return ast.WalkSkipChildren, nil
		case *ast.Text:
			v := n.Segment.Value(src)
			if !n.IsRaw() && n.Parent().Kind() != ast.KindCodeSpan {
				v = util.ResolveNumericReferences(util.ResolveEntityNames(util.UnescapePunctuations(v)))
			}
			b.Write(v)
		case *ast.String:
			// The typographer's quotes and dashes come as entities
			// (&rsquo;): the character, which the id then drops, as a
			// #Section link's id (mdtext.SectionAnchor) does.
			b.Write(util.ResolveEntityNames(n.Value))
		case *mathInline:
			b.Write(n.TeX)
		case *wikilinkNode:
			_, _, label := mdtext.SplitWikilink(n.Inner)
			b.WriteString(mdtext.PlainHeading(label))
		}
		return ast.WalkContinue, nil
	})
	return b.String()
}

func uniqueSlug(slug string, seen map[string]bool) string {
	u := slug
	for i := 1; seen[u]; i++ {
		u = fmt.Sprintf("%s-%d", slug, i)
	}
	seen[u] = true
	return u
}

// zoneRenderer: headings (✎, ☑ N), callouts, checkboxes, a page's raw
// HTML (defuseHTML); goldmark's renderers do the rest.
type zoneRenderer struct {
	env      *Env
	sections int
}

func (r *zoneRenderer) RegisterFuncs(reg renderer.NodeRendererFuncRegisterer) {
	reg.Register(ast.KindHeading, r.heading)
	reg.Register(ast.KindRawHTML, r.rawHTML)
	reg.Register(ast.KindHTMLBlock, r.htmlBlock)
	reg.Register(ast.KindBlockquote, r.blockquote)
	reg.Register(kindCheckbox, r.checkbox)
	reg.Register(kindMathInline, r.mathInline)
	reg.Register(kindMathBlock, r.mathBlock)
	reg.Register(kindWikilink, r.wikilink)
}

func attr(n ast.Node, name string) string {
	v, _ := n.AttributeString(name)
	s, _ := v.(string)
	return s
}

// heading: a section's heading on the page view sits in a section header
// with the ✎ that edits the section (numbered as pageSections cuts them),
// after its ☑ N chip when tasks link to it, which any ## or ### may have.
func (r *zoneRenderer) heading(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	n := node.(*ast.Heading)
	tag := fmt.Sprintf("h%d", n.Level)
	editable := r.env.EditSlug != "" && sectionHeading(n)
	if entering {
		if editable {
			fmt.Fprintf(w, `<div class="section-header section-header-%s">`, tag)
		}
		w.WriteString("<" + tag)
		if r.env.HeadingIDs == headingAsID {
			html.RenderAttributes(w, n, html.HeadingAttributeFilter)
		} else {
			id, hasID := n.AttributeString("id")
			for _, a := range n.Attributes() {
				if string(a.Name) != "id" && html.HeadingAttributeFilter.Contains(a.Name) {
					fmt.Fprintf(w, ` %s="%s"`, a.Name, util.EscapeHTML(attrBytes(a.Value)))
				}
			}
			if hasID && r.env.HeadingIDs == headingPID {
				fmt.Fprintf(w, ` data-pid="%s"`, util.EscapeHTML(attrBytes(id)))
			}
		}
		w.WriteString(">")
		return ast.WalkContinue, nil
	}
	w.WriteString("</" + tag + ">")
	if n.Level == 2 || n.Level == 3 {
		w.WriteString(r.env.SectionChips[attr(n, "id")])
	}
	if editable {
		r.sections++
		key := ""
		if r.sections <= len(r.env.SectionKeys) {
			key = "&amp;h=" + r.env.SectionKeys[r.sections-1]
		}
		fmt.Fprintf(w, `<a href="%s?section=%d%s" class="section-edit-btn" title="Edit section">✎</a></div>`,
			pathURL("edit", r.env.EditSlug), r.sections, key)
	}
	w.WriteString("\n")
	return ast.WalkContinue, nil
}

func (r *zoneRenderer) blockquote(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	kind := attr(node, "zone-alert")
	switch {
	case kind != "" && entering:
		fmt.Fprintf(w, "<div class=\"markdown-alert markdown-alert-%s\">\n<p class=\"markdown-alert-title\">%s</p>\n",
			strings.ToLower(kind), kind[:1]+strings.ToLower(kind[1:]))
	case kind != "":
		w.WriteString("</div>\n")
	case entering:
		w.WriteString("<blockquote>\n")
	default:
		w.WriteString("</blockquote>\n")
	}
	return ast.WalkContinue, nil
}

func (r *zoneRenderer) checkbox(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if entering {
		checked := ""
		if node.(*checkbox).Checked {
			checked = ` checked="checked"`
		}
		// The space is the one after "[ ]", which goldmark trims off the text.
		fmt.Fprintf(w, `<input class="task-list-item-checkbox" type="checkbox"%s /> `, checked)
	}
	return ast.WalkContinue, nil
}

// Task metadata comments (mdtext.TaskMetaSrcRE, mdtext.TaskDoneOnlyRE) as date chips.
func taskDates(s string) string {
	chip := func(kind, title, d string) string {
		return fmt.Sprintf(`<span class="task-date task-date-%s" title="%s %s">%s</span>`, kind, title, d, runePrefix(d, 10))
	}
	s = mdtext.TaskMetaSrcRE.ReplaceAllStringFunc(s, func(m string) string {
		g := mdtext.TaskMetaSrcRE.FindStringSubmatch(m)
		out := chip("created", "Created", g[1])
		if g[2] != "" {
			out += chip("done", "Done", g[2])
		}
		return out
	})
	return mdtext.TaskDoneOnlyRE.ReplaceAllStringFunc(s, func(m string) string {
		return chip("done", "Done", mdtext.TaskDoneOnlyRE.FindStringSubmatch(m)[1])
	})
}

// defusedTagRE: the start of a tag that makes a form or submits one, or
// that changes what the page is (a refresh to elsewhere, a base for its
// links), or that has the browser look another host up (<link
// rel=dns-prefetch>, which no policy stops: the name asked for can carry
// what the page's writer wants sent). A page's raw HTML is rendered as
// written, but these would work as Zone's own: script-src keeps a page's scripts from running and
// form-action keeps its forms from posting elsewhere, but a form posting to
// Zone itself (<form method=post action="/delete/…">, a <button
// formaction=…> or <button form=…> using one of Zone's own forms) is
// same-origin, so the Origin check lets it through, and one click on a page
// another user (or an assistant that read a prompt injection) wrote would
// delete or overwrite the viewer's pages. Their "<" is escaped, so they show
// as text.
var defusedTagRE = regexp.MustCompile(`(?i)<(/?(?:form|input|button|select|textarea|option|optgroup|datalist|fieldset|keygen|isindex|meta|base|link)(?:[\s/>]|$))`)

// htmlTagRE: a start tag, its quoted values whole (a ">" in one doesn't
// end it), for wikilinksInHTML. srcdocRE: a srcdoc attribute with a value.
// An iframe's srcdoc is a document of its own with the page's origin and
// policy, written with entities (&lt;form …), which defusedTagRE can't see:
// it is renamed, so the frame is empty. Wherever "srcdoc" is followed by
// "=" (HTML's whitespace between them or not), not only inside what looks
// like a tag: a browser's reading of where a tag ends differs from any
// pattern's (<iframe a=b' srcdoc="…">: the quote is part of a's value, no
// string's start), so no pattern decides it; and where the raw HTML ends
// right after the name, as what is rendered next may start with the "="
// (a list item's text after <pre></pre><iframe srcdoc). The word in a
// sentence stays.
//
// pingRE: a link's ping attribute with a value, likewise wherever it is
// followed by "=", but only as a name of its own (not "mapping=",
// "data-ping="): a followed link posts to each URL in it, same-origin and
// with the viewer's cookie, so <a ping="/delete/plans"> would delete a
// page with the click that reads on (the request checks refuse a ping
// too: notPing).
var (
	htmlTagRE = regexp.MustCompile(`<[A-Za-z](?:[^>"']|"[^"]*"|'[^']*')*`)
	srcdocRE  = regexp.MustCompile(`(?i)srcdoc([\t\n\f\r ]*(?:=|$))`)
	pingRE    = regexp.MustCompile(`(?i)(^|[^A-Za-z0-9_-])(ping[\t\n\f\r ]*(?:=|$))`)
)

// defuseHTML: raw HTML with defusedTagRE's tags as text and no srcdoc or
// ping.
func defuseHTML(raw []byte) []byte {
	raw = srcdocRE.ReplaceAll(raw, []byte("data-defused-srcdoc$1"))
	raw = pingRE.ReplaceAll(raw, []byte("${1}data-defused-$2"))
	return defusedTagRE.ReplaceAll(raw, []byte("&lt;$1"))
}

// rawHTML: inline raw HTML, as goldmark's unsafe renderer writes it, defused.
func (r *zoneRenderer) rawHTML(w util.BufWriter, source []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if entering {
		segs := node.(*ast.RawHTML).Segments
		var raw []byte
		for i := 0; i < segs.Len(); i++ {
			seg := segs.At(i)
			raw = append(raw, seg.Value(source)...)
		}
		_, _ = w.Write(defuseHTML(raw))
	}
	return ast.WalkSkipChildren, nil
}

// htmlBlock: an HTML block, as goldmark's unsafe renderer writes it, defused
// as a whole (a tag may span its lines).
func (r *zoneRenderer) htmlBlock(w util.BufWriter, source []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if !entering {
		return ast.WalkContinue, nil
	}
	n := node.(*ast.HTMLBlock)
	var raw []byte
	for i := 0; i < n.Lines().Len(); i++ {
		line := n.Lines().At(i)
		raw = append(raw, line.Value(source)...)
	}
	if n.HasClosure() {
		raw = append(raw, n.ClosureLine.Value(source)...)
	}
	_, _ = w.Write(r.wikilinksInHTML(defuseHTML(raw)))
	return ast.WalkContinue, nil
}

// wikilinksInHTML: an HTML block's wikilinks as links, in its text between
// the tags (goldmark parses no inlines in it), not in a tag's attributes.
func (r *zoneRenderer) wikilinksInHTML(raw []byte) []byte {
	if r.env == nil || r.env.Wikilink == nil || !bytes.Contains(raw, []byte("[[")) {
		return raw
	}
	var out []byte
	last := 0
	link := func(text []byte) []byte {
		return mdtext.WikilinkRE.ReplaceAllFunc(text, func(m []byte) []byte {
			if bytes.ContainsAny(m, "\n") {
				return m
			}
			open, label, close := r.env.Wikilink(string(m[2 : len(m)-2]))
			return []byte(open + renderLabel(label, r.env.Host) + close)
		})
	}
	for _, tag := range htmlTagRE.FindAllIndex(raw, -1) {
		out = append(out, link(raw[last:tag[0]])...)
		out = append(out, raw[tag[0]:tag[1]]...)
		last = tag[1]
	}
	return append(out, link(raw[last:])...)
}

// attrBytes: an attribute's value as goldmark keeps it ([]byte or string).
func attrBytes(v any) []byte {
	switch v := v.(type) {
	case []byte:
		return v
	case string:
		return []byte(v)
	}
	return []byte(fmt.Sprint(v))
}

// Wikilinks ([[Page]], [[Page|Label]], [[Page#Section]], [[#Section]]) are
// an inline of their own, parsed where goldmark parses links, so code spans,
// math and raw HTML keep theirs as text. A table splits its rows at "|"
// before inlines are parsed: escapeWikilinkPipes writes a wikilink's "|"
// as "\|" before the parse, GFM's pipe in a cell, and the parser reads it
// back.

var kindWikilink = ast.NewNodeKind("ZoneWikilink")

// wikilinkNode: [[Inner]].
type wikilinkNode struct {
	ast.BaseInline
	Inner string
}

func (n *wikilinkNode) Kind() ast.NodeKind         { return kindWikilink }
func (n *wikilinkNode) Dump(src []byte, level int) { ast.DumpHelper(n, src, level, nil, nil) }

type wikilinkExtension struct{}

func (wikilinkExtension) Extend(m goldmark.Markdown) {
	// Before goldmark's links (200), which would take the [ as a link's.
	m.Parser().AddOptions(parser.WithInlineParsers(util.Prioritized(wikilinkParser{}, 190)))
}

type wikilinkParser struct{}

func (wikilinkParser) Trigger() []byte { return []byte{'[', '!'} }

func (wikilinkParser) Parse(parent ast.Node, block text.Reader, pc parser.Context) ast.Node {
	line, seg := block.PeekLine()
	if line[0] == '!' {
		if _, ok := wikilinkAt(line[1:]); !ok {
			return nil
		}
		// The ! is text (an image's would take the [).
		block.Advance(1)
		return ast.NewTextSegment(seg.WithStop(seg.Start + 1))
	}
	n, ok := wikilinkAt(line)
	if !ok {
		return nil
	}
	block.Advance(n)
	return &wikilinkNode{Inner: strings.ReplaceAll(string(line[2:n-2]), `\|`, "|")}
}

// wikilinkAt: the length of the [[…]] line starts with, as mdtext.WikilinkRE
// matches one (no bracket inside, on one line).
func wikilinkAt(line []byte) (int, bool) {
	if !bytes.HasPrefix(line, []byte("[[")) {
		return 0, false
	}
	end := bytes.Index(line[2:], []byte("]]"))
	if end <= 0 || bytes.ContainsAny(line[2:2+end], "[]\n") {
		return 0, false
	}
	return 2 + end + 2, true
}

// escapeWikilinkPipes: text with the "|" of every wikilink in a table
// written "\\|", so the table doesn't split a cell at it (code masked: its
// text stays as it is). A table is a run of lines without a blank one that
// has a delimiter row (|---|:--:|); elsewhere a "\\|" would show.
func escapeWikilinkPipes(text string) string {
	if !strings.Contains(text, "|") || !strings.Contains(text, "-") {
		return text
	}
	lines := strings.SplitAfter(text, "\n")
	for start := 0; start < len(lines); {
		end := start
		for end < len(lines) && strings.TrimSpace(lines[end]) != "" {
			end++
		}
		if slices.ContainsFunc(lines[start:end], func(l string) bool { return tableDelimRE.MatchString(strings.TrimRight(l, "\r\n")) }) {
			for i := start; i < end; i++ {
				lines[i] = mdtext.WikilinkRE.ReplaceAllStringFunc(lines[i], func(m string) string {
					return strings.ReplaceAll(m, "|", `\|`)
				})
			}
		}
		start = end + 1
	}
	return strings.Join(lines, "")
}

// tableDelimRE: a table's delimiter row, a "|" in it.
var tableDelimRE = regexp.MustCompile(`^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$`)

// wikilink writes a wikilink as Env.Wikilink makes it, its label rendered
// as a link's text is (emphasis, code, math), or as written without one.
func (r *zoneRenderer) wikilink(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if !entering {
		return ast.WalkContinue, nil
	}
	n := node.(*wikilinkNode)
	if r.env == nil || r.env.Wikilink == nil {
		_, _ = w.WriteString(gohtml.EscapeString("[[" + n.Inner + "]]"))
		return ast.WalkContinue, nil
	}
	open, label, close := r.env.Wikilink(n.Inner)
	_, _ = w.WriteString(open)
	_, _ = w.WriteString(renderLabel(label, r.env.Host))
	_, _ = w.WriteString(close)
	return ast.WalkContinue, nil
}

// plainLabelRE: a label with nothing markdown or the typographer would
// change, written as it is (escaped) without a render.
var plainLabelRE = regexp.MustCompile("^[^*_\x60~$<>&\\\\'\"!\\[\\]]*$")

// renderLabel: a wikilink's label as inline markdown; its own HTML is text.
func renderLabel(label, host string) string {
	if plainLabelRE.MatchString(label) && !strings.Contains(label, "--") && !strings.Contains(label, "...") {
		return gohtml.EscapeString(label)
	}
	return markdown(gohtml.EscapeString(label), &Env{Host: host, HeadingIDs: headingNoID}, true)
}
