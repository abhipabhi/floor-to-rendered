"""PDF → drawing primitives.

CAD-plotted PDFs carry the drawing as real vector paths, so nothing here guesses:
every wall line in the output is a line that exists in the file. This module is
the only place that touches PyMuPDF; everything downstream works on the plain
dataclasses below.

Coordinates are **display space**: the page as a reader sees it, with ``/Rotate``
already applied, origin top-left, x right, y down, in PDF points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import fitz  # PyMuPDF


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Seg:
    """A straight stroked segment."""

    x0: float
    y0: float
    x1: float
    y1: float
    color: str  # colour class: black / blue / red / grey / other
    width: float  # stroke width in points
    dashed: bool = False

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    @property
    def is_h(self) -> bool:
        return abs(self.y1 - self.y0) <= AXIS_TOL and self.length > 0

    @property
    def is_v(self) -> bool:
        return abs(self.x1 - self.x0) <= AXIS_TOL and self.length > 0


@dataclass(frozen=True)
class Fill:
    """A filled path, reduced to its bounding box plus how rectangular it is."""

    x0: float
    y0: float
    x1: float
    y1: float
    color: str
    rectangular: bool

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Word:
    """One whitespace-delimited text token with its box in display space."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def vertical(self) -> bool:
        """True when the token is drawn turned 90° on the sheet."""
        return (self.y1 - self.y0) > 1.8 * (self.x1 - self.x0)


@dataclass
class Sheet:
    """Everything extracted from one PDF page."""

    name: str
    width: float
    height: float
    segs: list[Seg] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


AXIS_TOL = 0.35  # points; CAD output is exact, this only absorbs float noise


# --------------------------------------------------------------------------- #
# colour classification
# --------------------------------------------------------------------------- #
def classify_color(c) -> str:
    """Map an RGB triple to the pen colours used on these sheets.

    Architectural CAD layers are colour-coded and that coding is the single most
    useful signal in the file: black is fabric, blue is glazing/dimensions, red
    is RCC (columns, ramps).
    """
    if c is None:
        return "none"
    try:
        r, g, b = float(c[0]), float(c[1]), float(c[2])
    except (TypeError, ValueError, IndexError):
        return "other"
    if max(r, g, b) < 0.35:
        return "black"
    if b > 0.45 and r < 0.45 and g < 0.45:
        return "blue"
    if r > 0.45 and g < 0.4 and b < 0.4:
        return "red"
    if abs(r - g) < 0.12 and abs(g - b) < 0.12:
        return "grey"
    return "other"


def _is_dashed(item) -> bool:
    d = item.get("dashes")
    if not d:
        return False
    d = str(d).strip()
    return d not in ("", "[] 0", "[]0", "[ ] 0")


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def _quad_points(q):
    return [q.ul, q.ur, q.lr, q.ll]


def extract_page(page: fitz.Page, name: str = "") -> Sheet:
    """Pull every stroked segment, filled path and text token off one page."""
    m = page.rotation_matrix  # unrotated page space → display space
    rect = page.rect
    sheet = Sheet(name=name, width=rect.width, height=rect.height)

    for item in page.get_drawings():
        stroke = classify_color(item.get("color"))
        fillc = classify_color(item.get("fill"))
        width = float(item.get("width") or 0.0)
        dashed = _is_dashed(item)
        kind = item.get("type", "s")
        pts: list[fitz.Point] = []

        for op in item["items"]:
            if op[0] == "l":
                a, b = op[1] * m, op[2] * m
                pts += [a, b]
                if stroke != "none":
                    sheet.segs.append(
                        Seg(a.x, a.y, b.x, b.y, stroke, width, dashed)
                    )
            elif op[0] == "re":
                r = op[1] * m
                corners = [
                    (r.x0, r.y0),
                    (r.x1, r.y0),
                    (r.x1, r.y1),
                    (r.x0, r.y1),
                ]
                pts += [fitz.Point(*c) for c in corners]
                if stroke != "none":
                    for i in range(4):
                        (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
                        sheet.segs.append(
                            Seg(ax, ay, bx, by, stroke, width, dashed)
                        )
            elif op[0] == "qu":
                q = op[1] * m
                corners = _quad_points(q)
                pts += corners
                if stroke != "none":
                    for i in range(4):
                        a, b = corners[i], corners[(i + 1) % 4]
                        sheet.segs.append(
                            Seg(a.x, a.y, b.x, b.y, stroke, width, dashed)
                        )
            elif op[0] == "c":
                # Béziers are only ever door swings / arcs here; chord it so the
                # bounding box is right and nothing downstream sees a curve.
                a, d = op[1] * m, op[4] * m
                pts += [a, op[2] * m, op[3] * m, d]
                if stroke != "none":
                    sheet.segs.append(Seg(a.x, a.y, d.x, d.y, stroke, width, True))

        if kind in ("f", "fs") and fillc != "none" and pts:
            xs = [p.x for p in pts]
            ys = [p.y for p in pts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            # "rectangular" = every vertex sits on the bounding box
            rectangular = all(
                (abs(p.x - x0) < 0.4 or abs(p.x - x1) < 0.4)
                and (abs(p.y - y0) < 0.4 or abs(p.y - y1) < 0.4)
                for p in pts
            )
            sheet.fills.append(Fill(x0, y0, x1, y1, fillc, rectangular))

    for w in page.get_text("words"):
        r = fitz.Rect(w[0], w[1], w[2], w[3]) * m
        sheet.words.append(Word(w[4], r.x0, r.y0, r.x1, r.y1))

    return sheet


def load_sheet(path: str, name: str | None = None) -> Sheet:
    """First page of a PDF as a :class:`Sheet`."""
    with fitz.open(path) as doc:
        if doc.page_count == 0:
            raise ValueError(f"{path}: no pages")
        return extract_page(doc[0], name or path)


def render_page_png(path: str, dpi: int = 130, clip: tuple | None = None) -> bytes:
    """The page as a PNG in display space — used for the verification overlay."""
    with fitz.open(path) as doc:
        page = doc[0]
        r = fitz.Rect(*clip) if clip else None
        pix = page.get_pixmap(dpi=dpi, clip=r)
        return pix.tobytes("png")
