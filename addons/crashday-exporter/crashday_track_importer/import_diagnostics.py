"""Readable, deterministic text diagnostics for an imported Crashday scene.

No bpy dependency: formatter stays unit-testable on Python 3.7.
"""


def issue_count(report):
    """Count unresolved source references without double-counting preview warnings."""
    return (len(report.get("errors", []))
            + len(report.get("shared_cfl_constraint_errors", []))
            + sum(len(report.get(key, {})) for key in (
                "missing_cfl", "missing_p3d",
                "missing_dynamic_cdo", "missing_dynamic_p3d"))
            + len(report.get("missing_or_archive_only_textures", []))
            + sum(len((report.get("vegetation_preview") or {}).get(key, {}))
                  for key in ("missing_veg", "missing_veg_p3d"))
            + (1 if report.get("source_ambience_error") else 0))

def _vegetation_mode_summary(vegetation):
    """Distinguish full source-constrained approximation from bounded preview."""
    count = vegetation.get("mesh_instances", 0)
    if not vegetation.get("preview_enabled"):
        return "Native vegetation P3D preview: OFF ({} meshes; disabled)".format(count)
    selected = vegetation.get("selected_instances", count)
    candidates = vegetation.get("candidate_instances", "unknown")
    if vegetation.get("mode") == "FULL_APPROXIMATE":
        return (
            "Native vegetation P3D instances: FULL APPROXIMATE "
            "({} meshes; {} / {} source-constrained candidates, no sampling cap; "
            "native game RNG and copy allocation unverified)"
        ).format(count, selected, candidates)
    return (
        "Native vegetation P3D preview: ON "
        "({} meshes; {} / {} approximate candidates, intentionally sampled/capped; "
        "native game RNG and copy allocation unverified)"
    ).format(count, selected, candidates)


