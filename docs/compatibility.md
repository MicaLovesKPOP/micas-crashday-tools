# Compatibility and installation

[Home](../README.md) · [Downloads](downloads.md)

| Tool type | Environment |
|---|---|
| Blender add-ons | Blender 2.80, 2.83.20, 3.4.1 and 4.5.16 LTS have been exercised on underlying implementations. The paired final map-creation ZIPs were tested together in 3.4.1. |
| Standalone CLI utilities | Python 3.7+; standard library. |
| Game-specific data | Crashday: Redline Edition. Installed game data or appropriate mod assets are needed for workflows that use them. |

See each module guide for exact coverage and experimental limitations. The table is evidence of tested versions, not a blanket promise for all intermediary Blender releases, operating systems or native gameplay scenarios.

## Install Blender add-ons

Download the individual add-on ZIP from [Downloads](downloads.md). For legacy Blender versions, open Edit > Preferences > Add-ons > Install. In Blender 4.2 and later, choose Install from Disk in the Add-ons interface for the legacy ZIP. Enable the add-on. For updates, save your work, remove the old add-on if necessary, install the new ZIP and restart Blender. Keep a previous release ZIP and a backup of your scene.

The current packages are legacy add-ons, not Blender Extensions manifests. Modern extension packaging may be added later without dropping existing legacy support.

## CLI tools

Extract the individual ZIP; open a terminal in its folder and follow the module README. No installation into Crashday is required.

## Output safety

Use a new output directory outside the game installation. Keep original tracks, mods, textures and Blender scenes intact. To remove an add-on, disable/remove it via Preferences and restart Blender; generated files remain. Remove extracted CLI folders to uninstall them.
