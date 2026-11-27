package main

import (
	"math"
	"os"
	"strings"
	"testing"
)

func TestMeanPooled(t *testing.T) {
	// Two tokens of three floats: their mean (2, 0, 0) and (0, 0, 4) is
	// (1, 0, 2), normalised.
	got := meanPooled([]float32{2, 0, 0, 0, 0, 4}, 2, 3)
	want := []float64{1 / math.Sqrt(5), 0, 2 / math.Sqrt(5)}
	for i := range want {
		if math.Abs(float64(got[i])-want[i]) > 1e-7 {
			t.Fatalf("pooled %v, want %v", got, want)
		}
	}
	// No tokens, or tokens that cancel: zeros, not NaN.
	for _, v := range [][]float32{meanPooled(nil, 0, 3), meanPooled([]float32{1, -1, 0, -1, 1, 0}, 2, 3)} {
		for _, x := range v {
			if x != 0 {
				t.Errorf("pooled %v, want zeros", v)
			}
		}
	}
}

// The model's vectors are those onnxruntime 1.31.0 gave for the same texts
// (some of their components, written down when Zonewiki ran the model on
// it): a short text, a German one, and one cut at 512 tokens; a query is
// embedded like a passage, and a batch like each text alone. Needs the
// model's files (ZONE_MODEL_DIR, as in an image with semantic search, or
// ~/.cache/zonewiki); skipped without, never downloading them.
func TestModelVectorsAreOnnxruntimes(t *testing.T) {
	dir := modelDir(os.Getenv)
	if !modelPresent(dir) {
		t.Skipf("no model in %s", dir)
	}
	texts := []string{
		"How do I reset the router?",
		"Die Heizung zeigt einen Fehler und der Druck ist zu niedrig.",
		strings.Repeat("Notes from the meeting: the bike shop orders spare parts on Mondays. ", 40),
	}
	// Components 0, 1, 2, 383, 766 and 767.
	at := []int{0, 1, 2, 383, 766, 767}
	golden := [][]float32{
		{-0.038642954, 0.043532934, -0.02279709, 0.03560765, -0.04385607, 0.004636185},
		{0.010394662, -0.024974164, 0.057378996, -0.012328721, -0.016501945, 0.008030963},
		{0.02263759, -0.02401325, -0.06701673, -0.012396742, -0.043436375, 0.0011556448},
	}
	emb := newModelEmbedder(dir)
	vecs, err := emb.EmbedDocs(texts)
	if err != nil {
		t.Fatal(err)
	}
	check := func(what string, v, want []float32) {
		t.Helper()
		if len(v) != embedDim {
			t.Fatalf("%s: %d dimensions", what, len(v))
		}
		var sq float64
		for _, x := range v {
			sq += float64(x) * float64(x)
		}
		if math.Abs(sq-1) > 1e-5 {
			t.Errorf("%s: not a unit vector (%v)", what, sq)
		}
		for i, j := range at {
			if math.Abs(float64(v[j]-want[i])) > 5e-5 {
				t.Errorf("%s: component %d is %v, onnxruntime's %v", what, j, v[j], want[i])
			}
		}
	}
	for i := range texts {
		check(texts[i][:20], vecs[i], golden[i])
	}
	q, err := emb.EmbedQuery(texts[0])
	if err != nil {
		t.Fatal(err)
	}
	check("as a query", q, golden[0])
	alone, err := emb.EmbedDocs(texts[1:2])
	if err != nil {
		t.Fatal(err)
	}
	for j := range alone[0] {
		if alone[0][j] != vecs[1][j] {
			t.Fatalf("a text alone differs from itself in a batch at %d: %v, %v", j, alone[0][j], vecs[1][j])
		}
	}
	if none, err := emb.EmbedDocs(nil); err != nil || none != nil {
		t.Errorf("no texts: %v, %v", none, err)
	}
}
