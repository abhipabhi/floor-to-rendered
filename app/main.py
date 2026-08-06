"""The web app: upload a drawing set, check what was read, export a 3D model."""

from __future__ import annotations

import os

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import datum
from . import storage
from .blender import blender_script
from .build3d import build
from . import site as site_mod
from . import textures as tex
from .classify import FLOOR_PLAN, KIND_LABELS, level_name
from .finish import PRESETS, SLOTS
from .errors import AppError, handle, not_found
from .extract import extract_plan
from .glb import write_glb
from .models import BuildParams, JobState, Opening, PlanExtract, SheetInfo
from .obj import texture_files, write_mtl, write_obj
from .pdfvec import render_page_png
from .pipeline import default_params, extract_included
from .units import fmt_ft

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="floor to rendered", version="1.0.0")
app.add_exception_handler(AppError, handle)


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    with open(os.path.join(STATIC, "index.html")) as fh:
        return HTMLResponse(fh.read())


class RevalidatingStatic(StaticFiles):
    """Static files that are always revalidated.

    Without this the browser keeps serving a cached ``app.js`` after the app has
    been updated, and you are looking at last week's page with no way to tell.
    ETags make the revalidation a 304, so nothing is actually re-downloaded.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStatic(directory=STATIC), name="static")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _Ing:
    """Adapter so pipeline.extract_included can work off stored uploads."""

    def __init__(self, job_id: str, sheets: list[SheetInfo]):
        self.job_id = job_id
        self._sheets = {s.id: s for s in sheets}

    @property
    def raw(self):
        return _LazySheets(self.job_id, self._sheets)


class _LazySheets(dict):
    def __init__(self, job_id: str, sheets: dict[str, SheetInfo]):
        super().__init__()
        self.job_id = job_id
        self.sheets = sheets

    def __contains__(self, key) -> bool:
        return key in self.sheets

    def __getitem__(self, key):
        return storage.sheet_for(self.job_id, self.sheets[key].filename)


def _reextract(state: JobState, scale: float | None = None) -> list[str]:
    ing = _Ing(state.id, state.sheets)
    extracts, notes = extract_included(ing, state.sheets, scale)  # type: ignore[arg-type]
    state.extracts = extracts
    if not state.params.levels:
        state.params = default_params(extracts)
    else:
        known = {lp.level for lp in state.params.levels}
        for ex in extracts.values():
            if ex.level not in known:
                state.params.levels.append(
                    default_params({ex.sheet_id: ex}).levels[0]
                )
        state.params.levels = [
            lp for lp in state.params.levels if any(e.level == lp.level for e in extracts.values())
        ]
        state.params.levels.sort(key=lambda lp: lp.level)
    return notes


def _get(job_id: str) -> JobState:
    return storage.load_state(job_id)


def _extract_or_404(state: JobState, sheet_id: str) -> PlanExtract:
    ex = state.extracts.get(sheet_id)
    if ex is None:
        raise not_found("extraction for that sheet")
    return ex


def _sheet_or_404(state: JobState, sheet_id: str) -> SheetInfo:
    for s in state.sheets:
        if s.id == sheet_id:
            return s
    raise not_found("sheet")


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
@app.get("/api/jobs")
def jobs() -> list[dict]:
    return storage.list_jobs()


@app.post("/api/jobs")
async def create_job(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise AppError("no_files", "Add at least one PDF", 400)
    if len(files) > storage.MAX_FILES:
        raise AppError(
            "too_many", f"{len(files)} files; the limit is {storage.MAX_FILES}", 400
        )
    jid = storage.new_job()
    for f in files:
        storage.save_upload(jid, f.filename or "sheet.pdf", await f.read())

    from .pipeline import ingest

    ing = ingest(storage.upload_paths(jid))
    state = JobState(id=jid, created=storage.now(), title=ing.title, sheets=ing.sheets)
    notes = _reextract(state)
    storage.save_state(state)
    return {"job": state.model_dump(), "notes": notes}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return {"job": _get(job_id).model_dump()}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    storage.delete_job(job_id)
    return {"deleted": job_id}


# --------------------------------------------------------------------------- #
# sheets
# --------------------------------------------------------------------------- #
class SheetPatch(BaseModel):
    id: str
    include: bool | None = None
    level: int | None = None
    kind: str | None = None


@app.put("/api/jobs/{job_id}/sheets")
def update_sheets(job_id: str, patches: list[SheetPatch]) -> dict:
    state = _get(job_id)
    for p in patches:
        s = _sheet_or_404(state, p.id)
        if p.kind is not None:
            s.kind = p.kind
            s.kind_label = KIND_LABELS.get(p.kind, p.kind)
            s.evidence = "set by hand"
        if p.level is not None:
            s.level = p.level
            s.evidence = "set by hand"
            if s.kind != FLOOR_PLAN:
                s.kind = FLOOR_PLAN
                s.kind_label = level_name(p.level)
        if p.include is not None:
            s.include = p.include
        if s.include and s.level is None:
            raise AppError(
                "no_level",
                f"{s.filename} has no storey — set which floor it is before including it",
                400,
            )
    notes = _reextract(state)
    state.build = None
    storage.save_state(state)
    return {"job": state.model_dump(), "notes": notes}


@app.get("/api/jobs/{job_id}/sheets/{sheet_id}/plan.png")
def plan_png(job_id: str, sheet_id: str, dpi: int = 130) -> Response:
    state = _get(job_id)
    s = _sheet_or_404(state, sheet_id)
    path = os.path.join(storage.job_dir(job_id), "uploads", s.filename)
    if not os.path.exists(path):
        raise not_found("sheet file")
    png = render_page_png(path, dpi=max(60, min(300, dpi)))
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --------------------------------------------------------------------------- #
# geometry edits
# --------------------------------------------------------------------------- #
class ScaleRequest(BaseModel):
    px_per_ft: float | None = None
    # or: two points in plan feet (as currently displayed) and their true length
    p0: tuple[float, float] | None = None
    p1: tuple[float, float] | None = None
    length_ft: float | None = None


@app.put("/api/jobs/{job_id}/sheets/{sheet_id}/scale")
def set_scale(job_id: str, sheet_id: str, req: ScaleRequest) -> dict:
    state = _get(job_id)
    s = _sheet_or_404(state, sheet_id)
    ex = _extract_or_404(state, sheet_id)

    if req.px_per_ft:
        px = req.px_per_ft
    elif req.p0 and req.p1 and req.length_ft:
        if req.length_ft <= 0:
            raise AppError("bad_length", "The measured length must be positive", 400)
        drawn = ((req.p1[0] - req.p0[0]) ** 2 + (req.p1[1] - req.p0[1]) ** 2) ** 0.5
        if drawn <= 0:
            raise AppError("bad_points", "Pick two different points", 400)
        # the picked points are in the *current* feet, so convert back to points
        px = ex.scale.px_per_ft * drawn / req.length_ft
    else:
        raise AppError("bad_request", "Give px_per_ft, or two points and a length", 400)

    sheet = storage.sheet_for(job_id, s.filename)
    new = extract_plan(
        sheet, sheet_id, ex.level, ex.level_name, px_per_ft_override=px
    )
    new.scale.method = "manual"
    new.scale.confidence = "high"
    new.scale.note = f"calibrated by hand to {px:.3f} px/ft"
    state.extracts[sheet_id] = new
    state.build = None
    storage.save_state(state)
    return {"extract": new.model_dump()}


class OpeningPatch(BaseModel):
    wall_id: str
    opening_id: str | None = None  # None → add
    kind: str | None = None
    u0: float | None = None
    u1: float | None = None
    sill_ft: float | None = None
    head_ft: float | None = None
    delete: bool = False


@app.put("/api/jobs/{job_id}/sheets/{sheet_id}/openings")
def edit_openings(job_id: str, sheet_id: str, patches: list[OpeningPatch]) -> dict:
    state = _get(job_id)
    ex = _extract_or_404(state, sheet_id)
    walls = {w.id: w for w in ex.walls}
    for p in patches:
        w = walls.get(p.wall_id)
        if w is None:
            raise not_found(f"wall {p.wall_id}")
        if p.opening_id is None:
            if p.u0 is None or p.u1 is None:
                raise AppError("bad_request", "A new opening needs u0 and u1", 400)
            lo, hi = w.u_range()
            u0, u1 = sorted((max(p.u0, lo), min(p.u1, hi)))
            if u1 - u0 < 0.25:
                raise AppError(
                    "too_small", "That opening would be under 3 inches wide", 400
                )
            w.openings.append(
                Opening(
                    id=f"{w.id}m{len(w.openings)}",
                    kind=p.kind or "door",
                    u0=round(u0, 3),
                    u1=round(u1, 3),
                    sill_ft=p.sill_ft,
                    head_ft=p.head_ft,
                    source="manual",
                )
            )
            continue
        op = next((o for o in w.openings if o.id == p.opening_id), None)
        if op is None:
            raise not_found(f"opening {p.opening_id}")
        if p.delete:
            w.openings = [o for o in w.openings if o.id != p.opening_id]
            continue
        if p.kind:
            op.kind = p.kind  # type: ignore[assignment]
        if p.u0 is not None:
            op.u0 = p.u0
        if p.u1 is not None:
            op.u1 = p.u1
        op.sill_ft = p.sill_ft
        op.head_ft = p.head_ft
        op.source = "manual"
    state.build = None
    storage.save_state(state)
    return {"extract": ex.model_dump()}


@app.post("/api/jobs/{job_id}/sheets/{sheet_id}/reset")
def reset_sheet(job_id: str, sheet_id: str) -> dict:
    """Throw away manual edits and read the sheet again."""
    state = _get(job_id)
    s = _sheet_or_404(state, sheet_id)
    ex = _extract_or_404(state, sheet_id)
    sheet = storage.sheet_for(job_id, s.filename)
    state.extracts[sheet_id] = extract_plan(sheet, sheet_id, ex.level, ex.level_name)
    state.build = None
    storage.save_state(state)
    return {"extract": state.extracts[sheet_id].model_dump()}


# --------------------------------------------------------------------------- #
# parameters and build
# --------------------------------------------------------------------------- #
@app.put("/api/jobs/{job_id}/params")
def set_params(job_id: str, params: BuildParams) -> dict:
    state = _get(job_id)
    # Which heights are yours is decided here, by comparing what arrived with
    # what was stored — not by trusting the browser to declare it. That keeps
    # API clients honest and stops a pin drifting away from the value it pins.
    datum.pin_user_edits(state.params, params)
    datum.seed_defaults(params)
    state.params = params
    state.build = None
    storage.save_state(state)
    return {"job": state.model_dump()}


def _readme(state: JobState, summary: dict) -> str:
    lines = [
        "floor to rendered — 3D model export",
        "=" * 38,
        "",
        f"Project: {state.title or '(untitled)'}",
        f"Job:     {state.id}",
        f"Built:   {storage.now()}",
        "",
        "Files",
        "-----",
        "model.glb          glTF 2.0 binary. Blender: File ▸ Import ▸ glTF 2.0.",
        "                   Twinmotion: Import ▸ Geometry (Direct Import reads glTF).",
        "model.obj/.mtl     Wavefront, for anything that will not take glTF.",
        "blender_import.py  blender --python blender_import.py — imports the glb,",
        "                   adds a sun and a three-quarter camera.",
        "model.json         Everything the model was built from, including the",
        "                   scale, every wall and every opening.",
        "",
        "Units and orientation",
        "---------------------",
        "Metres, Y up in the file. Blender's glTF importer converts to Z up.",
    ]
    if summary.get("rotation_applied_deg"):
        lines.append(
            f"Rotated {summary['rotation_applied_deg']:.0f}° so north is −Z in the "
            "file (+Y once imported into Blender)."
        )
    lines += [
        "",
        "Storeys",
        "-------",
    ]
    for lv in summary.get("levels", []):
        lines.append(
            f"{lv['name']:<14} floor at {fmt_ft(lv['floor_elevation_ft']):>7}, "
            f"walls {fmt_ft(lv['wall_height_ft']):>7}, "
            f"{lv['walls']} walls, {lv['doors']} doors, {lv['windows']} windows, "
            f"{lv['area_sqft']:.0f} sq ft"
        )
    lines += [
        "",
        f"Overall height above ground: {fmt_ft(summary.get('overall_height_ft', 0))}",
        f"Triangles: {summary.get('triangles', 0)}",
        "",
        "What is measured and what is assumed",
        "------------------------------------",
        "Measured from the drawings: the plan — wall positions, wall thicknesses,",
        "openings, columns, room sizes, and the drawing scale, which is derived by",
        "measuring rooms against their own printed dimension labels.",
        "",
        "A floor plan states no heights, so every height below is either read off",
        "some other sheet in the set or assumed. Each one says which.",
        "",
    ]
    rows = datum.rows(state.params)
    if rows:
        w = max(len(r[0]) for r in rows)
        for what, value, story in rows:
            lines.append(f"{what:<{w}}  {value:>8}   {story}")
    else:
        lines.append("(no heights recorded)")
    return "\n".join(lines) + "\n"


@app.post("/api/jobs/{job_id}/build")
def build_model(job_id: str) -> dict:
    state = _get(job_id)
    extracts = list(state.extracts.values())
    if not extracts:
        raise AppError(
            "no_storeys",
            "No floor plans are included — mark at least one sheet as a storey",
            409,
        )
    road_xy = None
    lowest = min(state.extracts.values(), key=lambda e: e.level, default=None)
    if lowest is not None:
        si = next((s for s in state.sheets if s.id == lowest.sheet_id), None)
        if si:
            try:
                road_xy = site_mod.road_label_xy(
                    lowest, storage.sheet_for(job_id, si.filename).words
                )
            except Exception:
                road_xy = None
    result = build(extracts, state.params, road_xy=road_xy)
    if result.scene.triangle_count == 0:
        raise AppError(
            "empty_model", "Nothing to build: no walls survived the settings", 409
        )

    glb = write_glb(result.scene, extras={"summary": result.summary})
    import json as _json

    files: dict[str, bytes | str] = {
        "model.glb": glb,
        "model.obj": write_obj(result.scene),
        "model.mtl": write_mtl(result.scene),
        "blender_import.py": blender_script("model.glb", True),
        "model.json": _json.dumps(
            {
                "summary": result.summary,
                "params": state.params.model_dump(),
                "sheets": [s.model_dump() for s in state.sheets],
                "extracts": {k: v.model_dump() for k, v in state.extracts.items()},
            },
            indent=2,
        ),
    }
    files.update(texture_files(result.scene))
    storage.write_bundle(job_id, files, _readme(state, result.summary))
    state.build = result.summary
    storage.save_state(state)
    return {"summary": result.summary}


DOWNLOADS = {
    "model.glb": "model/gltf-binary",
    "model.obj": "text/plain",
    "model.mtl": "text/plain",
    "model.json": "application/json",
    "blender_import.py": "text/x-python",
    "README.txt": "text/plain",
    "model-bundle.zip": "application/zip",
}


@app.get("/api/jobs/{job_id}/download/{name}")
def download(job_id: str, name: str) -> FileResponse:
    if name not in DOWNLOADS:
        raise not_found(name)
    path = os.path.join(storage.build_dir(job_id), name)
    if not os.path.exists(path):
        raise AppError("not_built", "Build the model first", 409)
    return FileResponse(path, media_type=DOWNLOADS[name], filename=name)


@app.get("/api/catalog")
def catalog() -> dict:
    """What the materials step can offer: presets, surfaces and finishes."""
    per_slot = {
        "wall_ext": tex.WALL_TEXTURES,
        "wall_int": tex.WALL_TEXTURES,
        "base": ["stone", "brick", "concrete", "plaster", "none"],
        "trim": tex.WALL_TEXTURES,
        "roof": tex.FLAT_TEXTURES,
        "slab": ["concrete", "plaster", "screed", "none"],
        "column": ["concrete", "plaster", "stone", "none"],
        "door": ["wood", "metal", "none"],
        "glazing": ["none"],
        "railing": ["none", "metal"],
        "ground": tex.GROUND_TEXTURES,
        "drive": ["paving", "asphalt", "concrete", "none"],
        "boundary": tex.WALL_TEXTURES,
        "default": ["none"],
    }
    return {
        "slots": SLOTS,
        "presets": {k: {"label": v["label"], "note": v["note"], "slots": v["slots"]}
                    for k, v in PRESETS.items()},
        "textures": per_slot,
    }


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True})
