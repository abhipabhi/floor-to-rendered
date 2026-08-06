"""Pure geometry: intervals, collinear runs, wall bands, raster flood fill.

No PDF knowledge, no units, no I/O — everything here is points and numbers, so
it can be reasoned about and tested on its own.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

Interval = tuple[float, float]


# --------------------------------------------------------------------------- #
# intervals
# --------------------------------------------------------------------------- #
def union(ivs: list[Interval], join_gap: float = 0.0) -> list[Interval]:
    """Merge overlapping intervals, bridging gaps up to ``join_gap``."""
    if not ivs:
        return []
    ivs = sorted((min(a, b), max(a, b)) for a, b in ivs)
    out = [list(ivs[0])]
    for a, b in ivs[1:]:
        if a <= out[-1][1] + join_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def intersect(xs: list[Interval], ys: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    i = j = 0
    while i < len(xs) and j < len(ys):
        a = max(xs[i][0], ys[j][0])
        b = min(xs[i][1], ys[j][1])
        if b > a:
            out.append((a, b))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(xs: list[Interval], ys: list[Interval]) -> list[Interval]:
    """xs minus ys."""
    out: list[Interval] = []
    for a, b in xs:
        cuts = [(max(a, c), min(b, d)) for c, d in ys]
        cuts = sorted((c, d) for c, d in cuts if d > c)
        pos = a
        for c, d in cuts:
            if c > pos:
                out.append((pos, c))
            pos = max(pos, d)
        if pos < b:
            out.append((pos, b))
    return out


def total(ivs: list[Interval]) -> float:
    return sum(b - a for a, b in ivs)


# --------------------------------------------------------------------------- #
# collinear runs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Run:
    """A maximal collinear stretch of drawn line on one axis.

    ``axis`` is ``h`` (constant y) or ``v`` (constant x); ``pos`` is that
    constant coordinate and ``a``/``b`` the extent along the other one. ``pen``
    is the quantised stroke width: the two faces of a wall are always drawn with
    the same pen, and dimension lines with a different one, which is what keeps
    a dimension line from pairing with the wall face it runs beside.
    """

    axis: str
    pos: float
    a: float
    b: float
    color: str = "black"
    pen: float = 0.0

    @property
    def length(self) -> float:
        return self.b - self.a


def build_runs(
    segs,
    axis: str,
    colors: tuple[str, ...] = ("black",),
    pos_tol: float = 0.4,
    join_gap: float = 1.2,
    min_len: float = 1.0,
    exclude_dashed: bool = True,
) -> list[Run]:
    """Group axis-aligned segments into merged collinear runs.

    ``join_gap`` bridges the hairline breaks CAD leaves at path boundaries but
    stays far below any real opening, so doorways survive as gaps.
    """
    buckets: dict[tuple[float, int], list[tuple[float, float, float]]] = {}
    for s in segs:
        if s.color not in colors:
            continue
        if exclude_dashed and s.dashed:
            continue
        if axis == "h":
            if not s.is_h:
                continue
            pos = (s.y0 + s.y1) / 2
            a, b = sorted((s.x0, s.x1))
        else:
            if not s.is_v:
                continue
            pos = (s.x0 + s.x1) / 2
            a, b = sorted((s.y0, s.y1))
        if b - a <= 0:
            continue
        key = (pen_class(s.width), int(round(pos / pos_tol)))
        buckets.setdefault(key, []).append((pos, a, b))

    # Neighbouring buckets of the same pen belong together within pos_tol.
    runs: list[Run] = []
    for pen in sorted({k[0] for k in buckets}):
        keys = sorted(k[1] for k in buckets if k[0] == pen)
        group: list[tuple[float, float, float]] = []
        prev_key: int | None = None
        for k in keys:
            if prev_key is not None and k - prev_key > 1:
                runs += _flush(group, axis, pen, join_gap, min_len)
                group = []
            group += buckets[(pen, k)]
            prev_key = k
        runs += _flush(group, axis, pen, join_gap, min_len)
    return runs


def pen_class(width: float) -> float:
    """Quantise a stroke width. CAD line weights are discrete, not continuous."""
    return round(float(width or 0.0), 2)


def _flush(group, axis: str, pen: float, join_gap: float, min_len: float) -> list[Run]:
    if not group:
        return []
    pos = sum(g[0] for g in group) / len(group)
    merged = union([(a, b) for _, a, b in group], join_gap=join_gap)
    return [Run(axis, pos, a, b, pen=pen) for a, b in merged if b - a >= min_len]


# --------------------------------------------------------------------------- #
# wall bands
# --------------------------------------------------------------------------- #
@dataclass
class Band:
    """Two parallel faces a wall-thickness apart, over a shared extent.

    ``axis`` ``h`` means the wall runs east-west with its thickness in y.
    """

    axis: str
    lo: float  # first face coordinate
    hi: float  # second face coordinate
    a: float  # extent start along the run direction
    b: float  # extent end
    pen: float = 0.0  # the pen both faces are drawn with

    @property
    def thickness(self) -> float:
        return self.hi - self.lo

    @property
    def center(self) -> float:
        return (self.lo + self.hi) / 2

    @property
    def length(self) -> float:
        return self.b - self.a

    def rect(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) of the wall's footprint."""
        if self.axis == "h":
            return (self.a, self.lo, self.b, self.hi)
        return (self.lo, self.a, self.hi, self.b)


