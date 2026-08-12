"""Everything outside the building line.

**None of this is measured.** A floor plan does not say where the compound wall
runs, how many trees there are, or what is parked outside — so the site is
staging, in the same category as the heights: it exists to make the model
presentable, it is entirely under the user's control, and it lands in its own
``Site — …`` groups so it can be deleted in one selection.

What it *does* take from the drawing is placement: the road side comes from the
sheet's own ROAD label, and the car goes in the room the plan labels as parking.
Staging that sits where the drawing says is better than staging that does not.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .mesh import Scene
from .models import PlanExtract, SiteParams
from .units import FT_TO_M

PARK_WORDS = {"PARKING", "GARAGE", "CAR", "PORCH"}


@dataclass
class Plot:
    """The site in plan feet, and which way the road is."""

    x0: float
    y0: float
    x1: float
    y1: float
    road: str  # "+y", "-y", "+x", "-x" — the side the road is on
    drive_from: float  # centre of the driveway along the road edge
    drive_width: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def depth(self) -> float:
        return self.y1 - self.y0


def road_side(ex: PlanExtract, road_xy: tuple[float, float] | None) -> str:
    """Which side of the building the road is on, from the sheet's ROAD label."""
    x0, y0, x1, y1 = ex.bounds
    if road_xy is None:
        return "+y"
    rx, ry = road_xy
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = rx - cx, ry - cy
    if abs(dy) >= abs(dx):
        return "+y" if dy > 0 else "-y"
    return "+x" if dx > 0 else "-x"


def parking_area(ex: PlanExtract):
    """The room the plan calls parking, if it named one."""
    best = None
    for r in ex.rooms:
        words = set(r.name.upper().replace("-", " ").split())
        if words & PARK_WORDS:
            area = (r.x1 - r.x0) * (r.y1 - r.y0)
            if best is None or area > best[0]:
                best = (area, r)
    return best[1] if best else None


def plot_for(ex: PlanExtract, params: SiteParams, road_xy=None) -> Plot:
    """The compound, offset from the building by the setbacks."""
    x0, y0, x1, y1 = ex.bounds
    side = road_side(ex, road_xy)
    front = params.front_setback_ft
    sides = params.side_setback_ft

    px0 = x0 - (front if side == "-x" else sides)
    px1 = x1 + (front if side == "+x" else sides)
    py0 = y0 - (front if side == "-y" else sides)
    py1 = y1 + (front if side == "+y" else sides)

    park = parking_area(ex)
    if side in ("+y", "-y"):
        centre = ((park.x0 + park.x1) / 2) if park else (x0 + x1) / 2
        width = (park.x1 - park.x0) if park else 10.0
    else:
        centre = ((park.y0 + park.y1) / 2) if park else (y0 + y1) / 2
        width = (park.y1 - park.y0) if park else 10.0
    width = max(params.gate_width_ft, min(width, 16.0))
    return Plot(px0, py0, px1, py1, side, centre, width)


# --------------------------------------------------------------------------- #
# pieces
# --------------------------------------------------------------------------- #
def _box(mesh, x0, y0, x1, y1, z0, z1) -> None:
    mesh.add_box(
        x0 * FT_TO_M, z0 * FT_TO_M, y0 * FT_TO_M, x1 * FT_TO_M, z1 * FT_TO_M, y1 * FT_TO_M
    )


def add_boundary(scene: Scene, plot: Plot, params: SiteParams, ground_z: float) -> None:
    """A compound wall with a gate opening facing the road."""
    m = scene.mesh("Site — boundary wall", "boundary")
    t = params.boundary_thickness_ft
    h = params.boundary_height_ft
    z0, z1 = ground_z, ground_z + h
    x0, y0, x1, y1 = plot.x0, plot.y0, plot.x1, plot.y1
    gate = params.gate_width_ft
    a, b = plot.drive_from - gate / 2, plot.drive_from + gate / 2

    def run(axis: str, pos: float, lo: float, hi: float, gap=None) -> None:
        spans = [(lo, hi)]
        if gap:
            spans = [(lo, gap[0]), (gap[1], hi)]
        for s0, s1 in spans:
            if s1 - s0 <= 0.05:
                continue
            if axis == "h":
                _box(m, s0, pos - t / 2, s1, pos + t / 2, z0, z1)
            else:
                _box(m, pos - t / 2, s0, pos + t / 2, s1, z0, z1)

    run("h", y0, x0, x1, (a, b) if plot.road == "-y" else None)
    run("h", y1, x0, x1, (a, b) if plot.road == "+y" else None)
    run("v", x0, y0, y1, (a, b) if plot.road == "-x" else None)
    run("v", x1, y0, y1, (a, b) if plot.road == "+x" else None)

    # gate piers and a slatted gate in the opening
    piers = scene.mesh("Site — boundary wall", "boundary")
    gm = scene.mesh("Site — gate", "gate")
    pier = max(t * 1.6, 0.9)
    if plot.road in ("+y", "-y"):
        pos = y1 if plot.road == "+y" else y0
        for c in (a, b):
            _box(piers, c - pier / 2, pos - pier / 2, c + pier / 2, pos + pier / 2, z0, z1 + 0.6)
        for i in range(9):
            u = a + 0.35 + i * (gate - 0.7) / 8
            _box(gm, u - 0.08, pos - 0.08, u + 0.08, pos + 0.08, z0, z0 + h * 0.85)
        _box(gm, a + 0.2, pos - 0.09, b - 0.2, pos + 0.09, z0 + h * 0.8, z0 + h * 0.85)
    else:
        pos = x1 if plot.road == "+x" else x0
        for c in (a, b):
            _box(piers, pos - pier / 2, c - pier / 2, pos + pier / 2, c + pier / 2, z0, z1 + 0.6)
        for i in range(9):
            u = a + 0.35 + i * (gate - 0.7) / 8
            _box(gm, pos - 0.08, u - 0.08, pos + 0.08, u + 0.08, z0, z0 + h * 0.85)
        _box(gm, pos - 0.09, a + 0.2, pos + 0.09, b - 0.2, z0 + h * 0.8, z0 + h * 0.85)


