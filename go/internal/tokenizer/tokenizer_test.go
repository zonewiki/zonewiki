package tokenizer

import (
	"os"
	"reflect"
	"strings"
	"testing"
)

// The model's tokenizer.json is not in the repo: ZONE_MODEL_DIR names the
// folder it was downloaded to (embed.go). The ids are Hugging Face's.
func loadTok(t *testing.T) *Tokenizer {
	dir := os.Getenv("ZONE_MODEL_DIR")
	if dir == "" {
		t.Skip("ZONE_MODEL_DIR not set")
	}
	tok, err := Load(dir+"/tokenizer.json", 512)
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

func TestEncode(t *testing.T) {
	tok := loadTok(t)
	if id, ok := tok.ID("<pad>"); !ok || id != 1 {
		t.Errorf("<pad> = %d %v", id, ok)
	}
	for text, want := range map[string][]int{
		"Hello world":      {0, 17964, 1568, 2},
		"it's they're":     {0, 272, 507, 713, 1634, 2},
		"  two  spaces":    {0, 225, 1244, 225, 13319, 2},
		"text <mask> more": {0, 5339, 4, 708, 2},
		"Müller Straße":    {0, 49, 44720, 6779, 2},
		"😀 ok":             {0, 32164, 227, 8192, 2},
		"":                 {0, 2},
		"a\tb\nc":          {0, 69, 202, 70, 203, 71, 2},
	} {
		if got := tok.Encode(text); !reflect.DeepEqual(got, want) {
			t.Errorf("%q: got %v, want %v", text, got, want)
		}
	}
	if got := tok.Encode(strings.Repeat("word ", 699) + "word"); len(got) != 512 || got[511] != 2 {
		t.Errorf("truncation: %d ids, last %d", len(got), got[len(got)-1])
	}
}

// GPT-2's split, which go-huggingface's ByteLevel gets wrong (it splits at
// spaces only).
func TestPreTokenize(t *testing.T) {
	var got []string
	for i, s := 0, "it's  two\t\tx 3.14!? "; i < len(s); {
		n := gpt2Piece(s[i:])
		got = append(got, s[i:i+n])
		i += n
	}
	want := []string{"it", "'s", " ", " two", "\t", "\t", "x", " 3", ".", "14", "!?", " "}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
}
