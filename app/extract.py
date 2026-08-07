"""Floor plan sheet → walls, openings, columns and rooms in plan feet.

The whole extractor is deterministic: it reads the vector paths that the CAD
package plotted and nothing else. Where it has to make a judgement call it
records why, and every result is editable afterwards.

Conventions this relies on, all verifiable in the drawing itself:

* a wall is drawn as two parallel face lines a wall-thickness apart;
* a doorway leaves **one** face drawn across the opening and caps it with a
  jamb tick, so the band stops while a single line continues;
* glazing is drawn in a different pen (blue) inside the wall band;
* RCC columns are solid red rectangles.

Where a drawing set breaks a convention the user fixes it in the UI — the
extractor never invents geometry to cover a gap.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from . import geom
from . import stairs as stairs_mod
from .footprint import mark_exterior
from .geom import Band, Raster, Run
from .models import Column, Opening, PlanExtract, Room, ScaleInfo, Wall
from .pdfvec import Fill, Sheet, Word
from .units import Units, detect_units, find_room_dim, parse_room_dim

# --------------------------------------------------------------------------- #
# tuning — all in feet unless the name says px
# --------------------------------------------------------------------------- #
MIN_WALL_T_FT = 0.15  # 1.8"
MAX_WALL_T_FT = 1.5  # 18"
MIN_WALL_LEN_FT = 0.8
MIN_WALL_ASPECT = 1.5  # a wall is longer than it is thick; a stub is not a wall
MIN_DOOR_FT = 2.0  # nothing narrower than 2 ft is a door anyone walks through
MAX_DOOR_FT = 5.0
SHAFT_WORDS = {"DUCT", "SHAFT", "VOID"}  # you do not walk into these
MIN_WINDOW_FT = 1.0
MIN_RAILING_FT = 2.0
RAILING_MIN_T_FT = 0.25
MAX_RAILING_T_FT = 0.5  # thicker than 6" and it is glazing in a wall, not a guard
ROOM_KEYWORDS = {
    "BED", "ROOM", "BEDROOM", "TOILET", "BATH", "KITCHEN", "HALL", "LOBBY",
    "BALCONY", "PARKING", "DUCT", "LOFT", "VOID", "STAIR", "STAIRCASE",
    "PORCH", "STORE", "DINING", "DRAWING", "POOJA", "PUJA", "WASH", "UTILITY",
    "PASSAGE", "VERANDAH", "VERANDA", "GARAGE", "SHOP", "OFFICE", "STUDY",
    "LIVING", "MASTER", "GUEST", "SIT", "OUT", "TERRACE", "WC", "AREA",
}
NON_ROOM_WORDS = {"ROAD", "PLAN", "FLOOR", "GROUND", "FIRST", "SECOND", "TITLE"}


@dataclass
class LabelBlock:
    """A clustered text label: the words, their box, and any A×B dimension."""

    words: list[Word]
    x0: float
    y0: float
    x1: float
    y1: float
    name: str
    dim: tuple[float, float] | None

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Diagnostics:
    notes: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.notes.append(msg)


# --------------------------------------------------------------------------- #
# text clustering
# --------------------------------------------------------------------------- #
def cluster_words(
    words: list[Word], gap: float = 4.0, units: Units = "imperial"
) -> list[LabelBlock]:
    """Group text tokens whose boxes nearly touch into one label block."""
    items = [w for w in words if w.text.strip()]
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def near(a: Word, b: Word) -> bool:
        dx = max(a.x0 - b.x1, b.x0 - a.x1)
        dy = max(a.y0 - b.y1, b.y0 - a.y1)
        return dx <= gap and dy <= gap

    for i in range(n):
        for j in range(i + 1, n):
            if abs(items[i].cx - items[j].cx) > 90 or abs(items[i].cy - items[j].cy) > 60:
                continue
            if near(items[i], items[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[Word]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])

    blocks: list[LabelBlock] = []
    for ws in groups.values():
        ws = sorted(ws, key=lambda w: (round(w.cy, 1), w.cx))
        dim = None
        names: list[str] = []
        for w in ws:
            rd = parse_room_dim(w.text, units)
            if rd and dim is None:
                dim = (rd.a, rd.b)
            elif w.text.isalpha() and w.text.upper() != "X":
                # the X of "4.00 x 4.20" is a separator, not part of the name
                names.append(w.text.upper())
        if dim is None:
            # a metric sheet writes "4.00 x 4.20", which arrives as three
            # separate words — the dimension only exists across the whole block
            rd = find_room_dim(" ".join(w.text for w in ws), units)
            if rd:
                dim = (rd.a, rd.b)
        blocks.append(
            LabelBlock(
                words=ws,
                x0=min(w.x0 for w in ws),
                y0=min(w.y0 for w in ws),
                x1=max(w.x1 for w in ws),
                y1=max(w.y1 for w in ws),
                name=" ".join(names),
                dim=dim,
            )
        )
    return blocks


def room_labels(blocks: list[LabelBlock]) -> list[LabelBlock]:
    """Label blocks that name a room."""
    out = []
    for b in blocks:
        words = set(b.name.split())
        if not words:
            continue
        if words & NON_ROOM_WORDS and not (words & ROOM_KEYWORDS):
            continue
        if (words & ROOM_KEYWORDS) or b.dim:
            out.append(b)
    return out


# --------------------------------------------------------------------------- #
# building region
# --------------------------------------------------------------------------- #
def column_fills(sheet: Sheet, max_side: float = 60.0) -> list[Fill]:
    """Solid red rectangles — the RCC columns."""
    out = []
    for f in sheet.fills:
        if f.color != "red" or not f.rectangular:
            continue
        w, h = f.w, f.h
        if w < 1.0 or h < 1.0 or w > max_side or h > max_side:
            continue
        if max(w, h) / max(min(w, h), 0.01) > 4.0:  # ramp hatch strips
            continue
        out.append(f)
    return _dedupe_fills(out)


def _dedupe_fills(fills: list[Fill]) -> list[Fill]:
    kept: list[Fill] = []
    for f in fills:
        if any(
            abs(f.cx - k.cx) < 1.5 and abs(f.cy - k.cy) < 1.5 for k in kept
        ):
            continue
        kept.append(f)
    return kept


def building_region(
    sheet: Sheet, labels: list[LabelBlock], cols: list[Fill], diag: Diagnostics
) -> tuple[float, float, float, float]:
    """The part of the sheet that holds the plan, excluding the title block.

    Columns are the strongest evidence (they only ever sit in the building), the
    room labels the next; both are padded generously because walls, balconies
    and projections extend past them.
    """
    if len(cols) >= 3:
        x0 = min(c.x0 for c in cols)
        y0 = min(c.y0 for c in cols)
        x1 = max(c.x1 for c in cols)
        y1 = max(c.y1 for c in cols)
        pad = 0.25 * max(x1 - x0, y1 - y0)
        diag.add(f"building region from {len(cols)} columns")
    elif labels:
        x0 = min(b.x0 for b in labels)
        y0 = min(b.y0 for b in labels)
        x1 = max(b.x1 for b in labels)
        y1 = max(b.y1 for b in labels)
        pad = 0.5 * max(x1 - x0, y1 - y0)
        diag.add(f"building region from {len(labels)} room labels")
    else:
        xs = [v for s in sheet.segs for v in (s.x0, s.x1)]
        ys = [v for s in sheet.segs for v in (s.y0, s.y1)]
        diag.add("building region fell back to the whole sheet")
        return (min(xs), min(ys), max(xs), max(ys))
    return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)


def _in_region(x: float, y: float, region) -> bool:
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


# --------------------------------------------------------------------------- #
# bands
# --------------------------------------------------------------------------- #
def _clip_runs(runs: list[Run], region) -> list[Run]:
    lo_i, hi_i = (1, 3) if runs and runs[0].axis == "h" else (0, 2)
    out = []
    for r in runs:
        pos_lo, pos_hi = region[lo_i], region[hi_i]
        if not (pos_lo <= r.pos <= pos_hi):
            continue
        ext_lo = region[0] if r.axis == "h" else region[1]
        ext_hi = region[2] if r.axis == "h" else region[3]
        a, b = max(r.a, ext_lo), min(r.b, ext_hi)
        if b - a > 0.5:
            out.append(Run(r.axis, r.pos, a, b, r.color, r.pen))
    return out


def spacing_histogram(
    runs: list[Run], lo: float = 1.5, hi: float = 30.0
) -> dict[float, int]:
    """How often each parallel-line spacing occurs, in 0.25 pt bins.

    CAD output is exact, so every wall of one thickness lands in one bin and the
    wall thicknesses stand out as spikes.
    """
    by_key: dict[tuple[float, float], list[Run]] = {}
    for r in runs:
        by_key.setdefault((r.pen, round(r.pos, 2)), []).append(r)
    hist: dict[float, int] = {}
    pens = {k[0] for k in by_key}
    for pen in pens:
        positions = sorted(k[1] for k in by_key if k[0] == pen)
        for i, p in enumerate(positions):
            for q in positions[i + 1 :]:
                d = q - p
                if d < lo:
                    continue
                if d > hi:
                    break
                a = geom.union([(r.a, r.b) for r in by_key[(pen, p)]], 1.0)
                b = geom.union([(r.a, r.b) for r in by_key[(pen, q)]], 1.0)
                if geom.total(geom.intersect(a, b)) >= 8.0:
                    k = round(round(d / 0.25) * 0.25, 2)
                    hist[k] = hist.get(k, 0) + 1
    return hist


def modal_spacing(runs: list[Run], **kw) -> float | None:
    hist = spacing_histogram(runs, **kw)
    if not hist:
        return None
    return max(hist.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def significant_spacings(hist: dict[float, int], frac: float = 0.15) -> list[float]:
    """The spacings common enough to be wall thicknesses rather than noise."""
    if not hist:
        return []
    peak = max(hist.values())
    return sorted(d for d, n in hist.items() if n >= max(2, frac * peak))


def wall_spacings(hist: dict[float, int], max_ratio: float = 3.0) -> list[float]:
    """The one or two spacings that are actually wall thicknesses.

    A drawing repeats its wall thicknesses hundreds of times, so they are the
    tallest spikes by a wide margin; sums of two walls, furniture and glazing
    detail all trail well behind. Taking only the top two spikes — and only when
    they are within a factor of three of each other, as a partition and an
    external wall always are — keeps the pass-A wall set clean without assuming
    any particular thickness in inches.
    """
    if not hist:
        return []
    ranked = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))
    peak = ranked[0][1]
    out = [ranked[0][0]]
    for d, n in ranked[1:]:
        if n < 0.4 * peak:
            break
        ratio = max(d, out[0]) / min(d, out[0])
        if ratio <= max_ratio and all(abs(d - k) > 0.6 for k in out):
            out.append(d)
        if len(out) == 2:
            break
    return sorted(out)


def reject_stacks(
    bands: list[Band], runs: list[Run], tol: float = 0.15
) -> tuple[list[Band], list[Band]]:
    """Split bands into walls and rungs of an evenly spaced ladder.

    Stair treads, hatching and ramp strips are parallel lines at a constant
    pitch that looks exactly like a wall thickness. A real wall has nothing at
    the same pitch immediately beyond either face.

    The rungs are *returned* rather than dropped, because a stair is the most
    conspicuous thing missing from the model and this is where the drawing
    already told us exactly where it is.
    """
    by_pen: dict[float, dict[float, list[tuple[float, float]]]] = {}
    for r in runs:
        by_pen.setdefault(r.pen, {}).setdefault(round(r.pos, 2), []).append((r.a, r.b))

    def covered(pen: float, pos_target: float, slack: float, a: float, b: float) -> bool:
        span = b - a
        for p, ivs in by_pen.get(pen, {}).items():
            if abs(p - pos_target) > slack:
                continue
            ov = geom.total(geom.intersect(geom.union(ivs, 1.0), [(a, b)]))
            if ov >= 0.6 * span:
                return True
        return False

    out: list[Band] = []
    rungs: list[Band] = []
    for band in bands:
        t = band.thickness
        slack = max(0.3, tol * t)
        if any(
            covered(band.pen, target, slack, band.a, band.b)
            for target in (band.lo - t, band.hi + t)
        ):
            rungs.append(band)
        else:
            out.append(band)
    return out, rungs


def _column_targets(columns: list[Fill], axis: str) -> list[Band]:
    """Columns seen as something a wall of ``axis`` can die into.

    A corner column often holds the corner on its own, with both walls stopping
    at its faces. Expressed in the same shape as a band, it can be a target for
    the extension below without any special case there.
    """
    out = []
    for c in columns:
        if axis == "h":  # the wall runs in x, so the column is crossed in x
            out.append(Band("v", c.x0, c.x1, c.y0, c.y1))
        else:
            out.append(Band("h", c.y0, c.y1, c.x0, c.x1))
    return out


def corner_extend(
    bands: list[Band],
    runs: list[Run],
    limit: float,
    columns: list[Fill] | None = None,
    column_gap: float = 0.0,
) -> list[Band]:
    """Run each wall on to the wall it meets, where the drawing says it does.

    Where a wall tees into another, only its *exposed* face is drawn past the
    junction, so the two faces overlap by an inch or two and the band all but
    vanishes. The projecting bay a toilet sits in is built entirely of such
    junctions, and without this its side walls are missing and the bay stands
    off the facade with a slot down each side.

    An end is only run on while one of its faces is actually drawn, and only as
    far as a wall crossing it — so a site boundary drawn along the same line as
    the front wall, with nothing to meet, is left exactly where it ended.
    """
    cover: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for r in runs:
        cover.setdefault((r.pen, round(r.pos, 2)), []).append((r.a, r.b))
    for key in cover:
        cover[key] = geom.union(cover[key], 1.2)

    def drawn(pen: float, pos: float, start: float, direction: int) -> float:
        best = 0.0
        for a, b in cover.get((pen, round(pos, 2)), []):
            if direction > 0 and a - 0.3 <= start <= b:
                best = max(best, min(b - start, limit))
            elif direction < 0 and a <= start <= b + 0.3:
                best = max(best, min(start - a, limit))
        return best

    out: list[Band] = []
    for band in bands:
        a, b = band.a, band.b
        for direction in (-1, 1):
            start = b if direction > 0 else a
            reach = max(
                drawn(band.pen, band.lo, start, direction),
                drawn(band.pen, band.hi, start, direction),
            )
            if reach <= 0.05:
                continue
            best = None
            targets = [(p, 0.3) for p in bands] + [
                (p, column_gap) for p in _column_targets(columns or [], band.axis)
            ]
            for p, gap in targets:
                if p.axis == band.axis:
                    continue
                if not (p.a <= band.hi + gap and p.b >= band.lo - gap):
                    continue  # does not cross this wall
                if direction > 0:
                    if not (start - gap - 0.3 <= p.lo <= start + reach + 0.3):
                        continue
                    dist = p.hi - start
                else:
                    if not (start - reach - 0.3 <= p.hi <= start + gap + 0.3):
                        continue
                    dist = start - p.lo
                # only *into* the wall it meets: any further and this is a
                # doorway being bridged, not a junction being closed
                if (
                    dist <= 0
                    or dist > p.thickness * 1.05 + gap + 0.2
                    or dist > reach + 0.3
                ):
                    continue
                best = dist if best is None else min(best, dist)
            if best is None:
                continue
            if direction > 0:
                b = start + best
            else:
                a = start - best
        out.append(Band(band.axis, band.lo, band.hi, a, b, pen=band.pen))
    return out


def find_bands(
    runs_h: list[Run],
    runs_v: list[Run],
    min_t: float,
    max_t: float,
    min_len: float,
    diag: Diagnostics | None = None,
    only: list[float] | None = None,
    columns: list[Fill] | None = None,
    stacks: list[Band] | None = None,
) -> list[Band]:
    """Wall bands on both axes, within a thickness window in points.

    ``only`` restricts thicknesses to a short list of known spacings.
    ``stacks``, if given, collects the evenly spaced rungs that were rejected —
    the treads of a stair among them.
    """
    # Pair both axes generously first: the extension step needs to see the walls
    # a wall might meet, and those are the ones at right angles to it.
    paired = {
        "h": geom.pair_bands(runs_h, min_t, max_t, min_overlap=0.1),
        "v": geom.pair_bands(runs_v, min_t, max_t, min_overlap=0.1),
    }
    extended = corner_extend(
        paired["h"] + paired["v"],
        runs_h + runs_v,
        limit=2.0 * max_t,
        columns=columns,
        column_gap=0.6 * max_t,
    )

    bands: list[Band] = []
    dropped = 0
    stubs = 0
    for axis, runs in (("h", runs_h), ("v", runs_v)):
        cand = [c for c in extended if c.axis == axis and c.length >= min_len]
        if only:
            cand = [
                b for b in cand if min(abs(b.thickness - s) for s in only) <= 0.3
            ]
        cand = geom._drop_nested(cand)
        before = len(cand)
        # a band shorter than it is thick is a corner artefact, not a wall
        cand = [b for b in cand if b.length >= MIN_WALL_ASPECT * b.thickness]
        stubs += before - len(cand)
        kept, rungs = reject_stacks(cand, runs)
        dropped += len(rungs)
        bands += kept
        if stacks is not None:
            stacks += rungs
    if diag and dropped:
        diag.add(f"{dropped} evenly-spaced bands rejected as stairs, treads or hatch")
    if diag and stubs:
        diag.add(f"{stubs} stub bands rejected as thicker than they are long")
    return bands


# --------------------------------------------------------------------------- #
# doorways
# --------------------------------------------------------------------------- #
def _end_supported(
    host: Band, end: float, bands: list[Band], runs: list[Run], tol: float
) -> bool:
    """Is this end of a gap closed off, the way a doorway's reveal is?

    A doorway is a hole *in* a wall, so its far side has to be closed by
    something drawn: the wall picking up again, a wall crossing at right angles,
    or the jamb tick that caps the reveal. Two things get mistaken for doorways
    without this test — a site boundary drawn along the same line as the front
    wall and running on past the building, and the stretches of external wall
    where the drafter simply did not carry the inner face between columns.
    """
    for b in bands:
        if b is host:
            continue
        if (
            b.axis == host.axis
            and abs(b.lo - host.lo) <= 0.4
            and abs(b.hi - host.hi) <= 0.4
            and b.a - tol <= end <= b.b + tol
        ):
            return True
        if b.axis != host.axis:
            # a perpendicular band crosses the host between b.lo and b.hi
            if (
                b.lo - tol <= end <= b.hi + tol
                and b.a <= host.hi + tol
                and b.b >= host.lo - tol
            ):
                return True

    # a jamb: a short line drawn across the wall, closing the reveal
    thickness = host.hi - host.lo
    for r in runs:
        if r.axis == host.axis:
            continue
        if not (r.pos - tol <= end <= r.pos + tol):
            continue
        covered = min(r.b, host.hi) - max(r.a, host.lo)
        if covered >= 0.6 * thickness:
            return True
    return False


def solo_gaps(
    bands: list[Band],
    runs: list[Run],
    min_len: float,
    max_len: float,
    tol: float = 1.5,
    require_support: bool = True,
) -> list[tuple[Band, float, float]]:
    """Stretches where one face carries on alone — the doorways.

    For each band line, the interval covered by the band is subtracted from the
    interval drawn by each of its two faces; what is left, at door size, touching
    the band and closed at both ends, is an opening.
    """
    by_pos: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for r in runs:
        by_pos.setdefault((r.pen, round(r.pos, 2)), []).append((r.a, r.b))

    lines: dict[tuple[float, float, float], list[Band]] = {}
    for b in bands:
        lines.setdefault((b.pen, round(b.lo, 2), round(b.hi, 2)), []).append(b)

    # a doorway is inside the building; a site boundary drawn along the same line
    # as the front wall carries on past it, and its overshoot is not a doorway
    env_h = (
        min((b.a for b in bands if b.axis == "h"), default=0.0),
        max((b.b for b in bands if b.axis == "h"), default=0.0),
    )
    env_v = (
        min((b.a for b in bands if b.axis == "v"), default=0.0),
        max((b.b for b in bands if b.axis == "v"), default=0.0),
    )

    found: list[tuple[Band, float, float]] = []
    for (pen, lo, hi), pieces in lines.items():
        band_ivs = geom.union([(p.a, p.b) for p in pieces], 0.5)
        for face in (lo, hi):
            drawn = geom.union(by_pos.get((pen, face), []), 1.2)
            for a, b in geom.subtract(drawn, band_ivs):
                if not (min_len <= b - a <= max_len):
                    continue
                # must touch the band it belongs to
                host = None
                for p in pieces:
                    if abs(p.b - a) < 1.5 or abs(p.a - b) < 1.5:
                        host = p
                        break
                if host is None:
                    continue
                if require_support:
                    env = env_h if host.axis == "h" else env_v
                    if a < env[0] - tol or b > env[1] + tol:
                        continue
                    # the end that meets the host band is closed by the band
                    # itself; only the far end needs something to shut against
                    far = b if abs(host.b - a) < 1.5 else a
                    if not _end_supported(host, far, bands, runs, tol):
                        continue
                found.append((host, a, b))
    return found


# --------------------------------------------------------------------------- #
# rooms
# --------------------------------------------------------------------------- #
def rasterize(
    bands: list[Band], cols: list[Fill], region, cell: float
) -> Raster:
    r = geom.make_raster(region[0], region[1], region[2], region[3], cell)
    for b in bands:
        x0, y0, x1, y1 = b.rect()
        r.fill_rect(x0, y0, x1, y1)
    for c in cols:
        r.fill_rect(c.x0, c.y0, c.x1, c.y1)
    return r


@dataclass
class RoomFill:
    """A room label matched to the enclosed region it sits in."""

    label: LabelBlock
    bbox: tuple[float, float, float, float]
    extent: tuple[float, float]  # median width/height — recess-proof
    ratio: float  # filled area ÷ bounding-box area; < 1 means it leaked


def find_rooms(raster: Raster, labels: list[LabelBlock], region) -> list[RoomFill]:
    """The enclosed region each room label sits in."""
    regions = geom.label_regions(raster)
    area_limit = 0.75 * (region[2] - region[0]) * (region[3] - region[1])
    out: list[RoomFill] = []
    for lb in labels:
        lab = regions.at(lb.cx, lb.cy)
        if lab == 0:
            continue
        bbox = regions.bbox_xy(lab)
        extent = regions.median_extent(lab)
        if bbox is None or extent is None:
            continue
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if bw <= 0 or bh <= 0:
            continue
        filled = regions.area[lab] * raster.cell**2
        if filled > area_limit:
            continue
        out.append(RoomFill(lb, bbox, extent, filled / (bw * bh)))
    return out


# --------------------------------------------------------------------------- #
# scale
# --------------------------------------------------------------------------- #
def calibrate_from_rooms(rooms, diag: Diagnostics) -> ScaleInfo | None:
    """px per foot from room labels measured against the rooms they sit in.

    Each room gives two independent readings. They only agree if the label, the
    walls and the assumed orientation are all right, so disagreement is a
    rejection signal rather than something to average away.
    """
    per_room: list[tuple[float, str]] = []
    for rf in rooms:
        lb = rf.label
        if lb.dim is None or rf.ratio < 0.80:
            continue  # the fill leaked or the room is not rectangular
        w, h = rf.extent
        a, b = lb.dim
        # the label does not say which way round it is; take the reading that
        # makes the two axes agree, and reject the room if neither does
        best = min(
            [(w / a, h / b), (w / b, h / a)],
            key=lambda p: abs(p[0] - p[1]) / max(p[0], p[1]),
        )
        if abs(best[0] - best[1]) / max(best[0], best[1]) > 0.04:
            continue
        per_room.append(((best[0] + best[1]) / 2, lb.name))
    if len(per_room) < 2:
        return None

    med = statistics.median([v for v, _ in per_room])
    kept = [(v, n) for v, n in per_room if abs(v - med) / med <= 0.03]
    rejected = [n for v, n in per_room if abs(v - med) / med > 0.03]
    if len(kept) < 2:
        kept = per_room
        rejected = []
    values = [v for v, _ in kept]
    med = statistics.median(values)
    spread = 100 * (max(values) - min(values)) / med
    conf = "high" if len(kept) >= 4 and spread < 2.5 else "medium"
    diag.add(
        f"scale from {len(kept)} room labels, spread {spread:.1f}%"
        + (f"; ignored {', '.join(rejected)}" if rejected else "")
    )
    return ScaleInfo(
        px_per_ft=med,
        method="room_labels",
        confidence=conf,
        samples=len(kept),
        spread_pct=round(spread, 2),
        note=(
            f"{len(kept)} room dimension labels measured against the rooms they "
            f"sit in, agreeing to {spread:.1f}%"
        ),
    )


def calibrate_from_thickness(spacing: float | None, diag: Diagnostics) -> ScaleInfo:
    """Last resort: assume the commonest wall is a 5" partition."""
    if not spacing:
        diag.add("no parallel lines found; scale defaulted")
        return ScaleInfo(
            px_per_ft=10.0,
            method="default",
            confidence="low",
            note="nothing measurable on the sheet — calibrate manually",
        )
    px = spacing / (5.0 / 12.0)
    diag.add(f"scale assumed from {spacing:.2f}pt modal wall = 5 inches")
    return ScaleInfo(
        px_per_ft=px,
        method="wall_thickness",
        confidence="low",
        note=f'commonest wall band is {spacing:.2f}pt, assumed to be 5" — check this',
    )


