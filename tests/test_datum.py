"""Provenance: which number wins, and what happens to the ones that lose.

The rule this file exists to defend is that a conflict between two sheets is
reported rather than averaged, and that a number you typed is never silently
replaced by one a reader found later.
"""

import pytest

from app import datum
from app.datum import Quantity, Reading, building_key, level_key
from app.models import BuildParams, LevelParams


def _params(**kw) -> BuildParams:
    p = BuildParams(
        levels=[
            LevelParams(level=0, name="Ground floor"),
            LevelParams(level=1, name="First floor"),
        ],
        **kw,
    )
    datum.seed_defaults(p)
    return p


def _q(ft: float, source: str = "measured", **kw) -> Quantity:
    return Quantity(ft=ft, source=source, **kw)


# --------------------------------------------------------------------------- #
# seeding
# --------------------------------------------------------------------------- #
def test_every_default_height_says_it_is_assumed():
    p = _params()
    assert p.provenance["plinth_ft"].source == "default"
    assert p.level(0).provenance["floor_to_floor_ft"].source == "default"
    # a None field means "inherit", so there is no number to attribute
    assert "wall_height_ft" not in p.level(0).provenance
    assert "roof_slab_thickness_ft" not in p.provenance


def test_seeding_twice_does_not_overwrite_a_story_already_told():
    p = _params()
    p.provenance["plinth_ft"] = _q(2.5, "measured", method="elevation_ffl")
    p.plinth_ft = 2.5
    datum.seed_defaults(p)
    assert p.provenance["plinth_ft"].source == "measured"


# --------------------------------------------------------------------------- #
# precedence
# --------------------------------------------------------------------------- #
def test_a_measured_reading_beats_the_default():
    p = _params()
    datum.resolve(p, [Reading(key=building_key("plinth_ft"), q=_q(2.75))])
    assert p.plinth_ft == pytest.approx(2.75)
    assert p.provenance["plinth_ft"].source == "measured"


def test_measured_beats_derived():
    p = _params()
    datum.resolve(p, [
        Reading(key=building_key("parapet_ft"), q=_q(3.5, "derived")),
        Reading(key=building_key("parapet_ft"), q=_q(4.0, "measured")),
    ])
    assert p.parapet_ft == pytest.approx(4.0)


def test_a_level_reading_lands_on_the_right_storey():
    p = _params()
    datum.resolve(p, [Reading(key=level_key(1, "floor_to_floor_ft"), q=_q(11.5))])
    assert p.level(1).floor_to_floor_ft == pytest.approx(11.5)
    assert p.level(0).floor_to_floor_ft == pytest.approx(10.0)


def test_a_reading_for_a_field_that_does_not_exist_is_reported_not_crashed():
    p = _params()
    notes = datum.resolve(p, [
        Reading(key="building.nonsense_ft", q=_q(1.0)),
        Reading(key=level_key(9, "floor_to_floor_ft"), q=_q(1.0)),
    ])
    assert len(notes) == 2
    assert all("unknown field" in n for n in notes)


# --------------------------------------------------------------------------- #
# conflicts are surfaced, never averaged
# --------------------------------------------------------------------------- #
def test_two_measurements_that_disagree_are_both_kept():
    p = _params()
    notes = datum.resolve(p, [
        Reading(key=building_key("parapet_ft"),
                q=_q(3.0, method="section", sheet_id="s1", confidence="high")),
        Reading(key=building_key("parapet_ft"),
                q=_q(4.0, method="elevation", sheet_id="s2", confidence="low")),
    ])
    # the better-evidenced reading wins outright — nothing is split
    assert p.parapet_ft == pytest.approx(3.0)
    assert p.parapet_ft != pytest.approx(3.5), "a conflict must never be averaged"
    alts = p.provenance["parapet_ft"].alternatives
    assert len(alts) == 1 and "elevation" in alts[0]
    assert any("Nothing was averaged" in n for n in notes)


def test_two_readings_that_agree_are_not_reported_as_a_conflict():
    p = _params()
    datum.resolve(p, [
        Reading(key=building_key("parapet_ft"), q=_q(3.0, confidence="high")),
        Reading(key=building_key("parapet_ft"), q=_q(3.0, confidence="low")),
    ])
    assert p.provenance["parapet_ft"].alternatives == []


# --------------------------------------------------------------------------- #
# a number you typed is yours
# --------------------------------------------------------------------------- #
def test_a_user_value_survives_a_later_measured_reading():
    old = _params()
    new = old.model_copy(deep=True)
    new.plinth_ft = 2.5
    datum.pin_user_edits(old, new)
    assert "plinth_ft" in new.user_set
    assert new.provenance["plinth_ft"].source == "user"

    notes = datum.resolve(new, [Reading(key=building_key("plinth_ft"), q=_q(3.0))])
    assert new.plinth_ft == pytest.approx(2.5), "the drawing does not overrule you"
    assert any("kept your" in n for n in notes)
    # but the disagreement is visible rather than thrown away
    assert new.provenance["plinth_ft"].alternatives


def test_an_untouched_save_keeps_the_story_the_browser_never_sent():
    """The UI PUTs back plain numbers. Without carrying provenance across a
    save, every trip through the form would forget where a number came from."""
    old = _params()
    datum.resolve(old, [Reading(key=building_key("plinth_ft"),
                                q=_q(2.75, method="tie_beam_schedule"))])
    # what the browser sends back: same values, no provenance at all
    naked = BuildParams(**{**old.model_dump(), "provenance": {}, "user_set": []})
    for lp in naked.levels:
        lp.provenance, lp.user_set = {}, []

    datum.pin_user_edits(old, naked)
    assert naked.provenance["plinth_ft"].source == "measured"
    assert naked.provenance["plinth_ft"].method == "tie_beam_schedule"


def test_editing_one_storey_does_not_pin_another():
    old = _params()
    new = old.model_copy(deep=True)
    new.level(1).floor_to_floor_ft = 9.0
    datum.pin_user_edits(old, new)
    assert new.level(1).user_set == ["floor_to_floor_ft"]
    assert new.level(0).user_set == []


def test_clearing_a_field_releases_the_pin():
    old = _params()
    new = old.model_copy(deep=True)
    new.roof_slab_thickness_ft = 0.75
    datum.pin_user_edits(old, new)
    assert "roof_slab_thickness_ft" in new.user_set

    cleared = new.model_copy(deep=True)
    cleared.roof_slab_thickness_ft = None
    datum.pin_user_edits(new, cleared)
    assert "roof_slab_thickness_ft" not in cleared.user_set
    assert "roof_slab_thickness_ft" not in cleared.provenance


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def test_every_height_gets_a_row_naming_its_source():
    p = _params()
    datum.resolve(p, [Reading(key=building_key("plinth_ft"),
                              q=_q(2.75, method="tie_beam_schedule", sheet_id="s3"))])
    rows = datum.rows(p)
    by_what = {what: (value, story) for what, value, story in rows}
    assert by_what["plinth"][1].startswith("measured")
    assert "s3" in by_what["plinth"][1]
    assert by_what["parapet"][1] == "assumed"
    # and each storey's numbers are listed under that storey's name
    assert any(w.startswith("Ground floor —") for w in by_what)
    assert all(story for _v, story in by_what.values())
