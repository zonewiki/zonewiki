package tokenizer

import (
	"fmt"
	"math/rand"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

// The model's tokenizer.json is not in the repo: ZONE_MODEL_DIR names the
// folder it was downloaded to (embed.go). The ids are Hugging Face's.
func loadTok(t *testing.T) *Tokenizer {
	dir := os.Getenv("ZONE_MODEL_DIR")
	if dir == "" {
		t.Skip("ZONE_MODEL_DIR not set")
	}
	tok, err := Load(dir+"/tokenizer.json", 512)
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

func TestEncode(t *testing.T) {
	tok := loadTok(t)
	if id, ok := tok.ID("<pad>"); !ok || id != 1 {
		t.Errorf("<pad> = %d %v", id, ok)
	}
	for text, want := range map[string][]int{
		"Hello world":      {0, 17964, 1568, 2},
		"it's they're":     {0, 272, 507, 713, 1634, 2},
		"  two  spaces":    {0, 225, 1244, 225, 13319, 2},
		"text <mask> more": {0, 5339, 4, 708, 2},
		"Müller Straße":    {0, 49, 44720, 6779, 2},
		"😀 ok":             {0, 32164, 227, 8192, 2},
		"":                 {0, 2},
		"a\tb\nc":          {0, 69, 202, 70, 203, 71, 2},
	} {
		if got := tok.Encode(text); !reflect.DeepEqual(got, want) {
			t.Errorf("%q: got %v, want %v", text, got, want)
		}
	}
	if got := tok.Encode(strings.Repeat("word ", 699) + "word"); len(got) != 512 || got[511] != 2 {
		t.Errorf("truncation: %d ids, last %d", len(got), got[len(got)-1])
	}
}

// GPT-2's split, which go-huggingface's ByteLevel gets wrong (it splits at
// spaces only).
func TestPreTokenize(t *testing.T) {
	var got []string
	for i, s := 0, "it's  two\t\tx 3.14!? "; i < len(s); {
		n := gpt2Piece(s[i:])
		got = append(got, s[i:i+n])
		i += n
	}
	want := []string{"it", "'s", " ", " two", "\t", "\t", "x", " 3", ".", "14", "!?", " "}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
}

// loopBPE: the merges as the loop before the heap made them, each by a
// look through every pair: what bpe must still give.
func loopBPE(t *Tokenizer, piece string) []string {
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
		syms = append(syms[:at], append([]string{syms[at] + syms[at+1]}, syms[at+2:]...)...)
	}
	return syms
}

// testTok: a tokenizer of letters a–d whose merges join any two of its
// tokens, ranked at random; every token has an id.
func testTok(seed int64) *Tokenizer {
	rng := rand.New(rand.NewSource(seed))
	tok := &Tokenizer{vocab: map[string]int{}, ranks: map[[2]string]int{}, cls: 0, sep: 2, maxLen: 512, cache: map[string][]int{}}
	tokens := []string{"a", "b", "c", "d"}
	for i, a := range tokens {
		tok.vocab[a] = 10 + i
	}
	for rank := 0; rank < 60; rank++ {
		l, r := tokens[rng.Intn(len(tokens))], tokens[rng.Intn(len(tokens))]
		if _, ok := tok.ranks[[2]string{l, r}]; ok || len(l+r) > 6 {
			continue
		}
		tok.ranks[[2]string{l, r}] = rank
		if _, ok := tok.vocab[l+r]; !ok {
			tok.vocab[l+r] = len(tok.vocab) + 10
			tokens = append(tokens, l+r)
		}
	}
	return tok
}

// The heap merges as the loop did, ties to the left included.
func TestBPEMergesAsTheLoopDid(t *testing.T) {
	for seed := int64(1); seed <= 20; seed++ {
		tok := testTok(seed)
		rng := rand.New(rand.NewSource(seed))
		for range 50 {
			b := make([]byte, 1+rng.Intn(40))
			for i := range b {
				b[i] = "abcd"[rng.Intn(4)]
			}
			var want []int
			for _, s := range loopBPE(tok, string(b)) {
				want = append(want, tok.vocab[s])
			}
			if got := tok.bpe(string(b)); !reflect.DeepEqual(got, want) && len(want) > 0 {
				t.Fatalf("seed %d, %q: %v, want %v", seed, b, got, want)
			}
		}
	}
}

// A long word costs n log n, a long text what its first tokens do, and the
// cache doesn't grow with the words it is sent.
func TestEncodeCostsWhatItsTokensDo(t *testing.T) {
	tok := &Tokenizer{vocab: map[string]int{"a": 1, "aa": 3, " ": 4, "b": 5}, ranks: map[[2]string]int{{"a", "a"}: 0},
		cls: 0, sep: 2, maxLen: 512, cache: map[string][]int{}}
	start := time.Now()
	if got := tok.Encode(strings.Repeat("a", 200_000)); len(got) != 512 || got[1] != 3 || got[511] != 2 {
		t.Errorf("a long word: %d ids", len(got))
	}
	if got := tok.Encode(strings.Repeat("ab ", 2_000_000)); len(got) != 512 {
		t.Errorf("a long text: %d ids", len(got))
	}
	if d := time.Since(start); d > 10*time.Second { // the loop took minutes for the word alone
		t.Errorf("took %v", d)
	}
	for i := range cacheMax + 10 {
		tok.Encode(fmt.Sprintf("b%d", i))
	}
	if len(tok.cache) > cacheMax {
		t.Errorf("the cache holds %d pieces", len(tok.cache))
	}
}
