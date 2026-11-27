// The model, run by Zonewiki itself (internal/encoder: the model's network
// in Go, on the weights in its ONNX file), with its tokenizer
// (internal/tokenizer). Nothing else is loaded: no runtime, no library.
// Until 2026-11 onnxruntime ran it; the vectors are the same to seven
// digits (a cosine of 0.9999999 and more between the two), so a cache from
// then stays valid.
package main

import (
	"fmt"
	"math"
	"path/filepath"
	"sync"
	"zonewiki/internal/encoder"
	"zonewiki/internal/tokenizer"
)

// The model's recipe: at most 512 tokens a text, the token vectors
// averaged, then normalised; queries and passages alike, no prefixes.
const (
	maxTokens = 512
	embedDim  = 768
)

type modelEmbedder struct {
	folder string

	mu    sync.Mutex // guards loading
	tok   *tokenizer.Tokenizer
	model *encoder.Model
}

// newModelEmbedder: the model in folder, loaded on first use (under a
// second); downloaded first when its files are missing (a local run; the
// image has them).
func newModelEmbedder(folder string) embedder {
	return &modelEmbedder{folder: folder}
}

// Name is what the cache file is kept under: the name it had when
// fastembed ran the model, whose vectors these are (to seven digits), so a
// cache stays valid.
func (e *modelEmbedder) Name() string      { return "fastembed:" + defaultModel }
func (e *modelEmbedder) ModelName() string { return defaultModel }

func (e *modelEmbedder) load() (*tokenizer.Tokenizer, *encoder.Model, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.model != nil {
		return e.tok, e.model, nil
	}
	if !modelPresent(e.folder) {
		semLog("downloading %s to %s", defaultModel, e.folder)
		if err := downloadModel(e.folder); err != nil {
			return nil, nil, err
		}
	}
	tok, err := tokenizer.Load(filepath.Join(e.folder, "tokenizer.json"), maxTokens)
	if err != nil {
		return nil, nil, err
	}
	model, err := encoder.Load(filepath.Join(e.folder, "onnx", "model.onnx"))
	if err != nil {
		return nil, nil, err
	}
	if model.Dim() != embedDim {
		return nil, nil, fmt.Errorf("%s: vectors of %d dimensions, not %d", e.folder, model.Dim(), embedDim)
	}
	if !encoder.Vectorised {
		semLog("built without GOEXPERIMENT=simd: the model runs without vector arithmetic, many times slower")
	}
	e.tok, e.model = tok, model
	return tok, model, nil
}

func (e *modelEmbedder) EmbedDocs(texts []string) ([][]float32, error) {
	if len(texts) == 0 {
		return nil, nil
	}
	return e.embed(texts)
}

func (e *modelEmbedder) EmbedQuery(text string) ([]float32, error) {
	vecs, err := e.embed([]string{text})
	if err != nil {
		return nil, err
	}
	return vecs[0], nil
}

// embed: one unit vector per text. The texts go through the model
// together (the encoder takes as many at a time as fit its memory), each
// on its own tokens: none is padded to another's length.
func (e *modelEmbedder) embed(texts []string) ([][]float32, error) {
	tok, model, err := e.load()
	if err != nil {
		return nil, err
	}
	ids := make([][]int, len(texts))
	for i, t := range texts {
		ids[i] = tok.Encode(t)
	}
	hidden, err := model.HiddenBatch(ids)
	if err != nil {
		return nil, err
	}
	vecs := make([][]float32, len(texts))
	for i, h := range hidden {
		vecs[i] = meanPooled(h, len(ids[i]), embedDim)
	}
	return vecs, nil
}

// meanPooled: the mean of n token vectors of d floats, normalised (a zero
// vector for no tokens).
func meanPooled(h []float32, n, d int) []float32 {
	v := make([]float32, d)
	if n == 0 {
		return v
	}
	sum := make([]float64, d)
	for i := 0; i < n; i++ {
		for j, x := range h[i*d : (i+1)*d] {
			sum[j] += float64(x)
		}
	}
	var sq float64
	for j := range sum {
		sum[j] /= float64(n)
		sq += sum[j] * sum[j]
	}
	norm := math.Sqrt(sq)
	if norm == 0 {
		norm = 1
	}
	for j := range v {
		v[j] = float32(sum[j] / norm)
	}
	return v
}
