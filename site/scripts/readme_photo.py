#!/usr/bin/env python3
"""The README's photograph, as the OF design's "Frame" board shows it: cropped to the hero's proportions (1072 by 640), a viewfinder's four
white corners, and the photographer's credit in a dark label. One JPEG for both themes, sized for GitHub.

Needs ImageMagick (magick) and the JetBrains Mono font file; run it only to change the photograph.

Usage: readme_photo.py PHOTO MONO_FONT OUT.jpg --credit "Name · Unsplash" [--width 1600 --height 955]
"""
import argparse
import subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("mono_font")
    ap.add_argument("out")
    ap.add_argument("--credit", required=True)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=955)
    ap.add_argument("--gravity", default="center", help="which part of the photo the crop keeps")
    a = ap.parse_args()
    w, h = a.width, a.height
    k = w / 1072  # the design's hero photograph is 1072 by 640 (its board is 1200 wide with 64 px margins)
    inset, arm, stroke = 28 * k, 48 * k, 3 * k
    corners = []
    for x0, y0, dx, dy in ((inset, inset, 1, 1), (w - inset, inset, -1, 1),
                           (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)):
        corners += ["-draw", f"polyline {x0:.1f},{y0 + dy * arm:.1f} {x0:.1f},{y0:.1f} {x0 + dx * arm:.1f},{y0:.1f}"]
    size = 13 * k * 1.15
    pad_x, pad_y = 12 * k, 8 * k
    text_w = len(a.credit) * size * 0.6  # JetBrains Mono: every glyph advances 0.6 em
    lx, ly = 48 * k, h - 48 * k
    box = f"roundrectangle {lx:.1f},{ly - size - 2 * pad_y:.1f} {lx + text_w + 2 * pad_x:.1f},{ly:.1f} {4 * k:.1f},{4 * k:.1f}"
    cmd = ["magick", a.photo, "-auto-orient", "-resize", f"{w}x{h}^", "-gravity", a.gravity, "-extent", f"{w}x{h}",
           "+gravity",
           "-fill", "none", "-stroke", "#FFFFFF", "-strokewidth", f"{stroke:.1f}", *corners,
           "-stroke", "none", "-fill", "rgba(27,32,19,0.8)", "-draw", box,
           "-font", a.mono_font, "-pointsize", f"{size:.1f}", "-fill", "#F2F3EE",
           "-annotate", f"+{lx + pad_x:.1f}+{ly - pad_y - size * 0.22:.1f}", a.credit,
           "-strip", "-interlace", "Plane", "-sampling-factor", "4:2:0", "-quality", "78", a.out]
    subprocess.run(cmd, check=True)
    print(a.out)


if __name__ == "__main__":
    main()
