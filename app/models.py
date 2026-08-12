"""The job's data model.

Two coordinate spaces appear here and nowhere else is the difference blurred:

* **plan space** — feet, x right, y down, exactly as the sheet is read, shared by
  every storey after alignment. Everything extracted lives here.
* **model space** — metres, y up, produced only by the 3D builder.

Anything a human might reasonably disagree with (the scale, a wall thickness, a
window's sill height, which storey a sheet is) is stored, editable and round
trips through the API — the extractor's answer is a starting point, not a verdict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .datum import Quantity
from .units import FT_TO_M

Axis = Literal["h", "v"]
OpeningKind = Literal["door", "window", "opening"]


# --------------------------------------------------------------------------- #
# extracted plan geometry
# --------------------------------------------------------------------------- #
class Opening(BaseModel):
    """A hole in a wall, positioned along that wall's long axis in plan feet."""

    id: str
    kind: OpeningKind = "window"
    u0: float  # start along the wall's long axis (absolute plan ft)
    u1: float
    sill_ft: float | None = None  # None → take the level default for this kind
    head_ft: float | None = None
    source: str = "auto"  # auto | manual

    @property
    def width_ft(self) -> float:
        return abs(self.u1 - self.u0)


class Wall(BaseModel):
    """One axis-aligned wall, stored as its footprint rectangle in plan feet.

    A railing — a balcony edge or a stairwell guard, drawn in the glazing pen
    with no wall band behind it — is the same shape carried to a lower height,
    so it rides along here rather than in a parallel structure.
    """

    id: str
    axis: Axis
    x0: float
    y0: float
    x1: float
    y1: float
    kind: Literal["wall", "railing"] = "wall"
    height_ft: float | None = None  # None → the storey's wall height
    exterior: bool = False
    # *Which* face looks out: "lo" is the x0/y0 face, "hi" the x1/y1 face, and
    # "both" is a free-standing wall with weather on either side. Knowing the
    # side rather than merely the fact is what lets a sunshade sit outside, a
    # window reveal cut inwards, and an IFC material layer set face the right way.
    outside: Literal["lo", "hi", "both"] | None = None
    openings: list[Opening] = Field(default_factory=list)

    @property
    def is_railing(self) -> bool:
        return self.kind == "railing"

    @property
    def thickness_ft(self) -> float:
        return (self.y1 - self.y0) if self.axis == "h" else (self.x1 - self.x0)

    @property
    def length_ft(self) -> float:
        return (self.x1 - self.x0) if self.axis == "h" else (self.y1 - self.y0)

    def u_range(self) -> tuple[float, float]:
        return (self.x0, self.x1) if self.axis == "h" else (self.y0, self.y1)


class Flight(BaseModel):
    """One straight run of treads, as its footprint rectangle in plan feet."""

    axis: Axis  # the axis the tread lines run along
    x0: float
    y0: float
    x1: float
    y1: float
    treads: int
    going_ft: float  # measured: the tread depth, from the drawn pitch
    width_ft: float  # measured: how wide the flight is to walk up
    up: Literal["+x", "-x", "+y", "-y"] = "+x"


class Stair(BaseModel):
    """A stair: its flights, and the shaft they wrap around if they do.

    The riser is deliberately absent. It cannot be measured from a plan, only
    derived once a storey height is known, so it is worked out at build time
    and attributed there rather than looking like something the drawing said.
    """

    id: str
    flights: list[Flight] = Field(default_factory=list)
    well: tuple[float, float, float, float] | None = None
    treads: int = 0


class Column(BaseModel):
    id: str
    x0: float
    y0: float
    x1: float
    y1: float


class Room(BaseModel):
    name: str
    x0: float
    y0: float
    x1: float
    y1: float
    label_ft: tuple[float, float] | None = None  # dimensions read off the label
    measured_ft: tuple[float, float] | None = None  # dimensions measured on the sheet


