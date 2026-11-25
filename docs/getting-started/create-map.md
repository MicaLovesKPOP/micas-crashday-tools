# Create a Crashday map from Blender

[Getting started](../index.md) · [Downloads](../downloads.md)

This guide covers the current released Tile Splitter and World Builder workflow, not the planned one-click converter.

1. Install both tested companion add-ons listed in [Downloads](../downloads.md). Save a copy of your Blender scene.
2. Select the meshes you want to convert. In the 3D View, press N and open Crashday > Crashday Tiles. For the ordinary checkpoint workflow, choose 20 x 20 m tiles; leave Delete Originals off.
3. Split the selected meshes. Choose a new export folder outside the game installation. Disable Export Selected Tiles Only if you intend to export all generated tiles; keep Write Tile Manifest enabled.
4. Export Tiles to P3D. Render Native Overhead Tile Previews for the same tile scope. Keep the JSON manifests and generated files together.
5. In World Builder - Checkpoints, choose the exported tile manifest and Load Exported Tiles. Leave experimental oversized layout off for the ordinary workflow. Click or add valid 20 m checkpoint tiles. The first checkpoint is the start; choose relative native turns and order.
6. Choose starting-checkpoint mode and allowed game modes. Under Advanced source assets, choose the matching overhead preview manifest. Select an existing output directory outside the game and a new map name. Validate Map, then Build Map.
7. Save your editable scene. Inspect the resulting map folder and validation report. Appropriate mod setup and dependencies are still required in Crashday; the tool does not install into or launch the game.

For full controls, source texture handling and limitations, see the [Tile Splitter guide](../../addons/tile-splitter/) and [World Builder guide](../../addons/world-builder/).

Basic native main P3D collision has been demonstrated. Advanced physical layout, absolute elevation, checkpoint heading/trigger and gameplay are not fully game-qualified. The current workflow is not a guarantee of compatibility with arbitrary imported game worlds. Only redistribute assets you have the right to share.
