//go:build linux || darwin

package encoder

import (
	"syscall"
	"unsafe"
)

// arena: memory taken from the system itself (anonymous mappings), which
// the garbage collector neither scans nor counts. For what lives as long
// as the model and holds no pointers: its weights.
type arena struct {
	maps [][]byte
}

// halves: n zeroed uint16s (float16s), the arena's until free. Where the
// system gives no mapping, they come from Go's heap instead.
func (a *arena) halves(n int) []uint16 {
	if n == 0 {
		return nil
	}
	b, err := syscall.Mmap(-1, 0, 2*n, syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_ANON|syscall.MAP_PRIVATE)
	if err != nil {
		return make([]uint16, n)
	}
	a.maps = append(a.maps, b)
	// A mapping starts at a page, so it is aligned for uint16.
	return unsafe.Slice((*uint16)(unsafe.Pointer(unsafe.SliceData(b))), n)
}

// free gives the arena's memory back: what halves handed out is gone.
func (a *arena) free() {
	for _, b := range a.maps {
		syscall.Munmap(b)
	}
	a.maps = nil
}

// held: the bytes the arena has mapped.
func (a *arena) held() int {
	n := 0
	for _, b := range a.maps {
		n += len(b)
	}
	return n
}
