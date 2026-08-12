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
    ("frame", "Window and door frames"),
    ("clad", "Façade cladding"),
    ("accent", "Façade accent"),
    ("door", "Doors"),
    ("glazing", "Glazing"),
    ("railing", "Railings"),
    ("ground", "Ground"),
    ("drive", "Driveway and paving"),
    ("road", "Road surface"),
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


_MEANS: dict[str, float] = {}


def texture_mean(name: str) -> float:
    """How bright a texture is on average, measured off the image itself.

    A texture multiplies the material colour, so a dark one darkens whatever it
    is put on: teak #8B5E3C is 0.245 in linear light and the wood grain averages
    0.279, which lands the two together at 0.068 — near black, nothing like the
    colour the palette names. Dividing the colour through by this makes the
    texture *modulate* around the stated colour instead of dimming it.

    Measured rather than tabulated. A table of these was written by hand first
    and four of the thirteen were wrong, which silently lifts by the wrong
    amount — and the whole point is that the stated hex is what you get.
    """
    if name not in _MEANS:
        import numpy as np

        from . import textures as tex

        try:
            import fitz

            pix = fitz.Pixmap(tex.png(name))
            buf = np.frombuffer(pix.samples, dtype=np.uint8)
            buf = buf.reshape(pix.height, pix.width, pix.n)[..., :3] / 255.0
            # Averaged in *linear* light, because that is where the multiply
            # happens: a renderer decodes an sRGB texture before combining it
            # with the base colour. Averaging the raw bytes instead reads
            # asphalt as 0.365 when it actually multiplies by 0.11, and the
            # correction comes out three times too small — which is how the
            # road stayed black after it had supposedly been fixed.
            lin = np.where(
                buf <= 0.04045, buf / 12.92, ((buf + 0.055) / 1.055) ** 2.4
            )
            _MEANS[name] = max(float(lin.mean()), 0.02)
        except Exception:
            _MEANS[name] = 1.0
    return _MEANS[name]


