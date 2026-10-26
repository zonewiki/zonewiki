// Lexical search (src/search.py): folding, stemming, the query syntax and
// its filters, BM25 with prefix and typo expansion, the words a query
// matched, excerpts. prototype/search/compare.py checked it against Python
// on a wiki: the same results on 11,094 queries.
//
// Python's text model is code points, Go's bytes: offsets into a text
// (spans, excerpt windows) are counted in runes here.
package main

import (
	"math"
	"regexp"
	"sort"
	"strings"
	"sync"
	"unicode"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

// titleWeight: title tokens go into a document this many extra times.
const titleWeight = 3

const (
	bm25K1       = 1.5
	bm25B        = 0.75
	prefixWeight = 0.8
	fuzzyWeight  = 0.5
)

// ---- Python's notion of characters ----

// pyWord is Python's [^\W_]: a letter or a number, any script.
func pyWord(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) }

func casefold(s string) string { return cases.Fold().String(s) }

// squash: folded text with runs of whitespace as one space (phrase checks).
func squash(text string) string { return strings.Join(pySplit(fold(text)), " ") }

func runeLen(s string) int { return len([]rune(s)) }

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
		if pyWord(r) {
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

// stripLinkTargets: markdown with link and image targets off, link text kept.
func stripLinkTargets(text string) string {
	return linkDefRE.ReplaceAllString(linkTargetRE.ReplaceAllString(text, "]"), "$1")
}

// tokenize: folded, split into words, stemmed.
func tokenize(text string) []string {
	ws, _ := words(fold(text))
	for i, w := range ws {
		ws[i] = stem(w)
	}
	return ws
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

const pyNonSpace = `[^\s\pZ\x{85}\x{1c}-\x{1f}]`

var (
	queryPartRE  = regexp.MustCompile(`(-?)(?:"([^"]+)"|(` + pyNonSpace + `+))`)
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
			if pyStrip(quoted) == "" {
				continue
			}
			if negative {
				q.ExcludePhrases = append(q.ExcludePhrases, squash(quoted))
			} else {
				q.Phrases = append(q.Phrases, squash(quoted))
				phrases = append(phrases, pyStrip(quoted))
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
			for _, t := range tokenize(plain) {
				excl[t] = true
			}
		} else {
			ws = append(ws, plain)
		}
	}
	q.Text = strings.Join(append(phrases, ws...), " ")
	q.ExcludeStems, q.Tags, q.ExcludeTags = sortedKeys(excl), sortedKeys(tags), sortedKeys(extags)
	return q
}

// Phrase: the query's words as one phrase, when there are two or more.
func (q *Query) Phrase() string {
	if len(pySplit(q.Text)) >= 2 {
		return squash(q.Text)
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
func (q *Query) Accepts(text string, tokens []string, docTags []string, date string) bool {
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
	if len(q.ExcludeStems) > 0 {
		excl := map[string]bool{}
		for _, t := range q.ExcludeStems {
			excl[t] = true
		}
		for _, t := range tokens {
			if excl[t] {
				return false
			}
		}
	}
	if len(q.Phrases)+len(q.ExcludePhrases) > 0 {
		s := squash(text)
		for _, p := range q.Phrases {
			if !strings.Contains(s, p) {
				return false
			}
		}
		for _, p := range q.ExcludePhrases {
			if strings.Contains(s, p) {
				return false
			}
		}
	}
	return true
}

// ---- BM25 ----

type posting struct{ doc, tf int }

// Index is search.SearchIndex over documents' tokens.
type Index struct {
	n        int
	avgdl    float64
	lens     []int
	postings map[string][]posting
	idf      map[string]float64
	vocab    []string
}

func NewIndex(tokens [][]string) *Index {
	ix := &Index{n: len(tokens), postings: map[string][]posting{}, idf: map[string]float64{}}
	df := map[string]int{}
	total := 0
	for i, toks := range tokens {
		tf, order := map[string]int{}, []string{}
		for _, t := range toks {
			if tf[t] == 0 {
				order = append(order, t)
			}
			tf[t]++
		}
		ix.lens = append(ix.lens, len(toks))
		total += len(toks)
		for _, t := range order {
			df[t]++
			ix.postings[t] = append(ix.postings[t], posting{i, tf[t]})
		}
	}
	if ix.n > 0 {
		ix.avgdl = float64(total) / float64(ix.n)
	}
	for t, n := range df {
		ix.idf[t] = math.Log(1 + (float64(ix.n)-float64(n)+0.5)/(float64(n)+0.5))
		ix.vocab = append(ix.vocab, t)
	}
	sort.Strings(ix.vocab)
	return ix
}

type weighted struct {
	term   string
	weight float64
}

// expand: query tokens as (term, weight): exact 1.0, prefix 0.8, and when
// neither hits, close typos 0.5; the best weight per term, in the order
// terms were first found (Python's dict).
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
		if _, ok := ix.idf[t]; ok {
			bump(t, 1.0)
			matched = true
		}
		if runeLen((t)) >= 3 {
			for i := sort.SearchStrings(ix.vocab, t); i < len(ix.vocab) && strings.HasPrefix(ix.vocab[i], t); i++ {
				if ix.vocab[i] != t {
					bump(ix.vocab[i], prefixWeight)
					matched = true
				}
			}
		}
		if !matched && runeLen((t)) >= 4 {
			for _, c := range closeMatches(t, ix.vocab, 3, 0.8) {
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
func (ix *Index) Search(query string, limit int) []Hit {
	var qTokens []string
	for _, w := range queryWords(query) {
		qTokens = append(qTokens, stem(w))
	}
	if len(qTokens) == 0 || ix.n == 0 {
		return nil
	}
	scores := map[int]float64{}
	for _, tw := range ix.expand(qTokens) {
		idf := ix.idf[tw.term]
		for _, p := range ix.postings[tw.term] {
			f := float64(p.tf)
			denom := f + bm25K1*(1-bm25B+bm25B*float64(ix.lens[p.doc])/ix.avgdl)
			scores[p.doc] += tw.weight * idf * (f * (bm25K1 + 1) / denom)
		}
	}
	var hits []Hit
	for i, s := range scores {
		if s > 0 {
			hits = append(hits, Hit{i, s})
		}
	}
	sort.Slice(hits, func(x, y int) bool {
		if hits[x].Score != hits[y].Score {
			return hits[x].Score > hits[y].Score
		}
		return hits[x].Doc < hits[y].Doc
	})
	return hits[:min(limit, len(hits))]
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
		n := runeLen((s))
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
		if runeLen((t)) >= 3 {
			for ; i < len(a.distinct) && strings.HasPrefix(a.distinct[i], t); i++ {
				hits[a.distinct[i]] = true
			}
		}
	}
	if len(hits) == 0 {
		for t := range terms {
			n := runeLen((t))
			if n < 4 {
				continue
			}
			var cands []string
			for l := (2*n + 2) / 3; l <= 3*n/2; l++ {
				cands = append(cands, a.byLen[l]...)
			}
			for _, c := range closeMatches(t, cands, max(len(cands), 1), 0.8) {
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
		w := strings.Join(pySplit(sliceRunes(r, s.start, s.end)), " ")
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
		head := pyStrip(strings.ReplaceAll(sliceRunes(r, 0, min(150, len(r))), "\n", " "))
		if len(r) > 150 {
			return pyEscape(head) + "…"
		}
		return pyEscape(head)
	}
	start := max(0, spans[0].start-before)
	end := min(len(r), spans[0].end+after)
	var out strings.Builder
	pos := start
	for _, s := range spans {
		if s.start < start || s.end > end {
			continue
		}
		out.WriteString(pyEscape(sliceRunes(r, pos, s.start)))
		out.WriteString("<mark>" + pyEscape(sliceRunes(r, s.start, s.end)) + "</mark>")
		pos = s.end
	}
	out.WriteString(pyEscape(sliceRunes(r, pos, end)))
	snippet := strings.ReplaceAll(out.String(), "\n", " ")
	if start > 0 {
		snippet = "…" + snippet
	}
	if end < len(r) {
		snippet += "…"
	}
	return snippet
}

// Fuse: reciprocal rank fusion of ranked lists of keys (best first); ties
// keep the order keys were first seen in.
func Fuse(rankings [][]string) []string {
	const rrfK = 60
	scores := map[string]float64{}
	var order []string
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
