"""Read original Crashday CDTRK and Redline CDTR2 track/world data.

CDTR2/TRK2 extends the familiar CDTRK world body with a counted block of
track-authored game.dbs lines. The game precedence was reported by the owner;
the importer preserves these strings but does not emulate game rules.
This requires no Blender, Crashday launch, or game source modifications.
Reference: installed owner CDTR2 files, FlyX MapParser.cs and Loader.cs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


class TrackFormatError(ValueError):
    """Track is truncated, invalid, or outside the parser's safety limits."""


# Conservative authoring-tool input cap, not a claimed Crashday engine limit.
# The actual track sample grid is separately capped at 8 million floats.
MAX_TRACK_FILE_BYTES = 128 * 1024 * 1024
# Upper bound for a checksummed 8-million-sample float32 height Text archive.
# Incompressible float32 data expands slightly under zlib, then by 4/3 for
# base64, plus our 76-character line wraps. Reject oversized input before
# allocating a potentially huge decoded/compressed stream.
MAX_HEIGHT_ARCHIVE_TEXT_CHARS = 48 * 1024 * 1024


@dataclass
class Track:
    path: Path
    author: str
    comment: str
    ambience: str
    style: int
    width: int
    height: int
    field_files: list[str]
    tiles: list[tuple[int, int, int, int]]
    dynamic_files: list[str]
    dynamics: list[tuple[int, float, float, float, float]]
    checkpoints: list[int]
    permission: int
    ground_bumpiness: float
    scenery: int
    heights: tuple[float, ...]
    trailing_bytes: int
    source_format: str = "CDTRK"
    embedded_game_dbs: tuple[str, ...] = ()
    embedded_preview_bgra: bytes = b""  # Verified 1024x1024 BGRA8 TRK2 preview, if present.

    @property
    def samples_x(self) -> int:
        return 4 * self.width + 1

    @property
    def samples_y(self) -> int:
        return 4 * self.height + 1


class Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.at = 0

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.at + size > len(self.data):
            raise TrackFormatError(f"Truncated at byte {self.at}: expected {size} bytes")
        values = struct.unpack_from(fmt, self.data, self.at)
        self.at += size
        return values

    def cstr(self) -> str:
        end = self.data.find(b"\0", self.at)
        if end == -1:
            raise TrackFormatError(f"Unterminated string at byte {self.at}")
        raw = self.data[self.at:end]
        self.at = end + 1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")


