"""Plan geometry + vertical parameters → a 3D building.

A floor plan contains no vertical information whatsoever. Every height in the
output — plinth, floor-to-floor, sill, lintel, slab, parapet — comes from the
parameters the user sets, never from the drawing and never from a guess dressed
up as one. What the plans decide is the *plan*: where walls are, how thick, and
where the holes go.

Model space is metres, Y up, which is what glTF, Blender and Twinmotion all
expect. Feet survive right up to the last multiplication.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import facade as facade_mod
from . import site as site_mod
from . import sky as sky_mod
from .finish import materials_for
from .footprint import footprint_rects, ring_rects
from .mesh import Scene
from .models import BuildParams, LevelParams, PlanExtract, Wall
from .units import FT_TO_M


@dataclass
class BuildResult:
    scene: Scene
    summary: dict


# --------------------------------------------------------------------------- #
# walls with openings
# --------------------------------------------------------------------------- #
def _opening_band(op, lp: LevelParams) -> tuple[float, float]:
    """Sill and head for an opening, in feet above this storey's floor."""
    if op.kind == "door":
        sill = 0.0 if op.sill_ft is None else op.sill_ft
        head = lp.door_head_ft if op.head_ft is None else op.head_ft
    else:
        sill = lp.window_sill_ft if op.sill_ft is None else op.sill_ft
        head = lp.window_head_ft if op.head_ft is None else op.head_ft
    return sill, head


def add_wall(
    scene: Scene,
    wall: Wall,
    base_ft: float,
    height_ft: float,
    lp: LevelParams,
    group: str,
    material: str,
    params: BuildParams,
    glazing_group: str,
    door_group: str,
) -> None:
    """One wall, split into piers, sills and lintels around its openings."""
    m = scene.mesh(group, material)
    u0, u1 = wall.u_range()
    top = base_ft + height_ft

    ops = sorted(
        (o for o in wall.openings if o.u1 > u0 and o.u0 < u1),
        key=lambda o: min(o.u0, o.u1),
    )
    spans = []  # (a, b, sill, head, opening)
    for o in ops:
        a, b = sorted((o.u0, o.u1))
        a, b = max(a, u0), min(b, u1)
        if b - a < 0.05:
            continue
        sill, head = _opening_band(o, lp)
        head = min(head, height_ft)
        if head <= sill:
            continue
        spans.append((a, b, sill, head, o))

    # piers between openings
    cursor = u0
    for a, b, _s, _h, _o in spans:
        if a > cursor:
            _box_along(m, wall, cursor, a, base_ft, top)
        cursor = max(cursor, b)
    if cursor < u1:
        _box_along(m, wall, cursor, u1, base_ft, top)

    # sill and lintel over each opening, then the pane or leaf inside it
    for a, b, sill, head, _o in spans:
        if sill > 0.001:
            _box_along(m, wall, a, b, base_ft, base_ft + sill)
        if head < height_ft - 0.001:
            _box_along(m, wall, a, b, base_ft + head, top)

    d = params.detail
    outer, normal = _outer_face(wall)
    relief = d.enabled and wall.exterior and wall.outside in ("lo", "hi")

    for a, b, sill, head, o in spans:
        # The pane sits back in the reveal rather than flush, so the opening
        # reads as a hole with depth. This is the whole reason Wall.outside is
        # kept: flush glazing on a flat wall is what makes a model look printed
        # on rather than built.
        set_back = d.reveal_ft if relief else 0.0
        z0, z1 = base_ft + sill, base_ft + head
        if o.kind == "window" and params.glazing:
            pane = scene.mesh(glazing_group, "glazing")
            _inset(pane, wall, a, b, z0, z1, outer, normal, set_back, 0.06)
            if d.enabled and d.curtains:
                _curtains(scene, wall, a, b, z0, z1, outer, normal, d, group)
        elif o.kind == "door" and params.doors:
            leaf = scene.mesh(door_group, "door")
            _inset(leaf, wall, a, b, z0, z1, outer, normal, set_back, 0.12)

        if not relief:
            continue
        if d.frames:
            _frame(scene, wall, a, b, z0, z1, outer, normal, d, group)
        if d.sills and o.kind == "window":
            _sill(scene, wall, a, b, z0, outer, normal, d, group)
        if d.chajjas:
            _chajja(scene, wall, a, b, z1, outer, normal, d, params, group)


