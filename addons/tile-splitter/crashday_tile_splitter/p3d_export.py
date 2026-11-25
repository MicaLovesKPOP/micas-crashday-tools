"""Strict standalone Crashday P3D tile writer (no game writes, no Wurunduk dependency).

Encoding follows the documented native P3D2 layout; independent verification is
performed with the original Wurunduk parser in actual Blender tests. This is a
private prototype, not proof of in-game collision, materials, or height parity.
"""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct

MAT_TYPES = ("FLAT", "FLAT_METAL", "GOURAUD", "GOURAUD_METAL",
             "GOURAUD_METAL_ENV", "SHINING")
FLAG_VALUES = {
    "MAIN": 1, "VIS": 2, "TRACE": 4, "COLL": 8,
    "NOLOD": 16, "LOD0": 32, "LOD2": 64, "LOD3": 128, "LOD4": 256,
    "SUB0": 512, "SUB2": 1024, "SUB3": 2048, "SUB4": 4096,
    "DET": 8192, "BRG": 16384, "BRP": 32768, "BRW": 65536,
    "BRM": 131072, "BRE": 262144, "LIPL": 524288,
    "HDL": 1048576, "BRL": 2097152, "DMG": 4194304,
    "NOCL": 8388608,
}
MAX_TOTAL_OUTPUT_BYTES = 512 * 1024 * 1024


def _pack(file, fmt, *values):
    file.write(struct.pack(fmt, *values))


def _cstring(file, value):
    if not value or len(value) > 100 or not value.isascii() or "\x00" in value:
        raise ValueError("P3D requires a short nonempty ASCII name: {!r}".format(value))
    file.write(value.encode("ascii") + b"\0")


def _valid_label(value, fallback):
    # Changing/shortening a filename silently substitutes a DIFFERENT game
    # texture (or aliases two textures to one).  Fail closed rather than
    # manufacturing names that may never exist in the source mod.
    value = str(value) if value is not None else ""
    if not value:
        return fallback
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("Native texture identifier cannot be represented without changing its name: {!r}".format(value))
    return value.lower()


def _native_texture_basename(name):
    # Blender's duplicate-image suffix appears AFTER the source extension:
    # bush.dds.001 is still the native texture 'bush', not 'bush_dds'.
    # Only strip suffixes for known image formats; do not guess at arbitrary
    # dot-separated names or synthesize a missing game texture.
    text = str(name)
    match = re.search(r"(?i)\.(?:dds|tga|png|jpe?g|bmp)(?:\.\d{3})?$", text)
    return text[:match.start()] if match else text


def _texture_material(mat, polygon):
    if mat is None:
        return "colwhite", "FLAT"
    # Preserve explicit original Wurunduk material presets when registered.
    props = getattr(mat, "cdp3d", None)
    mat_type = getattr(props, "material_type", None) if props else None
    if mat_type not in MAT_TYPES:
        mat_type = "GOURAUD" if polygon.use_smooth else "FLAT"
    source_texture = None
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                source_texture = _native_texture_basename(node.image.name)
                break
    if not source_texture and props:
        source_texture = props.material_name
    if not source_texture:
        # A Blender material name is not proof that an equivalent Crashday
        # texture exists. Preserve geometry and use a native safe fallback.
        source_texture = "colwhite"
    return _valid_label(source_texture, "colwhite"), mat_type


def _bounds(points):
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])


def _center(bounds):
    return [(a + b) * 0.5 for a, b in zip(*bounds)]


def _mesh_flags(obj, name, has_shadow, has_collision):
    flags = 2  # Wurunduk default visible, without invented breakability.
    if "crashday_p3d_flags" in obj:
        source = obj["crashday_p3d_flags"]
        if not isinstance(source, int) or source < 0 or source > 0xFFFFFFFF:
            raise ValueError("Invalid explicit mesh flags on " + obj.name)
        flags = source
    else:
        props = getattr(obj.data, "cdp3d", None)
        if props:
            names = props.flags
            flags = 0
            for value in names:
                if value not in FLAG_VALUES:
                    raise ValueError("Unknown Wurunduk flag: " + value)
                flags |= FLAG_VALUES[value]
    if name == "main":
        flags |= 1
        if not has_shadow:
            flags |= 4
        if not has_collision:
            flags |= 8
    elif name == "mainshad":
        flags = (flags & ~2) | 4
    elif name == "maincoll":
        flags = (flags & ~2) | 8
    return flags


