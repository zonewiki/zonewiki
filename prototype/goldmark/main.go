package main

// Reads JSON lines on stdin: first the wiki, {"slugs": [...], "hidden": [...]},
// then one job per page, {"id", "content", "env"}: the page's file as it is.
// Writes {"id", "prepared", "html"} per job.
import (
	"bufio"
	"encoding/json"
	"os"
)

type wikiLine struct {
	Slugs  []string `json:"slugs"`
	Hidden []string `json:"hidden"`
}

type job struct {
	ID      string `json:"id"`
	Content string `json:"content"`
	Env     Env    `json:"env"`
}

type result struct {
	ID       string   `json:"id"`
	Prepared Prepared `json:"prepared"`
	HTML     string   `json:"html"`
	Error    string   `json:"error,omitempty"`
}

func set(items []string) map[string]bool {
	m := make(map[string]bool, len(items))
	for _, s := range items {
		m[s] = true
	}
	return m
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 64<<20), 64<<20)
	out := json.NewEncoder(os.Stdout)
	var w wikiLine
	if !in.Scan() || json.Unmarshal(in.Bytes(), &w) != nil {
		panic("first line: the wiki")
	}
	wiki := &Wiki{Slugs: set(w.Slugs), Hidden: set(w.Hidden)}
	for in.Scan() {
		var j job
		if err := json.Unmarshal(in.Bytes(), &j); err != nil {
			panic(err)
		}
		res := result{ID: j.ID, Prepared: Prepare(j.Content, wiki)}
		html, err := Render(res.Prepared.Text, &j.Env)
		res.HTML = html
		if err != nil {
			res.Error = err.Error()
		}
		if err := out.Encode(res); err != nil {
			panic(err)
		}
	}
	if err := in.Err(); err != nil {
		panic(err)
	}
}
