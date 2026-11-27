// The index in a file: written once (WriteIndex), then mapped into memory
// read-only (OpenIndex) and searched as the maps are. Its pages are the
// kernel's to drop and read again, so a big index needn't fit in Go's heap.
//
// The format, version 2, little-endian:
//
//	header    64 bytes: magic, version, a CRC32 (IEEE) of everything after
//	          the header and then of the header's own fields, then the number
//	          of docs, tokens and terms, the length of the terms' bytes, of
//	          the postings and of the texts
//	texts     per doc a text the writer gives (TextHas) and then its meta
//	          (Meta), one doc after the other
//	text ends per doc where its text starts and where its meta starts, and
//	          one more: where the last meta ends, uint64
//	lengths   per doc its number of tokens, uint32
//	keys      per doc a DocKey, given by the writer (what the doc was)
//	terms     per term, sorted, and one more at the end: where its bytes
//	          start (uint32), its document frequency (uint32), where its
//	          postings start (uint64); a term ends where the next starts
//	bytes     the terms' bytes, one after the other
//	postings  per term, per doc holding it in order: the gap to the doc
//	          before (the first: its number) and the term's count, uvarints
//
// The texts come first so that the writer can pass them on as it is given
// them, holding none; the checksum is of the header's fields last for the
// same reason.
//
// A mapped file is never written to: a new one is written beside it and
// renamed into its place, so a search reading the old one keeps reading it
// until it lets go. Nothing that points into the mapping leaves this file:
// terms, postings and keys are handed out as copies, texts are only
// searched, and the mapping is undone only once nothing holds its store.
package textsearch

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash"
	"hash/crc32"
	"io"
	"iter"
	"maps"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
)

const (
	fileMagic   = "ZONEIDX\x00"
	fileVersion = 2
	headerSize  = 64
	termRecSize = 16
)

// DocKey: what a doc in a file was, as its writer tells (a hash of what it
// was built from, say), so whoever opens the file can tell which docs it
// still has.
type DocKey [16]byte

// IndexDoc: a document as WriteIndex asks for it: its tokens, its key, a
// text to keep for TextHas (Zonewiki: its text squashed, for phrases), and what
// else its writer wants back with it (Meta; Zonewiki: what makes its doc, so a
// start needn't read its file).
type IndexDoc struct {
	Tokens []string
	Key    DocKey
	Text   string
	Meta   []byte
}

// ErrBadIndex: a file that isn't an index this version reads: another
// format or version, cut short or damaged. Build it again.
var ErrBadIndex = errors.New("not a usable index file")

// termBuf: the postings of a term while a file is written, already coded.
type termBuf struct {
	df   uint32
	last int
	buf  []byte
}

// WriteIndex writes the index of n documents to path, asking for each one in
// turn and keeping none: what it holds meanwhile is the coded postings, a few
// bytes each, and the texts go to the file as they come. The file appears
// whole or not at all.
func WriteIndex(path string, n int, docOf func(i int) IndexDoc) error {
	if n > math.MaxInt32 {
		return fmt.Errorf("index of %d documents: too many", n)
	}
	return writeAtomically(path, func(f *os.File) error {
		t, err := startFile(f, n)
		if err != nil {
			return err
		}
		byTerm := map[string]*termBuf{}
		tf := map[string]uint64{}
		var order []string
		for i := range n {
			doc := docOf(i)
			t.doc(i, []byte(doc.Text), doc.Meta, uint32(len(doc.Tokens)), doc.Key)
			clear(tf)
			order = order[:0]
			for _, tok := range doc.Tokens {
				if tf[tok] == 0 {
					order = append(order, tok)
				}
				tf[tok]++
			}
			for _, tok := range order {
				tb := byTerm[tok]
				if tb == nil {
					tb = &termBuf{}
					byTerm[tok] = tb
				}
				tb.buf = binary.AppendUvarint(tb.buf, uint64(i-tb.last))
				tb.buf = binary.AppendUvarint(tb.buf, tf[tok])
				tb.last = i
				tb.df++
			}
		}
		terms := slices.Sorted(maps.Keys(byTerm))
		t.terms = len(terms)
		t.term = func(i int) []byte { return []byte(terms[i]) }
		for _, tok := range terms {
			t.dfs = append(t.dfs, byTerm[tok].df)
			t.sizes = append(t.sizes, uint64(len(byTerm[tok].buf)))
		}
		t.postings = func(i int) []byte { return byTerm[terms[i]].buf }
		return t.finish()
	})
}

