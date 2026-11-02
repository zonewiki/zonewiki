package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestInitialFunc(t *testing.T) {
	initial := funcs["initial"].(func(string) string)
	for in, want := range map[string]string{"alice": "A", "émile": "É", "": ""} {
		if got := initial(in); got != want {
			t.Errorf("initial(%q) = %q, want %q", in, got, want)
		}
	}
}

// A page view walks the wiki's folders once, however many of its parts
// list the pages (backlinks, task lists, the Tasks home, wikilinks …).
func TestPageViewWalksOnce(t *testing.T) {
	testWiki(t, map[string]string{
		"index": "# Home\n", "a": "# A\n\n[[b]] [[missing]]\n\n[[tasks: list=work]]\n",
		"b": "# B\n\n[[a]]\n", "todo": "# Todo\n\n[[tasks: pin=1]]\n",
	})
	srv := httptest.NewServer(newHandler())
	defer srv.Close()
	before := pageWalks.Load()
	req, _ := http.NewRequest("GET", srv.URL+"/page/a", nil)
	req.Host = "localhost:5000" // the Host check's
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d", resp.StatusCode)
	}
	if n := pageWalks.Load() - before; n != 1 {
		t.Errorf("%d walks for one page view", n)
	}
}
