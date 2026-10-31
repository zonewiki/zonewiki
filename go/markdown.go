// Markdown to HTML on goldmark (the Python Zone used markdown-it). Not a
// copy of markdown-it's HTML: goldmark's own output where Zone doesn't care
// (footnotes, deflists, entities, URL escaping), Zone's rules where a
// feature depends on them: checkboxes the page can tick (counted as mdtext
// counts them), callouts, heading ids that [[Page#Section]] points at, ✎
// and ☑ N per section, image width and img-block, external links in a new
// tab, task-date chips. docs/spec/rendering.md has the differences.
package main

import (
	"bytes"
	"fmt"
	"regexp"
	"strings"
	"unicode"

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
	SectionChips map[string]string
	Host         string
}

// typographer: curly quotes, dashes and the ellipsis, as markdown-it's
// smartquotes and replacements; not « » for << >>.
var typographer = extension.NewTypographer(extension.WithTypographicSubstitutions(
	extension.TypographicSubstitutions{extension.LeftAngleQuote: []byte("&lt;&lt;"), extension.RightAngleQuote: []byte("&gt;&gt;")}))

// markdown renders src; inline renders it as one paragraph's inline
// content (markdown-it's renderInline: no headings, lists, quotes …).
func markdown(src string, env *Env, inline bool) string {
	opts := []goldmark.Option{
		goldmark.WithExtensions(extension.Table, extension.Strikethrough,
			extension.NewLinkify(extension.WithLinkifyURLRegexp(urlRE)),
			extension.Footnote, extension.DefinitionList, typographer),
		goldmark.WithParserOptions(
			parser.WithParagraphTransformers(
				util.Prioritized(alertTransformer{}, 100),
				util.Prioritized(taskTransformer{}, 200),
			),
			parser.WithASTTransformers(util.Prioritized(&leafTransformer{env: env}, 1000)),
		),
		goldmark.WithRendererOptions(
			html.WithUnsafe(), html.WithXHTML(),
			renderer.WithNodeRenderers(util.Prioritized(&leafRenderer{env: env}, 100)),
		),
	}
	if inline {
		opts = append([]goldmark.Option{goldmark.WithParser(parser.NewParser(
			parser.WithBlockParsers(util.Prioritized(parser.NewParagraphParser(), 1000)),
			parser.WithInlineParsers(parser.DefaultInlineParsers()...),
			parser.WithParagraphTransformers(parser.DefaultParagraphTransformers()...),
		))}, opts...)
	}
	var buf bytes.Buffer
	if err := goldmark.New(opts...).Convert([]byte(src), &buf); err != nil {
		logf("markdown: %v", err)
	}
	// Escaped text never holds a "<!--", so only raw HTML's comments match.
	out := taskDates(buf.String())
	if inline {
		out = strings.TrimSuffix(strings.TrimPrefix(strings.TrimSpace(out), "<p>"), "</p>")
	}
	return out
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
	bq.SetAttributeString("leaf-alert", kind)
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
// mdtext._TASK_ITEM_RE counts: ticking the Nth checkbox toggles the Nth
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
	li.SetAttributeString("leaf-task", v[1] != ' ')
	p.Lines().Set(0, first.WithStart(first.Start+3))
}

var kindCheckbox = ast.NewNodeKind("LeafCheckbox")

type checkbox struct {
	ast.BaseInline
	Checked bool
}

func (n *checkbox) Kind() ast.NodeKind         { return kindCheckbox }
func (n *checkbox) Dump(src []byte, level int) { ast.DumpHelper(n, src, level, nil, nil) }

// leafTransformer, after parsing: checkboxes, heading ids, images, links.
type leafTransformer struct{ env *Env }

var imgWidthRE = regexp.MustCompile(`\|(\d+)$`)

func (t *leafTransformer) Transform(doc *ast.Document, reader text.Reader, pc parser.Context) {
	src := reader.Source()
	slugs := map[string]bool{}
	var unsafe []ast.Node
	defer func() {
		// As markdown-it: such a link is its text.
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
			if checked, ok := n.AttributeString("leaf-task"); ok {
				n.RemoveAttributes()
				n.SetAttributeString("class", "task-list-item")
				p := n.FirstChild()
				p.InsertBefore(p, p.FirstChild(), &checkbox{Checked: checked.(bool)})
			}
		case *ast.Paragraph:
			// An image alone in its paragraph may grow beyond the text width.
			if n.ChildCount() == 1 && n.FirstChild().Kind() == ast.KindImage {
				n.SetAttributeString("class", "img-block")
			}
		case *ast.Image:
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
				n.SetAttributeString("id", uniqueSlug(slugify(headingTitle(n, src)), slugs))
			}
		}
		return ast.WalkContinue, nil
	})
}