def read_track(path: str | Path) -> Track:
    path = Path(path)
    # Do not load arbitrarily large or unexpectedly growing user-supplied
    # tracks into memory before checking the CDTRK size constraints.
    with path.open("rb") as stream:
        data = stream.read(MAX_TRACK_FILE_BYTES + 1)
    if len(data) > MAX_TRACK_FILE_BYTES:
        raise TrackFormatError("Track exceeds safe input size limit (128 MiB)")
    reader = Cursor(data)
    source_magic = reader.data[:5]
    if source_magic not in (b"CDTRK", b"CDTR2"):
        raise TrackFormatError(f"Not a CDTRK/CDTR2 track: {path}")

    reader.at = 5
    reader.unpack("<i")  # legacy timestamp
    author = reader.cstr()
    comment = reader.cstr()
    for _ in range(20):
        reader.unpack("<i")  # legacy race/score entry
        reader.cstr()

    style, = reader.unpack("<B")
    ambience = reader.cstr()
    n_fields, = reader.unpack("<H")
    field_files = [reader.cstr() for _ in range(n_fields)]
    width, height = reader.unpack("<HH")
    count = (4 * width + 1) * (4 * height + 1)
    if not width or not height or count > 8_000_000:
        raise TrackFormatError(f"Unsupported or unsafe dimensions: {width} x {height}")

    tiles = [reader.unpack("<HBBB") for _ in range(width * height)]
    n_dynamic_files, = reader.unpack("<H")
    dynamic_files = [reader.cstr() for _ in range(n_dynamic_files)]
    n_dynamics, = reader.unpack("<H")
    dynamics = [reader.unpack("<Hffff") for _ in range(n_dynamics)]
    # A damaged/modded track can contain NaN or infinity in a placed
    # object's position or orientation. Reject before any Blender scene
    # mutation, rather than creating nonfinite transforms downstream.
    if any(not all(math.isfinite(value) for value in item[1:])
           for item in dynamics):
        raise TrackFormatError("Track contains nonfinite dynamic transforms")
    n_checkpoints, = reader.unpack("<H")
    checkpoints = [reader.unpack("<H")[0] for _ in range(n_checkpoints)]
    permission, = reader.unpack("<B")
    ground_bumpiness, = reader.unpack("<f")
    if not math.isfinite(ground_bumpiness):
        raise TrackFormatError("Track contains nonfinite ground bumpiness")
    scenery, = reader.unpack("<B")

    byte_count = count * 4
    if reader.at + byte_count > len(reader.data):
        raise TrackFormatError("Track heightmap is truncated")
    heights = tuple(sample[0] for sample in
                    struct.iter_unpack("<f", memoryview(reader.data)[reader.at:reader.at + byte_count]))
    reader.at += byte_count
    if not all(math.isfinite(h) for h in heights):
        raise TrackFormatError("Track heightmap contains nonfinite heights")

    embedded_game_dbs = ()
    if source_magic == b"CDTR2":
        # Verified against 35 genuine, read-only CDTR2 tracks: after the
        # ordinary source heightmap, a little-endian uint32 gives the count
        # of NUL-terminated game.dbs override lines. Three also contain an
        # unidentified 4 MiB payload AFTER these lines. Never invent its role.
        n_overrides, = reader.unpack("<I")
        if n_overrides > 10000 or n_overrides > len(data) - reader.at:
            raise TrackFormatError("CDTR2 has unsafe embedded game.dbs count")
        entries = []
        for i in range(n_overrides):
            begin = reader.at
            # Guard before extracting a potentially malicious large string.
            end = data.find(b"\0", begin, min(len(data), begin + 16385))
            if end == -1:
                raise TrackFormatError(
                    "CDTR2 has missing/oversized game.dbs entry {}".format(i + 1))
            raw = data[begin:end]
            if not raw or any(x < 32 or x > 126 for x in raw):
                raise TrackFormatError(
                    "CDTR2 has invalid game.dbs text at entry {}".format(i + 1))
            entries.append(raw.decode("ascii"))
            reader.at = end + 1
        embedded_game_dbs = tuple(entries)

    # Three genuine Redline tracks include a headerless, upright 1024x1024
    # BGRA8 source screenshot after the override lines. Decode ONLY when the
    # exact byte extent and all-opaque alpha channel match verified evidence.
    # Unknown or damaged extensions remain unparsed, never treated as geometry.
    embedded_preview_bgra = b""
    preview_bytes = 1024 * 1024 * 4
    if source_magic == b"CDTR2" and len(data) - reader.at == preview_bytes:
        candidate = memoryview(data)[reader.at:]
        if all(alpha == 255 for alpha in candidate[3::4]):
            embedded_preview_bgra = candidate.tobytes()
            reader.at += preview_bytes

    return Track(path, author, comment, ambience, style, width, height,
                 field_files, tiles, dynamic_files, dynamics, checkpoints,
                 permission, ground_bumpiness, scenery, heights,
                 len(reader.data) - reader.at,
                 source_magic.decode("ascii"), embedded_game_dbs,
                 embedded_preview_bgra)


