"""Elevations and sections: the sheet that finally states the heights.

The published set has no elevation of the building, so most of this works
against :mod:`elevation_fixture`, which draws one whose levels we chose. The
real sheets appear here too, in the negative: a plan's setting-out chainages
look exactly like level tags, and must not be read as any.
"""

import pytest

import elevation_fixture as ef
from app import elevation as E
from app.classify import classify
from app.elevation import Datum, LevelTag, fit_datum
from app.pdfvec import load_sheet
from app.units import M_TO_FT
from conftest import LAYOUT, needs_example

M_PER_FT = 0.3048


@pytest.fixture(scope="module")
def elev_sheet(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("elev") / "front elevation.pdf")
    ef.write(path)
    return load_sheet(path, "front elevation.pdf")


@pytest.fixture(scope="module")
def elev_datum(elev_sheet):
    return E.read_datum(elev_sheet)


def _tags(pairs):
    return [LevelTag(value_ft=v / M_PER_FT, y_px=y, raw=f"+{v:.2f}") for v, y in pairs]


# --------------------------------------------------------------------------- #
# recognising the sheet
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "title,kind",
    [
        ("FRONT ELEVATION", "elevation"),
        ("SIDE ELEVATION SCALE 1:100", "elevation"),
        ("SECTION A-A", "section"),
        ("SECTION ON X-X", "section"),
        ("SECTIONAL ELEVATION", "section"),
        ("GROUND FLOOR PLAN", "floor_plan"),
    ],
)
def test_elevations_and_sections_are_told_apart(title, kind):
    assert classify(title, "", True).kind == kind


def test_a_sectional_elevation_is_read_as_a_section():
    """It is both; a section is the one that states levels through the
    building rather than on a face, so that reading wins."""
    assert classify("SECTIONAL ELEVATION", "", True).kind == "section"


def test_the_septic_sheet_stays_a_services_sheet(elev_sheet):
    """It contains SECTION ON X-X but is not a section of the building."""
    assert classify("SEPTIC TANK PLAN SECTION ON X-X SOAK PIT", "", True).kind == "services"


# --------------------------------------------------------------------------- #
# the datum
# --------------------------------------------------------------------------- #
def test_every_stated_level_is_read(elev_datum):
    got = sorted(round(t.value_ft * M_PER_FT, 3) for t in elev_datum.tags)
    assert got == pytest.approx(ef.LEVELS_M, abs=0.002)


def test_the_datum_tag_itself_is_read(elev_datum):
    """±0.00 is the tag every other level is measured from, and a plausibility
    window on bare numbers throws it away for being too small. It was doing
    exactly that until this was fixed."""
    assert any(t.is_datum for t in elev_datum.tags), "the ±0.00 datum was dropped"


def test_the_vertical_scale_is_measured_not_inherited(elev_datum):
    """The fixture is deliberately drawn at 26 pt/m where the plan fixture is
    at 20. A section and a plan share a sheet at different scales in the real
    set, so a reader that borrows the plan's scale is silently wrong."""
    assert elev_datum.px_per_ft == pytest.approx(ef.EXPECTED_PT_PER_FT, rel=1e-4)


def test_three_agreeing_tags_earn_high_confidence(elev_datum):
    assert elev_datum.confidence == "high"
    assert elev_datum.rms_ft < 0.01


def test_storey_heights_are_the_differences_between_stated_levels(elev_datum):
    got = [round(h * M_PER_FT, 3) for h in elev_datum.storey_heights()]
    assert got == pytest.approx(ef.EXPECTED_STOREY_M, abs=0.002)


def test_a_point_on_the_sheet_can_be_turned_into_a_level(elev_datum):
    """The datum is what lets a sill or a parapet drawn on the sheet be
    measured, not just the tags themselves."""
    tag = elev_datum.tags[-1]
    assert elev_datum.level_at(tag.y_px) == pytest.approx(tag.value_ft, abs=0.02)


# --------------------------------------------------------------------------- #
# what it refuses
# --------------------------------------------------------------------------- #
def test_one_tag_is_a_datum_and_nothing_more():
    assert fit_datum(_tags([(0.0, 300.0)])) is None


def test_tags_that_do_not_lie_on_a_line_are_refused():
    """Two views on one sheet, or a stray number in a title block. A scale
    fitted through those is wrong everywhere rather than obviously wrong
    somewhere, so nothing is returned."""
    assert fit_datum(_tags([(0.0, 300.0), (3.6, 220.0), (6.8, 30.0)])) is None


