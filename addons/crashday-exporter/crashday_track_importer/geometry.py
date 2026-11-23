"""Pure-math Crashday terrain sampling and tile placement.

Scale = native metres. The Blender visual scene uses Z up.
Coordinates are based on FlyX Loader/Build. Track file rows run north->south.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import math
import random


def p3d_material_is_smooth(material_type: str) -> bool:
    """Honor native P3D Gouraud/Flat face type, independently of .tex shader.

    P3D does not save custom normals. Crashday smooths Gouraud polygon
    groups and leaves Flat triangles faceted; shared Blender mesh vertices
    recreate that distinction without modifying any imported geometry.
    """
    return str(material_type).upper() in (
        "GOURAUD", "GOURAUD_METAL", "GOURAUD_METAL_ENV")


@dataclass(frozen=True)
class TerrainGrid:
    tiles_w: int
    tiles_h: int
    heights: tuple[float, ...]

    @property
    def nx(self) -> int:
        return self.tiles_w * 4 + 1

    @property
    def ny(self) -> int:
        return self.tiles_h * 4 + 1

    @property
    def zero(self) -> float:
        return self.heights[0]

    def height_at_index(self, i: int, j: int) -> float:
        return self.heights[j * self.nx + i] - self.zero

    def sample(self, east_m: float, north_m: float) -> float:
        """Bilinear sample of original height grid, matching game grid spacing.

        Outside map: zero, matching FlyX's out-of-bounds tile calculation.
        """
        u = east_m / 5
        v = (20 * self.tiles_h - north_m) / 5
        if u < -1e-6 or v < -1e-6 or u > self.nx - 1 + 1e-6 or v > self.ny - 1 + 1e-6:
            return 0.
        u = max(0, min(self.nx - 1, u))
        v = max(0, min(self.ny - 1, v))
        x0, y0 = math.floor(u), math.floor(v)
        x1, y1 = min(x0 + 1, self.nx - 1), min(y0 + 1, self.ny - 1)
        tx, ty = u - x0, v - y0
        a = self.height_at_index(x0, y0)
        b = self.height_at_index(x1, y0)
        c = self.height_at_index(x0, y1)
        d = self.height_at_index(x1, y1)
        return a * (1-tx)*(1-ty) + b*tx*(1-ty) + c*(1-tx)*ty + d*tx*ty


def dynamic_mesh_visual_ground_lift(model) -> float:
    """Translate a visual P3D's lowest actual vertex to its local Z=0 plane.

    A P3D header's nominal ``height / 2`` is not necessarily the distance
    from its origin to its lowest *rendered* vertex. This is visual grounding
    only, not a Crashday collision/contact-point or physics interpretation.
    """
    lowest = math.inf
    for sub in model.meshes:
        for vertex in sub.vertices:
            z = float(vertex[2]) + float(sub.pos[2])
            if not math.isfinite(z):
                raise ValueError("Dynamic P3D has non-finite vertex elevation")
            lowest = min(lowest, z)
    if not math.isfinite(lowest):
        raise ValueError("Dynamic P3D has no vertices to ground")
    return -lowest


def is_out_of_bounds_dynamic(east_m: float, south_m: float,
                             tiles_w: int, tiles_h: int) -> bool:
    """Classify an AUTHORED CDTRK dynamic origin beyond the mapped tile grid.

    The TRK dynamic horizontal coordinates are (east, south), while our
    Blender scene uses north=-south. Boundary origins remain in-bounds;
    an object anchored inside but overhanging the edge stays with the track.
    This cannot detect game-generated background meshes from TRK scenery.
    """
    if not all(math.isfinite(float(v)) for v in (east_m, south_m)):
        raise ValueError("Non-finite dynamic position")
    if tiles_w <= 0 or tiles_h <= 0:
        raise ValueError("Invalid track dimensions")
    north_m = -south_m
    return not (0.0 <= east_m <= tiles_w * 20.0 and
                0.0 <= north_m <= tiles_h * 20.0)


def dynamic_position(east_m: float, up_m: float, south_m: float,
                     grid: TerrainGrid) -> tuple[float, float, float]:
    """Visual preview placement for CDTRK's (east, up, south) reference.

    TRK stores horizontal north as negative south. Placed dynamics often
    have up=0 even over nonflat terrain; grounding to the heightmap is
    therefore a *visual approximation* of Crashday's dynamic drop/raycast,
    not a claim to preserve precise physics/collision or elevated bridges.
    """
    north_m = -south_m
    return east_m, north_m, up_m + grid.sample(east_m, north_m)


@dataclass(frozen=True)
class TilePlacement:
    east_m: float
    north_m: float
    angle: float
    mirrored: bool
    width_m: int
    depth_m: int

    def transform_xy(self, x: float, y: float) -> tuple[float, float]:
        # FlyX mirrors local X (and changes face winding), then applies yaw.
        xx = -x if self.mirrored else x
        c, s = math.cos(self.angle), math.sin(self.angle)
        return self.east_m + xx*c-y*s, self.north_m + xx*s+y*c

    def inverse_xy(self, east: float, north: float) -> tuple[float, float]:
        """Map a world-space seam point back into the original P3D tile."""
        dx, dy = east - self.east_m, north - self.north_m
        c, s = math.cos(self.angle), math.sin(self.angle)
        xx, yy = dx*c + dy*s, -dx*s + dy*c
        return (-xx if self.mirrored else xx), yy


def highest_triangle_height_at_xy(triangles, x, y):
    """Topmost Z intersection of a vertical ray with static tile triangles.

    Original VegetEdit documents CFL 'Auto Height' as dropping vegetation
    onto the TOP of the actual track-piece, not a generic TRK height grid.
    Use original/deformed tile faces without moving any vertices. This
    computes a visible top surface, not exact engine collision semantics.
    Return None for a miss: callers must mark their fallback approximate.
    """
    best = None
    for a, b, c in triangles:
        ax, ay, az = a
        bx, by, bz = b
        cx, cy, cz = c
        dx, dy = bx - ax, by - ay
        ex, ey = cx - ax, cy - ay
        det = dx * ey - dy * ex
        if abs(det) <= 1e-12:
            continue
        qx, qy = x - ax, y - ay
        u = (qx * ey - qy * ex) / det
        v = (dx * qy - dy * qx) / det
        if u < -1e-8 or v < -1e-8 or u + v > 1.0 + 1e-8:
            continue
        z = az + u * (bz - az) + v * (cz - az)
        if math.isfinite(z) and (best is None or z > best):
            best = z
    return best


def vegetation_zone_xy(placement: TilePlacement, tile_width: int, tile_depth: int,
                       source_x: float, source_y: float) -> tuple[float, float]:
    """Map a CFL vegetation article's top-left-based coordinates to world XY.

    CFL vegetation center X runs rightwards from zero; Y runs downward from
    zero into negative values. P3D tile origins are instead centered. The
    tile transform applies the same rotation/mirror as its parent CFL.
    This computes a zone center only; actual randomized engine spawning
    within its width/depth and collision dropping remain unknown.
    """
    if tile_width < 1 or tile_depth < 1:
        raise ValueError("Vegetation zone tile dimensions must be positive")
    if not math.isfinite(source_x) or not math.isfinite(source_y):
        raise ValueError("Vegetation zone coordinates must be finite")
    return placement.transform_xy(
        source_x - tile_width * 10., source_y + tile_depth * 10.)


def plan_vegetation_preview(track, loaded, placed_tile_indices,
                            veg_definitions, max_instances):
    """Plan bounded, seeded visual vegetation samples without Blender.

    The copy count range is real VEG metadata. Original SDK defines CFL
    probability per vegetation *group* (one chance for the whole article).
    Its random draw, copies-per-article and positions are still preview-only
    approximations of Crashday. Candidate records are selected by stable
    SHA256 priority across ALL placed tiles,
    so a small preview is geographically distributed, not just first cells.
    This emits transforms, never opens assets or launches/renderers.
    """
    if max_instances <= 0:
        # "Preview disabled" must not be mistaken for a vegetation-free CFL.
        count = 0
        for n in placed_tile_indices:
            fid = track.tiles[n][0]
            count += len(loaded[track.field_files[fid]][0].vegetation)
        return {"source_vegetation_zones": count, "candidate_instances": 0,
                "anchored_source_zones": 0, "anchored_candidate_instances": 0,
                "selected_anchored_instances": 0, "selected": ()}
    candidates = []
    zones = 0
    # Genuine zero-area, probability-one CFL articles with a fixed VEG copy
    # count specify deterministic XY anchors and counts. The actual game still
    # determines scale, spin, height/drop and any runtime visibility.
    anchored = set()
    anchored_candidates = 0
    for n in sorted(placed_tile_indices):
        fid, rotation, mirrored, _height = track.tiles[n]
        name = track.field_files[fid]
        definition = loaded[name][0]
        placement = tile_placement(n % track.width, n // track.width, track.height,
                                   definition.width, definition.depth,
                                   rotation, bool(mirrored))
        for idx, patch in enumerate(definition.vegetation):
            zones += 1
            # Source-scoped previews may contain same-named VEGs from distinct
            # mods. Prefer this cell's resolved definition over a global key.
            veg = veg_definitions.get((n, patch.veg_file),
                                      veg_definitions.get(patch.veg_file))
            if veg is None or veg.model is None:
                continue
            msg = "{}|{}|{}".format(n, idx, patch.veg_file).encode("utf-8")
            zone_seed = int.from_bytes(hashlib.sha256(msg).digest()[:8], "big")
            zone_rng = random.Random(zone_seed)
            copies = zone_rng.randint(veg.min_copies, veg.max_copies)
            # The original Crashday SDK defines CFL probability as the chance
            # the entire vegetation GROUP appears, not a per-copy Bernoulli
            # draw. This remains our deterministic preview RNG, not the game's.
            if zone_rng.random() >= patch.probability:
                continue
            if (patch.width == 0.0 and patch.depth == 0.0
                    and patch.probability == 1.0
                    and veg.min_copies == veg.max_copies and copies > 0):
                anchored.add((n, idx))
                anchored_candidates += copies
            for copy_idx in range(copies):
                seed_bytes = hashlib.sha256(
                    msg + b"|" + str(copy_idx).encode("ascii")).digest()
                priority = int.from_bytes(seed_bytes[:8], "big")
                rng = random.Random(priority)
                src_x = patch.x + (rng.random() - 0.5) * patch.width
                src_y = patch.y + (rng.random() - 0.5) * patch.depth
                east, north = vegetation_zone_xy(placement, definition.width,
                                                 definition.depth, src_x, src_y)
                candidates.append((priority, n, idx, copy_idx, patch, east, north,
                                   placement.angle, bool(mirrored)))
    # Never drop source-mandated point placements merely because optional
    # grass/shrub RNG samples rank earlier in a small preview. This is only
    # preview *selection*; non-anchored spawning remains an approximation.
    selected = tuple(heapq.nsmallest(
        max_instances, candidates,
        key=lambda item: (0 if (item[1], item[2]) in anchored else 1, item[0])))
    return {"source_vegetation_zones": zones,
            "candidate_instances": len(candidates),
            "anchored_source_zones": len(anchored),
            "anchored_candidate_instances": anchored_candidates,
            "selected_anchored_instances": sum(
                1 for item in selected if (item[1], item[2]) in anchored),
            "selected": selected}


def tile_placement(x: int, file_row: int, map_tiles_h: int,
                   width: int, depth: int, rotation_quarters: int,
                   mirrored: bool = False) -> TilePlacement:
    """Transform from CDTRK top-down row and CFL footprint to Blender metres.

    FlyX's Unity scene has 4 units per tile; source P3D geometries in metres.
    Positive Unity yaw maps to negative Blender Z rotation after axis swap.
    """
    if rotation_quarters not in (0, 1, 2, 3):
        raise ValueError(f"Invalid quarter turn: {rotation_quarters}")
    rw, rd = (depth, width) if rotation_quarters % 2 else (width, depth)
    angle_deg = rotation_quarters * (90 if mirrored else -90)
    return TilePlacement(
        east_m=x*20 + rw*10,
        north_m=(map_tiles_h-file_row)*20 - rd*10,
        angle=math.radians(angle_deg),
        mirrored=mirrored,
        width_m=rw*20,
        depth_m=rd*20
    )


@dataclass(frozen=True)
class TileHeightSurface:
    """CFL-restricted local tile height interpolation (first fidelity stage).

    V1/V2 suppress intermediate terrain samples along the corresponding
    20-metre local-X quarter, H1/H2 along local-Y. This implements the
    *per-tile* original_grid rule in FlyX Quarter.Generate_grid and the
    linear/bilinear fallback in Build.Calculate_All_RMC_points. The complete
    game uses a shared border vault across NEIGHBORING tiles, so the current
    caller must not report global bend parity as proven.
    """
    grid: TerrainGrid
    placement: TilePlacement
    width: int
    depth: int
    bend_rules: str

    def sample(self, local_x: float, local_y: float) -> float:
        world_x, world_y = self.placement.transform_xy(local_x, local_y)
        rules = frozenset(self.bend_rules.upper().split())
        if not any(token in rules for token in ("V1", "V2", "H1", "H2")):
            return self.grid.sample(world_x, world_y)

        # Original model origin is at the midpoint of its unrotated CFL
        # footprint; native P3D coordinates are metres, north in local +Y.
        west = -10.0 * self.width
        north = 10.0 * self.depth
        tx = (local_x - west) / 20.0
        ty = (north - local_y) / 20.0
        if not (-1e-5 <= tx <= self.width + 1e-5 and
                -1e-5 <= ty <= self.depth + 1e-5):
            # Real scenery may extend outside its registered tile footprint.
            return self.grid.sample(world_x, world_y)
        ix = max(0, min(self.width-1, math.floor(tx)))
        iy = max(0, min(self.depth-1, math.floor(ty)))
        vx = f"V{ix+1}" in rules
        hy = f"H{iy+1}" in rules
        if not vx and not hy:
            return self.grid.sample(world_x, world_y)

        left = west + 20.0 * ix
        right = left + 20.0
        top = north - 20.0 * iy
        bottom = top - 20.0
        fx = max(0.0, min(1.0, (local_x - left) / 20.0))
        fy = max(0.0, min(1.0, (top - local_y) / 20.0))

        def sample_at(x: float, y: float) -> float:
            wx, wy = self.placement.transform_xy(x, y)
            return self.grid.sample(wx, wy)

        if vx and hy:
            top_l = sample_at(left, top)
            top_r = sample_at(right, top)
            bot_l = sample_at(left, bottom)
            bot_r = sample_at(right, bottom)
            return ((1.0-fy) * ((1.0-fx)*top_l + fx*top_r) +
                    fy * ((1.0-fx)*bot_l + fx*bot_r))
        if vx:
            return (1.0-fx)*sample_at(left, local_y) + fx*sample_at(right, local_y)
        return (1.0-fy)*sample_at(local_x, top) + fy*sample_at(local_x, bottom)


class SharedBorderVault:
    """World-spanning CFL restricted quarter-edge map (FlyX-inspired).

    In FlyX Border_vault.Get_border_keys, Vn marks the north/south edges
    of a quarter (horizontal world-grid segments before rotation); Hn
    marks east/west edges. Neighboring tiles see the SAME restriction.
    This pass interpolates the 3 interior 5m terrain samples of each
    marked 20m edge from its unchanged endpoint heights. It corrects
    border seams without depending on tile iteration order.

    This is a deterministic, source-grounded static *approximation* of
    FlyX's mutable current_heights / Calculate_All_RMC_points process,
    not proof of complete game parity (especially overlapping and
    height-mixed pieces).
    """

    def __init__(self, original: TerrainGrid):
        self.original = original
        self.horizontal = set()
        self.vertical = set()
        self.out_of_bounds = 0

    def _add_world_midpoint(self, placement: TilePlacement,
                            local_x: float, local_y: float):
        east, north = placement.transform_xy(local_x, local_y)
        ix_float = east / 5.0
        iy_float = (20.0*self.original.tiles_h - north) / 5.0
        ix = round(ix_float)
        iy = round(iy_float)
        if abs(ix_float - ix) > 1e-5 or abs(iy_float - iy) > 1e-5:
            raise ValueError("CFL quarter border does not map to a 5m grid")
        if not (0 <= ix <= 4*self.original.tiles_w and
                0 <= iy <= 4*self.original.tiles_h):
            self.out_of_bounds += 1
            return
        if ix % 4 == 2 and iy % 4 == 0:
            self.horizontal.add((ix, iy))
        elif ix % 4 == 0 and iy % 4 == 2:
            self.vertical.add((ix, iy))
        else:
            raise ValueError("CFL border midpoint must straddle one 20m edge")

    def add_tile(self, placement: TilePlacement, width: int, depth: int,
                 bend_rules: str):
        """Register all 20m quarter borders restricted by this tile.

        Out-of-footprint V2/H2 tokens sometimes appear in stock 1x1
        definitions and are harmless. Mirror/rotation are applied to
        midpoints, not guessed from unrotated tile grid indices.
        """
        rules = set(bend_rules.upper().split())
        for x in range(width):
            if "V{}".format(x+1) in rules:
                local_x = -10.0*width + 20.0*x + 10.0
                for y in range(depth+1):
                    local_y = 10.0*depth - 20.0*y
                    self._add_world_midpoint(placement, local_x, local_y)
        for y in range(depth):
            if "H{}".format(y+1) in rules:
                local_y = 10.0*depth - 20.0*y - 10.0
                for x in range(width+1):
                    local_x = -10.0*width + 20.0*x
                    self._add_world_midpoint(placement, local_x, local_y)

    def adjusted_grid(self):
        """Return (TerrainGrid, diagnostics), preserving corner heights.

        Each 20m segment is interpolated independently. Horizontal
        segments only touch x%4 != 0 points along y%4 == 0 lines;
        vertical segments only touch y%4 != 0 points along x%4 == 0
        lines, so they cannot compete for a single interior vertex.
        """
        result = list(self.original.heights)
        nx = self.original.nx
        for x, y in self.horizontal:
            start = self.original.heights[y*nx + x-2]
            end = self.original.heights[y*nx + x+2]
            for offset in (-1, 0, 1):
                result[y*nx + x+offset] = (
                    start + (end-start)*(offset+2)/4.0)
        for x, y in self.vertical:
            start = self.original.heights[(y-2)*nx + x]
            end = self.original.heights[(y+2)*nx + x]
            for offset in (-1, 0, 1):
                result[(y+offset)*nx + x] = (
                    start + (end-start)*(offset+2)/4.0)
        changes = [abs(a-b) for a, b in zip(result, self.original.heights)
                   if abs(a-b) > 1e-5]
        return TerrainGrid(self.original.tiles_w, self.original.tiles_h,
                           tuple(result)), {
            "horizontal_border_segments": len(self.horizontal),
            "vertical_border_segments": len(self.vertical),
            "out_of_bounds_border_midpoints": self.out_of_bounds,
            "changed_height_samples": len(changes),
            "maximum_border_height_delta_m": max(changes) if changes else 0.0,
            "fidelity": ("Static shared-edge interpolation from FlyX "
                         "Border_vault; mutable tile-order and overlap "
                         "behavior not yet reproduced"),
        }
