// Lexical search: folding, stemming, the query syntax and
// its filters, BM25 with prefix and typo expansion, the words a query
// matched, excerpts. Ported from the Python Zone, checked against it on a
// wiki: the same results on 11,094 queries.
//
// Offsets into a text (spans, excerpt windows) are counted in runes, as
// the browser counts characters.
package textsearch

import (
	"html"
	"iter"
	"maps"
	"math"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"unicode"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

// TitleWeight: title tokens go into a document this many extra times.
const TitleWeight = 3

const (
	bm25K1       = 1.5
	bm25B        = 0.75
	prefixWeight = 0.8
	fuzzyWeight  = 0.5
)

// ---- Characters ----

// WordRune: a letter or a number, any script (a word's character, not _).
func WordRune(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) }

func casefold(s string) string { return cases.Fold().String(s) }

// Squash: folded text with runs of whitespace as one space (phrase checks).
func Squash(text string) string { return strings.Join(strings.Fields(fold(text)), " ") }

func RuneLen(s string) int { return len([]rune(s)) }

func sliceRunes(r []rune, a, b int) string { return string(r[a:b]) }

// span is a word's place in a text, in runes.
type span struct{ start, end int }

// words: the runs of letters and digits in text, with their rune spans.
func words(text string) ([]string, []span) {
	var out []string
	var spans []span
	start, i := -1, 0
	var b strings.Builder
	for _, r := range text {
		if WordRune(r) {
			if start < 0 {
				start = i
				b.Reset()
			}
			b.WriteRune(r)
		} else if start >= 0 {
			out, spans = append(out, b.String()), append(spans, span{start, i})
			start = -1
		}
		i++
	}
	if start >= 0 {
		out, spans = append(out, b.String()), append(spans, span{start, i})
	}
	return out, spans
}

