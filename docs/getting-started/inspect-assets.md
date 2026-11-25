# Inspect and troubleshoot assets

[Getting started](../index.md) · [Downloads](../downloads.md)

Both utilities run independently of Blender. Extract the selected ZIP, open a terminal in its extracted folder, and use Python 3.7 or newer.

**P3D Validator:** run python validate_p3d.py --help. For a single P3D, pass its path. For a directory, archive or tile export manifest, see the [P3D Validator guide](../../utilities/p3d-validator/).

**World Asset Preflight:** run python validate_world_assets.py --help. Use --content-root or --archive to inspect a mod; supply explicit --reference-archive inputs if assets are outside that mod. See the [Preflight guide](../../utilities/world-asset-preflight/) for dependency-check modes.

A structural or dependency PASS is not a native game-loading, texture, collision or gameplay guarantee. Both tools are read-only. JSON reports may contain local paths; review and sanitize them before sharing.
