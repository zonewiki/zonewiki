//go:build goexperiment.simd

package encoder

// The arithmetic in vectors, on Go's simd package (GOEXPERIMENT=simd at
// build time: AVX-512, AVX2 or NEON, whatever the machine has). A build
// without the experiment has kernels_plain.go's loops instead, which give
// the same within rounding, many times slower.

import (
	"math"
	"simd"
)

// Vectorised: whether this build does its arithmetic in vectors.
const Vectorised = true

// width: floats per vector on this machine (16 with AVX-512, 8 with AVX2,
// 4 with NEON).
var width = vectorWidth()

// vectorWidth: in a function, since a package variable may not be set
// from simd directly (a known bug of the experiment).
//
//go:noinline
func vectorWidth() int { return simd.BroadcastFloat32s(0).Len() }

// kernel4: four rows of c, a panel wide: c_r += a_r·bp, in 8 accumulators,
// two of bp's rows per turn. Measured on a Ryzen 9700X (AVX-512), in the
// first cache: about one multiply-add of 16 floats a cycle, whatever the
// loop's shape (4×3 and 6×2 accumulators, 2×4 and 1×8 tiles, a's rows
// interleaved, multiply then add, slices cut instead of indexed were all
// as fast or slower).
func kernel4(c0, c1, c2, c3, a0, a1, a2, a3, bp []float32) {
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	k := len(a0)
	a1, a2, a3 = a1[:k], a2[:k], a3[:k]
	bp = bp[:k*2*w]
	c00, c01 := simd.LoadFloat32s(c0), simd.LoadFloat32s(c0[w:])
	c10, c11 := simd.LoadFloat32s(c1), simd.LoadFloat32s(c1[w:])
	c20, c21 := simd.LoadFloat32s(c2), simd.LoadFloat32s(c2[w:])
	c30, c31 := simd.LoadFloat32s(c3), simd.LoadFloat32s(c3[w:])
	p := 0
	for ; p+2 <= k; p += 2 {
		rows := bp[p*2*w : (p+2)*2*w]
		b0, b1 := simd.LoadFloat32s(rows), simd.LoadFloat32s(rows[w:])
		x := simd.BroadcastFloat32s(a0[p])
		c00, c01 = x.MulAdd(b0, c00), x.MulAdd(b1, c01)
		x = simd.BroadcastFloat32s(a1[p])
		c10, c11 = x.MulAdd(b0, c10), x.MulAdd(b1, c11)
		x = simd.BroadcastFloat32s(a2[p])
		c20, c21 = x.MulAdd(b0, c20), x.MulAdd(b1, c21)
		x = simd.BroadcastFloat32s(a3[p])
		c30, c31 = x.MulAdd(b0, c30), x.MulAdd(b1, c31)
		b0, b1 = simd.LoadFloat32s(rows[2*w:]), simd.LoadFloat32s(rows[3*w:])
		x = simd.BroadcastFloat32s(a0[p+1])
		c00, c01 = x.MulAdd(b0, c00), x.MulAdd(b1, c01)
		x = simd.BroadcastFloat32s(a1[p+1])
		c10, c11 = x.MulAdd(b0, c10), x.MulAdd(b1, c11)
		x = simd.BroadcastFloat32s(a2[p+1])
		c20, c21 = x.MulAdd(b0, c20), x.MulAdd(b1, c21)
		x = simd.BroadcastFloat32s(a3[p+1])
		c30, c31 = x.MulAdd(b0, c30), x.MulAdd(b1, c31)
	}
	if p < k {
		rows := bp[p*2*w : (p+1)*2*w]
		b0, b1 := simd.LoadFloat32s(rows), simd.LoadFloat32s(rows[w:])
		x := simd.BroadcastFloat32s(a0[p])
		c00, c01 = x.MulAdd(b0, c00), x.MulAdd(b1, c01)
		x = simd.BroadcastFloat32s(a1[p])
		c10, c11 = x.MulAdd(b0, c10), x.MulAdd(b1, c11)
		x = simd.BroadcastFloat32s(a2[p])
		c20, c21 = x.MulAdd(b0, c20), x.MulAdd(b1, c21)
		x = simd.BroadcastFloat32s(a3[p])
		c30, c31 = x.MulAdd(b0, c30), x.MulAdd(b1, c31)
	}
	c00.Store(c0)
	c01.Store(c0[w:])
	c10.Store(c1)
	c11.Store(c1[w:])
	c20.Store(c2)
	c21.Store(c2[w:])
	c30.Store(c3)
	c31.Store(c3[w:])
}

