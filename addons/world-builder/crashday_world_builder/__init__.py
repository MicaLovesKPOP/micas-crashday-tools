"""PRIVATE native Blender UI for authored checkpoints and offline World Builds.

Exact source models and route are preflighted before creating a NEW private
world folder. Not native game/collision qualified. No .cat files.
"""
bl_info = {
    "name": "Mica's Crashday World Builder",
    "author": "Mica's Crashday Tools",
    "version": (0, 2, 0),
    "blender": (2, 80, 0),
    "location": "3D View > Sidebar > Crashday > World Builder",
    "description": "Ordered checkpoint tiles and private offline Validate / Build",
    "category": "Import-Export",
}

from pathlib import Path
import math
import sys
import bpy
from bpy_extras import view3d_utils
from bpy.app.handlers import persistent
from mathutils import Vector
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
    IntProperty, PointerProperty, StringProperty,
)
from . import checkpoint_routes, route_ui_model, viewport_routes, build_ui_model

ROTATIONS = [
    (str(i), "Native turn {}".format(i),
     "Native TRK quarter-turn code {}; compass orientation not yet verified".format(i), i)
    for i in range(4)
]


class CRASHDAY_PG_route_tile(bpy.types.PropertyGroup):
    key: StringProperty(name="Source Tile")
    cfl: StringProperty(name="Native CFL")
    rotation: EnumProperty(name="Tile Rotation", items=ROTATIONS, default="0")
    box_left: FloatProperty(name="Left", default=-8.7, min=-10.0, max=10.0, precision=2)
    box_top: FloatProperty(name="Top", default=4.2, min=-10.0, max=10.0, precision=2)
    box_right: FloatProperty(name="Right", default=8.7, min=-10.0, max=10.0, precision=2)
    box_bottom: FloatProperty(name="Bottom", default=0.0, min=-10.0, max=10.0, precision=2)


class CRASHDAY_PG_route_settings(bpy.types.PropertyGroup):
    manifest_path: StringProperty(
        name="Exported Tile Manifest", subtype="FILE_PATH",
        description="Tile Splitter 'tiles_manifest.json' alongside its native P3Ds",
    )
    source_manifest_sha: StringProperty(name="Validated Manifest Hash")
    source_catalog_macro: BoolProperty(name="Validated macro mode", default=False,
                                      options={"HIDDEN"})
    macro_layout: BoolProperty(
        name="Oversized physical tiles (experimental)", default=False,
        description="Use already exported physical rectangles on the 20m grid; does not carve or modify geometry")
    macro_void_cfl: StringProperty(name="Private void CFL", subtype="FILE_PATH")
    macro_void_p3d: StringProperty(name="Private void P3D", subtype="FILE_PATH")
    source_tile_count: IntProperty(name="Exported Tiles", min=0, default=0)
    available_checkpoint_count: IntProperty(name="Eligible 1x1 Tiles", min=0, default=0)
    route_filename: StringProperty(
        name="Private Route Filename", default="checkpoint_route.json",
        description="Create a NEW JSON next to the audited source tile manifest",
    )
    checkpoints: CollectionProperty(type=CRASHDAY_PG_route_tile)
    active_index: IntProperty(name="Selected Checkpoint", default=0, min=0)
    show_advanced: BoolProperty(name="Show Native Checkpoint Box", default=False)
    show_route_markers: BoolProperty(name="Show Checkpoint Markers", default=True)
    marker_status: StringProperty(name="Marker Display Warning", default="",
                                  options={"HIDDEN"})
    last_status: StringProperty(name="Last Result", default="Not validated")
    private_build_directory: StringProperty(
        name="Private Output Folder", subtype="DIR_PATH",
        description="Existing private folder for NEW world builds; never select Steam/game folders",
    )
    private_build_label: StringProperty(
        name="Map Output Name", default="my_crashday_world",
        description="New folder name, never overwrites an existing build",
    )
    source_tga_manifest: StringProperty(
        name="Source TGA Manifest", subtype="FILE_PATH",
        description="Optional exact Tile Splitter source-image manifest",
    )
    native_tex_policy: StringProperty(
        name="TEX Preset Policy", subtype="FILE_PATH",
        description="Optional explicit source-bound TEX policy; never inferred",
    )
    native_preview_manifest: StringProperty(
        name="Native Tile Previews", subtype="FILE_PATH",
        description="Optional exact overhead rendered tile preview manifest",
    )
    show_build_advanced: BoolProperty(name="Advanced Build Inputs", default=False)
    checkpoint_mode: EnumProperty(
        name="Starting checkpoint",
        items=[
            ("AUTHORED", "Use authored route", "Use the ordered 20m checkpoint tiles"),
            ("NONE", "No checkpoints", "Build a map without a route; Test Drive needs a starting checkpoint"),
            ("STOCK", "Add stock Test Drive strip", "Append a 20m stock checkpoint column; no authored geometry or terrain is relocated"),
        ],
        default="AUTHORED")
    allow_wrecking: BoolProperty(name="Wrecking Match", default=True)
    allow_stunt: BoolProperty(name="Stunt Show", default=True)
    allow_bomb_run: BoolProperty(name="Bomb Run", default=True)
    allow_lap_race: BoolProperty(name="Race (Laps / Knockout)", default=True)
    allow_hold_flag: BoolProperty(name="Hold the Flag", default=True)
    allow_pass_bomb: BoolProperty(name="Pass the Bomb", default=True)
    allow_point_to_point: BoolProperty(name="Race (Point-to-Point)", default=True)
    game_mode_unknown_bit: BoolProperty(name="Preserved native high bit", default=True,
                                      options={"HIDDEN"})


def _resolve_manifest_path(context):
    settings = context.scene.crashday_world_route
    path = settings.manifest_path.strip()
    if not path and hasattr(context.scene, "crashday_tile_splitter"):
        splitter = context.scene.crashday_tile_splitter
        base = str(splitter.export_directory).strip()
        prefix = str(splitter.export_file_prefix)
        if base and prefix:
            path = str(Path(bpy.path.abspath(base)) / (prefix + "tiles_manifest.json"))
    if not path:
        raise route_ui_model.UIModelError(
            "Export Tile Splitter P3Ds with a manifest, or choose the manifest file")
    return bpy.path.abspath(path)


