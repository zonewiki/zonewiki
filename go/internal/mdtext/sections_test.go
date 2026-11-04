package mdtext

import "testing"

func TestContentHash(t *testing.T) {
	if ContentHash("hello") != ContentHash("hello") || ContentHash("a") == ContentHash("b") || len(ContentHash("some content")) != 16 {
		t.Error("ContentHash: 16 hex characters, the same for the same text")
	}
}
