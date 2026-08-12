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

#: panel kinds, in the order they are drawn — later ones sit in front
KIND_ORDER = ["field", "recess", "clad", "band", "fin", "frame", "canopy", "pier"]

#: which finish slot each kind wears, following the document's palette roles
KIND_MATERIAL = {
    "field": "wall_ext",    # light grey — main wall finish
    "recess": "accent",     # dark grey — accent bands, vertical elements
    "clad": "clad",         # teak — vertical cladding
    "fin": "clad",          # teak — the vertical fins
    "band": "trim",         # warm white — horizontal bands
    "frame": "trim",        # warm white — box frames
    "canopy": "accent",     # dark grey — the roof canopy
    "pier": "accent",
}

#: default projection depths in feet, read off the document's own lvl tags
DEPTH = {
    "field": 0.0,
    "recess": -0.35,   # set back, not proud
    "clad": 0.35,
    "band": 0.50,
    "frame": 0.65,
    "fin": 1.00,
    "canopy": 3.15,    # the document writes lvl +3'2" on the top slab
    "pier": 0.75,
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

    # the wall itself, so the elevation drawing has a ground to sit on
    add("field", u0, u1, frame.z_ground, roof, label="Main wall")

    # a horizontal band at every floor line: the strongest ordering device on
    # the document's renders, and the thing that ties the composition together
    if fp.bands:
        for lv in floors[1:]:
            z = base_of[lv]
            add("band", u0, u1, z - fp.band_height_ft * 0.5,
                z + fp.band_height_ft * 0.5, label=f"Band at {lv}")

    # the entrance bay: a full-height slot of cladding with fins in front of it,
    # placed over the widest ground-floor opening, which is the way in
    openings = front_openings(extracts, elevations, frame, params)
    ground = [o for o in openings if o[2] < base_of[floors[0]] + 6.0]
    if fp.entrance_bay and ground:
        widest = max(ground, key=lambda o: o[1] - o[0])
        cx = (widest[0] + widest[1]) / 2
        half = max(fp.bay_width_ft, (widest[1] - widest[0]) * 0.75) / 2
        bx0, bx1 = max(u0, cx - half), min(u1, cx + half)
        add("recess", bx0, bx1, frame.z_ground, roof, label="Entrance recess")
        add("clad", bx0, bx1, frame.z_ground, roof, label="Entrance cladding",
            depth=DEPTH["clad"])
        # Fins start at the floor above, never at the ground. Run to grade they
        # stand across the front door, which is where people walk in.
        fin_base = base_of[floors[1]] if len(floors) > 1 else (
            base_of[floors[0]] + 8.0
        )
        if fp.fins:
            _fins(add, bx0, bx1, fin_base, roof, fp)

    # a cladding panel on the widest run of blank wall, so the facade is not
    # one material end to end
    if fp.clad_panel:
        gap = _widest_gap(u0, u1, [(o[0], o[1]) for o in openings], keep_out=1.5)
        if gap and gap[1] - gap[0] > 3.0:
            gx0, gx1 = gap
            top_floor = floors[-1]
            add("clad", gx0, gx1, base_of[top_floor] - fp.band_height_ft,
                roof - 0.4, label="Feature cladding")

    # box frames: a warm white surround standing proud of every opening on the
    # face. The single most recognisable move on the reference elevations.
    if fp.box_frames:
        m = fp.frame_margin_ft
        for a, b, z0, z1, _kind in openings:
            add("frame", a - m, b + m, z0 - m, z1 + m, label="Box frame",
                hole=(a, b, z0, z1))

    # the canopy: a deep dark slab across the top, the document's lvl +3'2"
    if fp.canopy:
        add("canopy", u0 - fp.canopy_side_ft, u1 + fp.canopy_side_ft,
            roof - fp.canopy_depth_ft, roof + fp.canopy_thickness_ft,
            depth=fp.canopy_projection_ft, label="Roof canopy")

    panels.sort(key=lambda p: KIND_ORDER.index(p.kind))
    return panels


def _fins(add, u0: float, u1: float, z0: float, z1: float, fp: FacadeParams) -> None:
    """A row of vertical timber fins across a bay."""
    span = u1 - u0
    pitch = max(0.6, fp.fin_pitch_ft)
    count = max(2, int(span / pitch))
    step = span / count
    for i in range(count):
        c = u0 + step * (i + 0.5)
        add("fin", c - fp.fin_width_ft / 2, c + fp.fin_width_ft / 2, z0, z1,
            label="Fin" if i == 0 else "")


def _widest_gap(u0: float, u1: float, taken, keep_out: float):
    """The widest stretch of face with no opening on it."""
    blocks = sorted((max(u0, a - keep_out), min(u1, b + keep_out)) for a, b in taken)
    best, cursor = None, u0
    for a, b in blocks:
        if a - cursor > (best[1] - best[0] if best else 0):
            best = (cursor, a)
        cursor = max(cursor, b)
    if u1 - cursor > (best[1] - best[0] if best else 0):
        best = (cursor, u1)
    return best


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
