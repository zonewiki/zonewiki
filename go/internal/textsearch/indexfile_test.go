package textsearch

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"math/rand"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

// randomDocs: documents of words that share prefixes and are a typo apart,
// some equal to the one before (ties).
func randomDocs(r *rand.Rand, n int) [][]string {
	words := strings.Fields("apple apples applet banana bananas cherry cherries date fig grape grapes kiwi lemon lime mango melon nectar orange papaya peach pear plum quince raisin müller über straße")
	docs := make([][]string, n)
	for i := range docs {
		if i > 0 && r.Intn(6) == 0 {
			docs[i] = docs[i-1]
			continue
		}
		for range r.Intn(30) {
			docs[i] = append(docs[i], words[r.Intn(len(words))])
		}
	}
	return docs
}

func keyOf(i int) DocKey {
	var k DocKey
	copy(k[:], fmt.Sprintf("doc %d", i))
	return k
}

// textOf: a doc's text in the file, its words with a space between.
func textOf(doc []string) string { return strings.Join(doc, " ") }

func writeOpen(t *testing.T, path string, docs [][]string) *Index {
	t.Helper()
	if err := WriteIndex(path, len(docs), func(i int) IndexDoc {
		return IndexDoc{Tokens: docs[i], Key: keyOf(i), Text: textOf(docs[i]), Meta: []byte(fmt.Sprintf("meta %d", i))}
	}); err != nil {
		t.Fatal(err)
	}
	ix, err := OpenIndex(path)
	if err != nil {
		t.Fatal(err)
	}
	return ix
}

func exactlySame(t *testing.T, label string, got, want []Hit) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s: %d hits, want %d", label, len(got), len(want))
	}
	for i := range got {
		if got[i] != want[i] { // the scores to the bit
			t.Fatalf("%s, hit %d: %v, want %v", label, i, got[i], want[i])
		}
	}
}

var fileQueries = []string{"apple", "banana cherry", "grap", "app", "aple", "bananna", "chery", "pech", "zzzz",
	"kiwi lemon lime", "fig", "quinc raisn", "muller", "strasse", "uber", "a", ""}

// The file and the maps give the same hits and scores, with changes (With)
// too, and the file keeps the docs' keys.
func TestFileIndexIsTheMaps(t *testing.T) {
	r := rand.New(rand.NewSource(5))
	dir := t.TempDir()
	for round := range 40 {
		docs := randomDocs(r, r.Intn(80))
		path := filepath.Join(dir, fmt.Sprintf("r%d.idx", round))
		file, maps := writeOpen(t, path, docs), NewIndex(docs)
		dead := map[int]bool{}
		for i := range docs {
			if r.Intn(4) == 0 {
				dead[i] = true
			}
		}
		extra := randomDocs(r, r.Intn(10))
		pos := make([]int32, len(docs)+len(extra))
		for i, p := range r.Perm(len(pos)) {
			pos[i] = int32(p)
		}
		pairs := [][2]*Index{{file, maps}, {file.With(dead, extra, pos), maps.With(dead, extra, pos)},
			{file.WithExtra(extra), maps.WithExtra(extra)}}
		for k, pair := range pairs {
			for _, q := range fileQueries {
				for _, limit := range []int{3, 1000} {
					exactlySame(t, fmt.Sprintf("round %d, index %d, %q top %d", round, k, q, limit), pair[0].Search(q, limit), pair[1].Search(q, limit))
				}
			}
			for _, w := range []string{"apple", "kiwi", "none"} {
				if a, b := pair[0].DocsWith(w), pair[1].DocsWith(w); !slices.Equal(a, b) {
					t.Fatalf("round %d, index %d: DocsWith(%q) %v, want %v", round, k, w, a, b)
				}
			}
		}
		for i, doc := range docs { // the texts, and none for a doc added after
			for _, sub := range []string{"apple", "pear plum", "e p", textOf(doc), "zz"} {
				if has, known := file.TextHas(i, sub); !known || has != strings.Contains(textOf(doc), sub) {
					t.Fatalf("round %d: doc %d has %q: %v %v", round, i, sub, has, known)
				}
			}
		}
		if _, known := file.WithExtra(extra).TextHas(len(docs), "apple"); known {
			t.Fatalf("round %d: an added doc has a text in the file", round)
		}
		if _, known := maps.TextHas(0, "apple"); known {
			t.Fatalf("round %d: an index in memory has texts", round)
		}
		keys := file.Keys()
		if len(keys) != len(docs) {
			t.Fatalf("round %d: %d keys for %d docs", round, len(keys), len(docs))
		}
		for i, k := range keys {
			if k != keyOf(i) {
				t.Fatalf("round %d: key %d is %q", round, i, k)
			}
			if m, ok := file.Meta(i); !ok || string(m) != fmt.Sprintf("meta %d", i) {
				t.Fatalf("round %d: meta %d is %q", round, i, m)
			}
			if has, _ := file.TextHas(i, "meta"); has {
				t.Fatalf("round %d: doc %d's text runs into its meta", round, i)
			}
		}
		if _, ok := file.Meta(len(docs)); ok {
			t.Fatalf("round %d: a meta past the docs", round)
		}
	}
	if NewIndex(nil).Keys() != nil {
		t.Error("an index in memory has keys")
	}
}