def _m(
    name: str,
    color: str,
    texture: str | None,
    roughness: float = 0.9,
    metallic: float = 0.0,
    alpha: float = 1.0,
) -> Material:
    tex = None if texture in (None, "none") else texture
    rgb = hex_to_rgb(color)
    if tex:
        # Clamped at 1.0, so the older presets — which say #FFFFFF and let a
        # dark texture supply the tone — come through exactly as they did.
        lift = 1.0 / texture_mean(tex)
        rgb = tuple(min(1.0, c * lift) for c in rgb)
    return Material(
        name=name,
        color=rgb,
        texture=tex,
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
    # The two below are drawn from contemporary Indian residential elevations:
    # a pale render carrying the mass, a darker base grounding it, timber or
    # stone as one warm accent, and dark slim joinery. What makes them read is
    # the *contrast between slots* — a facade in a single colour stays flat
    # however good the light is.
    # Straight off the supplied elevation document's palette sheet, roles and
    # all: dark grey for the roof, vertical elements and accent bands; light
    # grey for the main wall; warm white for balconies, box frames and
    # horizontal bands; teak for vertical cladding and gate panels.
    "elevation_spec": {
        "label": "Modern · elegant · timeless",
        "note": "Dark grey, light grey, warm white and teak — the document palette.",
        "slots": {
            "wall_ext": ("#D6D7D9", "plaster"),      # light grey, main wall
            "wall_int": ("#F4F4F2", "plaster"),
            "base": ("#3D4249", "concrete"),         # dark grey
            "trim": ("#F4F4F2", "concrete"),         # warm white bands and frames
            "roof": ("#3D4249", "screed"),           # dark grey roof
            "slab": ("#D6D7D9", "concrete"),
            "stair": ("#D6D7D9", "concrete"),
            "column": ("#F4F4F2", "plaster"),
            "clad": ("#8B5E3C", "wood"),
            "accent": ("#3D4249", "concrete"),
            "door": ("#8B5E3C", "wood"),             # teak
            "frame": ("#3D4249", None),
            "glazing": ("#8FA8B8", None),
            "railing": ("#3D4249", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#5E6167", "asphalt"),
            "boundary": ("#D6D7D9", "plaster"),
        },
    },
    "contemporary": {
        "label": "Contemporary",
        "note": "White render, charcoal joinery, stone base and timber doors.",
        "slots": {
            # trim carries every projecting thing — bands, sills, chajjas,
            # coping — so it has to sit clearly apart from the wall behind it.
            # Matched to the wall, all that relief goes invisible and the
            # facade is flat again however much of it was built.
            "wall_ext": ("#E7E0D5", "plaster"),
            "wall_int": ("#F7F4EF", "plaster"),
            "base": ("#5F5A55", "stone"),
            "trim": ("#FCFBF9", "concrete"),
            "roof": ("#B4AFA7", "screed"),
            "slab": ("#D3CFC8", "concrete"),
            "stair": ("#C8C3BB", "concrete"),
            "column": ("#FCFBF9", "plaster"),
            "clad": ("#7A5230", "wood"),
            "accent": ("#33373B", "concrete"),
            "door": ("#7A5230", "wood"),
            "frame": ("#33373B", None),
            "glazing": ("#8FB6CC", None),
            "railing": ("#3A3E42", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#6E7175", "asphalt"),
            "boundary": ("#E4E1DC", "plaster"),
        },
    },
    "tropical": {
        "label": "Tropical modern",
        "note": "Warm off-white with timber screens, exposed concrete bands.",
        "slots": {
            "wall_ext": ("#EDE8DE", "plaster"),
            "wall_int": ("#F7F3EB", "plaster"),
            "base": ("#8A8378", "stone"),
            "trim": ("#B9B3A7", "concrete"),
            "roof": ("#A9A398", "screed"),
            "slab": ("#C9C3B7", "concrete"),
            "stair": ("#BDB7AB", "concrete"),
            "column": ("#9E978B", "concrete"),
            "clad": ("#8A5A2B", "wood"),
            "accent": ("#5C5348", "concrete"),
            "door": ("#8A5A2B", "wood"),
            "frame": ("#4A3B2A", "wood"),
            "glazing": ("#9DC0D2", None),
            "railing": ("#5C4A34", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#6B6E72", "asphalt"),
            "boundary": ("#DED8CC", "plaster"),
        },
    },
    "plaster_stone": {
        "label": "Plaster & stone",
        "note": "Warm render with a stone base and timber doors.",
        "slots": {
            "wall_ext": ("#EDE7DC", "plaster"),
            "wall_int": ("#F2EFE9", "plaster"),
            "base": ("#9C968C", "stone"),
            "trim": ("#8C8A85", "plaster"),
            "roof": ("#CFCBC3", "screed"),
            "slab": ("#DEDAD2", "concrete"),
            "stair": ("#D6D2CA", "concrete"),
            "column": ("#9A9894", "concrete"),
            "clad": ("#8A6238", "wood"),
            "accent": ("#6E6B66", "concrete"),
            "door": ("#8A6238", "wood"),
            "frame": ("#5A5550", None),
            "glazing": ("#9FC4D6", None),
            "railing": ("#B7C3CB", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#71747A", "asphalt"),
            "boundary": ("#E4DED2", "plaster"),
        },
    },
    "brick": {
        "label": "Exposed brick",
        "note": "Brickwork with concrete bands and dark joinery.",
        "slots": {
            "wall_ext": ("#9B5A44", "brick"),
            "wall_int": ("#F0EDE7", "plaster"),
            "base": ("#9C968C", "stone"),
            "trim": ("#D8D4CC", "concrete"),
            "roof": ("#C6C2BA", "screed"),
            "slab": ("#D5D1C9", "concrete"),
            "stair": ("#CDC9C1", "concrete"),
            "column": ("#C9C5BD", "concrete"),
            "clad": ("#6E4A2C", "wood"),
            "accent": ("#3B3733", "concrete"),
            "door": ("#6E4A2C", "wood"),
            "frame": ("#3B3733", None),
            "glazing": ("#93B9CC", None),
            "railing": ("#4A4E52", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#6E7175", "asphalt"),
            "boundary": ("#9B5A44", "brick"),
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
            "clad": ("#6B5636", "wood"),
            "accent": ("#2E3236", "concrete"),
            "door": ("#3A3E42", "wood"),
            "frame": ("#2E3236", None),
            "glazing": ("#A8CBDC", None),
            "railing": ("#9FB0BA", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#70737A", "asphalt"),
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
            "clad": ("#7A4B24", "wood"),
            "accent": ("#7A6A50", "concrete"),
            "door": ("#7A4B24", "wood"),
            "frame": ("#6B563C", None),
            "glazing": ("#A6C6D2", None),
            "railing": ("#8A7A63", None),
            "ground": ("#7C9A5E", "grass"),
            "drive": ("#BFBBB4", "paving"),
            "road": ("#75726C", "asphalt"),
            "boundary": ("#DFC9A2", "plaster"),
        },
    },
}

DEFAULT_PRESET = "elevation_spec"

ROUGHNESS = {
    "glazing": 0.08,
    "frame": 0.45,
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
