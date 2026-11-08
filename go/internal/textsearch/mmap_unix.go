//go:build unix

package textsearch

import (
	"os"
	"syscall"
)

// mapFile: the file's first size bytes mapped read-only, and how to undo it.
func mapFile(f *os.File, size int) ([]byte, func([]byte), error) {
	data, err := syscall.Mmap(int(f.Fd()), 0, size, syscall.PROT_READ, syscall.MAP_SHARED)
	if err != nil {
		return nil, nil, err
	}
	return data, func(d []byte) { syscall.Munmap(d) }, nil
}