# --------------------------------------------------------------------------- #
# the small stuff that makes a wall read as built
# --------------------------------------------------------------------------- #
def _outer_face(wall: Wall) -> tuple[float, float]:
    """The coordinate of the face that looks outside, and which way it faces."""
    lo, hi = (wall.y0, wall.y1) if wall.axis == "h" else (wall.x0, wall.x1)
    if wall.outside == "lo":
        return lo, -1.0
    return hi, 1.0


def _box_across(mesh, wall: Wall, a: float, b: float, z0: float, z1: float,
                c0: float, c1: float) -> None:
    """A box ``a..b`` along the wall and ``c0..c1`` across its thickness."""
    lo, hi = min(c0, c1), max(c0, c1)
    if wall.axis == "h":
        mesh.add_box(a * FT_TO_M, z0 * FT_TO_M, lo * FT_TO_M,
                     b * FT_TO_M, z1 * FT_TO_M, hi * FT_TO_M)
    else:
        mesh.add_box(lo * FT_TO_M, z0 * FT_TO_M, a * FT_TO_M,
                     hi * FT_TO_M, z1 * FT_TO_M, b * FT_TO_M)


def _inset(mesh, wall: Wall, a: float, b: float, z0: float, z1: float,
           outer: float, normal: float, set_back: float, thick: float) -> None:
    face = outer - normal * set_back
    _box_across(mesh, wall, a, b, z0, z1, face, face - normal * thick)


#: how many folds a curtain is gathered into, per foot of window
CURTAIN_FOLDS_PER_FT = 1.15
#: how deep a fold is, as a fraction of the fold's own width
CURTAIN_FOLD_DEPTH = 0.62
#: where the tie sits, as a fraction of the opening height
CURTAIN_TIE_AT = 0.46


def _curtains(scene: Scene, wall: Wall, a: float, b: float, z0: float, z1: float,
              outer: float, normal: float, d, group: str) -> None:
    """A curtain across the window, gathered to a tie at the centre.

    It closes the opening: an empty window is a hole into an unmodelled
    interior and the eye goes straight to it, so the fabric covers the whole
    aperture. Tied back at the *sides* it looks more like a curtain and does
    not do that job, because the middle — the part you see through — is still
    a hole.

    So the tie is in the middle instead. The fabric hangs full width at the
    rail, is drawn in to a band at mid height, and falls away again below:
    the folds deepen as they approach the tie, which is what reads as gathered
    rather than as a blue card with a stripe across it.
    """
    span, height = b - a, z1 - z0
    if span < 0.9 or height < 1.2:
        return
    m = scene.mesh(f"{group} curtains", "curtain")
    # behind the pane and behind the reveal, hanging in the room
    front = outer - normal * (d.reveal_ft + 0.22)
    depth = min(0.34, span * 0.14)

    folds = max(4, int(round(span * CURTAIN_FOLDS_PER_FT)))
    step = span / folds
    zag = min(depth * CURTAIN_FOLD_DEPTH, step * 0.5)
    tie = z0 + height * CURTAIN_TIE_AT
    courses = 8

    def p(u: float, z: float, c: float):
        """A point, from the wall's own along/height/across axes into model xyz."""
        return (u, z, c) if wall.axis == "h" else (c, z, u)

    def gather(z: float) -> float:
        """How gathered the fabric is at this height: 1 at the tie, 0 at the ends."""
        t = abs(z - tie) / max(1e-6, max(tie - z0, z1 - tie))
        return 1.0 - min(1.0, t) ** 1.6

    across = (0.0, 0.0, 1.0) if wall.axis == "h" else (1.0, 0.0, 0.0)
    zs = [z0 + height * i / courses for i in range(courses + 1)]

    for j in range(courses):
        za, zb = zs[j], zs[j + 1]
        ga, gb = gather(za), gather(zb)
        for i in range(folds):
            ua, ub = a + i * step, a + (i + 1) * step
            # each pleat swings back and forth, and swings further where the
            # fabric is drawn in to the tie
            ca = front - normal * zag * (1.0 if i % 2 else 0.0) * (0.35 + 0.65 * ga)
            cb = front - normal * zag * (0.0 if i % 2 else 1.0) * (0.35 + 0.65 * gb)
            for flip in (1.0, -1.0):
                n = tuple(v * flip * (1.0 if normal > 0 else -1.0) for v in across)
                quad = (p(ua, za, ca), p(ub, za, cb), p(ub, zb, cb), p(ua, zb, ca))
                m.add_quad(*_m(quad if flip > 0 else quad[::-1]), n)

    # the tie itself, and a rail at the head for the curtain to hang from
    band = max(0.16, height * 0.05)
    _box_across(m, wall, a + span * 0.30, b - span * 0.30, tie - band / 2,
                tie + band / 2, front + normal * 0.03,
                front - normal * (zag + 0.05))
    _box_across(m, wall, a, b, z1 - 0.12, z1,
                front + normal * 0.02, front - normal * (zag + 0.06))