class ScaleInfo(BaseModel):
    """How the drawing was calibrated, and how much to trust it."""

    px_per_ft: float
    method: str  # room_labels | wall_thickness | overall_dimension | manual
    confidence: Literal["high", "medium", "low"] = "low"
    samples: int = 0
    spread_pct: float = 0.0
    note: str = ""


class PlanExtract(BaseModel):
    """Everything read off one floor-plan sheet, in plan feet."""

    sheet_id: str
    level: int
    level_name: str
    scale: ScaleInfo
    origin_px: tuple[float, float]  # plan-space origin in PDF points
    bounds: tuple[float, float, float, float]  # x0,y0,x1,y1 in plan ft
    walls: list[Wall] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    stairs: list[Stair] = Field(default_factory=list)
    north_deg: float | None = None  # compass bearing of plan +x, degrees CW from north
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# sheets and jobs
# --------------------------------------------------------------------------- #
class SheetInfo(BaseModel):
    id: str
    filename: str
    kind: str
    kind_label: str
    level: int | None
    evidence: str
    include: bool  # feeds the 3D model
    page_width: float
    page_height: float
    n_segments: int
    n_words: int
    extract_ok: bool = False
    extract_note: str = ""


class LevelParams(BaseModel):
    """Vertical parameters for one storey.

    A *plan* carries none of this, but a drawing *set* may: a section states a
    slab, an elevation states a floor level. So each number keeps a
    :class:`~app.datum.Quantity` saying where it came from, and ``user_set``
    lists the ones you typed, which no later reading is allowed to overwrite.
    """

    level: int
    name: str
    floor_to_floor_ft: float = 10.0
    wall_height_ft: float | None = None  # None → floor_to_floor − slab
    window_sill_ft: float = 3.0
    window_head_ft: float = 7.0
    door_head_ft: float = 7.0
    slab_thickness_ft: float = 5.0 / 12.0
    ffl_ft: float | None = None  # finished floor level above datum, once measured
    include: bool = True
    provenance: dict[str, Quantity] = Field(default_factory=dict)
    user_set: list[str] = Field(default_factory=list)


class FinishParams(BaseModel):
    """What the building is made of. Not in any drawing — entirely your choice."""

    preset: str = "elevation_spec"
    # slot → [colour, texture]; anything absent falls back to the preset
    slots: dict[str, list[str | None]] = Field(default_factory=dict)

    def resolved(self) -> dict[str, tuple[str, str | None]]:
        from .finish import preset_slots

        out = preset_slots(self.preset)
        for key, value in self.slots.items():
            if key not in out or not value:
                continue
            color = value[0] or out[key][0]
            texture = value[1] if len(value) > 1 else out[key][1]
            out[key] = (color, texture)
        return out


class DetailParams(BaseModel):
    """The relief on the facade.

    A plain extrusion of a floor plan is a box with holes in it, and good
    lighting has nothing to catch on it. What makes an elevation read as a
    building is the small stuff that projects: a reveal around every opening, a
    sill, a sunshade, a band at each floor. None of it is in a plan, so all of
    it is a choice — but the defaults are the ones this kind of house is
    actually built with.
    """

    enabled: bool = True
    reveal_ft: float = 0.25  # how far the glass sits back from the outer face
    frames: bool = True
    frame_ft: float = 0.22  # width of the frame around an opening
    sills: bool = True
    sill_projection_ft: float = 0.25
    chajjas: bool = True  # the sunshade over a window or door
    chajja_ft: float | None = None  # None → the projection measured off the set
    #: Curtains behind the glass, drawn across the opening. A window with
    #: nothing behind it is a hole into an unmodelled interior and the eye goes
    #: straight to it; closing it is the point, so they are drawn rather than
    #: tied back at the sides.
    curtains: bool = True
    floor_bands: bool = True
    band_projection_ft: float = 0.17
    coping: bool = True  # the cap course on top of the parapet
    balustrades: bool = True  # railings as glass and rail, not a solid slab