# --------------------------------------------------------------------------- #
# compass
# --------------------------------------------------------------------------- #
def north_bearing(sheet: Sheet) -> float | None:
    """Compass bearing of plan +x, in degrees clockwise from north.

    Read from the N/S/E/W letters of the title-block compass rose: the vector
    from S to N is north on the sheet.
    """
    marks: dict[str, list[Word]] = {}
    for w in sheet.words:
        t = w.text.strip().upper()
        if t in ("N", "S", "E", "W"):
            marks.setdefault(t, []).append(w)
    if "N" not in marks or "S" not in marks:
        return None
    best = None
    for n in marks["N"]:
        for s in marks["S"]:
            d = math.hypot(n.cx - s.cx, n.cy - s.cy)
            if d < 3 or d > 60:
                continue
            if best is None or d < best[0]:
                best = (d, n, s)
    if best is None:
        return None
    _, n, s = best
    dx, dy = n.cx - s.cx, n.cy - s.cy
    # bearing of plan +x relative to that north vector
    north_angle = math.degrees(math.atan2(dx, -dy))  # 0 = up the sheet
    return round((90.0 - north_angle) % 360.0, 1)


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #
def extract_plan(
    sheet: Sheet,
    sheet_id: str,
    level: int,
    level_name: str,
    px_per_ft_override: float | None = None,
) -> PlanExtract:
    """Read one floor-plan sheet into plan-space geometry."""
    diag = Diagnostics()
    # Which system the sheet prints in decides how its room labels read. Getting
    # it wrong scales the building by 3.28, so it is detected from the sheet's
    # own marks and reported, never assumed.
    unit_system = detect_units(sheet.text)
    if unit_system.metric:
        diag.add(f"metric drawing — {unit_system.evidence}")
    blocks = cluster_words(sheet.words, units=unit_system.units)
    cols_px = column_fills(sheet)
    region = building_region(sheet, room_labels(blocks), cols_px, diag)
    cols_px = [c for c in cols_px if _in_region(c.cx, c.cy, region)]
    labels = [b for b in room_labels(blocks) if _in_region(b.cx, b.cy, region)]

    runs_h = _clip_runs(geom.build_runs(sheet.segs, "h"), region)
    runs_v = _clip_runs(geom.build_runs(sheet.segs, "v"), region)
    hist = spacing_histogram(runs_h + runs_v)
    sig = wall_spacings(hist)

    # -- pass A: find walls without knowing the scale at all. The thickness
    #    window comes from the drawing's own spacing spikes, so nothing here
    #    assumes a wall is 5" rather than 9".
    scale: ScaleInfo | None = None
    if px_per_ft_override:
        scale = ScaleInfo(
            px_per_ft=px_per_ft_override,
            method="manual",
            confidence="high",
            note="calibrated by hand",
        )
    else:
        if sig:
            min_t, max_t = min(sig) - 0.3, max(sig) + 0.3
            diag.add(
                "commonest parallel-line spacings, taken as the wall thicknesses: "
                + ", ".join(f"{s:.2f}pt" for s in sig)
            )
        else:
            min_t, max_t = 1.5, 25.0
        bands_a = find_bands(
            runs_h,
            runs_v,
            min_t,
            max_t,
            max(6.0, 2.0 * min_t),
            only=sig or None,
            columns=cols_px,
        )
        sealed = _seal(bands_a, runs_h + runs_v, 1.2 * min_t, 12.0 * max_t)
        raster_a = rasterize(sealed, cols_px, region, cell=max(0.2, min_t / 4))
        rooms_a = find_rooms(raster_a, labels, region)
        scale = calibrate_from_rooms(rooms_a, diag)
        if scale is None:
            scale = calibrate_from_thickness(modal_spacing(runs_h + runs_v), diag)

    px = scale.px_per_ft
    _check_round_thicknesses(sig, px, scale, diag, unit_system.units)

    # -- pass B: the real extraction, with wall sizes in real units
    stacks: list[Band] = []
    bands = find_bands(
        runs_h,
        runs_v,
        MIN_WALL_T_FT * px,
        MAX_WALL_T_FT * px,
        MIN_WALL_LEN_FT * px,
        diag,
        columns=cols_px,
        stacks=stacks,
    )
    doors = solo_gaps(
        bands, runs_h + runs_v, MIN_DOOR_FT * px, MAX_DOOR_FT * px, tol=0.2 * px
    )
    walls_px = _seal_with_doors(
        bands, doors, runs=runs_h + runs_v, max_fill=MAX_DOOR_FT * px
    )
    raster = rasterize([b for b, _ in walls_px], cols_px, region, cell=max(0.2, 0.02 * px))
    rooms_px = find_rooms(raster, labels, region)

    # -- plan origin: the column grid if there is one, so that every storey of a
    #    set lands on the same datum without any further alignment.
    if len(cols_px) >= 3:
        ox = min(c.x0 for c in cols_px)
        oy = min(c.y0 for c in cols_px)
        diag.add("plan origin on the column grid")
    elif walls_px:
        # walls_px is (band, openings) pairs — this branch only runs on a plan
        # with fewer than three columns, which the example set never is
        rects = [b.rect() for b, _ in walls_px]
        ox = min(min(r[0], r[2]) for r in rects)
        oy = min(min(r[1], r[3]) for r in rects)
        diag.add("plan origin on the wall envelope")
    else:
        ox, oy = region[0], region[1]

    def fx(v: float) -> float:
        return round((v - ox) / px, 4)

    def fy(v: float) -> float:
        return round((v - oy) / px, 4)

    # -- the stair, out of the tread stacks that pass B rejected as walls
    stairs_found = stairs_mod.find_stairs(stacks, sheet.words, px, ox, oy, diag)

    # -- assemble
    walls: list[Wall] = []
    for i, (band, ops) in enumerate(walls_px):
        x0, y0, x1, y1 = band.rect()
        w = Wall(
            id=f"w{i}",
            axis=band.axis,
            x0=fx(x0),
            y0=fy(y0),
            x1=fx(x1),
            y1=fy(y1),
        )
        for j, (kind, a, b) in enumerate(ops):
            u = fx if band.axis == "h" else fy
            w.openings.append(
                Opening(id=f"{w.id}o{j}", kind=kind, u0=u(a), u1=u(b))
            )
        walls.append(w)

    _add_windows(sheet, walls_px, walls, px, ox, oy, region, diag)
    walls = _drop_duplicates(walls)
    walls = _largest_structure(walls, diag)
    # railings are found after the wall set is final, so that "not in a wall"
    # means not in a wall that survived
    walls += _find_railings(sheet, walls, px, ox, oy, region, diag)

    columns = [
        Column(id=f"c{i}", x0=fx(c.x0), y0=fy(c.y0), x1=fx(c.x1), y1=fy(c.y1))
        for i, c in enumerate(cols_px)
    ]
    mark_exterior([w for w in walls if not w.is_railing], columns=columns)

    xs_w = [v for w in walls for v in (w.x0, w.x1)] or [0.0]
    ys_w = [v for w in walls for v in (w.y0, w.y1)] or [0.0]
    envelope = (min(xs_w), min(ys_w), max(xs_w), max(ys_w))

    rooms: list[Room] = []
    leaked = 0
    for rf in rooms_px:
        if rf.ratio < 0.55:
            continue
        bbox = rf.bbox
        # a fill that ran outside the building escaped through an opening; it
        # measures nothing, so it is counted and dropped rather than shown
        if (
            fx(bbox[0]) < envelope[0] - 0.5
            or fy(bbox[1]) < envelope[1] - 0.5
            or fx(bbox[2]) > envelope[2] + 0.5
            or fy(bbox[3]) > envelope[3] + 0.5
        ):
            leaked += 1
            continue
        rooms.append(
            Room(
                name=rf.label.name.title() or "Room",
                x0=fx(bbox[0]),
                y0=fy(bbox[1]),
                x1=fx(bbox[2]),
                y1=fy(bbox[3]),
                label_ft=rf.label.dim,
                measured_ft=(
                    round(rf.extent[0] / px, 2),
                    round(rf.extent[1] / px, 2),
                ),
            )
        )

    shut = _close_shaft_openings(walls, rooms, diag)
    del shut
    if leaked:
        diag.add(
            f"{leaked} rooms open to the outside were not measured "
            "(a car port, lobby or balcony reads as one space with the street)"
        )
    bounds = envelope

    warnings: list[str] = []
    if scale.confidence == "low":
        warnings.append(
            "Scale is a guess — calibrate it before trusting any dimension."
        )
    if not walls:
        warnings.append("No walls found on this sheet.")
    if not rooms:
        warnings.append("No rooms could be measured; room labels may be missing.")

    return PlanExtract(
        sheet_id=sheet_id,
        level=level,
        level_name=level_name,
        scale=scale,
        origin_px=(round(ox, 3), round(oy, 3)),
        bounds=tuple(round(v, 3) for v in bounds),  # type: ignore[arg-type]
        walls=walls,
        columns=columns,
        rooms=rooms,
        stairs=stairs_found,
        north_deg=north_bearing(sheet),
        warnings=warnings + diag.notes,
    )


