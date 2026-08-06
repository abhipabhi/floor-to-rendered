"""Imperial length parsing for Indian/US architectural drawings.

The drawings this tool was built against label everything in feet and inches:
``30'``, ``6'-8"``, ``11'0"X12'0"``, ``5"``, ``+16'-9"``. Everything in the
pipeline is carried in **feet** as a float and converted to metres exactly once,
at export time, using :data:`FT_TO_M`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FT_TO_M = 0.3048
IN_TO_FT = 1.0 / 12.0

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

# 11'0"X12'0"   |  14'-0"X10'-0"X8'-0"
_SEP = re.compile(r"\s*[X×x]\s*")


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


@dataclass(frozen=True)
class RoomDim:
    """A ``A x B`` room dimension label, in feet."""

    a: float
    b: float
    raw: str

    @property
    def area(self) -> float:
        return self.a * self.b


def parse_room_dim(text: str) -> RoomDim | None:
    """Parse ``11'0"X12'0"`` into a :class:`RoomDim`.

    Requires exactly two lengths separated by an X, both plausible room
    dimensions (2'..60'), so that ``(14'-0"X10'-0"X8'-0")`` — a tank volume —
    and stray text are rejected.
    """
    if not text:
        return None
    parts = _SEP.split(text.strip().strip("()"))
    if len(parts) != 2:
        return None
    lens = [parse_length_ft(p) for p in parts]
    if any(v is None for v in lens):
        return None
    a, b = lens  # type: ignore[misc]
    if not (2.0 <= a <= 60.0 and 2.0 <= b <= 60.0):
        return None
    # The token must be *only* the dimension, not a sentence containing one.
    if len(_LEN.findall(text)) != 2:
        return None
    return RoomDim(a=a, b=b, raw=text)


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