// MergeDoc: a document of an index MergeIndex writes: one of the old
// index's, by its number there (Old ≥ 0), or a new one (Old < 0: Doc).
type MergeDoc struct {
	Old int
	Doc IndexDoc
}

// MergeIndex writes the index of n documents to path as WriteIndex does,
// the same bytes, but takes each document the old index has (its base, a
// file) from there: its postings, length, key, text and meta, not its
// tokens. Only the new docs' postings are held meanwhile; old's are read
// twice, to size the file and to write it. So a rebuild after a few changes
// asks only for the changed docs' tokens.
func MergeIndex(path string, old *Index, n int, docOf func(i int) MergeDoc) error {
	s, ok := old.base.(*fileStore)
	if !ok {
		return errors.New("merging an index: the old one isn't from a file")
	}
	if n > math.MaxInt32 {
		return fmt.Errorf("index of %d documents: too many", n)
	}
	defer runtime.KeepAlive(s)
	le := binary.LittleEndian
	return writeAtomically(path, func(f *os.File) error {
		t, err := startFile(f, n)
		if err != nil {
			return err
		}
		newOf := make([]int32, s.nDocs) // old's docs' numbers in the new file, -1: left out
		for i := range newOf {
			newOf[i] = -1
		}
		fresh := map[string][]posting{} // the new docs' postings
		tf := map[string]int32{}
		var order []string
		for i := range n {
			md := docOf(i)
			if o := md.Old; o >= 0 {
				if o >= s.nDocs || newOf[o] >= 0 {
					return fmt.Errorf("merging an index: old doc %d isn't there, or twice", o)
				}
				newOf[o] = int32(i)
				var key DocKey
				copy(key[:], s.keys[o*16:])
				t.doc(i, s.texts[le.Uint64(s.textAt[o*16:]):le.Uint64(s.textAt[o*16+8:])],
					s.texts[le.Uint64(s.textAt[o*16+8:]):le.Uint64(s.textAt[o*16+16:])], le.Uint32(s.lens[o*4:]), key)
				continue
			}
			doc := md.Doc
			t.doc(i, []byte(doc.Text), doc.Meta, uint32(len(doc.Tokens)), doc.Key)
			clear(tf)
			order = order[:0]
			for _, tok := range doc.Tokens {
				if tf[tok] == 0 {
					order = append(order, tok)
				}
				tf[tok]++
			}
			for _, tok := range order {
				fresh[tok] = append(fresh[tok], posting{int32(i), tf[tok]})
			}
		}

		// The terms, old's and the new docs' in order, each with its number
		// in old (-1: none) and the new docs' postings.
		type mergedTerm struct {
			old   int
			name  string // a term old doesn't have
			fresh []posting
		}
		newTerms := slices.Sorted(maps.Keys(fresh))
		var all []mergedTerm
		for i, j := 0, 0; i < s.nTerms || j < len(newTerms); {
			switch {
			case j == len(newTerms) || i < s.nTerms && string(s.term(i)) < newTerms[j]:
				all = append(all, mergedTerm{old: i})
				i++
			case i == s.nTerms || newTerms[j] < string(s.term(i)):
				all = append(all, mergedTerm{old: -1, name: newTerms[j], fresh: fresh[newTerms[j]]})
				j++
			default:
				all = append(all, mergedTerm{old: i, fresh: fresh[newTerms[j]]})
				i, j = i+1, j+1
			}
		}
		var ps, decoded []posting
		var coded []byte
		code := func(m mergedTerm) []byte { // the term's postings in the new file, coded
			ps = ps[:0]
			if m.old >= 0 {
				decoded = s.decode(m.old, decoded)
				for _, p := range decoded {
					if d := newOf[p.doc]; d >= 0 {
						ps = append(ps, posting{d, p.tf})
					}
				}
			}
			ps = append(ps, m.fresh...)
			byDoc := func(a, b posting) int { return int(a.doc) - int(b.doc) }
			if !slices.IsSortedFunc(ps, byDoc) {
				slices.SortFunc(ps, byDoc)
			}
			coded = coded[:0]
			last := int32(0)
			for _, p := range ps {
				coded = binary.AppendUvarint(coded, uint64(p.doc-last))
				coded = binary.AppendUvarint(coded, uint64(p.tf))
				last = p.doc
			}
			return coded
		}
		var kept []mergedTerm // those some doc still holds
		for _, m := range all {
			if c := code(m); len(c) > 0 {
				kept = append(kept, m)
				t.dfs = append(t.dfs, uint32(len(ps)))
				t.sizes = append(t.sizes, uint64(len(c)))
			}
		}
		t.terms = len(kept)
		t.term = func(i int) []byte {
			if m := kept[i]; m.old >= 0 {
				return s.term(m.old)
			}
			return []byte(kept[i].name)
		}
		t.postings = func(i int) []byte { return code(kept[i]) }
		return t.finish()
	})
}

