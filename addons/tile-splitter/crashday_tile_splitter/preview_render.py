"""Crashday palette thumbnail renderer for derived Tile Splitter (Blender 2.80+).

Renders each authored tile directly along -Z, using its LOGICAL footprint,
not the P3D mesh AABB. Writes native uncompressed TGA in the real game lookup
path. The temporary scene links original objects without changing them.
No original Crashday installation/Steam/Workshop paths may be written.
"""
from array import array
import hashlib
import json
import math
import os
import struct
from pathlib import Path
import tempfile

import bpy

from . import native_tile_preview, p3d_export

SCHEMA = "micas-crashday-native-tile-previews/v1"
RENDER_CONTRACT = "native-ortho-64-per-20m-eevee-standard-exact-fit-v2"
MAX_BATCH = 2048  # bounded: supports 1,137-tile worlds, with progress and hash reuse
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _selected_groups(context, settings):
    sources = (context.selected_objects if settings.export_selected_only
               else context.scene.objects)
    groups = {}
    for obj in sources:
        if obj.type == "MESH" and "crashday_tile_key" in obj:
            groups.setdefault(str(obj["crashday_tile_key"]), []).append(obj)
    if not groups:
        raise ValueError("No generated tile meshes available for previews")
    if len(groups) > MAX_BATCH:
        raise ValueError("Preview batch exceeds {} tiles; select a smaller group".format(MAX_BATCH))
    output = []
    prefix = str(settings.export_file_prefix)
    if not prefix or not p3d_export.re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", prefix):
        raise ValueError("Invalid native tile export prefix")
    for key in sorted(groups):
        objs = sorted(groups[key], key=lambda obj: obj.name)
        values = []
        for obj in objs:
            bounds = tuple(float(obj[prop]) for prop in (
                "crashday_tile_min_x", "crashday_tile_max_x",
                "crashday_tile_min_y", "crashday_tile_max_y"))
            if not all(math.isfinite(v) for v in bounds):
                raise ValueError("Tile has non-finite logical XY bounds")
            values.append(bounds)
        xmin, xmax, ymin, ymax = values[0]
        if any(any(abs(a - b) > 0.00001 for a, b in zip(v, values[0]))
               for v in values[1:]):
            raise ValueError("Conflicting logical bounds in tile " + key)
        width, height = xmax - xmin, ymax - ymin
        nx, ny = int(round(width / 20.0)), int(round(height / 20.0))
        native_tile_preview.expected_size(nx, ny)
        if abs(width - 20.0 * nx) > 0.00001 or abs(height - 20.0 * ny) > 0.00001:
            raise ValueError("Tile footprint must be exact native 20/40m dimensions")
        name = prefix + key + ".cfl"
        rel = native_tile_preview.tile_preview_path(name)
        output.append((key, objs, (xmin, xmax, ymin, ymax), nx, ny, rel))
    return output


def _image_digest(image, memo):
    """Return true image byte digest or None when no safe cache input exists."""
    if image is None or getattr(image, "is_dirty", False):
        return None
    pack = getattr(image, "packed_file", None)
    if pack:
        cache_id = ("packed", image.as_pointer())
        if cache_id not in memo:
            memo[cache_id] = hashlib.sha256(bytes(pack.data)).hexdigest()
        return memo[cache_id]
    if getattr(image, "source", None) != "FILE":
        return None
    library = getattr(image, "library", None)
    rawpath = getattr(image, "filepath", "")
    if not rawpath:
        return None
    path = bpy.path.abspath(rawpath, library=library) if library else bpy.path.abspath(rawpath)
    path = os.path.realpath(path)
    if not os.path.isfile(path):
        return None
    if path not in memo:
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        memo[path] = h.hexdigest()
    return memo[path]


