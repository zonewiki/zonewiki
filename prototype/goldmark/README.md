# Goldmark prototype

Can a Go port of Leaf render pages with [goldmark](https://github.com/yuin/goldmark)
instead of markdown-it-py? This prototype is `src/render.py`'s `render_md` up
to and including the markdown step in Go: the text pre-processing
(`preprocess.go`) and `md.render` (`render.go`), plus a harness that renders
a whole wiki with both and compares them.

The goal is **not** markdown-it's HTML byte for byte: the port may render
differently (a breaking change is fine), but nothing may break. So goldmark
renders what Leaf doesn't depend on its own way, and the prototype adds only
what a Leaf feature needs:

| Leaf feature | Why it needs a rule | Where |
|---|---|---|
| Clickable checkboxes | The Nth checkbox toggles the Nth line `mdtext._TASK_ITEM_RE` finds, so the same lines must get one. goldmark's tasklist also takes `- [ ]` alone and renders `disabled` | `taskTransformer` |
| Callouts (`> [!NOTE]` …) | goldmark has none; `style.css` styles `markdown-alert-*` | `alertTransformer`, `blockquote` |
| Heading ids | `[[Page#Section]]` links to `mdtext.section_anchor`'s slug, h1–h3, unique with `-1` | `slugify`, `uniqueSlug` |
| ✎ and ☑ N per section | the page view's section editing, task chips | `heading` |
| `![alt\|300](…)`, `img-block` | image width; a lone image may grow beyond the text | `leafTransformer` |
| External links in a new tab | `target="_blank" rel="noopener noreferrer"`, not for Leaf's own host | `external` |
| Task-date chips | `<!--t:… d:…-->` comments as chips | `taskDates` |
| `http://localhost:5000`, `http://nas:5000` as links | goldmark's linkify wants a dot in the host; a self-hosted wiki has such URLs | `urlRE` |

## Pre-processing

`Prepare` does what `render_md` does to a page before markdown, and gives
the same result as Python, byte for byte (it is input text, not markup):

- frontmatter off, its `tags`, `tldr` (plain, `>`, `|`) and `sources` parsed
  (`mdtext.parse_frontmatter`);
- fenced code and inline code spans masked, so `[[…]]` in code stays
  (`mdtext.mask_code`);
- `[[deck …]]` and `[[task:…]]` swapped for placeholders, their parameters
  and ids kept for the HTML that replaces them after rendering;
- `[[wikilinks]]` as links: missing pages, hidden ones in sharing mode,
  `#Section` anchors (`render.process_wikilinks`, `mdtext.section_anchor`).

Python's regexes don't all run on Go's RE2: the inline-code pattern needs
lookarounds and a backreference, so it is a scanner (`maskInlineCode`).
And Python's strings mean more by whitespace and line breaks than Go's
(`str.strip`, `splitlines`, `\s` and `\d` in patterns match non-ASCII
characters, a non-breaking space for one): `pyStrip`, `pyIsSpace`,
`splitLines` and `pySpace` are Python's versions.

Not ported: the HTML after markdown (deck embeds, task chips, the
frontmatter block, sources), which needs the deck's store.

## Running it

Needs Go ≥ 1.27 and the repo's `.venv`.

```bash
cd prototype/goldmark
go test ./...                       # Leaf's rules
go build -o /tmp/leafmd .
# Compare on a copy of the wiki (config.py creates _assets/ if missing):
cp -a ~/leaf /tmp/wiki-copy
../../.venv/bin/python compare.py /tmp/wiki-copy /tmp/leafmd /tmp/leafmd-out [EXTRA.md …]
```

Go gets each page's file as it is. `compare.py` checks three things per page:

1. **Pre-processing**: exactly Python's text, frontmatter, deck parameters
   and task refs (differences in `OUT_DIR/*.prep.json`).
2. **The checkbox contract**: goldmark's checkboxes = `mdtext`'s task lines.
3. **What a reader and Leaf's code see**: text, link targets, heading ids,
   checkboxes, images, the classes Leaf's CSS/JS select on. Markup that
   doesn't matter is ignored (`<p>` in tight lists, `<s>`/`<del>`, entities,
   attribute order, footnote ids). Differences go to `OUT_DIR/*.diff`.

`OUT_DIR` holds your pages' text: keep it out of the repo.

`compare.py --fuzz N WIKI GO_BINARY OUT_DIR` runs check 1 on N random texts
built from the syntax pre-processing looks for (wikilinks with sections and
labels, a hidden page, decks, task refs, code fences and spans, frontmatter)
and the characters Python and Go disagree on.

## Results (2026-10-26, goldmark 1.8.6)

- **A real wiki, 338 pages**: pre-processing is the same on every page, and
  so are the checkbox counts; 337 render
  the same. The one that differs has `[label](file:///…)` links: markdown-it
  refuses `file:` links and shows the markdown as text, goldmark makes them
  links (an improvement).
- **Leaf's own docs** (README, `docs/spec/`, AGENTS.md, CONTRIBUTING.md): all the same.
- **The 652 CommonMark 0.31.2 spec examples**: all the same, checkboxes too.
- `cases/features.md` (every feature Leaf uses, and edge cases) shows the
  behaviour changes below.
- **Fuzzing the pre-processing**: the same on all of 247,781 random texts
  (seeds 20000, 50001, 77777, 100003). On the way it found two differences,
  fixed: Python's `\s` matches a non-breaking space and Go's doesn't, so
  `[[deck\u00a0list=x]]` was a deck in Python only; and Python 3.13's
  `textwrap.dedent` counts a line of any whitespace (`\r`, U+2028) as blank.
- It also found a bug in Leaf: a page with the masking characters in its own
  text (U+E000, digits, U+E001) fails to render (`unmask_code` raises
  IndexError, the page view a 500). Go leaves such text alone.

## Behaviour changes (none breaks a page)

- Footnote markers read `1`, not `[1]`; ids are `fn:1`, not `fn1`.
- `~one~` is struck through, as in GFM (markdown-it: only `~~two~~`).
- Tight/loose lists and definition lists are decided by goldmark's rules.
- `[x](file:///…)` is a link (markdown-it: text).
- `[x](javascript:…)` is a link too (markdown-it: text). Not exploitable,
  since Leaf's page policy (`script-src 'self'`) doesn't run `javascript:` URLs
  and pages may contain raw HTML anyway, but a port could drop such hrefs
  as defence in depth.
- Not in goldmark: inline footnotes `^[…]` and `mailto:`/`xmpp:` autolinks
  (no page in the tested wiki used either).

## Left for the port

- `render_notes(…, inline=True)` and the frontmatter `sources` use
  `md.renderInline`; goldmark has no inline-only mode (render, then strip
  the one `<p>`).
- The HTML after markdown: deck embeds (`deck.build_deck_embed`), task
  chips (`render._task_ref_html`, the deck's store), the frontmatter block
  and sources.
- Two slug functions, as in Python: `slugify` (render.go, the heading ids
  markdown-it's anchors plugin gives) and `headingID` (preprocess.go,
  `mdtext.heading_id`, what `[[Page#Section]]` links to). A link only finds
  its heading where the two agree; the port could make them one.
