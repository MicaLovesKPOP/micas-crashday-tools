"""Plan a minimum-rectangle 20 m checkpoint carve from a larger authoring tile.

This only plans geometry partitions. It does NOT edit Blender meshes, emit
P3Ds/CFLs/TRKs, or assert that oversized Crashday geometry has passed physics.
Python 3.7+ standard library; safe to use for preview/preflight without bpy.
"""
import math
from functools import lru_cache

GRID_METERS = 20
SCHEMA = "micas-crashday-macro-checkpoint-carve/v1"


class CarveError(ValueError):
    pass


def _length_in_cells(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise CarveError(name + " must be a positive finite number of metres")
    cells = int(round(value / GRID_METERS))
    if cells < 1 or abs(value - cells * GRID_METERS) > 0.000001:
        raise CarveError(name + " must be a multiple of 20 metres")
    return cells


def _new_authoring_cells(value, name):
    """Accept 20m, 40m, then 40m increments (80m, 120m, ...).

    Existing carved or imported source regions may still have odd 20m widths;
    those are not choices offered for creating a NEW macro tile.
    """
    cells = _length_in_cells(value, name)
    if cells != 1 and cells % 2:
        raise CarveError(
            name + " for a NEW tile must be 20m or a positive multiple of 40m")
    return cells


def _origin(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise CarveError("World origin must be two finite coordinates")
    return [float(x) for x in value]


def _checkpoint(value, width, height):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            any(type(x) is not int for x in value)):
        raise CarveError("Choose a checkpoint using integer 20 m grid-cell indices")
    x, y = value
    if not 0 <= x < width or not 0 <= y < height:
        raise CarveError("Checkpoint cell lies outside the authoring tile")
    return x, y


def _horizontal(width, height, x, y):
    """Full-width rows around the checkpoint row, then its left/right ends."""
    result = [(x, y, 1, 1, "checkpoint")]
    if y:
        result.append((0, 0, width, y, "remainder"))
    if height - y - 1:
        result.append((0, y + 1, width, height - y - 1, "remainder"))
    if x:
        result.append((0, y, x, 1, "remainder"))
    if width - x - 1:
        result.append((x + 1, y, width - x - 1, 1, "remainder"))
    return result


def _vertical(width, height, x, y):
    """Full-height columns around the checkpoint column, then its ends."""
    result = [(x, y, 1, 1, "checkpoint")]
    if x:
        result.append((0, 0, x, height, "remainder"))
    if width - x - 1:
        result.append((x + 1, 0, width - x - 1, height, "remainder"))
    if y:
        result.append((x, 0, 1, y, "remainder"))
    if height - y - 1:
        result.append((x, y + 1, 1, height - y - 1, "remainder"))
    return result


def _validate_partition(rectangles, width, height):
    if sum(w * h for x, y, w, h, role in rectangles) != width * height:
        raise CarveError("Partition does not cover exactly the source area")
    for i, (x, y, w, h, role) in enumerate(rectangles):
        if w < 1 or h < 1 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise CarveError("Partition rectangle outside source bounds")
        for a, b, c, e, other_role in rectangles[i + 1:]:
            if x < a + c and a < x + w and y < b + e and b < y + h:
                raise CarveError("Partition rectangles overlap")


def _internal_seam_cells(rectangles, width, height):
    # Sum of all rectangle perimeters minus the source perimeter, divided by 2.
    return sum(w + h for x, y, w, h, role in rectangles) - (width + height)


def plan_checkpoint_carve(width_m, height_m, checkpoint_cell,
                          world_origin_xy=(0, 0), existing_source=False):
    """Return minimal axis-aligned RECTANGULAR pieces for one 20x20 m checkpoint.

    width_m, height_m: NEW tile sizes 20m, 40m, 80m, 120m, etc.
    checkpoint_cell: zero-based (column, row) inside this macro tile.
    world_origin_xy: original bottom-left coordinate (not moved or recentered).
    existing_source=True: permit other 20m-multiple dimensions (60m, 100m)
    only for imported/previously carved regions; never offer those sizes in
    the new-tile authoring picker. Generated remainders are never subdivided
    merely to fit the user-facing new-tile size menu.

    Output is an authoring/geometry partition only. Native Redline CFL logical
    footprints remain 1x1, 1x2, 2x1, 2x2; large physical pieces require a
    separately verified anchor + void/proxy-cell authoring strategy.
    """
    if type(existing_source) is not bool:
        raise CarveError("existing_source must be explicitly true or false")
    sizing = _length_in_cells if existing_source else _new_authoring_cells
    width = sizing(width_m, "Width")
    height = sizing(height_m, "Height")
    origin = _origin(world_origin_xy)
    x, y = _checkpoint(checkpoint_cell, width, height)
    candidates = [
        ("horizontal", _horizontal(width, height, x, y)),
        ("vertical", _vertical(width, height, x, y)),
    ]
    for axis, rectangles in candidates:
        _validate_partition(rectangles, width, height)
    # Both orientations have the minimum count (1 checkpoint + up to 4
    # nonempty remainder regions). Prefer fewer cut metres, then larger
    # contiguous chunks for a stable, economical partition.
    axis, rectangles = min(
        candidates,
        key=lambda item: (
            len(item[1]),
            _internal_seam_cells(item[1], width, height),
            -sum(w * w * h * h for x0, y0, w, h, role in item[1]),
            0 if item[0] == "horizontal" else 1))
    remainders = sorted((r for r in rectangles if r[4] == "remainder"),
                        key=lambda r: (-r[2] * r[3], r[1], r[0]))
    rectangles = [(x, y, 1, 1, "checkpoint")] + remainders
    pieces = []
    for px, py, w, h, role in rectangles:
        pieces.append({
            "role": role,
            "cell_origin": [px, py],
            "cell_dimensions": [w, h],
            "physical_dimensions_m": [w * GRID_METERS, h * GRID_METERS],
            "world_bounds_xy_m": [
                origin[0] + px * GRID_METERS,
                origin[0] + (px + w) * GRID_METERS,
                origin[1] + py * GRID_METERS,
                origin[1] + (py + h) * GRID_METERS,
            ],
        })
    return {
        "schema": SCHEMA,
        "grid_m": GRID_METERS,
        "authoring_dimensions_m": [width * GRID_METERS, height * GRID_METERS],
        "world_origin_xy_m": origin,
        "checkpoint_cell": [x, y],
        "checkpoint_world_bounds_xy_m": pieces[0]["world_bounds_xy_m"],
        "partition_axis": axis,
        "physical_piece_count": len(pieces),
        "underlying_native_20m_cells": width * height,
        "internal_cut_length_m": _internal_seam_cells(rectangles, width, height) * GRID_METERS,
        "pieces": pieces,
        "status": "PLANNED GEOMETRY ONLY; NO NATIVE GAME/PHYSICS ACCEPTANCE",
    }


# Bound the expensive search by the number of compressed subrectangles.
# Large or dense batches still get a deterministic feasible partition.
_BATCH_RECTANGLE_BUDGET = 12000
_BATCH_CHECKPOINT_LIMIT = 128


def _batch_score(rectangles):
    return (len(rectangles), sum(w + h for x, y, w, h, role in rectangles),
            -sum((w * h) ** 2 for x, y, w, h, role in rectangles))


def _sequential_partition(width, height, checkpoints):
    rectangles = [(0, 0, width, height, "remainder")]
    for cx, cy in checkpoints:
        for i, (x, y, w, h, role) in enumerate(rectangles):
            if x <= cx < x + w and y <= cy < y + h:
                options = (_horizontal(w, h, cx-x, cy-y),
                           _vertical(w, h, cx-x, cy-y))
                local = min(options, key=_batch_score)
                rectangles[i:i+1] = [(x+a, y+b, c, e, r)
                                      for a, b, c, e, r in local]
                break
    return rectangles


def _band_partition(width, height, checkpoints, transpose=False):
    if transpose:
        return [(y, x, h, w, role) for x, y, w, h, role in
                _band_partition(height, width, [(y, x) for x, y in checkpoints])]
    rows = {}
    for x, y in checkpoints:
        rows.setdefault(y, []).append(x)
    pieces = []
    previous_y = 0
    for y in sorted(rows):
        if y > previous_y:
            pieces.append((0, previous_y, width, y-previous_y, "remainder"))
        previous_x = 0
        for x in sorted(rows[y]):
            if x > previous_x:
                pieces.append((previous_x, y, x-previous_x, 1, "remainder"))
            pieces.append((x, y, 1, 1, "checkpoint"))
            previous_x = x+1
        if previous_x < width:
            pieces.append((previous_x, y, width-previous_x, 1, "remainder"))
        previous_y = y+1
    if previous_y < height:
        pieces.append((0, previous_y, width, height-previous_y, "remainder"))
    return pieces


def plan_checkpoint_batch(width_m, height_m, checkpoint_cells,
                          world_origin_xy=(0, 0), existing_source=False):
    """Plan several distinct 20m checkpoints together from one original tile.

    Minimize piece count, then internal cut length, then favor large pieces.
    Within the stated budget the search is exact among recursive straight
    full-region cuts at checkpoint edges. It does not claim optimality among
    arbitrary non-guillotine rectangle partitions. Above the budget select
    the best of deterministic sequential and row/column band partitions.
    Geometry is untouched; the input order is not a route order.
    """
    if type(existing_source) is not bool:
        raise CarveError("existing_source must be explicitly true or false")
    sizing = _length_in_cells if existing_source else _new_authoring_cells
    width, height = sizing(width_m, "Width"), sizing(height_m, "Height")
    origin = _origin(world_origin_xy)
    if (not isinstance(checkpoint_cells, (tuple, list)) or
            not 1 <= len(checkpoint_cells) <= _BATCH_CHECKPOINT_LIMIT):
        raise CarveError("Choose between 1 and 128 checkpoint cells per source tile")
    cells = tuple(sorted(_checkpoint(c, width, height) for c in checkpoint_cells))
    if len(set(cells)) != len(cells):
        raise CarveError("Duplicate checkpoint cell")
    xs = sorted({0, width} | {v for x, y in cells for v in (x, x+1)})
    ys = sorted({0, height} | {v for x, y in cells for v in (y, y+1)})
    max_states = len(xs)*(len(xs)-1)*len(ys)*(len(ys)-1)//4
    if max_states <= _BATCH_RECTANGLE_BUDGET:
        @lru_cache(maxsize=None)
        def best(a, b, c, d):
            x, y, w, h = xs[a], ys[c], xs[b]-xs[a], ys[d]-ys[c]
            inside = any(x <= px < x+w and y <= py < y+h for px, py in cells)
            if not inside or w == h == 1:
                return ((x, y, w, h, "checkpoint" if inside else "remainder"),)
            candidates = (
                [best(a, k, c, d) + best(k, b, c, d) for k in range(a+1, b)] +
                [best(a, b, c, k) + best(a, b, k, d) for k in range(c+1, d)])
            return min(candidates, key=_batch_score)
        rectangles = best(0, len(xs)-1, 0, len(ys)-1)
        best.cache_clear()
        method = "exact_checkpoint_edge_guillotine"
    else:
        rectangles = min((_sequential_partition(width, height, cells),
                          _sequential_partition(width, height, reversed(cells)),
                          _band_partition(width, height, cells),
                          _band_partition(width, height, cells, True)),
                         key=_batch_score)
        method = "bounded_deterministic_fallback"
    _validate_partition(rectangles, width, height)
    actual = {(x, y) for x, y, w, h, role in rectangles
              if role == "checkpoint" and w == h == 1}
    if actual != set(cells):
        raise CarveError("Batch partition lost a checkpoint")
    rectangles = sorted(rectangles,
        key=lambda r: (r[4] != "checkpoint", -r[2]*r[3], r[1], r[0]))
    pieces = []
    for x, y, w, h, role in rectangles:
        pieces.append({
            "role": role, "cell_origin": [x, y], "cell_dimensions": [w, h],
            "physical_dimensions_m": [w*GRID_METERS, h*GRID_METERS],
            "world_bounds_xy_m": [
                origin[0]+x*GRID_METERS, origin[0]+(x+w)*GRID_METERS,
                origin[1]+y*GRID_METERS, origin[1]+(y+h)*GRID_METERS],
        })
    return {
        "schema": "micas-crashday-macro-checkpoint-batch/v1",
        "grid_m": GRID_METERS,
        "authoring_dimensions_m": [width*GRID_METERS, height*GRID_METERS],
        "world_origin_xy_m": origin,
        "checkpoint_cells": [list(cell) for cell in cells],
        "partition_method": method,
        "physical_piece_count": len(pieces),
        "underlying_native_20m_cells": width*height,
        "internal_cut_length_m": _internal_seam_cells(rectangles, width, height)*GRID_METERS,
        "pieces": pieces,
        "status": "PLANNED GEOMETRY ONLY; NO NATIVE GAME/PHYSICS ACCEPTANCE",
    }