def _m(points):
    """Feet to metres, in the model's (x, height, z) order."""
    return [(x * FT_TO_M, y * FT_TO_M, z * FT_TO_M) for x, y, z in points]


def _frame(scene: Scene, wall: Wall, a: float, b: float, z0: float, z1: float,
           outer: float, normal: float, d, group: str) -> None:
    """A frame round the opening, standing just proud of the reveal."""
    m = scene.mesh(f"{group} frames", "frame")
    w = min(d.frame_ft, (b - a) / 2.2, (z1 - z0) / 2.2)
    if w <= 0.02:
        return
    c0 = outer + normal * 0.02
    c1 = outer - normal * max(0.0, d.reveal_ft - 0.02)
    _box_across(m, wall, a, a + w, z0, z1, c0, c1)          # jamb
    _box_across(m, wall, b - w, b, z0, z1, c0, c1)          # jamb
    _box_across(m, wall, a, b, z1 - w, z1, c0, c1)          # head
    _box_across(m, wall, a, b, z0, z0 + w, c0, c1)          # cill piece


def _sill(scene: Scene, wall: Wall, a: float, b: float, z0: float,
          outer: float, normal: float, d, group: str) -> None:
    """A projecting sill, run past the opening at both ends as a real one is."""
    m = scene.mesh(f"{group} sills", "trim")
    over = 0.3
    _box_across(m, wall, a - over, b + over, z0 - 0.22, z0,
                outer + normal * d.sill_projection_ft, outer - normal * 0.15)


def _chajja(scene: Scene, wall: Wall, a: float, b: float, z1: float,
            outer: float, normal: float, d, params: BuildParams, group: str) -> None:
    """The sunshade over an opening.

    Its depth is the one number here the drawings do state: the tie-beam sheet
    writes ``slab proj. 1'``. Where a set does not say, it falls back to a foot.
    """
    depth = d.chajja_ft or params.slab_projection_ft or 1.0
    m = scene.mesh(f"{group} chajjas", "trim")
    over = 0.75
    _box_across(m, wall, a - over, b + over, z1, z1 + 0.3,
                outer + normal * depth, outer - normal * 0.2)


def _box_along(
    mesh,
    wall: Wall,
    a: float,
    b: float,
    z0: float,
    z1: float,
    thickness_ft: float | None = None,
) -> None:
    """A box spanning ``a..b`` along the wall, full thickness unless told otherwise."""
    if wall.axis == "h":
        y0, y1 = wall.y0, wall.y1
        if thickness_ft is not None:
            c = (y0 + y1) / 2
            y0, y1 = c - thickness_ft / 2, c + thickness_ft / 2
        mesh.add_box(
            a * FT_TO_M, z0 * FT_TO_M, y0 * FT_TO_M, b * FT_TO_M, z1 * FT_TO_M, y1 * FT_TO_M
        )
    else:
        x0, x1 = wall.x0, wall.x1
        if thickness_ft is not None:
            c = (x0 + x1) / 2
            x0, x1 = c - thickness_ft / 2, c + thickness_ft / 2
        mesh.add_box(
            x0 * FT_TO_M, z0 * FT_TO_M, a * FT_TO_M, x1 * FT_TO_M, z1 * FT_TO_M, b * FT_TO_M
        )


#: room names that are shafts, not rooms — there is no floor over them
SHAFT_ROOMS = {"DUCT", "SHAFT", "VOID"}


