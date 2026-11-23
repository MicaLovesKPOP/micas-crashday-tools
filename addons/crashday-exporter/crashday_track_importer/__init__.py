"""Blender File > Import UI for Crashday Redline Edition tracks.

This package is built from shared, read-only scene/geometry parsers by
tools/build_addon.py. It never modifies the original game or tracks.
"""
bl_info = {
    "name": "Crashday Track (.trk) — World Assets",
    "author": "MicaLovesKPOP",
    "version": (0, 1, 0),
    "blender": (2, 80, 0),  # Headless operator/import/save/reopen verified on 2.80, 2.83, 3.4 and 4.5.
    "location": "File > Import > Crashday Track (.trk)",
    "description": "Bring original Crashday track geometry, heightmap, P3D models and textures into Blender",
    "category": "Import-Export",
}

from pathlib import Path
import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from .blender_scene import import_scene


def find_game_root(track_path, explicit_path):
    """Detect the actual game directory without requiring a hard-coded drive."""
    if explicit_path:
        root = Path(bpy.path.abspath(explicit_path))
        if (root / "data").is_dir():
            return root
        raise ValueError("Crashday game directory lacks data folder: {}".format(root))
    for parent in (track_path.parent,) + tuple(track_path.parents):
        if (parent / "data").is_dir() and (parent / "user").is_dir():
            return parent
    raise ValueError("Choose the Crashday game folder (containing data and user folders)")


class IMPORT_SCENE_OT_crashday_track(Operator, ImportHelper):
    bl_idname = "import_scene.crashday_track"
    bl_label = "Crashday Track (.trk)"
    bl_description = "Load original .trk terrain, P3D world models, UVs and textures"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".trk"
    filter_glob: StringProperty(default="*.trk", options={"HIDDEN"})
    game_directory: StringProperty(
        name="Crashday game directory",
        description="Containing data and user. Leave empty to detect from track location",
        default="", subtype="DIR_PATH",
    )
    mod_directory: StringProperty(
        name="Mod override folder (optional)",
        description="Leave blank for vanilla CPK assets plus missing content from mod_testing/Workshop; identical duplicates are safe. Select one mod folder to override conflicts or stock assets",
        default="", subtype="DIR_PATH",
    )
    max_tiles: IntProperty(
        name="Tile limit (0 = all)",
        description="Limit successfully imported P3D pieces for a preview; terrain and dynamics still cover the full map. Zero means all tiles",
        default=0, min=0, max=1000000,
    )
    preview_vegetation: IntProperty(
        name="Approximate vegetation (-1 = all, 0 = off)",
        description="Default -1 imports ALL CFL/VEG-based approximate instances, without an arbitrary cap. 0 skips vegetation; 1..2000 is a bounded quick preview. Original P3D models; NOT game-exact RNG or placement",
        default=-1, min=-1, max=2000,
    )

    include_out_of_bounds: BoolProperty(
        name="Include out-of-bounds scenery/background",
        description="Partial: import authored objects with origins outside the track grid. Native procedural scenery selected by the TRK byte is not yet decoded",
        default=False,
    )
    pack_imported_textures: BoolProperty(
        name="Pack imported textures into .blend",
        description="Embed Crashday image files in the Blender project so it can be reopened without the original game texture paths. Increases .blend size",
        default=False,
    )

    native_ambience_preview: BoolProperty(
        name="Experimental original ambience colors",
        description="Use .amb sky/sun colors and a tentative light direction; NOT a game-matched shader/lighting simulation",
        default=False,
    )

    def execute(self, context):
        src = Path(bpy.path.abspath(self.filepath))
        if not src.is_file():
            self.report({"ERROR"}, "Track does not exist: {}".format(src))
            return {"CANCELLED"}
        dependency = Path(__file__).resolve().with_name("wurunduk_p3d.py")
        if not dependency.is_file():
            self.report({"ERROR"}, "Bundled GPL P3D parser missing; reinstall the release ZIP")
            return {"CANCELLED"}
        try:
            game = find_game_root(src, self.game_directory)
            mod_root = (Path(bpy.path.abspath(self.mod_directory))
                        if self.mod_directory.strip() else None)
            result = import_scene(src, game, dependency, output=None,
                                  max_tiles=self.max_tiles, reset_scene=False,
                                  max_vegetation=self.preview_vegetation,
                                  native_ambience_preview=self.native_ambience_preview,
                                  include_out_of_bounds=self.include_out_of_bounds,
                                  pack_imported_textures=self.pack_imported_textures,
                                  mod_content_directory=mod_root)
        except Exception as error:
            # Operator undo can discard partially imported objects.
            self.report({"ERROR"}, "Crashday import failed: {}: {}".format(type(error).__name__, error))
            return {"CANCELLED"}
        if self.include_out_of_bounds:
            self.report({"WARNING"},
                        "Outside-grid authored objects included when present; procedural scenery is not yet supported")
        note = result.get("embedded_diagnostic_text", "Crashday - Import Report")
        scope = ("PREVIEW: {} of {} grid cells checked; ".format(
            result["tile_cells_examined"], result["tile_cells_total"])
            if result.get("tile_preview_partial") else "")
        if result.get("diagnostic_issue_count", 0):
            self.report({"WARNING"}, "{}Imported {} tile meshes with {} issues. See Text Editor > '{}'".format(
                scope, result["tile_objects"], result["diagnostic_issue_count"], note))
        else:
            self.report({"INFO"}, "{}Imported {} tile meshes. See Text Editor > '{}'".format(
                scope, result["tile_objects"], note))
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_directory")
        layout.prop(self, "mod_directory")
        layout.prop(self, "max_tiles")
        layout.prop(self, "preview_vegetation")
        layout.prop(self, "include_out_of_bounds")
        layout.prop(self, "pack_imported_textures")
        # Experimental native lighting remains script-accessible but is hidden:
        # this import tool exports world assets, not Crashday's rendering engine.


def _menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_crashday_track.bl_idname,
                         text="Crashday Track (.trk)")


classes = (IMPORT_SCENE_OT_crashday_track,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
