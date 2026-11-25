"""Opt-in, non-destructive private Blender-image -> TGA source-sidecar export.

This exports actual Blender image datablocks referenced by generated tile faces,
not invented native game textures or speculative .tex shader descriptors.
The native P3D texture ID is resolved by the SAME writer as the tile P3Ds.
No game/mod directories are writable; outputs are exclusive and transactional.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

from . import p3d_export

MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_BATCH_BYTES = 1024 * 1024 * 1024


def _material_image(material):
    if material is None or not material.use_nodes or not material.node_tree:
        return None
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            return node.image
    return None


def _external_image_path(image):
    """Resolve original file source with its Blender linked-library base, if any."""
    raw = str(getattr(image, "filepath", "") or "")
    if not raw:
        return None
    import bpy
    library = getattr(image, "library", None)
    return Path(bpy.path.abspath(raw, library=library) if library is not None
                else bpy.path.abspath(raw))


def _safe_same_external_source(a, b):
    """Deduplicate Blender's .001/.002 image datablocks only when source is shared.

    FBX imports often create a new bpy Image for every material, even though
    each image points at ONE original file. We may export one TGA per native
    texture ID only if unmodified FILE images share the exact source file and
    decoding interpretation. Different files, packed pixels and edited caches
    remain deliberately ambiguous. Missing paths are merged only for the
    following explicit aggregate missing-file rejection, never for export.
    """
    if a is b:
        return True
    if getattr(a, "source", None) != "FILE" or getattr(b, "source", None) != "FILE":
        return False
    if (getattr(a, "is_dirty", False) or getattr(b, "is_dirty", False) or
            getattr(a, "packed_file", None) or getattr(b, "packed_file", None)):
        return False
    settings = lambda im: (getattr(getattr(im, "colorspace_settings", None), "name", None),
                           getattr(im, "alpha_mode", None),
                           bool(getattr(im, "use_view_as_render", False)))
    if settings(a) != settings(b):
        return False
    pa, pb = _external_image_path(a), _external_image_path(b)
    if pa is None or pb is None:
        return False
    if os.path.normcase(os.path.realpath(str(pa))) != os.path.normcase(os.path.realpath(str(pb))):
        return False
    if pa.is_file():
        return True
    # A missing file with *both* image buffers unavailable can be safely
    # aggregated for a useful error, but must NEVER be exported.
    return not getattr(a, "has_data", False) and not getattr(b, "has_data", False)


def _require_accessible_source_images(images):
    """Diagnose missing DDS/PNG links before creating any output directory."""
    missing = []
    for name, im in sorted(images.items()):
        if getattr(im, "source", None) != "FILE":
            continue
        if getattr(im, "packed_file", None) or getattr(im, "has_data", False):
            continue  # Real packed/cached pixels can be rendered without file.
        path = _external_image_path(im)
        if path is None or not path.is_file():
            missing.append((name, str(path or "(empty filepath)")))
    if missing:
        example, path = missing[0]
        raise ValueError("{} native textures have missing external Blender image "
                         "sources; first '{}': {}. Restore/relink the source DDS/"
                         "image files before exporting TGA.".format(
                             len(missing), example, path))


def collect_referenced_images(objects):
    """Use only material slots actually referenced by generated tile faces."""
    images = {}
    unresolved = {}
    for obj in objects:
        if obj.type != "MESH" or "crashday_tile_key" not in obj:
            continue
        mesh = obj.data
        # The source texture is per material slot, NOT per triangle. Massive
        # imported worlds repeat the same material across thousands of faces;
        # resolve each USED slot only once, while preserving all used IDs.
        used_slots = {}
        for poly in mesh.polygons:
            used_slots.setdefault(poly.material_index, poly)
        for index, polygon in used_slots.items():
            material = mesh.materials[index] if index < len(mesh.materials) else None
            label, _style = p3d_export._texture_material(material, polygon)
            if label == "colwhite":
                continue  # The native default material is not a source image.
            image = _material_image(material)
            if image is None:
                unresolved.setdefault(label, set()).add(str(obj.name))
                continue
            source_label = p3d_export._valid_label(
                p3d_export._native_texture_basename(image.name), "colwhite")
            if source_label != label:
                raise ValueError("P3D/image texture name mismatch for " + label)
            if label in images and not _safe_same_external_source(images[label], image):
                raise ValueError("Different Blender images alias the same native P3D "
                                 "texture '" + label + "' with unproven source/pixel "
                                 "equivalence; resolve before export")
            images.setdefault(label, image)  # deterministic first native source
    for label in unresolved:
        if label in images:
            raise ValueError("Texture '{}' is image-backed on some tiles but lacks "
                             "an image on others".format(label))
    _require_accessible_source_images(images)
    return images, {name: len(objects) for name, objects in unresolved.items()}


def _check_tga(path, width, height):
    with path.open("rb") as file:
        header = file.read(18)
    if len(header) != 18:
        raise ValueError("Blender emitted a truncated TGA: " + path.name)
    ident, cmap, itype, _cstart, _clen, _cdepth, _x, _y, w, h, depth, _flags = struct.unpack(
        "<BBBHHBHHHHBB", header)
    if cmap or itype != 2 or depth not in (24, 32) or w != width or h != height:
        raise ValueError("Unexpected Blender TGA encoding or dimensions: " + path.name)
    if ident:
        raise ValueError("Unexpected TGA image identifier: " + path.name)


def _save_tga(image, path, scene):
    width, height = [int(value) for value in image.size]
    if width <= 0 or height <= 0 or width > 65535 or height > 65535:
        raise ValueError("Image has invalid TGA dimensions: " + image.name)
    if width * height * 4 > MAX_IMAGE_BYTES:
        raise ValueError("Single image exceeds 128 MiB decoded RGBA budget: " + image.name)
    settings = scene.render.image_settings
    view = scene.view_settings
    display = scene.display_settings
    previous = (settings.file_format, settings.color_mode, settings.color_depth)
    prior_view = (view.view_transform, view.look, view.exposure, view.gamma)
    prior_display = display.display_device
    try:
        settings.file_format = "TARGA_RAW"
        settings.color_mode = "RGBA"
        settings.color_depth = "8"
        # This is source artwork, NOT a rendered Filmic/AgX screenshot.
        # Enforce the neutral Standard sRGB display transform while saving
        # then restore the artist's pre-existing scene color-management state.
        display.display_device = "sRGB"
        view.view_transform = "Standard"
        view.look = "None"
        view.exposure = 0.0
        view.gamma = 1.0
        # save_render writes to a NEW temp destination and does not repath,
        # repack or rename the owner's original Blender image datablock.
        image.save_render(str(path), scene=scene)
    finally:
        settings.file_format, settings.color_mode, settings.color_depth = previous
        display.display_device = prior_display
        view.view_transform, view.look, view.exposure, view.gamma = prior_view
    _check_tga(path, width, height)
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES + 1024:
        raise ValueError("Converted TGA exceeds safe image-size limit")
    return width, height, size


def export_referenced_images(context, settings):
    root = p3d_export._directory_safe(__import__("bpy").path.abspath(
        settings.export_directory.strip()))
    if not settings.export_directory.strip():
        raise ValueError("Choose a private P3D/texture export folder")
    selected = (context.selected_objects if settings.export_selected_only
                else context.scene.objects)
    sources, unresolved = collect_referenced_images(selected)
    if not sources:
        raise ValueError("No image-backed tile materials found; no output written")
    texture_dir = root / "textures"
    manifest_path = root / "tile_textures_manifest.json"
    if os.path.lexists(str(manifest_path)):
        raise FileExistsError("Refusing to overwrite existing texture manifest")
    for label in sources:
        target = texture_dir / (label + ".tga")
        if os.path.lexists(str(target)):
            raise FileExistsError("Refusing to overwrite existing texture: " + str(target))
    root.mkdir(parents=True, exist_ok=True)
    records = []
    total = 0
    # The source image pixels remain immutable. TGA conversion is staged under
    # a temporary PRIVATE directory and only copied after every image succeeds.
    with tempfile.TemporaryDirectory(prefix=".crashday_tga_stage_", dir=str(root)) as temp:
        stage = Path(temp)
        for label, image in sorted(sources.items()):
            filename = label + ".tga"
            path = stage / filename
            width, height, size = _save_tga(image, path, context.scene)
            total += size
            if total > MAX_BATCH_BYTES:
                raise ValueError("TGA batch exceeds 1 GiB; export fewer selected tiles")
            records.append({"texture": label, "file": "textures/" + filename,
                            "image_name": image.name, "width": width,
                            "height": height, "bytes": size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {"schema": "micas-crashday-source-tga/v1",
                    "status": "Blender image conversion; .tex material presets not authored",
                    "native_texture_refs": len(sources),
                    "unresolved_native_refs": unresolved,
                    "images": records}
        payload = (json.dumps(manifest, indent=2, sort_keys=True,
                              ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if os.path.lexists(str(manifest_path)):
            raise FileExistsError("Texture manifest appeared while converting")
        for record in records:
            if os.path.lexists(str(root / record["file"])):
                raise FileExistsError("Texture appeared while converting: " + record["file"])
        created = []
        made_dir = False
        try:
            if not texture_dir.exists():
                texture_dir.mkdir()
                made_dir = True
            for record in records:
                destination = root / record["file"]
                with (stage / destination.name).open("rb") as src, destination.open("xb") as dst:
                    created.append(destination)
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            with manifest_path.open("xb") as dst:
                created.append(manifest_path)
                dst.write(payload)
        except Exception:
            for item in reversed(created):
                try:
                    item.unlink()
                except FileNotFoundError:
                    pass
            if made_dir and texture_dir.is_dir() and not any(texture_dir.iterdir()):
                texture_dir.rmdir()
            raise
    return {"images": len(records), "bytes": total,
            "unresolved": unresolved, "manifest": str(manifest_path),
            "manifest_sha256": hashlib.sha256(payload).hexdigest()}
