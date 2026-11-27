package encoder

// Reading an ONNX file: just enough of its protobuf to get the graph's
// nodes and its weights (the initializers), with no protobuf library.

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
)

// tensor: an initializer or a Constant node's value.
type tensor struct {
	name  string
	dims  []int
	dtype int       // 1 float32, 7 int64
	f32   []float32 // in memory: a small tensor
	i64   []int64
	// A big tensor of float32 stays in the file: its floats' bytes
	// (little-endian) are size bytes from off.
	off, size int64
}

type node struct {
	op, name string
	inputs   []string
	outputs  []string
	attrs    map[string]attr
}

type attr struct {
	f    float32
	i    int64
	s    string
	ints []int64
	t    *tensor
}

type graph struct {
	nodes   []node
	weights map[string]*tensor
}

var errProto = errors.New("onnx: malformed protobuf")

// field: the next field of a message: its number, wire type, and its value
// (a varint, or the bytes of a length-delimited or fixed field).
func field(b []byte) (num, wt int, v uint64, data, rest []byte, err error) {
	key, n := binary.Uvarint(b)
	if n <= 0 {
		return 0, 0, 0, nil, nil, errProto
	}
	b = b[n:]
	num, wt = int(key>>3), int(key&7)
	switch wt {
	case 0:
		v, n = binary.Uvarint(b)
		if n <= 0 {
			return 0, 0, 0, nil, nil, errProto
		}
		return num, wt, v, nil, b[n:], nil
	case 1:
		if len(b) < 8 {
			return 0, 0, 0, nil, nil, errProto
		}
		return num, wt, 0, b[:8], b[8:], nil
	case 2:
		l, n := binary.Uvarint(b)
		if n <= 0 || uint64(len(b)-n) < l {
			return 0, 0, 0, nil, nil, errProto
		}
		return num, wt, 0, b[n : n+int(l)], b[n+int(l):], nil
	case 5:
		if len(b) < 4 {
			return 0, 0, 0, nil, nil, errProto
		}
		return num, wt, 0, b[:4], b[4:], nil
	}
	return 0, 0, 0, nil, nil, fmt.Errorf("onnx: wire type %d", wt)
}

// each calls f for every field of a message.
func each(b []byte, f func(num, wt int, v uint64, data []byte) error) error {
	for len(b) > 0 {
		num, wt, v, data, rest, err := field(b)
		if err != nil {
			return err
		}
		if err := f(num, wt, v, data); err != nil {
			return err
		}
		b = rest
	}
	return nil
}

// varints: a repeated int64 field, packed or not.
func varints(wt int, v uint64, data []byte, out []int64) ([]int64, error) {
	if wt == 0 {
		return append(out, int64(v)), nil
	}
	for len(data) > 0 {
		x, n := binary.Uvarint(data)
		if n <= 0 {
			return nil, errProto
		}
		out = append(out, int64(x))
		data = data[n:]
	}
	return out, nil
}

func parseTensor(b []byte) (*tensor, error) {
	t := &tensor{}
	var raw []byte
	var floats []float32
	var ints []int64
	err := each(b, func(num, wt int, v uint64, data []byte) error {
		var err error
		switch num {
		case 1:
			var d []int64
			d, err = varints(wt, v, data, nil)
			for _, x := range d {
				t.dims = append(t.dims, int(x))
			}
		case 2:
			t.dtype = int(v)
		case 4:
			if wt == 5 {
				floats = append(floats, math.Float32frombits(binary.LittleEndian.Uint32(data)))
			} else {
				for i := 0; i+4 <= len(data); i += 4 {
					floats = append(floats, math.Float32frombits(binary.LittleEndian.Uint32(data[i:])))
				}
			}
		case 7:
			ints, err = varints(wt, v, data, ints)
		case 8:
			t.name = string(data)
		case 9:
			raw = data
		case 13, 14:
			if num == 13 || v != 0 {
				return fmt.Errorf("onnx: %s: external data", t.name)
			}
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	n := 1
	for _, d := range t.dims {
		n *= d
	}
	switch t.dtype {
	case 1:
		if raw != nil {
			if len(raw) != 4*n {
				return nil, fmt.Errorf("onnx: %s: %d bytes for %d floats", t.name, len(raw), n)
			}
			t.f32 = make([]float32, n)
			for i := range t.f32 {
				t.f32[i] = math.Float32frombits(binary.LittleEndian.Uint32(raw[4*i:]))
			}
		} else {
			t.f32 = floats
		}
	case 7:
		if raw != nil {
			t.i64 = make([]int64, len(raw)/8)
			for i := range t.i64 {
				t.i64[i] = int64(binary.LittleEndian.Uint64(raw[8*i:]))
			}
		} else {
			t.i64 = ints
		}
	}
	return t, nil
}

func parseAttr(b []byte) (string, attr, error) {
	var name string
	var a attr
	err := each(b, func(num, wt int, v uint64, data []byte) error {
		var err error
		switch num {
		case 1:
			name = string(data)
		case 2:
			a.f = math.Float32frombits(binary.LittleEndian.Uint32(data))
		case 3:
			a.i = int64(v)
		case 4:
			a.s = string(data)
		case 5:
			a.t, err = parseTensor(data)
		case 8:
			a.ints, err = varints(wt, v, data, a.ints)
		}
		return err
	})
	return name, a, err
}

func parseNode(b []byte) (node, error) {
	n := node{attrs: map[string]attr{}}
	err := each(b, func(num, wt int, v uint64, data []byte) error {
		switch num {
		case 1:
			n.inputs = append(n.inputs, string(data))
		case 2:
			n.outputs = append(n.outputs, string(data))
		case 3:
			n.name = string(data)
		case 4:
			n.op = string(data)
		case 5:
			k, a, err := parseAttr(data)
			if err != nil {
				return err
			}
			n.attrs[k] = a
		}
		return nil
	})
	return n, err
}
