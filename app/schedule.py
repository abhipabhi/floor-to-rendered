"""Read the structural schedules — what the floor plans do not say.

A floor plan is a horizontal cut: it states where things are and how thick they
are, and nothing about how deep or how tall. The rest of the set does say some
of that, in two forms this module reads:

* **ruled tables** — the footing schedule is a real grid of vector lines, so a
  cell is not guessed from alignment but bounded by the rules that actually
  enclose it. That makes it the most reliable text on the whole set.
* **stated notes** — single self-contained sentences ("DEPTH OF CUTTING SHALL
  BE 5'6" FROM NATURAL GROUND LEVEL") and section titles ("DETAIL OF TB1
  -10"X12"").

Everything here is *below the plinth* or a member size. None of it gives a
storey height, and none of it is allowed to pretend to: what it produces are
:class:`~app.datum.Reading` s at ``source="derived"``, each carrying the literal
text it came from.

Two rules, both learned from this set:

* **Never measure an NTS detail.** All four tie-beam section boxes are drawn the
  same size whether they say 10"x12" or 10"x15". The text is the dimension.
* **These title blocks are typed by hand.** They contain COULMN, BEAN for
  "been", YEILD, STREGTH. Every pattern here is spelling-tolerant, following the
  ``CO[ULM]{2,4}N`` precedent already in :mod:`app.classify`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .datum import Quantity, Reading
from .pdfvec import Sheet, Word
from .units import parse_length_ft

#: a rule has to be this long to be part of a table rather than a tick or hatch
MIN_RULE_PT = 40.0
#: two rules within this are the same rule drawn twice
RULE_TOL = 1.2
#: a table needs at least this many bands to be a table at all
MIN_COLS, MIN_ROWS = 3, 2


# --------------------------------------------------------------------------- #
# ruled tables
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rule:
    axis: str  # "h" | "v"
    pos: float
    a: float
    b: float

    def spans(self, v: float, tol: float = RULE_TOL) -> bool:
        return self.a - tol <= v <= self.b + tol


@dataclass
class Table:
    cols: list[float]
    rows: list[float]
    cells: dict[tuple[int, int], str] = field(default_factory=dict)

    @property
    def n_cols(self) -> int:
        return max(0, len(self.cols) - 1)

    @property
    def n_rows(self) -> int:
        return max(0, len(self.rows) - 1)

    def cell(self, r: int, c: int) -> str:
        return self.cells.get((r, c), "")

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.cols[0], self.rows[0], self.cols[-1], self.rows[-1])


def _rules(sheet: Sheet) -> list[Rule]:
    """Long axial lines, normalised and de-duplicated.

    The drawings repeat rules — the same line is emitted several times, and
    sometimes right-to-left — so both have to be normalised away before any of
    it can be treated as a grid.
    """
    seen: set[tuple[str, int, int, int]] = set()
    out: list[Rule] = []
    for g in sheet.segs:
        dx, dy = abs(g.x1 - g.x0), abs(g.y1 - g.y0)
        if dy < 0.3 and dx >= MIN_RULE_PT:
            axis, pos, a, b = "h", (g.y0 + g.y1) / 2, min(g.x0, g.x1), max(g.x0, g.x1)
        elif dx < 0.3 and dy >= MIN_RULE_PT:
            axis, pos, a, b = "v", (g.x0 + g.x1) / 2, min(g.y0, g.y1), max(g.y0, g.y1)
        else:
            continue
        key = (axis, round(pos), round(a), round(b))
        if key in seen:
            continue
        seen.add(key)
        out.append(Rule(axis, pos, a, b))
    return out


def _cluster(values: list[float], tol: float = RULE_TOL) -> list[float]:
    """Collapse near-identical coordinates to one representative each."""
    out: list[float] = []
    for v in sorted(values):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def find_tables(sheet: Sheet) -> list[Table]:
    """Every ruled grid on the sheet, largest first.

    Tables are found by which rules *cross* each other, not by looking in a
    known place, so a sheet carrying two schedules and a title block yields
    each of them separately.
    """
    rules = _rules(sheet)
    hs = [r for r in rules if r.axis == "h"]
    vs = [r for r in rules if r.axis == "v"]
    if not hs or not vs:
        return []

    # union-find over rules that intersect: a crossing pair belongs to one grid
    parent = list(range(len(hs) + len(vs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i, h in enumerate(hs):
        for j, v in enumerate(vs):
            if h.spans(v.pos) and v.spans(h.pos):
                union(i, len(hs) + j)

    groups: dict[int, tuple[list[Rule], list[Rule]]] = {}
    for i, h in enumerate(hs):
        groups.setdefault(find(i), ([], []))[0].append(h)
    for j, v in enumerate(vs):
        groups.setdefault(find(len(hs) + j), ([], []))[1].append(v)

    tables: list[Table] = []
    for group_hs, group_vs in groups.values():
        rows = _cluster([r.pos for r in group_hs])
        cols = _cluster([r.pos for r in group_vs])
        if len(cols) - 1 < MIN_COLS or len(rows) - 1 < MIN_ROWS:
            continue
        t = Table(cols=cols, rows=rows)
        _fill(t, sheet.words)
        tables.append(t)
    tables.sort(key=lambda t: -( (t.cols[-1] - t.cols[0]) * (t.rows[-1] - t.rows[0]) ))
    return tables


def _fill(t: Table, words: list[Word]) -> None:
    """Put every word in the band it sits in, joined in reading order."""
    buckets: dict[tuple[int, int], list[Word]] = {}
    for w in words:
        cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
        c = _band(t.cols, cx)
        r = _band(t.rows, cy)
        if c is None or r is None:
            continue
        buckets.setdefault((r, c), []).append(w)
    for key, ws in buckets.items():
        ws.sort(key=lambda w: (round((w.y0 + w.y1) / 2 / 6), w.x0))
        t.cells[key] = " ".join(w.text for w in ws).strip()


def _band(edges: list[float], v: float) -> int | None:
    for i in range(len(edges) - 1):
        if edges[i] <= v <= edges[i + 1]:
            return i
    return None


# --------------------------------------------------------------------------- #
# the footing schedule
# --------------------------------------------------------------------------- #
#: header text → the field it labels. Headers are split over two rows and some
#: cells are merged, so a column is identified by *any* header text in it.
_HEADERS: list[tuple[str, str]] = [
    (r"^S\.?\s*NO", "sno"),
    (r"FOOT\b.*NO|^FOOT$", "tag"),
    (r"FOOTING\s*SIZE", "size"),
    (r"CO[ULM]{2,4}N?\s*TYPE|^COL\b.*TYPE", "col_type"),
    (r"^D1$", "d1"),
    (r"^D2$", "d2"),
    (r"^D$", "d"),
    (r"JALI", "jali"),
    (r"REIN|SIZE\s*AND", "column"),
]


@dataclass
class Footing:
    """One row of the footing schedule, as written."""

    tag: str  # F1
    col_type: str = ""  # T1
    size_ft: tuple[float, float] | None = None  # the pad, 4'6" x 4'6"
    d1_ft: float | None = None
    d2_ft: float | None = None
    depth_ft: float | None = None  # D, the pad's total thickness
    column_ft: tuple[float, float] | None = None  # 10" x 15"
    raw: dict[str, str] = field(default_factory=dict)


def _column_map(t: Table, first_data_row: int) -> dict[str, int]:
    """Which grid column holds which field, read off the header rows."""
    out: dict[str, int] = {}
    for c in range(t.n_cols):
        text = " ".join(t.cell(r, c) for r in range(first_data_row)).strip().upper()
        text = re.sub(r"\s+", " ", text)
        for pattern, name in _HEADERS:
            if name in out:
                continue
            for piece in (text, *text.split(" ")):
                if re.search(pattern, piece):
                    out[name] = c
                    break
            else:
                continue
            break
    return out


def _two_lengths(text: str) -> tuple[float, float] | None:
    parts = re.split(r"\s*[X×x]\s*", (text or "").strip())
    if len(parts) != 2:
        return None
    a, b = parse_length_ft(parts[0]), parse_length_ft(parts[1])
    return (a, b) if a and b else None


def footings(sheet: Sheet) -> list[Footing]:
    """Every footing the schedule describes.

    The pads are also *drawn* on the footing plan, but the sheet's own note 2
    says "DO NOT SCALE, ONLY WRITTEN DIMENSIONS TO BE FOLLOWED" — and on this
    set the drawn and written sizes genuinely disagree. The written one wins;
    the caller is told about the other rather than it being quietly dropped.
    """
    for t in find_tables(sheet):
        rows = [
            r for r in range(t.n_rows)
            if any(re.fullmatch(r"F\s*\d+", t.cell(r, c).strip(), re.I)
                   for c in range(t.n_cols))
        ]
        if not rows:
            continue
        cmap = _column_map(t, rows[0])
        if "tag" not in cmap or "size" not in cmap:
            continue

        out: list[Footing] = []
        for r in rows:
            raw = {name: t.cell(r, c) for name, c in cmap.items()}
            tag = re.sub(r"\s+", "", raw.get("tag", ""))
            if not tag:
                continue
            col = raw.get("column", "")
            # COL-10"X15" as well as COLUMN / the COULMN typo, so {1,4} not {2,4}
            m = re.search(
                r"CO[ULM]{1,4}N?\s*-?\s*(\d+)\s*[\"”]?\s*[X×x]\s*(\d+)\s*[\"”]?", col, re.I
            )
            out.append(
                Footing(
                    tag=tag.upper(),
                    col_type=re.sub(r"\s+", "", raw.get("col_type", "")).upper(),
                    size_ft=_two_lengths(raw.get("size", "")),
                    d1_ft=parse_length_ft(raw.get("d1", "")),
                    d2_ft=parse_length_ft(raw.get("d2", "")),
                    depth_ft=parse_length_ft(raw.get("d", "")),
                    column_ft=(
                        (float(m.group(1)) / 12.0, float(m.group(2)) / 12.0) if m else None
                    ),
                    raw=raw,
                )
            )
        if out:
            return out
    return []


# --------------------------------------------------------------------------- #
# tie beam sections
# --------------------------------------------------------------------------- #
#: DETAIL OF TB1 -10"X12"  — the hyphen may be any dash, the quotes may be absent
_TB = re.compile(
    r"DETAIL\s*OF\s*TB\s*-?\s*(\d+)\s*[-–—]\s*(\d+)\s*[\"”]?\s*[X×x]\s*(\d+)\s*[\"”]?",
    re.I,
)
_SLAB_PROJ = re.compile(r"slab\s*proj\.?\s*((?:\d+(?:\.\d+)?\s*['’])(?:\s*\d+\s*[\"”])?)", re.I)


@dataclass(frozen=True)
class BeamSection:
    tag: str  # TB1
    width_ft: float
    depth_ft: float
    raw: str


def tie_beams(sheet: Sheet) -> list[BeamSection]:
    """The plinth-beam sections, from their titles.

    Read from the text and never from the boxes: all four are drawn at the same
    size regardless of what they say, so measuring them would make a 15" beam
    12" deep.
    """
    out: list[BeamSection] = []
    seen: set[str] = set()
    for m in _TB.finditer(sheet.text or ""):
        tag = f"TB{m.group(1)}"
        if tag in seen:
            continue
        seen.add(tag)
        out.append(
            BeamSection(
                tag=tag,
                width_ft=float(m.group(2)) / 12.0,
                depth_ft=float(m.group(3)) / 12.0,
                raw=m.group(0).strip(),
            )
        )
    return out


def slab_projection_ft(sheet: Sheet) -> tuple[float, str] | None:
    """The stated slab overhang, e.g. ``slab proj. 1'``."""
    m = _SLAB_PROJ.search(sheet.text or "")
    if not m:
        return None
    v = parse_length_ft(m.group(1))
    return (v, m.group(0).strip()) if v else None


