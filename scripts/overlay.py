"""Draw an extraction back over its own sheet, as a PNG.

    .venv/bin/python scripts/overlay.py example/*.pdf --out build/overlays

The point of this is verification: if a wall in the model is wrong, it is wrong
here too, on top of the drawing it came from, where you can see it.
"""

from __future__ import annotations

import argparse
import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.classify import FLOOR_PLAN, classify  # noqa: E402
from app.extract import extract_plan  # noqa: E402
from app.pdfvec import load_sheet  # noqa: E402

WALL = (0.15, 0.55, 0.95)
WALL_EXT = (0.05, 0.30, 0.85)
DOOR = (0.95, 0.45, 0.05)
WINDOW = (0.05, 0.75, 0.45)
COLUMN = (0.85, 0.15, 0.65)
ROOM = (0.55, 0.55, 0.60)
STAIR = (0.10, 0.65, 0.75)
WELL = (0.95, 0.15, 0.15)


def overlay(
    pdf: str,
    out_dir: str,
    dpi: int = 150,
    px_per_ft: float | None = None,
    crop: bool = False,
):
    sheet = load_sheet(pdf, os.path.basename(pdf))
    cls = classify(sheet.text, os.path.basename(pdf), has_vectors=len(sheet.segs) > 20)
    if cls.kind != FLOOR_PLAN:
        print(f"  skip ({cls.label})")
        return None
    ex = extract_plan(
        sheet, os.path.basename(pdf), cls.level or 0, cls.label, px_per_ft
    )

    doc = fitz.open(pdf)
    page = doc[0]
    # extraction is in display space; drawing happens in the page's own space
    dm = page.derotation_matrix

    ox, oy = ex.origin_px
    px = ex.scale.px_per_ft

    def R(x0: float, y0: float, x1: float, y1: float) -> fitz.Rect:
        return fitz.Rect(
            ox + x0 * px, oy + y0 * px, ox + x1 * px, oy + y1 * px
        ) * dm

    for r in ex.rooms:
        page.draw_rect(
            R(r.x0, r.y0, r.x1, r.y1), color=ROOM, width=0.4, dashes="[2 2] 0"
        )
    for c in ex.columns:
        page.draw_rect(R(c.x0, c.y0, c.x1, c.y1), color=COLUMN, width=1.0)
    for st in ex.stairs:
        for f in st.flights:
            page.draw_rect(R(f.x0, f.y0, f.x1, f.y1), color=STAIR, width=1.4)
            # every tread, so a wrong going shows up as drift against the drawing
            for i in range(1, f.treads):
                if f.up in ("+x", "-x"):
                    v = f.x0 + i * f.going_ft
                    page.draw_line(R(v, f.y0, v, f.y1).tl, R(v, f.y0, v, f.y1).br,
                                   color=STAIR, width=0.5)
                else:
                    v = f.y0 + i * f.going_ft
                    page.draw_line(R(f.x0, v, f.x1, v).tl, R(f.x0, v, f.x1, v).br,
                                   color=STAIR, width=0.5)
        if st.well:
            page.draw_rect(R(*st.well), color=WELL, width=1.6, dashes="[3 2] 0")
    for w in ex.walls:
        page.draw_rect(
            R(w.x0, w.y0, w.x1, w.y1),
            color=WALL_EXT if w.exterior else WALL,
            width=1.2 if w.exterior else 0.7,
        )
        for o in w.openings:
            col = DOOR if o.kind == "door" else WINDOW
            pad = 2.0 / px
            if w.axis == "h":
                r = R(o.u0, w.y0 - pad, o.u1, w.y1 + pad)
            else:
                r = R(w.x0 - pad, o.u0, w.x1 + pad, o.u1)
            page.draw_rect(r, color=col, width=1.6)

    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(pdf))[0].replace(" ", "_")
    path = os.path.join(out_dir, f"{name}.overlay.png")
    clip = None
    if crop:
        # the plan only. A title block carries the client's name, the site
        # address and the designer's phone number, none of which belong in a
        # screenshot you are about to share.
        # get_pixmap's clip is in display space, so this one is not derotated
        m = 6.0 * px
        clip = fitz.Rect(
            ox + ex.bounds[0] * px - m,
            oy + ex.bounds[1] * px - m,
            ox + ex.bounds[2] * px + m,
            oy + ex.bounds[3] * px + m,
        )
    page.get_pixmap(dpi=dpi, clip=clip).save(path)

    doors = sum(1 for w in ex.walls for o in w.openings if o.kind == "door")
    wins = sum(1 for w in ex.walls for o in w.openings if o.kind == "window")
    treads = sum(f.treads for s in ex.stairs for f in s.flights)
    print(
        f"  {cls.label}: {len(ex.walls)} walls, {doors} doors, {wins} windows, "
        f"{len(ex.columns)} columns, {len(ex.rooms)} rooms, "
        f"{len(ex.stairs)} stairs ({treads} treads), "
        f"{px:.3f} px/ft ({ex.scale.confidence}) → {path}"
    )
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--out", default="build/overlays")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--scale", type=float, default=None, help="px per foot override")
    ap.add_argument(
        "--crop",
        action="store_true",
        help="clip to the plan itself, leaving out the title block",
    )
    args = ap.parse_args()
    for p in args.pdfs:
        print(os.path.basename(p))
        overlay(p, args.out, args.dpi, args.scale, crop=args.crop)


if __name__ == "__main__":
    main()
