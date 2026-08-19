"""The fasād chat: an instruction in, a changed composition out.

The whole point of parsing against a closed vocabulary rather than asking a
model is that it can be tested exhaustively — so these go through the words a
person actually types, including the ones that must *not* be acted on.
"""

import pytest

from app import facadechat
from app.facadechat import interpret
from app.models import FacadeParams


def _say(text, params=None):
    return interpret(text, params or FacadeParams())


# --------------------------------------------------------------------------- #
# it does nothing rather than the wrong thing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "paint it purple",
    "make it nicer",
    "what do you think?",
    "add a swimming pool",
    "",
    "   ",
    "the quick brown fox",
])
def test_an_instruction_it_does_not_know_changes_nothing(text):
    """Quietly doing nothing and quietly doing the wrong thing look identical
    from outside, and one of them redesigns the elevation."""
    before = FacadeParams()
    reply = interpret(text, before)
    assert reply.understood is False
    assert reply.changes == []
    assert reply.params.model_dump() == before.model_dump()
    assert reply.message, "it has to say something"


def test_it_never_mutates_the_parameters_it_was_given():
    """The caller decides whether to keep the result. Editing in place would
    change a saved job even when the instruction was rejected."""
    before = FacadeParams()
    snapshot = before.model_dump()
    interpret("remove the canopy and make the screen much wider", before)
    assert before.model_dump() == snapshot


# --------------------------------------------------------------------------- #
# switches
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,flag", [
    ("remove the canopy", "canopy"),
    ("no canopy", "canopy"),
    ("get rid of the fins", "fins"),
    ("I don't want the balcony", "void"),
    ("drop the floor bands", "bands"),
    ("take off the box frames", "box_frames"),
    ("no clad bay", "mass"),
])
def test_it_turns_things_off(text, flag):
    reply = _say(text)
    assert reply.understood
    assert getattr(reply.params, flag) is False


@pytest.mark.parametrize("text,flag", [
    ("add a balcony", "void"),
    ("put the canopy back", "canopy"),
    ("give it a clad bay", "mass"),
    ("I want fins", "fins"),
])
def test_it_turns_things_on(text, flag):
    off = FacadeParams(void=False, canopy=False, mass=False, fins=False)
    reply = interpret(text, off)
    assert reply.understood
    assert getattr(reply.params, flag) is True


def test_asking_for_what_is_already_there_is_understood_but_changes_nothing():
    reply = _say("add a canopy")          # canopy is on by default
    assert reply.changes == []


# --------------------------------------------------------------------------- #
# sizes
# --------------------------------------------------------------------------- #
def test_wider_and_narrower_move_the_right_way():
    base = FacadeParams().screen_width_ft
    assert _say("make the screen wider").params.screen_width_ft > base
    assert _say("make the screen narrower").params.screen_width_ft < base


def test_how_much_wider_is_read_from_the_adverb():
    base = FacadeParams().screen_width_ft
    a = _say("the screen a bit wider").params.screen_width_ft
    b = _say("the screen wider").params.screen_width_ft
    c = _say("the screen much wider").params.screen_width_ft
    assert base < a < b < c


@pytest.mark.parametrize("text,expect", [
    ("make the screen 6 feet wide", 6.0),
    ("screen 6'", 6.0),
    ("screen width 4'-6\"", 4.5),
    ("clad bay 2 feet deep", 2.0),
    ("canopy 30 inches", 2.5),
    ("screen 2 m wide", 6.5617),
])
def test_a_stated_size_is_taken_literally(text, expect):
    reply = _say(text)
    assert reply.understood
    got = max(
        (c.after for c in reply.changes if isinstance(c.after, float)), default=None
    )
    assert got == pytest.approx(expect, abs=0.02)


def test_a_size_is_clamped_to_what_the_geometry_can_take():
    """A forty foot screen is not a screen. Clamping and saying so beats
    composing something absurd and leaving the user to wonder."""
    reply = _say("make the screen 40 feet wide")
    assert reply.understood
    assert reply.params.screen_width_ft == 20.0
    assert "as far as it goes" in reply.changes[0].said


def test_double_and_half_are_absolute_not_directional():
    base = FacadeParams().canopy_projection_ft
    assert _say("double the canopy").params.canopy_projection_ft == pytest.approx(
        min(base * 2, 8.0), abs=0.01)
    assert _say("half the canopy").params.canopy_projection_ft == pytest.approx(
        base * 0.5, abs=0.01)


# --------------------------------------------------------------------------- #
# the countable one
# --------------------------------------------------------------------------- #
def test_more_fins_means_more_of_them_not_a_wider_screen():
    """"More" of a row of things is a tighter pitch. Read as a size it would
    widen the screen, which is the opposite of what was asked."""
    base = FacadeParams()
    reply = _say("more fins")
    assert reply.params.fin_pitch_ft < base.fin_pitch_ft
    assert reply.params.screen_width_ft == base.screen_width_ft
    assert _say("fewer fins").params.fin_pitch_ft > base.fin_pitch_ft