// A file that is damaged, cut short, of another version or not an index at
// all is refused, as ErrBadIndex.
func TestFileIndexRefusesBadFiles(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "good.idx")
	writeOpen(t, path, randomDocs(rand.New(rand.NewSource(1)), 50))
	good, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string]func([]byte) []byte{
		"empty":         func(b []byte) []byte { return nil },
		"header only":   func(b []byte) []byte { return b[:headerSize] },
		"cut short":     func(b []byte) []byte { return b[:len(b)-1] },
		"one more byte": func(b []byte) []byte { return append(b, 0) },
		"a byte flipped": func(b []byte) []byte {
			b[len(b)/2] ^= 0x10
			return b
		},
		"a count changed": func(b []byte) []byte {
			b[32]++ // terms
			return b
		},
		"a count in the checksum": func(b []byte) []byte {
			b[24]++ // tokens: no size depends on it
			return b
		},
		"a text changed": func(b []byte) []byte {
			b[headerSize] ^= 1
			return b
		},
		"older version": func(b []byte) []byte {
			b[8] = 0
			return b
		},
		"not an index": func(b []byte) []byte {
			copy(b, "# A page")
			return b
		},
	}
	for name, damage := range cases {
		bad := filepath.Join(dir, "bad.idx")
		if err := os.WriteFile(bad, damage(slices.Clone(good)), 0o666); err != nil {
			t.Fatal(err)
		}
		if _, err := OpenIndex(bad); !errors.Is(err, ErrBadIndex) {
			t.Errorf("%s: %v, want ErrBadIndex", name, err)
		}
	}
	if _, err := OpenIndex(filepath.Join(dir, "missing.idx")); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("missing: %v", err)
	}
}

// A file whose checksum is right but whose terms or postings aren't as the
// writer makes them (a bug, not damage) is refused too, rather than making
// a search read past them or score a doc that isn't there.
func TestFileIndexRefusesWrongFilesWithTheirChecksum(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "good.idx")
	writeOpen(t, path, randomDocs(rand.New(rand.NewSource(1)), 50))
	good, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	s, err := parseStore(good)
	if err != nil {
		t.Fatal(err)
	}
	recsAt := headerSize + len(s.texts) + len(s.textAt) + len(s.lens) + len(s.keys)
	lastTermAt := recsAt + len(s.recs) + int(binary.LittleEndian.Uint32(s.recs[(s.nTerms-1)*termRecSize:]))
	postsAt := len(good) - len(s.posts)
	cases := map[string]func([]byte){
		"a document frequency past the docs": func(b []byte) { binary.LittleEndian.PutUint32(b[recsAt+4:], 51) },
		"a term starting past the next":      func(b []byte) { binary.LittleEndian.PutUint32(b[recsAt+termRecSize:], 1<<20) },
		"terms out of order":                 func(b []byte) { b[lastTermAt] = 0 }, // the last term first
		"a posting past the docs":            func(b []byte) { b[postsAt] = 0x7f }, // the first doc: 127 of 50
		"a posting cut":                      func(b []byte) { b[len(b)-1] = 0x80 },
		"a count of nought":                  func(b []byte) { b[len(b)-1] = 0 },
	}
	for name, wrong := range cases {
		b := slices.Clone(good)
		wrong(b)
		sum := crc32.Update(crc32.ChecksumIEEE(b[headerSize:]), crc32.IEEETable, b[16:headerSize])
		binary.LittleEndian.PutUint32(b[12:], sum)
		bad := filepath.Join(dir, "bad.idx")
		if err := os.WriteFile(bad, b, 0o666); err != nil {
			t.Fatal(err)
		}
		if _, err := OpenIndex(bad); !errors.Is(err, ErrBadIndex) {
			t.Errorf("%s: %v, want ErrBadIndex", name, err)
		}
	}
}

// A file replaced while it is searched: the search goes on reading the old
// one, and the next open reads the new one. No temporary file is left.
func TestFileReplacedDuringSearch(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pages.idx")
	r := rand.New(rand.NewSource(9))
	oldDocs, newDocs := randomDocs(r, 60), randomDocs(r, 70)
	old := writeOpen(t, path, oldDocs)
	before := old.Search("apple", 1000)
	writeOpen(t, path, newDocs)
	runtime.GC()
	exactlySame(t, "the old file", old.Search("apple", 1000), before)
	exactlySame(t, "the old file, as maps", old.Search("apple", 1000), NewIndex(oldDocs).Search("apple", 1000))
	again, err := OpenIndex(path)
	if err != nil {
		t.Fatal(err)
	}
	exactlySame(t, "the new file", again.Search("apple", 1000), NewIndex(newDocs).Search("apple", 1000))
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Errorf("left in the folder: %v", entries)
	}
}

