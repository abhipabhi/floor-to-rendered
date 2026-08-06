"""The structural schedules.

Every assertion here is against something the drawings say *in writing*, so a
failure means either the reader broke or the drawing changed — never that a
tolerance needed loosening.
"""

import pytest

from app import schedule
from conftest import needs_schedules


# --------------------------------------------------------------------------- #
# the ruled grid
# --------------------------------------------------------------------------- #
@needs_schedules
def test_the_footing_schedule_is_found_as_a_real_grid(detail_sheet):
    tables = schedule.find_tables(detail_sheet)
    assert tables, "the schedule is ruled with vector lines; it should be found"
    # the widest table is the schedule: 9 columns from S.NO to the rebar cage
    assert max(t.n_cols for t in tables) >= 9


@needs_schedules
def test_a_rule_that_does_not_span_does_not_split_a_cell(detail_sheet):
    """One header rule covers only part of the table's width — the merged
    "FOUNDATION DETAILS" heading. Treating the grid as a global lattice would
    invent cell boundaries where the drawing has none."""
    table = max(schedule.find_tables(detail_sheet), key=lambda t: t.n_cols)
    partial = [
        r for r in schedule._rules(detail_sheet)
        if r.axis == "h" and abs(r.pos - 179.2) < 1.5
    ]
    assert partial, "the partial header rule is still on the sheet"
    assert all(r.b - r.a < (table.cols[-1] - table.cols[0]) * 0.5 for r in partial)


# --------------------------------------------------------------------------- #
# the footing schedule
# --------------------------------------------------------------------------- #
@needs_schedules
def test_footings_read_exactly_what_the_schedule_prints(detail_sheet):
    got = {f.tag: f for f in schedule.footings(detail_sheet)}
    assert set(got) == {"F1", "F2"}

    assert got["F1"].col_type == "T1"
    assert got["F1"].size_ft == pytest.approx((4.5, 4.5))       # 4'6" X 4'6"
    assert got["F2"].col_type == "T2"
    assert got["F2"].size_ft == pytest.approx((5.0, 5.0))       # 5'0" X 5'0"

    for f in got.values():
        assert f.d1_ft == pytest.approx(0.5)                    # 6"
        assert f.d2_ft == pytest.approx(1.0)                    # 12"
        assert f.depth_ft == pytest.approx(1.5)                 # 1'6"
        assert f.depth_ft == pytest.approx(f.d1_ft + f.d2_ft), "D is D1 + D2"


@needs_schedules
def test_the_written_column_size_matches_the_columns_drawn_on_another_sheet(
    detail_sheet, gf_extract
):
    """The strongest check available on this set.

    The schedule *writes* COL-10"X15" on the details sheet. The floor plan
    *draws* thirteen columns, measured through a scale calibrated from room
    labels — a completely different sheet by a completely different method.
    If those two ever disagree, the scale is wrong and the tool should say so
    rather than quietly building a mis-sized house.
    """
    import statistics

    written = schedule.footings(detail_sheet)[0].column_ft
    assert written == pytest.approx((10 / 12, 15 / 12))

    drawn = (
        statistics.median(c.x1 - c.x0 for c in gf_extract.columns),
        statistics.median(c.y1 - c.y0 for c in gf_extract.columns),
    )
    assert len(gf_extract.columns) == 13
    assert drawn[0] == pytest.approx(written[0], rel=0.02)
    assert drawn[1] == pytest.approx(written[1], rel=0.02)


# --------------------------------------------------------------------------- #
# tie beams — text, never the box
# --------------------------------------------------------------------------- #
@needs_schedules
def test_all_four_tie_beam_sections_are_read(tie_beam_sheet):
    beams = {b.tag: b for b in schedule.tie_beams(tie_beam_sheet)}
    assert set(beams) == {"TB1", "TB2", "TB3", "TB4"}
    for tag in ("TB1", "TB2", "TB3"):
        assert beams[tag].width_ft == pytest.approx(10 / 12)
        assert beams[tag].depth_ft == pytest.approx(12 / 12)
    assert beams["TB4"].depth_ft == pytest.approx(15 / 12)


@needs_schedules
def test_the_nts_detail_boxes_are_not_measured(tie_beam_sheet):
    """TB1 and TB4 are drawn in identically sized boxes but state different
    depths. Anything that measured the box would make them the same beam."""
    beams = {b.tag: b for b in schedule.tie_beams(tie_beam_sheet)}
    assert beams["TB1"].depth_ft != beams["TB4"].depth_ft
    assert '15"' in beams["TB4"].raw and '12"' in beams["TB1"].raw


@needs_schedules
def test_the_stated_slab_projection_is_read(tie_beam_sheet):
    proj = schedule.slab_projection_ft(tie_beam_sheet)
    assert proj is not None
    assert proj[0] == pytest.approx(1.0)  # slab proj. 1'


# --------------------------------------------------------------------------- #
# notes
# --------------------------------------------------------------------------- #
@needs_schedules
def test_notes_are_read_despite_the_sheets_own_spelling(detail_sheet):
    n = schedule.notes(detail_sheet)
    assert n.cutting_depth_ft == pytest.approx(5.5)     # 5'6" below NGL
    assert n.bearing_depth_ft == pytest.approx(6 + 7 / 12)
    assert n.designed_for == "G+2ND"                    # "HAS BEAN DESIGN FOR"
    assert n.concrete_grade == "M-200"


@needs_schedules
def test_the_seismic_zone_is_not_confused_with_a_table_header(detail_sheet):
    """The word ZONE also appears as a column heading followed by COLUMN and
    FLOOR. Anchoring to the earthquake clause is what keeps that out."""
    n = schedule.notes(detail_sheet)
    assert n.seismic_zone == "II"
    assert "COLUMN" not in n.raw.get("seismic_zone", "").upper()


# --------------------------------------------------------------------------- #
# what reaches the model
# --------------------------------------------------------------------------- #
@needs_schedules
def test_readings_are_derived_and_carry_their_evidence(tie_beam_sheet):
    by_key = {r.key: r.q for r in schedule.readings(tie_beam_sheet, "tb")}
    assert by_key["building.plinth_beam_depth_ft"].ft == pytest.approx(15 / 12)
    for q in by_key.values():
        assert q.source == "derived"
        assert q.evidence, "a derived number must say what it was derived from"


@needs_schedules
def test_no_storey_height_is_invented_from_these_sheets(detail_sheet, tie_beam_sheet):
    """These sheets describe the foundation and the members. Deducing a floor
    height from "G+2ND", or from a beam depth, would be invention — which is the
    one thing this tool must never do."""
    keys = {
        r.key
        for s in (detail_sheet, tie_beam_sheet)
        for r in schedule.readings(s, "x")
    }
    assert not any("floor_to_floor" in k or "wall_height" in k for k in keys)
