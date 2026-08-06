"""The web API, exercised the way the page uses it."""

import io
import json
import os
import struct
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import EXAMPLE, GF, needs_example

pytestmark = needs_example


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("F2R_DATA", str(tmp_path / "jobs"))
    import importlib

    from app import storage

    importlib.reload(storage)
    from app import main

    importlib.reload(main)
    return TestClient(main.app)


@pytest.fixture()
def job(client, example_pdfs):
    files = [
        ("files", (os.path.basename(p), open(p, "rb").read(), "application/pdf"))
        for p in example_pdfs
    ]
    r = client.post("/api/jobs", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_classifies_the_set(job):
    j = job["job"]
    assert len(j["sheets"]) == 8
    kinds = {s["kind"] for s in j["sheets"]}
    assert {"floor_plan", "layout", "foundation", "tie_beam", "services", "detail",
            "reference"} <= kinds
    included = [s for s in j["sheets"] if s["include"]]
    assert len(included) == 2
    assert sorted(s["level"] for s in included) == [0, 1]
    assert len(j["extracts"]) == 2
    assert any("one drawing scale" in n for n in job["notes"])


def test_non_pdf_is_refused(client):
    r = client.post(
        "/api/jobs", files=[("files", ("x.pdf", b"not a pdf at all", "application/pdf"))]
    )
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "not_pdf"


def test_build_and_download_everything(client, job):
    jid = job["job"]["id"]
    r = client.post(f"/api/jobs/{jid}/build")
    assert r.status_code == 200, r.text
    summary = r.json()["summary"]
    assert summary["triangles"] > 1000
    assert len(summary["levels"]) == 2

    glb = client.get(f"/api/jobs/{jid}/download/model.glb")
    assert glb.status_code == 200
    assert glb.content[:4] == b"glTF"
    assert struct.unpack("<I", glb.content[8:12])[0] == len(glb.content)

    for name in ("model.obj", "model.mtl", "model.json", "blender_import.py", "README.txt"):
        assert client.get(f"/api/jobs/{jid}/download/{name}").status_code == 200

    z = client.get(f"/api/jobs/{jid}/download/model-bundle.zip")
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert "model.glb" in zf.namelist()
        assert "README.txt" in zf.namelist()

    doc = json.loads(client.get(f"/api/jobs/{jid}/download/model.json").text)
    assert doc["summary"]["overall_height_ft"] == pytest.approx(25.0)
    assert len(doc["extracts"]) == 2


def test_download_before_build_says_so(client, job):
    r = client.get(f"/api/jobs/{job['job']['id']}/download/model.glb")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_built"


def test_plan_png(client, job):
    sid = next(s["id"] for s in job["job"]["sheets"] if s["include"])
    r = client.get(f"/api/jobs/{job['job']['id']}/sheets/{sid}/plan.png")
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_including_a_sheet_adds_a_storey(client, job):
    jid = job["job"]["id"]
    layout = next(s for s in job["job"]["sheets"] if s["kind"] == "layout")
    r = client.put(
        "/api/jobs/%s/sheets" % jid, json=[{"id": layout["id"], "level": 2, "include": True}]
    )
    assert r.status_code == 200, r.text
    j = r.json()["job"]
    assert len(j["extracts"]) == 3
    assert len(j["params"]["levels"]) == 3


def test_a_sheet_cannot_be_a_storey_without_a_level(client, job):
    jid = job["job"]["id"]
    detail = next(s for s in job["job"]["sheets"] if s["kind"] == "detail")
    r = client.put("/api/jobs/%s/sheets" % jid, json=[{"id": detail["id"], "include": True}])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_level"


def test_editing_an_opening(client, job):
    jid = job["job"]["id"]
    sid, ex = next(iter(job["job"]["extracts"].items()))
    wall = next(w for w in ex["walls"] if w["openings"])
    op = wall["openings"][0]

    r = client.put(
        f"/api/jobs/{jid}/sheets/{sid}/openings",
        json=[
            {
                "wall_id": wall["id"],
                "opening_id": op["id"],
                "kind": "door",
                "sill_ft": 0.5,
                "head_ft": 8.0,
            }
        ],
    )
    assert r.status_code == 200, r.text
    edited = next(
        o
        for w in r.json()["extract"]["walls"]
        if w["id"] == wall["id"]
        for o in w["openings"]
        if o["id"] == op["id"]
    )
    assert edited["kind"] == "door"
    assert edited["head_ft"] == 8.0
    assert edited["source"] == "manual"

    # the edit survives a reload
    again = client.get(f"/api/jobs/{jid}").json()["job"]
    kept = next(
        o
        for w in again["extracts"][sid]["walls"]
        if w["id"] == wall["id"]
        for o in w["openings"]
        if o["id"] == op["id"]
    )
    assert kept["head_ft"] == 8.0


def test_adding_and_deleting_an_opening(client, job):
    jid = job["job"]["id"]
    sid, ex = next(iter(job["job"]["extracts"].items()))
    wall = max(ex["walls"], key=lambda w: abs(w["x1"] - w["x0"]) + abs(w["y1"] - w["y0"]))
    u0, u1 = (wall["x0"], wall["x1"]) if wall["axis"] == "h" else (wall["y0"], wall["y1"])
    mid = (u0 + u1) / 2
    before = len(wall["openings"])

    r = client.put(
        f"/api/jobs/{jid}/sheets/{sid}/openings",
        json=[{"wall_id": wall["id"], "u0": mid - 1.5, "u1": mid + 1.5, "kind": "door"}],
    )
    assert r.status_code == 200, r.text
    w2 = next(w for w in r.json()["extract"]["walls"] if w["id"] == wall["id"])
    assert len(w2["openings"]) == before + 1
    added = w2["openings"][-1]
    assert added["source"] == "manual"

    r = client.put(
        f"/api/jobs/{jid}/sheets/{sid}/openings",
        json=[{"wall_id": wall["id"], "opening_id": added["id"], "delete": True}],
    )
    w3 = next(w for w in r.json()["extract"]["walls"] if w["id"] == wall["id"])
    assert len(w3["openings"]) == before


def test_a_new_opening_is_clipped_to_its_wall(client, job):
    jid = job["job"]["id"]
    sid, ex = next(iter(job["job"]["extracts"].items()))
    wall = ex["walls"][0]
    r = client.put(
        f"/api/jobs/{jid}/sheets/{sid}/openings",
        json=[{"wall_id": wall["id"], "u0": -500.0, "u1": 500.0, "kind": "window"}],
    )
    assert r.status_code == 200
    w2 = next(w for w in r.json()["extract"]["walls"] if w["id"] == wall["id"])
    lo, hi = (w2["x0"], w2["x1"]) if w2["axis"] == "h" else (w2["y0"], w2["y1"])
    added = w2["openings"][-1]
    # stored to the nearest thousandth of a foot
    assert added["u0"] >= lo - 1e-3 and added["u1"] <= hi + 1e-3


def test_hand_calibration_rescales_the_sheet(client, job):
    jid = job["job"]["id"]
    sid, ex = next(iter(job["job"]["extracts"].items()))
    width_before = ex["bounds"][2] - ex["bounds"][0]
    px_before = ex["scale"]["px_per_ft"]

    # measure a stretch the extractor thinks is 10 ft and call it 10'-6"
    r = client.put(
        f"/api/jobs/{jid}/sheets/{sid}/scale",
        json={"p0": [0, 0], "p1": [10, 0], "length_ft": 10.5},
    )
    assert r.status_code == 200, r.text
    after = r.json()["extract"]
    assert after["scale"]["method"] == "manual"
    assert after["scale"]["px_per_ft"] == pytest.approx(px_before * 10 / 10.5, rel=1e-3)
    assert (after["bounds"][2] - after["bounds"][0]) == pytest.approx(
        width_before * 10.5 / 10, rel=0.01
    )

    # and reset puts it back
    r = client.post(f"/api/jobs/{jid}/sheets/{sid}/reset")
    assert r.json()["extract"]["scale"]["method"] == "room_labels"
    assert r.json()["extract"]["scale"]["px_per_ft"] == pytest.approx(px_before, rel=0.02)


def test_bad_calibration_input(client, job):
    jid = job["job"]["id"]
    sid = next(iter(job["job"]["extracts"]))
    r = client.put(f"/api/jobs/{jid}/sheets/{sid}/scale", json={})
    assert r.status_code == 400
    r = client.put(
        f"/api/jobs/{jid}/sheets/{sid}/scale",
        json={"p0": [0, 0], "p1": [0, 0], "length_ft": 10},
    )
    assert r.status_code == 400


def test_params_change_the_model(client, job):
    jid = job["job"]["id"]
    j = client.get(f"/api/jobs/{jid}").json()["job"]
    params = j["params"]
    for lp in params["levels"]:
        lp["floor_to_floor_ft"] = 12.0
    params["plinth_ft"] = 3.0
    params["parapet_ft"] = 0.0
    params["roof"] = "flat"
    assert client.put(f"/api/jobs/{jid}/params", json=params).status_code == 200

    s = client.post(f"/api/jobs/{jid}/build").json()["summary"]
    assert s["overall_height_ft"] == pytest.approx(3.0 + 12.0 * 2)
    assert "Parapet" not in [g["name"] for g in s["groups"]]


def test_excluding_every_storey_refuses_to_build(client, job):
    jid = job["job"]["id"]
    patches = [
        {"id": s["id"], "include": False} for s in job["job"]["sheets"] if s["include"]
    ]
    client.put(f"/api/jobs/{jid}/sheets", json=patches)
    r = client.post(f"/api/jobs/{jid}/build")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "no_storeys"


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_job_listing_and_delete(client, job):
    jid = job["job"]["id"]
    assert any(j["id"] == jid for j in client.get("/api/jobs").json())
    client.delete(f"/api/jobs/{jid}")
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_index_and_static_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "floor" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/three.module.min.js").status_code == 200
