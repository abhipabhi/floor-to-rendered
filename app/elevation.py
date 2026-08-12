"""Elevations and sections — where a drawing set states its heights.

This is the sheet the tool has been missing. A floor plan is a horizontal cut
and genuinely says nothing about height, which is why every vertical number so
far has been a setting you type. An elevation or a section says them outright,
in level tags against a datum::

    ROOF LEVEL          +6.80
    FIRST FLOOR LEVEL   +3.60
    GROUND FLOOR LEVEL  ±0.00

Read those and the storey height stops being a guess: it is the difference
between two stated levels, 3.60 m, measured the same way the plan's scale is.

**The vertical scale is measured here, never inherited.** A section and a plan
sit on the same sheet at different scales in this very drawing set, and sheets
within one set differ too — so borrowing the plan's ``px_per_ft`` would be
silently wrong. The tags give both the scale and the datum at once, by fitting
level against sheet position, and the residual of that fit is the self-check:
tags that do not sit on a straight line are not describing one datum, and are
refused rather than averaged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .datum import Quantity, Reading
from .pdfvec import Sheet, Word
from .units import M_TO_FT, UnitSystem, detect_units, parse_length_ft, parse_length_m

#: tokens that mark a level, all optional — a bare ``+3.60`` beside a line is
#: still a level tag, and plenty of sheets write nothing else
_LEVEL_WORD = re.compile(
    r"\b(?:F\.?\s?F\.?\s?L|FFL|S\.?\s?F\.?\s?L|LVL|L\.?V\.?L|LEVEL|EL|RL|GL)\b", re.I
)
#: a signed number: +3.60, ±0.00, -1.200, +16'-9"
_SIGNED = re.compile(r"^[+\-±]\s*[\d]")
#: how close two tokens must be to be one fragmented tag
_JOIN_PT = 10.0
#: a fit this far off a straight line is not describing a single datum
_MAX_RMS_FRACTION = 0.02
#: two tags this close in level are the same tag read twice
_SAME_FT = 0.05


@dataclass(frozen=True)
class LevelTag:
    """One stated level, and where it sits on the sheet."""

    value_ft: float
    y_px: float
    raw: str

    @property
    def is_datum(self) -> bool:
        return abs(self.value_ft) < _SAME_FT


@dataclass
class Datum:
    """The vertical scale and origin a view was drawn to."""

    px_per_ft: float
    y_at_zero: float  # sheet y of the ±0.00 line
    tags: list[LevelTag] = field(default_factory=list)
    rms_ft: float = 0.0
    confidence: str = "low"
    units: str = "metric"
    note: str = ""

    def level_at(self, y_px: float) -> float:
        """The level, in feet above datum, of a point on the sheet."""
        return (self.y_at_zero - y_px) / self.px_per_ft

    def storey_heights(self) -> list[float]:
        """Gaps between consecutive stated levels, bottom up."""
        vals = _distinct([t.value_ft for t in self.tags])
        return [round(b - a, 4) for a, b in zip(vals, vals[1:])]


def _distinct(values: list[float]) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > _SAME_FT:
            out.append(v)
    return out


# --------------------------------------------------------------------------- #
# reading the tags off the sheet
# --------------------------------------------------------------------------- #
def reassemble(words: list[Word], gap: float = _JOIN_PT) -> list[tuple[str, float, float]]:
    """Join tokens the PDF split apart, into ``(text, cx, cy)``.

    ``+16'-9"`` arrives as four separate words, and ``±0.00`` often as two.
    Anything that reads level tags has to put them back together first.
    """
    items = [w for w in words if w.text.strip()]
    items.sort(key=lambda w: (round(w.cy, 1), w.x0))
    out: list[tuple[str, float, float]] = []
    cur: list[Word] = []

    def flush() -> None:
        if not cur:
            return
        text = "".join(w.text for w in cur)
        out.append((
            text,
            sum(w.cx for w in cur) / len(cur),
            sum(w.cy for w in cur) / len(cur),
        ))

    for w in items:
        if cur and abs(w.cy - cur[-1].cy) <= 2.0 and 0 <= w.x0 - cur[-1].x1 <= gap:
            cur.append(w)
            continue
        flush()
        cur = [w]
    flush()
    return out


#: ±0.00, +0.000, -0,00 — the datum itself
_ZERO = re.compile(r"^[+\-±]\s*0+(?:[.,]0+)?\s*(?:M|MM|CM)?$", re.I)


def _parse_level(text: str, units: str) -> float | None:
    """A level tag's value in feet, or None if it is not one."""
    body = _LEVEL_WORD.sub("", text).strip()
    body = body.replace("±", "+")
    if not _SIGNED.match(body):
        return None
    # The datum is written ±0.00 and is the single most important tag on the
    # sheet — it is what every other level is measured from. A plausibility
    # window on bare numbers throws it away for being too small, so it is
    # recognised before any of that applies.
    if _ZERO.match(body):
        return 0.0
    if units == "metric":
        v = parse_length_m(body)
        return v * M_TO_FT if v is not None else None
    return parse_length_ft(body)


