"""Stairs, from the treads the extractor was already finding and discarding.

A tread is drawn exactly like a thin wall: two parallel lines a constant pitch
apart. :func:`app.extract.reject_stacks` exists to throw those away, because a
ladder of them would otherwise become a stack of phantom walls — and it worked,
so the most conspicuous thing in the plan never reached the model.

Rejecting a bad candidate is not the same as producing the right answer. The
rungs are a *measurement*: on the example set they come out at a 10.0" going
over a 3.51 ft width, against a sheet that prints ``3'6"WIDE`` beside them.

What a plan cannot say is the riser, because that depends on the storey height,
which no plan states. So the riser is *derived* — floor-to-floor divided by the
number of risers — and labelled as derived. The inverse is not done: inferring a
storey height from a "standard" 7" riser would be inventing a number from a
convention, which is the one thing this tool must not do.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import geom
from .geom import Band, Raster
from .models import Flight, Stair
from .pdfvec import Word

#: a going — the tread depth you walk on — outside this is not a stair
MIN_GOING_FT, MAX_GOING_FT = 8.0 / 12.0, 14.0 / 12.0
#: nobody builds a flight narrower than this, and a hatch pattern is narrower
MIN_FLIGHT_FT, MAX_FLIGHT_FT = 2.5, 6.0
#: two treads make a step, not a flight
MIN_TREADS = 3
#: a flight has to be this close to an UP/DN marker or a STAIR label, in feet.
#: The example set also draws a *ramp* with its own UP token, 23 ft away.
MARKER_REACH_FT = 6.0
#: tokens that mark a stair
UP_WORDS = {"UP", "DN", "DOWN"}
STAIR_WORDS = {"STAIR", "STAIRCASE", "STEPS"}
#: tokens found inside a stairwell
WELL_WORDS = {"VOID", "OPEN"}
#: a well smaller than this is a rounding artefact between flights
MIN_WELL_SQFT = 1.5


@dataclass
class Ladder:
    """A run of treads at a constant pitch — one flight, still in points."""

    axis: str  # the axis the tread lines run along
    rungs: list[Band]

    @property
    def pitch(self) -> float:
        return float(np.median([b.thickness for b in self.rungs]))

    @property
    def tread_len(self) -> float:
        return float(np.median([b.b - b.a for b in self.rungs]))

    def rect(self) -> tuple[float, float, float, float]:
        rs = [b.rect() for b in self.rungs]
        return (
            min(r[0] for r in rs), min(r[1] for r in rs),
            max(r[2] for r in rs), max(r[3] for r in rs),
        )


# --------------------------------------------------------------------------- #
# grouping rungs into flights
# --------------------------------------------------------------------------- #
def _ladders(rungs: list[Band], tol: float) -> list[Ladder]:
    """Group rungs into contiguous constant-pitch stacks.

    Treads of one flight share a span — they are all the same length, side by
    side — so rungs are first bucketed by overlapping span and only then split
    where the stack breaks. Without the span test the two parallel flights of a
    dogleg, which stack along the same axis, merge into one impossible flight.
    """
    out: list[Ladder] = []
    buckets: dict[tuple[str, float], list[Band]] = {}
    for b in rungs:
        buckets.setdefault((b.axis, round(b.pen, 2)), []).append(b)

    for (axis, _pen), group in buckets.items():
        spans: list[list[Band]] = []
        for b in sorted(group, key=lambda b: b.lo):
            for s in spans:
                lo = min(x.a for x in s)
                hi = max(x.b for x in s)
                overlap = min(b.b, hi) - max(b.a, lo)
                if overlap > 0.5 * min(b.b - b.a, hi - lo):
                    s.append(b)
                    break
            else:
                spans.append([b])

        for s in spans:
            s.sort(key=lambda b: b.lo)
            run = [s[0]]
            for prev, cur in zip(s, s[1:]):
                # contiguous: this tread starts where the last one ended
                if abs(cur.lo - prev.hi) <= tol:
                    run.append(cur)
                else:
                    out.append(Ladder(axis, run))
                    run = [cur]
            out.append(Ladder(axis, run))
    return out


def _up_dir(rect: tuple[float, float, float, float], axis: str, marker) -> str:
    """Which way the flight climbs, from the UP marker's position along it.

    The marker sits at the foot of the flight, so the direction of travel is
    away from it. Treads that run in y are climbed along x, and vice versa.
    """
    x0, y0, x1, y1 = rect
    if axis == "v":  # treads run in y, so the climb is in x
        if marker is None:
            return "+x"
        return "+x" if marker[0] <= (x0 + x1) / 2 else "-x"
    if marker is None:
        return "+y"
    return "+y" if marker[1] <= (y0 + y1) / 2 else "-y"


def _well(
    flights: list[Flight],
    markers: list[tuple[float, float, str]] | None = None,
    cell: float = 0.1,
) -> tuple | None:
    """The open shaft a dogleg wraps around, if there is one.

    Found rather than assumed. The flights are rasterised, and the well is the
    free region between them — preferring the one the drawing has labelled
    ``VOID``, because a dogleg's well is usually open on the side where there is
    no flight, so topology alone would let it escape to the border and be
    mistaken for outside. Where the sheet says nothing, only a region wholly
    enclosed by the flights counts. A straight flight has no well and gets none.
    """
    if len(flights) < 2:
        return None
    x0 = min(f.x0 for f in flights)
    y0 = min(f.y0 for f in flights)
    x1 = max(f.x1 for f in flights)
    y1 = max(f.y1 for f in flights)
    if x1 - x0 <= cell or y1 - y0 <= cell:
        return None

    cols = max(1, int(round((x1 - x0) / cell)))
    rows = max(1, int(round((y1 - y0) / cell)))
    raster = Raster(x0=x0, y0=y0, cell=cell, grid=np.zeros((rows, cols), bool))
    for f in flights:
        raster.fill_rect(f.x0, f.y0, f.x1, f.y1)

    regions = geom.label_regions(raster)
    rows_, cols_ = regions.labels.shape

    def bbox_of(label: int) -> tuple[tuple, float]:
        rs, cs = np.where(regions.labels == label)
        return (
            (x0 + cs.min() * cell, y0 + rs.min() * cell,
             x0 + (cs.max() + 1) * cell, y0 + (rs.max() + 1) * cell),
            len(rs) * cell * cell,
        )

    # first choice: the region the sheet has written VOID or OPEN inside
    for mx, my, text in markers or []:
        if text not in WELL_WORDS:
            continue
        r, c = raster.to_cell(mx, my)
        if not (0 <= r < rows_ and 0 <= c < cols_):
            continue
        label = int(regions.labels[r, c])
        if label == 0:
            continue
        box, area = bbox_of(label)
        if area >= MIN_WELL_SQFT:
            return box

    # otherwise only a region the flights fully enclose can be the well
    border = set(regions.labels[0].tolist()) | set(regions.labels[-1].tolist())
    border |= set(regions.labels[:, 0].tolist()) | set(regions.labels[:, -1].tolist())
    best, best_area = None, 0.0
    for label in np.unique(regions.labels):
        if label == 0 or label in border:
            continue
        box, area = bbox_of(int(label))
        if area > best_area:
            best, best_area = box, area
    return best if best_area >= MIN_WELL_SQFT else None


# --------------------------------------------------------------------------- #
# the reader
# --------------------------------------------------------------------------- #
def find_stairs(
    rungs: list[Band],
    words: list[Word],
    px: float,
    ox: float,
    oy: float,
    diag=None,
) -> list[Stair]:
    """Read stairs out of the rejected tread stacks.

    ``rungs`` are in points; ``px``/``ox``/``oy`` convert to plan feet.
    """
    if not rungs:
        return []

    def fx(v: float) -> float:
        return round((v - ox) / px, 4)

    def fy(v: float) -> float:
        return round((v - oy) / px, 4)

    markers = [
        (fx(w.cx), fy(w.cy), w.text.strip().upper())
        for w in words
        if w.text.strip().upper() in UP_WORDS | WELL_WORDS
        or any(s in w.text.strip().upper() for s in STAIR_WORDS)
    ]

    flights: list[tuple[Flight, tuple[float, float] | None]] = []
    for lad in _ladders(rungs, tol=0.35 * px):
        going = lad.pitch / px
        width = lad.tread_len / px
        if not (MIN_GOING_FT <= going <= MAX_GOING_FT):
            continue
        if not (MIN_FLIGHT_FT <= width <= MAX_FLIGHT_FT):
            continue
        if len(lad.rungs) < MIN_TREADS:
            continue

        r = lad.rect()
        rect = (fx(r[0]), fy(r[1]), fx(r[2]), fy(r[3]))
        near = [
            m for m in markers
            if rect[0] - MARKER_REACH_FT <= m[0] <= rect[2] + MARKER_REACH_FT
            and rect[1] - MARKER_REACH_FT <= m[1] <= rect[3] + MARKER_REACH_FT
        ]
        if not near:
            continue  # a hatch pattern or a ramp with no stair marker on it
        climb = next((m for m in near if m[2] in UP_WORDS), None)
        flights.append((
            Flight(
                axis="v" if lad.axis == "v" else "h",
                x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3],
                treads=len(lad.rungs),
                going_ft=round(going, 4),
                width_ft=round(width, 4),
                up=_up_dir(rect, lad.axis, (climb[0], climb[1]) if climb else None),
            ),
            (climb[0], climb[1]) if climb else None,
        ))

    if not flights:
        return []

    # flights that touch belong to one stair, joined at their landings
    groups = _chain([f for f, _ in flights])
    out: list[Stair] = []
    for i, group in enumerate(groups):
        group.sort(key=lambda f: (f.y0, f.x0))
        well = _well(group, markers)
        out.append(
            Stair(
                id=f"st{i}",
                flights=group,
                well=well,
                treads=sum(f.treads for f in group),
            )
        )
    if diag and out:
        for s in out:
            diag.add(
                f"stair read from {len(s.flights)} flights, {s.treads} treads at "
                f'{s.flights[0].going_ft * 12:.1f}" going, '
                f"{s.flights[0].width_ft:.2f} ft wide"
                + (" around an open well" if s.well else "")
            )
    return out


def _chain(flights: list[Flight], reach: float = 1.5) -> list[list[Flight]]:
    """Group flights whose footprints touch or nearly touch."""
    parent = list(range(len(flights)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(flights):
        for j, b in enumerate(flights[i + 1:], start=i + 1):
            gap_x = max(a.x0 - b.x1, b.x0 - a.x1)
            gap_y = max(a.y0 - b.y1, b.y0 - a.y1)
            if gap_x <= reach and gap_y <= reach:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri

    groups: dict[int, list[Flight]] = {}
    for i, f in enumerate(flights):
        groups.setdefault(find(i), []).append(f)
    return list(groups.values())
