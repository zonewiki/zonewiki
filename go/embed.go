// Semantic search: embeddings of page sections, searched by
// meaning. Opt-in via LEAF_SEMANTIC_SEARCH (semanticFromEnv). Each page is
// cut into sections (chunkSections), each section turned into a vector by
// an embedding model (embed_onnx.go), and a query is compared against every
// vector (cosine similarity, brute force). That is plenty fast at wiki
// scale, so there is no vector database: the vectors live in memory and in
// a rebuildable cache file, .leaf/index/embeddings.json in the wiki (kept
// out of git), the same file the Python Leaf kept: its cache stays valid.
//
// Vectors are content-addressed: a section's vector is keyed by a hash of
// its text, so editing one section re-embeds only that section, and a page
// renamed or touched without changes re-embeds nothing. Embedding runs in a
// goroutine (start); requests only poke it and query whatever is ready, so
// a slow model never blocks a page view.
//
// retrieval.go fuses these results with BM25 (Fuse) and shows each page's
// nearest neighbours as Related pages.
package main

import (
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// maxChunkChars: sections longer than this are split at paragraphs. Small
// models only read the first ~128–512 tokens of an input, so a long
// section's tail would be lost.
const maxChunkChars = 800

var (
	// An ATX heading: its #s and its text (manual.go reads the docs' with it too).
	chunkHeadingRE = regexp.MustCompile(`^(#{1,6})` + spaceClass + `+(.*?)` + spaceClass + `*#*` + spaceClass + `*$`)
	chunkLinkRE    = regexp.MustCompile(`(!?\[[^\]]*\])\([^)]*\)`)
	paragraphRE    = regexp.MustCompile(`\n` + spaceClass + `*\n`)
)

// splitLong: text cut at blank lines into pieces of at most maxChars
// characters (a single longer paragraph is cut at whitespace).
func splitLong(text string, maxChars int) []string {
	var pieces []string
	cur := ""
	for _, para := range paragraphRE.Split(text, -1) {
		p := []rune(strings.TrimSpace(para))
		if len(p) == 0 {
			continue
		}
		for len(p) > maxChars {
			cut := -1
			for i := maxChars - 1; i >= 0; i-- {
				if p[i] == ' ' {
					cut = i
					break
				}
			}
			if cut <= maxChars/2 {
				cut = maxChars
			}
			if cur != "" {
				pieces = append(pieces, cur)
				cur = ""
			}
			pieces = append(pieces, strings.TrimSpace(string(p[:cut])))
			p = []rune(strings.TrimSpace(string(p[cut:])))
		}
		para := string(p)
		if cur != "" && runeLen(cur)+2+len(p) > maxChars {
			pieces = append(pieces, cur)
			cur = ""
		}
		if cur != "" {
			cur += "\n\n" + para
		} else {
			cur = para
		}
	}
	if cur != "" {
		pieces = append(pieces, cur)
	}
	return pieces
}

// chunkSection is one of chunkSections' pieces: before the first heading,
// the heading is "" and the level 0. Path is the heading after those it
// sits under ("Problems › Windows"): a section headed "Windows" or "Next
// steps" says little on its own.
type chunkSection struct {
	Heading, Body string
	Level         int
	Path          string
}

type headingLevel struct {
	level int
	text  string
}

// chunkSections: a page body cut into sections at its headings (outside
// code fences), long sections further at paragraphs; link targets dropped,
// their text kept.
func chunkSections(text string, maxChars int) []chunkSection {
	sections := [][]string{{}}
	for _, l := range fencedLines(strings.Split(text, "\n")) {
		if !l.inFence && chunkHeadingRE.MatchString(l.line) {
			sections = append(sections, []string{})
		}
		sections[len(sections)-1] = append(sections[len(sections)-1], l.line)
	}
	var chunks []chunkSection
	var parents []headingLevel // the open headings above
	for _, lines := range sections {
		heading, level := "", 0
		if len(lines) > 0 {
			if m := chunkHeadingRE.FindStringSubmatch(lines[0]); m != nil {
				heading, level = m[2], len(m[1])
				lines = lines[1:]
				var kept []headingLevel
				for _, p := range parents {
					if p.level < level {
						kept = append(kept, p)
					}
				}
				parents = append(kept, headingLevel{level, heading})
			}
		}
		path := ""
		if heading != "" {
			names := make([]string, len(parents))
			for i, p := range parents {
				names[i] = p.text
			}
			path = strings.Join(names, " › ")
		}
		body := strings.TrimSpace(chunkLinkRE.ReplaceAllString(strings.Join(lines, "\n"), "${1}"))
		if body == "" {
			continue
		}
		for _, piece := range splitLong(body, maxChars) {
			chunks = append(chunks, chunkSection{heading, piece, level, path})
		}
	}
	return chunks
}

// chunkText: a section as it is embedded and indexed, its heading path
// then its text.
func chunkText(path, body string) string {
	if path != "" {
		return path + "\n" + body
	}
	return body
}

// chunkKey: a chunk's identity, the hash its vector is stored under.
func chunkKey(title, chunk string) string {
	if chunk != "" {
		return sha1Hex(title + "\n" + chunk)
	}
	return sha1Hex(title)
}

func sha1Hex(s string) string {
	h := sha1.Sum([]byte(s))
	return hex.EncodeToString(h[:])
}

// ── The model and the settings ──────────────────────────────────────────────

// The only model: German and English, also mixed. Its files at a fixed
// revision, each checked against its SHA-256 (the Containerfile downloads
// the same; tests/test_image.py checks they agree).
const (
	defaultModel  = "jinaai/jina-embeddings-v2-base-de"
	modelRevision = "3f9eede875721714945b6a99a3198299243cf2be"
)

var modelFiles = []struct{ name, sha256 string }{
	{"onnx/model.onnx", "51654b7441fbfcfef6598b01cbd1ea925ca0f0cad81202fcd36fee325783f1b0"},
	{"tokenizer.json", "376c53a06d6911569afc19f584002a11dbcd6b871afb6d96b8600eb3c4e7e7a7"}, // gitleaks:allow
}

// renamedSettings: the settings' names until 2026-10; set, they turn
// nothing on, and the log says what to rename them to.
var renamedSettings = [][2]string{
	{"LEAF_EMBEDDINGS", "LEAF_SEMANTIC_SEARCH"}, {"LEAF_EMBED_MIN_SCORE", "LEAF_SEMANTIC_MIN_SCORE"}}

// Similarity floors per model, (search, related, tasks):
//   - search: a query↔section similarity below this is not a hit. Vector
//     search always has a "nearest" page, so without a floor every query,
//     even gibberish, would fill the results. LEAF_SEMANTIC_MIN_SCORE
//     overrides it.
//   - related: pages less similar than this (page↔page, each page the mean
//     of its sections) are not listed as related.
//   - tasks: as search, for deck tasks. Their texts are a few words, and
//     short texts let nonsense score higher, so the floor sits higher.
//
// Measured on a test wiki: the floor sits above what nonsense queries and
// unrelated pages score, below what the right page scores. Another model
// (the tests' concept embedder) gets a guess.
var (
	thresholds        = map[string][3]float64{defaultModel: {0.25, 0.25, 0.3}}
	defaultThresholds = [3]float64{0.4, 0.4, 0.4}
)

// relatedMinChars: a page with less prose than this neither gets a Related
// list nor appears in one; search still finds it. A page that is only a
// title, or a hub that is only a list of links, has a vector that is little
// more than its title or an average of everything it links to.
const relatedMinChars = 100

const (
	cacheVersion = 1
	// How pages are cut into chunks; raise it when chunkSections or
	// semanticText change what a chunk's text is. A cache of another
	// chunking keeps its vectors (keyed by the text) but re-chunks every page.
	chunking = 2
)

// What doesn't count as prose: [[wikilinks]], [labels] (chunks keep a
// link's label, its target is already gone), image alts, and bare URLs.
var linkishRE = regexp.MustCompile(`\[\[[^\]]*\]\]|!?\[[^\]]*\]|https?://` + nonSpaceClass + `+|www\.` + nonSpaceClass + `+`)

// proseChars: letters and digits in text outside links (relatedMinChars).
func proseChars(text string) int {
	n := 0
	for _, r := range linkishRE.ReplaceAllString(text, " ") {
		if pyWord(r) {
			n++
		}
	}
	return n
}

func semLog(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "[semantic] "+format+"\n", args...)
}