def _refresh_catalog(context):
    settings = context.scene.crashday_world_route
    path = _resolve_manifest_path(context)
    catalog = route_ui_model.load_catalog(path, macro_layout=settings.macro_layout)
    _reject_stale_parents(context, catalog)
    for cp in settings.checkpoints:
        source = catalog["entries"].get(cp.key)
        if source is None or source["footprint"] != [1, 1] or source["cfl"] != cp.cfl:
            raise route_ui_model.UIModelError(
                "Existing checkpoint '{}' does not match the chosen manifest".format(cp.key))
    settings.manifest_path = path
    settings.source_manifest_sha = catalog["sha256"]
    settings.source_catalog_macro = settings.macro_layout
    settings.source_tile_count = catalog["total"]
    settings.available_checkpoint_count = catalog["one_by_one"]
    return catalog


def _catalog_unchanged(context):
    settings = context.scene.crashday_world_route
    if not settings.source_manifest_sha:
        raise route_ui_model.UIModelError("Load and verify exported tiles first")
    if settings.source_catalog_macro != settings.macro_layout:
        raise route_ui_model.UIModelError("Tile layout mode changed; reload exported tiles")
    catalog = route_ui_model.load_catalog(_resolve_manifest_path(context),
                                          macro_layout=settings.macro_layout)
    _reject_stale_parents(context, catalog)
    if catalog["sha256"] != settings.source_manifest_sha:
        raise route_ui_model.UIModelError(
            "Exported tile manifest changed; refresh it before creating a route")
    return catalog


def _reject_stale_parents(context, catalog):
    _, parents = viewport_routes.scene_leaf_tiles(context.scene.objects)
    viewport_routes.reject_superseded_export(catalog, parents)


def _scene_generated_tiles(context):
    """Frozen Tile Splitter v0.4.6 marks every generated mesh with a tile key.

    Include all generated meshes in this Blender scene, not only selected
    objects. Bounds are the Splitter's logical metadata, NOT the P3D's larger
    physical main/collider extent. Older generated meshes may lack bounds.
    """
    fields = ("crashday_tile_min_x", "crashday_tile_max_x",
              "crashday_tile_min_y", "crashday_tile_max_y")
    leaves, _ = viewport_routes.scene_leaf_tiles(context.scene.objects)
    for obj in leaves:
        bounds = [obj.get(field) for field in fields]
        if any(value is None for value in bounds):
            bounds = None
        yield str(obj.get("crashday_tile_key")), bounds


def _report_error(op, exception):
    op.report({"ERROR"}, str(exception))
    return {"CANCELLED"}


def _append_checkpoint(context, source):
    """One mutation path shared by selected-tile and click-to-pick operators."""
    settings = context.scene.crashday_world_route
    entry = settings.checkpoints.add()
    entry.key = source["key"]
    entry.name = source["key"]
    entry.cfl = source["cfl"]
    entry.rotation = "0"
    settings.active_index = len(settings.checkpoints) - 1
    settings.last_status = "Checkpoint {} added".format(
        settings.active_index + 1)
    _auto_route_markers(context.scene)


def _status_text(context, value):
    workspace = getattr(context, "workspace", None)
    if workspace is not None and hasattr(workspace, "status_text_set"):
        workspace.status_text_set(value)


