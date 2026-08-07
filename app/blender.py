"""A Blender scene shipped alongside the model.

Double-clicking a .glb gets you geometry in a grey void. This builds the scene
around it instead: a physical sky that lights the model and *is* the background,
a sun aimed at the compass bearing read off the drawing, cameras set up on the
building rather than the origin, and every storey in its own collection so the
first thing anyone does — hide the roof, swap a material, move a camera — takes
one click.

The point is not to render the picture here. It is to hand over a scene that is
already standing up, so the work in Blender starts at touch-ups rather than at
lighting a black screen.
"""

from __future__ import annotations

TEMPLATE = '''"""Scene set-up for the model exported by floor-to-rendered.

    blender --python blender_import.py
    # or: Blender ▸ Scripting ▸ Open ▸ Run

The model is metres, Y up in the file; Blender's glTF importer converts it to
Z up on import, after which north points along +Y.

Everything here is a starting point meant to be edited. The sun bearing and the
storey collections come from the drawings; the exposure, lens and sky are just
sensible values.
"""

import math
import os

import bpy
import mathutils

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "{glb_name}")
NORTH_ALIGNED = {north_aligned}

# Where the sun sits. Elevation and bearing are degrees; the bearing is measured
# clockwise from north, so it lands correctly against the compass on the sheet.
SUN_ELEVATION_DEG = {sun_elevation}
SUN_BEARING_DEG = {sun_bearing}
STOREYS = {storeys}


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def sun_vector(elevation_deg, bearing_deg):
    """Unit vector pointing from the building towards the sun, in Blender axes."""
    e = math.radians(elevation_deg)
    a = math.radians(bearing_deg)
    return mathutils.Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))


def aim(obj, direction):
    """Point an object's -Z down a direction vector."""
    obj.rotation_euler = mathutils.Vector((0.0, 0.0, -1.0)).rotation_difference(
        direction.normalized()
    ).to_euler()


def build_sky(scene):
    """A physical sky, used for both the background and the bounce light.

    Nishita gives a real horizon and a sun disc, so the model is lit by
    something with a direction and a colour rather than flat grey.
    """
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    out = tree.nodes.new("ShaderNodeOutputWorld")
    bg = tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.85
    try:
        sky = tree.nodes.new("ShaderNodeTexSky")
        sky.sky_type = "NISHITA"
        sky.sun_elevation = math.radians(SUN_ELEVATION_DEG)
        # Blender measures sky rotation from +X; the bearing is from +Y (north)
        sky.sun_rotation = math.radians(90.0 - SUN_BEARING_DEG)
        sky.sun_intensity = 0.6
        sky.altitude = 20.0
        tree.links.new(sky.outputs[0], bg.inputs["Color"])
    except Exception:  # older Blender, or no Nishita — a plain sky still works
        bg.inputs["Color"].default_value = (0.42, 0.58, 0.82, 1.0)
    tree.links.new(bg.outputs[0], out.inputs["Surface"])
    out.location = (300, 0)
    bg.location = (100, 0)


def collect(objects):
    """One collection per storey, plus the site — so parts can be hidden."""
    scene_col = bpy.context.scene.collection
    made = {{}}

    def collection_for(name):
        if name not in made:
            col = bpy.data.collections.new(name)
            scene_col.children.link(col)
            made[name] = col
        return made[name]

    for obj in objects:
        target = "Shell"
        if obj.name.startswith("Site"):
            target = "Site"
        else:
            for storey in STOREYS:
                if obj.name.startswith(storey):
                    target = storey
                    break
        col = collection_for(target)
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        col.objects.link(obj)
    return made


def add_cameras(size, zmax, centre):
    """A few framings to start from, all pointed at the building."""
    views = [
        ("Camera_ThreeQuarter", mathutils.Vector((size * 1.05, -size * 1.25, zmax * 1.35)), 35),
        ("Camera_Front", mathutils.Vector((0.0, -size * 1.9, zmax * 0.85)), 50),
        ("Camera_Corner", mathutils.Vector((-size * 1.15, -size * 1.05, zmax * 1.1)), 35),
        ("Camera_Aerial", mathutils.Vector((size * 0.8, -size * 0.9, zmax * 3.0)), 28),
    ]
    first = None
    for name, offset, lens in views:
        bpy.ops.object.camera_add(location=centre + offset)
        cam = bpy.context.object
        cam.name = name
        cam.data.lens = lens
        cam.data.clip_end = max(1000.0, size * 40)
        aim(cam, centre - (centre + offset))
        if first is None:
            first = cam
    return first


def main():
    clear_scene()

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"

    bpy.ops.import_scene.gltf(filepath=MODEL)
    imported = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not imported:
        raise SystemExit("nothing imported from " + MODEL)

    corners = [o.matrix_world @ mathutils.Vector(c) for o in imported for c in o.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    zmax = max(zs)
    size = max(max(xs) - min(xs), max(ys) - min(ys))
    centre = mathutils.Vector(
        ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (zmax + min(zs)) / 2)
    )

    towards_sun = sun_vector(SUN_ELEVATION_DEG, SUN_BEARING_DEG)
    bpy.ops.object.light_add(type="SUN", location=centre + towards_sun * size * 2.2)
    sun = bpy.context.object
    sun.name = "Sun"
    sun.data.energy = 3.2
    sun.data.angle = math.radians(0.9)   # a crisp but not razor-edged shadow
    aim(sun, -towards_sun)

    build_sky(scene)
    scene.camera = add_cameras(size, zmax, centre)
    collect(imported)

    # Render settings worth having set before the first F12
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    for look in ("AgX", "Filmic"):
        try:
            scene.view_settings.view_transform = look
            break
        except TypeError:
            continue
    try:
        scene.eevee.use_shadows = True
        scene.eevee.use_raytracing = True
    except AttributeError:
        pass

    for area in bpy.context.screen.areas if bpy.context.screen else []:
        if area.type == "VIEW_3D":
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.shading.type = "MATERIAL"
                    space.clip_end = max(10000.0, size * 60)

    print("Imported {{}} objects from {{}}".format(len(imported), MODEL))
    print("Collections: {{}}".format(", ".join(c.name for c in scene.collection.children)))
    print("Cameras: ThreeQuarter, Front, Corner, Aerial — scene camera is ThreeQuarter.")
    print("Sun at {{:.0f}}° elevation, bearing {{:.0f}}°.".format(
        SUN_ELEVATION_DEG, SUN_BEARING_DEG))
    if NORTH_ALIGNED:
        print("Model is north-aligned: north is +Y in Blender.")


main()
'''


def blender_script(
    glb_name: str = "model.glb",
    north_aligned: bool = True,
    storeys: list[str] | None = None,
    sun_elevation: float = 34.0,
    sun_bearing: float = 138.0,
) -> str:
    """The import script, told what the model it sits next to contains.

    ``storeys`` are the level names the builder used for its group names, so the
    script can sort the imported objects into a collection per storey without
    having to guess at them.
    """
    return TEMPLATE.format(
        glb_name=glb_name,
        north_aligned=bool(north_aligned),
        storeys=repr(list(storeys or [])),
        sun_elevation=float(sun_elevation),
        sun_bearing=float(sun_bearing),
    )
