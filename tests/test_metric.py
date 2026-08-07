"""Metric drawings.

The published example set is entirely imperial, so everything here works
against a sheet drawn by :mod:`metric_fixture` whose true dimensions we chose.
That makes the answers not a matter of opinion: if the reader says a room is
3.59 m and the sheet was drawn at 3.60, the 10 mm is the error.
"""

import pytest

import metric_fixture as mf
from app.extract import extract_plan
from app.pdfvec import load_sheet
from app.units import (
    detect_units,
    find_room_dim,
    parse_length_any,
    parse_length_ft,
    parse_length_m,
)
from conftest import needs_example

M_PER_FT = 0.3048


@pytest.fixture(scope="module")
def metric_extract(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("metric") / "plan.pdf")
    mf.write(path)
    return extract_plan(load_sheet(path, "plan.pdf"), "m", 0, "Ground floor")


# --------------------------------------------------------------------------- #
# telling the two systems apart
# --------------------------------------------------------------------------- #
def test_a_foot_mark_means_imperial():
    u = detect_units("BED ROOM 11'0\"X12'0\"  HALL 10'0\"X15'4\"")
    assert u.units == "imperial" and u.confidence == "high"


def test_metre_marks_and_a_ratio_scale_mean_metric():
    u = detect_units("LIVING 4.00 x 4.20  SCALE 1:100  PLOT SIZE 8.00m x 15.00m")
    assert u.units == "metric" and u.confidence == "high"


def test_a_level_tag_is_a_metric_mark():
    assert detect_units("GROUND FLOOR LEVEL ±0.00").units == "metric"


def test_a_sheet_with_no_marks_admits_it_is_guessing():
    u = detect_units("GROUND FLOOR PLAN")
    assert u.units == "imperial"
    assert u.confidence == "low"
    assert "assuming" in u.evidence


def test_a_metric_sheet_quoting_an_imperial_scale_still_reads_metric():
    """Drawings sometimes carry one stray imperial note. The majority wins,
    and the evidence records that it was a mixture."""
    u = detect_units(
        "SCALE 1:100  8.00m x 15.00m  4.00m  3.60m  2.40m  (1/4\"=1'-0\" equiv)"
    )
    assert u.units == "metric"
    assert "vs" in u.evidence


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,metres",
    [("4.00m", 4.0), ("3600mm", 3.6), ("250 mm", 0.25), ("2.4 M", 2.4), ("+3.60", 3.6)],
)
def test_metric_tokens_parse(text, metres):
    assert parse_length_m(text) == pytest.approx(metres)


def test_a_bare_number_outside_building_range_is_refused_not_guessed():
    """3600 with no unit could be millimetres or nonsense. Refusing is the only
    honest answer; guessing millimetres would be inventing a convention."""
    assert parse_length_m("3600") is None
    assert parse_length_m("0.001") is None


def test_parsing_in_the_sheets_own_system_returns_feet():
    assert parse_length_any("3.048m", "metric") == pytest.approx(10.0)
    assert parse_length_any("10'", "imperial") == pytest.approx(10.0)


def test_a_metric_dimension_split_across_words_is_still_found():
    """A metric sheet writes "4.00 x 4.20", which the PDF hands over as three
    separate tokens — so it only exists once the whole label block is read."""
    rd = find_room_dim("LIVING 4.00 x 4.20", "metric")
    assert rd is not None
    assert rd.a == pytest.approx(4.00 / M_PER_FT, rel=1e-6)
    assert rd.b == pytest.approx(4.20 / M_PER_FT, rel=1e-6)


def test_imperial_labels_are_untouched_by_the_metric_path():
    assert find_room_dim("BED ROOM 11'0\"X12'0\"", "imperial") is not None
    # and the imperial parser has not learned to accept bare decimals
    assert parse_length_ft("4.00") is None


# --------------------------------------------------------------------------- #
# the whole pipeline on a metric sheet
# --------------------------------------------------------------------------- #
def test_a_metric_sheet_calibrates_off_its_own_room_labels(metric_extract):
    s = metric_extract.scale
    assert s.method == "room_labels"
    assert s.confidence == "high"
    assert s.px_per_ft == pytest.approx(mf.EXPECTED_PT_PER_FT, rel=0.01)


def test_every_room_is_found_and_measures_what_it_is_labelled(metric_extract):
    got = {r.name.split()[0].upper(): r for r in metric_extract.rooms}
    assert set(got) == set(mf.ROOMS)
    for name, (w_m, h_m) in mf.ROOMS.items():
        measured = got[name].measured_ft
        assert measured is not None
        assert measured[0] * M_PER_FT == pytest.approx(w_m, abs=0.05)
        assert measured[1] * M_PER_FT == pytest.approx(h_m, abs=0.05)


def test_the_building_comes_out_the_size_it_was_drawn(metric_extract):
    x0, y0, x1, y1 = metric_extract.bounds
    assert (x1 - x0) * M_PER_FT == pytest.approx(mf.EXTERNAL[0], abs=0.05)
    assert (y1 - y0) * M_PER_FT == pytest.approx(mf.EXTERNAL[1], abs=0.05)


def test_both_wall_thicknesses_are_recovered(metric_extract):
    """230mm and 115mm brick are the metric equivalents of the 10" and 5" the
    imperial set is built from — the reader has to find both, not just one."""
    thick = sorted({round(w.thickness_ft * 1000 * M_PER_FT) for w in metric_extract.walls})
    assert any(abs(t - mf.EXT_WALL * 1000) <= 12 for t in thick)
    assert any(abs(t - mf.PARTITION * 1000) <= 12 for t in thick)


def test_the_scale_check_speaks_millimetres_on_a_metric_sheet(metric_extract):
    """A metric wall is 115 or 230mm and is never a round number of inches, so
    checking for round inches would report a false problem on every such sheet."""
    note = next(n for n in metric_extract.warnings if "wall thicknesses come out" in n)
    assert "mm" in note and '"' not in note
    assert "centimetres confirm the scale" in note


def test_the_separator_is_not_mistaken_for_part_of_the_room_name(metric_extract):
    for r in metric_extract.rooms:
        assert "X" not in r.name.split(), f"{r.name!r} picked up the x of '4.00 x 4.20'"


# --------------------------------------------------------------------------- #
# the real set must not be affected
# --------------------------------------------------------------------------- #
@needs_example
def test_the_example_set_still_reads_as_imperial(gf_sheet, ff_sheet):
    for sheet in (gf_sheet, ff_sheet):
        u = detect_units(sheet.text)
        assert u.units == "imperial"
        assert u.confidence == "high"
