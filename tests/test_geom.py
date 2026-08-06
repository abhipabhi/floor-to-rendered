import numpy as np
import pytest

from app import geom
from app.geom import Run
from app.pdfvec import Seg


def test_union_and_gap_bridging():
    assert geom.union([(0, 2), (1, 3), (5, 6)]) == [(0, 3), (5, 6)]
    assert geom.union([(0, 2), (2.5, 4)], join_gap=1.0) == [(0, 4)]
    assert geom.union([(0, 2), (2.5, 4)], join_gap=0.1) == [(0, 2), (2.5, 4)]


def test_intersect_and_subtract():
    assert geom.intersect([(0, 10)], [(2, 4), (6, 12)]) == [(2, 4), (6, 10)]
    assert geom.subtract([(0, 10)], [(2, 4)]) == [(0, 2), (4, 10)]
    assert geom.subtract([(0, 10)], [(0, 10)]) == []
    assert geom.total([(0, 3), (5, 6)]) == 4


def _h(y, x0, x1, pen=0.72):
    return Seg(x0, y, x1, y, "black", pen)


def _v(x, y0, y1, pen=0.72):
    return Seg(x, y0, x, y1, "black", pen)


def test_runs_merge_collinear_but_keep_openings():
    segs = [_h(10, 0, 20), _h(10, 20.5, 40), _h(10, 60, 80)]
    runs = geom.build_runs(segs, "h", join_gap=1.2)
    assert len(runs) == 2
    assert runs[0].a == 0 and runs[0].b == 40
    assert runs[1].a == 60


def test_runs_separate_by_pen():
    """A dimension line drawn with a different pen never joins a wall face."""
    segs = [_h(10, 0, 40, pen=0.72), _h(10, 0, 40, pen=0.84)]
    runs = geom.build_runs(segs, "h")
    assert len(runs) == 2
    assert {r.pen for r in runs} == {0.72, 0.84}


def test_pair_bands_needs_matching_pen():
    same = geom.build_runs([_h(10, 0, 40), _h(14.2, 0, 40)], "h")
    assert len(geom.pair_bands(same, 2.0, 20.0, 5.0)) == 1

    mixed = geom.build_runs([_h(10, 0, 40, pen=0.72), _h(14.2, 0, 40, pen=0.84)], "h")
    assert geom.pair_bands(mixed, 2.0, 20.0, 5.0) == []


def test_band_geometry():
    runs = geom.build_runs([_v(5, 0, 30), _v(13.4, 0, 30)], "v")
    bands = geom.pair_bands(runs, 2.0, 20.0, 5.0)
    assert len(bands) == 1
    b = bands[0]
    assert b.thickness == pytest.approx(8.4)
    assert b.rect() == pytest.approx((5.0, 0.0, 13.4, 30.0))


def test_bands_only_where_both_faces_are_drawn():
    """A doorway leaves one face alone, so the band stops there."""
    runs = geom.build_runs([_h(10, 0, 40), _h(14.2, 0, 25)], "h")
    bands = geom.pair_bands(runs, 2.0, 20.0, 5.0)
    assert len(bands) == 1
    assert bands[0].b == pytest.approx(25.0)


def test_decompose_rects_is_disjoint_and_complete():
    mask = np.zeros((6, 6), dtype=bool)
    mask[1:5, 1:4] = True
    mask[2, 5] = True
    rects = geom.decompose_rects(mask)
    covered = np.zeros_like(mask)
    for r0, c0, r1, c1 in rects:
        assert not covered[r0:r1, c0:c1].any(), "rectangles overlap"
        covered[r0:r1, c0:c1] = True
    assert (covered == mask).all()


def test_label_regions_separates_rooms():
    raster = geom.make_raster(0, 0, 20, 10, 1.0)
    raster.fill_rect(9, -2, 10, 14)  # a dividing wall, right across the grid
    regions = geom.label_regions(raster)
    left = regions.at(3, 5)
    right = regions.at(15, 5)
    assert left and right and left != right


def test_label_regions_joins_through_a_gap():
    raster = geom.make_raster(0, 0, 20, 10, 1.0)
    raster.fill_rect(9, -2, 10, 4)
    raster.fill_rect(9, 6, 10, 14)
    regions = geom.label_regions(raster)
    assert regions.at(3, 5) == regions.at(15, 5)


def test_median_extent_ignores_a_doorway_recess():
    raster = geom.make_raster(0, 0, 30, 30, 0.5)
    for x0, y0, x1, y1 in [(0, 0, 30, 1), (0, 29, 30, 30), (0, 0, 1, 30), (29, 0, 30, 30)]:
        raster.fill_rect(x0, y0, x1, y1)
    regions = geom.label_regions(raster)
    lab = regions.at(15, 15)
    w, h = regions.median_extent(lab)
    assert w == pytest.approx(28.0, abs=0.6)
    assert h == pytest.approx(28.0, abs=0.6)


def test_fill_rect_thin_rectangle_still_marks_a_cell():
    raster = geom.make_raster(0, 0, 10, 10, 1.0)
    raster.fill_rect(4, 4, 4.1, 8)
    assert raster.grid.any()