// What a search hands out is its own: terms (typo matching), postings and
// keys stay readable once the mapping they came from is undone. If any
// pointed into it, reading them would crash.
func TestNothingPointsIntoAnUndoneMapping(t *testing.T) {
	dir := t.TempDir()
	var terms []string
	var postings [][]posting
	var keys []DocKey
	var hits []Hit
	for i := range 5 {
		ix := writeOpen(t, filepath.Join(dir, fmt.Sprintf("%d.idx", i)), randomDocs(rand.New(rand.NewSource(int64(i))), 40))
		for v := range ix.base.terms("") {
			terms = append(terms, v)
		}
		for _, v := range terms {
			postings = append(postings, ix.base.postings(v))
		}
		keys = append(keys, ix.Keys()...)
		hits = append(hits, ix.Search("aple", 10)...) // a typo: the terms walked
	}
	for range 5 {
		runtime.GC() // the cleanups undo the mappings
	}
	n := 0
	for _, v := range terms {
		n += len(strings.ToUpper(v))
	}
	for _, ps := range postings {
		for _, p := range ps {
			n += int(p.doc + p.tf)
		}
	}
	for _, k := range keys {
		n += int(k[0])
	}
	if n == 0 || len(hits) == 0 {
		t.Error("nothing read")
	}
}

func TestEmptyFileIndex(t *testing.T) {
	ix := writeOpen(t, filepath.Join(t.TempDir(), "empty.idx"), nil)
	if hits := ix.Search("apple", 5); hits != nil {
		t.Errorf("hits %v", hits)
	}
	if hits := ix.WithExtra([][]string{{"quokkaflute"}}).Search("quokkaflute", 5); len(hits) != 1 {
		t.Errorf("with extra: %v", hits)
	}
}

// A merged file (MergeIndex: the docs kept from an old file, new ones from
// their tokens) is the very file WriteIndex writes over the same docs: the
// docs left out, kept in another order, all of them new or none.
func TestMergeIndexIsWriteIndex(t *testing.T) {
	dir := t.TempDir()
	r := rand.New(rand.NewSource(7))
	meta := func(i int) []byte { return []byte(fmt.Sprintf("meta %d", i)) }
	for round := range 40 {
		oldDocs := randomDocs(r, r.Intn(60))
		oldPath := filepath.Join(dir, "old.idx")
		old := writeOpen(t, oldPath, oldDocs)
		// The new docs: some of old's (by number), in order or not, and new ones (-1 - j).
		var picks []int
		for o := range oldDocs {
			if r.Intn(4) > 0 || round%10 == 9 {
				picks = append(picks, o)
			}
		}
		if round%10 == 8 {
			picks = nil // all new
		}
		if round%3 == 1 && len(picks) > 2 {
			i, j := r.Intn(len(picks)), r.Intn(len(picks))
			picks[i], picks[j] = picks[j], picks[i]
		}
		newDocs := randomDocs(r, r.Intn(20))
		if round%10 == 9 {
			newDocs = nil // none new
		}
		for j := range newDocs {
			at := r.Intn(len(picks) + 1)
			picks = slices.Insert(picks, at, -1-j)
		}
		docOf := func(i int) IndexDoc {
			if p := picks[i]; p >= 0 {
				return IndexDoc{Tokens: oldDocs[p], Key: keyOf(p), Text: textOf(oldDocs[p]), Meta: meta(p)}
			}
			j := -1 - picks[i]
			return IndexDoc{Tokens: newDocs[j], Key: keyOf(1000 + j), Text: textOf(newDocs[j]), Meta: meta(1000 + j)}
		}
		wrote, merged := filepath.Join(dir, "wrote.idx"), filepath.Join(dir, "merged.idx")
		if err := WriteIndex(wrote, len(picks), docOf); err != nil {
			t.Fatal(err)
		}
		if err := MergeIndex(merged, old, len(picks), func(i int) MergeDoc {
			if p := picks[i]; p >= 0 {
				return MergeDoc{Old: p}
			}
			return MergeDoc{Old: -1, Doc: docOf(i)}
		}); err != nil {
			t.Fatal(err)
		}
		a, _ := os.ReadFile(wrote)
		b, _ := os.ReadFile(merged)
		if !bytes.Equal(a, b) {
			t.Fatalf("round %d (%d old, %d picked): the merged file differs from the one written", round, len(oldDocs), len(picks))
		}
		if _, err := OpenIndex(merged); err != nil {
			t.Fatalf("round %d: %v", round, err)
		}
	}
	if err := MergeIndex(filepath.Join(dir, "x.idx"), NewIndex(nil), 0, nil); err == nil {
		t.Error("merged from an index in memory")
	}
}
