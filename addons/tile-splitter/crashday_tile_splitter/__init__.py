bl_info = {
    "name": "Mica's Crashday Tile Splitter",
    "author": "MicaLovesKPOP (AI-assisted)",
    "version": (0, 4, 7),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar > Crashday Tiles",
    "description": "Derived splitter with safe tile-centred P3D export and rollback.",
    "category": "Object",
}

import math
import os
import re
import struct
from collections import defaultdict

import bpy
import bmesh
from mathutils import Vector

from . import p3d_export, texture_export, preview_render

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)


EPSILON_DEFAULT = 0.00001
TILE_SIZE_STEP = 20
TILE_SIZE_MIN = 20
TILE_SIZE_MAX = 400
PHYSICS_DIAGONAL_LIMIT_DEFAULT = 200.0
# Use static RNA enum choices: mutually dependent callback items recursively invoke
# each other in Blender 2.83/3.4 and can leave the current value invalid.
TILE_SIZE_ITEMS = [(str(value), "{} m".format(value), "Tile size in meters", value)
                   for value in range(TILE_SIZE_MIN, TILE_SIZE_MAX + 1, TILE_SIZE_STEP)]


def tile_size_values():
    return range(TILE_SIZE_MIN, TILE_SIZE_MAX + 1, TILE_SIZE_STEP)


def tile_fits_physics_limit(size_x, size_y, diagonal_limit):
    # Authoring limit is center-to-corner radius, not full diagonal.
    # It is user configurable; this is not an empirically proven game physics limit.
    return math.hypot(float(size_x) * 0.5, float(size_y) * 0.5) <= float(diagonal_limit) + 0.000001


def allowed_tile_sizes_for_other_dimension(other_size, diagonal_limit):
    values = [size for size in tile_size_values() if tile_fits_physics_limit(size, other_size, diagonal_limit)]
    if not values:
        values = [TILE_SIZE_MIN]
    return values


def tile_size_x_items(self, _context):
    size_y = int(getattr(self, "tile_size_y", "40") or 40)
    limit = float(getattr(self, "physics_diagonal_limit", PHYSICS_DIAGONAL_LIMIT_DEFAULT) or PHYSICS_DIAGONAL_LIMIT_DEFAULT)
    return [(str(size), f"{size} m", f"Tile width {size} m; keeps center-to-corner within {limit:g} m", size) for size in allowed_tile_sizes_for_other_dimension(size_y, limit)]


def tile_size_y_items(self, _context):
    size_x = int(getattr(self, "tile_size_x", "40") or 40)
    limit = float(getattr(self, "physics_diagonal_limit", PHYSICS_DIAGONAL_LIMIT_DEFAULT) or PHYSICS_DIAGONAL_LIMIT_DEFAULT)
    return [(str(size), f"{size} m", f"Tile depth {size} m; keeps center-to-corner within {limit:g} m", size) for size in allowed_tile_sizes_for_other_dimension(size_x, limit)]


_CLAMPING_TILE_SIZES = False


def clamp_tile_dimensions(settings, changed_axis=None):
    # Stable static RNA choices + deterministic coupled clamp.  Dynamic RNA
    # item callbacks that query each other recurse and warn in native Blender.
    global _CLAMPING_TILE_SIZES
    if _CLAMPING_TILE_SIZES:
        return
    _CLAMPING_TILE_SIZES = True
    try:
        limit = max(float(settings.physics_diagonal_limit), float(TILE_SIZE_MIN))
        size_x = int(settings.tile_size_x)
        size_y = int(settings.tile_size_y)
        if tile_fits_physics_limit(size_x, size_y, limit):
            return
        allowed = [(x, y) for x in tile_size_values()
                   for y in tile_size_values()
                   if tile_fits_physics_limit(x, y, limit)]
        # At min=20, even 20x20 has radius ~14.14, thus never empty.
        if not allowed:
            return
        if changed_axis == "X":
            score = lambda pair: (abs(pair[0]-size_x), abs(pair[1]-size_y), -pair[0], -pair[1])
        elif changed_axis == "Y":
            score = lambda pair: (abs(pair[1]-size_y), abs(pair[0]-size_x), -pair[1], -pair[0])
        else:
            score = lambda pair: (abs(pair[0]-size_x)+abs(pair[1]-size_y),
                                  abs(pair[0]-size_x), abs(pair[1]-size_y))
        new_x, new_y = min(allowed, key=score)
        if size_x != new_x:
            settings.tile_size_x = str(new_x)
        if size_y != new_y:
            settings.tile_size_y = str(new_y)
    finally:
        _CLAMPING_TILE_SIZES = False