# --------------------------------------------------------------------------- #
# stated notes
# --------------------------------------------------------------------------- #
@dataclass
class Notes:
    cutting_depth_ft: float | None = None
    bearing_depth_ft: float | None = None
    designed_for: str = ""  # G+2ND
    seismic_zone: str = ""
    concrete_grade: str = ""
    raw: dict[str, str] = field(default_factory=dict)


def notes(sheet: Sheet) -> Notes:
    """Facts stated in prose, each a single self-contained phrase."""
    text = re.sub(r"\s+", " ", sheet.text or "")
    n = Notes()

    if m := re.search(r"DEPTH\s+OF\s+CUTTING\s+SHALL\s+BE\s+(.+?)\s+FROM", text, re.I):
        n.cutting_depth_ft = parse_length_ft(m.group(1))
        n.raw["cutting_depth"] = m.group(0)
    if m := re.search(
        r"BEARING\s+CAPACITY.{0,80}?AT\s+(\d+['’][-\s]?\d*[\"”]?)\s+BELOW", text, re.I
    ):
        n.bearing_depth_ft = parse_length_ft(m.group(1))
        n.raw["bearing_depth"] = m.group(0)
    # "HAS BEAN DESIGN FOR G+2ND" — the sheet's own spelling
    if m := re.search(r"BE[AE]N\s+DESIGN(?:ED)?\s+FOR\s+(G\s*\+\s*\d\s*(?:ND|ST|RD|TH)?)", text, re.I):
        n.designed_for = re.sub(r"\s+", "", m.group(1)).upper()
        n.raw["designed_for"] = m.group(0)
    # anchored to the earthquake clause: "ZONE" alone also appears as a table header
    if m := re.search(r"(?:EARTH\s*QUAKE|EARTHQUAKE|SEISMIC).{0,30}?ZONE\s*-?\s*(I{1,3}V?|\d)\b", text, re.I):
        n.seismic_zone = m.group(1).upper()
        n.raw["seismic_zone"] = m.group(0)
    if m := re.search(r"\bM\s*-\s*(\d{2,3})\b", text, re.I):
        n.concrete_grade = f"M-{m.group(1)}"
        n.raw["concrete_grade"] = m.group(0)
    return n


