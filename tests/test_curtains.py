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
# the shape: a pair, tied back
# --------------------------------------------------------------------------- #
def test_the_curtain_is_pinched_at_the_tieback():
    """This is the whole shape. Without the pinch it is a blind pulled down the
    side of the window, which is not what anyone means by curtains."""
    head = build3d._curtain_width(1.0)
    tie = build3d._curtain_width(0.45)
    foot = build3d._curtain_width(0.0)
    assert tie < head and tie < foot, "narrowest where it is tied"
    assert tie < head * 0.6, "and narrow enough to read as a tie, not a taper"
    assert 0.0 < head < 0.5, "a curtain, not half the window"


def test_the_profile_is_smooth_between_its_points():
    """Sampled coarsely it is a staircase; the smoothstep is what makes the
    fold a curve. No sample may jump more than a little from the last."""
    ws = [build3d._curtain_width(i / 60) for i in range(61)]
    assert max(abs(b - a) for a, b in zip(ws, ws[1:])) < 0.02


def test_the_profile_holds_outside_its_range():
    assert build3d._curtain_width(-0.2) == pytest.approx(
        build3d._curtain_width(0.0))
    assert build3d._curtain_width(1.4) == pytest.approx(
        build3d._curtain_width(1.0))


@pytest.mark.parametrize("tie", [0.2, 0.45, 0.7])
def test_the_tieback_can_be_moved_and_the_pinch_moves_with_it(tie):
    xs = [i / 100 for i in range(101)]
    ws = [build3d._curtain_width(build3d._shift(x, tie)) for x in xs]
    lowest = xs[ws.index(min(ws))]
    assert abs(lowest - tie) < 0.06, f"pinch at {lowest:.2f}, tieback at {tie}"


def test_the_pair_leaves_the_middle_of_the_window_clear():
    """They are tied back at the sides. Anything across the centre is a blind,
    and the drawn-across version that put fabric there was not what was wanted."""
    from app.finish import materials_for, preset_slots
    from app.mesh import Scene
    from app.models import Wall

    scene = Scene(materials=materials_for(preset_slots("elevation_spec")))
    wall = Wall(id="w", x0=0, y0=0, x1=20, y1=0.83, axis="h",
                exterior=True, outside="hi")
    build3d._curtains(scene, wall, 2.0, 12.0, 3.0, 9.0, 0.83, 1.0,
                      DetailParams(), "Test")
    m = scene.meshes[0]
    xs = [m.positions[i] / FT_TO_M for i in range(0, len(m.positions), 3)]
    assert min(xs) <= 2.05 and max(xs) >= 11.95, "it reaches both jambs"
    mid = [x for x in xs if 6.4 < x < 7.6]
    assert not mid, "fabric across the centre of the window"


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
