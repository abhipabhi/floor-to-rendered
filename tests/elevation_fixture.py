"""Draw an elevation whose levels we chose.

The published set contains no elevation or section of the building, so the
reader would otherwise be unverifiable. This draws one the way the reference
sheets do it — a level line with a tag against it, written as ``±0.00`` at the
datum and ``+3.60`` above — and the test then checks the reader recovers the
storey heights that were drawn.

Deliberately drawn at a *different* scale from the plan fixture, because the
one thing this reader must not do is inherit the plan's scale.
"""

from __future__ import annotations

import fitz

#: points per metre on this sheet — nothing like the plan fixture's 20
PT_PER_M = 26.0

#: the levels the sheet states, in metres above datum, bottom up
LEVELS_M = [0.00, 3.60, 6.80]
LABELS = ["GROUND FLOOR LEVEL", "FIRST FLOOR LEVEL", "ROOF LEVEL"]

ORIGIN = (70.0, 330.0)  # sheet position of the ±0.00 line
WIDTH_M = 8.0


def _fmt(v: float) -> str:
    return "±0.00" if abs(v) < 1e-6 else f"+{v:.2f}"


def write(path: str, metric: bool = True) -> str:
    doc = fitz.open()
    page = doc.new_page(width=360, height=420)
    ox, oy = ORIGIN
    w = WIDTH_M * PT_PER_M

    top = oy - LEVELS_M[-1] * PT_PER_M
    # the building outline, so the sheet is an elevation and not just tags
    page.draw_rect(fitz.Rect(ox, top, ox + w, oy), color=(0, 0, 0), width=0.8)
    # parapet and its coping
    page.draw_rect(fitz.Rect(ox - 2, top - 0.9 * PT_PER_M, ox + w + 2, top),
                   color=(0, 0, 0), width=0.7)
    page.draw_rect(fitz.Rect(ox - 4, top - 1.05 * PT_PER_M, ox + w + 4,
                             top - 0.9 * PT_PER_M), color=(0, 0, 0), width=0.7)

    for value, label in zip(LEVELS_M, LABELS):
        y = oy - value * PT_PER_M
        page.draw_line(fitz.Point(ox - 26, y), fitz.Point(ox + w + 34, y),
                       color=(0, 0, 0), width=0.4)
        page.insert_text((ox + w + 38, y - 2), label, fontsize=5)
        page.insert_text((ox + w + 38, y + 6), _fmt(value), fontsize=6)

    # windows and a door, drawn with frames and sills the way an elevation is,
    # so the sheet carries the linework a real one would
    for storey, sill in ((0, 1.10), (1, 4.70)):
        for i in range(3):
            x = ox + (0.8 + i * 2.4) * PT_PER_M
            y1 = oy - sill * PT_PER_M
            y0 = y1 - 1.35 * PT_PER_M
            page.draw_rect(fitz.Rect(x, y0, x + 1.5 * PT_PER_M, y1),
                           color=(0, 0, 1), width=0.6)
            page.draw_rect(fitz.Rect(x + 2, y0 + 2, x + 1.5 * PT_PER_M - 2, y1 - 2),
                           color=(0, 0, 1), width=0.4)
            page.draw_line(fitz.Point(x - 3, y1 + 2),
                           fitz.Point(x + 1.5 * PT_PER_M + 3, y1 + 2),
                           color=(0, 0, 0), width=0.5)
    door_x = ox + 3.2 * PT_PER_M
    page.draw_rect(fitz.Rect(door_x, oy - 2.1 * PT_PER_M,
                             door_x + 1.1 * PT_PER_M, oy), color=(0, 0, 0), width=0.7)
    # a string course at the first floor line, and the ground
    page.draw_rect(fitz.Rect(ox - 3, oy - 3.75 * PT_PER_M, ox + w + 3,
                             oy - 3.55 * PT_PER_M), color=(0, 0, 0), width=0.5)
    page.draw_line(fitz.Point(ox - 40, oy), fitz.Point(ox + w + 30, oy),
                   color=(0, 0, 0), width=1.4)

    page.insert_text((ox, oy + 42), "FRONT ELEVATION", fontsize=9)
    page.insert_text((ox, oy + 54), "SCALE 1:100", fontsize=7)
    if metric:
        page.insert_text((ox, oy + 66), "ALL LEVELS IN m", fontsize=7)

    doc.save(path)
    doc.close()
    return path


#: what a correct read must produce
EXPECTED_PT_PER_FT = PT_PER_M * 0.3048
EXPECTED_STOREY_M = [
    round(b - a, 4) for a, b in zip(LEVELS_M, LEVELS_M[1:])
]  # [3.60, 3.20]
