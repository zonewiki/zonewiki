//go:build !cgo

// Built without cgo (no C compiler): no onnxruntime, so semantic search is
// off and search works by keywords.
package main

import "errors"

const maxTokens = 512

func newOnnxEmbedder(folder string) (embedder, error) {
	return nil, errors.New("this build of Zone has no onnxruntime (built without cgo)")
}