def on_tile_size_x_changed(self, _context):
    clamp_tile_dimensions(self, changed_axis="X")


def on_tile_size_y_changed(self, _context):
    clamp_tile_dimensions(self, changed_axis="Y")


def on_physics_limit_changed(self, _context):
    clamp_tile_dimensions(self, changed_axis=None)


def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "tile"


def index_name(value):
    return f"p{value:04d}" if value >= 0 else f"m{abs(value):04d}"


def tile_key(ix, iy):
    return f"x{index_name(ix)}_y{index_name(iy)}"


def world_bounds_from_points(points):
    if not points:
        return None

    low = Vector((points[0].x, points[0].y, points[0].z))
    high = Vector((points[0].x, points[0].y, points[0].z))

    for point in points[1:]:
        low.x = min(low.x, point.x)
        low.y = min(low.y, point.y)
        low.z = min(low.z, point.z)
        high.x = max(high.x, point.x)
        high.y = max(high.y, point.y)
        high.z = max(high.z, point.z)

    return low, high


def object_world_bounds(obj, depsgraph, use_modifiers):
    source = obj.evaluated_get(depsgraph) if use_modifiers else obj
    points = [source.matrix_world @ Vector(corner) for corner in source.bound_box]
    return world_bounds_from_points(points)


def mesh_world_bounds(mesh):
    if not mesh.vertices:
        return None
    return world_bounds_from_points([v.co for v in mesh.vertices])


def tile_index_range(min_value, max_value, origin_value, tile_size, epsilon):
    first = math.floor((min_value - origin_value) / tile_size)
    last = math.floor(((max_value - origin_value) - epsilon) / tile_size)
    if last < first:
        last = first
    return first, last


def make_world_mesh(context, obj, use_modifiers):
    depsgraph = context.evaluated_depsgraph_get()

    if use_modifiers:
        evaluated = obj.evaluated_get(depsgraph)
        try:
            mesh = bpy.data.meshes.new_from_object(
                evaluated,
                depsgraph=depsgraph,
                preserve_all_data_layers=True,
            )
        except TypeError:
            mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
        matrix = evaluated.matrix_world.copy()
    else:
        mesh = obj.data.copy()
        matrix = obj.matrix_world.copy()

    mesh.transform(matrix)
    mesh.update(calc_edges=True)
    mesh.name = f"{safe_name(obj.name)}_world_source"
    return mesh


def bisect_keep_inside_xy(mesh, xmin, xmax, ymin, ymax, epsilon):
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Normals point toward the part we want to delete when clear_outer=True.
    planes = (
        (Vector((xmin, 0.0, 0.0)), Vector((-1.0, 0.0, 0.0))),
        (Vector((xmax, 0.0, 0.0)), Vector((1.0, 0.0, 0.0))),
        (Vector((0.0, ymin, 0.0)), Vector((0.0, -1.0, 0.0))),
        (Vector((0.0, ymax, 0.0)), Vector((0.0, 1.0, 0.0))),
    )

    freed = False
    try:
        for plane_co, plane_no in planes:
            if not bm.faces:
                break

            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
            bmesh.ops.bisect_plane(
                bm,
                geom=geom,
                dist=epsilon,
                plane_co=plane_co,
                plane_no=plane_no,
                clear_outer=True,
                clear_inner=False,
            )

        if bm.faces:
            tiny_faces = [face for face in bm.faces if face.calc_area() <= epsilon * epsilon]
            if tiny_faces:
                bmesh.ops.delete(bm, geom=tiny_faces, context="FACES_ONLY")

        if not bm.faces:
            bm.free()
            freed = True
            return False

        loose_edges = [edge for edge in bm.edges if not edge.link_faces]
        if loose_edges:
            bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")

        loose_verts = [vert for vert in bm.verts if not vert.link_edges]
        if loose_verts:
            bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")

        bm.to_mesh(mesh)
    finally:
        if not freed:
            bm.free()

    mesh.update(calc_edges=True)
    return len(mesh.polygons) > 0


