package encoder

import (
	"math"
	"math/rand"
	"testing"
)

// toHalf: f as a float16, for the tests; f must be one exactly.
func toHalf(t testing.TB, f float32) uint16 {
	b := math.Float32bits(f)
	sign := uint16(b>>31) << 15
	exp := int(b>>23&0xff) - 127 + 15
	man := b & 0x7fffff
	var h uint16
	switch {
	case b<<1 == 0:
		h = sign
	case exp <= 0:
		h = sign | uint16((man|0x800000)>>(14-exp))
	default:
		h = sign | uint16(exp)<<10 | uint16(man>>13)
	}
	if math.Float32bits(half(h)) != b {
		t.Fatalf("%v is no float16", f)
	}
	return h
}

// randomHalves: n float16s of ordinary size (between 1/32 and 1, either
// sign), as weights are.
func randomHalves(r *rand.Rand, n int) []uint16 {
	s := make([]uint16, n)
	for i := range s {
		s[i] = uint16(r.Intn(2))<<15 | uint16(10+r.Intn(5))<<10 | uint16(r.Intn(1024))
	}
	return s
}

func TestHalf(t *testing.T) {
	for h, want := range map[uint16]float64{
		0x0000: 0, 0x3c00: 1, 0xc000: -2, 0x3555: 0.333251953125, 0x7bff: 65504,
		0x0001: math.Pow(2, -24), 0x03ff: 1023 * math.Pow(2, -24), 0x0400: math.Pow(2, -14),
		0x7c00: math.Inf(1), 0xfc00: math.Inf(-1),
	} {
		if got := float64(half(h)); got != want {
			t.Errorf("half(%#04x) = %v, want %v", h, got, want)
		}
	}
	if math.Float32bits(half(0x8000)) != 1<<31 {
		t.Error("half(-0) is not -0")
	}
	if x := half(0x7e00); x == x {
		t.Error("half(NaN) is a number")
	}
	// Every float16 that is a number is another float32, in order, and
	// comes back.
	prev := float32(math.Inf(-1))
	for h := 0xfbff; h >= 0x8000; h-- { // the negative ones, rising
		if x := half(uint16(h)); x < prev || toHalf(t, x) != uint16(h) {
			t.Fatalf("half(%#04x) = %v after %v", h, x, prev)
		} else {
			prev = x
		}
	}
	for h := 0; h <= 0x7bff; h++ {
		if x := half(uint16(h)); x < prev || toHalf(t, x) != uint16(h) {
			t.Fatalf("half(%#04x) = %v after %v", h, x, prev)
		} else {
			prev = x
		}
	}
}

// widen gives what half gives, bit for bit, for every float16 that is a
// number (subnormals and both zeros too), in its order: a row's first
// vector from the even places, the second from the odd.
func TestWidenIsHalf(t *testing.T) {
	w := width
	var src []uint16
	for h := 0; h < 1<<16; h++ {
		if h>>10&0x1f != 31 {
			src = append(src, uint16(h))
		}
	}
	for len(src)%(2*w) != 0 {
		src = append(src, 0)
	}
	dst := make([]float32, len(src)+1)
	dst[len(src)] = 99
	widen(dst[:len(src)], src)
	if dst[len(src)] != 99 {
		t.Fatal("wrote past the end")
	}
	for r := 0; r < len(src); r += 2 * w {
		for jj := 0; jj < 2*w; jj++ {
			got, want := dst[r+jj], half(src[r+halfSlot(jj, w)])
			if math.Float32bits(got) != math.Float32bits(want) {
				t.Fatalf("widen(%#04x) = %v, want %v", src[r+halfSlot(jj, w)], got, want)
			}
		}
	}
}

// A matrix given as its transpose in float16, some rows at a time, is
// the matrix packed in float32, once widened; and a product with it is
// the product with that, bit for bit, bias and all.
func TestPackedHalves(t *testing.T) {
	r := rand.New(rand.NewSource(13))
	for _, sz := range [][3]int{{1, 1, 1}, {5, 3, 7}, {64, 2*width + 5, 9}, {7, 4 * width, 4}, {300, 6*width + 1, 130}} {
		k, m, n := sz[0], sz[1], sz[2] // B is k×m, the file's matrix m×k; n rows of a
		file := randomHalves(r, m*k)
		dense := make([]float32, k*m)
		for j := 0; j < m; j++ {
			for p := 0; p < k; p++ {
				dense[p*m+j] = half(file[j*k+p])
			}
		}
		want := pack(nil, dense, m, k, m)
		got := newPackedHalf(func(n int) []uint16 { return make([]uint16, n) }, k, m)
		for j := 0; j < m; j += 5 { // five of its rows at a time
			c := min(5, m-j)
			got.setColumnHalves(j, c, file[j*k:(j+c)*k])
		}
		wide := make([]float32, len(got.half))
		widen(wide, got.half)
		for i := range wide {
			if wide[i] != want.data[i] {
				t.Fatalf("%v: packed [%d] = %v, want %v", sz, i, wide[i], want.data[i])
			}
		}
		bias := randFloats(r, m)
		got, want = got.withBias(bias), want.withBias(bias)
		a := randFloats(r, n*k)
		c1, c2 := make([]float32, n*m), make([]float32, n*m)
		product(c1, a, n, &got)
		product(c2, a, n, &want)
		for i := range c1 {
			if c1[i] != c2[i] {
				t.Fatalf("%v: product [%d] = %v, with float32 weights %v", sz, i, c1[i], c2[i])
			}
		}
	}
}