PanelKind = Literal[
    "field",    # the wall itself, at lvl 0
    "recess",   # set back — a balcony void, an entry, a shadow joint
    "clad",     # timber facing, flat against whatever it is on
    "band",     # a horizontal band at a floor line
    "slab",     # a balcony's own soffit and top edge, projecting
    "mass",     # a whole bay pushed forward — the biggest move on the facade
    "fin",      # one blade of a vertical screen
    "post",     # a slim column holding the canopy off the wall
    "frame",    # a surround standing proud of an opening
    "canopy",   # the deep plane across the top
    "pier",
]


class Panel(BaseModel):
    """One area of the front elevation: a rectangle, a material, a depth.

    ``depth_ft`` is the ``lvl`` tag the supplied elevation document writes on
    every coloured area — how far this piece stands proud of the wall behind
    it. Positive projects towards the street, negative sets back into a recess,
    zero is the wall face itself.

    ``u`` runs along the face from its left-hand end as seen from the street;
    ``z`` is height above the surrounding ground.
    """

    id: str
    kind: PanelKind
    u0: float
    u1: float
    z0: float
    z1: float
    depth_ft: float = 0.0
    material: str = "wall_ext"
    label: str = ""
    #: an opening left through the panel, as ``(u0, u1, z0, z1)``. A box frame
    #: is a surround, not a plate: filled in, it bricks up the window it was
    #: drawn around.
    hole: tuple[float, float, float, float] | None = None

    @property
    def width_ft(self) -> float:
        return self.u1 - self.u0

    @property
    def height_ft(self) -> float:
        return self.z1 - self.z0


class FacadeParams(BaseModel):
    """How the front is composed. Every one of these is a design choice.

    The defaults reproduce the vocabulary of the supplied document's renders —
    fins, box frames, bands, a clad entrance bay and a deep canopy — sized as
    fractions of the building rather than as fixed numbers, so the composition
    fits whatever plan it is handed.
    """

    enabled: bool = True
    #: which arrangement to lay out; see app.facade.ARRANGEMENTS
    arrangement: str = "layered"
    #: which end the tall fin screen stands at, looking from the street
    screen_side: Literal["left", "right", "auto"] = "auto"
    #: composed once and then editable; empty means compose from the model
    panels: list[Panel] = Field(default_factory=list)

    bands: bool = True
    band_height_ft: float = 1.0
    box_frames: bool = True
    frame_margin_ft: float = 0.6
    #: the recessed balcony between the screen and the projecting mass
    void: bool = True
    void_depth_ft: float = 1.6
    #: the clad bay pushed forward — the strongest element after the screen
    mass: bool = True
    mass_projection_ft: float = 1.3
    #: the vertical screen: full height, standing clear of everything else
    fins: bool = True
    fin_width_ft: float = 0.42
    fin_pitch_ft: float = 0.72
    fin_projection_ft: float = 2.0
    screen_width_ft: float = 4.0
    canopy: bool = True
    canopy_projection_ft: float = 3.15  # the document's own lvl +3'2"
    canopy_thickness_ft: float = 0.9
    canopy_side_ft: float = 1.0


