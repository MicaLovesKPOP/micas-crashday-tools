<!-- Generated from docs/catalog.json by scripts/render_catalog.py. -->
# Mica's Crashday Tools

Create, convert, edit and validate worlds and assets for **Crashday: Redline Edition**.

Independent open-source Blender add-ons and standalone utilities. Separate versions and installers; original game files stay untouched.

**[Downloads](docs/downloads.md) | [Getting started](docs/index.md) | [Compatibility](docs/compatibility.md) | [Roadmap](docs/roadmap.md) | [Get help](https://github.com/MicaLovesKPOP/micas-crashday-tools/issues)**

## What do you want to do?

### Create a Crashday map from Blender

Use Tile Splitter and World Builder together. Start with geometry already in Blender; the current workflow is multi-step.

**Tile Splitter** — Split meshes into tiles, export native P3D models and generate overhead editor previews.

[Guide](addons/tile-splitter/) · [Download v0.4.7](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/tile-splitter-v0.4.7/micas-crashday-tools-tile-splitter-v0.4.7.zip) · [Release notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/tile-splitter-v0.4.7)

**World Builder** — Author checkpoints and allowed game modes, validate and build new offline Crashday maps.

[Guide](addons/world-builder/) · [Download v0.2.0](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/world-builder-v0.2.0/micas-crashday-tools-world-builder-v0.2.0.zip) · [Release notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/world-builder-v0.2.0)

### Import existing Crashday maps

Inspect and edit native maps in Blender without modifying the game.

**Crashday to Blender Exporter** — Read TRK/TRK2 tracks into editable Blender scenes with terrain, models, textures and source-constrained approximate vegetation.

[Guide](addons/crashday-exporter/) · [Download v0.1.0](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/exporter-v0.1.0/micas-crashday-tools-exporter-v0.1.0.zip) · [Release notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/exporter-v0.1.0)

### Inspect and troubleshoot assets

Independent read-only command-line utilities. No Blender dependency.

**P3D Validator** — Check P3D structure, bounds, archives and Tile Splitter manifests without writing game files.

[Guide](utilities/p3d-validator/) · [Download v0.4.0](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/p3d-validator-v0.4.0/micas-crashday-tools-p3d-validator-v0.4.0.zip) · [Release notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/p3d-validator-v0.4.0)

**World Asset Preflight** — Check CFL/VEG definitions and asset dependencies in loose folders and archives.

[Guide](utilities/world-asset-preflight/) · [Download v0.2](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/world-asset-preflight-v0.2/micas-crashday-tools-world-asset-preflight-v0.2.zip) · [Release notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/world-asset-preflight-v0.2)

## Before you install

Download the individual release ZIP, not GitHub's repository-wide source archive. See [installation and compatibility](docs/compatibility.md). Blender 2.80, 2.83, 3.4.1 and 4.5 LTS were exercised on the underlying add-ons; individual guides explain exact coverage. CLI tools require Python 3.7+.

The current map-building workflow is offline and multi-step. Fully automated preparation is planned, not shipped. Advanced native collision, custom checkpoint behavior and gameplay have documented limits. No original game assets are included.

## Contribute

[Contributing](CONTRIBUTING.md) · [Architecture](docs/architecture.md) · [Security](SECURITY.md) · [License scopes](LICENSES.md)

Independent community project, unaffiliated with the game's rights holders.
