#!/usr/bin/env python3
"""The README's header image, in a light and a dark variant, in the OF design (direction "Frame": the viewfinder
symbol, Inter Tight tightly tracked, JetBrains Mono for labels, colours taken from the photographs).

GitHub shows a README without its CSS or web fonts, and an SVG shown as an image loads no font, so the text is drawn
as outlines here: the image looks the same everywhere. The README picks the variant with a <picture> element.

Needs fontTools and uharfbuzz (pip install fonttools uharfbuzz); run it only to change the header, never in a build.

Usage: readme_header.py --tokens TOKENS.json --out-dir .github/readme [--strip features="What works today" …]
TOKENS.json: {"display": {"font": "<InterTight[wght].ttf>", "axes": {"wght": 500}, "tracking": -0.045},
              "body": {"font": "<InterTight[wght].ttf>", "axes": {"wght": 400}, "tracking": -0.01},
              "mono": {"font": "<JetBrainsMono[wght].ttf>", "axes": {"wght": 400}, "tracking": 0},
              "light": {"bg": "#…", "fg": "#…", "muted": "#…", "frame": "#…", "dot": "#…", "line": "#…"},
              "dark": {…the same keys…},
              "label": "the mono line above the title", "tagline": ["line 1", "line 2"]}
"""
import argparse
import io
import json
import os

W, H = 1280, 400


class Face:
    def __init__(self, spec):
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
        import uharfbuzz as hb
        font = TTFont(spec["font"])
        if "fvar" in font and spec.get("axes"):
            font = instancer.instantiateVariableFont(font, spec["axes"])
        buf = io.BytesIO()
        font.save(buf)
        self.font, self.hb = font, hb.Font(hb.Face(buf.getvalue()))
        self.tracking = spec.get("tracking", 0)

    def path(self, text, size):
        """(SVG path data with the baseline at 0, advance width) for text, shaped by HarfBuzz."""
        import uharfbuzz as hb
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen
        b = hb.Buffer()
        b.add_str(text)
        b.guess_segment_properties()
        hb.shape(self.hb, b, {"kern": True, "liga": True})
        glyphs, order = self.font.getGlyphSet(), self.font.getGlyphOrder()
        scale = size / self.font["head"].unitsPerEm
        track = self.tracking * size
        pen = SVGPathPen(glyphs, ntos=lambda v: ("%.1f" % v).rstrip("0").rstrip("."))
        x = 0.0
        for info, pos in zip(b.glyph_infos, b.glyph_positions):
            t = TransformPen(pen, (scale, 0, 0, -scale, x + pos.x_offset * scale, -pos.y_offset * scale))
            glyphs[order[info.codepoint]].draw(t)
            x += pos.x_advance * scale + track
        return pen.getCommands(), x - track


def symbol(x, y, size, frame, dot):
    """The OF symbol: a viewfinder's four corners and a dot (the design's 48 px drawing, viewBox 64)."""
    s = size / 64
    return (f'<g transform="translate({x} {y}) scale({s:.4f})">'
            f'<path d="M5 22 V5 H22 M42 5 H59 V22 M59 42 V59 H42 M22 59 H5 V42" fill="none" stroke="{frame}" '
            f'stroke-width="5"/><circle cx="32" cy="32" r="6" fill="{dot}"/></g>')


def header(tokens, theme, faces):
    c = tokens[theme]
    left, sym = 88, 120
    tx = left + sym + 48
    label, _ = faces["mono"].path(tokens["label"], 17)
    title, _ = faces["display"].path("Organic Framework", 104)
    lines = [faces["body"].path(t, 27)[0] for t in tokens["tagline"]]
    tag = "".join(f'<path transform="translate({tx} {290 + 38 * i})" d="{d}" fill="{c["muted"]}"/>'
                  for i, d in enumerate(lines))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img"
  aria-label="Organic Framework: run your AI agents as an organization">
  <rect width="{W}" height="{H}" rx="6" fill="{c['bg']}"/>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="5.5" fill="none" stroke="{c['line']}"/>
  {symbol(left, 118, sym, c['frame'], c['dot'])}
  <path transform="translate({tx} 108)" d="{label}" fill="{c['muted']}"/>
  <path transform="translate({tx} 216)" d="{title}" fill="{c['fg']}"/>
  {tag}
</svg>
"""


def strip(tokens, theme, faces, label):
    """A slim strip for the top of a document: the symbol, the name, and the document's label in mono."""
    c, h = tokens[theme], 120
    name, _ = faces["display"].path("Organic Framework", 40)
    lab, lw = faces["mono"].path(label, 17)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img"
  aria-label="Organic Framework: {label}">
  <rect width="{W}" height="{h}" rx="6" fill="{c['bg']}"/>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{h - 1}" rx="5.5" fill="none" stroke="{c['line']}"/>
  {symbol(48, 36, 48, c['frame'], c['dot'])}
  <path transform="translate(124 74)" d="{name}" fill="{c['fg']}"/>
  <path transform="translate({W - 48 - lw:.1f} 72)" d="{lab}" fill="{c['muted']}"/>
</svg>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--strip", action="append", default=[], metavar="NAME=LABEL",
                    help="also a slim strip for a document: strip-NAME-light.svg and strip-NAME-dark.svg")
    a = ap.parse_args()
    with open(a.tokens) as fh:
        tokens = json.load(fh)
    faces = {k: Face(tokens[k]) for k in ("display", "body", "mono")}
    os.makedirs(a.out_dir, exist_ok=True)
    for theme in ("light", "dark"):
        p = os.path.join(a.out_dir, f"header-{theme}.svg")
        with open(p, "w") as fh:
            fh.write(header(tokens, theme, faces))
        print(p, os.path.getsize(p))
        for spec in a.strip:
            name, label = spec.split("=", 1)
            p = os.path.join(a.out_dir, f"strip-{name}-{theme}.svg")
            with open(p, "w") as fh:
                fh.write(strip(tokens, theme, faces, label))
            print(p, os.path.getsize(p))


if __name__ == "__main__":
    main()
