"""Render the README's screenshots (docs/screenshots/*.png) from a small
sample wiki, so they can be redone whenever the UI changes:

    ./run.sh screenshots

It runs Zone on a throwaway wiki (pages, tasks dated around today, a git
repo) and photographs it with headless Chromium (Playwright), as the browser
tests do: in the tools image, which has both.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import json
import base64
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

WIKI = Path(tempfile.mkdtemp(prefix="zonewiki_screenshots_"))
# hosts in the settings file, not ZONE_ALLOW_REMOTE (which would show its
# badge): the server listens on a random port, and the Host check compares
# names only.
os.environ.update(ZONE_DATA=str(WIKI))
os.environ.pop("ZONE_ALLOW_REMOTE", None)
(WIKI / ".zone").mkdir()
(WIKI / ".zone" / "config").write_text("hosts = 127.0.0.1\nno-login = yes\n")  # no-login: no first-run page
# The commit the screenshots show at the bottom left, without "+dirty".
os.environ["ZONE_BUILD"] = subprocess.run(
    ["git", "-c", "safe.directory=*", "-C", str(ROOT), "log", "-1", "--format=%h %cd", "--date=format:%Y-%m-%d %H:%M"],
    capture_output=True, text=True).stdout.strip() or "unknown"
sys.path.insert(0, str(ROOT))

TODAY = date.today()
# The phone pair's width in CSS px (its PNG is twice that); the README's <img
# shows it at this width, so on a wide page it doesn't fill the column.
PHONE_WIDTH = 640


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


PAGES = {
    "index": """# Home

What's going on:

- [[Projects/Garden shed]]: building it this spring
- [[Recipes/Sourdough]]: the weekend loaf
- [[Reading list]]
""",
    "projects/garden-shed": """---
tags: project, garden
tldr: A 2×3 m timber shed at the back of the garden, built over a few spring weekends.
---
# Garden shed

> [!NOTE]
> No permit needed below 10 m²: checked with the town office on 3 March.

## Plan

| Step | When | Status |
|------|------|--------|
| Foundation: gravel and pavers | Week 1 | done |
| Floor frame | Week 2 | done |
| Walls and roof | Weeks 3–4 | in progress |
| Stain and gutter | Week 5 | |

## Materials

- [x] Pressure-treated timber 45×95, 24 pieces
- [x] Roofing felt, 2 rolls
- [ ] Screws 5×80, a box of 500
- [ ] Wood stain, grey

## Roof

A single slope towards the back, 15°, so the rain runs off towards the
[[Garden/Rain barrel]]. Felt over 18 mm OSB, battens every 40 cm.

## Open questions

- Gutter into the rain barrel or into the drain?
- Shelves now, or once the walls are stained?

## Log

- **Week 1**: foundation level within 3 mm over the whole length.
- **Week 2**: floor frame up; one joist replaced (warped).
""",
    "garden/rain-barrel": """# Rain barrel

300 litres, next to the [[Projects/Garden shed]]. Overflow into the gravel bed.
""",
    "recipes/sourdough": """---
tags: recipe, baking
---
# Sourdough

A slow, forgiving loaf: mix in the evening, bake the next morning.

## Ingredients

| | Amount |
|---|---|
| Bread flour | 450 g |
| Whole wheat | 50 g |
| Water | 360 g |
| Starter, active | 100 g |
| Salt | 10 g |

## Method

1. Mix flour and water, rest 1 hour.
2. Add starter and salt, then four sets of folds, 30 minutes apart.
3. Shape, into the banneton, overnight in the fridge.
4. Bake at 250 °C: 20 minutes covered, 25 uncovered.

> [!TIP]
> The dough is ready to shape when it has grown by half and jiggles.
""",
    "reading-list": """# Reading list

