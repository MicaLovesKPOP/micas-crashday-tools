# Crashday to Blender Exporter v0.1.0

Independent Blender add-on for reading Crashday: Redline Edition TRK and
TRK2 / CDTR2 tracks into an editable scene. Requires installed game files,
which are read without modifying them.

**Supported Blender versions tested:** 2.80.0, 2.83.20, 3.4.1 and 4.5.16.
The functional source passed owner interactive import, Save As and reopen
in 3.4.1. The public package changes only add-on author metadata plus
documentation and license/attribution files.

## Installation

1. Download micas-crashday-tools-exporter-v0.1.0.zip from the tagged release.
2. In Blender choose Edit > Preferences > Add-ons > Install and select the ZIP.
3. Enable Crashday Track (.trk) - World Assets in the add-on list.
4. Choose File > Import > Crashday Track (.trk), select a track and your
   installed Crashday game directory. Select a specific mod/Workshop item
   directory when needed to resolve conflicting mod assets.
5. Inspect imported collections and the Import Report. Save As a new .blend
   file and reopen it using the same Blender version.

## Capabilities and limitations

- Imports native track terrain, editable tile meshes and objects, supported
  textures/UVs and authored dynamics, while preserving source float32 terrain
  heightmap samples for inspection.
- Supports genuine per-CFL/VEG model and image references. **Vegetation
  population and positions use seeded, source-constrained approximations**;
  game-exact RNG/counts are not claimed.
- Full approximate vegetation is enabled in normal mode. Bounded/off preview
  is available for faster tests. Large tracks can use substantial memory.
- Out-of-bounds scenery/background defaults OFF. Engine-generated scenery and
  original-game renderer/physics behavior are not fully recreated.
- This READ-ONLY import tool does not generate playable Crashday tracks from
  Blender; separate World Builder remains unfinished and is not included.
- If same-name assets differ between Workshop packages, choose an explicit
  mod/Workshop source rather than silently merging incompatible content.
- Invisible game collision walls may appear as visible Blender geometry;
  this represents original source objects, not a scale defect.
- The tool does not distribute or require modification of proprietary game data.

## Support and licensing

Open GitHub issues with tool/version, Blender and Crashday version, steps and
sanitized logs. Do not attach copyrighted game assets or personal file paths.

**GPL-2.0-only** for the combined add-on including pinned Wurunduk GPLv2
parser. Full terms in LICENSE, NOTICE.md and
crashday_track_importer/LICENSE_WURUNDUK_P3D.txt. Corresponding source is
included in the public tagged GitHub repository and ZIP.
