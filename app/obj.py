"""Wavefront OBJ/MTL writer — the universal fallback.

glb is the primary format, but every 3D package on earth reads OBJ, and OBJ is
plain text you can inspect. Groups become ``o`` objects so the outliner still
shows walls, slabs and glazing separately. OBJ is Y-up like glTF, so the
coordinates are written unchanged.

Textures are written next to the .obj as PNGs and referenced by ``map_Kd``,
because OBJ has no way to carry them inline.
"""

from __future__ import annotations

from . import textures as tex
from .mesh import Scene


def write_obj(scene: Scene, mtl_name: str = "model.mtl") -> str:
    scene.prune()
    out: list[str] = [
        "# floor-to-rendered",
        "# units: metres, Y up",
        f"mtllib {mtl_name}",
    ]
    voff = toff = 1
    for mesh in scene.meshes:
        has_uv = len(mesh.uvs) == 2 * mesh.vertex_count
        out.append(f"o {mesh.name}")
        out.append(f"usemtl {mesh.material}")
        for i in range(mesh.vertex_count):
            x, y, z = mesh.positions[3 * i : 3 * i + 3]
            out.append(f"v {x:.5f} {y:.5f} {z:.5f}")
        if has_uv:
            for i in range(mesh.vertex_count):
                u, v = mesh.uvs[2 * i : 2 * i + 2]
                out.append(f"vt {u:.5f} {v:.5f}")
        for i in range(mesh.vertex_count):
            x, y, z = mesh.normals[3 * i : 3 * i + 3]
            out.append(f"vn {x:.3f} {y:.3f} {z:.3f}")
        for i in range(0, len(mesh.indices), 3):
            a, b, c = (v + voff for v in mesh.indices[i : i + 3])
            if has_uv:
                ta, tb, tc = (v + toff for v in mesh.indices[i : i + 3])
                out.append(f"f {a}/{ta}/{a} {b}/{tb}/{b} {c}/{tc}/{c}")
            else:
                out.append(f"f {a}//{a} {b}//{b} {c}//{c}")
        voff += mesh.vertex_count
        if has_uv:
            toff += mesh.vertex_count
    return "\n".join(out) + "\n"


def write_mtl(scene: Scene) -> str:
    out: list[str] = ["# floor-to-rendered"]
    for name, m in scene.materials.items():
        r, g, b = m.color
        out += [
            f"newmtl {name}",
            f"Kd {r:.4f} {g:.4f} {b:.4f}",
            f"Ka {r * 0.2:.4f} {g * 0.2:.4f} {b * 0.2:.4f}",
            f"Ks {0.1 + 0.6 * m.metallic:.4f} {0.1 + 0.6 * m.metallic:.4f} "
            f"{0.1 + 0.6 * m.metallic:.4f}",
            f"Ns {max(1.0, (1.0 - m.roughness) * 200):.1f}",
            f"d {m.alpha:.3f}",
            "illum 2",
        ]
        if m.texture and m.texture in tex.GENERATORS:
            out.append(f"map_Kd textures/{m.texture}.png")
        out.append("")
    return "\n".join(out) + "\n"


def texture_files(scene: Scene) -> dict[str, bytes]:
    """The PNGs an OBJ export needs beside it, keyed by relative path."""
    return {f"textures/{name}.png": tex.png(name) for name in scene.textures}
