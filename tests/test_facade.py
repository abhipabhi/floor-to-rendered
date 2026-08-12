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
    # with one wall behind the whole thing: a frame that records no wall has
    # nothing to fix a panel to, and correctly builds nothing
    return facade.Frame(
        side=side, face=40.0, u0=0.0, u1=30.0, z_ground=0.0, z_top=22.0,
        faces=[facade.Wallface(0.0, 30.0, 0.0, 22.0, 40.0)],
    )


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
# zones: the massing the composition is laid out on
# --------------------------------------------------------------------------- #
def test_a_nine_inch_step_is_a_construction_joint_not_a_change_of_massing():
    frame = facade.Frame(
        side="+y", face=40.75, u0=0.0, u1=30.0, z_ground=0.0, z_top=20.0,
        faces=[facade.Wallface(0.0, 15.0, 0.0, 20.0, 40.0),
               facade.Wallface(15.0, 15.75, 0.0, 20.0, 40.75),  # a pier
               facade.Wallface(15.75, 30.0, 0.0, 20.0, 40.0)],
    )
    zs = facade.zones(frame)
    assert len(zs) == 2, "a 9 inch pier is not a zone of its own"
    assert all(z.front == 40.0 for z in zs), (
        "and it must not drag a zone's plane forward with it"
    )


def test_a_stub_of_wall_does_not_make_an_overhung_storey_walled():
    """A zone counts a wall that covers most of it. Without that rule the last
    9 inches of ground floor poking under a 12 ft overhang would say the
    ground storey is walled, and the porch would never be found."""
    frame = facade.Frame(
        side="+y", face=41.0, u0=0.0, u1=24.0, z_ground=0.0, z_top=20.0,
        faces=[facade.Wallface(0.0, 13.0, 0.0, 10.0, 40.0),    # ground floor
               facade.Wallface(11.0, 24.0, 10.0, 20.0, 41.0)],  # first, overhanging
    )
    open_below = [z for z in facade.zones(frame) if z.walls and not z.walled(0, 10)]
    assert open_below, "the overhung ground storey is open"
    assert open_below[0].u1 == 24.0, "and it runs to the end of the overhang"


def test_the_plinth_follows_the_wall_directly_above_it():
    """Below the lowest wall there is nothing to read. Taking the frontmost
    wall in the column instead of the nearest stood the plinth a foot proud
    of the storey it carries."""
    frame = facade.Frame(
        side="+y", face=42.0, u0=0.0, u1=10.0, z_ground=0.0, z_top=22.0,
        faces=[facade.Wallface(0.0, 10.0, 2.0, 12.0, 40.0),
               facade.Wallface(0.0, 10.0, 12.0, 22.0, 42.0)],
    )
    assert frame.wall_at(5, 0.0, 2.0) is None, "no wall below the plinth line"
    assert frame.face_at(5, 0.0, 2.0) == 40.0, "the storey above it, not the front"
    assert frame.face_at(5, 13.0, 20.0) == 42.0
    assert frame.face_at(5, 11.5, 12.5) == 42.0, "a band spanning both takes the front"


@needs_example
def test_the_front_breaks_into_the_zones_the_building_has(gf_extract, ff_extract):
    extracts = [gf_extract, ff_extract]
    params = BuildParams(levels=[LevelParams(level=0, name="Ground floor"),
                                 LevelParams(level=1, name="First floor")])
    elevations = level_elevations(extracts, params)
    f = facade.front_frame(extracts, elevations, "+y", params.plinth_ft)
    zs = facade.zones(f)

    assert len(zs) >= 2, "a front that steps is not one plane"
    ends = {round(w.u0, 2) for w in f.faces} | {round(w.u1, 2) for w in f.faces}
    for zn in zs[1:]:
        assert round(zn.u0, 2) in ends, "a zone boundary is the end of a real wall"
    tall = [z for z in zs if z.walls and z.top >= f.z_top - 1.0]
    low = [z for z in zs if z.walls and z.top < f.z_top - 1.0]
    assert tall and low, (
        "the example is a two-storey block beside a single-storey wing"
    )


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
    for want in ("field", "recess", "mass", "fin", "frame", "canopy"):
        assert want in kinds, want


