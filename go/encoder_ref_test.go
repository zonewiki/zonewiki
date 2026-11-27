//go:build cgo

package main

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/encoder"
	"zonewiki/internal/tokenizer"
)

// TestGoEncoderMatchesOnnx (spike): the Go encoder's vectors against
// onnxruntime's, for every chunk of the retrieval sample wiki and its
// questions. ZONE_ENCODER_REF=model folder, onnxruntime on LD_LIBRARY_PATH.
func TestGoEncoderMatchesOnnx(t *testing.T) {
	dir := os.Getenv("ZONE_ENCODER_REF")
	if dir == "" {
		t.Skip("ZONE_ENCODER_REF not set")
	}
	var texts []string
	root := "../tests/retrieval/wiki"
	filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(p, ".md") {
			return err
		}
		b, _ := os.ReadFile(p)
		slug := strings.TrimSuffix(strings.TrimPrefix(p, root+"/"), ".md")
		title, body := semanticText(string(b), slug)
		for _, c := range semanticChunks(title, body) {
			texts = append(texts, c[1])
		}
		return nil
	})
	var qs []struct{ Q string }
	b, _ := os.ReadFile("../tests/retrieval/questions.json")
	json.Unmarshal(b, &qs)
	for _, q := range qs {
		texts = append(texts, q.Q)
	}

	ort, _ := newOnnxEmbedder(dir)
	if _, err := ort.EmbedQuery("warm up"); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	var want [][]float32
	for i := 0; i < len(texts); i += 64 {
		v, err := ort.EmbedDocs(texts[i:min(i+64, len(texts))])
		if err != nil {
			t.Fatal(err)
		}
		want = append(want, v...)
	}
	tOrt := time.Since(start)

	start = time.Now()
	m, err := encoder.Load(filepath.Join(dir, "onnx", "model.onnx"))
	if err != nil {
		t.Fatal(err)
	}
	tok, err := tokenizer.Load(filepath.Join(dir, "tokenizer.json"), maxTokens)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("Go encoder loaded in %v", time.Since(start))

	start = time.Now()
	tokens := 0
	got := goEmbed(t, m, tok, texts, &tokens)
	tGo := time.Since(start)

	sims := make([]float64, len(texts))
	maxDiff := 0.0
	for i := range texts {
		var s float64
		for d := range got[i] {
			s += float64(got[i][d]) * float64(want[i][d])
			maxDiff = max(maxDiff, math.Abs(float64(got[i][d]-want[i][d])))
		}
		sims[i] = s
	}
	sort.Float64s(sims)
	t.Logf("%d texts, %d tokens (%.0f a text)", len(texts), tokens, float64(tokens)/float64(len(texts)))
	t.Logf("onnxruntime: %v (%.1f ms a text)  Go: %v (%.1f ms a text)", tOrt, ms(tOrt)/float64(len(texts)), tGo, ms(tGo)/float64(len(texts)))
	t.Logf("cosine to onnxruntime: min %.7f median %.7f; largest difference in a component %.2g", sims[0], sims[len(sims)/2], maxDiff)
	if sims[0] < 0.9999 {
		t.Errorf("a vector differs: cosine %v", sims[0])
	}

	// Longer texts: chunks joined to about 800 characters (a full chunk)
	// and to 3000 (cut at 512 tokens).
	for _, size := range []int{800, 3000} {
		var long []string
		cur := ""
		for _, s := range texts {
			cur += s + "\n\n"
			if len(cur) >= size {
				long = append(long, cur[:size])
				cur = ""
			}
		}
		long = long[:min(len(long), 40)]
		start := time.Now()
		for i := 0; i < len(long); i += 64 {
			if _, err := ort.EmbedDocs(long[i:min(i+64, len(long))]); err != nil {
				t.Fatal(err)
			}
		}
		tOrt := time.Since(start)
		start = time.Now()
		tokens := 0
		goEmbed(t, m, tok, long, &tokens)
		tGo := time.Since(start)
		t.Logf("%d texts of %d chars (%.0f tokens): onnxruntime %.1f ms a text, Go %.1f ms a text",
			len(long), size, float64(tokens)/float64(len(long)), ms(tOrt)/float64(len(long)), ms(tGo)/float64(len(long)))
	}
}

func ms(d time.Duration) float64 { return float64(d) / 1e6 }

// goEmbed: the texts' vectors from the Go encoder, in batches of 64 as the
// index embeds them; the tokens counted into tokens.
func goEmbed(t *testing.T, m *encoder.Model, tok *tokenizer.Tokenizer, texts []string, tokens *int) [][]float32 {
	out := make([][]float32, 0, len(texts))
	for i := 0; i < len(texts); i += 64 {
		var ids [][]int
		for _, text := range texts[i:min(i+64, len(texts))] {
			ids = append(ids, tok.Encode(text))
			*tokens += len(ids[len(ids)-1])
		}
		hs, err := m.HiddenBatch(ids)
		if err != nil {
			t.Fatal(err)
		}
		for j, h := range hs {
			out = append(out, meanPool(h, len(ids[j]), m.Dim()))
		}
	}
	return out
}

// meanPool: the mean of the token vectors, normalised.
func meanPool(h []float32, n, d int) []float32 {
	v := make([]float32, d)
	for i := 0; i < n; i++ {
		for j := range v {
			v[j] += h[i*d+j]
		}
	}
	var sq float64
	for j := range v {
		v[j] /= float32(n)
		sq += float64(v[j]) * float64(v[j])
	}
	nn := float32(math.Sqrt(sq))
	for j := range v {
		v[j] /= nn
	}
	return v
}