// fileWriter: an index file as it is written: the texts as the docs come,
// then the rest, given by its writer (finish).
type fileWriter struct {
	f      *os.File
	sum    hash.Hash32
	w      *bufWriter
	n      int
	total  uint64
	texts  uint64
	textAt []uint64
	lens   []uint32
	keys   []DocKey

	terms    int                // how many, sorted
	term     func(i int) []byte // term i's bytes
	dfs      []uint32           // per term its document frequency
	sizes    []uint64           // and the length of its postings
	postings func(i int) []byte // term i's postings, coded
}

// startFile: a writer for n docs, its header's room written.
func startFile(f *os.File, n int) (*fileWriter, error) {
	if _, err := f.Write(make([]byte, headerSize)); err != nil {
		return nil, err
	}
	t := &fileWriter{f: f, sum: crc32.NewIEEE(), n: n, textAt: make([]uint64, 0, 2*n+1),
		lens: make([]uint32, n), keys: make([]DocKey, n)}
	t.w = newBufWriter(io.MultiWriter(f, t.sum))
	return t, nil
}

// doc: doc i's text and meta, written now, and its length and key.
func (t *fileWriter) doc(i int, text, meta []byte, length uint32, key DocKey) {
	t.textAt = append(t.textAt, t.texts)
	t.w.write(text)
	t.texts += uint64(len(text))
	t.textAt = append(t.textAt, t.texts)
	t.w.write(meta)
	t.texts += uint64(len(meta))
	t.lens[i], t.keys[i] = length, key
	t.total += uint64(length)
}

// finish: the rest after the texts, then the header.
func (t *fileWriter) finish() error {
	t.textAt = append(t.textAt, t.texts)
	var strsLen, postsLen uint64
	for i := range t.terms {
		strsLen += uint64(len(t.term(i)))
		postsLen += t.sizes[i]
	}
	if strsLen > math.MaxUint32 {
		return fmt.Errorf("index with %d bytes of terms: too many", strsLen)
	}
	w := t.w
	var b [termRecSize]byte
	for _, at := range t.textAt {
		w.write(binary.LittleEndian.AppendUint64(b[:0], at))
	}
	for _, l := range t.lens {
		w.write(binary.LittleEndian.AppendUint32(b[:0], l))
	}
	for _, k := range t.keys {
		w.write(k[:])
	}
	var strAt, postAt uint64
	for i := range t.terms {
		binary.LittleEndian.PutUint32(b[0:], uint32(strAt))
		binary.LittleEndian.PutUint32(b[4:], t.dfs[i])
		binary.LittleEndian.PutUint64(b[8:], postAt)
		w.write(b[:])
		strAt += uint64(len(t.term(i)))
		postAt += t.sizes[i]
	}
	binary.LittleEndian.PutUint32(b[0:], uint32(strAt))
	binary.LittleEndian.PutUint32(b[4:], 0)
	binary.LittleEndian.PutUint64(b[8:], postAt)
	w.write(b[:])
	for i := range t.terms {
		w.write(t.term(i))
	}
	for i := range t.terms {
		w.write(t.postings(i))
	}
	if err := w.flush(); err != nil {
		return err
	}
	header := make([]byte, headerSize)
	copy(header, fileMagic)
	binary.LittleEndian.PutUint32(header[8:], fileVersion)
	binary.LittleEndian.PutUint64(header[16:], uint64(t.n))
	binary.LittleEndian.PutUint64(header[24:], t.total)
	binary.LittleEndian.PutUint64(header[32:], uint64(t.terms))
	binary.LittleEndian.PutUint64(header[40:], strsLen)
	binary.LittleEndian.PutUint64(header[48:], postsLen)
	binary.LittleEndian.PutUint64(header[56:], t.texts)
	t.sum.Write(header[16:])
	binary.LittleEndian.PutUint32(header[12:], t.sum.Sum32())
	_, err := t.f.WriteAt(header, 0)
	return err
}

