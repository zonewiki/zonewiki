package encoder

import (
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// tinyModel: a model file of the real one's architecture and names, small
// and with random weights: 12 heads of 2 dimensions, a feed-forward of
// 16, two layers, a vocabulary of 9. With the weights, for a reference.
func tinyModel(t *testing.T, drop string) (string, map[string][]float64, map[string][]int) {
	const d, ff, layers, vocab = 24, 16, 2, 9
	r := rand.New(rand.NewSource(17))
	var tensors []stTensor
	weights, shapes := map[string][]float64{}, map[string][]int{}
	add := func(name string, shape ...int) {
		if name == drop {
			return
		}
		n := 1
		for _, s := range shape {
			n *= s
		}
		h := randomHalves(r, n)
		w := make([]float64, n)
		for i, x := range h {
			w[i] = float64(half(x))
		}
		tensors = append(tensors, stTensor{name: name, shape: shape, data: h})
		weights[name], shapes[name] = w, shape
	}
	add("embeddings.word_embeddings.weight", vocab, d)
	add("embeddings.token_type_embeddings.weight", 2, d)
	add("embeddings.LayerNorm.weight", d)
	add("embeddings.LayerNorm.bias", d)
	for l := 0; l < layers; l++ {
		p := fmt.Sprintf("encoder.layer.%d.", l)
		for _, name := range []string{"attention.self.query", "attention.self.key", "attention.self.value", "attention.output.dense"} {
			add(p+name+".weight", d, d)
			add(p+name+".bias", d)
		}
		add(p+"attention.output.LayerNorm.weight", d)
		add(p+"attention.output.LayerNorm.bias", d)
		add(p+"mlp.gated_layers.weight", 2*ff, d)
		add(p+"mlp.wo.weight", d, ff)
		add(p+"mlp.wo.bias", d)
		add(p+"mlp.layernorm.weight", d)
		add(p+"mlp.layernorm.bias", d)
	}
	add("pooler.dense.weight", d, d) // in the real file too, and not the model's
	path := filepath.Join(t.TempDir(), "model.safetensors")
	if err := os.WriteFile(path, stFile(tensors), 0o600); err != nil {
		t.Fatal(err)
	}
	return path, weights, shapes
}

// reference: the model as its description says it, a text at a time, in
// float64 and plain loops: what Hidden must give.
func reference(w map[string][]float64, ids []int) [][]float64 {
	const d, heads, ff, layers = 24, 12, 16, 2
	n := len(ids)
	linear := func(x [][]float64, name string, out int, bias bool) [][]float64 {
		W := w[name+".weight"] // out×in
		in := len(W) / out
		y := make([][]float64, len(x))
		for i := range x {
			y[i] = make([]float64, out)
			for o := 0; o < out; o++ {
				s := 0.0
				for p := 0; p < in; p++ {
					s += x[i][p] * W[o*in+p]
				}
				if bias {
					s += w[name+".bias"][o]
				}
				y[i][o] = s
			}
		}
		return y
	}
	norm := func(x [][]float64, name string) {
		for i := range x {
			mean, variance := 0.0, 0.0
			for _, v := range x[i] {
				mean += v / d
			}
			for _, v := range x[i] {
				variance += (v - mean) * (v - mean) / d
			}
			for j, v := range x[i] {
				x[i][j] = (v-mean)/math.Sqrt(variance+1e-12)*w[name+".weight"][j] + w[name+".bias"][j]
			}
		}
	}
	add := func(x, y [][]float64) {
		for i := range x {
			for j := range x[i] {
				x[i][j] += y[i][j]
			}
		}
	}
	x := make([][]float64, n)
	for i, id := range ids {
		x[i] = make([]float64, d)
		for j := range x[i] {
			x[i][j] = w["embeddings.word_embeddings.weight"][id*d+j] + w["embeddings.token_type_embeddings.weight"][j]
		}
	}
	norm(x, "embeddings.LayerNorm")
	slopes := alibiSlopes(heads) // negative; TestAlibiSlopes holds them
	dh := d / heads
	for l := 0; l < layers; l++ {
		p := fmt.Sprintf("encoder.layer.%d.", l)
		q, k, v := linear(x, p+"attention.self.query", d, true), linear(x, p+"attention.self.key", d, true), linear(x, p+"attention.self.value", d, true)
		ctx := make([][]float64, n)
		for i := range ctx {
			ctx[i] = make([]float64, d)
			for h := 0; h < heads; h++ {
				scores, sum := make([]float64, n), 0.0
				for j := 0; j < n; j++ {
					s := 0.0
					for c := h * dh; c < (h+1)*dh; c++ {
						s += q[i][c] * k[j][c]
					}
					scores[j] = math.Exp(s/math.Sqrt(float64(dh)) + float64(slopes[h])*math.Abs(float64(i-j)))
					sum += scores[j]
				}
				for j := 0; j < n; j++ {
					for c := h * dh; c < (h+1)*dh; c++ {
						ctx[i][c] += scores[j] / sum * v[j][c]
					}
				}
			}
		}
		add(x, linear(ctx, p+"attention.output.dense", d, true))
		norm(x, p+"attention.output.LayerNorm")
		gate := linear(x, p+"mlp.gated_layers", 2*ff, false)
		g := make([][]float64, n)
		for i := range g {
			g[i] = make([]float64, ff)
			for j := range g[i] {
				a := gate[i][j]
				g[i][j] = a * 0.5 * (1 + math.Erf(a/math.Sqrt2)) * gate[i][ff+j]
			}
		}
		add(x, linear(g, p+"mlp.wo", d, true))
		norm(x, p+"mlp.layernorm")
	}
	return x
}

// The model, loaded from a file as the real one's and run, gives what the
// reference gives, for texts of one token, a few, and more than one
// attention job takes; alone and in a batch. This is the test of the
// whole network that needs no model file: the real model's vectors are
// held by TestModelVectorsAreOnnxruntimes, where its file is.
func TestModelIsTheReference(t *testing.T) {
	path, weights, _ := tinyModel(t, "")
	m, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	if m.Dim() != 24 || m.ff != 16 || len(m.layers) != 2 || m.vocab != 9 {
		t.Fatalf("loaded: %d dimensions, a feed-forward of %d, %d layers, %d words", m.Dim(), m.ff, len(m.layers), m.vocab)
	}
	r := rand.New(rand.NewSource(18))
	var texts [][]int
	for _, n := range []int{1, 5, attentionBlock + 5, 2*attentionBlock + 8, 3} {
		ids := make([]int, n)
		for i := range ids {
			ids[i] = r.Intn(9)
		}
		texts = append(texts, ids)
	}
	batch, err := m.HiddenBatch(texts)
	if err != nil {
		t.Fatal(err)
	}
	for ti, ids := range texts {
		want := reference(weights, ids)
		alone, err := m.Hidden(ids)
		if err != nil {
			t.Fatal(err)
		}
		for i := range ids {
			for j := 0; j < 24; j++ {
				got := alone[i*24+j]
				if math.Abs(float64(got)-want[i][j]) > 2e-4 {
					t.Fatalf("text %d, token %d [%d]: %v, the reference %v", ti, i, j, got, want[i][j])
				}
				if batch[ti][i*24+j] != got {
					t.Fatalf("text %d, token %d [%d]: %v in the batch, %v alone", ti, i, j, batch[ti][i*24+j], got)
				}
			}
		}
	}
	if _, err := m.Hidden([]int{0, 9}); err == nil {
		t.Error("a token outside the vocabulary ran")
	}
	if _, err := m.Hidden([]int{-1}); err == nil {
		t.Error("a negative token ran")
	}
	if out, err := m.HiddenBatch([][]int{{}, {1}}); err != nil || len(out[0]) != 0 || len(out[1]) != 24 {
		t.Errorf("an empty text in a batch: %v, %v", out, err)
	}
}

// A file without one of the model's tensors, or with one of another
// shape, is refused when loaded, with the tensor's name, and holds no
// memory after.
func TestLoadRefusesAnotherModel(t *testing.T) {
	for _, name := range []string{
		"embeddings.word_embeddings.weight", "embeddings.token_type_embeddings.weight", "embeddings.LayerNorm.bias",
		"encoder.layer.0.attention.self.query.weight", "encoder.layer.1.attention.self.key.bias",
		"encoder.layer.0.mlp.gated_layers.weight", "encoder.layer.1.mlp.wo.weight", "encoder.layer.1.mlp.layernorm.weight",
	} {
		path, _, _ := tinyModel(t, name)
		m, err := Load(path)
		if err == nil {
			m.Close()
			// Without layer 0's first matrix there are no layers; without
			// layer 1's, the model would have one: but then its other
			// tensors are missing, which the names above are not.
			t.Errorf("without %s: loaded", name)
			continue
		}
		if name != "encoder.layer.0.attention.self.query.weight" && !strings.Contains(err.Error(), strings.TrimSuffix(strings.TrimSuffix(name, ".weight"), ".bias")) &&
			!strings.Contains(err.Error(), "word embeddings") && !strings.Contains(err.Error(), "feed-forward") {
			t.Errorf("without %s: %v", name, err)
		}
	}
	if _, err := Load(filepath.Join(t.TempDir(), "none")); err == nil {
		t.Error("no file: loaded")
	}
}
