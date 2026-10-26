package main

import (
	"sort"
	"strings"
)

// The keyword path of src/retrieval.py (semantic search off): page search
// for the browser (do_search) and section retrieval for LLMs (retrieve).

const fuseDepth = 30 // retrieval.FUSE_DEPTH

// Doc is a page, a page's section or a task, as retrieval.py indexes it.
type Doc struct {
	Parts   [][2]any `json:"parts"` // [text, times]: its tokens are these texts' tokens
	Text    string   `json:"text"`  // _doc_text: the searchable text
	Tags    []string `json:"tags"`
	Date    string   `json:"date"` // _doc_date
	Slug    string   `json:"slug"`
	Type    string   `json:"type"`    // "task" or ""
	Heading string   `json:"heading"` // a section's
	Body    string   `json:"body"`    // a section's text
	Excerpt string   `json:"excerpt_text"`

	tokens   []string
	squashed string
}

// Corpus is a SearchIndex with its documents.
type Corpus struct {
	Docs []*Doc
	ix   *Index
}

func NewCorpus(docs []*Doc) *Corpus {
	toks := make([][]string, len(docs))
	for i, d := range docs {
		for _, p := range d.Parts {
			t := Tokenize(p[0].(string))
			for n := int(p[1].(float64)); n > 0; n-- {
				d.tokens = append(d.tokens, t...)
			}
		}
		toks[i] = d.tokens
	}
	return &Corpus{Docs: docs, ix: NewIndex(toks)}
}

func (c *Corpus) passes(q *Query, d *Doc) bool {
	return !q.HasFilters() || q.Accepts(d.Text, d.tokens, d.Tags, d.Date)
}

func (c *Corpus) hasPhrase(d *Doc, phrase string) bool {
	if d.squashed == "" {
		d.squashed = squash(d.Text)
	}
	return strings.Contains(d.squashed, phrase)
}

func (c *Corpus) bm25(q *Query, depth int) []int {
	var out []int
	limit := depth
	if q.HasFilters() {
		limit = len(c.Docs)
	}
	for _, h := range c.ix.Search(q.RankingText(), limit) {
		if c.passes(q, c.Docs[h.Doc]) {
			out = append(out, h.Doc)
		}
	}
	return out[:min(depth, len(out))]
}

// phraseFirst: the documents with the query as a phrase first (in BM25
// order, ones the ranking cut off too), then the rest.
func (c *Corpus) phraseFirst(ranked []int, q *Query) []int {
	phrase := q.Phrase()
	if phrase == "" {
		return ranked
	}
	var hits []int
	first := map[int]bool{}
	for _, h := range c.ix.Search(q.RankingText(), len(c.Docs)) {
		if d := c.Docs[h.Doc]; c.passes(q, d) && c.hasPhrase(d, phrase) {
			hits = append(hits, h.Doc)
			first[h.Doc] = true
		}
	}
	if hits == nil {
		return ranked
	}
	for _, i := range ranked {
		if !first[i] {
			hits = append(hits, i)
		}
	}
	return hits
}

// Ranked is _ranked_docs: the `limit` best documents; for a query of only
// date filters, all that pass, newest first.
func (c *Corpus) Ranked(q *Query, limit int) []int {
	if pyStrip(q.RankingText()) == "" {
		if len(q.Dates) == 0 {
			return nil
		}
		var docs []int
		for i, d := range c.Docs {
			if c.passes(q, d) {
				docs = append(docs, i)
			}
		}
		sort.SliceStable(docs, func(x, y int) bool { return c.Docs[docs[x]].Date > c.Docs[docs[y]].Date })
		return docs[:min(limit, len(docs))]
	}
	r := c.phraseFirst(c.bm25(q, limit), q)
	return r[:min(limit, len(r))]
}

// Retrieve is retrieve(): the sections and tasks for an LLM.
func (c *Corpus) Retrieve(q *Query, limit int) []int {
	if pyStrip(q.RankingText()) == "" {
		return nil
	}
	r := c.phraseFirst(c.bm25(q, fuseDepth), q)
	return r[:min(limit, len(r))]
}

// Result is one of do_search's results, what decides how it shows.
type Result struct {
	Doc     int      `json:"doc"`
	Section int      `json:"section"` // the chunk its link opens, -1 for none
	Excerpt string   `json:"excerpt"`
	Words   []string `json:"words"` // what the page highlights
}

// DoSearch is do_search's keyword path: pages and tasks, each with its best
// section, excerpt and highlighted words.
func DoSearch(raw string, pages, chunks *Corpus, limit int) []Result {
	if pyStrip(raw) == "" {
		return nil
	}
	q := ParseQuery(raw)
	rt := q.RankingText()
	docs := pages.Ranked(q, limit)
	slugs := map[string]bool{}
	for _, i := range docs {
		if d := pages.Docs[i]; d.Type != "task" {
			slugs[d.Slug] = true
		}
	}
	best := chunks.bestSections(q, slugs)
	var out []Result
	for _, i := range docs {
		d := pages.Docs[i]
		r := Result{Doc: i, Section: -1}
		text := d.Excerpt
		if s, ok := best[d.Slug]; ok && d.Type != "task" {
			r.Section = s
			text = pyStrip(chunks.Docs[s].Heading + "\n" + chunks.Docs[s].Body)
		}
		r.Excerpt = Excerpt(text, rt)
		if d.Type != "task" {
			r.Words = MatchedWords(d.Excerpt, rt, 10)
		}
		out = append(out, r)
	}
	return out
}

// bestSections is _best_sections without semantic search: per page, the
// first section by BM25 with the query as a phrase, else with a query word.
func (c *Corpus) bestSections(q *Query, slugs map[string]bool) map[string]int {
	rt := q.RankingText()
	var ranked []int
	for _, h := range c.ix.Search(rt, len(c.Docs)) {
		if d := c.Docs[h.Doc]; d.Type != "task" && slugs[d.Slug] {
			ranked = append(ranked, h.Doc)
		}
	}
	best := map[string]int{}
	if phrase := q.Phrase(); phrase != "" {
		for _, i := range ranked {
			d := c.Docs[i]
			if _, ok := best[d.Slug]; !ok && strings.Contains(squash(d.Heading+"\n"+d.Body), phrase) {
				best[d.Slug] = i
			}
		}
	}
	for _, i := range ranked {
		d := c.Docs[i]
		if _, ok := best[d.Slug]; !ok && Matches(d.Heading+"\n"+d.Body, rt) {
			best[d.Slug] = i
		}
	}
	return best
}
