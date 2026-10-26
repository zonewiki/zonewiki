package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// src/embed.py's index in Go (embed.go), with tests/test_semantic.py's
// fake embedder: words map to a few "concepts", so texts sharing no words
// can still be similar.

var testConcepts = map[string]int{
	"backup": 0, "remote": 0, "push": 0, "copy": 0, "git": 0,
	"car": 1, "repair": 1, "werkstatt": 1, "mechaniker": 1, "bremsen": 1, "golf": 1,
	"sleep": 2, "bed": 2, "tired": 2, "evening": 2, "routine": 2,
	"money": 3, "budget": 3, "rent": 3, "savings": 3, "insurance": 3,
}

// recorder is the concept embedder, keeping every text it embeds as a
// document.
type recorder struct {
	conceptEmbedder
	calls []string
}

func (r *recorder) EmbedDocs(texts []string) ([][]float32, error) {
	vecs, err := r.conceptEmbedder.EmbedDocs(texts)
	if err == nil {
		r.calls = append(r.calls, texts...)
	}
	return vecs, err
}

func newRecorder() *recorder {
	return &recorder{conceptEmbedder: conceptEmbedder{name: "fake:concepts", model: "fake", concepts: testConcepts, dim: 4}}
}

func cachePath() string { return filepath.Join(cfg.Data, ".leaf", "index", "embeddings.json") }

func testIndex(emb embedder, tasks func() []semTask) *semanticIndex {
	return newSemanticIndex(emb, cachePath(), tasks, 0.5, 0.5, 0.5, 0)
}

var semanticPages = map[string]string{
	"git-remote": "# Git remote\n\nSet LEAF_GIT_REMOTE, then push to copy every commit.\n",
	"golf":       "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n",
	"evening":    "# Evening routine\n\nNo screens late, bed by eleven.\n",
	"money":      "# Budget\n\nRent and savings.\n",
}

func putPage(t *testing.T, slug, text string) string {
	t.Helper()
	p := filepath.Join(cfg.Data, slug+".md")
	if err := os.WriteFile(p, []byte(text), 0o666); err != nil {
		t.Fatal(err)
	}
	// A later mtime than the last write's, however coarse the clock.
	later := time.Now().Add(time.Duration(len(text)) * time.Second)
	_ = os.Chtimes(p, later, later)
	return p
}

func hitSlugs(hits []semHit) []string {
	var out []string
	for _, h := range hits {
		out = append(out, h.Slug)
	}
	return out
}

func ids(hits []idScore) []string {
	var out []string
	for _, h := range hits {
		out = append(out, h.ID)
	}
	return out
}

func TestSemanticSearch(t *testing.T) {
	testWiki(t, semanticPages)
	x := testIndex(newRecorder(), nil)
	if x.ready.Load() || len(x.search("car", nil, 30)) > 0 {
		t.Fatal("ready before the first sync")
	}
	if !x.sync() {
		t.Fatal("first sync changed nothing")
	}
	if got := hitSlugs(x.search("car repair", nil, 30)); !reflect.DeepEqual(got, []string{"golf"}) {
		t.Errorf("car repair: %v", got)
	}
	if h := x.search("car repair", nil, 30); h[0].Text != "Werkstatt\nTermin beim Mechaniker, Bremsen quietschen." {
		t.Errorf("its section: %q", h[0].Text)
	}
	if len(x.search("xqzt blorf", nil, 30)) > 0 {
		t.Error("nonsense found something")
	}
	if len(x.search("car repair", map[string]bool{"money": true}, 30)) > 0 {
		t.Error("known didn't filter")
	}
}

func TestSemanticSyncEmbedsOnlyWhatChanged(t *testing.T) {
	testWiki(t, map[string]string{"long": "# Long\n\n## One\n\nFirst.\n\n## Two\n\nSecond.\n"})
	r := newRecorder()
	x := testIndex(r, nil)
	x.sync()
	r.calls = nil
	if x.sync() || r.calls != nil {
		t.Fatalf("unchanged pages: sync changed something, embedded %q", r.calls)
	}
	putPage(t, "long", "# Long\n\n## One\n\nFirst.\n\n## Two\n\nChanged.\n")
	if !x.sync() || !reflect.DeepEqual(r.calls, []string{"Long\nTwo\nChanged."}) {
		t.Errorf("embedded %q", r.calls)
	}
	os.Remove(filepath.Join(cfg.Data, "long.md"))
	x.sync()
	if _, ok := x.pageVecs["long"]; ok {
		t.Error("a deleted page kept its vector")
	}
}

