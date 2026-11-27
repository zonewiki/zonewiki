package encoder

import (
	"math"
	"math/rand"
	"os"
	"runtime"
	"testing"
)

// The arena's float16s are zeroed, can be written, and are gone with free.
func TestArena(t *testing.T) {
	var a arena
	if a.halves(0) != nil {
		t.Error("no float16s: not nil")
	}
	x, y := a.halves(1000), a.halves(300_000)
	for _, s := range [][]uint16{x, y} {
		for i, v := range s {
			if v != 0 {
				t.Fatalf("[%d] = %v, not zeroed", i, v)
			}
			s[i] = uint16(i)
		}
	}
	if x[999] != 999 || y[299_999] != 299_999%65536 {
		t.Error("a write is lost")
	}
	if runtime.GOOS == "linux" || runtime.GOOS == "darwin" {
		if got := a.held(); got != 2*(1000+300_000) {
			t.Errorf("holds %d bytes", got)
		}
	}
	a.free()
	if a.held() != 0 {
		t.Error("holds memory after free")
	}
	a.free() // twice does nothing
}

func testModel(t testing.TB) *Model {
	path := os.Getenv("ZONE_ENCODER_MODEL")
	if path == "" {
		t.Skip("ZONE_ENCODER_MODEL not set")
	}
	m, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(m.Close)
	return m
}

func randomTexts(m *Model, seed int64, lengths ...int) [][]int {
	r := rand.New(rand.NewSource(seed))
	texts := make([][]int, len(lengths))
	for i, n := range lengths {
		texts[i] = make([]int, n)
		for j := range texts[i] {
			texts[i][j] = r.Intn(m.vocab)
		}
	}
	return texts
}

// A forward pass gives the same, bit for bit, in a workspace another batch
// left full of anything (here NaN, which would show wherever a buffer were
// read before it is written) as in a new one.
func TestAWorkspaceLeftDirty(t *testing.T) {
	m := testModel(t)
	texts := randomTexts(m, 11, 5, 70, 33, 1, 200)
	want, err := m.forwardIn(texts, new(workspace))
	if err != nil {
		t.Fatal(err)
	}
	dirty := new(workspace)
	nan := float32(math.NaN())
	for i := range dirty.bufs {
		// Longer than this batch needs, and shorter, by turns.
		buf := dirty.floats(i, []int{4_000_000, 1000}[i%2])
		for j := range buf {
			buf[j] = nan
		}
	}
	for round := 0; round < 2; round++ { // and once more in what the first pass left
		got, err := m.forwardIn(texts, dirty)
		if err != nil {
			t.Fatal(err)
		}
		for i := range want {
			if got[i] != want[i] {
				t.Fatalf("round %d: [%d] = %v, in a new workspace %v", round, i, got[i], want[i])
			}
		}
	}
}

// The model's weights are outside Go's heap, where the system maps memory
// for Zonewiki: loaded, the heap holds little, and so the garbage
// collector lets little garbage pile up. And running batches doesn't grow
// the heap by the batches' working memory each time: it is reused.
func TestTheModelIsNotInTheHeap(t *testing.T) {
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("the weights are in the heap on " + runtime.GOOS)
	}
	heap := func() uint64 {
		runtime.GC()
		var ms runtime.MemStats
		runtime.ReadMemStats(&ms)
		return ms.HeapAlloc
	}
	before := heap()
	m := testModel(t)
	if got := m.mem.held(); got < 250<<20 {
		t.Errorf("the arena holds %d MB, the weights are more", got>>20)
	}
	if grown := int64(heap()) - int64(before); grown > 20<<20 {
		t.Errorf("loading the model grew the heap by %d MB", grown>>20)
	}
	// Ten batches of 64 texts of 200 tokens: about 1.2 GB of working
	// memory in all, were it made anew each time.
	texts := randomTexts(m, 12, make([]int, 64)...)
	for i := range texts {
		texts[i] = randomTexts(m, int64(100+i), 200)[0]
	}
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)
	start := ms.TotalAlloc
	for i := 0; i < 10; i++ {
		if _, err := m.HiddenBatch(texts); err != nil {
			t.Fatal(err)
		}
	}
	runtime.ReadMemStats(&ms)
	// What a batch must allocate is its result (64·200·768 floats: 39 MB);
	// the rest is bookkeeping.
	if perBatch := (ms.TotalAlloc - start) / 10 >> 20; perBatch > 60 {
		t.Errorf("a batch allocates %d MB: its working memory isn't reused", perBatch)
	}
}

func TestAClosedModelRefuses(t *testing.T) {
	m := testModel(t)
	m.Close()
	if _, err := m.Hidden([]int{1, 2, 3}); err == nil {
		t.Error("a closed model ran")
	}
	if m.mem.held() != 0 {
		t.Error("a closed model holds memory")
	}
}