// fold: case and accents off ("Müller", "MÜLLER", "muller" match; ß → ss).
func fold(text string) string {
	var b strings.Builder
	for _, r := range norm.NFKD.String(casefold(text)) {
		if norm.NFKD.PropertiesString(string(r)).CCC() == 0 {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// stem: the conservative suffix stripper of search.stem (run/runs/running).
func stem(word string) string {
	w := []rune(word)
	if len(w) <= 3 {
		return word
	}
	for _, suf := range []string{"ing", "ed"} {
		if strings.HasSuffix(word, suf) && len(w)-len(suf) >= 3 {
			base := w[:len(w)-len(suf)]
			if suf == "ed" && base[len(base)-1] == 'e' {
				break
			}
			if !strings.ContainsAny(string(base), "aeiouy") {
				break
			}
			if len(base) >= 4 && base[len(base)-1] == base[len(base)-2] && !strings.ContainsRune("aeioulsz", base[len(base)-1]) {
				base = base[:len(base)-1]
			}
			return string(base)
		}
	}
	switch {
	case strings.HasSuffix(word, "sses"):
		return string(w[:len(w)-2])
	case strings.HasSuffix(word, "ies") && len(w) > 4:
		return string(w[:len(w)-3]) + "y"
	case strings.HasSuffix(word, "ss"):
		return word
	case strings.HasSuffix(word, "s") && len(w) > 3:
		return string(w[:len(w)-1])
	}
	return word
}

var (
	linkTargetRE = regexp.MustCompile(`\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+"[^"]*")?\)`)
	linkDefRE    = regexp.MustCompile(`(?m)^( {0,3}\[[^\]]+\]:)\s+\S+.*$`)
)

// StripLinkTargets: markdown with link and image targets off, link text kept.
func StripLinkTargets(text string) string {
	return linkDefRE.ReplaceAllString(linkTargetRE.ReplaceAllString(text, "]"), "$1")
}

// Tokenize: folded, split into words, stemmed.
func Tokenize(text string) []string {
	ws, _ := words(fold(text))
	for i, w := range ws {
		ws[i] = stem(w)
	}
	return ws
}

// TokensAndSquash: Tokenize(text) and Squash(text), folding the text once
// for both.
func TokensAndSquash(text string) ([]string, string) {
	f := fold(text)
	ws, _ := words(f)
	for i, w := range ws {
		ws[i] = stem(w)
	}
	return ws, strings.Join(strings.Fields(f), " ")
}

var stopwords = map[string]bool{}

func init() {
	for _, w := range strings.Fields(`a an and are as at be been but by can could did do does for from had has have
how i if in into is me my of on or our so than that the their them then there
these they this those to was we were what when where which who whom why with
would you your about after before over under up down out
aber als am an auf aus bei bin bis bist da dann das dass dem den der des die
dies diese dieser doch du ein eine einem einen einer er es fur hab habe hat
hatte ich ihr im in ist ja mich mir mit nach nicht noch nur ob oder sich sie
sind so uber um und uns von vor war waren was welche welcher wenn wer wie wir
wo zu zum zur`) {
		stopwords[w] = true
	}
}

// queryWords: the folded words of a query but the stopwords, unless that
// leaves none.
func queryWords(query string) []string {
	ws, _ := words(fold(query))
	var kept []string
	for _, w := range ws {
		if !stopwords[w] {
			kept = append(kept, w)
		}
	}
	if kept == nil {
		return ws
	}
	return kept
}

// ---- the query syntax: "phrase", -word, -"phrase", #tag, -#tag, dates ----

// nonSpaceClass: a character that isn't white space (as package mdtext's).
const nonSpaceClass = `[^\s\v\pZ\x{85}]`

var (
	queryPartRE  = regexp.MustCompile(`(-?)(?:"([^"]+)"|(` + nonSpaceClass + `+))`)
	dateFilterRE = regexp.MustCompile(`^(?i)(edited|after|before):(\p{Nd}{4}(?:-\p{Nd}{2}(?:-\p{Nd}{2})?)?)$`)
)

type dateFilter struct{ Kind, Value string }

// Query is search.Query: Text ranks and is marked in excerpts, the rest
// filter.
type Query struct {
	Text           string       `json:"text"`
	Phrases        []string     `json:"phrases"`
	ExcludePhrases []string     `json:"exclude_phrases"`
	ExcludeStems   []string     `json:"exclude_stems"` // sorted
	Tags           []string     `json:"tags"`          // sorted
	ExcludeTags    []string     `json:"exclude_tags"`  // sorted
	Dates          []dateFilter `json:"dates"`
}

func ParseQuery(raw string) *Query {
	q := &Query{}
	var ws, phrases []string
	excl, tags, extags := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, m := range queryPartRE.FindAllStringSubmatchIndex(raw, -1) {
		negative := m[3] > m[2]
		if m[4] >= 0 {
			quoted := raw[m[4]:m[5]]
			if strings.TrimSpace(quoted) == "" {
				continue
			}
			if negative {
				q.ExcludePhrases = append(q.ExcludePhrases, Squash(quoted))
			} else {
				q.Phrases = append(q.Phrases, Squash(quoted))
				phrases = append(phrases, strings.TrimSpace(quoted))
			}
			continue
		}
		plain := raw[m[6]:m[7]]
		if d := dateFilterRE.FindStringSubmatch(plain); d != nil {
			kind := strings.ToLower(d[1])
			if negative {
				kind = map[string]string{"after": "before", "before": "after", "edited": "not-edited"}[kind]
			}
			q.Dates = append(q.Dates, dateFilter{kind, d[2]})
		} else if strings.HasPrefix(plain, "#") && strings.Trim(plain, "#") != "" {
			tag := strings.ToLower(strings.TrimLeft(plain, "#"))
			if negative {
				extags[tag] = true
			} else {
				tags[tag] = true
			}
		} else if negative {
			for _, t := range Tokenize(plain) {
				excl[t] = true
			}
		} else {
			ws = append(ws, plain)
		}
	}
	q.Text = strings.Join(append(phrases, ws...), " ")
	q.ExcludeStems, q.Tags, q.ExcludeTags = slices.Sorted(maps.Keys(excl)), slices.Sorted(maps.Keys(tags)), slices.Sorted(maps.Keys(extags))
	return q
}

// Phrase: the query's words as one phrase, when there are two or more.
func (q *Query) Phrase() string {
	if len(strings.Fields(q.Text)) >= 2 {
		return Squash(q.Text)
	}
	return ""
}

func (q *Query) HasFilters() bool {
	return len(q.Phrases)+len(q.ExcludePhrases)+len(q.ExcludeStems)+len(q.Tags)+len(q.ExcludeTags)+len(q.Dates) > 0
}

// RankingText: the words and phrases, or for only tags the tag words.
func (q *Query) RankingText() string {
	if q.Text != "" {
		return q.Text
	}
	return strings.Join(q.Tags, " ")
}

// Accepts: whether a document passes the filters (its text, tokens, tags,
// date "YYYY-MM-DD"; partial dates compare as prefixes).
func (q *Query) Accepts(text string, docTags []string, date string) bool {
	squashed := ""
	return q.AcceptsWith(func(phrase string) bool {
		if squashed == "" {
			squashed = Squash(text)
		}
		return strings.Contains(squashed, phrase)
	}, docTags, date)
}

// AcceptsWith: Accepts with has telling whether the text, squashed (Squash),
// holds a phrase; asked only for the query's phrase filters.
func (q *Query) AcceptsWith(has func(phrase string) bool, docTags []string, date string) bool {
	for _, d := range q.Dates {
		if date == "" {
			return false
		}
		switch {
		case d.Kind == "edited" && !strings.HasPrefix(date, d.Value),
			d.Kind == "not-edited" && strings.HasPrefix(date, d.Value),
			d.Kind == "after" && date < d.Value,
			d.Kind == "before" && date[:min(len(d.Value), len(date))] >= d.Value:
			return false
		}
	}
	have := map[string]bool{}
	for _, t := range docTags {
		have[strings.ToLower(t)] = true
	}
	for _, t := range q.Tags {
		if !have[t] {
			return false
		}
	}
	for _, t := range q.ExcludeTags {
		if have[t] {
			return false
		}
	}
	for _, p := range q.Phrases {
		if !has(p) {
			return false
		}
	}
	for _, p := range q.ExcludePhrases {
		if has(p) {
			return false
		}
	}
	return true
}

// HoldsExcluded: tokens hold a word the query excludes (-word). Apart from
// Accepts, since an index can answer it without the tokens (DocsWith).
func (q *Query) HoldsExcluded(tokens []string) bool {
	for _, t := range tokens {
		if slices.Contains(q.ExcludeStems, t) {
			return true
		}
	}
	return false
}

// ---- BM25 ----

// posting: 32 bits are plenty, and a big wiki holds millions of these.
type posting struct{ doc, tf int32 }

// store: the documents an Index was built over, as the Index reads them:
// maps in memory (NewIndexOf) or a file (OpenIndex). Only read, by any number
// of searches at once.
type store interface {
	docCount() int
	tokenCount() int
	docLen(doc int) int
	df(t string) int                    // how many documents hold t (0: none)
	postings(t string) []posting        // the documents holding t, in order; not to be changed
	terms(from string) iter.Seq[string] // the terms from `from` on, sorted
	all() iter.Seq2[string, []posting]  // every term and its postings, not to be kept past the next
}

// mapStore: a store in memory.
type mapStore struct {
	total  int
	lens   []int
	byTerm map[string][]posting
	vocab  []string // sorted
}

func (m *mapStore) docCount() int               { return len(m.lens) }
func (m *mapStore) tokenCount() int             { return m.total }
func (m *mapStore) docLen(doc int) int          { return m.lens[doc] }
func (m *mapStore) df(t string) int             { return len(m.byTerm[t]) }
func (m *mapStore) postings(t string) []posting { return m.byTerm[t] }

func (m *mapStore) terms(from string) iter.Seq[string] {
	return slices.Values(m.vocab[sort.SearchStrings(m.vocab, from):])
}

func (m *mapStore) all() iter.Seq2[string, []posting] {
	return func(yield func(string, []posting) bool) {
		for _, t := range m.vocab {
			if !yield(t, m.byTerm[t]) {
				return
			}
		}
	}
}

// Index is search.SearchIndex over documents' tokens.
type Index struct {
	base  store
	n     int // the base's documents
	avgdl float64
	over  *overlay // changes to the documents, searched along with them (View)
}

// overlay: changes to an Index's documents that leave it as it is (View):
// some of its documents left out (rm), others added after them in layers,
// each layer's numbered on from the one before (the first from ix.n). A
// search with them uses the statistics of the documents that are left, as
// if one index were built over those alone; the Index itself, which others
// search, never learns of them. It holds no document's words itself: what
// is left out and each layer are made once and shared by every overlay that
// names them, so an overlay costs a few words, whatever the index's size.
type overlay struct {
	rm       *Removed  // the Index's documents left out (nil: none)
	layers   []LayerAt // the documents added
	first    []int     // each layer's first document's number
	end      int       // one past the last document's number
	n, total int       // the documents left, their tokens
	pos      []int32   // each of the Index's documents' place in the order ties go by (nil: its number)
}

// Layer: documents to search along with an Index's (View), numbered among
// themselves from 0: their lengths and what they hold. Made once from their
// tokens and only read after, so any number of views, of any number of
// viewers, share one: a group's pages are one layer for all its members.
type Layer struct {
	lens     []int
	total    int
	postings map[string][]posting
	vocab    []string // sorted

	mu     sync.Mutex
	newFor newKey   // which Index, and what is left out of it, newTo is for
	newTo  []string // the terms no document left of that Index holds
}

type newKey struct {
	base store
	rm   *Removed
}

// LayerAt: a layer in a view, with each of its documents' place in the
// order ties go by (nil: its number).
type LayerAt struct {
	Layer *Layer
	Pos   []int32
}

// NewLayer: the layer of n documents, asking for each one's tokens in turn.
func NewLayer(n int, tokensOf func(i int) []string) *Layer {
	l := &Layer{postings: map[string][]posting{}, lens: make([]int, 0, n)}
	for i := range n {
		toks := tokensOf(i)
		tf, order := map[string]int{}, []string{}
		for _, t := range toks {
			if tf[t] == 0 {
				order = append(order, t)
			}
			tf[t]++
		}
		l.lens = append(l.lens, len(toks))
		l.total += len(toks)
		for _, t := range order {
			l.postings[t] = append(l.postings[t], posting{int32(i), int32(tf[t])})
		}
	}
	l.vocab = slices.Sorted(maps.Keys(l.postings))
	return l
}

// Len: how many documents the layer has.
func (l *Layer) Len() int { return len(l.lens) }

// newTerms: the layer's terms that no document left of the index with base
// (without rm) holds: what typo matching looks through beside the index's
// own. Worked out once for an index and what is left out of it.
func (l *Layer) newTerms(base store, rm *Removed) []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	if key := (newKey{base, rm}); l.newFor != key {
		var out []string
		for _, t := range l.vocab {
			if base.df(t) == 0 || rm != nil && rm.gone[t] {
				out = append(out, t)
			}
		}
		l.newFor, l.newTo = key, out
	}
	return l.newTo
}

// Removed: documents of an Index left out of a search (View): which, and
// what that takes from the index's statistics. Made once (Without) and only
// read after.
type Removed struct {
	dead   []bool          // by the document's number
	df     map[string]int  // for each term, how many of them hold it
	gone   map[string]bool // terms no document left holds
	count  int
	tokens int // their lengths together
}

// Without: the documents dead names, as what a view of ix leaves out. Nil
// for none. One pass over all of ix's postings (the tokens aren't kept),
// once per change, not per search or viewer.
func (ix *Index) Without(dead map[int]bool) *Removed {
	if len(dead) == 0 {
		return nil
	}
	rm := &Removed{dead: make([]bool, ix.n), df: map[string]int{}, gone: map[string]bool{}, count: len(dead)}
	for id := range dead {
		rm.dead[id] = true
		rm.tokens += ix.base.docLen(id)
	}
	for t, ps := range ix.base.all() {
		n := 0
		for _, p := range ps {
			if rm.dead[p.doc] {
				n++
			}
		}
		if n > 0 {
			rm.df[t] = n
			if n == len(ps) {
				rm.gone[t] = true
			}
		}
	}
	return rm
}

// idf: a term's weight, from how many of n documents hold it.
func idf(n, df int) float64 {
	return math.Log(1 + (float64(n)-float64(df)+0.5)/(float64(df)+0.5))
}

func newIndex(s store) *Index {
	ix := &Index{base: s, n: s.docCount()}
	if ix.n > 0 {
		ix.avgdl = float64(s.tokenCount()) / float64(ix.n)
	}
	return ix
}

func NewIndex(tokens [][]string) *Index {
	return NewIndexOf(len(tokens), func(i int) []string { return tokens[i] })
}

// NewIndexOf: the index of n documents, asking for each one's tokens in turn
// and keeping none of them: they'd be many times what the index holds.
func NewIndexOf(n int, tokensOf func(i int) []string) *Index {
	l := NewLayer(n, tokensOf)
	return newIndex(&mapStore{total: l.total, lens: l.lens, byTerm: l.postings, vocab: l.vocab})
}

// WithExtra: ix with more documents, numbered after its own.
func (ix *Index) WithExtra(tokens [][]string) *Index { return ix.With(nil, tokens, nil) }

// With: ix without the documents dead names and with the extra ones (their
// tokens), numbered from ix.n, searched as if one index were built over the
// documents left: the same scores, the same hits. pos is each document's
// place among them (by number, the extra ones after ix's), which equal
// scores are ordered by; nil: the number. View, for one layer made here.
func (ix *Index) With(dead map[int]bool, extra [][]string, pos []int32) *Index {
	var layers []LayerAt
	var basePos []int32
	if pos != nil {
		basePos = pos[:ix.n]
	}
	if len(extra) > 0 {
		at := LayerAt{Layer: NewLayer(len(extra), func(i int) []string { return extra[i] })}
		if pos != nil {
			at.Pos = pos[ix.n:]
		}
		layers = []LayerAt{at}
	}
	return ix.View(ix.Without(dead), basePos, layers)
}

// View: ix (an index as it was built or opened, not itself a view) without
// the documents rm leaves out and with the layers' documents, the first
// layer's numbered from ix.n and each next one's on from there, searched as
// if one index were built over the documents left: the same scores, the
// same hits. pos is each of ix's documents' place in the order equal scores
// go by, and a layer's Pos its documents' (nil: the number). ix, rm and the
// layers are not changed, only read: a view is made per search where the
// viewers differ, and costs nothing that grows with the index.
func (ix *Index) View(rm *Removed, pos []int32, layers []LayerAt) *Index {
	if rm == nil && len(layers) == 0 && pos == nil {
		return ix
	}
	o := &overlay{rm: rm, layers: layers, pos: pos, n: ix.n, total: ix.base.tokenCount(), end: ix.n}
	if rm != nil {
		o.n, o.total = o.n-rm.count, o.total-rm.tokens
	}
	for _, at := range layers {
		o.first = append(o.first, o.end)
		o.end += at.Layer.Len()
		o.n += at.Layer.Len()
		o.total += at.Layer.total
	}
	cp := *ix
	cp.over = o
	cp.avgdl = 0
	if o.n > 0 {
		cp.avgdl = float64(o.total) / float64(o.n)
	}
	return &cp
}

// layerOf: which layer the document numbered doc (not one of the Index's
// own) is in, and its number there.
func (o *overlay) layerOf(doc int) (LayerAt, int) {
	k := len(o.first) - 1
	for k > 0 && doc < o.first[k] {
		k--
	}
	return o.layers[k], doc - o.first[k]
}

// isDead: the Index's document doc is left out.
func (o *overlay) isDead(doc int) bool { return o != nil && o.rm != nil && o.rm.dead[doc] }

// DocsWith: the documents holding the term, those With left out too, which no
// search reaches.
func (ix *Index) DocsWith(t string) []int {
	var docs []int
	for _, p := range ix.base.postings(t) {
		docs = append(docs, int(p.doc))
	}
	if ix.over != nil {
		for k, at := range ix.over.layers {
			for _, p := range at.Layer.postings[t] {
				docs = append(docs, ix.over.first[k]+int(p.doc))
			}
		}
	}
	return docs
}

// df: how many documents hold the term.
func (ix *Index) df(t string) int {
	df := ix.base.df(t)
	if o := ix.over; o != nil {
		if o.rm != nil {
			df -= o.rm.df[t]
		}
		for _, at := range o.layers {
			df += len(at.Layer.postings[t])
		}
	}
	return df
}

// idfOf: a term's weight, from its document frequency among the documents
// the search is over.
func (ix *Index) idfOf(t string) float64 {
	if ix.over == nil {
		return idf(ix.n, ix.base.df(t))
	}
	return idf(ix.over.n, ix.df(t))
}

func (ix *Index) hasTerm(t string) bool { return ix.df(t) > 0 }

func (ix *Index) docLen(doc int) int {
	if doc < ix.n {
		return ix.base.docLen(doc)
	}
	at, i := ix.over.layerOf(doc)
	return at.Layer.lens[i]
}

// place: where the document comes in the order equal scores go by.
func (ix *Index) place(doc int) int {
	switch o := ix.over; {
	case o == nil:
	case doc < ix.n:
		if o.pos != nil {
			return int(o.pos[doc])
		}
	default:
		if at, i := o.layerOf(doc); at.Pos != nil {
			return int(at.Pos[i])
		}
	}
	return doc
}

// before: a ranks ahead of b: the higher score, else the document that comes
// first (its place in the order, for an index with changes, else its number).
func (ix *Index) before(a, b Hit) bool {
	if a.Score != b.Score {
		return a.Score > b.Score
	}
	return ix.place(a.Doc) < ix.place(b.Doc)
}

type weighted struct {
	term   string
	weight float64
}

// fuzzyTerms: every term a document left holds, each once, for typo matching.
func (ix *Index) fuzzyTerms() iter.Seq[string] {
	o := ix.over
	if o == nil {
		return ix.base.terms("")
	}
	return func(yield func(string) bool) {
		for t := range ix.base.terms("") {
			if (o.rm == nil || !o.rm.gone[t]) && !yield(t) {
				return
			}
		}
		for k, at := range o.layers {
		terms:
			for _, t := range at.Layer.newTerms(ix.base, o.rm) {
				for _, earlier := range o.layers[:k] {
					if _, has := earlier.Layer.postings[t]; has {
						continue terms // said already
					}
				}
				if !yield(t) {
					return
				}
			}
		}
	}
}

// expand: query tokens as (term, weight): exact 1.0, prefix 0.8, and when
// neither hits, close typos 0.5; the best weight per term, in the order
// terms were first found.
func (ix *Index) expand(qTokens []string) []weighted {
	var out []weighted
	at := map[string]int{}
	bump := func(term string, w float64) {
		if i, ok := at[term]; ok {
			if w > out[i].weight {
				out[i].weight = w
			}
			return
		}
		at[term] = len(out)
		out = append(out, weighted{term, w})
	}
	for _, t := range qTokens {
		matched := false
		if ix.hasTerm(t) {
			bump(t, 1.0)
			matched = true
		}
		if RuneLen(t) >= 3 {
			// The words starting with it, the index's and each layer's, in
			// the order of one index over them all.
			var found []string
			starting := func(terms iter.Seq[string]) {
				for v := range terms {
					if !strings.HasPrefix(v, t) {
						return
					}
					if v != t {
						found = append(found, v)
					}
				}
			}
			starting(ix.base.terms(t))
			if ix.over != nil && len(ix.over.layers) > 0 {
				for _, at := range ix.over.layers {
					starting(slices.Values(at.Layer.vocab[sort.SearchStrings(at.Layer.vocab, t):]))
				}
				sort.Strings(found)
				found = slices.Compact(found)
			}
			for _, v := range found {
				if ix.hasTerm(v) {
					bump(v, prefixWeight)
					matched = true
				}
			}
		}
		if !matched && RuneLen(t) >= 4 {
			for _, c := range closeMatchesIn(t, ix.fuzzyTerms(), 3, 0.8) {
				bump(c, fuzzyWeight)
			}
		}
	}
	return out
}

type Hit struct {
	Doc   int     `json:"doc"`
	Score float64 `json:"score"`
}

// Search: the best `limit` documents for the query, by BM25.
func (ix *Index) Search(query string, limit int) []Hit { return ix.SearchWhere(query, limit, nil) }

// topHits: the best limit hits, in order. For a few, one pass keeping a
// short sorted list instead of sorting every hit.
func (ix *Index) topHits(hits []Hit, limit int) []Hit {
	if limit >= len(hits) || limit > 64 {
		sort.Slice(hits, func(x, y int) bool { return ix.before(hits[x], hits[y]) })
		return hits[:min(limit, len(hits))]
	}
	top := make([]Hit, 0, limit)
	for _, h := range hits {
		if len(top) == limit && !ix.before(h, top[limit-1]) {
			continue
		}
		i := sort.Search(len(top), func(i int) bool { return ix.before(h, top[i]) })
		if len(top) < limit {
			top = append(top, Hit{})
		}
		copy(top[i+1:], top[i:])
		top[i] = h
	}
	return top
}

// SearchWhere: Search among the documents accept is true for (nil: all). A
// document is asked once, and one that is refused is not scored: the same
// hits and scores as filtering Search's list, without ranking and sorting
// what would be thrown away.
func (ix *Index) SearchWhere(query string, limit int, accept func(doc int) bool) []Hit {
	var qTokens []string
	for _, w := range queryWords(query) {
		qTokens = append(qTokens, stem(w))
	}
	if len(qTokens) == 0 || ix.docCount() == 0 {
		return nil
	}
	var asked []int8 // per document: 0 not asked yet, 1 accepted, 2 refused
	if accept != nil {
		asked = make([]int8, ix.numbers())
	}
	scores := map[int]float64{}
	for _, tw := range ix.expand(qTokens) {
		idf := ix.idfOf(tw.term)
		// first: the number of the postings' document 0 (a layer's; the
		// index's own, which may be left out, are numbered from 0).
		score := func(postings []posting, first int) {
			for _, p := range postings {
				doc := first + int(p.doc)
				if first == 0 && ix.over.isDead(doc) {
					continue
				}
				if accept != nil {
					if asked[doc] == 0 {
						asked[doc] = 2
						if accept(doc) {
							asked[doc] = 1
						}
					}
					if asked[doc] == 2 {
						continue
					}
				}
				f := float64(p.tf)
				denom := f + bm25K1*(1-bm25B+bm25B*float64(ix.docLen(doc))/ix.avgdl)
				scores[doc] += tw.weight * idf * (f * (bm25K1 + 1) / denom)
			}
		}
		score(ix.base.postings(tw.term), 0)
		if ix.over != nil {
			for k, at := range ix.over.layers {
				score(at.Layer.postings[tw.term], ix.over.first[k])
			}
		}
	}
	hits := make([]Hit, 0, len(scores))
	for i, s := range scores {
		if s > 0 {
			hits = append(hits, Hit{i, s})
		}
	}
	if len(hits) == 0 {
		return nil
	}
	return ix.topHits(hits, limit)
}

// HaveAll: which of docs hold every word of the query, each word by its stem
// or as the start of a word, as Search finds it. A close typo, which Search
// falls back on, doesn't count: "Ferien" isn't in a doc that says "serien".
// Stopwords don't count, as in Search. Nil for a query without words.
func (ix *Index) HaveAll(query string, docs []int) map[int]bool {
	var tokens []string
	for _, w := range queryWords(query) {
		if t := stem(w); !slices.Contains(tokens, t) {
			tokens = append(tokens, t)
		}
	}
	if len(tokens) == 0 || ix.docCount() == 0 {
		return nil
	}
	// Per doc, how many of the words so far it holds: it stays in the running
	// only while that is all of them.
	found := make(map[int]int, len(docs))
	for _, d := range docs {
		found[d] = 0
	}
	for i, t := range tokens {
		mark := func(postings []posting, first int) {
			for _, p := range postings {
				if n, ok := found[first+int(p.doc)]; ok && n == i {
					found[first+int(p.doc)] = i + 1
				}
			}
		}
		for _, tw := range ix.expand([]string{t}) {
			if tw.weight <= fuzzyWeight { // a typo's guess, not the word
				continue
			}
			mark(ix.base.postings(tw.term), 0)
			if ix.over != nil {
				for k, at := range ix.over.layers {
					mark(at.Layer.postings[tw.term], ix.over.first[k])
				}
			}
		}
	}
	all := map[int]bool{}
	for d, n := range found {
		if n == len(tokens) {
			all[d] = true
		}
	}
	return all
}

// numbers: one past the highest number a document has.
func (ix *Index) numbers() int {
	if ix.over != nil {
		return ix.over.end
	}
	return ix.n
}

// docCount: how many documents a search is over.
func (ix *Index) docCount() int {
	if ix.over != nil {
		return ix.over.n
	}
	return ix.n
}

// ---- matched words, excerpts ----

type analyzed struct {
	spans    []span
	folded   []string
	stems    []string
	distinct []string
	byLen    map[int][]string
}

// analyzeCache keeps analyze's answers: the same page or section text is
// matched against query after query, and splitting and stemming it was most
// of a search's time. Emptied when full (search._analyze's lru_cache).
var (
	analyzeMu    sync.Mutex
	analyzeCache = map[string]*analyzed{}
)

const analyzeCacheMax = 4096

func analyze(text string) *analyzed {
	analyzeMu.Lock()
	a := analyzeCache[text]
	analyzeMu.Unlock()
	if a != nil {
		return a
	}
	a = analyzeText(text)
	analyzeMu.Lock()
	if len(analyzeCache) >= analyzeCacheMax {
		clear(analyzeCache)
	}
	analyzeCache[text] = a
	analyzeMu.Unlock()
	return a
}

// analyzeText: a text's words (rune spans, folded, stems), its distinct
// stems sorted (prefix matches by bisection) and by length (typo candidates).
func analyzeText(text string) *analyzed {
	ws, spans := words(text)
	a := &analyzed{spans: spans, byLen: map[int][]string{}}
	seen := map[string]bool{}
	for _, w := range ws {
		f := fold(w)
		s := stem(f)
		a.folded, a.stems = append(a.folded, f), append(a.stems, s)
		if !seen[s] {
			seen[s] = true
			a.distinct = append(a.distinct, s)
		}
	}
	sort.Strings(a.distinct)
	for _, s := range a.distinct {
		n := RuneLen(s)
		a.byLen[n] = append(a.byLen[n], s)
	}
	return a
}

// matchSpans: the words of text the query matched as the index matches
// them (stem, prefix, else typo); a query of several words found as a
// phrase is one span, the only one.
func matchSpans(text, query string) []span {
	a := analyze(text)
	qw, _ := words(query)
	for i := range qw {
		qw[i] = fold(qw[i])
	}
	if len(a.spans) == 0 || len(qw) == 0 {
		return nil
	}
	if n := len(qw); n > 1 {
		for i := 0; i+n <= len(a.folded); i++ {
			all := true
			for k := 0; k < n && all; k++ {
				all = a.folded[i+k] == qw[k]
			}
			if all {
				return []span{{a.spans[i].start, a.spans[i+n-1].end}}
			}
		}
	}
	terms := map[string]bool{}
	for _, w := range queryWords(query) {
		terms[stem(w)] = true
	}
	hits := map[string]bool{}
	for t := range terms {
		i := sort.SearchStrings(a.distinct, t)
		if i < len(a.distinct) && a.distinct[i] == t {
			hits[t] = true
		}
		if RuneLen(t) >= 3 {
			for ; i < len(a.distinct) && strings.HasPrefix(a.distinct[i], t); i++ {
				hits[a.distinct[i]] = true
			}
		}
	}
	if len(hits) == 0 {
		for t := range terms {
			n := RuneLen(t)
			if n < 4 {
				continue
			}
			var cands []string
			for l := (2*n + 2) / 3; l <= 3*n/2; l++ {
				cands = append(cands, a.byLen[l]...)
			}
			for _, c := range CloseMatches(t, cands, max(len(cands), 1), 0.8) {
				hits[c] = true
			}
		}
	}
	var out []span
	for i, s := range a.stems {
		if hits[s] {
			out = append(out, a.spans[i])
		}
	}
	return out
}

// MatchedWords: the distinct words (or the one phrase) of text the query
// matched, as written, at most limit.
func MatchedWords(text, query string, limit int) []string {
	r := []rune(text)
	var out []string
	seen := map[string]bool{}
	for _, s := range matchSpans(text, query) {
		w := strings.Join(strings.Fields(sliceRunes(r, s.start, s.end)), " ")
		if k := casefold(w); !seen[k] {
			seen[k] = true
			out = append(out, w)
		}
		if len(seen) >= limit {
			break
		}
	}
	return out
}

// Matches: whether text has a word the query matches.
func Matches(text, query string) bool { return len(matchSpans(text, query)) > 0 }

// Excerpt: escaped text around the first match, every match in it in <mark>;
// the text's start when nothing matches.
func Excerpt(text, query string) string {
	const before, after = 60, 90
	r := []rune(text)
	spans := matchSpans(text, query)
	if len(spans) == 0 {
		head := strings.TrimSpace(strings.ReplaceAll(sliceRunes(r, 0, min(150, len(r))), "\n", " "))
		if len(r) > 150 {
			return html.EscapeString(head) + "…"
		}
		return html.EscapeString(head)
	}
	start := max(0, spans[0].start-before)
	end := min(len(r), spans[0].end+after)
	var out strings.Builder
	pos := start
	for _, s := range spans {
		if s.start < start || s.end > end {
			continue
		}
		out.WriteString(html.EscapeString(sliceRunes(r, pos, s.start)))
		out.WriteString("<mark>" + html.EscapeString(sliceRunes(r, s.start, s.end)) + "</mark>")
		pos = s.end
	}
	out.WriteString(html.EscapeString(sliceRunes(r, pos, end)))
	snippet := strings.ReplaceAll(out.String(), "\n", " ")
	if start > 0 {
		snippet = "…" + snippet
	}
	if end < len(r) {
		snippet += "…"
	}
	return snippet
}

// Fuse: reciprocal rank fusion of ranked lists (best first); an item high
// in several lists beats one high in a single list. Ties keep the order
// items were first seen in, so the first list wins them.
func Fuse[T comparable](rankings [][]T) []T {
	const rrfK = 60
	scores := map[T]float64{}
	var order []T
	for _, ranking := range rankings {
		for rank, k := range ranking {
			if _, ok := scores[k]; !ok {
				order = append(order, k)
			}
			scores[k] += 1.0 / float64(rrfK+rank+1)
		}
	}
	sort.SliceStable(order, func(x, y int) bool { return scores[order[x]] > scores[order[y]] })
	return order
}