def move_mesh_origin_without_moving_world(mesh, origin):
    for vertex in mesh.vertices:
        vertex.co -= origin
    mesh.update(calc_edges=True)


def ensure_child_collection(parent_collection, name):
    collection = bpy.data.collections.new(name)
    parent_collection.children.link(collection)
    return collection


def set_tile_metadata(obj, ix, iy, size_x, size_y, xmin, xmax, ymin, ymax):
    key = tile_key(ix, iy)
    obj["crashday_tile_key"] = key
    obj["crashday_tile_x_index"] = ix
    obj["crashday_tile_y_index"] = iy
    obj["crashday_tile_size_x_m"] = float(size_x)
    obj["crashday_tile_size_y_m"] = float(size_y)
    # Backwards-compatible value for old square-tile tooling.
    obj["crashday_tile_size_m"] = float(size_x) if abs(float(size_x) - float(size_y)) <= 0.000001 else 0.0
    obj["crashday_tile_min_x"] = float(xmin)
    obj["crashday_tile_max_x"] = float(xmax)
    obj["crashday_tile_min_y"] = float(ymin)
    obj["crashday_tile_max_y"] = float(ymax)


class CrashdayTileSplitterSettings(bpy.types.PropertyGroup):
    physics_diagonal_limit: FloatProperty(
        name="Max Center-to-Corner Distance",
        description="Provisional tile-center-to-corner authoring bound, in meters; NOT a verified Crashday engine limit",
        default=PHYSICS_DIAGONAL_LIMIT_DEFAULT,
        min=TILE_SIZE_MIN,
        max=10000.0,
        step=20,
        precision=1,
        update=on_physics_limit_changed,
    )

    tile_size_x: EnumProperty(
        name="Tile Width X",
        description="World X tile size in meters, constrained by center-to-corner bound",
        items=TILE_SIZE_ITEMS,
        default="40",
        update=on_tile_size_x_changed,
    )

    tile_size_y: EnumProperty(
        name="Tile Depth Y",
        description="World Y tile size in meters, constrained by center-to-corner bound",
        items=TILE_SIZE_ITEMS,
        default="40",
        update=on_tile_size_y_changed,
    )

    grid_shift_x: FloatProperty(
        name="Grid Shift X",
        description="Offset the grid in world X without changing tile dimensions",
        default=0.0,
        precision=3,
    )

    grid_shift_y: FloatProperty(
        name="Grid Shift Y",
        description="Offset the grid in world Y without changing tile dimensions",
        default=0.0,
        precision=3,
    )

    grid_origin: EnumProperty(
        name="Grid Origin",
        description="Where the tile grid starts",
        items=(
            ("WORLD_ZERO", "World 0,0", "Use world X=0, Y=0 as the tile grid origin"),
            ("SELECTION_MIN", "Selection Minimum", "Start the grid at the selected meshes' minimum X/Y corner"),
            ("CURSOR", "3D Cursor", "Use the 3D cursor X/Y location as the tile grid origin"),
        ),
        default="WORLD_ZERO",
    )

    use_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Split the evaluated mesh with visible modifiers applied",
        default=True,
    )

    # Kept for migration compatibility with 0.2 saves; ignored deliberately:
    # joining strips semantic P3D submesh identities such as maincoll/wood/dest.
    merge_per_tile: BoolProperty(
        name="Legacy Merge Pieces (Ignored)",
        description="Always preserve independent source mesh fragments",
        default=False,
    )

    create_tile_collections: BoolProperty(
        name="Create Collection Per Tile",
        description="Put each tile in its own collection for easier review and future export",
        default=True,
    )

    center_tile_origins: BoolProperty(
        name="Origin At Tile Center",
        description="Place each generated object's origin at the tile center without moving the visible geometry",
        default=True,
    )

    delete_originals: BoolProperty(
        name="Delete Original Meshes",
        description="Delete the original selected mesh objects after a successful split",
        default=False,
    )

    epsilon: FloatProperty(
        name="Cut Tolerance",
        description="Small tolerance used by Blender's bisect operation",
        default=EPSILON_DEFAULT,
        min=0.0000001,
        max=0.01,
        precision=7,
    )

    output_collection_name: StringProperty(
        name="Output Collection",
        description="Name of the generated tile collection",
        default="Crashday Tiles",
    )

    export_directory: StringProperty(
        name="P3D Export Folder",
        description="Folder where per-tile .p3d files will be written when using Export Tiles to P3D",
        subtype="DIR_PATH",
        default="",
    )

    use_floor_level_empty: BoolProperty(
        name="Use Existing Floor-level Empty",
        description="Opt in to Wurunduk-style P3D height from a scene Empty named floor_level; otherwise preserve native main height convention",
        default=False,
    )

    export_selected_only: BoolProperty(
        name="Export Selected Tiles Only",
        description="If enabled, export only selected generated tile objects. Otherwise export all generated tile objects in the scene",
        default=True,
    )

    export_file_prefix: StringProperty(
        name="Export File Prefix",
        description="Prefix for generated .p3d files",
        default="tile_",
    )

    write_export_manifest: BoolProperty(
        name="Write Tile Manifest",
        description="Save audited tile centres, P3D hashes, source texture names and native mesh flags beside the exported models (no game files)",
        default=True,
    )