def add_street(
    scene: Scene, plot: Plot, params: SiteParams, ground_z: float
) -> None:
    """Footpath, kerb and carriageway along the side the plan calls ROAD.

    A house presented on a lawn that stops in mid-air reads as a model. A house
    that meets a kerb reads as a house. The street runs the full width of the
    view rather than the width of the plot, so it carries past the edge of the
    frame instead of ending in a visible seam.
    """
    path = scene.mesh("Site — footpath", "drive")
    kerb = scene.mesh("Site — kerb", "trim")
    road = scene.mesh("Site — road", "road")

    # runs past the plot on both sides so the street leaves the frame rather
    # than ending in a visible seam — but not so far that it swamps the model's
    # bounds and pushes every camera back to fit a strip of tarmac in
    along = plot.width if plot.road in ("+y", "-y") else plot.depth
    over = along * 0.7
    fp, kb, rw = params.footpath_ft, params.kerb_ft, params.road_width_ft
    kerb_top = ground_z + 0.5

    if plot.road in ("+y", "-y"):
        sign = 1 if plot.road == "+y" else -1
        edge = plot.y1 if sign > 0 else plot.y0
        a, b = plot.x0 - over, plot.x1 + over
        _band_y(path, a, b, edge, edge + sign * fp, ground_z, kerb_top - 0.02)
        _band_y(kerb, a, b, edge + sign * fp, edge + sign * (fp + kb), ground_z, kerb_top)
        _band_y(road, a, b, edge + sign * (fp + kb), edge + sign * (fp + kb + rw),
                ground_z - 0.02, ground_z + 0.02)
    else:
        sign = 1 if plot.road == "+x" else -1
        edge = plot.x1 if sign > 0 else plot.x0
        a, b = plot.y0 - over, plot.y1 + over
        _band_x(path, a, b, edge, edge + sign * fp, ground_z, kerb_top - 0.02)
        _band_x(kerb, a, b, edge + sign * fp, edge + sign * (fp + kb), ground_z, kerb_top)
        _band_x(road, a, b, edge + sign * (fp + kb), edge + sign * (fp + kb + rw),
                ground_z - 0.02, ground_z + 0.02)


def _band_y(mesh, a: float, b: float, c0: float, c1: float, z0: float, z1: float) -> None:
    _box(mesh, a, min(c0, c1), b, max(c0, c1), z0, z1)


def _band_x(mesh, a: float, b: float, c0: float, c1: float, z0: float, z1: float) -> None:
    _box(mesh, min(c0, c1), a, max(c0, c1), b, z0, z1)


def add_ground(
    scene: Scene, plot: Plot, ex_bounds, params: SiteParams, ground_z: float
) -> None:
    """Lawn over the plot, paving from the gate to the building."""
    lawn = scene.mesh("Site — ground", "ground")
    margin = params.verge_ft
    _box(
        lawn,
        plot.x0 - margin,
        plot.y0 - margin,
        plot.x1 + margin,
        plot.y1 + margin,
        ground_z - 0.5,
        ground_z,
    )
    # A paved apron round the building. Without it the walls grow straight out
    # of the lawn, which is the single clearest sign of an untreated model —
    # real houses meet the ground on something.
    bx0, by0, bx1, by1 = ex_bounds
    apron = scene.mesh("Site — apron", "drive")
    _box(apron, bx0 - 3.0, by0 - 3.0, bx1 + 3.0, by1 + 3.0, ground_z, ground_z + 0.06)

    if params.road:
        add_street(scene, plot, params, ground_z)

    if not params.driveway:
        return
    drive = scene.mesh("Site — driveway", "drive")
    a = plot.drive_from - plot.drive_width / 2
    b = plot.drive_from + plot.drive_width / 2
    if plot.road == "+y":
        _box(drive, a, by1 - 1.0, b, plot.y1 + margin, ground_z, ground_z + 0.05)
    elif plot.road == "-y":
        _box(drive, a, plot.y0 - margin, b, by0 + 1.0, ground_z, ground_z + 0.05)
    elif plot.road == "+x":
        _box(drive, bx1 - 1.0, a, plot.x1 + margin, b, ground_z, ground_z + 0.05)
    else:
        _box(drive, plot.x0 - margin, a, bx0 + 1.0, b, ground_z, ground_z + 0.05)


