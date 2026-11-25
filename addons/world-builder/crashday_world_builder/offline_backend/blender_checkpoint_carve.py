"""Source-only Blender adapter for a targeted, non-destructive checkpoint carve.

Reuse the qualified Tile Splitter's world transform, BMesh clip and metadata
helpers. Not registered, packaged or installed; no game or file writes.
Caller supplies the loaded Splitter module and a display prefix. New keys
can be allocated automatically from the current scene. Bounds, not these
opaque keys, set placement.
"""
import math
import re

from .checkpoint_macro_carve import plan_checkpoint_carve, plan_checkpoint_batch


def allocate_carve_key_offset(plan, occupied_keys, tile_key):
    """Choose an unused canonical four-digit index offset for all pieces.

    Prefer (0,0), then nonnegative offsets, then negative offsets. Existing
    keys are opaque IDs: their indices do not imply physical placement.
    """
    origins = [tuple(p["cell_origin"]) for p in plan["pieces"]]
    if not origins or len(set(origins)) != len(origins):
        raise ValueError("Invalid carve piece origins")
    limits = [(max(-9999 - p[a] for p in origins),
               min(9999 - p[a] for p in origins)) for a in (0, 1)]
    if any(lo > hi for lo, hi in limits):
        raise ValueError("Carve tile keys exceed supported four-digit indices")
    occupied = {k for k in occupied_keys if isinstance(k, str)}

    def candidates(lo, hi):
        yield from range(max(lo, 0), hi + 1)
        yield from range(min(hi, -1), lo - 1, -1)

    for y in candidates(*limits[1]):
        for x in candidates(*limits[0]):
            if all(tile_key(x + dx, y + dy) not in occupied
                   for dx, dy in origins):
                return (x, y)
    raise ValueError("No unused four-digit tile-key offset for carve")


def _scene_checkpoint_selection(scene_objects, source_key, world_xy):
    """Resolve ALL meshes of one selected tile and a precise native 20m cell.

    Reject inconsistent metadata, previously superseded parents and ambiguous
    grid-edge clicks; do not depend on the ray-hit object's identity.
    """
    objects = tuple(scene_objects)
    if not isinstance(source_key, str) or not source_key:
        raise ValueError("Select one generated source tile")
    group = [o for o in objects if o.get("crashday_tile_key") == source_key]
    if not group or any(o.type != "MESH" for o in group):
        raise ValueError("Source tile must contain only scene meshes")
    if any(o.get("crashday_carve_parent_key") == source_key for o in objects):
        raise ValueError("Source tile already carved; select a generated remainder")
    names = ("min_x", "max_x", "min_y", "max_y", "size_x_m", "size_y_m")
    def metadata(o):
        values = tuple(o.get("crashday_tile_" + name) for name in names)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("Missing or invalid source tile bounds")
        return values
    x0, x1, y0, y1, w, h = metadata(group[0])
    if any(metadata(o) != (x0, x1, y0, y1, w, h) for o in group):
        raise ValueError("Inconsistent source tile metadata")
    if abs(x1-x0-w) > 1e-6 or abs(y1-y0-h) > 1e-6 or w <= 0 or h <= 0:
        raise ValueError("Source tile size does not match bounds")
    if w == h == 20:
        raise ValueError("Source is already a 20m checkpoint-sized tile")
    if (not isinstance(world_xy, (tuple, list)) or len(world_xy) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in world_xy)):
        raise ValueError("Choose a finite world XY checkpoint location")
    x, y = world_xy
    if not (x0 < x < x1 and y0 < y < y1):
        raise ValueError("Choose a checkpoint inside the selected source tile")
    gx, gy = (x-x0)/20, (y-y0)/20
    if min(abs(gx-round(gx)), abs(gy-round(gy))) < 0.0005:
        raise ValueError("Click farther inside the 20m cell, away from its edge")
    return group, (w, h), (x0, y0), (int(gx), int(gy))


def plan_scene_checkpoint_carve(scene_objects, source_key, world_xy, tile_key):
    objects = tuple(scene_objects)
    group, (w, h), origin, cell = _scene_checkpoint_selection(
        objects, source_key, world_xy)
    plan = plan_checkpoint_carve(w, h, cell, origin, existing_source=True)
    offset = allocate_carve_key_offset(plan,
        [o.get("crashday_tile_key") for o in objects], tile_key)
    return {"objects": group, "plan": plan, "key_offset": offset}


def scene_checkpoint_pick_signature(scene_objects, source_key):
    """Bind pending picks to the current source tile's logical frame."""
    group = [o for o in scene_objects if o.get("crashday_tile_key") == source_key]
    if not group:
        raise ValueError("Source tile no longer exists; cancel and pick again")
    return tuple(group[0].get("crashday_tile_" + field) for field in
                 ("min_x", "max_x", "min_y", "max_y", "size_x_m", "size_y_m"))


