// Package encoder runs the embedding model, jina-embeddings-v2-base-de, in
// Go: a BERT encoder with ALiBi attention and a gated GELU feed-forward
// (JinaBERT), its weights read from the model's safetensors file and kept
// as it has them, in float16. Nearly all the work is matrix
// multiplication, done with Go's simd package (matmul.go).
package encoder

import (
	"errors"
	"fmt"
	"math"
	"os"
)

type norm struct{ w, b []float32 }

// linear: x·w + b, w (in×out) packed for the kernel, with its bias.
type linear struct {
	w       packed
	in, out int
}

type layer struct {
	q, k, v, o linear // attention, o its output
	ln1        norm
	gate       linear // dim → 2·ff, the GELU half first, no bias
	down       linear // ff → dim
	ln2        norm
}

// Model: the weights, read-only once loaded, so Hidden may run on several
// goroutines at once.
type Model struct {
	// mem holds the big weights outside Go's heap (arena.go): the garbage
	// collector lets a heap grow to twice what is live in it, and 0.3 GB
	// of weights there would let as much garbage pile up beside them.
	mem    arena
	closed bool

	dim, heads, ff int
	vocab          int
	eps            float32
	word           []uint16  // vocab×dim, float16
	typ0           []float32 // token type 0's embedding (all tokens are type 0)
	embLN          norm
	layers         []layer
	slopes         []float32 // ALiBi, per head, negative
}

func (m *Model) Dim() int { return m.dim }

// Load reads the model from its safetensors file. The file is never in
// memory as a whole: a tensor is read where it is in it, a megabyte at a
// time, straight into what the model keeps, so loading takes little more
// memory than the loaded model.
func Load(path string) (*Model, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	src, err := openTensors(f, st.Size())
	if err != nil {
		return nil, err
	}
	m := &Model{heads: 12, eps: 1e-12}
	if err := m.load(src); err != nil {
		m.Close()
		return nil, err
	}
	return m, nil
}

// Close gives the model's memory back. The model must not be in use, and
// can't be used after. A server that keeps its model never calls it.
func (m *Model) Close() {
	m.closed = true
	m.word, m.layers = nil, nil
	m.mem.free()
}

