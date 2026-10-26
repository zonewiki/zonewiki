package main

import "sort"

// closeMatches is difflib.get_close_matches(word, possibilities, n, cutoff):
// the possibilities whose SequenceMatcher ratio against word is at least
// cutoff, best first, at most n. Equal scores order the larger string
// first, as Python's nlargest over (score, string) does. (Python also
// checks real_quick_ratio and quick_ratio first; they are upper bounds of
// ratio, so they only save time.)
func closeMatches(word string, possibilities []string, n int, cutoff float64) []string {
	type scored struct {
		score float64
		s     string
	}
	b := []rune(word)
	b2j := buildB2J(b)
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

// buildB2J is SequenceMatcher.__chain_b: where each element of b is, with
// autojunk's "popular" elements (more than 1% of a b of 200 or more) left
// out. There is no isjunk, so nothing is junk: popular elements aren't
// matched from, but may extend a match (find_longest_match).
func buildB2J(b []rune) map[rune][]int {
	b2j := map[rune][]int{}
	for j, r := range b {
		b2j[r] = append(b2j[r], j)
	}
	popular := map[rune]bool{}
	if n := len(b); n >= 200 {
		ntest := n/100 + 1
		for r, idxs := range b2j {
			if len(idxs) > ntest {
				popular[r] = true
			}
		}
		for r := range popular {
			delete(b2j, r)
		}
	}
	return b2j
}

// ratio is SequenceMatcher(None, a, b).ratio(): 2·M / (len a + len b), M
// the size of the matching blocks.
func ratio(a, b []rune, b2j map[rune][]int) float64 {
	total := len(a) + len(b)
	if total == 0 {
		return 1.0
	}
	matches := 0
	type job struct{ alo, ahi, blo, bhi int }
	queue := []job{{0, len(a), 0, len(b)}}
	for len(queue) > 0 {
		q := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		i, j, k := longestMatch(a, b, b2j, q.alo, q.ahi, q.blo, q.bhi)
		if k > 0 {
			matches += k
			if q.alo < i && q.blo < j {
				queue = append(queue, job{q.alo, i, q.blo, j})
			}
			if i+k < q.ahi && j+k < q.bhi {
				queue = append(queue, job{i + k, q.ahi, j + k, q.bhi})
			}
		}
	}
	return 2.0 * float64(matches) / float64(total)
}

// longestMatch is SequenceMatcher.find_longest_match without junk: the
// best match from b2j, then extended by equal elements on both sides.
func longestMatch(a, b []rune, b2j map[rune][]int, alo, ahi, blo, bhi int) (int, int, int) {
	besti, bestj, bestsize := alo, blo, 0
	j2len := map[int]int{}
	for i := alo; i < ahi; i++ {
		newj2len := map[int]int{}
		for _, j := range b2j[a[i]] {
			if j < blo {
				continue
			}
			if j >= bhi {
				break
			}
			k := j2len[j-1] + 1
			newj2len[j] = k
			if k > bestsize {
				besti, bestj, bestsize = i-k+1, j-k+1, k
			}
		}
		j2len = newj2len
	}
	for besti > alo && bestj > blo && a[besti-1] == b[bestj-1] {
		besti, bestj, bestsize = besti-1, bestj-1, bestsize+1
	}
	for besti+bestsize < ahi && bestj+bestsize < bhi && a[besti+bestsize] == b[bestj+bestsize] {
		bestsize++
	}
	return besti, bestj, bestsize
}
