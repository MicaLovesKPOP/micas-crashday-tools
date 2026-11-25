"""Verify Blender source-image TGA sidecars for private, offline World Builder stages.

Accept only the Tile Splitter's actual source-image manifest. This copies art,
not native game .tex material descriptors. No Steam/game directory writes.
Python 3.7+, standard library only, with path traversal/symlink/size/hash guards.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

SCHEMA = "micas-crashday-source-tga/v1"
NATIVE_ID = re.compile(r"[a-z0-9_-]{1,80}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 128 * 1024 * 1024 + 1024
MAX_BATCH_BYTES = 1024 * 1024 * 1024


class SourceTGAError(ValueError):
    pass


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for part in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _native_refs(tile_manifest):
    refs = set()
    for piece in tile_manifest["p3d_files"]:
        textures = piece.get("textures")
        if not isinstance(textures, list):
            raise SourceTGAError("Original tile entry missing native P3D texture names")
        for name in textures:
            if not isinstance(name, str) or not NATIVE_ID.fullmatch(name):
                raise SourceTGAError("Unsafe original P3D texture identifier")
            if name != "colwhite":
                refs.add(name)
    return refs


def verify_source_tga_manifest(tile_manifest_path, texture_manifest_path):
    """Return manifest-bound, verified TGA descriptors and uncovered native IDs.

    Manifest must be beside tile P3Ds (one authoring batch) to prevent a
    similarly named but unrelated texture folder from being silently merged.
    Reject aliasing, path redirection, invalid TGA image data or hash drift.
    """
    tile_path = Path(tile_manifest_path).resolve(strict=True)
    tex_path = Path(texture_manifest_path)
    if tex_path.is_symlink() or not tex_path.is_file():
        raise SourceTGAError("Missing or redirected TGA sidecar manifest")
    tex_path = tex_path.resolve(strict=True)
    if tex_path.parent != tile_path.parent:
        raise SourceTGAError("TGA manifest must share the source P3D manifest directory")
    if tex_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise SourceTGAError("TGA manifest is too large")
    payload = tex_path.read_bytes()
    try:
        source = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SourceTGAError("Malformed UTF-8 source TGA manifest") from exc
    if not isinstance(source, dict) or source.get("schema") != SCHEMA:
        raise SourceTGAError("Not the verified Tile Splitter source-TGA schema")
    images = source.get("images")
    if not isinstance(images, list) or len(images) > 8192:
        raise SourceTGAError("Invalid source image list")
    if type(source.get("native_texture_refs")) is not int or source["native_texture_refs"] != len(images):
        raise SourceTGAError("TGA declared image count disagrees with image list")
    native = _native_refs(json.loads(tile_path.read_text(encoding="utf-8")))
    folder = tex_path.parent / "textures"
    if images and (folder.is_symlink() or not folder.is_dir()):
        raise SourceTGAError("Source TGA directory missing or redirected")
    listed = set()
    total = 0
    records = []
    for row in images:
        if not isinstance(row, dict):
            raise SourceTGAError("Malformed TGA source record")
        name, relative = row.get("texture"), row.get("file")
        if (not isinstance(name, str) or not NATIVE_ID.fullmatch(name) or
                name == "colwhite" or name in listed):
            raise SourceTGAError("Duplicate or unsafe TGA native image identifier")
        if name not in native:
            raise SourceTGAError("TGA image not referenced by original P3D tiles: " + name)
        if relative != "textures/" + name + ".tga":
            raise SourceTGAError("TGA record path differs from strict native identifier")
        width, height, size = row.get("width"), row.get("height"), row.get("bytes")
        digest = row.get("sha256")
        if (type(width) is not int or type(height) is not int or
                not 1 <= width <= 65535 or not 1 <= height <= 65535 or
                type(size) is not int or not 18 < size <= MAX_IMAGE_BYTES or
                not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise SourceTGAError("Malformed TGA dimensions, size or SHA-256 for " + name)
        path = folder / (name + ".tga")
        if (path.is_symlink() or not path.is_file() or path.resolve().parent !=
                folder.resolve(strict=True)):
            raise SourceTGAError("Missing or redirected source TGA: " + name)
        if path.stat().st_size != size:
            raise SourceTGAError("Source TGA length differs from manifest: " + name)
        with path.open("rb") as src:
            header = src.read(18)
        if len(header) != 18:
            raise SourceTGAError("Truncated TGA header: " + name)
        ident, cmap, kind, _start, _length, _cmdepth, _x, _y, w, h, depth, _flag = struct.unpack(
            "<BBBHHBHHHHBB", header)
        payload_bytes = w * h * (depth // 8)
        if (ident != 0 or cmap != 0 or kind != 2 or depth not in (24, 32) or
                w != width or h != height or size < 18 + payload_bytes or
                size > 18 + payload_bytes + 1024):
            raise SourceTGAError("Invalid source TGA pixel extent or encoding: " + name)
        if _hash_file(path) != digest:
            raise SourceTGAError("Source TGA SHA-256 mismatch: " + name)
        total += size
        if total > MAX_BATCH_BYTES:
            raise SourceTGAError("TGA batch exceeds 1 GiB private limit")
        listed.add(name)
        records.append({"texture": name, "file": name + ".tga", "width": width,
                        "height": height, "bytes": size, "sha256": digest})
    unresolved = source.get("unresolved_native_refs")
    if (not isinstance(unresolved, dict) or
            any(not isinstance(name, str) or not NATIVE_ID.fullmatch(name) or
                type(count) is not int or count < 1 or name not in native
                for name, count in unresolved.items())):
        raise SourceTGAError("Invalid unresolved native texture reference list")
    if listed.intersection(unresolved):
        raise SourceTGAError("Source TGA is simultaneously resolved and unresolved")
    return {"records": sorted(records, key=lambda row: row["texture"]),
            "unresolved": sorted(native - listed),
            "native_refs": sorted(native),
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            "total_bytes": total}
