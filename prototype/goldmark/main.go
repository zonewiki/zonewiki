package main

// Reads JSON lines {"id", "text", "env"} on stdin, writes {"id", "html"}.
import (
	"bufio"
	"encoding/json"
	"os"
)

type job struct {
	ID   string `json:"id"`
	Text string `json:"text"`
	Env  Env    `json:"env"`
}

type result struct {
	ID    string `json:"id"`
	HTML  string `json:"html"`
	Error string `json:"error,omitempty"`
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 64<<20), 64<<20)
	out := json.NewEncoder(os.Stdout)
	for in.Scan() {
		var j job
		if err := json.Unmarshal(in.Bytes(), &j); err != nil {
			panic(err)
		}
		res := result{ID: j.ID}
		html, err := Render(j.Text, &j.Env)
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
