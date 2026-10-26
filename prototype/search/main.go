package main

// Reads one JSON object on stdin, {"pages": [Doc…], "chunks": [Doc…],
// "queries": [string…]}, and writes what Go makes of it: each document's
// tokens, and per query its parse, the raw BM25 ranking of both indexes,
// do_search's results and retrieve's sections.
import (
	"encoding/json"
	"os"
)

type input struct {
	Pages   []*Doc   `json:"pages"`
	Chunks  []*Doc   `json:"chunks"`
	Queries []string `json:"queries"`
}

type queryResult struct {
	Parsed    *Query   `json:"parsed"`
	PageHits  []Hit    `json:"page_hits"`
	ChunkHits []Hit    `json:"chunk_hits"`
	Search    []Result `json:"search"`
	Retrieve  []int    `json:"retrieve"`
	SearchAll []int    `json:"search_all"` // the /search page: up to 50
}

type output struct {
	PageTokens  [][]string    `json:"page_tokens"`
	ChunkTokens [][]string    `json:"chunk_tokens"`
	Queries     []queryResult `json:"queries"`
}

func main() {
	var in input
	if err := json.NewDecoder(os.Stdin).Decode(&in); err != nil {
		panic(err)
	}
	pages, chunks := NewCorpus(in.Pages), NewCorpus(in.Chunks)
	var out output
	for _, d := range pages.Docs {
		out.PageTokens = append(out.PageTokens, d.tokens)
	}
	for _, d := range chunks.Docs {
		out.ChunkTokens = append(out.ChunkTokens, d.tokens)
	}
	for _, raw := range in.Queries {
		q := ParseQuery(raw)
		out.Queries = append(out.Queries, queryResult{
			Parsed:    q,
			PageHits:  pages.ix.Search(q.RankingText(), len(pages.Docs)),
			ChunkHits: chunks.ix.Search(q.RankingText(), len(chunks.Docs)),
			Search:    DoSearch(raw, pages, chunks, 15),
			Retrieve:  chunks.Retrieve(q, 8),
			SearchAll: pages.Ranked(q, 50),
		})
	}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		panic(err)
	}
}
