# Goldmark prototype

Can a Go port of Leaf render pages with [goldmark](https://github.com/yuin/goldmark)
instead of markdown-it-py? This prototype is the markdown step of
`src/render.py` (`md.render`) in Go, plus a harness that renders a whole wiki
with both and compares them.

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

Leaf's text pre-processing (frontmatter, code masking, `[[wikilinks]]`,
`[[deck]]`, `[[task:…]]`) is string work, ported later as it is; here
`compare.py` runs Leaf's Python for it and feeds both renderers the result.

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

`compare.py` checks two things per page:

1. **The checkbox contract**: goldmark's checkboxes = `mdtext`'s task lines.
2. **What a reader and Leaf's code see**: text, link targets, heading ids,
   checkboxes, images, the classes Leaf's CSS/JS select on. Markup that
   doesn't matter is ignored (`<p>` in tight lists, `<s>`/`<del>`, entities,
   attribute order, footnote ids). Differences go to `OUT_DIR/*.diff`, which
   holds your pages' text: keep it out of the repo.

## Results (2026-10-26, goldmark 1.8.6)

- **A real wiki, 338 pages**: checkbox counts agree on every page; 337 render
  the same. The one that differs has `[label](file:///…)` links: markdown-it
  refuses `file:` links and shows the markdown as text, goldmark makes them
  links (an improvement).
- **Leaf's own docs** (README, `docs/spec/`, AGENTS.md, CONTRIBUTING.md): all the same.
- **The 652 CommonMark 0.31.2 spec examples**: all the same, checkboxes too.
- `cases/features.md` (every feature Leaf uses, and edge cases) shows the
  behaviour changes below.

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
- The pre-processing in Go: `mask_code`, wikilinks, `[[deck]]`, `[[task:…]]`
  (`mdtext.py`, `render.py`, `deck.py`), and the HTML after it.
- `mdtext.section_anchor` and `slugify` here must stay the same function;
  in the port both live in Go.
