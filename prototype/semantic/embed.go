package main

import (
	"fmt"
	"math"

	ort "github.com/yalue/onnxruntime_go"
)

// The model's recipe (embed.OnnxEmbedder): at most 512 tokens, a batch
// padded to its longest text with <pad>, the token vectors averaged where
// the attention mask is set, then normalised.
const (
	maxTokens = 512
	dim       = 768
)

type Embedder struct {
	tok     *Tokenizer
	session *ort.DynamicAdvancedSession
	pad     int
}

// NewEmbedder loads the model; lib is onnxruntime's shared library.
func NewEmbedder(modelDir, lib string) (*Embedder, error) {
	ort.SetSharedLibraryPath(lib)
	if err := ort.InitializeEnvironment(); err != nil {
		return nil, err
	}
	tok, err := LoadTokenizer(modelDir+"/tokenizer.json", maxTokens)
	if err != nil {
		return nil, err
	}
	pad, ok := tok.vocab["<pad>"]
	if !ok {
		return nil, fmt.Errorf("no <pad> token")
	}
	s, err := ort.NewDynamicAdvancedSession(modelDir+"/onnx/model.onnx",
		[]string{"input_ids", "attention_mask"}, []string{"last_hidden_state"}, nil)
	if err != nil {
		return nil, err
	}
	return &Embedder{tok: tok, session: s, pad: pad}, nil
}

// Embed: one unit vector per text, the texts run as one batch.
func (e *Embedder) Embed(texts []string) ([][]float32, error) {
	enc := make([][]int, len(texts))
	n := 0
	for i, t := range texts {
		enc[i] = e.tok.Encode(t)
		n = max(n, len(enc[i]))
	}
	b := len(texts)
	ids, mask := make([]int64, b*n), make([]int64, b*n)
	for i, toks := range enc {
		for j := 0; j < n; j++ {
			if j < len(toks) {
				ids[i*n+j], mask[i*n+j] = int64(toks[j]), 1
			} else {
				ids[i*n+j] = int64(e.pad)
			}
		}
	}
	shape := ort.NewShape(int64(b), int64(n))
	idT, err := ort.NewTensor(shape, ids)
	if err != nil {
		return nil, err
	}
	defer idT.Destroy()
	maskT, err := ort.NewTensor(shape, mask)
	if err != nil {
		return nil, err
	}
	defer maskT.Destroy()
	out, err := ort.NewEmptyTensor[float32](ort.NewShape(int64(b), int64(n), dim))
	if err != nil {
		return nil, err
	}
	defer out.Destroy()
	if err := e.session.Run([]ort.Value{idT, maskT}, []ort.Value{out}); err != nil {
		return nil, err
	}
	hidden := out.GetData()
	vecs := make([][]float32, b)
	for i := range enc {
		// numpy, in float32: the masked sum along the sequence (a strided
		// axis: added slice by slice), / the count; then / the norm, a
		// pairwise sum of squares along the contiguous last axis.
		v := make([]float32, dim)
		for j := 0; j < n; j++ {
			w := float32(mask[i*n+j])
			for d := 0; d < dim; d++ {
				v[d] += hidden[(i*n+j)*dim+d] * w
			}
		}
		cnt := float32(len(enc[i]))
		sq := make([]float32, dim)
		for d := range v {
			v[d] /= cnt
			sq[d] = v[d] * v[d]
		}
		nn := float32(math.Sqrt(float64(pairwiseSum(sq))))
		for d := range v {
			v[d] /= nn
		}
		vecs[i] = v
	}
	return vecs, nil
}

// Normalize is embed._normalize, which the index applies to every vector:
// the norm summed in float64, each value divided in float64, kept float32.
func Normalize(v []float32) []float32 {
	var sum float64
	for _, x := range v {
		sum += float64(x) * float64(x)
	}
	norm := math.Sqrt(sum)
	if norm == 0 {
		norm = 1
	}
	out := make([]float32, len(v))
	for i, x := range v {
		out[i] = float32(float64(x) / norm)
	}
	return out
}

// pairwiseSum is numpy's float32 sum along an axis: blocks of 8
// accumulators below 128 elements, halves above.
func pairwiseSum(a []float32) float32 {
	if len(a) < 8 {
		var s float32
		for _, x := range a {
			s += x
		}
		return s
	}
	if len(a) <= 128 {
		var r [8]float32
		copy(r[:], a[:8])
		i := 8
		for ; i+8 <= len(a); i += 8 {
			for k := 0; k < 8; k++ {
				r[k] += a[i+k]
			}
		}
		s := ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
		for ; i < len(a); i++ {
			s += a[i]
		}
		return s
	}
	m := len(a) / 2
	m -= m % 8
	return pairwiseSum(a[:m]) + pairwiseSum(a[m:])
}