def require_scene_checkpoint_pick_signature(scene_objects, source_key, signature):
    if scene_checkpoint_pick_signature(scene_objects, source_key) != signature:
        raise ValueError("Source tile bounds changed; cancel and pick again")


def toggle_scene_checkpoint_pick(scene_objects, source_key, world_xy, picks):
    """Return new ordered picks, one source tile per batch, without mutation."""
    _, _, _, cell = _scene_checkpoint_selection(
        scene_objects, source_key, world_xy)
    result = list(picks)
    if result and result[0][0] != source_key:
        raise ValueError("Finish this tile's batch before choosing another tile")
    pick = (source_key, cell)
    if pick in result:
        result.remove(pick)
    else:
        if len(result) >= 128:
            raise ValueError("Choose at most 128 checkpoint cells per tile")
        result.append(pick)
    return result


def plan_scene_checkpoint_batch(scene_objects, source_key, cells, tile_key):
    objects = tuple(scene_objects)
    # Resolve fresh metadata and lineage; cell centers are checked against
    # current dimensions again by the pure batch planner.
    group = [o for o in objects if o.get("crashday_tile_key") == source_key]
    if not group:
        raise ValueError("Source tile must contain only scene meshes")
    x0, y0 = (group[0].get("crashday_tile_min_x"),
              group[0].get("crashday_tile_min_y"))
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x0,y0)):
        raise ValueError("Missing or invalid source tile bounds")
    group, (w,h), origin, _ = _scene_checkpoint_selection(
        objects, source_key, (x0+10, y0+10))
    plan = plan_checkpoint_batch(w, h, cells, origin, existing_source=True)
    offset = allocate_carve_key_offset(plan,
        [o.get("crashday_tile_key") for o in objects], tile_key)
    return {"objects": group, "plan": plan, "key_offset": offset}


def carve_scene_checkpoint_batch(context, splitter, source_key, cells,
                                 use_modifiers=False):
    chosen = plan_scene_checkpoint_batch(context.scene.objects, source_key,
                                         cells, splitter.tile_key)
    plan = chosen["plan"]
    return _carve_planned(context, chosen["objects"], splitter, plan, "batch",
                          chosen["key_offset"], use_modifiers, 0.00001,
                          source_key)


def carve_scene_checkpoint(context, splitter, source_key, world_xy,
                           use_modifiers=False):
    """Non-destructive scene action; original source remains for explicit export scope."""
    chosen = plan_scene_checkpoint_carve(context.scene.objects, source_key,
                                         world_xy, splitter.tile_key)
    plan = chosen["plan"]
    return carve_checkpoint(context, chosen["objects"], splitter,
                            *plan["authoring_dimensions_m"],
                            plan["checkpoint_cell"], plan["world_origin_xy_m"],
                            "tile", chosen["key_offset"], use_modifiers,
                            source_tile_key=source_key)


def carve_checkpoint(context, source_objects, splitter, width_m, height_m,
                     checkpoint_cell, world_origin_xy, key_prefix,
                     key_offset=None, use_modifiers=False, epsilon=0.00001,
                     source_tile_key=None):
    plan = plan_checkpoint_carve(width_m, height_m, checkpoint_cell,
                                 world_origin_xy, existing_source=True)
    return _carve_planned(context, source_objects, splitter, plan, key_prefix,
                          key_offset, use_modifiers, epsilon, source_tile_key)


def carve_checkpoint_batch(context, source_objects, splitter, width_m, height_m,
                           checkpoint_cells, world_origin_xy, key_prefix,
                           key_offset=None, use_modifiers=False, epsilon=0.00001,
                           source_tile_key=None):
    """Carve all cells in one non-destructive transaction from original meshes."""
    plan = plan_checkpoint_batch(width_m, height_m, checkpoint_cells,
                                 world_origin_xy, existing_source=True)
    return _carve_planned(context, source_objects, splitter, plan, key_prefix,
                          key_offset, use_modifiers, epsilon, source_tile_key)


