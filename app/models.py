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
    """Vertical parameters for one storey. A plan carries none of this."""

    level: int
    name: str
    floor_to_floor_ft: float = 10.0
    wall_height_ft: float | None = None  # None → floor_to_floor − slab
    window_sill_ft: float = 3.0
    window_head_ft: float = 7.0
    door_head_ft: float = 7.0
    slab_thickness_ft: float = 5.0 / 12.0
    include: bool = True


class FinishParams(BaseModel):
    """What the building is made of. Not in any drawing — entirely your choice."""

    preset: str = "plaster_stone"
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


class SiteParams(BaseModel):
    """The compound and its staging. None of it is measured from the drawings."""

    enabled: bool = True
    ground: bool = True
    boundary_wall: bool = True
    boundary_height_ft: float = 6.0
    boundary_thickness_ft: float = 0.75
    gate_width_ft: float = 12.0
    front_setback_ft: float = 16.0
    side_setback_ft: float = 8.0
    verge_ft: float = 10.0
    driveway: bool = True
    trees: int = 4
    tree_height_ft: float = 14.0
    cars: int = 1


class BuildParams(BaseModel):
    """Everything the plans cannot tell you."""

    plinth_ft: float = 2.0  # ground floor level above surrounding ground
    parapet_ft: float = 3.0
    parapet_thickness_ft: float = 5.0 / 12.0
    railing_ft: float = 3.5  # balcony and stairwell guards
    roof: Literal["flat_parapet", "flat", "none"] = "flat_parapet"
    levels: list[LevelParams] = Field(default_factory=list)
    columns: bool = True
    glazing: bool = True
    doors: bool = True
    ground: bool = True
    ground_margin_ft: float = 12.0
    align_north: bool = True
    units: Literal["m", "ft"] = "m"
    finish: FinishParams = Field(default_factory=FinishParams)
    site: SiteParams = Field(default_factory=SiteParams)

    def level(self, idx: int) -> LevelParams | None:
        for lp in self.levels:
            if lp.level == idx:
                return lp
        return None


class JobState(BaseModel):
    id: str
    created: str
    title: str = ""
    sheets: list[SheetInfo] = Field(default_factory=list)
    extracts: dict[str, PlanExtract] = Field(default_factory=dict)  # sheet_id → extract
    params: BuildParams = Field(default_factory=BuildParams)
    build: dict | None = None  # summary of the last 3D build


def ft(v: float) -> float:
    """Feet → metres, the single conversion point in the codebase."""
    return v * FT_TO_M
