package jsonobj

import "testing"

func TestJSONObj(t *testing.T) {
	for _, in := range []string{`null`, `[]`, `"x"`, `{`} {
		if _, ok := Parse([]byte(in)); ok {
			t.Errorf("%s read as an object", in)
		}
	}
	o, ok := Parse([]byte(`{"s": "a", "n": 2.0, "f": 2.5, "b": true, "z": null, "l": ["a", 1]}`))
	if !ok || !o.Has("z") || !o.Null("z") || !o.Null("missing") || o.Null("s") {
		t.Fatalf("%v", o)
	}
	if s, ok := As[string](o["s"]); !ok || s != "a" {
		t.Error("s")
	}
	if _, ok := As[string](o["z"]); ok {
		t.Error("null read as a string")
	}
	if _, ok := As[bool](o["n"]); ok {
		t.Error("a number read as a bool")
	}
	if n, ok := WholeNumber(o["n"]); !ok || n != 2 {
		t.Error("2.0 is whole")
	}
	if _, ok := WholeNumber(o["f"]); ok {
		t.Error("2.5 is not whole")
	}
	if !True(o["b"]) || True(o["n"]) {
		t.Error("only true is true")
	}
	for k, want := range map[string]string{"s": "a", "n": "2.0", "b": "true", "z": "", "l": ""} {
		if got, _ := ScalarText(o[k]); got != want {
			t.Errorf("ScalarText(%s) = %q, want %q", k, got, want)
		}
	}
	o.Set("t", "<a & b>")
	if s, _ := As[string](o["t"]); s != "<a & b>" {
		t.Errorf("set: %s", o["t"])
	}
	if got := Text("<ü>"); got != `"<ü>"` {
		t.Errorf("jsonText: %s", got)
	}
}
