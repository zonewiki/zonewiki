package encoder

import (
	"runtime"
	"sync"
	"sync/atomic"
)

// maxPanel: the most floats a panel may have (two vectors of 512 bits are
// 32).
const maxPanel = 64

// mat: a row-major matrix in a slice, its rows ld floats apart (a part of
// a wider matrix, such as one attention head's columns).
type mat struct {
	s  []float32
	ld int
}

func (x mat) row(i int) []float32 { return x.s[i*x.ld:] }

// packed: a matrix B (k×m), the right side of a product, laid out as the
// kernel reads it: its columns in panels of pw (two vectors), each panel's
// k rows one after another, so the kernel's inner loop goes straight
// through memory. The last panel is filled up with zeros.
type packed struct {
	k, m   int
	pw     int
	panels int
	data   []float32 // panels × k × pw
	bias   []float32 // panels × pw: what a product starts from; nil for zero
}

func panelsFor(m int) (pw, panels int) {
	pw = 2 * width
	return pw, (m + pw - 1) / pw
}

// packedLen: the floats a packed k×m matrix takes.
func packedLen(k, m int) int {
	pw, panels := panelsFor(m)
	return panels * k * pw
}

// newPacked: a packed k×m matrix of zeros, for setRows to fill.
func newPacked(k, m int) packed {
	pw, panels := panelsFor(m)
	return packed{k: k, m: m, pw: pw, panels: panels, data: make([]float32, panels*k*pw)}
}

// setRows: B's rows p0..p0+n are src's, m floats each, ld apart.
func (B *packed) setRows(p0, n int, src []float32, ld int) {
	k, m, pw := B.k, B.m, B.pw
	for pn := 0; pn < B.panels; pn++ {
		cols := min(pw, m-pn*pw)
		for p := 0; p < n; p++ {
			out := B.data[(pn*k+p0+p)*pw : (pn*k+p0+p+1)*pw]
			copy(out, src[p*ld+pn*pw:p*ld+pn*pw+cols])
			clear(out[cols:])
		}
	}
}

// pack: B's k rows of m floats, ld apart in src, packed into dst (of
// packedLen(k, m), or nil to make it).
func pack(dst, src []float32, ld, k, m int) packed {
	pw, panels := panelsFor(m)
	if dst == nil {
		dst = make([]float32, panels*k*pw)
	}
	B := packed{k: k, m: m, pw: pw, panels: panels, data: dst}
	B.setRows(0, k, src, ld)
	return B
}

// packT: the transpose of src's m rows of k floats (ld apart) packed into
// dst: B[p][j] = src[j][p].
func packT(dst, src []float32, ld, k, m int) packed {
	pw, panels := panelsFor(m)
	for pn := 0; pn < panels; pn++ {
		cols := min(pw, m-pn*pw)
		panel := dst[pn*k*pw : (pn+1)*k*pw]
		if cols < pw {
			clear(panel)
		}
		for jj := 0; jj < cols; jj++ {
			for p, x := range src[(pn*pw+jj)*ld : (pn*pw+jj)*ld+k] {
				panel[p*pw+jj] = x
			}
		}
	}
	return packed{k: k, m: m, pw: pw, panels: panels, data: dst}
}

// withBias: the packed matrix with b (m floats) as what a product starts
// from.
func (B packed) withBias(b []float32) packed {
	B.bias = make([]float32, B.panels*B.pw)
	copy(B.bias, b)
	return B
}

// chunkBytes: how much of a panel the kernel goes through at a time, again
// and again for a job's rows: it should stay in a core's first cache (32
// KB and more, shared by a core's two threads).
const chunkBytes = 16 << 10

// gemm: c = a·B (+ B's bias) for rows i0..i1 of a and c and the panels
// p0..p1 of B's columns, on this goroutine. B's rows (a's columns) are
// taken in chunks: a chunk of a panel stays in the first cache while the
// job's rows go past it, four at a time, and those rows' chunk stays in
// the second while the panels do.
func gemm(c, a mat, B *packed, i0, i1, p0, p1 int) {
	k, pw := B.k, B.pw
	// What the sums start from.
	for i := i0; i < i1; i++ {
		row := c.row(i)[p0*pw : min(p1*pw, B.m)]
		if B.bias != nil {
			copy(row, B.bias[p0*pw:])
		} else {
			clear(row)
		}
	}
	chunk := max(8, chunkBytes/(pw*4))
	var tmp [4 * maxPanel]float32
	for k0 := 0; k0 < k; k0 += chunk {
		k1 := min(k, k0+chunk)
		for pn := p0; pn < p1; pn++ {
			j := pn * pw
			bp := B.data[(pn*k+k0)*pw : (pn*k+k1)*pw]
			if j+pw > B.m {
				// The last panel, not full: by way of tmp.
				cols := B.m - j
				for i := i0; i < i1; i++ {
					t := tmp[:pw]
					clear(t)
					copy(t, c.row(i)[j:j+cols])
					kernel1(t, a.row(i)[k0:k1], bp)
					copy(c.row(i)[j:j+cols], t)
				}
				continue
			}
			i := i0
			for ; i+4 <= i1; i += 4 {
				kernel4(c.row(i)[j:j+pw], c.row(i + 1)[j:j+pw], c.row(i + 2)[j:j+pw], c.row(i + 3)[j:j+pw],
					a.row(i)[k0:k1], a.row(i + 1)[k0:k1], a.row(i + 2)[k0:k1], a.row(i + 3)[k0:k1], bp)
			}
			for ; i < i1; i++ {
				kernel1(c.row(i)[j:j+pw], a.row(i)[k0:k1], bp)
			}
		}
	}
}

// blockRows: the rows of a job, at most: a chunk of them (chunkBytes/8
// floats a row at most) should stay in a core's second cache.
const blockRows = 512

// product: c = a·B (+ bias) for n rows, c and a dense, on all CPUs. A job
// is a block of rows and some panels of B, three jobs a CPU or more, so
// that none waits long for the last.
func product(c, a []float32, n int, B *packed) {
	blocks := (n + blockRows - 1) / blockRows
	rows := ((n+blocks-1)/blocks + 3) &^ 3
	blocks = (n + rows - 1) / rows
	group := max(1, B.panels*blocks/(3*runtime.GOMAXPROCS(0)))
	groups := (B.panels + group - 1) / group
	C, A := mat{c, B.m}, mat{a, B.k}
	parallel(groups*blocks, func(job int) {
		g, b := job/blocks, job%blocks
		gemm(C, A, B, b*rows, min(n, (b+1)*rows), g*group, min(B.panels, (g+1)*group))
	})
}

// parallel runs f(0..n-1) on all CPUs.
func parallel(n int, f func(int)) {
	workers := min(runtime.GOMAXPROCS(0), n)
	if workers <= 1 {
		for i := 0; i < n; i++ {
			f(i)
		}
		return
	}
	var next atomic.Int64
	var wg sync.WaitGroup
	for range workers {
		wg.Go(func() {
			for {
				i := int(next.Add(1)) - 1
				if i >= n {
					return
				}
				f(i)
			}
		})
	}
	wg.Wait()
}

// parallelRows: parallel, in blocks of rows, for work too small per row to
// hand out one by one.
func parallelRows(n int, f func(int)) {
	const block = 16
	parallel((n+block-1)/block, func(b int) {
		for i := b * block; i < min(n, (b+1)*block); i++ {
			f(i)
		}
	})
}