// load fills the model from the file's tensors. The big ones (the word
// embeddings, the layers' matrices) stay float16, as the file has them,
// and go into m.mem, outside the garbage collector's heap; the small ones
// (biases, norms) are widened to float32 here.
func (m *Model) load(src *tensorFile) error {
	vec := func(name string, n int) ([]float32, error) {
		h := make([]uint16, n)
		if err := src.read(name, []int{n}, h, 0); err != nil {
			return nil, err
		}
		out := make([]float32, n)
		for i, x := range h {
			out[i] = half(x)
		}
		return out, nil
	}
	const wordName = "embeddings.word_embeddings.weight"
	shape := src.shape(wordName)
	if len(shape) != 2 || shape[0] < 1 || shape[1] < 1 {
		return fmt.Errorf("encoder: no word embeddings")
	}
	m.vocab, m.dim = shape[0], shape[1]
	d := m.dim
	if d%m.heads != 0 {
		return fmt.Errorf("encoder: %d dimensions for %d heads", d, m.heads)
	}
	m.word = m.mem.halves(m.vocab * d)
	if err := src.read(wordName, shape, m.word, 0); err != nil {
		return err
	}
	// Two token types, the first for every token: only its row is kept.
	h := make([]uint16, d)
	if err := src.read("embeddings.token_type_embeddings.weight", []int{2, d}, h, 0); err != nil {
		return err
	}
	m.typ0 = make([]float32, d)
	for i, x := range h {
		m.typ0[i] = half(x)
	}
	var err error
	if m.embLN, err = loadNorm(vec, "embeddings.LayerNorm", d); err != nil {
		return err
	}

	// lin: a layer's matrix, in the file as PyTorch keeps a Linear's, out×in
	// (the transpose of what the product takes), packed some of its rows at
	// a time, as they come from the file.
	lin := func(l int, name string, bias bool, in, out int) (linear, error) {
		name = fmt.Sprintf("encoder.layer.%d.%s", l, name)
		r := linear{w: newPackedHalf(m.mem.halves, in, out), in: in, out: out}
		block := max(1, (1<<18)/in)
		rows := make([]uint16, block*in)
		for j := 0; j < out; j += block {
			n := min(block, out-j)
			if err := src.read(name+".weight", []int{out, in}, rows[:n*in], j*in); err != nil {
				return linear{}, err
			}
			r.w.setColumnHalves(j, n, rows)
		}
		if bias {
			b, err := vec(name+".bias", out)
			if err != nil {
				return linear{}, err
			}
			r.w = r.w.withBias(b)
		}
		return r, nil
	}
	nlayers := 0
	for src.shape(fmt.Sprintf("encoder.layer.%d.attention.self.query.weight", nlayers)) != nil {
		nlayers++
	}
	if nlayers == 0 {
		return fmt.Errorf("encoder: no layers")
	}
	gate := src.shape("encoder.layer.0.mlp.gated_layers.weight")
	if len(gate) != 2 || gate[0] < 2 || gate[0]%2 != 0 {
		return fmt.Errorf("encoder: no feed-forward")
	}
	m.ff = gate[0] / 2
	ff := m.ff
	for l := 0; l < nlayers; l++ {
		var L layer
		var err error
		steps := []func() error{
			func() error { L.q, err = lin(l, "attention.self.query", true, d, d); return err },
			func() error { L.k, err = lin(l, "attention.self.key", true, d, d); return err },
			func() error { L.v, err = lin(l, "attention.self.value", true, d, d); return err },
			func() error { L.o, err = lin(l, "attention.output.dense", true, d, d); return err },
			func() error { L.gate, err = lin(l, "mlp.gated_layers", false, d, 2*ff); return err },
			func() error { L.down, err = lin(l, "mlp.wo", true, ff, d); return err },
			func() error {
				L.ln1, err = loadNorm(vec, fmt.Sprintf("encoder.layer.%d.attention.output.LayerNorm", l), d)
				return err
			},
			func() error {
				L.ln2, err = loadNorm(vec, fmt.Sprintf("encoder.layer.%d.mlp.layernorm", l), d)
				return err
			},
		}
		for _, s := range steps {
			if err := s(); err != nil {
				return err
			}
		}
		m.layers = append(m.layers, L)
	}
	m.slopes = alibiSlopes(m.heads)
	return nil
}

func loadNorm(vec func(string, int) ([]float32, error), prefix string, d int) (norm, error) {
	w, err := vec(prefix+".weight", d)
	if err != nil {
		return norm{}, err
	}
	b, err := vec(prefix+".bias", d)
	return norm{w, b}, err
}

// alibiSlopes: ALiBi's slope per head, negated (a score falls by the slope
// for each token of distance): for a power of two n, 2^(-8i/n); else those
// of the power of two below, then every other one of the power above.
func alibiSlopes(n int) []float32 {
	pow2 := func(n int) []float64 {
		s := make([]float64, n)
		start := math.Pow(2, -8/float64(n))
		for i := range s {
			s[i] = math.Pow(start, float64(i+1))
		}
		return s
	}
	p := 1
	for p*2 <= n {
		p *= 2
	}
	s := pow2(p)
	if p < n {
		extra := pow2(2 * p)
		for i := 0; len(s) < n; i += 2 {
			s = append(s, extra[i])
		}
	}
	out := make([]float32, n)
	for i, x := range s {
		out[i] = -float32(x)
	}
	return out
}

// Hidden: the last hidden state of one text's tokens, len(ids)×Dim(),
// row-major.
func (m *Model) Hidden(ids []int) ([]float32, error) {
	out, err := m.HiddenBatch([][]int{ids})
	if err != nil {
		return nil, err
	}
	return out[0], nil
}

// batchTokens: at most this many tokens go through the model together
// (the feed-forward's buffers are 3·ff floats a token: 36 MB for 2048).
const batchTokens = 2048

