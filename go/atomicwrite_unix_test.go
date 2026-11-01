//go:build unix

package main

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

// atomicWrite: a new file's mode as os.WriteFile gives it (the umask's), an
// old one's kept, a link written through.
func TestAtomicWriteModesAndLinks(t *testing.T) {
	dir := t.TempDir()
	old := syscall.Umask(0o002)
	defer syscall.Umask(old)
	fresh := filepath.Join(dir, "new.md")
	if err := atomicWrite(fresh, "# New\n"); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(fresh); st.Mode().Perm() != 0o664 {
		t.Errorf("new file: %o", st.Mode().Perm())
	}
	_ = os.Chmod(fresh, 0o600)
	if err := atomicWrite(fresh, "# Again\n"); err != nil {
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
	if err := atomicWrite(link, "# Through\n"); err != nil {
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
