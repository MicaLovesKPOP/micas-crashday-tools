# Architecture and development

[Home](../README.md) · [Contributing](../CONTRIBUTING.md) · [Licenses](../LICENSES.md)

Mica's Crashday Tools is a modular repository, not a single mandatory add-on. Each module has an independent installer, version, guide, license and attribution notice.

| Directory | Role |
|---|---|
| addons/ | Independently installable Blender modules |
| utilities/ | Independently usable CLI tools |
| docs/ | Task guides, compatibility, roadmap and download catalog |
| scripts/ | Source-only validation and reproducible packaging |
| tests/ | Regression and packaging smoke tests |
| .github/ | CI and issue-report templates |

Shared code should have one editable origin and a documented, license-compatible dependency relationship; self-contained installers may contain generated copies. A future shared directory will be added only when real reusable components justify it. Preserve operator IDs, native format behavior, source files and legacy scene compatibility. Do not force the Exporter's GPL-2.0-only dependency into other tools; consult LICENSES.md.

Planned world-authoring architecture: Blender UI and adapters -> workflow coordinator -> geometry/material/texture/naming/route services -> native format writers and independent validators. Stable data contracts and versioned cache keys allow incremental rebuilds and independently useful processing stages. This is a design direction, not a claim that the released World Builder implements the entire pipeline.

## Source and test workflow

Run python -m unittest discover -s tests -v from the repository root. Run python scripts/render_catalog.py --check to verify generated landing and download pages. For approved catalog edits, run the same script without --check to regenerate them.

## Package a tool

Use the shared source-only builder from the repository root:

- Exact published reproduction: `python scripts/package_tool.py --module tile-splitter --output-dir EXISTING_FOLDER`
- Local development candidate: `python scripts/package_tool.py --module tile-splitter --output-dir EXISTING_FOLDER --candidate`

The module IDs are listed in the [download catalog](catalog.json). The shared package recipe explicitly maps source files to archive members and preserves the historical order, timestamps, line endings and hashes of each released ZIP. Frozen builds must reproduce the published digest or fail without writing. A different compressor/toolchain may require the qualified environment. Use an existing output folder outside the game installation.

Candidates permit local edits within the explicit layout and use a distinct hash-suffixed filename. They are **not qualified or published releases**; license and upstream component locks remain enforced. An add-on source manifest is regenerated inside the candidate to reflect its actual bytes. Review the package recipe if adding or removing source members. A future release requires its own approved version, source/rights/test review, frozen hashes and catalog update; a successful candidate build alone grants no publication permission.

Legacy build-script entry points still work on main through the common builder. Original tagged source and build scripts remain available for historical reproduction. Do not overwrite old tags or checksums. Native Blender and game acceptance are separate from source-only CI.

Use small synthetic fixtures and avoid including original game assets, private data or native runtime code. Follow module licenses and preserve notices when sharing components.
