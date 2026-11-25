"""Create-only native anchor preview producer for World Builder macro tiles.

Uses the existing Tile Splitter renderer; no source mesh clipping, mutation,
or cache reuse. A fresh P3D serialization must match each exported model.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import viewport_routes


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _render_crop(renderer, objects, bounds, nx, ny, temp_path):
    """Use the frozen renderer's scene setup, correcting portrait camera fit."""
    scene = renderer._make_scene(objects, bounds, nx, ny, temp_path)
    try:
        width, height = bounds[1] - bounds[0], bounds[3] - bounds[2]
        camera = scene.camera.data
        camera.sensor_fit = "AUTO"
        camera.ortho_scale = max(width, height)
        frame = camera.view_frame(scene=scene)
        span_x = max(v.x for v in frame) - min(v.x for v in frame)
        span_y = max(v.y for v in frame) - min(v.y for v in frame)
        if abs(span_x - width) > 0.0002 or abs(span_y - height) > 0.0002:
            raise ValueError("Native preview camera does not cover the exact logical bounds")
        renderer.bpy.ops.render.render(scene=scene.name, write_still=True)
        if not temp_path.is_file():
            raise RuntimeError("Blender did not write the temporary preview image")
        return renderer._png_rgba_topdown(temp_path, 64 * nx, 64 * ny)
    finally:
        renderer._drop_scene(scene)


def generate(context, manifest, backend, renderer, exporter, expected_manifest_sha=None):
    """Render all active leaves into the same private export folder."""
    path = Path(manifest)
    root = exporter._directory_safe(path.parent)
    if path.parent.resolve(strict=True) != root or path.is_symlink():
        raise ValueError("Redirected source manifest")
    plan, _ = backend.plan_tiles(path, macro_layout=True)
    digest = plan["input_manifest_sha256"]
    if (expected_manifest_sha is not None and expected_manifest_sha != digest) or _sha(path.read_bytes()) != digest:
        raise ValueError("Export manifest changed")
    leaves, _ = viewport_routes.scene_leaf_tiles(context.scene.objects)
    groups = {}
    for obj in leaves:
        groups.setdefault(str(obj["crashday_tile_key"]), []).append(obj)
    pieces = plan["pieces"]
    if set(groups) != {p["tile_key"] for p in pieces}:
        raise ValueError("Active scene tiles must match the complete export manifest")
    sidecar = root / "tile_previews_manifest.json"
    pending = []
    if os.path.lexists(str(sidecar)):
        raise FileExistsError("Preview manifest already exists; use a fresh export folder")

    def verify(piece, objects):
        payload, audit = exporter.build_tile_p3d(
            objects, floor_z=piece["source_floor_z"])
        if (_sha(payload) != piece["p3d_sha256"] or
                _sha((root / piece["p3d"]).read_bytes()) != piece["p3d_sha256"] or
                any(abs(a-b) > 0.0002 for a,b in zip(
                    audit["logical_tile_bounds_xy"], piece["physical_bounds_world_xy"])) or
                any(abs(a-b) > 0.0002 for a,b in zip(
                    audit["main_world_center"], piece["source_main_world_center_xyz"]))):
            raise ValueError("Scene geometry or P3D changed; re-export " + piece["tile_key"])

    for piece in pieces:
        key = piece["tile_key"]
        objects = groups[key]
        verify(piece, objects)
        nx, ny = piece["logical_cell_footprint"]
        rel = renderer.native_tile_preview.tile_preview_path(piece["cfl"])
        dest = root.joinpath(*rel.split("/"))
        if exporter._directory_safe(dest.parent) != dest.parent:
            raise ValueError("Redirected preview destination")
        if os.path.lexists(str(dest)):
            raise FileExistsError("Existing preview: " + rel)
        pending.append((key, objects, piece["logical_bounds_world_xy"],
                        nx, ny, rel, dest, piece))
    renderer._check_source_images([row[:6] for row in pending])
    records, created = {}, []
    progress = getattr(context, "window_manager", None)
    if progress is not None:
        progress.progress_begin(0, len(pending))
    try:
        with tempfile.TemporaryDirectory(prefix=".macro_preview_", dir=str(root)) as tmp:
            for index, (key, objects, bounds, nx, ny, rel, dest, piece) in enumerate(pending):
                if progress is not None:
                    progress.progress_update(index)
                raw = _render_crop(renderer, objects, bounds, nx, ny,
                                           Path(tmp) / ("frame_%04d.png" % index))
                data = renderer.native_tile_preview.encode_rgba_preview(raw, nx, ny)
                proof = renderer.native_tile_preview.validate_tga(data, nx, ny)
                verify(piece, objects)
                contract = renderer.RENDER_CONTRACT + "-blender-" + renderer.bpy.app.version_string + "-exact-fit"
                # Content receipt only, not a reusable scene cache key.
                source_key = _sha(json.dumps([digest, piece["p3d_sha256"],
                    bounds, contract, proof["sha256"]], separators=(",", ":")).encode("ascii"))
                if exporter._directory_safe(dest.parent) != dest.parent:
                    raise ValueError("Redirected preview destination")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("xb") as f:
                    created.append(dest)
                    f.write(data)
                records[rel] = {"tile": key, "fields": [nx, ny],
                    "sha256": proof["sha256"], "source_key": source_key,
                    "render_contract": contract,
                    "crop_contract": "centered-native-anchor-v2",
                    "crop_bounds_world_xy": bounds,
                    "physical_bounds_world_xy": piece["physical_bounds_world_xy"],
                    "p3d_sha256": piece["p3d_sha256"],
                    "p3d_manifest_sha256": digest}
            if _sha(path.read_bytes()) != digest:
                raise ValueError("Export manifest changed during rendering")
            payload = (json.dumps({"schema": renderer.SCHEMA, "tiles": records},
                                  sort_keys=True, indent=2) + "\n").encode("utf-8")
            if len(payload) > renderer.MAX_MANIFEST_BYTES:
                raise ValueError("Preview manifest exceeds safety budget")
            with sidecar.open("xb") as f:
                created.append(sidecar)
                f.write(payload)
            # Independent downstream contract validation, before reporting success.
            checked, _ = backend.plan_tiles(path, tile_preview_manifest=sidecar,
                                             macro_layout=True)
            if checked["input_manifest_sha256"] != digest:
                raise ValueError("Source changed during final validation")
        return {"manifest": str(sidecar), "tiles": len(records)}
    except Exception:
        for file in reversed(created):
            try:
                file.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if progress is not None:
            progress.progress_end()
