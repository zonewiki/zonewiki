# Search

Part of Zone's spec (see `AGENTS.md` for the index).

## Search

- **Ranking is BM25**, implemented in Go (`go/internal/textsearch`, a package of its own without Zone's state; no database; `golang.org/x/text` for case folding and accents).
  Pages and tasks are scored in **one combined index** so their relevance scores are directly comparable.
- `GET /api/search?q=...` collects every page **and** all task titles/descriptions as documents, ranks them, and returns JSON: `[{ slug, title, excerpt, href, meta, type? }]`.
  `meta` (`resultMeta`) is the small line under a result's title in the modal and on `/search`, telling alike titles apart: a page's folder and last edit (`conversations/2026-07-08_alex-sam-debrief · edited 2026-10-02`), a task's list and status (`Tasks › zoo · done 2026-05-01`).
  `href` is the server-computed destination URL for the result; task results have `type: "task"` and `task_id` fields.
- **Multi-word queries** score each term independently (rarer terms weigh more via IDF); the whole phrase no longer has to appear verbatim.
- **Exact phrase first** (`Phrase`, `phraseFirst`): a document containing the query's words as a phrase (case, accents and spacing ignored, stopwords included) moves to the front,
  in BM25 order,
  even one the ranking had cut off: "alex again" puts the note saying "Alex again" above notes with both words apart.
  Browser search and `search_wiki` alike; the result opens the section with the phrase (`bestSections`).
  The squashed text is checked in the index file (`TextHas`, below), or for a doc not in it, read again and kept for a while (`docTexts`).
- **Words in any script**: `tokenize` splits on runs of letters/digits (`[^\W_]+`), so "Müller" is one word, and folds case and accents (`fold`: ü→u, é→e, ß→ss), so `muller`, `MÜLLER` and `Müller` match each other.
- **Link targets are not indexed** (`stripLinkTargets`, pages and tasks): the `(url)` of `[text](url)`/`![alt](url)` and the URL of `[id]: url` definitions are dropped before tokenizing and excerpting, the link text stays.
  Otherwise every page with a `/page/…` link matched `page`.
  Bare URLs in the text stay searchable.
- **Stemming** (`stem`) collapses inflections (run/runs/running, database/databases) so word variations match.
- **Partial words** match via prefix expansion; **typos** match via an edit-distance fuzzy fallback (`difflib`) only when a term has no exact/prefix hit.
- **Memory**: a page's or section's doc keeps only what all of its page's docs share (slug, title, tags, mtime, its file) and, for a section, which of the page's sections it is: not its text, its tokens, nor a section's heading, path, anchor or semantic key.
  Those are read from its file again when asked for (`body`, `tokens`, `section`: a section's from its page split again),
  which a search does only for the docs it shows or checks,
  through `docTexts`,
  which keeps the texts and splits read again (and the texts squashed of docs not in an index file) up to 16 MB (`docTextsMax`) and then starts over.
  A big wiki has hundreds of thousands of sections, and their docs were most of its memory: a doc is 136 bytes (`TestSearchDocStaysSmall`), where it was about 430 with what it held.
  A task's own fields (its text, status, year, last activity) hang off its doc (`taskDocInfo`), so a page's or section's doesn't carry them.
  `search_wiki`'s fusion with semantic search matches a section by its key (`semKey`), worked out only for the pages its semantic hits are on (each hit names its page).
  So excerpts, highlights, section links and `search_wiki`'s passages read the files of the docs they show; a page gone since is left out of the results, a page changed since gives its text now (the next search makes its doc anew).
  Tasks keep their text.
  Building an index asks for each document's tokens in turn and keeps only the postings, a `-word` filter asks the postings (`DocsWith`), and leaving out a changed page takes one pass over them (`With`).
  What that costs: a page edited or private, added to an index for a viewer, is read and tokenized each time that viewer's index is made again, and a rebuild past `maxChanges` reads, splits and tokenizes everything, its tokens and its text squashed from one fold (`indexed`, `TokensAndSquash`).
- **The index on disk** (`go/internal/textsearch/indexfile.go`): the base of each index,
  pages and sections,
  is a file,
  `.zone/index/pages.idx` and `.zone/index/sections.idx`,
  mapped into memory read-only (`syscall.Mmap`; where there is none, as on Windows, read into memory),
  so it isn't in Go's heap and the kernel can drop its pages and read them again.
  With each doc's tokens it holds its text squashed, so a phrase (`phraseFirst`, a quoted query's filter) is checked in the mapping (`TextHas`) without reading or folding anything; the kernel can drop those pages too.
  A rebuild writes it straight from the docs (`WriteIndex`, holding only the postings meanwhile, coded as uvarints; the texts go to the file as they come) to a temporary file beside it, flushed and renamed into place, then maps it.
  A mapped file is never written to, so a search still reading the old one goes on reading it; the mapping is undone once nothing holds it, and nothing read from it points into it (terms, postings and keys are copies, texts are only searched).
  The file has a version and a checksum, and its terms and postings are checked when it's opened: one Zone can't read (another version, cut short, damaged, or not as the writer makes them) is built again, with a log line.
  Each doc has a key in it, a hash of what its tokens are made from (`docKey`; raise `indexKeyVersion` when that changes): on a start, the docs whose key the file has get their numbers back and the others are changes, as after an edit, so a start doesn't build the index (past `maxChanges` it does).
  With each doc the file keeps what makes the doc again (`docMeta`: its slug, title, tags, mtime, which section it is),
  so a start makes the docs of the pages whose mtime is still the one there from the file (`seedPageDocs`, `seedChunkDocs`) and reads none of them: its first search only lists the pages (a page with `[[task:…]]` is read, its doc depends on the tasks too).
  A rebuild with its base in a file merges from it (`MergeIndex`): the docs the file has bring their postings, lengths, texts and metas along, only the others are read and tokenized, and the file is the very one a rebuild from scratch would write (a test compares their bytes);
  docs in another order than the file's (a task moved in its list) keep their numbers, and equal scores go by the order now (`reordered`).
  A doc whose file changed while a rebuild read it is written under no key, so a start doesn't take its tokens for the doc.
  Where no file can be written, the index stays in memory.
  The files are rebuildable data in `.zone/`, so not in git (`.git/info/exclude`) nor in `storage-max`; with the texts they hold about twice as many bytes as the markdown (the pages' text and the sections' again).
- **Title boost**: title tokens are folded into the document token bag `titleWeight` (3) extra times, so title matches rank higher.
- **File and folder names**: a page's slug words are in its token bag once (pages and sections alike), so `robin` finds `messages/2026-06-18_roof-update-alex-robin` even when the text never says it; once, like one mention in the text, so a file name never outranks what pages say.
- Excerpt (`Excerpt`) shows a window around the first match, HTML-escaped, with every match in it marked (`<mark>`).
  Matches are whole words found the way the index finds them (`matchSpans`: same stem, prefix, else fuzzy), so `running` marks "runs" and `art` never marks the inside of "restart".
  A multi-word query found as a phrase (case and accents ignored) is marked as one span instead.
- **Performance**: page documents are made once and cached in `searchDocCache` keyed by slug, invalidated per-file on `mtime` change (so on-disk edits stay reflected).
  The BM25 index itself is cached too (`searchIndex()`, `indexCache`), keyed by the identities of the cached docs.
  A change to a page or task does not build it again: the cache keeps the index made at the last rebuild,
  leaves out the docs that are gone from it and adds the new ones (`Index.With`),
  searched with the statistics of the docs that are left,
  so hits,
  scores and the order of equal scores are exactly those of an index built over them (tests compare the two on random edits);
  a task that didn't change keeps its doc, so only what changed counts.
  Only when the changes pass 2% of the docs (`maxChanges`, at least 32) is it built again, so an edit costs a few percent of a rebuild on average (5,000 pages: the first search after an edit 75 ms instead of 570 ms; 20,000: 0.3 s instead of 2.3 s);
  it keeps an inverted index (`postings`: term → docs), so a search scores only the docs containing its terms, ties in document order.
  Prefix expansion bisects the sorted vocabulary; typo matching (`closeMatches`) skips a candidate whose `real_quick_ratio` or `quick_ratio`, upper bounds of the difflib ratio, are under the cutoff, as Python does.
  A search that wants only some documents (phrase hits, the sections of the pages shown, the query's filters) asks `SearchWhere`, which scores only those and, for a short limit, keeps just the best few instead of sorting every hit: the same hits in the same order.
  Matching words in a text for excerpts, highlights and section links (`matchSpans`) caches each text's split into words and stems (`analyze`, with the distinct stems sorted for prefix bisection and grouped by length for fuzzy candidates) and each word's fold and stem;
  that was most of a search's time.
  `iterPages` walks the wiki skipping `_*` and hidden folders (`SkipDir`), instead of walking `.git`'s objects;
  a search lists the pages and their files' mtimes once (`rq.pageStats`), for both its indexes and its results' task lists, instead of walking and checking every file three times (at 17,350 pages that was most of a search's time).
  A search takes a few tens of ms; a test wiki of 330 pages and 3,600 sections went from ~150 to ~25 ms.
  Task docs come from `taskSearchDocs()`: title, description, comments and tags (as `#tag` words in the text, and as a `tags` list for the `#tag` filter).
- **Results open the matching section** (`bestSections`, `doSearch`): a page result's `href` gets the anchor of the section the query matched, and its excerpt comes from that section, so the excerpt shows what the link jumps to.
  The section is one whose own text has the query as a phrase, else the best BM25-scored one whose own text (heading and body, not the page title) has a query word (`matches`), else, for a page found by meaning, its most similar section.
  A page matched only by its title gets no anchor (every section would match equally), nor does a match before the first heading or under a `####`+ heading, which has no id (`sectionAnchorOf`: h1–h3 only).
  Sections come from the same split as retrieval (`pageChunks`).
- **Highlights on the opened page** (`MatchedWords`, `initSearchHighlights` in `js/search.js`): a page result's `href` carries the words that matched as `?hl=…` (before the `#anchor`),
  as written in that page,
  so stems,
  typos and phrases are already resolved ("running" → `hl=runs`, a phrase as one `hl`);
  at most 10, tasks get none.
  The page marks every whole-word occurrence (`mark.search-hl`; phrase words may be separated by any whitespace), skipping `<pre>` code (highlight.js rewrites it) and task list embeds (`.tasks-app`).
  A bar (`.search-hl-bar`, bottom centre, top on phones where the bottom edge holds the tab bar) shows the terms and "2 of 5" with ↑ ↓ ×;
  **n**/**N** step (in the capture phase, so a task list embed's `n` doesn't also open a new task), **Esc** or × unmarks and drops `hl` from the URL (`history.replaceState`).
  It starts at the first match inside the section the `#anchor` opened.
  Listed in the `?` help.
- `GET /search?q=...` renders a full results page for direct links / no-JS fallback.
- Search is case-insensitive.
  Returns up to 15 results (pages + tasks combined; `searchLimit`), or `&limit=N` (1–100, `searchMaxLimit`).
  The search modal asks for 16: when the 16th comes back it shows 15 and an **All results →** link (`.search-more`) to `/search?q=…` (plus `&unlinked=1` when that box is checked), which lists up to 50 (`searchPageLimit`) under a count ("17 results", "The first 50 results").
  With semantic search each ranking goes as deep as the list must be long (`rankedDocs`, `depth`).
- **Semantic search (opt-in, `go/embed.go`)**: set `ZONE_SEMANTIC_SEARCH=on` in `.env` (a local ONNX model, `jinaai/jina-embeddings-v2-base-de`, German + English; `off`, or unset, turns it off; any other value is logged and keeps it off).
  It is the only model: `onnxEmbedder` runs it on onnxruntime (`go/embed_onnx.go`, through onnxruntime_go, which loads `libonnxruntime.so` when the model is first used; a binary built without cgo has no semantic search) with its tokenizer in Go (`go/internal/tokenizer`, the model's `tokenizer.json`),
  with its recipe (at most 512 tokens, the token vectors averaged where the attention mask is set, normalised, no query or passage prefixes),
  and another model may need another recipe,
  so there is no setting for one.
  Until 2026-10 the Python Zone ran it, with fastembed before that; the vectors are the same bit for bit, and the cache keeps its name `fastembed:<model>`, so it stays valid.
  (An Ollama backend existed and was removed as unused; it is in the git history.) Unset, Zone is keyword-only and nothing below runs.
  Set,
  `run.sh` passes the setting as a build arg and the `Containerfile`'s `semantic` stage fetches onnxruntime's library and the model's files at a fixed revision,
  each checked against its SHA-256 (the same as `modelRevision`, `modelFiles`),
  into `ZONE_MODEL_DIR=/opt/zonewiki-model`: the image grows by about 0.7 GB,
  and the first search doesn't wait for a download.
  Without the container the model goes to `~/.cache/zonewiki/`, downloaded on first use (`downloadModel`, the same hashes), and onnxruntime's library must be on the loader's path (`LD_LIBRARY_PATH`).
  That stage copies nothing from the repo, so its layer is rebuilt only when the model or onnxruntime changes, not on every code change.
  A missing library, a bad setting or a failing model is logged (`[semantic] …`) and search falls back to keywords.
  - **Chunks**: each page is split at its headings, as the markdown parser finds them (`chunkSections`, `headingsIn`: setext and indented ones too, not those in code, comments, quotes or lists), long sections further at paragraphs (`maxChunkChars`).
    Each chunk is embedded as `title\nheading path\nbody`, the path being the heading after the ones it sits under (`Problems › Windows`, `chunkText`): a section headed "Windows" or "Next steps" says little on its own.
    The BM25 section index (`pageChunks`) keeps the section's own heading (with the path in its tokens too, every parent word repeated across sibling sections cost keyword ranking on a test set: `./run.sh eval`), and `search_wiki` shows the path (`Page › Problems › Windows`);
    the anchor stays the section's own heading's;
    `semanticText` drops the frontmatter block, the H1 and the `[[tasks: …]]` directives (markup, not content: pages holding only a task list embed came out as each other's closest match) and puts tldr and tags first.
    **Tasks** are embedded too, one vector each, from their search text (title, description, `#tags`; `semanticTasks`, the index's tasks function, started in `main` once the task files are set up), keyed by a hash of it in the same cache (`"tasks"`: id → hash), so only new or edited tasks are embedded.
  - **Storage**: no vector database.
    Vectors are in memory and cached in `$ZONE_DATA/.zone/index/embeddings.json` (rebuildable, listed in `.git/info/exclude` so it never shows as uncommitted; `iterPages` skips hidden dirs).
    The cache is content-addressed (chunk text hash → vector) and per model: editing one section re-embeds only that section, a model change starts over.
  - **Chunking version**: the cache records how its pages were chunked (`chunking`; raise it when what a chunk's text is changes).
    A cache of another chunking keeps its vectors (keyed by text, so still right where a text didn't change) but re-chunks every page; otherwise a page not edited since kept chunk keys retrieval's sections no longer have, and its meaning hits were dropped.
  - **Freshness**: a background thread (`start`) syncs at startup and whenever a search or page view pokes it; requests never wait for embedding, they query what is ready.
    A deleted page is filtered out by the caller (`known=`) before the worker catches up.
  - **Query**: brute-force cosine similarity, a page scoring as its best chunk.
    The vectors are rows per index generation and a query is one pass of dot products (a few ms for 3,600 sections).
    `scored` scores all sections and tasks for a query once per generation and keeps the last 32 queries' scores, so one search (pages, tasks, section links) scores each vector once.
    Hits below a per-model floor (`thresholds`: search, related, tasks; measured on a test wiki; `semantic-min-score` in the settings file overrides the search one, `searchFloor`, also on a reload) are dropped, since vector search otherwise "finds" something for any query.
    Tasks have their own, higher floor (0.3 for the default model): short texts let nonsense score higher ("xqzt blorf" reached 0.27 against a task title).
  - **Hybrid ranking**: `rankedDocs` merges the BM25 list and the semantic list (page hits and task hits, ordered by similarity together, `bySimilarity`) by reciprocal rank fusion (`Fuse`, `RRF_K = 60`, ranks only, so the two score scales never need normalising; ties go to BM25).
    A page found only by meaning gets its most similar section as excerpt and link target (see "Results open the matching section"), since it has no word to mark.
  - **A short query is a lookup** (`wordsFirst`, `lookupWords`: at most 3 words, stopwords counted): its results start with the BM25 docs holding every word of it (`HaveAll`: found as search finds a word, by stem, prefix, else typo), in BM25 order, and the rest follow in fused order.
    Fusion goes by ranks alone, so the doc most similar to the query came as high as the doc saying it most, and a lone word or name is about as similar to any text as nonsense is (on a wiki of 350 pages "seccomp" reached 0.35, "xqzt blorf" 0.32, the floor is 0.25; a question's answer 0.4 to 0.8):
    "seccomp" gave the one page with the word and then guesses, "leave" a guess between every two pages with the word, and the pages that were also among the guesses on top.
    A longer query is a question and keeps the fused order: any doc holds some of its words, and meaning finds the answer (docs with all of a question's words first lost answers on a test set, `./run.sh eval`).
    In the browser's search, `search_wiki` and `search_tasks`' query alike.
  - **Related pages**: each page's vector is the mean of its chunks; `related` lists the nearest above the related floor.
    A page with little prose (under `relatedMinChars`, 100 letters and digits outside links: `proseChars` ignores `[[wikilinks]]`, link labels and bare URLs) neither gets a list nor appears in one.
    That covers near-empty pages, whose vector is little more than their title, and hubs that are only lists of links (like the index), whose vector averages everything they link to; their neighbours were noise.
    Search still finds them.
  - **Related to a text** (`relatedToText`): the same for a page's text the index doesn't have yet (MCP write_page names the pages closest to a new page): its sections are embedded at once, the ones the cache knows reused, and their mean compared like a page's.
    Little prose or a failing model give nothing.
- **Selecting a task result** (search modal or `/search` page) opens `/tasks?task=<id>&view=<open|closed>` (plus `&list=<name>` for a task in a separate list, so it opens that list) (plus `&year=YYYY` for a task in an earlier year's archive) on the tab matching the task's state,
  with that task selected,
  scrolled into view and briefly highlighted (`.task-flash`) — rather than the bare `_tasks` storage slug.
  The link is by id, not title: titles can repeat or contain a `#word` the filter field would read as a tag.
  Built by `taskHref` (the search modal uses the result's `href`).
  A page's "Linked tasks" entries use the same link.

- **Query syntax** (`Query`, the same in `/api/search`, `/search`, `search_wiki` and `search_tasks`' query; listed in the `?` help modal): `"exact phrase"` (case, accents and spacing ignored),
  `-word` (its stem must not occur; `-dockers` excludes "docker"),
  `-"phrase"`,
  `#tag` (pages by frontmatter `tags:`, tasks by their tags, case ignored) and `-#tag`,
  and **date filters**: `edited:2026-09` (a year, month or day, as a prefix),
  `after:2026-07-01` (on or after),
  `before:2026-08` (strictly before; partial dates compare as prefixes, so this is July and earlier),
  negated as `-edited:` (not then),
  `-after:` (= `before:`),
  `-before:` (= `after:`).
  A page's date is its last edit, a task's its last activity (`lastActivity`: the latest of completion, history events, comments and creation; `_doc_date`); undated docs fail a date filter (`SearchDoc.date`).
  A query of only date filters lists everything passing, newest first (`rankedDocs`), e.g. `edited:2026-09`.
  An unclosed quote, a lone `-` or `#`, or an invalid date (`edited:soon`) stay text.
  `Query.Text` (words and phrases, no operators) ranks, both by keyword and by meaning, and is what excerpts mark; the rest are filters (`Accepts`, applied by `Accepts` before ranking, so meaning hits obey them too).
  A query of only tags ranks by the tag words (`RankingText`); one of only exclusions finds nothing.
  Docs carry lowercased `Tags` for this (`mdtext.PageTags`, `taskSearchDocs`).
  In the search modal the instant title filter fuzzy-matches only the query's words (`queryWords` in `js/search.js`, mirroring `Query`: quotes dropped, `-…`, `#tag` and date-filter parts left out, so a query of only `#tag` shows no title hits), while Content matches get the raw query.
- **Stopwords** (`stopwords`, `queryWords`): English and German function words ("the", "is", "in", "und", "für") are dropped from the **query**, never from the index, unless that leaves nothing ("what is this" still searches).
  In a small wiki they aren't in every page, so BM25 gave them weight: a question phrased as a sentence ranked pages by its filler words.
  Words that are also nouns or names (may, will, it, man) are not in the list.
  Excerpts don't mark them either; a phrase match still marks the whole phrase.

## Measuring retrieval

`zonewiki eval` (`go/eval.go`) measures how well `search_wiki`'s retrieval (`retrieve`, sections rather than pages) finds the answers in your own wiki, so a change to chunking, ranking or the model can be judged by numbers rather than by feel.

- **The questions** are a JSON file you write, by default `_eval/retrieval.json` in the wiki (a `_` folder: not a page, not searched, versioned with the wiki): a list of `{"q": "question", "expect": [target, …], "note": "…"}`.
  A target is a page (`people/alex`), a section (`people/alex#current-projects`, the anchor in the section's URL; `####` and deeper have none) or a task (`task:<id>`).
  Phrase questions the way you'd ask them, not in the page's words, and mix kinds: paraphrases, exact ids and names, German and English, sections under generic headings.
  Targets that name nothing in the wiki (a typo, a renamed page) are listed before the report.
  The search dialog shows whether semantic search is on (Search mode in `navigation.md`).
- **Run** `./run.sh eval [FILE]` (a path in the wiki, extra args go to `zonewiki eval`, e.g. `--k 5`): it builds the app image with semantic search as `ZONE_SEMANTIC_SEARCH` says, as `up` does, and mounts the wiki read-only.
  Without a container: `ZONE_DATA=~/zonewiki go/zonewiki eval FILE` (hybrid only with onnxruntime's library on the loader's path).
  Tasks are results as in `search_wiki` (the Python tool, until 2026-10, left them out by mistake: numbers from then are higher than now for the same ranking).
- **The report**: per mode (`keyword`, BM25 alone; `hybrid`, fused with semantic search when `ZONE_SEMANTIC_SEARCH` is on), hit@1, hit@3, hit@k (default 8, `search_wiki`'s default limit) and MRR (mean of 1/rank, 0 when not found).
  A question is found at the rank of the first result matching one of its targets; a page target matches any of its sections.
  Then the questions not ranked first in every mode, with their rank per mode (`-` = not in the top k).
- **The query log** (`query-log = yes` in the settings file, off by default): `search_wiki` appends each query to `.zone/queries.jsonl` (`logQuery`), one JSON line: `at` (local time), `q`, `under` when given, and `results`, what it found as targets (`slug#anchor`, `task:<id>`, `resultTarget`).
  In `.zone/`, so never in git; past 1 MB (`queryLogMax`) the file moves to `queries.1.jsonl`, replacing the one before.
  A write that fails is logged (`[queries] …`) and doesn't fail the search.
  Only `search_wiki`: the browser's search runs as you type, so its queries are fragments.
- **New questions from the log**: `./run.sh eval FILE --new` (or `zonewiki eval --new FILE`) prints the logged queries FILE doesn't have yet (case and spacing ignored), newest first, each once, as stubs `{"q", "expect": [], "results"}`, separated by commas to paste into FILE's list.
  `results` (the first 5) is what the search found then: a hint for `expect`, not the answer.
  A question whose `expect` is empty is skipped, and the report counts them, so stubs can sit in FILE until you fill them in.
- **Read-only**: the semantic cache is read from the wiki (`.zone/index/embeddings.json`), sections changed since are embedded into a temporary copy, never written back (`semanticFromEnv`, the index without its worker).
  The keyword indexes' files are read, never written: a rebuild stays in memory (`writeIndexFiles`).

