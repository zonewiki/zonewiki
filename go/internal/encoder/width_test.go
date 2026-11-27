package encoder

import "testing"

// TestWidth says which vectors this run has (go test -v): with
// GODEBUG=cpu.avx512f=off, say, a machine with AVX-512 is tested as one
// without.
func TestWidth(t *testing.T) {
	t.Logf("vectorised: %v, %d floats a vector", Vectorised, width)
}
