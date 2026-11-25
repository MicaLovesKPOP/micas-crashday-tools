"""Private offline layout of oversized physical regions on the native 20m grid.

No geometry is clipped or emitted here. The center of each physical region
must be the export reference frame; model bytes and engine acceptance are
separate gates. Native CFL footprints remain 1x1, 1x2, 2x1 or 2x2.
"""
import math

from .author_trk_offline import MAX_AXIS, SAFE


class MacroLayoutError(ValueError):
    pass


def _pair(value, name, minimum, maximum):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            any(type(v) is not int or not minimum <= v <= maximum for v in value)):
        raise MacroLayoutError("Invalid " + name)
    return tuple(value)


def _bounds(x, y, w, h, origin):
    return [origin[0] + 20 * x, origin[0] + 20 * (x + w),
            origin[1] + 20 * y, origin[1] + 20 * (y + h)]


def plan_centered_macro_anchors(grid_dimensions_cells, world_origin_xy, regions):
    """Plan nonoverlapping centered native anchors for a full physical partition.

    Each region supplies cfl, role (checkpoint/remainder), cell_origin and
    cell_dimensions. These are physical grid-aligned rectangles, not logical
    CFL sizes. Odd axes use a centered 1-cell anchor, even axes use 2 cells.
    The model's native XY frame then coincides with the physical region center
    without needing an XY translation caused solely by anchor selection.
    Remaining cells require a separately verified native void asset.
    """
    width, height = _pair(grid_dimensions_cells, "world grid dimensions", 1, MAX_AXIS)
    if (not isinstance(world_origin_xy, (list, tuple)) or len(world_origin_xy) != 2 or
            any(type(v) not in (float, int) or not math.isfinite(v)
                for v in world_origin_xy)):
        raise MacroLayoutError("Invalid world origin")
    if not isinstance(regions, (list, tuple)) or not 1 <= len(regions) <= width * height:
        raise MacroLayoutError("Invalid physical region count")
    physical = set()
    occupied = set()
    names = set()
    pieces = []
    for region in regions:
        if not isinstance(region, dict):
            raise MacroLayoutError("Invalid physical region")
        name = region.get("cfl")
        role = region.get("role")
        if (not isinstance(name, str) or not SAFE.fullmatch(name) or
                not name.endswith(".cfl") or name.casefold() in names):
            raise MacroLayoutError("Invalid or duplicate region CFL name")
        names.add(name.casefold())
        x, y = _pair(region.get("cell_origin"), "physical region origin", 0, MAX_AXIS-1)
        w, h = _pair(region.get("cell_dimensions"), "physical region dimensions", 1, MAX_AXIS)
        if x+w > width or y+h > height:
            raise MacroLayoutError("Physical region exceeds declared world grid")
        if role not in ("checkpoint", "remainder"):
            raise MacroLayoutError("Unknown physical region role")
        if role == "checkpoint" and (w, h) != (1, 1):
            raise MacroLayoutError("Checkpoint must have a physical 20x20m region")
        covered = {(i, j) for i in range(x, x+w) for j in range(y, y+h)}
        if physical & covered:
            raise MacroLayoutError("Physical regions overlap")
        physical.update(covered)
        aw, ah = (1 if w % 2 else 2), (1 if h % 2 else 2)
        ax, ay = x + (w-aw)//2, y + (h-ah)//2
        anchor = {(i, j) for i in range(ax, ax+aw) for j in range(ay, ay+ah)}
        if not anchor <= covered or anchor & occupied:
            raise MacroLayoutError("Native anchor overlaps or escapes physical region")
        occupied.update(anchor)
        bounds = _bounds(x, y, w, h, world_origin_xy)
        logical = _bounds(ax, ay, aw, ah, world_origin_xy)
        pieces.append({
            "cfl": name,
            "role": role,
            "physical_cell_origin": [x, y],
            "physical_cell_dimensions": [w, h],
            "physical_bounds_world_xy": bounds,
            "logical_cell_origin": [ax, ay],
            "logical_cell_footprint": [aw, ah],
            "logical_bounds_world_xy": logical,
            "required_model_origin_world_xy": [
                world_origin_xy[0] + 20*x + 10*w,
                world_origin_xy[1] + 20*y + 10*h],
        })
    if len(physical) != width * height:
        raise MacroLayoutError("Physical partition does not cover the declared world grid")
    voids = sorted(physical - occupied, key=lambda xy: (xy[1], xy[0]))
    return {
        "schema": "micas-crashday-macro-native-layout/v1",
        "cell_size_meters": 20,
        "grid_dimensions_cells": [width, height],
        "grid_origin_world_xy": list(world_origin_xy),
        "occupied_cell_count": len(occupied),
        "physical_cell_count": len(physical),
        "void_cell_count": len(voids),
        "void_cells": [list(xy) for xy in voids],
        "pieces": pieces,
        "status": "OFFLINE LAYOUT ONLY; NO MODEL EXPORT OR NATIVE PHYSICS PROOF",
    }
