# Mica's Crashday Tools: P3D Validator v0.4

Read-only, independent Python 3.7+ validator for Crashday P3D2 files.
No Blender, Crashday runtime, third-party Python packages or game asset downloads required.

## Install
Extract the standalone ZIP. Open a terminal in the p3d_validator folder.
Run: python validate_p3d.py --help

## Examples
- Single file: python validate_p3d.py model.p3d
- Loose tile folder: python validate_p3d.py --directory C:/example/tiles
- Native CPK or Workshop ZIP-like BIN: python validate_p3d.py --archive C:/example/dat006.cpk
- Splitter batch identity: python validate_p3d.py --tile-manifest C:/example/tiles_manifest.json
- Detailed geometry bounds: add --bounds
- Machine-readable report: add --json (may contain private local paths)

Exit 0 indicates readable known native structure. Exit 2 indicates invalid/unsafe input.
Warnings are not automatically errors. PASS does not prove game loading, collision,
availability of actual textures, authored world placement, destruction or AI behavior.

Run only on data you have the right to access. No game assets are bundled.
Community-made and unaffiliated with the Crashday developers.

## Proposed license
GPL-3.0-or-later (pending first-public rights-holder signoff). See LICENSE and NOTICE.md.