@needs_example
def test_the_elements_stack_in_a_depth_ladder(gf_extract, ff_extract):
    """A facade reads as layers or it reads as wallpaper. The screen stands in
    front of the canopy, the canopy in front of the balcony slabs, those in
    front of the clad mass, and the mass in front of the wall. Flatten that
    ladder and no amount of colour brings the composition back."""
    _f, panels, _p = _composed(gf_extract, ff_extract)
    depth = {}
    for p in panels:
        depth[p.kind] = max(depth.get(p.kind, -99), p.depth_ft)
    assert depth["field"] == 0.0
    assert depth["recess"] < 0, "a recess goes back, not forward"
    assert depth["mass"] > depth["band"] > depth["field"]
    assert depth["fin"] > depth["mass"]
    assert depth["canopy"] > depth["fin"]


def _supported(frame, u: float, z0: float, z1: float) -> float:
    """How much of a panel's height at ``u`` the building actually carries.

    Walled counts, and so does anything below the lowest wall in the column —
    that is the plinth, which the wall above it stands on.
    """
    runs = sorted((max(w.z0, z0), min(w.z1, z1)) for w in frame.faces
                  if w.u0 - 0.05 <= u <= w.u1 + 0.05 and w.z1 > z0 and w.z0 < z1)
    foot = min((w.z0 for w in frame.faces if w.u0 - 0.05 <= u <= w.u1 + 0.05),
               default=z1)
    if foot > z0:
        runs = sorted(runs + [(z0, min(foot, z1))])
    total, at = 0.0, z0
    for a, b in runs:
        total += max(0.0, b - max(a, at))
        at = max(at, b)
    return total / max(z1 - z0, 1e-9)


@needs_example
def test_no_element_is_composed_where_the_building_is_not(gf_extract, ff_extract):
    """The regression this whole zone model exists for.

    The composition used to be laid out in fractions of the overall width,
    which on a building with a single-storey wing put the balcony recess, its
    three slabs and the full-height screen over the wing's *roof* — a balcony
    in mid-air, and the clutter that came with it.
    """
    f, panels, _p = _composed(gf_extract, ff_extract)
    free = {"canopy", "fin", "post"}   # these stand off the building on purpose
    checked = 0
    for p in panels:
        if p.kind in free or p.z1 - p.z0 < 2.0:
            continue  # a band spans a floor line; that is not a defect
        if p.kind == "recess" and "porch" in p.label.lower():
            continue  # the porch *is* the absence of wall
        for u in (p.u0 + 0.1, (p.u0 + p.u1) / 2, p.u1 - 0.1):
            got = _supported(f, min(max(u, f.u0), f.u1), p.z0, p.z1)
            assert got > 0.9, (
                f"{p.kind} {p.label!r} at u={u:.1f} has building behind only "
                f"{got:.0%} of its height"
            )
            checked += 1
    assert checked, "nothing was checked"


@needs_example
def test_an_open_storey_under_an_overhang_is_drawn_as_a_porch(gf_extract, ff_extract):
    """The example's first floor overhangs an open ground storey. That is the
    deepest shadow the elevation has and it is already in the drawing — far
    better than inventing a balcony somewhere else on the wall."""
    f, panels, _p = _composed(gf_extract, ff_extract)
    porch = [p for p in panels if p.kind == "recess" and "porch" in p.label.lower()]
    assert porch, "an overhung open storey should be composed as a porch"
    p = porch[0]
    u = (p.u0 + p.u1) / 2
    assert p.depth_ft < 0, "a porch goes back, not forward"
    assert f.wall_at(u, p.z0 + 0.1, p.z1 - 0.1) is None, "a porch has no wall"
    assert f.face_at(u, p.z0, p.z1) is not None, (
        "but it is cut back from the floor above, which is what carries it"
    )


