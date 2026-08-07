"""The stair — the thing the extractor was already measuring and throwing away.

Everything asserted here is checkable against the sheet: the flight width is
printed beside it as 3'6"WIDE, the going comes out a round ten inches, and the
well is where the drawing writes VOID.
"""

import pytest

from app import stairs as stairs_mod
from app.build3d import _slab_holes, build
from app.footprint import footprint_rects
from app.models import Flight
from app.pipeline import default_params
from conftest import needs_example

FT = 1 / 0.3048


# --------------------------------------------------------------------------- #
# reading it off the plan
# --------------------------------------------------------------------------- #
@needs_example
def test_the_stair_is_found_on_both_storeys(gf_extract, ff_extract):
    for ex in (gf_extract, ff_extract):
        assert len(ex.stairs) == 1, f"{ex.level_name} should have exactly one stair"
        assert len(ex.stairs[0].flights) == 3, "a dogleg: up, across, back"
        assert ex.stairs[0].treads == 16


@needs_example
def test_the_flight_width_matches_the_width_printed_beside_it(gf_extract):
    """The sheet writes 3'6"WIDE next to this stair. Nothing in the reader
    consults that text — it is measured off the tread lines — so agreement is
    an independent check on both the reader and the drawing scale."""
    for f in gf_extract.stairs[0].flights:
        assert f.width_ft == pytest.approx(3.5, abs=0.05)


@needs_example
def test_the_going_is_a_round_ten_inches(gf_extract):
    """A wrong scale does not produce round inches — the same argument the
    README already makes for the 5" and 10" walls."""
    for f in gf_extract.stairs[0].flights:
        inches = f.going_ft * 12
        assert abs(inches - round(inches)) < 0.15
        assert round(inches) == 10


@needs_example
def test_the_same_stair_is_read_at_the_same_place_on_both_storeys(gf_extract, ff_extract):
    """It is one stair through the building, so the two reads must agree.

    These fixtures extract each sheet on its own, *without* the pooled scale
    the pipeline normally applies, so the two land about 0.2% apart — the same
    spread the per-sheet calibrations show. Agreement to an inch or two across
    two independent reads is the point; exactness would mean they were not
    independent.
    """
    a = sorted(gf_extract.stairs[0].flights, key=lambda f: (f.y0, f.x0))
    b = sorted(ff_extract.stairs[0].flights, key=lambda f: (f.y0, f.x0))
    for fa, fb in zip(a, b):
        assert fa.x0 == pytest.approx(fb.x0, abs=0.25)
        assert fa.y0 == pytest.approx(fb.y0, abs=0.25)


@needs_example
def test_the_well_is_where_the_drawing_writes_void(gf_extract, gf_sheet):
    well = gf_extract.stairs[0].well
    assert well is not None
    px = gf_extract.scale.px_per_ft
    ox, oy = gf_extract.origin_px
    void = next(w for w in gf_sheet.words if w.text.strip().upper() == "VOID")
    vx, vy = (void.cx - ox) / px, (void.cy - oy) / px
    assert well[0] <= vx <= well[2] and well[1] <= vy <= well[3]


@needs_example
def test_the_ramp_is_not_mistaken_for_a_stair(gf_extract, gf_sheet):
    """The ground floor also draws a ramp, with its own UP token 23 ft away.
    Hatched like treads, and it must not become a flight."""
    px = gf_extract.scale.px_per_ft
    ox, oy = gf_extract.origin_px
    ramp = next(w for w in gf_sheet.words if w.text.strip().upper() == "RAMP")
    ry = (ramp.cy - oy) / px
    for f in gf_extract.stairs[0].flights:
        assert not (f.y0 - 3 <= ry <= f.y1 + 3), "a flight was placed on the ramp"


# --------------------------------------------------------------------------- #
# the hole it leaves in the floor
# --------------------------------------------------------------------------- #
@needs_example
def test_the_stairwell_is_cut_out_of_the_slab(gf_extract):
    holes = _slab_holes(gf_extract)
    well = gf_extract.stairs[0].well
    assert well in holes

    area = lambda rs: sum((r[2] - r[0]) * (r[3] - r[1]) for r in rs)
    solid = area(footprint_rects(gf_extract.walls, columns=gf_extract.columns))
    cut = area(footprint_rects(gf_extract.walls, columns=gf_extract.columns, holes=holes))
    assert cut < solid
    # what was removed is what was asked for, give or take overlap with walls
    assert solid - cut == pytest.approx(area(holes), rel=0.15)