def _material_signature(material, image_memo):
    """Conservative material fingerprint; unknown node types prevent cache hits."""
    if material is None:
        return ("none", True)
    if getattr(material, "animation_data", None):
        return ("animation", False)
    spec = [str(material.name), tuple(float(x) for x in material.diffuse_color),
            bool(material.use_nodes)]
    if not material.use_nodes or not material.node_tree:
        return (repr(spec), True)
    allowed = {"ShaderNodeBsdfPrincipled", "ShaderNodeTexImage",
               "ShaderNodeOutputMaterial", "ShaderNodeBsdfDiffuse",
               "ShaderNodeEmission", "ShaderNodeMixShader",
               "ShaderNodeTexCoord", "ShaderNodeUVMap"}
    nodes = material.node_tree.nodes
    for node in sorted(nodes, key=lambda x: x.name):
        if node.bl_idname not in allowed or getattr(node, "animation_data", None):
            return (repr(spec), False)
        info = [node.name, node.bl_idname, bool(node.mute)]
        for socket in node.inputs:
            val = getattr(socket, "default_value", None)
            if isinstance(val, (int, float, str, bool)):
                val = repr(val)
            else:
                try:
                    val = repr(tuple(float(x) for x in val))
                except (TypeError, ValueError):
                    val = None
            info.append((socket.name, val))
        if node.bl_idname == "ShaderNodeTexImage":
            digest = _image_digest(node.image, image_memo)
            if digest is None or getattr(node, "extension", "REPEAT") not in ("REPEAT", "EXTEND", "CLIP"):
                return (repr(spec), False)
            info.extend([digest, str(node.interpolation), str(node.extension),
                         str(node.image.colorspace_settings.name)])
        spec.append(info)
    spec.append(sorted((link.from_node.name, link.from_socket.name,
                        link.to_node.name, link.to_socket.name)
                       for link in material.node_tree.links))
    return repr(spec), True


def _cache_key(objs, nx, ny, image_memo, material_memo):
    """Hash actual Blender visual dependencies, NOT native P3D footprint.

    Crucial: authored oversized P3D meshes may overhang a nominal 20m CFL.
    Native P3D's conservative exporter rejects those, but the preview must
    STILL be able to crop/render them. Never touch or truncate source faces.
    """
    geometry = hashlib.sha256()
    material_hashes = []
    for obj in objs:
        if obj.modifiers or obj.animation_data or obj.data.shape_keys:
            return None  # source might evaluate differently across frames
        geometry.update(obj.name.encode("utf-8"))
        geometry.update(struct.pack("<16d", *[float(v) for row in obj.matrix_world for v in row]))
        geometry.update(b"hidden" if obj.hide_render else b"visible")
        mesh = obj.data
        geometry.update(struct.pack("<2I", len(mesh.vertices), len(mesh.polygons)))
        for vertex in mesh.vertices:
            geometry.update(struct.pack("<3d", *[float(v) for v in vertex.co]))
        for poly in mesh.polygons:
            geometry.update(struct.pack("<II", poly.material_index, len(poly.vertices)))
            for vi in poly.vertices:
                geometry.update(struct.pack("<I", vi))
        for layer in mesh.uv_layers:
            geometry.update(layer.name.encode("utf-8"))
            for item in layer.data:
                geometry.update(struct.pack("<2f", float(item.uv[0]), float(item.uv[1])))
        for mat in mesh.materials:
            index = mat.as_pointer() if mat else None
            if index not in material_memo:
                material_memo[index] = _material_signature(mat, image_memo)
            spec, reliable = material_memo[index]
            if not reliable:
                return None
            material_hashes.append(spec)
    material_sha = hashlib.sha256(repr(sorted(material_hashes)).encode("utf-8")).hexdigest()
    return native_tile_preview.preview_content_key(
        geometry.hexdigest(), [material_sha], nx, ny,
        RENDER_CONTRACT + "-blender-" + bpy.app.version_string)


def _camera_z(objects):
    upper = None
    lower = None
    for obj in objects:
        for local in obj.bound_box:
            z = (obj.matrix_world @ __import__("mathutils").Vector(local)).z
            upper = z if upper is None else max(upper, z)
            lower = z if lower is None else min(lower, z)
    if upper is None:
        raise ValueError("No tile geometry for overhead preview")
    return upper + max(20.0, upper - lower + 20.0), max(1000.0, 4 * (upper - lower + 20.0))