// HiddenBatch: Hidden for several texts. Their tokens go through the
// linear layers together, so that the weights are read once for all of
// them (for short texts that, not the arithmetic, is what costs); each
// attends to its own tokens only, so none needs padding or a mask.
func (m *Model) HiddenBatch(texts [][]int) ([][]float32, error) {
	if m.closed {
		return nil, errors.New("encoder: the model is closed")
	}
	out := make([][]float32, len(texts))
	for i := 0; i < len(texts); {
		j, tokens := i, 0
		for j < len(texts) && (j == i || tokens+len(texts[j]) <= batchTokens) {
			tokens += len(texts[j])
			j++
		}
		h, err := m.forward(texts[i:j])
		if err != nil {
			return nil, err
		}
		o := 0
		for t := i; t < j; t++ {
			n := len(texts[t]) * m.dim
			out[t] = h[o : o+n : o+n]
			o += n
		}
		i = j
	}
	return out, nil
}

// forward: the texts' hidden states, one after another. The working
// memory is a workspace's, kept for the next batch.
func (m *Model) forward(texts [][]int) ([]float32, error) {
	ws := workspaces.Get().(*workspace)
	defer workspaces.Put(ws)
	return m.forwardIn(texts, ws)
}

func (m *Model) forwardIn(texts [][]int, ws *workspace) ([]float32, error) {
	d := m.dim
	starts := make([]int, len(texts)+1)
	for i, t := range texts {
		starts[i+1] = starts[i] + len(t)
	}
	n := starts[len(texts)]
	x := make([]float32, n*d)
	r := 0
	for _, ids := range texts {
		for _, id := range ids {
			if id < 0 || id >= m.vocab {
				return nil, fmt.Errorf("encoder: token %d out of range", id)
			}
			row, w := x[r*d:(r+1)*d], m.word[id*d:(id+1)*d]
			for j := range row {
				row[j] = half(w[j]) + m.typ0[j]
			}
			r++
		}
	}
	layerNorm(x, n, d, m.embLN, m.eps)
	q, k, v := ws.floats(wsQ, n*d), ws.floats(wsK, n*d), ws.floats(wsV, n*d)
	ctx, tmp := ws.floats(wsCtx, n*d), ws.floats(wsTmp, n*d)
	h, g := ws.floats(wsH, n*2*m.ff), ws.floats(wsG, n*m.ff)
	at := m.newAttention(starts, ws)
	for li := range m.layers {
		L := &m.layers[li]
		product(q, x, n, &L.q.w)
		product(k, x, n, &L.k.w)
		product(v, x, n, &L.v.w)
		at.run(ctx, q, k, v)
		product(tmp, ctx, n, &L.o.w)
		addNorm(x, tmp, n, d, L.ln1, m.eps)
		product(h, x, n, &L.gate.w)
		geglu(g, h, n, m.ff)
		product(tmp, g, n, &L.down.w)
		addNorm(x, tmp, n, d, L.ln2, m.eps)
	}
	return x, nil
}

// addNorm: x = LayerNorm(y + x), the residual and the norm after it.
func addNorm(x, y []float32, n, d int, nm norm, eps float32) {
	parallelRows(n, func(i int) {
		row, add := x[i*d:(i+1)*d], y[i*d:(i+1)*d]
		for j := range row {
			row[j] = add[j] + row[j]
		}
		normRow(row, nm, eps)
	})
}

func layerNorm(x []float32, n, d int, nm norm, eps float32) {
	parallelRows(n, func(i int) { normRow(x[i*d:(i+1)*d], nm, eps) })
}

func normRow(row []float32, nm norm, eps float32) {
	var mean float32
	for _, v := range row {
		mean += v
	}
	mean /= float32(len(row))
	var variance float32
	for j, v := range row {
		row[j] = v - mean
		variance += row[j] * row[j]
	}
	variance /= float32(len(row))
	sd := float32(math.Sqrt(float64(variance + eps)))
	for j := range row {
		row[j] = row[j]/sd*nm.w[j] + nm.b[j]
	}
}
