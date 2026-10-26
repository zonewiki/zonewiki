package main

import "testing"

func TestJSONObj(t *testing.T) {
	for _, in := range []string{`null`, `[]`, `"x"`, `{`} {
		if _, ok := parseObj([]byte(in)); ok {
			t.Errorf("%s read as an object", in)
		}
	}
	o, ok := parseObj([]byte(`{"s": "a", "n": 2.0, "f": 2.5, "b": true, "z": null, "l": ["a", 1]}`))
	if !ok || !o.has("z") || !o.null("z") || !o.null("missing") || o.null("s") {
		t.Fatalf("%v", o)
	}
	if s, ok := as[string](o["s"]); !ok || s != "a" {
		t.Error("s")
	}
	if _, ok := as[string](o["z"]); ok {
		t.Error("null read as a string")
	}
	if _, ok := as[bool](o["n"]); ok {
		t.Error("a number read as a bool")
	}
	if n, ok := wholeNumber(o["n"]); !ok || n != 2 {
		t.Error("2.0 is whole")
	}
	if _, ok := wholeNumber(o["f"]); ok {
		t.Error("2.5 is not whole")
	}
	if !jsonTrue(o["b"]) || jsonTrue(o["n"]) {
		t.Error("only true is true")
	}
	for k, want := range map[string]string{"s": "a", "n": "2.0", "b": "true", "z": "", "l": ""} {
		if got, _ := scalarText(o[k]); got != want {
			t.Errorf("scalarText(%s) = %q, want %q", k, got, want)
		}
	}
	o.set("t", "<a & b>")
	if s, _ := as[string](o["t"]); s != "<a & b>" {
		t.Errorf("set: %s", o["t"])
	}
	if got := jsonText("<ü>"); got != `"<ü>"` {
		t.Errorf("jsonText: %s", got)
	}
}
