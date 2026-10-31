// Zone's own documentation, for the MCP tool leaf_help: the
// README and the spec (docs/spec/*.md), searched by section with BM25, so
// an assistant answers questions about Zone itself (how to rename a task list's
// tabs, a setting, the query syntax) from the docs rather than from guesses.
package main

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// docsDir holds README.md and docs/spec/ (the -docs flag); "" finds them
// next to the browser code's folder (public/): in the checkout and in the
// image alike.
var docsDir string

// How Zone is tested: nothing a user asks about.
var manualSkip = map[string]bool{"testing": true}

// manualChunkChars: a search hit's size. Long sections are split at
// paragraphs into pieces of at most this, so a hit is the passage, not a
// whole long section.
const manualChunkChars = 1500

func docsRoot() string {
	candidates := []string{docsDir}
	if docsDir == "" {
		candidates = []string{filepath.Dir(publicDir)}
	}
	for _, root := range candidates {
		if isDir(filepath.Join(root, "docs", "spec")) {
			return root
		}
	}
	return ""
}

type manualTopic struct{ name, path string }

// manualTopics: the docs by topic name, "readme", then each spec file's
// name ("tasks", "editor", …), in that order.
func manualTopics() []manualTopic {
	root := docsRoot()
	if root == "" {
		return nil
	}
	var found []manualTopic
	if readme := filepath.Join(root, "README.md"); exists(readme) {
		found = append(found, manualTopic{"readme", readme})
	}
	paths, _ := filepath.Glob(filepath.Join(root, "docs", "spec", "*.md"))
	sort.Strings(paths)
	for _, p := range paths {
		if name := strings.TrimSuffix(filepath.Base(p), ".md"); !manualSkip[name] {
			found = append(found, manualTopic{name, p})
		}
	}
	return found
}

type docHeading struct {
	level int
	text  string
}

// manualHeadings: a doc's headings, those in code fences left out.
func manualHeadings(text string) []docHeading {
	var out []docHeading
	for _, l := range fencedLines(strings.Split(text, "\n")) {
		if m := chunkHeadingRE.FindStringSubmatch(l.line); m != nil && !l.inFence {
			out = append(out, docHeading{len(m[1]), m[2]})
		}
	}
	return out
}

// manualTitle: a doc's first heading ("Tasks"), else the topic name.
func manualTitle(topic, text string) string {
	if h := manualHeadings(text); len(h) > 0 {
		return h[0].text
	}
	return topic
}

// manualDoc is a passage of the docs.
type manualDoc struct{ topic, title, heading, text string }

var manualIndex struct {
	mu   sync.Mutex
	key  string
	docs []manualDoc
	ix   *Index
}

// manualFind: the passages most relevant to the query, best first. The
// BM25 index over every doc's sections is rebuilt when a doc file changes.
func manualFind(query string, limit int) []manualDoc {
	topics := manualTopics()
	var key strings.Builder
	for _, t := range topics {
		if st, err := os.Stat(t.path); err == nil {
			key.WriteString(t.path + "\x00" + st.ModTime().String() + "\x00")
		}
	}
	manualIndex.mu.Lock()
	defer manualIndex.mu.Unlock()
	if manualIndex.ix == nil || manualIndex.key != key.String() {
		var docs []manualDoc
		var tokens [][]string
		for _, t := range topics {
			text, err := readText(t.path)
			if err != nil {
				continue
			}
			title := manualTitle(t.name, text)
			for _, s := range chunkSections(text, manualChunkChars) {
				docs = append(docs, manualDoc{t.name, title, s.Heading, s.Body})
				tokens = append(tokens, append(tokenize(s.Heading+"\n"+s.Body),
					repeatTokens(t.name+" "+title+" "+s.Heading, titleWeight)...))
			}
		}
		manualIndex.key, manualIndex.docs, manualIndex.ix = key.String(), docs, NewIndex(tokens)
	}
	var out []manualDoc
	for _, h := range manualIndex.ix.Search(query, limit) {
		out = append(out, manualIndex.docs[h.Doc])
	}
	return out
}