func TestSemanticCache(t *testing.T) {
	testWiki(t, semanticPages)
	testIndex(newRecorder(), nil).sync()

	r := newRecorder()
	fresh := testIndex(r, nil)
	if !fresh.ready.Load() || hitSlugs(fresh.search("car repair", nil, 30))[0] != "golf" || fresh.sync() || r.calls != nil {
		t.Errorf("the cache wasn't used: ready %v, embedded %q", fresh.ready.Load(), r.calls)
	}

	// As Python reads it: version, chunking, model, pages with mtime and
	// [hash, text] chunks, vectors base64 float32.
	var c map[string]any
	b, _ := os.ReadFile(cachePath())
	if err := json.Unmarshal(b, &c); err != nil {
		t.Fatal(err)
	}
	if c["version"] != 1.0 || c["chunking"] != 2.0 || c["model"] != "fake:concepts" {
		t.Errorf("cache header: %v %v %v", c["version"], c["chunking"], c["model"])
	}
	golf := c["pages"].(map[string]any)["golf"].(map[string]any)
	chunk := golf["chunks"].([]any)[0].([]any)
	if chunk[0] != chunkKey("Golf", "Werkstatt\nTermin beim Mechaniker, Bremsen quietschen.") {
		t.Errorf("golf's chunk: %v", chunk)
	}
	if v, _ := decodeVec(c["vectors"].(map[string]any)[chunk[0].(string)].(string)); !reflect.DeepEqual(v, []float32{0, 1, 0, 0}) {
		t.Errorf("golf's vector: %v", v)
	}

	// Another model's cache is not used.
	other := newRecorder()
	other.name = "fake:other"
	o := testIndex(other, nil)
	if o.ready.Load() {
		t.Error("another model's cache used")
	}
	o.sync()
	if other.calls == nil {
		t.Error("nothing embedded for another model")
	}

	// A corrupt one is ignored and written anew.
	os.WriteFile(cachePath(), []byte("{not json"), 0o666)
	if x := testIndex(newRecorder(), nil); x.ready.Load() {
		t.Error("corrupt cache used")
	} else if x.sync(); !strings.Contains(readFile(t, cachePath()), `"model":"fake:concepts"`) {
		t.Error("cache not rewritten")
	}
}

