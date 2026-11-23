// Zonewiki's own documentation, for the MCP tool zone_help: the manual
// (docs/manual/*.md, for users), the README and the spec (docs/spec/*.md,
// for whoever builds Zonewiki), searched by section with BM25, so an assistant
// answers questions about Zonewiki itself (how to rename a task list's tabs, a
// setting, the query syntax) from the docs rather than from guesses. The
// manual's passages come first: they say it in a user's words.
package main

import (
	"io/fs"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"zonewiki/internal/textsearch"
)

// docsDir holds README.md and docs/spec/ (the --docs flag); "" finds them
// next to the browser code's folder (public/) in a checkout, else takes the
// binary's copy (docsRoot in files.go).
var docsDir string

// How Zonewiki is tested: nothing a user asks about.
var manualSkip = map[string]bool{"testing": true}

// manualChunkChars: a search hit's size. Long sections are split at
// paragraphs into pieces of at most this, so a hit is the passage, not a
// whole long section.
const manualChunkChars = 1500

type manualTopic struct{ name, path string }

// manualTopics: the docs by topic name: the manual ("manual", its
// contents, then "manual/tasks" … in reading order), "readme", then each
// spec file's name ("tasks", "editor", …), in that order.
func manualTopics() []manualTopic {
	root := docsRoot()
	if root == nil {
		return nil
	}
	var found []manualTopic
	for _, d := range manualPages() {
		name := "manual"
		if d.name != "" {
			name += "/" + d.name
		}
		found = append(found, manualTopic{name, d.path})
	}
	if fsExists(root, "README.md") {
		found = append(found, manualTopic{"readme", "README.md"})
	}
	paths, _ := fs.Glob(root, "docs/spec/*.md")
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

// manualHeadings: a doc's headings.
func manualHeadings(text string) []docHeading {
	var out []docHeading
	for _, h := range pageHeadings(text) {
		out = append(out, docHeading{h.level, h.text})
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
	ix   *textsearch.Index
}

// manualFind: the passages most relevant to the query, best first. The
// BM25 index over every doc's sections is rebuilt when a doc file changes.
func manualFind(query string, limit int) []manualDoc {
	topics, root := manualTopics(), docsRoot()
	var key strings.Builder
	for _, t := range topics {
		if st, err := fs.Stat(root, t.path); err == nil {
			key.WriteString(t.path + "\x00" + st.ModTime().String() + "\x00")
		}
	}
	manualIndex.mu.Lock()
	defer manualIndex.mu.Unlock()
	if manualIndex.ix == nil || manualIndex.key != key.String() {
		var docs []manualDoc
		var tokens [][]string
		for _, t := range topics {
			text, err := readDoc(t.path)
			if err != nil {
				continue
			}
			title := manualTitle(t.name, text)
			for _, s := range chunkSections(text, manualChunkChars) {
				docs = append(docs, manualDoc{t.name, title, s.Heading, s.Body})
				tokens = append(tokens, append(textsearch.Tokenize(s.Heading+"\n"+s.Body),
					repeatTokens(t.name+" "+title+" "+s.Heading, textsearch.TitleWeight)...))
			}
		}
		manualIndex.key, manualIndex.docs, manualIndex.ix = key.String(), docs, textsearch.NewIndex(tokens)
	}
	// The manual's best passages first, in the first half of the places
	// (rounded up), then the rest's (README, spec), each filling what the
	// other leaves: a user's question gets the manual's answer first and
	// the spec's details after it. Their scores aren't compared: a spec
	// section that lists files scores high on any word it names.
	isManual := func(doc int) bool { return isManualTopic(manualIndex.docs[doc].topic) }
	manual := manualIndex.ix.SearchWhere(query, limit, isManual)
	rest := manualIndex.ix.SearchWhere(query, limit, func(doc int) bool { return !isManual(doc) })
	n := min(len(manual), max((limit+1)/2, limit-len(rest)))
	var out []manualDoc
	for _, h := range append(manual[:n], rest...) {
		if len(out) == limit {
			break
		}
		out = append(out, manualIndex.docs[h.Doc])
	}
	return out
}

func isManualTopic(topic string) bool {
	return topic == "manual" || strings.HasPrefix(topic, "manual/")
}
