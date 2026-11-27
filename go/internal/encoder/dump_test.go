package encoder

import (
	"fmt"
	"os"
	"sort"
	"testing"
)

// TestDumpModel prints a model's weights and nodes (ONNX_DUMP=file).
func TestDumpModel(t *testing.T) {
	path := os.Getenv("ONNX_DUMP")
	if path == "" {
		t.Skip()
	}
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	st, _ := f.Stat()
	g, err := parseModelFile(f, st.Size())
	if err != nil {
		t.Fatal(err)
	}
	names := []string{}
	for k := range g.weights {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, k := range names {
		fmt.Println("W", k, g.weights[k].dims, g.weights[k].dtype)
	}
	for _, n := range g.nodes {
		extra := ""
		if c, ok := n.attrs["value"]; ok && c.t != nil {
			extra = fmt.Sprint(" const", c.t.dims, c.t.f32, c.t.i64)
			if len(extra) > 200 {
				extra = extra[:200]
			}
		}
		for k, a := range n.attrs {
			if k != "value" {
				extra += fmt.Sprintf(" %s=%v/%v/%v/%q", k, a.i, a.f, a.ints, a.s)
			}
		}
		fmt.Println("N", n.op, n.inputs, n.outputs, extra)
	}
}
