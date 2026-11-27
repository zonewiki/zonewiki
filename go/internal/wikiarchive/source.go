package wikiarchive

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"unicode/utf8"
)

// Tar: the entries of a gzipped tar read from r. A tar's folders, files and
// all else are its type flags; global headers are no entries.
func Tar(r io.Reader) Source { return &tarSource{r: r} }

type tarSource struct {
	r       io.Reader
	tr      *tar.Reader
	entries int
}

func (s *tarSource) Next() (Entry, error) {
	if s.tr == nil {
		zr, err := gzip.NewReader(s.r)
		if err != nil {
			return Entry{}, errFormat{"a gzipped tar", err.Error()}
		}
		s.tr = tar.NewReader(zr)
	}
	for {
		h, err := s.tr.Next()
		if err != nil && !errors.Is(err, io.EOF) && s.entries == 0 {
			// Some other file, gzipped: "cut off or damaged" would say to
			// send the same one again.
			return Entry{}, errFormat{"a gzipped tar", fmt.Sprintf("what is gzipped is no tar (%v)", err)}
		}
		if err != nil {
			return Entry{}, err
		}
		s.entries++
		if h.Typeflag == tar.TypeXGlobalHeader {
			continue
		}
		e := Entry{Name: h.Name, Kind: Other,
			Open: func() (io.ReadCloser, error) { return io.NopCloser(s.tr), nil }}
		switch h.Typeflag {
		case tar.TypeDir:
			e.Kind = Folder
		case tar.TypeReg:
			e.Kind = File
		}
		return e, nil
	}
}

// Zip: the entries of the zip in r, size bytes long; a zip is read from its
// end, so it needs a file to seek in, not a stream. A zip's links are in
// its mode bits, which make them Other; a name that isn't UTF-8 refuses the
// archive (a zip may hold names in an old code page, which no one could
// read back). A size its headers state is never trusted: Unpack counts what
// it writes, and a file longer than its header says is an error of its
// reader.
func Zip(r io.ReaderAt, size int64) Source { return &zipSource{r: r, size: size} }

type zipSource struct {
	r     io.ReaderAt
	size  int64
	files []*zip.File
	open  bool
}

func (s *zipSource) Next() (Entry, error) {
	if !s.open {
		zr, err := zip.NewReader(s.r, s.size)
		if err != nil && !errors.Is(err, zip.ErrInsecurePath) { // a name leading out: nameParts says which
			return Entry{}, errFormat{"a zip", err.Error()}
		}
		s.files, s.open = zr.File, true
	}
	if len(s.files) == 0 {
		return Entry{}, io.EOF
	}
	f := s.files[0]
	s.files = s.files[1:]
	if !utf8.ValidString(f.Name) {
		return Entry{}, errRefused(fmt.Sprintf("the name %q in it isn't UTF-8", f.Name))
	}
	e := Entry{Name: f.Name, Kind: Other, Open: f.Open}
	switch f.Mode().Type() {
	case fs.ModeDir:
		e.Kind = Folder
	case 0:
		e.Kind = File
	}
	return e, nil
}
