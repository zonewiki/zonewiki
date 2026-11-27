package encoder

import "math"

// geglu: g = GELU(h's first half) · h's second half, row by row.
func geglu(g, h []float32, n, ff int) {
	parallelRows(n, func(i int) { gegluRow(g[i*ff:(i+1)*ff], h[i*2*ff:(i+1)*2*ff]) })
}

// attention: what attention needs for a batch of texts (rows
// starts[t]..starts[t+1] each), the same in every layer: its buffers, its
// jobs and the ALiBi bias.
type attention struct {
	m      *Model
	starts []int
	// Per text, where its heads' packed keys (transposed, dh×n), packed
	// values (n×dh) and scores (n×n) begin; a head's follow the one before.
	ktOff, vOff, scOff []int
	kt, v, scores      []float32
	// alibi: per head, slope·|δ| for δ = -(nmax-1)..nmax-1.
	alibi []float32
	nmax  int
	jobs  []attentionJob
}

// attentionJob: a text, a head and a block of its rows (queries).
type attentionJob struct{ text, hd, i0, i1 int }

// attentionBlock: the rows of a job, at most.
const attentionBlock = 32

func (m *Model) newAttention(starts []int) *attention {
	dh := m.dim / m.heads
	at := &attention{m: m, starts: starts}
	texts := len(starts) - 1
	kt, v, sc := 0, 0, 0
	for t := 0; t < texts; t++ {
		n := starts[t+1] - starts[t]
		at.ktOff, at.vOff, at.scOff = append(at.ktOff, kt), append(at.vOff, v), append(at.scOff, sc)
		kt += m.heads * packedLen(dh, n)
		v += m.heads * packedLen(n, dh)
		sc += m.heads * n * n
		at.nmax = max(at.nmax, n)
		for hd := 0; hd < m.heads; hd++ {
			for i0 := 0; i0 < n; i0 += attentionBlock {
				at.jobs = append(at.jobs, attentionJob{t, hd, i0, min(n, i0+attentionBlock)})
			}
		}
	}
	at.kt, at.v, at.scores = make([]float32, kt), make([]float32, v), make([]float32, sc)
	span := 2*at.nmax - 1
	at.alibi = make([]float32, m.heads*span)
	for hd := 0; hd < m.heads; hd++ {
		for i := 0; i < span; i++ {
			dist := i - (at.nmax - 1)
			if dist < 0 {
				dist = -dist
			}
			at.alibi[hd*span+i] = m.slopes[hd] * float32(dist)
		}
	}
	return at
}

// keys, values: a text's head's, packed (run fills them).
func (at *attention) keys(t, hd int) packed {
	dh := at.m.dim / at.m.heads
	n := at.starts[t+1] - at.starts[t]
	size := packedLen(dh, n)
	pw, panels := panelsFor(n)
	return packed{k: dh, m: n, pw: pw, panels: panels, data: at.kt[at.ktOff[t]+hd*size : at.ktOff[t]+(hd+1)*size]}
}

func (at *attention) values(t, hd int) packed {
	dh := at.m.dim / at.m.heads
	n := at.starts[t+1] - at.starts[t]
	size := packedLen(n, dh)
	pw, panels := panelsFor(dh)
	return packed{k: n, m: dh, pw: pw, panels: panels, data: at.v[at.vOff[t]+hd*size : at.vOff[t]+(hd+1)*size]}
}

// run: ctx = softmax(q·kᵀ/√dh + alibi)·v, per head, the heads side by side
// in a row; each text on its own.
func (at *attention) run(ctx, q, k, v []float32) {
	m := at.m
	d := m.dim
	dh := d / m.heads
	inv := 1 / float32(math.Sqrt(float64(dh)))
	span := 2*at.nmax - 1
	// The keys (transposed, so that q·kᵀ is a product like the others) and
	// the values, each head's packed for the kernel.
	parallel((len(at.starts)-1)*m.heads, func(th int) {
		t, hd := th/m.heads, th%m.heads
		s0, n := at.starts[t], at.starts[t+1]-at.starts[t]
		packT(at.keys(t, hd).data, k[s0*d+hd*dh:], d, dh, n)
		pack(at.values(t, hd).data, v[s0*d+hd*dh:], d, n, dh)
	})
	parallel(len(at.jobs), func(ji int) {
		jb := at.jobs[ji]
		t, hd, i0, i1 := jb.text, jb.hd, jb.i0, jb.i1
		s0, n := at.starts[t], at.starts[t+1]-at.starts[t]
		off := s0*d + hd*dh
		S := mat{at.scores[at.scOff[t]+hd*n*n : at.scOff[t]+(hd+1)*n*n], n}
		K, V := at.keys(t, hd), at.values(t, hd)
		gemm(S, mat{q[off:], d}, &K, i0, i1, 0, K.panels)
		alibi := at.alibi[hd*span : (hd+1)*span]
		var sums [attentionBlock]float32
		for i := i0; i < i1; i++ {
			s := S.row(i)[:n]
			mx := scaleAddMax(s, alibi[at.nmax-1-i:], inv)
			sums[i-i0] = expShift(s, mx)
		}
		// The weights are not divided by their sum: their product with
		// the values is, a head's dh floats instead of a row's n.
		C := mat{ctx[off:], d}
		gemm(C, S, &V, i0, i1, 0, V.panels)
		for i := i0; i < i1; i++ {
			scale(C.row(i)[:dh], 1/sums[i-i0])
		}
	})
}