def _prepared_mesh(obj, logical_name):
    mesh = obj.data
    if not mesh.vertices or not mesh.polygons:
        raise ValueError("Tile fragment has no geometry: " + obj.name)
    if len(mesh.vertices) > 65535:
        raise ValueError("P3D 16-bit vertex limit exceeded in " + obj.name)
    # Generated fragments' local coordinates are already centred on their tile.
    points = [tuple(obj.matrix_world @ vert.co) for vert in mesh.vertices]
    if any(not all(math.isfinite(float(v)) for v in point) for point in points):
        raise ValueError("P3D source mesh has non-finite coordinates: " + obj.name)
    bounds = _bounds(points)
    center = _center(bounds)
    mesh.calc_loop_triangles()
    uv = mesh.uv_layers.active
    polys = []
    for tri in mesh.loop_triangles:
        poly = mesh.polygons[tri.polygon_index]
        slot = poly.material_index
        mat = mesh.materials[slot] if slot < len(mesh.materials) else None
        tex, style = _texture_material(mat, poly)
        if uv is None and tex != "colwhite":
            # Replacing a textured face's missing authored UVs with (0,0)
            # silently destroys its material mapping in the exported P3D.
            # Only the uniform native colwhite fallback can safely omit UVs.
            raise ValueError("Textured source mesh needs a UV layer for native P3D: "
                             + obj.name + " / " + tex)
        face_uvs = [tuple(uv.data[li].uv) if uv else (0.0, 0.0) for li in tri.loops]
        polys.append((tex, style, tuple(tri.vertices), tuple(face_uvs)))
    if len(polys) > 65535:
        raise ValueError("P3D 16-bit polygon limit exceeded in " + obj.name)
    if not polys:
        raise ValueError("No triangulated faces for " + obj.name)
    return {"name": logical_name, "obj": obj, "bounds": bounds,
            "center": center, "points": points, "polys": polys,
            "source_objects": [obj.name]}


# The native P3D submesh names encode roles, not arbitrary Blender/FBX object
# identifiers. Keep known functional roles separate and translate ordinary
# imported geometry to the collidable main mesh without changing scene objects.
_SEMANTIC_MESH_NAMES = frozenset(("main", "maincoll", "mainshad", "wood", "dest"))


def _merge_main_parts(parts):
    """Join only *serialized* main geometry, preserving authored UVs/materials.

    Blender objects and their source names are never modified. Native P3D has
    per-submesh 16-bit indices: fail closed when a combined main exceeds them.
    """
    if len(parts) == 1:
        return parts[0]
    points, polys, sources = [], [], []
    for part in parts:
        base = len(points)
        points.extend(part["points"])
        polys.extend((tex, style, tuple(i + base for i in verts), uvs)
                     for tex, style, verts, uvs in part["polys"])
        sources.extend(part["source_objects"])
    if len(points) > 65535 or len(polys) > 65535:
        raise ValueError("Combined main mesh exceeds native P3D 16-bit limits; "
                         "export smaller cells or simplify source geometry")
    bounds = _bounds(points)
    return {"name": "main", "obj": parts[0]["obj"], "bounds": bounds,
            "center": _center(bounds), "points": points, "polys": polys,
            "source_objects": sources}


def _validate_group(objects):
    by_name = {}
    key = None
    sources = []
    for obj in objects:
        if obj.type != "MESH" or "crashday_tile_key" not in obj:
            raise ValueError("Not a generated tile mesh: " + obj.name)
        obj_key = obj["crashday_tile_key"]
        if key is None:
            key = obj_key
        if key != obj_key or not re.fullmatch(r"x[pm]\d{4}_y[pm]\d{4}", key):
            raise ValueError("Mixed or malformed tile keys")
        name = str(obj.get("crashday_source_mesh_name", "")).strip().lower()
        if not name:
            raise ValueError("Missing source mesh identity on " + obj.name)
        sources.append((obj, name, bool(re.fullmatch(r"[a-z0-9_-]{1,50}", name))))
    has_main = any(name == "main" for _obj, name, _valid in sources)
    seen_named = set()
    # A generic Blender/FBX object name is not a Crashday mesh role. When
    # there is no authored main, consolidate non-role geometry into main; if
    # an authored main exists, preserve all other already-valid submesh names.
    # Too-long/non-ASCII names can still be safely grouped into main, rather
    # than fabricating truncated native IDs. This is deterministic even when
    # Blender's selection/object enumeration order changes.
    for obj, name, valid in sorted(
            sources, key=lambda item: (item[1] != "main", item[1], item[0].name)):
        native_role = name if valid and (has_main or name in _SEMANTIC_MESH_NAMES) else "main"
        if native_role == "main" and name != "main":
            if "crashday_p3d_flags" in obj:
                raise ValueError("Cannot auto-group explicitly flagged mesh into main: "
                                 + obj.name + "; assign its native P3D role first")
        if native_role != "main" or name == "main":
            if native_role in seen_named:
                raise ValueError("Two semantic meshes named '{}' in {}".format(native_role, key))
            seen_named.add(native_role)
        by_name.setdefault(native_role, []).append(obj)
    if "main" not in by_name:
        raise ValueError("Tile {} has no 'main' mesh; no P3D was written".format(key))
    return key, by_name


