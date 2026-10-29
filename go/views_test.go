package main

import "testing"

func TestInitialFunc(t *testing.T) {
	initial := funcs["initial"].(func(string) string)
	for in, want := range map[string]string{"alice": "A", "émile": "É", "": ""} {
		if got := initial(in); got != want {
			t.Errorf("initial(%q) = %q, want %q", in, got, want)
		}
	}
}
