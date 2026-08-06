"""glTF 2.0 binary (.glb) writer.

Written out directly rather than through a library: the scene here is a few
dozen box meshes with flat normals, and glTF is a JSON header plus one binary
blob, so a dependency would cost more than it saves. glb is the interchange
format because Blender imports it natively and Twinmotion's Direct Import reads
it, so the same file serves both without a conversion step.

Textures are embedded in the same binary chunk, so the .glb is one
self-contained file with nothing to relink.

Layout: one buffer, one bufferView per attribute stream and per image, one mesh
and node per group, so every group lands in Blender's outliner as a separately
named object.
"""

from __future__ import annotations

import json
import struct

from . import textures as tex
from .mesh import Scene

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

FLOAT = 5126
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
REPEAT = 10497
LINEAR = 9729
LINEAR_MIPMAP_LINEAR = 9987


def _pad4(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * (-len(b) % 4)


def write_glb(scene: Scene, extras: dict | None = None) -> bytes:
    """Serialise a :class:`Scene` to glb bytes."""
    scene.prune()

    buffers = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    meshes: list[dict] = []
    nodes: list[dict] = []
    images: list[dict] = []
    gltf_textures: list[dict] = []

    def add_view(data: bytes, target: int | None = None) -> int:
        offset = len(buffers)
        buffers.extend(data)
        buffers.extend(b"\x00" * (-len(buffers) % 4))
        view: dict = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    # -- textures first, so materials can point at them
    texture_index: dict[str, int] = {}
    for name in scene.textures:
        try:
            png = tex.png(name)
        except KeyError:
            continue
        images.append(
            {"name": name, "mimeType": "image/png", "bufferView": add_view(png)}
        )
        gltf_textures.append({"name": name, "sampler": 0, "source": len(images) - 1})
        texture_index[name] = len(gltf_textures) - 1

    # -- materials, only the ones something actually uses, so an importer's
    #    material list matches what is in the scene
    used_materials = {m.material for m in scene.meshes}
    mat_index: dict[str, int] = {}
    materials: list[dict] = []
    for name, m in scene.materials.items():
        if name not in used_materials:
            continue
        mat_index[name] = len(materials)
        pbr: dict = {
            "baseColorFactor": [*m.color, m.alpha],
            "metallicFactor": m.metallic,
            "roughnessFactor": m.roughness,
        }
        if m.texture and m.texture in texture_index:
            pbr["baseColorTexture"] = {"index": texture_index[m.texture], "texCoord": 0}
        mat: dict = {
            "name": name,
            "doubleSided": True,
            "pbrMetallicRoughness": pbr,
        }
        if m.blend:
            mat["alphaMode"] = "BLEND"
        materials.append(mat)

    # -- geometry
    for mesh in scene.meshes:
        n = mesh.vertex_count
        pos = struct.pack(f"<{len(mesh.positions)}f", *mesh.positions)
        nrm = struct.pack(f"<{len(mesh.normals)}f", *mesh.normals)
        uvs = struct.pack(f"<{len(mesh.uvs)}f", *mesh.uvs)
        idx = struct.pack(f"<{len(mesh.indices)}I", *mesh.indices)

        xs, ys, zs = mesh.positions[0::3], mesh.positions[1::3], mesh.positions[2::3]
        accessors.append(
            {
                "bufferView": add_view(pos, ARRAY_BUFFER),
                "componentType": FLOAT,
                "count": n,
                "type": "VEC3",
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            }
        )
        a_pos = len(accessors) - 1
        accessors.append(
            {
                "bufferView": add_view(nrm, ARRAY_BUFFER),
                "componentType": FLOAT,
                "count": n,
                "type": "VEC3",
            }
        )
        a_nrm = len(accessors) - 1
        attributes = {"POSITION": a_pos, "NORMAL": a_nrm}
        material = scene.materials.get(mesh.material)
        wants_uv = bool(material and material.texture and material.texture in texture_index)
        if wants_uv and len(mesh.uvs) == 2 * n:
            accessors.append(
                {
                    "bufferView": add_view(uvs, ARRAY_BUFFER),
                    "componentType": FLOAT,
                    "count": n,
                    "type": "VEC2",
                }
            )
            attributes["TEXCOORD_0"] = len(accessors) - 1
        accessors.append(
            {
                "bufferView": add_view(idx, ELEMENT_ARRAY_BUFFER),
                "componentType": UNSIGNED_INT,
                "count": len(mesh.indices),
                "type": "SCALAR",
            }
        )
        a_idx = len(accessors) - 1

        meshes.append(
            {
                "name": mesh.name,
                "primitives": [
                    {
                        "attributes": attributes,
                        "indices": a_idx,
                        "material": mat_index.get(mesh.material, 0),
                        "mode": 4,
                    }
                ],
            }
        )
        nodes.append({"name": mesh.name, "mesh": len(meshes) - 1})

    gltf: dict = {
        "asset": {"version": "2.0", "generator": "floor-to-rendered"},
        "scene": 0,
        "scenes": [{"name": "Building", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials or [{"name": "default"}],
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buffers)}],
    }
    if images:
        gltf["images"] = images
        gltf["textures"] = gltf_textures
        gltf["samplers"] = [
            {
                "wrapS": REPEAT,
                "wrapT": REPEAT,
                "magFilter": LINEAR,
                "minFilter": LINEAR_MIPMAP_LINEAR,
            }
        ]
    if extras:
        gltf["asset"]["extras"] = extras

    json_chunk = _pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = _pad4(bytes(buffers))

    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(json_chunk), JSON_CHUNK)
    out += json_chunk
    out += struct.pack("<II", len(bin_chunk), BIN_CHUNK)
    out += bin_chunk
    return bytes(out)
