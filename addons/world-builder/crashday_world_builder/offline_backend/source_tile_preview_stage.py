"""Fail-closed staging of Blender-rendered native per-CFL palette TGA previews.

Only genuine, hash-verified 20/40m footprint preview sidecars may accompany an
existing P3D tile export in a private offline World Builder output. Not a game
runtime loader; does not create or infer images or require modifying Steam.
Python 3.7 stdlib; no Blender dependency.
"""
import hashlib
import json
from pathlib import Path
import re

from .native_tile_preview import tile_preview_path, validate_tga

SCHEMA = "micas-crashday-native-tile-previews/v1"
RENDER_PREFIX = "native-ortho-64-per-20m-eevee-standard-v1-blender-"
EXACT_RENDER_PREFIX = "native-ortho-64-per-20m-eevee-standard-exact-fit-v2-blender-"

def _supported_contract(contract, fields):
    if not isinstance(contract, str) or len(contract) > 100:
        return False
    if contract.startswith(EXACT_RENDER_PREFIX):
        return True
    # Legacy v1 portrait previews had a demonstrably incorrect crop.
    # Retain separately corrected macro previews and unaffected sizes.
    return (contract.startswith(RENDER_PREFIX) and
            (tuple(fields) != (1, 2) or contract.endswith("-exact-fit")))

def _requires_binding(contract):
    return contract.startswith(EXACT_RENDER_PREFIX) or contract.endswith("-exact-fit")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_TOTAL_IMAGE_BYTES = 256 * 1024 * 1024
MAX_IMAGE_BYTES = 65580


class PreviewStageError(ValueError):
    pass


def file_sha(path):
    """Bounded streaming SHA-256; source file is never modified."""
    sha = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _safe_file(file, base, max_size):
    """Refuse a symlink or junction escape even beneath a nominally safe root."""
    path = Path(file)
    folder = Path(base)
    if (not path.is_file() or path.is_symlink() or
            not folder.is_dir() or folder.is_symlink() or
            path.resolve(strict=True).parent != folder.resolve(strict=True)):
        raise PreviewStageError("Missing, redirected or outside approved preview source: " + str(path))
    size = path.stat().st_size
    if not 1 <= size <= max_size:
        raise PreviewStageError("Preview source size exceeds safety limit: " + str(path))
    return size


def validate_preview_manifest(tile_manifest, preview_manifest, tile_entries):
    """Return full preview records only for exactly the matching CFL tiles.

    This validates names, dimensions, source hashes and renderer cache identity.
    Optional newer P3D bindings independently attest the EXACT exported P3D
    bytes and P3D manifest digest. Historical unbound v0.4.6 manifests remain
    supported but must not be presented as geometry-equivalent evidence.
    """
    tile_file = Path(tile_manifest)
    sidecar = Path(preview_manifest)
    if sidecar.name != "tile_previews_manifest.json":
        raise PreviewStageError("Expected the Tile Splitter native preview manifest")
    # Match exact root, do not accept a preview manifest from another scene.
    if tile_file.parent.resolve(strict=True) != sidecar.parent.resolve(strict=True):
        raise PreviewStageError("Preview manifest must accompany the SAME P3D export folder")
    manifest_size = _safe_file(sidecar, tile_file.parent, MAX_MANIFEST_BYTES)
    raw = sidecar.read_bytes()
    if len(raw) != manifest_size:
        raise PreviewStageError("Preview manifest changed while reading")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise PreviewStageError("Malformed preview manifest") from exc
    found = document.get("tiles") if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get("schema") != SCHEMA or not isinstance(found, dict):
        raise PreviewStageError("Unknown preview manifest schema")
    if not 1 <= len(found) <= MAX_ENTRIES or len(found) != len(tile_entries):
        raise PreviewStageError("Missing/extra previews; every authored CFL needs exactly one")
    expected = {}
    for entry in tile_entries:
        cfl = entry["file"][:-4] + ".cfl"
        name = tile_preview_path(cfl)
        bounds = entry["logical_tile_bounds_xy"]
        fx, fy = round((bounds[1] - bounds[0]) / 20), round((bounds[3] - bounds[2]) / 20)
        if name in expected:
            raise PreviewStageError("Duplicate tile preview identity")
        expected[name] = (entry["key"], int(fx), int(fy))
    if set(expected) != set(found):
        raise PreviewStageError("Preview names differ from authored CFL names")
    base = tile_file.parent
    tile_manifest_sha = file_sha(tile_file)
    expected_p3d = {item["file"][:-4] + ".cfl": item["sha256"]
                    for item in tile_entries}
    result = []
    total = 0
    for name in sorted(found):
        original = found[name]
        if not isinstance(original, dict):
            raise PreviewStageError("Malformed preview record")
        key, fx, fy = expected[name]
        digest, source_key = original.get("sha256"), original.get("source_key")
        contract = original.get("render_contract")
        if (original.get("tile") != key or
                original.get("fields") != [fx, fy] or
                not isinstance(digest, str) or not DIGEST.fullmatch(digest) or
                not isinstance(source_key, str) or not DIGEST.fullmatch(source_key) or
                not _supported_contract(contract, (fx, fy))):
            raise PreviewStageError("Preview identity, dimensions or cache signature mismatch: " + name)
        rel = name.split("/")
        parent = base.joinpath(*rel[:-1])
        path = parent / rel[-1]
        size = _safe_file(path, parent, MAX_IMAGE_BYTES)
        data = path.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise PreviewStageError("Preview TGA changed after export: " + name)
        proof = validate_tga(data, fx, fy)
        if proof["sha256"] != digest:
            raise PreviewStageError("Unrecognized native preview image digest")
        bound_sha, bound_manifest = (original.get("p3d_sha256"),
                                     original.get("p3d_manifest_sha256"))
        bound_present = ("p3d_sha256" in original or
                         "p3d_manifest_sha256" in original)
        if _requires_binding(contract) and not bound_present:
            raise PreviewStageError("Exact-fit preview requires P3D binding: " + name)
        if bound_present and (
                not isinstance(bound_sha, str) or not DIGEST.fullmatch(bound_sha) or
                not isinstance(bound_manifest, str) or
                not DIGEST.fullmatch(bound_manifest) or
                bound_sha != expected_p3d[name.split("/")[-1][:-4] + ".cfl"] or
                bound_manifest != tile_manifest_sha):
            raise PreviewStageError("Preview/P3D source attestation mismatch: " + name)
        total += size
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise PreviewStageError("Preview TGA batch exceeds private safety budget")
        record = {"tile": key, "path": name, "fields": [fx, fy],
                  "sha256": digest, "bytes": size,
                  "source_key": source_key, "render_contract": contract}
        if bound_present:
            record.update(p3d_sha256=bound_sha,
                          p3d_manifest_sha256=bound_manifest)
        result.append(record)
    return {"records": result, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "total_bytes": total}


