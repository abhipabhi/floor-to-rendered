"""Length parsing for architectural drawings, imperial and metric.

The set this tool was built against labels everything in feet and inches:
``30'``, ``6'-8"``, ``11'0"X12'0"``, ``5"``, ``+16'-9"``. Plenty of drawings do
not — the same practices produce sheets reading ``4.00 x 4.20``, ``SCALE 1:100``
and ``GROUND FLOOR LEVEL ±0.00``, where a bare ``3.60`` means metres.

Which system a sheet uses is **detected from what it prints**, never assumed,
because the two are ambiguous on their own: ``12`` could be twelve feet or
twelve metres, and getting it wrong scales the whole building by 3.28. See
:func:`detect_units`, which reports its evidence and its confidence the way
:class:`~app.models.ScaleInfo` does.

Everything in the pipeline is still carried in **feet** as a float, converted
to metres exactly once at export using :data:`FT_TO_M`. A metric sheet is
parsed to feet at the boundary, so nothing downstream has to care.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FT_TO_M = 0.3048
M_TO_FT = 1.0 / FT_TO_M
IN_TO_FT = 1.0 / 12.0
MM_TO_M = 0.001

Units = Literal["imperial", "metric"]

# 12'  |  12'-6"  |  12'6"  |  6"  |  +12'-6"  |  12.5'
_LEN = re.compile(
    r"""
    (?P<sign>[+-])?\s*
    (?:
        (?P<feet>\d+(?:\.\d+)?)\s*(?:'|’|FT\b)      # feet part
        (?:\s*-\s*|\s*)?
        (?:(?P<inches>\d+(?:\.\d+)?)\s*(?:"|”|''))? # optional inches part
      |
        (?P<inches_only>\d+(?:\.\d+)?)\s*(?:"|”|'')  # inches alone
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 11'0"X12'0"   |  14'-0"X10'-0"X8'-0"   |   4.00 x 4.20
_SEP = re.compile(r"\s*[X×x]\s*")

# 4.00m  |  3600mm  |  2.4 M  |  +3.60  |  ±0.00  |  4,20
_MET = re.compile(
    r"""
    (?P<sign>[+\-±])?\s*
    (?P<val>\d+(?:[.,]\d+)?)\s*
    (?P<unit>MM|CM|M)?(?![A-Za-z0-9'"’”])
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: a bare decimal on a metric sheet is metres if it could be a building
#: dimension; anything outside this is refused rather than guessed at
BARE_M_RANGE = (0.05, 100.0)


def parse_length_ft(text: str) -> float | None:
    """Parse a single length token to feet. Returns None if it isn't a length.

    >>> parse_length_ft("6'-8\\"")
    6.666666666666667
    >>> parse_length_ft("10\\"")
    0.8333333333333334
    """
    m = _LEN.search(text or "")
    if not m:
        return None
    if m.group("inches_only") is not None:
        val = float(m.group("inches_only")) * IN_TO_FT
    else:
        val = float(m.group("feet"))
        if m.group("inches"):
            val += float(m.group("inches")) * IN_TO_FT
    if m.group("sign") == "-":
        val = -val
    return val


def parse_all_lengths_ft(text: str) -> list[float]:
    """Every length token in a string, left to right."""
    out: list[float] = []
    for m in _LEN.finditer(text or ""):
        if m.group("inches_only") is not None:
            val = float(m.group("inches_only")) * IN_TO_FT
        else:
            val = float(m.group("feet"))
            if m.group("inches"):
                val += float(m.group("inches")) * IN_TO_FT
        out.append(-val if m.group("sign") == "-" else val)
    return out


# --------------------------------------------------------------------------- #
# which system is this sheet drawn in?
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class UnitSystem:
    """Which units a sheet prints, and how sure that is."""

    units: Units = "imperial"
    confidence: Literal["high", "medium", "low"] = "low"
    evidence: str = ""

    @property
    def metric(self) -> bool:
        return self.units == "metric"


# a foot or inch mark is unambiguous; nothing metric uses them
_IMPERIAL_MARK = re.compile(r"\d\s*(?:'|’|\"|”|''|\bFT\b|\bINCH\b)", re.I)
# an explicit metric unit, a ratio scale, or a ± datum tag
_METRIC_MARK = re.compile(
    # no \b before the unit: there is no word boundary between the 0 and the m
    # of "8.00m", so anchoring that way silently matches nothing
    r"""\d\s*(?:MM|CM|MTR|SQM|M)(?![A-Za-z0-9])      # 3600mm, 8.00m, 120 sqm
      | \bSCALE\s*[:=]?\s*1\s*[:：]\s*\d{1,4}          # SCALE 1:100
      | ±\s*0[.,]0                                    # ±0.00 datum tag
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 4.00 x 4.20 — a dimension pair written with spaces, as metric sheets do
_DIM_PAIR = re.compile(r"(\d+(?:[.,]\d+)?)\s*[X×x]\s*(\d+(?:[.,]\d+)?)")


def detect_units(text: str) -> UnitSystem:
    """Decide from the sheet's own text whether it is imperial or metric.

    Counting marks rather than looking in a fixed place, because the giveaway
    can be anywhere: a room label, a dimension chain, a scale note or a level
    tag. A sheet with no marks at all is reported as imperial at ``low``
    confidence — that is this tool's historical default, and saying so is
    better than pretending the question was answered.
    """
    body = text or ""
    imperial = len(_IMPERIAL_MARK.findall(body))
    metric = len(_METRIC_MARK.findall(body))

    if not imperial and not metric:
        return UnitSystem("imperial", "low", "no unit marks found; assuming imperial")
    if imperial and not metric:
        return UnitSystem("imperial", "high", f"{imperial} foot/inch marks, no metric")
    if metric and not imperial:
        return UnitSystem("metric", "high", f"{metric} metric marks, no foot/inch")

    # both appear — a metric sheet may still write 1/4"=1'-0", and an imperial
    # one may print a metre note, so go with the clear majority and say it was close
    ratio = max(imperial, metric) / max(1, min(imperial, metric))
    winner: Units = "imperial" if imperial > metric else "metric"
    conf = "high" if ratio >= 4 else "medium" if ratio >= 2 else "low"
    return UnitSystem(
        winner, conf, f"{imperial} foot/inch marks vs {metric} metric marks"
    )


# --------------------------------------------------------------------------- #
# metric
# --------------------------------------------------------------------------- #
def parse_length_m(text: str) -> float | None:
    """Parse a single metric token to **metres**, or None.

    An explicit unit always wins. A bare number is taken as metres only when it
    is plausibly a building dimension; anything else is refused rather than
    guessed at, since ``3600`` could be millimetres and ``12`` could be feet.
    """
    m = _MET.search(text or "")
    if not m:
        return None
    val = float(m.group("val").replace(",", "."))
    unit = (m.group("unit") or "").upper()
    if unit == "MM":
        val *= MM_TO_M
    elif unit == "CM":
        val *= 0.01
    elif not unit:
        lo, hi = BARE_M_RANGE
        if not (lo <= val <= hi):
            return None
    if m.group("sign") == "-":
        val = -val
    return val


def parse_length_any(text: str, units: Units = "imperial") -> float | None:
    """Parse a token to **feet**, reading it in the sheet's own system."""
    if units == "metric":
        v = parse_length_m(text)
        return v * M_TO_FT if v is not None else None
    return parse_length_ft(text)


@dataclass(frozen=True)
class RoomDim:
    """A ``A x B`` room dimension label, in feet."""

    a: float
    b: float
    raw: str

    @property
    def area(self) -> float:
        return self.a * self.b


def parse_room_dim(text: str, units: Units = "imperial") -> RoomDim | None:
    """Parse ``11'0"X12'0"`` — or metric ``4.00 x 4.20`` — into a :class:`RoomDim`.

    Requires exactly two lengths separated by an X, both plausible room
    dimensions (2'..60'), so that ``(14'-0"X10'-0"X8'-0")`` — a tank volume —
    and stray text are rejected. The result is always in feet.
    """
    if not text:
        return None
    parts = _SEP.split(text.strip().strip("()"))
    if len(parts) != 2:
        return None
    lens = [parse_length_any(p, units) for p in parts]
    if any(v is None for v in lens):
        return None
    a, b = lens  # type: ignore[misc]
    if not (2.0 <= a <= 60.0 and 2.0 <= b <= 60.0):
        return None
    # The token must be *only* the dimension, not a sentence containing one.
    pattern = _MET if units == "metric" else _LEN
    if len(pattern.findall(text)) != 2:
        return None
    return RoomDim(a=a, b=b, raw=text)


def find_room_dim(text: str, units: Units = "imperial") -> RoomDim | None:
    """Find an ``A x B`` dimension anywhere in a whole label block.

    Imperial sheets write ``11'0"X12'0"`` as one unbroken token, so the
    per-token parse finds it. Metric sheets write ``4.00 x 4.20``, which the
    PDF hands over as three separate words — the dimension only exists once the
    block is read as a whole.
    """
    if not text:
        return None
    if units != "metric":
        return parse_room_dim(text, units)
    m = _DIM_PAIR.search(text)
    if not m:
        return None
    a = parse_length_m(m.group(1))
    b = parse_length_m(m.group(2))
    if a is None or b is None:
        return None
    a, b = a * M_TO_FT, b * M_TO_FT
    if not (2.0 <= a <= 60.0 and 2.0 <= b <= 60.0):
        return None
    return RoomDim(a=a, b=b, raw=m.group(0))


def ft_to_m(ft: float) -> float:
    return ft * FT_TO_M


def fmt_ft(ft: float) -> str:
    """Feet as a drawing-style string: 12.5 -> ``12'-6"``."""
    neg = ft < 0
    ft = abs(ft)
    whole = int(ft)
    inches = round((ft - whole) * 12)
    if inches == 12:
        whole += 1
        inches = 0
    s = f"{whole}'-{inches}\"" if inches else f"{whole}'"
    return "-" + s if neg else s