// kernel1: kernel4 for one row.
func kernel1(c, a, bp []float32) {
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	k := len(a)
	bp = bp[:k*2*w]
	c0, c1 := simd.LoadFloat32s(c), simd.LoadFloat32s(c[w:])
	for p := 0; p < k; p++ {
		row := bp[p*2*w : p*2*w+2*w]
		x := simd.BroadcastFloat32s(a[p])
		c0, c1 = x.MulAdd(simd.LoadFloat32s(row), c0), x.MulAdd(simd.LoadFloat32s(row[w:]), c1)
	}
	c0.Store(c)
	c1.Store(c[w:])
}

// widen: dst is src's float16s as float32s, exactly (no infinity or NaN
// among them). src is in rows of two vectors, their halves by turns
// (halfSlot): read as one vector of 32-bit lanes, a lane's low half is the
// first vector's float and its high half the second's. A float16's 15
// bits, moved up 13, are a float32 that is 2^112 too small, subnormals
// too; the sign goes on after.
func widen(dst []float32, src []uint16) {
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	mask, sign := simd.BroadcastUint32s(0x7fff), simd.BroadcastUint32s(0x8000)
	magic := simd.BroadcastFloat32s(0x1p112)
	dst = dst[:len(src)]
	for i := 0; i+2*w <= len(src); i += 2 * w {
		lo := simd.LoadUint16s(src[i:]).ReshapeToUint32s()
		hi := lo.ShiftAllRight(16)
		a := lo.And(mask).ShiftAllLeft(13).BitsToFloat32().Mul(magic).ToBits().Or(lo.And(sign).ShiftAllLeft(16)).BitsToFloat32()
		b := hi.And(mask).ShiftAllLeft(13).BitsToFloat32().Mul(magic).ToBits().Or(hi.And(sign).ShiftAllLeft(16)).BitsToFloat32()
		a.Store(dst[i:])
		b.Store(dst[i+w:])
	}
}

// expConsts: what exp needs, made once per loop.
type expConsts struct {
	lo, log2e, magic, ln2hi, ln2lo simd.Float32s
	p0, p1, p2, p3, p4, p5, one    simd.Float32s
	bias                           simd.Uint32s
}

func newExpConsts() expConsts {
	return expConsts{
		lo:    simd.BroadcastFloat32s(-87.33654),
		log2e: simd.BroadcastFloat32s(math.Log2E),
		// 1.5·2^23: adding it rounds to an integer, kept in the low bits.
		magic: simd.BroadcastFloat32s(12582912),
		ln2hi: simd.BroadcastFloat32s(-0.693359375),
		ln2lo: simd.BroadcastFloat32s(2.12194440e-4),
		p0:    simd.BroadcastFloat32s(1.9875691500e-4),
		p1:    simd.BroadcastFloat32s(1.3981999507e-3),
		p2:    simd.BroadcastFloat32s(8.3334519073e-3),
		p3:    simd.BroadcastFloat32s(4.1665795894e-2),
		p4:    simd.BroadcastFloat32s(1.6666665459e-1),
		p5:    simd.BroadcastFloat32s(5.0000001201e-1),
		one:   simd.BroadcastFloat32s(1),
		// The rounded sum's bits are 0x4B400000+n: take its 0x400000 off
		// and add the exponent's bias (mod 2^32; the bits above 9 shift out).
		bias: simd.BroadcastUint32s(1<<32 - 0x400000 + 127),
	}
}

// exp: e^x for x ≤ 0 (and a little above), in vectors: e^x = 2^n·e^r with
// n the nearest integer to x/ln 2 and |r| ≤ ln2/2, e^r by Cephes'
// polynomial for expf (within about an ulp), 2^n made in the exponent's
// bits. Under e^-87 (2^-126) the result is e^-87: next to 1, nothing.
func (k *expConsts) exp(x simd.Float32s) simd.Float32s {
	x = x.Max(k.lo)
	t := x.MulAdd(k.log2e, k.magic)
	n := t.Sub(k.magic)
	r := n.MulAdd(k.ln2hi, x)
	r = n.MulAdd(k.ln2lo, r)
	p := k.p0.MulAdd(r, k.p1)
	p = p.MulAdd(r, k.p2)
	p = p.MulAdd(r, k.p3)
	p = p.MulAdd(r, k.p4)
	p = p.MulAdd(r, k.p5)
	y := p.Mul(r).MulAdd(r, r).Add(k.one) // p·r² + r + 1
	return y.Mul(t.ToBits().Add(k.bias).ShiftAllLeft(23).BitsToFloat32())
}