# --------------------------------------------------------------------------- #
# helpers used by extract_plan
# --------------------------------------------------------------------------- #
def _check_round_thicknesses(
    sig: list[float], px: float, scale: ScaleInfo, diag: Diagnostics,
    units: Units = "imperial",
) -> None:
    """Cross-check the scale: real walls are built to round sizes.

    If the calibrated scale turns every wall spacing on the sheet into a whole
    number of inches — or of centimetres on a metric drawing — that is
    independent evidence the scale is right, and if it does not, the user
    should be told before anything is exported. A metric wall is 115 or 230mm,
    never 4.53", so the check has to be made in the units the wall was built in.
    """
    if not sig:
        return
    if units == "metric":
        sizes = [s / px * 0.3048 * 1000 for s in sig]  # millimetres
        off = [abs(v / 10 - round(v / 10)) for v in sizes]  # round centimetres
        reading = ", ".join(f"{v:.0f}mm" for v in sizes)
        what, tol = "round centimetres", 0.35
    else:
        sizes = [s / px * 12 for s in sig]
        off = [abs(v - round(v)) for v in sizes]
        reading = ", ".join(f'{v:.1f}"' for v in sizes)
        what, tol = "round inches", 0.35
    if max(off) <= tol:
        diag.add(f"wall thicknesses come out as {reading} — {what} confirm the scale")
        if scale.confidence == "medium" and scale.method == "room_labels":
            scale.confidence = "high"
    else:
        diag.add(f"wall thicknesses come out as {reading} — not round, so check the scale")


