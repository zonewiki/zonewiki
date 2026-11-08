//go:build !unix

package textsearch

import (
	"io"
	"os"
)

// mapFile: where there is no syscall.Mmap (Windows), the file read into
// memory: the same index, held in Go's heap.
func mapFile(f *os.File, size int) ([]byte, func([]byte), error) {
	data := make([]byte, size)
	if _, err := io.ReadFull(f, data); err != nil {
		return nil, nil, err
	}
	return data, func([]byte) {}, nil
}