def _carve_planned(context, source_objects, splitter, plan, key_prefix,
                   key_offset, use_modifiers, epsilon, source_tile_key):
    import bpy
    import bmesh
    from mathutils import Vector

    if context.mode != "OBJECT":
        raise ValueError("Checkpoint carving requires Object Mode")
    if not isinstance(key_prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", key_prefix):
        raise ValueError("Supply a unique ASCII carve key prefix")
    if not math.isfinite(epsilon) or not 0.0000001 <= epsilon <= 0.001:
        raise ValueError("Invalid carve tolerance")
    objects = tuple(source_objects)
    if not objects or len({obj.as_pointer() for obj in objects}) != len(objects):
        raise ValueError("Select distinct source mesh objects")
    if any(obj.type != "MESH" or context.scene.objects.get(obj.name) != obj
           for obj in objects):
        raise ValueError("Every source must be a mesh in the current scene")
    ox, oy = plan["world_origin_xy_m"]
    width_m, height_m = plan["authoring_dimensions_m"]
    outer = (ox, ox + width_m, oy, oy + height_m)
    if key_offset is None:
        key_offset = allocate_carve_key_offset(plan,
            [o.get("crashday_tile_key") for o in context.scene.objects],
            splitter.tile_key)
    if (not isinstance(key_offset, (tuple, list)) or len(key_offset) != 2 or
            any(type(x) is not int for x in key_offset)):
        raise ValueError("Supply an integer tile-key namespace offset")
    indices = [(key_offset[0] + piece["cell_origin"][0],
                key_offset[1] + piece["cell_origin"][1]) for piece in plan["pieces"]]
    if any(abs(v) > 9999 for pair in indices for v in pair):
        raise ValueError("Carve tile key exceeds supported four-digit indices")
    keys = [splitter.tile_key(*pair) for pair in indices]
    if any(obj.get("crashday_tile_key") in keys for obj in context.scene.objects):
        raise ValueError("Carve output tile key already exists")
    meshes, generated, collections = [], [], []
    by_piece = [[] for _ in keys]

    def remove_shared_boundary_faces(mesh, bounds):
        # A vertical face exactly on a shared cut belongs to the positive-axis
        # side only. Outer edges are retained. Compare representable float32
        # plane positions, not a broad tolerance that can delete nearby faces.
        xmax = Vector((bounds[1], 0, 0)).x
        ymax = Vector((0, bounds[3], 0)).y
        cut_x = bounds[1] != outer[1]
        cut_y = bounds[3] != outer[3]
        if not (cut_x or cut_y):
            return
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            drop = [f for f in bm.faces if
                    (cut_x and all(v.co.x == xmax for v in f.verts)) or
                    (cut_y and all(v.co.y == ymax for v in f.verts))]
            if drop:
                bmesh.ops.delete(bm, geom=drop, context="FACES_ONLY")
                loose = [e for e in bm.edges if not e.link_faces]
                if loose:
                    bmesh.ops.delete(bm, geom=loose, context="EDGES")
                loose = [v for v in bm.verts if not v.link_edges]
                if loose:
                    bmesh.ops.delete(bm, geom=loose, context="VERTS")
                bm.to_mesh(mesh)
                mesh.update(calc_edges=True)
        finally:
            bm.free()

    try:
        for source in objects:
            world = splitter.make_world_mesh(context, source, use_modifiers)
            meshes.append(world)
            if not world.polygons:
                raise ValueError("Source has no faces: " + source.name)
            for v in world.vertices:
                x, y, z = v.co
                if (not all(math.isfinite(c) for c in (x, y, z)) or
                        x < outer[0] - epsilon or x > outer[1] + epsilon or
                        y < outer[2] - epsilon or y > outer[3] + epsilon):
                    raise ValueError("Source geometry outside carve region: " + source.name)
            for i, piece in enumerate(plan["pieces"]):
                bounds = piece["world_bounds_xy_m"]
                fragment = world.copy()
                meshes.append(fragment)
                if not splitter.bisect_keep_inside_xy(fragment, *bounds, epsilon):
                    continue
                remove_shared_boundary_faces(fragment, bounds)
                if not fragment.polygons:
                    continue
                xmin, xmax, ymin, ymax = bounds
                origin = Vector(((xmin + xmax) / 2, (ymin + ymax) / 2, 0))
                splitter.move_mesh_origin_without_moving_world(fragment, origin)
                obj = bpy.data.objects.new(source.name + "_" + keys[i], fragment)
                generated.append(obj)
                obj.location = origin
                splitter.set_tile_metadata(obj, *indices[i],
                                           *piece["physical_dimensions_m"], *bounds)
                obj["crashday_tile_key"] = keys[i]
                obj["crashday_source_mesh_name"] = source.get("crashday_source_mesh_name", source.name)
                obj["crashday_source_mesh_data_name"] = source.get("crashday_source_mesh_data_name", source.data.name)
                if "crashday_p3d_flags" in source:
                    obj["crashday_p3d_flags"] = source["crashday_p3d_flags"]
                obj["crashday_carve_role"] = piece["role"]
                if source_tile_key is not None:
                    obj["crashday_carve_parent_key"] = source_tile_key
                by_piece[i].append(obj)
        if any(not group for group in by_piece):
            raise ValueError("Carve contains a geometry-free region; no partial result created")
        master = bpy.data.collections.new("Checkpoint carve " + key_prefix)
        collections.append(master)
        context.scene.collection.children.link(master)
        for key, group in zip(keys, by_piece):
            child = bpy.data.collections.new(key)
            collections.append(child)
            master.children.link(child)
            for obj in group:
                child.objects.link(obj)
        return {"plan": plan, "tile_keys": keys, "objects_by_piece": by_piece,
                "collection": master, "replaced_source_key": source_tile_key}
    except Exception:
        for obj in reversed(generated):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in reversed(collections):
            bpy.data.collections.remove(collection)
        raise
    finally:
        for mesh in meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
