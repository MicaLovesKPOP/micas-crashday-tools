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

Reproduce a specific release from its tagged source using its module guide. Existing builders pin qualified source; they intentionally reject unqualified changes. A common candidate/qualification builder is planned to streamline future development. Do not overwrite old tags or checksums. Native Blender and game acceptance are separate from source-only CI.

Use small synthetic fixtures and avoid including original game assets, private data or native runtime code. Follow module licenses and preserve notices when sharing components.
