"""What the walls of a storey enclose.

Three things need the same answer and must not disagree: the floor slab, the
parapet ring, and which walls are external. All three come from one rasterised
outline here.
"""

from __future__ import annotations

import numpy as np

from . import geom
from .models import Column, Wall

CELL_FT = 0.25
# A car port opening or an open lobby front is routinely 10–12 ft wide and the
# floor plate still runs through it, so the seal has to be at least that.
SEAL_FT = 12.0


def _shift_or(m: np.ndarray, r: int, axis: int) -> np.ndarray:
    out = m.copy()
    for k in range(1, r + 1):
        if axis == 0:
            out[k:] |= m[:-k]
            out[:-k] |= m[k:]
        else:
            out[:, k:] |= m[:, :-k]
            out[:, :-k] |= m[:, k:]
    return out


def dilate(m: np.ndarray, r: int) -> np.ndarray:
    return _shift_or(_shift_or(m, r, 0), r, 1) if r > 0 else m


def erode(m: np.ndarray, r: int) -> np.ndarray:
    return ~dilate(~m, r) if r > 0 else m


def wall_raster(
    walls: list[Wall],
    cell_ft: float = CELL_FT,
    seal_ft: float = SEAL_FT,
    columns: list[Column] | None = None,
) -> geom.Raster | None:
    """Walls and columns burnt into a grid, with a margin wider than the closing.

    Columns count as building: at a corner where the drawing stops both walls
    short and lets the column hold the corner, leaving it out puts a notch in
    the slab and a step in the parapet above it.

    The margin matters too: if closing can reach the edge of the grid there is no
    free border left to flood from, and the whole sheet reads as enclosed.
    """
    boxes = [(w.x0, w.y0, w.x1, w.y1) for w in walls]
    boxes += [(c.x0, c.y0, c.x1, c.y1) for c in (columns or [])]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    pad = seal_ft / 2 + 4 * cell_ft
    r = geom.make_raster(x0 - pad, y0 - pad, x1 + pad, y1 + pad, cell_ft)
    for b in boxes:
        r.fill_rect(*b)
    return r


def enclosed_mask(
    raster: geom.Raster,
    seal_ft: float = SEAL_FT,
    holes: list[tuple[float, float, float, float]] | None = None,
) -> np.ndarray:
    """Walls plus everything they enclose, less anything punched through.

    A car port open to the street, or an entrance lobby, is still part of the
    floor plate, so gaps narrower than ``seal_ft`` are closed before deciding
    what is inside. The closing is used only to make that decision — the walls
    themselves are never moved.

    ``holes`` are the places there is deliberately no floor: a stairwell, a
    duct, a double-height void. They are cut *after* the enclosure decision,
    because a shaft is inside the building — it just has no slab over it.
    """
    r = max(1, int(round(seal_ft / 2 / raster.cell)))
    closed = erode(dilate(raster.grid, r), r) | raster.grid

    # label_regions labels the *free* cells, so it takes the solid mask as-is
    regions = geom.label_regions(
        geom.Raster(raster.x0, raster.y0, raster.cell, closed)
    )
    border = set(regions.labels[0].tolist()) | set(regions.labels[-1].tolist())
    border |= set(regions.labels[:, 0].tolist()) | set(regions.labels[:, -1].tolist())
    border.discard(0)
    inside = closed.copy()
    if regions.area:
        inside |= ~np.isin(regions.labels, list(border)) & ~closed
    for h in holes or []:
        cut = geom.Raster(
            raster.x0, raster.y0, raster.cell, np.zeros_like(raster.grid)
        )
        cut.fill_rect(*h)
        inside &= ~cut.grid
    return inside


def _to_rects(mask: np.ndarray, raster: geom.Raster):
    out = []
    for r0, c0, r1, c1 in geom.decompose_rects(mask):
        ax, ay = raster.to_xy(r0, c0)
        bx, by = raster.to_xy(r1, c1)
        out.append((ax, ay, bx, by))
    return out


def _wall_coordinates(walls: list[Wall], raster: geom.Raster, columns=None, holes=None):
    """The x and y lines the building is actually drawn on.

    Decomposing the footprint on *these* lines rather than on the raster grid is
    what keeps a slab edge exactly on the wall face below it, instead of up to
    one cell away with a staircase of slivers along every step in the plan.
    """
    rows, cols = raster.grid.shape
    x_end = raster.x0 + cols * raster.cell
    y_end = raster.y0 + rows * raster.cell

    def axis_lines(values, lo: float, hi: float) -> list[float]:
        out: list[float] = []
        for v in sorted({round(v, 4) for v in values} | {lo, hi}):
            if v < lo or v > hi:
                continue
            if not out or v - out[-1] > 1e-3:
                out.append(v)
        return out

    items = list(walls) + list(columns or [])
    xv = [v for w in items for v in (w.x0, w.x1)]
    yv = [v for w in items for v in (w.y0, w.y1)]
    # a hole's own edges are cut lines too, or its boundary lands on the raster
    # grid and gets the sliver staircase that snapping exists to avoid
    for h in holes or []:
        xv += [h[0], h[2]]
        yv += [h[1], h[3]]
    return axis_lines(xv, raster.x0, x_end), axis_lines(yv, raster.y0, y_end)