def bgra_preview_to_png(bgra):
    """Losslessly convert an original 1024x1024 BGRA8 screenshot to PNG.

    PNG rows are top-to-bottom, and its channels are RGBA; no flipping is
    needed. Uses only Python stdlib, including Blender 2.80's Python 3.7.
    """
    width = height = 1024
    stride = width * 4
    if len(bgra) != stride * height or any(a != 255 for a in bgra[3::4]):
        raise ValueError("Not a verified opaque 1024x1024 BGRA8 track preview")

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xffffffff))

    compressor = zlib.compressobj(level=6)
    stream = bytearray()
    for start in range(0, len(bgra), stride):
        row = bytearray(bgra[start:start + stride])
        row[0::4], row[2::4] = row[2::4], row[0::4]
        stream.extend(compressor.compress(bytes((0,)) + row))
    stream.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (bytes((137, 80, 78, 71, 13, 10, 26, 10)) +
            chunk(b"IHDR", header) + chunk(b"IDAT", bytes(stream)) +
            chunk(b"IEND", b""))


def encode_float32_height_grid(heights, nx, ny):
    """Archive original absolute float32 sample bits as portable Blender Text.

    A mesh stores *relative* heights and Blender coordinates undergo float32
    rounding. This independent, checksummed stream retains the source samples
    exactly for future uses, without relying on external game files.
    """
    count = nx * ny
    if nx < 1 or ny < 1 or count > 8_000_000 or len(heights) != count:
        raise ValueError("Invalid original height-grid dimensions")
    digest = hashlib.sha256()
    compressor = zlib.compressobj(level=6)
    compressed = bytearray()
    for offset in range(0, count, 16384):
        portion = heights[offset:offset + 16384]
        if not all(math.isfinite(v) for v in portion):
            raise ValueError("Original grid contains non-finite float32 heights")
        raw = struct.pack("<{}f".format(len(portion)), *portion)
        digest.update(raw)
        compressed.extend(compressor.compress(raw))
    compressed.extend(compressor.flush())
    encoded = base64.b64encode(bytes(compressed)).decode("ascii")
    wrapped = "\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76))
    text = (
        "CRASHDAY_FLOAT32_HEIGHT_GRID_V1\n"
        "width_samples={}\nheight_samples={}\nfloat32_bytes={}\n"
        "sha256={}\nencoding=zlib+base64\n\n"
    ).format(nx, ny, count * 4, digest.hexdigest())
    return text + wrapped + "\n"