class CRASHDAY_OT_route_refresh(bpy.types.Operator):
    bl_idname = "crashday.route_refresh"
    bl_label = "Load Exported Tiles"
    bl_description = "Read and verify the native P3D export manifest without changing the scene"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            catalog = _refresh_catalog(context)
            settings = context.scene.crashday_world_route
            try:
                coverage = route_ui_model.audit_generated_scene_tiles(
                    _scene_generated_tiles(context), catalog)
            except route_ui_model.UIModelError as exc:
                settings.last_status = "Incomplete scene export: " + str(exc)[:100]
                self.report({"WARNING"}, str(exc))
                return {"FINISHED"}
            self.report({"INFO"}, "Verified {} exported tiles ({} are 1x1)".format(
                catalog["total"], catalog["one_by_one"]))
            settings.last_status = "OFFLINE manifest: {} tile(s), {} scene tile(s)".format(
                catalog["total"], coverage["scene_tile_count"])
            return {"FINISHED"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _report_error(self, exc)


class CRASHDAY_OT_route_add_selected(bpy.types.Operator):
    bl_idname = "crashday.route_add_selected"
    bl_label = "Add Selected 1x1 Tile"
    bl_description = "Use selected generated tile mesh; cannot overlap an existing 2x2 tile"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            settings = context.scene.crashday_world_route
            catalog = _catalog_unchanged(context)
            selected = [str(o.get("crashday_tile_key")) for o in context.selected_objects
                        if o.type == "MESH" and o.get("crashday_tile_key") is not None]
            src = route_ui_model.choose_selected_tile(
                selected, catalog, [item.cfl for item in settings.checkpoints])
            _append_checkpoint(context, src)
            return {"FINISHED"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _report_error(self, exc)


class CRASHDAY_OT_route_pick_viewport(bpy.types.Operator):
    bl_idname = "crashday.route_pick_viewport"
    bl_label = "Click 20m Checkpoint Tile"
    bl_description = ("Click a visible exported 1x1 generated tile in the 3D viewport; "
                      "Escape or right-click to cancel")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        try:
            catalog = _catalog_unchanged(context)
            if catalog["one_by_one"] == 0:
                raise route_ui_model.UIModelError(
                    "No eligible exported 20m tiles; export at least one 1x1 tile")
            if context.space_data.region_3d is None:
                raise route_ui_model.UIModelError("Open a 3D viewport to pick checkpoints")
            region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
            if region is None:
                raise route_ui_model.UIModelError("Could not find the 3D viewport window")
            # Do not cache source bytes or mutate Blender selection while picking.
            context.window_manager.modal_handler_add(self)
            _status_text(context, "Click an exported 20m tile. Esc/right-click cancels.")
            return {"RUNNING_MODAL"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _report_error(self, exc)

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            _status_text(context, None)
            return {"CANCELLED"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        if region is None:
            _status_text(context, None)
            return _report_error(self, ValueError("Viewport is no longer available"))
        mx, my = event.mouse_x - region.x, event.mouse_y - region.y
        if not (0 <= mx < region.width and 0 <= my < region.height):
            # Sidebar click must not accidentally pick a tile or activate another control.
            return {"RUNNING_MODAL"}
        try:
            rv3d = context.space_data.region_3d
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mx, my))
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (mx, my))
            hit, location, _normal, _face, _obj, _matrix = context.scene.ray_cast(
                (context.view_layer if bpy.app.version < (2, 91, 0) else
                 context.evaluated_depsgraph_get()), origin, direction)
            if hit:
                world_x, world_y = float(location.x), float(location.y)
            else:
                # An empty 20m cell has no selectable mesh. Project the
                # cursor ray to world Z=0 rather than guessing a tile ID.
                if abs(direction.z) < 1e-9:
                    raise viewport_routes.ViewportError(
                        "Look toward the map from above to pick an empty tile cell")
                distance = -origin.z / direction.z
                if distance <= 0:
                    raise viewport_routes.ViewportError(
                        "The map plane is behind the camera")
                world_x = float(origin.x + distance * direction.x)
                world_y = float(origin.y + distance * direction.y)
            settings = context.scene.crashday_world_route
            catalog = _catalog_unchanged(context)
            chosen = viewport_routes.pick_checkpoint_at_xy(
                world_x, world_y, catalog,
                [cp.cfl for cp in settings.checkpoints],
                route_ui_model.choose_selected_tile)
            _append_checkpoint(context, chosen)
            _status_text(context, None)
            self.report({"INFO"}, "Checkpoint {}: {}".format(
                settings.active_index + 1, chosen["key"]))
            context.area.tag_redraw()
            return {"FINISHED"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # An invalid click is recoverable; user can try another tile or Esc.
            self.report({"WARNING"}, str(exc))
            return {"RUNNING_MODAL"}


def _enabled_splitter(context):
    name = "crashday_tile_splitter"
    if name not in context.preferences.addons:
        raise ValueError("Enable the independently installed Tile Splitter add-on first")
    module = sys.modules.get(name)
    helpers = ("tile_key", "make_world_mesh", "bisect_keep_inside_xy",
               "move_mesh_origin_without_moving_world", "set_tile_metadata")
    if module is None or not all(callable(getattr(module, h, None)) for h in helpers):
        raise ValueError("The enabled Tile Splitter lacks the required carving API")
    return module


class CRASHDAY_OT_carve_checkpoint_viewport(bpy.types.Operator):
    bl_idname = "crashday.carve_checkpoint_viewport"
    bl_label = "Carve 20m Checkpoint Cell"
    bl_description = ("Click inside a larger active tile to create 20m checkpoint "
                      "regions and minimal remainders; retain the editable source")
    bl_options = {"REGISTER", "UNDO"}
    batch: BoolProperty(name="Multiple cells", default=False,
                        options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return (context.mode == "OBJECT" and context.area is not None and
                context.area.type == "VIEW_3D")

    def invoke(self, context, event):
        try:
            _enabled_splitter(context)
            if not viewport_routes.scene_leaf_tiles(context.scene.objects)[0]:
                raise ValueError("Generate tiles using Tile Splitter first")
            if context.space_data.region_3d is None:
                raise ValueError("Open a 3D viewport")
            context.window_manager.modal_handler_add(self)
            self._picks = []
            self._pick_signature = None
            _status_text(context, ("Click cells in one tile; click again to remove. "
                                   "Enter applies; Esc cancels.") if self.batch else
                         "Click inside a larger tile. Esc/right-click cancels.")
            return {"RUNNING_MODAL"}
        except (ValueError, RuntimeError) as exc:
            return _report_error(self, exc)

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            _status_text(context, None)
            return {"CANCELLED"}
        batch = getattr(self, "batch", False)
        apply_batch = batch and event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS"
        if not apply_batch and (event.type != "LEFTMOUSE" or event.value != "PRESS"):
            return {"PASS_THROUGH"}
        if context.area is None or context.area.type != "VIEW_3D":
            _status_text(context, None)
            return {"CANCELLED"}
        if apply_batch:
            try:
                if not self._picks:
                    raise ValueError("Choose at least one cell before applying")
                key = self._picks[0][0]
                settings = context.scene.crashday_world_route
                if any(cp.key == key for cp in settings.checkpoints):
                    raise ValueError("Remove this tile from the route before carving")
                from .offline_backend.blender_checkpoint_carve import (
                    carve_scene_checkpoint_batch, require_scene_checkpoint_pick_signature)
                require_scene_checkpoint_pick_signature(
                    context.scene.objects, key, self._pick_signature)
                result = carve_scene_checkpoint_batch(
                    context, _enabled_splitter(context), key,
                    [cell for _, cell in self._picks])
            except (ValueError, RuntimeError, ImportError, TypeError, KeyError) as exc:
                self.report({"WARNING"}, str(exc))
                return {"RUNNING_MODAL"}
            return CRASHDAY_OT_carve_checkpoint_viewport.finish_carve(self, context, result)
        region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        if region is None:
            _status_text(context, None)
            return {"CANCELLED"}
        mx, my = event.mouse_x - region.x, event.mouse_y - region.y
        if not (0 <= mx < region.width and 0 <= my < region.height):
            return {"RUNNING_MODAL"}
        try:
            rv3d = context.space_data.region_3d
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mx, my))
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (mx, my))
            hit, location, _normal, _face, _obj, _matrix = context.scene.ray_cast(
                (context.view_layer if bpy.app.version < (2, 91, 0) else
                 context.evaluated_depsgraph_get()), origin, direction)
            if not hit:
                raise ValueError("Click visible source geometry inside a larger tile")
            xy = (float(location.x), float(location.y))
            key = viewport_routes.pick_scene_leaf_at_xy(context.scene.objects, *xy)
            settings = context.scene.crashday_world_route
            if any(cp.key == key for cp in settings.checkpoints):
                raise ValueError("Remove this tile from the route before carving")
            if batch:
                from .offline_backend.blender_checkpoint_carve import (
                    toggle_scene_checkpoint_pick, scene_checkpoint_pick_signature,
                    require_scene_checkpoint_pick_signature)
                if self._picks:
                    require_scene_checkpoint_pick_signature(
                        context.scene.objects, self._picks[0][0], self._pick_signature)
                picks = toggle_scene_checkpoint_pick(
                    context.scene.objects, key, xy, self._picks)
                self._pick_signature = (scene_checkpoint_pick_signature(
                    context.scene.objects, key) if picks else None)
                self._picks = picks
                _status_text(context, "{} cells selected: {}. Enter applies; Esc cancels.".format(
                    len(self._picks), ", ".join(str(c) for _, c in self._picks[:8])))
                return {"RUNNING_MODAL"}
            from .offline_backend.blender_checkpoint_carve import carve_scene_checkpoint
            result = carve_scene_checkpoint(context, _enabled_splitter(context), key, xy)
        except (ValueError, RuntimeError, ImportError, TypeError, KeyError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"RUNNING_MODAL"}
        return CRASHDAY_OT_carve_checkpoint_viewport.finish_carve(self, context, result)

    def finish_carve(self, context, result):
        settings = context.scene.crashday_world_route
        settings.source_manifest_sha = ""
        settings.source_tile_count = 0
        settings.available_checkpoint_count = 0
        settings.macro_layout = True
        settings.last_status = "Carved {} pieces; re-export active tiles".format(
            len(result["tile_keys"]))
        _status_text(context, None)
        self.report({"INFO"}, settings.last_status)
        context.area.tag_redraw()
        return {"FINISHED"}


