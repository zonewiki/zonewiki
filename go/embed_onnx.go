//go:build cgo

// The model on onnxruntime (embed.OnnxEmbedder), through onnxruntime_go,
// which loads libonnxruntime.so with dlopen when semantic search first
// embeds: the binary itself links only libc, and an image without the
// library runs with keyword search only. The vectors are Python's, bit for
// bit (prototype/semantic).
package main

import (
	"fmt"
	"math"
	"path/filepath"
	"sync"

	ort "github.com/yalue/onnxruntime_go"
)

// The model's recipe, as fastembed ran it: at most 512 tokens, a batch
// padded to its longest text with <pad>, the token vectors averaged where
// the attention mask is set, then normalised; queries and passages alike,
// no prefixes.
const (
	maxTokens = 512
	embedDim  = 768
)

// onnxLibrary: onnxruntime's shared library, found by the dynamic loader
// (the image puts it in /usr/local/lib; locally, LD_LIBRARY_PATH).
const onnxLibrary = "libonnxruntime.so"

type onnxEmbedder struct {
	folder string

	mu      sync.Mutex // guards loading
	tok     *Tokenizer
	session *ort.DynamicAdvancedSession
	pad     int
}

// newOnnxEmbedder: the model in folder, loaded on first use (seconds);
// downloaded first when its files are missing (a local run; the image has
// them).
func newOnnxEmbedder(folder string) (embedder, error) {
	return &onnxEmbedder{folder: folder}, nil
}

// Name is what the cache file is kept under: the name it had when
// fastembed ran the model, whose vectors are the same, so a cache stays
// valid.
func (e *onnxEmbedder) Name() string      { return "fastembed:" + defaultModel }
func (e *onnxEmbedder) ModelName() string { return defaultModel }

func (e *onnxEmbedder) load() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.session != nil {
		return nil
	}
	if !modelPresent(e.folder) {
		semLog("downloading %s to %s", defaultModel, e.folder)
		if err := downloadModel(e.folder); err != nil {
			return err
		}
	}
	if !ort.IsInitialized() {
		ort.SetSharedLibraryPath(onnxLibrary)
		if err := ort.InitializeEnvironment(); err != nil {
			return err
		}
	}
	tok, err := LoadTokenizer(filepath.Join(e.folder, "tokenizer.json"), maxTokens)
	if err != nil {
		return err
	}
	pad, ok := tok.vocab["<pad>"]
	if !ok {
		return fmt.Errorf("%s: no <pad> token", e.folder)
	}
	s, err := ort.NewDynamicAdvancedSession(filepath.Join(e.folder, "onnx", "model.onnx"),
		[]string{"input_ids", "attention_mask"}, []string{"last_hidden_state"}, nil)
	if err != nil {
		return err
	}
	e.tok, e.session, e.pad = tok, s, pad
	return nil
}

func (e *onnxEmbedder) EmbedDocs(texts []string) ([][]float32, error) {
	if len(texts) == 0 {
		return nil, nil
	}
	return e.embed(texts)
}

func (e *onnxEmbedder) EmbedQuery(text string) ([]float32, error) {
	vecs, err := e.embed([]string{text})
	if err != nil {
		return nil, err
	}
	return vecs[0], nil
}

// embed: one unit vector per text, the texts run as one batch.
func (e *onnxEmbedder) embed(texts []string) ([][]float32, error) {
	if err := e.load(); err != nil {
		return nil, err
	}
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
	out, err := ort.NewEmptyTensor[float32](ort.NewShape(int64(b), int64(n), embedDim))
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
		v := make([]float32, embedDim)
		for j := 0; j < n; j++ {
			w := float32(mask[i*n+j])
			for d := 0; d < embedDim; d++ {
				v[d] += hidden[(i*n+j)*embedDim+d] * w
			}
		}
		cnt := float32(len(enc[i]))
		sq := make([]float32, embedDim)
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