def verify_staged_previews(stage_root, plan):
    """Verify stage byte identity before copying to final private world."""
    entries = plan.get("native_tile_previews")
    if entries is None:
        if any(k in plan for k in ("native_tile_preview_manifest_sha256",
                                  "native_tile_preview_bytes")):
            raise PreviewStageError("Partial native preview stage metadata")
        return []
    if (not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES or
            len(entries) != len(plan.get("pieces", [])) or
            not isinstance(plan.get("native_tile_preview_manifest_sha256"), str) or
            not DIGEST.fullmatch(plan["native_tile_preview_manifest_sha256"])):
        raise PreviewStageError("Malformed staged preview provenance")
    root = Path(stage_root)
    pairs = {(p["tile_key"], p["cfl"]): p["logical_cell_footprint"]
             for p in plan["pieces"]}
    seen, result, total = set(), [], 0
    for rec in entries:
        if not isinstance(rec, dict):
            raise PreviewStageError("Malformed staged preview entry")
        name = rec.get("path")
        if not isinstance(name, str):
            raise PreviewStageError("Missing preview filename")
        matching = [cfl for tile, cfl in pairs if tile == rec.get("tile")]
        if len(matching) != 1 or tile_preview_path(matching[0]) != name:
            raise PreviewStageError("Staged preview is not one of the authored tiles")
        fields = pairs[(rec["tile"], matching[0])]
        if rec.get("fields") != fields or name in seen:
            raise PreviewStageError("Duplicate, mismatched native preview footprint")
        digest, size = rec.get("sha256"), rec.get("bytes")
        if (not isinstance(digest, str) or not DIGEST.fullmatch(digest) or
                type(size) is not int or not 18 <= size <= MAX_IMAGE_BYTES or
                not isinstance(rec.get("source_key"), str) or
                not DIGEST.fullmatch(rec["source_key"]) or
                not _supported_contract(rec.get("render_contract"), fields)):
            raise PreviewStageError("Malformed staged preview digests or cache identity")
        has_p3d_binding = ("p3d_sha256" in rec or
                           "p3d_manifest_sha256" in rec)
        if _requires_binding(rec["render_contract"]) and not has_p3d_binding:
            raise PreviewStageError("Exact-fit staged preview requires P3D binding")
        if has_p3d_binding:
            matching_piece = [p for p in plan["pieces"]
                              if p["tile_key"] == rec["tile"]]
            if (len(matching_piece) != 1 or
                    rec.get("p3d_sha256") != matching_piece[0]["p3d_sha256"] or
                    rec.get("p3d_manifest_sha256") != plan.get("input_manifest_sha256")):
                raise PreviewStageError("Staged preview/P3D binding mismatch")
        parts = name.split("/")
        folder = root.joinpath(*parts[:-1])
        path = folder / parts[-1]
        if _safe_file(path, folder, MAX_IMAGE_BYTES) != size or file_sha(path) != digest:
            raise PreviewStageError("Staged native preview missing or modified: " + name)
        with path.open("rb") as f:
            validate_tga(f.read(), fields[0], fields[1])
        result.append((path, rec))
        seen.add(name)
        total += size
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise PreviewStageError("Staged native preview byte count exceeds safe maximum")
    if total != plan.get("native_tile_preview_bytes"):
        raise PreviewStageError("Staged native preview byte total is inconsistent")
    return result
