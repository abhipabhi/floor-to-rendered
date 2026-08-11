"""Ingest a set of PDFs, extract every floor plan, and build the model.

Shared by the CLI and the web API so that both do exactly the same thing.
"""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field

from . import datum
from . import elevation
from . import schedule
from . import site as site_mod
from .build3d import BuildResult, build
from .classify import (
    ELEVATION,
    FLOOR_PLAN,
    LEVEL_ORDINALS,
    SECTION,
    classify,
    level_name,
    project_title,
)
from .datum import Reading
from .extract import extract_plan
from .models import BuildParams, LevelParams, PlanExtract, SheetInfo
from .pdfvec import Sheet, load_sheet

SCALE_AGREEMENT = 0.02  # sheets within 2% are treated as one drawing scale


@dataclass
class Ingest:
    sheets: list[SheetInfo] = field(default_factory=list)
    raw: dict[str, Sheet] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    title: str = ""


def sheet_id(filename: str, index: int) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0]
    keep = "".join(c if c.isalnum() else "-" for c in stem).strip("-").lower()
    return f"{index:02d}-{keep[:48]}"


def ingest(paths: list[str]) -> Ingest:
    """Read and classify every PDF in a set."""
    out = Ingest()
    for i, path in enumerate(sorted(paths)):
        name = os.path.basename(path)
        try:
            sheet = load_sheet(path, name)
        except Exception as exc:  # unreadable PDF: keep it visible, skip it
            sid = sheet_id(name, i)
            out.sheets.append(
                SheetInfo(
                    id=sid,
                    filename=name,
                    kind="unknown",
                    kind_label="Could not be read",
                    level=None,
                    evidence=str(exc)[:200],
                    include=False,
                    page_width=0,
                    page_height=0,
                    n_segments=0,
                    n_words=0,
                )
            )
            continue
        cls = classify(sheet.text, name, has_vectors=len(sheet.segs) > 20)
        sid = sheet_id(name, i)
        out.raw[sid] = sheet
        out.paths[sid] = path
        out.sheets.append(
            SheetInfo(
                id=sid,
                filename=name,
                kind=cls.kind,
                kind_label=cls.label,
                level=cls.level,
                evidence=cls.evidence,
                include=cls.kind == FLOOR_PLAN and cls.level is not None,
                page_width=round(sheet.width, 2),
                page_height=round(sheet.height, 2),
                n_segments=len(sheet.segs),
                n_words=len(sheet.words),
            )
        )
        if not out.title:
            out.title = project_title(sheet.text)
    return out


def extract_included(
    ing: Ingest, sheets: list[SheetInfo], scale_override: float | None = None
) -> tuple[dict[str, PlanExtract], list[str]]:
    """Extract every included sheet, then put them all on one drawing scale.

    Sheets of a set are plotted at the same scale, so if the per-sheet
    calibrations agree they are pooled into a single number and every storey is
    re-extracted with it. Storeys that disagree keep their own scale and say so —
    silently averaging a real discrepancy is how a building ends up 3% wrong.
    """
    notes: list[str] = []
    first: dict[str, PlanExtract] = {}
    for si in sheets:
        if not si.include or si.id not in ing.raw:
            continue
        lvl = si.level if si.level is not None else 0
        first[si.id] = extract_plan(
            ing.raw[si.id],
            si.id,
            lvl,
            level_name(lvl),
            px_per_ft_override=scale_override,
        )

    if scale_override or len(first) < 2:
        return first, notes

    scales = [e.scale.px_per_ft for e in first.values() if e.scale.method == "room_labels"]
    if len(scales) < 2:
        return first, notes
    med = statistics.median(scales)
    if max(abs(s - med) / med for s in scales) > SCALE_AGREEMENT:
        notes.append(
            "Storeys calibrated to different scales ("
            + ", ".join(f"{s:.3f}" for s in scales)
            + " px/ft); each keeps its own. Check the sheets are the same plot scale."
        )
        return first, notes

    notes.append(
        f"All storeys put on one drawing scale, {med:.3f} px/ft "
        f"(per-sheet readings agreed to "
        f"{100 * max(abs(s - med) / med for s in scales):.1f}%)."
    )
    unified: dict[str, PlanExtract] = {}
    for sid, ex in first.items():
        re_ex = extract_plan(
            ing.raw[sid], sid, ex.level, ex.level_name, px_per_ft_override=med
        )
        re_ex.scale.method = "room_labels_pooled"
        re_ex.scale.confidence = "high"
        re_ex.scale.samples = sum(
            e.scale.samples for e in first.values() if e.scale.method == "room_labels"
        )
        re_ex.scale.note = (
            f"pooled from {len(scales)} storeys calibrated independently on their "
            "own room labels"
        )
        unified[sid] = re_ex
    return unified, notes


