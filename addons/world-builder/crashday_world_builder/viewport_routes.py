"""Small pure-Python bridge from native 20m cells to checkpoint markers.

The Blender arrow is an authoring aid, not a native-game compass oracle.
Owner establishes default north; read-only source track route trends suggest
native turns advance clockwise. Actual engine headings are NOT game-verified.
No Blender, Crashday runtime, game assets or file writes here.
"""
import math

class ViewportError(ValueError):
    pass


def scene_leaf_tiles(objects):
    """Return generated mesh leaves and retained parent keys, without mutation."""
    groups, ancestry = {}, {}
    for obj in objects:
        key = obj.get("crashday_tile_key")
        if key is None:
            continue
        parent = obj.get("crashday_carve_parent_key")
        if (not isinstance(key, str) or not key or
                getattr(obj, "type", None) != "MESH" or
                (parent is not None and
                 (not isinstance(parent, str) or not parent or parent == key))):
            raise ViewportError("Invalid generated tile lineage")
        if key in ancestry and ancestry[key] != parent:
            raise ViewportError("Inconsistent generated tile lineage: " + key)
        ancestry[key] = parent
        groups.setdefault(key, []).append(obj)
    parents = {p for p in ancestry.values() if p is not None}
    for key in ancestry:
        seen = set()
        cursor = key
        while cursor in ancestry and ancestry[cursor] is not None:
            if cursor in seen:
                raise ViewportError("Cyclic generated tile lineage")
            seen.add(cursor)
            cursor = ancestry[cursor]
    return [obj for key, group in groups.items() if key not in parents
            for obj in group], parents


def reject_superseded_export(catalog, parent_keys):
    stale = set(catalog["entries"]) & set(parent_keys)
    if stale:
        raise ViewportError("Manifest includes superseded source tiles; "
                            "select and re-export active tiles: " +
                            ", ".join(sorted(stale)[:3]))


def pick_scene_leaf_at_xy(objects, x, y):
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (x, y)):
        raise ViewportError("Viewport click has no finite world XY location")
    leaves, _ = scene_leaf_tiles(objects)
    choices = set()
    for obj in leaves:
        bounds = [obj.get("crashday_tile_" + field) for field in
                  ("min_x", "max_x", "min_y", "max_y")]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds):
            raise ViewportError("Active tile is missing valid XY bounds")
        x0, x1, y0, y1 = bounds
        if x0 - .01 <= x <= x1 + .01 and y0 - .01 <= y <= y1 + .01:
            if min(abs(x-x0), abs(x-x1), abs(y-y0), abs(y-y1)) < .01:
                raise ViewportError("Click farther inside the tile, away from its edge")
            if x0 < x < x1 and y0 < y < y1:
                choices.add(obj.get("crashday_tile_key"))
    if len(choices) != 1:
        raise ViewportError("Click inside exactly one active generated tile")
    return next(iter(choices))


def provisional_marker_yaw(turn):
    """Blender Z yaw: turn 0 -> +Y, subsequent turns clockwise.

    This fixes an authoring-preview handedness error. Read-only original
    track checkpoint-to-next direction trends strongly support 1=E, 2=S,
    3=W, but terrain curvature and duplicate maps prevent game-proof.
    We therefore keep the native controls named 'turn 0..3'.
    """
    if type(turn) is not int or turn not in (0, 1, 2, 3):
        raise ViewportError("Native checkpoint turn must be 0..3")
    return -turn * math.pi / 2.0


def pick_checkpoint_key(key, catalog, current_cfls, select_fn):
    """Resolve an explicitly clicked exported tile, rejecting occupied/multicell."""
    if not isinstance(key, str) or not key:
        raise ViewportError(
            "Click a generated exported tile (20m by 20m) in the 3D view")
    return select_fn([key], catalog, current_cfls)


def pick_checkpoint_at_xy(x, y, catalog, current_cfls, select_fn):
    """Choose the single exported logical cell under a viewport click.

    The original source mesh may lie ON TOP of generated split tiles, so
    Blender object-ray-hit identity alone must never decide the tile. Use
    actual world XY and SHA-verified logical footprint instead. Reject
    ambiguous edge clicks rather than silently choosing the wrong cell.
    """
    if (type(x) not in (int, float) or type(y) not in (int, float)
            or not math.isfinite(x) or not math.isfinite(y)):
        raise ViewportError("Viewport click has no finite world XY location")
    choices = []
    for record in catalog["entries"].values():
        x0, x1, y0, y1 = record["bounds"]
        # Treat clicks within 1cm of a native tile border as ambiguous.
        if x0 - 0.01 <= x <= x1 + 0.01 and y0 - 0.01 <= y <= y1 + 0.01:
            if (min(abs(x - x0), abs(x - x1),
                    abs(y - y0), abs(y - y1)) < 0.01):
                raise ViewportError("Click farther inside the 20m tile, away from its edge")
            if x0 < x < x1 and y0 < y < y1:
                choices.append(record["key"])
    if len(choices) != 1:
        raise ViewportError(
            "Click inside exactly one exported native tile cell; found {}".format(
                len(choices)))
    return select_fn(choices, catalog, current_cfls)


def route_marker_specs(route_items, catalog):
    """Deterministic marker identities and XY centres; no inferred heading."""
    if len(route_items) > 1024:
        raise ViewportError("Too many checkpoint markers")
    specs = []
    known = set()
    for index, item in enumerate(route_items):
        key = item["key"]
        record = catalog["entries"].get(key)
        if record is None or record["footprint"] != [1, 1] or record["cfl"] != item["cfl"]:
            raise ViewportError(
                "Checkpoint {} has no matching exported 1x1 tile".format(index + 1))
        if key in known:
            raise ViewportError("Duplicate checkpoint tile: " + key)
        known.add(key)
        turn = item["rotation"]
        if type(turn) is not int or turn not in (0, 1, 2, 3):
            raise ViewportError("Native checkpoint turn must be 0..3")
        x0, x1, y0, y1 = record["bounds"]
        if not all(math.isfinite(x) for x in (x0, x1, y0, y1)):
            raise ViewportError("Checkpoint bounds must be finite")
        specs.append({
            "key": key, "turn": turn,
            "center": ((x0 + x1) * 0.5, (y0 + y1) * 0.5),
            "label": "CP {:03d} {} Turn {}".format(
                index + 1, "START" if index == 0 else "NEXT", turn),
        })
    return specs
