// Package wikiarchive writes and reads the file a whole wiki moves in
// (Moving a wiki in docs/spec/deployment.md): a gzipped tar with one folder,
// wiki/, that holds a clone of the wiki's git repository and, in
// .zone/config, its settings file.
//
// Reading takes an archive for what anyone may have made: of all it holds,
// only the repository's history (its objects and refs) and the settings
// file are written out, as plain files; the repository's config, its hooks
// and its work tree stay in the archive.
//
// What makes reading safe isn't tar's, so it is in two parts (unpack.go,
// source.go): a source of entries (Tar, or Zip) and one unpacking with
// every check (Unpack), told by its caller what to keep. Read is the wiki's
// caller.
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
// Read stops with ErrTooBig. The settings file has a limit of its own
// (ConfigMax).
//
// An archive that isn't one of a wiki is an error: something beside wiki/,
// a name that leads out of it, a link or a device where the history or the
// settings file should be, the same file twice, no repository.
func Read(r io.Reader, dir string, max int64) (Found, error) {
	var found Found
	var head, objects bool
	n, err := Unpack(Tar(r), dir, Rules{
		Not:      "not a Zonewiki export",
		MaxBytes: max,
		Keep: func(name []string, kind Kind) (Take, error) {
			if name[0] != Top {
				return Take{}, fmt.Errorf("not a Zonewiki export: it holds %q beside %s/", name[0], Top)
			}
			rel := name[1:]
			isConfig := slices.Equal(rel, []string{".zone", "config"})
			switch {
			case isConfig && kind == Folder:
				return Take{}, fmt.Errorf("%s is a folder in the archive, not the settings file", strings.Join(name, "/"))
			case isConfig:
				found.Config = true // a link or a device is refused by Unpack
				return Take{Keep: true, Max: ConfigMax, Private: true}, nil
			case !isHistory(rel):
				return Take{}, nil // the work tree, the repository's config and hooks: not read
			case kind == Folder:
				objects = objects || slices.Equal(rel, []string{".git", "objects"})
			case kind == File:
				head = head || slices.Equal(rel, []string{".git", "HEAD"})
				objects = objects || len(rel) > 2 && rel[1] == "objects"
			}
			return Take{Keep: true}, nil
		},
	})
	found.Bytes = n
	if err != nil {
		return found, err
	}
	if !head || !objects {
		return found, fmt.Errorf("not a Zonewiki export: no git repository in it (%s/.git)", Top)
	}
	return found, nil
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
