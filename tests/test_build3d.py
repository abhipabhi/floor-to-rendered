"""The 3D build: heights come from parameters, plan comes from the drawing."""

import json
import struct

import pytest

from app.build3d import BuildResult, build, level_elevations
from app.footprint import enclosed_mask, footprint_rects, mark_exterior, ring_rects, wall_raster
from app.glb import write_glb
from app.mesh import Mesh, Scene
from app.models import (
    BuildParams,
    Column,
    LevelParams,
    Opening,
    PlanExtract,
    ScaleInfo,
    Wall,
)
from app.obj import write_mtl, write_obj
from app.units import FT_TO_M
from conftest import needs_example


# --------------------------------------------------------------------------- #
# mesh primitives
# --------------------------------------------------------------------------- #
def test_box_is_twelve_triangles_and_has_outward_normals():
    m = Mesh("test", "wall")
    assert m.add_box(0, 0, 0, 1, 2, 3)
    assert m.triangle_count == 12
    assert m.vertex_count == 24
    assert m.bounds() == (0, 0, 0, 1, 2, 3)
    # every normal is a unit axis vector
    for i in range(0, len(m.normals), 3):
        n = m.normals[i : i + 3]
        assert sorted(abs(v) for v in n) == [0.0, 0.0, 1.0]


def test_degenerate_box_is_skipped():
    m = Mesh("test", "wall")
    assert not m.add_box(0, 0, 0, 0, 2, 3)
    assert m.triangle_count == 0


# --------------------------------------------------------------------------- #
# a single wall with openings
# --------------------------------------------------------------------------- #
def _wall_extract(openings):
    w = Wall(id="w0", axis="h", x0=0.0, y0=0.0, x1=20.0, y1=10 / 12, openings=openings)
    return PlanExtract(
        sheet_id="s",
        level=0,
        level_name="Ground floor",
        scale=ScaleInfo(px_per_ft=10.0, method="manual"),
        origin_px=(0.0, 0.0),
        bounds=(0.0, 0.0, 20.0, 10 / 12),
        walls=[w],
    )


def _params(**kw):
    p = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor", floor_to_floor_ft=10.0)],
        ground=False,
        columns=False,
        align_north=False,
        roof="none",
        **kw,
    )
    return p


def test_a_plain_wall_is_one_box():
    r = build([_wall_extract([])], _params(glazing=False, doors=False))
    walls = [m for m in r.scene.meshes if m.name.endswith("walls")]
    assert len(walls) == 1
    assert walls[0].triangle_count == 12


def test_a_window_splits_the_wall_into_three_boxes():
    op = Opening(id="o", kind="window", u0=8.0, u1=13.0)
    r = build([_wall_extract([op])], _params(glazing=False, doors=False))
    wall = next(m for m in r.scene.meshes if m.name.endswith("walls"))
    # two piers + sill + lintel
    assert wall.triangle_count == 12 * 4


def test_a_door_leaves_no_sill():
    op = Opening(id="o", kind="door", u0=8.0, u1=11.0)
    r = build([_wall_extract([op])], _params(glazing=False, doors=False))
    wall = next(m for m in r.scene.meshes if m.name.endswith("walls"))
    # two piers + lintel only
    assert wall.triangle_count == 12 * 3


def test_the_hole_is_actually_a_hole():
    """No wall geometry occupies the opening between sill and head."""
    op = Opening(id="o", kind="window", u0=8.0, u1=13.0)
    r = build([_wall_extract([op])], _params(glazing=False, doors=False))
    wall = next(m for m in r.scene.meshes if m.name.endswith("walls"))
    # sample the middle of the opening, 5 ft above this storey's floor
    x = 10.5 * FT_TO_M
    y = (2.0 + 5.0) * FT_TO_M  # plinth + 5 ft
    for i in range(0, len(wall.positions), 72):  # one box at a time
        box = wall.positions[i : i + 72]
        xs, ys = box[0::3], box[1::3]
        inside_x = min(xs) < x < max(xs)
        inside_y = min(ys) < y < max(ys)
        assert not (inside_x and inside_y), "geometry left inside the opening"


