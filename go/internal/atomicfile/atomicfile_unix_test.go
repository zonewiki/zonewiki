//go:build unix

package atomicfile

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

// Write: a new file's mode as os.WriteFile gives it (the umask's), an
// old one's kept, a link written through.
func TestAtomicWriteModesAndLinks(t *testing.T) {
	dir := t.TempDir()
	old := syscall.Umask(0o002)
	defer syscall.Umask(old)
	fresh := filepath.Join(dir, "new.md")
	if err := Write(fresh, "# New\n"); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(fresh); st.Mode().Perm() != 0o664 {
		t.Errorf("new file: %o", st.Mode().Perm())
	}
	_ = os.Chmod(fresh, 0o600)
	if err := Write(fresh, "# Again\n"); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(fresh); st.Mode().Perm() != 0o600 {
		t.Errorf("kept: %o", st.Mode().Perm())
	}
	target, link := filepath.Join(dir, "elsewhere.md"), filepath.Join(dir, "link.md")
	_ = os.WriteFile(target, []byte("# Old\n"), 0o644)
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	if err := Write(link, "# Through\n"); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(target)
	if st, _ := os.Lstat(link); st.Mode()&os.ModeSymlink == 0 || string(b) != "# Through\n" {
		t.Errorf("link replaced, or its file not written: %q", b)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 3 {
		t.Errorf("left over: %v", entries)
	}
}

// WriteOwnerOnly: the file is its owner's alone, a new one and an old one
// that others could read.
func TestWriteOwnerOnly(t *testing.T) {
	dir := t.TempDir()
	fresh, old := filepath.Join(dir, "new.json"), filepath.Join(dir, "old.json")
	_ = os.WriteFile(old, []byte("{}\n"), 0o644)
	for _, p := range []string{fresh, old} {
		if err := WriteOwnerOnly(p, "{}\n"); err != nil {
			t.Fatal(err)
		}
		if st, _ := os.Stat(p); st.Mode().Perm() != 0o600 {
			t.Errorf("%s: %o", p, st.Mode().Perm())
		}
	}
}