// lanesSum, lanesMax: a vector's floats added up, and the largest.
func lanesSum(v simd.Float32s) float32 {
	var lanes [maxPanel]float32
	v.Store(lanes[:width])
	var s float32
	for _, x := range lanes[:width] {
		s += x
	}
	return s
}

func lanesMax(v simd.Float32s) float32 {
	var lanes [maxPanel]float32
	v.Store(lanes[:width])
	m := lanes[0]
	for _, x := range lanes[1:width] {
		m = max(m, x)
	}
	return m
}

// scaleAddMax: s[j] = s[j]·scale + add[j] for all j, and the largest of
// them (attention's scores, scaled, with their ALiBi bias).
func scaleAddMax(s, add []float32, scale float32) float32 {
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	add = add[:len(s)]
	mx := float32(math.Inf(-1))
	j := 0
	if len(s) >= w {
		vs, vmx := simd.BroadcastFloat32s(scale), simd.BroadcastFloat32s(mx)
		for ; j+w <= len(s); j += w {
			x := simd.LoadFloat32s(s[j:]).MulAdd(vs, simd.LoadFloat32s(add[j:]))
			x.Store(s[j:])
			vmx = vmx.Max(x)
		}
		mx = lanesMax(vmx)
	}
	for ; j < len(s); j++ {
		s[j] = s[j]*scale + add[j]
		mx = max(mx, s[j])
	}
	return mx
}

// expShift: s[j] = e^(s[j]-mx) for all j, with mx at least every s[j] (a
// softmax's step), and their sum.
func expShift(s []float32, mx float32) float32 {
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	k := newExpConsts()
	vmx := simd.BroadcastFloat32s(mx)
	var sum simd.Float32s
	j := 0
	for ; j+w <= len(s); j += w {
		y := k.exp(simd.LoadFloat32s(s[j:]).Sub(vmx))
		y.Store(s[j:])
		sum = sum.Add(y)
	}
	total := lanesSum(sum)
	for ; j < len(s); j++ {
		s[j] = float32(math.Exp(float64(s[j] - mx)))
		total += s[j]
	}
	return total
}

// scale: s[j] *= f.
func scale(s []float32, f float32) {
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	vf := simd.BroadcastFloat32s(f)
	j := 0
	for ; j+w <= len(s); j += w {
		simd.LoadFloat32s(s[j:]).Mul(vf).Store(s[j:])
	}
	for ; j < len(s); j++ {
		s[j] *= f
	}
}

// gegluRow: g[j] = GELU(h[j])·h[ff+j], ff = len(g), GELU the exact one:
// x·(1+erf(x/√2))/2. With z = |x|/√2, erfc(z) = poly(t)·e^(-z²), t =
// 1/(1+pz) (Abramowitz and Stegun 7.1.26, within 1.5e-7), and 1+erf(x/√2)
// is 2-erfc(z) for x ≥ 0, erfc(z) below: nothing cancels on either side.
func gegluRow(g, h []float32) {
	ff := len(g)
	a, b := h[:ff], h[ff:2*ff]
	w := simd.Float32s{}.Len() // a constant where the compiler makes the function per width
	j := 0
	if ff >= w {
		k := newExpConsts()
		var (
			zero, half, two = simd.BroadcastFloat32s(0), simd.BroadcastFloat32s(0.5), simd.BroadcastFloat32s(2)
			rsqrt2          = simd.BroadcastFloat32s(1 / math.Sqrt2)
			p               = simd.BroadcastFloat32s(0.3275911)
			a1, a2, a3      = simd.BroadcastFloat32s(0.254829592), simd.BroadcastFloat32s(-0.284496736), simd.BroadcastFloat32s(1.421413741)
			a4, a5          = simd.BroadcastFloat32s(-1.453152027), simd.BroadcastFloat32s(1.061405429)
		)
		for ; j+w <= ff; j += w {
			x := simd.LoadFloat32s(a[j:])
			z := x.Abs().Mul(rsqrt2)
			t := k.one.Div(z.MulAdd(p, k.one))
			poly := a5.MulAdd(t, a4)
			poly = poly.MulAdd(t, a3)
			poly = poly.MulAdd(t, a2)
			poly = poly.MulAdd(t, a1)
			erfc := poly.Mul(t).Mul(k.exp(z.Mul(z).Neg()))
			onePlusErf := two.Sub(erfc).IfElse(x.GreaterEqual(zero), erfc)
			x.Mul(onePlusErf).Mul(half).Mul(simd.LoadFloat32s(b[j:])).Store(g[j:])
		}
	}
	for ; j < ff; j++ {
		x := a[j]
		g[j] = x * (float32(math.Erf(float64(x/math.Sqrt2))) + 1) * 0.5 * b[j]
	}
}
