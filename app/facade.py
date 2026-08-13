"""Fasād — the front of the house, composed as panels with a projection depth.

The supplied elevation document is the specification for this module. Read it
carefully and it is not a picture: every coloured area on that sheet is a
**panel** with three properties — a rectangle, a material, and a ``lvl +X'Y"``
tag saying how far it stands proud of the wall behind. ``lvl +0'0"`` is the
wall face; ``lvl +3'2"`` is the deep canopy over the top. That is a facade
expressed as relief, which is exactly what makes an elevation read as designed
rather than as an extrusion with holes in it.

So this module models the same thing:

* a :class:`Panel` is a rectangle on the front face, a material and a depth;
* :func:`compose` lays out a default set of them **from the building** — its
  real width, its storey heights, where its openings actually are — so nothing
  is a fixed size and the composition fits whatever plan it is given;
* :func:`build` turns each panel into a box projecting from the wall.

The vocabulary comes from the document's own renders: vertical teak fins, warm
white box frames standing proud around the windows, wood cladding to one bay,
dark grey recesses, horizontal bands at the floor lines, and a deep dark canopy
across the top.

None of this is measured from a floor plan and none of it pretends to be. It is
a design, offered as a starting point and editable in every dimension — which
is why every panel carries its numbers and why they are shown on the elevation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .mesh import Scene
from .models import FacadeParams, Panel, PlanExtract
from .units import FT_TO_M

#: panel kinds, back to front — the order they are drawn in
KIND_ORDER = [
    "field", "recess", "clad", "band", "mass", "slab", "frame", "post", "fin", "canopy",
    "pier",
]

#: Kinds that are fixed to a wall, and only exist where the building has one.
#: Everything else — the canopy and the screen — is a structure standing off
#: the building, and spans whether there is wall behind it or not.
WALL_MOUNTED = {"recess", "clad", "band", "slab", "mass", "frame", "pier", "post"}

#: Kinds that step with the wall in **height** as well as in width. A full
#: height clad panel on a building whose upper storey comes forward has to step
#: forward with it. A floor band does not: it is the edge of the slab that
#: separates the two, and it sits on the frontmost of them, which is what makes
#: it read as a single line across the building rather than as two.
Z_STEPPED = {"field", "clad", "recess", "mass"}

#: which finish slot each kind wears, following the document's palette roles
KIND_MATERIAL = {
    "field": "wall_ext",    # light grey — main wall finish
    "recess": "accent",     # dark grey — the shadow behind everything
    "clad": "clad",         # teak — vertical cladding
    "mass": "clad",         # teak — the projecting bay
    "fin": "clad",          # teak — the screen blades
    "band": "trim",         # warm white — horizontal bands
    "slab": "trim",         # warm white — balcony soffit and top
    "frame": "trim",        # warm white — box frames
    "post": "trim",         # warm white — slim supports, read against the dark
    "canopy": "accent",     # dark grey — the roof plane
    "pier": "accent",
}

#: Default projection in feet — the ``lvl`` the document writes on each area.
#: These are a *ladder*, not a set of independent numbers: the screen stands in
#: front of the canopy, the canopy in front of the balcony slabs, those in front
#: of the clad mass, and the mass in front of the wall. Flatten the ladder and
#: the elevation stops reading as layers however good the colours are.
DEPTH = {
    "field": 0.0,
    "recess": -0.55,   # set back into shadow
    "clad": 0.30,
    "band": 0.40,
    "mass": 1.30,
    "slab": 1.55,
    "frame": 0.55,     # measured from whatever it sits on, added below
    "post": 0.45,      # standing at the face of the overhang it carries
    "fin": 2.00,
    "canopy": 3.15,    # the document writes lvl +3'2" on the top slab
    "pier": 0.75,
}

#: Kinds that are a solid area of finish. A window behind one of these has to
#: be cut out of it; a fin or a post standing over one is a screen, which is
#: the point of a screen.
SOLID = {"clad", "mass", "recess", "band", "slab", "pier"}

#: The arrangements on offer. Each is the same vocabulary in a different order,
#: because a facade is a composition and one composition does not suit every
#: plan — or every client.
ARRANGEMENTS = {
    "layered": "Screen, recessed balcony and a projecting clad bay",
    "framed": "A clad bay each side of a recessed centre",
    "quiet": "Bands and box frames only — no screen, no projecting bay",
}


# --------------------------------------------------------------------------- #
# where the front is, and how to get onto it
# --------------------------------------------------------------------------- #
@dataclass
class Wallface:
    """One stretch of front wall: where it is along the face, and how far out."""

    u0: float
    u1: float
    z0: float
    z1: float
    face: float


@dataclass
class Frame:
    """The plane the facade is composed on.

    ``u`` runs along the face from its left-hand end as seen from the street,
    ``z`` is height above ground, and ``depth`` is measured outwards. Keeping
    that mapping in one place is what lets the composition be written in plain
    left-to-right terms whichever side of the plan the road happens to be on.

    A front is rarely one plane. On the example set the ground floor stands at
    y=39.89 while the storey above it comes forward to y=41.12, and the left
    half of that upper floor has no front wall at all. ``faces`` records where
    each stretch of wall actually is, so a panel can be fixed to the wall behind
    it rather than to a single plane drawn through the furthest-forward corner —
    which is what left the façade hanging a foot off the building.
    """

    side: str          # "+y" | "-y" | "+x" | "-x"
    face: float        # the outermost front wall, used when nothing is behind
    u0: float          # extent along the face
    u1: float
    z_ground: float
    z_top: float
    faces: list[Wallface] = field(default_factory=list)

    def face_at(self, u: float, z0: float, z1: float | None = None) -> float | None:
        """Where the wall is behind a piece of panel, or None.

        The straight answer is the frontmost wall covering ``(u, z0..z1)``. If
        the building has no wall *there* but does have one higher or lower in
        the same column — the open ground storey under an overhang, or the
        plinth below the lowest wall — the answer is the **nearest** of them,
        because that is the plane the void is cut back from and the plane
        anything spanning it is carried on. Taking the frontmost instead puts
        the plinth a foot proud of the wall it is under.

        None means the column is empty: no wall at this ``u`` at any height.
        That is a gap in the frontage, not a recess in it, and a panel there
        has nothing to be fixed to. The caller drops those pieces rather than
        guessing at a plane for them.
        """
        over = self.wall_at(u, z0, z1)
        if over is not None:
            return over
        z1 = z0 if z1 is None else z1
        near: tuple[float, float] | None = None   # (distance in z, face)
        for w in self.faces:
            if not (w.u0 - 0.05 <= u <= w.u1 + 0.05):
                continue
            gap = max(0.0, w.z0 - z1, z0 - w.z1)
            if near is None or gap < near[0] - 0.02 or (
                abs(gap - near[0]) <= 0.02 and self._further(w.face, near[1])
            ):
                near = (gap, w.face)
        return None if near is None else near[1]

    def wall_at(self, u: float, z0: float, z1: float | None = None) -> float | None:
        """The frontmost wall actually covering this patch of elevation.

        Unlike :meth:`face_at` this does not fall back to the rest of the
        column: None here means the building genuinely has nothing at this
        height, which is what tells a porch from a wall.
        """
        z1 = z0 if z1 is None else z1
        best = None
        for w in self.faces:
            if w.u0 - 0.05 <= u <= w.u1 + 0.05 and (
                min(w.z1, z1) - max(w.z0, z0) > 0.02
            ):
                if best is None or self._further(w.face, best):
                    best = w.face
        return best

    def _further(self, a: float, b: float) -> bool:
        return a > b if self.side in ("+y", "+x") else a < b

    @property
    def out(self) -> float:
        """+1 or -1: which way is outwards, towards the street."""
        return 1.0 if self.side in ("+y", "+x") else -1.0

    @property
    def width(self) -> float:
        return self.u1 - self.u0

    @property
    def height(self) -> float:
        return self.z_top - self.z_ground

    def box(self, u0: float, u1: float, z0: float, z1: float, depth: float,
            face: float | None = None):
        """A panel's world-space box: ``(x0, y0, z0, x1, y1, z1)`` in feet.

        ``face`` is the wall it is fixed to. Left out it falls back to the
        frontmost wall on the building, which is only right where that is in
        fact the wall behind the panel.
        """
        out = self.out
        base = self.face if face is None else face
        near, far = base, base + out * depth
        c0, c1 = min(near, far), max(near, far)
        if depth < 0:  # a recess cuts back into the wall instead
            c0, c1 = min(base + out * depth, base), max(base + out * depth, base)
        if self.side in ("+y", "-y"):
            return (u0, c0, z0, u1, c1, z1)
        return (c0, u0, z0, c1, u1, z1)


@dataclass
class Zone:
    """A stretch of the front over which the massing does not change.

    A front is not one plane and not one height, and this is the unit the
    composition is laid out in. The example set has three zones: a single
    storey wing to u=11.89, a two-storey slot standing at y=40.86, and beyond
    that a first floor at y=41.19 overhanging an open ground storey. Those are
    three different things, and a balcony laid across all three is a balcony
    in mid-air over the first of them — which is exactly what the previous
    composition drew, because it worked in fractions of the overall width and
    never asked where the building was.

    Every element belongs to one zone, so none of them straddles a step.
    """

    u0: float
    u1: float
    walls: list[Wallface] = field(default_factory=list)
    front: float = 0.0     # the frontmost wall face in the zone

    @property
    def width(self) -> float:
        return self.u1 - self.u0

    @property
    def mid(self) -> float:
        return (self.u0 + self.u1) / 2

    @property
    def top(self) -> float:
        return max((w.z1 for w in self.walls), default=0.0)

    @property
    def bottom(self) -> float:
        return min((w.z0 for w in self.walls), default=0.0)

    def walled(self, z0: float, z1: float) -> bool:
        """Is this storey walled, or is it an opening in the massing?"""
        want = (z1 - z0) * 0.6
        return any(min(w.z1, z1) - max(w.z0, z0) > want for w in self.walls)


def zones(frame: Frame, min_width: float | None = None) -> list[Zone]:
    """Break the front into the stretches the building itself defines.

    The cuts are the ends of the real walls. A cut that would leave a sliver
    narrower than ``min_width`` is dropped — a 9 inch step is a construction
    joint, not a change of massing — and a wall is only counted as belonging
    to a zone if it covers most of it, so a stub poking into a wide zone does
    not misdescribe it.
    """
    if min_width is None:
        min_width = max(1.5, frame.width * 0.07)
    cuts = {round(frame.u0, 4), round(frame.u1, 4)}
    for w in frame.faces:
        for u in (w.u0, w.u1):
            if frame.u0 + 1e-6 < u < frame.u1 - 1e-6:
                cuts.add(round(u, 4))
    edges = sorted(cuts)

    keep = [edges[0]]
    for u in edges[1:-1]:
        if u - keep[-1] >= min_width:
            keep.append(u)
    if len(keep) > 1 and edges[-1] - keep[-1] < min_width:
        keep.pop()
    keep.append(edges[-1])

    out: list[Zone] = []
    for a, b in zip(keep, keep[1:]):
        walls = [w for w in frame.faces
                 if min(w.u1, b) - max(w.u0, a) > (b - a) * 0.6]
        front = frame.face
        for w in walls:
            if w is walls[0] or frame._further(w.face, front):
                front = w.face
        out.append(Zone(a, b, walls, front))
    return out


def front_frame(
    extracts: list[PlanExtract], elevations: dict, side: str, plinth_ft: float
) -> Frame | None:
    """The plane of the street-facing wall, from the walls that actually face it."""
    if not extracts:
        return None
    axis = "h" if side in ("+y", "-y") else "v"
    want = "hi" if side in ("+y", "+x") else "lo"

    faces: list[float] = []
    stretches: list[Wallface] = []
    for ex in extracts:
        base, f2f, _wh = elevations[ex.level]
        for w in ex.walls:
            if w.is_railing or not w.exterior or w.axis != axis:
                continue
            if w.outside not in (want, "both"):
                continue
            at = (w.y1 if side == "+y" else w.y0 if side == "-y"
                  else w.x1 if side == "+x" else w.x0)
            faces.append(at)
            a, b = w.u_range()
            stretches.append(Wallface(min(a, b), max(a, b), base, base + f2f, at))
    if not faces:
        return None
    face = max(faces) if side in ("+y", "+x") else min(faces)

    lo, hi = [], []
    for ex in extracts:
        x0, y0, x1, y1 = ex.bounds
        lo.append(x0 if axis == "h" else y0)
        hi.append(x1 if axis == "h" else y1)

    top = max(base + f2f for base, f2f, _wh in elevations.values())
    return Frame(side, face, min(lo), max(hi), 0.0, top, faces=stretches)


def front_openings(
    extracts: list[PlanExtract], elevations: dict, frame: Frame, params
) -> list[tuple[float, float, float, float, str]]:
    """Openings on the street face as ``(u0, u1, z0, z1, kind)``.

    These are what the box frames go round, so they come from the real
    extraction rather than from anything invented here.
    """
    axis = "h" if frame.side in ("+y", "-y") else "v"
    want = "hi" if frame.side in ("+y", "+x") else "lo"
    near = 2.5  # a wall this close to the face is on it
    out: list[tuple[float, float, float, float, str]] = []
    for ex in extracts:
        lp = params.level(ex.level)
        base = elevations[ex.level][0]
        for w in ex.walls:
            if w.is_railing or not w.exterior or w.axis != axis:
                continue
            if w.outside not in (want, "both"):
                continue
            pos = (w.y0 + w.y1) / 2 if axis == "h" else (w.x0 + w.x1) / 2
            if abs(pos - frame.face) > near:
                continue
            for o in w.openings:
                sill = o.sill_ft if o.sill_ft is not None else (
                    0.0 if o.kind == "door"
                    else (lp.window_sill_ft if lp else 3.0)
                )
                head = o.head_ft if o.head_ft is not None else (
                    (lp.door_head_ft if lp else 7.0) if o.kind == "door"
                    else (lp.window_head_ft if lp else 7.0)
                )
                a, b = sorted((o.u0, o.u1))
                out.append((a, b, base + sill, base + head, o.kind))
    return sorted(out, key=lambda t: (t[2], t[0]))


# --------------------------------------------------------------------------- #
# composing a facade
# --------------------------------------------------------------------------- #
def compose(
    extracts: list[PlanExtract], elevations: dict, frame: Frame, params
) -> list[Panel]:
    """Lay out a facade on the wall planes the building actually has.

    The composition is assigned to :class:`Zone` s rather than measured off the
    overall width, because a fraction of the overall width means nothing on a
    building whose front steps. Each zone gets a job — the frontmost tall one
    carries the clad bay, the tall one furthest from it carries the screen, an
    unwalled storey under an overhang is a porch and is drawn as one — and no
    element crosses from one zone into the next.

    Everything is still sized from what the drawing gave us: the real storey
    heights, the real openings, the real wall planes. Nothing is a fixed
    number, so the composition adapts to whatever plan it is handed.
    """
    fp: FacadeParams = params.facade
    panels: list[Panel] = []
    n = [0]

    def add(kind: str, u0: float, u1: float, z0: float, z1: float, **kw) -> None:
        if u1 - u0 < 0.15 or z1 - z0 < 0.15:
            return
        n[0] += 1
        panels.append(Panel(
            id=f"p{n[0]}",
            kind=kind,
            u0=round(u0, 3), u1=round(u1, 3), z0=round(z0, 3), z1=round(z1, 3),
            depth_ft=round(kw.get("depth", DEPTH[kind]), 3),
            material=kw.get("material", KIND_MATERIAL[kind]),
            label=kw.get("label", ""),
            hole=kw.get("hole"),
        ))

    u0, u1 = frame.u0, frame.u1
    span = u1 - u0
    floors = sorted(elevations)
    base_of = {lv: elevations[lv][0] for lv in floors}
    f2f_of = {lv: elevations[lv][1] for lv in floors}
    roof = frame.z_top
    upper = base_of[floors[1]] if len(floors) > 1 else base_of[floors[0]] + 10.0
    openings = front_openings(extracts, elevations, frame, params)
    ground_ops = [o for o in openings if o[2] < upper - 1.0]
    upper_ops = [o for o in openings if o[2] >= upper - 1.0]

    # ---- what the building is ---------------------------------------------
    zs = zones(frame)
    solid = [z for z in zs if z.walls]
    tall = [z for z in solid if z.top >= roof - 1.0]
    if not tall:  # nothing reaches the top: work with the tallest there is
        tall = sorted(solid, key=lambda z: z.top)[-1:]
    low = [z for z in solid if z not in tall]

    # the silhouette, one panel per zone over the wall it actually has, so the
    # elevation drawing shows the massing instead of one flat rectangle
    lowest = base_of[floors[0]] + 0.5
    for zn in solid:
        add("field", zn.u0, zn.u1,
            frame.z_ground if zn.bottom <= lowest else zn.bottom, zn.top,
            label="Wall")
    if not solid:
        add("field", u0, u1, frame.z_ground, roof, label="Main wall")

    # an unwalled storey with building above it is a porch: the deepest shadow
    # on the elevation, and it costs nothing to draw because it is already
    # there. Not governed by ``fp.void`` — this is the building, not a choice.
    porches: list[tuple[Zone, float, float]] = []
    for zn in solid:
        for lv in floors:
            b, h = base_of[lv], f2f_of[lv]
            if zn.walled(b, b + h) or zn.top <= b + h - 0.5:
                continue
            foot = frame.z_ground if lv == floors[0] else b
            porches.append((zn, foot, b + h))

    if fp.arrangement == "quiet":
        _quiet(add, params, frame, fp, floors, base_of, tall, low, openings,
               porches)
        panels = _unblock(panels, openings)
        panels.sort(key=lambda p: KIND_ORDER.index(p.kind))
        return panels

    # ---- give each zone its job -------------------------------------------
    # The clad bay goes on the frontmost tall zone: that is the block already
    # standing forward, and cladding it says what the building is doing rather
    # than arguing with it. The screen then goes on the tall zone furthest
    # away, so the two heavy elements sit apart rather than fighting.
    bay = max(tall, key=lambda z: (frame.out * z.front, z.width))
    rest = [z for z in tall if z is not bay]
    if fp.screen_side in ("left", "right") and tall:
        pick = min if fp.screen_side == "left" else max
        screen_zone = pick(tall, key=lambda z: z.mid)
        if screen_zone is bay and rest:
            bay = max(rest, key=lambda z: (frame.out * z.front, z.width))
            rest = [z for z in tall if z is not bay]
    elif rest:
        screen_zone = max(rest, key=lambda z: abs(z.mid - bay.mid))
    else:
        screen_zone = bay  # one plane only: the screen takes one end of it

    # ---- the projecting clad bay, round the best window on its zone --------
    ups = [o for o in upper_ops if bay.u0 - 0.2 <= (o[0] + o[1]) / 2 <= bay.u1 + 0.2]
    pad = max(1.6, span * 0.055)
    if ups:
        feature = max(ups, key=lambda o: (o[1] - o[0]) * (o[3] - o[2]))
        mass0 = max(bay.u0, feature[0] - pad)
        mass1 = min(bay.u1, feature[1] + pad)
    else:
        feature = None
        mass0, mass1 = bay.u0 + bay.width * 0.1, bay.u1 - bay.width * 0.1

    # ---- the screen, at the end of its zone away from the bay --------------
    sw = min(fp.screen_width_ft, screen_zone.width * 0.85)
    if screen_zone is bay:
        sw = min(fp.screen_width_ft, screen_zone.width * 0.3)
    if screen_zone.mid <= bay.mid:
        scr0, scr1 = screen_zone.u0, screen_zone.u0 + sw
    else:
        scr0, scr1 = screen_zone.u1 - sw, screen_zone.u1
    if screen_zone is bay:  # keep the two off each other inside one plane
        mass0 = max(mass0, scr1 + 0.6) if scr0 <= bay.mid else mass0
        mass1 = min(mass1, scr0 - 0.6) if scr0 > bay.mid else mass1

    taken = [(mass0, mass1), (scr0, scr1)]

    if fp.arrangement == "framed":
        # a clad bay at each end of the tall run instead of a screen
        scr0 = scr1 = 0.0
        other = screen_zone if screen_zone is not bay else None
        if other is not None:
            o0 = other.u0 if other.mid <= bay.mid else other.u1 - min(
                other.width * 0.8, mass1 - mass0)
            o1 = o0 + min(other.width * 0.8, max(2.0, mass1 - mass0))
            add("recess", o0 - 0.35, o1 + 0.35, upper - 1.3, upper,
                depth=-0.3, label="Shadow joint")
            add("mass", o0, o1, upper, other.top - 0.5,
                depth=fp.mass_projection_ft, label="Clad bay")
            taken.append((o0, o1))

    # ---- the porches, and the posts that carry them ------------------------
    for zn, foot, head in porches:
        for a, b in _open_runs(frame, zn, foot, head):
            add("recess", a, b, foot, head,
                depth=-fp.void_depth_ft, label="Recessed porch")
            if b - a > 3.0:
                # a lintel across the head, so the void stops on a line rather
                # than fading into the wall above it, and posts to give it scale
                add("slab", a, b, head - 0.6, head + 0.3, depth=0.5,
                    label="Porch head")
                for c in (a + 0.45, b - 0.8):
                    add("post", c, c + 0.35, foot, head - 0.6, label="Porch post")

    # ---- a balcony, but only where the building has no recess of its own ---
    if fp.void and not porches:
        spot = _widest_free(tall, taken)
        if spot and spot[2] - spot[1] > max(3.0, span * 0.1):
            _zn, a, b = spot
            add("recess", a, b, upper - 1.2, roof - 0.4,
                depth=-fp.void_depth_ft, label="Balcony recess")
            # the white soffit under it and the parapet edge in front of it,
            # which is what gives the void depth in a straight-on view
            add("slab", a, b, upper - 1.2, upper + 0.1, label="Balcony soffit")
            add("slab", a, b, roof - 1.5, roof - 0.4, label="Balcony head")
            add("slab", a, b, upper + 2.4, upper + 3.3, label="Balcony edge")
            taken.append((a, b))

    # ---- the projecting clad mass -----------------------------------------
    if fp.mass and mass1 - mass0 > 1.0:
        # a dark joint under it, so it reads as sitting on the wall not in it —
        # unless it already sits over a porch, which is a deeper shadow than
        # any joint and does not want a second one drawn inside it
        if not any(zn.u0 < mass1 and zn.u1 > mass0 and abs(head - upper) < 0.6
                   for zn, _foot, head in porches):
            add("recess", mass0 - 0.35, mass1 + 0.35, upper - 1.3, upper,
                depth=-0.3, label="Shadow joint")
        add("mass", mass0, mass1, upper, bay.top - 0.5,
            depth=fp.mass_projection_ft, label="Clad bay")
        if fp.box_frames and feature is not None:
            a, b, z0, z1 = feature[0], feature[1], feature[2], feature[3]
            m = fp.frame_margin_ft
            add("frame", a - m, b + m, z0 - m, z1 + m, hole=(a, b, z0, z1),
                depth=fp.mass_projection_ft + DEPTH["frame"], label="Bay window frame")

    # ---- the screen ---------------------------------------------------------
    if fp.fins and fp.arrangement != "framed" and sw > 0.8:
        top = _cap(params, screen_zone.top) + fp.canopy_thickness_ft + 0.6
        # A backing panel behind the blades. Without it the screen is a handful
        # of poles standing in front of nothing; with it, it reads as a screen,
        # which is the whole point of the element. It steps with the wall.
        add("clad", scr0, scr1, frame.z_ground, screen_zone.top, depth=0.25,
            label="Screen backing")
        add("recess", scr0, scr1, upper, screen_zone.top, depth=-0.25,
            label="Screen shadow")
        _fins(add, scr0, scr1, frame.z_ground, top, fp)

    # ---- the canopy, capping the tall part ----------------------------------
    if fp.canopy and tall:
        ta = min(z.u0 for z in tall) - fp.canopy_side_ft
        tb = max(z.u1 for z in tall) + fp.canopy_side_ft
        cap = _cap(params, roof)
        add("canopy", ta, tb, cap, cap + fp.canopy_thickness_ft,
            depth=fp.canopy_projection_ft, label="Roof canopy")

    # ---- everything left over is plain wall, banded and framed --------------
    if fp.bands:
        h = fp.band_height_ft
        for lv in floors[1:]:
            z = base_of[lv]
            for a, b in _gaps(u0, u1, taken):
                add("band", a, b, z - h * 0.5, z + h * 0.5, label="Floor band")
        # a coping on any wing that stops short of the roof, at its own top —
        # the band that used to run at roof level across it sat in mid-air
        lines = [base_of[lv] for lv in floors[1:]] + [roof]
        for zn in low:
            if any(abs(zn.top - t) < h * 0.6 for t in lines):
                continue
            for a, b in _gaps(zn.u0, zn.u1, taken):
                add("band", a, b, zn.top - h * 0.6, zn.top + h * 0.4,
                    label="Parapet coping")

    if fp.box_frames:
        m = fp.frame_margin_ft
        for a, b, z0, z1, _kind in ground_ops + (
            [o for o in upper_ops if not (mass0 <= o[0] and o[1] <= mass1)]
        ):
            if scr1 > scr0 and scr0 - 0.3 <= a and b <= scr1 + 0.3:
                continue  # behind the screen; a frame there is never seen
            add("frame", a - m, b + m, z0 - m, z1 + m, hole=(a, b, z0, z1),
                label="Box frame")

    panels = _unblock(panels, openings)
    panels.sort(key=lambda p: KIND_ORDER.index(p.kind))
    return panels


def _widest_free(zs: list[Zone], taken) -> tuple[Zone, float, float] | None:
    """The widest run of face still free — **inside a single zone**.

    Searching across zones is what put a balcony over a single-storey wing.
    """
    best = None
    for zn in zs:
        for a, b in _gaps(zn.u0, zn.u1, taken):
            if best is None or b - a > best[2] - best[1]:
                best = (zn, a, b)
    return best


def _cap(params, roof: float) -> float:
    """The top of the building as it is actually built — parapet included.

    ``Frame.z_top`` is the top of the *walls*. The parapet stands three feet
    above that, so a canopy placed at ``z_top`` reads as a dark band halfway up
    a white box rather than as the thing that finishes the building.
    """
    if getattr(params, "roof", None) == "flat_parapet":
        return roof + max(0.0, params.parapet_ft)
    return roof


def _quiet(add, params, frame, fp, floors, base_of, tall, low, openings,
           porches) -> None:
    """The restrained arrangement: bands and frames, nothing projecting far.

    Zone-aware all the same — the roof band goes over the tall part, each wing
    is coped at its own parapet, and a porch is still a porch.
    """
    h = fp.band_height_ft
    for lv in floors[1:]:
        z = base_of[lv]
        add("band", frame.u0, frame.u1, z - h * 0.5, z + h * 0.5,
            label="Floor band")
    lines = [base_of[lv] for lv in floors[1:]] + [frame.z_top]
    for zn in low:
        if not any(abs(zn.top - t) < h * 0.6 for t in lines):
            add("band", zn.u0, zn.u1, zn.top - h * 0.6, zn.top + h * 0.4,
                label="Parapet coping")
    for zn, foot, head in porches:
        add("recess", zn.u0, zn.u1, foot, head,
            depth=-min(fp.void_depth_ft, 1.9), label="Recessed porch")
    m = fp.frame_margin_ft
    for a, b, z0, z1, _k in openings:
        add("frame", a - m, b + m, z0 - m, z1 + m, hole=(a, b, z0, z1),
            label="Box frame")
    if fp.canopy and tall:
        cap = _cap(params, frame.z_top)
        add("canopy", min(z.u0 for z in tall) - 0.5,
            max(z.u1 for z in tall) + 0.5,
            cap, cap + 0.7, depth=1.4, label="Roof band")


def _open_runs(frame: Frame, zn: Zone, z0: float, z1: float):
    """The stretches of a zone with no wall over this storey — the real void.

    A zone counts a wall that covers most of it, so a stub of ground floor
    poking into a porch does not stop it being a porch; but the porch must not
    be drawn across that stub, or it is a recess cut through a solid wall.
    """
    blocks = [(w.u0, w.u1) for w in frame.faces
              if min(w.z1, z1) - max(w.z0, z0) > 0.02]
    return _gaps(zn.u0, zn.u1, blocks)


def _carve(u0: float, u1: float, z0: float, z1: float, blocks):
    """A rectangle with the openings taken out of it, as a list of rectangles.

    ``Panel.hole`` carries one opening. A clad bay or a screen may stand over
    several, so this bands the area in height and keeps the gaps beside each.
    """
    cuts = sorted({z0, z1} | {v for b in blocks for v in (b[2], b[3])
                              if z0 + 0.05 < v < z1 - 0.05})
    out = []
    for c, d in zip(cuts, cuts[1:]):
        over = [(b[0], b[1]) for b in blocks if b[2] < d - 0.01 and b[3] > c + 0.01]
        for a, b in _gaps(u0, u1, over):
            out.append((a, b, c, d))
    return out


def _unblock(panels: list[Panel], openings) -> list[Panel]:
    """Take the building's openings out of anything solid laid across them.

    The composition places its elements by where the building *steps*, which
    is the right question to ask, but it means an element lands wherever the
    massing puts it — and the clad bay lands over the very window it exists to
    frame. Built solid it bricks it up, which is what the first zone-aligned
    composition did to two of the three windows on the front.

    A screen in front of a window is the point of a screen, so fins and posts
    are left alone; the field is the wall itself and is never built.
    """
    out: list[Panel] = []
    for p in panels:
        blocks = [o for o in openings
                  if o[0] < p.u1 - 0.1 and o[1] > p.u0 + 0.1
                  and o[2] < p.z1 - 0.1 and o[3] > p.z0 + 0.1]
        if p.kind not in SOLID or p.hole or not blocks:
            out.append(p)
            continue
        for i, (a, b, c, d) in enumerate(_carve(p.u0, p.u1, p.z0, p.z1, blocks)):
            out.append(p.model_copy(update={
                "id": p.id if i == 0 else f"{p.id}-{i}",
                "u0": round(a, 3), "u1": round(b, 3),
                "z0": round(c, 3), "z1": round(d, 3),
                "label": p.label if i == 0 else "",
            }))
    return out


def _gaps(u0: float, u1: float, taken):
    """The stretches of face left over once the big elements have their bays."""
    blocks = sorted((max(u0, a), min(u1, b)) for a, b in taken if b > a)
    out, cursor = [], u0
    for a, b in blocks:
        if a - cursor > 0.6:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if u1 - cursor > 0.6:
        out.append((cursor, u1))
    return out


def _fins(add, u0: float, u1: float, z0: float, z1: float, fp: FacadeParams) -> None:
    """A row of vertical timber fins across a bay."""
    span = u1 - u0
    pitch = max(0.45, fp.fin_pitch_ft)
    count = max(2, int(span / pitch))
    step = span / count
    for i in range(count):
        c = u0 + step * (i + 0.5)
        add("fin", c - fp.fin_width_ft / 2, c + fp.fin_width_ft / 2, z0, z1,
            label="Fin" if i == 0 else "")


# --------------------------------------------------------------------------- #
# building it
# --------------------------------------------------------------------------- #
def build(scene: Scene, panels: list[Panel], frame: Frame, group: str = "Façade") -> int:
    """Turn panels into boxes on the front face. Returns how many were built."""
    made = 0
    for panel in panels:
        if panel.kind == "field":
            continue  # the wall is already there; the field is for the drawing
        if abs(panel.depth_ft) < 0.01:
            continue
        mesh = scene.mesh(f"{group} — {panel.kind}", panel.material)
        mounted = panel.kind in WALL_MOUNTED
        for u0, u1, z0, z1 in _pieces(panel):
            parts = (_by_wall(frame, u0, u1, z0, z1, panel.kind in Z_STEPPED)
                     if mounted else [(u0, u1, z0, z1, frame.face)])
            for a, b, c, d, at in parts:
                x0, y0, zz0, x1, y1, zz1 = frame.box(a, b, c, d, panel.depth_ft, at)
                mesh.add_box(
                    x0 * FT_TO_M, zz0 * FT_TO_M, y0 * FT_TO_M,
                    x1 * FT_TO_M, zz1 * FT_TO_M, y1 * FT_TO_M,
                )
                made += 1
    return made


def _by_wall(frame: Frame, u0: float, u1: float, z0: float, z1: float,
             stepped: bool = True):
    """Split a panel where the wall behind it steps, so each piece sits on it.

    A band running the width of a house whose upper storey comes forward has to
    step with it; built on one plane it stands a foot off the lower wall at one
    end and buries itself at the other. The cuts are the ends of the real
    walls, so a piece is only ever created where the plane actually changes.

    ``stepped`` says whether to cut in height as well as in width. A clad panel
    running two storeys does; a floor band does not, because it is the edge of
    the slab dividing them and belongs on one plane, not sliced in two.
    """
    ucuts = _cuts(u0, u1, [(w.u0, w.u1) for w in frame.faces])
    zcuts = (_cuts(z0, z1, [(w.z0, w.z1) for w in frame.faces]) if stepped
             else [z0, z1])
    out = []
    for a, b in zip(ucuts, ucuts[1:]):
        for c, d in zip(zcuts, zcuts[1:]):
            at = frame.face_at((a + b) / 2, c, d)
            if at is not None:  # no wall in this column: nothing to fix to
                out.append((a, b, c, d, at))
    return _weld(out)


def _cuts(lo: float, hi: float, edges) -> list[float]:
    got = {lo, hi}
    for a, b in edges:
        for v in (a, b):
            if lo + 0.02 < v < hi - 0.02:
                got.add(v)
    return sorted(got)


def _weld(parts):
    """Put the split pieces back together wherever the plane did not change.

    The cuts come from every wall on the front, so most of them fall inside one
    plane and would otherwise leave a seam — two boxes meeting face to face,
    which z-fights in the viewer and triples the triangle count for nothing.
    """
    def pass_(parts, key, joins, grow):
        out = []
        for p in sorted(parts, key=key):
            prev = out[-1] if out else None
            if prev and abs(prev[4] - p[4]) < 1e-9 and joins(prev, p):
                out[-1] = grow(prev, p)
            else:
                out.append(p)
        return out

    parts = pass_(  # in height: same column, one starting where the last ended
        parts, lambda p: (p[0], p[1], p[2]),
        lambda a, b: a[0] == b[0] and a[1] == b[1] and abs(a[3] - b[2]) < 1e-9,
        lambda a, b: (a[0], a[1], a[2], b[3], a[4]),
    )
    parts = pass_(  # then in width, over the same band of height
        parts, lambda p: (p[2], p[3], p[0]),
        lambda a, b: a[2] == b[2] and a[3] == b[3] and abs(a[1] - b[0]) < 1e-9,
        lambda a, b: (a[0], b[1], a[2], a[3], a[4]),
    )
    return [p for p in parts if p[1] - p[0] > 0.02 and p[3] - p[2] > 0.02]


def _pieces(panel: Panel):
    """A panel as rectangles — four of them if it has an opening through it."""
    if not panel.hole:
        return [(panel.u0, panel.u1, panel.z0, panel.z1)]
    hu0, hu1, hz0, hz1 = panel.hole
    hu0, hu1 = max(panel.u0, hu0), min(panel.u1, hu1)
    hz0, hz1 = max(panel.z0, hz0), min(panel.z1, hz1)
    if hu1 <= hu0 or hz1 <= hz0:
        return [(panel.u0, panel.u1, panel.z0, panel.z1)]
    return [
        (panel.u0, hu0, panel.z0, panel.z1),   # left jamb
        (hu1, panel.u1, panel.z0, panel.z1),   # right jamb
        (hu0, hu1, hz1, panel.z1),             # head
        (hu0, hu1, panel.z0, hz0),             # cill
    ]
