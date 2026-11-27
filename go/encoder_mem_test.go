//go:build cgo

package main

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"testing"
	"zonewiki/internal/encoder"
	"zonewiki/internal/tokenizer"
)

// TestEncoderMemory (spike): what one runtime takes in memory, as the
// index uses it (batches of 64). A process per runtime, since the peak is
// the process's: ZONE_ENCODER_MEM=onnx or go, ZONE_ENCODER_REF=model folder.
func TestEncoderMemory(t *testing.T) {
	which, dir := os.Getenv("ZONE_ENCODER_MEM"), os.Getenv("ZONE_ENCODER_REF")
	if which == "" || dir == "" {
		t.Skip("ZONE_ENCODER_MEM, ZONE_ENCODER_REF not set")
	}
	var texts []string
	root := "../tests/retrieval/wiki"
	filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(p, ".md") {
			return err
		}
		b, _ := os.ReadFile(p)
		title, body := semanticText(string(b), strings.TrimSuffix(strings.TrimPrefix(p, root+"/"), ".md"))
		for _, c := range semanticChunks(title, body) {
			texts = append(texts, c[1])
		}
		return nil
	})
	joined := func(size, n int) []string {
		var out []string
		for i := 0; len(out) < n; i++ {
			cur := ""
			for j := i; len(cur) < size; j++ {
				cur += texts[j%len(texts)] + "\n\n"
			}
			out = append(out, cur[:size])
		}
		return out
	}
	report := func(what string) {
		rss, peak := procMem(t)
		t.Logf("%-34s now %5d MB   peak %5d MB", what, rss, peak)
	}
	report("start")

	var embed func([]string)
	switch which {
	case "onnx":
		ort, _ := newOnnxEmbedder(dir)
		embed = func(batch []string) {
			if _, err := ort.EmbedDocs(batch); err != nil {
				t.Fatal(err)
			}
		}
		embed([]string{"load"})
	case "go":
		m, err := encoder.Load(filepath.Join(dir, "onnx", "model.onnx"))
		if err != nil {
			t.Fatal(err)
		}
		tok, err := tokenizer.Load(filepath.Join(dir, "tokenizer.json"), maxTokens)
		if err != nil {
			t.Fatal(err)
		}
		embed = func(batch []string) {
			var ids [][]int
			for _, s := range batch {
				ids = append(ids, tok.Encode(s))
			}
			if _, err := m.HiddenBatch(ids); err != nil {
				t.Fatal(err)
			}
		}
		embed([]string{"load"})
	default:
		t.Fatal("ZONE_ENCODER_MEM: onnx or go")
	}
	report("loaded, one query")
	runtime.GC()
	debug.FreeOSMemory()
	report("  unused memory given back")
	for i := 0; i < 3; i++ {
		embed(texts[i*64 : (i+1)*64])
	}
	report("3 × 64 sample chunks (31 tokens)")
	for i := 0; i < 2; i++ {
		embed(joined(800, 64))
	}
	report("2 × 64 full chunks (219 tokens)")
	for i := 0; i < 2; i++ {
		embed(joined(3000, 64))
	}
	report("2 × 64 longest texts (512 tokens)")
	embed([]string{"a query at the end"})
	defer embed([]string{"the model is held until here"})
	runtime.GC()
	debug.FreeOSMemory()
	report("idle again, unused memory given back")
}

// procMem: the process's resident memory now and at its peak, in MB.
func procMem(t *testing.T) (rss, peak int) {
	b, err := os.ReadFile("/proc/self/status")
	if err != nil {
		t.Fatal(err)
	}
	get := func(key string) int {
		m := regexp.MustCompile(key + `:\s+(\d+) kB`).FindSubmatch(b)
		if m == nil {
			t.Fatalf("no %s", key)
		}
		n, _ := strconv.Atoi(string(m[1]))
		return n / 1024
	}
	return get("VmRSS"), get("VmHWM")
}