// parseSemanticSpec: the model LEAF_SEMANTIC_SEARCH turns on, defaultModel
// for on (1, true, yes), "" for off (0, false, no, empty); anything else is
// an error.
func parseSemanticSpec(value string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "0", "off", "false", "no":
		return "", nil
	case "1", "on", "true", "yes":
		return defaultModel, nil
	}
	return "", fmt.Errorf("LEAF_SEMANTIC_SEARCH: expected on or off, got %s", strconv.Quote(value))
}

// modelDir: where the model's files are, LEAF_MODEL_DIR (the image's
// /opt/leaf-model), else a cache folder in the home directory, per revision.
func modelDir(getenv func(string) string) string {
	if d := getenv("LEAF_MODEL_DIR"); d != "" {
		return d
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".cache", "leaf",
		strings.ReplaceAll(defaultModel, "/", "--")+"@"+modelRevision[:12])
}

func modelPresent(folder string) bool {
	for _, f := range modelFiles {
		if !exists(filepath.Join(folder, f.name)) {
			return false
		}
	}
	return true
}

// modelURL: where a model file is downloaded from (a variable for the tests).
var modelURL = func(name string) string {
	return "https://huggingface.co/" + defaultModel + "/resolve/" + modelRevision + "/" + name
}

// downloadModel fetches the model files the folder doesn't have yet, each
// checked against its hash before it takes its name (a half download or a
// changed file never counts as the model).
func downloadModel(folder string) error {
	client := &http.Client{Transport: &http.Transport{
		Proxy: http.ProxyFromEnvironment, ResponseHeaderTimeout: 60 * time.Second}}
	for _, f := range modelFiles {
		dest := filepath.Join(folder, f.name)
		if exists(dest) {
			continue
		}
		if err := os.MkdirAll(filepath.Dir(dest), 0o777); err != nil {
			return err
		}
		url := modelURL(f.name)
		resp, err := client.Get(url)
		if err != nil {
			return err
		}
		tmp := dest + ".part"
		out, err := os.Create(tmp)
		if err != nil {
			resp.Body.Close()
			return err
		}
		digest := sha256.New()
		if resp.StatusCode != http.StatusOK {
			err = fmt.Errorf("%s: %s", url, resp.Status)
		} else {
			_, err = io.Copy(io.MultiWriter(out, digest), resp.Body)
		}
		resp.Body.Close()
		if cerr := out.Close(); err == nil {
			err = cerr
		}
		if got := hex.EncodeToString(digest.Sum(nil)); err == nil && got != f.sha256 {
			err = fmt.Errorf("%s: SHA-256 %s, expected %s", url, got, f.sha256)
		}
		if err == nil {
			err = os.Rename(tmp, dest)
		}
		if err != nil {
			os.Remove(tmp)
			return err
		}
	}
	return nil
}

