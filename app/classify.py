"""What is this sheet, and which storey does it belong to?

A drawing set is a mixed bag: two or three floor plans that become storeys, a
setting-out layout, foundation and tie-beam sheets that describe what is *below*
ground, service details, and sometimes a designer's render. Only floor plans are
extruded; everything else is kept, labelled and skipped, and the user can
override any call in the UI.

The sheet title in the title block is the primary evidence; the file name is the
fallback, because CAD title blocks are occasionally left on the template default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# sheet kinds
FLOOR_PLAN = "floor_plan"
LAYOUT = "layout"
FOUNDATION = "foundation"
TIE_BEAM = "tie_beam"
SERVICES = "services"
DETAIL = "detail"
REFERENCE = "reference"
UNKNOWN = "unknown"

KIND_LABELS = {
    FLOOR_PLAN: "Floor plan",
    LAYOUT: "Setting-out layout",
    FOUNDATION: "Foundation / footing",
    TIE_BEAM: "Tie beam / plinth beam",
    SERVICES: "Services detail",
    DETAIL: "Structural detail",
    REFERENCE: "Reference image",
    UNKNOWN: "Unrecognised",
}

# level name → storey index. Ground is 0; anything below is negative.
_LEVELS: list[tuple[str, int, str]] = [
    (r"\bBASEMENT\b", -1, "basement"),
    (r"\bSTILT\b", 0, "stilt"),
    (r"\bGROUND\s*FLOOR\b|\bG\.?\s*F\.?\s*PLAN\b", 0, "ground"),
    (r"\bFIRST\s*FLOOR\b|\bF\.?\s*F\.?\s*PLAN\b|\b1ST\s*FLOOR\b", 1, "first"),
    (r"\bSECOND\s*FLOOR\b|\b2ND\s*FLOOR\b|\bS\.?\s*F\.?\s*PLAN\b", 2, "second"),
    (r"\bTHIRD\s*FLOOR\b|\b3RD\s*FLOOR\b", 3, "third"),
    (r"\bFOURTH\s*FLOOR\b|\b4TH\s*FLOOR\b", 4, "fourth"),
    (r"\bTYPICAL\s*FLOOR\b", 1, "typical"),
]

# Order matters. A column-detail sheet also says "FOUNDATION DETAILS", and a
# footing plan also names columns, so the more specific title wins first and the
# generic structural words are only accepted next to PLAN.
_NON_PLAN: list[tuple[str, str]] = [
    (r"SEPTIC\s*TANK|SOAK\s*PIT|SEWER|DRAIN(?:AGE)?\s*(?:PLAN|DETAIL)", SERVICES),
    # CO[ULM]{2,4}N absorbs the COULMN / COLMN typos these title blocks carry.
    (
        r"\bCO[ULM]{2,4}N\b\s*(?:DETAIL|SIZE)|DETAILS?\s*OF\s*CO[ULM]{2,4}N"
        r"|REINFORCEMENT\s*DETAIL|BEAM\s*DETAIL|SLAB\s*DETAIL",
        DETAIL,
    ),
    (r"TIE\s*BEAM|PLINTH\s*BEAM", TIE_BEAM),
    (r"FOOTING\s*PLAN|FOUNDATION\s*PLAN|\bFOOTING\b(?!\s*SIZE)", FOUNDATION),
    (r"LAY\s*OUT|LAYOUT|SETTING\s*OUT|CENTRE\s*LINE|CENTER\s*LINE", LAYOUT),
]

LEVEL_ORDINALS = {
    -2: "Lower basement",
    -1: "Basement",
    0: "Ground floor",
    1: "First floor",
    2: "Second floor",
    3: "Third floor",
    4: "Fourth floor",
}


def level_name(level: int) -> str:
    return LEVEL_ORDINALS.get(level, f"Level {level}")


@dataclass
class Classification:
    kind: str
    level: int | None  # storey index for FLOOR_PLAN, else None
    label: str  # human-readable
    evidence: str  # the text that decided it, shown in the UI


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").upper())


def classify(text: str, filename: str = "", has_vectors: bool = True) -> Classification:
    """Classify one sheet from its text content and file name."""
    body = _norm(text)
    fname = _norm(filename)

    if not has_vectors:
        return Classification(
            REFERENCE, None, KIND_LABELS[REFERENCE], "image-only page, no vector geometry"
        )

    for source, where in ((body, "sheet"), (fname, "file name")):
        # Non-plan sheets are checked first: a layout sheet embeds the ground
        # floor plan and would otherwise read as one.
        for pattern, kind in _NON_PLAN:
            m = re.search(pattern, source)
            if m:
                return Classification(kind, None, KIND_LABELS[kind], f"{where}: “{m.group(0)}”")
        for pattern, level, _slug in _LEVELS:
            m = re.search(pattern, source)
            if m:
                return Classification(
                    FLOOR_PLAN, level, level_name(level), f"{where}: “{m.group(0)}”"
                )

    if re.search(r"\bPLAN\b", body) or re.search(r"\bPLAN\b", fname):
        return Classification(FLOOR_PLAN, None, "Floor plan (level unknown)", "sheet: “PLAN”")
    return Classification(UNKNOWN, None, KIND_LABELS[UNKNOWN], "no title match")


def project_title(text: str) -> str:
    """Best effort project name from the title block, for naming exports."""
    m = re.search(r"PROPOSED\s+(.{4,60}?)\s+(?:FOR|AT)\b", _norm(text))
    if m:
        return m.group(1).title().strip()
    return ""
