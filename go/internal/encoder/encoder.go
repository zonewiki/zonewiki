// Package encoder runs the embedding model, jina-embeddings-v2-base-de, in
// Go: a BERT encoder with ALiBi attention and a gated GELU feed-forward
// (JinaBERT), its weights read from the model's ONNX file. Nearly all the
// work is matrix multiplication, done with Go's simd package (matmul.go).
package encoder

import (
	"errors"
	"fmt"
	"math"
	"os"
	"regexp"
	"strconv"
)

type norm struct{ w, b []float32 }

// linear: x·w + b, w (in×out, as ONNX's MatMul takes it) packed for the
// kernel, with its bias.
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
	// collector lets a heap grow to twice what is live in it, and 0.64 GB
	// of weights there would let as much garbage pile up beside them.
	mem    arena
	closed bool

	dim, heads, ff int
	vocab          int
	eps            float32
	word           []float32 // vocab×dim
	typ0           []float32 // token type 0's embedding (all tokens are type 0)
	embLN          norm
	layers         []layer
	slopes         []float32 // ALiBi, per head, negative
}

func (m *Model) Dim() int { return m.dim }

var matmulRE = regexp.MustCompile(`^/encoder/layer\.(\d+)/(attention/self/query|attention/self/key|attention/self/value|attention/output/dense|mlp/gated_layers|mlp/wo)/MatMul_output_0$`)

// Load reads the model from its ONNX file. The file is never in memory as
// a whole: its big tensors are read where they are in it, a megabyte at a
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
	g, err := parseModelFile(f, st.Size())
	if err != nil {
		return nil, err
	}
	m := &Model{heads: 12, eps: 1e-12}
	if err := m.load(g, &tensorReader{r: f}); err != nil {
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

// load fills the model from the file's graph and its tensors. The big
// ones (the word embeddings, the layers' matrices) go into m.mem, outside
// the garbage collector's heap.
func (m *Model) load(g *graph, src *tensorReader) error {
	vec := func(name string, n int) ([]float32, error) {
		t := g.weights[name]
		if t == nil || t.dtype != 1 || t.count() != n {
			return nil, fmt.Errorf("encoder: %s: missing or not %d floats", name, n)
		}
		out := make([]float32, n)
		return out, src.read(t, out, 0)
	}
	word := g.weights["embeddings.word_embeddings.weight"]
	if word == nil || len(word.dims) != 2 || word.dtype != 1 {
		return fmt.Errorf("encoder: no word embeddings")
	}
	m.vocab, m.dim = word.dims[0], word.dims[1]
	m.word = m.mem.floats(m.vocab * m.dim)
	if err := src.read(word, m.word, 0); err != nil {
		return err
	}
	typ, err := vec("embeddings.token_type_embeddings.weight", 2*m.dim)
	if err != nil {
		return err
	}
	m.typ0 = typ[:m.dim]
	if m.embLN, err = loadNorm(vec, "embeddings.LayerNorm", m.dim); err != nil {
		return err
	}

	// The MatMul weights have no names of their own (onnx::MatMul_1704):
	// the node that uses one says which it is.
	type key struct {
		layer int
		role  string
	}
	mats := map[key]*tensor{}
	nlayers := 0
	for _, n := range g.nodes {
		if n.op != "MatMul" || len(n.inputs) != 2 || len(n.outputs) != 1 {
			continue
		}
		sm := matmulRE.FindStringSubmatch(n.outputs[0])
		w := g.weights[n.inputs[1]]
		if sm == nil || w == nil {
			continue
		}
		l, _ := strconv.Atoi(sm[1])
		mats[key{l, sm[2]}] = w
		nlayers = max(nlayers, l+1)
	}
	if nlayers == 0 {
		return fmt.Errorf("encoder: no layers")
	}
	lin := func(l int, role, bias string, in, out int) (linear, error) {
		w := mats[key{l, role}]
		if w == nil || w.dtype != 1 || len(w.dims) != 2 || w.dims[0] != in || w.dims[1] != out {
			return linear{}, fmt.Errorf("encoder: layer %d %s: missing or not %d×%d", l, role, in, out)
		}
		// Packed some rows at a time, as they come from the file.
		r := linear{w: newPackedIn(m.mem.floats, in, out), in: in, out: out}
		block := max(1, (1<<18)/out)
		rows := make([]float32, block*out)
		for p := 0; p < in; p += block {
			n := min(block, in-p)
			if err := src.read(w, rows[:n*out], p*out); err != nil {
				return linear{}, err
			}
			r.w.setRows(p, n, rows, out)
		}
		if bias != "" {
			b, err := vec(fmt.Sprintf("encoder.layer.%d.%s.bias", l, bias), out)
			if err != nil {
				return linear{}, err
			}
			r.w = r.w.withBias(b)
		}
		return r, nil
	}
	if gw := mats[key{0, "mlp/gated_layers"}]; gw != nil && len(gw.dims) == 2 {
		m.ff = gw.dims[1] / 2
	}
	d, ff := m.dim, m.ff
	for l := 0; l < nlayers; l++ {
		var L layer
		var err error
		steps := []func() error{
			func() error { L.q, err = lin(l, "attention/self/query", "attention.self.query", d, d); return err },
			func() error { L.k, err = lin(l, "attention/self/key", "attention.self.key", d, d); return err },
			func() error { L.v, err = lin(l, "attention/self/value", "attention.self.value", d, d); return err },
			func() error { L.o, err = lin(l, "attention/output/dense", "attention.output.dense", d, d); return err },
			func() error { L.gate, err = lin(l, "mlp/gated_layers", "", d, 2*ff); return err },
			func() error { L.down, err = lin(l, "mlp/wo", "mlp.wo", ff, d); return err },
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
	if d%m.heads != 0 {
		return fmt.Errorf("encoder: %d dimensions for %d heads", d, m.heads)
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
				row[j] = w[j] + m.typ0[j]
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
