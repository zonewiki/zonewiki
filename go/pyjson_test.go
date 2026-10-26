package main

import (
	"os"
	"testing"
)

// A file Python's json.dumps(indent=2, ensure_ascii=False) wrote reads and
// writes back byte for byte: key order, ints and floats, escapes.
func TestDumpsIsPythons(t *testing.T) {
	want, err := os.ReadFile("testdata/python.json")
	if err != nil {
		t.Fatal(err)
	}
	v, err := loads(want)
	if err != nil {
		t.Fatal(err)
	}
	if got := dumps(v); got != string(want) {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}

func TestPyFloatRepr(t *testing.T) {
	for f, want := range map[float64]string{
		1: "1.0", 0.1: "0.1", 1e16: "1e+16", 1.5e16: "1.5e+16", 1e-5: "1e-05", 1e-4: "0.0001",
		123456789.123: "123456789.123", -0.5: "-0.5", 1e100: "1e+100", 9999999999999998: "9999999999999998.0",
	} {
		if got := pyFloatRepr(f); got != want {
			t.Errorf("%v: %s, want %s", f, got, want)
		}
	}
}

func TestObjKeepsOrder(t *testing.T) {
	o := NewObj("b", int64(1), "a", int64(2))
	o.Set("b", int64(3)) // an existing key keeps its place
	o.Set("c", nil)
	o.Delete("a")
	if got := dumps(o); got != "{\n  \"b\": 3,\n  \"c\": null\n}" {
		t.Fatal(got)
	}
	if !pyEqual(NewObj("x", int64(1), "y", 1.5), NewObj("y", 1.5, "x", 1.0)) {
		t.Fatal("pyEqual: key order or 1 == 1.0")
	}
}