def format_import_report(report):
    """Create a human-readable report to store directly inside the .blend."""
    if report.get("tile_preview_partial"):
        tile_scope = ("PARTIAL: {} of {} logical grid cells examined (mesh limit {}). "
                      "Other cells may contain unreported missing assets.").format(
                          report.get("tile_cells_examined", "?"),
                          report.get("tile_cells_total", "?"),
                          report.get("tile_limit", "?"))
    elif report.get("tile_scan_complete") is False:
        tile_scope = ("INCOMPLETE: {} of {} grid cells examined (import stopped early). "
                      "Unexamined cells may contain missing assets.").format(
                          report.get("tile_cells_examined", "?"),
                          report.get("tile_cells_total", "?"))
    elif "tile_cells_total" in report:
        tile_scope = "FULL: all {} logical grid cells examined".format(
            report["tile_cells_total"])
    else:
        tile_scope = "not specified"
    vegetation = report.get("vegetation_preview") or {}
    height_methods = vegetation.get("height_placement_counts") or {}
    height_summary = ("; ".join("{}: {}".format(key, height_methods[key])
                               for key in sorted(height_methods))
                      if height_methods else "none recorded")
    lines = [
        "CRASHDAY TRACK - WORLD-ASSET IMPORT REPORT",
        "This report is embedded in the Blender file (Text Editor > text datablocks).",
        "Original Crashday files were read only.",
        "",
        "Track: {}".format(report.get("input_track", "unknown")),
        "Track format: {}".format(report.get("source_track_format", "CDTRK")),
        "TRK2 embedded game.dbs lines: {}{}".format(
            report.get("embedded_game_dbs_count", 0),
            " (Text Editor > '{}'; not applied in Blender)".format(
                report["embedded_game_dbs_text"])
            if report.get("embedded_game_dbs_text") else ""),
        "Original TRK2 screenshot: {}".format(
            "packed Blender Image > '{}' (1024x1024 BGRA8 source)".format(
                report["embedded_original_preview_image"])
            if report.get("embedded_original_preview_image") else "(not present)"),
        "Unparsed track extension bytes: {}{}".format(
            report.get("unparsed_extension_bytes", 0),
            " (unknown TRK2 trailing payload; no guessed interpretation)"
            if report.get("source_track_format") == "CDTR2"
            and report.get("unparsed_extension_bytes", 0) else ""),
        "Explicit mod override: {}".format(
            ", ".join(report.get("selected_mod_content_paths", [])) or "(none)"),
        "Default mod_testing root: {}".format(
            report.get("mod_testing_root") or "(not discovered)"),
        "Default Steam Workshop root: {}".format(
            report.get("workshop_root") or "(not discovered)"),
        "Mod assets used only when not in vanilla CPK/stock: {}".format(
            len(report.get("fallback_assets_used", []))),
        "Identical duplicate mod assets verified with SHA-256: {}".format(
            len(report.get("fallback_identical_asset_sources", {}))),
        "Different/unsafe mod versions requiring a selection: {}".format(
            len(report.get("fallback_ambiguous_asset_sources", {}))),
        "Cross-mod model dependencies blocked: {}".format(
            len(report.get("blocked_cross_mod_model_dependencies", {}))),
        "Cross-mod texture dependencies blocked: {}".format(
            len(report.get("blocked_cross_mod_texture_dependencies", {}))),
        "Imported P3D track meshes: {}".format(report.get("tile_objects", 0)),
        "Track tile coverage: {}".format(tile_scope),
        "Imported dynamic P3D meshes: {}".format(report.get("dynamic_mesh_objects", 0)),
        "CFL vegetation articles in assembled tiles: {}".format(
            (report.get("vegetation_preview") or {}).get("source_vegetation_zones", "unavailable")),
        _vegetation_mode_summary(vegetation),
        "Vegetation height placement modes: {}".format(height_summary),
        "Fixed-XY source vegetation candidates imported: {}".format(
            "{} / {}".format(
                (report.get("vegetation_preview") or {}).get("selected_anchored_instances", 0),
                (report.get("vegetation_preview") or {}).get("anchored_candidate_instances", 0))
            if (report.get("vegetation_preview") or {}).get("preview_enabled")
            else "not evaluated (preview disabled)"),
        "Original height-grid vertices: {}".format(report.get("source_terrain_vertices", 0)),
        "Neutral preview camera/lighting created: {}".format(
            "yes" if report.get("preview_setup_created") else "no (existing scene preserved)"),
        "Missing/unresolved issue entries: {}".format(issue_count(report)),
        "Out-of-bounds background requested: {}".format(
            "yes" if report.get("include_out_of_bounds") else "no"),
        "Out-of-bounds limitations: {}".format(report.get("outside_background_status", "not reported")),
        "",
        "IMPORTANT LIMITATIONS",
        "The native track geometry/UVs are the goal; this is not a Crashday renderer.",
        "Terrain deformation and dynamic ground-drop can be approximations.",
        "Vegetation uses original CFL/VEG/P3D assets, but native game RNG, copy allocation,",
        "individual XY placement, rotation and numeric height rules are not fully verified.",
        "Full mode attempts all generated approximate candidates without a sample cap;",
        "Procedural outer world is not decoded.",
        "A tile mesh limit does not limit the terrain height grid or authored dynamic objects.",
        "",
    ]
    sections = (
        ("Tile definitions missing or invalid (.cfl)", "missing_cfl"),
        ("Tile models missing (.p3d)", "missing_p3d"),
        ("Dynamic definitions missing or invalid (.cdo)", "missing_dynamic_cdo"),
        ("Dynamic P3D models missing", "missing_dynamic_p3d"),
        ("Unresolved texture references", "missing_or_archive_only_textures"),
        ("Track tile assembly errors", "errors"),
        ("Shared CFL border errors", "shared_cfl_constraint_errors"),
    )
    for heading, key in sections:
        value = report.get(key) or {}
        lines.append("{} ({}):".format(heading, len(value)))
        if isinstance(value, dict):
            lines.extend("  {}: {}".format(k, value[k]) for k in sorted(value))
        else:
            lines.extend("  {}".format(item) for item in value)
        if not value:
            lines.append("  None")
        lines.append("")
    if report.get("missing_cfl"):
        suggestions = report.get("suggested_mod_folders_for_missing_cfl") or {}
        lines.append("Possible installed mod folders for missing CFLs ({}):".format(len(suggestions)))
        if suggestions:
            lines.append("  Candidates only: these mods were NOT loaded or mixed automatically.")
            lines.append("  Select ONE known matching folder in 'Mod override folder' and re-import.")
            lines.append("  If multiple mods match a name, the correct source is ambiguous.")
            for cfl in sorted(suggestions):
                lines.append("  {}: {}".format(cfl, " ; ".join(suggestions[cfl])))
        else:
            lines.append("  None found in direct user/mod_testing mod folders; Workshop conflicts are reported separately.")
        lines.append("")
    # Model and dynamic-file hints extend the CFL lookup without changing
    # missing-asset counts or loading any unselected mod automatically.
    missing_models = report.get("missing_p3d") or {}
    missing_dynamic_models = report.get("missing_dynamic_p3d") or {}
    missing_dynamic_defs = report.get("missing_dynamic_cdo") or {}
    for requested, heading, key in (
        (bool(missing_models or missing_dynamic_models),
         "Possible installed mod folders for missing P3D models",
         "suggested_mod_folders_for_missing_p3d"),
        (bool(missing_dynamic_defs),
         "Possible installed mod folders for missing CDO definitions",
         "suggested_mod_folders_for_missing_cdo"),
    ):
        if not requested:
            continue
        suggestions = report.get(key) or {}
        lines.append("{} ({}):".format(heading, len(suggestions)))
        if suggestions:
            lines.append("  Filename matches only; these mods were NOT activated automatically.")
            lines.append("  Choose the correct 'Mod override folder' and re-import.")
            for asset in sorted(suggestions):
                lines.append("  {}: {}".format(asset, " ; ".join(suggestions[asset])))
        else:
            lines.append("  No additional mod filename matches found")
        lines.append("")
    identical = report.get("fallback_identical_asset_sources") or {}
    if identical:
        lines.append("Identical mod asset copies safely deduplicated ({}):".format(
            len(identical)))
        lines.append("  SHA-256 verified identical bytes, not merely matching filenames.")
        for asset in sorted(identical):
            lines.append("  {}: {}".format(asset, " ; ".join(identical[asset])))
        lines.append("")
    conflicts = report.get("fallback_ambiguous_asset_sources") or {}
    if conflicts:
        lines.append("Unresolved mod asset-source conflicts ({}):".format(len(conflicts)))
        lines.append("  Different or unverified bytes; no arbitrary source was loaded.")
        lines.append("  Select the correct mod_testing or Steam Workshop item folder "
                     "in 'Mod override folder' and re-import.")
        for asset in sorted(conflicts):
            lines.append("  {}: {}".format(asset, " ; ".join(conflicts[asset])))
        lines.append("")
    cross_mod = report.get("blocked_cross_mod_model_dependencies") or {}
    if cross_mod:
        lines.append("Blocked cross-mod P3D dependencies ({}):".format(len(cross_mod)))
        lines.append("  Definition and model were found in different mods; no unrelated model was loaded.")
        lines.append("  Select a complete matching mod folder/item containing the model, or use a complete source package.")
        lines.append("  Automatic cross-mod composition is not supported; no source files were changed.")
        for name in sorted(cross_mod):
            detail = cross_mod[name]
            lines.append("  {} from {} [{}]; other model roots: {}".format(
                name, detail["definition"], detail["definition_source"],
                " ; ".join(detail["other_model_sources"])))
        lines.append("")
    cross_tex = report.get("blocked_cross_mod_texture_dependencies") or {}
    if cross_tex:
        lines.append("Blocked cross-mod P3D texture/.tex dependencies ({}):".format(len(cross_tex)))
        lines.append("  Only same-mod or vanilla/explicit textures were considered; no unrelated image or shader loaded.")
        lines.append("  Select the complete source mod folder/item to resolve missing textures.")
        for name in sorted(cross_tex):
            detail = cross_tex[name]
            lines.append("  {} from P3D {} [{}]; other texture roots: {}".format(
                name, detail["definition"], detail["definition_source"],
                " ; ".join(detail["other_model_sources"])))
        lines.append("")
    # Retain the exact asset choice (including ZIP member and reason) in
    # every saved .blend. Group by unique definition, not every repeated cell.
    for heading, key in (
        ("Imported tile CFL/P3D provenance", "tile_asset_source_provenance"),
        ("Imported dynamic CDO/P3D provenance", "dynamic_asset_source_provenance"),
    ):
        chosen = report.get(key) or {}
        lines.append("{} ({} unique definitions):".format(heading, len(chosen)))
        if chosen:
            for name in sorted(chosen):
                lines.append("  {}:".format(name))
                for role in sorted(chosen[name]):
                    lines.append("    {}: {}".format(role, chosen[name][role]))
        else:
            lines.append("  None")
        lines.append("")
    inherited = report.get("vegetation_alias_asset_source_provenance") or {}
    if inherited:
        lines.append("Inherited vegetation CFL source choices ({}):".format(len(inherited)))
        for name in sorted(inherited):
            source = inherited[name]
            lines.append("  {}: {} | {} | {}".format(
                name, source.get("reason", "?"),
                source.get("package_or_file", "?"),
                source.get("archive_member", "")))
        lines.append("")
    vegetation = report.get("vegetation_preview") or {}
    for heading, key in (("Vegetation definitions missing", "missing_veg"),
                         ("Vegetation P3D models missing", "missing_veg_p3d")):
        missing = vegetation.get(key) or {}
        lines.append("{} ({}):".format(heading, len(missing)))
        lines.extend("  {}: {}".format(k, missing[k]) for k in sorted(missing))
        if not missing:
            lines.append("  None")
        lines.append("")
    if report.get("source_ambience_error"):
        lines.extend(("Original ambience read warning:", "  " + str(report["source_ambience_error"]), ""))
    return "\n".join(lines)
