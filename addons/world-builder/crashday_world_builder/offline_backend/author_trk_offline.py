"""PRIVATE offline flat-height CDTRK draft from independently staged CFL/P3D tiles.

The binary structure and 2x2 filler IDs are checked against real read-only
Crashday tracks. This is NOT game-loader/collision or gameplay qualification.
Never writes the installed game, user mods, or an existing destination.
Python 3.7+ standard library; source-only reproducible output.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
SCHEMA = "micas-crashday-offline-tile-stage/v0"
SAFE = re.compile(r"[A-Za-z0-9_-]{1,80}\.(?:cfl|p3d)\Z")
LABEL = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MAX_AXIS = 128
MAX_TOTAL_SOURCE_BYTES = 256 * 1024 * 1024
# Genuine top-left anchored multi-cell entries: top-right=0xFFC0,
# bottom-left=0xFFBF, bottom-right=0xFFBE. Verified in owner TRK data.
CONT_RIGHT = 65472
CONT_DOWN = 65471
CONT_DIAGONAL = 65470

# Original CDTRK scenery selector: 0/1/2 are native bounded backgrounds.
# WorldWrap already owns selector 3 as a *topology* declaration. Selector 4
# is a proposed Crashday Unleashed Void profile, not engine-verified yet.
SCENERY_PROFILES = {"highlands": 0, "racetrack": 1, "alps": 2,
                    "void_experimental": 4}


class DraftError(ValueError):
    pass


def validate_game_mode_mask(mask):
    """Exact serialized permission byte, including the preserved unknown high bit."""
    if type(mask) is not int or not 0 <= mask <= 255:
        raise DraftError("Game-mode permission byte must be an integer from 0 to 255")
    return mask


def _scenery_selector(profile, acknowledge_experimental_void=False):
    if type(profile) is not str or profile not in SCENERY_PROFILES:
        raise DraftError("Unknown scenery profile; WorldWrap selector 3 is reserved")
    if profile == "void_experimental" and not acknowledge_experimental_void:
        raise DraftError(
            "Experimental Void background selector 4 requires explicit acknowledgement; "
            "Crashday Unleashed support is NOT implemented or game-tested")
    return SCENERY_PROFILES[profile]


def _private_output_root(workspace=None):
    """Verify a real explicitly selected PRIVATE output directory.

    A package installed in Blender must not default to the addon installation
    directory. Refuse Steam/game trees even if the caller explicitly names one.
    This is not a game/mod installer; existing destinations stay untouched.
    """
    path = Path(workspace) if workspace is not None else OUT
    if not path.is_dir() or path.is_symlink():
        raise DraftError("Private output directory is missing or redirected")
    safe = path.resolve(strict=True)
    if any(part.casefold() == "steamapps" for part in safe.parts):
        raise DraftError("Refusing private world output inside Steam/game files")
    for ancestor in (safe,) + tuple(safe.parents):
        content = ancestor / "data"
        if content.is_dir() and any(content.glob("dat[0-9][0-9][0-9].cpk")):
            raise DraftError("Refusing private world output inside a game installation")
    return safe


def _verified_input(root, workspace=None):
    safe_out = _private_output_root(workspace)
    source = Path(root).resolve(strict=True)
    if source.parent != safe_out or not source.is_dir() or source.is_symlink():
        raise DraftError("Source must be a direct private project out/ stage directory")
    manifest = source / "world_tile_stage.json"
    if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size > 2_000_000:
        raise DraftError("Missing or unsafe staged world manifest")
    plan = json.loads(manifest.read_text(encoding="utf-8"))
    if plan.get("schema") != SCHEMA or plan.get("cell_size_meters") != 20:
        raise DraftError("Unexpected staged world schema or cell size")
    pieces = plan.get("pieces")
    if not isinstance(pieces, list) or not 1 <= len(pieces) <= 8192:
        raise DraftError("Unsafe or empty piece list")
    for directory in ("tiles", "models"):
        folder = source / "content" / directory
        if not folder.is_dir() or folder.resolve() != (source / "content" / directory).absolute():
            raise DraftError("Unsafe or missing native content directory: " + directory)
    origin = plan.get("grid_origin_world_xy")
    if (not isinstance(origin, list) or len(origin) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in origin)):
        raise DraftError("Missing finite authored world grid origin")
    seen = set()
    assets = []
    total_bytes = 0
    for piece in pieces:
        if not isinstance(piece, dict):
            raise DraftError("Malformed staged piece")
        for kind, folder, suffix in (("cfl", "tiles", ".cfl"), ("p3d", "models", ".p3d")):
            name = piece.get(kind)
            digest = piece.get(kind + "_sha256")
            if (not isinstance(name, str) or not SAFE.fullmatch(name)
                    or not name.endswith(suffix)
                    or not isinstance(digest, str) or
                    not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise DraftError("Unsafe file or missing source SHA-256 for " + kind)
            if (kind, name.casefold()) in seen:
                raise DraftError("Duplicate staged native filename")
            seen.add((kind, name.casefold()))
            file = source / "content" / folder / name
            if not file.is_file() or file.is_symlink() or file.resolve().parent != (source / "content" / folder).resolve():
                raise DraftError("Missing or redirected staged model/CFL: " + name)
            byte_size = file.stat().st_size
            total_bytes += byte_size
            if byte_size < 1 or byte_size > 64 * 1024 * 1024 or total_bytes > MAX_TOTAL_SOURCE_BYTES:
                raise DraftError("Source asset or total source size exceeds bounded private limit")
            raw = file.read_bytes()
            if len(raw) != byte_size or hashlib.sha256(raw).hexdigest() != digest:
                raise DraftError("Source SHA-256/size mismatch for: " + name)
            assets.append((folder, name, raw, digest))
        # A matching filename/hash alone is not proof the CFL references its
        # paired P3D or uses the footprint claimed by the manifest.
        cfl_bytes = next(raw for folder, name, raw, _ in assets
                         if folder == "tiles" and name == piece["cfl"])
        try:
            lines = cfl_bytes.decode("ascii").splitlines()
        except UnicodeError as exc:
            raise DraftError("Non-ASCII provisional CFL: " + piece["cfl"]) from exc
        dims = piece.get("logical_cell_footprint")
        if (len(lines) < 4 or lines[0] != "Crashday-FieldObject-File" or
                lines[2] != piece["p3d"] or
                not isinstance(dims, list) or len(dims) != 2 or
                lines[3] != "{} {}".format(*dims)):
            raise DraftError("CFL model or footprint disagrees with staged provenance")
    return plan, assets


def _verified_staged_source_tgas(stage_root, plan):
    """Verify optional source-image sidecars without buffering all artwork.

    Geometry/TRK remains independent of textures. Only source-authored,
    SHA-verified Blender TGA images pass through; never synthesize native TEX.
    """
    metadata = plan.get("source_textures")
    if metadata is None:
        if any(key in plan for key in ("source_native_texture_refs",
                                       "unresolved_source_texture_refs",
                                       "source_texture_manifest_sha256")):
            raise DraftError("Partial staged source TGA provenance")
        return []
    from .source_tga_stage import _hash_file, NATIVE_ID, SHA256, MAX_BATCH_BYTES, MAX_IMAGE_BYTES
    if not isinstance(metadata, list) or len(metadata) > 8192:
        raise DraftError("Malformed staged source TGA records")
    refs = plan.get("source_native_texture_refs")
    unresolved = plan.get("unresolved_source_texture_refs")
    manifest_hash = plan.get("source_texture_manifest_sha256")
    if (not isinstance(refs, list) or
            any(not isinstance(n, str) or not NATIVE_ID.fullmatch(n) or n == "colwhite" for n in refs) or
            len(refs) != len(set(refs)) or
            not isinstance(unresolved, list) or
            any(not isinstance(n, str) or n not in refs for n in unresolved) or
            len(unresolved) != len(set(unresolved)) or
            not isinstance(manifest_hash, str) or not SHA256.fullmatch(manifest_hash)):
        raise DraftError("Malformed staged TGA source provenance")
    folder = Path(stage_root) / "content" / "textures"
    seen, items, total = set(), [], 0
    if metadata and (not folder.is_dir() or folder.is_symlink()):
        raise DraftError("Missing/redirected staged TGA directory")
    for item in metadata:
        if not isinstance(item, dict):
            raise DraftError("Malformed staged TGA entry")
        texture, filename = item.get("texture"), item.get("file")
        width, height, size, digest = (item.get(k) for k in ("width", "height", "bytes", "sha256"))
        if (not isinstance(texture, str) or not NATIVE_ID.fullmatch(texture) or
                texture not in refs or texture in seen or filename != texture + ".tga" or
                type(width) is not int or not 1 <= width <= 65535 or
                type(height) is not int or not 1 <= height <= 65535 or
                type(size) is not int or not 18 < size <= MAX_IMAGE_BYTES or
                not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise DraftError("Unsafe staged TGA record/name/hash")
        source = folder / filename
        if (not source.is_file() or source.is_symlink() or
                source.resolve().parent != folder.resolve(strict=True) or
                source.stat().st_size != size or _hash_file(source) != digest):
            raise DraftError("Missing, redirected, resized or altered staged TGA: " + filename)
        total += size
        if total > MAX_BATCH_BYTES:
            raise DraftError("Staged TGA batch exceeds 1 GiB")
        seen.add(texture)
        items.append((source, item))
    if (set(refs) != set(unresolved).union(seen) or
            set(unresolved).intersection(seen) or
            plan.get("source_texture_bytes") != total):
        raise DraftError("Staged TGA resolved/unresolved references or byte counts disagree")
    return items


def _validate_macro_void_cfl(raw, records):
    """Only accept the complete, inert authored gap-filler CFL subset.

    This is a deliberately narrow structural contract, not a native-engine
    claim. Existing non-macro private drafts retain their legacy reader.
    """
    if not raw.endswith(b"\r\n") or b"\n" in raw.replace(b"\r\n", b"") or b"\r" in raw.replace(b"\r\n", b""):
        raise DraftError("Macro void CFL must use complete CRLF records")
    if len(records) != 15 or records[4] != "GROUND_VOID":
        raise DraftError("Macro void CFL requires complete 1x1 GROUND_VOID records")
    if records[5] != "0":
        raise DraftError("Macro void CFL must use Autolist=0")
    if records[6] != "NONE" or records[7] not in ("0", "0.0"):
        raise DraftError("Macro void CFL must not replace or displace geometry")
    if records[8] not in ("0", "1") or records[9] != "0" or records[10] not in ("0", "1"):
        raise DraftError("Macro void CFL must be non-checkpoint with valid flags")
    if (records[11:15] != ["default", "STOP",
                          "------------------- VEGETATION -------------------",
                          "NO_VEGETATION"]):
        raise DraftError("Macro void CFL requires default height, STOP and NO_VEGETATION")


def _verified_void_pair(cfl_path, p3d_path, staged_assets, macro_layout=False):
    """Use an explicitly selected, verified existing 1x1 native void fixture.

    Unlike a fabricated blank tile, this preserves the owner's source-grounded
    void CFL/P3D bytes for *private* authoring. Never search game folders or
    silently copy an unknown stock/mod asset into a distributable kit.
    """
    if cfl_path is None and p3d_path is None:
        return None, [], None
    if cfl_path is None or p3d_path is None:
        raise DraftError("Provide both --void-cfl and --void-p3d, or neither")
    sources = []
    existing = {(folder, name.casefold()) for folder, name, _raw, _sha in staged_assets}
    for path, folder, suffix, cap in (
            (Path(cfl_path), "tiles", ".cfl", 1024 * 1024),
            (Path(p3d_path), "models", ".p3d", 64 * 1024 * 1024)):
        if (not path.is_file() or path.is_symlink() or
                not SAFE.fullmatch(path.name) or not path.name.endswith(suffix)):
            raise DraftError("Unsafe or missing explicit native void source: " + path.name)
        if (folder, path.name.casefold()) in existing:
            raise DraftError("Explicit void source collides with authored staged asset: " + path.name)
        size = path.stat().st_size
        if size < 1 or size > cap:
            raise DraftError("Native void source exceeds bounded size: " + path.name)
        raw = path.read_bytes()
        if len(raw) != size:
            raise DraftError("Native void source changed while reading: " + path.name)
        sources.append((folder, path.name, raw, hashlib.sha256(raw).hexdigest()))
    # Exact source CFL declarations, not model name guesses, define 1x1 void
    # behavior. Check model reference and native grammar landmarks first.
    try:
        # Native owner CFLs may carry Windows-encoded German comments. Strip
        # comments as bytes; only structural identifiers must be ASCII.
        lines = [line.split(b"#", 1)[0].strip().decode("ascii")
                 for line in sources[0][2].splitlines()]
        lines = [line for line in lines if line]
    except UnicodeError as exc:
        raise DraftError("Explicit void CFL structural identifiers are not ASCII") from exc
    if (len(lines) < 5 or lines[0] != "Crashday-FieldObject-File" or
            lines[2] != sources[1][1] or lines[3] != "1 1" or
            lines[4] not in ("GROUND_VOID", "GROUND_FLAT")):
        raise DraftError("Explicit void CFL 1x1 model reference or ground type is invalid")
    if macro_layout:
        _validate_macro_void_cfl(sources[0][2], lines)
    # A native P3D structural check is necessary even when a custom source
    # explicitly points to it. This does NOT prove in-game null collision.
    from .validate_p3d import validate_bytes
    report = validate_bytes(sources[1][2], sources[1][1])
    if not report["ok"]:
        raise DraftError("Explicit native P3D void source invalid: " +
                         "; ".join(report["errors"][:3]))
    return sources[0][1], sources, lines[4]


def plan_flat_track(plan, void_field=None):
    """Derive native top-down 20m cells, not P3D collision/visual extents.

    Require full authored rectangular coverage by default. Explicit native
    1x1 void source may fill otherwise empty cells, with no invented geometry.
    """
    pieces = plan["pieces"]
    cells = {}
    anchors = {}
    xmax = ymax = 0
    names = set()
    for piece in pieces:
        xy = piece.get("logical_cell_origin")
        wh = piece.get("logical_cell_footprint")
        name = piece["cfl"]
        if (not isinstance(xy, list) or len(xy) != 2 or
                any(type(v) is not int or v < 0 for v in xy) or
                not isinstance(wh, list) or len(wh) != 2 or
                any(type(v) is not int or v not in (1, 2) for v in wh)):
            raise DraftError("Missing/unsupported native 1x1, 1x2, 2x1, 2x2 cell footprint")
        x, y = xy
        w, h = wh
        world = piece.get("logical_bounds_world_xy")
        origin = plan["grid_origin_world_xy"]
        expected = [origin[0] + 20 * x, origin[0] + 20 * (x + w),
                    origin[1] + 20 * y, origin[1] + 20 * (y + h)]
        if (not isinstance(world, list) or len(world) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v)
                    for v in world) or
                any(abs(value - truth) > 0.0002
                    for value, truth in zip(world, expected))):
            raise DraftError("Stage logical cell coordinates disagree with world bounds")
        xmax, ymax = max(xmax, x + w), max(ymax, y + h)
        if name.casefold() in names:
            raise DraftError("Conflicting/duplicated CFL filenames")
        names.add(name.casefold())
        anchors[(x, y)] = (name, w, h)
        for dx in range(w):
            for dy in range(h):
                pos = (x + dx, y + dy)
                if pos in cells:
                    raise DraftError("Overlapping authored map tile at {}".format(pos))
                cells[pos] = name
    if "grid_dimensions_cells" in plan:
        # An oversized physical region may have a centered small CFL anchor.
        # Deriving world dimensions from only the anchors would truncate the
        # outer void cells, shifting checkpoint indices and terrain extent.
        dimensions = plan["grid_dimensions_cells"]
        if (not isinstance(dimensions, list) or len(dimensions) != 2 or
                any(type(v) is not int or not 1 <= v <= MAX_AXIS for v in dimensions)
                or dimensions[0] < xmax or dimensions[1] < ymax):
            raise DraftError("Invalid declared world grid dimensions for native anchors")
        xmax, ymax = dimensions
    if xmax > MAX_AXIS or ymax > MAX_AXIS or not xmax or not ymax:
        raise DraftError("World dimensions exceed conservative private authoring limits")
    if len(cells) != plan.get("occupied_cell_count"):
        raise DraftError("Staged occupied cell count does not match exact coverage")
    if len(cells) != xmax * ymax and void_field is None:
        raise DraftError("Uncovered logical cells; no invented background/void tiles")
    field_files = sorted((name for name, _, _ in anchors.values()), key=lambda s: s.casefold())
    if void_field is not None:
        if not isinstance(void_field, str) or not SAFE.fullmatch(void_field) or not void_field.endswith(".cfl"):
            raise DraftError("Unsafe explicit void CFL filename")
        if void_field.casefold() in (name.casefold() for name in field_files):
            raise DraftError("Explicit void field collides with an authored CFL")
        field_files.append(void_field)
        field_files.sort(key=lambda s: s.casefold())
    field_ids = {name: i for i, name in enumerate(field_files)}
    tiles = [(None, 0, 0, 0)] * (xmax * ymax)
    # Stage has Blender Y north, while file row 0 is the *north* edge.
    def set_cell(col, north_row, fid):
        file_row = ymax - 1 - north_row
        index = file_row * xmax + col
        if tiles[index][0] is not None:
            raise DraftError("Conflicting native cell fill")
        tiles[index] = (fid, 0, 0, 0)
    for (x, y), (name, w, h) in anchors.items():
        top = y + h - 1
        set_cell(x, top, field_ids[name])
        if w == 2:
            set_cell(x + 1, top, CONT_RIGHT)
        if h == 2:
            set_cell(x, y, CONT_DOWN)
        if w == 2 and h == 2:
            set_cell(x + 1, y, CONT_DIAGONAL)
    if void_field is not None:
        for x in range(xmax):
            for y in range(ymax):
                file_row = ymax - 1 - y
                index = file_row * xmax + x
                if tiles[index][0] is None:
                    set_cell(x, y, field_ids[void_field])
    if any(t[0] is None for t in tiles):
        raise DraftError("Native grid has unassigned cells")
    return xmax, ymax, field_files, tiles


def _native_test_drive_strip(width, height, fields, tiles):
    """Append a stock 1x1 ground/checkpoint strip without moving source cells.

    Based on the original engine-written 2026-11-23 owner Test Drive TRK:
    an entirely filled 4x4 source world was enlarged to 5x4, and the right
    column held three field.cfl cells plus one chkfield.cfl at TRK cell 14.
    The stock CFL/P3D assets are REFERENCES, never copied or redistributed.
    This is an opt-in spawn convenience, NOT proof of custom P3D collision.
    """
    if width >= MAX_AXIS:
        raise DraftError("Test Drive strip would exceed private track width limit")
    existing = {name.casefold() for name in fields}
    if "field.cfl" in existing or "chkfield.cfl" in existing:
        raise DraftError("Authored/void field name conflicts with stock Test Drive strip")
    names = list(fields) + ["field.cfl", "chkfield.cfl"]
    ground_id, checkpoint_id = len(fields), len(fields) + 1
    checkpoint_row = height // 2  # zero-based, TRK file rows run north to south
    expanded = []
    for row in range(height):
        expanded.extend(tiles[row * width:(row + 1) * width])
        expanded.append((checkpoint_id if row == checkpoint_row else ground_id,
                         0, 0, 0))
    new_width = width + 1
    checkpoint_index = checkpoint_row * new_width + width
    return new_width, height, names, expanded, [checkpoint_index]


def _track_layout(plan, void_field=None, test_drive_start=False,
                  checkpoint_route=None):
    width, height, fields, tiles = plan_flat_track(plan, void_field)
    checkpoints = []
    if test_drive_start and checkpoint_route:
        raise DraftError("Cannot combine stock Test Drive strip and authored checkpoint route")
    if checkpoint_route:
        from .checkpoint_routes import native_route_layout
        tiles, checkpoints = native_route_layout(
            plan, fields, tiles, width, height, checkpoint_route)
    elif test_drive_start:
        width, height, fields, tiles, checkpoints = _native_test_drive_strip(
            width, height, fields, tiles)
    return width, height, fields, tiles, checkpoints


def make_track_bytes(plan, void_field=None, scenery_profile="highlands",
                     acknowledge_experimental_void=False,
                     test_drive_start=False, checkpoint_route=None, game_mode_mask=255):
    game_mode_mask = validate_game_mode_mask(game_mode_mask)
    selector = _scenery_selector(scenery_profile, acknowledge_experimental_void)
    width, height, fields, tiles, checkpoints = _track_layout(
        plan, void_field, test_drive_start, checkpoint_route)
    # CDTRK original source format; no invented dynamic or embedded game.dbs
    # records. The OPTIONAL native stock checkpoint uses an observed real
    # engine TRK pattern; authored world geometry and terrain stay unchanged.
    parts = [b"CDTRK", struct.pack("<i", 0),
             b"Mica's Crashday Tools\0",
             b"PRIVATE OFFLINE FLAT TRACK DRAFT - NOT GAME TESTED\0"]
    parts.extend(struct.pack("<i", 0) + b"\0" for _ in range(20))
    parts.extend([struct.pack("<B", 0), b"day.amb\0",
                  struct.pack("<H", len(fields))])
    parts.extend(name.encode("ascii") + b"\0" for name in fields)
    parts.append(struct.pack("<HH", width, height))
    parts.extend(struct.pack("<HBBB", *tile) for tile in tiles)
    parts.extend([struct.pack("<H", 0),  # dynamic file table
                  struct.pack("<H", 0),  # dynamic instance table
                  struct.pack("<H", len(checkpoints))])
    parts.extend(struct.pack("<H", position) for position in checkpoints)
    parts.append(struct.pack("<BfB", game_mode_mask, 0.25, selector))
    count = (4 * width + 1) * (4 * height + 1)
    parts.append(b"\0\0\0\0" * count)  # flat source heightmap, 5 m samples
    return b"".join(parts)


def _shared_world_z_model(original, piece):
    """Derive a *private* P3D header variant on a common 0m vertical datum.

    The Tile Splitter stores mesh Z relative to its own source main centre.
    Its historical header height = 2 * original main maximum Z, which our
    source-grounded Blender reader interprets as a further height/2 lift.
    Preserve the original vertex bytes; correct only the 4-byte P3D model
    header so (local main centre + header/2) == original world main centre.

    This restores our documented Blender *geometry* reconstruction; native
    game rendering/collision semantics remain UNTESTED. Never alter originals.
    """
    if piece.get("source_floor_z") is not None:
        raise DraftError("Shared-world-Z mode requires default non-floor-level source P3Ds")
    center = piece.get("source_main_world_center_xyz")
    if (not isinstance(center, list) or len(center) != 3 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in center)):
        raise DraftError("Shared-world-Z requires finite authored original main Z in stage")
    from .validate_p3d import validate_bytes
    native = validate_bytes(original, piece["p3d"])
    if not native["ok"]:
        raise DraftError("Invalid original P3D for world-Z correction: " + piece["p3d"])
    mains = [m for m in native["meshes"] if m["name"] == "main"]
    if len(mains) != 1 or mains[0]["positioned_vertex_bounds_xyz"] is None:
        raise DraftError("Shared-world-Z requires exactly one real main mesh")
    zlo = mains[0]["positioned_vertex_bounds_xyz"]["min"][2]
    zhi = mains[0]["positioned_vertex_bounds_xyz"]["max"][2]
    local_center_z = (zlo + zhi) * 0.5
    local_half_span = (zhi - zlo) * 0.5
    previous_half_height = native["header_dimensions_xyz"][2] * 0.5
    original_center_z = float(center[2])
    # This check proves we're correcting the writer's known default rather
    # than blindly changing an arbitrary third-party author's P3D header.
    expected_old_half_height = original_center_z + local_half_span
    if abs(previous_half_height - expected_old_half_height) > 0.005:
        raise DraftError("P3D header is not the verified Tile Splitter Z convention: " +
                         piece["p3d"])
    new_half_height = original_center_z - local_center_z
    try:
        patched = bytearray(original)
        struct.pack_into("<f", patched, 8, 2.0 * new_half_height)
    except (OverflowError, struct.error) as exc:
        raise DraftError("Source world-Z is outside native float32 model range") from exc
    adjusted = bytes(patched)
    check = validate_bytes(adjusted, piece["p3d"])
    if not check["ok"] or check["meshes"] != native["meshes"] or check["textures"] != native["textures"]:
        raise DraftError("Rebased model did not retain native mesh structure")
    restored_center = local_center_z + check["header_dimensions_xyz"][2] * 0.5
    if abs(restored_center - original_center_z) > 0.0002:
        raise DraftError("Failed to reproduce authored source Z centre exactly")
    return adjusted, {
        "p3d": piece["p3d"],
        "source_main_center_z": original_center_z,
        "original_header_height": native["header_dimensions_xyz"][2],
        "new_header_height": check["header_dimensions_xyz"][2],
        "previous_vertical_error": previous_half_height + local_center_z - original_center_z,
        "restored_vertical_error": restored_center - original_center_z,
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "private_variant_sha256": hashlib.sha256(adjusted).hexdigest(),
    }


def _rebase_assets_shared_world_z(plan, assets):
    by_name = {p["p3d"]: p for p in plan["pieces"]}
    adjusted_assets = []
    corrections = []
    for folder, name, raw, sha in assets:
        if folder != "models" or name not in by_name:
            adjusted_assets.append((folder, name, raw, sha))
            continue
        corrected, details = _shared_world_z_model(raw, by_name[name])
        adjusted_assets.append((folder, name, corrected,
                                hashlib.sha256(corrected).hexdigest()))
        corrections.append(details)
    if len(corrections) != len(by_name):
        raise DraftError("Shared-world-Z did not inspect all authored P3Ds")
    return adjusted_assets, corrections


def prepare_track(stage_root, void_cfl=None, void_p3d=None,
                  vertical_mode="native_unmodified", scenery_profile="highlands",
                  acknowledge_experimental_void=False, test_drive_start=False,
                  checkpoint_route_json=None, workspace=None, game_mode_mask=255):
    game_mode_mask = validate_game_mode_mask(game_mode_mask)
    plan, assets = _verified_input(stage_root, workspace=workspace)
    source_tgas = _verified_staged_source_tgas(stage_root, plan)
    from .source_tile_preview_stage import verify_staged_previews
    native_previews = verify_staged_previews(stage_root, plan)
    from .source_tex_stage import verify_staged_tex
    source_tex = verify_staged_tex(stage_root, plan)
    if vertical_mode not in ("native_unmodified", "shared_source_z"):
        raise DraftError("Unknown private vertical placement mode")
    corrections = []
    if vertical_mode == "shared_source_z":
        assets, corrections = _rebase_assets_shared_world_z(plan, assets)
    void_name, void_assets, void_ground = _verified_void_pair(
        void_cfl, void_p3d, assets, macro_layout=plan.get("macro_layout", False))
    route = None
    patches = []
    checkpoint_model_rotations = []
    if checkpoint_route_json is not None:
        from .checkpoint_routes import read_route, patch_cfl_assets, counterrotate_checkpoint_assets
        route = read_route(checkpoint_route_json)
        # Validate the route before looking up model targets. The native
        # turn rotates the entire tile; compensate in private model bytes.
        _track_layout(plan, void_name, test_drive_start, route)
        assets, patches = patch_cfl_assets(assets, route)
        assets, checkpoint_model_rotations = counterrotate_checkpoint_assets(plan, assets, route)
    content = make_track_bytes(plan, void_name, scenery_profile,
                               acknowledge_experimental_void, test_drive_start, route,
                               game_mode_mask=game_mode_mask)
    digest = hashlib.sha256(content).hexdigest()
    w, h, names, tiles, checkpoints = _track_layout(
        plan, void_name, test_drive_start, route)
    source_w, source_h, _, _ = plan_flat_track(plan, void_name)
    summary = {
        "schema": "micas-crashday-private-flat-trk-draft/v0",
        "status": "OFFLINE SOURCE PARSER ONLY; NO GAME LOAD/COLLISION ACCEPTANCE",
        "tile_dimensions": [w, h],
        "test_drive_start": bool(test_drive_start),
        "test_drive_checkpoint_indices": checkpoints if test_drive_start else [],
        "ordered_checkpoint_indices": checkpoints,
        "authored_checkpoint_route": bool(route),
        "authored_checkpoint_cfl_order": ([item["cfl"] for item in route] if route else []),
        "authored_checkpoint_rotation_values": ([item["rotation"] for item in route] if route else []),
        "checkpoint_cfl_source_rewrites": patches,
        "checkpoint_model_counterrotations": checkpoint_model_rotations,
        "test_drive_stock_cfl_references": (
            ["field.cfl", "chkfield.cfl"] if test_drive_start else []),
        "test_drive_stock_assets_bundled": False,
        "source_grid_origin_world_xy": plan["grid_origin_world_xy"],
        "height_samples": [(4 * w + 1), (4 * h + 1)],
        "authored_fields": len(plan["pieces"]),
        "native_field_definitions": len(names),
        "void_cells": (source_w * source_h - plan["occupied_cell_count"]),
        "stock_test_drive_strip_cells": (h if test_drive_start else 0),
        "void_cfl": void_name, "void_ground": void_ground,
        "void_p3d_sha256": void_assets[1][3] if void_assets else None,
        "scenery_profile": scenery_profile,
        "game_mode_mask": game_mode_mask,
        "trk_scenery_selector": _scenery_selector(
            scenery_profile, acknowledge_experimental_void),
        "requires_unleashed_void_runtime": scenery_profile == "void_experimental",
        "void_background_status": (
            "PROPOSED SELECTOR 4 ONLY; NO ENGINE IMPLEMENTATION OR GAME TEST"
            if scenery_profile == "void_experimental" else "NATIVE STOCK BACKGROUND"),
        "vertical_mode": vertical_mode,
        "source_z_adjusted_models": len(corrections),
        "source_tga_count": len(source_tgas),
        "source_tga_bytes": sum(row["bytes"] for _src, row in source_tgas),
        "native_tile_preview_count": len(native_previews),
        "native_tile_preview_bytes": sum(row["bytes"] for _src, row in native_previews),
        "native_tile_preview_manifest_sha256": plan.get("native_tile_preview_manifest_sha256"),
        "native_tile_preview_status": ("BLENDER-RENDERED NATIVE TGA; GAME PALETTE LOAD UNTESTED"
                                       if native_previews else "NOT INCLUDED"),
        "unresolved_source_texture_refs": plan.get("unresolved_source_texture_refs", []),
        "source_texture_manifest_sha256": plan.get("source_texture_manifest_sha256"),
        "native_tex_descriptors": (len(source_tex) if "native_tex_descriptors" in plan
                                   else "NOT GENERATED OR GAME-VALIDATED"),
        "native_tex_status": ("EXPERIMENTAL OWNER-SELECTED PRESETS; NO GAME TEST"
                              if "native_tex_descriptors" in plan
                              else "NOT GENERATED"),
        "native_tex_policy_sha256": plan.get("native_tex_policy_sha256"),
        "unresolved_native_tex_refs": plan.get("unresolved_native_tex_refs", []),
        "source_z_max_original_offset_m": max(
            (abs(c["previous_vertical_error"]) for c in corrections), default=0.0),
        "source_z_max_restored_error_m": max(
            (abs(c["restored_vertical_error"]) for c in corrections), default=0.0),
        "private_model_header_adjustments": corrections,
        "reserved_multicell_slots": sum(t[0] >= 65470 for t in tiles),
        "trk_sha256": digest, "source_stage": Path(stage_root).name,
        "limitations": (["Flat 0m terrain only", "No dynamics/AI/navigation",
                         "Stock field.cfl/chkfield.cfl required in target game",
                         "Authored P3D collision unverified"] if test_drive_start else
                        (["Flat 0m terrain only", "No dynamics/AI/navigation",
                          "Custom checkpoint bounds/rotations need native-game acceptance"]
                         if route else ["Flat 0m terrain only", "No checkpoints/dynamics/AI/navigation"])) +
                       ["CFL ground/texture semantics provisional",
                        "This generated output has not been separately game-tested"],
    }
    if scenery_profile == "void_experimental":
        summary["limitations"].append(
            "Selector 4 is NOT supported by unmodified Crashday; requires future Unleashed runtime")
    return content, assets + void_assets + source_tex, summary


def write_private_track(stage_root, label, void_cfl=None, void_p3d=None,
                        vertical_mode="native_unmodified", scenery_profile="highlands",
                        acknowledge_experimental_void=False, test_drive_start=False,
                        checkpoint_route_json=None, workspace=None, game_mode_mask=255):
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise DraftError("Unsafe private output label")
    content, assets, summary = prepare_track(
        stage_root, void_cfl=void_cfl, void_p3d=void_p3d,
        vertical_mode=vertical_mode, scenery_profile=scenery_profile,
        acknowledge_experimental_void=acknowledge_experimental_void,
        test_drive_start=test_drive_start,
        checkpoint_route_json=checkpoint_route_json, workspace=workspace,
        game_mode_mask=game_mode_mask)
    out = _private_output_root(workspace)
    target = out / label
    if target.exists() or target.is_symlink():
        raise DraftError("Private target already exists")
    created = False
    try:
        target.mkdir()
        created = True
        for folder, name, raw, digest in assets:
            place = target / "content" / folder
            place.mkdir(parents=True, exist_ok=True)
            (place / name).write_bytes(raw)
        # Streaming native-name TGA copy: never buffer the potentially large
        # source-image batch in the .trk writer's model/CFL byte arrays.
        source_tgas = _verified_staged_source_tgas(stage_root, json.loads(
            (Path(stage_root) / "world_tile_stage.json").read_text(encoding="utf-8")))
        if source_tgas:
            from .source_tga_stage import _hash_file
            texture_dir = target / "content" / "textures"
            texture_dir.mkdir(parents=True, exist_ok=True)
            for src, record in source_tgas:
                dst = texture_dir / record["file"]
                with src.open("rb") as reader, dst.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                if dst.stat().st_size != record["bytes"] or _hash_file(dst) != record["sha256"]:
                    raise DraftError("Private TGA copy failed source SHA-256 parity")
        # Palette previews are a separate native mod asset class. Copy the
        # renderer's exact bytes, never rescale or infer a P3D visual thumbnail
        # from native physical geometry bounding boxes.
        from .source_tile_preview_stage import verify_staged_previews, file_sha
        native_previews = verify_staged_previews(
            stage_root, json.loads(
                (Path(stage_root) / "world_tile_stage.json").read_text(encoding="utf-8")))
        for src, record in native_previews:
            dst = target.joinpath(*record["path"].split("/"))
            dst.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as reader, dst.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            if dst.stat().st_size != record["bytes"] or file_sha(dst) != record["sha256"]:
                raise DraftError("Native palette preview did not preserve its source SHA-256")
        trk_dir = target / "content" / "tiles"
        trk_name = label + ".trk"
        (trk_dir / trk_name).write_bytes(content)
        summary["track_file"] = "content/tiles/" + trk_name
        (target / "PRIVATE_TRACK_DRAFT.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="ascii")
    except Exception:
        if created and target.is_dir() and not target.is_symlink():
            shutil.rmtree(str(target))
        raise
    return target, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage-root", required=True)
    ap.add_argument("--void-cfl", help="Existing read-only 1x1 native void CFL; private gap fill ONLY")
    ap.add_argument("--void-p3d", help="Existing read-only P3D referenced by --void-cfl")
    ap.add_argument("--vertical-mode", choices=("native_unmodified", "shared_source_z"),
                    default="native_unmodified",
                    help="Private experimental P3D Z header correction from source main centres")
    ap.add_argument("--scenery-profile", choices=tuple(SCENERY_PROFILES), default="highlands",
                    help="TRK scenery; void_experimental requires a future Unleashed runtime")
    ap.add_argument("--acknowledge-experimental-void", action="store_true",
                    help="Permit private selector-4 TRK fixture; NOT game-load qualified")
    ap.add_argument("--test-drive-start", action="store_true",
                    help="Append one right-side native stock ground/checkpoint column; "
                         "requires original field.cfl/chkfield.cfl, never bundles them")
    ap.add_argument("--checkpoint-route-json",
                    help="Ordered 1x1 authored checkpoint CFLs/rotations/bounds JSON; "
                         "mutually exclusive with --test-drive-start")
    ap.add_argument("--game-mode-mask", type=lambda v: int(v, 0), default=255,
                    help="Native permission byte (0..255); default preserves 0xff")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--output-label", default="offline_flat_track_draft")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.write:
            target, result = write_private_track(
                args.stage_root, args.output_label, args.void_cfl, args.void_p3d,
                args.vertical_mode, args.scenery_profile,
                args.acknowledge_experimental_void, args.test_drive_start,
                args.checkpoint_route_json, game_mode_mask=args.game_mode_mask)
            result["private_directory"] = str(target)
        else:
            _, _, result = prepare_track(
                args.stage_root, args.void_cfl, args.void_p3d,
                args.vertical_mode, args.scenery_profile,
                args.acknowledge_experimental_void, args.test_drive_start,
                args.checkpoint_route_json, game_mode_mask=args.game_mode_mask)
        print(json.dumps(result, sort_keys=True, indent=2) if args.json
              else "{}: {} fields, {}x{} cells, NO GAME VALIDATION".format(
                  "WRITTEN PRIVATE DRAFT" if args.write else "DRY RUN",
                  result["authored_fields"], *result["tile_dimensions"]))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("INVALID PRIVATE TRK DRAFT: " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