// unsafeLink: a URL markdown-it refuses to link (javascript:, vbscript:,
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

// external: an http(s) link to another site opens in a new tab; one to the
// host the request came in on (Zone itself) does not. Not url.Parse: it
// refuses some URLs a browser follows (a ` in the host, say).
func (t *leafTransformer) external(n ast.Node, href []byte) {
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
			b.Write(n.Value)
		}
		return ast.WalkContinue, nil
	})
	return b.String()
}

// slugify is mdit_py_plugins.anchors', which mdtext.section_anchor mirrors
// for [[Page#Section]] (Python's \w is Unicode letters, digits and _).
var slugStripRE = regexp.MustCompile(`[^\p{L}\p{N}_\x{4e00}-\x{9fff}\- ]`)

func slugify(title string) string {
	s := strings.ReplaceAll(strings.ToLower(strings.TrimFunc(title, unicode.IsSpace)), " ", "-")
	return slugStripRE.ReplaceAllString(s, "")
}

func uniqueSlug(slug string, seen map[string]bool) string {
	u := slug
	for i := 1; seen[u]; i++ {
		u = fmt.Sprintf("%s-%d", slug, i)
	}
	seen[u] = true
	return u
}

// leafRenderer: headings (✎, ☑ N), callouts, checkboxes; goldmark's
// renderers do the rest.
type leafRenderer struct {
	env      *Env
	sections int
}

func (r *leafRenderer) RegisterFuncs(reg renderer.NodeRendererFuncRegisterer) {
	reg.Register(ast.KindHeading, r.heading)
	reg.Register(ast.KindBlockquote, r.blockquote)
	reg.Register(kindCheckbox, r.checkbox)
}

func attr(n ast.Node, name string) string {
	v, _ := n.AttributeString(name)
	s, _ := v.(string)
	return s
}

// heading: a ## or ### heading on the page view sits in a section header
// with the ✎ that edits the section (numbered as mdtext.split_sections
// counts them), after its ☑ N chip when tasks link to it.
func (r *leafRenderer) heading(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	n := node.(*ast.Heading)
	tag := fmt.Sprintf("h%d", n.Level)
	sectioned := n.Level == 2 || n.Level == 3
	if entering {
		if r.env.EditSlug != "" && sectioned {
			fmt.Fprintf(w, `<div class="section-header section-header-%s">`, tag)
		}
		w.WriteString("<" + tag)
		html.RenderAttributes(w, n, html.HeadingAttributeFilter)
		w.WriteString(">")
		return ast.WalkContinue, nil
	}
	w.WriteString("</" + tag + ">")
	if sectioned {
		w.WriteString(r.env.SectionChips[attr(n, "id")])
		if r.env.EditSlug != "" {
			r.sections++
			fmt.Fprintf(w, `<a href="%s?section=%d" class="section-edit-btn" title="Edit section">✎</a></div>`,
				pathURL("edit", r.env.EditSlug), r.sections)
		}
	}
	w.WriteString("\n")
	return ast.WalkContinue, nil
}

func (r *leafRenderer) blockquote(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	kind := attr(node, "leaf-alert")
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

func (r *leafRenderer) checkbox(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
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

// Task metadata comments (taskMetaSrcRE, taskDoneOnlyRE) as date chips.
func taskDates(s string) string {
	chip := func(kind, title, d string) string {
		return fmt.Sprintf(`<span class="task-date task-date-%s" title="%s %s">%s</span>`, kind, title, d, runePrefix(d, 10))
	}
	s = taskMetaSrcRE.ReplaceAllStringFunc(s, func(m string) string {
		g := taskMetaSrcRE.FindStringSubmatch(m)
		out := chip("created", "Created", g[1])
		if g[2] != "" {
			out += chip("done", "Done", g[2])
		}
		return out
	})
	return taskDoneOnlyRE.ReplaceAllStringFunc(s, func(m string) string {
		return chip("done", "Done", taskDoneOnlyRE.FindStringSubmatch(m)[1])
	})
}
