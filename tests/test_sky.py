"""The light the model is shown in.

One preset feeds two renderers that share no parameters, so what these check is
mostly that the one decision reaches both of them and that no preset is missing
a number a renderer will reach for.
"""

import pytest

from app import sky
from app.blender import blender_script
from app.build3d import build
from app.models import BuildParams, LevelParams
from conftest import needs_example


def test_the_day_runs_in_order():
    assert list(sky.SKIES) == ["dawn", "morning", "noon"]
    assert [c["label"] for c in sky.choices()] == ["Dawn", "Morning", "Noon"]


def test_the_sun_climbs_through_the_day():
    e = [sky.SKIES[k].elevation_deg for k in ("dawn", "morning", "noon")]
    assert e == sorted(e), "the sun does not go down before noon"
    b = [sky.SKIES[k].bearing_deg for k in ("dawn", "morning", "noon")]
    assert b == sorted(b), "and it moves east to south"


def test_an_unknown_sky_is_the_default_not_a_crash():
    """A saved job from a later version, or a typo in the URL, must not take
    the render down — it gets the everyday light."""
    assert sky.get("teatime").name == sky.DEFAULT
    assert sky.get(None).name == sky.DEFAULT
    assert sky.get("").name == sky.DEFAULT
    assert sky.get("NOON").name == "noon"


def test_every_preset_carries_every_number_both_renderers_reach_for():
    """A missing key is a KeyError inside the generated Blender script, which
    surfaces as a stack trace in someone else's Blender."""
    wanted = set(sky.SKIES["morning"].as_dict())
    for s in sky.SKIES.values():
        assert set(s.as_dict()) == wanted, s.name
        for k, v in s.as_dict().items():
            assert v is not None, f"{s.name}.{k}"
            if k.endswith("_hex"):
                assert v.startswith("#") and len(v) == 7, f"{s.name}.{k}"


def test_dawn_is_warmer_and_dimmer_than_noon():
    dawn, noon = sky.SKIES["dawn"], sky.SKIES["noon"]

    def warmth(hex_colour):
        r = int(hex_colour[1:3], 16)
        b = int(hex_colour[5:7], 16)
        return r - b

    assert warmth(dawn.sun_hex) > warmth(noon.sun_hex), "a low sun is orange"
    assert dawn.sun_energy < noon.sun_energy
    assert dawn.sun_angle_deg > noon.sun_angle_deg, "and its shadows are softer"


def test_exposure_is_carried_separately_for_each_renderer():
    """Blender's view transform takes stops with 0 neutral; three.js takes a
    multiplier with 1.0 neutral. One number for both under- or over-exposes
    one of them, silently."""
    for s in sky.SKIES.values():
        assert -3.0 < s.exposure_ev < 3.0, f"{s.name} stops out of range"
        assert 0.4 < s.exposure < 2.0, f"{s.name} multiplier out of range"


def test_the_backdrop_is_brighter_than_the_light():
    """The sky has two jobs. Dim enough to keep the cast shadows, it is a
    washed-out grey to look at; bright enough to look like sky, it drowns the
    sun. The Blender scene splits them with a Light Path node."""
    for s in sky.SKIES.values():
        assert s.backdrop_strength > s.sky_strength, s.name
        assert s.backdrop_saturation > 1.0, f"{s.name}: AgX will grey it out"


# --------------------------------------------------------------------------- #
# it reaches the renderers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["dawn", "morning", "noon"])
def test_the_blender_script_is_built_around_the_chosen_sky(name):
    s = sky.SKIES[name]
    src = blender_script("model.glb", sky=name)
    assert f"SUN_ELEVATION_DEG = {s.elevation_deg}" in src
    assert f"SUN_BEARING_DEG = {s.bearing_deg}" in src
    assert repr(s.sun_hex) in src
    # and it is valid Python, which a f-string template is not guaranteed to be
    compile(src, "blender_import.py", "exec")


def test_choosing_a_different_sky_changes_the_scene():
    a = blender_script("model.glb", sky="dawn")
    b = blender_script("model.glb", sky="noon")
    assert a != b


def test_an_explicit_sun_still_overrides_the_preset():
    """The bearing read off the drawing's compass has to win: the sky preset is
    a look, the compass is a fact about the site."""
    src = blender_script("model.glb", sky="noon", sun_bearing=41.0)
    assert "SUN_BEARING_DEG = 41.0" in src


@needs_example
def test_the_build_summary_tells_the_viewer_which_light_to_use(gf_extract):
    params = BuildParams(
        levels=[LevelParams(level=0, name="Ground floor")],
        align_north=False,
        sky="dawn",
    )
    got = build([gf_extract], params).summary["sky"]
    assert got["name"] == "dawn"
    # the viewer reads these straight off it
    for key in ("elevation_deg", "rayleigh", "sun_hex", "exposure", "ground_hex"):
        assert key in got