@needs_example
def test_the_canopy_does_not_oversail_a_single_storey_wing(gf_extract, ff_extract):
    f, panels, _p = _composed(gf_extract, ff_extract)
    zs = facade.zones(f)
    low = [z for z in zs if z.walls and z.top < f.z_top - 1.0]
    assert low, "the example has a single-storey wing"
    canopy = next(p for p in panels if p.kind == "canopy")
    for zn in low:
        over = min(canopy.u1, zn.u1) - max(canopy.u0, zn.u0)
        assert over < zn.width * 0.5, (
            "the roof canopy is at the top of the two-storey block; run across "
            "the wing it floats ten feet above its roof"
        )


@needs_example
def test_the_screen_and_the_clad_bay_land_in_different_zones(gf_extract, ff_extract):
    f, panels, _p = _composed(gf_extract, ff_extract)
    zs = facade.zones(f)

    def zone_of(u):
        return next(z for z in zs if z.u0 - 0.1 <= u <= z.u1 + 0.1)

    fins = [p for p in panels if p.kind == "fin"]
    mass = [p for p in panels if p.kind == "mass"]
    assert fins and mass
    a = zone_of(sum((p.u0 + p.u1) / 2 for p in fins) / len(fins))
    b = zone_of(sum((p.u0 + p.u1) / 2 for p in mass) / len(mass))
    assert a is not b, "the two heavy elements belong to different parts of the house"


@needs_example
def test_the_composition_is_asymmetric(gf_extract, ff_extract):
    """The reference elevations put their weight on one side. A screen and a
    clad bay sitting on top of each other is not a composition."""
    _f, panels, _p = _composed(gf_extract, ff_extract)
    fins = [p for p in panels if p.kind == "fin"]
    mass = [p for p in panels if p.kind == "mass"]
    assert fins and mass
    fin_mid = sum((p.u0 + p.u1) / 2 for p in fins) / len(fins)
    mass_mid = sum((p.u0 + p.u1) / 2 for p in mass) / len(mass)
    assert abs(fin_mid - mass_mid) > 6.0, "the two heavy elements must sit apart"


@needs_example
def test_the_screen_runs_past_the_canopy(gf_extract, ff_extract):
    """It is the one element tying the whole height together; stopped at the
    roof it becomes a stripe."""
    f, panels, _p = _composed(gf_extract, ff_extract)
    fins = [p for p in panels if p.kind == "fin"]
    canopy = next(p for p in panels if p.kind == "canopy")
    assert min(p.z0 for p in fins) <= f.z_ground + 0.1
    assert max(p.z1 for p in fins) > canopy.z1, "it has to pass the canopy"


@needs_example
def test_the_canopy_caps_the_building_as_it_is_built(gf_extract, ff_extract):
    """Placed at the top of the walls it is a dark band halfway up a white
    parapet. It belongs on top of the parapet, finishing the building."""
    f, panels, params = _composed(gf_extract, ff_extract)
    canopy = next(p for p in panels if p.kind == "canopy")
    assert canopy.z0 == pytest.approx(f.z_top + params.parapet_ft, abs=0.01)


@needs_example
def test_no_solid_panel_bricks_up_a_window(gf_extract, ff_extract):
    """Elements are placed by where the building steps, so the clad bay lands
    over the very window it exists to frame — and built solid it walls it up,
    which it did to two of this building's three windows.

    Fins and posts are excluded: a screen standing in front of a window is the
    point of a screen.
    """
    f, panels, params = _composed(gf_extract, ff_extract)
    elevations = level_elevations([gf_extract, ff_extract], params)
    openings = facade.front_openings([gf_extract, ff_extract], elevations, f, params)
    assert openings, "the front has windows"
    for p in panels:
        if p.kind not in facade.SOLID or p.hole:
            continue
        for a, b, z0, z1, _k in openings:
            assert not (p.u0 < b - 0.1 and p.u1 > a + 0.1
                        and p.z0 < z1 - 0.1 and p.z1 > z0 + 0.1), (
                f"{p.kind} {p.label!r} covers the opening at "
                f"u {a:.1f}..{b:.1f}, z {z0:.1f}..{z1:.1f}"
            )


