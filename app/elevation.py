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


def read_datum(sheet: Sheet) -> Datum | None:
    """The datum an elevation or section sheet was drawn to."""
    system: UnitSystem = detect_units(sheet.text)
    tags = find_level_tags(sheet, system.units)
    return fit_datum(tags, system.units)


# --------------------------------------------------------------------------- #
# what it contributes to the model
# --------------------------------------------------------------------------- #
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
