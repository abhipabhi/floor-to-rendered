"""Seamless textures, generated rather than shipped.

Every texture here is drawn from numpy arrays at build time and encoded to PNG.
Nothing is downloaded, nothing is licensed from anywhere, and the whole set adds
a few hundred kilobytes to a .glb instead of tens of megabytes.

All of them tile: patterns are laid out modulo the image size and the noise is
made periodic by mirroring, so a wall of any length shows no seam.

Colour convention: the neutral finishes (plaster, concrete, screed, paving) are
generated near-white, so the colour you pick in the UI drives the hue through
glTF's baseColorFactor. The ones whose colour *is* the material — brick, stone,
wood, foliage — carry their own colour and default to a white factor.
"""

from __future__ import annotations

import numpy as np

SIZE = 256


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _periodic_noise(n: int, octaves: int = 4, seed: int = 0) -> np.ndarray:
    """Value noise that wraps, by generating at low resolution and tiling up."""
    rng = _rng(seed)
    out = np.zeros((n, n), dtype=np.float64)
    amplitude = 1.0
    total = 0.0
    for octave in range(octaves):
        cells = 2 ** (octave + 2)
        grid = rng.random((cells, cells))
        # bilinear upsample with wraparound
        ys = np.linspace(0, cells, n, endpoint=False)
        xs = np.linspace(0, cells, n, endpoint=False)
        y0 = np.floor(ys).astype(int) % cells
        x0 = np.floor(xs).astype(int) % cells
        y1 = (y0 + 1) % cells
        x1 = (x0 + 1) % cells
        fy = (ys - np.floor(ys))[:, None]
        fx = (xs - np.floor(xs))[None, :]
        fy = fy * fy * (3 - 2 * fy)
        fx = fx * fx * (3 - 2 * fx)
        top = grid[np.ix_(y0, x0)] * (1 - fx) + grid[np.ix_(y0, x1)] * fx
        bot = grid[np.ix_(y1, x0)] * (1 - fx) + grid[np.ix_(y1, x1)] * fx
        out += amplitude * (top * (1 - fy) + bot * fy)
        total += amplitude
        amplitude *= 0.5
    return out / total