def _slab_holes(ex) -> list[tuple[float, float, float, float]]:
    """Where this storey deliberately has no floor.

    A stairwell and a duct are inside the building but have no slab across
    them. Both are measured: the well from the flights that wrap it, the shaft
    from a room the drawing named.
    """
    holes = [st.well for st in ex.stairs if st.well]
    for r in ex.rooms:
        if set(r.name.upper().split()) & SHAFT_ROOMS:
            holes.append((r.x0, r.y0, r.x1, r.y1))
    return holes


def _add_stair(scene: Scene, stair, base: float, f2f: float, group: str) -> None:
    """Build a stair as solid steps rising from this storey's floor.

    The going and the width are measured off the drawing; the **riser is not
    and cannot be** — a plan states no heights. It is derived by dividing the
    storey height by the number of risers, which is one more than the number of
    treads because the last riser lands you on the floor above. That also means
    the top tread finishes a riser short of the slab, which is exactly where a
    real one stops.
    """
    treads = sum(f.treads for f in stair.flights)
    if not treads:
        return
    riser = f2f / (treads + 1)
    mesh = scene.mesh(group, "stair")
    step = 0
    for flight in stair.flights:
        for i in range(flight.treads):
            step += 1
            top = base + step * riser
            if flight.up in ("+x", "-x"):
                a = flight.x0 + i * flight.going_ft
                b = a + flight.going_ft
                if flight.up == "-x":
                    b = flight.x1 - i * flight.going_ft
                    a = b - flight.going_ft
                box = (a, flight.y0, b, flight.y1)
            else:
                a = flight.y0 + i * flight.going_ft
                b = a + flight.going_ft
                if flight.up == "-y":
                    b = flight.y1 - i * flight.going_ft
                    a = b - flight.going_ft
                box = (flight.x0, a, flight.x1, b)
            mesh.add_box(
                box[0] * FT_TO_M, base * FT_TO_M, box[1] * FT_TO_M,
                box[2] * FT_TO_M, top * FT_TO_M, box[3] * FT_TO_M,
            )


def _slab(scene: Scene, group: str, rects, z0: float, z1: float, material="slab") -> None:
    m = scene.mesh(group, material)
    for x0, y0, x1, y1 in rects:
        m.add_box(
            x0 * FT_TO_M, z0 * FT_TO_M, y0 * FT_TO_M, x1 * FT_TO_M, z1 * FT_TO_M, y1 * FT_TO_M
        )


def _add_balustrade(
    scene: Scene, wall: Wall, base_ft: float, height_ft: float, group: str
) -> None:
    """A balcony guard as glass between posts under a rail.

    Built as a solid slab it reads as a low wall, which is what the balconies
    looked like — heavy, and nothing like the light guards these elevations
    actually have. Kerb, glass, posts and a capping rail cost a few boxes and
    change the whole character of a facade.
    """
    m = scene.mesh(group, "railing")
    glass = scene.mesh(f"{group} glass", "glazing")
    a, b = wall.u_range()
    span = b - a
    if span <= 0.2:
        return
    top = base_ft + height_ft
    thick = max(0.08, min(0.16, wall.thickness_ft))
    mid = (
        (wall.y0 + wall.y1) / 2 if wall.axis == "h" else (wall.x0 + wall.x1) / 2
    )
    c0, c1 = mid - thick / 2, mid + thick / 2

    _box_across(m, wall, a, b, base_ft, base_ft + 0.25, c0, c1)      # kerb
    _box_across(m, wall, a, b, top - 0.18, top, c0 - 0.05, c1 + 0.05)  # capping rail
    # glass sits between them, thinner than the rail so the rail reads as a cap
    _box_across(
        glass, wall, a + 0.08, b - 0.08, base_ft + 0.25, top - 0.18,
        mid - thick / 4, mid + thick / 4,
    )
    # posts about every four feet, and always at both ends
    n = max(1, int(round(span / 4.0)))
    for i in range(n + 1):
        u = a + span * i / n
        _box_across(
            m, wall, min(max(u - 0.09, a), b - 0.18), min(max(u + 0.09, a + 0.18), b),
            base_ft, top, c0 - 0.03, c1 + 0.03,
        )


