"""The elevation document: the façade drawn the way the supplied sheet draws it.

One layout, two outputs. The interactive view in the browser and the exported
PDF are the same drawing — panels filled in their own material colours, a
``lvl +X'Y"`` tag on each saying how far it stands proud, and dimension chains
down the side and along the bottom. Rendering them from one description is what
keeps the drawing the client signs off identical to the one on screen.

Everything is laid out in **paper points**, y downwards, so both renderers can
take it literally.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .finish import PRESETS, preset_slots
from .models import BuildParams, Panel
from .units import fmt_ft

#: the drawing's own ink
INK = "#1b1c1e"
DIM = "#2f6fd0"      # dimension lines and figures, blue as on the sheet
LVL = "#c0392b"      # the lvl tags, red as on the sheet
FAINT = "#9aa0a8"

#: paper
PAGE_W, PAGE_H = 1191.0, 842.0   # A3 landscape, the sheet's own size
MARGIN = 54.0


@dataclass
class Draw:
    """A page as primitives, in points with y down."""

    rects: list[tuple] = field(default_factory=list)   # x0,y0,x1,y1,fill,stroke,w
    lines: list[tuple] = field(default_factory=list)   # x0,y0,x1,y1,colour,w,dash
    texts: list[tuple] = field(default_factory=list)   # x,y,text,size,colour,anchor
    width: float = PAGE_W
    height: float = PAGE_H

    def rect(self, x0, y0, x1, y1, fill=None, stroke=None, w=0.6):
        self.rects.append((x0, y0, x1, y1, fill, stroke, w))

    def line(self, x0, y0, x1, y1, colour=INK, w=0.5, dash=False):
        self.lines.append((x0, y0, x1, y1, colour, w, dash))

    def text(self, x, y, s, size=7.0, colour=INK, anchor="start"):
        if s:
            self.texts.append((x, y, str(s), size, colour, anchor))


# --------------------------------------------------------------------------- #
# the drawing
# --------------------------------------------------------------------------- #
def _colours(params: BuildParams) -> dict[str, str]:
    slots = preset_slots(params.finish.preset)
    for key, value in (params.finish.slots or {}).items():
        if value and value[0]:
            slots[key] = (value[0], slots.get(key, (None, None))[1])
    return {k: v[0] for k, v in slots.items()}


def elevation(
    panels: list[Panel],
    frame,
    params: BuildParams,
    title: str = "FRONT ELEVATION",
    subtitle: str = "",
) -> Draw:
    """The dimensioned front elevation."""
    d = Draw()
    colours = _colours(params)

    # fit the elevation into the sheet, leaving room for the chains and a legend
    left, right = MARGIN + 96, PAGE_W - MARGIN - 250
    top, bottom = MARGIN + 58, PAGE_H - MARGIN - 76
    fw = max(frame.width, 1e-6)
    fh = max(frame.z_top - frame.z_ground, 1e-6)
    s = min((right - left) / fw, (bottom - top) / fh)
    ox = left + ((right - left) - fw * s) / 2
    oy = bottom

    def px(u: float) -> float:
        return ox + (u - frame.u0) * s

    def py(z: float) -> float:
        return oy - (z - frame.z_ground) * s

    # panels, back to front, each in its own material colour
    for p in panels:
        fill = colours.get(p.material, "#cccccc")
        x0, y0, x1, y1 = px(p.u0), py(p.z1), px(p.u1), py(p.z0)
        if p.hole:
            hu0, hu1, hz0, hz1 = p.hole
            for a, b, c, e in (
                (p.u0, hu0, p.z0, p.z1), (hu1, p.u1, p.z0, p.z1),
                (hu0, hu1, hz1, p.z1), (hu0, hu1, p.z0, hz0),
            ):
                if b - a > 0.01 and e - c > 0.01:
                    d.rect(px(a), py(e), px(b), py(c), fill, INK, 0.4)
        else:
            d.rect(x0, y0, x1, y1, fill, INK, 0.4 if p.kind != "field" else 0.7)

    # the lvl tag on every panel that stands proud, exactly as the sheet does it
    for p in panels:
        if abs(p.depth_ft) < 0.01 and p.kind != "field":
            continue
        cx, cy = (px(p.u0) + px(p.u1)) / 2, (py(p.z0) + py(p.z1)) / 2
        if px(p.u1) - px(p.u0) < 26 or py(p.z0) - py(p.z1) < 11:
            continue
        d.text(cx, cy + 2.5, f"lvl {_lvl(p.depth_ft)}", 7.2, LVL, "middle")

    _height_chain(d, panels, frame, px, py)
    _width_chain(d, panels, frame, px, py)

    d.text(MARGIN, MARGIN + 18, title, 17.0, INK)
    if subtitle:
        d.text(MARGIN, MARGIN + 34, subtitle, 8.5, FAINT)
    d.text(PAGE_W - MARGIN, PAGE_H - MARGIN + 12,
           "All dimensions in feet and inches · lvl is projection from the wall face",
           7.0, FAINT, "end")
    _legend(d, panels, params, colours)
    return d


def _lvl(depth_ft: float) -> str:
    """A projection depth written the way the sheet writes it: ``+3'2"``."""
    s = fmt_ft(abs(depth_ft)).replace("-", "")
    return ("-" if depth_ft < -0.005 else "+") + s