class CRASHDAYTILES_OT_split_selected(bpy.types.Operator):
    bl_idname = "crashday_tiles.split_selected"
    bl_label = "Split Selected Meshes Into Tiles"
    bl_description = "Split selected mesh object(s) into exact X/Y grid tiles"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.crashday_tile_splitter
        tile_size_x = float(settings.tile_size_x)
        tile_size_y = float(settings.tile_size_y)
        diagonal_limit = float(settings.physics_diagonal_limit)
        epsilon = float(settings.epsilon)

        if not tile_fits_physics_limit(tile_size_x, tile_size_y, diagonal_limit):
            self.report({"ERROR"}, "The chosen tile dimensions exceed the configured physics diagonal limit.")
            return {"CANCELLED"}

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")

        source_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not source_objects:
            self.report({"ERROR"}, "Select at least one mesh object to split.")
            return {"CANCELLED"}

        depsgraph = context.evaluated_depsgraph_get()
        source_bounds = []
        for obj in source_objects:
            bounds = object_world_bounds(obj, depsgraph, settings.use_modifiers)
            if bounds is not None:
                source_bounds.append(bounds)

        if not source_bounds:
            self.report({"ERROR"}, "Selected meshes have no usable bounds.")
            return {"CANCELLED"}

        selection_low = Vector((source_bounds[0][0].x, source_bounds[0][0].y, source_bounds[0][0].z))
        selection_high = Vector((source_bounds[0][1].x, source_bounds[0][1].y, source_bounds[0][1].z))
        for low, high in source_bounds[1:]:
            selection_low.x = min(selection_low.x, low.x)
            selection_low.y = min(selection_low.y, low.y)
            selection_low.z = min(selection_low.z, low.z)
            selection_high.x = max(selection_high.x, high.x)
            selection_high.y = max(selection_high.y, high.y)
            selection_high.z = max(selection_high.z, high.z)

        if settings.grid_origin == "SELECTION_MIN":
            origin_xy = Vector((selection_low.x, selection_low.y))
        elif settings.grid_origin == "CURSOR":
            cursor = context.scene.cursor.location
            origin_xy = Vector((cursor.x, cursor.y))
        else:
            origin_xy = Vector((0.0, 0.0))
        origin_xy.x += float(settings.grid_shift_x)
        origin_xy.y += float(settings.grid_shift_y)

        master_name = safe_name(settings.output_collection_name) or "Crashday Tiles"
        master_collection = ensure_child_collection(context.scene.collection, master_name)

        tile_collections = {}
        tile_objects = defaultdict(list)
        temp_meshes = []
        clipped_meshes = []
        created_objects = []
        created_count = 0

        try:
            for source_obj in source_objects:
                world_mesh = make_world_mesh(context, source_obj, settings.use_modifiers)
                temp_meshes.append(world_mesh)

                bounds = mesh_world_bounds(world_mesh)
                if bounds is None or len(world_mesh.polygons) == 0:
                    continue

                low, high = bounds
                ix_min, ix_max = tile_index_range(low.x, high.x, origin_xy.x, tile_size_x, epsilon)
                iy_min, iy_max = tile_index_range(low.y, high.y, origin_xy.y, tile_size_y, epsilon)

                for ix in range(ix_min, ix_max + 1):
                    xmin = origin_xy.x + ix * tile_size_x
                    xmax = xmin + tile_size_x
                    for iy in range(iy_min, iy_max + 1):
                        ymin = origin_xy.y + iy * tile_size_y
                        ymax = ymin + tile_size_y

                        clipped_mesh = world_mesh.copy()
                        clipped_meshes.append(clipped_mesh)
                        clipped_mesh.name = f"{safe_name(source_obj.name)}_{tile_key(ix, iy)}_Mesh"

                        if not bisect_keep_inside_xy(clipped_mesh, xmin, xmax, ymin, ymax, epsilon):
                            clipped_meshes.remove(clipped_mesh)
                            bpy.data.meshes.remove(clipped_mesh)
                            continue

                        key = tile_key(ix, iy)
                        if settings.center_tile_origins:
                            object_origin = Vector(((xmin + xmax) * 0.5, (ymin + ymax) * 0.5, 0.0))
                            move_mesh_origin_without_moving_world(clipped_mesh, object_origin)
                        else:
                            object_origin = Vector((0.0, 0.0, 0.0))

                        obj_name = f"{safe_name(source_obj.name)}_{key}"
                        tile_obj = bpy.data.objects.new(obj_name, clipped_mesh)
                        created_objects.append(tile_obj)
                        tile_obj.location = object_origin
                        set_tile_metadata(tile_obj, ix, iy, tile_size_x, tile_size_y, xmin, xmax, ymin, ymax)
                        tile_obj["crashday_source_mesh_name"] = source_obj.name
                        tile_obj["crashday_source_mesh_data_name"] = source_obj.data.name
                        # Retain explicit native P3D semantic flags through the
                        # generated fragments. The writer reads flags from each
                        # fragment, not the unchanged source object. Losing this
                        # silently removed authored BRW/BRP/DET/other roles.
                        if "crashday_p3d_flags" in source_obj:
                            tile_obj["crashday_p3d_flags"] = source_obj["crashday_p3d_flags"]

                        if settings.create_tile_collections:
                            if key not in tile_collections:
                                tile_collections[key] = ensure_child_collection(master_collection, f"Tile_{key}")
                            tile_collections[key].objects.link(tile_obj)
                        else:
                            master_collection.objects.link(tile_obj)

                        tile_objects[(ix, iy)].append(tile_obj)
                        created_count += 1
        except Exception as error:
            # Blender's Undo flag is not a reliable substitute for cleanup
            # when an operator raises halfway through a multi-mesh slice.
            # Remove ONLY data owned by this invocation; leave all source
            # objects, their materials and other scene collections untouched.
            for tile_obj in reversed(created_objects):
                bpy.data.objects.remove(tile_obj, do_unlink=True)
            for collection in reversed(list(tile_collections.values())):
                bpy.data.collections.remove(collection)
            bpy.data.collections.remove(master_collection)
            self.report({"ERROR"}, "Tile split failed; created geometry rolled back: {}".format(error))
            return {"CANCELLED"}
        finally:
            # Successful fragments remain linked to their new objects.
            # Unlinked fragments and temporary transformed sources must not
            # leak even when bisect or scene linking fails mid-operation.
            for mesh in clipped_meshes + temp_meshes:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)

        if created_count == 0:
            bpy.data.collections.remove(master_collection)
            self.report({"ERROR"}, "No tile geometry was created. Check that the selection contains faces.")
            return {"CANCELLED"}

        # Preserve a separate object for EACH source mesh within each tile.
        # Merging collapses source semantic mesh flags (dest/wood/maincoll).
        bpy.ops.object.select_all(action="DESELECT")
        for objects in tile_objects.values():
            for obj in objects:
                obj.select_set(True)
        first_group = next(iter(tile_objects.values()))
        if first_group:
            context.view_layer.objects.active = first_group[0]

        if settings.delete_originals:
            for obj in source_objects:
                if obj.name in bpy.data.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)

        unique_tiles = len(tile_objects)
        final_objects = sum(len(objects) for objects in tile_objects.values())
        self.report(
            {"INFO"},
            f"Created {final_objects} tile object(s) across {unique_tiles} tile(s) at {int(tile_size_x)} x {int(tile_size_y)} m.",
        )
        return {"FINISHED"}


