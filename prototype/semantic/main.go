package main

// Reads JSON on stdin, {"model": dir, "lib": libonnxruntime.so, "texts": […],
// "batch": 64}, and writes each text's token ids and its vector as the index
// stores it (embed._normalize of the model's), the vectors base64 float32
// little-endian as Leaf's cache has them, and how long embedding took.
import (
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"math"
	"os"
	"time"
)

type input struct {
	Model string   `json:"model"`
	Lib   string   `json:"lib"`
	Texts []string `json:"texts"`
	Batch int      `json:"batch"`
}

type output struct {
	IDs     [][]int  `json:"ids"`
	Vectors []string `json:"vectors"`
	Seconds float64  `json:"seconds"`
}

func main() {
	var in input
	if err := json.NewDecoder(os.Stdin).Decode(&in); err != nil {
		panic(err)
	}
	e, err := NewEmbedder(in.Model, in.Lib)
	if err != nil {
		panic(err)
	}
	var out output
	for _, t := range in.Texts {
		out.IDs = append(out.IDs, e.tok.Encode(t))
	}
	start := time.Now()
	for i := 0; i < len(in.Texts); i += in.Batch {
		vecs, err := e.Embed(in.Texts[i:min(i+in.Batch, len(in.Texts))])
		if err != nil {
			panic(err)
		}
		for _, v := range vecs {
			buf := make([]byte, 4*len(v))
			for d, x := range Normalize(v) {
				binary.LittleEndian.PutUint32(buf[4*d:], math.Float32bits(x))
			}
			out.Vectors = append(out.Vectors, base64.StdEncoding.EncodeToString(buf))
		}
	}
	out.Seconds = time.Since(start).Seconds()
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		panic(err)
	}
}
