package encoder

import (
	"fmt"
	"math"
	"math/rand"
	"testing"
)

func plainMatmul(a, b []float32, n, k, m int) []float32 {
	c := make([]float32, n*m)
	for i := 0; i < n; i++ {
		for p := 0; p < k; p++ {
			for j := 0; j < m; j++ {
				c[i*m+j] += a[i*k+p] * b[p*m+j]
			}
		}
	}
	return c
}

func randFloats(r *rand.Rand, n int) []float32 {
	s := make([]float32, n)
	for i := range s {
		s[i] = r.Float32()*2 - 1
	}
	return s
}

// The SIMD kernels give what a plain loop gives, for sizes that fill no
// panel, part of one, and leave rows and columns over.
func TestProductIsAMatmul(t *testing.T) {
	r := rand.New(rand.NewSource(1))
	for _, sz := range [][3]int{{1, 1, 1}, {3, 5, 7}, {4, 8, 2 * width}, {5, 17, 2*width + 3}, {13, 64, 6*width + 1},
		{200, 96, 128}, {130, 700, 40 * width}} {
		n, k, m := sz[0], sz[1], sz[2]
		a, b, bias := randFloats(r, n*k), randFloats(r, k*m), randFloats(r, m)
		want := plainMatmul(a, b, n, k, m)
		for _, withBias := range []bool{false, true} {
			B := pack(nil, b, m, k, m)
			if withBias {
				B = B.withBias(bias)
			}
			c := make([]float32, n*m+1)
			for i := range c {
				c[i] = 99 // a product overwrites, never adds, and stays in its rows
			}
			product(c[:n*m], a, n, &B)
			if c[n*m] != 99 {
				t.Fatalf("%v: wrote past the end", sz)
			}
			for i := range want {
				w := want[i]
				if withBias {
					w += bias[i%m]
				}
				if math.Abs(float64(c[i]-w)) > 1e-4*float64(k) {
					t.Fatalf("%v bias %v: c[%d] = %v, want %v", sz, withBias, i, c[i], w)
				}
			}
		}
	}
}

// A transposed pack is the pack of the transpose, and a matrix inside a
// wider one (rows further apart than they are long) packs like a dense one.
func TestPackTAndStrides(t *testing.T) {
	r := rand.New(rand.NewSource(2))
	for _, sz := range [][2]int{{1, 1}, {5, 3}, {64, 2*width + 5}, {7, 4 * width}} {
		k, m := sz[0], sz[1]
		const ld = 100 // src: m rows of k floats, ld apart
		if k > ld {
			t.Fatal("k > ld")
		}
		src := randFloats(r, m*ld)
		dense := make([]float32, k*m) // the transpose, k×m
		for j := 0; j < m; j++ {
			for p := 0; p < k; p++ {
				dense[p*m+j] = src[j*ld+p]
			}
		}
		want := pack(nil, dense, m, k, m)
		got := make([]float32, packedLen(k, m))
		for i := range got {
			got[i] = 99
		}
		packT(got, src, ld, k, m)
		for i := range got {
			if got[i] != want.data[i] {
				t.Fatalf("%v: packT[%d] = %v, want %v", sz, i, got[i], want.data[i])
			}
		}
		// And pack with a stride: the m×k matrix src holds.
		denseSrc := make([]float32, m*k)
		for j := 0; j < m; j++ {
			copy(denseSrc[j*k:(j+1)*k], src[j*ld:])
		}
		w1, w2 := pack(nil, src, ld, m, k), pack(nil, denseSrc, k, m, k)
		for i := range w1.data {
			if w1.data[i] != w2.data[i] {
				t.Fatalf("%v: pack with a stride [%d] = %v, want %v", sz, i, w1.data[i], w2.data[i])
			}
		}
	}
}

// gegluRow is GELU(a)·b as math.Erf gives it, within the approximation.
func TestGeglu(t *testing.T) {
	r := rand.New(rand.NewSource(8))
	for _, ff := range []int{1, width - 1, width, 3*width + 5, 3072} {
		h := make([]float32, 2*ff)
		for i := range h {
			h[i] = r.Float32()*16 - 8
		}
		h[0] = 0
		g := make([]float32, ff)
		gegluRow(g, h)
		for j := range g {
			x := float64(h[j])
			want := x * (math.Erf(x/math.Sqrt2) + 1) * 0.5 * float64(h[ff+j])
			if math.Abs(float64(g[j])-want) > 2e-6*math.Max(1, math.Abs(float64(h[ff+j]))) {
				t.Fatalf("%d: geglu(%v, %v) = %v, want %v", ff, h[j], h[ff+j], g[j], want)
			}
		}
	}
}

