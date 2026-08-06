"""The extractor, checked against the drawings it was built from.

The assertions are deliberately tied to things the drawings state in writing —
room labels, the 29' × 39'-10" column grid on the layout sheet, 5" and 10" wall
thicknesses — so a regression shows up as a disagreement with the paper, not
with a previous run of this code.
"""

import pytest

from app.classify import FLOOR_PLAN, classify
from app.extract import (
    column_fills,
    cluster_words,
    north_bearing,
    room_labels,
    spacing_histogram,
    wall_spacings,
)
from app.pdfvec import load_sheet
from conftest import FF, FOOTING, GF, LAYOUT, needs_example

pytestmark = needs_example


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path,kind,level",
    [
        (GF, FLOOR_PLAN, 0),
        (FF, FLOOR_PLAN, 1),
        (LAYOUT, "layout", None),
        (FOOTING, "foundation", None),
    ],
)
def test_classification(path, kind, level):
    import os

    sheet = load_sheet(path)
    c = classify(sheet.text, os.path.basename(path), has_vectors=len(sheet.segs) > 20)
    assert c.kind == kind
    assert c.level == level


def test_layout_sheet_is_not_mistaken_for_the_ground_floor():
    """The layout embeds the ground floor plan; only its title distinguishes it."""
    sheet = load_sheet(LAYOUT)
    assert "BED ROOM" in sheet.text  # it really does contain the plan
    assert classify(sheet.text, "LAY OUT PLAN.pdf").kind == "layout"


# --------------------------------------------------------------------------- #
# scale
# --------------------------------------------------------------------------- #
def test_scale_agrees_with_the_drawing(gf_extract, ff_extract):
    for ex in (gf_extract, ff_extract):
        assert ex.scale.method == "room_labels"
        assert ex.scale.confidence in ("high", "medium")
        assert ex.scale.px_per_ft == pytest.approx(10.1, rel=0.02)


def test_both_storeys_calibrate_to_the_same_scale(gf_extract, ff_extract):
    a, b = gf_extract.scale.px_per_ft, ff_extract.scale.px_per_ft
    assert abs(a - b) / a < 0.01


def test_wall_thicknesses_are_whole_inches(gf_extract):
    inches = sorted(
        {round(w.thickness_ft * 12, 1) for w in gf_extract.walls if not w.is_railing}
    )
    assert 5.0 == pytest.approx(min(inches), abs=0.3)
    assert any(abs(i - 10.0) < 0.4 for i in inches)
    for i in inches:
        assert abs(i - round(i)) < 0.45, f"{i}in is not a whole number of inches"


def test_no_wall_is_thicker_than_it_is_long(gf_extract, ff_extract):
    """Stub bands at balcony corners used to show as lumps on the facade."""
    for ex in (gf_extract, ff_extract):
        for w in ex.walls:
            if w.is_railing:
                continue
            assert w.length_ft >= 1.4 * w.thickness_ft, w.id


def test_wall_spacing_histogram_finds_two_thicknesses(gf_sheet):
    from app import geom
    from app.extract import Diagnostics, _clip_runs, building_region

    d = Diagnostics()
    blocks = cluster_words(gf_sheet.words)
    region = building_region(gf_sheet, room_labels(blocks), column_fills(gf_sheet), d)
    runs = _clip_runs(geom.build_runs(gf_sheet.segs, "h"), region) + _clip_runs(
        geom.build_runs(gf_sheet.segs, "v"), region
    )
    sig = wall_spacings(spacing_histogram(runs))
    assert len(sig) == 2
    assert sig[0] == pytest.approx(4.25, abs=0.3)  # 5"
    assert sig[1] == pytest.approx(8.5, abs=0.3)  # 10"


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def test_ground_floor_matches_the_setting_out_grid(gf_extract):
    """The layout sheet dimensions the column grid 29' × 39'-10"."""
    xs = [v for c in gf_extract.columns for v in (c.x0, c.x1)]
    ys = [v for c in gf_extract.columns for v in (c.y0, c.y1)]
    assert (max(xs) - min(xs)) == pytest.approx(29.0, abs=0.3)
    assert (max(ys) - min(ys)) == pytest.approx(39.83, abs=0.3)


def test_the_toilet_bay_projects_a_foot_past_the_grid(gf_extract, ff_extract):
    """The toilet sits in a 1 ft bump-out, which the model has to include.

    Its side walls tee into the facade, so they never form a band of their own;
    without the corner extension the bay is missing and the toilet is open to
    the street.
    """
    for ex in (gf_extract, ff_extract):
        grid_top = min(c.y0 for c in ex.columns)
        assert ex.bounds[1] == pytest.approx(grid_top - 1.0, abs=0.25)

        bay = [w for w in ex.walls if w.axis == "h" and w.y1 < grid_top - 0.1]
        assert bay, "the bay's outer wall"
        assert any(o.kind == "window" for w in bay for o in w.openings)
        sides = [
            w
            for w in ex.walls
            if w.axis == "v" and w.y0 < grid_top - 0.5 and not w.is_railing
        ]
        assert len(sides) >= 2, "the bay is closed on both sides"