# --------------------------------------------------------------------------- #
# the building
# --------------------------------------------------------------------------- #
def level_elevations(
    extracts: list[PlanExtract], params: BuildParams
) -> dict[int, tuple[float, float, float]]:
    """level → (floor elevation ft, floor-to-floor ft, wall height ft)."""
    levels = sorted({e.level for e in extracts})
    out: dict[int, tuple[float, float, float]] = {}
    z = params.plinth_ft
    # levels at or above ground stack upwards from the plinth
    for lv in [x for x in levels if x >= 0]:
        lp = params.level(lv) or LevelParams(level=lv, name=f"Level {lv}")
        h = lp.wall_height_ft or (lp.floor_to_floor_ft - lp.slab_thickness_ft)
        out[lv] = (z, lp.floor_to_floor_ft, h)
        z += lp.floor_to_floor_ft
    # anything below ground hangs off the ground floor
    z = params.plinth_ft
    for lv in sorted((x for x in levels if x < 0), reverse=True):
        lp = params.level(lv) or LevelParams(level=lv, name=f"Level {lv}")
        z -= lp.floor_to_floor_ft
        h = lp.wall_height_ft or (lp.floor_to_floor_ft - lp.slab_thickness_ft)
        out[lv] = (z, lp.floor_to_floor_ft, h)
    return out


