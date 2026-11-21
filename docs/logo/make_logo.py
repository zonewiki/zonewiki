"""Write Zone's logos (docs/logo/*.svg) from the favicon's mark and the headings' serif.

The mark is public/favicon.svg's: a green rounded square, a white Z.
"Zone" is Source Serif 4 (public/vendor/source-serif) at weight 600,
turned into outlines here, so the logo looks the same wherever it is shown
(GitHub's README, a browser without the font) and loads nothing.

    pip install fonttools==4.55.3 brotli==1.1.0
    python3 docs/logo/make_logo.py

social-preview.png (GitHub: Settings, Social preview) is a 1280x640
screenshot of social-preview.svg, e.g. in the Playwright image (zonewiki-tools).
"""
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).resolve().parent
FONT = HERE.parent.parent / "public/vendor/source-serif/source-serif-4-latin.woff2"

GREEN = "#3a7563"            # --accent in public/style.css
INK, INK_DARK = "#1d1f1c", "#ebeae4"
MUTED = "#676a63"
PAPER = "#f6f4ee"

font = instantiateVariableFont(TTFont(FONT), {"wght": 600})
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
upm = font["head"].unitsPerEm
cap = font["OS/2"].sCapHeight
hmtx = font["hmtx"]


def text_width(text, size):
    """The ink width of text at size px: its advances, less the last glyph's right bearing."""
    last = cmap[ord(text[-1])]
    bounds = BoundsPen(glyphs)
    glyphs[last].draw(bounds)
    units = sum(hmtx[cmap[ord(c)]][0] for c in text) - (hmtx[last][0] - bounds.bounds[2])
    return units * size / upm


def text_path(text, size, x, baseline):
    """SVG path data for text at size px, starting at x on the baseline."""
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    k = size / upm
    for c in text:
        g = cmap[ord(c)]
        glyphs[g].draw(TransformPen(pen, (k, 0, 0, -k, x, baseline)))
        x += hmtx[g][0] * k
    return pen.getCommands()


def mark(x, y, s):
    """The favicon's mark (a 16-unit grid) at x, y, s px wide."""
    k = s / 16
    return (
        f'<rect x="{x:g}" y="{y:g}" width="{s:g}" height="{s:g}" rx="{3.5 * k:g}" fill="{GREEN}"/>\n'
        f'<path d="M{x + 5 * k:g} {y + 5 * k:g}H{x + 11 * k:g}L{x + 5 * k:g} {y + 11 * k:g}H{x + 11 * k:g}" '
        f'fill="none" stroke="#fff" stroke-width="{1.9 * k:g}" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def lockup(x, y, s):
    """The mark at x, y, s px, and "Zone" beside it, its capitals 62% of the mark; returns (svg, width)."""
    size = s * 0.62 * upm / cap
    gap = s * 0.28
    baseline = y + s / 2 + s * 0.31
    return mark(x, y, s), text_path("Zone", size, x + s + gap, baseline), s + gap + text_width("Zone", size)


def svg(w, h, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w:g}" height="{h:g}" '
        f'role="img" aria-label="Zone">\n<title>Zone</title>\n{body}\n</svg>\n'
    )


def r(v):
    return round(v, 2)


# The logo: the mark and "Zone", for a light and a dark page.
m, word, w = lockup(0, 0, 96)
for name, ink in (("logo.svg", INK), ("logo-dark.svg", INK_DARK)):
    (HERE / name).write_text(svg(r(w), 96, f'{m}\n<path d="{word}" fill="{ink}"/>'))

# The mark alone, for an avatar.
(HERE / "mark.svg").write_text(svg(512, 512, mark(0, 0, 512)))

# GitHub's social preview, 1280x640: the logo and the README's tagline on paper.
W, H, S = 1280, 640, 168
_, _, w = lockup(0, 0, S)
m, word, _ = lockup((W - w) / 2, 196, S)
body = [f'<rect width="{W}" height="{H}" fill="{PAPER}"/>', m, f'<path d="{word}" fill="{INK}"/>']
lines = ["A wiki with tasks that you, your team and your AI assistants share,", "in plain files you own."]
for i, line in enumerate(lines):
    size = 32
    path = text_path(line, size, (W - text_width(line, size)) / 2, 196 + S + 84 + i * 46)
    body.append(f'<path d="{path}" fill="{MUTED}"/>')
(HERE / "social-preview.svg").write_text(svg(W, H, "\n".join(body)))
