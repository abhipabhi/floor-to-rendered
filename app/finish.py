"""What the building is made of, and what stands around it.

None of this is in a floor plan, so all of it is a choice — the same status as
the heights in step 4. It is kept apart from the geometry so that changing a
colour never re-reads a drawing, and so the exported model.json records exactly
which finish produced the file.
"""

from __future__ import annotations

from .mesh import Material

# --------------------------------------------------------------------------- #
# surfaces the user can dress
# --------------------------------------------------------------------------- #
SLOTS = [
    ("wall_ext", "External walls"),
    ("wall_int", "Internal walls"),
    ("base", "Plinth band"),
    ("trim", "Parapet and bands"),
    ("roof", "Roof surface"),
    ("slab", "Floor slabs and soffits"),
    ("column", "Exposed columns"),
    ("stair", "Stairs"),
    ("door", "Doors"),
    ("glazing", "Glazing"),
    ("railing", "Railings"),
    ("ground", "Ground"),
    ("drive", "Driveway and paving"),
    ("boundary", "Boundary wall"),
]

# tile size in metres for each texture, so a brick is brick-sized
TILE_M = {
    "brick": 0.90,
    "stone": 1.20,
    "plaster": 2.40,
    "concrete": 2.40,
    "screed": 2.00,
    "wood": 1.10,
    "paving": 1.60,
    "asphalt": 2.50,
    "grass": 2.00,
    "foliage": 0.60,
    "bark": 0.50,
    "metal": 1.00,
    "tile_roof": 1.00,
}