- *The Overstory*, Richard Powers
- *A Pattern Language*, Christopher Alexander
- [[Projects/Garden shed]] links: *The Backyard Builder*
""",
    "tasks": "# Tasks\n\n[[tasks: view=today]]\n",
}

TASKS = [
    {"title": "Order screws for the shed", "tags": ["garden"], "start_date": day(0),
     "description": "5×80, a box of 500. For [[Projects/Garden shed]]."},
    {"title": "Call the roofer about the gutter", "tags": ["garden"], "start_date": day(1),
     "due_date": day(3)},
    {"title": "Feed the starter", "tags": ["kitchen"], "start_date": day(0), "rec_type": "daily"},
    {"title": "Renew passport", "tags": ["admin"], "due_date": day(12)},
    {"title": "Book flights to Lisbon", "tags": ["travel"], "start_date": day(2)},
    {"title": "Return library books", "tags": ["errands"], "due_date": day(0)},
    {"title": "Plan Ana's birthday dinner", "tags": ["family"], "start_date": day(4)},
    {"title": "Back up the photo library", "tags": ["admin"], "due_date": day(6),
     "rec_type": "monthly"},
    {"title": "Stain the shed walls", "tags": ["garden"], "start_date": day(5),
     "description": "Grey, two coats. [[Projects/Garden shed]]"},
    {"title": "Read The Overstory, chapter 5", "tags": ["reading"]},
]


def build_wiki():
    for slug, text in PAGES.items():
        path = WIKI / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def post(base, path, data):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")


def main():
    from playwright.sync_api import sync_playwright
    from tests.goserver import GoServer

    build_wiki()                     # before Zone starts: its first commit takes them in
    srv = GoServer({k: v for k, v in os.environ.items() if k.startswith("ZONE_")}, WIKI.parent / "zonewiki.log",
                   config="")       # the wiki has its own (above)
    base = f"http://127.0.0.1:{srv.port}"

    ids = [post(base, "/api/tasks", t)["id"] for t in TASKS]
    post(base, f"/api/tasks/{ids[0]}/comments", {"text": "Hardware store has them on Saturday."})
    # A task inline on the page, as a chip.
    shed = WIKI / "projects/garden-shed.md"
    shed.write_text(shed.read_text().replace(
        "- Shelves now, or once the walls are stained?\n",
        f"- Shelves now, or once the walls are stained?\n\nNext: [[task:{ids[0][:8]}]]\n"))
    subprocess.run(["git", "commit", "-qm", "Next step", "--", str(shed)], cwd=WIKI, check=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page(width, height, scheme="light", scale=1, **opts):
            ctx = browser.new_context(base_url=base, viewport={"width": width, "height": height},
                                      color_scheme=scheme, device_scale_factor=scale, **opts)
            return ctx.new_page()

        def settle(pg):
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(300)

        # 1. A page on a wide screen: contents left, links right.
        pg = page(1440, 900)
        pg.goto("/page/projects/garden-shed")
        settle(pg)
        pg.screenshot(path=OUT / "page.png")

        # 2. The tasks' Open tab with Plan on: the board.
        pg = page(1280, 820)
        pg.goto("/tasks?view=open&group=day")
        settle(pg)
        pg.screenshot(path=OUT / "tasks.png")

        # 3. The editor with live preview, in dark mode.
        pg = page(1440, 820, scheme="dark")
        pg.goto("/edit/recipes/sourdough")
        settle(pg)
        pg.screenshot(path=OUT / "editor.png")

        # 4. On a phone: a page and the task app's Focus, side by side. Each phone
        # is shown at two thirds of its size (260 of 390 px), so the pair reads
        # as phones next to the desktop shots rather than as a wall of text;
        # at 2x the text stays sharp. The README shows it at PHONE_WIDTH.
        shots = []
        for url in ("/page/projects/garden-shed", "/page/tasks"):
            pg = page(390, 780, scale=2, is_mobile=True, has_touch=True)
            pg.goto(url)
            settle(pg)
            shots.append(base64.b64encode(pg.screenshot()).decode())
        frame = page(PHONE_WIDTH, 600, scale=2)
        frame.set_content(f"""<body style="margin:0;background:#e6e3da;display:flex;gap:32px;
            justify-content:center;align-items:center;height:100vh">
            {''.join(f'<img src="data:image/png;base64,{s}" style="width:260px;border-radius:20px;'
                     f'box-shadow:0 6px 24px rgba(0,0,0,.18)">' for s in shots)}</body>""")
        frame.screenshot(path=OUT / "phone.png")

        browser.close()
    srv.stop()
    compress(sorted(OUT.glob("*.png")))
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))


def compress(paths):
    """Lossy palette compression (pngquant), about a quarter of the size
    with no visible change for UI shots. Skipped without pngquant (the tools
    image has it)."""
    if not shutil.which("pngquant"):
        print("pngquant not found: screenshots left uncompressed")
        return
    for path in paths:
        before = path.stat().st_size
        subprocess.run(["pngquant", "--quality=70-95", "--speed=1", "--strip", "--force",
                        "--skip-if-larger", "--ext", ".png", str(path)], check=False)
        print(f"{path.name}: {before // 1024} KB -> {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
