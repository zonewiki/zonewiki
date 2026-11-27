package encoder

import (
	"math/rand"
	"testing"
)

// BenchmarkKernel: the kernel alone, on one CPU, everything in the first
// cache: what the arithmetic can do at best.
func BenchmarkKernel(b *testing.B) {
	const k = 128
	pw := 2 * width
	r := rand.New(rand.NewSource(10))
	a, bp, c := randFloats(r, 4*k), randFloats(r, k*pw), make([]float32, 4*pw)
	for b.Loop() {
		kernel4(c[:pw], c[pw:2*pw], c[2*pw:3*pw], c[3*pw:], a[:k], a[k:2*k], a[2*k:3*k], a[3*k:], bp)
	}
	b.ReportMetric(float64(2*4*k*pw)*float64(b.N)/b.Elapsed().Seconds()/1e9, "GFLOP/s")
}
