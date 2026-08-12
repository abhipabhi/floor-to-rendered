"""Fasād — the front composed as panels with a projection depth.

The supplied elevation document is the specification: every coloured area on it
is a rectangle, a material and a ``lvl`` tag saying how far it stands proud.
These tests hold the composition to that shape, and to the building's own
proportions rather than to numbers that happen to suit one house.
"""

import pytest

from app import facade
from app.build3d import build, level_elevations
from app.mesh import Scene
from app.models import BuildParams, FacadeParams, LevelParams, Panel
from app.finish import materials_for, preset_slots
from conftest import needs_example


def _frame(side="+y"):
    return facade.Frame(side=side, face=40.0, u0=0.0, u1=30.0, z_ground=0.0, z_top=22.0)


# --------------------------------------------------------------------------- #
# the frame: where the front is
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "side,expect_axis",
    [("+y", "y"), ("-y", "y"), ("+x", "x"), ("-x", "x")],
)
def test_a_panel_lands_on_the_face_whichever_side_the_road_is(side, expect_axis):
    f = _frame(side)
    x0, y0, z0, x1, y1, z1 = f.box(2.0, 6.0, 3.0, 9.0, 1.0)
    assert (z0, z1) == (3.0, 9.0)
    if expect_axis == "y":
        assert (x0, x1) == (2.0, 6.0), "u runs along x for a road on y"
        assert min(y0, y1) <= 40.0 <= max(y0, y1) + 1e-9
    else:
        assert (y0, y1) == (2.0, 6.0), "u runs along y for a road on x"
        assert min(x0, x1) <= 40.0 <= max(x0, x1) + 1e-9


def test_a_projection_stands_outwards_and_a_recess_cuts_back():
    f = _frame("+y")
    _x0, out0, _z0, _x1, out1, _z1 = f.box(0, 5, 0, 5, 2.0)
    assert max(out0, out1) > 40.0, "a projection reaches towards the street"
    _x0, in0, _z0, _x1, in1, _z1 = f.box(0, 5, 0, 5, -1.0)
    assert min(in0, in1) < 40.0, "a recess goes the other way"


@needs_example
def test_the_front_is_found_on_the_side_the_plan_calls_road(gf_extract, ff_extract):
    extracts = [gf_extract, ff_extract]
    params = BuildParams(levels=[
        LevelParams(level=0, name="Ground floor"),
        LevelParams(level=1, name="First floor"),
    ])
    elevations = level_elevations(extracts, params)
    f = facade.front_frame(extracts, elevations, "+y", params.plinth_ft)
    assert f is not None
    assert f.side == "+y"
    # the face is a wall the building actually has, near its own bounds
    assert f.face == pytest.approx(max(e.bounds[3] for e in extracts), abs=1.5)
    assert f.width > 20.0 and f.height > 15.0


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #
@needs_example
def _composed(gf_extract, ff_extract, **kw):
    extracts = [gf_extract, ff_extract]
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor"),
                LevelParams(level=1, name="First floor")],
        facade=FacadeParams(**kw),
    )
    elevations = level_elevations(extracts, params)
    f = facade.front_frame(extracts, elevations, "+y", params.plinth_ft)
    return f, facade.compose(extracts, elevations, f, params), params


@needs_example
def test_the_composition_uses_the_document_vocabulary(gf_extract, ff_extract):
    _f, panels, _p = _composed(gf_extract, ff_extract)
    kinds = {p.kind for p in panels}
    for want in ("field", "band", "frame", "clad", "fin", "canopy"):
        assert want in kinds, want


@needs_example
def test_every_panel_carries_a_projection_depth(gf_extract, ff_extract):
    """The lvl tag is the whole point — a panel without one is just a colour."""
    _f, panels, _p = _composed(gf_extract, ff_extract)
    for p in panels:
        assert isinstance(p.depth_ft, float)
    assert any(p.depth_ft > 0 for p in panels), "something has to stand proud"
    assert max(p.depth_ft for p in panels) == pytest.approx(3.15, abs=0.01), (
        "the canopy carries the document's own lvl +3'2\""
    )