def test_glazing_and_leaves_are_separate_groups():
    ops = [
        Opening(id="o1", kind="window", u0=3.0, u1=7.0),
        Opening(id="o2", kind="door", u0=12.0, u1=15.0),
    ]
    r = build([_wall_extract(ops)], _params())
    names = {m.name: m for m in r.scene.meshes}
    assert any(n.endswith("glazing") for n in names)
    assert any(n.endswith("doors") for n in names)
    glazing = next(m for n, m in names.items() if n.endswith("glazing"))
    assert glazing.material == "glazing"
    assert r.scene.materials["glazing"].alpha < 1.0


def test_openings_can_override_the_level_defaults():
    a = build(
        [_wall_extract([Opening(id="o", kind="window", u0=8.0, u1=13.0)])],
        _params(glazing=True, doors=False),
    )
    b = build(
        [
            _wall_extract(
                [Opening(id="o", kind="window", u0=8.0, u1=13.0, sill_ft=1.0, head_ft=8.0)]
            )
        ],
        _params(glazing=True, doors=False),
    )
    ga = next(m for m in a.scene.meshes if m.name.endswith("glazing")).bounds()
    gb = next(m for m in b.scene.meshes if m.name.endswith("glazing")).bounds()
    assert (gb[4] - gb[1]) > (ga[4] - ga[1])


# --------------------------------------------------------------------------- #
# stacking
# --------------------------------------------------------------------------- #
def test_level_elevations_stack_on_the_plinth():
    extracts = [
        _wall_extract([]),
        PlanExtract(
            sheet_id="s2",
            level=1,
            level_name="First floor",
            scale=ScaleInfo(px_per_ft=10.0, method="manual"),
            origin_px=(0.0, 0.0),
            bounds=(0.0, 0.0, 20.0, 1.0),
            walls=[Wall(id="w1", axis="h", x0=0.0, y0=0.0, x1=20.0, y1=10 / 12)],
        ),
    ]
    params = BuildParams(
        plinth_ft=2.0,
        levels=[
            LevelParams(level=0, name="Ground floor", floor_to_floor_ft=10.0),
            LevelParams(level=1, name="First floor", floor_to_floor_ft=9.5),
        ],
    )
    el = level_elevations(extracts, params)
    assert el[0][0] == pytest.approx(2.0)
    assert el[1][0] == pytest.approx(12.0)
    assert el[1][2] == pytest.approx(9.5 - 5 / 12)


def test_overall_height_is_the_sum_of_the_settings():
    r = build(
        [_wall_extract([])],
        BuildParams(
            plinth_ft=2.0,
            parapet_ft=3.0,
            roof="flat_parapet",
            levels=[LevelParams(level=0, name="Ground floor", floor_to_floor_ft=10.0)],
            ground=False,
            columns=False,
            align_north=False,
        ),
    )
    assert r.summary["overall_height_ft"] == pytest.approx(15.0)


# --------------------------------------------------------------------------- #
# footprint
# --------------------------------------------------------------------------- #
def _box_of_walls(w=20.0, h=30.0, t=10 / 12):
    return [
        Wall(id="n", axis="h", x0=0, y0=0, x1=w, y1=t),
        Wall(id="s", axis="h", x0=0, y0=h - t, x1=w, y1=h),
        Wall(id="w", axis="v", x0=0, y0=0, x1=t, y1=h),
        Wall(id="e", axis="v", x0=w - t, y0=0, x1=w, y1=h),
    ]


def test_footprint_covers_the_enclosed_area():
    rects = footprint_rects(_box_of_walls())
    area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
    assert area == pytest.approx(20 * 30, rel=0.05)
    # and the rectangles are disjoint
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            ox = min(a[2], b[2]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[1], b[1])
            assert ox <= 1e-9 or oy <= 1e-9