def _seal(bands: list[Band], runs: list[Run], min_len: float, max_len: float):
    """Bands with every door-sized gap closed — used only to separate rooms."""
    # pass A only needs rooms separated, so every gap is closed here, whether or
    # not it is a doorway — a car port opening counts too
    gaps = solo_gaps(bands, runs, min_len, max_len, require_support=False)
    return [b for b, _ in _seal_with_doors(bands, gaps)]


def _seal_with_doors(
    bands: list[Band],
    doors: list[tuple[Band, float, float]],
    runs: list[Run] | None = None,
    max_fill: float = 0.0,
) -> list[tuple[Band, list[tuple[str, float, float]]]]:
    """Extend bands across their doorways, keeping the doorway as an opening.

    The wall exists across a doorway — it is the *hole* that is the opening — so
    the band is grown and the gap recorded, rather than the wall being left
    broken.

    A short gap between two stretches of the same wall that is *not* a doorway
    is filled in solid. One face is still drawn across it, so the wall is there;
    it just has no opening in it. Without this, a stretch where the drafter let
    the inner face lapse leaves a hole in the facade — and a hole is a worse
    guess than a wall, since a wall can be opened again in one click.
    """
    per_line: dict[tuple[str, float, float, float], list[Band]] = {}
    door_map: dict[tuple[str, float, float, float], list[tuple[float, float]]] = {}
    for b in bands:
        per_line.setdefault((b.axis, round(b.lo, 2), round(b.hi, 2), b.pen), []).append(b)
    for host, a, b in doors:
        key = (host.axis, round(host.lo, 2), round(host.hi, 2), host.pen)
        door_map.setdefault(key, []).append((a, b))

    out: list[tuple[Band, list[tuple[str, float, float]]]] = []
    for key, pieces in per_line.items():
        axis, lo, hi, pen = key
        ivs = [(p.a, p.b) for p in pieces] + door_map.get(key, [])
        merged = geom.union(ivs, 0.5)

        if runs and max_fill > 0 and len(merged) > 1:
            face = geom.union(
                [
                    (r.a, r.b)
                    for r in runs
                    if r.axis == axis
                    and r.pen == pen
                    and (abs(r.pos - lo) < 0.4 or abs(r.pos - hi) < 0.4)
                ],
                1.2,
            )
            fills = []
            for i in range(len(merged) - 1):
                gap = (merged[i][1], merged[i + 1][0])
                width = gap[1] - gap[0]
                if width <= 0 or width > max_fill:
                    continue  # wider than a door and not one: a real opening
                if geom.total(geom.intersect(face, [gap])) >= 0.9 * width:
                    fills.append(gap)
            if fills:
                merged = geom.union(merged + fills, 0.5)

        for a, b in merged:
            band = Band(axis, lo, hi, a, b, pen=pen)
            ops = [
                ("door", max(a, d0), min(b, d1))
                for d0, d1 in door_map.get(key, [])
                if d1 > a and d0 < b
            ]
            out.append((band, ops))
    return out


