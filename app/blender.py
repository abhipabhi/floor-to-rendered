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
#: which way the front of the house looks, in Blender's axes. The street side
#: comes from the plan's own ROAD label, and the model may then be turned to put
#: north on an axis, so the front camera has to be told where the front went.
FACADE_DIR = {facade_dir}


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
    """Point an object's -Z down a direction vector, keeping its head up.

    ``to_track_quat`` is the part that matters: the obvious
    ``rotation_difference`` gives the *shortest* rotation onto the direction,
    which says nothing about roll, so every camera comes out banked and the
    horizon runs diagonally across the render.
    """
    obj.rotation_euler = direction.normalized().to_track_quat("-Z", "Y").to_euler()


def _set_any(node, attr, values):
    """Set an enum to the first value this Blender actually accepts."""
    for value in values:
        try:
            setattr(node, attr, value)
            return True
        except (TypeError, ValueError):
            continue
    return False


def _set_soft(node, attr, value):
    """Set an attribute if this Blender has it, and shrug if it does not."""
    try:
        setattr(node, attr, value)
    except (AttributeError, TypeError, ValueError):
        pass


def build_sky(scene):
    """A physical sky, used for both the background and the bounce light.

    A real horizon and sun disc means the model is lit by something with a
    direction and a colour instead of flat grey.

    Every property here is set defensively and *separately*. Blender renames
    these between versions — the physical sky model has been both NISHITA and
    MULTIPLE_SCATTERING — and the trap is wrapping the whole block in one
    try/except: the sky node gets created, one renamed property raises, and the
    node is left sitting in the tree unlinked. The result is a scene that looks
    like it has a sky and renders a flat grey background.
    """
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    # Only reach for use_nodes if there is genuinely no tree — from Blender 5 on
    # worlds always have one and setting the flag is deprecated
    if getattr(world, "node_tree", None) is None:
        _set_soft(world, "use_nodes", True)
    tree = world.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    out = tree.nodes.new("ShaderNodeOutputWorld")
    bg = tree.nodes.new("ShaderNodeBackground")
    # Deliberately low. The sky is doing ambient fill only; the Sun lamp is the
    # key light. Turn this up and the fill drowns the sun — at 0.9 the model
    # renders with no cast shadows at all, which reads as a bug but is just the
    # two light sources fighting. Raise it for an overcast look, and expect the
    # shadows to go with it.
    bg.inputs["Strength"].default_value = 0.22
    out.location, bg.location = (300, 0), (100, 0)
    tree.links.new(bg.outputs[0], out.inputs["Surface"])

    try:
        sky = tree.nodes.new("ShaderNodeTexSky")
    except Exception:
        sky = None
    if sky is None:
        bg.inputs["Color"].default_value = (0.42, 0.58, 0.82, 1.0)
        return

    sky.location = (-120, 0)
    _set_any(sky, "sky_type", ("MULTIPLE_SCATTERING", "NISHITA", "PREETHAM"))
    _set_soft(sky, "sun_elevation", math.radians(SUN_ELEVATION_DEG))
    # Blender measures sky rotation from +X; the bearing is from +Y (north)
    _set_soft(sky, "sun_rotation", math.radians(90.0 - SUN_BEARING_DEG))
    # no disc in the sky texture: the Sun lamp already provides it, and having
    # both double-counts the key light and washes the shadows out
    _set_soft(sky, "sun_intensity", 0.0)
    _set_soft(sky, "altitude", 20.0)
    tree.links.new(sky.outputs[0], bg.inputs["Color"])