def pair_bands(
    runs: list[Run],
    min_t: float,
    max_t: float,
    min_overlap: float,
    split_gap: float = 0.8,
) -> list[Band]:
    """Pair parallel runs a plausible wall thickness apart.

    Each face may be broken; the band only exists where *both* faces are drawn,
    which is what makes a doorway show up later as a single unpartnered face.
    """
    if not runs:
        return []
    axis = runs[0].axis
    bands: list[Band] = []
    for pen in sorted({r.pen for r in runs}):
        by_pos: dict[float, list[Run]] = {}
        for r in runs:
            if r.pen != pen:
                continue
            by_pos.setdefault(round(r.pos, 3), []).append(r)
        positions = sorted(by_pos)
        for i, p_lo in enumerate(positions):
            for p_hi in positions[i + 1 :]:
                t = p_hi - p_lo
                if t < min_t:
                    continue
                if t > max_t:
                    break
                lo_ivs = union([(r.a, r.b) for r in by_pos[p_lo]], split_gap)
                hi_ivs = union([(r.a, r.b) for r in by_pos[p_hi]], split_gap)
                for a, b in intersect(lo_ivs, hi_ivs):
                    if b - a >= min_overlap:
                        bands.append(Band(axis, p_lo, p_hi, a, b, pen=pen))
    return _drop_nested(bands)


def _drop_nested(bands: list[Band]) -> list[Band]:
    """Remove bands wholly contained in a thicker one on the same stretch.

    A 10" wall drawn with a centre line would otherwise yield three bands.
    """
    keep: list[Band] = []
    for b in sorted(bands, key=lambda x: -x.thickness):
        nested = False
        for k in keep:
            if (
                k.lo - 0.3 <= b.lo
                and b.hi <= k.hi + 0.3
                and k.a - 0.3 <= b.a
                and b.b <= k.b + 0.3
            ):
                nested = True
                break
        if not nested:
            keep.append(b)
    return keep


def merge_collinear_bands(bands: list[Band], join_gap: float) -> list[Band]:
    """Join band pieces that share faces and are separated by a small gap."""
    groups: dict[tuple[str, float, float, float], list[Band]] = {}
    for b in bands:
        groups.setdefault((b.axis, round(b.lo, 2), round(b.hi, 2), b.pen), []).append(b)
    out: list[Band] = []
    for (axis, lo, hi, pen), items in groups.items():
        for a, b in union([(x.a, x.b) for x in items], join_gap):
            out.append(Band(axis, lo, hi, a, b, pen=pen))
    return out