class CRASHDAY_OT_select_active_export_tiles(bpy.types.Operator):
    bl_idname = "crashday.select_active_export_tiles"
    bl_label = "Select Active Tiles for Export"
    bl_description = ("Select every active generated mesh, not retained carve parents; "
                      "enable selected-only export in Tile Splitter")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            _enabled_splitter(context)
            if context.mode != "OBJECT":
                raise ValueError("Use Object Mode to select export tiles")
            leaves, _ = viewport_routes.scene_leaf_tiles(context.scene.objects)
            if not leaves:
                raise ValueError("No active generated tiles")
            for obj in leaves:
                if (context.view_layer.objects.get(obj.name) != obj or
                        not obj.visible_get() or obj.hide_select):
                    raise ValueError("Unhide and unlock active tile before export: " + obj.name)
            splitter = context.scene.crashday_tile_splitter
            old_selection = list(context.selected_objects)
            old_active = context.view_layer.objects.active
            old_scope = splitter.export_selected_only
            try:
                for obj in old_selection:
                    obj.select_set(False)
                for obj in leaves:
                    obj.select_set(True)
                if {obj.as_pointer() for obj in context.selected_objects} != {
                        obj.as_pointer() for obj in leaves}:
                    raise ValueError("Could not select all active tile meshes")
                context.view_layer.objects.active = leaves[0]
                splitter.export_selected_only = True
            except Exception:
                for obj in list(context.selected_objects):
                    obj.select_set(False)
                for obj in old_selection:
                    obj.select_set(True)
                context.view_layer.objects.active = old_active
                splitter.export_selected_only = old_scope
                raise
            self.report({"INFO"}, "Selected {} active meshes; export using Tile Splitter".format(
                len(leaves)))
            return {"FINISHED"}
        except (ValueError, RuntimeError, AttributeError) as exc:
            return _report_error(self, exc)


class CRASHDAY_OT_route_remove(bpy.types.Operator):
    bl_idname = "crashday.route_remove"
    bl_label = "Remove Checkpoint"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.crashday_world_route
        if not 0 <= settings.active_index < len(settings.checkpoints):
            return _report_error(self, ValueError("Select a checkpoint first"))
        settings.checkpoints.remove(settings.active_index)
        settings.active_index = min(settings.active_index, max(0, len(settings.checkpoints)-1))
        settings.last_status = "Checkpoint removed"
        _auto_route_markers(context.scene)
        return {"FINISHED"}


class CRASHDAY_OT_route_move(bpy.types.Operator):
    bl_idname = "crashday.route_move"
    bl_label = "Reorder Checkpoint"
    bl_options = {"REGISTER", "UNDO"}
    direction: EnumProperty(items=[("UP", "Earlier", ""), ("DOWN", "Later", "")])

    def execute(self, context):
        settings = context.scene.crashday_world_route
        src = settings.active_index
        dst = src + (-1 if self.direction == "UP" else 1)
        if not (0 <= src < len(settings.checkpoints) and
                0 <= dst < len(settings.checkpoints)):
            return {"CANCELLED"}
        settings.checkpoints.move(src, dst)
        settings.active_index = dst
        _auto_route_markers(context.scene)
        return {"FINISHED"}


class CRASHDAY_OT_route_select(bpy.types.Operator):
    bl_idname = "crashday.route_select"
    bl_label = "Select Checkpoint Tile"
    bl_description = "Select the Blender objects associated with this checkpoint"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.crashday_world_route
        if not 0 <= settings.active_index < len(settings.checkpoints):
            return _report_error(self, ValueError("Select a checkpoint first"))
        key = settings.checkpoints[settings.active_index].key
        found = [obj for obj in context.scene.objects
                 if obj.type == "MESH" and obj.get("crashday_tile_key") == key]
        if not found:
            return _report_error(self, ValueError("No generated Blender mesh has this tile key"))
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in found:
            obj.select_set(True)
        context.view_layer.objects.active = found[0]
        return {"FINISHED"}



ROUTE_MARKER_COLLECTION = "Mica Crashday Route Markers"
ROUTE_MARKER_TAG = "micas_crashday_route_marker_v1"


def _owned_marker_collection(scene, create=False):
    # Scope derived markers to this scene, never a similarly named user
    # collection or an owned collection shared with another scene.
    candidates = [c for c in scene.collection.children
                  if bool(c.get(ROUTE_MARKER_TAG))]
    if len(candidates) > 1:
        raise ValueError("Multiple owned route marker collections in this scene")
    coll = candidates[0] if candidates else None
    if coll is None:
        if scene.collection.children.get(ROUTE_MARKER_COLLECTION) is not None:
            raise ValueError("Route marker collection name belongs to the user")
        if not create:
            return None
        coll = bpy.data.collections.new(ROUTE_MARKER_COLLECTION)
        coll[ROUTE_MARKER_TAG] = True
        scene.collection.children.link(coll)
    if coll.users != 1 or len(coll.children):
        raise ValueError("Route marker collection is shared or contains child collections")
    for obj in coll.objects:
        if (not bool(obj.get(ROUTE_MARKER_TAG)) or
                len(obj.users_collection) != 1):
            raise ValueError("Route marker collection contains non-owned or linked objects")
    return coll


def _clear_route_markers(scene):
    coll = _owned_marker_collection(scene)
    if coll is None:
        return 0
    owned = list(coll.objects)
    for obj in owned:
        bpy.data.objects.remove(obj, do_unlink=True)
    return len(owned)