class CRASHDAYTILES_OT_export_p3d_tiles(bpy.types.Operator):
    bl_idname = "crashday_tiles.export_p3d_tiles"
    bl_label = "Export Tiles to P3D"
    bl_description = "Export generated tile geometry; generic source names become native main meshes"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            result = p3d_export.export_generated_tiles(context, context.scene.crashday_tile_splitter)
        except (OSError, ValueError, AssertionError, struct.error) as error:
            self.report({"ERROR"}, "P3D export refused: {}".format(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Wrote {} P3Ds ({} bytes){}; originals unchanged".format(
            result["files"], result["bytes"],
            " + audited manifest" if result["manifest"] else ""))
        if result.get("auto_main_fragments"):
            self.report({"WARNING"}, "{} generic source fragment(s) grouped into "
                        "native main meshes; review manifest provenance".format(
                            result["auto_main_fragments"]))
        return {"FINISHED"}



class CRASHDAYTILES_OT_export_source_images(bpy.types.Operator):
    bl_idname = "crashday_tiles.export_source_images"
    bl_label = "Export Referenced Source Images to TGA"
    bl_description = "Opt-in actual Blender tile image conversion; writes TGAs and manifest, NOT unverified .tex shaders"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            result = texture_export.export_referenced_images(
                context, context.scene.crashday_tile_splitter)
        except (OSError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, "Source TGA export refused: {}".format(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Wrote {} real image TGA(s), {} bytes; originals unchanged".format(
            result["images"], result["bytes"]))
        if result["unresolved"]:
            self.report({"WARNING"}, "{} named P3D textures have no Blender image "
                        "source; see source TGA manifest".format(len(result["unresolved"])))
        return {"FINISHED"}


