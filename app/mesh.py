"""Triangle meshes: boxes, and the few round things a site needs.

Most of this building is rectangular prisms — walls, piers, lintels, slabs,
columns, panes — so a box primitive with flat per-face normals is the whole
geometry kernel for the building itself. No CSG: an opening is made by *not
building* the boxes where the hole is, which keeps the mesh tiny and free of the
coincident faces that make a boolean result render badly.

Trees, cars and gate posts need curves, so there are cylinders, cones and
spheres too — kept low-poly, because they are staging, not survey.

Every vertex carries a UV derived from its position in metres and divided by the
material's tile size, so a brick reads the same size on a 3 m wall as on a 12 m
one and nothing has to be unwrapped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Material:
    name: str
    color: tuple[float, float, float]
    metallic: float = 0.0
    roughness: float = 0.85
    alpha: float = 1.0
    texture: str | None = None  # id in app.textures
    tile_m: float = 1.0  # metres covered by one repeat of the texture

    @property
    def blend(self) -> bool:
        return self.alpha < 1.0


@dataclass
class Mesh:
    """One named group of triangles sharing a material."""

    name: str
    material: str
    tile_m: float = 1.0
    positions: list[float] = field(default_factory=list)
    normals: list[float] = field(default_factory=list)
    uvs: list[float] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 3

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3

    # ----------------------------------------------------------------- faces
    def add_quad(self, p0, p1, p2, p3, n, uv=None) -> None:
        base = self.vertex_count
        pts = (p0, p1, p2, p3)
        if uv is None:
            uv = [self._planar_uv(p, n) for p in pts]
        for p, t in zip(pts, uv):
            self.positions += [float(p[0]), float(p[1]), float(p[2])]
            self.normals += [float(n[0]), float(n[1]), float(n[2])]
            self.uvs += [float(t[0]), float(t[1])]
        self.indices += [base, base + 1, base + 2, base, base + 2, base + 3]

    def add_tri(self, p0, p1, p2, n, uv=None) -> None:
        base = self.vertex_count
        pts = (p0, p1, p2)
        if uv is None:
            uv = [self._planar_uv(p, n) for p in pts]
        for p, t in zip(pts, uv):
            self.positions += [float(p[0]), float(p[1]), float(p[2])]
            self.normals += [float(n[0]), float(n[1]), float(n[2])]
            self.uvs += [float(t[0]), float(t[1])]
        self.indices += [base, base + 1, base + 2]

    def _planar_uv(self, p, n):
        """Project world position onto the plane of the face it belongs to."""
        ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
        if ay >= ax and ay >= az:
            u, v = p[0], p[2]
        elif ax >= az:
            u, v = p[2], p[1]
        else:
            u, v = p[0], p[1]
        s = self.tile_m or 1.0
        return (u / s, v / s)

    # ------------------------------------------------------------ primitives
    def add_box(
        self,
        x0: float,
        y0: float,
        z0: float,
        x1: float,
        y1: float,
        z1: float,
        eps: float = 1e-6,
    ) -> bool:
        """Axis-aligned box. Returns False (and adds nothing) if degenerate."""
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        z0, z1 = min(z0, z1), max(z0, z1)
        if x1 - x0 <= eps or y1 - y0 <= eps or z1 - z0 <= eps:
            return False
        a = (x0, y0, z0)
        b = (x1, y0, z0)
        c = (x1, y0, z1)
        d = (x0, y0, z1)
        e = (x0, y1, z0)
        f = (x1, y1, z0)
        g = (x1, y1, z1)
        h = (x0, y1, z1)
        self.add_quad(e, h, g, f, (0, 1, 0))  # top    +Y
        self.add_quad(a, b, c, d, (0, -1, 0))  # bottom −Y
        self.add_quad(a, e, f, b, (0, 0, -1))  # back   −Z
        self.add_quad(d, c, g, h, (0, 0, 1))  # front  +Z
        self.add_quad(a, d, h, e, (-1, 0, 0))  # left   −X
        self.add_quad(b, f, g, c, (1, 0, 0))  # right  +X
        return True

    def add_cylinder(
        self,
        cx: float,
        cz: float,
        y0: float,
        y1: float,
        r: float,
        segments: int = 12,
        r_top: float | None = None,
        caps: bool = True,
        axis: str = "y",
    ) -> None:
        """A cylinder or truncated cone, up ``axis`` (``y``, ``x`` or ``z``)."""
        if r <= 0 or abs(y1 - y0) <= 1e-6:
            return
        rt = r if r_top is None else r_top

        def point(angle: float, along: float, radius: float):
            u, v = math.cos(angle) * radius, math.sin(angle) * radius
            if axis == "y":
                return (cx + u, along, cz + v)
            if axis == "x":
                return (along, cx + u, cz + v)
            return (cx + u, cz + v, along)

        for i in range(segments):
            a0 = 2 * math.pi * i / segments
            a1 = 2 * math.pi * (i + 1) / segments
            p0, p1 = point(a0, y0, r), point(a1, y0, r)
            p2, p3 = point(a1, y1, rt), point(a0, y1, rt)
            mid = (a0 + a1) / 2
            if axis == "y":
                n = (math.cos(mid), 0.0, math.sin(mid))
            elif axis == "x":
                n = (0.0, math.cos(mid), math.sin(mid))
            else:
                n = (math.cos(mid), math.sin(mid), 0.0)
            span = abs(y1 - y0)
            uv = [
                ((i / segments) * 2 * math.pi * r / (self.tile_m or 1), 0.0),
                (((i + 1) / segments) * 2 * math.pi * r / (self.tile_m or 1), 0.0),
                (
                    ((i + 1) / segments) * 2 * math.pi * r / (self.tile_m or 1),
                    span / (self.tile_m or 1),
                ),
                (
                    (i / segments) * 2 * math.pi * r / (self.tile_m or 1),
                    span / (self.tile_m or 1),
                ),
            ]
            self.add_quad(p0, p1, p2, p3, n, uv)
            if caps:
                if axis == "y":
                    up, down = (0, 1, 0), (0, -1, 0)
                elif axis == "x":
                    up, down = (1, 0, 0), (-1, 0, 0)
                else:
                    up, down = (0, 0, 1), (0, 0, -1)
                if rt > 1e-6:
                    self.add_tri(point(0, y1, 0), p3, p2, up)
                if r > 1e-6:
                    self.add_tri(point(0, y0, 0), p1, p0, down)

    def add_cone(
        self, cx: float, cz: float, y0: float, y1: float, r: float, segments: int = 12
    ) -> None:
        self.add_cylinder(cx, cz, y0, y1, r, segments, r_top=0.0)

    def add_sphere(
        self, cx: float, cy: float, cz: float, r: float, segments: int = 10
    ) -> None:
        """A low-poly UV sphere — foliage, near enough."""
        rings = max(3, segments // 2)
        for j in range(rings):
            t0 = math.pi * j / rings
            t1 = math.pi * (j + 1) / rings
            for i in range(segments):
                a0 = 2 * math.pi * i / segments
                a1 = 2 * math.pi * (i + 1) / segments

                def pt(t, a):
                    return (
                        cx + r * math.sin(t) * math.cos(a),
                        cy + r * math.cos(t),
                        cz + r * math.sin(t) * math.sin(a),
                    )

                p0, p1, p2, p3 = pt(t0, a0), pt(t0, a1), pt(t1, a1), pt(t1, a0)
                n = (
                    (p0[0] + p2[0]) / 2 - cx,
                    (p0[1] + p2[1]) / 2 - cy,
                    (p0[2] + p2[2]) / 2 - cz,
                )
                ln = math.sqrt(sum(v * v for v in n)) or 1.0
                n = (n[0] / ln, n[1] / ln, n[2] / ln)
                uv = [
                    (i / segments, j / rings),
                    ((i + 1) / segments, j / rings),
                    ((i + 1) / segments, (j + 1) / rings),
                    (i / segments, (j + 1) / rings),
                ]
                self.add_quad(p0, p1, p2, p3, n, uv)

    # ---------------------------------------------------------------- bounds
    def bounds(self):
        if not self.positions:
            return None
        xs = self.positions[0::3]
        ys = self.positions[1::3]
        zs = self.positions[2::3]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


@dataclass
class Scene:
    """A named set of meshes plus the materials they refer to."""

    meshes: list[Mesh] = field(default_factory=list)
    materials: dict[str, Material] = field(default_factory=dict)

    def mesh(self, name: str, material: str) -> Mesh:
        for m in self.meshes:
            if m.name == name:
                return m
        mat = self.materials.get(material)
        m = Mesh(name=name, material=material, tile_m=mat.tile_m if mat else 1.0)
        self.meshes.append(m)
        return m

    def prune(self) -> None:
        self.meshes = [m for m in self.meshes if m.triangle_count]

    @property
    def triangle_count(self) -> int:
        return sum(m.triangle_count for m in self.meshes)

    @property
    def vertex_count(self) -> int:
        return sum(m.vertex_count for m in self.meshes)

    @property
    def textures(self) -> list[str]:
        """Texture ids actually used, in a stable order."""
        used = []
        for m in self.meshes:
            mat = self.materials.get(m.material)
            if mat and mat.texture and mat.texture not in used:
                used.append(mat.texture)
        return used

    def bounds(self):
        boxes = [m.bounds() for m in self.meshes if m.bounds()]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            min(b[2] for b in boxes),
            max(b[3] for b in boxes),
            max(b[4] for b in boxes),
            max(b[5] for b in boxes),
        )
