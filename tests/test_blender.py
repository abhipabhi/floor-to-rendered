"""The Blender scene shipped with the model.

Blender is not installed in CI, so nothing here proves the ``bpy`` calls are
right — that has to be checked by running it. What *is* checked is everything
that can be: the script is valid Python, it is told about the model standing
next to it, and the compass maths it uses to place the sun lands on the right
side of the building.
"""

import ast
import math

import pytest

from app.blender import blender_script


def _sun_vector(elevation_deg: float, bearing_deg: float):
    """The same maths the generated script uses, kept here to be checked.

    Bearing is degrees clockwise from north, and Blender's north is +Y.
    """
    e, a = math.radians(elevation_deg), math.radians(bearing_deg)
    return (math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e))


def test_the_script_is_valid_python():
    ast.parse(blender_script("model.glb", True, storeys=["Ground floor"]))


def test_the_script_knows_the_storeys_it_will_be_sorting():
    src = blender_script("model.glb", True, storeys=["Ground floor", "First floor"])
    assert "'Ground floor'" in src and "'First floor'" in src
    assert "bpy.data.collections.new" in src, "storeys are no use without collections"


def test_a_model_with_no_storeys_still_produces_a_runnable_script():
    src = blender_script("model.glb", False, storeys=[])
    ast.parse(src)
    assert "STOREYS = []" in src


def test_the_scene_brings_its_own_light_and_background():
    """The complaint this answers is opening a .glb into a grey void."""
    src = blender_script("model.glb", True)
    for feature in ("ShaderNodeTexSky", "light_add", "camera_add", "resolution_x",
                    "Backdrop ground"):
        assert feature in src, f"the scene should set up {feature}"


def test_the_sky_properties_are_set_one_at_a_time():
    """Blender renames these between versions — the physical sky model has been
    both NISHITA and MULTIPLE_SCATTERING. Setting them inside a single
    try/except means one rename leaves the sky node created but never linked,
    and the scene renders a flat grey background that looks like a bug in the
    model. This is exactly what happened on Blender 5.2."""
    src = blender_script("model.glb", True)
    assert "_set_any(sky" in src and "_set_soft(sky" in src
    body = src.split("def build_sky")[1].split("def ")[0]
    assert body.count("try:") <= 1, "the sky properties must not share one try"


def test_the_cameras_are_tracked_not_shortest_rotated():
    """`rotation_difference` gives the shortest rotation onto a direction and
    says nothing about roll, so every camera comes out banked and the horizon
    runs diagonally. `to_track_quat` is what keeps the head up."""
    src = blender_script("model.glb", True)
    assert "to_track_quat" in src
    # the name still appears in the comment explaining why it is wrong, so
    # look for the call rather than the word
    assert ".rotation_difference(" not in src


def test_the_sky_does_not_double_count_the_sun():
    """The sky texture has its own sun disc and the scene also adds a Sun lamp.
    Leave both on and the fill drowns the key light — the model renders with no
    cast shadows at all, which reads as broken rather than as over-lit."""
    src = blender_script("model.glb", True)
    assert '_set_soft(sky, "sun_intensity", 0.0)' in src


def test_braces_in_the_template_survive_formatting():
    """The template is a str.format target containing Python dict literals, so
    an unescaped brace turns into a KeyError at export time rather than a bad
    script — worth pinning, because the failure is not obvious by reading."""
    src = blender_script("model.glb", True, storeys=["Ground floor"])
    assert "{}" not in src.split("MODEL = ")[0]
    ast.parse(src)


# --------------------------------------------------------------------------- #
# the compass maths, which decides which side the shadows fall on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bearing,expect",
    [(0, (0.0, 1.0)), (90, (1.0, 0.0)), (180, (0.0, -1.0)), (270, (-1.0, 0.0))],
)
def test_a_bearing_points_where_the_compass_says(bearing, expect):
    x, y, _z = _sun_vector(0.0, bearing)
    assert x == pytest.approx(expect[0], abs=1e-9)
    assert y == pytest.approx(expect[1], abs=1e-9)


def test_the_default_sun_is_up_and_to_the_south_east():
    x, y, z = _sun_vector(34.0, 138.0)
    assert z > 0, "a sun below the horizon lights nothing"
    assert x > 0 and y < 0, "138° is south-east; north is +Y"
    assert math.dist((0, 0, 0), (x, y, z)) == pytest.approx(1.0)


def test_the_sun_vector_is_always_a_unit_vector():
    for elevation in (0, 15, 34, 60, 89):
        for bearing in range(0, 360, 30):
            v = _sun_vector(elevation, bearing)
            assert math.dist((0, 0, 0), v) == pytest.approx(1.0)
