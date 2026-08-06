"""Job storage: one directory per job, plain files, no database.

    data/jobs/<id>/uploads/*.pdf     exactly what was uploaded
    data/jobs/<id>/state.json        JobState, including every manual edit
    data/jobs/<id>/build/            the exported model
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone

from .errors import AppError, not_found
from .models import JobState
from .pdfvec import Sheet, load_sheet

ROOT = os.environ.get("F2R_DATA", os.path.join(os.getcwd(), "data", "jobs"))
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_FILES = 30

_sheet_cache: dict[tuple[str, str, float], Sheet] = {}


def job_dir(job_id: str) -> str:
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    if not safe or safe != job_id:
        raise not_found("job")
    return os.path.join(ROOT, safe)


def new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    os.makedirs(os.path.join(job_dir(jid), "uploads"), exist_ok=True)
    return jid


def save_upload(job_id: str, filename: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(
            "too_large",
            f"{filename} is {len(data) // 1024 // 1024} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB",
            413,
        )
    if not data[:5].startswith(b"%PDF"):
        raise AppError("not_pdf", f"{filename} is not a PDF", 415)
    base = os.path.basename(filename).replace("/", "_") or "sheet.pdf"
    path = os.path.join(job_dir(job_id), "uploads", base)
    n = 1
    while os.path.exists(path):
        stem, ext = os.path.splitext(base)
        path = os.path.join(job_dir(job_id), "uploads", f"{stem}-{n}{ext}")
        n += 1
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def upload_paths(job_id: str) -> list[str]:
    d = os.path.join(job_dir(job_id), "uploads")
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".pdf")
    )


def state_path(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "state.json")


def save_state(state: JobState) -> None:
    with open(state_path(state.id), "w") as fh:
        fh.write(state.model_dump_json(indent=2))


def load_state(job_id: str) -> JobState:
    p = state_path(job_id)
    if not os.path.exists(p):
        raise not_found("job")
    with open(p) as fh:
        return JobState.model_validate(json.load(fh))


def list_jobs() -> list[dict]:
    """Every saved job, newest first.

    Sorted on the stored timestamp, not the directory name: job ids are random
    hex, so listing them alphabetically puts them in no order at all.
    """
    if not os.path.isdir(ROOT):
        return []
    out = []
    for jid in os.listdir(ROOT):
        p = os.path.join(ROOT, jid, "state.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p) as fh:
                s = json.load(fh)
            out.append(
                {
                    "id": s["id"],
                    "created": s["created"],
                    "title": s.get("title", ""),
                    "sheets": len(s.get("sheets", [])),
                    "storeys": len(s.get("extracts", {})),
                }
            )
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda j: j["created"], reverse=True)


def delete_job(job_id: str) -> None:
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def sheet_for(job_id: str, filename: str) -> Sheet:
    """Parse a sheet, memoised on its modification time."""
    path = os.path.join(job_dir(job_id), "uploads", filename)
    if not os.path.exists(path):
        raise not_found(filename)
    key = (job_id, filename, os.path.getmtime(path))
    if key not in _sheet_cache:
        if len(_sheet_cache) > 40:
            _sheet_cache.clear()
        _sheet_cache[key] = load_sheet(path, filename)
    return _sheet_cache[key]


def build_dir(job_id: str) -> str:
    d = os.path.join(job_dir(job_id), "build")
    os.makedirs(d, exist_ok=True)
    return d


def write_bundle(job_id: str, files: dict[str, bytes | str], readme: str) -> str:
    """Write the export set plus a zip of everything."""
    d = build_dir(job_id)
    for name, content in files.items():
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(path, mode) as fh:
            fh.write(content)
    with open(os.path.join(d, "README.txt"), "w") as fh:
        fh.write(readme)
    zip_path = os.path.join(d, "model-bundle.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in list(files) + ["README.txt"]:
            z.write(os.path.join(d, name), name)
    return zip_path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
