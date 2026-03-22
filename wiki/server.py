import html
import re
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request
from markdown_it import MarkdownIt

app = Flask(__name__, static_folder="public", static_url_path="/public")

PAGES_DIR = Path(__file__).parent / "pages"
PAGES_DIR.mkdir(exist_ok=True)

md = MarkdownIt(options_update={"html": True}).enable("table")


# ── Helpers ───────────────────────────────────────────────────────────────────


def to_slug(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def safe_path(slug: str) -> Path | None:
    """Return resolved path only if it's safely inside PAGES_DIR."""
    pages_root = PAGES_DIR.resolve()
    target = (PAGES_DIR / f"{slug}.md").resolve()
    if target.parent == pages_root:
        return target
    return None


def all_slugs() -> list[str]:
    return sorted(p.stem for p in PAGES_DIR.glob("*.md"))


def page_title(content: str, slug: str) -> str:
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else slug.replace("-", " ").title()


def process_wikilinks(text: str) -> str:
    slugs = set(all_slugs())

    def replace(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            page_name, label = inner.split("|", 1)
        else:
            page_name = label = inner
        slug = to_slug(page_name)
        label = html.escape(label.strip())
        css = "wikilink" if slug in slugs else "wikilink wikilink-missing"
        return f'<a href="/page/{slug}" class="{css}">{label}</a>'

    return re.sub(r"\[\[([^\[\]]+)\]\]", replace, text)


def render_md(text: str) -> str:
    return md.render(process_wikilinks(text))


def strip_h1(content: str) -> str:
    """Remove the first H1 line so it isn't duplicated below the page header."""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            if re.match(r"^#\s+", line):
                del lines[i]
            break
    return "\n".join(lines)


def update_references(old_slug: str, new_slug: str) -> None:
    """Rewrite [[wikilinks]] across all pages after a rename."""
    for md_file in PAGES_DIR.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")

        def replace(m: re.Match) -> str:
            inner = m.group(1)
            if "|" in inner:
                page_name, label = inner.split("|", 1)
                if to_slug(page_name) == old_slug:
                    return f"[[{new_slug}|{label}]]"
            else:
                if to_slug(inner) == old_slug:
                    return f"[[{new_slug}]]"
            return m.group(0)

        new_content = re.sub(r"\[\[([^\[\]]+)\]\]", replace, content)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")


def shell(title: str, body: str, *, slug: str = "", editor: bool = False) -> str:
    edit_btn = f'<a href="/edit/{slug}" class="btn">Edit</a>' if slug and not editor else ""
    body_class = ' class="editor-page"' if editor else ""
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} — Wiki</title>
  <link rel="stylesheet" href="/public/style.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" media="(prefers-color-scheme: light)">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css" media="(prefers-color-scheme: dark)">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js" defer></script>
  <script src="/public/wiki.js" defer></script>
</head>
<body{body_class}>
  <nav>
    <a href="/" class="site-title">Wiki</a>
    <div class="search-wrap">
      <input type="search" id="search-input" placeholder="Search…" autocomplete="off">
      <div id="search-dropdown" class="search-dropdown hidden"></div>
    </div>
    <div class="nav-actions">
      {edit_btn}
      <a href="/pages" class="btn">Pages</a>
      <a href="/new" class="btn btn-primary">New Page</a>
    </div>
  </nav>
  <main>
    {body}
  </main>
</body>
</html>"""


# ── Page routes ───────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return redirect("/page/index")


@app.get("/page/<slug>")
def view_page(slug):
    path = safe_path(slug)
    if path is None or not path.exists():
        body = f"""<div class="not-found">
  <h1>Page not found</h1>
  <p>The page <strong>{html.escape(slug)}</strong> doesn't exist yet.</p>
  <a href="/edit/{html.escape(slug)}" class="btn btn-primary">Create it</a>
</div>"""
        return shell("Not Found", body), 404

    content = path.read_text(encoding="utf-8")
    title = page_title(content, slug)
    body = f"""<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <a href="/edit/{slug}" class="btn">Edit</a>
</header>
<article class="page-content">
  {render_md(strip_h1(content))}
</article>"""
    return shell(title, body)


@app.get("/edit/<slug>")
def edit_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    title = page_title(content, slug) if content else slug.replace("-", " ").title()
    safe_content = html.escape(content)
    safe_slug = html.escape(slug)
    safe_title = html.escape(title)
    can_rename = slug != "index"
    rename_btn = '<button type="button" class="btn" id="rename-toggle">Rename…</button>' if can_rename else ""
    rename_bar = f"""<div class="rename-bar hidden" id="rename-bar">
    <form method="post" action="/rename/{safe_slug}" class="rename-form">
      <label>New name</label>
      <input type="text" name="new_name" id="rename-input" value="{safe_title}" autocomplete="off">
      <button type="submit" class="btn btn-primary">Rename</button>
      <button type="button" class="btn" id="rename-cancel">Cancel</button>
    </form>
  </div>""" if can_rename else ""
    body = f"""<div class="editor-wrap">
  <div class="editor-pane">
    <div class="editor-toolbar">
      <span class="slug-label">/page/{safe_slug}</span>
      <button type="submit" form="editor-form" class="btn btn-primary">Save</button>
      <a href="/page/{safe_slug}" class="btn">Cancel</a>
      {rename_btn}
    </div>
    {rename_bar}
    <form id="editor-form" method="post" action="/edit/{safe_slug}">
      <textarea id="editor-textarea" name="content"
                spellcheck="false" autocorrect="off" autocapitalize="off">{safe_content}</textarea>
    </form>
  </div>
  <div class="preview-pane">
    <p class="pane-label">Preview</p>
    <div id="editor-preview" class="page-content"></div>
  </div>
</div>"""
    return shell(f"Edit — {title}", body, editor=True)


@app.post("/edit/<slug>")
def save_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = request.form.get("content", "")
    path.write_text(content, encoding="utf-8")
    return redirect(f"/page/{slug}")


@app.get("/new")
def new_page():
    body = """<h1>New Page</h1>
<form class="new-page-form" method="post" action="/new">
  <label for="page-name">Page name</label>
  <input type="text" id="page-name" name="slug"
         placeholder="e.g. My New Page" autofocus required>
  <button type="submit" class="btn btn-primary">Create</button>
</form>"""
    return shell("New Page", body)


@app.post("/new")
def create_page():
    raw = request.form.get("slug", "")
    slug = to_slug(raw)
    if not slug:
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    if not path.exists():
        title = raw.strip().title()
        path.write_text(f"# {title}\n\n", encoding="utf-8")
    return redirect(f"/edit/{slug}")


@app.post("/rename/<slug>")
def rename_page(slug):
    if slug == "index":
        abort(400)
    old_path = safe_path(slug)
    if old_path is None or not old_path.exists():
        abort(404)
    raw = request.form.get("new_name", "").strip()
    new_slug = to_slug(raw)
    if not new_slug or new_slug == slug:
        return redirect(f"/edit/{slug}")
    new_path = safe_path(new_slug)
    if new_path is None or new_path.exists():
        abort(400)
    old_path.rename(new_path)
    update_references(slug, new_slug)
    return redirect(f"/page/{new_slug}")


@app.get("/pages")
def pages_list():
    pages = []
    for md_file in PAGES_DIR.glob("*.md"):
        slug = md_file.stem
        content = md_file.read_text(encoding="utf-8")
        title = page_title(content, slug)
        pages.append((title, slug))
    pages.sort(key=lambda x: x[0].lower())

    items = "".join(
        f'<li class="page-list-item" data-title="{html.escape(t.lower())}" data-slug="{html.escape(s)}">'
        f'<a href="/page/{s}">{html.escape(t)}</a>'
        f'<span class="page-slug">{html.escape(s)}</span>'
        f'</li>'
        for t, s in pages
    )
    body = f"""<div class="page-list-wrap">
  <h1>All Pages <span class="page-count">({len(pages)})</span></h1>
  <input type="text" id="page-filter" placeholder="Filter pages…" autocomplete="off">
  <ul class="page-list" id="page-list">
    {items}
  </ul>
</div>"""
    return shell("All Pages", body)


# ── Search ────────────────────────────────────────────────────────────────────


def do_search(q: str) -> list[dict]:
    q_lower = q.lower()
    results = []

    for md_file in PAGES_DIR.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        content_lower = content.lower()
        slug = md_file.stem
        title = page_title(content, slug)

        score = (10 if q_lower in title.lower() else 0) + content_lower.count(q_lower)
        if score == 0:
            continue

        # Build excerpt around first match
        idx = content_lower.find(q_lower)
        if idx == -1:
            excerpt = html.escape(content[:150].replace("\n", " ")) + "…"
        else:
            start = max(0, idx - 60)
            end = min(len(content), idx + 90)
            snippet = content[start:end].replace("\n", " ")
            # Find match inside snippet and bold it
            snip_lower = snippet.lower()
            mi = snip_lower.find(q_lower)
            if mi != -1:
                pre = html.escape(snippet[:mi])
                match = html.escape(snippet[mi : mi + len(q)])
                post = html.escape(snippet[mi + len(q) :])
                excerpt = ("…" if start > 0 else "") + f"{pre}<mark>{match}</mark>{post}" + ("…" if end < len(content) else "")
            else:
                excerpt = html.escape(snippet)

        results.append({"slug": slug, "title": title, "excerpt": excerpt, "score": score})

    results.sort(key=lambda r: -r["score"])
    for r in results:
        del r["score"]
    return results[:10]


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(do_search(q))


@app.get("/search")
def search_page():
    q = request.args.get("q", "").strip()
    results = do_search(q) if q else []

    if results:
        cards = "".join(
            f"""<div class="search-result-card">
  <a href="/page/{r['slug']}">{html.escape(r['title'])}</a>
  <p>{r['excerpt']}</p>
</div>"""
            for r in results
        )
    else:
        cards = "<p>No results found.</p>"

    body = f"""<div class="search-results">
  <h1>Search: {html.escape(q)}</h1>
  {cards}
</div>"""
    return shell(f"Search: {q}", body)


# ── API ───────────────────────────────────────────────────────────────────────


@app.post("/api/preview")
def api_preview():
    text = request.get_data(as_text=True)
    return render_md(text)


@app.get("/api/pages")
def api_pages():
    return jsonify(all_slugs())


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