def default_params(extracts: dict[str, PlanExtract]) -> BuildParams:
    """Sensible vertical parameters for the storeys found.

    Every one of them is an assumption, and each is labelled as one, so the
    heights page can say so per number instead of in a banner over the lot.
    """
    params = BuildParams()
    for ex in sorted(extracts.values(), key=lambda e: e.level):
        params.levels.append(
            LevelParams(
                level=ex.level,
                name=LEVEL_ORDINALS.get(ex.level, ex.level_name),
                floor_to_floor_ft=10.0,
            )
        )
    datum.seed_defaults(params)
    return params


def sheet_readings(
    ing: Ingest, sheets: list[SheetInfo], levels: list[int] | None = None
) -> list[Reading]:
    """Everything the *non-plan* sheets state, as vertical readings.

    v1 classified these sheets, showed them, and then ignored them. They carry
    the only vertical dimensions anywhere in this set — beam depths, the
    excavation, the slab projection — so they are read here and attributed to
    the sheet they came from.

    Takes ``sheets`` separately from ``ing`` for the same reason
    :func:`extract_included` does: the caller decides which sheets are in play.
    """
    out: list[Reading] = []
    raw = ing.raw
    for si in sheets:
        if si.kind == FLOOR_PLAN or si.id not in raw:
            continue
        try:
            sheet = raw[si.id]
        except Exception:  # an unreadable sheet must not stop the others
            continue
        out.extend(schedule.readings(sheet, si.id))
        # Only elevations and sections are asked for levels. A plan's setting-out
        # chainages look exactly like level tags — "+9'-5"", "+14'-1"" — and the
        # only reason they are not mistaken for them is that this never runs on a
        # plan. The datum fit rejects them anyway, because chainages rise going
        # down the sheet and levels rise going up, but the guard is the classifier.
        if si.kind in (ELEVATION, SECTION):
            found = elevation.read_datum(sheet)
            if found is not None:
                out.extend(elevation.readings(found, si.id, levels))
    return out


def road_xy_for(ing: Ingest, extracts: dict[str, PlanExtract]):
    """Where the lowest storey's sheet writes ROAD, in that plan's feet."""
    if not extracts:
        return None
    sid = min(extracts, key=lambda k: extracts[k].level)
    sheet = ing.raw.get(sid) if hasattr(ing.raw, "get") else None
    if sheet is None:
        try:
            sheet = ing.raw[sid]
        except Exception:
            return None
    return site_mod.road_label_xy(extracts[sid], sheet.words)


def run(paths: list[str]) -> tuple[Ingest, dict[str, PlanExtract], BuildParams, BuildResult, list[str]]:
    """The whole pipeline with default parameters — used by the CLI and tests."""
    ing = ingest(paths)
    extracts, notes = extract_included(ing, ing.sheets)
    params = default_params(extracts)
    notes += datum.resolve(
        params, sheet_readings(ing, ing.sheets, sorted({e.level for e in extracts.values()}))
    )
    datum.seed_defaults(params)
    result = build(list(extracts.values()), params, road_xy=road_xy_for(ing, extracts))
    return ing, extracts, params, result, notes
