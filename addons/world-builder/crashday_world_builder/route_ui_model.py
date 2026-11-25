"""Pure Python 3.7 tile catalogue and checkpoint UI -> canonical route bridge.

No Blender/game dependency. Verifies the exported Tile Splitter manifest,
source hashes and logical 20m cells; never rewrites existing files.
Final native TRK/CFL qualification remains the independent writer's job.
"""
import hashlib
import json
import math
from pathlib import Path
import re

MANIFEST_SCHEMA = "micas-crashday-tile-export/v1"
ROUTE_SCHEMA = "micas-crashday-checkpoint-route/v1"
_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\.p3d\Z")
_KEY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
MAX_MANIFEST = 4 * 1024 * 1024
MAX_ASSET = 64 * 1024 * 1024
MAX_TILES = 2048
# Exact box from read-only stock dat006.cpk content/tiles/chkfield.cfl.
# This describes the checkpoint region, not proven car spawn/collision.
STOCK_CHECKPOINT_BOX = [-8.7, 4.2, 8.7, 0.0]


class UIModelError(ValueError):
    pass


def safe_route_filename(name):
    """Allow only portable plain JSON leaf names, never NTFS stream/device aliases.

    Windows treats colons as alternate-data-stream separators, including
    streams attached to existing user files despite an 'xb' output open.
    Its DOS device names are also reserved even with additional extensions.
    The Blender UI and core writer both run this check before any file IO.
    """
    if (type(name) is not str or not re.fullmatch(
            r"[A-Za-z0-9_-][A-Za-z0-9 _.-]{0,79}\.json", name, re.IGNORECASE)
            or name[:-5].endswith((" ", "."))):
        raise UIModelError("Unsafe route filename; use a short plain .json name")
    device = name[:-5].split(".", 1)[0].upper()
    if (device in {"CON", "PRN", "AUX", "NUL"} or
            re.fullmatch(r"(?:COM|LPT)[1-9]", device)):
        raise UIModelError("Unsafe reserved Windows route filename")
    return name


def _hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _verified_manifest_path(path):
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise UIModelError("Choose a real Tile Splitter manifest JSON file")
    if not 1 <= source.stat().st_size <= MAX_MANIFEST:
        raise UIModelError("Tile manifest is empty or exceeds 4 MiB")
    resolved = source.resolve(strict=True)
    if resolved.parent != source.parent.resolve(strict=True):
        raise UIModelError("Tile manifest path is redirected")
    return resolved