def _levels(panels, frame) -> list[float]:
    """The horizontal lines worth dimensioning: floor bands and the top."""
    out = {round(frame.z_ground, 2), round(frame.z_top, 2)}
    for p in panels:
        if p.kind == "band":
            out.add(round((p.z0 + p.z1) / 2, 2))
    return sorted(out)


def _height_chain(d: Draw, panels, frame, px, py) -> None:
    """The vertical chain down the left, as on the supplied sheet."""
    xs = px(frame.u0) - 40
    levels = _levels(panels, frame)
    d.line(xs, py(levels[0]), xs, py(levels[-1]), DIM, 0.6)
    for a, b in zip(levels, levels[1:]):
        for z in (a, b):
            d.line(xs - 5, py(z), px(frame.u0), py(z), DIM, 0.35, dash=True)
        mid = (py(a) + py(b)) / 2
        d.text(xs - 6, mid + 3, fmt_ft(b - a), 8.0, DIM, "end")
        _arrow(d, xs, py(a), xs, py(b))


def _width_chain(d: Draw, panels, frame, px, py) -> None:
    """The horizontal chain along the bottom, off the panel edges."""
    ys = py(frame.z_ground) + 34
    edges = {round(frame.u0, 2), round(frame.u1, 2)}
    for p in panels:
        if p.kind in ("clad", "recess", "frame", "canopy"):
            edges.add(round(max(p.u0, frame.u0), 2))
            edges.add(round(min(p.u1, frame.u1), 2))
    cut = sorted(e for e in edges if frame.u0 - 0.01 <= e <= frame.u1 + 0.01)
    merged = [cut[0]]
    for e in cut[1:]:
        if e - merged[-1] > 0.9:
            merged.append(e)
    d.line(px(merged[0]), ys, px(merged[-1]), ys, DIM, 0.6)
    for a, b in zip(merged, merged[1:]):
        for u in (a, b):
            d.line(px(u), py(frame.z_ground), px(u), ys + 5, DIM, 0.35, dash=True)
        d.text((px(a) + px(b)) / 2, ys - 4, fmt_ft(b - a), 7.4, DIM, "middle")
        _arrow(d, px(a), ys, px(b), ys)
    d.text(px(merged[0]), ys + 26, f"OVERALL  {fmt_ft(frame.width)}", 8.5, DIM)


def _arrow(d: Draw, x0, y0, x1, y1) -> None:
    d.line(x0, y0, x1, y1, DIM, 0.5)
    t = 2.4
    if abs(x1 - x0) < 0.01:  # vertical
        for y in (y0, y1):
            d.line(x0 - t, y, x0 + t, y, DIM, 0.7)
    else:
        for x in (x0, x1):
            d.line(x, y0 - t, x, y0 + t, DIM, 0.7)


def _legend(d: Draw, panels, params: BuildParams, colours) -> None:
    """What each material is, and which panels use it."""
    x = PAGE_W - MARGIN - 218
    y = MARGIN + 64
    d.text(x, y - 14, "MATERIALS & PROJECTIONS", 9.0, INK)
    seen: dict[str, list[Panel]] = {}
    for p in panels:
        seen.setdefault(p.material, []).append(p)
    for material, group in seen.items():
        d.rect(x, y, x + 26, y + 18, colours.get(material, "#cccccc"), INK, 0.4)
        label = next((k for k, v in _MATERIAL_LABEL.items() if k == material), material)
        d.text(x + 33, y + 8, _MATERIAL_LABEL.get(label, material).upper(), 7.6, INK)
        d.text(x + 33, y + 17, colours.get(material, "").upper(), 6.8, FAINT)
        depths = sorted({_lvl(p.depth_ft) for p in group})
        d.text(x + 150, y + 12, ", ".join(depths[:3]), 7.0, LVL)
        y += 27
    note = PRESETS.get(params.finish.preset, {}).get("note", "")
    if note:
        d.text(x, y + 12, note, 7.0, FAINT)


