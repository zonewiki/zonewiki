package encoder

// A safetensors file, read where it is: 8 bytes with the header's length
// (little-endian), the header (JSON: per tensor its type, shape and where
// its bytes are), then the tensors' bytes. Only what the model needs of
// it: tensors of float16.

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
)

// maxHeader: a header longer than this is not a model's (the model's is
// 20 KB).
const maxHeader = 16 << 20

type tensorInfo struct {
	Dtype   string   `json:"dtype"`
	Shape   []int    `json:"shape"`
	Offsets [2]int64 `json:"data_offsets"` // from the end of the header
}

// tensorFile: an open safetensors file's tensors.
type tensorFile struct {
	r       io.ReaderAt
	data    int64 // where the tensors' bytes start
	size    int64 // of the file
	tensors map[string]tensorInfo
	buf     []byte
}

// openTensors reads the header of the safetensors file in r, size bytes
// long. Every float16 tensor's place is checked here: inside the file,
// and as long as its shape says.
func openTensors(r io.ReaderAt, size int64) (*tensorFile, error) {
	var head [8]byte
	if size < 8 {
		return nil, fmt.Errorf("safetensors: a file of %d bytes", size)
	}
	if _, err := r.ReadAt(head[:], 0); err != nil {
		return nil, err
	}
	n := binary.LittleEndian.Uint64(head[:])
	if n > maxHeader || int64(n) > size-8 {
		return nil, fmt.Errorf("safetensors: a header of %d bytes in a file of %d", n, size)
	}
	b := make([]byte, n)
	if _, err := r.ReadAt(b, 8); err != nil {
		return nil, err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(b, &raw); err != nil {
		return nil, fmt.Errorf("safetensors: the header: %w", err)
	}
	f := &tensorFile{r: r, data: 8 + int64(n), size: size, tensors: map[string]tensorInfo{}}
	for name, v := range raw {
		if name == "__metadata__" {
			continue
		}
		var t tensorInfo
		if err := json.Unmarshal(v, &t); err != nil {
			return nil, fmt.Errorf("safetensors: %s: %w", name, err)
		}
		if t.Dtype != "F16" {
			continue // not the model's: read refuses it
		}
		count := int64(1)
		for _, d := range t.Shape {
			if d < 0 || (d > 0 && count > (1<<40)/int64(d)) {
				return nil, fmt.Errorf("safetensors: %s: shape %v", name, t.Shape)
			}
			count *= int64(d)
		}
		begin, end := t.Offsets[0], t.Offsets[1]
		if begin < 0 || end < begin || end > size-f.data || end-begin != 2*count {
			return nil, fmt.Errorf("safetensors: %s: bytes %d to %d for shape %v, in %d", name, begin, end, t.Shape, size-f.data)
		}
		f.tensors[name] = t
	}
	return f, nil
}

// shape: a float16 tensor's shape, nil when the file has none of the name.
func (f *tensorFile) shape(name string) []int {
	t, ok := f.tensors[name]
	if !ok {
		return nil
	}
	return t.Shape
}

// read: the tensor's float16s from the i-th on, as many as dst takes. The
// tensor must have the shape given.
func (f *tensorFile) read(name string, shape []int, dst []uint16, i int) error {
	t, ok := f.tensors[name]
	if !ok {
		return fmt.Errorf("safetensors: no float16 tensor %s", name)
	}
	if len(t.Shape) != len(shape) {
		return fmt.Errorf("safetensors: %s: shape %v, not %v", name, t.Shape, shape)
	}
	count := 1
	for k, d := range shape {
		if t.Shape[k] != d {
			return fmt.Errorf("safetensors: %s: shape %v, not %v", name, t.Shape, shape)
		}
		count *= d
	}
	if i < 0 || i > count || len(dst) > count-i {
		return fmt.Errorf("safetensors: %s: read outside the tensor", name)
	}
	if f.buf == nil {
		f.buf = make([]byte, 1<<20)
	}
	at := f.data + t.Offsets[0] + 2*int64(i)
	for len(dst) > 0 {
		n := min(len(dst), len(f.buf)/2)
		b := f.buf[:2*n]
		if _, err := f.r.ReadAt(b, at); err != nil {
			return err
		}
		for j := range dst[:n] {
			dst[j] = binary.LittleEndian.Uint16(b[2*j:])
		}
		dst, at = dst[n:], at+int64(2*n)
	}
	return nil
}