def load_catalog(path, macro_layout=False):
    """Return a sha-bound map of physical regions (or standard logical tiles).

    Macro mode accepts already exported 20m-grid rectangles, including carved
    remainders. Only a physical 1x1 region qualifies as a checkpoint.

    The manifest must be the exact source that the downstream World Builder
    expects, never a guessed filename from Blender object naming alone.
    """
    if type(macro_layout) is not bool:
        raise UIModelError("Macro layout must be explicitly enabled or disabled")
    source = _verified_manifest_path(path)
    content = source.read_bytes()
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise UIModelError("Tile manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise UIModelError("This is not a Tile Splitter export manifest")
    records = manifest.get("p3d_files")
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_TILES:
        raise UIModelError("Expected 1..{} exported tiles".format(MAX_TILES))
    if manifest.get("p3d_count") != len(records):
        raise UIModelError("Tile manifest file count differs from entries")
    entries = {}
    names = set()
    anchors = []
    for item in records:
        if not isinstance(item, dict):
            raise UIModelError("Invalid tile entry")
        name, key, digest = item.get("file"), item.get("key"), item.get("sha256")
        raw = item.get("logical_tile_bounds_xy")
        if (not isinstance(name, str) or not _NAME.fullmatch(name) or
                not isinstance(key, str) or not _KEY.fullmatch(key) or
                not isinstance(digest, str) or not _SHA.fullmatch(digest) or
                not isinstance(raw, list) or len(raw) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in raw)):
            raise UIModelError("Tile has invalid native name, key, hash or XY bounds")
        if key in entries or name.casefold() in names:
            raise UIModelError("Duplicate tile identity in manifest")
        names.add(name.casefold())
        x0, x1, y0, y1 = [float(v) for v in raw]
        w, h = x1 - x0, y1 - y0
        if macro_layout:
            if (not 1 <= round(w / 20) <= 128 or not 1 <= round(h / 20) <= 128 or
                    abs(w - round(w / 20) * 20) > 0.0002 or
                    abs(h - round(h / 20) * 20) > 0.0002):
                raise UIModelError("Macro regions require 1..128 physical 20m cells per axis")
        elif (min(w, h) <= 0 or
                min(abs(w - 20), abs(w - 40)) > 0.0002 or
                min(abs(h - 20), abs(h - 40)) > 0.0002):
            raise UIModelError("Tile requires exact 20m/40m logical footprint")
        file = source.parent / name
        if (not file.is_file() or file.is_symlink() or
                file.resolve(strict=True).parent != source.parent or
                not 1 <= file.stat().st_size <= MAX_ASSET or
                _hash_file(file) != digest):
            raise UIModelError("Tile model is absent or changed: " + name)
        entries[key] = {"key": key, "p3d": name, "cfl": name[:-4] + ".cfl",
                        "bounds": [x0, x1, y0, y1],
                        "footprint": [int(round(w / 20)), int(round(h / 20))],
                        "sha256": digest,
                        "main_world_center": item.get("main_world_center"),
                        "floor_z": item.get("floor_z")}
        anchors.append((x0, y0))
    ox = min(x for x, _y in anchors)
    oy = min(y for _x, y in anchors)
    occupied = set()
    grid = [0, 0]
    for entry in entries.values():
        x0, x1, y0, y1 = entry["bounds"]
        cx = round((x0 - ox) / 20)
        cy = round((y0 - oy) / 20)
        if (abs((x0 - ox) / 20 - cx) > 0.00001 or
                abs((y0 - oy) / 20 - cy) > 0.00001):
            raise UIModelError("Tiles are not aligned to the same native 20m grid")
        end_x, end_y = cx + entry["footprint"][0], cy + entry["footprint"][1]
        if macro_layout and max(end_x, end_y) > 128:
            raise UIModelError("Macro world exceeds 128 native cells per axis")
        grid = [max(grid[0], end_x), max(grid[1], end_y)]
        for dx in range(entry["footprint"][0]):
            for dy in range(entry["footprint"][1]):
                if (cx+dx, cy+dy) in occupied:
                    raise UIModelError("Two exported tiles occupy the same 20m cell")
                occupied.add((cx+dx, cy+dy))
    if macro_layout and len(occupied) != grid[0] * grid[1]:
        raise UIModelError("Macro physical regions must cover the complete rectangular grid")
    return {"path": str(source), "sha256": hashlib.sha256(content).hexdigest(),
            "entries": entries, "total": len(entries),
            "one_by_one": sum(e["footprint"] == [1, 1] for e in entries.values())}


