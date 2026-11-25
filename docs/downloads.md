<!-- Generated from docs/catalog.json by scripts/render_catalog.py. -->
# Downloads

[Home](../README.md) · [Getting started](index.md) · [Compatibility](compatibility.md)

Choose the installer for your task. Every module is independently versioned; the repository-wide Latest release is not the latest version of each tool.

| Tool | Version | Requirements | Download | Source / notes |
|---|---|---|---|---|
| Tile Splitter | 0.4.7 | Blender | [ZIP](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/tile-splitter-v0.4.7/micas-crashday-tools-tile-splitter-v0.4.7.zip) | [Source](https://github.com/MicaLovesKPOP/micas-crashday-tools/tree/tile-splitter-v0.4.7/addons/tile-splitter) · [Notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/tile-splitter-v0.4.7) |
| World Builder | 0.2.0 | Blender; Tile Splitter for integrated authoring | [ZIP](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/world-builder-v0.2.0/micas-crashday-tools-world-builder-v0.2.0.zip) | [Source](https://github.com/MicaLovesKPOP/micas-crashday-tools/tree/world-builder-v0.2.0/addons/world-builder) · [Notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/world-builder-v0.2.0) |
| Crashday to Blender Exporter | 0.1.0 | Blender and installed Crashday assets | [ZIP](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/exporter-v0.1.0/micas-crashday-tools-exporter-v0.1.0.zip) | [Source](https://github.com/MicaLovesKPOP/micas-crashday-tools/tree/exporter-v0.1.0/addons/crashday-exporter) · [Notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/exporter-v0.1.0) |
| P3D Validator | 0.4.0 | Python 3.7+ | [ZIP](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/p3d-validator-v0.4.0/micas-crashday-tools-p3d-validator-v0.4.0.zip) | [Source](https://github.com/MicaLovesKPOP/micas-crashday-tools/tree/p3d-validator-v0.4.0/utilities/p3d-validator) · [Notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/p3d-validator-v0.4.0) |
| World Asset Preflight | 0.2 | Python 3.7+ | [ZIP](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/world-asset-preflight-v0.2/micas-crashday-tools-world-asset-preflight-v0.2.zip) | [Source](https://github.com/MicaLovesKPOP/micas-crashday-tools/tree/world-asset-preflight-v0.2/utilities/world-asset-preflight) · [Notes](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/tag/world-asset-preflight-v0.2) |

## Map creation

Tested combination: [Tile Splitter v0.4.7](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/tile-splitter-v0.4.7/micas-crashday-tools-tile-splitter-v0.4.7.zip) + [World Builder v0.2.0](https://github.com/MicaLovesKPOP/micas-crashday-tools/releases/download/world-builder-v0.2.0/micas-crashday-tools-world-builder-v0.2.0.zip). Exact paired automated Blender 3.4.1 workflow; underlying components tested on 2.80, 2.83.20 and 4.5.16 too. See module guides for limits.

Install both individual ZIPs. A combined convenience kit may be offered later.

## Verify your download

Each release includes SHA256SUMS.txt; compare its hash with your downloaded ZIP. The [machine-readable catalog](catalog.json) also records the exact published ZIP SHA-256, tag, source commit and license for every module.

For command-line tools, extract the ZIP and follow the module guide. For add-ons, use Blender Preferences to install the ZIP itself. Do not unpack files into the game folder.
