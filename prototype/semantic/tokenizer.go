// The tokenizer of Leaf's embedding model (jina-embeddings-v2-base-de) in
// Go: what Hugging Face's `tokenizers` does with its tokenizer.json, which
// is a byte-level BPE (GPT-2's), no normalizer, RoBERTa's <s> … </s>
// around the text, at most 512 tokens (embed.MAX_TOKENS). Only what this
// file uses is here; Tokenizer refuses a tokenizer.json that wants more.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"unicode"
	"unicode/utf8"
)

type addedToken struct {
	ID      int    `json:"id"`
	Content string `json:"content"`
	Lstrip  bool   `json:"lstrip"`
	Rstrip  bool   `json:"rstrip"`
	Single  bool   `json:"single_word"`
}

type tokenizerJSON struct {
	AddedTokens  []addedToken    `json:"added_tokens"`
	Normalizer   json.RawMessage `json:"normalizer"`
	PreTokenizer struct {
		Type           string `json:"type"`
		AddPrefixSpace bool   `json:"add_prefix_space"`
		UseRegex       bool   `json:"use_regex"`
	} `json:"pre_tokenizer"`
	PostProcessor struct {
		Type string `json:"type"`
		Sep  [2]any `json:"sep"`
		Cls  [2]any `json:"cls"`
	} `json:"post_processor"`
	Model struct {
		Type         string         `json:"type"`
		Vocab        map[string]int `json:"vocab"`
		Merges       []any          `json:"merges"` // "a b", or ["a", "b"] in newer files
		Dropout      *float64       `json:"dropout"`
		Unk          *string        `json:"unk_token"`
		ByteFallback bool           `json:"byte_fallback"`
		Prefix       string         `json:"continuing_subword_prefix"`
		Suffix       string         `json:"end_of_word_suffix"`
	} `json:"model"`
}

// Tokenizer is a loaded tokenizer.json.
type Tokenizer struct {
	vocab  map[string]int
	ranks  map[[2]string]int
	added  []addedToken
	cls    int
	sep    int
	maxLen int
	cache  map[string][]int
}

func LoadTokenizer(path string, maxLen int) (*Tokenizer, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var tj tokenizerJSON
	if err := json.Unmarshal(raw, &tj); err != nil {
		return nil, err
	}
	m, pre, post := tj.Model, tj.PreTokenizer, tj.PostProcessor
	if string(tj.Normalizer) != "null" || pre.Type != "ByteLevel" || pre.AddPrefixSpace || !pre.UseRegex ||
		post.Type != "RobertaProcessing" || m.Type != "BPE" || m.Dropout != nil || m.Unk != nil ||
		m.ByteFallback || m.Prefix != "" || m.Suffix != "" {
		return nil, fmt.Errorf("%s: a tokenizer this port doesn't do", path)
	}
	for _, a := range tj.AddedTokens {
		if a.Rstrip || a.Single {
			return nil, fmt.Errorf("%s: added token %q's options", path, a.Content)
		}
	}
	t := &Tokenizer{vocab: m.Vocab, ranks: map[[2]string]int{}, added: tj.AddedTokens,
		cls: int(post.Cls[1].(float64)), sep: int(post.Sep[1].(float64)), maxLen: maxLen, cache: map[string][]int{}}
	for i, mg := range m.Merges {
		var pair [2]string
		switch mg := mg.(type) {
		case string:
			a, b, _ := strings.Cut(mg, " ")
			pair = [2]string{a, b}
		case []any:
			pair = [2]string{mg[0].(string), mg[1].(string)}
		}
		if _, ok := t.ranks[pair]; !ok {
			t.ranks[pair] = i
		}
	}
	return t, nil
}

// Encode: the ids of <s> text </s>, the text cut to fit maxLen.
func (t *Tokenizer) Encode(text string) []int {
	ids := []int{t.cls}
	for _, seg := range t.splitAdded(text) {
		if seg.id >= 0 {
			ids = append(ids, seg.id)
			continue
		}
		for _, piece := range preTokenize(seg.text) {
			ids = append(ids, t.bpe(piece)...)
		}
	}
	if len(ids) > t.maxLen-1 {
		ids = ids[:t.maxLen-1]
	}
	return append(ids, t.sep)
}

type segment struct {
	text string
	id   int // an added token's, or -1 for text
}