def _add_windows(
    sheet: Sheet,
    walls_px,
    walls: list[Wall],
    px: float,
    ox: float,
    oy: float,
    region,
    diag: Diagnostics,
) -> None:
    """Blue pen inside a wall band is glazing."""
    blue = [
        s
        for s in sheet.segs
        if s.color == "blue" and _in_region((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2, region)
    ]
    if not blue:
        return
    n = 0
    for (band, _ops), wall in zip(walls_px, walls):
        x0, y0, x1, y1 = band.rect()
        pad = 0.6
        spans: list[tuple[float, float]] = []
        for s in blue:
            sx0, sx1 = sorted((s.x0, s.x1))
            sy0, sy1 = sorted((s.y0, s.y1))
            if band.axis == "h":
                if not (s.is_h and y0 - pad <= (sy0 + sy1) / 2 <= y1 + pad):
                    continue
                a, b = max(sx0, x0), min(sx1, x1)
            else:
                if not (s.is_v and x0 - pad <= (sx0 + sx1) / 2 <= x1 + pad):
                    continue
                a, b = max(sy0, y0), min(sy1, y1)
            if b - a > MIN_WINDOW_FT * px:
                spans.append((a, b))
        for a, b in geom.union(spans, 0.5 * px):
            if b - a < MIN_WINDOW_FT * px:
                continue
            u = (lambda v: (v - ox) / px) if band.axis == "h" else (lambda v: (v - oy) / px)
            # a window never sits where a door already is
            if any(
                min(o.u1, u(b)) - max(o.u0, u(a)) > 0.3
                for o in wall.openings
                if o.kind == "door"
            ):
                continue
            wall.openings.append(
                Opening(
                    id=f"{wall.id}win{len(wall.openings)}",
                    kind="window",
                    u0=round(u(a), 4),
                    u1=round(u(b), 4),
                )
            )
            n += 1
    if n:
        diag.add(f"{n} windows read from the glazing pen")


def _close_shaft_openings(
    walls: list[Wall], rooms: list[Room], diag: Diagnostics
) -> int:
    """A ventilation shaft is drawn open on one side; that is not a doorway.

    The wall beside a duct is often drawn with its inner face missing, which has
    the same signature as a doorway — and a full-height hole in an external wall
    beside a toilet reads, wrongly, as a way in. Named shafts get their wall
    closed again.
    """
    shafts = [
        r for r in rooms if set(r.name.upper().split()) & SHAFT_WORDS
    ]
    if not shafts:
        return 0
    removed = 0
    for w in walls:
        if not w.openings:
            continue
        keep = []
        for o in w.openings:
            if o.kind != "door" or o.source == "manual":
                keep.append(o)
                continue
            if w.axis == "h":
                box = (min(o.u0, o.u1), w.y0, max(o.u0, o.u1), w.y1)
            else:
                box = (w.x0, min(o.u0, o.u1), w.x1, max(o.u0, o.u1))
            if any(_touches(box, s, 0.3) for s in shafts):
                removed += 1
                continue
            keep.append(o)
        w.openings = keep
    if removed:
        diag.add(f"{removed} openings onto a duct or shaft closed up — not doorways")
    return removed


def _touches(box, room: Room, tol: float) -> bool:
    return (
        min(box[2], room.x1) - max(box[0], room.x0) > -tol
        and min(box[3], room.y1) - max(box[1], room.y0) > -tol
    )


def _find_railings(
    sheet: Sheet,
    walls: list[Wall],
    px: float,
    ox: float,
    oy: float,
    region,
    diag: Diagnostics,
) -> list[Wall]:
    """Glazing-pen lines with no wall behind them are guards, not windows.

    A balcony's outer edge and a stairwell's void are drawn as a few closely
    spaced lines in the same pen as glazing, but with no wall band under them.
    Building them as walls would box the balcony in; ignoring them would leave it
    a hole in the facade. They are railings: same plan, waist height.
    """
    out: list[Wall] = []
    for axis in ("h", "v"):
        runs = _clip_runs(
            geom.build_runs(sheet.segs, axis, colors=("blue",), min_len=1.0), region
        )
        free: list[tuple[float, float, float]] = []  # pos, a, b — in plan feet
        for r in runs:
            pos = ((r.pos - (oy if axis == "h" else ox))) / px
            a = (r.a - (ox if axis == "h" else oy)) / px
            b = (r.b - (ox if axis == "h" else oy)) / px
            covered = []
            for w in walls:
                if w.axis != axis or w.is_railing:
                    continue
                lo, hi = (w.y0, w.y1) if axis == "h" else (w.x0, w.x1)
                if not (lo - 0.1 <= pos <= hi + 0.1):
                    continue
                covered.append(w.u_range())
            for sa, sb in geom.subtract([(a, b)], geom.union(covered)):
                if sb - sa >= MIN_RAILING_FT:
                    free.append((pos, sa, sb))
        out += _cluster_railings(free, axis, len(out))

    if out:
        diag.add(f"{len(out)} railings read from the glazing pen with no wall behind")
    return out


def _cluster_railings(
    lines: list[tuple[float, float, float]], axis: str, offset: int
) -> list[Wall]:
    """Merge the few parallel lines that draw one railing into one object."""
    n = len(lines)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            pi, ai, bi = lines[i]
            pj, aj, bj = lines[j]
            if abs(pi - pj) > 0.6:
                continue
            overlap = min(bi, bj) - max(ai, aj)
            if overlap >= 0.5 * min(bi - ai, bj - aj):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[tuple[float, float, float]]] = {}
    for i, ln in enumerate(lines):
        groups.setdefault(find(i), []).append(ln)

    out: list[Wall] = []
    for g in groups.values():
        pos_lo = min(p for p, _a, _b in g)
        pos_hi = max(p for p, _a, _b in g)
        a = min(a for _p, a, _b in g)
        b = max(b for _p, _a, b in g)
        if b - a < MIN_RAILING_FT:
            continue
        # a thick band of glazing lines is a window whose wall went undetected,
        # not a railing — building it waist high would be worse than skipping it
        if pos_hi - pos_lo > MAX_RAILING_T_FT:
            continue
        centre = (pos_lo + pos_hi) / 2
        half = max(RAILING_MIN_T_FT, pos_hi - pos_lo) / 2
        wid = f"r{offset + len(out)}"
        if axis == "h":
            out.append(
                Wall(
                    id=wid,
                    axis="h",
                    x0=round(a, 4),
                    y0=round(centre - half, 4),
                    x1=round(b, 4),
                    y1=round(centre + half, 4),
                    kind="railing",
                    exterior=True,
                )
            )
        else:
            out.append(
                Wall(
                    id=wid,
                    axis="v",
                    x0=round(centre - half, 4),
                    y0=round(a, 4),
                    x1=round(centre + half, 4),
                    y1=round(b, 4),
                    kind="railing",
                    exterior=True,
                )
            )
    return out