@needs_example
def test_a_named_shaft_also_loses_its_floor(gf_extract):
    """A DUCT is inside the building but has no slab over it."""
    holes = _slab_holes(gf_extract)
    duct = next(r for r in gf_extract.rooms if "DUCT" in r.name.upper())
    assert (duct.x0, duct.y0, duct.x1, duct.y1) in holes


# --------------------------------------------------------------------------- #
# building it
# --------------------------------------------------------------------------- #
@needs_example
def test_the_stair_is_built_and_stops_a_riser_below_the_floor_above(
    gf_extract, ff_extract
):
    """The riser cannot be measured from a plan — it is derived by dividing the
    storey height by one *more* than the number of treads, because the last
    riser lands you on the floor above. So the top tread finishes just under
    the slab, which is where a real one stops."""
    extracts = [gf_extract, ff_extract]
    result = build(extracts, default_params({e.sheet_id: e for e in extracts}))
    meshes = {m.name: m for m in result.scene.meshes}

    stair = meshes["Ground floor stairs"]
    slab_above = meshes["First floor slab"]
    assert stair.material == "stair"
    top_of_stair = stair.bounds()[4]
    underside = slab_above.bounds()[1]
    assert top_of_stair < underside, "the stair must not push through the slab"
    # and it gets close — within one riser of it
    riser = (10.0 / 17) * 0.3048
    assert underside - top_of_stair < riser * 1.2


@needs_example
def test_the_derived_riser_is_a_plausible_one(gf_extract):
    """Not an assertion about the drawing — a sanity check that dividing a
    normal storey height by these treads lands where stairs really are built."""
    riser_ft = 10.0 / (gf_extract.stairs[0].treads + 1)
    assert 6.0 <= riser_ft * 12 <= 8.0


# --------------------------------------------------------------------------- #
# the grouping rules, on shapes we control
# --------------------------------------------------------------------------- #
def test_a_straight_flight_has_no_well():
    one = Flight(axis="v", x0=0, y0=0, x1=10, y1=3.5, treads=12,
                 going_ft=10 / 12, width_ft=3.5, up="+x")
    assert stairs_mod._well([one]) is None


def _dogleg() -> list[Flight]:
    """Two flights and a landing wrapping a shaft — open on one side, as a
    real dogleg is."""
    return [
        Flight(axis="v", x0=0, y0=0, x1=3.5, y1=10, treads=12,
               going_ft=10 / 12, width_ft=3.5, up="+x"),
        Flight(axis="v", x0=6, y0=0, x1=9.5, y1=10, treads=12,
               going_ft=10 / 12, width_ft=3.5, up="-x"),
        Flight(axis="h", x0=0, y0=10, x1=9.5, y1=13, treads=4,
               going_ft=10 / 12, width_ft=3.5, up="+y"),
    ]


def test_a_labelled_void_locates_the_well_of_an_open_sided_dogleg():
    """A dogleg's well is open on the side with no flight, so shape alone
    cannot tell it from the room next door. The drawing settles it by writing
    VOID inside, which is better evidence than topology."""
    well = stairs_mod._well(_dogleg(), [(4.7, 5.0, "VOID")])
    assert well is not None
    assert well[0] == pytest.approx(3.5, abs=0.15)
    assert well[2] == pytest.approx(6.0, abs=0.15)


def test_without_a_label_an_open_sided_well_is_refused_rather_than_guessed():
    """The consequence of the rule above, stated as a test: no label and no
    enclosure means no hole is cut. Leaving the slab solid is the conservative
    error — inventing an opening in a floor is not."""
    assert stairs_mod._well(_dogleg(), []) is None


def test_reject_stacks_hands_back_what_it_drops():
    """The rungs used to be discarded inside the function. Nothing else could
    ever have found the stair, because by then it no longer existed."""
    from app import extract as E
    from app.geom import Band, Run

    rungs = [Band("h", 10 + i, 11 + i, 0, 20, pen=0.7) for i in range(5)]
    runs = [Run("h", float(10 + i), 0, 20, pen=0.7) for i in range(7)]
    kept, dropped = E.reject_stacks(rungs, runs)
    assert dropped, "an evenly spaced ladder must be handed back, not lost"
    assert len(kept) + len(dropped) == len(rungs)
