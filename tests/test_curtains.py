"""Curtains behind the glass.

A window with nothing behind it is a hole, and a row of holes is most of what
makes a model read as a shell rather than a house. These are staging, like the
site: not measured, entirely a choice, and in their own groups.
"""

import pytest

from app import build3d
from app.build3d import build
from app.models import BuildParams, DetailParams, LevelParams
from conftest import needs_example
from app.units import FT_TO_M


# --------------------------------------------------------------------------- #
# the shape: drawn across, gathered into folds
# --------------------------------------------------------------------------- #
def test_the_curtain_closes_the_whole_opening():
    """The point is to hide the interior. Tied back at the sides it looks more
    like a curtain and does not do the job, because the middle — the part you
    see through — is still a hole."""
    from app.finish import materials_for, preset_slots
    from app.mesh import Scene
    from app.models import Wall

    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    wall = Wall(id="w", x0=0, y0=0, x1=10, y1=0.83, axis="h",
                exterior=True, outside="hi")
    build3d._curtains(scene, wall, 2.0, 8.0, 3.0, 7.0, 0.83, 1.0,
                      DetailParams(), "Test")
    m = scene.meshes[0]
    xs = [m.positions[i] / FT_TO_M for i in range(0, len(m.positions), 3)]
    assert min(xs) <= 2.05 and max(xs) >= 7.95, "it must span the opening"
    # and there is geometry across the middle, not only at the jambs
    mid = [x for x in xs if 4.4 < x < 5.6]
    assert mid, "nothing across the centre of the window"


def test_the_curtain_is_gathered_into_folds():
    """Flat, it is a blue card in a hole. The folds are what catch the light
    on one side and not the other, which is all the shading needed here."""
    from app.finish import materials_for, preset_slots
    from app.mesh import Scene
    from app.models import Wall

    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    wall = Wall(id="w", x0=0, y0=0, x1=10, y1=0.83, axis="h",
                exterior=True, outside="hi")
    build3d._curtains(scene, wall, 1.0, 7.0, 3.0, 7.0, 0.83, 1.0,
                      DetailParams(), "Test")
    m = scene.meshes[0]
    zs = {round(m.positions[i + 2] / FT_TO_M, 3)
          for i in range(0, len(m.positions), 3)}
    assert len(zs) >= 3, f"a flat sheet, not folds: depths {sorted(zs)}"
    assert max(zs) - min(zs) > 0.03, "the folds have no depth"


def test_a_wider_window_gets_more_folds_not_wider_ones():
    from app.finish import materials_for, preset_slots
    from app.mesh import Scene
    from app.models import Wall

    def folds(width):
        scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
        wall = Wall(id="w", x0=0, y0=0, x1=40, y1=0.83, axis="h",
                    exterior=True, outside="hi")
        build3d._curtains(scene, wall, 1.0, 1.0 + width, 3.0, 7.0, 0.83, 1.0,
                          DetailParams(), "Test")
        return scene.meshes[0].triangle_count

    assert folds(12.0) > folds(4.0), "the pleats should not just stretch"


# --------------------------------------------------------------------------- #
# in the model
# --------------------------------------------------------------------------- #
@needs_example
def _built(gf_extract, ff_extract, **detail):
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor"),
                LevelParams(level=1, name="First floor")],
        align_north=False,
        detail=DetailParams(**detail),
    )
    return build([gf_extract, ff_extract], params, road_xy=(15.0, 60.0))


@needs_example
def test_every_window_gets_curtains(gf_extract, ff_extract):
    result = _built(gf_extract, ff_extract)
    curtains = [m for m in result.scene.meshes if m.name.endswith("curtains")]
    assert curtains, "no curtains were built"
    assert all(m.material == "curtain" for m in curtains)
    assert sum(m.triangle_count for m in curtains) > 100


@needs_example
def test_turning_them_off_leaves_the_windows_bare(gf_extract, ff_extract):
    result = _built(gf_extract, ff_extract, curtains=False)
    assert not any(m.name.endswith("curtains") for m in result.scene.meshes)


@needs_example
def test_they_hang_inside_the_building(gf_extract, ff_extract):
    """Seen through the glass or not at all. A curtain that pokes out through
    the wall is worse than no curtain."""
    result = _built(gf_extract, ff_extract)
    curtains = [m for m in result.scene.meshes if m.name.endswith("curtains")]
    shell = [m for m in result.scene.meshes
             if "walls" in m.name and not m.name.endswith("curtains")]
    sb = _bounds(shell)
    cb = _bounds(curtains)
    for i in (0, 2):          # plan axes; the building's own footprint
        assert cb[i] >= sb[i] - 0.02, "sticks out of the building"
        assert cb[i + 3] <= sb[i + 3] + 0.02, "sticks out of the building"


@needs_example
def test_a_curtain_is_not_built_across_the_middle_of_the_window(
    gf_extract, ff_extract
):
    """They are tied back at the sides. Anything in the centre is a blind."""
    result = _built(gf_extract, ff_extract)
    curtains = [m for m in result.scene.meshes if m.name.endswith("curtains")]
    assert curtains
    # for each curtain group, the geometry has to fall in two clumps along its
    # own long axis, with a clear gap between them
    for m in curtains:
        xs = sorted(m.positions[i] for i in range(0, len(m.positions), 3))
        zs = sorted(m.positions[i + 2] for i in range(0, len(m.positions), 3))
        spread = max(max(xs) - min(xs), max(zs) - min(zs))
        assert spread > 0.3, m.name


