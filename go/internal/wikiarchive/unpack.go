package wikiarchive

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

// Kind: what an entry of an archive is.
type Kind int

const (
	File   Kind = iota
	Folder      // a folder
	Other       // a link, a device, anything that isn't a file or a folder
)

// Entry: one entry of an archive, as its source gives it.
type Entry struct {
	Name string // as the archive spells it, folders with or without a trailing /
	Kind Kind
	// Open: a file's bytes, until the source's next entry. Unpack opens only
	// what it keeps.
	Open func() (io.ReadCloser, error)
}

// Source: the entries of an archive, one at a time; io.EOF after the last.
// Tar and Zip are the two.
type Source interface {
	Next() (Entry, error)
}

// Take: what Rules.Keep makes of an entry.
type Take struct {
	Keep    bool  // written out; what isn't kept is never written, whatever it is
	Max     int64 // a file's own most bytes, in place of what is left of the budget (0: none of its own)
	Private bool  // the file is written for its owner only (0600)
}

// Rules: what Unpack keeps of an archive, how much it may write, and the
// words of a refusal.
type Rules struct {
	// Not: what an archive that is refused isn't, at the start of the
	// refusal ("not a Zonewiki export", "not a zip of notes").
	Not string
	// Keep: what becomes of an entry, by its name's parts (never empty,
	// checked by nameParts). An error refuses the archive.
	Keep func(name []string, kind Kind) (Take, error)
	// MaxBytes: how many bytes may be written (0: any number), counted as
	// they are written, whatever an entry says of its size. Past it Unpack
	// stops with ErrTooBig.
	MaxBytes int64
	// MaxEntries: how many entries the archive may hold, kept or not (0: any
	// number).
	MaxEntries int
}

// errFormat: a source's file isn't of its format at all.
type errFormat struct{ format, detail string }

func (e errFormat) Error() string { return "not " + e.format + ": " + e.detail }

// errRefused: what refuses an archive as its source sees it, without the
// caller's words.
type errRefused string

func (e errRefused) Error() string { return string(e) }

// Unpack writes what rules keep of the archive src into dir, which must be
// the caller's own and empty, and returns how many bytes it wrote. Every
// name is checked (nameParts), kept or not; among what is kept there are
// files and folders only, no file twice, and no name both a file and a
// folder. Whatever is wrong refuses the whole archive, in rules' words; what
// was written by then is left for the caller to remove with dir.
func Unpack(src Source, dir string, rules Rules) (int64, error) {
	var written int64
	for entries := 1; ; entries++ {
		e, err := src.Next()
		if errors.Is(err, io.EOF) {
			return written, nil
		}
		var format errFormat
		if errors.As(err, &format) {
			return written, fmt.Errorf("%s (%s): %s", rules.Not, format.format, format.detail)
		}
		var refused errRefused
		if errors.As(err, &refused) {
			return written, fmt.Errorf("%s: %s", rules.Not, refused)
		}
		if err != nil {
			return written, fmt.Errorf("the archive is cut off or damaged: %v", err)
		}
		if rules.MaxEntries > 0 && entries > rules.MaxEntries {
			return written, fmt.Errorf("%s: it holds more than %d entries", rules.Not, rules.MaxEntries)
		}
		parts, err := nameParts(e.Name, rules.Not)
		if err != nil {
			return written, err
		}
		if len(parts) == 0 {
			continue // "./"
		}
		take, err := rules.Keep(parts, e.Kind)
		if err != nil {
			return written, err
		}
		if !take.Keep {
			continue
		}
		path := filepath.Join(append([]string{dir}, parts...)...)
		switch e.Kind {
		case Folder:
			if err := os.MkdirAll(path, 0o777); err != nil {
				return written, both(err, e.Name, rules.Not)
			}
			continue
		case File:
		default:
			return written, fmt.Errorf("%s: %s is a link or a device in it, where only files and folders may be", rules.Not, e.Name)
		}
		limit := int64(-1)
		switch {
		case take.Max > 0:
			limit = take.Max
		case rules.MaxBytes > 0:
			// Never below 0, which is "any number": a file with a limit
			// of its own may have taken the budget past its end.
			limit = max(rules.MaxBytes-written, 0)
		}
		r, err := e.Open()
		if err != nil {
			return written, fmt.Errorf("the archive can't be unpacked (cut off or damaged): %v", err)
		}
		n, err := writeFile(path, r, limit, take.Private, rules.Not)
		r.Close()
		written += n
		if err != nil {
			return written, both(err, e.Name, rules.Not)
		}
	}
}

// both: err, or a refusal when it is that a name is a file and a folder in
// the archive (a file where a folder was, a folder where a file was, a path
// through a file).
func both(err error, name, not string) error {
	if errors.Is(err, syscall.ENOTDIR) || errors.Is(err, fs.ErrExist) {
		return fmt.Errorf("%s: %q is both a file and a folder in it", not, name)
	}
	return err
}

// nameParts: an entry's name as its folders and its own name; an error for
// one that could lead anywhere but into the folder unpacked into, or that no
// file Zonewiki or git writes has.
func nameParts(name, not string) ([]string, error) {
	bad := func() ([]string, error) {
		return nil, fmt.Errorf("%s: the name %q in it", not, name)
	}
	if strings.HasPrefix(name, "/") || strings.ContainsRune(name, '\\') {
		return bad()
	}
	for _, c := range name {
		if c < 0x20 || c == 0x7f {
			return bad()
		}
	}
	var parts []string
	for i, p := range strings.Split(strings.TrimSuffix(name, "/"), "/") {
		switch {
		case p == "." && i == 0:
			continue // tar -C … . writes ./wiki
		case p == "" || p == "." || p == "..":
			return bad()
		}
		parts = append(parts, p)
	}
	return parts, nil
}

// writeFile writes what r holds as the new file at path, at most limit
// bytes (-1: any number), and returns how many it wrote. The file must not
// be there: an archive names each file once.
func writeFile(path string, r io.Reader, limit int64, ownerOnly bool, not string) (int64, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		return 0, err
	}
	mode := os.FileMode(0o666)
	if ownerOnly {
		mode = 0o600
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if err != nil {
		if errors.Is(err, fs.ErrExist) {
			if st, serr := os.Lstat(path); serr == nil && st.IsDir() {
				return 0, err // a folder there: both a file and a folder
			}
			return 0, fmt.Errorf("%s: it holds %s twice", not, filepath.Base(path))
		}
		return 0, err
	}
	if limit >= 0 {
		r = io.LimitReader(r, limit+1)
	}
	n, err := io.Copy(f, r)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil && limit >= 0 && n > limit {
		err = ErrTooBig
	}
	if err != nil && !errors.Is(err, ErrTooBig) {
		err = fmt.Errorf("the archive can't be unpacked (cut off, damaged, or no room for it): %w", err)
	}
	return n, err
}