func readFile(t *testing.T, p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// A cache of an older chunking keeps its vectors but re-chunks every page.
func TestSemanticCacheOfAnOlderChunking(t *testing.T) {
	testWiki(t, semanticPages)
	testIndex(newRecorder(), nil).sync()
	var c map[string]any
	json.Unmarshal([]byte(readFile(t, cachePath())), &c)
	c["chunking"] = chunking - 1
	c["pages"].(map[string]any)["golf"].(map[string]any)["chunks"] = [][]string{{"stale-key", "Werkstatt"}}
	b, _ := json.Marshal(c)
	os.WriteFile(cachePath(), b, 0o666)
	r := newRecorder()
	fresh := testIndex(r, nil)
	if fresh.ready.Load() {
		t.Error("ready with no pages")
	}
	fresh.sync()
	if r.calls != nil {
		t.Errorf("embedded %q: the texts are unchanged", r.calls)
	}
	keys := map[string]bool{}
	for _, d := range chunkIndex().docs {
		keys[d.Key] = true
	}
	hits := fresh.searchChunks("car repair", nil, 30)
	if len(hits) == 0 || !keys[hits[0].ID] {
		t.Errorf("hits %v", hits)
	}
}

func TestSemanticEmbeddingFailure(t *testing.T) {
	testWiki(t, semanticPages)
	r := newRecorder()
	x := testIndex(r, nil)
	x.sync()
	r.fail = true
	putPage(t, "garage", "# Garage\n\nMechaniker und Bremsen.\n")
	x.sync()
	if _, ok := x.pageVecs["golf"]; !ok || x.pages["garage"] != nil || !x.failing() {
		t.Error("a failure lost old vectors or kept the new page")
	}
	if len(x.search("repair the car", nil, 30)) > 0 {
		t.Error("a query that can't be embedded found something")
	}
	r.fail = false
	putPage(t, "garage", "# Garage\n\nMechaniker und Bremsen!\n")
	x.sync()
	if got := hitSlugs(x.search("car repair", nil, 30)); !reflect.DeepEqual(got, []string{"garage", "golf"}) || x.failing() {
		t.Errorf("retried: %v, failing %v", got, x.failing())
	}
}

func TestSemanticRelated(t *testing.T) {
	testWiki(t, semanticPages)
	r := newRecorder()
	x := testIndex(r, nil)
	putPage(t, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
	x.sync()
	if got := ids(x.related("golf", nil, nil, 5)); !reflect.DeepEqual(got, []string{"brakes"}) {
		t.Errorf("related: %v", got)
	}
	if len(x.related("golf", nil, map[string]bool{"brakes": true}, 5)) > 0 {
		t.Error("exclude ignored")
	}

	os.Remove(filepath.Join(cfg.Data, "brakes.md"))
	x.sync()
	text := "# Brakes\n\nBremsen beim Mechaniker.\n"
	if got := ids(x.relatedToText("brakes", text, nil, nil, 5)); !reflect.DeepEqual(got, []string{"golf"}) {
		t.Errorf("relatedToText: %v", got)
	}
	before := len(r.calls)
	x.relatedToText("brakes", text, nil, nil, 5)
	if len(r.calls) != before+1 {
		t.Errorf("embedded %q: its section, not the whole wiki", r.calls[before:])
	}
	if got := ids(x.relatedToText("x", "# Sleep\n\nTired in bed.\n", nil, nil, 5)); !reflect.DeepEqual(got, []string{"evening"}) {
		t.Errorf("sleep: %v", got)
	}
	if len(x.relatedToText("x", "# Notes\n\nNothing in particular.\n", nil, nil, 5)) > 0 {
		t.Error("nothing similar, yet related")
	}
	r.fail = true
	if len(x.relatedToText("brakes", text, nil, nil, 5)) > 0 {
		t.Error("related despite failing")
	}
}

// Thin pages and link hubs neither get a Related list nor appear in one.
func TestSemanticRelatedNeedsProse(t *testing.T) {
	long := strings.Repeat("Werkstatt beim Mechaniker, Bremsen quietschen, Termin am Dienstag. ", 3)
	var hub []string
	for i := range 10 {
		hub = append(hub, "- [[Werkstatt Mechaniker "+string(rune('0'+i))+"]]\n- [Bremsen](golf-x.md)")
	}
	testWiki(t, map[string]string{
		"golf": "# Golf\n\n" + long + "\n", "brakes": "# Brakes\n\n" + long + "\n",
		"garage": "# Garage\n\nMechaniker.\n",
		"index":  "# Home\n\n" + strings.Join(hub, "\n") + "\n- https://example.com/werkstatt/mechaniker\n",
	})
	x := newSemanticIndex(newRecorder(), cachePath(), nil, 0.5, 0.5, 0.5, relatedMinChars)
	x.sync()
	if got := ids(x.related("golf", nil, nil, 5)); !reflect.DeepEqual(got, []string{"brakes"}) {
		t.Errorf("golf: %v", got)
	}
	if len(x.related("garage", nil, nil, 5)) > 0 || len(x.related("index", nil, nil, 5)) > 0 {
		t.Error("a thin page or hub got a list")
	}
	if got := hitSlugs(x.search("car repair", nil, 30)); !reflect.DeepEqual(got, []string{"brakes", "garage", "golf", "index"}) {
		t.Errorf("search: %v", got)
	}
}

func TestProseChars(t *testing.T) {
	for text, want := range map[string]int{
		"- [[People and Teams]]\n- [Projects]\n- ![img]\n- https://x.org/a": 0,
		"See [[State]] for the plan, ok?":                                   len("Seefortheplanok"),
		"Werkstatt_2026":                                                    len("Werkstatt2026"),
	} {
		if got := proseChars(text); got != want {
			t.Errorf("%q: %d, want %d", text, got, want)
		}
	}
}

func TestSemanticTasks(t *testing.T) {
	testWiki(t, map[string]string{"index": "# Home\n"})
	tasks := []semTask{{"t1", "Book the Werkstatt"}, {"t2", "Buy milk"}, {"t3", "Evening routine: bed early"}}
	r := newRecorder()
	x := testIndex(r, func() []semTask { return tasks })
	x.sync()
	if got := ids(x.searchTasks("car repair", nil, 30)); !reflect.DeepEqual(got, []string{"t1"}) {
		t.Errorf("car repair: %v", got)
	}
	if len(x.searchTasks("xqzt", nil, 30)) > 0 || len(x.searchTasks("car repair", map[string]bool{"t2": true}, 30)) > 0 {
		t.Error("nonsense or a filtered task found")
	}
	r.calls = nil
	if x.sync() || r.calls != nil {
		t.Error("unchanged tasks cost something")
	}
	tasks = []semTask{{"t1", "Book the Werkstatt"}, {"t2", "Buy oat milk"}}
	if !x.sync() || !reflect.DeepEqual(r.calls, []string{"Buy oat milk"}) || len(x.tasks) != 2 {
		t.Errorf("embedded %q, tasks %v", r.calls, x.tasks)
	}
	r2 := newRecorder()
	fresh := testIndex(r2, func() []semTask { return tasks })
	if fresh.sync() || r2.calls != nil || ids(fresh.searchTasks("car repair", nil, 30))[0] != "t1" {
		t.Error("not cached across restarts")
	}

	// A task under its own floor; one that failed is retried.
	floor := newSemanticIndex(newRecorder(), filepath.Join(t.TempDir(), "c.json"),
		func() []semTask { return []semTask{{"t1", "Werkstatt and milk and bed"}} }, 0.5, 0.5, 0.9, 0)
	floor.sync()
	if len(floor.searchTasks("car repair", nil, 30)) > 0 {
		t.Error("found under the task floor")
	}
	failing := newRecorder()
	failing.fail = true
	f := newSemanticIndex(failing, filepath.Join(t.TempDir(), "c.json"), func() []semTask { return tasks }, 0.5, 0.5, 0.5, 0)
	f.sync()
	if len(f.searchTasks("car repair", nil, 30)) > 0 {
		t.Error("found while failing")
	}
	failing.fail = false
	f.queries = map[string][]float32{}
	f.sync()
	if got := ids(f.searchTasks("car repair", nil, 30)); !reflect.DeepEqual(got, []string{"t1"}) {
		t.Errorf("retried: %v", got)
	}
}

func TestSemanticWorker(t *testing.T) {
	testWiki(t, semanticPages)
	x := testIndex(newRecorder(), nil)
	x.start()
	waitFor(t, func() bool { return x.ready.Load() })
	putPage(t, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
	x.poke()
	waitFor(t, func() bool {
		x.mu.Lock()
		defer x.mu.Unlock()
		return x.pages["brakes"] != nil
	})
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end); time.Sleep(10 * time.Millisecond) {
		if cond() {
			return
		}
	}
	t.Fatal("timed out")
}

func TestSemanticSettings(t *testing.T) {
	testWiki(t, nil)
	for _, v := range []string{"", "off", "0", "no", "false"} {
		if m, err := parseSemanticSpec(v); m != "" || err != nil {
			t.Errorf("%q: %q %v", v, m, err)
		}
	}
	for _, v := range []string{"on", " ON ", "1", "true", "yes"} {
		if m, err := parseSemanticSpec(v); m != defaultModel || err != nil {
			t.Errorf("%q: %q %v", v, m, err)
		}
	}
	if _, err := parseSemanticSpec("bogus:x"); err == nil || err.Error() != "LEAF_SEMANTIC_SEARCH: expected on or off, got 'bogus:x'" {
		t.Errorf("bogus: %v", err)
	}
	env := func(m map[string]string) func(string) string { return func(k string) string { return m[k] } }
	if semanticFromEnv(env(nil)) != nil || semanticFromEnv(env(map[string]string{"LEAF_SEMANTIC_SEARCH": "bogus"})) != nil {
		t.Error("on without being asked")
	}
	// The old names turn nothing on.
	if semanticFromEnv(env(map[string]string{"LEAF_EMBEDDINGS": "fastembed"})) != nil {
		t.Error("an old name turned it on")
	}
	if modelDir(env(map[string]string{"LEAF_MODEL_DIR": "/opt/leaf-model"})) != "/opt/leaf-model" {
		t.Error("LEAF_MODEL_DIR ignored")
	}
	if !strings.HasSuffix(modelDir(env(nil)), "/.cache/leaf/jinaai--jina-embeddings-v2-base-de@3f9eede87572") {
		t.Errorf("model dir %s", modelDir(env(nil)))
	}
	x := semanticFromEnv(env(map[string]string{"LEAF_SEMANTIC_SEARCH": "on", "LEAF_SEMANTIC_MIN_SCORE": "0.42"}))
	if newOnnxEmbedderWorks() {
		if x == nil || x.minScore != 0.42 || x.relatedMinScore != 0.25 || x.taskMinScore != 0.3 {
			t.Errorf("index %+v", x)
		}
	} else if x != nil {
		t.Error("on without onnxruntime")
	}
	if newSemanticIndex(newRecorder(), cachePath(), nil, math.NaN(), math.NaN(), math.NaN(), 0).minScore != 0.4 {
		t.Error("another model's floors aren't the default's")
	}
}

func newOnnxEmbedderWorks() bool {
	_, err := newOnnxEmbedder("")
	return err == nil
}

// The model's files are checked against their hashes, and those there are
// not fetched again.
func TestDownloadModel(t *testing.T) {
	files := map[string]string{"onnx/model.onnx": "weights", "tokenizer.json": "vocab"}
	var fetched []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		name := strings.TrimPrefix(r.URL.Path, "/")
		fetched = append(fetched, name)
		w.Write([]byte(files[name]))
	}))
	defer srv.Close()
	oldURL, oldFiles := modelURL, modelFiles
	t.Cleanup(func() { modelURL, modelFiles = oldURL, oldFiles })
	modelURL = func(name string) string { return srv.URL + "/" + name }
	sum := func(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }
	modelFiles = []struct{ name, sha256 string }{
		{"onnx/model.onnx", sum("weights")}, {"tokenizer.json", sum("vocab")}}
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "tokenizer.json"), []byte("vocab"), 0o666)
	if err := downloadModel(dir); err != nil || !reflect.DeepEqual(fetched, []string{"onnx/model.onnx"}) {
		t.Fatalf("fetched %v: %v", fetched, err)
	}
	if readFile(t, filepath.Join(dir, "onnx", "model.onnx")) != "weights" || !modelPresent(dir) {
		t.Error("not downloaded")
	}

	files["onnx/model.onnx"] = "tampered"
	dir = t.TempDir()
	if err := downloadModel(dir); err == nil || !strings.Contains(err.Error(), "SHA-256") {
		t.Errorf("a changed file: %v", err)
	}
	if exists(filepath.Join(dir, "onnx", "model.onnx")) || exists(filepath.Join(dir, "onnx", "model.onnx.part")) {
		t.Error("a changed file kept")
	}
}

func TestVectorCodec(t *testing.T) {
	v := []float32{0.5, -1.25, 3e-8, 0}
	// As Python's array("f", v).tobytes(), base64.
	if s := encodeVec(v); s != "AAAAPwAAoL9Z2QAzAAAAAA==" {
		t.Errorf("encoded %s", s)
	}
	back, err := decodeVec(encodeVec(v))
	if err != nil || !reflect.DeepEqual(back, v) {
		t.Errorf("round trip: %v %v", back, err)
	}
	if got := normalize([]float32{3, 4}); !reflect.DeepEqual(got, []float32{0.6, 0.8}) {
		t.Errorf("normalize: %v", got)
	}
	if got := normalize([]float32{0, 0}); !reflect.DeepEqual(got, []float32{0, 0}) {
		t.Errorf("normalize zero: %v", got)
	}
}