def find_level_tags(sheet: Sheet, units: str | None = None) -> list[LevelTag]:
    """Every level tag on the sheet, as stated."""
    system = units or detect_units(sheet.text).units
    tags: list[LevelTag] = []
    for text, _cx, cy in reassemble(sheet.words):
        value = _parse_level(text, system)
        if value is None:
            continue
        tags.append(LevelTag(value_ft=round(value, 4), y_px=cy, raw=text.strip()))
    return tags


# --------------------------------------------------------------------------- #
# fitting them to a datum
# --------------------------------------------------------------------------- #
def fit_datum(tags: list[LevelTag], units: str = "metric") -> Datum | None:
    """Least-squares the stated levels against their place on the sheet.

    The gradient is the vertical scale and the intercept locates the datum, so
    one fit gives both — and its residual says whether to believe either. Tags
    that do not fall on a line are not describing a single datum: a title-block
    number, a dimension, two views on one sheet. Those are refused, because a
    scale fitted through them would be wrong everywhere rather than obviously
    wrong somewhere.
    """
    usable = [t for t in tags if True]
    if len(usable) < 2:
        return None

    n = len(usable)
    sy = sum(t.y_px for t in usable)
    sv = sum(t.value_ft for t in usable)
    syy = sum(t.y_px * t.y_px for t in usable)
    syv = sum(t.y_px * t.value_ft for t in usable)
    denom = n * syy - sy * sy
    if abs(denom) < 1e-9:
        return None
    a = (n * syv - sy * sv) / denom  # feet per point, negative: y grows downward
    b = (sv - a * sy) / n
    if a >= -1e-9:
        return None  # level rising with y means these are not level tags

    px_per_ft = 1.0 / -a
    y_at_zero = -b / a
    spread = max(t.value_ft for t in usable) - min(t.value_ft for t in usable)
    rms = (sum((a * t.y_px + b - t.value_ft) ** 2 for t in usable) / n) ** 0.5
    if spread > 0 and rms > _MAX_RMS_FRACTION * spread:
        return None

    distinct = len(_distinct([t.value_ft for t in usable]))
    confidence = "high" if distinct >= 3 else "medium"
    return Datum(
        px_per_ft=px_per_ft,
        y_at_zero=y_at_zero,
        tags=sorted(usable, key=lambda t: t.value_ft),
        rms_ft=round(rms, 5),
        confidence=confidence,
        units=units,
        note=(
            f"{distinct} level tags fitted to one datum at "
            f"{px_per_ft:.3f} px/ft, residual {rms * 12:.2f}\""
        ),
    )


#: how far from its line a level tag's text may sit
SNAP_PT = 14.0


def snap_to_level_lines(sheet: Sheet, tags: list[LevelTag]) -> list[LevelTag]:
    """Move each tag onto the level line it annotates.

    A level tag is text written *beside* its line, not on it — above it, below
    it, inside a bubble on a leader. Fitting the text positions gives the right
    scale, because a constant offset cancels in the gradient, but it puts the
    datum itself wherever the lettering happened to sit. That does not matter
    for a storey height, which is a difference between two tags, and matters a
    great deal for a sill, which is measured from the datum outright.

    The correction is one shift applied to every tag, not a per-tag snap. A
    drafter writes the lettering the same way each time, so the offset is a
    constant — and taking the median of it is what stops a single tag being
    dragged onto the wrong line. There is usually other linework within a few
    points of a level: on the test sheet the +3.60 tag sat nearer a string
    course at 3.55 than its own line, and snapping it individually put a real
    0.93" of error into a fit that had been exact.

    A uniform shift also leaves the scale untouched, which was already right.
    """
    import statistics

    from . import geom

    lines = [r.pos for r in geom.build_runs(sheet.segs, "h") if r.b - r.a > 20.0]
    if not lines or not tags:
        return tags
    offsets = []
    for tag in tags:
        near = min(lines, key=lambda y: abs(y - tag.y_px))
        if abs(near - tag.y_px) <= SNAP_PT:
            offsets.append(near - tag.y_px)
    if not offsets:
        return tags
    shift = statistics.median(offsets)
    return [
        LevelTag(value_ft=t.value_ft, y_px=t.y_px + shift, raw=t.raw) for t in tags
    ]


