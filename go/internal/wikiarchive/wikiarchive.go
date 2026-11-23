// Package wikiarchive writes and reads the file a whole wiki moves in
// (Moving a wiki in docs/spec/deployment.md): a gzipped tar with one folder,
// wiki/, that holds a clone of the wiki's git repository and, in
// .zone/config, its settings file.
//
// Reading takes an archive for what anyone may have made: of all it holds,
// only the repository's history (its objects and refs) and the settings
// file are written out, as plain files; the repository's config, its hooks
// and its work tree stay in the archive.
package wikiarchive

import (
	"archive/tar"
	"compress/gzip"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

// Top: the one folder an archive holds.
const Top = "wiki"

// ConfigMax: the biggest settings file an archive may hold. Zonewiki's own is a
// few lines a user.
const ConfigMax = 4 << 20

// ErrTooBig: the archive unpacks to more than Read was given.
var ErrTooBig = errors.New("the archive holds more than fits")

// Write writes dir, a clone of a wiki with its settings file, to w as an
// archive. Only files and folders go in: anything else in dir (a link, a
// device) is an error, since a reader refuses it.
func Write(w io.Writer, dir string) error {
	zw := gzip.NewWriter(w)
	tw := tar.NewWriter(zw)
	err := filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(dir, path)
		if err != nil {
			return err
		}
		name := Top
		if rel != "." {
			name += "/" + filepath.ToSlash(rel)
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		switch {
		case info.IsDir():
			return tw.WriteHeader(&tar.Header{Typeflag: tar.TypeDir, Name: name + "/",
				Mode: int64(info.Mode().Perm()), ModTime: info.ModTime()})
		case info.Mode().IsRegular():
			f, err := os.Open(path)
			if err != nil {
				return err
			}
			defer f.Close()
			if err := tw.WriteHeader(&tar.Header{Typeflag: tar.TypeReg, Name: name,
				Mode: int64(info.Mode().Perm()), ModTime: info.ModTime(), Size: info.Size()}); err != nil {
				return err
			}
			// CopyN: a file that grew since its size was read doesn't break
			// the archive (a shrunk one is an error, as it should be).
			_, err = io.CopyN(tw, f, info.Size())
			return err
		}
		return fmt.Errorf("%s is neither a file nor a folder", rel)
	})
	if err == nil {
		err = tw.Close()
	}
	if err == nil {
		err = zw.Close()
	}
	return err
}

// Found: what Read took from an archive.
type Found struct {
	Config bool  // the settings file, at wiki/.zone/config under the folder read into
	Bytes  int64 // what was written
}

// Read unpacks the archive r into dir/wiki: the repository's history
// (.git/HEAD, .git/packed-refs, .git/objects/, .git/refs/) and the settings
// file (.zone/config), and nothing else of it. dir must be the caller's own
// and empty. max is how many bytes may be written (0: any number); past it
// Read stops with ErrTooBig.
//
// An archive that isn't one of a wiki is an error: something beside wiki/,
// a name that leads out of it, a link or a device where the history or the
// settings file should be, the same file twice, no repository.
func Read(r io.Reader, dir string, max int64) (Found, error) {
	var found Found
	zr, err := gzip.NewReader(r)
	if err != nil {
		return found, fmt.Errorf("not a Zonewiki export (a gzipped tar): %v", err)
	}
	tr := tar.NewReader(zr)
	var head, objects bool
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return found, fmt.Errorf("the archive is cut off or damaged: %v", err)
		}
		if h.Typeflag == tar.TypeXGlobalHeader {
			continue
		}
		parts, err := nameParts(h.Name)
		if err != nil {
			return found, err
		}
		if len(parts) == 0 {
			continue // "./"
		}
		if parts[0] != Top {
			return found, fmt.Errorf("not a Zonewiki export: it holds %q beside %s/", parts[0], Top)
		}
		rel := parts[1:]
		isConfig := slices.Equal(rel, []string{".zone", "config"})
		if !isConfig && !isHistory(rel) {
			continue // the work tree, the repository's config and hooks: not read
		}
		path := filepath.Join(append([]string{dir, Top}, rel...)...)
		switch h.Typeflag {
		case tar.TypeDir:
			if isConfig {
				return found, fmt.Errorf("%s is a folder in the archive, not the settings file", h.Name)
			}
			if err := os.MkdirAll(path, 0o777); err != nil {
				return found, err
			}
			objects = objects || slices.Equal(rel, []string{".git", "objects"})
			continue
		case tar.TypeReg:
		default:
			return found, fmt.Errorf("%s is a link or a device in the archive: an export holds files and folders only", h.Name)
		}
		limit := int64(-1)
		switch {
		case isConfig:
			limit = ConfigMax
		case max > 0:
			limit = max - found.Bytes
		}
		n, err := writeFile(path, tr, limit, isConfig)
		found.Bytes += n
		if err != nil {
			return found, err
		}
		found.Config = found.Config || isConfig
		head = head || slices.Equal(rel, []string{".git", "HEAD"})
		objects = objects || len(rel) > 2 && rel[1] == "objects"
	}
	if !head || !objects {
		return found, fmt.Errorf("not a Zonewiki export: no git repository in it (%s/.git)", Top)
	}
	return found, nil
}

// nameParts: an entry's name as its folders and its own name; an error for
// one that could lead anywhere but into the archive's folder, or that no
// file Zonewiki or git writes has.
func nameParts(name string) ([]string, error) {
	bad := func() ([]string, error) {
		return nil, fmt.Errorf("not a Zonewiki export: the name %q in it", name)
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

// isHistory: rel (under wiki/) is of the repository's history: what names
// its commits and what holds them. Not its config, hooks, index or info/,
// which say what git runs and where else it looks, and not
// objects/info/alternates, which names other folders to read objects from.
func isHistory(rel []string) bool {
	if len(rel) == 0 || rel[0] != ".git" {
		return false
	}
	switch {
	case len(rel) == 1:
		return true
	case len(rel) == 2:
		return rel[1] == "HEAD" || rel[1] == "packed-refs" || rel[1] == "objects" || rel[1] == "refs"
	case rel[1] == "refs":
		return true
	case rel[1] == "objects":
		return !(rel[2] == "info" && len(rel) > 3 && strings.HasSuffix(rel[3], "alternates"))
	}
	return false
}

// writeFile writes what r holds as the new file at path, at most limit
// bytes (-1: any number), and returns how many it wrote. The file must not
// be there: an archive names each file once.
func writeFile(path string, r io.Reader, limit int64, ownerOnly bool) (int64, error) {
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
			return 0, fmt.Errorf("not a Zonewiki export: it holds %s twice", filepath.Base(path))
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
