// Zone's old names: what Zone still answers to from before its renames, so
// links, bookmarks, clients, settings and wikis from then keep working.
// Every one of them is here or named here, and all go in Zone 2.0:
//
//   - Leaf → Zone: LEAF_… variables read for unset ZONE_… ones (zoneEnv,
//     config.go); .leaf/ moved to .zone/ at start-up (migrate.go; stateDirIn
//     reads .leaf/ while it is the wiki's only one, excludeState its
//     exclude lines); the git committer Leaf taken over (gitSetup) and its
//     commits shown as Zone's (legacyAppName, commitAuthor); the sharing
//     cookie leaf-sharing (sharingOn); the MCP tool leaf_help
//     (mcpToolAliases); browser storage under leaf-… keys
//     (public/js/oldnames.js); the semantic model downloaded to
//     ~/.cache/leaf (modelDir).
//   - Deck → Tasks: _deck/ moved to _tasks/ (migrate.go; tasksDir reads
//     _deck/ while there is no _tasks/); /deck and /api/deck/… (below).
//   - Tasks home → pinned lists: [[tasks: … home=1]] pins as pin=1 does
//     (pinnedParam).
//   - Scratch → Notes: /page/_scratch and POST /api/scratch (below),
//     ?scratch=1 on /api/search (apiSearch), the MCP tool file_scratch_note
//     (mcpToolAliases).
package main

import (
	"net/http"
	"strings"
)

// oldNamesUntil: the version that drops the old names.
const oldNamesUntil = "2.0"

// oldNameRoutes: the routes by their old names, each the route by its new
// one (mux: where those are).
func oldNameRoutes(mux *http.ServeMux) {
	// Notes' and Tasks' pages, while they were Scratch and Deck: their links
	// and bookmarks.
	mux.HandleFunc("GET /page/_scratch", redirectKeepingQuery("/notes"))
	mux.HandleFunc("GET /deck", redirectKeepingQuery("/tasks"))
	mux.HandleFunc("POST /api/scratch", writesPages(apiNewNote))
	// /api/deck/…: the task API's routes, for clients that still use them.
	deckAPI := func(w http.ResponseWriter, r *http.Request) {
		r2 := r.Clone(r.Context())
		r2.URL.Path = "/api/tasks" + strings.TrimPrefix(r.URL.Path, "/api/deck")
		r2.URL.RawPath = ""
		mux.ServeHTTP(w, r2)
	}
	mux.HandleFunc("/api/deck", deckAPI)
	mux.HandleFunc("/api/deck/{rest...}", deckAPI)
}

// redirectKeepingQuery: a redirect to the path to, the request's query kept.
func redirectKeepingQuery(to string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		target := to
		if r.URL.RawQuery != "" {
			target += "?" + r.URL.RawQuery
		}
		http.Redirect(w, r, target, http.StatusFound)
	}
}
