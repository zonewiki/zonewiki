//go:build !(linux || darwin)

package encoder

// arena: on systems where Zonewiki maps no memory itself, the weights are
// in Go's heap (arena_mmap.go says what that costs).
type arena struct{}

func (a *arena) floats(n int) []float32 { return make([]float32, n) }
func (a *arena) free()                  {}
func (a *arena) held() int              { return 0 }