# --------------------------------------------------------------------------- #
# what any of this contributes to the model
# --------------------------------------------------------------------------- #
def readings(sheet: Sheet, sheet_id: str) -> list[Reading]:
    """Turn what this sheet states into vertical parameters.

    Deliberately short. These sheets describe the foundation and the members;
    they do not state a storey height, and inferring one from "G+2ND" or from a
    beam depth would be invention. What they do give is the plinth beam depth
    and the slab projection, both written on the sheet in so many words.
    """
    out: list[Reading] = []

    beams = tie_beams(sheet)
    if beams:
        deepest = max(beams, key=lambda b: b.depth_ft)
        out.append(Reading(key="building.plinth_beam_depth_ft", q=Quantity(
            ft=deepest.depth_ft, source="derived", method="tie_beam_schedule",
            confidence="high", sheet_id=sheet_id, samples=len(beams),
            evidence=deepest.raw,
        )))

    if proj := slab_projection_ft(sheet):
        value, raw = proj
        out.append(Reading(key="building.slab_projection_ft", q=Quantity(
            ft=value, source="derived", method="slab_projection_note",
            confidence="medium", sheet_id=sheet_id, evidence=raw,
        )))

    n = notes(sheet)
    if n.cutting_depth_ft:
        out.append(Reading(key="building.excavation_depth_ft", q=Quantity(
            ft=n.cutting_depth_ft, source="derived", method="foundation_note",
            confidence="high", sheet_id=sheet_id,
            evidence=n.raw.get("cutting_depth", ""),
        )))
    return out
