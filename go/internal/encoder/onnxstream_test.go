package encoder

import (
	"bytes"
	"encoding/binary"
	"math"
	"testing"
)

// Protobuf, written by hand for the tests.
func pbVarint(x uint64) []byte { return binary.AppendUvarint(nil, x) }

func pbBytes(num int, b []byte) []byte {
	out := append(pbVarint(uint64(num)<<3|2), pbVarint(uint64(len(b)))...)
	return append(out, b...)
}

func pbInt(num int, x uint64) []byte { return append(pbVarint(uint64(num)<<3), pbVarint(x)...) }

func pbFloats(f []float32) []byte {
	b := make([]byte, 4*len(f))
	for i, x := range f {
		binary.LittleEndian.PutUint32(b[4*i:], math.Float32bits(x))
	}
	return b
}

// pbTensor: a float32 TensorProto, its data raw or as packed float_data.
func pbTensor(name string, dims []int, f []float32, packedFloats bool) []byte {
	var b []byte
	for _, d := range dims {
		b = append(b, pbInt(1, uint64(d))...)
	}
	b = append(b, pbInt(2, 1)...)
	b = append(b, pbBytes(8, []byte(name))...)
	if packedFloats {
		return append(b, pbBytes(4, pbFloats(f))...)
	}
	return append(b, pbBytes(9, pbFloats(f))...)
}

// A model's graph comes out of the file the same whether a tensor is small
// (read whole) or big (left in the file), and a big one's floats are read
// from any place in it.
func TestParseModelFile(t *testing.T) {
	small := []float32{1, 2, 3, 4, 5, 6}
	big := make([]float32, 300_000) // 1.2 MB: over inMemory
	for i := range big {
		big[i] = float32(i) * 0.5
	}
	node := append(pbBytes(1, []byte("x")), pbBytes(1, []byte("w.big"))...)
	node = append(node, pbBytes(2, []byte("/layer/MatMul_output_0"))...)
	node = append(node, pbBytes(4, []byte("MatMul"))...)
	var graphBytes []byte
	graphBytes = append(graphBytes, pbBytes(1, node)...)
	graphBytes = append(graphBytes, pbBytes(5, pbTensor("w.small", []int{2, 3}, small, false))...)
	graphBytes = append(graphBytes, pbBytes(5, pbTensor("w.big", []int{300, 1000}, big, false))...)
	graphBytes = append(graphBytes, pbBytes(5, pbTensor("w.big.packed", []int{300_000}, big, true))...)
	graphBytes = append(graphBytes, pbBytes(2, []byte("a graph's name: skipped"))...)
	model := append(pbInt(1, 8), pbBytes(7, graphBytes)...) // ir_version, graph

	g, err := parseModelFile(bytes.NewReader(model), int64(len(model)))
	if err != nil {
		t.Fatal(err)
	}
	if len(g.nodes) != 1 || g.nodes[0].op != "MatMul" || g.nodes[0].inputs[1] != "w.big" || g.nodes[0].outputs[0] != "/layer/MatMul_output_0" {
		t.Errorf("nodes: %+v", g.nodes)
	}
	src := &tensorReader{r: bytes.NewReader(model)}
	for name, want := range map[string][]float32{"w.small": small, "w.big": big, "w.big.packed": big} {
		ts := g.weights[name]
		if ts == nil || ts.dtype != 1 || ts.count() != len(want) {
			t.Fatalf("%s: %+v", name, ts)
		}
		if (ts.f32 == nil) != (len(want) > 1000) {
			t.Errorf("%s: in memory: %v", name, ts.f32 != nil)
		}
		got := make([]float32, len(want))
		if err := src.read(ts, got, 0); err != nil {
			t.Fatal(err)
		}
		for i := range want {
			if got[i] != want[i] {
				t.Fatalf("%s[%d] = %v, want %v", name, i, got[i], want[i])
			}
		}
		// A part of it, from the middle to the end; and not past the end.
		from := len(want) / 3
		part := make([]float32, len(want)-from)
		if err := src.read(ts, part, from); err != nil || part[0] != want[from] || part[len(part)-1] != want[len(want)-1] {
			t.Errorf("%s from %d: %v, err %v", name, from, part[0], err)
		}
		if err := src.read(ts, part, from+1); err == nil {
			t.Errorf("%s: read past the end", name)
		}
	}
	if d := g.weights["w.big"].dims; len(d) != 2 || d[0] != 300 || d[1] != 1000 {
		t.Errorf("dims %v", d)
	}
}

// A file cut short, or one whose big tensor is not what its dims say, is
// an error, not a panic or a read outside it.
func TestParseModelFileBroken(t *testing.T) {
	big := make([]float32, 300_000)
	node := pbBytes(4, []byte("MatMul"))
	good := pbBytes(7, append(pbBytes(1, node), pbBytes(5, pbTensor("w", []int{300_000}, big, false))...))
	for cut := 1; cut < len(good); cut += len(good) / 37 {
		if _, err := parseModelFile(bytes.NewReader(good[:cut]), int64(cut)); err == nil {
			t.Errorf("cut at %d of %d: no error", cut, len(good))
		}
	}
	wrongDims := pbBytes(7, append(pbBytes(1, node), pbBytes(5, pbTensor("w", []int{7}, big, false))...))
	if _, err := parseModelFile(bytes.NewReader(wrongDims), int64(len(wrongDims))); err == nil {
		t.Error("a tensor of other dims than its data: no error")
	}
	empty := pbInt(1, 8)
	if _, err := parseModelFile(bytes.NewReader(empty), int64(len(empty))); err == nil {
		t.Error("no graph: no error")
	}
}