def _scene_route_marker_specs(scene):
    """Visual preview from saved logical scene metadata, not export validation.

    Avoid disk rehashing on each turn/reorder. The actual Build/Validate
    operators still verify the catalogue, source hashes and live geometry.
    """
    settings = scene.crashday_world_route
    route = [{"key": cp.key, "cfl": cp.cfl, "rotation": int(cp.rotation)}
             for cp in settings.checkpoints]
    fields = ("crashday_tile_min_x", "crashday_tile_max_x",
              "crashday_tile_min_y", "crashday_tile_max_y")
    groups = {}
    wanted = {item["key"] for item in route}
    leaves, _parents = viewport_routes.scene_leaf_tiles(scene.objects)
    for obj in leaves:
        key = obj.get("crashday_tile_key")
        if key in wanted:
            groups.setdefault(key, []).append(obj)
    records, elevations = {}, {}
    for item in route:
        key = item["key"]
        objects = groups.get(key, [])
        if not objects:
            raise ValueError("No Blender source mesh for checkpoint " + key)
        bounds = None
        for obj in objects:
            values = [obj.get(f) for f in fields]
            if any(v is None for v in values):
                raise ValueError("Missing logical checkpoint bounds for " + key)
            current = tuple(float(v) for v in values)
            if not all(math.isfinite(v) for v in current):
                raise ValueError("Invalid logical checkpoint bounds for " + key)
            if (abs(current[1]-current[0]-20) > 1e-5 or
                    abs(current[3]-current[2]-20) > 1e-5):
                raise ValueError("Checkpoint is no longer a 20m tile: " + key)
            if bounds is not None and any(abs(a-b) > 1e-5 for a,b in zip(bounds,current)):
                raise ValueError("Conflicting scene bounds for checkpoint " + key)
            bounds = current
        records[key] = {"footprint": [1, 1], "cfl": item["cfl"], "bounds": bounds}
        elevations[key] = max((obj.matrix_world @ Vector(corner)).z
                              for obj in objects for corner in obj.bound_box)
        if not math.isfinite(elevations[key]):
            raise ValueError("Invalid checkpoint elevation: " + key)
    return viewport_routes.route_marker_specs(route, {"entries": records}), elevations


def _sync_route_markers(scene):
    settings = scene.crashday_world_route
    if not settings.show_route_markers or not len(settings.checkpoints):
        _clear_route_markers(scene)
        return 0
    specs, elevations = _scene_route_marker_specs(scene)
    coll = _owned_marker_collection(scene, create=True)
    previous = list(coll.objects)
    created = []
    try:
        # Stage complete new set before deleting old owned objects. Rename
        # after removal so normal refresh does not append Blender .001 suffixes.
        for spec in specs:
            obj = bpy.data.objects.new("__MicaRoutePending", None)
            created.append(obj)
            coll.objects.link(obj)
            obj[ROUTE_MARKER_TAG] = True
            obj["crashday_checkpoint_tile_key"] = spec["key"]
            obj["crashday_native_rotation_unverified_heading"] = spec["turn"]
            obj.empty_display_type = "SINGLE_ARROW"
            obj.empty_display_size = 6.0
            obj.location = (spec["center"][0], spec["center"][1],
                            elevations[spec["key"]] + 1.0)
            obj.rotation_euler = (-math.pi / 2, 0.0,
                                  viewport_routes.provisional_marker_yaw(spec["turn"]))
            obj.show_name = True
            obj.show_in_front = True
            obj.hide_render = True
            obj.hide_select = True
        for obj in previous:
            bpy.data.objects.remove(obj, do_unlink=True)
        for obj, spec in zip(created, specs):
            obj.name = spec["label"]
        return len(created)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        for obj in reversed(created):
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        raise


def _auto_route_markers(scene):
    """Derived display failure never invalidates an already valid route edit."""
    settings = scene.crashday_world_route
    try:
        count = _sync_route_markers(scene)
        settings.marker_status = ""
        return count
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        settings.marker_status = str(exc)
        # Do not show obsolete arrows on a failed edit; if ownership is
        # ambiguous, leave foreign data untouched and report the error.
        try:
            _clear_route_markers(scene)
        except (ValueError, RuntimeError):
            pass
        return None


@persistent
def _restore_route_markers(_dummy):
    # Old saved scenes may contain stale/manual markers. Sync only scenes
    # that already have a route or our owned marker collection.
    for scene in bpy.data.scenes:
        settings = scene.crashday_world_route
        if len(settings.checkpoints) or any(bool(c.get(ROUTE_MARKER_TAG))
                                            for c in scene.collection.children):
            _auto_route_markers(scene)


class CRASHDAY_OT_route_rotate(bpy.types.Operator):
    bl_idname = "crashday.route_rotate"
    bl_label = "Checkpoint Rotation"
    bl_description = "Choose a native quarter-turn and update the authoring arrow"
    bl_options = {"REGISTER", "UNDO"}
    rotation: EnumProperty(name="Tile Rotation", items=ROTATIONS, default="0")

    def execute(self, context):
        settings = context.scene.crashday_world_route
        if not 0 <= settings.active_index < len(settings.checkpoints):
            return _report_error(self, ValueError("Select a checkpoint first"))
        settings.checkpoints[settings.active_index].rotation = self.rotation
        _auto_route_markers(context.scene)
        return {"FINISHED"}


class CRASHDAY_OT_route_update_markers(bpy.types.Operator):
    # Retain legacy operator ID for scripts/F3; no normal refresh step.
    bl_idname = "crashday.route_update_markers"
    bl_label = "Show Route Markers"
    bl_description = "Show automatically updated Blender-only checkpoint arrows"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.crashday_world_route
        settings.show_route_markers = True
        _auto_route_markers(context.scene)
        return {"FINISHED"}


class CRASHDAY_OT_route_hide_markers(bpy.types.Operator):
    bl_idname = "crashday.route_hide_markers"
    bl_label = "Hide Route Markers"
    bl_description = "Hide generated checkpoint arrows; keep the authored route"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.crashday_world_route
        settings.show_route_markers = False
        _auto_route_markers(context.scene)
        return {"FINISHED"}


