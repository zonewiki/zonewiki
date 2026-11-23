// Package atomicfile writes a file so that no reader ever sees half of it.
package atomicfile

import (
	"os"
	"path/filepath"
)

// Write writes a temp file and renames it into place: a reader sees
// the whole old file or the whole new one, never a half-written one, and a
// crash in the middle leaves the old one. The file keeps its permissions
// (a new one: 0666 less the umask), a link stays a link.
func Write(path, text string) error { return write(path, text, 0) }

// WriteOwnerOnly: Write for a file only its owner reads, whatever mode it
// had (0600): Zonewiki's own state that holds users' words (what they searched,
// what they saw, their tasks' records), which other accounts on the host
// have no business reading, as they have none reading the settings file.
func WriteOwnerOnly(path, text string) error { return write(path, text, 0o600) }

// write: Write, the file's mode mode (0: as it was, or a new file's).
func write(path, text string, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil { // _tasks/ in a new wiki
		return err
	}
	// A link (a page linked into the wiki) is written through, as
	// os.WriteFile would: the file it points at changes, the link stays.
	if real, err := filepath.EvalSymlinks(path); err == nil {
		path = real
	}
	// A new file gets the mode os.WriteFile would give it (0666 less the
	// umask, so a group-shared wiki stays group-writable): it is made
	// empty first, then replaced like an old one, keeping its mode.
	if f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o666); err == nil {
		f.Close()
	}
	st, err := os.Stat(path)
	if err != nil {
		return err
	}
	if mode == 0 {
		mode = st.Mode().Perm()
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	_, err = tmp.WriteString(text)
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Chmod(tmp.Name(), mode)
	}
	if err == nil {
		err = os.Rename(tmp.Name(), path)
	}
	if err != nil {
		os.Remove(tmp.Name())
	}
	return err
}