// bufWriter: writes in large pieces, keeping the first error.
type bufWriter struct {
	w   io.Writer
	buf []byte
	err error
}

func newBufWriter(w io.Writer) *bufWriter { return &bufWriter{w: w, buf: make([]byte, 0, 1<<16)} }

func (b *bufWriter) write(p []byte) {
	if len(b.buf)+len(p) > cap(b.buf) {
		b.flush()
	}
	if len(p) > cap(b.buf) {
		if b.err == nil {
			_, b.err = b.w.Write(p)
		}
		return
	}
	b.buf = append(b.buf, p...)
}

func (b *bufWriter) flush() error {
	if b.err == nil && len(b.buf) > 0 {
		_, b.err = b.w.Write(b.buf)
	}
	b.buf = b.buf[:0]
	return b.err
}

// writeAtomically: a temporary file beside path, written, flushed to disk
// and renamed into place, then the folder flushed too; on any error removed.
func writeAtomically(path string, write func(*os.File) error) (err error) {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, "."+filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			f.Close()
			os.Remove(f.Name())
		}
	}()
	if err = write(f); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = os.Rename(f.Name(), path); err != nil {
		return err
	}
	if d, e := os.Open(dir); e == nil {
		d.Sync() // not every system can; the file is in place either way
		d.Close()
	}
	return nil
}

// fileStore: a store reading a mapped file. Its parts are slices of the
// mapping, so every method keeps the store alive until it is done with them
// (runtime.KeepAlive): the mapping goes when the store does.
type fileStore struct {
	nDocs, nTokens, nTerms int
	texts, textAt, lens    []byte
	keys, recs, strs       []byte
	posts                  []byte
}

// OpenIndex maps the index file at path. A file this version can't read is
// ErrBadIndex.
func OpenIndex(path string) (*Index, error) {
	s, err := openStore(path)
	if err != nil {
		return nil, err
	}
	return newIndex(s), nil
}

