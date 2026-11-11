#!/usr/bin/env python3
"""The README's tagline strip and the documents' strips, in the OF design (the founder's design system, direction
"Frame"), each in a light and a dark variant for GitHub's two themes.

The design's rules this follows: on the photograph only the frame and the name (readme_photo.py, the founder's
variant A); the tagline stands under it, on the design's ground. Type: Inter Tight 500 tightly tracked for the
name and titles, Inter Tight 400 for text, JetBrains Mono 400 for labels. The main mark is the name in two lines inside
a viewfinder's four corners.

GitHub shows a README without its CSS or web fonts, and an SVG shown as an image loads no font, so the text is drawn
as outlines: the image looks the same everywhere. The README picks the variant with a <picture> element.

Needs fontTools and uharfbuzz (pip install fonttools uharfbuzz); run it only to change these images, never in a build.

Usage: readme_header.py --tokens TOKENS.json --out-dir .github/readme [--strip features="What works today" …]
TOKENS.json: {"display": {"font": "<InterTight[wght].ttf>", "axes": {"wght": 500}, "tracking": -0.045},
              "mark": {"font": "<InterTight[wght].ttf>", "axes": {"wght": 500}, "tracking": -0.04},
              "body": {"font": "<InterTight[wght].ttf>", "axes": {"wght": 400}, "tracking": 0},
              "mono": {"font": "<JetBrainsMono[wght].ttf>", "axes": {"wght": 400}, "tracking": 0},
              "light": {"fg": "#…", "muted": "#…", "label": "#…", "frame": "#…", "line": "#…"},
              "dark": {…the same keys…},
              "label": "the mono line under the title", "tagline": ["line 1", "line 2"]}
"""
import argparse
import io
import json
import os

W = 1280


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


def corners(x, y, w, h, arm, stroke, colour):
    """A viewfinder's four corners around the box (x, y, w, h), drawn inside it like the design's CSS borders."""
    s = stroke / 2
    d = (f"M{x + s} {y + arm} V{y + s} H{x + arm} M{x + w - arm} {y + s} H{x + w - s} V{y + arm} "
         f"M{x + w - s} {y + h - arm} V{y + h - s} H{x + w - arm} M{x + arm} {y + h - s} H{x + s} V{y + h - arm}")
    return f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{stroke}"/>'


def mark(faces, x, y, k, colour, frame):
    """The main mark (design board "Frame", 01): "Organic" over "Framework", Inter Tight 500, 26 px with a line height
    of 1.02, inside corners of 14 px at 2 px, with 18 px and 22 px of padding; all times k. Returns (svg, w, h)."""
    size = 26 * k
    top, word = faces["mark"].path("Organic", size)
    bottom, w2 = faces["mark"].path("Framework", size)
    pad_x, pad_y, lh = 22 * k, 18 * k, 1.02 * size
    w, h = pad_x * 2 + max(word, w2), pad_y * 2 + 2 * lh
    asc = 0.8 * size  # the baseline of the first line, from the top of its line box
    svg = (corners(x, y, w, h, 14 * k, 2 * k, frame)
           + f'<path transform="translate({x + pad_x:.1f} {y + pad_y + asc:.1f})" d="{top}" fill="{colour}"/>'
           + f'<path transform="translate({x + pad_x:.1f} {y + pad_y + asc + lh:.1f})" d="{bottom}" fill="{colour}"/>')
    return svg, w, h


def tagline_strip(tokens, theme, faces):
    """Under the photograph, which carries the name: the tagline on the design's ground (Paper in light, Ink in dark),
    aligned with the name on the photo. Its first line is the headline (Inter Tight 500), the rest is text (400),
    then the mono label."""
    c, x = tokens[theme], 74
    head, *rest = tokens["tagline"]
    hd, _ = faces["mark"].path(head, 46)
    lines = [faces["body"].path(t, 30)[0] for t in rest]
    label, _ = faces["mono"].path(tokens["label"], 20)
    y = 100
    out = [f'<path transform="translate({x} {y})" d="{hd}" fill="{c["fg"]}"/>']
    y += 54
    for d in lines:
        out.append(f'<path transform="translate({x} {y})" d="{d}" fill="{c["muted"]}"/>')
        y += 42
    h = y + 62
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img"
  aria-label="{tokens.get('alt') or ' '.join(tokens['tagline'])}">
  <rect width="{W}" height="{h}" fill="{c['bg']}"/>
  {''.join(out)}
  <path transform="translate({x} {h - 46})" d="{label}" fill="{c['label']}"/>
</svg>
"""


def strip(tokens, theme, faces, label):
    """The top of a document: the main mark at the left, the document's name in mono at the right, a hairline."""
    c, h, k = tokens[theme], 132, 1.25
    m, mw, mh = mark(faces, 2, 8, k, c["fg"], c["frame"])
    lab, lw = faces["mono"].path(label, 21)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img"
  aria-label="Organic Framework: {label}">
  {m}
  <path transform="translate({W - 2 - lw:.1f} {8 + mh - 22:.1f})" d="{lab}" fill="{c['label']}"/>
  <rect x="0" y="{h - 2}" width="{W}" height="2" fill="{c['line']}"/>
</svg>
"""


def mark_file(tokens, theme, faces):
    """The main mark alone, cut to its own size, for wherever it shows at its own size (not the site's small logo)."""
    c = tokens[theme]
    m, w, h = mark(faces, 1, 1, 1.0, c["fg"], c["frame"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w + 2:.1f} {h + 2:.1f}" width="{w + 2:.1f}" height="{h + 2:.1f}" role="img"
  aria-label="Organic Framework">
  {m}
</svg>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--strip", action="append", default=[], metavar="NAME=LABEL",
                    help="also a strip for a document: strip-NAME-light.svg and strip-NAME-dark.svg")
    ap.add_argument("--mark-dir", help="also the main mark alone, main-mark-light.svg and main-mark-dark.svg, here")
    a = ap.parse_args()
    with open(a.tokens) as fh:
        tokens = json.load(fh)
    faces = {k: Face(tokens[k]) for k in ("display", "mark", "body", "mono")}
    os.makedirs(a.out_dir, exist_ok=True)
    for theme in ("light", "dark"):
        outs = {f"header-{theme}.svg": tagline_strip(tokens, theme, faces)}
        for spec in a.strip:
            name, label = spec.split("=", 1)
            outs[f"strip-{name}-{theme}.svg"] = strip(tokens, theme, faces, label)
        if a.mark_dir:
            outs[os.path.join(a.mark_dir, f"main-mark-{theme}.svg")] = mark_file(tokens, theme, faces)
        for name, svg in outs.items():
            p = os.path.join(a.out_dir, name)
            with open(p, "w") as fh:
                fh.write(svg)
            print(p, os.path.getsize(p))


if __name__ == "__main__":
    main()