def _overlap_area(a: Wall, b: Wall) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return max(0.0, w) * max(0.0, h)


def _drop_duplicates(walls: list[Wall], frac: float = 0.85) -> list[Wall]:
    """Remove walls almost entirely covered by another — they would z-fight."""
    order = sorted(walls, key=lambda w: -(w.length_ft * w.thickness_ft))
    keep: list[Wall] = []
    for w in order:
        area = w.length_ft * w.thickness_ft
        if area <= 0:
            continue
        if any(_overlap_area(w, k) >= frac * area for k in keep):
            continue
        keep.append(w)
    return sorted(keep, key=lambda w: w.id)


def _largest_structure(
    walls: list[Wall], diag: Diagnostics, margin_ft: float = 2.5
) -> list[Wall]:
    """Drop wall fragments that are nowhere near the building.

    Stray bands from dimension lines or a site plan sit well outside the
    building, so they can go. But "connected" is too strong a test on its own: a
    projecting bay — the toilet's, here — meets the rest of the plan only at its
    corners, where the two faces of its side walls overlap by an inch or two and
    never form a band. Keeping every fragment that lies within a couple of feet
    of the main structure keeps the bay and still drops the strays.
    """
    if len(walls) < 3:
        return walls
    n = len(walls)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tol = 0.15
    for i in range(n):
        a = walls[i]
        for j in range(i + 1, n):
            b = walls[j]
            if (
                min(a.x1, b.x1) - max(a.x0, b.x0) >= -tol
                and min(a.y1, b.y1) - max(a.y0, b.y0) >= -tol
            ):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[Wall]] = {}
    for i, w in enumerate(walls):
        groups.setdefault(find(i), []).append(w)
    main = max(groups.values(), key=lambda g: sum(w.length_ft for w in g))
    env = (
        min(w.x0 for w in main) - margin_ft,
        min(w.y0 for w in main) - margin_ft,
        max(w.x1 for w in main) + margin_ft,
        max(w.y1 for w in main) + margin_ft,
    )

    kept: list[Wall] = []
    dropped = 0
    for group in groups.values():
        if group is main or all(
            env[0] <= w.x0 and w.x1 <= env[2] and env[1] <= w.y0 and w.y1 <= env[3]
            for w in group
        ):
            kept += group
        else:
            dropped += len(group)
    if dropped:
        diag.add(f"{dropped} wall fragments dropped as too far from the building")
    return sorted(kept, key=lambda w: w.id)


