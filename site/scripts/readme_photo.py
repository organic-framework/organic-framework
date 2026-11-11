#!/usr/bin/env python3
"""The README's photograph, after the OF design's "Frame" board and the founder's pick (variant A, 2026-11-11): the
photo cropped to the hero's proportions, a viewfinder's four white corners, and the name "Organic Framework" set low
on the left in the design's Inter Tight, woven into the grass: the photo's brightest strands are laid back over the
letters, so the grass crosses the name. No caption on the image (the founder: the credit stays out of the picture;
the README's License section names the photographer). One JPEG for both GitHub themes, sized for GitHub.

Needs ImageMagick (magick) and a static Inter Tight 500 font file (instantiate the variable font with fontTools).

Usage: readme_photo.py PHOTO INTER_TIGHT_500.ttf OUT.jpg [--width 1600 --height 955] [--credit "…" --mono FONT]
"""
import argparse
import os
import subprocess
import tempfile


def magick(*a):
    subprocess.run(["magick", *a], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("display_font", help="Inter Tight at weight 500, as a static .ttf")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=955)
    ap.add_argument("--gravity", default="center", help="which part of the photo the crop keeps")
    ap.add_argument("--name", default="Organic Framework", help="the text woven into the grass; empty for none")
    ap.add_argument("--credit", help="a caption in a dark label at the lower left (the design's board style)")
    ap.add_argument("--mono", help="JetBrains Mono .ttf, for --credit")
    a = ap.parse_args()
    w, h = a.width, a.height
    k = w / 1072  # the design's hero photograph is 1072 by 640 (its board is 1200 wide with 64 px margins)
    with tempfile.TemporaryDirectory() as tmp:
        base, text, mask, woven = (os.path.join(tmp, n) for n in ("base.png", "text.png", "mask.png", "woven.png"))
        magick(a.photo, "-auto-orient", "-resize", f"{w}x{h}^", "-gravity", a.gravity, "-extent", f"{w}x{h}",
               "+repage", base)
        if a.name:
            size = 176 * w / 1600
            magick("-size", f"{w}x{h}", "xc:none", "-font", a.display_font, "-pointsize", f"{size:.1f}",
                   "-kerning", f"{-0.07 * size:.1f}", "-fill", "#F2F3EE",
                   "-annotate", f"+{92 * w / 1600:.1f}+{h - 120 * w / 1600:.1f}", a.name, text)
            # the brightest strands, softened: where they cross the letters, the grass lies over the name
            magick(base, "-colorspace", "gray", "-level", "58%,82%", "-blur", "0x0.7", mask)
            magick(base, text, "-composite", "(", base, mask, "-alpha", "off", "-compose", "CopyOpacity",
                   "-composite", ")", "-compose", "Over", "-composite", woven)
        else:
            woven = base
        inset, arm, stroke = 28 * k, 48 * k, 3 * k
        draw = ["-fill", "none", "-stroke", "#FFFFFF", "-strokewidth", f"{stroke:.1f}"]
        for x0, y0, dx, dy in ((inset, inset, 1, 1), (w - inset, inset, -1, 1),
                               (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)):
            draw += ["-draw", f"polyline {x0:.1f},{y0 + dy * arm:.1f} {x0:.1f},{y0:.1f} {x0 + dx * arm:.1f},{y0:.1f}"]
        if a.credit:
            size = 13 * k * 1.15
            pad_x, pad_y = 12 * k, 8 * k
            text_w = len(a.credit) * size * 0.6  # JetBrains Mono: every glyph advances 0.6 em
            lx, ly = 48 * k, h - 48 * k
            draw += ["-stroke", "none", "-fill", "rgba(27,32,19,0.8)", "-draw",
                     f"roundrectangle {lx:.1f},{ly - size - 2 * pad_y:.1f} {lx + text_w + 2 * pad_x:.1f},{ly:.1f} "
                     f"{4 * k:.1f},{4 * k:.1f}",
                     "-font", a.mono, "-pointsize", f"{size:.1f}", "-fill", "#F2F3EE",
                     "-annotate", f"+{lx + pad_x:.1f}+{ly - pad_y - size * 0.22:.1f}", a.credit]
        magick(woven, "+gravity", *draw, "-strip", "-interlace", "Plane", "-sampling-factor", "4:2:0",
               "-quality", "78", a.out)
    print(a.out)


if __name__ == "__main__":
    main()