_MATERIAL_LABEL = {
    "wall_ext": "Main wall finish",
    "trim": "Bands & box frames",
    "clad": "Vertical cladding",
    "accent": "Accent / canopy",
    "base": "Plinth",
    "frame": "Joinery",
    "glazing": "Glazing",
}


def palette(params: BuildParams) -> Draw:
    """The material and colour sheet — the document's second page."""
    d = Draw()
    colours = _colours(params)
    preset = PRESETS.get(params.finish.preset, {})
    d.text(MARGIN, MARGIN + 20, "MATERIAL & COLOUR PALETTE", 21.0, INK)
    d.text(MARGIN, MARGIN + 40, (preset.get("label") or "").upper(), 10.0, FAINT)
    d.text(MARGIN, MARGIN + 56, preset.get("note", ""), 8.5, FAINT)

    roles = [
        ("accent", "Roof, vertical elements, accent bands"),
        ("wall_ext", "Main wall finish"),
        ("trim", "Balcony, box frames, horizontal bands"),
        ("clad", "Vertical cladding, gate panels"),
        ("base", "Plinth"),
        ("glazing", "Glazing"),
    ]
    y = MARGIN + 96
    for slot, used_for in roles:
        hexv = colours.get(slot)
        if not hexv:
            continue
        d.rect(MARGIN, y, MARGIN + 132, y + 88, hexv, INK, 0.5)
        d.text(MARGIN + 150, y + 16, _MATERIAL_LABEL.get(slot, slot).upper(), 11.0, INK)
        d.text(MARGIN + 150, y + 32, f"HEX {hexv.upper()}", 8.5, FAINT)
        d.text(MARGIN + 150, y + 50, "USED FOR:", 8.0, INK)
        d.text(MARGIN + 150, y + 63, used_for, 8.5, FAINT)
        y += 104
        if y > PAGE_H - MARGIN - 100:
            break
    return d


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #
def to_svg(d: Draw) -> str:
    """The drawing as SVG, for the browser."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {d.width:.0f} {d.height:.0f}" '
        f'width="100%" style="background:#fff">'
    ]
    for x0, y0, x1, y1, fill, stroke, w in d.rects:
        parts.append(
            f'<rect x="{min(x0,x1):.2f}" y="{min(y0,y1):.2f}" '
            f'width="{abs(x1-x0):.2f}" height="{abs(y1-y0):.2f}" '
            f'fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{w}"/>'
        )
    for x0, y0, x1, y1, colour, w, dash in d.lines:
        da = ' stroke-dasharray="3 2"' if dash else ""
        parts.append(
            f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
            f'stroke="{colour}" stroke-width="{w}"{da}/>'
        )
    for x, y, s, size, colour, anchor in d.texts:
        parts.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{colour}" '
            f'text-anchor="{anchor}" font-family="ui-sans-serif,system-ui,sans-serif">'
            f"{_esc(s)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def to_pdf(pages: list[Draw]) -> bytes:
    """The drawing as a PDF, in the supplied document's page size."""
    import fitz

    doc = fitz.open()
    for d in pages:
        page = doc.new_page(width=d.width, height=d.height)
        for x0, y0, x1, y1, fill, stroke, w in d.rects:
            r = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            page.draw_rect(r, color=_rgb(stroke), fill=_rgb(fill), width=w)
        for x0, y0, x1, y1, colour, w, dash in d.lines:
            page.draw_line(fitz.Point(x0, y0), fitz.Point(x1, y1),
                           color=_rgb(colour), width=w,
                           dashes="[3 2] 0" if dash else None)
        for x, y, s, size, colour, anchor in d.texts:
            width = fitz.get_text_length(s, fontname="helv", fontsize=size)
            ax = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
            page.insert_text((ax, y), s, fontsize=size, color=_rgb(colour) or (0, 0, 0))
    out = doc.tobytes()
    doc.close()
    return out


def _rgb(value: str | None):
    if not value:
        return None
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return tuple(int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None