def _tile_xy_bounds(objects):
    """Require every fragment to share a finite authored XY tile, not a mesh bbox.

    A partial corner mesh has a different bbox centre than the 40m (or other
    configured) logical tile. Re-centering P3D on the mesh silently changes
    where that geometry sits when the tile is placed by its CFL cell centre.
    """
    attrs = ("crashday_tile_min_x", "crashday_tile_max_x",
             "crashday_tile_min_y", "crashday_tile_max_y")
    expected = None
    for obj in objects:
        try:
            bounds = tuple(float(obj[name]) for name in attrs)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Tile fragment lacks XY grid bounds metadata: " + obj.name) from exc
        if not all(math.isfinite(value) for value in bounds):
            raise ValueError("Tile grid bounds are non-finite on " + obj.name)
        if bounds[1] <= bounds[0] or bounds[3] <= bounds[2]:
            raise ValueError("Tile grid dimensions are empty/reversed on " + obj.name)
        if expected is None:
            expected = bounds
        elif any(abs(a-b) > 0.0001 for a,b in zip(bounds,expected)):
            raise ValueError("Inconsistent tile grid bounds among source fragments")
    if expected is None:
        raise ValueError("Empty tile fragment group")
    return expected


def build_tile_p3d(objects, floor_z=None):
    if floor_z is not None and not math.isfinite(float(floor_z)):
        raise ValueError("P3D floor level is non-finite")
    key, by_name = _validate_group(objects)
    ordered = [("main", by_name["main"])] + sorted(
        ((name, group) for name, group in by_name.items() if name != "main"))
    meshes = []
    for name, group in ordered:
        parts = [_prepared_mesh(obj, name) for obj in group]
        meshes.append(_merge_main_parts(parts) if name == "main" else parts[0])
    xmin, xmax, ymin, ymax = _tile_xy_bounds(objects)
    for item in meshes:
        low, high = item["bounds"]
        if (low[0] < xmin-0.025 or high[0] > xmax+0.025 or
                low[1] < ymin-0.025 or high[1] > ymax+0.025):
            raise ValueError("Tile fragment extends outside its declared grid cell: " + item["obj"].name)
    main_bounds = meshes[0]["bounds"]
    main_center = _center(main_bounds)
    # The placed P3D origin is the logical tile CELL centre, not the centre
    # of a partially occupied main mesh. Use the actual cell's full X/Y span
    # for P3D header dimensions even when visible geometry covers only a corner.
    main_center[0] = (xmin+xmax)*0.5
    main_center[1] = (ymin+ymax)*0.5
    if floor_z is not None:
        # Matches Wurunduk's use_empty_for_floor_level convention; native
        # game floor behavior still requires controlled in-game verification.
        main_center[2] += (floor_z - main_bounds[0][2]) * 0.5
    textures = sorted({poly[0] for mesh in meshes for poly in mesh["polys"]})
    if len(textures) > 255:
        raise ValueError("P3D has more than 255 textures in " + key)

    stream = io.BytesIO()
    stream.write(b"P3D\x02")
    low, high = main_bounds
    p3d_height = (-2.0 * floor_z) if floor_z is not None else (2.0 * high[2])
    _pack(stream, "<3f", xmax-xmin, p3d_height, ymax-ymin)
    stream.write(b"TEX")
    _pack(stream, "<IB", 1337, len(textures))
    for texture in textures:
        _cstring(stream, texture + ".tga")
    stream.write(b"LIGHTS")
    _pack(stream, "<IH", 1337, 0)
    stream.write(b"MESHES")
    _pack(stream, "<IH", 1337, len(meshes))
    stats = []
    for item in meshes:
        name = item["name"]
        low, high = item["bounds"]
        center = item["center"]
        stream.write(b"SUBMESH")
        _pack(stream, "<I", 1337)
        _cstring(stream, name)
        flags = _mesh_flags(item["obj"], name, "mainshad" in by_name, "maincoll" in by_name)
        rel = [center[i] - main_center[i] for i in range(3)]
        if name == "main":
            rel = [0.0, 0.0, 0.0]
        # P3D stores XYZ values with Y/Z transposed relative to Blender.
        _pack(stream, "<I6f", flags, rel[0], rel[2], rel[1],
              high[0]-low[0], high[2]-low[2], high[1]-low[1])
        # Group triangles ONCE by native texture/material, retaining source
        # order within each group. Re-scanning every triangle for each of six
        # material types * every texture scales needlessly with texture count.
        # Native per-texture counts/order and emitted bytes stay unchanged.
        by_material = {}
        for face in item["polys"]:
            by_material.setdefault((face[0], face[1]), []).append(face)
        n = 0
        sorted_polys = []
        for texture in textures:
            counts = []
            for style in MAT_TYPES:
                matches = by_material.get((texture, style), ())
                counts.append(len(matches))
                sorted_polys.extend(matches)
            if any(c > 65535 for c in counts) or n > 65535:
                raise ValueError("P3D texture-info 16-bit limit exceeded")
            _pack(stream, "<7H", n, *counts)
            n += sum(counts)
        if n != len(item["polys"]):
            raise AssertionError("Triangle classification lost polygons")
        _pack(stream, "<H", len(item["points"]))
        for point in item["points"]:
            # Unlike ordinary Wurunduk bbox-normalization, the tile's main
            # vertices retain their authored XY offset from the fixed grid
            # origin. Auxiliary meshes remain local to their own centres and
            # are translated by the P3D submesh position as before.
            xy_reference = main_center if name == "main" else center
            _pack(stream, "<3f", point[0]-xy_reference[0],
                  point[2]-center[2], point[1]-xy_reference[1])
        _pack(stream, "<H", len(sorted_polys))
        for _tex, _style, verts, uvcoords in sorted_polys:
            # Native P3D format stores vertices in order 1,3,2 and flips V.
            for vi in (0, 2, 1):
                vertex = verts[vi]
                u,v = uvcoords[vi]
                if vertex > 65535 or not math.isfinite(u) or not math.isfinite(v):
                    raise ValueError("Invalid P3D triangle data")
                _pack(stream, "<H2f", vertex, u, 1.0-v)
        stats.append({"mesh": name, "flags": flags,
                      "vertices": len(item["points"]), "triangles": len(item["polys"]),
                      "source_objects": item.get("source_objects", [item["obj"].name])})
    stream.write(b"USER")
    _pack(stream, "<Ii", 1337, 0)
    auto_main_sources = [obj.name for obj in by_name["main"]
                         if str(obj.get("crashday_source_mesh_name", "")).strip().lower() != "main"]
    return stream.getvalue(), {"key": key, "meshes": stats, "textures": textures,
                               "auto_main_sources": auto_main_sources,
                               # Distinguish authored logical cell bounds from
                               # potentially oversized actual native P3D geometry.
                               # Never infer a future CFL footprint from mesh AABB.
                               "logical_tile_bounds_xy": [xmin, xmax, ymin, ymax],
                               "tile_origin_world_xy": [main_center[0], main_center[1]],
                               "main_world_center": _center(main_bounds), "floor_z": floor_z}