def test_footprint_edges_sit_exactly_on_the_walls():
    """A slab that misses its own wall by an inch is visible in the model."""
    walls = _box_of_walls(w=20.0, h=30.0)
    walls.append(Wall(id="bump", axis="h", x0=4.0, y0=-1.5, x1=9.0, y1=0.0))
    rects = footprint_rects(walls)
    coords_x = {round(v, 4) for w in walls for v in (w.x0, w.x1)}
    coords_y = {round(v, 4) for w in walls for v in (w.y0, w.y1)}
    for r in rects:
        assert round(r[0], 4) in coords_x and round(r[2], 4) in coords_x
        assert round(r[1], 4) in coords_y and round(r[3], 4) in coords_y
    # the bump-out is part of the plate, and nothing beyond it is
    assert min(r[1] for r in rects) == pytest.approx(-1.5)
    assert max(r[3] for r in rects) == pytest.approx(30.0)


def test_a_wide_opening_still_gets_a_floor():
    """A car port is open to the street but is still part of the slab."""
    walls = _box_of_walls()
    walls[1].x1 = 9.0  # leave an 11 ft gap in the south wall
    rects = footprint_rects(walls)
    area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
    assert area == pytest.approx(20 * 30, rel=0.08)


def test_parapet_ring_closes():
    t = 5 / 12
    ring = ring_rects(_box_of_walls(), t)
    assert ring
    xs0 = min(r[0] for r in ring)
    ys0 = min(r[1] for r in ring)
    xs1 = max(r[2] for r in ring)
    ys1 = max(r[3] for r in ring)
    # exactly on the outline, not within a raster cell of it
    assert (xs0, ys0, xs1, ys1) == pytest.approx((0.0, 0.0, 20.0, 30.0), abs=1e-6)
    # a band of the right thickness, not a filled slab
    for r in ring:
        assert min(r[2] - r[0], r[3] - r[1]) == pytest.approx(t, abs=1e-6)
    area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in ring)
    assert area < 0.35 * 20 * 30


def test_parapet_follows_a_stepped_outline_all_the_way_round():
    walls = _box_of_walls(w=20.0, h=30.0)
    walls.append(Wall(id="bump", axis="h", x0=4.0, y0=-2.0, x1=9.0, y1=0.0))
    ring = ring_rects(walls, 5 / 12)
    # the ring reaches the far edge of the bump-out
    assert min(r[1] for r in ring) == pytest.approx(-2.0, abs=1e-6)
    # and the two sides of the bump-out are ringed too
    verticals = [r for r in ring if (r[2] - r[0]) < (r[3] - r[1])]
    assert any(abs(r[0] - 4.0) < 1e-6 for r in verticals)
    assert any(abs(r[2] - 9.0) < 1e-6 for r in verticals)


def test_roof_slab_takes_the_roof_material_and_its_own_thickness():
    """The finish catalog has offered a roof surface since the finish system
    landed, but the roof slab never asked for it — so it wore the floor-slab
    material and picking a roof colour changed nothing you could see."""
    params = BuildParams(
        plinth_ft=0.0,
        roof="flat_parapet",
        roof_slab_thickness_ft=8 / 12,
        ground=False,
        columns=False,
        align_north=False,
        levels=[
            LevelParams(
                level=0,
                name="Ground floor",
                floor_to_floor_ft=10.0,
                slab_thickness_ft=5 / 12,
            )
        ],
    )
    ex = PlanExtract(
        sheet_id="s",
        level=0,
        level_name="Ground floor",
        scale=ScaleInfo(px_per_ft=10.0, method="manual"),
        origin_px=(0.0, 0.0),
        bounds=(0.0, 0.0, 20.0, 30.0),
        walls=_box_of_walls(),
    )
    result = build([ex], params)
    roof = next(m for m in result.scene.meshes if m.name == "Roof slab")
    assert roof.material == "roof"
    # and it is 8", the thickness asked for, not the storey's 5"
    b = roof.bounds()
    assert (b[4] - b[1]) == pytest.approx(8 / 12 * FT_TO_M, abs=1e-4)