class CRASHDAY_OT_route_export(bpy.types.Operator):
    bl_idname = "crashday.route_export"
    bl_label = "Export Private Checkpoint Route"
    bl_description = ("Write a NEW validated route JSON beside the exported models; "
                      "does not change original game or build a TRK")
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.crashday_world_route
        try:
            catalog = _catalog_unchanged(context)
            records = [{
                "key": cp.key, "cfl": cp.cfl, "rotation": int(cp.rotation),
                "box": [cp.box_left, cp.box_top, cp.box_right, cp.box_bottom],
            } for cp in settings.checkpoints]
            payload = route_ui_model.validated_payload(
                records, catalog, checkpoint_routes.validated_route)
            name = route_ui_model.safe_route_filename(settings.route_filename.strip())
            output = Path(catalog["path"]).parent / name
            target, digest = route_ui_model.export_private_route(output, payload)
            settings.last_status = "Route: {} checkpoint(s), SHA-256 {}".format(
                len(records), digest[:12])
            self.report({"INFO"}, "Saved {} checkpoint(s) to {}".format(
                len(records), target))
            return {"FINISHED"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _report_error(self, exc)


class CRASHDAY_OT_macro_previews(bpy.types.Operator):
    bl_idname = "crashday.macro_previews"
    bl_label = "Generate Native Anchor Previews"
    bl_description = ("Render centered native CFL crops of active physical tiles; "
                      "create-only output beside matching P3Ds, no geometry changes")
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.crashday_world_route
        try:
            if not settings.macro_layout:
                raise ValueError("Enable oversized physical tile mode first")
            catalog = _catalog_unchanged(context)
            from . import macro_preview
            from crashday_tile_splitter import preview_render, p3d_export
            with build_ui_model.bundled_backend(
                    Path(__file__).resolve().parent / "offline_backend") as backend:
                result = macro_preview.generate(
                    context, catalog["path"], backend, preview_render, p3d_export,
                    expected_manifest_sha=catalog["sha256"])
            settings.native_preview_manifest = result["manifest"]
            settings.last_status = "Rendered {} native anchor previews".format(result["tiles"])
            self.report({"INFO"}, settings.last_status)
            return {"FINISHED"}
        except Exception as exc:
            return _report_error(self, exc)


class CRASHDAY_OT_world_validate_build(bpy.types.Operator):
    bl_idname = "crashday.world_validate_build"
    bl_label = "Validate / Build Private Crashday Map"
    bl_description = ("Validate a complete authored checkpoint route, then optionally "
                      "create a NEW private native world; original game never modified")
    bl_options = {"REGISTER"}
    write: BoolProperty(name="Build Map", default=False)

    def execute(self, context):
        settings = context.scene.crashday_world_route
        try:
            catalog = _catalog_unchanged(context)
            # The frozen Tile Splitter defaults to "Export Selected Tiles
            # Only". Reject silent 1-of-4 worlds when its manifest omits
            # other generated tiles in the CURRENT scene.
            route_ui_model.audit_generated_scene_tiles(
                _scene_generated_tiles(context), catalog)
            leaves, _ = viewport_routes.scene_leaf_tiles(context.scene.objects)
            if leaves:
                if context.mode != "OBJECT":
                    raise ValueError("Switch to Object Mode before validating generated tiles")
                try:
                    from crashday_tile_splitter import p3d_export
                except ImportError as exc:
                    raise ValueError("Enable Tile Splitter to verify live generated geometry") from exc
                groups = {}
                for obj in leaves:
                    groups.setdefault(str(obj["crashday_tile_key"]), []).append(obj)
                context.view_layer.update()
                route_ui_model.audit_live_scene_models(
                    groups, catalog, p3d_export.build_tile_p3d)
            records = [{
                "key": cp.key, "cfl": cp.cfl, "rotation": int(cp.rotation),
                "box": [cp.box_left, cp.box_top, cp.box_right, cp.box_bottom],
            } for cp in settings.checkpoints]
            mode = settings.checkpoint_mode
            if mode == "AUTHORED":
                payload = route_ui_model.validated_payload(
                    records, catalog, checkpoint_routes.validated_route)
            else:
                if records:
                    raise build_ui_model.BuildUIError(
                        "Existing authored checkpoint route cannot be discarded; use authored route")
                payload = None
            chosen_root = settings.private_build_directory.strip()
            if not chosen_root:
                raise build_ui_model.BuildUIError(
                    "Choose an existing PRIVATE output folder first")
            kwargs = dict(
                game_mode_mask=build_ui_model.game_mode_mask(settings),
                checkpoint_mode=mode,
                macro_layout=settings.macro_layout,
                void_cfl=(bpy.path.abspath(settings.macro_void_cfl)
                          if settings.macro_layout and settings.macro_void_cfl.strip() else None),
                void_p3d=(bpy.path.abspath(settings.macro_void_p3d)
                          if settings.macro_layout and settings.macro_void_p3d.strip() else None),
                texture_manifest=(bpy.path.abspath(settings.source_tga_manifest)
                                  if settings.source_tga_manifest.strip() else None),
                tex_policy=(bpy.path.abspath(settings.native_tex_policy)
                            if settings.native_tex_policy.strip() else None),
                tile_preview_manifest=(bpy.path.abspath(settings.native_preview_manifest)
                                       if settings.native_preview_manifest.strip() else None),
            )
            with build_ui_model.bundled_backend(
                    Path(__file__).resolve().parent / "offline_backend") as backend:
                result = build_ui_model.private_validate_or_build(
                    catalog["path"], payload, bpy.path.abspath(chosen_root),
                    settings.private_build_label.strip(), backend,
                    write=self.write,
                    expected_source_manifest_sha256=catalog["sha256"], **kwargs)
            preview = result["preview"]
            if self.write:
                settings.last_status = "PRIVATE build: {} tile(s), {} CPs".format(
                    preview["source_tiles"], preview["checkpoint_count"])
                self.report({"INFO"}, "Private map built at " + result["private_directory"])
            else:
                settings.last_status = "OFFLINE valid: {} tiles, {} CPs".format(
                    preview["source_tiles"], preview["checkpoint_count"])
                self.report({"INFO"}, "Offline source validation passed; game physics unverified")
            return {"FINISHED"}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as exc:
            settings.last_status = "Offline build refused: " + str(exc)[:120]
            return _report_error(self, exc)


class CRASHDAY_UL_route_entries(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index=0):
        if self.layout_type == "GRID":
            layout.label(text="", icon="OUTLINER_DATA_EMPTY")
            return
        row = layout.row(align=True)
        row.label(text="START" if index == 0 else "CP {}".format(index + 1))
        row.label(text=item.key)
        row.label(text="Turn {}".format(item.rotation))


# BEGIN shared native sidebar sizing
class _SidebarLayout:
    """Estimate native UI text demand; use ordinary Blender layout controls.

    Blender exposes region width, not per-widget clipping. Keep complete
    label/value pairs together when their estimated minima fit, otherwise
    give each a full row. Measurements are conservative, not pixel promises.
    """
    def __init__(self, context, measure=None):
        prefs = context.preferences
        self.scale = float(getattr(prefs.system, "ui_scale", 0) or
                           getattr(prefs.view, "ui_scale", 1) or 1)
        self.scale = max(0.5, self.scale)
        self.width = max(1, float(context.region.width) - 36*self.scale)
        if measure is None:
            import blf
            try:
                points = prefs.ui_styles[0].widget.points
            except (AttributeError, IndexError):
                points = 11
            size = max(1, int(round(points*self.scale)))
            try:
                blf.size(0, size, 72)
            except TypeError:
                blf.size(0, size)
            measure = lambda text: blf.dimensions(0, text)[0]
        self.measure = measure

    def demand(self, text, chrome=12):
        return max(1, self.measure(str(text)) + chrome*self.scale)

    def _prop_parts(self, data, name, label=None):
        prop = data.bl_rna.properties[name]
        label = prop.name if label is None else label
        value = getattr(data, name)
        if prop.type == "ENUM":
            item = prop.enum_items.get(value)
            value = item.name if item else value
        elif prop.type == "FLOAT":
            value = ("{:." + str(prop.precision) + "f}").format(value)
        # Native arrows, picker, padding and possible unit suffix.
        chrome = 44 if prop.type in {"FLOAT", "INT", "ENUM"} else 30
        return label, self.demand(label), max(56*self.scale, self.demand(value, chrome))

    def prop(self, layout, data, name, label=None, width=None):
        width = self.width if width is None else max(1, width)
        label, left, right = self._prop_parts(data, name, label)
        if left + right + 4*self.scale <= width:
            # Reserve the value's own readable minimum; don't waste its
            # surplus while the label is clipped. Once both fit, split surplus.
            factor = (left + (width-left-right)*0.5)/width
            row = layout.split(factor=max(.01, min(.99, factor)), align=True)
            row.label(text=label)
            row.prop(data, name, text="")
        else:
            col = layout.column(align=True)
            self.label(col, label, width=width)
            col.prop(data, name, text="")

    def pair(self, layout, data, first, second, width=None):
        width = self.width if width is None else max(1, width)
        a = sum(self._prop_parts(data, first)[1:]) + 4*self.scale
        b = sum(self._prop_parts(data, second)[1:]) + 4*self.scale
        gap = 8*self.scale
        if a+b+gap <= width:
            left = a + (width-a-b-gap)*0.5
            row = layout.split(factor=left/(width-gap), align=True)
            self.prop(row.column(align=True), data, first, width=left)
            self.prop(row.column(align=True), data, second, width=width-gap-left)
        else:
            self.prop(layout, data, first, width=width)
            self.prop(layout, data, second, width=width)

    def label(self, layout, text, icon="NONE", width=None):
        width = self.width if width is None else max(1, width)
        # Wrap informational text instead of silently dropping its ending.
        # Preserve long indivisible tokens as character chunks.
        budget = max(1, width - (24 if icon != "NONE" else 8)*self.scale)
        lines, current = [], ""
        for word in str(text).split():
            joined = current + " " + word if current else word
            if self.measure(joined) <= budget:
                current = joined
                continue
            if current:
                lines.append(current)
                current = ""
            for char in word:
                if current and self.measure(current + char) > budget:
                    lines.append(current)
                    current = ""
                current += char
        if current or not lines:
            lines.append(current)
        for i, line in enumerate(lines):
            layout.label(text=line, icon=icon if i == 0 else "NONE")

    def action_pair(self, layout, first, second, width=None):
        """Return two layout slots sized for their actual button captions."""
        width = self.width if width is None else max(1, width)
        a, b = self.demand(first, 34), self.demand(second, 34)
        if a+b+8*self.scale > width:
            col = layout.column(align=True)
            return col, col
        factor = (a + (width-a-b)*0.5)/width
        row = layout.split(factor=max(.01,min(.99,factor)), align=True)
        return row.column(align=True), row.column(align=True)
# END shared native sidebar sizing


class VIEW3D_PT_crashday_world_route(bpy.types.Panel):
    bl_label = "World Builder - Checkpoints"
    bl_idname = "VIEW3D_PT_crashday_world_route"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crashday"
    bl_order = 20  # after independently installed Tile Splitter (default 0)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.crashday_world_route
        ui = _SidebarLayout(context)
        col = layout.column(align=True)
        ui.label(col, "1. Prepare / load exported tiles")
        col.operator("crashday.carve_checkpoint_viewport", icon="MOD_BOOLEAN").batch = False
        col.operator("crashday.carve_checkpoint_viewport", text="Carve Multiple Cells in Tile",
                     icon="MOD_BOOLEAN").batch = True
        col.operator("crashday.select_active_export_tiles", icon="RESTRICT_SELECT_OFF")
        ui.label(col, "After carving, export selected tiles to a new folder.")
        ui.prop(col, settings, "manifest_path", label="Tile manifest")
        col.prop(settings, "macro_layout")
        if settings.macro_layout:
            ui.label(col, "Includes carved physical regions.", icon="INFO")
        col.operator("crashday.route_refresh", icon="FILE_REFRESH")
        if settings.source_manifest_sha:
            ui.label(col, "{} tiles, {} checkpoint eligible".format(
                settings.source_tile_count, settings.available_checkpoint_count),
                icon="CHECKMARK")
        layout.separator()
        col = layout.column(align=True)
        ui.label(col, "2. Choose 20m checkpoint tiles")
        col.operator("crashday.route_pick_viewport", icon="RESTRICT_SELECT_OFF")
        col.operator("crashday.route_add_selected", icon="ADD")
        ui.label(col, "Only exported 1x1 / 20m tiles are valid.")
        row = col.row(align=True)
        row.template_list("CRASHDAY_UL_route_entries", "route", settings,
                          "checkpoints", settings, "active_index", rows=4)
        buttons = row.column(align=True)
        buttons.operator("crashday.route_remove", icon="REMOVE", text="")
        buttons.separator()
        buttons.operator("crashday.route_move", icon="TRIA_UP", text="").direction = "UP"
        buttons.operator("crashday.route_move", icon="TRIA_DOWN", text="").direction = "DOWN"
        if 0 <= settings.active_index < len(settings.checkpoints):
            cp = settings.checkpoints[settings.active_index]
            ui.label(col, "{}: {}".format(
                "Starting checkpoint" if settings.active_index == 0
                else "Checkpoint {}".format(settings.active_index + 1), cp.key))
            col.operator_menu_enum("crashday.route_rotate", "rotation",
                                   text="Native turn " + cp.rotation)
            col.operator("crashday.route_select", icon="RESTRICT_SELECT_OFF")
            col.prop(settings, "show_advanced", text="Native checkpoint region")
            if settings.show_advanced:
                box = col.box()
                ui.label(box, "Native 20m box: left / top / right / bottom")
                inner = max(1, ui.width - 20*ui.scale)
                ui.pair(box, cp, "box_left", "box_top", width=inner)
                ui.pair(box, cp, "box_right", "box_bottom", width=inner)
        layout.separator()
        col = layout.column(align=True)
        ui.label(col, "Route preview (Blender only)")
        if settings.show_route_markers:
            col.operator("crashday.route_hide_markers", icon="HIDE_ON")
        else:
            col.operator("crashday.route_update_markers", icon="HIDE_OFF")
        ui.label(col, "Relative turns; compass unverified.")
        if settings.marker_status:
            ui.label(col, "Marker warning: " + settings.marker_status, icon="ERROR")
        layout.separator()
        col = layout.column(align=True)
        ui.label(col, "3. Save private route")
        ui.prop(col, settings, "route_filename", label="Filename")
        col.operator("crashday.route_export", icon="EXPORT")
        ui.label(col, "Output stays beside private P3Ds.")
        layout.separator()
        box = layout.box()
        col = box.column(align=True)
        ui.label(col, "4. Allowed game modes")
        for name, label, _bit in build_ui_model.GAME_MODES:
            col.prop(settings, name, text=label)
        if not build_ui_model.game_mode_mask(settings) & 0x7f:
            ui.label(col, "No known game modes enabled", icon="ERROR")
        ui.label(col, "Permissions only; gameplay untested.", icon="INFO")
        box = layout.box()
        col = box.column(align=True)
        ui.label(col, "5. Build Crashday Map (offline)")
        if settings.source_manifest_sha:
            ui.label(col, "Scope: ALL {} exported tiles".format(
                settings.source_tile_count))
        ui.prop(col, settings, "checkpoint_mode", label="Start", width=max(1, ui.width-20*ui.scale))
        if settings.checkpoint_mode == "STOCK":
            ui.label(col, "Adds a stock 20m ground strip.", icon="INFO")
        elif settings.checkpoint_mode == "NONE":
            ui.label(col, "Test Drive needs a checkpoint.", icon="INFO")
        if settings.checkpoint_mode != "AUTHORED" and len(settings.checkpoints):
            ui.label(col, "Existing route conflicts with mode.", icon="ERROR")
        if settings.macro_layout:
            ui.prop(col, settings, "macro_void_cfl", label="Void CFL", width=max(1, ui.width-20*ui.scale))
            ui.prop(col, settings, "macro_void_p3d", label="Void P3D", width=max(1, ui.width-20*ui.scale))
            ui.label(col, "Explicit private assets; collision untested.", icon="INFO")
        ui.prop(col, settings, "private_build_directory", label="Private folder", width=max(1, ui.width-20*ui.scale))
        ui.prop(col, settings, "private_build_label", label="New map name", width=max(1, ui.width-20*ui.scale))
        validate, build = ui.action_pair(col, "Validate Map", "Build Map",
                                         width=max(1, ui.width-20*ui.scale))
        validate.operator("crashday.world_validate_build",
                          text="Validate Map", icon="CHECKMARK").write = False
        build.operator("crashday.world_validate_build",
                       text="Build Map", icon="EXPORT").write = True
        ui.label(col, "Always creates a NEW private folder.")
        ui.label(col, "No game installation or .cat writes.")
        col.prop(settings, "show_build_advanced", text="Advanced source assets")
        if settings.show_build_advanced:
            ui.prop(col, settings, "source_tga_manifest", label="Source TGAs", width=max(1, ui.width-20*ui.scale))
            ui.prop(col, settings, "native_tex_policy", label="Explicit TEX", width=max(1, ui.width-20*ui.scale))
            ui.prop(col, settings, "native_preview_manifest", label="Overhead previews", width=max(1, ui.width-20*ui.scale))
            if settings.macro_layout:
                col.operator("crashday.macro_previews", icon="RENDER_STILL")
                ui.label(col, "Experimental: native crop QA pending.", icon="INFO")
        if settings.last_status:
            ui.label(layout, settings.last_status, icon="INFO")
        layout.separator()
        ui.label(layout, "Game-mode encoding verified offline.", icon="INFO")
        ui.label(layout, "No CAT files, native Autolist=0.")
        ui.label(layout, "Native directions are not yet mapped.")


CLASSES = (
    CRASHDAY_PG_route_tile,
    CRASHDAY_PG_route_settings,
    CRASHDAY_OT_route_refresh,
    CRASHDAY_OT_route_add_selected,
    CRASHDAY_OT_route_pick_viewport,
    CRASHDAY_OT_carve_checkpoint_viewport,
    CRASHDAY_OT_select_active_export_tiles,
    CRASHDAY_OT_route_remove,
    CRASHDAY_OT_route_move,
    CRASHDAY_OT_route_select,
    CRASHDAY_OT_route_rotate,
    CRASHDAY_OT_route_update_markers,
    CRASHDAY_OT_route_hide_markers,
    CRASHDAY_OT_route_export,
    CRASHDAY_OT_macro_previews,
    CRASHDAY_OT_world_validate_build,
    CRASHDAY_UL_route_entries,
    VIEW3D_PT_crashday_world_route,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.crashday_world_route = PointerProperty(type=CRASHDAY_PG_route_settings)
    if _restore_route_markers not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_route_markers)


def unregister():
    if _restore_route_markers in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_route_markers)
    if hasattr(bpy.types.Scene, "crashday_world_route"):
        del bpy.types.Scene.crashday_world_route
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
