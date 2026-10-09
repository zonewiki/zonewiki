# Leaf

A local-first personal wiki with a built-in task deck. Your notes are plain
Markdown files in a folder you choose; Leaf renders them in the browser, lets
you edit them there or in any editor, searches them, and keeps every version
in git. No database, no build step, no cloud.

## Features

**Wiki**
- Markdown pages, rendered on the server: `[[wikilinks]]` (also to a section,
  `[[Page#Section]]`), backlinks, `[[toc]]`, callouts, sortable tables, image
  sizing, frontmatter tags and a one-line summary.
- Edit in the browser (split view with live preview, auto-save, a formatting
  bar, link and page completion, per-section editing) or in any text editor:
  changes on disk show up immediately.
- Paste or drop files and images into the editor to upload them.
- Folders for structure; renaming a page updates every link to it.
- Version history with diffs and one-click restore; deleted pages can be
  brought back. Every save is a git commit, so `git push` backs it all up.

**Search**
- Full-text search across pages and tasks (BM25 with English stemming,
  prefixes, typo tolerance and accent folding), opening the matching section
  with the words highlighted.
- Query syntax: `"exact phrase"`, `-word`, `#tag`, `edited:2026-09`,
  `after:` / `before:`.
- Optional semantic search with a local embedding model, fused with the
  keyword ranking, plus "Related pages". Nothing leaves your machine.
- A quick switcher (Ctrl+P) with fuzzy matching and a preview pane.

**Deck (tasks)**
- A task list with start dates and deadlines kept apart: **Open** (all, in
  your own order), **Plan** (a day board: drag tasks onto days), **Focus**
  (what you're working on today).
- Recurring tasks, "keep" tasks for lists you reuse (shopping), comments,
  notes with checklists, tags, and separate lists declared on a page.
- Quick-add shorthand: `Call Bob tomorrow #work due:fri`.
- Embed a deck in any page (`[[deck tag=work view=plan]]`), show a single
  task inline (`[[task:…]]`), or turn lines of meeting notes into tasks.
- Compact or full cards, keyboard-driven, with touch gestures on phones
  (swipe to plan, long-press to drag).

**Everyday use**
- Scratch notes for quick capture, moved into the wiki or appended to a page
  later.
- Keyboard shortcuts for everything (`?` lists them), presentation mode from
  a page's headings.
- Sharing mode: hides links to other pages and pages marked not for sharing
  while you share your screen.
- Works on phones (a responsive layout with a tab bar, installable on the
  home screen) and in light and dark mode.

**For AI assistants**
- An [MCP](https://modelcontextprotocol.io) server at `/mcp`: any MCP client can search your notes and tasks and read
  pages (read-only), with links back into Leaf.

## Quick start

Leaf runs in a container with [Podman](https://podman.io):

```bash
mkdir -p ~/leaf                       # your wiki folder (Leaf won't create it)
echo 'WIKI_DATA=~/leaf' > .env
./run.sh up                           # build and start
```

Open <http://localhost:5000>. `./run.sh down` stops it, `./run.sh logs`
follows the log.

Without a container:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt
WIKI_DATA=~/leaf python src/server.py
```

## Configuration

Settings go in `.env`, all optional except `WIKI_DATA`:

| Setting | Does |
|---------|------|
| `WIKI_DATA` | The wiki folder (default `~/leaf`) |
| `ALLOW_REMOTE=1` | Listen on the LAN (e.g. for your phone); by default only localhost |
| `LEAF_PASSWORD` | A login gate; `SECRET_KEY` keeps sessions across restarts |
| `GIT_REMOTE` | A remote for the wiki's git repository |
| `LEAF_EMBEDDINGS=fastembed` | Semantic search with a local model (adds about 1 GB to the image) |
| `LEAF_MCP_TOKEN` | A bearer token for `/mcp` |
| `LEAF_BASE_URL` | Leaf's address as your browser knows it, for links handed to AI clients |
| `LEAF_NOSHARE` | Pages hidden in sharing mode, e.g. `#noshare,people/` |
| `TZ` | The timezone for "today" (default: the host's) |

Connect an MCP client:

Register `http://localhost:5000/mcp` with your MCP client (HTTP transport).

## Your data

Everything is files in `WIKI_DATA`: pages are `.md` files (in folders if you
like), uploads are in `_assets/`, tasks in `_deck/` as JSON. The folder is a
git repository. Stop using Leaf and your notes are still just Markdown.

## Development

```bash
./run.sh test     # unit and API tests (pytest) in the app image
./run.sh e2e      # browser tests (Playwright) and the deck logic tests (Node)
```

Python with Flask and markdown-it-py, plain ES modules in the browser, and
one hand-written stylesheet; no bundler, no npm. `AGENTS.md` describes how
the project is built, and `docs/spec/` describes every feature in detail.

## License

[AGPL-3.0-or-later](LICENSE). The vendored [highlight.js](https://highlightjs.org) in
`src/public/vendor/highlight/` is under its own BSD-3-Clause license.