def test_levels_that_rise_going_down_the_sheet_are_not_levels():
    """A level increases as you go *up* a sheet. Anything increasing downward
    is a horizontal distance that happens to be written with a + sign."""
    assert fit_datum(_tags([(0.0, 100.0), (3.6, 200.0), (6.8, 300.0)])) is None


@needs_example
def test_a_plans_setting_out_chainages_are_not_read_as_levels():
    """The real trap. The layout sheet writes +0'-0", +9'-5", +14'-1" down its
    margin — the same shape as a level tag, and they even vary with y. They are
    distances along the grid, and reading them as levels would put the storeys
    at the plan's dimensions."""
    sheet = load_sheet(LAYOUT, "layout.pdf")
    tags = E.find_level_tags(sheet)
    assert len(tags) > 5, "the chainages should be found as candidates"
    assert E.read_datum(sheet) is None, "and then refused"


@needs_example
def test_no_floor_plan_yields_a_datum(gf_sheet, ff_sheet):
    for sheet in (gf_sheet, ff_sheet):
        assert E.read_datum(sheet) is None


# --------------------------------------------------------------------------- #
# what reaches the model
# --------------------------------------------------------------------------- #
def test_readings_are_measured_and_show_their_arithmetic(elev_datum):
    by_key = {r.key: r.q for r in E.readings(elev_datum, "elev", [0, 1])}
    assert by_key["level.0.floor_to_floor_ft"].ft == pytest.approx(3.60 / M_PER_FT, rel=1e-3)
    assert by_key["level.1.floor_to_floor_ft"].ft == pytest.approx(3.20 / M_PER_FT, rel=1e-3)
    for q in by_key.values():
        assert q.source == "measured"
        assert q.method == "elevation_ffl"
    assert "−" in by_key["level.0.floor_to_floor_ft"].evidence, "show the subtraction"


def test_a_single_level_produces_no_storey_height():
    datum = Datum(px_per_ft=8.0, y_at_zero=300.0, tags=_tags([(0.0, 300.0)]))
    assert E.readings(datum, "elev") == []


def test_a_raised_ground_floor_is_read_as_the_plinth():
    datum = fit_datum(_tags([(0.6, 300.0), (3.6, 190.0), (6.6, 80.0)]))
    assert datum is not None
    keys = {r.key: r.q for r in E.readings(datum, "elev", [0, 1])}
    assert keys["building.plinth_ft"].ft == pytest.approx(0.6 / M_PER_FT, rel=1e-3)


# --------------------------------------------------------------------------- #
# the whole set
# --------------------------------------------------------------------------- #
@needs_example
def test_dropping_an_elevation_in_makes_the_storey_heights_measured(
    example_pdfs, tmp_path
):
    """The point of the whole module: with an elevation in the set, the number
    the model is built from stops being the one typed on the heights page."""
    import shutil

    from app import datum as datum_mod
    from app.pipeline import run

    folder = tmp_path / "set"
    folder.mkdir()
    for pdf in example_pdfs:
        shutil.copy(pdf, folder)
    ef.write(str(folder / "front elevation.pdf"))

    _ing, _ex, params, _result, _notes = run([str(p) for p in folder.glob("*.pdf")])
    ground = params.level(0)
    assert ground.provenance["floor_to_floor_ft"].source == "measured"
    assert ground.floor_to_floor_ft == pytest.approx(3.60 / M_PER_FT, rel=1e-3)
    assert ground.floor_to_floor_ft != pytest.approx(10.0), "10 ft was the assumption"


@needs_example
def test_without_an_elevation_the_heights_stay_honest_assumptions(example_pdfs):
    from app.pipeline import run

    _ing, _ex, params, _result, _notes = run(example_pdfs)
    assert params.level(0).provenance["floor_to_floor_ft"].source == "default"


# --------------------------------------------------------------------------- #
# openings, measured against the datum
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def elev_openings(elev_sheet, elev_datum):
    return E.find_openings(elev_sheet, elev_datum)


def test_every_opening_on_the_face_is_found(elev_openings):
    """Six windows and a door. They are found from raw segments, not from
    merged runs: three window heads at one height merge into a single
    twenty-foot line, and every one of them is then too wide to be an opening."""
    assert len(elev_openings) == 7


