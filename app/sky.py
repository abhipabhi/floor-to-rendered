"""The light the model is shown in.

One dictionary, three consumers: the Blender scene, the browser viewer and the
UI. They render with completely different machinery — Blender's Nishita sky is
a physical atmosphere model, three.js ships Preetham — so the two do not share
a parameter list, and hard-coding numbers in both is how they drift apart. Each
preset therefore states the *physical* facts once (where the sun is, how thick
the air is) and then carries the tuning each renderer needs beside them.

None of this is measured. It is presentation, and every figure is a judgement
about what a house looks good in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Sky:
    """One time of day, for both renderers."""

    name: str
    label: str
    blurb: str

    #: where the sun is. Elevation above the horizon and bearing clockwise from
    #: north — the same convention the compass on the sheet uses.
    elevation_deg: float
    bearing_deg: float

    #: Preetham, and **only** the viewer uses these — Blender's Nishita sky has
    #: no rayleigh term, it scatters from the air/dust/ozone densities below.
    #: That distinction matters: values that make Nishita look right are far too
    #: high for Preetham, whose output is already enormous, and ACES then maps
    #: the lot to grey. Keep these low.
    turbidity: float
    rayleigh: float
    mie: float
    mie_g: float

    #: Nishita, for Blender. Densities are multiples of a clear sea-level
    #: atmosphere; dust is what reddens a low sun.
    air_density: float
    dust_density: float
    ozone_density: float

    #: the key light: colour as hex, strength in each renderer's own units
    sun_hex: str
    sun_energy: float       # Blender Sun lamp
    sun_intensity: float    # three.js DirectionalLight
    sun_angle_deg: float    # how soft the shadow edge is

    #: Ambient fill — the strength of the sky as a *light*. Too high and it
    #: drowns the sun and the model renders with no cast shadows at all.
    sky_strength: float
    #: The strength of the same sky as the *backdrop* the camera sees. Higher,
    #: because a sky dim enough to keep the shadows is a washed-out grey to look
    #: at. Blender gets both through a Light Path node; the viewer's dome is
    #: already only a backdrop, so it ignores this.
    backdrop_strength: float
    #: The viewer's equivalent knob. three.js r160 has `backgroundIntensity`,
    #: which scales the background without touching the lighting — the same
    #: split the Blender scene gets from a Light Path node. Without it the
    #: Preetham sky's very high dynamic range is tone-mapped straight to grey.
    background_intensity: float
    #: Saturation applied to the backdrop only. Blender renders through AgX,
    #: which is filmic and deliberately desaturates bright colours — beautiful
    #: on the building, and it turns the sky white. Pushing saturation on the
    #: camera path alone gives a blue sky and leaves the lighting untouched.
    #: (Switching the whole render to Standard also gives blue, and blows the
    #: road out to white — measured, not guessed.)
    backdrop_saturation: float
    hemi_sky_hex: str
    hemi_ground_hex: str
    hemi_intensity: float

    #: The cloud layer. A physical sky is a gradient and nothing else, and a
    #: bare gradient is most of what makes a render look like a render.
    #: `cover` is where the noise ramp starts — lower is more sky covered —
    #: and `softness` how quickly the edge comes in. `glow` is emission, and it
    #: carries them: the dome's normals all point inwards, so a cloud on it
    #: cannot be shaded by the sun the way a real one is, and lit only by the
    #: sky it comes out the grey of smoke.
    cloud_hex: str
    cloud_scale: float
    cloud_cover: float
    cloud_softness: float
    cloud_glow: float

    #: the land the model sits on
    ground_hex: str
    #: Exposure, twice, because the two renderers do not mean the same thing by
    #: the word: Blender's view transform takes **stops** with 0 as neutral,
    #: three.js takes a linear **multiplier** with 1.0 as neutral. One number
    #: used for both silently over- or under-exposes one of them.
    exposure_ev: float      # Blender, stops
    exposure: float         # three.js, multiplier

    def as_dict(self) -> dict:
        return asdict(self)


#: A low sun is the flattering one — it rakes across the facade and every
#: projection on it casts a shadow, which is the entire point of composing the
#: front as relief. Noon is included because clients ask for it, but it is the
#: least kind light this building will ever be in.
SKIES: dict[str, Sky] = {
    "dawn": Sky(
        name="dawn",
        label="Dawn",
        blurb="A low sun just off the horizon — long shadows, warm light.",
        elevation_deg=6.0,
        bearing_deg=84.0,
        turbidity=5.4,
        rayleigh=0.85,
        mie=0.010,
        mie_g=0.87,
        air_density=1.5,
        dust_density=3.4,
        ozone_density=1.6,
        sun_hex="#FFB067",
        sun_energy=3.6,
        sun_intensity=2.6,
        sun_angle_deg=1.4,
        sky_strength=0.42,
        backdrop_strength=1.5,
        background_intensity=0.3,
        backdrop_saturation=1.55,
        hemi_sky_hex="#F0C39B",
        hemi_ground_hex="#6A5A47",
        hemi_intensity=0.34,
        cloud_hex="#F6D9C4",
        cloud_scale=3.6,
        cloud_cover=0.42,
        cloud_softness=0.18,
        cloud_glow=1.05,
        ground_hex="#8E8A72",
        exposure_ev=0.45,
        exposure=1.12,
    ),
    "morning": Sky(
        name="morning",
        label="Morning",
        blurb="Mid-morning sun, clear air — the everyday presentation light.",
        elevation_deg=34.0,
        bearing_deg=118.0,
        turbidity=2.8,
        rayleigh=0.34,
        mie=0.005,
        mie_g=0.80,
        air_density=1.0,
        dust_density=1.0,
        ozone_density=1.0,
        sun_hex="#FFEACB",
        sun_energy=5.0,
        sun_intensity=3.0,
        sun_angle_deg=0.9,
        sky_strength=0.26,
        backdrop_strength=1.25,
        background_intensity=0.26,
        backdrop_saturation=1.95,
        hemi_sky_hex="#BCD6F2",
        hemi_ground_hex="#7D7565",
        hemi_intensity=0.28,
        cloud_hex="#FDFEFF",
        cloud_scale=4.2,
        cloud_cover=0.44,
        cloud_softness=0.16,
        cloud_glow=1.25,
        ground_hex="#A8B189",
        exposure_ev=0.0,
        exposure=1.0,
    ),
    "noon": Sky(
        name="noon",
        label="Noon",
        blurb="Sun overhead — bright, short shadows, the least flattering.",
        elevation_deg=72.0,
        bearing_deg=172.0,
        turbidity=2.1,
        rayleigh=0.26,
        mie=0.004,
        mie_g=0.78,
        air_density=0.9,
        dust_density=0.6,
        ozone_density=0.9,
        sun_hex="#FFF7EC",
        sun_energy=5.6,
        sun_intensity=3.3,
        sun_angle_deg=0.7,
        sky_strength=0.30,
        backdrop_strength=1.15,
        background_intensity=0.22,
        backdrop_saturation=1.8,
        hemi_sky_hex="#CFE3FA",
        hemi_ground_hex="#8B8474",
        hemi_intensity=0.32,
        cloud_hex="#FFFFFF",
        cloud_scale=4.8,
        cloud_cover=0.46,
        cloud_softness=0.15,
        cloud_glow=1.40,
        ground_hex="#AEB694",
        exposure_ev=-0.2,
        exposure=0.94,
    ),
}

DEFAULT = "morning"


def get(name: str | None) -> Sky:
    """The named sky, or the default — never a KeyError into a black render."""
    return SKIES.get((name or "").lower(), SKIES[DEFAULT])


def choices() -> list[dict]:
    """What the UI offers, in the order the day runs."""
    return [{"name": s.name, "label": s.label, "blurb": s.blurb}
            for s in SKIES.values()]
