package encoder

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// stTensor: a tensor for a safetensors file made by the tests.
type stTensor struct {
	name  string
	dtype string
	shape []int
	data  []uint16
}

// stFile: a safetensors file of the tensors, as the format has it.
func stFile(tensors []stTensor) []byte {
	header := map[string]any{"__metadata__": map[string]string{"format": "pt"}}
	var data []byte
	for _, x := range tensors {
		begin := len(data)
		for _, h := range x.data {
			data = binary.LittleEndian.AppendUint16(data, h)
		}
		dtype := x.dtype
		if dtype == "" {
			dtype = "F16"
		}
		header[x.name] = map[string]any{"dtype": dtype, "shape": x.shape, "data_offsets": []int{begin, len(data)}}
	}
	h, _ := json.Marshal(header)
	for len(h)%8 != 0 {
		h = append(h, ' ') // the format pads its header with spaces
	}
	out := binary.LittleEndian.AppendUint64(nil, uint64(len(h)))
	return append(append(out, h...), data...)
}

func openBytes(b []byte) (*tensorFile, error) { return openTensors(bytes.NewReader(b), int64(len(b))) }

func TestSafetensors(t *testing.T) {
	big := make([]uint16, 700_000) // more than the reader's buffer takes at once
	for i := range big {
		big[i] = uint16(i * 7)
	}
	file := stFile([]stTensor{
		{name: "small", shape: []int{2, 3}, data: []uint16{1, 2, 3, 4, 5, 6}},
		{name: "big", shape: []int{700, 1000}, data: big},
		{name: "ints", dtype: "I64", shape: []int{1}, data: []uint16{1, 0, 0, 0}},
		{name: "empty", shape: []int{0, 4}},
	})
	f, err := openBytes(file)
	if err != nil {
		t.Fatal(err)
	}
	if s := f.shape("big"); len(s) != 2 || s[0] != 700 || s[1] != 1000 {
		t.Errorf("shape %v", s)
	}
	if f.shape("none") != nil || f.shape("ints") != nil {
		t.Error("a shape for a tensor that isn't there, or isn't float16")
	}
	got := make([]uint16, len(big))
	if err := f.read("big", []int{700, 1000}, got, 0); err != nil {
		t.Fatal(err)
	}
	for i := range big {
		if got[i] != big[i] {
			t.Fatalf("big[%d] = %d, want %d", i, got[i], big[i])
		}
	}
	// A part, from the middle to the very end; nothing, at the end.
	part := make([]uint16, 3)
	if err := f.read("small", []int{2, 3}, part, 3); err != nil || part[0] != 4 || part[2] != 6 {
		t.Errorf("small from 3: %v, %v", part, err)
	}
	if err := f.read("small", []int{2, 3}, nil, 6); err != nil {
		t.Errorf("nothing at the end: %v", err)
	}
	for what, err := range map[string]error{
		"past the end":       f.read("small", []int{2, 3}, part, 4),
		"before the start":   f.read("small", []int{2, 3}, part, -1),
		"another shape":      f.read("small", []int{3, 2}, part, 0),
		"another rank":       f.read("small", []int{6}, part, 0),
		"no such tensor":     f.read("none", []int{3}, part, 0),
		"not float16":        f.read("ints", []int{1}, part[:1], 0),
		"more than there is": f.read("empty", []int{0, 4}, part, 0),
	} {
		if err == nil {
			t.Errorf("%s: no error", what)
		}
	}
}

// A file cut short, or one whose header points outside it or says what
// its bytes aren't, is an error when it is opened: not a panic, and not a
// read of what isn't the tensor's.
func TestSafetensorsBroken(t *testing.T) {
	good := stFile([]stTensor{{name: "w", shape: []int{2, 2}, data: []uint16{1, 2, 3, 4}}})
	if _, err := openBytes(good); err != nil {
		t.Fatal(err)
	}
	for cut := 0; cut < len(good); cut += 7 {
		if _, err := openBytes(good[:cut]); err == nil {
			t.Errorf("cut at %d of %d: no error", cut, len(good))
		}
	}
	raw := func(header string, data int) []byte {
		out := binary.LittleEndian.AppendUint64(nil, uint64(len(header)))
		return append(append(out, header...), make([]byte, data)...)
	}
	tensor := func(shape string, begin, end int) string {
		return fmt.Sprintf(`{"w":{"dtype":"F16","shape":%s,"data_offsets":[%d,%d]}}`, shape, begin, end)
	}
	for what, file := range map[string][]byte{
		"the bytes end after the file":   raw(tensor("[2,2]", 0, 8), 6),
		"fewer bytes than the shape":     raw(tensor("[2,2]", 0, 6), 8),
		"more bytes than the shape":      raw(tensor("[2,2]", 0, 10), 16),
		"the end before the start":       raw(tensor("[2,2]", 8, 0), 16),
		"a start before the data":        raw(tensor("[2,2]", -8, 0), 16),
		"a negative size":                raw(tensor("[-2,-2]", 0, 8), 8),
		"a shape too big to count":       raw(tensor("[4294967296,4294967296,4294967296]", 0, 0), 8),
		"a header that is not JSON":      raw("{not json", 8),
		"a tensor that is not an object": raw(`{"w":[1,2]}`, 8),
		"a header longer than the file":  binary.LittleEndian.AppendUint64(nil, 1<<40),
		"a header longer than any":       append(binary.LittleEndian.AppendUint64(nil, maxHeader+1), make([]byte, 64)...),
	} {
		if _, err := openBytes(file); err == nil {
			t.Errorf("%s: no error", what)
		} else if !strings.Contains(err.Error(), "safetensors") {
			t.Errorf("%s: %v", what, err)
		}
	}
}