def read_datum(sheet: Sheet) -> Datum | None:
    """The datum an elevation or section sheet was drawn to."""
    system: UnitSystem = detect_units(sheet.text)
    tags = snap_to_level_lines(sheet, find_level_tags(sheet, system.units))
    return fit_datum(tags, system.units)


def read_sheet(sheet: Sheet, sheet_id: str, levels: list[int] | None = None):
    """Everything one elevation or section states, as readings.

    Returns ``(datum, readings)``. Without a datum nothing else is attempted:
    a sill measured against a scale that was never established is a number with
    no meaning, and would be worse than the setting it replaced.
    """
    datum = read_datum(sheet)
    if datum is None:
        return None, []
    out = readings(datum, sheet_id, levels)
    openings = find_openings(sheet, datum)
    out += opening_readings(opening_levels(datum, openings, levels), datum, sheet_id)
    parapet = parapet_height(sheet, datum)
    if parapet is not None:
        out.append(Reading(key="building.parapet_ft", q=Quantity(
            ft=parapet, source="measured", method="elevation_parapet",
            confidence=datum.confidence, sheet_id=sheet_id,
            evidence=f"wall carries {_fmt(parapet, datum.units)} above the roof level",
        )))
    return datum, out


# --------------------------------------------------------------------------- #
# openings, read against the datum
# --------------------------------------------------------------------------- #
#: an opening in elevation is at least this wide and tall, and at most this big
MIN_OPENING_FT, MAX_OPENING_FT = 1.0, 14.0
#: how far a rectangle's corners may miss each other, in points
CLOSE_PT = 1.6
#: sills land in 1½-inch bins; CAD repeats a level exactly, so they spike
SILL_BIN_FT = 0.125
#: a second spike this close in size to the first is a real second value
RIVAL = 0.6


@dataclass(frozen=True)
class Opening:
    """A rectangle on an elevation: a window or a door, in sheet points."""

    x0: float
    y_top: float
    x1: float
    y_bottom: float
    pen: float

    @property
    def width_pt(self) -> float:
        return self.x1 - self.x0

    @property
    def height_pt(self) -> float:
        return self.y_bottom - self.y_top


def find_openings(sheet: Sheet, datum: Datum) -> list[Opening]:
    """Closed rectangles on the sheet, sized like openings.

    Worked from raw segments rather than from :func:`geom.build_runs`, which
    merges collinear lines: three window heads at the same height on one storey
    become a single twenty-foot run, and every one of them is then too wide to
    be an opening. Here each drawn line stays its own line, and a rectangle is
    two of them at the same extent closed by two more.
    """
    from .geom import pen_class

    px = datum.px_per_ft
    lo, hi = MIN_OPENING_FT * px, MAX_OPENING_FT * px

    hs: list[tuple[float, float, float, float]] = []  # pos, a, b, pen
    vs: list[tuple[float, float, float, float]] = []
    for s in sheet.segs:
        dx, dy = abs(s.x1 - s.x0), abs(s.y1 - s.y0)
        if dy <= 0.25 and dx > 1.0:
            hs.append(((s.y0 + s.y1) / 2, min(s.x0, s.x1), max(s.x0, s.x1),
                       pen_class(s.width)))
        elif dx <= 0.25 and dy > 1.0:
            vs.append(((s.x0 + s.x1) / 2, min(s.y0, s.y1), max(s.y0, s.y1),
                       pen_class(s.width)))

    out: list[Opening] = []
    seen: set[tuple[int, int, int, int]] = set()
    for i, (ytop, ax, bx, pen) in enumerate(hs):
        for ybot, ax2, bx2, pen2 in hs[i + 1:]:
            if pen2 != pen:
                continue
            top, bottom = min(ytop, ybot), max(ytop, ybot)
            height = bottom - top
            if not (lo <= height <= hi):
                continue
            # the two horizontals must be the same line, not merely overlapping
            if abs(ax - ax2) > CLOSE_PT or abs(bx - bx2) > CLOSE_PT:
                continue
            if not (lo <= bx - ax <= hi):
                continue
            if not (_has_side(vs, pen, ax, top, bottom)
                    and _has_side(vs, pen, bx, top, bottom)):
                continue
            key = (round(ax), round(top), round(bx), round(bottom))
            if key in seen:
                continue
            seen.add(key)
            out.append(Opening(ax, top, bx, bottom, pen))
    return _drop_nested(out)