class CRASHDAYTILES_OT_export_tile_previews(bpy.types.Operator):
    bl_idname = "crashday_tiles.export_tile_previews"
    bl_label = "Render Native Overhead Tile Previews"
    bl_description = "Bake actual top-down native CFL thumbnail TGAs with safe cache; does not touch the game"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            result = preview_render.export_tile_previews(
                context, context.scene.crashday_tile_splitter)
        except (OSError, ValueError, RuntimeError, AssertionError, struct.error) as error:
            self.report({"ERROR"}, "Preview export refused: {}".format(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Tile previews: {} rendered, {} unchanged cached ({} tiles).".format(
            result["rendered"], result["cache_hits"], result["tiles"]))
        return {"FINISHED"}


# BEGIN shared native sidebar sizing
class _SidebarLayout:
    """Estimate native UI text demand; use ordinary Blender layout controls.

    Blender exposes region width, not per-widget clipping. Keep complete
    label/value pairs together when their estimated minima fit, otherwise
    give each a full row. Measurements are conservative, not pixel promises.
    """
    def __init__(self, context, measure=None):
        prefs = context.preferences
        self.scale = float(getattr(prefs.system, "ui_scale", 0) or
                           getattr(prefs.view, "ui_scale", 1) or 1)
        self.scale = max(0.5, self.scale)
        self.width = max(1, float(context.region.width) - 36*self.scale)
        if measure is None:
            import blf
            try:
                points = prefs.ui_styles[0].widget.points
            except (AttributeError, IndexError):
                points = 11
            size = max(1, int(round(points*self.scale)))
            try:
                blf.size(0, size, 72)
            except TypeError:
                blf.size(0, size)
            measure = lambda text: blf.dimensions(0, text)[0]
        self.measure = measure

    def demand(self, text, chrome=12):
        return max(1, self.measure(str(text)) + chrome*self.scale)

    def _prop_parts(self, data, name, label=None):
        prop = data.bl_rna.properties[name]
        label = prop.name if label is None else label
        value = getattr(data, name)
        if prop.type == "ENUM":
            item = prop.enum_items.get(value)
            value = item.name if item else value
        elif prop.type == "FLOAT":
            value = ("{:." + str(prop.precision) + "f}").format(value)
        # Native arrows, picker, padding and possible unit suffix.
        chrome = 44 if prop.type in {"FLOAT", "INT", "ENUM"} else 30
        return label, self.demand(label), max(56*self.scale, self.demand(value, chrome))

    def prop(self, layout, data, name, label=None, width=None):
        width = self.width if width is None else max(1, width)
        label, left, right = self._prop_parts(data, name, label)
        if left + right + 4*self.scale <= width:
            # Reserve the value's own readable minimum; don't waste its
            # surplus while the label is clipped. Once both fit, split surplus.
            factor = (left + (width-left-right)*0.5)/width
            row = layout.split(factor=max(.01, min(.99, factor)), align=True)
            row.label(text=label)
            row.prop(data, name, text="")
        else:
            col = layout.column(align=True)
            self.label(col, label, width=width)
            col.prop(data, name, text="")

    def pair(self, layout, data, first, second, width=None):
        width = self.width if width is None else max(1, width)
        a = sum(self._prop_parts(data, first)[1:]) + 4*self.scale
        b = sum(self._prop_parts(data, second)[1:]) + 4*self.scale
        gap = 8*self.scale
        if a+b+gap <= width:
            left = a + (width-a-b-gap)*0.5
            row = layout.split(factor=left/(width-gap), align=True)
            self.prop(row.column(align=True), data, first, width=left)
            self.prop(row.column(align=True), data, second, width=width-gap-left)
        else:
            self.prop(layout, data, first, width=width)
            self.prop(layout, data, second, width=width)

    def label(self, layout, text, icon="NONE", width=None):
        width = self.width if width is None else max(1, width)
        # Wrap informational text instead of silently dropping its ending.
        # Preserve long indivisible tokens as character chunks.
        budget = max(1, width - (24 if icon != "NONE" else 8)*self.scale)
        lines, current = [], ""
        for word in str(text).split():
            joined = current + " " + word if current else word
            if self.measure(joined) <= budget:
                current = joined
                continue
            if current:
                lines.append(current)
                current = ""
            for char in word:
                if current and self.measure(current + char) > budget:
                    lines.append(current)
                    current = ""
                current += char
        if current or not lines:
            lines.append(current)
        for i, line in enumerate(lines):
            layout.label(text=line, icon=icon if i == 0 else "NONE")

    def action_pair(self, layout, first, second, width=None):
        """Return two layout slots sized for their actual button captions."""
        width = self.width if width is None else max(1, width)
        a, b = self.demand(first, 34), self.demand(second, 34)
        if a+b+8*self.scale > width:
            col = layout.column(align=True)
            return col, col
        factor = (a + (width-a-b)*0.5)/width
        row = layout.split(factor=max(.01,min(.99,factor)), align=True)
        return row.column(align=True), row.column(align=True)