def test_railings_are_built_at_railing_height():
    walls = _box_of_walls()
    walls.append(
        Wall(id="rail", axis="h", x0=2.0, y0=30.0, x1=14.0, y1=30.25, kind="railing")
    )
    params = BuildParams(
        plinth_ft=0.0,
        railing_ft=3.5,
        roof="none",
        ground=False,
        columns=False,
        align_north=False,
        levels=[LevelParams(level=0, name="Ground floor", floor_to_floor_ft=10.0)],
    )
    ex = PlanExtract(
        sheet_id="s",
        level=0,
        level_name="Ground floor",
        scale=ScaleInfo(px_per_ft=10.0, method="manual"),
        origin_px=(0.0, 0.0),
        bounds=(0.0, 0.0, 20.0, 30.25),
        walls=walls,
    )
    result = build([ex], params)
    rail = next(m for m in result.scene.meshes if m.name.endswith("railings"))
    assert rail.material == "railing"
    b = rail.bounds()
    assert (b[4] - b[1]) == pytest.approx(3.5 * FT_TO_M, abs=1e-4)
    walls_mesh = next(m for m in result.scene.meshes if m.name.endswith("walls"))
    assert (walls_mesh.bounds()[4]) > b[4], "walls are taller than railings"


def test_a_column_holding_a_corner_is_part_of_the_floor_plate():
    """Both walls stop short and the column holds the corner between them.

    Leaving the column out puts a notch in the slab and a step in the parapet
    above it, which reads as the corner pier stopping below the roof.
    """
    walls = [
        Wall(id="n", axis="h", x0=0, y0=0, x1=20, y1=10 / 12),
        Wall(id="w", axis="v", x0=0, y0=0, x1=10 / 12, y1=30),
        Wall(id="e", axis="v", x0=19.17, y0=0, x1=20, y1=27.5),  # stops short
        Wall(id="s", axis="h", x0=0, y0=29.17, x1=18.5, y1=30),  # stops short
    ]
    column = Column(id="c", x0=19.17, y0=27.9, x1=20.0, y1=29.15)

    rects = footprint_rects(walls, columns=[column])
    assert max(r[2] for r in rects) == pytest.approx(20.0, abs=1e-6)
    assert max(r[3] for r in rects) == pytest.approx(30.0, abs=1e-6)

    ring = ring_rects(walls, 5 / 12, columns=[column])
    assert any(abs(r[2] - 20.0) < 1e-6 and r[3] > 29.0 for r in ring), (
        "the parapet turns the corner"
    )


def test_mark_exterior():
    walls = _box_of_walls()
    walls.append(Wall(id="mid", axis="h", x0=1, y0=14, x1=19, y1=14.4))
    mark_exterior(walls)
    assert all(w.exterior for w in walls[:4])
    assert not walls[4].exterior


def test_mark_exterior_records_which_face_looks_out():
    """Knowing a wall is external is not enough to hang anything on it.

    On this box the north and west walls face out across their x0/y0 side and
    the south and east walls across their x1/y1 side; the spine wall faces in
    on both. A sunshade, a reveal or an IFC material layer needs that
    distinction, which ``exterior`` alone throws away.
    """
    walls = _box_of_walls()
    walls.append(Wall(id="mid", axis="h", x0=1, y0=14, x1=19, y1=14.4))
    mark_exterior(walls)
    assert {w.id: w.outside for w in walls} == {
        "n": "lo",
        "s": "hi",
        "w": "lo",
        "e": "hi",
        "mid": None,
    }


def test_enclosed_mask_has_free_space_around_it():
    raster = wall_raster(_box_of_walls())
    inside = enclosed_mask(raster)
    assert not inside[0].any() and not inside[-1].any()
    assert not inside[:, 0].any() and not inside[:, -1].any()