def decode_float32_height_grid(text):
    """Validate and recover exact original source float32 values from Text.

    The archive can come from a third-party .blend. Check both the overall
    string and the dimension-specific encoded size BEFORE base64 allocation.
    """
    if not isinstance(text, str) or len(text) > MAX_HEIGHT_ARCHIVE_TEXT_CHARS:
        raise ValueError("Unsafe source-height-grid archive text size/type")
    try:
        # Blender Text can be copied/saved with Windows CRLF line endings.
        # Accept either separator without copying the full encoded body.
        separator = "\n\n" if "\n\n" in text else "\r\n\r\n"
        header, found, encoded = text.partition(separator)
        if not found:
            raise ValueError("Missing source-height-grid header separator")
        if len(header) > 2048:
            raise ValueError("Unsafe source-height-grid archive header")
        lines = header.splitlines()
        if lines[0] != "CRASHDAY_FLOAT32_HEIGHT_GRID_V1":
            raise ValueError("Unknown source-height-grid format")
        properties = dict(line.split("=", 1) for line in lines[1:])
        nx, ny = int(properties["width_samples"]), int(properties["height_samples"])
        count = nx * ny
        if nx < 1 or ny < 1 or count > 8_000_000:
            raise ValueError("Unsafe source-height-grid dimensions")
        expected = count * 4
        if int(properties["float32_bytes"]) != expected or properties["encoding"] != "zlib+base64":
            raise ValueError("Unexpected source-height-grid size/encoding")
        # zlib worst-case expansion is small; the allowance also covers our
        # normal 76-character line wrapping and CRLF reformats. An attacker
        # cannot pad a tiny four-float archive to megabytes of base64/space.
        compressed_bound = expected + expected // 1000 + 256
        b64_bound = 4 * ((compressed_bound + 2) // 3)
        if len(encoded) > b64_bound + b64_bound // 40 + 512:
            raise ValueError("Oversized source-height-grid encoded payload")
        data = base64.b64decode("".join(encoded.split()), validate=True)
        decomp = zlib.decompressobj()
        raw = decomp.decompress(data, expected + 1)
        if (len(raw) != expected or not decomp.eof
                or decomp.unused_data or decomp.unconsumed_tail
                or hashlib.sha256(raw).hexdigest() != properties["sha256"]):
            raise ValueError("Damaged source-height-grid compressed stream or checksum")
        return nx, ny, tuple(v[0] for v in struct.iter_unpack("<f", raw))
    except (KeyError, IndexError, TypeError, UnicodeError, OverflowError, zlib.error) as exc:
        raise ValueError("Invalid source-height-grid archive") from exc


def track_report(t: Track) -> dict:
    initial = t.heights[0]
    valid_tiles = [t.field_files[fid] for fid, _, _, _ in t.tiles if fid < len(t.field_files)]
    return {
        "source": str(t.path),
        "source_format": t.source_format,
        "embedded_game_dbs_count": len(t.embedded_game_dbs),
        "embedded_game_dbs_lines": list(t.embedded_game_dbs),
        "embedded_preview_bgra_bytes": len(t.embedded_preview_bgra),
        "embedded_preview_sha256": (hashlib.sha256(t.embedded_preview_bgra).hexdigest()
                                    if t.embedded_preview_bgra else None),
        "opaque_extension_bytes": t.trailing_bytes if t.source_format == "CDTR2" else 0,
        "dimensions_tiles": [t.width, t.height],
        "dimensions_m": [t.width * 20, t.height * 20],
        "samples": [t.samples_x, t.samples_y],
        "height_baseline_raw": initial,
        "height_range_m_assuming_native_scale": [
            min(t.heights) - initial, max(t.heights) - initial],
        "field_files_count": len(t.field_files),
        "placed_field_tiles": len(valid_tiles),
        "unique_field_tiles": dict(Counter(valid_tiles)),
        "placeholder_or_unknown_tile_ids": len(t.tiles) - len(valid_tiles),
        "mirrored_tile_cells": sum(bool(m) for _, _, m, _ in t.tiles),
        "dynamic_files": list(t.dynamic_files),
        "dynamic_instances": len(t.dynamics),
        "checkpoints": len(t.checkpoints),
        "style": t.style,
        "ambience": t.ambience,
        "scenery": t.scenery,
        "ground_bumpiness": t.ground_bumpiness,
        "trailing_bytes": t.trailing_bytes,
    }


def export_terrain_obj(t: Track, path: str | Path) -> None:
    """Produce a Blender-readable terrain OBJ, Z up, 5m sample spacing.

    Original row 0 lies along the top/north edge: Blender Y = 20*H - 5*row.
    Height is original float minus header's baseline sample, not 8-bit image.
    The final addon must verify world-coordinate agreement with placed P3D tiles.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    nx, ny, baseline = t.samples_x, t.samples_y, t.heights[0]
    with target.open("w", encoding="ascii", newline="\n") as file:
        file.write("# Original Crashday terrain, x/y in meters, z up\n"
                   "# height z = CDTRK_float_height - CDTRK_first_height\n"
                   "o CrashdayTerrain\n")
        for j in range(ny):
            for i in range(nx):
                z = t.heights[j * nx + i] - baseline
                file.write(f"v {i*5} {(ny-1-j)*5} {z:.9g}\n")
        for j in range(ny - 1):
            for i in range(nx - 1):
                a = j * nx + i + 1
                b, c, d = a + 1, a + nx, a + nx + 1
                # Correct upward-facing triangle winding for inverted file row Y.
                file.write(f"f {a} {c} {b}\n")
                file.write(f"f {b} {c} {d}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("track", type=Path, help="Read-only input .trk")
    ap.add_argument("--obj", type=Path, help="Optional Blender-readable terrain OBJ")
    ap.add_argument("--report", type=Path, help="Optional JSON diagnostics")
    args = ap.parse_args()
    track = read_track(args.track)
    report = track_report(track)
    if args.obj:
        export_terrain_obj(track, args.obj)
        report["terrain_obj"] = str(args.obj)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
