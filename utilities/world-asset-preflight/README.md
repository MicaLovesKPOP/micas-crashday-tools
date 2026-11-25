# Mica's Crashday Tools — CFL/VEG World Dependency Preflight v0.2

Independent, read-only authoring **reference** checker for a primary Crashday content package/folder and optional explicitly supplied reference CPK/Workshop archives. No Blender dependency or changes to game/mod files. Python **3.7+**, standard library, and the bundled dependency parser; no game assets are included.

In an extracted `world_asset_preflight` folder:

```text
python validate_world_assets.py --content-root "D:\staging\my_mod\content"
python validate_world_assets.py --archive "D:\staging\my_mod_legacy.bin"
python validate_world_assets.py --content-root "D:\staging\my_mod\content" --require-self-contained
python validate_world_assets.py --archive "D:\staging\my_mod_legacy.bin" --json
python validate_world_assets.py --archive "D:\staging\my_mod_legacy.bin" --reference-archive "D:\stock\dat004.cpk" --json
python validate_world_assets.py --archive "D:\staging\my_mod_legacy.bin" --reference-archive "D:\stock\dat004.cpk" --require-resolved-direct
python validate_world_assets.py --archive "D:\staging\my_mod_legacy.bin" --reference-archive "D:\stock\dat004.cpk" --require-resolved-transitive --json
```

The tool checks native `content/tiles/*.cfl` definitions with the current Exporter's bounded CFL parser, and `content/vegetat/*.veg` with its native VEG parser. It checks relative existence of referenced `content/models/*.p3d`, vegetation definitions, VEG model P3Ds and inherited CFL aliases; detects loops in same-package vegetation inheritance. It rejects duplicate case-insensitive archive paths, malicious traversal and oversized/unbounded CFL/VEG sources, and refuses symlinks/reparse points or escaped paths in loose directory trees. Native game and downloaded mods stay **read only**; ZIP inputs are opened in memory without extracting.

**Default:** absent primary and supplied-reference direct dependencies are reported as **UNRESOLVED warnings**, not assumed broken. Crashday ordinarily distributes vanilla content across multiple CPK archives. The optional repeatable `--reference-archive` checks **presence of named assets only** in up to 12 explicitly selected read-only ZIP-format CPK/Workshop packages; it does NOT automatically search/merge Workshop mods. Assets present in the primary source always take precedence. When two references offer the same required filename, the utility hashes their **actual decompressed bytes** and accepts only identical copies; conflicting or unhashable candidates are reported **AMBIGUOUS and fail closed** rather than silently selecting one. Unrelated duplicate filenames are irrelevant.

`--require-resolved-transitive` is the stricter opt-in closure check: recursively parse only the CFL and VEG definitions actually referenced from the primary source within the explicitly selected archives, then require their newly discovered P3D/VEG/CFL references too. It also detects mixed primary/external CFL vegetation inheritance cycles and records which external definitions were parsed. The primary source wins over external copies; conflicting external definitions must have identical decompressed SHA-256 bytes or the check fails. Malformed/oversized referenced CFL/VEG contents fail closed. It **does not** recursively validate P3D binary geometry, TEX, textures or in-game compatibility; it does not implicitly add any Workshop packages. External dependency expansion is bounded to 1 MiB per native definition, 128 MiB total, and explicit references only. The `cfl_count` and `veg_count` fields remain primary-source counts; `external_definitions_parsed` records the added external graph vertices. This mode is exclusive with the other two strictness switches.

`--require-self-contained` retains its original meaning: require every primary direct reference **inside the primary package**, even when external packages were supplied. `--require-resolved-direct` separately requires every primary CFL/VEG direct reference to be present in either the primary source or an unambiguous explicit reference package. These switches are mutually exclusive. The latter is an **existence-only direct dependency check**: externally referenced VEG/CFL contents are not recursively parsed or validated, and P3D binary contents are not checked. It is not a proof of complete transitive source/model closure or actual engine compatibility. Missing file references are grouped by source/relation/asset with true occurrence counts.

**PASS** means parseable known CFL/VEG structure and no confirmed within-source inheritance cycles. It does **not** independently validate P3D binary contents, textures or .TEX materials, game loading, collision, world geometry, navigation, or third-party mod compatibility. Run the separately verified P3D Validator `--directory` or `--archive` for actual P3D structural checks. The parser accepts bounded positive nonsequential and repeated `Next: N` headings, preserving article order. A rejected file is not thereby proven rejected by the game.

Return code **0** for no parser/safety errors (external missing sources may still be unresolved); **2** for bad inputs, parser errors, cyclic local aliases or strict missing dependencies. Machine-readable `--json` includes private filesystem paths and authored asset names; review before sharing. No files are installed into Crashday. To remove the utility, delete its extracted folder. Use game originals exclusively as read-only fixtures.

**Native CFL framing:** add `--native-cfl-framing` to check primary CFLs and reachable external CFLs in transitive mode for CRLF framing; a final CRLF is optional. The supported P3D reference scope is `content/models/*.p3d`, and CFL/VEG definitions must remain within the supported parser directories. A passed framing or dependency check does not certify native loading or collision.

Report reproducible defects with the command, version, sanitized error and a small redistributable fixture at https://github.com/MicaLovesKPOP/micas-crashday-tools/issues. Do not attach private paths or copyrighted game assets.