def _make_scene(objs, bounds, nx, ny, path):
    scene = bpy.data.scenes.new("Crashday Temporary Overhead Preview")
    camera_data = None
    light_data = None
    world = None
    try:
        for obj in objs:
            scene.collection.objects.link(obj)  # same datablocks, never copied/mutated
        cam = bpy.data.objects.new("Temporary Ortho Camera", bpy.data.cameras.new("Temporary Ortho Lens"))
        camera_data = cam.data
        scene.collection.objects.link(cam)
        scene.camera = cam
        xmin, xmax, ymin, ymax = bounds
        z, depth = _camera_z(objs)
        cam.location = ((xmin + xmax) * 0.5, (ymin + ymax) * 0.5, z)
        cam.rotation_euler = (0.0, 0.0, 0.0)  # -Z down; +Y is image up
        camera_data.type = "ORTHO"
        camera_data.sensor_fit = "AUTO"
        camera_data.ortho_scale = max(xmax - xmin, ymax - ymin)
        camera_data.clip_start = 0.01
        camera_data.clip_end = depth
        lamp = bpy.data.objects.new("Temporary Preview Sun",
                                    bpy.data.lights.new("Temporary Preview Sun Data", "SUN"))
        light_data = lamp.data
        scene.collection.objects.link(lamp)
        lamp.rotation_euler = (math.radians(24), math.radians(-12), math.radians(30))
        light_data.energy = 2.0
        world = bpy.data.worlds.new("Temporary Preview World")
        world.use_nodes = True
        for node in world.node_tree.nodes:
            if node.type == "BACKGROUND":
                node.inputs["Color"].default_value = (0.75, 0.75, 0.75, 1.0)
                node.inputs["Strength"].default_value = 0.7
        scene.world = world
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except (TypeError, ValueError):
            scene.render.engine = "BLENDER_EEVEE_NEXT"
        scene.render.resolution_x = 64 * nx
        scene.render.resolution_y = 64 * ny
        scene.render.resolution_percentage = 100
        scene.render.pixel_aspect_x = 1
        scene.render.pixel_aspect_y = 1
        frame = camera_data.view_frame(scene=scene)
        span_x = max(v.x for v in frame) - min(v.x for v in frame)
        span_y = max(v.y for v in frame) - min(v.y for v in frame)
        if (abs(span_x - (xmax - xmin)) > 0.0001 or
                abs(span_y - (ymax - ymin)) > 0.0001):
            raise ValueError("Preview camera does not match logical XY crop")
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "8"
        scene.render.filepath = str(path)
        scene.render.use_file_extension = False
        scene.render.use_compositing = False
        scene.render.use_sequencer = False
        scene.display_settings.display_device = "sRGB"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0
        scene.view_settings.gamma = 1
        # Preview is low-resolution; avoid unnecessary heavy sampling.
        if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 16
        return scene
    except Exception:
        _drop_scene(scene)
        raise


def _drop_scene(scene):
    # Only temp-owned camera, sun, lens and world may be deleted. Linked source
    # objects, their mesh data, materials and original collection memberships live on.
    cam = scene.camera
    owned = [obj for obj in scene.objects if obj.type == "LIGHT"
             and obj.name.startswith("Temporary Preview Sun")]
    if cam is not None:
        owned.append(cam)
    world = scene.world
    bpy.data.scenes.remove(scene)
    for obj in owned:
        datablock = obj.data
        kind = obj.type
        bpy.data.objects.remove(obj, do_unlink=True)
        if datablock is not None and datablock.users == 0:
            if kind == "CAMERA":
                bpy.data.cameras.remove(datablock)
            else:
                bpy.data.lights.remove(datablock)
    if world is not None and world.users == 0:
        bpy.data.worlds.remove(world)


def _png_rgba_topdown(path, width, height):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        if image.size[0] != width or image.size[1] != height:
            raise ValueError("Unexpected Blender preview PNG dimensions")
        # Keep exact PNG display byte colours: Blender otherwise converts
        # sRGB PNG RGB channels into scene-linear floats upon readback.
        image.colorspace_settings.name = "Non-Color"
        values = array("f", [0.0]) * (width * height * 4)
        if hasattr(image.pixels, "foreach_get"):
            image.pixels.foreach_get(values)
        else:
            # Native Blender 2.80's bpy_prop_array lacks foreach_get even
            # though later Blender images expose it. Pixel order is unchanged.
            legacy = image.pixels[:]
            if len(legacy) != len(values):
                raise ValueError("Truncated legacy Blender PNG pixel buffer")
            values = array("f", legacy)
        rgba = bytearray(len(values))
        stride = 4 * width
        for y in range(height):
            src = (height - 1 - y) * stride
            dst = y * stride
            for i in range(stride):
                rgba[dst + i] = max(0, min(255, int(round(values[src + i] * 255.0))))
        return bytes(rgba)
    finally:
        bpy.data.images.remove(image)


def _render_one(objs, bounds, nx, ny, temp_path):
    scene = _make_scene(objs, bounds, nx, ny, temp_path)
    try:
        bpy.ops.render.render(scene=scene.name, write_still=True)
        if not temp_path.is_file():
            raise RuntimeError("Blender did not write the temporary preview image")
        return _png_rgba_topdown(temp_path, 64 * nx, 64 * ny)
    finally:
        _drop_scene(scene)


