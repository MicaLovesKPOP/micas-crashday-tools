# Tile Splitter v0.4.7

Split Blender meshes into grid-aligned Crashday tiles, export native P3D models and generate overhead TGA previews. First public release of the recovered and extended tool.

## Install and use

Download the installable ZIP from [Releases](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/tile-splitter-v0.4.7). In Blender use Edit > Preferences > Add-ons > Install and enable Mica's Crashday Tile Splitter. Restart if updating. Open N > Crashday > Crashday Tiles. No separate P3D plugin is required.

1. Select the source mesh objects, choose width/depth (20 m for ordinary World Builder checkpoints), and keep Delete Originals disabled unless you intend to delete them.
2. Split Selected Meshes Into Tiles. Originals are retained by default.
3. Choose a fresh export folder. Disable Export Selected Tiles Only to export all generated tiles; leave Write Tile Manifest enabled.
4. Export Tiles to P3D, then Render Native Overhead Tile Previews into the same folder and scope. Retain both JSON manifests.
5. For map assembly install the separate [World Builder](../world-builder/).

The configurable centre-to-corner distance is an authoring constraint, not a proven native physics limit. Geometry can cover only part of a logical tile. Basic generated main P3D ramp contact was confirmed in Crashday; arbitrary oversized collision, seams and absolute elevation are not game-qualified. Texture conversion is separate from native texture setup.

## Compatibility and safety

Blender 2.80, 2.83.20, 3.4.1 and 4.5.16 LTS were exercised on the underlying implementation and adaptive panel draw logic. The paired final workflow was exercised automatically on Blender 3.4.1; the preceding candidate also passed human interactive functional acceptance. Actual rendered adaptive layout across all widths and scales, panel order on screen and interactive marker undo/redo were not independently verified for this release. Save your work and keep backups. Use new output folders outside the game installation. No automatic game installation or game launch.

## Uninstall / rollback

Disable and remove the add-on in Blender Preferences > Add-ons, restart, and reinstall a previous version if necessary. This does not remove generated geometry or map files. Preserve original game data and user tracks.

## Support and source

Report Blender and add-on versions, steps, expected/actual behavior and sanitized errors in [Issues](https://github.com/MicaLovesKPOP/micas-crashday-tools/issues). Do not upload proprietary game data or private files. Complete modifiable source is in this repository and the installable ZIP. To rebuild from the repository root with Python 3.7+, run python scripts/build_paired_addons.py --module tile-splitter --output-dir EXISTING_FOLDER. See LICENSE and NOTICE.md for the module's license.

License: GPL-2.0-or-later.
