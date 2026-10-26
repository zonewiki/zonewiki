package main

import "testing"

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
	if got := jsonText(o); got != `{"b":3,"c":null}` {
		t.Fatal(got)
	}
}