def _check_source_images(groups):
    """Refuse a missing externally linked texture before creating any preview.

    A missing source file otherwise renders magenta in Eevee, creating a
    plausible-looking *invalid* mod thumbnail. Packed or genuinely loaded
    in-memory source pixels are fine.
    """
    missing = []
    visited = set()
    for _key, objects, _bounds, _nx, _ny, _rel in groups:
        for obj in objects:
            indices = {poly.material_index for poly in obj.data.polygons}
            for slot in indices:
                mat = obj.data.materials[slot] if slot < len(obj.data.materials) else None
                if not mat or not mat.use_nodes or not mat.node_tree:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type != "TEX_IMAGE" or not node.image:
                        continue
                    im = node.image
                    if im.as_pointer() in visited:
                        continue
                    visited.add(im.as_pointer())
                    if (im.source != "FILE" or im.packed_file or im.has_data):
                        continue
                    raw = getattr(im, "filepath", "")
                    library = getattr(im, "library", None)
                    path = (bpy.path.abspath(raw, library=library) if library else
                            bpy.path.abspath(raw)) if raw else ""
                    if not path or not os.path.isfile(path):
                        missing.append((im.name, path))
    if missing:
        raise ValueError("{} required Blender image source(s) are missing; first '{}': {}. "
                         "Relink/pack source artwork before generating tile previews".format(
                             len(missing), missing[0][0], missing[0][1]))


def _verified_p3d_source_binding(root, groups, prefix):
    """Bind a thumbnail to the EXACT exported P3D bytes from these Blender meshes.

    A standalone preview can still be rendered without a P3D export manifest,
    including an oversized tile rejected by the conservative P3D writer.
    When an authored P3D manifest IS present, however, mismatch fails closed:
    never attest a thumbnail of a later-edited mesh as that older native P3D.
    """
    manifest_path = root / (prefix + "tiles_manifest.json")
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return {}
    if (not manifest_path.is_file() or manifest_path.is_symlink() or
            manifest_path.stat().st_size > MAX_MANIFEST_BYTES):
        raise ValueError("Unsafe or oversized P3D tile manifest")
    raw = manifest_path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
        entries = doc["p3d_files"]
    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise ValueError("Malformed P3D tile manifest") from exc
    if (doc.get("schema") != "micas-crashday-tile-export/v1" or
            not isinstance(entries, list) or
            type(doc.get("p3d_count")) is not int or
            len(entries) != doc["p3d_count"] or len(entries) > MAX_BATCH):
        raise ValueError("Unexpected P3D tile manifest identity/count")
    by_key = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid P3D tile manifest entry")
        key = entry.get("key")
        if (not isinstance(key, str) or
                not __import__("re").fullmatch(r"x[pm][0-9]{4}_y[pm][0-9]{4}", key) or
                key in by_key or entry.get("file") != prefix + key + ".p3d"):
            raise ValueError("Duplicate or wrong P3D tile identity")
        by_key[key] = entry
    manifest_sha = hashlib.sha256(raw).hexdigest()
    out = {}
    for key, objs, bounds, _nx, _ny, _rel in groups:
        entry = by_key.get(key)
        if entry is None:
            raise ValueError("Preview tile missing in P3D export manifest: " + key)
        recorded_bounds = entry.get("logical_tile_bounds_xy")
        if (not isinstance(recorded_bounds, list) or
                len(recorded_bounds) != 4 or
                any(type(value) not in (float, int) or
                    abs(float(value) - expected) > 0.0001
                    for value, expected in zip(recorded_bounds, bounds))):
            raise ValueError("P3D and preview logical XY footprints differ: " + key)
        sha = entry.get("sha256")
        filename = root / entry["file"]
        if (not isinstance(sha, str) or
                not __import__("re").fullmatch("[0-9a-f]{64}", sha) or
                not filename.is_file() or filename.is_symlink() or
                filename.stat().st_size != entry.get("bytes") or
                hashlib.sha256(filename.read_bytes()).hexdigest() != sha):
            raise ValueError("Missing/modified native P3D export: " + key)
        # Rebuild the native file from the current live objects. This catches
        # source edits after P3D export, including source UVs, mesh flags, XYZ
        # and materials that a preview-only geometry hash might miss.
        current_bytes, _audit = p3d_export.build_tile_p3d(
            objs, floor_z=entry.get("floor_z"))
        if hashlib.sha256(current_bytes).hexdigest() != sha:
            raise ValueError("Blender source tile changed after P3D export: " + key)
        out[key] = {"p3d_sha256": sha, "p3d_manifest_sha256": manifest_sha}
    return out


