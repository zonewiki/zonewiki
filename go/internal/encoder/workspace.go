package encoder

import "sync"

// workspace: the working memory of a forward pass (up to about 170 MB for
// 2048 tokens of long texts), kept from one batch to the next: made anew
// each time, it was garbage as fast as the model ran, some hundred MB a
// second. Its buffers are handed out as they were left, not zeroed: every
// one is written in full before it is read (TestAWorkspaceLeftDirty).
type workspace struct {
	bufs [wsCount][]float32
}

// A workspace's buffers.
const (
	wsQ = iota
	wsK
	wsV
	wsCtx
	wsTmp
	wsH
	wsG
	wsKeys
	wsValues
	wsScores
	wsAlibi
	wsCount
)

// floats: buffer i, n floats long, with whatever it held.
func (w *workspace) floats(i, n int) []float32 {
	if cap(w.bufs[i]) < n {
		w.bufs[i] = make([]float32, n)
	}
	return w.bufs[i][:n]
}

// workspaces: one per forward pass running at a time (an index's batch,
// and a search's query beside it). A pool lets go of what isn't used over
// two garbage collections, so an idle server holds none.
var workspaces = sync.Pool{New: func() any { return new(workspace) }}