// ── Vectors ──────────────────────────────────────────────────────────────────

// normalize is embed._normalize, which the index applies to every vector:
// the norm summed in float64, each value divided in float64, kept float32.
func normalize(v []float32) []float32 {
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

func dot(a, b []float32) float64 {
	var s float64
	for i := range min(len(a), len(b)) {
		s += float64(a[i]) * float64(b[i])
	}
	return s
}

// meanVec: the normalised mean of vecs, nil for none.
func meanVec(vecs [][]float32) []float32 {
	if len(vecs) == 0 {
		return nil
	}
	acc := make([]float64, len(vecs[0]))
	for _, v := range vecs {
		for i := range min(len(acc), len(v)) {
			acc[i] += float64(v[i])
		}
	}
	f := make([]float32, len(acc))
	for i, x := range acc {
		f[i] = float32(x)
	}
	return normalize(f)
}

// encodeVec, decodeVec: a vector as the cache keeps it, base64 of its
// float32s, little-endian.
func encodeVec(v []float32) string {
	b := make([]byte, 4*len(v))
	for i, x := range v {
		binary.LittleEndian.PutUint32(b[4*i:], math.Float32bits(x))
	}
	return base64.StdEncoding.EncodeToString(b)
}

func decodeVec(s string) ([]float32, error) {
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil || len(b)%4 != 0 {
		return nil, errors.New("not a vector")
	}
	v := make([]float32, len(b)/4)
	for i := range v {
		v[i] = math.Float32frombits(binary.LittleEndian.Uint32(b[4*i:]))
	}
	return v, nil
}

// ── Embedders ────────────────────────────────────────────────────────────────

// embedder turns texts into vectors: the model (embed_onnx.go) or the
// tests' concept embedder (embedtest.go).
type embedder interface {
	Name() string      // the cache file is per name
	ModelName() string // the similarity floors are per model
	EmbedDocs(texts []string) ([][]float32, error)
	EmbedQuery(text string) ([]float32, error)
}

// ── The index ────────────────────────────────────────────────────────────────

// semPage is a page in the index: its file's mtime (as Python's st_mtime)
// and its chunks, [hash, text] each.
type semPage struct {
	Mtime  float64     `json:"mtime"`
	Chunks [][2]string `json:"chunks"`
	prose  int         // proseChars of its chunks
}

func newSemPage(mtime float64, chunks [][2]string) *semPage {
	p := &semPage{Mtime: mtime, Chunks: chunks}
	p.countProse()
	return p
}

func (p *semPage) countProse() {
	p.prose = 0
	for _, c := range p.Chunks {
		p.prose += proseChars(c[1])
	}
}

// semCache is the cache file, .leaf/index/embeddings.json.
type semCache struct {
	Version  int                 `json:"version"`
	Chunking *int                `json:"chunking,omitempty"`
	Model    string              `json:"model"`
	Pages    map[string]*semPage `json:"pages"`
	Tasks    map[string]string   `json:"tasks"`
	Vectors  map[string]string   `json:"vectors"`
}

type semTask struct{ id, text string }

type chunkRow struct {
	slug, hash, text string
	vec              []float32
}

type taskRow struct {
	id  string
	vec []float32
}

type queryScores struct {
	gen          int
	chunk, tasks []float64
}

// semHit is a page found by meaning: its best section's score and text.
type semHit struct {
	Slug  string
	Score float64
	Text  string
}

// idScore is a section (by its key), a task (by its id) or a page (by its
// slug) and its similarity.
type idScore struct {
	ID    string
	Score float64
}

// semanticIndex holds the section vectors of every page, kept fresh by a
// background worker. A page's sections are embedded as "title\nsection",
// so each carries its page's topic along; each task is one vector, its
// search text.
type semanticIndex struct {
	emb             embedder
	cachePath       string
	tasksFn         func() []semTask
	minScore        float64
	relatedMinScore float64
	taskMinScore    float64
	relatedMinChars int

	syncMu sync.Mutex // one sync at a time; it alone replaces the state below
	mu     sync.Mutex // guards the state below
	pages  map[string]*semPage
	vecs   map[string][]float32 // chunk hash → unit vector
	// slug → the mean of its chunks
	pageVecs  map[string][]float32
	tasks     map[string]string // task id → hash of its text
	taskOrder []string          // the ids as the deck lists them
	queries   map[string][]float32
	// Scoring: rows per index generation, and the scores of recent
	// queries, so one search scores each vector once.
	gen, rowsGen int
	chunkRows    []chunkRow
	taskRows     []taskRow
	scores       map[string]queryScores

	wake    chan struct{}
	started atomic.Bool
	failed  atomic.Bool
	ready   atomic.Bool
}

// newSemanticIndex: the index for emb, its vectors from the cache file
// when that is of this model. Floors not given (NaN) are the model's.
func newSemanticIndex(emb embedder, cachePath string, tasksFn func() []semTask,
	minScore, relatedMinScore, taskMinScore float64, minChars int) *semanticIndex {
	floors, ok := thresholds[emb.ModelName()]
	if !ok {
		floors = defaultThresholds
	}
	for i, given := range []float64{minScore, relatedMinScore, taskMinScore} {
		if !math.IsNaN(given) {
			floors[i] = given
		}
	}
	x := &semanticIndex{emb: emb, cachePath: cachePath, tasksFn: tasksFn,
		minScore: floors[0], relatedMinScore: floors[1], taskMinScore: floors[2], relatedMinChars: minChars,
		pages: map[string]*semPage{}, vecs: map[string][]float32{}, pageVecs: map[string][]float32{},
		tasks: map[string]string{}, queries: map[string][]float32{}, rowsGen: -1,
		scores: map[string]queryScores{}, wake: make(chan struct{}, 1)}
	x.loadCache()
	return x
}

// failing: whether the last try to embed failed (the model is missing or
// broken).
func (x *semanticIndex) failing() bool { return x.failed.Load() }

func (x *semanticIndex) loadCache() {
	b, err := os.ReadFile(x.cachePath)
	if err != nil {
		return
	}
	var c semCache
	if json.Unmarshal(b, &c) != nil || c.Version != cacheVersion || c.Model != x.emb.Name() || c.Vectors == nil {
		return
	}
	vecs := map[string][]float32{}
	for h, s := range c.Vectors {
		v, err := decodeVec(s)
		if err != nil {
			return
		}
		vecs[h] = v
	}
	pages := map[string]*semPage{}
	if c.Chunking == nil && chunking == 1 || c.Chunking != nil && *c.Chunking == chunking {
		if c.Pages == nil {
			return
		}
		for slug, p := range c.Pages {
			if p == nil {
				return
			}
			p.countProse()
			pages[slug] = p
		}
	}
	x.vecs, x.pages = vecs, pages
	if c.Tasks != nil {
		x.tasks = c.Tasks
	}
	x.taskOrder = sortedKeys(x.tasks)
	for slug, p := range pages {
		if v := x.meanOf(p, vecs); v != nil {
			x.pageVecs[slug] = v
		}
	}
	x.ready.Store(len(pages) > 0)
}

func (x *semanticIndex) saveCache() {
	vectors := make(map[string]string, len(x.vecs))
	for h, v := range x.vecs {
		vectors[h] = encodeVec(v)
	}
	ch := chunking
	b, err := json.Marshal(semCache{Version: cacheVersion, Chunking: &ch, Model: x.emb.Name(),
		Pages: x.pages, Tasks: x.tasks, Vectors: vectors})
	if err == nil {
		err = os.MkdirAll(filepath.Dir(x.cachePath), 0o777)
	}
	if err == nil {
		tmp := strings.TrimSuffix(x.cachePath, filepath.Ext(x.cachePath)) + "." + strconv.Itoa(os.Getpid()) + ".tmp"
		if err = os.WriteFile(tmp, b, 0o666); err == nil {
			err = os.Rename(tmp, x.cachePath)
		}
	}
	if err != nil {
		semLog("could not write %s: %v", x.cachePath, err)
	}
}

func (x *semanticIndex) meanOf(p *semPage, vecs map[string][]float32) []float32 {
	var vs [][]float32
	for _, c := range p.Chunks {
		if v, ok := vecs[c[0]]; ok {
			vs = append(vs, v)
		}
	}
	return meanVec(vs)
}

// pyMtime: a file time as Python's st_mtime (a float of seconds), which
// the cache keeps.
func pyMtime(t time.Time) float64 {
	return float64(t.Unix()) + float64(t.Nanosecond())*1e-9
}

// semanticChunks: a page's (title, body) as the index's chunks, [hash,
// text] each; a page with only a title is one chunk of its title.
func semanticChunks(title, body string) [][2]string {
	var chunks [][2]string
	for _, s := range chunkSections(body, maxChunkChars) {
		c := chunkText(s.Path, s.Body)
		chunks = append(chunks, [2]string{chunkKey(title, c), c})
	}
	if len(chunks) == 0 && title != "" {
		chunks = [][2]string{{chunkKey(title, ""), ""}}
	}
	return chunks
}

// sync brings the vectors in line with the pages on disk: it embeds new and
// changed sections and drops deleted pages. Whether anything changed.
func (x *semanticIndex) sync() bool {
	x.syncMu.Lock()
	defer x.syncMu.Unlock()
	return x.doSync()
}

func (x *semanticIndex) doSync() bool {
	type file struct {
		path  string
		mtime float64
	}
	current := map[string]file{}
	var order []string
	for _, p := range iterPages() {
		st, err := os.Stat(p.Path)
		if err != nil {
			continue
		}
		current[p.Slug] = file{p.Path, pyMtime(st.ModTime())}
		order = append(order, p.Slug)
	}
	pages := map[string]*semPage{}
	for slug, p := range x.pages {
		if _, ok := current[slug]; ok {
			pages[slug] = p
		}
	}
	changed := len(pages) != len(x.pages)
	todo := map[string]string{} // hash → text to embed
	var todoOrder []string
	want := func(h, text string) {
		if _, ok := x.vecs[h]; ok {
			return
		}
		if _, ok := todo[h]; !ok {
			todoOrder = append(todoOrder, h)
		}
		todo[h] = text
	}
	for _, slug := range order {
		f := current[slug]
		if old := pages[slug]; old != nil && old.Mtime == f.mtime {
			continue
		}
		content, err := readText(f.path)
		if err != nil {
			continue
		}
		title, body := semanticText(strings.ToValidUTF8(content, "�"), slug)
		chunks := semanticChunks(title, body)
		for _, c := range chunks {
			want(c[0], strings.TrimSpace(title+"\n"+c[1]))
		}
		pages[slug] = newSemPage(f.mtime, chunks)
		changed = true
	}
	// Tasks are short, so they are simply re-hashed each time: a changed
	// task gets a new hash and only that one is embedded.
	tasks := map[string]string{}
	var taskOrder []string
	if x.tasksFn != nil {
		for _, t := range x.tasksFn() {
			if strings.TrimSpace(t.text) == "" {
				continue
			}
			if _, ok := tasks[t.id]; !ok {
				taskOrder = append(taskOrder, t.id)
			}
			h := sha1Hex(t.text)
			tasks[t.id] = h
			want(h, t.text)
		}
	}
	changed = changed || !maps.Equal(tasks, x.tasks)
	newVecs := map[string][]float32{}
	if len(todoOrder) > 0 {
		if err := x.embedAll(todoOrder, todo, newVecs); err == nil {
			if x.failed.Swap(false) {
				semLog("embedding works again")
			}
		} else {
			if !x.failed.Swap(true) {
				semLog("embedding failed, search falls back to keywords: %v", err)
			}
			// Pages whose sections couldn't be embedded are retried next time.
			for slug, p := range pages {
				for _, c := range p.Chunks {
					_, had := x.vecs[c[0]]
					if _, got := newVecs[c[0]]; !had && !got {
						if old := x.pages[slug]; old != nil {
							pages[slug] = old
						} else {
							delete(pages, slug)
						}
						break
					}
				}
			}
		}
	}
	if !changed && len(newVecs) == 0 {
		return false
	}
	// A task whose embedding failed keeps its hash and is retried next time.
	used := map[string]bool{}
	for _, p := range pages {
		for _, c := range p.Chunks {
			used[c[0]] = true
		}
	}
	for _, h := range tasks {
		used[h] = true
	}
	vecs := map[string][]float32{}
	for _, m := range []map[string][]float32{x.vecs, newVecs} {
		for h, v := range m {
			if used[h] {
				vecs[h] = v
			}
		}
	}
	pageVecs := map[string][]float32{}
	for slug, p := range pages {
		if v, ok := x.pageVecs[slug]; ok && x.pages[slug] == p {
			pageVecs[slug] = v
		} else if v := x.meanOf(p, vecs); v != nil {
			pageVecs[slug] = v
		}
	}
	x.mu.Lock()
	x.pages, x.vecs, x.pageVecs, x.tasks, x.taskOrder = pages, vecs, pageVecs, tasks, taskOrder
	x.gen++
	x.mu.Unlock()
	x.ready.Store(true)
	x.saveCache()
	return true
}

// embedDocs: the texts' unit vectors, one per text.
func (x *semanticIndex) embedDocs(texts []string) ([][]float32, error) {
	vecs, err := x.emb.EmbedDocs(texts)
	if err == nil && len(vecs) != len(texts) {
		err = fmt.Errorf("%d vectors for %d texts", len(vecs), len(texts))
	}
	if err != nil {
		return nil, err
	}
	for i, v := range vecs {
		vecs[i] = normalize(v)
	}
	return vecs, nil
}

// embedAll embeds the texts of hashes in batches of 64 into out; on an
// error, out keeps the batches done.
func (x *semanticIndex) embedAll(hashes []string, texts map[string]string, out map[string][]float32) error {
	for i := 0; i < len(hashes); i += 64 {
		batch := hashes[i:min(i+64, len(hashes))]
		in := make([]string, len(batch))
		for j, h := range batch {
			in[j] = texts[h]
		}
		vecs, err := x.embedDocs(in)
		if err != nil {
			return err
		}
		for j, h := range batch {
			out[h] = vecs[j]
		}
	}
	return nil
}

// start runs sync in a goroutine, now and after every poke.
func (x *semanticIndex) start() {
	if x.started.Swap(true) {
		return
	}
	go func() {
		first := true
		for {
			x.safeSync(first)
			first = false
			<-x.wake
		}
	}()
}

func (x *semanticIndex) safeSync(first bool) {
	defer func() {
		if e := recover(); e != nil {
			semLog("sync failed: %v", e)
		}
	}()
	x.sync()
	if first {
		x.mu.Lock()
		sections := 0
		for _, p := range x.pages {
			sections += len(p.Chunks)
		}
		semLog("%d pages (%d sections) and %d tasks indexed", len(x.pages), sections, len(x.tasks))
		x.mu.Unlock()
	}
}

// poke asks the worker to check for changed pages (it doesn't wait).
func (x *semanticIndex) poke() {
	select {
	case x.wake <- struct{}{}:
	default:
	}
}

// ── Queries ──────────────────────────────────────────────────────────────────

func queryKey(q string) string { return strings.ToLower(strings.TrimSpace(q)) }

func (x *semanticIndex) queryVec(query string) []float32 {
	key := queryKey(query)
	x.mu.Lock()
	v := x.queries[key]
	x.mu.Unlock()
	if v != nil {
		return v
	}
	raw, err := x.emb.EmbedQuery(query)
	if err != nil {
		if !x.failed.Swap(true) {
			semLog("query embedding failed, search falls back to keywords: %v", err)
		}
		return nil
	}
	v = normalize(raw)
	x.mu.Lock()
	if len(x.queries) >= 128 {
		clear(x.queries)
	}
	x.queries[key] = v
	x.mu.Unlock()
	return v
}

// scored: the chunk rows, their scores, the task rows and theirs for the
// query, scoring every vector once per query and index generation (a
// search asks several times: pages, tasks, sections for the links). Rows
// are in the pages' A–Z order, the tasks' in the deck's.
func (x *semanticIndex) scored(query string) ([]chunkRow, []float64, []taskRow, []float64) {
	key := queryKey(query)
	x.mu.Lock()
	gen := x.gen
	if x.rowsGen != gen {
		x.chunkRows, x.taskRows = nil, nil
		for _, slug := range sortedKeys(x.pages) {
			for _, c := range x.pages[slug].Chunks {
				if v, ok := x.vecs[c[0]]; ok {
					x.chunkRows = append(x.chunkRows, chunkRow{slug, c[0], c[1], v})
				}
			}
		}
		for _, id := range x.taskOrder {
			if v, ok := x.vecs[x.tasks[id]]; ok {
				x.taskRows = append(x.taskRows, taskRow{id, v})
			}
		}
		x.rowsGen, x.scores = gen, map[string]queryScores{}
	}
	chunkRows, taskRows := x.chunkRows, x.taskRows
	cached, ok := x.scores[key]
	x.mu.Unlock()
	if ok && cached.gen == gen {
		return chunkRows, cached.chunk, taskRows, cached.tasks
	}
	q := x.queryVec(query)
	if q == nil {
		return nil, nil, nil, nil
	}
	cs, ts := make([]float64, len(chunkRows)), make([]float64, len(taskRows))
	for i, r := range chunkRows {
		cs[i] = dot(q, r.vec)
	}
	for i, r := range taskRows {
		ts[i] = dot(q, r.vec)
	}
	x.mu.Lock()
	if len(x.scores) >= 32 {
		clear(x.scores)
	}
	x.scores[key] = queryScores{gen, cs, ts}
	x.mu.Unlock()
	return chunkRows, cs, taskRows, ts
}

type chunkScore struct {
	row   chunkRow
	score float64
}

// chunkScores: the sections at least minScore similar to the query (of
// the pages in known, unless nil), in row order.
func (x *semanticIndex) chunkScores(query string, known map[string]bool) []chunkScore {
	if !x.ready.Load() || strings.TrimSpace(query) == "" {
		return nil
	}
	rows, scores, _, _ := x.scored(query)
	var out []chunkScore
	for i, r := range rows {
		if scores[i] >= x.minScore && (known == nil || known[r.slug]) {
			out = append(out, chunkScore{r, scores[i]})
		}
	}
	return out
}

// search: the pages whose best section is at least minScore similar to
// the query, best first.
func (x *semanticIndex) search(query string, known map[string]bool, limit int) []semHit {
	best := map[string]*semHit{}
	var order []string
	for _, c := range x.chunkScores(query, known) {
		if b := best[c.row.slug]; b == nil {
			best[c.row.slug] = &semHit{c.row.slug, c.score, c.row.text}
			order = append(order, c.row.slug)
		} else if c.score > b.Score {
			b.Score, b.Text = c.score, c.row.text
		}
	}
	hits := make([]semHit, len(order))
	for i, slug := range order {
		hits[i] = *best[slug]
	}
	sort.SliceStable(hits, func(i, j int) bool { return hits[i].Score > hits[j].Score })
	return hits[:min(limit, len(hits))]
}

// searchTasks: the tasks at least taskMinScore similar to the query (of
// those in known, unless nil), best first, as (id, score).
func (x *semanticIndex) searchTasks(query string, known map[string]bool, limit int) []idScore {
	if !x.ready.Load() || strings.TrimSpace(query) == "" {
		return nil
	}
	_, _, rows, scores := x.scored(query)
	var hits []idScore
	for i, r := range rows {
		if scores[i] >= x.taskMinScore && (known == nil || known[r.id]) {
			hits = append(hits, idScore{r.id, scores[i]})
		}
	}
	sort.SliceStable(hits, func(i, j int) bool { return hits[i].Score > hits[j].Score })
	return hits[:min(limit, len(hits))]
}

// searchChunks: the sections most similar to the query, best first, as
// (key, score); several may come from one page (retrieval for an LLM wants
// passages).
func (x *semanticIndex) searchChunks(query string, known map[string]bool, limit int) []idScore {
	hits := x.chunkScores(query, known)
	sort.SliceStable(hits, func(i, j int) bool { return hits[i].score > hits[j].score })
	out := make([]idScore, min(limit, len(hits)))
	for i := range out {
		out[i] = idScore{hits[i].row.hash, hits[i].score}
	}
	return out
}

func (x *semanticIndex) hasContent(p *semPage) bool {
	return p != nil && p.prose >= x.relatedMinChars
}

// related: the pages most similar to slug (by their mean vectors), best
// first. Pages with little prose (relatedMinChars) take no part, either way.
func (x *semanticIndex) related(slug string, known, exclude map[string]bool, limit int) []idScore {
	x.mu.Lock()
	pages, pageVecs := x.pages, x.pageVecs
	x.mu.Unlock()
	me := pageVecs[slug]
	if me == nil || !x.hasContent(pages[slug]) {
		return nil
	}
	return x.nearest(me, pages, pageVecs, slug, known, exclude, limit)
}

// relatedToText is related for a page's text, which the index may not have
// yet (a page just written; the worker catches up later): its sections are
// embedded now, those the index knows reused. None for little prose, or
// when embedding fails.
func (x *semanticIndex) relatedToText(slug, content string, known, exclude map[string]bool, limit int) []idScore {
	if !x.ready.Load() {
		return nil
	}
	title, body := semanticText(content, slug)
	var chunks []string
	prose := 0
	for _, s := range chunkSections(body, maxChunkChars) {
		c := chunkText(s.Path, s.Body)
		chunks = append(chunks, c)
		prose += proseChars(c)
	}
	if prose < x.relatedMinChars {
		return nil
	}
	x.mu.Lock()
	pages, vecs, pageVecs := x.pages, x.vecs, x.pageVecs
	x.mu.Unlock()
	keys := make([]string, len(chunks))
	var missing, texts []string
	for i, c := range chunks {
		keys[i] = chunkKey(title, c)
		if _, ok := vecs[keys[i]]; !ok {
			missing = append(missing, keys[i])
			texts = append(texts, strings.TrimSpace(title+"\n"+c))
		}
	}
	fresh := map[string][]float32{}
	if len(missing) > 0 {
		got, err := x.embedDocs(texts)
		if err != nil {
			semLog("embedding failed, no related pages for %s: %v", slug, err)
			return nil
		}
		for i, h := range missing {
			fresh[h] = got[i]
		}
	}
	all := make([][]float32, len(keys))
	for i, h := range keys {
		if v, ok := vecs[h]; ok {
			all[i] = v
		} else {
			all[i] = fresh[h]
		}
	}
	me := meanVec(all)
	if me == nil {
		return nil
	}
	return x.nearest(me, pages, pageVecs, slug, known, exclude, limit)
}

func (x *semanticIndex) nearest(me []float32, pages map[string]*semPage, pageVecs map[string][]float32,
	slug string, known, exclude map[string]bool, limit int) []idScore {
	var scored []idScore
	for _, other := range sortedKeys(pageVecs) {
		if other == slug || exclude[other] || known != nil && !known[other] || !x.hasContent(pages[other]) {
			continue
		}
		if s := dot(me, pageVecs[other]); s >= x.relatedMinScore {
			scored = append(scored, idScore{other, s})
		}
	}
	sort.SliceStable(scored, func(i, j int) bool { return scored[i].Score > scored[j].Score })
	return scored[:min(limit, len(scored))]
}

// ── Setting it up ───────────────────────────────────────────────────────────

// semantic is the index LEAF_SEMANTIC_SEARCH turns on, nil when it is off:
// then search is by keywords only and pages have no Related section.
var semantic *semanticIndex

// semanticTasks: the deck's tasks for the index, each embedded as its
// search text (title, notes and #tags).
func semanticTasks() []semTask {
	docs := taskSearchDocs()
	out := make([]semTask, len(docs))
	for i, d := range docs {
		out[i] = semTask{d.TaskID, d.ExcerptText}
	}
	return out
}

// semanticFromEnv: the index LEAF_SEMANTIC_SEARCH asks for, its worker not
// started, or nil (keywords only). A bad setting, one by its old name, or a
// build without the model's runtime is logged, not fatal.
func semanticFromEnv(getenv func(string) string) *semanticIndex {
	for _, r := range renamedSettings {
		if getenv(r[0]) != "" {
			extra := ""
			if r[1] == "LEAF_SEMANTIC_SEARCH" {
				extra = " (on or off)"
			}
			semLog("%s was renamed to %s (ignored): set %s in .env instead%s", r[0], r[1], r[1], extra)
		}
	}
	model, err := parseSemanticSpec(getenv("LEAF_SEMANTIC_SEARCH"))
	if err != nil {
		semLog("%v", err)
		return nil
	}
	if model == "" {
		return nil
	}
	emb, err := newOnnxEmbedder(modelDir(getenv))
	if err != nil {
		semLog("LEAF_SEMANTIC_SEARCH is on but %v; using keyword search only", err)
		return nil
	}
	minScore := math.NaN()
	if s := getenv("LEAF_SEMANTIC_MIN_SCORE"); s != "" {
		if f, err := strconv.ParseFloat(strings.TrimSpace(s), 64); err == nil {
			minScore = f
		} else {
			semLog("LEAF_SEMANTIC_MIN_SCORE is not a number; using the default")
		}
	}
	semLog("semantic search on, model %s", emb.Name())
	return newSemanticIndex(emb, filepath.Join(cfg.Data, ".leaf", "index", "embeddings.json"),
		semanticTasks, minScore, math.NaN(), math.NaN(), relatedMinChars)
}

// semanticStatus: how content search works now, for the search dialog:
// "on" (by keywords and meaning), "indexing" (the wiki isn't embedded
// yet), "failed" (the model fails) or "off" (LEAF_SEMANTIC_SEARCH off).
// The last three search by keywords only.
func semanticStatus() string {
	switch {
	case semantic == nil:
		return "off"
	case semantic.failing():
		return "failed"
	case semantic.ready.Load():
		return "on"
	}
	return "indexing"
}