def _drop_nested(openings: list[Opening], inset: float = 4.0) -> list[Opening]:
    """Keep the outer of two rectangles drawn one inside the other.

    A window is drawn as a frame with the glass inside it, so it registers
    twice, a couple of points apart. Left in, the pair shows up as two sill
    heights on the same storey and gets reported as a genuine second value.
    """
    out: list[Opening] = []
    for op in sorted(openings, key=lambda o: -(o.width_pt * o.height_pt)):
        if any(
            k.x0 - inset <= op.x0 and k.y_top - inset <= op.y_top
            and k.x1 + inset >= op.x1 and k.y_bottom + inset >= op.y_bottom
            for k in out
        ):
            continue
        out.append(op)
    return out


def _has_side(vs, pen: float, x: float, y_top: float, y_bottom: float) -> bool:
    """Is there a vertical line at ``x`` closing this pair of horizontals?"""
    for pos, a, b, vpen in vs:
        if vpen != pen or abs(pos - x) > CLOSE_PT:
            continue
        if a <= y_top + CLOSE_PT and b >= y_bottom - CLOSE_PT:
            return True
    return False


def _dominant(values: list[float], bin_ft: float = SILL_BIN_FT):
    """The value CAD repeated, plus any genuine rival — never an average.

    Returns ``(value, count, rivals)``. A drawing that uses two sill heights —
    1100 in the bedrooms, 650 in the living room, which is a real convention —
    has two spikes, and both are reported. Averaging them would produce a sill
    height that appears nowhere on the drawing.
    """
    if not values:
        return None, 0, []
    import statistics
    from collections import Counter

    groups: dict[int, list[float]] = {}
    for v in values:
        groups.setdefault(round(v / bin_ft), []).append(v)
    ranked = Counter({k: len(vs) for k, vs in groups.items()}).most_common()
    best, n = ranked[0]
    # The bin only decides *which* readings belong together. Reporting its
    # centre would hand back a quantised number no line on the drawing sits at,
    # so the answer is the median of the readings themselves.
    rivals = [statistics.median(groups[k]) for k, c in ranked[1:] if c >= RIVAL * n]
    return statistics.median(groups[best]), n, rivals


@dataclass
class OpeningLevels:
    """Sill and head heights above their own storey's floor, per storey."""

    sill_ft: dict[int, float] = field(default_factory=dict)
    head_ft: dict[int, float] = field(default_factory=dict)
    samples: dict[int, int] = field(default_factory=dict)
    rivals: dict[int, list[float]] = field(default_factory=dict)


def opening_levels(
    datum: Datum, openings: list[Opening], levels: list[int] | None = None
) -> OpeningLevels:
    """Group openings by the storey they sit on and find each storey's sill.

    A sill is measured from the floor it belongs to, so every opening is first
    assigned to the highest stated level at or below it. That is why this needs
    the datum and not just the rectangles.
    """
    floors = _distinct([t.value_ft for t in datum.tags])
    if not floors:
        return OpeningLevels()
    order = sorted(levels) if levels else list(range(len(floors)))

    per_floor: dict[int, list[tuple[float, float]]] = {}
    for op in openings:
        sill = datum.level_at(op.y_bottom)
        head = datum.level_at(op.y_top)
        idx = max((i for i, f in enumerate(floors) if f <= sill + 0.05), default=None)
        if idx is None or idx >= len(order):
            continue
        per_floor.setdefault(order[idx], []).append((sill - floors[idx], head - floors[idx]))

    out = OpeningLevels()
    for level, pairs in per_floor.items():
        sill, n, rivals = _dominant([p[0] for p in pairs])
        head, _n2, _r2 = _dominant([p[1] for p in pairs])
        if sill is None or head is None or head <= sill:
            continue
        out.sill_ft[level] = round(sill, 4)
        out.head_ft[level] = round(head, 4)
        out.samples[level] = n
        if rivals:
            out.rivals[level] = [round(r, 4) for r in rivals]
    return out


