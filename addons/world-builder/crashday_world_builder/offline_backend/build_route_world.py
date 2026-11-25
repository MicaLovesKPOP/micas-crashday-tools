"""One-command PRIVATE native world build with an ordered authored checkpoint route.

Thin composition of the existing audited staging, real P3D/CFL writer and
checkpoint-route codec.  Never mutates a source, installed game or Steam files.
Python 3.7+, stdlib only.  NOT native-game/collision qualified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from . import author_trk_offline as native
from .checkpoint_routes import native_route_layout, read_route, counterrotate_model, patch_cfl_assets
from .stage_tile_cfls import plan_tiles, stage_tiles


LABEL = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")


class RouteBuildError(ValueError):
    pass


def _sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_PLAN_DIGESTS = ("input_manifest_sha256", "source_texture_manifest_sha256",
                 "native_tex_policy_sha256", "native_tile_preview_manifest_sha256")


def _plan_digests(plan):
    return {key: plan.get(key) for key in _PLAN_DIGESTS}


def _route_digest(path):
    path = Path(path)
    if (not path.is_file() or path.is_symlink() or
            not 1 <= path.stat().st_size <= 65536):
        raise RouteBuildError("Unsafe or missing checkpoint route JSON (64 KiB maximum)")
    return _sha_file(path)


def _label(label):
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise RouteBuildError("Output label must be 1-48 safe ASCII characters")
    return label


def _void_snapshot(plan, cfls, void_cfl, void_p3d, macro_layout):
    """Bind explicit private gap-fill assets to the same preflight as the track.

    Structural checks only; no claim that this is a noncolliding native tile.
    """
    if not macro_layout and (void_cfl is not None or void_p3d is not None):
        raise RouteBuildError("Explicit void assets require macro-layout mode")
    names = [("tiles", name, raw, hashlib.sha256(raw).hexdigest())
             for name, raw in cfls.items()]
    names.extend(("models", piece["p3d"], b"", piece["p3d_sha256"])
                 for piece in plan["pieces"])
    name, assets, ground = native._verified_void_pair(
        void_cfl, void_p3d, names, macro_layout=macro_layout)
    if assets and ground != "GROUND_VOID":
        raise RouteBuildError("Macro gap filler must declare GROUND_VOID")
    return name, {folder + "/" + filename: digest
                  for folder, filename, raw, digest in assets}


def inspect_route_world(tile_manifest, route_json, texture_manifest=None,
                        tex_policy=None, tile_preview_manifest=None,
                        scenery_profile="highlands", vertical_mode="shared_source_z",
                        game_mode_mask=255, checkpoint_mode="AUTHORED",
                        macro_layout=False, void_cfl=None, void_p3d=None):
    """Read-only source preflight with the same vertical contract as Build."""
    game_mode_mask = native.validate_game_mode_mask(game_mode_mask)
    native._scenery_selector(scenery_profile)
    if vertical_mode not in ("shared_source_z", "native_unmodified"):
        raise RouteBuildError("Unsupported vertical mode")
    plan, _native_cfls = plan_tiles(
        tile_manifest, texture_manifest=texture_manifest, tex_policy=tex_policy,
        tile_preview_manifest=tile_preview_manifest, macro_layout=macro_layout)
    void_name, void_digests = _void_snapshot(
        plan, _native_cfls, void_cfl, void_p3d, macro_layout)
    if checkpoint_mode not in ("AUTHORED", "NONE", "STOCK"):
        raise RouteBuildError("Unknown checkpoint route mode")
    if (checkpoint_mode == "AUTHORED") != (route_json is not None):
        raise RouteBuildError("Checkpoint mode conflicts with the supplied route")
    route_sha = _route_digest(route_json) if route_json is not None else None
    route = read_route(route_json) if route_json is not None else []
    width, height, fields, tiles, ordered = native._track_layout(
        plan, void_field=void_name, test_drive_start=checkpoint_mode == "STOCK",
        checkpoint_route=route)
    # Validate generated CFL checkpoint records with the same pure
    # transformation used by Build; no output or source file is changed.
    patch_cfl_assets([("tiles", name, raw, hashlib.sha256(raw).hexdigest())
                      for name, raw in _native_cfls.items()], route)
    # Apply the same in-memory transformations as Build, including private
    # counterrotation for checkpoint turns. Reject unsupported geometry
    # before creating any output, even in native_unmodified vertical mode.
    turns = {e["cfl"].casefold(): e["rotation"] for e in route}
    source_root = Path(tile_manifest).resolve(strict=True).parent
    for piece in plan["pieces"]:
        turn = turns.get(piece["cfl"].casefold(), 0)
        if vertical_mode != "shared_source_z" and not turn:
            continue
        model = source_root / piece["p3d"]
        if not model.is_file() or model.is_symlink():
            raise RouteBuildError("Source P3D missing or redirected: " + piece["p3d"])
        raw = model.read_bytes()
        if hashlib.sha256(raw).hexdigest() != piece["p3d_sha256"]:
            raise RouteBuildError("Source P3D changed during vertical/rotation preflight")
        if vertical_mode == "shared_source_z":
            raw, _details = native._shared_world_z_model(raw, piece)
        if turn:
            counterrotate_model(raw, turn, piece["p3d"])
    digests = _plan_digests(plan)
    for path, key in ((tile_manifest, "input_manifest_sha256"),
                      (texture_manifest, "source_texture_manifest_sha256"),
                      (tex_policy, "native_tex_policy_sha256"),
                      (tile_preview_manifest, "native_tile_preview_manifest_sha256")):
        if path is not None and _sha_file(path) != digests[key]:
            raise RouteBuildError("Source manifest or policy changed during preflight: " + key)
    if route_json is not None and _route_digest(route_json) != route_sha:
        raise RouteBuildError("Checkpoint route changed during preflight")
    if any(_sha_file(path) != void_digests[folder + "/" + Path(path).name]
           for path, folder in ((void_cfl, "tiles"), (void_p3d, "models"))
           if path is not None):
        raise RouteBuildError("Void source changed during preflight")
    return {
        "macro_layout": macro_layout,
        "void_source_digests": void_digests,
        "void_cell_count": plan.get("void_cell_count", 0),
        "status": "PRIVATE OFFLINE PREFLIGHT ONLY; NATIVE GAME NOT VERIFIED",
        "source_tiles": plan["tile_count"],
        "occupied_cells": plan["occupied_cell_count"],
        "grid_cells": [width, height],
        "ordered_checkpoint_indices": ordered,
        "checkpoint_count": len(ordered),
        "checkpoint_mode": checkpoint_mode,
        "source_manifest_sha256": digests["input_manifest_sha256"],
        "route_sha256": route_sha,
        "source_plan_digests": digests,
        "checkpoint_native_turns": [entry["rotation"] for entry in route],
        "native_tile_preview_count": len(plan.get("native_tile_previews", [])),
        "scenery_profile": scenery_profile,
        "game_mode_mask": game_mode_mask,
        "vertical_mode": vertical_mode,
    }


def build_route_world(tile_manifest, route_json, label, texture_manifest=None,
                      tex_policy=None, tile_preview_manifest=None,
                      vertical_mode="shared_source_z",
                      scenery_profile="highlands", workspace=None, game_mode_mask=255,
                      expected_preflight=None, checkpoint_mode="AUTHORED",
                      macro_layout=False, void_cfl=None, void_p3d=None):
    """Create new private P3D+CFL+TRK and provenance. Fail closed and rollback.

    Source and route must be unchanged from the read-only preflight. Stages
    and final targets are created exclusively; only this call's files are
    removed after a failure. Standard mode requires full logical coverage;
    macro mode requires full physical coverage and explicit native void inputs
    for uncovered logical cells. Actual collision remains unverified.
    """
    _label(label)
    if vertical_mode not in ("shared_source_z", "native_unmodified"):
        raise RouteBuildError("Unsupported vertical mode")
    source = Path(tile_manifest)
    route_file = Path(route_json) if route_json is not None else None
    before = inspect_route_world(
        source, route_file, texture_manifest, tex_policy, tile_preview_manifest,
        scenery_profile, vertical_mode, game_mode_mask=game_mode_mask,
        checkpoint_mode=checkpoint_mode, macro_layout=macro_layout,
        void_cfl=void_cfl, void_p3d=void_p3d)
    if expected_preflight is not None and before != expected_preflight:
        raise RouteBuildError("Source or route changed since Blender preflight; refresh and retry")
    # Explicit, bounded private root allows the writer to be embedded in a
    # standalone Blender add-on without writing to its installed source folder.
    root = native._private_output_root(workspace)
    stage_label = label + "_stage_internal"
    stage_target, output_target = root / stage_label, root / label
    if any(x.exists() or x.is_symlink() for x in (stage_target, output_target)):
        raise RouteBuildError("Private stage or output already exists; refusing overwrite")
    stage_owned = False
    final_owned = False
    final_identity = None
    try:
        stage, plan = stage_tiles(
            source, stage_label, workspace=root,
            texture_manifest=texture_manifest, tex_policy=tex_policy,
            tile_preview_manifest=tile_preview_manifest, macro_layout=macro_layout)
        stage_owned = True
        if (_plan_digests(plan) != before["source_plan_digests"] or
                _sha_file(source) != before["source_manifest_sha256"] or
                (route_file is not None and _route_digest(route_file) != before["route_sha256"])):
            raise RouteBuildError("Source manifest, policy or checkpoint route changed after preflight")
        result, summary = native.write_private_track(
            stage, label, vertical_mode=vertical_mode,
            scenery_profile=scenery_profile, checkpoint_route_json=route_file,
            workspace=root, game_mode_mask=game_mode_mask,
            test_drive_start=checkpoint_mode == "STOCK", void_cfl=void_cfl,
            void_p3d=void_p3d)
        final_owned = True
        final_identity = result.stat().st_ino
        if (summary["void_cells"] != before["void_cell_count"] or
                summary["tile_dimensions"] != before["grid_cells"]):
            raise RouteBuildError("Native grid or void count changed during build")
        for path, folder in ((void_cfl, "tiles"), (void_p3d, "models")):
            if path is not None:
                key = folder + "/" + Path(path).name
                expected = before["void_source_digests"][key]
                output_asset = result / "content" / folder / Path(path).name
                if _sha_file(output_asset) != expected or _sha_file(path) != expected:
                    raise RouteBuildError("Void source changed since preflight: " + key)
        if summary["game_mode_mask"] != before["game_mode_mask"]:
            raise RouteBuildError("Game-mode permission changed during build")
        if (summary["ordered_checkpoint_indices"] != before["ordered_checkpoint_indices"] or
                summary["authored_checkpoint_rotation_values"] != before["checkpoint_native_turns"]):
            raise RouteBuildError("Checkpoint order or rotation changed during build")
        if result != output_target:
            raise RouteBuildError("Native writer returned unexpected destination")
        stage_data = (stage / "world_tile_stage.json").read_bytes()
        receipt = {
            "schema": "micas-crashday-route-world-build/v1",
            "status": "PRIVATE OFFLINE BUILD; NATIVE GAME LOAD/COLLISION NOT VERIFIED",
            "source_manifest_sha256": before["source_manifest_sha256"],
            "checkpoint_route_sha256": before["route_sha256"],
            "checkpoint_mode": checkpoint_mode,
            "macro_layout": macro_layout,
            "void_cell_count": before["void_cell_count"],
            "void_source_digests": before["void_source_digests"],
            "source_plan_digests": before["source_plan_digests"],
            "source_tiles": plan["tile_count"],
            "world_grid_cells": [*summary["tile_dimensions"]],
            "checkpoint_count": len(before["ordered_checkpoint_indices"]),
            "ordered_checkpoint_indices": before["ordered_checkpoint_indices"],
            "checkpoint_native_turns": summary["authored_checkpoint_rotation_values"],
            "checkpoint_cfl_rewrites": summary["checkpoint_cfl_source_rewrites"],
            "checkpoint_model_counterrotations": summary["checkpoint_model_counterrotations"],
            "trk_sha256": summary["trk_sha256"],
            "vertical_mode": vertical_mode,
            "scenery_profile": scenery_profile,
            "game_mode_mask": summary["game_mode_mask"],
            "native_tile_preview_count": before["native_tile_preview_count"],
            "no_cat": True,
            "authored_cfl_autolist": 0,
        }
        with (result / "world_tile_stage.json").open("xb") as sink:
            sink.write(stage_data)
        # Keep the exact authoring route beside the receipt, so the private
        # build is reproducible even if a temporary GUI input is later deleted.
        route_copy = None
        if route_file is not None:
            route_copy = result / "AUTHORED_CHECKPOINT_ROUTE.json"
            with route_file.open("rb") as reader, route_copy.open("xb") as sink:
                shutil.copyfileobj(reader, sink, length=1024 * 1024)
        receipt["route_json"] = route_copy.name if route_copy is not None else None
        receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("ascii")
        with (result / "WORLD_BUILD_RECEIPT.json").open("xb") as sink:
            sink.write(receipt_bytes)
        if ((result / "world_tile_stage.json").read_bytes() != stage_data or
                (result / "WORLD_BUILD_RECEIPT.json").read_bytes() != receipt_bytes or
                (route_copy is not None and
                 (_sha_file(route_copy) != before["route_sha256"] or
                  _sha_file(route_file) != before["route_sha256"]))):
            raise RouteBuildError("Written output or checkpoint route changed during build")
        return result, receipt
    except Exception:
        if (final_owned and output_target.is_dir() and not output_target.is_symlink()
                and output_target.stat().st_ino == final_identity):
            shutil.rmtree(str(output_target))
        raise
    finally:
        if stage_owned and stage_target.is_dir() and not stage_target.is_symlink():
            shutil.rmtree(str(stage_target))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-manifest", required=True)
    route_options = parser.add_mutually_exclusive_group(required=True)
    route_options.add_argument("--checkpoint-route-json")
    route_options.add_argument("--no-checkpoints", action="store_true")
    route_options.add_argument("--test-drive-start", action="store_true")
    parser.add_argument("--label", default="checkpoint_world")
    parser.add_argument("--texture-manifest")
    parser.add_argument("--tex-policy")
    parser.add_argument("--tile-preview-manifest")
    parser.add_argument("--macro-layout", action="store_true",
                        help="Use oversized physical regions and centered native anchors")
    parser.add_argument("--void-cfl", help="Explicit private 1x1 GROUND_VOID CFL source")
    parser.add_argument("--void-p3d", help="Explicit private model referenced by void CFL")
    parser.add_argument("--vertical-mode",
                        choices=("shared_source_z", "native_unmodified"),
                        default="shared_source_z")
    parser.add_argument("--scenery-profile",
                        choices=("highlands", "racetrack", "alps"),
                        default="highlands")
    parser.add_argument("--game-mode-mask", type=lambda v: int(v, 0), default=255,
                        help="Native track permission byte (0..255)")
    parser.add_argument("--output-root",
                        help="Existing PRIVATE output directory (never Steam/game); "
                             "defaults to project out/")
    parser.add_argument("--write", action="store_true",
                        help="Create only a NEW private output-root/<label>, never a game path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        _label(args.label)
        opts = (args.tile_manifest, args.checkpoint_route_json)
        kw = dict(texture_manifest=args.texture_manifest, tex_policy=args.tex_policy,
                  tile_preview_manifest=args.tile_preview_manifest,
                  scenery_profile=args.scenery_profile, game_mode_mask=args.game_mode_mask,
                  macro_layout=args.macro_layout, void_cfl=args.void_cfl,
                  void_p3d=args.void_p3d,
                  checkpoint_mode=("STOCK" if args.test_drive_start else
                                   "NONE" if args.no_checkpoints else "AUTHORED"))
        if args.write:
            destination, data = build_route_world(
                *opts, args.label, vertical_mode=args.vertical_mode,
                workspace=args.output_root, **kw)
            data = dict(data, private_directory=str(destination))
        else:
            if args.output_root is not None:
                native._private_output_root(args.output_root)
            data = inspect_route_world(*opts, vertical_mode=args.vertical_mode, **kw)
        print(json.dumps(data, indent=2, sort_keys=True) if args.json
              else "{}: {} checkpoint(s); NO NATIVE GAME VALIDATION".format(
                  "PRIVATE BUILD" if args.write else "DRY RUN",
                  data["checkpoint_count"]))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("PRIVATE WORLD BUILD REFUSED: {}".format(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