def build(
    extracts: list[PlanExtract],
    params: BuildParams,
    road_xy: tuple[float, float] | None = None,
) -> BuildResult:
    """Assemble every included storey, and its site, into one scene.

    ``road_xy`` is where the sheet writes ROAD, in plan feet — it decides which
    way the gate and driveway face.
    """
    extracts = [
        e
        for e in extracts
        if (params.level(e.level) is None or params.level(e.level).include)
    ]
    extracts = sorted(extracts, key=lambda e: e.level)
    scene = Scene(materials=materials_for(params.finish.resolved()))
    if not extracts:
        return BuildResult(scene, {"levels": [], "note": "no storeys included"})

    elevations = level_elevations(extracts, params)
    summary_levels = []
    top_level = max(e.level for e in extracts)
    top_footprint: list[tuple[float, float, float, float]] = []
    top_walls: list[Wall] = []

    for ex in extracts:
        lp = params.level(ex.level) or LevelParams(level=ex.level, name=ex.level_name)
        base, f2f, wh = elevations[ex.level]
        name = ex.level_name

        holes = _slab_holes(ex)
        rects = footprint_rects(ex.walls, columns=ex.columns, holes=holes)
        _slab(scene, f"{name} slab", rects, base - lp.slab_thickness_ft, base)

        # The floor band: the slab edge expressed on the outside, which is what
        # gives an elevation its horizontal lines and puts a shadow under every
        # storey. Grown outwards from the footprint boundary, so it follows a
        # balcony and a bay the same way the parapet already does.
        d = params.detail
        if d.enabled and d.floor_bands and ex.level > min(e.level for e in extracts):
            _slab(
                scene,
                f"{name} floor band",
                ring_rects(ex.walls, d.band_projection_ft, rects=rects, outward=True),
                base - lp.slab_thickness_ft - 0.18,
                base + 0.22,
                material="trim",
            )

        for st in ex.stairs:
            _add_stair(scene, st, base, f2f, f"{name} stairs")

        for w in ex.walls:
            if w.is_railing and params.detail.enabled and params.detail.balustrades:
                _add_balustrade(
                    scene, w, base, w.height_ft or params.railing_ft,
                    f"{name} railings",
                )
                continue
            if w.is_railing:
                # a balcony guard: same plan, waist height, its own material
                add_wall(
                    scene,
                    w,
                    base,
                    w.height_ft or params.railing_ft,
                    lp,
                    group=f"{name} railings",
                    material="railing",
                    params=params,
                    glazing_group=f"{name} glazing",
                    door_group=f"{name} doors",
                )
                continue
            add_wall(
                scene,
                w,
                base,
                w.height_ft or wh,
                lp,
                group=f"{name} external walls" if w.exterior else f"{name} walls",
                material="wall_ext" if w.exterior else "wall_int",
                params=params,
                glazing_group=f"{name} glazing",
                door_group=f"{name} doors",
            )

        if params.columns and ex.columns:
            cm = scene.mesh(f"{name} columns", "column")
            inset = 0.004  # keeps column faces off the wall faces
            for c in ex.columns:
                cm.add_box(
                    (c.x0 + inset) * FT_TO_M,
                    (base - lp.slab_thickness_ft) * FT_TO_M,
                    (c.y0 + inset) * FT_TO_M,
                    (c.x1 - inset) * FT_TO_M,
                    (base + wh) * FT_TO_M,
                    (c.y1 - inset) * FT_TO_M,
                )

        summary_levels.append(
            {
                "level": ex.level,
                "name": name,
                "floor_elevation_ft": round(base, 3),
                "wall_height_ft": round(wh, 3),
                "walls": sum(1 for w in ex.walls if not w.is_railing),
                "railings": sum(1 for w in ex.walls if w.is_railing),
                "doors": sum(1 for w in ex.walls for o in w.openings if o.kind == "door"),
                "windows": sum(
                    1 for w in ex.walls for o in w.openings if o.kind == "window"
                ),
                "columns": len(ex.columns),
                "area_sqft": round(sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects), 1),
            }
        )
        if ex.level == top_level:
            # The roof keeps its voids filled. A stairwell is a hole in a
            # *floor* — the drawing says so — but a hole in the roof would be a
            # weather opening the drawing never states, and a stair reaching a
            # roof is covered by a mumty this model does not build. Solid is the
            # conservative reading; the parapet then closes round the outside
            # only, instead of ringing an imaginary shaft in the roof.
            top_footprint = footprint_rects(ex.walls, columns=ex.columns)
            top_walls = ex.walls

    # roof
    lp_top = params.level(top_level) or LevelParams(level=top_level, name="top")
    base_top, f2f_top, _wh = elevations[top_level]
    roof_top = base_top + f2f_top
    roof_slab_t = params.roof_slab_thickness_ft or lp_top.slab_thickness_ft
    if params.roof != "none" and top_footprint:
        _slab(
            scene,
            "Roof slab",
            top_footprint,
            roof_top - roof_slab_t,
            roof_top,
            material="roof",
        )
    if params.roof == "flat_parapet" and params.parapet_ft > 0 and top_footprint:
        # the parapet follows the roof outline, not the walls, so the ring always
        # closes — including along a balcony edge where there is no wall below
        _slab(
            scene,
            "Parapet",
            ring_rects(
                top_walls, params.parapet_thickness_ft, rects=top_footprint
            ),
            roof_top,
            roof_top + params.parapet_ft,
            material="trim",
        )
        # A coping course caps the parapet and oversails it slightly, which is
        # what stops a roofline reading as a cut edge against the sky.
        if params.detail.enabled and params.detail.coping:
            _slab(
                scene,
                "Parapet coping",
                ring_rects(
                    top_walls,
                    params.parapet_thickness_ft + 0.3,
                    rects=top_footprint,
                ),
                roof_top + params.parapet_ft,
                roof_top + params.parapet_ft + 0.28,
                material="trim",
            )

    # the plinth: the band of wall between the ground and the ground floor, given
    # its own material so a stone or tiled base reads the way it does on site
    ground_ex = min(extracts, key=lambda e: e.level)
    base_ground, _f2f, _wh = elevations[ground_ex.level]
    if params.plinth_ft > 0.02:
        _slab(
            scene,
            "Plinth",
            footprint_rects(ground_ex.walls, columns=ground_ex.columns),
            0.0,
            base_ground - (params.level(ground_ex.level) or LevelParams(
                level=ground_ex.level, name="g")).slab_thickness_ft,
            material="base",
        )

    # site
    bounds_ft = _plan_bounds(extracts)
    # the façade, on the street-facing wall. Composed from the building's own
    # proportions unless the user has edited the panels, in which case theirs win.
    facade_summary: dict = {}
    if params.facade.enabled and bounds_ft:
        side = site_mod.road_side(ground_ex, road_xy)
        frame = facade_mod.front_frame(extracts, elevations, side, params.plinth_ft)
        if frame is not None:
            panels = params.facade.panels or facade_mod.compose(
                extracts, elevations, frame, params
            )
            built = facade_mod.build(scene, panels, frame)
            facade_summary = {
                "side": side,
                "face_ft": round(frame.face, 3),
                "width_ft": round(frame.width, 2),
                "height_ft": round(frame.height, 2),
                "panels": len(panels),
                "built": built,
            }

    site_summary: dict = {}
    if params.site.enabled and bounds_ft:
        site_summary = site_mod.populate(
            scene, ground_ex, params.site, ground_z=0.0, road_xy=road_xy
        )
    elif params.ground and bounds_ft:
        g = params.ground_margin_ft
        gm = scene.mesh("Site — ground", "ground")
        gm.add_box(
            (bounds_ft[0] - g) * FT_TO_M,
            -0.5 * FT_TO_M,
            (bounds_ft[1] - g) * FT_TO_M,
            (bounds_ft[2] + g) * FT_TO_M,
            0.0,
            (bounds_ft[3] + g) * FT_TO_M,
        )

    scene.prune()

    north = next((e.north_deg for e in extracts if e.north_deg is not None), None)
    rotation = 0.0
    if params.align_north and north is not None:
        rotation = _snap90(90.0 - north)
        rotate_scene(scene, rotation)
    centre_scene(scene)

    # Which way the front looks once the model has been turned. Plan +y becomes
    # model +Z, and the scene may then be rotated to put north on an axis — so
    # anything that wants to stand in front of the house, a camera above all,
    # has to be told rather than assume the street is at -Z.
    if facade_summary:
        plan_normal = {"+y": (0.0, 1.0), "-y": (0.0, -1.0),
                       "+x": (1.0, 0.0), "-x": (-1.0, 0.0)}[facade_summary["side"]]
        a = math.radians(rotation)
        nx, nz = plan_normal[0], plan_normal[1]
        facade_summary["normal_xz"] = [
            round(nx * math.cos(a) + nz * math.sin(a), 4),
            round(-nx * math.sin(a) + nz * math.cos(a), 4),
        ]

    b = scene.bounds()
    summary = {
        "levels": summary_levels,
        "roof_top_ft": round(roof_top, 3),
        "overall_height_ft": round(
            roof_top + (params.parapet_ft if params.roof == "flat_parapet" else 0.0), 2
        ),
        "north_deg": north,
        # the viewer lights itself from this, so the browser and the Blender
        # scene are showing the model in the same light
        "sky": sky_mod.get(params.sky).as_dict(),
        "facade": facade_summary,
        "rotation_applied_deg": round(rotation, 2),
        "triangles": scene.triangle_count,
        "vertices": scene.vertex_count,
        "size_m": None
        if not b
        else [round(b[3] - b[0], 3), round(b[4] - b[1], 3), round(b[5] - b[2], 3)],
        "groups": [
            {"name": m.name, "material": m.material, "triangles": m.triangle_count}
            for m in scene.meshes
        ],
        "finish": params.finish.preset,
        "textures": scene.textures,
        "site": site_summary,
    }
    return BuildResult(scene, summary)


