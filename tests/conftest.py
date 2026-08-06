"""Test fixtures.

The example drawing set is a real project's and is not published with the code,
so nothing here names a file. The sheets are found the way the app finds them —
by classifying each PDF in ``example/`` — which also means dropping a different
set in that folder exercises the same tests.

Everything that needs the drawings is marked :data:`needs_example` and skips
cleanly when the folder is empty.
"""

from __future__ import annotations

import glob
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EXAMPLE = os.path.join(ROOT, "example")
_PDFS = sorted(glob.glob(os.path.join(EXAMPLE, "*.pdf")))


def _discover() -> dict[str, str]:
    """Map a role — ground / first / layout / foundation — to a file path."""
    if not _PDFS:
        return {}
    from app.classify import FLOOR_PLAN, classify
    from app.pdfvec import load_sheet

    found: dict[str, str] = {}
    for path in _PDFS:
        try:
            sheet = load_sheet(path, os.path.basename(path))
        except Exception:
            continue
        cls = classify(
            sheet.text, os.path.basename(path), has_vectors=len(sheet.segs) > 20
        )
        if cls.kind == FLOOR_PLAN and cls.level == 0:
            found.setdefault("ground", path)
        elif cls.kind == FLOOR_PLAN and cls.level == 1:
            found.setdefault("first", path)
        elif cls.kind in ("layout", "foundation"):
            found.setdefault(cls.kind, path)
    return found


_SHEETS = _discover()

GF = _SHEETS.get("ground", "")
FF = _SHEETS.get("first", "")
LAYOUT = _SHEETS.get("layout", "")
FOOTING = _SHEETS.get("foundation", "")

needs_example = pytest.mark.skipif(
    not (GF and FF and LAYOUT and FOOTING),
    reason="example drawing set not present (it is not published with the code)",
)


@pytest.fixture(scope="session")
def gf_sheet():
    from app.pdfvec import load_sheet

    return load_sheet(GF, os.path.basename(GF))


@pytest.fixture(scope="session")
def ff_sheet():
    from app.pdfvec import load_sheet

    return load_sheet(FF, os.path.basename(FF))


@pytest.fixture(scope="session")
def gf_extract(gf_sheet):
    from app.extract import extract_plan

    return extract_plan(gf_sheet, "gf", 0, "Ground floor")


@pytest.fixture(scope="session")
def ff_extract(ff_sheet):
    from app.extract import extract_plan

    return extract_plan(ff_sheet, "ff", 1, "First floor")


@pytest.fixture
def example_pdfs():
    return list(_PDFS)
