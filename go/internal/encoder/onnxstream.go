package encoder

// An ONNX file read where it is: the graph's fields are walked in the
// file, nodes and small tensors read whole, and of a big tensor only where
// its floats are. So a 640 MB model costs its loader a megabyte.

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
)

// inMemory: a tensor up to this many bytes is read whole.
const inMemory = 1 << 20

// fileEach calls f for every field of the message at off..end of r: its
// number and wire type, a varint's value, and where the bytes of a
// length-delimited or fixed field are.
func fileEach(r io.ReaderAt, off, end int64, f func(num, wt int, v uint64, at, n int64) error) error {
	var head [20]byte // a key and a length, 10 bytes each at most
	for off < end {
		got, err := r.ReadAt(head[:min(int64(len(head)), end-off)], off)
		if err != nil && err != io.EOF {
			return err
		}
		b := head[:got]
		key, kn := binary.Uvarint(b)
		if kn <= 0 {
			return errProto
		}
		num, wt := int(key>>3), int(key&7)
		var v uint64
		at, n := off+int64(kn), int64(0)
		switch wt {
		case 0:
			var vn int
			if v, vn = binary.Uvarint(b[kn:]); vn <= 0 {
				return errProto
			}
			at += int64(vn)
		case 1:
			n = 8
		case 5:
			n = 4
		case 2:
			l, ln := binary.Uvarint(b[kn:])
			if ln <= 0 || l > uint64(end-at-int64(ln)) {
				return errProto
			}
			at, n = at+int64(ln), int64(l)
		default:
			return fmt.Errorf("onnx: wire type %d", wt)
		}
		if at+n > end {
			return errProto
		}
		if err := f(num, wt, v, at, n); err != nil {
			return err
		}
		off = at + n
	}
	return nil
}

func readAt(r io.ReaderAt, at, n int64) ([]byte, error) {
	b := make([]byte, n)
	if _, err := r.ReadAt(b, at); err != nil {
		return nil, err
	}
	return b, nil
}

// parseModelFile: the graph of the ONNX model in r, size bytes long.
func parseModelFile(r io.ReaderAt, size int64) (*graph, error) {
	g := &graph{weights: map[string]*tensor{}}
	err := fileEach(r, 0, size, func(num, wt int, v uint64, at, n int64) error {
		if num != 7 || wt != 2 {
			return nil
		}
		return fileEach(r, at, at+n, func(num, wt int, v uint64, at, n int64) error {
			if wt != 2 || (num != 1 && num != 5) {
				return nil
			}
			var t *tensor
			var err error
			switch {
			case num == 1:
				var b []byte
				if b, err = readAt(r, at, n); err != nil {
					return err
				}
				var nd node
				if nd, err = parseNode(b); err != nil {
					return err
				}
				g.nodes = append(g.nodes, nd)
				return nil
			case n <= inMemory:
				var b []byte
				if b, err = readAt(r, at, n); err != nil {
					return err
				}
				t, err = parseTensor(b)
			default:
				t, err = parseTensorFile(r, at, at+n)
			}
			if err != nil {
				return err
			}
			g.weights[t.name] = t
			return nil
		})
	})
	if err == nil && len(g.nodes) == 0 {
		err = errors.New("onnx: no graph")
	}
	return g, err
}

// parseTensorFile: a big tensor, its floats left in the file.
func parseTensorFile(r io.ReaderAt, off, end int64) (*tensor, error) {
	t := &tensor{off: -1}
	err := fileEach(r, off, end, func(num, wt int, v uint64, at, n int64) error {
		switch {
		case num == 1 && wt == 0:
			t.dims = append(t.dims, int(v))
		case num == 1 && wt == 2 && n <= 1024:
			b, err := readAt(r, at, n)
			if err != nil {
				return err
			}
			d, err := varints(wt, 0, b, nil)
			for _, x := range d {
				t.dims = append(t.dims, int(x))
			}
			return err
		case num == 2:
			t.dtype = int(v)
		case num == 8 && n <= 4096:
			b, err := readAt(r, at, n)
			t.name = string(b)
			return err
		case (num == 9 || num == 4) && wt == 2:
			// raw_data, or float_data packed: the same bytes.
			t.off, t.size = at, n
		case num == 13, num == 14 && v != 0:
			return fmt.Errorf("onnx: %s: external data", t.name)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if t.dtype != 1 || t.off < 0 || t.size != 4*int64(t.count()) {
		return nil, fmt.Errorf("onnx: %s: a big tensor that is not float32, or not its size", t.name)
	}
	return t, nil
}

// count: how many values the tensor has.
func (t *tensor) count() int {
	n := 1
	for _, d := range t.dims {
		n *= d
	}
	return n
}

// tensorReader reads tensors' floats from the file, through one buffer.
type tensorReader struct {
	r   io.ReaderAt
	buf []byte
}

// read: the tensor's floats from the i-th on, as many as dst takes.
func (s *tensorReader) read(t *tensor, dst []float32, i int) error {
	if i < 0 || i+len(dst) > t.count() || t.dtype != 1 {
		return fmt.Errorf("onnx: %s: read outside the tensor", t.name)
	}
	if t.f32 != nil {
		copy(dst, t.f32[i:])
		return nil
	}
	if s.buf == nil {
		s.buf = make([]byte, inMemory)
	}
	at := t.off + 4*int64(i)
	for len(dst) > 0 {
		n := min(len(dst), len(s.buf)/4)
		b := s.buf[:4*n]
		if _, err := s.r.ReadAt(b, at); err != nil {
			return err
		}
		for j := range dst[:n] {
			dst[j] = math.Float32frombits(binary.LittleEndian.Uint32(b[4*j:]))
		}
		dst, at = dst[n:], at+int64(4*n)
	}
	return nil
}