def _directory_safe(target):
    # Resolve Windows junctions/symlinks before testing ancestry: a non-Steam
    # Crashday install can live anywhere, including a custom portable folder.
    target = Path(os.path.realpath(str(target)))
    parts = [p.lower() for p in target.parts]
    if "steamapps" in parts or ("crashday" in parts and "games" in parts):
        raise ValueError("Refusing output inside Steam or Crashday game/mod directories")
    for ancestor in (target,) + tuple(target.parents):
        source_data = ancestor / "data"
        if source_data.is_dir() and any(source_data.glob("dat[0-9][0-9][0-9].cpk")):
            raise ValueError("Refusing output inside an installed Crashday game or its mods: " + str(ancestor))
    if target.exists() and not target.is_dir():
        raise ValueError("Destination is not a directory")
    return target


def export_generated_tiles(context, settings):
    if not settings.export_directory.strip():
        raise ValueError("Choose a private P3D export folder")
    directory = _directory_safe(__import__("bpy").path.abspath(settings.export_directory))
    prefix = settings.export_file_prefix
    if not prefix or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", prefix):
        raise ValueError("File prefix must be 1–50 ASCII letters, digits, underscores or hyphens")
    items = context.selected_objects if settings.export_selected_only else context.scene.objects
    groups = {}
    for obj in items:
        if obj.type == "MESH" and "crashday_tile_key" in obj:
            groups.setdefault(obj["crashday_tile_key"], []).append(obj)
    if not groups:
        raise ValueError("No generated tile meshes available for export")
    floor_z = None
    if settings.use_floor_level_empty:
        floor = context.scene.objects.get("floor_level")
        if floor is None or floor.type != "EMPTY":
            raise ValueError("Enable floor-level only with an existing scene Empty named 'floor_level'")
        floor_z = float(floor.matrix_world.translation.z)
    pending = []
    total = 0
    for key in sorted(groups):
        payload, audit = build_tile_p3d(groups[key], floor_z=floor_z)
        path = directory / (prefix + key + ".p3d")
        if os.path.lexists(str(path)):
            raise FileExistsError("Refusing to overwrite existing P3D: " + str(path))
        total += len(payload)
        if total > MAX_TOTAL_OUTPUT_BYTES:
            raise ValueError("Generated P3D set exceeds 512 MiB in-memory safety limit")
        audit["bytes"] = len(payload)
        audit["sha256"] = hashlib.sha256(payload).hexdigest()
        pending.append((path, payload, audit))

    # Persist source-authored placement and exact output identities for future
    # World Builder preparation and independent offline validation. No source
    # filenames, local game paths, or owner-specific absolute paths are stored.
    # Every output, INCLUDING this manifest, participates in no-overwrite and
    # rollback semantics. Generate and validate it before any filesystem write.
    manifest_path = None
    manifest_bytes = None
    if getattr(settings, "write_export_manifest", False):
        manifest_path = directory / (prefix + "tiles_manifest.json")
        if os.path.lexists(str(manifest_path)):
            raise FileExistsError("Refusing to overwrite existing tile manifest: " + str(manifest_path))
        manifest = {
            "schema": "micas-crashday-tile-export/v1",
            "coordinate_system": "Blender XYZ in meters; logical tile centre in world XY",
            "p3d_files": [],
            "p3d_count": len(pending),
            "total_p3d_bytes": total,
        }
        for path, payload, audit in pending:
            manifest["p3d_files"].append({
                "file": path.name,
                "bytes": len(payload),
                "sha256": audit["sha256"],
                "key": audit["key"],
                "tile_origin_world_xy": audit.get("tile_origin_world_xy"),
                "logical_tile_bounds_xy": audit.get("logical_tile_bounds_xy"),
                "main_world_center": audit.get("main_world_center"),
                "floor_z": audit.get("floor_z"),
                "auto_main_sources": audit.get("auto_main_sources", []),
                "textures": audit.get("textures", []),
                "meshes": audit.get("meshes", []),
            })
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2,
                                     ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if total + len(manifest_bytes) > MAX_TOTAL_OUTPUT_BYTES:
            raise ValueError("Generated P3D set and manifest exceed 512 MiB in-memory safety limit")
    # Validate *every* tile before creating any files.
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, payload, _audit in pending:
            with path.open("xb") as out:
                created.append(path)
                out.write(payload)
        if manifest_path is not None:
            with manifest_path.open("xb") as out:
                created.append(manifest_path)
                out.write(manifest_bytes)
    except Exception:
        for path in created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return {"directory": str(directory), "files": len(pending), "bytes": total,
            "sha256": {path.name: audit["sha256"] for path, _, audit in pending},
            "tiles": [audit for _, _, audit in pending],
            "auto_main_fragments": sum(len(audit.get("auto_main_sources", []))
                                       for _, _, audit in pending),
            "manifest": str(manifest_path) if manifest_path is not None else None,
            "manifest_sha256": (hashlib.sha256(manifest_bytes).hexdigest()
                                if manifest_bytes is not None else None)}
