package main

import (
	"math/rand"
	"reflect"
	"sort"
	"testing"
)

// closeMatchesNaive: closeMatches without the quick upper bounds.
func closeMatchesNaive(word string, possibilities []string, n int, cutoff float64) []string {
	b := []rune(word)
	b2j := buildB2J(b)
	type scored struct {
		score float64
		s     string
	}
	var result []scored
	for _, x := range possibilities {
		if r := ratio([]rune(x), b, b2j); r >= cutoff {
			result = append(result, scored{r, x})
		}
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].score != result[j].score {
			return result[i].score > result[j].score
		}
		return result[i].s > result[j].s
	})
	out := make([]string, 0, min(n, len(result)))
	for _, r := range result[:min(n, len(result))] {
		out = append(out, r.s)
	}
	return out
}

func TestCloseMatchesQuickBoundsChangeNothing(t *testing.T) {
	r := rand.New(rand.NewSource(7))
	alphabet := []rune("abcdeäöß12")
	word := func() string {
		s := make([]rune, r.Intn(9))
		for i := range s {
			s[i] = alphabet[r.Intn(len(alphabet))]
		}
		return string(s)
	}
	vocab := make([]string, 400)
	for i := range vocab {
		vocab[i] = word()
	}
	for i := 0; i < 300; i++ {
		w := word()
		for _, cutoff := range []float64{0.5, 0.8, 0.95} {
			got, want := closeMatches(w, vocab, 3, cutoff), closeMatchesNaive(w, vocab, 3, cutoff)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("closeMatches(%q, cutoff %v) = %q, want %q", w, cutoff, got, want)
			}
		}
	}
}