def audit_generated_scene_tiles(scene_tiles, catalog):
    """Guard against silently building only selected exported tiles.

    Each scene item is (logical_key, native_bounds_or_None). Multiple mesh
    fragments sharing one logical tile count only once. A manifest may include
    extra tiles not present in the current scene (external authoring is valid),
    but it must not silently omit any scene-generated tile. Bounds comparison
    rejects a stale export from an earlier grid origin/size using the same key.
    No source geometry is changed and this does NOT prove P3D mesh freshness.
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get("entries"), dict):
        raise UIModelError("Load and verify exported tiles before auditing the scene")
    missing = set()
    count = set()
    for key, bounds in scene_tiles:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise UIModelError("Generated Blender tile has an invalid source key")
        count.add(key)
        exported = catalog["entries"].get(key)
        if exported is None:
            missing.add(key)
            continue
        if bounds is not None:
            if (not isinstance(bounds, (tuple, list)) or len(bounds) != 4 or
                    any(type(v) not in (int, float) or
                        not math.isfinite(v) for v in bounds)):
                raise UIModelError("Generated Blender tile has invalid XY metadata: " + key)
            if any(abs(float(a) - float(b)) > 0.0002
                   for a, b in zip(bounds, exported["bounds"])):
                raise UIModelError(
                    "Scene tile {} no longer matches exported grid bounds; "
                    "re-export the generated tiles".format(key))
    if missing:
        sample = ", ".join(sorted(missing)[:3])
        raise UIModelError(
            "Incomplete Tile Splitter export: {} of {} generated scene tiles "
            "absent from manifest ({}); export ALL generated tiles, not only "
            "the selected objects".format(len(missing), len(count), sample))
    return {"scene_tile_count": len(count),
            "manifest_tile_count": len(catalog["entries"]),
            "scene_tiles_covered": len(count)}


def audit_live_scene_models(groups, catalog, serialize):
    """Check current generated geometry against the exact exported model and placement."""
    for key, objects in sorted(groups.items()):
        entry = catalog["entries"].get(key)
        if entry is None:
            raise UIModelError("Generated tile missing from export: " + key)
        center = entry.get("main_world_center")
        floor = entry.get("floor_z")
        if (not isinstance(center, (list, tuple)) or len(center) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in center) or
                (floor is not None and
                 (type(floor) not in (int, float) or not math.isfinite(floor)))):
            raise UIModelError("Missing or invalid source placement; re-export " + key)
        payload, audit = serialize(objects, floor_z=floor)
        for field, expected in (("main_world_center", center),
                                ("logical_tile_bounds_xy", entry["bounds"])):
            actual = audit.get(field)
            if (not isinstance(actual, (list, tuple)) or len(actual) != len(expected) or
                    any(not math.isfinite(v) or abs(v-e) > 0.0002
                        for v, e in zip(actual, expected))):
                raise UIModelError("Scene geometry or placement changed; re-export " + key)
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise UIModelError("Scene geometry or placement changed; re-export " + key)
    return len(groups)


def choose_selected_tile(selected_keys, catalog, existing_cfls):
    """Choose exactly one *selected* generated 1x1 tile; no silent all fallback."""
    keys = set(selected_keys)
    if len(keys) != 1:
        raise UIModelError("Select objects belonging to exactly one generated tile")
    key = next(iter(keys))
    entry = catalog["entries"].get(key)
    if entry is None:
        raise UIModelError("Selected tile is not in the exported tile manifest")
    if entry["footprint"] != [1, 1]:
        raise UIModelError("Checkpoint tiles must be 1x1 (20m by 20m)")
    if entry["cfl"].casefold() in {x.casefold() for x in existing_cfls}:
        raise UIModelError("This tile is already in the checkpoint route")
    return entry


def validated_payload(entries, catalog, canonical_validator):
    """Canonical route JSON, cross-checked against source tile identities."""
    checkpoints = []
    for entry in entries:
        key = entry["key"]
        item = catalog["entries"].get(key)
        if item is None or item["footprint"] != [1, 1] or item["cfl"] != entry["cfl"]:
            raise UIModelError("Route references an absent/non-1x1 source tile")
        checkpoints.append({"cfl": item["cfl"], "rotation": entry["rotation"],
                            "box": list(entry["box"])})
    route = {"schema": ROUTE_SCHEMA, "checkpoints": checkpoints}
    canonical_validator(route)  # Reuse exactly the native writer's validator.
    return (json.dumps(route, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def export_private_route(path, payload):
    """Create a new route file exclusively; never change the game or existing data."""
    target = Path(path)
    safe_route_filename(target.name)
    parent = target.parent.resolve(strict=True)
    # Reject game/Steam content and redirects to game installations.
    if any(part.casefold() == "steamapps" for part in parent.parts):
        raise UIModelError("Refusing to write route data inside Steam/Crashday")
    for ancestor in (parent,) + tuple(parent.parents):
        if (ancestor / "data").is_dir() and any((ancestor / "data").glob("dat[0-9][0-9][0-9].cpk")):
            raise UIModelError("Refusing game installation or mod path")
    dest = parent / target.name
    created_identity = None
    try:
        with dest.open("xb") as out:
            # Only this exclusive create establishes ownership of the file.
            created_identity = dest.stat().st_ino
            out.write(payload)
        if dest.read_bytes() != payload:
            raise IOError("Route byte verification failed")
    except FileExistsError:
        raise UIModelError("Route already exists; choose a new filename")
    except Exception:
        # Roll back only the file created by this invocation. Never delete an
        # existing route or another process's replacement at the same path.
        if created_identity is not None:
            try:
                if dest.stat().st_ino == created_identity:
                    dest.unlink()
            except FileNotFoundError:
                pass
        raise
    return str(dest), hashlib.sha256(payload).hexdigest()