# END shared native sidebar sizing


class VIEW3D_PT_crashday_tile_splitter(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_crashday_tile_splitter"
    bl_label = "Crashday Tiles"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crashday"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        settings = context.scene.crashday_tile_splitter
        ui = _SidebarLayout(context)

        col = layout.column(align=True)
        ui.label(col, "Split")
        ui.prop(col, settings, "physics_diagonal_limit")
        ui.pair(col, settings, "tile_size_x", "tile_size_y")
        try:
            radius = math.hypot(float(settings.tile_size_x) * 0.5, float(settings.tile_size_y) * 0.5)
            ui.label(col, f"Center to corner: {radius:.1f} m", icon="INFO")
        except Exception:
            pass
        ui.prop(col, settings, "grid_origin")
        ui.pair(col, settings, "grid_shift_x", "grid_shift_y")
        col.prop(settings, "use_modifiers")
        col.prop(settings, "create_tile_collections")
        col.prop(settings, "center_tile_origins")
        col.prop(settings, "delete_originals")
        ui.prop(col, settings, "output_collection_name")
        col.operator(CRASHDAYTILES_OT_split_selected.bl_idname, icon="MOD_BOOLEAN")

        layout.separator()

        col = layout.column(align=True)
        ui.label(col, "P3D export (strict source naming)")
        ui.prop(col, settings, "export_directory")
        ui.prop(col, settings, "export_file_prefix")
        col.prop(settings, "export_selected_only")
        col.prop(settings, "use_floor_level_empty")
        col.prop(settings, "write_export_manifest")
        col.operator(CRASHDAYTILES_OT_export_p3d_tiles.bl_idname, icon="EXPORT")
        ui.label(col, "Generic source meshes export as native main.")
        col.operator(CRASHDAYTILES_OT_export_source_images.bl_idname, icon="IMAGE_DATA")
        ui.label(col, "Opt-in original image -> TGA sidecars only.")
        col.operator(CRASHDAYTILES_OT_export_tile_previews.bl_idname, icon="RENDER_STILL")
        ui.label(col, "Overhead native 20/40m CFL thumbnail TGAs.")
        ui.label(col, "Original scene preserved; caches identical tiles.")
        ui.label(col, "Native .tex shader files NOT generated.")
        ui.label(col, "No overwrite; rollback on export errors.")


        layout.separator()
        box = layout.box()
        ui.label(box, "Cuts on Blender X/Y. Z stays height.")
        ui.label(box, "Authoring radius is provisional, not game-verified.")
        ui.label(box, "Independent source meshes/UVs/materials retained.")


classes = (
    CrashdayTileSplitterSettings,
    CRASHDAYTILES_OT_split_selected,
    CRASHDAYTILES_OT_export_p3d_tiles,
    CRASHDAYTILES_OT_export_source_images,
    CRASHDAYTILES_OT_export_tile_previews,
    VIEW3D_PT_crashday_tile_splitter,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.crashday_tile_splitter = PointerProperty(type=CrashdayTileSplitterSettings)


def unregister():
    if hasattr(bpy.types.Scene, "crashday_tile_splitter"):
        del bpy.types.Scene.crashday_tile_splitter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