@needs_example
@pytest.mark.parametrize("arrangement", ["layered", "framed", "quiet"])
def test_every_arrangement_composes(gf_extract, ff_extract, arrangement):
    _f, panels, _p = _composed(gf_extract, ff_extract, arrangement=arrangement)
    assert len(panels) >= 3
    assert all(p.u1 > p.u0 and p.z1 > p.z0 for p in panels)


@needs_example
def test_the_quiet_arrangement_leaves_nothing_sticking_out(gf_extract, ff_extract):
    _f, panels, _p = _composed(gf_extract, ff_extract, arrangement="quiet")
    assert not any(p.kind in ("fin", "mass") for p in panels)
    assert max(p.depth_ft for p in panels) < 2.0


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
    f, panels, params = _composed(gf_extract, ff_extract)
    # `z_top` is the top of the *walls*. The parapet stands three feet above it
    # and the canopy caps that, so the building as built is taller than z_top.
    cap = facade._cap(params, f.z_top) + params.facade.canopy_thickness_ft
    for p in panels:
        if p.kind == "canopy":
            continue  # the canopy oversails on purpose
        assert p.u0 >= f.u0 - 1.5 and p.u1 <= f.u1 + 1.5, p.label
        assert p.z0 >= -0.1 and p.z1 <= cap + 0.7, p.label


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


def test_a_panel_with_no_wall_behind_it_is_not_built():
    """Above a set-back storey there is nothing to fix a panel to. Building one
    anyway leaves it hanging a foot off the building in mid-air, which is
    exactly what a single front plane through the frontmost corner produced."""
    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    frame = facade.Frame(
        side="+y", face=40.0, u0=0.0, u1=30.0, z_ground=0.0, z_top=22.0,
        faces=[facade.Wallface(0.0, 10.0, 0.0, 22.0, 40.0)],  # wall on the left only
    )
    panels = [Panel(id="a", kind="band", u0=0, u1=30, z0=10, z1=11, depth_ft=0.4)]
    assert facade.build(scene, panels, frame) == 1, "one piece, over the wall only"
    mesh = next(m for m in scene.meshes if "band" in m.name)
    b = mesh.bounds()
    assert b[3] / 0.3048 <= 10.5, "nothing built out over the gap"


def test_a_panel_steps_with_the_wall_behind_it():
    """A band across a house whose upper storey comes forward has to step with
    it; on one plane it stands off the lower wall at one end."""
    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    frame = facade.Frame(
        side="+y", face=42.0, u0=0.0, u1=20.0, z_ground=0.0, z_top=22.0,
        faces=[facade.Wallface(0.0, 10.0, 0.0, 22.0, 40.0),
               facade.Wallface(10.0, 20.0, 0.0, 22.0, 42.0)],
    )
    panels = [Panel(id="a", kind="band", u0=0, u1=20, z0=10, z1=11, depth_ft=0.5)]
    assert facade.build(scene, panels, frame) == 2, "one piece per wall plane"


def test_a_panel_on_one_plane_is_one_box_however_many_walls_make_it():
    """The splitter cuts at the end of every wall on the front, so most cuts
    fall inside a single plane. Left unwelded they leave boxes meeting face to
    face — z-fighting in the viewer, and triangles for nothing."""
    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    frame = facade.Frame(
        side="+y", face=40.0, u0=0.0, u1=30.0, z_ground=0.0, z_top=20.0,
        faces=[facade.Wallface(0.0, 10.0, 0.0, 20.0, 40.0),
               facade.Wallface(10.0, 20.0, 0.0, 20.0, 40.0),
               facade.Wallface(20.0, 30.0, 0.0, 20.0, 40.0)],
    )
    panels = [Panel(id="a", kind="band", u0=0, u1=30, z0=9, z1=10, depth_ft=0.4)]
    assert facade.build(scene, panels, frame) == 1