# --------------------------------------------------------------------------- #
# whole-composition words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,arrangement", [
    ("keep it simple", "quiet"),
    ("make it quieter", "quiet"),
    ("something more restrained", "quiet"),
    ("make it symmetrical", "framed"),
    ("I want it framed", "framed"),
    ("layered please", "layered"),
])
def test_it_hears_an_arrangement(text, arrangement):
    assert _say(text).params.arrangement == arrangement


@pytest.mark.parametrize("text,side", [
    ("put the screen on the left", "left"),
    ("screen on the right", "right"),
    ("move the screen to the right hand side", "right"),
    ("screen wherever it fits", "auto"),
])
def test_it_moves_the_screen_from_side_to_side(text, side):
    assert _say(text).params.screen_side == side


def test_reset_goes_back_to_the_defaults():
    edited = FacadeParams(arrangement="quiet", canopy=False, screen_width_ft=12.0)
    reply = interpret("reset", edited)
    assert reply.understood
    assert reply.params.model_dump() == FacadeParams().model_dump()


def test_reset_keeps_the_facade_switched_off_if_it_was():
    reply = interpret("start again", FacadeParams(enabled=False))
    assert reply.params.enabled is False


# --------------------------------------------------------------------------- #
# more than one thing at a time
# --------------------------------------------------------------------------- #
def test_two_instructions_in_one_message_both_land():
    reply = _say("remove the canopy and make the screen much wider")
    assert reply.params.canopy is False
    assert reply.params.screen_width_ft > FacadeParams().screen_width_ft
    assert len(reply.changes) == 2


def test_the_element_carries_across_a_split():
    """"a bit wider and deeper" is one element with two instructions, not two
    instructions with no element."""
    reply = _say("make the clad bay bigger and deeper")
    assert reply.understood
    assert reply.params.mass_projection_ft > FacadeParams().mass_projection_ft


def test_the_message_names_every_change_it_made():
    reply = _say("no canopy, no bands, screen on the left")
    assert reply.params.canopy is False and reply.params.bands is False
    assert reply.params.screen_side == "left"
    assert len(reply.changes) == 3
    for c in reply.changes:
        assert c.said and c.field


# --------------------------------------------------------------------------- #
# the named extras
# --------------------------------------------------------------------------- #
def test_a_named_dimension_can_be_reached_directly():
    base = FacadeParams()
    assert _say("fin spacing 9 inches").params.fin_pitch_ft == pytest.approx(0.75)
    assert _say("canopy thickness 1'").params.canopy_thickness_ft == pytest.approx(1.0)
    wider = _say("wider fin spacing").params.fin_pitch_ft
    assert wider > base.fin_pitch_ft


# --------------------------------------------------------------------------- #
# words that must not fire
# --------------------------------------------------------------------------- #
def test_a_word_inside_another_word_does_not_fire():
    """"band" must not match "abandon", or a stray sentence rewrites the
    elevation."""
    assert facadechat._has("floor band", "band")
    assert not facadechat._has("abandon the plan", "band")
    assert not facadechat._has("brightness", "right")


def test_every_control_points_at_a_real_parameter():
    """A typo in the table is a control that silently never works."""
    fields = set(FacadeParams().model_dump())
    for control in facadechat.CONTROLS:
        if control.flag:
            assert control.flag in fields, control.name
        if control.size:
            assert control.size in fields, control.name
        if control.countable_pitch:
            assert control.countable_pitch in fields, control.name
    for phrase, (fieldname, _lo, _hi, _noun) in facadechat.EXTRAS.items():
        assert fieldname in fields, phrase


def test_every_arrangement_it_offers_is_one_the_composer_knows():
    from app.facade import ARRANGEMENTS

    for name, _words in facadechat.ARRANGEMENT_WORDS:
        assert name in ARRANGEMENTS


# --------------------------------------------------------------------------- #
# through the API, on the real building
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,check", [
    ("no canopy", lambda p: not any(x["kind"] == "canopy" for x in p)),
    ("remove the fins", lambda p: not any(x["kind"] == "fin" for x in p)),
    ("keep it simple", lambda p: not any(x["kind"] == "mass" for x in p)),
])
def test_an_instruction_reaches_the_drawing(monkeypatch, text, check):
    """The endpoint has to clear the stored panels, or the parameters change
    and the elevation does not — the edit looks ignored."""
    from app.facade import compose, front_frame
    from app.build3d import level_elevations
    from app.models import BuildParams, LevelParams, PlanExtract, ScaleInfo, Wall

    ex = PlanExtract(
        sheet_id="s", level=0, level_name="Ground floor",
        scale=ScaleInfo(px_per_ft=10.0, method="manual"),
        origin_px=(0.0, 0.0), bounds=(0.0, 0.0, 30.0, 24.0),
        walls=[Wall(id="f", axis="h", x0=0, y0=23.2, x1=30, y1=24.0,
                    exterior=True, outside="hi")],
    )
    params = BuildParams(levels=[LevelParams(level=0, name="Ground floor")])
    reply = interpret(text, params.facade)
    assert reply.understood
    params.facade = reply.params

    els = level_elevations([ex], params)
    frame = front_frame([ex], els, "+y", params.plinth_ft)
    panels = [p.model_dump() for p in compose([ex], els, frame, params)]
    assert panels, "something should still be composed"
    assert check(panels), f"{text!r} did not reach the panels"
