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

// floats: n zeroed floats, the arena's until free. Where the system gives
// no mapping, they come from Go's heap instead.
func (a *arena) floats(n int) []float32 {
	if n == 0 {
		return nil
	}
	b, err := syscall.Mmap(-1, 0, 4*n, syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_ANON|syscall.MAP_PRIVATE)
	if err != nil {
		return make([]float32, n)
	}
	a.maps = append(a.maps, b)
	// A mapping starts at a page, so it is aligned for float32.
	return unsafe.Slice((*float32)(unsafe.Pointer(unsafe.SliceData(b))), n)
}

// free gives the arena's memory back: what floats handed out is gone.
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
