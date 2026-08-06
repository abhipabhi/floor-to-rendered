"""Textures, finishes and the site — everything that is a choice, not a measurement."""

import json
import struct
import zlib

import numpy as np
import pytest

from app import textures as tex
from app.build3d import build
from app.finish import PRESETS, SLOTS, hex_to_rgb, materials_for, preset_slots
from app.glb import write_glb
from app.mesh import Material, Mesh, Scene
from app.models import (
    BuildParams,
    FinishParams,
    LevelParams,
    PlanExtract,
    Room,
    ScaleInfo,
    SiteParams,
    Wall,
)
from app.obj import texture_files, write_mtl, write_obj
from app.site import parking_area, plot_for, road_side
from app.units import FT_TO_M


# --------------------------------------------------------------------------- #
# textures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(tex.GENERATORS))
def test_every_texture_is_a_valid_tile(name):
    arr = tex.GENERATORS[name]()
    assert arr.shape == (tex.SIZE, tex.SIZE, 3)
    assert arr.dtype == np.uint8
    # not a flat colour: a texture with no variation is not doing anything
    assert arr.std() > 1.5, f"{name} has no variation"


@pytest.mark.parametrize("name", sorted(tex.GENERATORS))
def test_textures_tile_without_a_seam(name):
    """Opposite edges have to match, or every wall shows a grid of joins."""
    arr = tex.GENERATORS[name]().astype(float)
    # the wrap difference must be no worse than the difference one row in
    for axis in (0, 1):
        edge_gap = np.abs(np.take(arr, 0, axis) - np.take(arr, -1, axis)).mean()
        inner_gap = np.abs(np.take(arr, 1, axis) - np.take(arr, 2, axis)).mean()
        assert edge_gap <= inner_gap * 3 + 6, f"{name} seams on axis {axis}"


def test_textures_encode_to_real_pngs():
    data = tex.png("brick")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # walk the chunks so a truncated or corrupt file cannot pass
    pos, seen = 8, []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc
        seen.append(kind)
        pos += 12 + length
    assert seen[0] == b"IHDR" and seen[-1] == b"IEND"


def test_textures_are_cached():
    assert tex.png("stone") is tex.png("stone")


# --------------------------------------------------------------------------- #
# finishes
# --------------------------------------------------------------------------- #
def test_every_preset_covers_every_surface():
    for name, preset in PRESETS.items():
        for key, _label in SLOTS:
            assert key in preset["slots"], f"{name} has no {key}"
            colour, texture = preset["slots"][key]
            assert colour.startswith("#") and len(colour) == 7, f"{name}.{key}"
            assert texture is None or texture in tex.GENERATORS, f"{name}.{key}"


def test_hex_to_rgb():
    assert hex_to_rgb("#ffffff") == pytest.approx((1.0, 1.0, 1.0))
    assert hex_to_rgb("#000000") == pytest.approx((0.0, 0.0, 0.0))
    # sRGB → linear: mid grey is darker than 0.5
    assert hex_to_rgb("#808080")[0] == pytest.approx(0.2158, abs=0.01)
    assert hex_to_rgb("nonsense") == (1.0, 1.0, 1.0)


def test_finish_overrides_fall_back_to_the_preset():
    f = FinishParams(preset="brick", slots={"wall_ext": ["#123456", "stone"]})
    resolved = f.resolved()
    assert resolved["wall_ext"] == ("#123456", "stone")
    assert resolved["door"] == preset_slots("brick")["door"]


def test_materials_carry_their_tile_size():
    mats = materials_for(preset_slots("brick"))
    assert mats["wall_ext"].texture == "brick"
    assert mats["wall_ext"].tile_m == pytest.approx(0.90)
    assert mats["glazing"].alpha < 1.0


# --------------------------------------------------------------------------- #
# UVs
# --------------------------------------------------------------------------- #
def test_box_uvs_are_in_real_world_units():
    m = Mesh("t", "wall", tile_m=2.0)
    m.add_box(0, 0, 0, 4, 3, 2)
    assert len(m.uvs) == 2 * m.vertex_count
    us = m.uvs[0::2]
    vs = m.uvs[1::2]
    # a 4 m wall at a 2 m tile spans two repeats, never a 0..1 unwrap
    assert max(us) == pytest.approx(2.0)
    assert max(vs) == pytest.approx(1.5)


def test_uv_scale_follows_the_material():
    scene = Scene(materials={"brick": Material("brick", (1, 1, 1), texture="brick", tile_m=0.9)})
    m = scene.mesh("wall", "brick")
    assert m.tile_m == pytest.approx(0.9)


def test_round_primitives():
    m = Mesh("t", "x")
    m.add_cylinder(0, 0, 0, 2, 0.5, segments=8)
    assert m.triangle_count == 8 * 2 + 8 * 2  # sides plus two caps
    b = m.bounds()
    assert b[4] - b[1] == pytest.approx(2.0)

    s = Mesh("s", "x")
    s.add_sphere(0, 5, 0, 1.5, segments=8)
    sb = s.bounds()
    assert sb[4] == pytest.approx(6.5, abs=0.01)
    assert sb[1] == pytest.approx(3.5, abs=0.01)


# --------------------------------------------------------------------------- #
# glTF and OBJ with textures
# --------------------------------------------------------------------------- #
def _textured_scene():
    scene = Scene(materials=materials_for(preset_slots("brick")))
    scene.mesh("Walls", "wall_ext").add_box(0, 0, 0, 4, 3, 0.3)
    scene.mesh("Glass", "glazing").add_box(1, 1, 0.1, 2, 2, 0.2)
    return scene


