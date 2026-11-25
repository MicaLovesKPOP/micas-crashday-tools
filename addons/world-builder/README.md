# World Builder v0.2.0

Build new offline Crashday maps from exported Blender tiles, author ordered 20 m checkpoints, choose native permission bits and validate/create a new map folder. First public release; not a runtime modification.

## Install and use

Download the installable ZIP from [Releases](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/world-builder-v0.2.0). In Blender use Edit > Preferences > Add-ons > Install and enable Mica's Crashday World Builder. Install [Tile Splitter v0.4.7](../tile-splitter/) for integrated mesh authoring and export. An existing compatible export manifest can also be used. Restart if updating. Open N > Crashday.

1. Split selected meshes into ordinary 20 x 20 m tiles, export P3Ds and the tile manifest, then render native overhead previews.
2. In World Builder - Checkpoints choose the exported tile manifest and Load Exported Tiles. Leave oversized physical tiles disabled for this standard workflow.
3. Click a valid 20 m checkpoint tile or add a selected tile. The first route entry is start. Reorder and select the relative Native turn. Markers update automatically and can be hidden.
4. Choose starting-checkpoint mode and allowed game modes. For authored routes use the ordered checkpoints. No-checkpoint builds cannot provide an ordinary Test Drive start; the stock starting strip is a separate route-free option.
5. Choose an existing output directory outside Crashday and a new map name. In Advanced source assets select the matching overhead preview manifest. Validate Map, then Build Map. Validate should not create a map; Build creates a NEW folder. No optional CAT catalogue; CFL Autolist=0.
6. Save your Blender scene separately to preserve editable tiles and route.

The generated folder requires appropriate assets/mod setup in Crashday. No original game files are distributed. Basic native main P3D ramp collision was verified with the unchanged serializer. Native checkpoint heading, actual custom-route/game-mode playability, absolute elevation and advanced oversized/void collision are not game-qualified. Oversized physical layout/carving is experimental and requires an explicit compatible void CFL/P3D pair. Checkpoint markers are authoring guides, not AI navigation.

## Compatibility and safety

Blender 2.80, 2.83.20, 3.4.1 and 4.5.16 LTS were exercised on the underlying implementation and adaptive panel draw logic. The paired final workflow was exercised automatically on Blender 3.4.1; the preceding candidate also passed human interactive functional acceptance. Actual rendered adaptive layout across all widths and scales, panel order on screen and interactive marker undo/redo were not independently verified for this release. Save your work and keep backups. Use new output folders outside the game installation. No automatic game installation or game launch.

## Uninstall / rollback

Disable and remove the add-on in Blender Preferences > Add-ons, restart, and reinstall a previous version if necessary. This does not remove generated geometry or map files. Preserve original game data and user tracks.

## Support and source

Report Blender and add-on versions, steps, expected/actual behavior and sanitized errors in [Issues](https://github.com/MicaLovesKPOP/micas-crashday-tools/issues). Do not upload proprietary game data or private files. Complete modifiable source is in this repository and the installable ZIP. To rebuild from the repository root with Python 3.7+, run python scripts/build_paired_addons.py --module world-builder --output-dir EXISTING_FOLDER. See LICENSE and NOTICE.md for the module's license.

License: GPL-3.0-or-later.
