package main

import "testing"

// zonewiki health asks localhost at the port Zone listens on.
func TestHealthURL(t *testing.T) {
	for args, want := range map[string]string{
		"":               "http://localhost:5000/api/health",
		":8080":          "http://localhost:8080/api/health",
		"127.0.0.1:9000": "http://localhost:9000/api/health",
		"nonsense":       "http://localhost:5000/api/health",
	} {
		var a []string
		if args != "" {
			a = []string{args}
		}
		if got := healthURL(a); got != want {
			t.Errorf("%q: %s", args, got)
		}
	}
}