def test_a_frame_and_its_glass_count_as_one_opening(elev_sheet, elev_datum):
    """Each window is drawn as a frame with the glass inside it, so it
    registers twice a couple of points apart. Left in, the pair reads as two
    different sill heights on the same storey."""
    raw = E._drop_nested([], inset=4.0)
    assert raw == []
    nested = E.find_openings(elev_sheet, elev_datum)
    sills = {round(o.y_bottom, 1) for o in nested}
    assert len(sills) <= 3, "one sill line per storey, plus the door"


def test_the_sill_is_measured_from_its_own_storeys_floor(elev_datum, elev_openings):
    levels = E.opening_levels(elev_datum, elev_openings, [0, 1])
    for level in (0, 1):
        assert levels.sill_ft[level] * M_PER_FT == pytest.approx(1.10, abs=0.025)
        assert levels.head_ft[level] * M_PER_FT == pytest.approx(2.45, abs=0.025)
        assert levels.samples[level] == 3


def test_two_sill_heights_on_one_storey_are_both_reported():
    """1100 in the bedrooms and 650 in the living room is a real convention.
    Averaging them gives a sill height that appears nowhere on the drawing."""
    value, n, rivals = E._dominant([3.6, 3.6, 3.6, 2.1, 2.1])
    assert value == pytest.approx(3.6)
    assert n == 3
    assert rivals and rivals[0] == pytest.approx(2.1)


def test_the_reported_value_is_a_real_reading_not_a_bin_centre():
    """The bin decides which readings belong together; the answer is their
    median, so it is a number something on the drawing actually sits at."""
    value, _n, _r = E._dominant([3.61, 3.62, 3.63])
    assert value == pytest.approx(3.62)


# --------------------------------------------------------------------------- #
# the datum origin — what makes an absolute level trustworthy
# --------------------------------------------------------------------------- #
def test_tags_are_snapped_onto_the_lines_they_label(elev_sheet, elev_datum):
    """A level tag is text written beside its line. Fitting the text gives the
    right *scale*, because a constant offset cancels in the gradient — which is
    why storey heights were already exact — but it puts the datum wherever the
    lettering sat. A sill is measured from the datum outright, so it inherited
    the error: 1.26m read for a 1.10m sill until the tags were snapped."""
    raw = E.find_level_tags(elev_sheet)
    snapped = E.snap_to_level_lines(elev_sheet, raw)
    moved = [b.y_px - a.y_px for a, b in zip(raw, snapped)]
    assert all(abs(m) > 0.5 for m in moved), "the tags sat off their lines"
    assert all(abs(m) < E.SNAP_PT for m in moved)
    # one shift for all of them, not a per-tag snap: a drafter letters the same
    # way each time, and snapping individually drags a tag onto whatever line
    # happens to be nearest — a string course, a band — and corrupts the fit
    assert max(moved) - min(moved) < 1e-9, "the correction must be uniform"


def test_the_datum_puts_the_ground_line_at_zero(elev_datum):
    """The whole point of snapping: level 0.00 is where the drawing puts it."""
    ground = min(elev_datum.tags, key=lambda t: t.value_ft)
    assert elev_datum.level_at(ground.y_px) == pytest.approx(0.0, abs=0.02)


def test_the_parapet_is_measured_above_the_roof_level(elev_sheet, elev_datum):
    height = E.parapet_height(elev_sheet, elev_datum)
    assert height is not None
    assert height * M_PER_FT == pytest.approx(1.05, abs=0.05)


# --------------------------------------------------------------------------- #
# nothing without a datum
# --------------------------------------------------------------------------- #
@needs_example
def test_a_sheet_with_no_datum_yields_no_openings_either(gf_sheet):
    """A sill measured against a scale that was never established is a number
    with no meaning — worse than the setting it would replace."""
    datum, out = E.read_sheet(gf_sheet, "gf")
    assert datum is None and out == []


@needs_example
def test_the_whole_set_measures_sills_parapet_and_storeys(example_pdfs, tmp_path):
    import shutil

    from app.pipeline import run

    folder = tmp_path / "set6"
    folder.mkdir()
    for pdf in example_pdfs:
        shutil.copy(pdf, folder)
    ef.write(str(folder / "front elevation.pdf"))
    _i, _e, params, _r, _n = run([str(p) for p in folder.glob("*.pdf")])

    for field in ("floor_to_floor_ft", "window_sill_ft", "window_head_ft"):
        assert params.level(0).provenance[field].source == "measured", field
    assert params.provenance["parapet_ft"].source == "measured"
    # and what the set genuinely does not state stays an assumption
    assert params.level(0).provenance["door_head_ft"].source == "default"