// splitAdded: the text split at its added (special) tokens, leftmost and
// then longest first; one with lstrip takes the whitespace before it.
func (t *Tokenizer) splitAdded(text string) []segment {
	var out []segment
	start := 0
	for i := 0; i < len(text); {
		var hit *addedToken
		for k := range t.added {
			a := &t.added[k]
			if strings.HasPrefix(text[i:], a.Content) && (hit == nil || len(a.Content) > len(hit.Content)) {
				hit = a
			}
		}
		if hit == nil {
			i++
			continue
		}
		end := i
		if hit.Lstrip {
			end = len(strings.TrimRightFunc(text[start:i], unicode.IsSpace)) + start
		}
		if end > start {
			out = append(out, segment{text[start:end], -1})
		}
		out = append(out, segment{"", hit.ID})
		i += len(hit.Content)
		start = i
	}
	if start < len(text) {
		out = append(out, segment{text[start:], -1})
	}
	return out
}

// preTokenize is ByteLevel's split, GPT-2's pattern
// 's|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+
// (a lookahead RE2 lacks, so a scanner), each piece's bytes as GPT-2's
// printable characters.
func preTokenize(text string) []string {
	var pieces []string
	for i := 0; i < len(text); {
		n := gpt2Piece(text[i:])
		pieces = append(pieces, byteLevel(text[i:i+n]))
		i += n
	}
	return pieces
}

func isOther(r rune) bool { return !unicode.IsSpace(r) && !unicode.IsLetter(r) && !unicode.IsNumber(r) }

// run: the length of the runes at the start of s that are in class.
func run(s string, class func(rune) bool) int {
	n := 0
	for n < len(s) {
		r, size := utf8.DecodeRuneInString(s[n:])
		if !class(r) {
			break
		}
		n += size
	}
	return n
}

// gpt2Piece: the length of the pattern's match at the start of s.
func gpt2Piece(s string) int {
	if s[0] == '\'' {
		for _, c := range []string{"s", "t", "re", "ve", "m", "ll", "d"} {
			if strings.HasPrefix(s[1:], c) {
				return 1 + len(c)
			}
		}
	}
	for _, class := range []func(rune) bool{unicode.IsLetter, unicode.IsNumber, isOther} {
		if s[0] == ' ' {
			if n := run(s[1:], class); n > 0 {
				return 1 + n
			}
		}
		if n := run(s, class); n > 0 {
			return n
		}
	}
	// Whitespace: \s+(?!\S) takes the run but its last character when a
	// non-space follows (that one starts the next piece), else \s+ all.
	n := run(s, unicode.IsSpace)
	if n < len(s) {
		_, last := utf8.DecodeLastRuneInString(s[:n])
		if n-last > 0 {
			return n - last
		}
	}
	return n
}

// byteToRune is GPT-2's bytes_to_unicode: printable bytes stand for
// themselves, the others for 256 and up.
var byteToRune = func() [256]rune {
	var m [256]rune
	n := 0
	for b := 0; b < 256; b++ {
		if b >= '!' && b <= '~' || b >= 0xA1 && b <= 0xAC || b >= 0xAE && b <= 0xFF {
			m[b] = rune(b)
		} else {
			m[b] = rune(256 + n)
			n++
		}
	}
	return m
}()

func byteLevel(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		b.WriteRune(byteToRune[s[i]])
	}
	return b.String()
}

// bpe: a piece's ids, merging the adjacent pair of lowest rank (leftmost
// first) until none merges.
func (t *Tokenizer) bpe(piece string) []int {
	if ids, ok := t.cache[piece]; ok {
		return ids
	}
	var syms []string
	for _, r := range piece {
		syms = append(syms, string(r))
	}
	for len(syms) > 1 {
		best, at := -1, -1
		for i := 0; i+1 < len(syms); i++ {
			if r, ok := t.ranks[[2]string{syms[i], syms[i+1]}]; ok && (best < 0 || r < best) {
				best, at = r, i
			}
		}
		if at < 0 {
			break
		}
		merged := syms[at] + syms[at+1]
		syms = append(syms[:at], append([]string{merged}, syms[at+2:]...)...)
	}
	ids := make([]int, 0, len(syms))
	for _, s := range syms {
		if id, ok := t.vocab[s]; ok {
			ids = append(ids, id)
		}
	}
	t.cache[piece] = ids
	return ids
}