class SiteParams(BaseModel):
    """The setting the house is shown in. None of it is in the drawings.

    The default is a street frontage, not a compound in a garden: the house
    stands on the road the plan names, its car port opens straight onto it, and
    a compound wall closes the frontage either side of the building. Nothing
    stands between the camera and the front of the house — no forecourt, no
    lawn, no trees, no parked car. Those are all still here and still work;
    they are simply off, because each of them got in front of the elevation,
    which is the one thing a client is looking at.
    """

    enabled: bool = True
    ground: bool = True
    #: the four-sided compound with a gate. Off: it rings the house in and the
    #: run across the front stands in the elevation.
    boundary_wall: bool = False
    boundary_height_ft: float = 6.0
    boundary_thickness_ft: float = 0.75
    gate_width_ft: float = 12.0
    #: the compound wall along the road only, stopping where the building
    #: itself meets it — so the frontage is closed but the facade is not
    road_wall: bool = True
    road_wall_height_ft: float = 6.0
    #: The house sits on the road, as the reference elevation shows it. Any
    #: setback here becomes paving in front of the elevation.
    front_setback_ft: float = 0.0
    side_setback_ft: float = 8.0
    verge_ft: float = 10.0
    #: a lawn round the plot. Off by default: the reference elevation puts the
    #: house on the street, and grass between the camera and the front of the
    #: building is the thing nobody asked for.
    lawn: bool = False
    #: paving between the house and the kerb. Off: with the house on the road
    #: there is nothing for it to cover, and it read as a patio.
    forecourt: bool = False
    driveway: bool = False
    trees: int = 0
    tree_height_ft: float = 14.0
    cars: int = 0
    # the street in front: its footpath, kerb and carriageway. The footpath is
    # part of the street, not of the plot — it runs the length of the road past
    # the frontage, outside the wall. That is what tells it from a forecourt.
    road: bool = True
    road_width_ft: float = 26.0
    footpath_ft: float = 8.0
    kerb_ft: float = 0.5


class BuildParams(BaseModel):
    """Everything the plans cannot tell you."""

    plinth_ft: float = 2.0  # ground floor level above surrounding ground
    parapet_ft: float = 3.0
    parapet_thickness_ft: float = 5.0 / 12.0
    railing_ft: float = 3.5  # balcony and stairwell guards
    roof: Literal["flat_parapet", "flat", "none"] = "flat_parapet"
    # None → the top storey's slab thickness, which is what a roof slab was
    # silently borrowing before it could be asked about separately
    roof_slab_thickness_ft: float | None = None
    # Facts the structural sheets state outright. None until a reader finds one,
    # so they are absent rather than invented when the set does not include them.
    plinth_beam_depth_ft: float | None = None
    slab_projection_ft: float | None = None
    excavation_depth_ft: float | None = None
    levels: list[LevelParams] = Field(default_factory=list)
    columns: bool = True
    glazing: bool = True
    doors: bool = True
    ground: bool = True
    ground_margin_ft: float = 12.0
    align_north: bool = True
    units: Literal["m", "ft"] = "m"
    #: the light the model is shown in; see app.sky.SKIES
    sky: Literal["dawn", "morning", "noon"] = "morning"
    finish: FinishParams = Field(default_factory=FinishParams)
    site: SiteParams = Field(default_factory=SiteParams)
    detail: DetailParams = Field(default_factory=DetailParams)
    facade: FacadeParams = Field(default_factory=FacadeParams)
    provenance: dict[str, Quantity] = Field(default_factory=dict)
    user_set: list[str] = Field(default_factory=list)

    def level(self, idx: int) -> LevelParams | None:
        for lp in self.levels:
            if lp.level == idx:
                return lp
        return None


#: Bumped when a *default* changes in a way that ought to reach jobs already on
#: disk. Changing a default never touches a saved job — its old value is written
#: into state.json — so without this the compound wall and the trees stayed on
#: every set made before they were switched off, which is exactly what happened.
SCHEMA_VERSION = 4


class JobState(BaseModel):
    id: str
    created: str
    title: str = ""
    schema_version: int = 0  # 0 = saved before versioning; see storage.load_state
    sheets: list[SheetInfo] = Field(default_factory=list)
    extracts: dict[str, PlanExtract] = Field(default_factory=dict)  # sheet_id → extract
    params: BuildParams = Field(default_factory=BuildParams)
    build: dict | None = None  # summary of the last 3D build


def ft(v: float) -> float:
    """Feet → metres, the single conversion point in the codebase."""
    return v * FT_TO_M
