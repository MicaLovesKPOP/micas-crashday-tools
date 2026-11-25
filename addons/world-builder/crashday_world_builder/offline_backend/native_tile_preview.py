"""Crashday native top-down track-tile preview TGA contract (pure Python 3.7).

Source truth: all 267 stock dat006.cpk content/textures/pictures/tiles/*.tga
use an uncompressed lower-left-origin TGA with 64 pixels per 20m CFL field.
Previews are separate from full-track CDTR2 embedded screenshots and from
P3D diffuse textures. This module does NOT invent Blender camera/rendering
or modify game data; it validates or encodes already-rendered pixels.
"""
import hashlib
import json
import re
import struct

PIXELS_PER_FIELD = 64
HEADER_FORMAT = "<BBBHHBHHHHBB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
TRUEVISION_FOOTER = b"\0" * 8 + b"TRUEVISION-XFILE.\0"
SAFE_CFL = re.compile(r"[a-z0-9_-]{1,80}\.cfl\Z")


class TilePreviewError(ValueError):
    pass


def tile_preview_path(cfl):
    """The actual native resource path, not a new convention."""
    if not isinstance(cfl, str) or not SAFE_CFL.fullmatch(cfl):
        raise TilePreviewError("Unsafe/unsupported CFL preview identity")
    return "content/textures/pictures/tiles/" + cfl[:-4] + ".tga"


def expected_size(fields_x, fields_y):
    if type(fields_x) is not int or type(fields_y) is not int:
        raise TilePreviewError("Native CFL dimensions must be integers")
    if fields_x not in (1, 2) or fields_y not in (1, 2):
        raise TilePreviewError("Preview supports verified native 1x1, 1x2, 2x1, 2x2 CFL cells only")
    return PIXELS_PER_FIELD * fields_x, PIXELS_PER_FIELD * fields_y


def validate_tga(data, fields_x, fields_y):
    """Check precise stock-native palette-TGA framing, dimensions and footer."""
    width, height = expected_size(fields_x, fields_y)
    if not isinstance(data, bytes) or len(data) < HEADER_SIZE:
        raise TilePreviewError("Truncated or non-bytes native tile preview")
    (id_size, cmap, kind, cmap_start, cmap_len, cmap_bpp,
     x_origin, y_origin, w, h, depth, flags) = struct.unpack_from(HEADER_FORMAT, data)
    if ((id_size, cmap, kind, cmap_start, cmap_len, cmap_bpp,
         x_origin, y_origin) != (0, 0, 2, 0, 0, 0, 0, 0)
            or w != width or h != height or depth not in (24, 32)
            or flags not in (0, 8)):
        raise TilePreviewError("Not a stock-shaped uncompressed bottom-origin tile-preview TGA")
    if depth == 24 and flags != 0:
        raise TilePreviewError("24-bit native tile preview cannot declare alpha bits")
    raster_size = w * h * (depth // 8)
    tail = data[HEADER_SIZE + raster_size:]
    if len(data) < HEADER_SIZE + raster_size or tail not in (b"", TRUEVISION_FOOTER):
        raise TilePreviewError("Native tile-preview TGA has invalid pixels or unsupported footer")
    return {"width": width, "height": height, "bits": depth,
            "footer_bytes": len(tail),
            "sha256": hashlib.sha256(data).hexdigest()}


def encode_rgba_preview(pixels, fields_x, fields_y, with_footer=True):
    """Encode top-to-bottom RGBA8 pixels into stock-compatible bottom-up BGRA TGA.

    Intended as a small output adapter for a future ortho Blender image render.
    Pixels are RGB + alpha in display row order; never infer landscape textures.
    """
    width, height = expected_size(fields_x, fields_y)
    if not isinstance(pixels, bytes) or len(pixels) != width * height * 4:
        raise TilePreviewError("Top-down preview RGBA raster must match exact native resolution")
    header = struct.pack(HEADER_FORMAT, 0, 0, 2, 0, 0, 0, 0, 0,
                         width, height, 32, 8)
    result = bytearray(header)
    row_bytes = width * 4
    for row in range(height - 1, -1, -1):
        chunk = pixels[row * row_bytes:(row + 1) * row_bytes]
        for offset in range(0, row_bytes, 4):
            red, green, blue, alpha = chunk[offset:offset + 4]
            result.extend((blue, green, red, alpha))
    if with_footer:
        result.extend(TRUEVISION_FOOTER)
    payload = bytes(result)
    validate_tga(payload, fields_x, fields_y)
    return payload


def preview_content_key(p3d_sha256, art_sha256s, fields_x, fields_y,
                        render_contract="overhead-blender-ortho-v1"):
    """Stable cache ID for one logical tile and its actual image dependencies."""
    expected_size(fields_x, fields_y)
    keys = [p3d_sha256] + list(art_sha256s)
    if (not isinstance(render_contract, str) or not render_contract
            or any(not isinstance(v, str) or
                   not re.fullmatch(r"[0-9a-f]{64}", v) for v in keys)):
        raise TilePreviewError("Source/content cache key requires valid SHA-256 digests")
    payload = {"p3d": p3d_sha256, "images": sorted(keys[1:]),
               "fields": [fields_x, fields_y], "renderer": render_contract}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                    separators=(",", ":")).encode("ascii")).hexdigest()
