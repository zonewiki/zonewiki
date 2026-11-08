// The index in a file: written once (WriteIndex), then mapped into memory
// read-only (OpenIndex) and searched as the maps are. Its pages are the
// kernel's to drop and read again, so a big index needn't fit in Go's heap.
//
// The format, version 1, little-endian:
//
//	header    64 bytes: magic, version, a CRC32 (IEEE) of everything after
//	          the header and then of the header's own fields, then the number
//	          of docs, tokens and terms, the length of the terms' bytes, of
//	          the postings and of the texts
//	texts     per doc a text the writer gives (TextHas), one after the other
//	text ends per doc where its text starts, and one more: where the last
//	          ends, uint64
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
	"hash/crc32"
	"io"
	"iter"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
)

const (
	fileMagic   = "ZONEIDX\x00"
	fileVersion = 1
	headerSize  = 64
	termRecSize = 16
)

// DocKey: what a doc in a file was, as its writer tells (a hash of what it
// was built from, say), so whoever opens the file can tell which docs it
// still has.
type DocKey [16]byte

// IndexDoc: a document as WriteIndex asks for it: its tokens, its key, and
// a text to keep for TextHas (Zone: its text squashed, for phrases).
type IndexDoc struct {
	Tokens []string
	Key    DocKey
	Text   string
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
		header := make([]byte, headerSize)
		if _, err := f.Write(header); err != nil {
			return err
		}
		sum := crc32.NewIEEE()
		w := newBufWriter(io.MultiWriter(f, sum))

		textAt := make([]uint64, 0, n+1)
		lens := make([]uint32, n)
		keys := make([]DocKey, n)
		byTerm := map[string]*termBuf{}
		tf := map[string]uint64{}
		var order []string
		var total, textsLen uint64
		for i := range n {
			doc := docOf(i)
			textAt = append(textAt, textsLen)
			w.write([]byte(doc.Text))
			textsLen += uint64(len(doc.Text))
			keys[i] = doc.Key
			clear(tf)
			order = order[:0]
			for _, t := range doc.Tokens {
				if tf[t] == 0 {
					order = append(order, t)
				}
				tf[t]++
			}
			lens[i] = uint32(len(doc.Tokens))
			total += uint64(len(doc.Tokens))
			for _, t := range order {
				tb := byTerm[t]
				if tb == nil {
					tb = &termBuf{}
					byTerm[t] = tb
				}
				tb.buf = binary.AppendUvarint(tb.buf, uint64(i-tb.last))
				tb.buf = binary.AppendUvarint(tb.buf, tf[t])
				tb.last = i
				tb.df++
			}
		}
		textAt = append(textAt, textsLen)
		terms := slices.Sorted(func(yield func(string) bool) {
			for t := range byTerm {
				if !yield(t) {
					return
				}
			}
		})
		var strsLen, postsLen uint64
		for _, t := range terms {
			strsLen += uint64(len(t))
			postsLen += uint64(len(byTerm[t].buf))
		}
		if strsLen > math.MaxUint32 {
			return fmt.Errorf("index with %d bytes of terms: too many", strsLen)
		}

		var b [termRecSize]byte
		for _, at := range textAt {
			w.write(binary.LittleEndian.AppendUint64(b[:0], at))
		}
		for _, l := range lens {
			w.write(binary.LittleEndian.AppendUint32(b[:0], l))
		}
		for _, k := range keys {
			w.write(k[:])
		}
		var strAt, postAt uint64
		for _, t := range terms {
			tb := byTerm[t]
			binary.LittleEndian.PutUint32(b[0:], uint32(strAt))
			binary.LittleEndian.PutUint32(b[4:], tb.df)
			binary.LittleEndian.PutUint64(b[8:], postAt)
			w.write(b[:])
			strAt += uint64(len(t))
			postAt += uint64(len(tb.buf))
		}
		binary.LittleEndian.PutUint32(b[0:], uint32(strAt))
		binary.LittleEndian.PutUint32(b[4:], 0)
		binary.LittleEndian.PutUint64(b[8:], postAt)
		w.write(b[:])
		for _, t := range terms {
			w.write([]byte(t))
		}
		for _, t := range terms {
			w.write(byTerm[t].buf)
		}
		if err := w.flush(); err != nil {
			return err
		}

		copy(header, fileMagic)
		binary.LittleEndian.PutUint32(header[8:], fileVersion)
		binary.LittleEndian.PutUint64(header[16:], uint64(n))
		binary.LittleEndian.PutUint64(header[24:], total)
		binary.LittleEndian.PutUint64(header[32:], uint64(len(terms)))
		binary.LittleEndian.PutUint64(header[40:], strsLen)
		binary.LittleEndian.PutUint64(header[48:], postsLen)
		binary.LittleEndian.PutUint64(header[56:], textsLen)
		sum.Write(header[16:])
		binary.LittleEndian.PutUint32(header[12:], sum.Sum32())
		_, err := f.WriteAt(header, 0)
		return err
	})
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
		textsLen+(docs+1)*8+docs*(4+16)+(terms+1)*termRecSize+strsLen+postsLen != rest {
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
	s.textAt = part((docs + 1) * 8)
	s.lens = part(docs * 4)
	s.keys = part(docs * 16)
	s.recs = part((terms + 1) * termRecSize)
	s.strs = part(strsLen)
	s.posts = part(postsLen)
	for i := range docs + 1 { // where the texts start: in order, inside them
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

// TextHas: whether doc's text in the file holds sub; known is false where
// there is no such text: a doc not in the file (added by With), an index not
// read from one.
func (ix *Index) TextHas(doc int, sub string) (has, known bool) {
	s, ok := ix.base.(*fileStore)
	if !ok || doc < 0 || doc >= ix.n {
		return false, false
	}
	defer runtime.KeepAlive(s)
	le := binary.LittleEndian
	text := s.texts[le.Uint64(s.textAt[doc*8:]):le.Uint64(s.textAt[(doc+1)*8:])]
	return bytes.Contains(text, []byte(sub)), true
}
