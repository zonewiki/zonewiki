// Math in pages: $…$ inline, $$…$$ for display, on goldmark. The TeX is
// kept as written (no emphasis, typographer or escapes inside it) and goes
// out as text in a .math element, which js/figures.js renders with KaTeX
// in the browser; without it, the TeX shows as written. A ```math fence is
// display math too, rendered there from its code block.
package main

import (
	"bytes"

	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/parser"
	"github.com/yuin/goldmark/renderer/html"
	"github.com/yuin/goldmark/text"
	"github.com/yuin/goldmark/util"
)

var kindMathInline = ast.NewNodeKind("ZoneMathInline")
var kindMathBlock = ast.NewNodeKind("ZoneMathBlock")

// mathInline: $tex$, or $$tex$$ in running text (Display).
type mathInline struct {
	ast.BaseInline
	TeX     []byte
	Display bool
}

func (n *mathInline) Kind() ast.NodeKind         { return kindMathInline }
func (n *mathInline) Dump(src []byte, level int) { ast.DumpHelper(n, src, level, nil, nil) }

// mathBlock: lines between a $$ line and the next line ending in $$
// (closed: $$tex$$ on one line, done when opened).
type mathBlock struct {
	ast.BaseBlock
	closed bool
}

func (n *mathBlock) Kind() ast.NodeKind         { return kindMathBlock }
func (n *mathBlock) IsRaw() bool                { return true }
func (n *mathBlock) Dump(src []byte, level int) { ast.DumpHelper(n, src, level, nil, nil) }

// mathExtension adds the parsers; block: $$ lines too (not in a render of
// one paragraph's inline content).
type mathExtension struct{ block bool }

func (e mathExtension) Extend(m goldmark.Markdown) {
	m.Parser().AddOptions(parser.WithInlineParsers(util.Prioritized(mathInlineParser{}, 150)))
	if e.block {
		// Before the paragraph (1000) and the fenced code (700) parsers.
		m.Parser().AddOptions(parser.WithBlockParsers(util.Prioritized(mathBlockParser{}, 690)))
	}
}

// mathInlineParser, pandoc's rules, so prices stay text: the opening $ has
// no space after it, the closing $ none before it and no digit after it
// ("$5 and $10", "$5–$10" are no math); $$…$$ needs only its closing $$. On
// one line; a backslash keeps the next character from closing it (\$).
type mathInlineParser struct{}

func (mathInlineParser) Trigger() []byte { return []byte{'$'} }

func (mathInlineParser) Parse(parent ast.Node, block text.Reader, pc parser.Context) ast.Node {
	line, _ := block.PeekLine()
	open := 1
	if len(line) > 1 && line[1] == '$' {
		open = 2
	}
	body := line[open:]
	if len(body) == 0 || isSpaceByte(body[0]) || body[0] == '$' {
		return nil
	}
	for i := 0; i < len(body); i++ {
		switch body[i] {
		case '\\':
			i++
		case '\n', '\r':
			return nil
		case '$':
			if open == 2 {
				if i+1 >= len(body) || body[i+1] != '$' {
					continue
				}
			} else if isSpaceByte(body[i-1]) || i+1 < len(body) && body[i+1] >= '0' && body[i+1] <= '9' {
				continue
			}
			block.Advance(open + i + open)
			return &mathInline{TeX: bytes.Clone(body[:i]), Display: open == 2}
		}
	}
	return nil
}

func isSpaceByte(b byte) bool { return b == ' ' || b == '\t' || b == '\n' || b == '\r' }

// mathBlockParser: a line of only $$ (up to three spaces before it) opens
// display math, which runs to a line ending in $$ (or the end, as an
// unclosed fence does); $$tex$$ alone on its line is display math in one.
// A paragraph line that only starts with $$ ("$$100 bills") stays text.
type mathBlockParser struct{}

func (mathBlockParser) Trigger() []byte { return []byte{'$'} }

func (mathBlockParser) Open(parent ast.Node, reader text.Reader, pc parser.Context) (ast.Node, parser.State) {
	line, seg := reader.PeekLine()
	indent := 0
	for indent < len(line) && line[indent] == ' ' {
		indent++
	}
	if indent > 3 {
		return nil, parser.NoChildren
	}
	trimmed := bytes.TrimSpace(line)
	if !bytes.HasPrefix(trimmed, []byte("$$")) {
		return nil, parser.NoChildren
	}
	node := &mathBlock{}
	if len(trimmed) == 2 {
		reader.AdvanceToEOL()
		return node, parser.NoChildren
	}
	if len(trimmed) > 4 && bytes.HasSuffix(trimmed, []byte("$$")) {
		start := seg.Start + indent + 2
		node.Lines().Append(text.NewSegment(start, start+len(trimmed)-4))
		node.closed = true
		reader.AdvanceToEOL()
		return node, parser.NoChildren
	}
	return nil, parser.NoChildren
}

func (mathBlockParser) Continue(node ast.Node, reader text.Reader, pc parser.Context) parser.State {
	if node.(*mathBlock).closed {
		return parser.Close
	}
	line, seg := reader.PeekLine()
	trimmed := bytes.TrimRight(line, " \t\r\n")
	if bytes.HasSuffix(trimmed, []byte("$$")) {
		if before := bytes.TrimSpace(trimmed[:len(trimmed)-2]); len(before) > 0 {
			node.Lines().Append(text.NewSegment(seg.Start, seg.Start+len(trimmed)-2))
		}
		reader.AdvanceToEOL()
		return parser.Close
	}
	node.Lines().Append(seg)
	reader.AdvanceToEOL()
	return parser.Continue | parser.NoChildren
}

func (mathBlockParser) Close(node ast.Node, reader text.Reader, pc parser.Context) {}
func (mathBlockParser) CanInterruptParagraph() bool                                { return true }
func (mathBlockParser) CanAcceptIndentedLine() bool                                { return false }

func (r *zoneRenderer) mathInline(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if entering {
		n := node.(*mathInline)
		class := "math math-inline"
		if n.Display {
			class = "math math-display"
		}
		w.WriteString(`<span class="` + class + `">`)
		w.Write(util.EscapeHTML(n.TeX))
		w.WriteString("</span>")
	}
	return ast.WalkSkipChildren, nil
}

func (r *zoneRenderer) mathBlock(w util.BufWriter, src []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if entering {
		w.WriteString(`<div class="math math-display">`)
		lines := node.Lines()
		for i := 0; i < lines.Len(); i++ {
			seg := lines.At(i)
			html.DefaultWriter.RawWrite(w, seg.Value(src))
		}
		w.WriteString("</div>\n")
	}
	return ast.WalkSkipChildren, nil
}