def _snap_to_walls(inside, raster: geom.Raster, walls: list[Wall], columns=None, holes=None):
    xs, ys = _wall_coordinates(walls, raster, columns, holes)
    if len(xs) < 2 or len(ys) < 2:
        return _to_rects(inside, raster)

    rows, cols = inside.shape
    grid = np.zeros((len(ys) - 1, len(xs) - 1), dtype=bool)
    for j in range(len(ys) - 1):
        r = int(((ys[j] + ys[j + 1]) / 2 - raster.y0) / raster.cell)
        if not (0 <= r < rows):
            continue
        for i in range(len(xs) - 1):
            c = int(((xs[i] + xs[i + 1]) / 2 - raster.x0) / raster.cell)
            if 0 <= c < cols and inside[r, c]:
                grid[j, i] = True

    return [
        (xs[c0], ys[r0], xs[c1], ys[r1])
        for r0, c0, r1, c1 in geom.decompose_rects(grid)
    ]


def footprint_rects(
    walls: list[Wall],
    cell_ft: float = CELL_FT,
    seal_ft: float = SEAL_FT,
    columns: list[Column] | None = None,
    holes: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[float, float, float, float]]:
    """The slab outline as disjoint rectangles, with edges on the walls.

    Disjoint because two coplanar overlapping slabs z-fight; on the walls
    because a slab that misses its own wall by an inch is visible. ``holes``
    are cut out of the plate — a stairwell has no floor over it.
    """
    raster = wall_raster(walls, cell_ft, seal_ft, columns)
    if raster is None:
        return []
    mask = enclosed_mask(raster, seal_ft, holes)
    return _snap_to_walls(mask, raster, walls, columns, holes)


def outline_edges(rects, tol: float = 1e-4):
    """The boundary of a set of disjoint rectangles.

    Yields ``(axis, pos, a, b, inward)`` where ``inward`` is +1 or -1 along the
    axis normal — enough to grow a parapet inwards from every free edge.
    """
    out = []
    for axis, lo_key, hi_key in (("h", 1, 3), ("v", 0, 2)):
        for r in rects:
            span = (r[0], r[2]) if axis == "h" else (r[1], r[3])
            for side, pos, inward in (("lo", r[lo_key], 1), ("hi", r[hi_key], -1)):
                touching = []
                for other in rects:
                    if other is r:
                        continue
                    face = other[hi_key] if side == "lo" else other[lo_key]
                    if abs(face - pos) > tol:
                        continue
                    o = (other[0], other[2]) if axis == "h" else (other[1], other[3])
                    touching.append(o)
                for a, b in geom.subtract([span], geom.union(touching)):
                    if b - a > tol:
                        out.append((axis, pos, a, b, inward))
    return out


def ring_rects(
    walls: list[Wall],
    thickness_ft: float,
    cell_ft: float = CELL_FT,
    seal_ft: float = SEAL_FT,
    rects=None,
    columns: list[Column] | None = None,
    outward: bool = False,
) -> list[tuple[float, float, float, float]]:
    """A closed band along the outline — the parapet, or a projecting band.

    Built from the footprint's own boundary rather than from the external walls,
    so the ring always closes: along a balcony edge, across a car port, anywhere
    there is no wall to follow. ``outward`` grows the band away from the
    building instead of into it, which is what a floor band or a coping does.
    """
    if rects is None:
        rects = footprint_rects(walls, cell_ft, seal_ft, columns)
    if not rects:
        return []
    sign = -1 if outward else 1
    out = []
    for axis, pos, a, b, inward in outline_edges(rects):
        grow = thickness_ft * sign * (1 if inward > 0 else -1)
        lo, hi = sorted((pos, pos + grow))
        if axis == "h":
            out.append((a, lo, b, hi))
        else:
            out.append((lo, a, hi, b))
    return out


def mark_exterior(
    walls: list[Wall],
    cell_ft: float = CELL_FT,
    seal_ft: float = SEAL_FT,
    columns: list[Column] | None = None,
) -> None:
    """Set ``wall.exterior`` and ``wall.outside`` from the outline.

    Both faces are probed. ``exterior`` says whether either one sees weather;
    ``outside`` says *which*, because everything that hangs on a facade — a
    sunshade, a reveal, a per-face material — needs the side, not the fact.
    """
    raster = wall_raster(walls, cell_ft, seal_ft, columns)
    if raster is None:
        return
    inside = enclosed_mask(raster, seal_ft)
    rows, cols = inside.shape
    probe = 0.4

    def outside_at(x: float, y: float) -> bool:
        r, c = raster.to_cell(x, y)
        if not (0 <= r < rows and 0 <= c < cols):
            return True
        return not inside[r, c]

    for w in walls:
        if w.axis == "h":
            samples = [(u, w.y0 - probe) for u in _thirds(w.x0, w.x1)] + [
                (u, w.y1 + probe) for u in _thirds(w.x0, w.x1)
            ]
            sides = [
                any(outside_at(x, y) for x, y in samples[:3]),
                any(outside_at(x, y) for x, y in samples[3:]),
            ]
        else:
            samples = [(w.x0 - probe, v) for v in _thirds(w.y0, w.y1)] + [
                (w.x1 + probe, v) for v in _thirds(w.y0, w.y1)
            ]
            sides = [
                any(outside_at(x, y) for x, y in samples[:3]),
                any(outside_at(x, y) for x, y in samples[3:]),
            ]
        lo_out, hi_out = sides
        w.exterior = lo_out or hi_out
        w.outside = (
            "both" if lo_out and hi_out else "lo" if lo_out else "hi" if hi_out else None
        )


def _thirds(a: float, b: float) -> list[float]:
    return [a + (b - a) * f for f in (0.2, 0.5, 0.8)]
