"""Write the logo files of MITOS-JourneyGen.

    python assets/make_logo.py

The mark is a journey in three steps: a path that climbs from one service to
the next and ends in a ring, the point where the services come together. It
is drawn here once and written as SVG (for the side menu) and as PNG (for the
browser tab), so the two cannot drift apart.

Needs Pillow for the PNG.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
BLUE = "#0D5EAF"            # the blue of the Greek flag
PATH = [(13, 47), (27, 47), (27, 32), (41, 32), (41, 17), (51, 17)]   # on a 64 x 64 grid
NODES = [(13, 47), (34, 32)]
END = (51, 17)
WORDMARK = {"light": "#0B2A4A", "dark": "#E8F1FB"}   # by the theme it is shown on


def mark(x: float = 0, y: float = 0, size: float = 64) -> str:
    k = size / 64
    pts = " ".join(f"{x + px * k:.1f},{y + py * k:.1f}" for px, py in PATH)
    nodes = "".join(f'<circle cx="{x + cx * k:.1f}" cy="{y + cy * k:.1f}" r="{5.5 * k:.1f}" '
                    f'fill="#fff"/>' for cx, cy in NODES)
    ex, ey = x + END[0] * k, y + END[1] * k
    return (f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{14 * k:.1f}" '
            f'fill="{BLUE}"/>'
            f'<polyline points="{pts}" fill="none" stroke="#fff" stroke-width="{4.5 * k:.1f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>{nodes}'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="{7.5 * k:.1f}" fill="#fff"/>'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="{3.5 * k:.1f}" fill="{BLUE}"/>')


def icon_svg() -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" '
            f'aria-label="MITOS-JourneyGen">{mark()}</svg>\n')


def logo_svg(theme: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 330 64" role="img" '
            f'aria-label="MITOS-JourneyGen">{mark()}'
            f'<text x="78" y="42" font-family="Source Sans Pro, Segoe UI, Helvetica, Arial, '
            f'sans-serif" font-size="29" font-weight="600" fill="{WORDMARK[theme]}">'
            'MITOS<tspan font-weight="400">-JourneyGen</tspan></text></svg>\n')


def icon_png(path: str, size: int = 256) -> None:
    from PIL import Image, ImageDraw

    scale = 4                                     # drawn large, then reduced, for smooth edges
    n = size * scale
    k = n / 64
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=14 * k, fill=BLUE)
    pts = [(px * k, py * k) for px, py in PATH]
    d.line(pts, fill="#FFFFFF", width=round(4.5 * k), joint="curve")
    for px, py in pts:                            # round ends and corners
        r = 2.25 * k
        d.ellipse([px - r, py - r, px + r, py + r], fill="#FFFFFF")
    for cx, cy in NODES:
        r = 5.5 * k
        d.ellipse([cx * k - r, cy * k - r, cx * k + r, cy * k + r], fill="#FFFFFF")
    ex, ey = END[0] * k, END[1] * k
    d.ellipse([ex - 7.5 * k, ey - 7.5 * k, ex + 7.5 * k, ey + 7.5 * k], fill="#FFFFFF")
    d.ellipse([ex - 3.5 * k, ey - 3.5 * k, ex + 3.5 * k, ey + 3.5 * k], fill=BLUE)
    img.resize((size, size), Image.LANCZOS).save(path)


def main() -> None:
    files = {"icon.svg": icon_svg(), "logo_light.svg": logo_svg("light"),
             "logo_dark.svg": logo_svg("dark")}
    for name, text in files.items():
        with open(os.path.join(HERE, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    icon_png(os.path.join(HERE, "icon.png"))
    print("wrote", ", ".join(list(files) + ["icon.png"]))


if __name__ == "__main__":
    main()
