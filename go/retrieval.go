// Search and retrieval (src/retrieval.py). Semantic search isn't ported yet
// (PORT.md step 6): the search dialog says it is off, and pages have no
// Related section.
package main

// semanticStatus: how content search works now, for the search dialog:
// "on", "indexing", "failed" or "off".
func semanticStatus() string { return "off" }

// relatedSection: the pages most similar in meaning (semantic search only).
func relatedSection(rq *Req, slug, path string, backlinks []PageRef) M { return nil }
