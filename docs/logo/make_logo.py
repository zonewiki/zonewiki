"""Write Zonewiki's mark and logos from one drawing: public/favicon.svg, public/icon.svg
(the home-screen icon) and docs/logo/*.svg, with the headings' serif.

The mark: a green rounded square, and in it a white square frame drawn in one
line, its top-right corner left open with a small square in it (an area of one's
own, open to whom one lets in). It replaced a white Z in November 2026:
a lone bold Z on a square looks like the symbol of Russia's war against Ukraine.
The page bar's copy (icon_zone in go/templates/partials/icons.html) and the
website's and Zonewiki CP's are the same drawing: `python3 docs/logo/make_logo.py --inner`
prints its lines on the 16-unit grid, to paste there.
"Zonewiki" is Source Serif 4 (public/vendor/source-serif) at weight 600,
turned into outlines here, so the logo looks the same wherever it is shown
(GitHub's README, a browser without the font) and loads nothing.

    pip install fonttools==4.55.3 brotli==1.1.0
    python3 docs/logo/make_logo.py

social-preview.png (GitHub: Settings, Social preview) is a 1280x640
screenshot of social-preview.svg, e.g. in the Playwright image (zonewiki-tools).
"""
import math
import sys
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


# The mark on a 16-unit grid: the frame's corners, its line's width, the small square's
# side and the gap between it and the line's ends. The frame and square together are
# centred in the grid (FRAME_X, FRAME_Y: the frame's top-left corner).
FRAME_X, FRAME_Y, FRAME_SIDE, FRAME_LINE, FRAME_SQUARE, FRAME_GAP = 4.15, 4.65, 7.2, 1.4, 2.4, 1.2


def n(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def inner(x, y, k, color="#fff"):
    """The white drawing inside the square: the 16-unit grid at x, y, scaled by k."""
    left, top = FRAME_X, FRAME_Y
    right, bottom = left + FRAME_SIDE, top + FRAME_SIDE
    half = FRAME_SQUARE / 2  # the small square sits on the open corner (right, top)
    line = [(right, top + half + FRAME_GAP), (right, bottom), (left, bottom), (left, top), (right - half - FRAME_GAP, top)]
    d = "M" + "L".join(f"{n(x + px * k)} {n(y + py * k)}" for px, py in line)
    return (
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{n(FRAME_LINE * k)}" stroke-linejoin="miter"/>\n'
        f'<rect x="{n(x + (right - half) * k)}" y="{n(y + (top - half) * k)}" '
        f'width="{n(FRAME_SQUARE * k)}" height="{n(FRAME_SQUARE * k)}" fill="{color}"/>'
    )


def mark(x, y, s):
    """The mark (a 16-unit grid) at x, y, s px wide."""
    k = s / 16
    return f'<rect x="{x:g}" y="{y:g}" width="{s:g}" height="{s:g}" rx="{3.5 * k:g}" fill="{GREEN}"/>\n' + inner(x, y, k)


if "--inner" in sys.argv:
    print(inner(0, 0, 1))
    sys.exit(0)


def lockup(x, y, s):
    """The mark at x, y, s px, and "Zonewiki" beside it, its capitals 62% of the mark; returns (svg, width)."""
    size = s * 0.62 * upm / cap
    gap = s * 0.28
    baseline = y + s / 2 + s * 0.31
    return mark(x, y, s), text_path("Zonewiki", size, x + s + gap, baseline), s + gap + text_width("Zonewiki", size)


def svg(w, h, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w:g}" height="{h:g}" '
        f'role="img" aria-label="Zonewiki">\n<title>Zonewiki</title>\n{body}\n</svg>\n'
    )


def r(v):
    return round(v, 2)


# The logo: the mark and "Zonewiki", for a light and a dark page.
m, word, w = lockup(0, 0, 96)
for name, ink in (("logo.svg", INK), ("logo-dark.svg", INK_DARK)):
    (HERE / name).write_text(svg(r(w), 96, f'{m}\n<path d="{word}" fill="{ink}"/>'))

# The mark alone, for an avatar.
(HERE / "mark.svg").write_text(svg(512, 512, mark(0, 0, 512)))

# The favicon: the mark on its 16-unit grid.
PUBLIC = HERE.parent.parent / "public"
(PUBLIC / "favicon.svg").write_text(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">\n{mark(0, 0, 16)}\n</svg>\n')

# The home-screen icon, full-bleed: iOS rounds the corners itself and Android crops a
# "maskable" icon to a circle, so the drawing is scaled into the central 80% (a circle
# of radius 204.8 around the middle). The PNGs beside it are screenshots of this file
# (docs/spec/deployment.md, Home-screen app).
K = 30
(PUBLIC / "icon.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n'
    '  <!-- Written by docs/logo/make_logo.py: the mark, full-bleed, inside the maskable safe zone. -->\n'
    f'  <rect width="512" height="512" fill="{GREEN}"/>\n{inner(256 - 8 * K, 256 - 8 * K, K)}\n</svg>\n')

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