def _to_rgb(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.clip(np.dstack([r, g, b]) * 255.0, 0, 255).astype(np.uint8)


def _tint(base: tuple[float, float, float], v: np.ndarray) -> np.ndarray:
    return _to_rgb(v * base[0], v * base[1], v * base[2])


# --------------------------------------------------------------------------- #
# the textures
# --------------------------------------------------------------------------- #
def plaster(n: int = SIZE) -> np.ndarray:
    """Smooth render — a hint of trowel mottling, nothing more."""
    v = 0.90 + 0.10 * _periodic_noise(n, 5, seed=1)
    grain = 0.02 * (_rng(2).random((n, n)) - 0.5)
    return _tint((1.0, 1.0, 1.0), v + grain)


def concrete(n: int = SIZE) -> np.ndarray:
    v = 0.86 + 0.12 * _periodic_noise(n, 5, seed=3)
    # faint pour mottling rather than blotches
    v = v - 0.05 * np.clip(_periodic_noise(n, 7, seed=4) - 0.55, 0, None) * 4
    grit = 0.03 * (_rng(6).random((n, n)) - 0.5)
    return _tint((1.0, 0.995, 0.98), v + grit)


def screed(n: int = SIZE) -> np.ndarray:
    """Roof finish: floated cement with faint panel joints."""
    v = 0.80 + 0.16 * _periodic_noise(n, 4, seed=5)
    yy, xx = np.mgrid[0:n, 0:n]
    joints = ((xx % (n // 2) < 2) | (yy % (n // 2) < 2)).astype(float)
    return _tint((1.0, 1.0, 0.99), v - 0.10 * joints)


def brick(n: int = SIZE) -> np.ndarray:
    """Running bond. Course and brick sizes divide the tile exactly, and the
    joints straddle the tile edge, so opposite edges match and it wraps."""
    courses, per_course = 8, 4
    ch, bw = n // courses, n // per_course
    yy, xx = np.mgrid[0:n, 0:n]
    row = yy // ch
    shifted = (xx + (row % 2) * (bw // 2)) % n
    col = shifted // bw

    joint = 3
    mortar_h = ((yy + joint // 2 + 1) % ch) < joint
    mortar_v = ((shifted + joint // 2 + 1) % bw) < joint
    mortar = mortar_h | mortar_v

    rng = _rng(11)
    jitter = rng.normal(0, 0.055, (courses + 1, per_course + 1))
    shade = 1.0 + jitter[row % (courses + 1), col % (per_course + 1)]
    grain = 0.10 * _periodic_noise(n, 5, seed=12)

    base = np.array([0.62, 0.30, 0.22])
    v = np.clip(shade * (0.92 + grain), 0.55, 1.35)
    r = base[0] * v
    g = base[1] * v
    b = base[2] * v
    m = np.array([0.78, 0.76, 0.72])
    mv = 0.92 + 0.12 * _periodic_noise(n, 4, seed=13)
    r = np.where(mortar, m[0] * mv, r)
    g = np.where(mortar, m[1] * mv, g)
    b = np.where(mortar, m[2] * mv, b)
    return _to_rgb(r, g, b)


def stone(n: int = SIZE) -> np.ndarray:
    """Coursed rubble cladding — the base band in the reference render."""
    courses = 8  # divides the tile, so the courses line up across a wrap
    ch = n // courses
    yy, xx = np.mgrid[0:n, 0:n]
    row = yy // ch
    rng = _rng(21)
    widths = rng.integers(n // 7, n // 3, size=(courses + 1, 12))
    offsets = np.cumsum(widths, axis=1) % n
    col = np.zeros_like(xx)
    for c in range(courses + 1):
        edges = np.sort(offsets[c])
        col = np.where(row % (courses + 1) == c, np.searchsorted(edges, xx), col)
    joint = ((yy + 2) % ch) < 3
    for c in range(courses + 1):
        edges = np.sort(offsets[c])
        # circular distance, so a joint near x = 0 also appears near x = n
        d = np.abs(xx[..., None] - edges[None, None, :])
        near = np.min(np.minimum(d, n - d), axis=2) < 2
        joint = joint | ((row % (courses + 1) == c) & near)

    jitter = rng.normal(0, 0.09, (courses + 1, 16))
    shade = 1.0 + jitter[row % (courses + 1), col % 16]
    grain = 0.14 * _periodic_noise(n, 6, seed=22)
    v = np.clip(shade * (0.88 + grain), 0.45, 1.3)
    base = np.array([0.52, 0.50, 0.47])
    r, g, b = base[0] * v, base[1] * v, base[2] * v
    r = np.where(joint, 0.38, r)
    g = np.where(joint, 0.37, g)
    b = np.where(joint, 0.35, b)
    return _to_rgb(r, g, b)


def wood(n: int = SIZE) -> np.ndarray:
    """Vertical grain, for door leaves and soffit slats."""
    yy, xx = np.mgrid[0:n, 0:n]
    warp = 8 * _periodic_noise(n, 4, seed=31)
    rings = np.sin((xx + warp) * 0.55) * 0.5 + 0.5
    fine = 0.20 * _periodic_noise(n, 6, seed=32)
    v = 0.72 + 0.28 * rings + fine - 0.1
    boards = ((xx % (n // 4)) < 2).astype(float)
    v = v - 0.18 * boards
    base = np.array([0.50, 0.31, 0.17])
    return _to_rgb(base[0] * v, base[1] * v, base[2] * v)


def paving(n: int = SIZE) -> np.ndarray:
    """Rectangular pavers for the drive and the approach."""
    pw, ph = n // 4, n // 8  # both divide the tile
    yy, xx = np.mgrid[0:n, 0:n]
    row = yy // ph
    shifted = (xx + (row % 2) * (pw // 2)) % n
    joint = (((yy + 2) % ph) < 3) | (((shifted + 2) % pw) < 3)
    rng = _rng(41)
    jitter = rng.normal(0, 0.05, (12, 8))
    shade = 1.0 + jitter[row % 12, (shifted // pw) % 8]
    v = np.clip(shade * (0.86 + 0.14 * _periodic_noise(n, 5, seed=42)), 0.5, 1.2)
    r, g, b = 0.80 * v, 0.79 * v, 0.76 * v
    r = np.where(joint, 0.55, r)
    g = np.where(joint, 0.55, g)
    b = np.where(joint, 0.53, b)
    return _to_rgb(r, g, b)


def asphalt(n: int = SIZE) -> np.ndarray:
    v = 0.30 + 0.12 * _periodic_noise(n, 6, seed=51)
    grit = 0.06 * (_rng(52).random((n, n)) - 0.5)
    return _tint((1.0, 1.0, 1.03), v + grit)


def grass(n: int = SIZE) -> np.ndarray:
    """Mown lawn — deliberately desaturated, so it sits next to the render
    rather than shouting over it."""
    clumps = _periodic_noise(n, 6, seed=61)
    blades = 0.09 * (_rng(62).random((n, n)) - 0.5)
    v = 0.62 + 0.38 * clumps + blades
    r = 0.40 * v + 0.06
    g = 0.53 * v + 0.08
    b = 0.30 * v + 0.05
    return _to_rgb(r, g, b)


def foliage(n: int = SIZE) -> np.ndarray:
    leaves = _periodic_noise(n, 7, seed=71)
    v = 0.45 + 0.55 * leaves
    return _to_rgb(0.22 * v + 0.03, 0.46 * v + 0.05, 0.20 * v + 0.02)


def bark(n: int = SIZE) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    ridges = np.sin(xx * 0.9 + 6 * _periodic_noise(n, 4, seed=81)) * 0.5 + 0.5
    v = 0.55 + 0.45 * ridges * (0.6 + 0.4 * _periodic_noise(n, 5, seed=82))
    return _to_rgb(0.34 * v, 0.26 * v, 0.20 * v)


def metal(n: int = SIZE) -> np.ndarray:
    v = 0.78 + 0.10 * _periodic_noise(n, 3, seed=91)
    return _tint((1.0, 1.0, 1.01), v)


def tile_roof(n: int = SIZE) -> np.ndarray:
    """Only used if someone picks it; the default roof here is screed."""
    rows = 8  # divides the tile
    rh = n // rows
    yy, _xx = np.mgrid[0:n, 0:n]
    wave = np.sin(((yy + 0.5) % rh) / rh * np.pi)
    v = 0.65 + 0.35 * wave
    return _to_rgb(0.62 * v, 0.30 * v, 0.22 * v)


GENERATORS = {
    "plaster": plaster,
    "concrete": concrete,
    "screed": screed,
    "brick": brick,
    "stone": stone,
    "wood": wood,
    "paving": paving,
    "asphalt": asphalt,
    "grass": grass,
    "foliage": foliage,
    "bark": bark,
    "metal": metal,
    "tile_roof": tile_roof,
}

# what the UI offers for each kind of surface, in a sensible order
WALL_TEXTURES = ["plaster", "brick", "stone", "concrete", "none"]
GROUND_TEXTURES = ["grass", "paving", "asphalt", "concrete", "none"]
FLAT_TEXTURES = ["screed", "concrete", "paving", "tile_roof", "none"]

_cache: dict[str, bytes] = {}


def png(name: str) -> bytes:
    """The texture as PNG bytes, generated once per process."""
    if name not in _cache:
        if name not in GENERATORS:
            raise KeyError(name)
        arr = GENERATORS[name]()
        _cache[name] = encode_png(arr)
    return _cache[name]


def encode_png(rgb: np.ndarray) -> bytes:
    """Encode an (h, w, 3) uint8 array as PNG, without a new dependency."""
    import fitz

    h, w, _ = rgb.shape
    pix = fitz.Pixmap(fitz.csRGB, w, h, bytes(np.ascontiguousarray(rgb)), False)
    return pix.tobytes("png")
