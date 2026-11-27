package encoder

import "math"

// The weights are float16 (IEEE half precision: a sign, 5 bits of
// exponent, 10 of mantissa), as the model's file has them, and stay so in
// memory: half as much of it as float32, for a widening where they are
// used (widen in the kernels' files; half here, one at a time).

// half: a float16's bits as a float32, exactly.
func half(h uint16) float32 {
	sign := uint32(h>>15) << 31
	exp := uint32(h>>10) & 0x1f
	man := uint32(h) & 0x3ff
	switch {
	case exp == 0 && man == 0:
		return math.Float32frombits(sign)
	case exp == 0: // subnormal: shift the mantissa up to its leading 1
		e := uint32(127 - 15 + 1)
		for man&0x400 == 0 {
			man <<= 1
			e--
		}
		return math.Float32frombits(sign | e<<23 | (man&0x3ff)<<13)
	case exp == 31: // infinity, NaN
		return math.Float32frombits(sign | 0xff<<23 | man<<13)
	}
	return math.Float32frombits(sign | (exp+127-15)<<23 | man<<13)
}

// halfSlot: where a panel row's column jj (of 2w) is among its float16s.
// The row's two vectors are kept with their halves by turns, the first's
// in the even places, since that is how widen takes two float32 vectors
// out of one of float16: a 32-bit lane's low half and its high half.
func halfSlot(jj, w int) int {
	if jj < w {
		return 2 * jj
	}
	return 2*(jj-w) + 1
}

// newPackedHalf: a packed k×m matrix of float16 zeros, from alloc, for
// setColumnHalves to fill.
func newPackedHalf(alloc func(int) []uint16, k, m int) packed {
	pw, panels := panelsFor(m)
	return packed{k: k, m: m, pw: pw, panels: panels, half: alloc(panels * k * pw)}
}

// setColumnHalves: B's columns j0..j0+n are src's rows, k float16s each:
// the matrix comes as its transpose (a PyTorch Linear's weight, out×in).
func (B *packed) setColumnHalves(j0, n int, src []uint16) {
	k, pw := B.k, B.pw
	for c := 0; c < n; c++ {
		j := j0 + c
		pn := j / pw
		panel := B.half[pn*k*pw : (pn+1)*k*pw]
		slot := halfSlot(j%pw, pw/2)
		for p, h := range src[c*k : (c+1)*k] {
			panel[p*pw+slot] = h
		}
	}
}
