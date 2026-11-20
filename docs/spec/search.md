# Search

Part of Zone's spec (see `AGENTS.md` for the index).

What a search is asked first (Query syntax, How words match), then what it gives back (Results) and who finds what (Who searches what).
How it works follows: Indexing, Ranking, Semantic search (opt-in), The index on disk.
Measuring retrieval, at the end, says how search's quality is measured on your wiki and on Zone's sample wiki.

Search runs on the server, over pages and tasks alike.
It answers the search modal (Ctrl+P, `js/search.js`), the results page `/search`, `GET /api/search` and the MCP tools `search_wiki` and `search_tasks` (`docs/spec/mcp.md`).
Search is case-insensitive.

## Query syntax

A query is words, and what filters them (`Query`, the same in `/api/search`, `/search`, `search_wiki` and `search_tasks`' query; listed in the `?` help modal):

| Syntax | Means | Example |
|--------|-------|---------|
| words | Docs with any of them, each scored on its own (How words match) | `roof repair` |
| `"exact phrase"` | The words as a phrase (case, accents and spacing ignored) | `"alex again"` |
| `-word` | Its stem must not occur | `-dockers` excludes "docker" |
| `-"phrase"` | The phrase must not occur | `-"next steps"` |
| `#tag` | Pages by frontmatter `tags:`, tasks by their tags, case ignored | `#recipe` |
| `-#tag` | Without the tag | `-#done` |
| `edited:` | A year, month or day, as a prefix | `edited:2026-09` |
| `after:` | On or after | `after:2026-07-01` |
| `before:` | Strictly before | `before:2026-08` (July and earlier) |
| `-edited:` | Not then | `-edited:2026-09` |
| `-after:` | = `before:` | `-after:2026-07-01` |
| `-before:` | = `after:` | `-before:2026-08` |

An unclosed quote, a lone `-` or `#`, or an invalid date (`edited:soon`) stay text.

### A query is a few words

A query is at most 500 characters (`MaxQueryChars`): a longer one is cut at its last space before that (`cutQuery` in `ParseQuery`; the browser's title filter likewise), and the assistants' `search_wiki` and `search_tasks` refuse it, saying so.
It ranks by its distinct words, each once and at most 32 of them (`queryWords`), and only the first 8 of those that match no word of the index are looked for as typos (`fuzzyWords`, How words match).
Each such word is compared with every word of the index, so before these limits a query as long as an address may be (a megabyte, the search modal's GET or a public wiki's visitor's) kept a processor busy for hours.

### Date filters

A page's date is its last edit, a task's its last activity (`lastActivity`: the latest of completion, history events, comments and creation).
Partial dates compare as prefixes, so `before:2026-08` is July and earlier.
Undated docs fail a date filter (`SearchDoc.date`).
A query of only date filters lists everything passing, newest first (`rankedDocs`), e.g. `edited:2026-09`.

### What ranks and what filters

`Query.Text` (words and phrases, no operators) ranks, both by keyword and by meaning, and is what excerpts mark;
the rest are filters (`Accepts`, applied before ranking, so meaning hits obey them too).
A query of only tags ranks by the tag words (`RankingText`); one of only exclusions finds nothing.
`search_tasks` is the exception: a query without words (only `-word`, `#tag` or date filters) filters the tasks asked for and keeps the tool's `order`, so one of only exclusions lists the tasks without them.
Docs carry lowercased `Tags` for this (`mdtext.PageTags`, `taskSearchDocs`).

In the search modal the instant title filter fuzzy-matches only the query's words (`queryWords` in `js/search.js`, mirroring `Query`: quotes dropped, `-…`, `#tag` and date-filter parts left out, so a query of only `#tag` shows no title hits),
while Content matches get the raw query.

### Stopwords

English and German function words ("the", "is", "in", "und", "für") are dropped from the **query**, never from the index, unless that leaves nothing ("what is this" still searches) (`stopwords`, `queryWords`).
In a small wiki they aren't in every page, so BM25 gave them weight: a question phrased as a sentence ranked pages by its filler words.
Words that are also nouns or names (may, will, it, man) are not in the list.
Excerpts don't mark them either; a phrase match still marks the whole phrase.

## How words match

- **Multi-word queries** score each term independently (rarer terms weigh more via IDF); the whole phrase no longer has to appear verbatim.
- **Words in any script**: `Tokenize` splits on runs of letters/digits (`[^\W_]+`), so "Müller" is one word,
  and folds case and accents (`fold`: ü→u, é→e, ß→ss), so `muller`, `MÜLLER` and `Müller` match each other.
- **Stemming** (`stem`) collapses inflections (run/runs/running, database/databases) so word variations match.
- **Partial words** match via prefix expansion; **typos** match via an edit-distance fuzzy fallback (`difflib`) only when a term has no exact/prefix hit, for the first 8 such terms of a query (A query is a few words).
- **Title boost**: title tokens are folded into the document token bag `TitleWeight` (3) extra times, so title matches rank higher.
- **File and folder names**: a page's slug words are in its token bag once (pages and sections alike),
  so `robin` finds `messages/2026-06-18_roof-update-alex-robin` even when the text never says it;
  once, like one mention in the text, so a file name never outranks what pages say.
- **Link targets are not indexed** (`StripLinkTargets`, pages and tasks): the `(url)` of `[text](url)`/`![alt](url)` and the URL of `[id]: url` definitions are dropped before tokenizing and excerpting, the link text stays.
  Otherwise every page with a `/page/…` link matched `page`.
  Bare URLs in the text stay searchable.

### Exact phrase first

A document containing the query's words as a phrase (case, accents and spacing ignored, stopwords included) moves to the front (`Phrase`, `phraseFirst`),
in BM25 order,
even one the ranking had cut off: "alex again" puts the note saying "Alex again" above notes with both words apart.
Browser search and `search_wiki` alike; the result opens the section with the phrase (`bestSections`).
The squashed text is checked in the index file (`TextHas`, The index on disk), or for a doc not in it, read again and kept for a while (`docTexts`).

## Results

### The result list

`GET /api/search?q=...` collects every page **and** all task titles/descriptions as documents, ranks them, and returns JSON: `[{ slug, title, excerpt, href, meta, type? }]`.
It returns up to 15 results (pages + tasks combined; `searchLimit`), or `&limit=N` (1–100, `searchMaxLimit`).
`href` is the server-computed destination URL for the result; task results have `type: "task"` and `task_id` fields.
With semantic search each ranking goes as deep as the list must be long (`rankedDocs`, `depth`).

The search modal asks for 16: when the 16th comes back it shows 15 and an **All results →** link (`.search-more`) to `/search?q=…` (plus `&unlinked=1` when that box is checked),
which lists up to 50 (`searchPageLimit`) under a count ("17 results", "The first 50 results").
`GET /search?q=...` renders a full results page for direct links / no-JS fallback.

### The line under a result

`meta` (`resultMeta`) is the small line under a result's title in the modal and on `/search`, telling alike titles apart:
a page's folder and last edit (`conversations/2026-07-08_alex-sam-debrief · edited 2026-10-02`),
a task's list and status (`Tasks › zoo · done 2026-05-01`).

**It says whose a result is, by sign and name, not by path**: a page of a group of yours reads `👥 dev · edited …` (`👥 dev/specs` in a folder there),
one of your private pages `🔒 Private/notes · …`,
a task of a list for some `👥 Tasks › hiring · open`,
a private task `🔒 Tasks › …`;
what is everyone's has no sign.

### Results open the matching section

A page result's `href` gets the anchor of the section the query matched, and its excerpt comes from that section, so the excerpt shows what the link jumps to (`bestSections`, `doSearch`).
The section is one whose own text has the query as a phrase,
else the best BM25-scored one whose own text (heading and body, not the page title) has a query word (`matches`),
else, for a page found by meaning, its most similar section.
A page matched only by its title gets no anchor (every section would match equally),
nor does a match before the first heading or under a `####`+ heading, which has no id (`sectionAnchorOf`: h1–h3 only).
Sections come from the same split as retrieval (`pageChunks`).

### Excerpts

An excerpt (`Excerpt`) shows a window around the first match, HTML-escaped, with every match in it marked (`<mark>`).
Matches are whole words found the way the index finds them (`matchSpans`: same stem, prefix, else fuzzy), so `running` marks "runs" and `art` never marks the inside of "restart".
A multi-word query found as a phrase (case and accents ignored) is marked as one span instead.

### Highlights on the opened page

A page result's `href` carries the words that matched as `?hl=…` (before the `#anchor`) (`MatchedWords`, `initSearchHighlights` in `js/search.js`),
as written in that page,
so stems,
typos and phrases are already resolved ("running" → `hl=runs`, a phrase as one `hl`);
at most 10, tasks get none.

The page marks every whole-word occurrence (`mark.search-hl`; phrase words may be separated by any whitespace), skipping `<pre>` code (highlight.js rewrites it) and task list embeds (`.tasks-app`).
A bar (`.search-hl-bar`, bottom centre, top on phones where the bottom edge holds the tab bar) shows the terms and "2 of 5" with ↑ ↓ ×.
It starts at the first match inside the section the `#anchor` opened.

| Key | Does |
|-----|------|
| **n** / **N** | Next / previous match (in the capture phase, so a task list embed's `n` doesn't also open a new task) |
| **Esc** or × | Unmarks and drops `hl` from the URL (`history.replaceState`); this Esc goes no further, so a task list embed's selection stays until the next Esc |

Listed in the `?` help.

### Selecting a task result

Selecting a task result (search modal or `/search` page) opens `/tasks?task=<id>&view=<open|closed>` on the tab matching the task's state,
plus `&list=<name>` for a task in a separate list, so it opens that list,
plus `&year=YYYY` for a task in an earlier year's archive,
with that task selected,
scrolled into view and briefly highlighted (`.task-flash`) — rather than the bare `_tasks` storage slug.
The link is by id, not title: titles can repeat or contain a `#word` the filter field would read as a tag.
Built by `taskHref` (the search modal uses the result's `href`).
A page's "Linked tasks" entries use the same link.

### Three sections of a page at most

**`search_wiki` gives at most three sections of a page** (`sectionsPerPage`, `capPerPage` in `retrieve`): a page about the query filled the list with its sections,
where an assistant wants to see what else there is and can read the page for more.
The sections after a page's first three are left out and the next results move up, so the list is as long as asked for.
Tasks aren't counted.

## Who searches what

With users, a search finds what its user may see and nothing else, and what they may not see takes no part in how their results rank (Private pages and Group pages in `docs/spec/users.md`).

- **One index of what is shared, and a layer per place**: search's keyword index (BM25) is made of the shared pages and tasks alone (`indexCache`);
  what isn't everyone's is searched along with it by those who see it, place by place
  (a user's private pages, their private tasks, a group's folder: `textsearch.Layer`, `Index.View`;
  their document count, frequencies and lengths join the shared ones at query time, so they rank exactly as if one index held what the viewer sees),
  while another user's words,
  counts and lengths take no part in anyone's ranking,
  nor in the vocabulary that prefix and typo matching draw on (else a typo that stops matching would tell that a word is in a private page).
- **A group's pages** are one layer: the keyword index of the shared pages has nothing of a group's;
  its pages are searched along with the shared ones by its members and nobody else, as a user's private pages are by them
  (`docShared`, `docPlace`, `Index.View`; a list for some's tasks likewise, Lists for some in `docs/spec/tasks.md`),
  so a group's words, counts and lengths take part in no one else's ranking, and a typo of a word only the group has suggests nothing to anyone else.
  The layer is made when a member first searches after a change in the folder, for all of them, and every further member's search finds it there
  (`TestAMembersIndexOfALargeGroup`; `TestAGroupsPagesAreInItsMembersIndexAlone` in `go/retrieval_test.go`).
- **The semantic index** holds every page's vectors, but a search looks only at the visible ones (pages, sections, tasks, Related, and the pages a new page is compared with).
- **What is private is in no index file** (The index on disk): a user's own private tasks' words are in their layer,
  and are kept from one making of it to the next (`viewTokens`, by the docs' keys, up to 400,000 tokens),
  so their private archives are read for that once, not again whenever one of their tasks changes.

### Nothing is kept per viewer

The docs that aren't everyone's are kept by their place (`indexCache.get`, `arrange`; `docPlace`: a user's private pages, a user's private tasks, a group's folder, no one's, the tasks of a shared list that is for some),
each place's as one layer (`textsearch.Layer`: its docs' lengths and postings, numbered among themselves),
made when someone who sees the place first searches and kept until one of its docs changes.
Who sees one doc of a place sees them all (`canSee` asks the place alone, `canSeeTask` of a private task its owner and of a shared one its list), so a search asks once per place.
Its view is the base with the layers of the places its viewer sees (`Index.View`), put together per search: a few words, whatever the wiki's size,
since what the views share (each doc's place in the order, the shared docs with their numbers, what the changed docs take from the base's statistics) is worked out once per change, not per viewer.
So a group's pages are indexed once, not once per member,
a search never waits for an index because others searched since,
and memory doesn't grow with who has searched.
Before, each viewer had an index of their own, numbering every doc of the wiki for them;
the bench's pages-and-groups case in `docs/spec/testing.md` guards that nothing is made per searcher again.

## Indexing

**Ranking is BM25**, implemented in Go (`go/internal/textsearch`, a package of its own without Zone's state; no database; `golang.org/x/text` for case folding and accents).
Pages and tasks are scored in **one combined index** so their relevance scores are directly comparable.

### What is indexed

- **Pages**, and their sections for `search_wiki` and section links (`pageChunks`).
  A page's documents are made once and cached in `searchDocCache` keyed by slug, invalidated per-file on `mtime` change (so on-disk edits stay reflected).
- **Tasks**: their docs come from `taskSearchDocs()`: title, description, comments, tags (as `#tag` words in the text, and as a `tags` list for the `#tag` filter) and the name of its list (`taskSearchText`).

### A task's record

**A task's doc keeps its record, not the task** (`taskRecord`): everything a search filters and sorts by and a result shows (its title, list, assignee, tags, dates, status, whose it is),
without what is long (its description, comments and history).
The whole task is read from its file when it is asked for (`fullTask`, `taskfiles.TaskIn`): for a hit's text and excerpt, for its tokens when it is indexed, for an assistant's answer.
Its words are in the index file, as a page's are, so nothing is read to search.
So the closed tasks of a wiki's years are about 0.9 KB each in memory, not the tasks themselves twice (once parsed, once the search's copy).
Of a record's 0.9 KB, 360 bytes are the task's own fields (a record is a `Task` without its long ones, so everything that asks a task something asks a record the same),
180 the search doc every page and section has too,
and the rest its id, title and times;
its short words (a list's name, a user's, a tag, a day) are one string for all records that say them (`same`).
Smaller than that would be another kind of doc for the search itself, not a leaner record.

**Made file by file** (`taskDocs.files`): when a task file changes, that file's docs are made anew, from what the store has parsed, without a copy of the tasks (`taskfiles.Look`);
every other file's stay, unread.
A task that didn't change keeps its doc, which is how the index tells what did.

**An archive's docs are kept in Zone's folder** (`.zone/index/tasks/<hash>.gob`: `archiveDocs`, `saveArchiveDocs`; the records with their keys, as made from the file with that time and size),
so a start knows the closed tasks of earlier months without reading them: its first search reads no archive but for a hit in one.
Whose a task is never comes from there: it is the folder its archive is in.

Ranking tasks for an assistant (`search_tasks` with a query, the tag hints of `create_task`) goes through this index too, among the tasks asked for (`searchWhere`),
not through an index built over them each time, which read every one of them.
So an assistant's tasks are ranked as the browser's search ranks tasks: a word's weight is by how rare it is in the wiki the user sees, pages included, not among the tasks asked for.

### Memory

A page's or section's doc keeps only what all of its page's docs share (slug, title, tags, mtime, its file) and, for a section, which of the page's sections it is:
not its text, its tokens, nor a section's heading, path, anchor or semantic key.
Those are read from its file again when asked for (`body`, `tokens`, `section`: a section's from its page split again),
which a search does only for the docs it shows or checks,
through `docTexts`,
which keeps the texts and splits read again (and the texts squashed of docs not in an index file) up to 16 MB (`docTextsMax`) and then starts over.
A big wiki has hundreds of thousands of sections, and their docs were most of its memory: a doc is 136 bytes (`TestSearchDocStaysSmall`).
A task's own fields (its text, status, year, last activity) hang off its doc (`taskDocInfo`), so a page's or section's doesn't carry them.
`search_wiki`'s fusion with semantic search matches a section by its key (`semKey`), worked out only for the pages its semantic hits are on (each hit names its page).

So excerpts, highlights, section links and `search_wiki`'s passages read the files of the docs they show;
a page gone since is left out of the results, a page changed since gives its text now (the next search makes its doc anew).
Tasks keep their text.
Building an index asks for each document's tokens in turn and keeps only the postings,
a `-word` filter asks the postings (`DocsWith`),
and leaving out a changed page takes one pass over them (`Without`), once per change.
What that costs: a page that was edited, or isn't everyone's, is read and tokenized when its layer is made (Nothing is kept per viewer: once per change of that place, not per viewer),
and a rebuild past `maxChanges` reads, splits and tokenizes everything, its tokens and its text squashed from one fold (`indexed`, `TokensAndSquash`).

### Keeping the index current

The BM25 index itself is cached (`searchIndex()`, `indexCache`), keyed by the identities of the cached docs.
A change to a page or task does not build it again: the cache keeps the index made at the last rebuild,
leaves out the docs that are gone from it and adds the new ones (`Index.View`: what is left out, `Without`, and a layer of the new ones),
searched with the statistics of the docs that are left,
so hits,
scores and the order of equal scores are exactly those of an index built over them (tests compare the two on random edits);
a task that didn't change keeps its doc, so only what changed counts.
Only when the changes pass 2% of the docs (`maxChanges`, at least 32) is it built again, so an edit costs a few percent of a rebuild on average.

`iterPages` walks the wiki skipping `_*` and hidden folders (`SkipDir`), instead of walking `.git`'s objects;
a search lists the pages and their files' mtimes once (`rq.pageStats`), for both its indexes and its results' task lists, instead of walking and checking every file three times.

## Ranking

### The numbers

In `go/internal/textsearch/search.go` unless said otherwise:

- BM25 with `k1 = 1.5` and `b = 0.75` (`bm25K1`, `bm25B`); a term's IDF is `ln(1 + (N − n + 0.5) / (n + 0.5))`.
- A query word counts with weight 1 where a doc has it (as its stem),
  0.8 for each word it starts (`prefixWeight`; only for query words of 3 characters or more),
  and, only when it has neither, 0.5 for each of up to 3 close typos (`fuzzyWeight`; words of 4 characters or more, difflib's ratio at least 0.8, `closeMatchesIn`).
- A title's words count 3 more times (`TitleWeight`), a slug's words once.
- With semantic search:
  each ranking goes 30 deep into the fusion, or as deep as the list asked for (`fuseDepth` in `go/retrieval.go`), fused with `rrfK = 60`;
  sections longer than 800 characters are split at paragraphs (`maxChunkChars` in `go/embed.go`);
  the model reads at most 512 tokens and gives vectors of 768 dimensions (`maxTokens`, `embedDim` in `go/embed_onnx.go`);
  its floors are 0.25 for search and Related pages and 0.3 for tasks (`thresholds` in `go/embed.go`; 0.4 each for a model without its own).

### Scoring fast

The index keeps an inverted index (`postings`: term → docs), so a search scores only the docs containing its terms, ties in document order.
Prefix expansion bisects the sorted vocabulary;
typo matching (`CloseMatches`) skips a candidate whose `real_quick_ratio` or `quick_ratio`, upper bounds of the difflib ratio, are under the cutoff, as Python does.
A search that wants only some documents (phrase hits, the sections of the pages shown, the query's filters) asks `SearchWhere`,
which scores only those and, for a short limit, keeps just the best few instead of sorting every hit: the same hits in the same order.
Matching words in a text for excerpts, highlights and section links (`matchSpans`) caches each text's split into words and stems
(`analyze`, with the distinct stems sorted for prefix bisection and grouped by length for fuzzy candidates)
and each word's fold and stem;
that was most of a search's time.

## Semantic search

Semantic search finds what a query means as well as the words it says (`go/embed.go`).
It is opt-in; unset, Zone is keyword-only and nothing in this section runs.

### Turning it on

Set `ZONE_SEMANTIC_SEARCH=on` in `.env` (a local ONNX model, `jinaai/jina-embeddings-v2-base-de`, German + English; `off`, or unset, turns it off; any other value is logged and keeps it off).
The search dialog shows whether it is on (Search mode in `navigation.md`).

Set,
`run.sh` passes the setting as a build arg and the `Containerfile`'s `semantic` stage fetches onnxruntime's library and the model's files at a fixed revision,
each checked against its SHA-256 (the same as `modelRevision`, `modelFiles`),
into `ZONE_MODEL_DIR=/opt/zonewiki-model`: the image grows by about 0.7 GB,
and the first search doesn't wait for a download.
That stage copies nothing from the repo, so its layer is rebuilt only when the model or onnxruntime changes, not on every code change.
Without the container the model goes to `~/.cache/zonewiki/`, downloaded on first use (`downloadModel`, the same hashes), and onnxruntime's library must be on the loader's path (`LD_LIBRARY_PATH`).
A missing library, a bad setting or a failing model is logged (`[semantic] …`) and search falls back to keywords.

### The model

It is the only model: `onnxEmbedder` runs it on onnxruntime (`go/embed_onnx.go`, through onnxruntime_go, which loads `libonnxruntime.so` when the model is first used; a binary built without cgo has no semantic search)
with its tokenizer in Go (`go/internal/tokenizer`, the model's `tokenizer.json`; it encodes a text's pieces only until it has 512 tokens, merges a piece's pairs from a heap, in n log n of its length, so a long unbroken word costs little, and keeps the tokens of at most 100,000 short pieces),
with its recipe (at most 512 tokens, the token vectors averaged where the attention mask is set, normalised, no query or passage prefixes),
and another model may need another recipe,
so there is no setting for one.
Until 2026-10 the Python Zone ran it, with fastembed before that; the vectors are the same bit for bit, and the cache keeps its name `fastembed:<model>`, so it stays valid.
(An Ollama backend existed and was removed as unused; it is in the git history.)

### Chunks

Each page is split at its headings, as the markdown parser finds them (`chunkSections`, `headingsIn`: setext and indented ones too, not those in code, comments, quotes or lists), long sections further at paragraphs (`maxChunkChars`).
Each chunk is embedded as `title\nheading path\nbody`, the path being the heading after the ones it sits under (`Problems › Windows`, `chunkText`): a section headed "Windows" or "Next steps" says little on its own.
The BM25 section index (`pageChunks`) keeps the section's own heading
(with the path in its tokens too, every parent word repeated across sibling sections cost keyword ranking on a test set: `./run.sh eval`),
and `search_wiki` shows the path (`Page › Problems › Windows`);
the anchor stays the section's own heading's.
`semanticText` drops the frontmatter block, the H1 and the `[[tasks: …]]` directives (markup, not content: pages holding only a task list embed came out as each other's closest match) and puts tldr and tags first.

**Tasks** are embedded too, one vector each, from their search text (title, description, comments, `#tags` and list; `semanticTasks`, the index's tasks function, started in `main` once the task files are set up),
keyed by a hash of it in the same cache (`"tasks"`: id → hash), so only new or edited tasks are embedded.

### The vector cache

No vector database.
Vectors are in memory and cached in `$ZONE_DATA/.zone/index/embeddings.json` (rebuildable, listed in `.git/info/exclude` so it never shows as uncommitted; `iterPages` skips hidden dirs).
The cache is content-addressed (chunk text hash → vector) and per model: editing one section re-embeds only that section, a model change starts over.

**Chunking version**: the cache records how its pages were chunked (`chunking`; raise it when what a chunk's text is changes).
A cache of another chunking keeps its vectors (keyed by text, so still right where a text didn't change) but re-chunks every page;
otherwise a page not edited since kept chunk keys retrieval's sections no longer have, and its meaning hits were dropped.

### Freshness

A background thread (`start`) syncs at startup and whenever a search or page view pokes it; requests never wait for embedding, they query what is ready.
A deleted page is filtered out by the caller (`known=`) before the worker catches up.

### Scoring by meaning

Brute-force cosine similarity, a page scoring as its best chunk.
The vectors are rows per index generation and a query is one pass of dot products.
`scored` scores all sections and tasks for a query once per generation and keeps the last 32 queries' scores, so one search (pages, tasks, section links) scores each vector once.
Hits below a per-model floor (`thresholds`: search, related, tasks; measured on a test wiki; `semantic-min-score` in the settings file overrides the search one, `searchFloor`, also on a reload) are dropped,
since vector search otherwise "finds" something for any query.
Tasks have their own, higher floor (0.3 for the default model): short texts let nonsense score higher ("xqzt blorf" reached 0.27 against a task title).

### Hybrid ranking

`rankedDocs` merges the BM25 list and the semantic list (page hits and task hits, ordered by similarity together, `bySimilarity`) by reciprocal rank fusion
(`Fuse`, `rrfK = 60`, ranks only, so the two score scales never need normalising).
Then two rules, both in `hybrid`, in the browser's search, `search_wiki` and `search_tasks`' query alike:

| Query | Is | Its results |
|-------|----|-------------|
| At most 3 words (stopwords counted) | A lookup | Docs holding every word first, in BM25 order; the rest in fused order, those found by meaning alone only above the lookup floor |
| Longer | A question | Fused order, ties by meaning |

A page found only by meaning gets its most similar section as excerpt and link target (Results open the matching section), since it has no word to mark.
The fusion's constant, its depth and a weight for the meaning ranking were measured (`./run.sh eval`) and left as they are: other values gained nothing.
Zone's floor holds both rankings to what they find (Zone's own floor below, `tests/retrieval/README.md`).

#### Ties

**Ties go by meaning for a question, by BM25 for a lookup** (`hybrid`): each by the ranking that kind of query trusts.
Ties are common, since the fusion knows ranks alone:
two docs whose ranks are swapped between the rankings (first and second, second and third),
and the best doc by words alone against the best by meaning alone.
A question's words are in many docs in an order that says little, while meaning puts the answer first more often:
a page's sections that each hold one word of the question come by words in the order of their lengths, by meaning the ones that answer it ahead.

#### A short query is a lookup

A lookup (`lookupWords`: at most 3 words, stopwords counted): when docs hold every word of it, its results start with those, in BM25 order, and the rest follow in fused order.
Holding a word (`HaveAll`) is by its stem or as the start of a word, as search finds it;
a close typo, which search falls back on, doesn't count ("Ferien" isn't in a doc that says "serien"), so a query matched only by typos keeps the fused order.
Fusion goes by ranks alone, so the doc most similar to the query came as high as the doc saying it most,
and a lone word or name is about as similar to any text as nonsense is:
"seccomp" gave the one page with the word and then guesses,
"leave" a guess between every two pages with the word, and the pages that were also among the guesses on top.
A longer query is a question and keeps the fused order: any doc holds some of its words, and meaning finds the answer (docs with all of a question's words first lost answers on a test set, `./run.sh eval`).

#### The lookup floor

After a lookup's word matches, a doc found by meaning alone follows only when it is at least this similar (`lookupFloors`, `lookupFloor`: 0.4 for the default model, and never under the search floor).
No score tells a guess from a find in general: the best guess for a nonsense word can score higher than a question's answer, in absolute terms and against the query's other scores alike (a floor of mean plus some deviations was tried).
For one or two words it does well enough: a translation or synonym scores above the guesses ("Urlaub" finds the note saying "vacation").
A lookup of words the wiki doesn't have keeps every meaning hit over the search floor: they are all there is.

### Related pages

Each page's vector is the mean of its chunks; `related` lists the nearest above the related floor.
A page with little prose (under `relatedMinChars`, 100 letters and digits outside links: `proseChars` ignores `[[wikilinks]]`, link labels and bare URLs) neither gets a list nor appears in one.
That covers near-empty pages, whose vector is little more than their title, and hubs that are only lists of links (like the index), whose vector averages everything they link to; their neighbours were noise.
Search still finds them.

### Related to a text

`relatedToText` does the same for a page's text the index doesn't have yet (MCP write_page names the pages closest to a new page):
its sections are embedded at once, the ones the cache knows reused, and their mean compared like a page's.
Little prose or a failing model give nothing.

## The index on disk

The base of each index (`go/internal/textsearch/indexfile.go`),
pages and sections,
is a file,
`.zone/index/pages.idx` and `.zone/index/sections.idx`,
mapped into memory read-only (`syscall.Mmap`; where there is none, as on Windows, read into memory),
so it isn't in Go's heap and the kernel can drop its pages and read them again.
With each doc's tokens it holds its text squashed, so a phrase (`phraseFirst`, a quoted query's filter) is checked in the mapping (`TextHas`) without reading or folding anything; the kernel can drop those pages too.
Where no file can be written, the index stays in memory.
The files are rebuildable data in `.zone/`, so not in git (`.git/info/exclude`) nor in `storage-max`;
with the texts they hold about twice as many bytes as the markdown (the pages' text and the sections' again).

### Writing and reading it

A rebuild writes it straight from the docs (`WriteIndex`, holding only the postings meanwhile, coded as uvarints; the texts go to the file as they come) to a temporary file beside it, flushed and renamed into place, then maps it.
A mapped file is never written to, so a search still reading the old one goes on reading it;
the mapping is undone once nothing holds it, and nothing read from it points into it (terms, postings and keys are copies, texts are only searched).
The file has a version and a checksum, and its terms and postings are checked when it's opened:
one Zone can't read (another version, cut short, damaged, or not as the writer makes them) is built again, with a log line.

### A start without a rebuild

Each doc has a key in it, a hash of what its tokens are made from (`docKey`; raise `indexKeyVersion` when that changes):
on a start, the docs whose key the file has get their numbers back and the others are changes, as after an edit, so a start doesn't build the index (past `maxChanges` it does).
With each doc the file keeps what makes the doc again (`docMeta`: its slug, title, tags, mtime, which section it is),
so a start makes the docs of the pages whose mtime is still the one there from the file (`seedPageDocs`, `seedChunkDocs`) and reads none of them:
its first search only lists the pages (a page with `[[task:…]]` is read, its doc depends on the tasks too).
A doc whose file changed while a rebuild read it is written under no key, so a start doesn't take its tokens for the doc.

A rebuild with its base in a file merges from it (`MergeIndex`): the docs the file has bring their postings, lengths, texts and metas along, only the others are read and tokenized,
and the file is the very one a rebuild from scratch would write (a test compares their bytes);
docs in another order than the file's (a task moved in its list) keep their numbers, and equal scores go by the order now (`reordered`).

## Measuring retrieval

`zonewiki eval` (`go/eval.go`) measures how well `search_wiki`'s retrieval (`retrieve`, sections rather than pages) finds the answers in your own wiki,
so a change to chunking, ranking or the model can be judged by numbers rather than by feel.

### The questions

The questions are a JSON file you write, by default `_eval/retrieval.json` in the wiki (a `_` folder: not a page, not searched, versioned with the wiki): a list of `{"q": "question", "expect": [target, …], "note": "…"}`.
A target is a page (`people/alex`), a section (`people/alex#current-projects`, the anchor in the section's URL; `####` and deeper have none) or a task (`task:<id>`).
Phrase questions the way you'd ask them, not in the page's words, and mix kinds: paraphrases, exact ids and names, German and English, sections under generic headings.
Targets that name nothing in the wiki (a typo, a renamed page) are listed before the report.

### Running it

`./run.sh eval [FILE]` (a path in the wiki, extra args go to `zonewiki eval`, e.g. `--k 5`) builds the app image with semantic search as `ZONE_SEMANTIC_SEARCH` says, as `up` does, and mounts the wiki read-only.
Without a container: `ZONE_DATA=~/zonewiki go/zonewiki eval FILE` (hybrid only with onnxruntime's library on the loader's path).
Tasks are results as in `search_wiki` (the Python tool, until 2026-10, left them out by mistake: numbers from then are higher than now for the same ranking).

### The report

Per mode (`keyword`, BM25 alone; `hybrid`, fused with semantic search when `ZONE_SEMANTIC_SEARCH` is on): hit@1, hit@3, hit@k (default 8, `search_wiki`'s default limit) and MRR (mean of 1/rank, 0 when not found).
A question is found at the rank of the first result matching one of its targets; a page target matches any of its sections.
Then the questions not ranked first in every mode, with their rank per mode (`-` = not in the top k).

### The query log

With `query-log = yes` in the settings file (off by default), `search_wiki` appends each query to `.zone/queries.jsonl` (`logQuery`),
one JSON line: `at` (local time), `q`, `under` when given, and `results`, what it found as targets (`slug#anchor`, `task:<id>`, `resultTarget`).
In `.zone/`, so never in git, and readable by Zone's user alone (0600); past 1 MB (`queryLogMax`) the file moves to `queries.1.jsonl`, replacing the one before.
A write that fails is logged (`[queries] …`) and doesn't fail the search.
Only `search_wiki`: the browser's search runs as you type, so its queries are fragments.

### New questions from the log

`./run.sh eval FILE --new` (or `zonewiki eval --new FILE`) prints the logged queries FILE doesn't have yet (case and spacing ignored), newest first, each once,
as stubs `{"q", "expect": [], "results"}`, separated by commas to paste into FILE's list.
`results` (the first 5) is what the search found then: a hint for `expect`, not the answer.
A question whose `expect` is empty is skipped, and the report counts them, so stubs can sit in FILE until you fill them in.

### A floor

`--floor FILE` gives the least each mode must reach, a JSON file `{"keyword": {"hit@1": 44, "hit@3": 51, "hit@8": 56, "MRR": 0.70}, "hybrid": {…}}` (the hits as counts of questions).
After the report the command says what is under its floor and exits 1, or "No mode is under its floor."; what is above is listed too ("raise it …, so it is kept").
A mode with a floor that didn't run (hybrid without its model) is under it,
and so is a run with a target that names nothing: its question would count as not found, and lower the numbers unseen.

### Zone's own floor

`tests/retrieval/` (its `README.md`) holds a sample wiki (54 pages and 12 tasks of a made-up household and the bike workshop in its yard, a third of it German: projects, recipes, how-tos, meeting notes with the same headings, tickets with ids, people),
68 questions (in other words than the page's, by id and name, German and English, and across the two)
and `floors.json`, set to what was measured.
So a change to chunking, the tokens, the ranking or the model that loses an answer fails:
the keyword ranking in `./run.sh test` (`TestRetrievalHoldsItsFloor`, no model needed; it fails as well when the ranking got better than its floor, until the floor is raised),
both rankings in `./run.sh retrieval` (the app's image with the model; CI's `retrieval` job).
The floor says what search does, not what it should.

### Read-only

The semantic cache is read from the wiki (`.zone/index/embeddings.json`), sections changed since are embedded into a temporary copy, never written back (`semanticFromEnv`, the index without its worker).
The keyword indexes' files are read, never written: a rebuild stays in memory (`writeIndexFiles`).
