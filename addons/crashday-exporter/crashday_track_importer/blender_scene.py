"""Crashday -> Blender P3D track-scene prototype, callable from Blender add-on or CLI.

Execute using the Blender version desired for the output .blend (2.80+ tested):
 blender.exe --background --factory-startup --python tools/blender_scene.py -- \
   --track "I:\\...\\user\\example.trk" --game "I:\\...\\Crashday" \
   --p3d-parser ".cache\\deps\\p3d-addon\\crashday\\p3d.py" \
   --output "out\\example_preview.blend"

Preview fidelity only: native height grid, real P3D geometry, CFL quarter
restrictions and world-shared quarter-edge constraints are implemented.
FlyX mutable terrain updates, vegetation and exact dynamic drop/rotation
behavior are NOT yet reproduced. Original game/tracks remain read-only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import random
import importlib.util
import io
import json
import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector

if __package__:
    from .asset_library import (AssetLibrary, AssetError, candidate_model_keys,
                                suggest_installed_mods_for_missing_cfl,
                                suggest_installed_mods_for_missing_assets)
    from .geometry import TerrainGrid, TileHeightSurface, SharedBorderVault, tile_placement, dynamic_position, dynamic_mesh_visual_ground_lift, plan_vegetation_preview, highest_triangle_height_at_xy, p3d_material_is_smooth, is_out_of_bounds_dynamic
    from .trk_probe import read_track, encode_float32_height_grid, bgra_preview_to_png
    from .import_diagnostics import format_import_report, issue_count
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from asset_library import (AssetLibrary, AssetError, candidate_model_keys,
                               suggest_installed_mods_for_missing_cfl,
                               suggest_installed_mods_for_missing_assets)
    from geometry import TerrainGrid, TileHeightSurface, SharedBorderVault, tile_placement, dynamic_position, dynamic_mesh_visual_ground_lift, plan_vegetation_preview, highest_triangle_height_at_xy, p3d_material_is_smooth, is_out_of_bounds_dynamic
    from trk_probe import read_track, encode_float32_height_grid, bgra_preview_to_png
    from import_diagnostics import format_import_report, issue_count


class Reporter:
    def report(self, *_args):
        pass


def read_p3d_class(path: Path):
    spec = importlib.util.spec_from_file_location("wurunduk_p3d_external", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Invalid P3D dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_collection(name, parent=None):
    col = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(col)
    return col


def terrain_object(grid: TerrainGrid, collection, visual_offset_m=0.12,
                   name="TERRAIN - CFL border-adjusted heightmap",
                   material=None, uv_repeat_m=None):
    """Create a visual ground underlay, or a zero-offset source height mesh.

    A small downward render-only bias prevents parts of the 5-metre terrain
    triangulation from protruding through differently tessellated P3D fields.
    The unmodified source geometry is stored separately, hidden by default.
    """
    vertices = []
    for j in range(grid.ny):
        for i in range(grid.nx):
            vertices.append((i*5., grid.tiles_h*20-j*5.,
                             grid.height_at_index(i, j)-visual_offset_m))
    faces = []
    for j in range(grid.ny-1):
        for i in range(grid.nx-1):
            n = j*grid.nx+i
            # inverted north->south row order means two triangles wind upwards
            faces.append((n, n+grid.nx, n+1))
            faces.append((n+1, n+grid.nx, n+grid.nx+1))
    mesh = bpy.data.meshes.new("Original CDTRK Height Grid")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    if uv_repeat_m is not None:
        if uv_repeat_m <= 0:
            raise ValueError("Terrain preview UV repeat distance must be positive")
        # Field.p3d uses (local_x + 10)/20 and (local_y + 10)/20.
        # World-space 20m repeat matches 1x1 field tiles at 20m boundaries.
        # This is a VISUAL fallback where a P3D tile leaves a gap, not proof
        # the original game uses the same texture on every ground surface.
        uv_layer = mesh.uv_layers.new(name="20m ground fallback UV")
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                v = mesh.vertices[mesh.loops[li].vertex_index].co
                uv_layer.data[li].uv = (v.x / uv_repeat_m, v.y / uv_repeat_m)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj["grid_samples"] = [grid.nx, grid.ny]
    obj["terrain_scale_m"] = 5.
    obj["baseline_original_value"] = grid.zero
    obj["visual_underlay_offset_m"] = visual_offset_m
    obj["height_origin"] = "TRK first north-west float32 sample, relative Z=0"
    if material is None:
        material = bpy.data.materials.new("Terrain - background fill")
        material.diffuse_color = (0.14,0.25,0.12,1)
    mesh.materials.append(material)
    return obj


def material_for_type(mat_type: str, texture_name: str, library: AssetLibrary,
                      materials: dict, missing_tex: set, model_ref=None):
    # Identically named textures in different mods may have different bytes.
    # A material shared between their P3Ds must not silently reuse one image.
    scope = library._source_mod_root(model_ref) if model_ref is not None else None
    key = (mat_type, texture_name, scope)
    if key in materials:
        return materials[key]
    mat = bpy.data.materials.new(f"CD {mat_type} : {texture_name}")
    mat.use_nodes = True
    # Preserve the *resolved* native texture shader rather than guessing
    # that every terrain image is plain diffuse or that alpha is opacity.
    # These source facts do not modify the approximation in Principled BSDF.
    try:
        shader_info = library.texture_shader_info(str(texture_name), model_ref)
        if shader_info is not None:
            mat["crashday_tex_source"] = shader_info.tex_file
            mat["crashday_tex_material_class"] = shader_info.material_type
            mat["crashday_tex_authored_shader"] = shader_info.authored_shader
            mat["crashday_tex_effective_shader"] = shader_info.effective_shader
            mat["crashday_tex_shader_source"] = shader_info.shader_source
            mat["crashday_tex_has_alpha"] = shader_info.has_alpha
            mat["crashday_tex_parameters"] = " | ".join(shader_info.parameters)
            if shader_info.trailing_tokens:
                mat["crashday_tex_trailing_tokens"] = " | ".join(shader_info.trailing_tokens)
    except (AssetError, ValueError) as exc:
        mat["crashday_tex_resolution_error"] = str(exc)
    mat["crashday_tex_preview_semantics"] = "Native shader only recorded; not implemented in BSDF"

    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Roughness"].default_value = 0.8
        if "METAL" in mat_type:
            bsdf.inputs["Metallic"].default_value = 0.35
        # Resolve .tga/.dds as one decision: stock or explicitly chosen
        # texture first; otherwise the P3D's own mod family. A foreign .tga
        # must not poison a legitimate same-mod .dds or vice versa.
        ref = library.texture_image_for_model(texture_name, model_ref)
        if ref is not None:
            tex_origin = library.describe_source(ref)
            mat["source_texture_path"] = tex_origin["package_or_file"]
            mat["source_texture_member"] = tex_origin["archive_member"]
            mat["source_texture_selection"] = tex_origin["reason"]
            try:
                if library.texture_cache_dir is None and ref.member is not None:
                    raise AssetError("No cache directory available for CPK-only texture")
                local_path = (ref.local_texture_path(library.texture_cache_dir)
                              if ref.member is not None else ref.source)
                if library.pack_imported_textures:
                    # A locally loaded image may already be shared with objects
                    # in the owner's existing scene. Create/reuse an image only
                    # within THIS import so packing cannot mutate their data.
                    image_key = str(local_path).casefold()
                    image = library.imported_images.get(image_key)
                    if image is None:
                        image = bpy.data.images.load(str(local_path), check_existing=False)
                        image.pack()
                        library.imported_images[image_key] = image
                else:
                    image = bpy.data.images.load(str(local_path), check_existing=True)
                    if ref.member is not None and image.packed_file is None:
                        # CPK-extracted cache is temporary: always pack those.
                        image.pack()
                node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                node.image = image
                mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
                # Do NOT use alpha as opacity for every DDS. Crashday often
                # stores specular/reflection masks in track texture alpha.
                # Only verified .tex alpha-test shaders are safe to cut out.
                alpha_mode = library.texture_alpha_mode(texture_name, model_ref)
                if alpha_mode and node.outputs.get("Alpha") and bsdf.inputs.get("Alpha"):
                    mat.node_tree.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
                    try:
                        if hasattr(mat, "blend_method"):
                            mat.blend_method = "CLIP"
                        elif hasattr(mat, "surface_render_method"):
                            # Blender 4.x renamed the transparency control;
                            # dither is the closest safe preview fallback.
                            mat.surface_render_method = "DITHERED"
                        if hasattr(mat, "alpha_threshold"):
                            mat.alpha_threshold = 0.5
                        if (alpha_mode == "CLIP_DOUBLE_SIDED"
                                and hasattr(mat, "use_backface_culling")):
                            mat.use_backface_culling = False
                    except (AttributeError, ValueError, TypeError):
                        # Preserve visible diffuse materials if an API enum
                        # changes; do not fail the entire track import.
                        pass
                    mat["crashday_tex_alpha_preview"] = alpha_mode
            except Exception:
                missing_tex.add(str(texture_name))
        else:
            # The P3D model can still load with a clearly visible fallback.
            missing_tex.add(str(texture_name))
            bsdf.inputs["Base Color"].default_value = (0.48,0.48,0.48,1)
    materials[key] = mat
    return mat


def assemble_tile(name, row, col, tile_entry, t, grid, definition, model,
                  lib, out_collection, materials, missing_tex):
    field_id, rotation, mirrored, tile_height = tile_entry
    placement = tile_placement(
        col,row,t.height,definition.width,definition.depth,rotation, bool(mirrored))
    surface = TileHeightSurface(grid, placement, definition.width,
                                definition.depth, definition.bend_rules)
    vertices, faces, uv_faces, face_materials, face_smoothing = [], [], [], [], []
    use_materials = {}
    for sub in model.meshes:
        offset = len(vertices)
        for vertex in sub.vertices:
            local_x, local_y = vertex[0]+sub.pos[0], vertex[1]+sub.pos[1]
            x, y = placement.transform_xy(local_x, local_y)
            # P3D vertex Z=0 centered; model's bounding height extends +/- height/2.
            # Bottom of P3D aligns with terrain after adding half the model height.
            # Local CFL quarter rules deform the model over the shared-edge
            # height grid. Exact FlyX iterative height updates remain a TODO.
            z = (vertex[2]+sub.pos[2] + model.height/2. +
                 tile_height + surface.sample(local_x, local_y))
            vertices.append((x,y,z))
        for polygon in sub.polys:
            inds = [offset+polygon.p1, offset+polygon.p2, offset+polygon.p3]
            uvs = [(polygon.u1, polygon.v1), (polygon.u2, polygon.v2), (polygon.u3, polygon.v3)]
            if mirrored:
                inds.reverse()
                uvs.reverse()
            faces.append(inds)
            uv_faces.append(uvs)
            material_key = (str(polygon.material), str(polygon.texture))
            if material_key not in use_materials:
                use_materials[material_key] = len(use_materials)
            face_materials.append(use_materials[material_key])
            face_smoothing.append(p3d_material_is_smooth(polygon.material))
    if not vertices or not faces:
        return None
    mesh = bpy.data.meshes.new(f"{name} {col} {row}")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for key in use_materials:
        mesh.materials.append(material_for_type(*key, lib, materials, missing_tex,
                                                model_ref=definition.model))
    uv_layer = mesh.uv_layers.new(name="Crashday UV")
    for polygon, uv in zip(mesh.polygons,uv_faces):
        for li, coords in zip(polygon.loop_indices, uv):
            uv_layer.data[li].uv = coords
    for polygon, material_index, smooth in zip(mesh.polygons, face_materials, face_smoothing):
        polygon.material_index = material_index
        polygon.use_smooth = smooth
    obj = bpy.data.objects.new(f"tile/{col:03d}/{row:03d} {name}",mesh)
    out_collection.objects.link(obj)
    obj["tile_file"] = name
    obj["tile_grid"] = [col, row]
    obj["rotation_quarters"] = rotation
    obj["is_mirrored"] = bool(mirrored)
    obj["base_height_raw"] = tile_height
    obj["cfl_bend_rules"] = definition.bend_rules
    # Each imported tile carries its real CFL and P3D origins. This matters
    # when same-named Workshop resources differ and an override was selected.
    for role, ref in (("cfl", definition.source_ref), ("p3d", definition.model)):
        if ref is not None:
            origin = lib.describe_source(ref)
            obj["source_" + role + "_path"] = origin["package_or_file"]
            obj["source_" + role + "_member"] = origin["archive_member"]
            obj["source_" + role + "_selection"] = origin["reason"]
    obj["bend_fidelity"] = "shared CFL border constraints + local interpolation; FlyX mutable updates not yet equivalent"
    return obj


def build_dynamic_mesh(name, model, lib, materials, missing_tex, prefix="CDO",
                       model_ref=None):
    """One reusable P3D mesh datablock for all instances of a CDO type.

    Align the lowest rendered P3D vertex to the visual ground plane. The
    nominal model height is not an exact mesh bounding box for every shipped
    CDO P3D. Engine collision/contact/drop and yaw remain unverified.
    """
    visual_lift = dynamic_mesh_visual_ground_lift(model)
    verts, faces, uv_faces, face_materials, face_smoothing = [], [], [], [], []
    used = {}
    for sub in model.meshes:
        offset = len(verts)
        for vertex in sub.vertices:
            verts.append((vertex[0]+sub.pos[0],
                          vertex[1]+sub.pos[1],
                          vertex[2]+sub.pos[2]+visual_lift))
        for polygon in sub.polys:
            faces.append((offset+polygon.p1, offset+polygon.p2, offset+polygon.p3))
            uv_faces.append(((polygon.u1,polygon.v1),
                             (polygon.u2,polygon.v2),
                             (polygon.u3,polygon.v3)))
            key = (str(polygon.material), str(polygon.texture))
            if key not in used:
                used[key] = len(used)
            face_materials.append(used[key])
            face_smoothing.append(p3d_material_is_smooth(polygon.material))
    if not verts or not faces:
        raise ValueError("Dynamic P3D has no visible mesh faces: {}".format(name))
    mesh = bpy.data.meshes.new(prefix + " " + name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for key in used:
        mesh.materials.append(material_for_type(*key, lib, materials, missing_tex,
                                                model_ref=model_ref))
    layer = mesh.uv_layers.new(name="Crashday UV")
    for poly, uvs in zip(mesh.polygons, uv_faces):
        for loop_idx, uv in zip(poly.loop_indices, uvs):
            layer.data[loop_idx].uv = uv
    for poly, material_idx, smooth in zip(mesh.polygons, face_materials, face_smoothing):
        poly.material_index = material_idx
        poly.use_smooth = smooth
    return mesh


def add_vegetation_preview(track, library, loaded, placed_tile_indices, grid,
                           tile_objects, p3d, collection, materials, missing_tex, max_instances):
    """Opt-in bounded VEG preview using actual P3D meshes and copy ranges.

    The source supplies .veg min/max copies per tile and CFL zone probability,
    extents and height. We use stable pseudorandom samples in those ranges;
    Crashday's RNG, count allocation and exact vegetation transforms remain
    unknown. Native full-quality P3D faces are never replaced by LODs.
    For CFL Auto Height, project vertically onto this tile's imported faces
    (SDK VegetEdit says TOP of trackpiece), retaining an explicit fallback
    if there is no actual tile-face hit. Preview remains disabled by default.
    """
    if max_instances <= 0:
        # Report authentic CFL definitions even when not spawning any meshes.
        # The absence of preview meshes does not imply missing source content.
        zone_count = sum(len(loaded[track.field_files[track.tiles[n][0]]][0].vegetation)
                         for n in placed_tile_indices)
        return {"preview_enabled": False,
                "source_vegetation_zones": zone_count,
                "candidate_instances": 0,
                "anchored_source_zones": 0,
                "anchored_candidate_instances": 0,
                "selected_anchored_instances": 0,
                "mesh_instances": 0, "unique_veg_models": 0,
                "missing_veg": {}, "missing_veg_p3d": {}}

    veg_definitions = {}
    # Cache same-name VEGs by the actual CFL defining the vegetation (which
    # can differ from the placed tile's CFL due to SAME_VEGETATION_AS). Never
    # merge unrelated Workshop/mod families into one global VEG preview.
    source_definitions = {}
    missing_veg = {}
    missing_p3d = {}
    for n in sorted(placed_tile_indices):
        fid = track.tiles[n][0]
        definition = loaded[track.field_files[fid]][0]
        parent = (getattr(definition, "vegetation_source_ref", None)
                  or getattr(definition, "source_ref", None))
        for patch in definition.vegetation:
            source_key = (patch.veg_file, parent)
            label = "{} @ {}".format(
                patch.veg_file, str(parent.source) if parent else "unscoped")
            if source_key not in source_definitions:
                try:
                    veg = library.vegetation(patch.veg_file, parent_source=parent)
                    source_definitions[source_key] = veg
                    if veg.model is None:
                        missing_p3d[label] = veg.model_file
                except (AssetError, ValueError, OSError) as ex:
                    missing_veg[label] = "{}: {}".format(type(ex).__name__, ex)
                    source_definitions[source_key] = None
            veg_definitions[(n, patch.veg_file)] = source_definitions[source_key]
    plan = plan_vegetation_preview(track, loaded, placed_tile_indices,
                                   veg_definitions, max_instances)
    subset = plan["selected"]
    templates = {}
    count = 0
    height_methods = Counter()
    current_cell = None
    tile_triangles = ()
    # Keep only one cell's triangles in memory. Blender mesh vertices are
    # already in WORLD coordinates and incorporate placed CFL restrictions.
    for priority, n, idx, copy_index, patch, east, north, angle, mirrored in sorted(
            subset, key=lambda item: (item[1], item[0])):
        if n != current_cell:
            current_cell = n
            tile_obj = tile_objects.get(n)
            if tile_obj is None:
                tile_triangles = ()
            else:
                coords = [tuple(v.co) for v in tile_obj.data.vertices]
                tile_triangles = tuple(
                    tuple(coords[i] for i in poly.vertices)
                    for poly in tile_obj.data.polygons if len(poly.vertices) == 3)
        veg = veg_definitions[(n, patch.veg_file)]
        template_key = (patch.veg_file, veg.source_ref)
        if template_key not in templates:
            try:
                if veg.model is None:
                    missing_p3d["{} @ {}".format(patch.veg_file, veg.source_ref.source)] = (
                        veg.model_file)
                    templates[template_key] = (None, veg)
                else:
                    model = p3d.P3D()
                    status = model.read(Reporter(), io.BytesIO(veg.model.read_bytes()))
                    if status != p3d.ImportError.NO_ERROR:
                        raise AssetError("VEG P3D reader failed: " + patch.veg_file)
                    mesh = build_dynamic_mesh(patch.veg_file, model, library,
                                              materials, missing_tex, prefix="VEG",
                                              model_ref=veg.model)
                    templates[template_key] = (mesh, veg)
            except (ValueError, OSError, AssetError) as ex:
                missing_veg["{} @ {}".format(patch.veg_file, veg.source_ref.source)] = (
                    "{}: {}".format(type(ex).__name__, ex))
                templates[template_key] = (None, None)
        mesh, definition = templates[template_key]
        if mesh is None:
            continue
        rng = random.Random(priority + 1)
        scale = rng.uniform(definition.min_scale, definition.max_scale)
        # Most stock VEG files say 'random'; numeric rotation semantics are
        # unknown, so numeric/nonrandom is left at its parent's orientation.
        spin = rng.uniform(-math.pi, math.pi) if definition.rotation == "random" else 0
        if definition.rotation == "random90deg":
            spin = rng.randrange(4) * math.pi / 2
        obj = bpy.data.objects.new("vegetation/{:04d}/{:02d}/{:03d} {}".format(
                                   n, idx, copy_index, patch.veg_file), mesh)
        collection.objects.link(obj)
        if patch.height is None:
            surface_z = highest_triangle_height_at_xy(tile_triangles, east, north)
            if surface_z is None:
                # Explicitly marked fallback, not an asserted native drop.
                surface_z = grid.sample(east, north)
                height_mode = "AUTO_NO_TILE_FACE__HEIGHTMAP_FALLBACK"
            else:
                height_mode = "AUTO_TOP_OF_IMPORTED_TILE_FACE"
        else:
            # Preserve the previously tested numeric-Y preview until a
            # controlled native test proves its exact local/world semantics.
            surface_z = grid.sample(east, north) + patch.height
            height_mode = "NUMERIC_Y__HEIGHTMAP_APPROXIMATION"
        height_methods[height_mode] += 1
        obj.location = (east, north, surface_z + definition.height_offset)
        obj["vegetation_height_placement"] = height_mode
        obj.scale = (-scale if mirrored else scale, scale, scale)
        obj.rotation_euler.z = angle + spin
        obj["veg_file"] = patch.veg_file
        # Preserve the real files selected for same-named vegetation across
        # separately imported mods, including after Blender Save As/reopen.
        for role, source in (("veg", definition.source_ref),
                             ("p3d", definition.model)):
            if source is not None:
                provenance = library.describe_source(source)
                obj["source_" + role + "_path"] = provenance["package_or_file"]
                obj["source_" + role + "_member"] = provenance["archive_member"]
                obj["source_" + role + "_selection"] = provenance["reason"]
        obj["source_cell"] = n
        obj["source_article_index"] = idx + 1
        obj["source_copy_preview_index"] = copy_index
        obj["vegetation_fidelity"] = (
            "APPROXIMATE deterministic seeded preview using VEG min/max copies; "
            "game RNG, copy allocation, tilt and exact native height still unknown")
        count += 1
    return {
        "preview_enabled": True, "source_vegetation_zones": plan["source_vegetation_zones"],
        "candidate_instances": plan["candidate_instances"],
        "anchored_source_zones": plan["anchored_source_zones"],
        "anchored_candidate_instances": plan["anchored_candidate_instances"],
        "selected_anchored_instances": plan["selected_anchored_instances"],
        "selected_instances": len(subset), "mesh_instances": count,
        "unique_veg_models": sum(mesh is not None for mesh, _ in templates.values()),
        "height_placement_counts": dict(height_methods),
        "missing_veg": missing_veg, "missing_veg_p3d": missing_p3d,
        "mode": ("FULL_APPROXIMATE" if max_instances == sys.maxsize
                 else "BOUNDED_QUICK_PREVIEW"),
        "fidelity": (
            "Full source-constrained approximate native P3D vegetation; "
            "game RNG, copy allocation, exact XY/rotation and numeric Y are "
            "NOT verified. Auto Height uses imported tile faces."
            if max_instances == sys.maxsize else
            "Bounded approximate native P3D quick preview; exact game "
            "RNG/placement not verified")
    }


def add_camera(grid, ambience=None, native_ambience_preview=False):
    center=Vector((grid.tiles_w*10.,grid.tiles_h*10.,0))
    width=max(grid.tiles_w*20.,grid.tiles_h*20.)
    camera=bpy.data.cameras.new("CD map preview camera")
    camera.type="ORTHO"
    camera.ortho_scale=width*1.8
    camera.clip_end = max(10000.,width*8.)
    obj=bpy.data.objects.new("CD map preview camera",camera)
    bpy.context.scene.collection.objects.link(obj)
    obj.location=center+Vector((width*.65,-width*.9,width*.85))
    obj.rotation_euler=(center-obj.location).to_track_quat("-Z","Y").to_euler()
    bpy.context.scene.camera=obj
    for area in bpy.context.screen.areas if bpy.context.screen else []:
        if area.type=="VIEW_3D":
            area.spaces.active.region_3d.view_location=center
            area.spaces.active.region_3d.view_distance=width*1.5
            area.spaces.active.clip_end=max(20000., width*4.)
    world=bpy.data.worlds.new("Crashday sky preview")
    world.use_nodes=True
    background=world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value=(0.42,0.51,0.65,1)
    background.inputs["Strength"].default_value=0.9
    bpy.context.scene.world=world
    sun_data=bpy.data.lights.new("Preview sun","SUN")
    sun_data.energy=1.7
    sun_obj=bpy.data.objects.new("Preview sun",sun_data)
    bpy.context.scene.collection.objects.link(sun_obj)
    sun_obj.rotation_euler=(math.radians(25),math.radians(-20),math.radians(20))
    if native_ambience_preview and ambience is not None:
        # Preview only: native .amb colors and sun vector are source-authored,
        # but game gamma, shader mixing, azimuth signs and energy mapping are
        # not yet solved. Keep the tested neutral preview as the default.
        background.inputs["Color"].default_value = tuple(
            channel / 255.0 for channel in ambience.sky_rgb) + (1.0,)
        sun_data.color = tuple(channel / 255.0 for channel in ambience.sun_rgb)
        sun_data.energy = 1.7 * ambience.sun_max_strength
        direction = Vector(ambience.sun_direction)
        if direction.length > 1e-6:
            angle = math.radians(ambience.sun_azimuth_degrees)
            ray = Vector((math.cos(angle)*direction.x - math.sin(angle)*direction.y,
                          math.sin(angle)*direction.x + math.cos(angle)*direction.y,
                          direction.z))
            sun_obj.rotation_euler = ray.to_track_quat("-Z", "Y").to_euler()
        world["lighting_fidelity"] = (
            "EXPERIMENTAL: sampled native AMB sky/sun RGB and yaw; Blender sun "
            "energy/gamma and Crashday ground/specular shading are unmatched")
        world["source_amb_file"] = ambience.file_name


def import_scene(track, game, dependency, output=None, max_tiles=0,
                 reset_scene=True, max_vegetation=0, native_ambience_preview=False,
                 include_out_of_bounds=False, pack_imported_textures=False,
                 mod_content_directory=None):
    """Import a Crashday track into the running Blender version.

    reset_scene=True is for isolated headless CLI tests only.  An interactive
    File > Import operator MUST pass reset_scene=False; never reset or save
    somebody's current Blender project without their explicit action.
    output=None imports into the current scene without saving the .blend.
    """
    if reset_scene:
        # Only the isolated CLI/test path may replace the in-memory scene.
        bpy.ops.wm.read_factory_settings(use_empty=True)
    # Blender File > Import is additive. Do not replace a user's camera,
    # lighting, world or viewport framing when their scene already contains
    # objects. Only isolated/empty scenes get our convenient preview setup;
    # the legacy explicit ambience option intentionally opts into one.
    had_scene_objects = bool(len(bpy.context.scene.objects))
    make_preview_setup = (reset_scene or not had_scene_objects
                          or native_ambience_preview)
    t=read_track(track)
    # The Blender mesh is relative-Z and float32-coordinate storage. Preserve
    # absolute, original source float32 sample bits independently in a
    # compressed, checksummed Text datablock that survives .blend save/reopen.
    source_height_archive = encode_float32_height_grid(
        t.heights, t.samples_x, t.samples_y)
    cache_override = os.environ.get("CRASHDAY_TEXTURE_CACHE_DIR")
    # Blender-managed user files persist across sessions; actual extraction
    # happens only if a scene refers to a ZIP-only DDS/TGA source.
    texture_cache = (Path(cache_override) if cache_override else Path(
        bpy.utils.user_resource("CONFIG", path="crashday_track_importer/tex_cache", create=False)))
    lib=AssetLibrary(game, texture_cache_dir=texture_cache,
                     mod_content_roots=([mod_content_directory] if mod_content_directory else None))
    lib.pack_imported_textures = bool(pack_imported_textures)
    lib.imported_images = {}
    ambience = None
    ambience_error = None
    try:
        ambience = lib.ambience(t.ambience)
    except (AssetError, ValueError, OSError) as error:
        ambience_error = "{}: {}".format(type(error).__name__, error)
    source_grid=TerrainGrid(t.width,t.height,t.heights)
    vault=SharedBorderVault(source_grid)
    border_definitions={}
    border_errors=[]
    # Gather world-shared constraints across ALL occupied tiles before
    # creating meshes. Adjacent pieces must see the same restricted seam.
    for n,(fid,rotation,mirrored,_height) in enumerate(t.tiles):
        if fid>=len(t.field_files):
            continue
        name=t.field_files[fid]
        if name not in border_definitions:
            try:
                border_definitions[name]=lib.tile(name)
            except (AssetError,ValueError,OSError) as e:
                border_definitions[name]=None
                border_errors.append("{}: {}".format(name,e))
        definition=border_definitions[name]
        if definition is None:
            continue
        try:
            p=tile_placement(n%t.width,n//t.width,t.height,
                             definition.width,definition.depth,rotation,bool(mirrored))
            vault.add_tile(p,definition.width,definition.depth,definition.bend_rules)
        except ValueError as e:
            border_errors.append("cell {} ({}): {}".format(n,name,e))
    grid,border_report=vault.adjusted_grid()
    group=make_collection("Crashday Track")
    # Blender File > Import is additive. Each imported world must retain its
    # OWN provenance: scene custom properties below are only latest-import
    # convenience pointers and will change after a second track is imported.
    group["crashday_source_track"] = str(Path(track))
    group["crashday_source_format"] = t.source_format
    group["crashday_mod_override"] = (
        str(mod_content_directory) if mod_content_directory else "")
    original_collection=make_collection("00 - Original source heightmap (hidden)",group)
    terrain_collection=make_collection("01 - Ground and heightmap",group)
    tile_collection=make_collection("02 - P3D pieces (terrain sampled)",group)
    dynamic_collection=make_collection("03 - Dynamic P3D objects (approximate ground drop)",group)
    vegetation_collection=make_collection("04 - Vegetation P3D (optional sampled preview)",group)
    external_collection = (make_collection("05 - Optional out-of-bounds authored objects", group)
                           if include_out_of_bounds else None)
    # Preserve the exact imported original float grid (relative to the first
    # sample), independently from CFL adjustments and the rendering underlay.
    raw_heightmap=terrain_object(
        source_grid,original_collection,visual_offset_m=0.0,
        name="SOURCE - original TRK float32 height grid")
    raw_heightmap.hide_render=True
    raw_heightmap.hide_viewport=True
    raw_heightmap["source_fidelity"] = (
        "Relative visual grid from TRK float32 samples; exact absolute "
        "float32 values preserved in the referenced Blender Text archive")
    height_text = bpy.data.texts.new("Crashday - Exact Original Float32 Heights")
    height_text.write(source_height_archive)
    group["crashday_original_float32_height_text"] = height_text.name
    group["crashday_original_float32_height_samples"] = [t.samples_x, t.samples_y]
    raw_heightmap["crashday_original_float32_height_text"] = height_text.name
    bpy.context.scene["crashday_original_float32_height_text"] = height_text.name
    bpy.context.scene["crashday_original_float32_height_samples"] = [t.samples_x, t.samples_y]
    # TRK2 lines are track-authored overrides of external game.dbs values.
    # Preserve them in the scene for inspection, without evaluating or
    # applying any game rule to Blender geometry/materials.
    embedded_dbs_text = None
    if t.source_format == "CDTR2":
        embedded_dbs_text = bpy.data.texts.new(
            "Crashday - TRK2 embedded game.dbs settings")
        embedded_dbs_text.write("\n".join(t.embedded_game_dbs) +
                                ("\n" if t.embedded_game_dbs else ""))
        group["crashday_trk2_embedded_game_dbs_text"] = embedded_dbs_text.name
        bpy.context.scene["crashday_trk2_embedded_game_dbs_text"] = embedded_dbs_text.name
    # Keep the original TRK2 reference screenshot independent of any terrain
    # texture or material. A temporary lossless PNG is loaded then PACKED;
    # no external file is needed when the .blend is reopened on another PC.
    # Unrecognized extensions are deliberately not interpreted as images.
    embedded_preview_image = None
    if t.embedded_preview_bgra:
        with tempfile.TemporaryDirectory(prefix="crashday_trk2_preview_") as temp_dir:
            preview_path = Path(temp_dir) / "original_track_preview.png"
            preview_path.write_bytes(bgra_preview_to_png(t.embedded_preview_bgra))
            embedded_preview_image = bpy.data.images.load(
                str(preview_path), check_existing=False)
            embedded_preview_image.name = "Crashday - TRK2 original source preview"
            embedded_preview_image.pack()
            embedded_preview_image["crashday_source_kind"] = "original TRK2 BGRA screenshot"
            embedded_preview_image["crashday_bgra_sha256"] = hashlib.sha256(
                t.embedded_preview_bgra).hexdigest()
            group["crashday_trk2_original_preview_image"] = embedded_preview_image.name
            bpy.context.scene["crashday_trk2_original_preview_image"] = embedded_preview_image.name
            # Blender does not save orphan images merely because a custom
            # string property names them. Anchor the packed original to an
            # UNCONNECTED Image Texture node on the already-hidden SOURCE
            # terrain material. It is never projected onto visible geometry.
            archive_material = raw_heightmap.data.materials[0]
            archive_material.use_nodes = True
            archive_node = archive_material.node_tree.nodes.new("ShaderNodeTexImage")
            archive_node.name = "Unconnected original TRK2 preview archive"
            archive_node.label = "Original track screenshot (not a terrain texture)"
            archive_node.image = embedded_preview_image
            archive_node.hide = True
    bpy.context.scene["crashday_source_track_format"] = t.source_format
    bpy.context.scene["crashday_embedded_game_dbs_line_count"] = len(t.embedded_game_dbs)
    bpy.context.scene["crashday_unparsed_track_extension_bytes"] = t.trailing_bytes
    materials={}
    missing_tex=set()
    # Pale uncovered gaps between native P3D tiles were our untextured,
    # deliberately lowered backdrop, NOT missing game assets. Use the stock
    # field diffuse as a repeat-tiled preview instead of conspicuous mint.
    # Source height values remain separate and exact in raw_heightmap.
    ground_material=material_for_type("GOURAUD", "floor1", lib, materials, missing_tex)
    underlay=terrain_object(grid,terrain_collection, material=ground_material,
                            uv_repeat_m=20.0)
    underlay["ground_material_fidelity"]=(
        "VISUAL fallback: floor1 texture repeating every 20m; "
        "uncovered tile regions/game native ground shader still unverified")
    p3d=read_p3d_class(dependency)
    seen={}
    loaded={}
    missing_cfl={}
    missing_p3d={}
    tile_count=0
    placed_tile_indices=set()
    placed_tile_objects={}
    ignored=0
    errors=[]
    scanned_tile_cells=0
    for n, entry in enumerate(t.tiles):
        if max_tiles and tile_count >= max_tiles:
            break
        scanned_tile_cells += 1
        fid, rotation, mirrored, height=entry
        if fid>=len(t.field_files):
            ignored += 1
            continue
        name=t.field_files[fid]
        if name not in loaded:
            try:
                cfl=lib.tile(name)
                if cfl.model is None:
                    missing_p3d[name]=cfl.model_file
                    loaded[name]=None
                else:
                    parsed=p3d.P3D()
                    status=parsed.read(Reporter(),io.BytesIO(cfl.model.read_bytes()))
                    if status != p3d.ImportError.NO_ERROR:
                        errors.append(f"P3D reader failed: {name}")
                        loaded[name]=None
                    else:
                        loaded[name]=(cfl,parsed)
            except (AssetError, ValueError, OSError) as e:
                missing_cfl[name]=str(e)
                loaded[name]=None
        model=loaded.get(name)
        if model is None:
            continue
        try:
            obj=assemble_tile(name,n//t.width,n%t.width,entry,t,grid,
                              model[0],model[1],lib,tile_collection,materials,missing_tex)
            if obj is not None:
                tile_count += 1
                placed_tile_indices.add(n)
                placed_tile_objects[n] = obj
        except Exception as e:
            errors.append(f"tile {n} ({name}): {type(e).__name__}: {e}")
            if len(errors)>=50: break
    dynamic_templates={}
    missing_dynamic_cdo={}
    missing_dynamic_p3d={}
    dynamic_mesh_count=0
    external_dynamic_total = sum(is_out_of_bounds_dynamic(x,z,t.width,t.height)
                                 for _oid,x,_up,z,_yaw in t.dynamics)
    external_dynamic_imported = 0
    external_dynamic_meshes = 0
    for n,(object_id,x,y,z,yaw) in enumerate(t.dynamics):
        external = is_out_of_bounds_dynamic(x,z,t.width,t.height)
        if external and not include_out_of_bounds:
            continue
        item_name=t.dynamic_files[object_id] if object_id<len(t.dynamic_files) else "UNKNOWN"
        if item_name not in dynamic_templates:
            try:
                definition=lib.dynamic(item_name)
                if definition.model is None:
                    missing_dynamic_p3d[item_name]=definition.high_model_file
                    dynamic_templates[item_name]=(None,definition)
                else:
                    parsed=p3d.P3D()
                    status=parsed.read(Reporter(),io.BytesIO(definition.model.read_bytes()))
                    if status != p3d.ImportError.NO_ERROR:
                        raise AssetError("Dynamic P3D reader failed: {}".format(definition.high_model_file))
                    mesh=build_dynamic_mesh(item_name,parsed,lib,materials,missing_tex,
                                             model_ref=definition.model)
                    dynamic_templates[item_name]=(mesh,definition)
            except Exception as ex:
                missing_dynamic_cdo[item_name]="{}: {}".format(type(ex).__name__,ex)
                dynamic_templates[item_name]=(None,None)
        mesh,definition=dynamic_templates[item_name]
        obj=bpy.data.objects.new(("outside/" if external else "dynamic/") +
                                 "{:03d} {}".format(n,item_name), mesh)
        (external_collection if external else dynamic_collection).objects.link(obj)
        obj["outside_track_grid"] = external
        if external:
            external_dynamic_imported += 1
        obj.location=dynamic_position(x,y,z,grid)
        # TRK stores a degree yaw, but handedness/engine dynamic orientation
        # has not been matched against the in-game marker locations yet.
        obj.rotation_euler.z=math.radians(yaw)
        obj["cdo_file"]=item_name
        if definition is not None:
            for role, ref in (("cdo", definition.source_ref),
                              ("p3d", definition.model)):
                if ref is not None:
                    origin = lib.describe_source(ref)
                    obj["source_" + role + "_path"] = origin["package_or_file"]
                    obj["source_" + role + "_member"] = origin["archive_member"]
                    obj["source_" + role + "_selection"] = origin["reason"]
        obj["source_transform"]=[x,y,z,yaw]
        obj["transform_fidelity"]="Approximate: P3D lowest-vertex visual grounding on heightmap; real object collision/drop, elevated tile geometry and yaw sign unverified"
        if mesh is None:
            obj.empty_display_type="PLAIN_AXES"
            obj["unresolved_dynamic_asset"]=missing_dynamic_cdo.get(item_name,missing_dynamic_p3d.get(item_name,"P3D mesh has no visible geometry"))
        else:
            dynamic_mesh_count+=1
            if external:
                external_dynamic_meshes += 1
            obj["cdo_material"]=definition.material
            obj["cdo_mass_kg"]=definition.mass
            obj["cdo_center_of_mass"]=definition.center_of_mass
            obj["low_model_fallback"]=definition.using_low_fallback
    # -1 means ALL source-constrained, deterministic (not game-exact)
    # vegetation instances. Positive limits remain bounded quick previews;
    # zero remains explicitly off. Do not silently truncate full imports.
    vegetation_limit = (sys.maxsize if max_vegetation == -1
                        else min(2000, max(0, max_vegetation)))
    vegetation_report = add_vegetation_preview(
        t, lib, loaded, placed_tile_indices, grid, placed_tile_objects,
        p3d, vegetation_collection, materials, missing_tex,
        vegetation_limit)
    if make_preview_setup:
        add_camera(grid, ambience, native_ambience_preview)
    bpy.context.scene["crashday_preview_setup_created"] = bool(make_preview_setup)
    bpy.context.scene["source_ambience_file"] = t.ambience
    bpy.context.scene["ambience_preview_mode"] = (
        "experimental_native_AMB_color" if native_ambience_preview and ambience
        else "neutral_unmatched_preview")
    if ambience is not None:
        bpy.context.scene["native_ambience_sun_rgb"] = list(ambience.sun_rgb)
        bpy.context.scene["native_ambience_sky_rgb"] = list(ambience.sky_rgb)
        bpy.context.scene["native_ambience_sun_direction"] = list(ambience.sun_direction)
        bpy.context.scene["native_ambience_sunlit_object_rgb"] = list(ambience.sunlit_object_rgb)
        bpy.context.scene["native_ambience_ambient_min_rgb"] = list(ambience.ambient_min_rgb)
        bpy.context.scene["native_ambience_texture_filter_rgb"] = list(ambience.texture_filter_rgb)
        bpy.context.scene["native_ambience_screen_gamma"] = list(ambience.screen_gamma)
        bpy.context.scene["native_ambience_apply_gamma_to_sky"] = ambience.apply_gamma_to_sky
    bpy.context.scene["source_scenery_code"] = int(t.scenery)
    bpy.context.scene["include_out_of_bounds"] = bool(include_out_of_bounds)
    bpy.context.scene["crashday_pack_imported_textures"] = bool(pack_imported_textures)
    bpy.context.scene["outside_authored_dynamics_total"] = external_dynamic_total
    bpy.context.scene["outside_authored_dynamics_imported"] = external_dynamic_imported
    bpy.context.scene["outside_background_status"] = (
        "PARTIAL: only explicitly authored dynamic objects centered outside "
        "the grid can be included; .trk scenery-byte procedural/background "
        "geometry and its placement are not yet decoded")
    bpy.context.scene["source_track_path"]=str(t.path)
    bpy.context.scene["source_game_path"]=str(game)
    # Blender scene IDProperties support individual strings across 2.80+;
    # avoid storing a Python list of strings as an IDProperty array.
    bpy.context.scene["source_mod_content_path"] = (
        str(lib.mod_content_paths[0]) if lib.mod_content_paths else "")
    bpy.context.scene["source_workshop_root"] = (
        str(lib.workshop_root) if lib.workshop_root else "")
    bpy.context.scene["source_mod_testing_root"] = str(lib.mod_testing_root)
    bpy.context.scene["fallback_mod_assets_selected"] = len(lib.fallback_resolved)
    bpy.context.scene["visual_status"]="PROTOTYPE: shared CFL borders + dynamic P3D geometry; vegetation is optional sampled approximation; exact game fidelity unverified"
    # Suggest other installed mods for MISSING models/definitions as well as
    # CFLs. This is filename-only diagnostics, never implicit activation or
    # a source-precedence guess. Only an actually absent CDO qualifies;
    # a malformed existing CDO needs fixing, not a speculative override.
    missing_model_names = sorted({name for name in
                                  list(missing_p3d.values()) + list(missing_dynamic_p3d.values())
                                  if name})
    missing_cdo_names = sorted(name for name, error in missing_dynamic_cdo.items()
                               if "Missing asset:" in error)
    candidate_paths = {}
    for name in missing_model_names:
        try:
            candidate_paths[name] = candidate_model_keys(name)
        except AssetError:
            continue
    for name in missing_cdo_names:
        candidate_paths[name] = (name if "/" in name else "content/dynamics/" + name,)
    mod_asset_hints = (suggest_installed_mods_for_missing_assets(
        game, candidate_paths, excluded_content_roots=lib.mod_content_paths)
        if candidate_paths else {})
    # Unique asset origins in the saved Text report complement each object's
    # provenance IDProperties; don't repeat thousands of tile-cell paths.
    def source_label(ref):
        if ref is None:
            return "(unresolved)"
        origin = lib.describe_source(ref)
        member = (" :: " + origin["archive_member"]
                  if origin["archive_member"] else "")
        return "{} | {}{}".format(origin["reason"], origin["package_or_file"], member)

    tile_source_report = {
        name: {"CFL": source_label(value[0].source_ref),
               "P3D": source_label(value[0].model)}
        for name, value in loaded.items() if value is not None
    }
    dynamic_source_report = {
        name: {"CDO": source_label(value[1].source_ref),
               "P3D": source_label(value[1].model)}
        for name, value in dynamic_templates.items() if value[1] is not None
    }
    report={
        "tile_asset_source_provenance":tile_source_report,
        "vegetation_alias_asset_source_provenance":lib.vegetation_alias_sources,
        "dynamic_asset_source_provenance":dynamic_source_report,
        "input_track":str(track),"blend":str(output) if output else None,
        "source_track_format":t.source_format,
        "embedded_game_dbs_count":len(t.embedded_game_dbs),
        "embedded_game_dbs_text":embedded_dbs_text.name if embedded_dbs_text else None,
        "unparsed_extension_bytes":t.trailing_bytes,
        "selected_mod_content_paths":[str(path) for path in lib.mod_content_paths],
        "workshop_root":str(lib.workshop_root) if lib.workshop_root else None,
        "mod_testing_root":str(lib.mod_testing_root),
        "fallback_assets_used":sorted(lib.fallback_resolved),
        "fallback_identical_asset_sources":lib.fallback_identical,
        "fallback_ambiguous_asset_sources":lib.fallback_ambiguities,
        "blocked_cross_mod_model_dependencies":lib.fallback_dependency_conflicts,
        "blocked_cross_mod_texture_dependencies":lib.fallback_texture_conflicts,
        "source_ambience":t.ambience,
        "source_ambience_error":ambience_error,
        "preview_setup_created":bool(make_preview_setup),
        "source_ambience_sun_rgb":list(ambience.sun_rgb) if ambience else None,
        "source_ambience_sky_rgb":list(ambience.sky_rgb) if ambience else None,
        "source_ambience_sunlit_object_rgb":list(ambience.sunlit_object_rgb) if ambience else None,
        "source_ambience_texture_filter_rgb":list(ambience.texture_filter_rgb) if ambience else None,
        "source_ambience_screen_gamma":list(ambience.screen_gamma) if ambience else None,
        "source_ambience_apply_gamma_to_sky":ambience.apply_gamma_to_sky if ambience else None,
        "native_ambience_preview":bool(native_ambience_preview and ambience),
        "ambience_fidelity":"Native AMB source fields only; object sunlit tint, texture filter and gamma are stored, NOT applied to materials; game lighting/shaders unverified",
        "terrain_grid":[grid.nx,grid.ny],"terrain_vertices":grid.nx*grid.ny,
        "source_terrain_vertices":source_grid.nx*source_grid.ny,
        "source_height_float32_text":height_text.name,
        "source_height_exact_archive_bytes":len(source_height_archive),
        "preview_underlay_offset_m":0.12,
        "preview_underlay_material":"floor1 20m world-UV fallback (game parity unverified)",
        "embedded_original_preview_image": (embedded_preview_image.name
                                             if embedded_preview_image else None),
        "embedded_original_preview_bgra_bytes": len(t.embedded_preview_bgra),
        "tile_objects":tile_count,"unique_tile_models":len([x for x in loaded.values() if x]),
        "placeholder_cells":ignored,
        "tile_limit":int(max_tiles),
        "tile_cells_examined":scanned_tile_cells,
        "tile_cells_total":len(t.tiles),
        "tile_preview_partial":bool(max_tiles and tile_count >= max_tiles
                                    and scanned_tile_cells < len(t.tiles)),
        "tile_scan_complete":bool(scanned_tile_cells == len(t.tiles)),
        "dynamic_references":len(t.dynamics),
        "dynamic_mesh_objects":dynamic_mesh_count,
        "unique_dynamic_meshes":sum(mesh is not None for mesh,_definition in dynamic_templates.values()),
        "unresolved_dynamic_references":(len(t.dynamics) -
                                         (external_dynamic_total if not include_out_of_bounds else 0) -
                                         dynamic_mesh_count),
        "dynamic_position_fidelity":"Approximate terrain-height grounding; CDO P3D visual models only, no physical drop or in-game rotation parity",
        "missing_dynamic_cdo":missing_dynamic_cdo,
        "missing_dynamic_p3d":missing_dynamic_p3d,
        "vegetation_preview":vegetation_report,
        "source_scenery_code":int(t.scenery),
        "include_out_of_bounds":bool(include_out_of_bounds),
        "pack_imported_textures":bool(pack_imported_textures),
        "packed_distinct_import_images":len(lib.imported_images),
        "outside_authored_dynamic_references":external_dynamic_total,
        "outside_authored_dynamic_instances":external_dynamic_imported,
        "outside_authored_dynamic_meshes":external_dynamic_meshes,
        "outside_background_status":bpy.context.scene["outside_background_status"],
        "shared_cfl_border_constraints":border_report,
        "shared_cfl_constraint_errors":border_errors[:50],
        "missing_cfl":missing_cfl,"missing_p3d":missing_p3d,
        "suggested_mod_folders_for_missing_cfl": (
            suggest_installed_mods_for_missing_cfl(
                game, missing_cfl, excluded_content_roots=lib.mod_content_paths)
            if missing_cfl else {}),
        "suggested_mod_folders_for_missing_p3d": {
            name: mod_asset_hints[name] for name in missing_model_names
            if name in mod_asset_hints},
        "suggested_mod_folders_for_missing_cdo": {
            name: mod_asset_hints[name] for name in missing_cdo_names
            if name in mod_asset_hints},
        "missing_or_archive_only_textures":sorted(missing_tex),
        "errors":errors,
        "status":"PREVIEW_ONLY: shared CFL border + dynamic P3D objects; vegetation optional and approximate, FlyX mutable terrain, real game drop/rotation and shader parity unverified",
    }
    report["diagnostic_issue_count"] = issue_count(report)
    # A normal interactive import must not require opening a system console.
    # Preserve the complete concise report as a Blender Text datablock; it
    # remains available through the Text Editor after native .blend save/reopen.
    diagnostics = bpy.data.texts.new("Crashday - Import Report")
    diagnostics.write(format_import_report(report))
    group["crashday_import_report"] = diagnostics.name
    bpy.context.scene["crashday_import_report"] = diagnostics.name
    report["embedded_diagnostic_text"] = diagnostics.name
    if output is not None:
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        # Save AFTER storing the text datablock, so diagnostics survive reopen.
        bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=True)
        report_path = output.with_suffix(".report.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("CRASHDAY_IMPORT_REPORT", json.dumps(report))
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--track",type=Path,required=True)
    parser.add_argument("--game",type=Path,required=True)
    parser.add_argument("--p3d-parser",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--max-tiles",type=int,default=0)
    parser.add_argument("--max-vegetation",type=int,default=0,
                        help="Optional P3D vegetation preview count; one representative per source zone")
    parser.add_argument("--include-out-of-bounds",action="store_true",
                        help="Include authored dynamics outside grid; procedural scenery not yet supported")
    parser.add_argument("--pack-imported-textures", action="store_true",
                        help="Embed imported DDS/TGA images into .blend for portability")
    parser.add_argument("--native-ambience-preview",action="store_true",
                        help="Experimental native AMB sky/sun colors, not game-matched shading")
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else [])
    if args.max_tiles < 0:
        parser.error("max-tiles must not be negative")
    import_scene(args.track,args.game,args.p3d_parser,args.output,args.max_tiles,
                 max_vegetation=args.max_vegetation,
                 native_ambience_preview=args.native_ambience_preview,
                 include_out_of_bounds=args.include_out_of_bounds,
                 pack_imported_textures=args.pack_imported_textures)
