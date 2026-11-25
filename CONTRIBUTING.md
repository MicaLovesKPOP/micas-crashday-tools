# Contributing

Start with the [architecture and development guide](docs/architecture.md) and the affected tool's README.

For bugs, open an issue with the module/version, OS, relevant Blender/Python/Crashday versions, reproduction steps, expected and actual behavior, and sanitized logs. A small synthetic or legally shareable fixture is more useful than an entire imported game map. Suggestions are welcome in the same issue tracker.

For code contributions, keep changes scoped to the affected module, preserve supported versions and independent installation, and add regression tests where practical. Run python -m unittest discover -s tests -v before submitting changes. If changing release metadata, regenerate the catalog pages using python scripts/render_catalog.py and verify with --check.

Contributions must have correct provenance and be compatible with the module license; preserve existing notices. Do not bundle proprietary game assets, private files, credentials or machine-specific data. Never modify original game files as a test side effect. Game-runtime changes belong to the separate Crashday Unleashed project.

See [license scopes](LICENSES.md) and [security reporting](SECURITY.md).