func TestScaleAddMaxAndScale(t *testing.T) {
	r := rand.New(rand.NewSource(9))
	for _, n := range []int{1, width - 1, width, 3*width + 5} {
		s, add := randFloats(r, n), randFloats(r, n+3)
		in := append([]float32(nil), s...)
		mx := scaleAddMax(s, add, 0.125)
		want := float32(math.Inf(-1))
		for j := range s {
			if w := in[j]*0.125 + add[j]; math.Abs(float64(s[j]-w)) > 1e-6 {
				t.Fatalf("%d: [%d] %v, want %v", n, j, s[j], w)
			}
			want = max(want, s[j])
		}
		if mx != want {
			t.Errorf("%d: max %v, want %v", n, mx, want)
		}
		scaled := append([]float32(nil), s...)
		scale(scaled, 3)
		for j := range s {
			if scaled[j] != s[j]*3 {
				t.Fatalf("%d: scale [%d] %v", n, j, scaled[j])
			}
		}
	}
}

// expShift is e^(x-mx) within a few ulps, down to where it is negligible,
// and sums what it gives.
func TestExpShift(t *testing.T) {
	r := rand.New(rand.NewSource(5))
	for _, n := range []int{0, 1, width - 1, width, 3*width + 5, 1000} {
		s := make([]float32, n)
		for i := range s {
			s[i] = r.Float32()*100 - 90 // -90..10
		}
		mx := float32(10)
		in := append([]float32(nil), s...)
		sum := expShift(s, mx)
		var want float64
		for i, x := range in {
			e := math.Exp(float64(x - mx))
			want += e
			if e > 1e-37 && math.Abs(float64(s[i])-e) > 4e-7*e {
				t.Fatalf("%d: e^%v = %v, want %v", n, x-mx, s[i], e)
			}
		}
		if math.Abs(float64(sum)-want) > 1e-5*want+1e-30 {
			t.Errorf("%d: sum %v, want %v", n, sum, want)
		}
	}
}

// The slopes are those the model was exported with (its graph's constant).
func TestAlibiSlopes(t *testing.T) {
	want := []float32{-0.5, -0.25, -0.125, -0.0625, -0.03125, -0.015625, -0.0078125, -0.00390625,
		-0.70710677, -0.35355338, -0.17677669, -0.088388346}
	got := alibiSlopes(12)
	for i := range want {
		if math.Abs(float64(got[i]-want[i])) > 1e-7 {
			t.Fatalf("slopes %v, want %v", got, want)
		}
	}
}

// BenchmarkHidden: one text of n tokens through the model
// (ZONE_ENCODER_MODEL=model.safetensors).
func BenchmarkHidden(b *testing.B) {
	m := testModel(b)
	r := rand.New(rand.NewSource(4))
	for _, n := range []int{16, 219, 512} {
		ids := make([]int, n)
		for i := range ids {
			ids[i] = 1000 + r.Intn(20000)
		}
		b.Run(fmt.Sprint(n), func(b *testing.B) {
			for b.Loop() {
				if _, err := m.Hidden(ids); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

// A text's hidden state is the same in a batch as alone, whatever it is
// batched with (ZONE_ENCODER_MODEL=model.safetensors).
func TestBatchIsEachAlone(t *testing.T) {
	m := testModel(t)
	r := rand.New(rand.NewSource(6))
	var texts [][]int
	for _, n := range []int{1, 3, 40, 17, 512, 2, 33, 600, 64} {
		ids := make([]int, n)
		for i := range ids {
			ids[i] = r.Intn(m.vocab)
		}
		texts = append(texts, ids)
	}
	batch, err := m.HiddenBatch(texts)
	if err != nil {
		t.Fatal(err)
	}
	for i, ids := range texts {
		alone, _ := m.Hidden(ids)
		if len(alone) != len(batch[i]) {
			t.Fatalf("text %d: %d floats, alone %d", i, len(batch[i]), len(alone))
		}
		for j := range alone {
			if alone[j] != batch[i][j] {
				t.Fatalf("text %d [%d]: %v in the batch, %v alone", i, j, batch[i][j], alone[j])
			}
		}
	}
}

// BenchmarkBatch: 64 texts of 31 tokens, the sample wiki's average.
func BenchmarkBatch(b *testing.B) {
	m := testModel(b)
	r := rand.New(rand.NewSource(7))
	texts := make([][]int, 64)
	for i := range texts {
		texts[i] = make([]int, 31)
		for j := range texts[i] {
			texts[i][j] = 1000 + r.Intn(20000)
		}
	}
	for b.Loop() {
		if _, err := m.HiddenBatch(texts); err != nil {
			b.Fatal(err)
		}
	}
	b.ReportMetric(float64(b.Elapsed().Milliseconds())/float64(b.N)/64, "ms/text")
}

// BenchmarkProduct: the model's three shapes of linear layer, for 512 rows.
func BenchmarkProduct(b *testing.B) {
	const n = 512
	r := rand.New(rand.NewSource(3))
	for _, sz := range [][2]int{{768, 768}, {768, 6144}, {3072, 768}} {
		k, m := sz[0], sz[1]
		x, c := randFloats(r, n*k), make([]float32, n*m)
		B := pack(nil, randFloats(r, k*m), m, k, m)
		b.Run(fmt.Sprintf("%dx%d", k, m), func(b *testing.B) {
			for b.Loop() {
				product(c, x, n, &B)
			}
			b.ReportMetric(float64(2*n*k*m)*float64(b.N)/b.Elapsed().Seconds()/1e9, "GFLOP/s")
		})
	}
}
