"""Build a metric floor-plan PDF whose true dimensions we chose.

The example set is entirely imperial, so metric support would otherwise be
unverifiable. Drawing a sheet with known dimensions lets the whole pipeline —
unit detection, room-label calibration, wall extraction — be checked against an
answer that is not in question.

This is a *drawn* PDF carrying real vector linework, not a stub: the extractor
reads it through :func:`app.pdfvec.load_sheet` exactly as it reads a CAD sheet.
It is deliberately a plausible little building rather than a single box —
calibration works off the spread of wall thicknesses and room labels, so a
plan with one room and one wall thickness is not a test of anything.
"""

from __future__ import annotations

import fitz

#: how the sheet is plotted, points to the metre
PT_PER_M = 20.0

EXTERNAL = (8.00, 15.00)  # metres, outside face to outside face
EXT_WALL = 0.25
PARTITION = 0.15
ORIGIN = (60.0, 60.0)  # top-left of the building on the page, in points

#: name → (width, height) in metres, as designed and as labelled on the sheet
ROOMS = {
    "LIVING": (3.60, 5.00),
    "KITCHEN": (3.75, 5.00),
    "BEDROOM": (7.50, 4.50),
    "STUDY": (7.50, 4.70),
}


def _m(v: float) -> float:
    return v * PT_PER_M


def write(path: str) -> str:
    doc = fitz.open()
    page = doc.new_page(width=320, height=470)
    ox, oy = ORIGIN
    w, h = _m(EXTERNAL[0]), _m(EXTERNAL[1])
    t, pt_ = _m(EXT_WALL), _m(PARTITION)

    def wall(x0: float, y0: float, x1: float, y1: float) -> None:
        """A wall the way CAD draws one: a closed rect, so both faces exist."""
        page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=0.7)

    # envelope
    wall(ox, oy, ox + w, oy + t)                 # north
    wall(ox, oy + h - t, ox + w, oy + h)         # south
    wall(ox, oy, ox + t, oy + h)                 # west
    wall(ox + w - t, oy, ox + w, oy + h)         # east

    ix0, iy0 = ox + t, oy + t                    # interior top-left
    ix1 = ox + w - t

    y_a = iy0 + _m(ROOMS["LIVING"][1])           # below the top band
    y_b = y_a + pt_ + _m(ROOMS["BEDROOM"][1])
    wall(ix0, y_a, ix1, y_a + pt_)               # top band / bedroom
    wall(ix0, y_b, ix1, y_b + pt_)               # bedroom / study
    x_a = ix0 + _m(ROOMS["LIVING"][0])           # living / kitchen
    wall(x_a, iy0, x_a + pt_, y_a)

    def label(name: str, cx: float, cy: float) -> None:
        wm, hm = ROOMS[name]
        page.insert_text((cx - 16, cy), name, fontsize=6)
        page.insert_text((cx - 16, cy + 8), f"{wm:.2f} x {hm:.2f}", fontsize=6)

    label("LIVING", (ix0 + x_a) / 2, (iy0 + y_a) / 2)
    label("KITCHEN", (x_a + pt_ + ix1) / 2, (iy0 + y_a) / 2)
    label("BEDROOM", (ix0 + ix1) / 2, (y_a + pt_ + y_b) / 2)
    label("STUDY", (ix0 + ix1) / 2, (y_b + pt_ + oy + h - t) / 2)

    # the marks that make the sheet detectably metric
    page.insert_text((ox, oy + h + 22), "GROUND FLOOR PLAN", fontsize=9)
    page.insert_text((ox, oy + h + 34), "SCALE 1:100", fontsize=7)
    page.insert_text((ox, oy + h + 44), "PLOT SIZE 8.00m x 15.00m", fontsize=7)

    doc.save(path)
    doc.close()
    return path


#: what a correct read must produce
EXPECTED_PT_PER_FT = PT_PER_M * 0.3048
EXPECTED_EXTERNAL_FT = (EXTERNAL[0] / 0.3048, EXTERNAL[1] / 0.3048)
