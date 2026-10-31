// The tests' embedder (-semantic-test): tests/semantic.py's fake, which
// maps words to a few "concepts", so texts sharing no words can still be
// similar, deterministically and without a model. tests/goserver.py hands
// the server its embedder, floors and state as this flag.
package main

import (
	"encoding/json"
	"errors"
)

type conceptEmbedder struct {
	name, model string
	concepts    map[string]int
	dim         int
	fail        bool
}

func (e *conceptEmbedder) Name() string      { return e.name }
func (e *conceptEmbedder) ModelName() string { return e.model }

func (e *conceptEmbedder) vec(text string) []float32 {
	v := make([]float32, e.dim)
	for _, w := range tokenize(text) {
		if i, ok := e.concepts[w]; ok {
			v[i]++
		}
	}
	return v
}

func (e *conceptEmbedder) EmbedDocs(texts []string) ([][]float32, error) {
	if e.fail {
		return nil, errors.New("model unavailable")
	}
	out := make([][]float32, len(texts))
	for i, t := range texts {
		out[i] = e.vec(t)
	}
	return out, nil
}

func (e *conceptEmbedder) EmbedQuery(text string) ([]float32, error) {
	if e.fail {
		return nil, errors.New("model unavailable")
	}
	return e.vec(text), nil
}

// semanticTestSpec is -semantic-test's JSON: the embedder, the index's
// floors, and whether the test synced it (then it syncs once before the
// server listens; there is no worker).
type semanticTestSpec struct {
	Name            string         `json:"name"`
	ModelName       string         `json:"model_name"`
	Concepts        map[string]int `json:"concepts"`
	Dim             int            `json:"dim"`
	Fail            bool           `json:"fail"`
	MinScore        float64        `json:"min_score"`
	RelatedMinScore float64        `json:"related_min_score"`
	TaskMinScore    float64        `json:"task_min_score"`
	RelatedMinChars int            `json:"related_min_chars"`
	Synced          bool           `json:"synced"`
}

func semanticForTest(spec string) (*semanticIndex, error) {
	var s semanticTestSpec
	if err := json.Unmarshal([]byte(spec), &s); err != nil {
		return nil, err
	}
	emb := &conceptEmbedder{s.Name, s.ModelName, s.Concepts, s.Dim, s.Fail}
	x := newSemanticIndex(emb, statePath("index", "embeddings.json"), semanticTasks,
		s.MinScore, s.RelatedMinScore, s.TaskMinScore, s.RelatedMinChars)
	if s.Synced {
		x.sync()
	}
	return x, nil
}
