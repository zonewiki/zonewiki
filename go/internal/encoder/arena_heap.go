//go:build !(linux || darwin)

package encoder

// arena: on systems where Zonewiki maps no memory itself, the weights are
// in Go's heap (arena_mmap.go says what that costs).
type arena struct{}

func (a *arena) halves(n int) []uint16 { return make([]uint16, n) }
func (a *arena) free()                 {}
func (a *arena) held() int             { return 0 }
