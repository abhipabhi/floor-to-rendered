"""Command line: a folder of plan PDFs in, a 3D model out.

    python -m app.cli example --out build
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from .blender import blender_script
from .build3d import build
from .glb import write_glb
from .obj import texture_files, write_mtl, write_obj
from .pipeline import default_params, extract_included, ingest, road_xy_for
from .units import fmt_ft


def collect(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "*.pdf")))
        else:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="PDF files or a folder of them")
    ap.add_argument("--out", default="build")
    ap.add_argument("--scale", type=float, default=None, help="px per foot override")
    ap.add_argument("--floor-to-floor", type=float, default=None, help="feet")
    ap.add_argument("--plinth", type=float, default=None, help="feet")
    ap.add_argument("--parapet", type=float, default=None, help="feet")
    args = ap.parse_args(argv)

    pdfs = collect(args.inputs)
    if not pdfs:
        print("no PDFs found", file=sys.stderr)
        return 2

    ing = ingest(pdfs)
    print(f"{len(ing.sheets)} sheets")
    for s in ing.sheets:
        mark = "→ storey" if s.include else "  skipped"
        print(f"  {mark}  {s.filename[:52]:54s} {s.kind_label}  ({s.evidence})")

    extracts, notes = extract_included(ing, ing.sheets, args.scale)
    if not extracts:
        print("\nno floor plans found — nothing to build", file=sys.stderr)
        return 1
    for n in notes:
        print(f"\n{n}")

    params = default_params(extracts)
    for lp in params.levels:
        if args.floor_to_floor:
            lp.floor_to_floor_ft = args.floor_to_floor
    if args.plinth is not None:
        params.plinth_ft = args.plinth
    if args.parapet is not None:
        params.parapet_ft = args.parapet

    print()
    for ex in sorted(extracts.values(), key=lambda e: e.level):
        s = ex.scale
        print(
            f"{ex.level_name}: {len(ex.walls)} walls, "
            f"{sum(1 for w in ex.walls for o in w.openings if o.kind == 'door')} doors, "
            f"{sum(1 for w in ex.walls for o in w.openings if o.kind == 'window')} windows, "
            f"{len(ex.columns)} columns, {len(ex.rooms)} rooms"
        )
        print(f"    scale {s.px_per_ft:.3f} px/ft [{s.confidence}] — {s.note}")
        for w in ex.warnings:
            print(f"    · {w}")

    result = build(
        list(extracts.values()), params, road_xy=road_xy_for(ing, extracts)
    )

    os.makedirs(args.out, exist_ok=True)
    glb_path = os.path.join(args.out, "model.glb")
    with open(glb_path, "wb") as fh:
        fh.write(write_glb(result.scene, extras={"summary": result.summary}))
    with open(os.path.join(args.out, "model.obj"), "w") as fh:
        fh.write(write_obj(result.scene))
    with open(os.path.join(args.out, "model.mtl"), "w") as fh:
        fh.write(write_mtl(result.scene))
    for rel, data in texture_files(result.scene).items():
        path = os.path.join(args.out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    with open(os.path.join(args.out, "blender_import.py"), "w") as fh:
        fh.write(
            blender_script(
                "model.glb",
                bool(result.summary.get("rotation_applied_deg") is not None),
                storeys=[lv["name"] for lv in result.summary.get("levels", [])],
            )
        )
    with open(os.path.join(args.out, "model.json"), "w") as fh:
        json.dump(
            {
                "summary": result.summary,
                "sheets": [s.model_dump() for s in ing.sheets],
                "params": params.model_dump(),
                "extracts": {k: v.model_dump() for k, v in extracts.items()},
            },
            fh,
            indent=2,
        )

    sz = result.summary.get("size_m") or [0, 0, 0]
    print(
        f"\n{result.summary['triangles']} triangles in "
        f"{len(result.scene.meshes)} groups; "
        f"{sz[0]:.2f} × {sz[2]:.2f} m on plan, {sz[1]:.2f} m tall "
        f"(ridge {fmt_ft(result.summary['overall_height_ft'])} above ground)"
    )
    print(f"wrote {args.out}/model.glb, model.obj, model.mtl, blender_import.py, model.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
