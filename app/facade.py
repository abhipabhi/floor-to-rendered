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

from dataclasses import dataclass

from .mesh import Scene
from .models import FacadeParams, Panel, PlanExtract
from .units import FT_TO_M

#: panel kinds, back to front — the order they are drawn in
KIND_ORDER = [
    "field", "recess", "clad", "band", "mass", "slab", "frame", "post", "fin", "canopy",
    "pier",
]

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
    "post": "accent",       # dark grey — the canopy's slim supports
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
    "post": 1.90,
    "fin": 2.00,
    "canopy": 3.15,    # the document writes lvl +3'2" on the top slab
    "pier": 0.75,
}

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
class Frame:
    """The plane the facade is composed on.

    ``u`` runs along the face from its left-hand end as seen from the street,
    ``z`` is height above ground, and ``depth`` is measured outwards. Keeping
    that mapping in one place is what lets the composition be written in plain
    left-to-right terms whichever side of the plan the road happens to be on.
    """

    side: str          # "+y" | "-y" | "+x" | "-x"
    face: float        # the coordinate of the outer face
    u0: float          # extent along the face
    u1: float
    z_ground: float
    z_top: float

    @property
    def width(self) -> float:
        return self.u1 - self.u0

    @property
    def height(self) -> float:
        return self.z_top - self.z_ground

    def box(self, u0: float, u1: float, z0: float, z1: float, depth: float):
        """A panel's world-space box: ``(x0, y0, z0, x1, y1, z1)`` in feet."""
        out = 1.0 if self.side in ("+y", "+x") else -1.0
        near, far = self.face, self.face + out * depth
        c0, c1 = min(near, far), max(near, far)
        if depth < 0:  # a recess cuts back into the wall instead
            c0, c1 = min(self.face + out * depth, self.face), max(
                self.face + out * depth, self.face
            )
        if self.side in ("+y", "-y"):
            return (u0, c0, z0, u1, c1, z1)
        return (c0, u0, z0, c1, u1, z1)