# --------------------------------------------------------------------------- #
# raster helpers
# --------------------------------------------------------------------------- #
@dataclass
class Raster:
    """A binary mask over a rectangular slice of plan space."""

    x0: float
    y0: float
    cell: float
    grid: np.ndarray  # (rows, cols) bool — True = solid

    @property
    def shape(self):
        return self.grid.shape

    def to_cell(self, x: float, y: float) -> tuple[int, int]:
        return (
            int((y - self.y0) / self.cell),
            int((x - self.x0) / self.cell),
        )

    def to_xy(self, r: int, c: int) -> tuple[float, float]:
        return (self.x0 + c * self.cell, self.y0 + r * self.cell)

    def fill_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Mark a rectangle solid, rounded to the nearest cell edge.

        Rounding rather than flooring keeps the rasterised size unbiased, which
        matters because room measurements are read back off this grid; a
        rectangle thinner than a cell still gets one cell so no wall vanishes.
        """
        rows, cols = self.grid.shape
        r0 = int(round((min(y0, y1) - self.y0) / self.cell))
        r1 = int(round((max(y0, y1) - self.y0) / self.cell))
        c0 = int(round((min(x0, x1) - self.x0) / self.cell))
        c1 = int(round((max(x1, x0) - self.x0) / self.cell))
        r1 = max(r1, r0 + 1)
        c1 = max(c1, c0 + 1)
        r0, r1 = max(0, r0), min(rows, r1)
        c0, c1 = max(0, c0), min(cols, c1)
        if r1 > r0 and c1 > c0:
            self.grid[r0:r1, c0:c1] = True


def make_raster(
    x0: float, y0: float, x1: float, y1: float, cell: float
) -> Raster:
    rows = max(1, int((y1 - y0) / cell) + 2)
    cols = max(1, int((x1 - x0) / cell) + 2)
    return Raster(x0, y0, cell, np.zeros((rows, cols), dtype=bool))


@dataclass
class Regions:
    """Connected free regions of a raster, labelled in one pass.

    Row-run labelling with union-find: rooms are found once for the whole sheet
    rather than by re-flooding for every label, which is what makes extraction
    interactive rather than something you wait on.
    """

    labels: np.ndarray  # (rows, cols) int32, 0 = solid
    area: dict[int, int]  # label → cell count
    bbox: dict[int, tuple[int, int, int, int]]  # label → r0,c0,r1,c1 (inclusive)
    raster: Raster

    def at(self, x: float, y: float) -> int:
        r, c = self.raster.to_cell(x, y)
        rows, cols = self.labels.shape
        if not (0 <= r < rows and 0 <= c < cols):
            return 0
        return int(self.labels[r, c])

    def bbox_xy(self, label: int):
        if label not in self.bbox:
            return None
        r0, c0, r1, c1 = self.bbox[label]
        x0, y0 = self.raster.to_xy(r0, c0)
        x1, y1 = self.raster.to_xy(r1 + 1, c1 + 1)
        return (x0, y0, x1, y1)

    def median_extent(self, label: int):
        """Typical width and height of a region, in plan units.

        The median row width ignores the doorway recesses and wall nibs that
        would otherwise stretch a bounding box past the room it describes.
        """
        if label not in self.bbox:
            return None
        r0, c0, r1, c1 = self.bbox[label]
        sub = self.labels[r0 : r1 + 1, c0 : c1 + 1] == label
        if not sub.any():
            return None
        widths = sub.sum(axis=1)
        heights = sub.sum(axis=0)
        widths = widths[widths > 0.5 * widths.max()]
        heights = heights[heights > 0.5 * heights.max()]
        return (
            float(np.median(widths)) * self.raster.cell,
            float(np.median(heights)) * self.raster.cell,
        )


def label_regions(raster: Raster) -> Regions:
    """Label every 4-connected region of free cells."""
    grid = raster.grid
    rows, cols = grid.shape
    labels = np.zeros((rows, cols), dtype=np.int32)

    parent: list[int] = [0]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def unite(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    prev_runs: list[tuple[int, int, int]] = []  # (c0, c1, label)
    for r in range(rows):
        row = grid[r]
        free = ~row
        if not free.any():
            prev_runs = []
            continue
        idx = np.flatnonzero(np.diff(np.concatenate(([False], free, [False])).astype(np.int8)))
        runs = list(zip(idx[0::2], idx[1::2]))
        cur_runs: list[tuple[int, int, int]] = []
        for c0, c1 in runs:
            lab = 0
            for pc0, pc1, plab in prev_runs:
                if pc0 < c1 and c0 < pc1:
                    if lab == 0:
                        lab = plab
                    else:
                        unite(lab, plab)
                elif pc0 >= c1:
                    break
            if lab == 0:
                parent.append(len(parent))
                lab = len(parent) - 1
            labels[r, c0:c1] = lab
            cur_runs.append((int(c0), int(c1), lab))
        prev_runs = cur_runs

    # resolve to root labels and measure
    if len(parent) > 1:
        lut = np.array([find(i) for i in range(len(parent))], dtype=np.int32)
        labels = lut[labels]

    area: dict[int, int] = {}
    bbox: dict[int, tuple[int, int, int, int]] = {}
    rr, cc = np.nonzero(labels)
    if rr.size:
        vals = labels[rr, cc]
        order = np.argsort(vals, kind="stable")
        sv, sr, sc = vals[order], rr[order], cc[order]
        cuts = np.flatnonzero(np.diff(sv)) + 1
        starts = np.concatenate(([0], cuts))
        ends = np.concatenate((cuts, [sv.size]))
        for s, e in zip(starts.tolist(), ends.tolist()):
            v = int(sv[s])
            area[v] = e - s
            bbox[v] = (
                int(sr[s:e].min()),
                int(sc[s:e].min()),
                int(sr[s:e].max()),
                int(sc[s:e].max()),
            )
    return Regions(labels=labels, area=area, bbox=bbox, raster=raster)


def flood(raster: Raster, x: float, y: float, limit: int | None = None):
    """4-connected fill of free cells from a seed. Returns (mask, area_cells).

    Kept for one-off queries and tests; :func:`label_regions` is what the
    extractor uses.
    """
    rows, cols = raster.grid.shape
    r, c = raster.to_cell(x, y)
    if not (0 <= r < rows and 0 <= c < cols) or raster.grid[r, c]:
        return None, 0
    seen = np.zeros_like(raster.grid)
    seen[r, c] = True
    q = deque([(r, c)])
    n = 0
    while q:
        cr, cc = q.popleft()
        n += 1
        if limit and n > limit:
            break
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < rows and 0 <= nc < cols and not seen[nr, nc]:
                if not raster.grid[nr, nc]:
                    seen[nr, nc] = True
                    q.append((nr, nc))
    return seen, n


def mask_bbox(mask: np.ndarray, raster: Raster):
    """Plan-space bounding box of a boolean mask, or None if empty."""
    rs, cs = np.nonzero(mask)
    if len(rs) == 0:
        return None
    x0, y0 = raster.to_xy(rs.min(), cs.min())
    x1, y1 = raster.to_xy(rs.max() + 1, cs.max() + 1)
    return (x0, y0, x1, y1)


def decompose_rects(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Greedy decomposition of a boolean mask into disjoint rectangles.

    Returns ``(r0, c0, r1, c1)`` half-open cell rectangles. Disjoint matters:
    overlapping coplanar slab boxes would z-fight in any renderer.
    """
    work = mask.copy()
    rows, cols = work.shape
    out: list[tuple[int, int, int, int]] = []
    for r in range(rows):
        c = 0
        while c < cols:
            if not work[r, c]:
                c += 1
                continue
            c1 = c
            while c1 < cols and work[r, c1]:
                c1 += 1
            r1 = r + 1
            while r1 < rows and work[r1, c:c1].all():
                r1 += 1
            out.append((r, c, r1, c1))
            work[r:r1, c:c1] = False
            c = c1
    return out