# --------------------------------------------------------------------------- #
# exporters
# --------------------------------------------------------------------------- #
def _scene():
    s = Scene(materials={"wall": __import__("app.mesh", fromlist=["Material"]).Material("wall", (0.8, 0.8, 0.8))})
    s.mesh("Test walls", "wall").add_box(0, 0, 0, 1, 2, 3)
    return s


def test_glb_is_a_valid_container():
    data = write_glb(_scene())
    magic, version, total = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67
    assert version == 2
    assert total == len(data)
    assert len(data) % 4 == 0

    off, chunks = 12, []
    while off < len(data):
        length, ctype = struct.unpack("<II", data[off : off + 8])
        chunks.append((ctype, length))
        assert length % 4 == 0
        off += 8 + length
    assert off == len(data)
    assert [c[0] for c in chunks] == [0x4E4F534A, 0x004E4942]


def test_glb_json_describes_the_scene():
    data = write_glb(_scene())
    jlen = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20 : 20 + jlen].decode("utf-8"))
    assert doc["asset"]["version"] == "2.0"
    assert len(doc["meshes"]) == 1
    assert doc["meshes"][0]["name"] == "Test walls"
    prim = doc["meshes"][0]["primitives"][0]
    pos = doc["accessors"][prim["attributes"]["POSITION"]]
    assert pos["count"] == 24
    assert pos["min"] == [0, 0, 0] and pos["max"] == [1, 2, 3]
    assert doc["accessors"][prim["indices"]]["count"] == 36
    # every accessor's view must fit inside the buffer
    total = doc["buffers"][0]["byteLength"]
    for v in doc["bufferViews"]:
        assert v["byteOffset"] + v["byteLength"] <= total


def test_obj_and_mtl():
    s = _scene()
    obj = write_obj(s)
    assert obj.count("\nv ") == 24
    assert obj.count("\nf ") == 12
    assert "o Test walls" in obj
    assert "usemtl wall" in obj
    assert "newmtl wall" in write_mtl(s)


# --------------------------------------------------------------------------- #
# the example set, end to end
# --------------------------------------------------------------------------- #
@needs_example
def test_the_road_side_corner_is_square(ff_extract):
    """Both walls stop at the corner column, which used to leave the slab
    notched and the parapet stepped above the corner pier."""
    rects = footprint_rects(ff_extract.walls, columns=ff_extract.columns)
    x1 = max(w.x1 for w in ff_extract.walls if not w.is_railing)
    y1 = max(w.y1 for w in ff_extract.walls if not w.is_railing)
    assert max(r[2] for r in rects) == pytest.approx(x1, abs=0.05)
    assert max(r[3] for r in rects) == pytest.approx(y1, abs=0.05)
    # the corner itself is covered, not cut away
    assert any(
        r[2] > x1 - 0.05 and r[3] > y1 - 0.05 for r in rects
    ), "the road-side corner is part of the floor plate"


@needs_example
def test_full_build(example_pdfs):
    from app.pipeline import default_params, extract_included, ingest

    ing = ingest(example_pdfs)
    extracts, _ = extract_included(ing, ing.sheets)
    assert len(extracts) == 2
    result = build(list(extracts.values()), default_params(extracts))
    assert isinstance(result, BuildResult)

    names = [m.name for m in result.scene.meshes]
    for expect in ("Ground floor walls", "First floor walls", "Roof slab", "Parapet"):
        assert expect in names
    assert result.scene.triangle_count > 1200

    # 2 storeys at 10 ft on a 2 ft plinth, with a 3 ft parapet
    assert result.summary["overall_height_ft"] == pytest.approx(25.0)
    # the drawing's own title block says 1211 sq ft per floor
    for lv in result.summary["levels"]:
        assert lv["area_sqft"] == pytest.approx(1211, rel=0.12)
    # north was read and snapped to a right angle
    assert result.summary["rotation_applied_deg"] % 90 == pytest.approx(0.0)

    data = write_glb(result.scene, extras={"summary": result.summary})
    assert data[:4] == b"glTF"
    assert len(data) > 10_000