def front_frame(
    extracts: list[PlanExtract], elevations: dict, side: str, plinth_ft: float
) -> Frame | None:
    """The plane of the street-facing wall, from the walls that actually face it."""
    if not extracts:
        return None
    axis = "h" if side in ("+y", "-y") else "v"
    want = "hi" if side in ("+y", "+x") else "lo"

    faces: list[float] = []
    for ex in extracts:
        for w in ex.walls:
            if w.is_railing or not w.exterior or w.axis != axis:
                continue
            if w.outside not in (want, "both"):
                continue
            faces.append(w.y1 if side == "+y" else w.y0 if side == "-y"
                         else w.x1 if side == "+x" else w.x0)
    if not faces:
        return None
    face = max(faces) if side in ("+y", "+x") else min(faces)

    lo, hi = [], []
    for ex in extracts:
        x0, y0, x1, y1 = ex.bounds
        lo.append(x0 if axis == "h" else y0)
        hi.append(x1 if axis == "h" else y1)

    top = max(base + f2f for base, f2f, _wh in elevations.values())
    return Frame(side, face, min(lo), max(hi), 0.0, top)


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
    """Lay out a facade over the building's own proportions.

    Every dimension here is a fraction of something the drawing gave us — the
    building's width, its storey heights, where its openings are — so the
    composition adapts instead of assuming a house the size of the one it was
    written against.
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
    floors = sorted(elevations)
    base_of = {lv: elevations[lv][0] for lv in floors}
    roof = frame.z_top
    upper = base_of[floors[1]] if len(floors) > 1 else base_of[floors[0]] + 10.0
    openings = front_openings(extracts, elevations, frame, params)
    ground_ops = [o for o in openings if o[2] < upper - 1.0]
    upper_ops = [o for o in openings if o[2] >= upper - 1.0]

    # the wall itself, so the elevation drawing has a ground to sit on
    add("field", u0, u1, frame.z_ground, roof, label="Main wall")

    if fp.arrangement == "quiet":
        _quiet(add, frame, fp, floors, base_of, roof, openings)
        panels.sort(key=lambda p: KIND_ORDER.index(p.kind))
        return panels

    # ---- decide the three zones ------------------------------------------
    # The clad mass goes round the best upper-floor window, because that is the
    # element it exists to frame. The screen then stands at whichever end is
    # further from it, so the two heavy elements sit apart rather than fighting.
    span = u1 - u0
    if upper_ops:
        feature = max(upper_ops, key=lambda o: (o[1] - o[0]) * (o[3] - o[2]))
        fx0, fx1 = feature[0], feature[1]
    else:
        fx0, fx1 = u0 + span * 0.58, u0 + span * 0.88
    pad = max(1.6, span * 0.055)
    mass0, mass1 = max(u0, fx0 - pad), min(u1, fx1 + pad)

    side = fp.screen_side
    if side == "auto":
        # whichever end has more wall left over — the screen and the clad bay
        # are the two heavy elements, and they have to sit apart or they read
        # as one lump. Picking the *narrower* side lands them on top of
        # each other, which is what the first version of this did.
        side = "left" if (mass0 - u0) > (u1 - mass1) else "right"
    sw = min(fp.screen_width_ft, span * 0.22)
    if side == "left":
        scr0, scr1 = u0, u0 + sw
        void0, void1 = scr1, mass0
    else:
        scr0, scr1 = u1 - sw, u1
        void0, void1 = mass1, scr0

    if fp.arrangement == "framed":
        # a clad bay at each end, the recess in the middle
        other0, other1 = (u1 - (mass1 - mass0), u1) if side == "left" else (
            u0, u0 + (mass1 - mass0))
        void0, void1 = min(mass1, other1), max(mass0, other0)
        if void1 - void0 < span * 0.15:
            void0, void1 = u0 + span * 0.38, u0 + span * 0.62

    # ---- the recessed void, and its slabs ---------------------------------
    if fp.void and void1 - void0 > span * 0.12:
        add("recess", void0, void1, upper - 1.2, roof - 0.4,
            depth=-fp.void_depth_ft, label="Balcony recess")
        # the white soffit under it and the parapet edge in front of it, which
        # is what gives the void its depth in a straight-on view
        add("slab", void0, void1, upper - 1.2, upper + 0.1, label="Balcony soffit")
        add("slab", void0, void1, roof - 1.5, roof - 0.4, label="Balcony head")
        add("slab", void0, void1, upper + 2.4, upper + 3.3, label="Balcony edge")

    # ---- the projecting clad mass -----------------------------------------
    if fp.mass and mass1 - mass0 > 1.0:
        # a dark joint under it, so it reads as sitting on the wall not in it
        add("recess", mass0 - 0.35, mass1 + 0.35, upper - 1.3, upper,
            depth=-0.3, label="Shadow joint")
        add("mass", mass0, mass1, upper, roof - 0.5,
            depth=fp.mass_projection_ft, label="Clad bay")
        if fp.box_frames and upper_ops:
            a, b, z0, z1 = feature[0], feature[1], feature[2], feature[3]
            m = fp.frame_margin_ft
            add("frame", a - m, b + m, z0 - m, z1 + m, hole=(a, b, z0, z1),
                depth=fp.mass_projection_ft + DEPTH["frame"], label="Bay window frame")

    # ---- the screen ---------------------------------------------------------
    if fp.fins and sw > 0.8:
        top = roof + fp.canopy_thickness_ft + 0.6
        # A backing panel behind the blades, but only as high as the building
        # goes. Without it the screen is a handful of poles standing in front of
        # nothing; with it, it reads as a screen — which is the whole point of
        # the element.
        add("clad", scr0, scr1, frame.z_ground, roof, depth=0.25,
            label="Screen backing")
        add("recess", scr0, scr1, upper, roof, depth=-0.25, label="Screen shadow")
        _fins(add, scr0, scr1, frame.z_ground, top, fp)

    # ---- the canopy, on posts ----------------------------------------------
    if fp.canopy:
        add("canopy", u0 - fp.canopy_side_ft, u1 + fp.canopy_side_ft,
            roof, roof + fp.canopy_thickness_ft,
            depth=fp.canopy_projection_ft, label="Roof canopy")
        # slim posts carrying its far edge, at the end away from the screen
        px = mass1 - 0.9 if side == "left" else mass0 + 0.3
        for i in range(3):
            c = px + i * 0.75
            add("post", c, c + 0.32, upper + 2.0, roof,
                depth=DEPTH["post"],
                label="Canopy post" if i == 0 else "")

    # ---- everything that is left is plain wall, banded and framed ----------
    if fp.bands:
        for lv in floors[1:]:
            z = base_of[lv]
            for a, b in _gaps(u0, u1, [(mass0, mass1), (scr0, scr1), (void0, void1)]):
                add("band", a, b, z - fp.band_height_ft * 0.5,
                    z + fp.band_height_ft * 0.5, label="Floor band")

    if fp.box_frames:
        m = fp.frame_margin_ft
        for a, b, z0, z1, _kind in ground_ops + (
            [o for o in upper_ops if not (mass0 <= o[0] and o[1] <= mass1)]
        ):
            if scr0 - 0.3 <= a and b <= scr1 + 0.3:
                continue  # behind the screen; a frame there is never seen
            add("frame", a - m, b + m, z0 - m, z1 + m, hole=(a, b, z0, z1),
                label="Box frame")

    panels.sort(key=lambda p: KIND_ORDER.index(p.kind))
    return panels


def _quiet(add, frame, fp, floors, base_of, roof, openings) -> None:
    """The restrained arrangement: bands and frames, nothing projecting far."""
    for lv in floors[1:]:
        z = base_of[lv]
        add("band", frame.u0, frame.u1, z - fp.band_height_ft * 0.5,
            z + fp.band_height_ft * 0.5, label="Floor band")
    m = fp.frame_margin_ft
    for a, b, z0, z1, _k in openings:
        add("frame", a - m, b + m, z0 - m, z1 + m, hole=(a, b, z0, z1),
            label="Box frame")
    if fp.canopy:
        add("canopy", frame.u0 - 0.5, frame.u1 + 0.5, roof, roof + 0.7,
            depth=1.4, label="Roof band")


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
        for u0, u1, z0, z1 in _pieces(panel):
            x0, y0, zz0, x1, y1, zz1 = frame.box(u0, u1, z0, z1, panel.depth_ft)
            mesh.add_box(
                x0 * FT_TO_M, zz0 * FT_TO_M, y0 * FT_TO_M,
                x1 * FT_TO_M, zz1 * FT_TO_M, y1 * FT_TO_M,
            )
            made += 1
    return made


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