def parapet_height(sheet: Sheet, datum: Datum) -> float | None:
    """How far the wall carries on above the topmost stated level.

    The roof line is the highest level tag; anything drawn above it is the
    parapet. Measured as the distance from that level to the highest long
    horizontal line on the sheet.
    """
    from . import geom

    floors = _distinct([t.value_ft for t in datum.tags])
    if not floors:
        return None
    roof = floors[-1]
    px = datum.px_per_ft
    top = None
    for run in geom.build_runs(sheet.segs, "h"):
        if run.b - run.a < 3.0 * px:  # a parapet runs the width of the building
            continue
        level = datum.level_at(run.pos)
        if level <= roof + 0.05:
            continue
        top = level if top is None else max(top, level)
    if top is None:
        return None
    height = top - roof
    return round(height, 4) if 0.5 <= height <= 8.0 else None


# --------------------------------------------------------------------------- #
# what it contributes to the model
# --------------------------------------------------------------------------- #
def opening_readings(
    levels: OpeningLevels, datum: Datum, sheet_id: str
) -> list[Reading]:
    """Sill and lintel heights, per storey, from the openings drawn on the face."""
    out: list[Reading] = []
    for level, sill in levels.sill_ft.items():
        rivals = levels.rivals.get(level, [])
        out.append(Reading(key=f"level.{level}.window_sill_ft", q=Quantity(
            ft=sill, source="measured", method="elevation_openings",
            confidence="high" if levels.samples.get(level, 0) >= 3 else "medium",
            sheet_id=sheet_id, samples=levels.samples.get(level, 0),
            evidence=f"{levels.samples.get(level, 0)} openings on this storey",
            alternatives=[
                f"{_fmt(r, datum.units)} above floor — a second sill height on the "
                "same storey" for r in rivals
            ],
        )))
    for level, head in levels.head_ft.items():
        out.append(Reading(key=f"level.{level}.window_head_ft", q=Quantity(
            ft=head, source="measured", method="elevation_openings",
            confidence="high" if levels.samples.get(level, 0) >= 3 else "medium",
            sheet_id=sheet_id, samples=levels.samples.get(level, 0),
            evidence="top of the openings drawn on this storey",
        )))
    return out


def readings(datum: Datum, sheet_id: str, levels: list[int] | None = None) -> list[Reading]:
    """Turn stated levels into storey heights and a plinth.

    Only differences between *stated* levels become storey heights. The spacing
    of drawn lines is not used, and neither is any convention about what a floor
    height usually is — if the sheet states two levels, that is one storey
    height, and if it states one, that is a datum and nothing more.
    """
    out: list[Reading] = []
    values = _distinct([t.value_ft for t in datum.tags])
    if len(values) < 2:
        return out

    order = sorted(levels) if levels else list(range(len(values) - 1))
    # the lowest stated level above the datum is the ground floor's finish level
    if values[0] > _SAME_FT:
        out.append(Reading(key="building.plinth_ft", q=Quantity(
            ft=values[0], source="measured", method="elevation_ffl",
            confidence=datum.confidence, sheet_id=sheet_id,
            evidence=f"lowest stated level {_fmt(values[0], datum.units)}",
        )))

    for i, gap in enumerate(datum.storey_heights()):
        if i >= len(order):
            break
        lo, hi = values[i], values[i + 1]
        out.append(Reading(key=f"level.{order[i]}.floor_to_floor_ft", q=Quantity(
            ft=gap, source="measured", method="elevation_ffl",
            confidence=datum.confidence, sheet_id=sheet_id,
            samples=len(datum.tags), spread_pct=round(datum.rms_ft / max(gap, 1e-6) * 100, 3),
            evidence=(
                f"{_fmt(hi, datum.units)} − {_fmt(lo, datum.units)} "
                f"between stated levels"
            ),
        )))
        out.append(Reading(key=f"level.{order[i]}.ffl_ft", q=Quantity(
            ft=lo, source="measured", method="elevation_ffl",
            confidence=datum.confidence, sheet_id=sheet_id,
            evidence=f"stated level {_fmt(lo, datum.units)}",
        )))
    return out


def _fmt(ft: float, units: str) -> str:
    if units == "metric":
        return f"{ft / M_TO_FT:+.2f}m"
    from .units import fmt_ft

    return fmt_ft(ft)