def test_columns(gf_extract, ff_extract):
    assert len(gf_extract.columns) == 13
    assert len(ff_extract.columns) == 13
    for c in gf_extract.columns:
        w, d = (c.x1 - c.x0) * 12, (c.y1 - c.y0) * 12
        assert 8 <= min(w, d) <= 12
        assert 12 <= max(w, d) <= 18


def test_storeys_share_a_datum(example_pdfs):
    """Pinned on the column grid and pooled to one scale, storeys stack exactly."""
    from app.pipeline import extract_included, ingest

    ing = ingest(example_pdfs)
    extracts, notes = extract_included(ing, ing.sheets)
    assert any("one drawing scale" in n for n in notes)
    gf, ff = sorted(extracts.values(), key=lambda e: e.level)
    assert gf.scale.px_per_ft == ff.scale.px_per_ft
    gx = sorted(round(c.x0, 2) for c in gf.columns)
    fx = sorted(round(c.x0, 2) for c in ff.columns)
    assert gx == pytest.approx(fx, abs=0.06)
    gy = sorted(round(c.y0, 2) for c in gf.columns)
    fy = sorted(round(c.y0, 2) for c in ff.columns)
    assert gy == pytest.approx(fy, abs=0.06)


def test_openings_are_plausible(gf_extract):
    doors = [o for w in gf_extract.walls for o in w.openings if o.kind == "door"]
    windows = [o for w in gf_extract.walls for o in w.openings if o.kind == "window"]
    assert len(doors) >= 6
    assert len(windows) >= 4
    for d in doors:
        assert 1.8 <= d.width_ft <= 5.0
    for w in windows:
        assert 1.0 <= w.width_ft <= 12.0


def test_no_doorway_hangs_off_the_end_of_the_building(gf_extract, ff_extract):
    """The site boundary is drawn along the same line as the front wall.

    It carries on past the building, and the overshoot used to be read as a
    doorway at the road end of the first floor.
    """
    for ex in (gf_extract, ff_extract):
        x0, y0, x1, y1 = ex.bounds
        for w in ex.walls:
            for o in w.openings:
                if w.axis == "h":
                    assert x0 - 0.3 <= min(o.u0, o.u1)
                    assert max(o.u0, o.u1) <= x1 + 0.3
                else:
                    assert y0 - 0.3 <= min(o.u0, o.u1)
                    assert max(o.u0, o.u1) <= y1 + 0.3


def test_the_first_floor_stops_at_its_own_walls(ff_extract):
    """29'-1" across, not 33 ft: nothing of the road may end up in the model."""
    x0, _y0, x1, _y1 = ff_extract.bounds
    assert (x1 - x0) == pytest.approx(29.1, abs=0.4)


def test_the_ground_floor_has_no_doors_in_its_external_walls(gf_extract):
    """Every way into this house is on the road side, which is the first floor's
    balcony and the ground floor's open car port — neither is a door in a wall.

    Stretches of external wall where the drafter did not carry the inner face
    between columns used to come out as doorways, so the model appeared to have
    a way in beside the toilet.
    """
    for w in gf_extract.walls:
        if not w.exterior or w.is_railing:
            continue
        assert not [o for o in w.openings if o.kind == "door"], w.id


def test_a_duct_is_not_a_doorway(gf_extract):
    ducts = [r for r in gf_extract.rooms if "Duct" in r.name]
    assert ducts, "the ground floor has a duct"
    for w in gf_extract.walls:
        for o in w.openings:
            if o.kind != "door":
                continue
            box = (
                (min(o.u0, o.u1), w.y0, max(o.u0, o.u1), w.y1)
                if w.axis == "h"
                else (w.x0, min(o.u0, o.u1), w.x1, max(o.u0, o.u1))
            )
            for d in ducts:
                overlap_x = min(box[2], d.x1) - max(box[0], d.x0)
                overlap_y = min(box[3], d.y1) - max(box[1], d.y0)
                assert overlap_x < -0.2 or overlap_y < -0.2, (
                    f"{w.id} opens onto the duct"
                )


def test_doors_are_wide_enough_to_walk_through(gf_extract, ff_extract):
    for ex in (gf_extract, ff_extract):
        for w in ex.walls:
            for o in w.openings:
                if o.kind == "door":
                    assert o.width_ft >= 2.0


def _edge_gaps(ex, axis, edge, span):
    """Stretches of an outside edge with no wall on them."""
    from app import geom

    if axis == "h":
        segs = [
            (w.x0, w.x1)
            for w in ex.walls
            if w.axis == "h"
            and not w.is_railing
            and (abs(w.y0 - edge) < 1.2 or abs(w.y1 - edge) < 1.2)
        ]
    else:
        segs = [
            (w.y0, w.y1)
            for w in ex.walls
            if w.axis == "v"
            and not w.is_railing
            and (abs(w.x0 - edge) < 1.2 or abs(w.x1 - edge) < 1.2)
        ]
    return [g for g in geom.subtract([span], geom.union(segs)) if g[1] - g[0] > 0.3]