def _plan_bounds(extracts: list[PlanExtract]):
    xs = [v for e in extracts for w in e.walls for v in (w.x0, w.x1)]
    ys = [v for e in extracts for w in e.walls for v in (w.y0, w.y1)]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _snap90(deg: float, tol: float = 3.0) -> float:
    """Snap to a right angle when the compass is within a few degrees of one.

    A 0.4° rotation buys nothing and costs axis alignment for every downstream
    tool, so it is not applied.
    """
    nearest = round(deg / 90.0) * 90.0
    return nearest if abs(deg - nearest) <= tol else deg


def rotate_scene(scene: Scene, deg: float) -> None:
    """Rotate about Y so that geographic north points along −Z."""
    if abs(deg) < 1e-9:
        return
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    for m in scene.meshes:
        for arr in (m.positions, m.normals):
            for i in range(0, len(arr), 3):
                x, z = arr[i], arr[i + 2]
                arr[i] = x * c + z * s
                arr[i + 2] = -x * s + z * c


def centre_scene(scene: Scene) -> None:
    """Put the building's centre on the origin, ground at y = 0."""
    b = scene.bounds()
    if not b:
        return
    dx = -(b[0] + b[3]) / 2
    dz = -(b[2] + b[5]) / 2
    for m in scene.meshes:
        for i in range(0, len(m.positions), 3):
            m.positions[i] += dx
            m.positions[i + 2] += dz
