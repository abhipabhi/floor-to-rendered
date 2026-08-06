"""A Blender import script shipped alongside the model.

Double-clicking a .glb in Blender works, but you land in a scene with no sun, no
units and a camera pointing at the origin. This script imports the model, sets
the scene to metres, adds a sun at a sensible altitude, points a camera at the
building and switches the viewport to material preview — so ``blender --python
blender_import.py`` gives you something to look at immediately.
"""

from __future__ import annotations

TEMPLATE = '''"""Import the model exported by floor-to-rendered.

    blender --python blender_import.py
    # or: Blender ▸ Scripting ▸ Open ▸ Run

The model is metres, Y up in the file; Blender's glTF importer converts it to
Z up on import, after which north points along +Y.
"""

import math
import os

import bpy
import mathutils

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "{glb_name}")
NORTH_ALIGNED = {north_aligned}


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def main():
    clear_scene()

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"

    bpy.ops.import_scene.gltf(filepath=MODEL)
    imported = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not imported:
        raise SystemExit("nothing imported from " + MODEL)

    # world-space bounds, so the camera and sun suit the building's actual size
    corners = [o.matrix_world @ mathutils.Vector(c) for o in imported for c in o.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    zmax = max(zs)
    size = max(max(xs) - min(xs), max(ys) - min(ys))

    bpy.ops.object.light_add(type="SUN", location=(size, -size, size * 1.6))
    sun = bpy.context.object
    sun.data.energy = 4.0
    sun.data.angle = math.radians(1.5)
    sun.rotation_euler = (math.radians(55), 0.0, math.radians(35))
    sun.name = "Sun"

    bpy.ops.object.camera_add(location=(size * 1.1, -size * 1.3, zmax * 1.4))
    cam = bpy.context.object
    cam.name = "ThreeQuarterView"
    cam.data.lens = 32
    cam.rotation_euler = (math.radians(72), 0.0, math.radians(40))
    scene.camera = cam

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.55, 0.68, 0.85, 1.0)
        bg.inputs[1].default_value = 1.1

    for area in bpy.context.screen.areas if bpy.context.screen else []:
        if area.type == "VIEW_3D":
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.shading.type = "MATERIAL"
                    space.clip_end = 10000

    print("Imported {{}} objects from {{}}".format(len(imported), MODEL))
    if NORTH_ALIGNED:
        print("Model is north-aligned: north is +Y in Blender.")


main()
'''


def blender_script(glb_name: str = "model.glb", north_aligned: bool = True) -> str:
    return TEMPLATE.format(glb_name=glb_name, north_aligned=bool(north_aligned))