def add_backdrop(size, ground_z):
    """A wide ground plane for the model to sit on.

    The exported site stops at the plot boundary, so without this the camera
    looks past it straight into the sky's below-horizon region, which every
    physical sky model renders near-black. The building ends up floating at
    dusk. This is scene dressing, not part of the model — delete it freely.
    """
    bpy.ops.mesh.primitive_plane_add(size=max(400.0, size * 30), location=(0, 0, ground_z))
    plane = bpy.context.object
    plane.name = "Backdrop ground"
    mat = bpy.data.materials.new("Backdrop")
    if getattr(mat, "node_tree", None) is None:
        _set_soft(mat, "use_nodes", True)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.26, 0.29, 0.22, 1.0)
        _set_soft(bsdf.inputs["Roughness"], "default_value", 1.0)
    plane.data.materials.append(mat)
    return plane


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
        if obj.name.startswith("Backdrop"):
            target = "Site"
        elif obj.name.startswith("Site"):
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

    # Whatever is left in the startup collection is the sun and the cameras,
    # so give it a name that says so rather than leaving a stray "Collection"
    for child in list(scene_col.children):
        if child.name in made:
            continue
        if not child.objects and not child.children:
            scene_col.children.unlink(child)
            bpy.data.collections.remove(child)
        elif child.name.startswith("Collection"):
            child.name = "Lighting & cameras"
    return made


def add_cameras(size, zmax, centre):
    """A few framings to start from, all pointed at the front of the building.

    Heights are given as a fraction of the building's own height **above the
    ground**, not as a multiple of it. Set as a multiple these sat two and a
    half storeys over the parapet, so every view but the aerial was a picture
    of the roof with the facade foreshortened away underneath it. A house is
    photographed from about the height of the storey you are looking at.
    """
    front = mathutils.Vector((FACADE_DIR[0], FACADE_DIR[1], 0.0))
    if front.length < 1e-6:
        front = mathutils.Vector((0.0, -1.0, 0.0))
    front.normalize()
    side = mathutils.Vector((-front.y, front.x, 0.0))  # along the facade
    ground = 2.0 * centre.z - zmax

    def eye(frac):
        return mathutils.Vector((0.0, 0.0, ground + zmax * frac - centre.z))

    views = [
        ("Camera_ThreeQuarter", front * size * 1.6 + side * size * 0.72
         + eye(0.70), 35),
        ("Camera_Front", front * size * 2.0 + eye(0.42), 50),
        ("Camera_Corner", front * size * 1.35 - side * size * 1.1 + eye(0.58), 35),
        ("Camera_Aerial", front * size * 1.1 + side * size * 0.5 + eye(2.6), 28),
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

    # Frame on the building, not the scene. The street and the ground run well
    # past it on purpose, and a camera fitted to those pulls back until the
    # house is a speck in a field of tarmac.
    subject = [o for o in imported if not o.name.startswith("Site")] or imported
    corners = [o.matrix_world @ mathutils.Vector(c) for o in subject for c in o.bound_box]
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
    sun.data.energy = 5.0
    sun.data.angle = math.radians(0.9)   # a crisp but not razor-edged shadow
    aim(sun, -towards_sun)

    build_sky(scene)
    backdrop = add_backdrop(size, min(zs))
    scene.camera = add_cameras(size, zmax, centre)
    collect(imported + [backdrop])

    # Render settings worth having set before the first F12
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False
    _set_any(scene.render, "engine", ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"))
    _set_any(scene.view_settings, "view_transform", ("AgX", "Filmic"))
    if getattr(scene, "eevee", None) is not None:
        _set_soft(scene.eevee, "use_shadows", True)
        _set_soft(scene.eevee, "use_raytracing", True)
        _set_soft(scene.eevee, "taa_render_samples", 96)

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
    facade_normal_xz: tuple[float, float] | None = None,
) -> str:
    """The import script, told what the model it sits next to contains.

    ``storeys`` are the level names the builder used for its group names, so the
    script can sort the imported objects into a collection per storey without
    having to guess at them.
    """
    # glTF is Y-up and Blender's importer converts to Z-up, mapping model
    # (x, y, z) to (x, -z, y) — so a facade normal of (nx, nz) points along
    # (nx, -nz) once it is in the scene.
    nx, nz = facade_normal_xz or (0.0, -1.0)
    return TEMPLATE.format(
        glb_name=glb_name,
        north_aligned=bool(north_aligned),
        storeys=repr(list(storeys or [])),
        sun_elevation=float(sun_elevation),
        sun_bearing=float(sun_bearing),
        facade_dir=repr((round(float(nx), 4), round(-float(nz), 4))),
    )