@needs_example
def test_panels_stay_on_the_building(gf_extract, ff_extract):
    f, panels, _p = _composed(gf_extract, ff_extract)
    for p in panels:
        if p.kind == "canopy":
            continue  # the canopy oversails on purpose
        assert p.u0 >= f.u0 - 1.5 and p.u1 <= f.u1 + 1.5, p.label
        assert p.z0 >= -0.1 and p.z1 <= f.z_top + 1.5, p.label


@needs_example
def test_the_composition_scales_with_the_building_not_with_a_constant(
    gf_extract, ff_extract
):
    """A facade written against one house and hard-coded is useless on the
    next. The bands sit on the real floor lines and the frames on the real
    openings, so both move when the building does."""
    f, panels, params = _composed(gf_extract, ff_extract)
    elevations = level_elevations([gf_extract, ff_extract], params)
    floor_lines = {round(elevations[lv][0], 1) for lv in elevations if lv > 0}
    bands = {round((p.z0 + p.z1) / 2, 1) for p in panels if p.kind == "band"}
    assert bands & floor_lines, "a band should sit on a real floor line"


# --------------------------------------------------------------------------- #
# box frames are frames
# --------------------------------------------------------------------------- #
def test_a_box_frame_is_a_surround_not_a_plate():
    """Built solid it bricks up the window it was drawn around — which is
    exactly what happened the first time."""
    p = Panel(id="p", kind="frame", u0=0, u1=6, z0=0, z1=6,
              depth_ft=0.6, hole=(1, 5, 1, 5))
    pieces = facade._pieces(p)
    assert len(pieces) == 4
    # nothing covers the middle of the hole
    for u0, u1, z0, z1 in pieces:
        assert not (u0 < 3 < u1 and z0 < 3 < z1), "the opening is blocked"


def test_a_panel_with_no_hole_is_one_rectangle():
    p = Panel(id="p", kind="clad", u0=0, u1=4, z0=0, z1=9, depth_ft=0.3)
    assert facade._pieces(p) == [(0, 4, 0, 9)]


@needs_example
def test_every_frame_leaves_its_opening_clear(gf_extract, ff_extract):
    _f, panels, _p = _composed(gf_extract, ff_extract)
    frames = [p for p in panels if p.kind == "frame"]
    assert frames
    for p in frames:
        assert p.hole is not None, "a frame with no hole is a plate"


# --------------------------------------------------------------------------- #
# building it
# --------------------------------------------------------------------------- #
def test_the_field_panel_is_drawn_but_not_built():
    """It is the wall, which already exists — it is in the list so the
    elevation drawing has a background to sit on."""
    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    panels = [
        Panel(id="a", kind="field", u0=0, u1=10, z0=0, z1=10, depth_ft=0.0),
        Panel(id="b", kind="band", u0=0, u1=10, z0=4, z1=5, depth_ft=0.5),
    ]
    built = facade.build(scene, panels, _frame())
    assert built == 1
    assert not any("field" in m.name for m in scene.meshes)


@needs_example
def test_the_facade_reaches_the_model_and_says_so(gf_extract, ff_extract):
    extracts = [gf_extract, ff_extract]
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor"),
                LevelParams(level=1, name="First floor")],
        align_north=False,
    )
    result = build(extracts, params, road_xy=(15.0, 60.0))
    summary = result.summary["facade"]
    assert summary["side"] == "+y"
    assert summary["panels"] > 5 and summary["built"] > 0
    assert any(m.name.startswith("Façade") for m in result.scene.meshes)
    # and the front's outward direction is recorded, or no camera can find it
    assert len(summary["normal_xz"]) == 2


@needs_example
def test_turning_the_facade_off_leaves_the_building_alone(gf_extract, ff_extract):
    extracts = [gf_extract, ff_extract]
    base = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor"),
                LevelParams(level=1, name="First floor")],
        align_north=False,
        facade=FacadeParams(enabled=False),
    )
    result = build(extracts, base, road_xy=(15.0, 60.0))
    assert not any(m.name.startswith("Façade") for m in result.scene.meshes)
    assert result.summary["facade"] == {}