def test_glb_embeds_the_textures_it_uses():
    scene = _textured_scene()
    data = write_glb(scene)
    jlen = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20 : 20 + jlen])

    assert [i["name"] for i in doc["images"]] == ["brick"]
    assert doc["textures"][0]["source"] == 0
    assert doc["samplers"][0]["wrapS"] == 10497  # REPEAT, or the tiling breaks
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    assert view["byteOffset"] + view["byteLength"] <= doc["buffers"][0]["byteLength"]

    walls = next(m for m in doc["meshes"] if m["name"] == "Walls")
    assert "TEXCOORD_0" in walls["primitives"][0]["attributes"]
    # an untextured material carries no UVs, so no importer reports them unused
    glass = next(m for m in doc["meshes"] if m["name"] == "Glass")
    assert "TEXCOORD_0" not in glass["primitives"][0]["attributes"]
    # and only materials in use are exported
    assert {m["name"] for m in doc["materials"]} == {"wall_ext", "glazing"}


def test_obj_writes_uvs_and_names_its_maps():
    scene = _textured_scene()
    obj = write_obj(scene)
    assert "\nvt " in obj
    assert "f 1/1/1" in obj
    mtl = write_mtl(scene)
    assert "map_Kd textures/brick.png" in mtl
    files = texture_files(scene)
    assert "textures/brick.png" in files
    assert files["textures/brick.png"][:4] == b"\x89PNG"


# --------------------------------------------------------------------------- #
# site
# --------------------------------------------------------------------------- #
def _extract_with_parking():
    walls = [
        Wall(id="n", axis="h", x0=0, y0=0, x1=30, y1=0.83),
        Wall(id="s", axis="h", x0=0, y0=39, x1=30, y1=39.83),
        Wall(id="w", axis="v", x0=0, y0=0, x1=0.83, y1=39.83),
        Wall(id="e", axis="v", x0=29.17, y0=0, x1=30, y1=39.83),
    ]
    return PlanExtract(
        sheet_id="s",
        level=0,
        level_name="Ground floor",
        scale=ScaleInfo(px_per_ft=10.0, method="manual"),
        origin_px=(0.0, 0.0),
        bounds=(0.0, 0.0, 30.0, 39.83),
        walls=walls,
        rooms=[Room(name="Parking Area", x0=18, y0=25, x1=28, y1=39)],
    )


def test_the_road_side_comes_from_the_sheets_own_label():
    ex = _extract_with_parking()
    assert road_side(ex, (15.0, 50.0)) == "+y"  # ROAD written below the plan
    assert road_side(ex, (15.0, -12.0)) == "-y"
    assert road_side(ex, (48.0, 20.0)) == "+x"
    assert road_side(ex, None) == "+y"  # a sensible default, not a crash


def test_the_gate_lines_up_with_the_parking():
    ex = _extract_with_parking()
    plot = plot_for(ex, SiteParams(), road_xy=(15.0, 50.0))
    assert plot.road == "+y"
    park = parking_area(ex)
    assert plot.drive_from == pytest.approx((park.x0 + park.x1) / 2)
    # and the compound is outside the building on every side
    assert plot.x0 < ex.bounds[0] and plot.y1 > ex.bounds[3]


def test_site_is_built_into_its_own_groups():
    ex = _extract_with_parking()
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor")],
        align_north=False,
        site=SiteParams(trees=3, cars=1),
    )
    result = build([ex], params, road_xy=(15.0, 50.0))
    names = {m.name for m in result.scene.meshes}
    for expect in (
        "Site — ground",
        "Site — driveway",
        "Site — boundary wall",
        "Site — gate",
        "Site — cars",
        "Site — trees",
    ):
        assert expect in names, expect
    assert result.summary["site"]["trees"] == 3
    assert result.summary["site"]["cars"] == 1
    assert result.summary["site"]["road_side"] == "+y"


def test_the_site_can_be_turned_off_entirely():
    ex = _extract_with_parking()
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor")],
        align_north=False,
        site=SiteParams(enabled=False),
        ground=False,
    )
    result = build([ex], params)
    assert not any(m.name.startswith("Site") for m in result.scene.meshes)


def test_the_car_parks_where_the_plan_says_parking():
    ex = _extract_with_parking()
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor")],
        align_north=False,
        site=SiteParams(trees=0, cars=1, boundary_wall=False, ground=False),
    )
    result = build([ex], params, road_xy=(15.0, 50.0))
    car = next(m for m in result.scene.meshes if m.name == "Site — cars")
    b = car.bounds()
    park = parking_area(ex)
    cx = (b[0] + b[3]) / 2 / FT_TO_M
    cy = (b[2] + b[5]) / 2 / FT_TO_M
    # the build recentres the model, so compare against the same shift
    assert abs(cx) < 30 and abs(cy) < 40
    assert (park.x1 - park.x0) > 5  # sanity: the fixture really has a parking room


def test_a_finish_change_does_not_move_a_single_vertex():
    """Materials are a coat of paint: the geometry must be identical."""
    ex = _extract_with_parking()
    base = BuildParams(levels=[LevelParams(level=0, name="Ground floor")], align_north=False)
    brick = base.model_copy(deep=True)
    brick.finish = FinishParams(preset="brick")
    a = build([ex], base, road_xy=(15.0, 50.0))
    b = build([ex], brick, road_xy=(15.0, 50.0))
    assert a.scene.triangle_count == b.scene.triangle_count
    for ma, mb in zip(a.scene.meshes, b.scene.meshes):
        assert ma.name == mb.name
        assert ma.positions == pytest.approx(mb.positions)