def export_tile_previews(context, settings):
    if not settings.export_directory.strip():
        raise ValueError("Choose a PRIVATE P3D export folder")
    root = p3d_export._directory_safe(bpy.path.abspath(settings.export_directory))
    groups = _selected_groups(context, settings)
    _check_source_images(groups)
    # If a P3D manifest exists beside this Blender output, attestation to
    # the current exact source geometry is mandatory (never a loose filename).
    p3d_bindings = _verified_p3d_source_binding(
        root, groups, str(settings.export_file_prefix))
    manifest_path = root / "tile_previews_manifest.json"
    existing = {}
    if manifest_path.exists():
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Preview manifest too large")
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if parsed.get("schema") != SCHEMA or not isinstance(parsed.get("tiles"), dict):
            raise ValueError("Unrecognized existing preview manifest; refusing overwrite")
        existing = parsed["tiles"]
    if os.path.lexists(str(manifest_path)) and (not manifest_path.is_file() or manifest_path.is_symlink()):
        raise ValueError("Preview manifest cannot be a directory/symlink")
    root.mkdir(parents=True, exist_ok=True)
    image_memo, material_memo = {}, {}
    pending, hits = [], 0
    for key, objs, bounds, nx, ny, rel in groups:
        cache_key = _cache_key(objs, nx, ny, image_memo, material_memo)
        path = root.joinpath(*rel.split("/"))
        # Reject symlink redirects EVEN under a safe private output root.
        p3d_export._directory_safe(path.parent)
        rec = existing.get(rel)
        binding = p3d_bindings.get(key, {})
        if os.path.lexists(str(path)):
            if not path.is_file() or path.is_symlink() or not rec or not cache_key:
                raise FileExistsError("Existing preview is not a known unchanged cache entry: " + rel)
            with path.open("rb") as file:
                data = file.read()
            if (rec.get("source_key") != cache_key or
                    rec.get("sha256") != hashlib.sha256(data).hexdigest() or
                    {name: rec.get(name) for name in
                     ("p3d_sha256", "p3d_manifest_sha256") if name in rec}
                    != binding):
                raise FileExistsError("Existing preview changed; use a fresh private export folder: " + rel)
            native_tile_preview.validate_tga(data, nx, ny)
            hits += 1
        else:
            pending.append((key, objs, bounds, nx, ny, rel, path, cache_key, binding))
    created = []
    manifest_temp = None
    records = dict(existing)
    progress = getattr(context, "window_manager", None)
    if progress is not None and pending:
        progress.progress_begin(0, len(pending))
    try:
        with tempfile.TemporaryDirectory(prefix=".cd_tile_preview_",
                                          dir=str(root)) as staging:
            stage = Path(staging)
            for idx, (key, objs, bounds, nx, ny, rel, path, cache_key, binding) in enumerate(pending):
                if progress is not None:
                    progress.progress_update(idx)
                raw = _render_one(objs, bounds, nx, ny, stage / ("frame_%03d.png" % idx))
                data = native_tile_preview.encode_rgba_preview(raw, nx, ny)
                proof = native_tile_preview.validate_tga(data, nx, ny)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    created.append(path)
                    handle.write(data)
                record = {"tile": key, "fields": [nx, ny],
                          "sha256": proof["sha256"],
                          "source_key": cache_key,
                          "render_contract": RENDER_CONTRACT + "-blender-" + bpy.app.version_string}
                record.update(binding)
                records[rel] = record
            if pending or not manifest_path.exists():
                # Recheck the exact P3D binding after the render batch.
                if _verified_p3d_source_binding(root, groups,
                        str(settings.export_file_prefix)) != p3d_bindings:
                    raise ValueError("P3D binding changed during preview generation")
                payload = (json.dumps({"schema": SCHEMA, "tiles": records},
                                      sort_keys=True, indent=2) + "\n").encode("utf-8")
                if len(payload) > MAX_MANIFEST_BYTES:
                    raise ValueError("Preview manifest exceeds 4 MiB safety budget")
                manifest_temp = stage / "tile_previews_manifest.json"
                with manifest_temp.open("xb") as handle:
                    handle.write(payload)
                os.replace(str(manifest_temp), str(manifest_path))
        return {"rendered": len(pending), "cache_hits": hits,
                "tiles": len(groups), "manifest": str(manifest_path)}
    except Exception:
        for path in created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if progress is not None and pending:
            progress.progress_end()
