//go:build !goexperiment.simd

package encoder

// The arithmetic without vectors, for a build without GOEXPERIMENT=simd
// (a plain `go build`): the same results within rounding as
// kernels_simd.go's, many times slower. It keeps Zonewiki building with
// any Go, and says in loops what the vector code computes.

import "math"

// Vectorised: whether this build does its arithmetic in vectors.
const Vectorised = false

// width: half a panel's floats (kernels_simd.go's vector width).
const width = 8

// kernel4: four rows of c, a panel wide: c_r += a_r·bp.
func kernel4(c0, c1, c2, c3, a0, a1, a2, a3, bp []float32) {
	kernel1(c0, a0, bp)
	kernel1(c1, a1, bp)
	kernel1(c2, a2, bp)
	kernel1(c3, a3, bp)
}

// kernel1: c += a·bp, bp's rows a panel (2·width floats) each.
func kernel1(c, a, bp []float32) {
	const pw = 2 * width
	c = c[:pw]
	for p, x := range a {
		row := bp[p*pw : p*pw+pw]
		for j := range c {
			c[j] += x * row[j]
		}
	}
}

// widen: dst is src's float16s as float32s. src is in rows of 2·width,
// a row's two halves by turns (halfSlot).
func widen(dst []float32, src []uint16) {
	const pw = 2 * width
	for r := 0; r+pw <= len(src); r += pw {
		for i := 0; i < width; i++ {
			dst[r+i], dst[r+width+i] = half(src[r+2*i]), half(src[r+2*i+1])
		}
	}
}

// scaleAddMax: s[j] = s[j]·scale + add[j] for all j, and the largest of
// them (attention's scores, scaled, with their ALiBi bias).
func scaleAddMax(s, add []float32, scale float32) float32 {
	mx := float32(math.Inf(-1))
	for j := range s {
		s[j] = s[j]*scale + add[j]
		mx = max(mx, s[j])
	}
	return mx
}

// expShift: s[j] = e^(s[j]-mx) for all j, with mx at least every s[j] (a
// softmax's step), and their sum.
func expShift(s []float32, mx float32) float32 {
	var total float32
	for j := range s {
		s[j] = float32(math.Exp(float64(s[j] - mx)))
		total += s[j]
	}
	return total
}

// scale: s[j] *= f.
func scale(s []float32, f float32) {
	for j := range s {
		s[j] *= f
	}
}

// gegluRow: g[j] = GELU(h[j])·h[ff+j], ff = len(g), GELU the exact one:
// x·(1+erf(x/√2))/2.
func gegluRow(g, h []float32) {
	ff := len(g)
	for j := range g {
		x := h[j]
		g[j] = x * (float32(math.Erf(float64(x/math.Sqrt2))) + 1) * 0.5 * h[ff+j]
	}
}