func openStore(path string) (*fileStore, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if st.Size() < headerSize || st.Size() > math.MaxInt {
		return nil, fmt.Errorf("%s: %w (size %d)", path, ErrBadIndex, st.Size())
	}
	data, unmap, err := mapFile(f, int(st.Size()))
	if err != nil {
		return nil, err
	}
	s, err := parseStore(data)
	if err != nil {
		unmap(data)
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	runtime.AddCleanup(s, func(d []byte) { unmap(d) }, data)
	return s, nil
}

// parseStore: the parts of a file's bytes, checked: magic, version, sizes,
// checksum.
func parseStore(data []byte) (*fileStore, error) {
	le := binary.LittleEndian
	if string(data[:8]) != fileMagic {
		return nil, fmt.Errorf("%w: not an index", ErrBadIndex)
	}
	if v := le.Uint32(data[8:]); v != fileVersion {
		return nil, fmt.Errorf("%w: version %d, not %d", ErrBadIndex, v, fileVersion)
	}
	docs, tokens, terms := le.Uint64(data[16:]), le.Uint64(data[24:]), le.Uint64(data[32:])
	strsLen, postsLen, textsLen := le.Uint64(data[40:]), le.Uint64(data[48:]), le.Uint64(data[56:])
	// Each part fits in what is left, so their sum can't overflow.
	rest := uint64(len(data)) - headerSize
	if docs > math.MaxInt32 || terms > math.MaxInt32 || strsLen > rest || postsLen > rest || textsLen > rest ||
		textsLen+(2*docs+1)*8+docs*(4+16)+(terms+1)*termRecSize+strsLen+postsLen != rest {
		return nil, fmt.Errorf("%w: its parts don't add up to its size", ErrBadIndex)
	}
	sum := crc32.Update(crc32.ChecksumIEEE(data[headerSize:]), crc32.IEEETable, data[16:headerSize])
	if sum != le.Uint32(data[12:]) {
		return nil, fmt.Errorf("%w: damaged (checksum)", ErrBadIndex)
	}
	s := &fileStore{nDocs: int(docs), nTokens: int(tokens), nTerms: int(terms)}
	at := uint64(headerSize)
	part := func(n uint64) []byte {
		p := data[at : at+n : at+n]
		at += n
		return p
	}
	s.texts = part(textsLen)
	s.textAt = part((2*docs + 1) * 8)
	s.lens = part(docs * 4)
	s.keys = part(docs * 16)
	s.recs = part((terms + 1) * termRecSize)
	s.strs = part(strsLen)
	s.posts = part(postsLen)
	for i := range 2*docs + 1 { // where the texts and metas start: in order, inside them
		if t := le.Uint64(s.textAt[i*8:]); t > textsLen || i > 0 && t < le.Uint64(s.textAt[(i-1)*8:]) {
			return nil, fmt.Errorf("%w: its texts are out of order", ErrBadIndex)
		}
	}
	if err := s.check(); err != nil {
		return nil, err
	}
	return s, nil
}

// check: the terms and postings are as the writer makes them, so that no
// search reads past them or gets a doc that isn't there; the checksum only
// catches damage, not a file wrong from the start. One pass, as the
// checksum's.
func (s *fileStore) check() error {
	le := binary.LittleEndian
	rec := func(i int) (str uint32, df uint32, post uint64) {
		r := s.recs[i*termRecSize:]
		return le.Uint32(r), le.Uint32(r[4:]), le.Uint64(r[8:])
	}
	str0, _, post0 := rec(0)
	strEnd, _, postEnd := rec(s.nTerms)
	if str0 != 0 || post0 != 0 || uint64(strEnd) != uint64(len(s.strs)) || postEnd != uint64(len(s.posts)) {
		return fmt.Errorf("%w: its terms don't span their parts", ErrBadIndex)
	}
	for i := range s.nTerms { // first all of them, so that each term's parts are inside the file
		str, df, post := rec(i)
		nextStr, _, nextPost := rec(i + 1)
		if nextStr < str || nextPost < post || df == 0 || int(df) > s.nDocs {
			return fmt.Errorf("%w: term %d is out of place", ErrBadIndex, i)
		}
	}
	for i := range s.nTerms {
		_, df, post := rec(i)
		_, _, nextPost := rec(i + 1)
		if i > 0 && string(s.term(i-1)) >= string(s.term(i)) {
			return fmt.Errorf("%w: its terms are out of order", ErrBadIndex)
		}
		p, doc := s.posts[post:nextPost], 0
		for k := range df {
			gap, n := binary.Uvarint(p)
			if n <= 0 {
				return fmt.Errorf("%w: term %d's postings are cut", ErrBadIndex, i)
			}
			tf, m := binary.Uvarint(p[n:])
			if m <= 0 || tf == 0 || tf > math.MaxInt32 || k > 0 && gap == 0 || gap >= uint64(s.nDocs) {
				return fmt.Errorf("%w: term %d's postings are wrong", ErrBadIndex, i)
			}
			p = p[n+m:]
			if doc += int(gap); doc >= s.nDocs { // as decode: the first gap is the doc's number
				return fmt.Errorf("%w: term %d names a doc that isn't there", ErrBadIndex, i)
			}
		}
		if len(p) != 0 {
			return fmt.Errorf("%w: term %d's postings are too long", ErrBadIndex, i)
		}
	}
	return nil
}

func (s *fileStore) docCount() int   { return s.nDocs }
func (s *fileStore) tokenCount() int { return s.nTokens }

func (s *fileStore) docLen(doc int) int {
	defer runtime.KeepAlive(s)
	return int(binary.LittleEndian.Uint32(s.lens[doc*4:]))
}

// term: the bytes of term i, a slice of the mapping: not to leave the store.
func (s *fileStore) term(i int) []byte {
	r := s.recs[i*termRecSize:]
	return s.strs[binary.LittleEndian.Uint32(r):binary.LittleEndian.Uint32(r[termRecSize:])]
}

// find: the number of term t, or -1.
func (s *fileStore) find(t string) int {
	i := sort.Search(s.nTerms, func(i int) bool { return string(s.term(i)) >= t })
	if i < s.nTerms && string(s.term(i)) == t {
		return i
	}
	return -1
}

func (s *fileStore) df(t string) int {
	defer runtime.KeepAlive(s)
	i := s.find(t)
	if i < 0 {
		return 0
	}
	return int(binary.LittleEndian.Uint32(s.recs[i*termRecSize+4:]))
}

func (s *fileStore) postings(t string) []posting {
	defer runtime.KeepAlive(s)
	i := s.find(t)
	if i < 0 {
		return nil
	}
	return s.decode(i, nil)
}

// decode: the postings of term i, appended to into (a copy, the caller's).
func (s *fileStore) decode(i int, into []posting) []posting {
	r := s.recs[i*termRecSize:]
	df := int(binary.LittleEndian.Uint32(r[4:]))
	p := s.posts[binary.LittleEndian.Uint64(r[8:]):binary.LittleEndian.Uint64(r[termRecSize+8:])]
	out := slices.Grow(into[:0], df)
	doc := 0
	for range df {
		gap, n := binary.Uvarint(p)
		tf, m := binary.Uvarint(p[n:])
		p = p[n+m:]
		doc += int(gap)
		out = append(out, posting{int32(doc), int32(tf)})
	}
	return out
}

func (s *fileStore) terms(from string) iter.Seq[string] {
	return func(yield func(string) bool) {
		defer runtime.KeepAlive(s)
		i := sort.Search(s.nTerms, func(i int) bool { return string(s.term(i)) >= from })
		for ; i < s.nTerms; i++ {
			if !yield(string(s.term(i))) {
				return
			}
		}
	}
}

// all: every term and its postings; the postings slice is used again for
// the next term, so it isn't to be kept.
func (s *fileStore) all() iter.Seq2[string, []posting] {
	return func(yield func(string, []posting) bool) {
		defer runtime.KeepAlive(s)
		var buf []posting
		for i := range s.nTerms {
			buf = s.decode(i, buf)
			if !yield(string(s.term(i)), buf) {
				return
			}
		}
	}
}

// Keys: each doc's key as the file has it, by number; nil for an index not
// read from a file.
func (ix *Index) Keys() []DocKey {
	s, ok := ix.base.(*fileStore)
	if !ok {
		return nil
	}
	defer runtime.KeepAlive(s)
	keys := make([]DocKey, s.nDocs)
	for i := range keys {
		copy(keys[i][:], s.keys[i*16:])
	}
	return keys
}

// TextHas: whether doc's text in the file holds sub: the index's file, or
// for a doc of a view's layer that is an index from a file (Index.Layer)
// that one's; known is false where there is no such text: a doc of a layer
// in memory (added by With), an index not read from a file.
func (ix *Index) TextHas(doc int, sub string) (has, known bool) {
	from := ix.base
	if o := ix.over; o != nil && doc >= ix.n && doc < o.end {
		at, i := o.layerOf(doc)
		from, doc = at.Layer.s, i
	}
	s, ok := from.(*fileStore)
	if !ok || doc < 0 || doc >= s.nDocs {
		return false, false
	}
	defer runtime.KeepAlive(s)
	le := binary.LittleEndian
	text := s.texts[le.Uint64(s.textAt[doc*16:]):le.Uint64(s.textAt[doc*16+8:])]
	return bytes.Contains(text, []byte(sub)), true
}

// Meta: what the writer kept with doc (IndexDoc.Meta), a copy; false for a
// doc not in the file (added by With), or an index not read from one.
func (ix *Index) Meta(doc int) ([]byte, bool) {
	s, ok := ix.base.(*fileStore)
	if !ok || doc < 0 || doc >= ix.n {
		return nil, false
	}
	defer runtime.KeepAlive(s)
	le := binary.LittleEndian
	return bytes.Clone(s.texts[le.Uint64(s.textAt[doc*16+8:]):le.Uint64(s.textAt[doc*16+16:])]), true
}