def _m(
    name: str,
    color: str,
    texture: str | None,
    roughness: float = 0.9,
    metallic: float = 0.0,
    alpha: float = 1.0,
) -> Material:
    return Material(
        name=name,
        color=hex_to_rgb(color),
        texture=None if texture in (None, "none") else texture,
        tile_m=TILE_M.get(texture or "", 1.0),
        roughness=roughness,
        metallic=metallic,
        alpha=alpha,
    )


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """``#RRGGBB`` to linear-ish floats. glTF wants linear; sRGB is close
    enough for flat colours and keeps the picker honest about what you chose."""
    v = (value or "#ffffff").lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        r, g, b = (int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (1.0, 1.0, 1.0)
    # sRGB → linear, so what you pick is what you see once tone mapped
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (lin(r), lin(g), lin(b))


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
PRESETS: dict[str, dict] = {
    "plaster_stone": {
        "label": "Plaster & stone",
        "note": "Warm render with a stone base and timber doors.",
        "slots": {
            "wall_ext": ("#EDE7DC", "plaster"),
            "wall_int": ("#F2EFE9", "plaster"),
            "base": ("#FFFFFF", "stone"),
            "trim": ("#8C8A85", "plaster"),
            "roof": ("#CFCBC3", "screed"),
            "slab": ("#DEDAD2", "concrete"),
            "stair": ("#D6D2CA", "concrete"),
            "column": ("#9A9894", "concrete"),
            "door": ("#FFFFFF", "wood"),
            "glazing": ("#9FC4D6", None),
            "railing": ("#B7C3CB", None),
            "ground": ("#FFFFFF", "grass"),
            "drive": ("#FFFFFF", "paving"),
            "boundary": ("#E4DED2", "plaster"),
        },
    },
    "brick": {
        "label": "Exposed brick",
        "note": "Brickwork with concrete bands and dark joinery.",
        "slots": {
            "wall_ext": ("#FFFFFF", "brick"),
            "wall_int": ("#F0EDE7", "plaster"),
            "base": ("#FFFFFF", "stone"),
            "trim": ("#D8D4CC", "concrete"),
            "roof": ("#C6C2BA", "screed"),
            "slab": ("#D5D1C9", "concrete"),
            "stair": ("#CDC9C1", "concrete"),
            "column": ("#C9C5BD", "concrete"),
            "door": ("#6E4A2C", "wood"),
            "glazing": ("#93B9CC", None),
            "railing": ("#4A4E52", None),
            "ground": ("#FFFFFF", "grass"),
            "drive": ("#FFFFFF", "paving"),
            "boundary": ("#FFFFFF", "brick"),
        },
    },
    "modern_white": {
        "label": "Modern white",
        "note": "Flat white render, charcoal trim, glass balustrades.",
        "slots": {
            "wall_ext": ("#F6F4F1", "plaster"),
            "wall_int": ("#FAF9F7", "plaster"),
            "base": ("#5E6166", "concrete"),
            "trim": ("#43474C", "plaster"),
            "roof": ("#C9C6C0", "screed"),
            "slab": ("#E3E0DB", "concrete"),
            "stair": ("#D8D5D0", "concrete"),
            "column": ("#5E6166", "concrete"),
            "door": ("#3A3E42", "wood"),
            "glazing": ("#A8CBDC", None),
            "railing": ("#9FB0BA", None),
            "ground": ("#FFFFFF", "grass"),
            "drive": ("#FFFFFF", "paving"),
            "boundary": ("#EDEAE5", "plaster"),
        },
    },
    "sandstone": {
        "label": "Sandstone",
        "note": "Warm sand render with terracotta accents.",
        "slots": {
            "wall_ext": ("#E4CFA8", "plaster"),
            "wall_int": ("#F3EADA", "plaster"),
            "base": ("#B99A6B", "stone"),
            "trim": ("#A8875E", "plaster"),
            "roof": ("#C7B79A", "screed"),
            "slab": ("#DCCFB4", "concrete"),
            "stair": ("#D2C5AA", "concrete"),
            "column": ("#B3A183", "concrete"),
            "door": ("#7A4B24", "wood"),
            "glazing": ("#A6C6D2", None),
            "railing": ("#8A7A63", None),
            "ground": ("#FFFFFF", "grass"),
            "drive": ("#FFFFFF", "paving"),
            "boundary": ("#DFC9A2", "plaster"),
        },
    },
}

DEFAULT_PRESET = "plaster_stone"

ROUGHNESS = {
    "glazing": 0.08,
    "railing": 0.25,
    "door": 0.55,
    "roof": 0.95,
    "ground": 1.0,
    "drive": 0.95,
}
METALLIC = {"railing": 0.6}
ALPHA = {"glazing": 0.32, "railing": 0.55}


def materials_for(slots: dict[str, tuple[str, str | None]]) -> dict[str, Material]:
    """Build the material table the mesh layer refers to by name."""
    out: dict[str, Material] = {}
    for key, _label in SLOTS:
        color, texture = slots.get(key, ("#CCCCCC", None))
        out[key] = _m(
            key,
            color,
            texture,
            roughness=ROUGHNESS.get(key, 0.9),
            metallic=METALLIC.get(key, 0.0),
            alpha=ALPHA.get(key, 1.0),
        )
    # site extras that are not user-facing slots
    out["foliage"] = _m("foliage", "#FFFFFF", "foliage", roughness=0.95)
    out["bark"] = _m("bark", "#FFFFFF", "bark", roughness=0.95)
    out["car_body"] = Material(
        "car_body", hex_to_rgb("#C8CCD2"), metallic=0.55, roughness=0.28
    )
    out["car_glass"] = Material(
        "car_glass", hex_to_rgb("#2A3138"), metallic=0.2, roughness=0.12, alpha=0.75
    )
    out["car_tyre"] = Material("car_tyre", hex_to_rgb("#1A1C1E"), roughness=0.95)
    out["gate"] = Material(
        "gate", hex_to_rgb("#3A3F44"), metallic=0.7, roughness=0.35
    )
    return out


def preset_slots(name: str) -> dict[str, tuple[str, str | None]]:
    preset = PRESETS.get(name) or PRESETS[DEFAULT_PRESET]
    return {k: tuple(v) for k, v in preset["slots"].items()}  # type: ignore[misc]