def test_the_ground_floor_envelope_has_no_holes_in_it(gf_extract):
    """Every gap in an outside wall must be a real opening, not a lapse.

    Where the drafter let a wall's inner face lapse for a couple of feet, the
    band broke and left a hole in the facade — at the back-right corner, under
    the balcony above. A gap narrower than a door with a face drawn across it is
    wall, not opening.
    """
    x0, y0, x1, y1 = gf_extract.bounds
    for axis, edge, span in (
        ("h", y0, (x0, x1)),
        ("h", y1, (x0, x1)),
        ("v", x0, (y0, y1)),
        ("v", x1, (y0, y1)),
    ):
        for a, b in _edge_gaps(gf_extract, axis, edge, span):
            width = b - a
            # the only openings left are the car port front and the projecting
            # bay, where the side walls take over
            assert width > 5.0 or width <= 1.05, (
                f"{width:.2f} ft hole in the {axis} edge at {edge:.2f} ({a:.2f}..{b:.2f})"
            )


def test_the_back_right_corner_is_walled_on_the_ground_floor(gf_extract, ff_extract):
    """The first floor is open there — it is a balcony — but the ground floor
    below it is a bedroom and must be closed."""
    x1 = gf_extract.bounds[2]
    right = [
        (w.y0, w.y1)
        for w in gf_extract.walls
        if w.axis == "v" and not w.is_railing and abs(w.x1 - x1) < 1.2
    ]
    from app import geom

    covered = geom.union(right)
    assert any(a <= 0.1 and b >= 3.5 for a, b in covered), (
        "the ground floor's right wall runs past the balcony above"
    )
    # and the first floor really is open there, held by a railing
    assert any(
        w.is_railing and w.axis == "v" and w.y0 < 0.5 and w.x1 > ff_extract.bounds[2] - 1.0
        for w in ff_extract.walls
    )


def test_openings_stay_inside_their_wall(gf_extract, ff_extract):
    for ex in (gf_extract, ff_extract):
        for w in ex.walls:
            lo, hi = w.u_range()
            for o in w.openings:
                assert lo - 0.05 <= min(o.u0, o.u1)
                assert max(o.u0, o.u1) <= hi + 0.05


def test_walls_do_not_duplicate_each_other(gf_extract):
    for i, a in enumerate(gf_extract.walls):
        for b in gf_extract.walls[i + 1 :]:
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.y1, b.y1) - max(a.y0, b.y0)
            overlap = max(0.0, ox) * max(0.0, oy)
            smaller = min(
                a.length_ft * a.thickness_ft, b.length_ft * b.thickness_ft
            )
            assert overlap < 0.85 * smaller


def test_rooms_measure_close_to_their_labels(gf_extract):
    checked = 0
    for r in gf_extract.rooms:
        if not r.label_ft or not r.measured_ft:
            continue
        a, b = sorted(r.label_ft)
        m, n = sorted(r.measured_ft)
        assert m == pytest.approx(a, rel=0.06)
        assert n == pytest.approx(b, rel=0.06)
        checked += 1
    assert checked >= 3


def test_balcony_edges_come_out_as_railings(ff_extract):
    """Both first-floor balconies are drawn as glazing-pen lines with no wall."""
    rails = [w for w in ff_extract.walls if w.is_railing]
    assert len(rails) >= 4
    for r in rails:
        assert r.thickness_ft * 12 <= 6.0, "a railing is thin"
        assert r.length_ft >= 2.0

    # the 3'0" balcony runs along the top of the plan, the 2'6" one along the
    # bottom; both are ~12 ft of open edge
    long_rails = sorted(r.length_ft for r in rails if r.length_ft > 8)
    assert len(long_rails) >= 2
    assert all(10 < L < 14 for L in long_rails)


def test_railings_are_not_walls(ff_extract):
    for w in ff_extract.walls:
        if w.is_railing:
            assert w.openings == []
        else:
            assert w.kind == "wall"


def test_north_is_read_from_the_compass(gf_sheet):
    bearing = north_bearing(gf_sheet)
    assert bearing is not None
    # on this sheet the compass puts north along plan +x
    assert bearing == pytest.approx(0.0, abs=5) or bearing == pytest.approx(360, abs=5)


def test_hand_calibrating_to_the_measured_scale_reproduces_it(gf_sheet, gf_extract):
    from app.extract import extract_plan

    b = extract_plan(
        gf_sheet, "gf", 0, "Ground floor", px_per_ft_override=gf_extract.scale.px_per_ft
    )
    assert b.scale.method == "manual"
    assert b.bounds == pytest.approx(gf_extract.bounds, abs=1e-6)


def test_hand_calibrating_off_by_five_percent_moves_dimensions_by_five_percent(
    gf_sheet, gf_extract
):
    from app.extract import extract_plan

    px = gf_extract.scale.px_per_ft
    b = extract_plan(gf_sheet, "gf", 0, "Ground floor", px_per_ft_override=px * 1.05)
    width_a = gf_extract.bounds[2] - gf_extract.bounds[0]
    width_b = b.bounds[2] - b.bounds[0]
    assert width_b == pytest.approx(width_a / 1.05, rel=0.01)