def _bounds(meshes):
    b = [1e9, 1e9, 1e9, -1e9, -1e9, -1e9]
    for m in meshes:
        mb = m.bounds()
        for i in range(3):
            b[i] = min(b[i], mb[i])
            b[i + 3] = max(b[i + 3], mb[i + 3])
    return b


def test_a_window_too_small_to_curtain_gets_none():
    """A 2 ft ventilator with a pair of curtains in it looks like a mistake."""
    from app.finish import materials_for, preset_slots
    from app.mesh import Scene
    from app.models import Wall

    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    wall = Wall(id="w", x0=0, y0=0, x1=10, y1=0.83, axis="h",
                exterior=True, outside="hi")
    d = DetailParams()
    build3d._curtains(scene, wall, 1.0, 1.6, 4.0, 5.2, 0.83, 1.0, d, "Test")
    assert not scene.meshes, "too narrow and too short to curtain"
    build3d._curtains(scene, wall, 1.0, 5.0, 3.0, 7.0, 0.83, 1.0, d, "Test")
    assert scene.meshes, "a real window should get them"


def test_the_curtain_hangs_behind_the_glass_not_in_front_of_it():
    from app.finish import materials_for, preset_slots
    from app.mesh import Scene
    from app.models import Wall

    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    wall = Wall(id="w", x0=0, y0=0, x1=10, y1=0.83, axis="h",
                exterior=True, outside="hi")
    d = DetailParams()
    outer, normal = 0.83, 1.0
    build3d._curtains(scene, wall, 1.0, 5.0, 3.0, 7.0, outer, normal, d, "Test")
    m = scene.meshes[0]
    # "across the wall" is model z for a wall running along x
    zs = [m.positions[i + 2] / FT_TO_M for i in range(0, len(m.positions), 3)]
    assert max(zs) < outer - d.reveal_ft, "in front of the pane"


# --------------------------------------------------------------------------- #
# light fittings
# --------------------------------------------------------------------------- #
@needs_example
def test_the_facade_gets_light_fittings_where_a_house_has_them(
    gf_extract, ff_extract
):
    """Not spaced evenly along the wall — that reads as a car park. A house
    has a light either side of the way in, one in the porch, and a pair on
    the upper wall."""
    from app.build3d import level_elevations
    from app import facade
    from app.models import BuildParams as BP

    params = BP(levels=[LevelParams(level=0, name="Ground floor"),
                        LevelParams(level=1, name="First floor")])
    els = level_elevations([gf_extract, ff_extract], params)
    f = facade.front_frame([gf_extract, ff_extract], els, "+y", params.plinth_ft)
    panels = facade.compose([gf_extract, ff_extract], els, f, params)
    lamps = [p for p in panels if p.kind == "lamp"]
    assert len(lamps) >= 2 and len(lamps) % 2 == 0, "they come in pairs"
    assert all(p.depth_ft > 0 for p in lamps), "a fitting stands off the wall"
    # spread over more than one height: entrance, porch and upper wall
    heights = {round(p.z0, 1) for p in lamps}
    assert len(heights) >= 2, f"all at one height: {heights}"


@needs_example
def test_the_lamps_can_be_turned_off(gf_extract, ff_extract):
    from app.build3d import level_elevations
    from app import facade
    from app.models import BuildParams as BP, FacadeParams

    params = BP(levels=[LevelParams(level=0, name="Ground floor"),
                        LevelParams(level=1, name="First floor")],
                facade=FacadeParams(lamps=False))
    els = level_elevations([gf_extract, ff_extract], params)
    f = facade.front_frame([gf_extract, ff_extract], els, "+y", params.plinth_ft)
    panels = facade.compose([gf_extract, ff_extract], els, f, params)
    assert not any(p.kind == "lamp" for p in panels)


def test_a_lamp_is_emissive_and_a_wall_is_not():
    """A lamp is not a pale surface, it is a surface that emits. Without that
    a light fitting in a daylit render is a grey blob on the wall."""
    from app.finish import materials_for, preset_slots

    m = materials_for(preset_slots("elevation_spec"))
    assert m["light"].emissive > 1.0
    assert m["wall_ext"].emissive == 0.0
    assert m["curtain"].emissive == 0.0


def test_the_glb_declares_the_emissive_extension_it_uses():
    """A glTF that uses an extension without listing it in extensionsUsed is
    invalid, and the Khronos validator says so."""
    import json
    import struct

    from app.finish import materials_for, preset_slots
    from app.glb import write_glb
    from app.mesh import Scene

    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    scene.mesh("Lamp", "light").add_box(0, 0, 0, 1, 1, 1)
    data = write_glb(scene)
    # the JSON chunk of a glb: 12-byte header, then a length-prefixed chunk
    length = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + length])
    assert "KHR_materials_emissive_strength" in doc.get("extensionsUsed", [])
    mat = doc["materials"][0]
    assert mat["emissiveFactor"] != [0, 0, 0]
