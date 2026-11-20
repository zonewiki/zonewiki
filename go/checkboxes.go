package main

// A text's checkboxes as the markdown parser finds them, as sections.go
// finds headings: the Nth box a page (or a task's notes) shows is the Nth
// here by construction, so a tick and the page view's keys go to the box
// the reader sees. A "- [ ]" line the render shows no box for (in an HTML
// comment, raw HTML, indented code, a fence in a quote or a list) shifts no
// number. (mdtext.TaskItemLines counts by lines, as the browser's editor
// does: js/mdtext.js taskCheckboxOffsets.)

import (
	"strings"
	"time"

	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/text"

	"zonewiki/internal/mdtext"
)

// taskItems: content's checkboxes in rendered order, each as its line
// (mdtext.SplitLines) and the bytes of its mark there. frontmatter: a
// page's text, whose leading frontmatter is no markdown (its render leaves
// it out too); a task's notes have none.
func taskItems(content string, frontmatter bool) []mdtext.TaskLine {
	if !strings.Contains(content, "[ ]") && !strings.Contains(content, "[x]") && !strings.Contains(content, "[X]") {
		return nil // no box, and no parse for it (a page view asks for its keys)
	}
	body := content
	if frontmatter {
		_, body = mdtext.ParseFrontmatter(content)
	}
	offset := len(content) - len(body)
	m := blockRenderers.Get().(*mdRenderer)
	defer blockRenderers.Put(m)
	m.transformer.env = &Env{}
	doc := m.md.Parser().Parse(text.NewReader([]byte(body)))
	m.transformer.env = nil
	var marks []int // in content
	_ = ast.Walk(doc, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if _, ok := n.(*checkbox); !entering || !ok {
			return ast.WalkContinue, nil
		}
		// The item's first line starts past its "[ ]" and the space after
		// it (taskTransformer).
		at := -1
		if lines := n.Parent().Lines(); lines.Len() > 0 {
			start := lines.At(0).Start
			if end := strings.LastIndexByte(body[:start], ']'); end >= 2 && body[end-2] == '[' &&
				strings.Trim(body[end+1:start], " \t") == "" {
				at = end - 1
			}
		}
		if at < 0 {
			logf("checkbox: no mark where the parser has one")
			return ast.WalkContinue, nil
		}
		marks = append(marks, offset+at)
		return ast.WalkContinue, nil
	})
	var items []mdtext.TaskLine
	start := 0
	for i, line := range mdtext.SplitLines(content) {
		for len(marks) > 0 && marks[0] < start+len(line) {
			items = append(items, mdtext.TaskLine{Index: i, Start: marks[0] - start, End: marks[0] - start + 1})
			marks = marks[1:]
		}
		start += len(line)
	}
	return items
}

// taskLineKeys: a key per checkbox of a page, in rendered order, for the
// page view (data-task-keys) and its ticks (mdtext.TaskLineKeys).
func taskLineKeys(content string) []string {
	return mdtext.TaskLineKeys(content, taskItems(content, true))
}

// toggleNthCheckbox ticks or unticks the Nth checkbox (0-based) the text
// renders, recording when it was done (at); an index past the last
// changes nothing. frontmatter as taskItems.
func toggleNthCheckbox(content string, idx int, frontmatter bool, at time.Time) string {
	items := taskItems(content, frontmatter)
	if idx >= len(items) {
		return content
	}
	return mdtext.ToggleCheckbox(content, items[idx], at)
}