def add_tree(scene: Scene, x: float, y: float, height_ft: float, seed: int) -> None:
    """Trunk and three overlapping canopies — enough at massing scale."""
    rng = random.Random(seed)
    trunk = scene.mesh("Site — trees", "bark")
    leaf = scene.mesh("Site — trees foliage", "foliage")
    h = height_ft * rng.uniform(0.85, 1.15)
    r_trunk = max(0.25, h * 0.035)
    trunk.add_cylinder(
        x * FT_TO_M,
        y * FT_TO_M,
        0.0,
        h * 0.55 * FT_TO_M,
        r_trunk * FT_TO_M,
        segments=8,
        r_top=r_trunk * 0.7 * FT_TO_M,
    )
    crown = h * 0.30
    for i in range(3):
        cx = x + rng.uniform(-0.35, 0.35) * crown
        cy = y + rng.uniform(-0.35, 0.35) * crown
        cz = h * (0.62 + 0.13 * i) + rng.uniform(-0.1, 0.1)
        leaf.add_sphere(
            cx * FT_TO_M,
            cz * FT_TO_M,
            cy * FT_TO_M,
            crown * rng.uniform(0.75, 1.0) * FT_TO_M,
            segments=10,
        )


def add_car(scene: Scene, cx: float, cy: float, along: str, seed: int) -> None:
    """A car, blocked out: body, cabin, glass and four wheels."""
    rng = random.Random(seed)
    body = scene.mesh("Site — cars", "car_body")
    glass = scene.mesh("Site — cars glass", "car_glass")
    tyre = scene.mesh("Site — cars wheels", "car_tyre")

    length, width = 14.8, 5.9
    if along == "y":
        lx, ly = width, length
    else:
        lx, ly = length, width
    x0, x1 = cx - lx / 2, cx + lx / 2
    y0, y1 = cy - ly / 2, cy + ly / 2

    _box(body, x0, y0, x1, y1, 1.15, 3.35)  # main body
    inset = 0.55
    if along == "y":
        _box(body, x0 + inset, y0 + ly * 0.22, x1 - inset, y1 - ly * 0.26, 3.35, 4.65)
        _box(glass, x0 + inset - 0.06, y0 + ly * 0.24, x1 - inset + 0.06,
             y1 - ly * 0.28, 3.5, 4.45)
    else:
        _box(body, x0 + lx * 0.22, y0 + inset, x1 - lx * 0.26, y1 - inset, 3.35, 4.65)
        _box(glass, x0 + lx * 0.24, y0 + inset - 0.06, x1 - lx * 0.28,
             y1 - inset + 0.06, 3.5, 4.45)

    # wheels: axles run across the car. add_cylinder takes the two coordinates
    # of the circle's centre first and the extent along its axis second, so for
    # an axle along model X the centre is (height, plan y).
    r = 1.15
    offs = 0.32
    for sx in (-1, 1):
        for sy in (-1, 1):
            if along == "y":
                wx = cx + sx * (lx / 2 - 0.1)
                wy = cy + sy * (ly * offs)
                tyre.add_cylinder(
                    r * FT_TO_M,  # centre height
                    wy * FT_TO_M,  # centre along plan y  → model Z
                    wx * FT_TO_M,  # axle from
                    (wx - sx * 0.5) * FT_TO_M,  # axle to
                    r * FT_TO_M,
                    segments=10,
                    axis="x",
                )
            else:
                wx = cx + sx * (lx * offs)
                wy = cy + sy * (ly / 2 - 0.1)
                tyre.add_cylinder(
                    wx * FT_TO_M,  # centre along plan x → model X
                    r * FT_TO_M,  # centre height
                    wy * FT_TO_M,  # axle from
                    (wy - sy * 0.5) * FT_TO_M,  # axle to
                    r * FT_TO_M,
                    segments=10,
                    axis="z",
                )
    del rng


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def populate(
    scene: Scene,
    ex: PlanExtract,
    params: SiteParams,
    ground_z: float,
    road_xy=None,
) -> dict:
    """Lay the site out around the building. Returns what it placed."""
    plot = plot_for(ex, params, road_xy)
    placed = {"road_side": plot.road, "trees": 0, "cars": 0}

    if params.ground:
        add_ground(scene, plot, ex.bounds, params, ground_z)
    if params.boundary_wall:
        add_boundary(scene, plot, params, ground_z)

    # cars: in the parking room if the plan named one, else on the drive
    park = parking_area(ex)
    if params.cars > 0:
        along = "y" if plot.road in ("+y", "-y") else "x"
        spots: list[tuple[float, float]] = []
        if park:
            pw, pd = park.x1 - park.x0, park.y1 - park.y0
            cx, cy = (park.x0 + park.x1) / 2, (park.y0 + park.y1) / 2
            lanes = max(1, int((pw if along == "y" else pd) // 8.0))
            for i in range(lanes):
                off = (i - (lanes - 1) / 2) * 8.0
                spots.append((cx + off, cy) if along == "y" else (cx, cy + off))
        drive_gap = 9.0
        for i in range(params.cars - len(spots)):
            step = (i + 1) * drive_gap
            if plot.road == "+y":
                spots.append((plot.drive_from, ex.bounds[3] + step))
            elif plot.road == "-y":
                spots.append((plot.drive_from, ex.bounds[1] - step))
            elif plot.road == "+x":
                spots.append((ex.bounds[2] + step, plot.drive_from))
            else:
                spots.append((ex.bounds[0] - step, plot.drive_from))
        for i, (cx, cy) in enumerate(spots[: params.cars]):
            add_car(scene, cx, cy, along, seed=i)
            placed["cars"] += 1

    if params.trees > 0:
        for i, (tx, ty) in enumerate(
            _tree_spots(plot, ex.bounds, params, params.trees)
        ):
            add_tree(scene, tx, ty, params.tree_height_ft, seed=100 + i)
            placed["trees"] += 1
    return placed


def _tree_spots(plot: Plot, bounds, params: SiteParams, count: int):
    """Space trees around the setback strips, clear of the drive and the walls."""
    bx0, by0, bx1, by1 = bounds
    gate_a = plot.drive_from - plot.drive_width / 2 - 3
    gate_b = plot.drive_from + plot.drive_width / 2 + 3
    inset = max(1.5, params.boundary_thickness_ft * 2)
    candidates: list[tuple[float, float]] = []

    def strip(axis: str, pos: float, lo: float, hi: float, n: int):
        for i in range(n):
            t = lo + (hi - lo) * (i + 0.5) / n
            candidates.append((t, pos) if axis == "h" else (pos, t))

    # Along each side, in the gap between the wall and the building, and only
    # where that gap is wide enough for a tree not to grow through the facade.
    # Trees sit nearer the boundary than the building, the way they do on a plot.
    need = max(6.0, params.tree_height_ft * 0.35)

    def lerp(a, b, t):
        return a + (b - a) * t

    if by0 - plot.y0 > need:
        strip("h", lerp(plot.y0, by0, 0.38), plot.x0 + inset, plot.x1 - inset, 3)
    if plot.y1 - by1 > need:
        strip("h", lerp(plot.y1, by1, 0.38), plot.x0 + inset, plot.x1 - inset, 3)
    if bx0 - plot.x0 > need:
        strip("v", lerp(plot.x0, bx0, 0.38), plot.y0 + inset, plot.y1 - inset, 3)
    if plot.x1 - bx1 > need:
        strip("v", lerp(plot.x1, bx1, 0.38), plot.y0 + inset, plot.y1 - inset, 3)

    out = []
    for x, y in candidates:
        if plot.road in ("+y", "-y") and gate_a <= x <= gate_b:
            continue
        if plot.road in ("+x", "-x") and gate_a <= y <= gate_b:
            continue
        clear = params.tree_height_ft * 0.28
        if bx0 - clear < x < bx1 + clear and by0 - clear < y < by1 + clear:
            continue  # too close to the building for the canopy to clear it
        out.append((x, y))
        if len(out) >= count:
            break
    # if the setbacks are too tight, fall back to the corners of the plot
    if not out:
        d = 2.5
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            out.append(
                (
                    plot.x1 - d if sx > 0 else plot.x0 + d,
                    plot.y1 - d if sy > 0 else plot.y0 + d,
                )
            )
        out = out[:count]
    return out


def road_label_xy(ex: PlanExtract, words) -> tuple[float, float] | None:
    """Where the sheet writes ROAD, in plan feet — the road is that way."""
    ox, oy = ex.origin_px
    px = ex.scale.px_per_ft
    for w in words:
        if w.text.strip().upper() == "ROAD":
            return ((w.cx - ox) / px, (w.cy - oy) / px)
    return None


def _unused(*_a):  # pragma: no cover - keeps math import honest
    return math
