"""Read-only Crashday P3D2 structural validator; no Blender or game writes.

Purpose: detect corrupt/unsafe P3D authoring output before it reaches the game.
Not a substitute for actual game loading, collision or visual tests. The layout
is independently checked against original Wurunduk P3D parsing and read-only
stock Redline assets. Python 3.7+, stdlib only.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import zipfile

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_MESHES = 4096
MAX_LIGHTS = 4096
MAX_CSTRING = 512
MATERIALS = ("FLAT", "FLAT_METAL", "GOURAUD", "GOURAUD_METAL",
             "GOURAUD_METAL_ENV", "SHINING")


class InvalidP3D(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0
        self.placeholder_sections = []

    def read(self, size, what):
        if size < 0 or size > len(self.data) - self.offset:
            raise InvalidP3D("Truncated {} at byte 0x{:X}".format(what, self.offset))
        end = self.offset + size
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def unpack(self, fmt, what):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt), what))

    def text(self, what):
        end = self.data.find(b"\0", self.offset, min(len(self.data), self.offset + MAX_CSTRING + 1))
        if end < 0:
            raise InvalidP3D("Unterminated or oversized {} at byte 0x{:X}".format(what, self.offset))
        raw = self.read(end - self.offset + 1, what)[:-1]
        if not raw:
            raise InvalidP3D("Empty {} at byte 0x{:X}".format(what, self.offset))
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            raise InvalidP3D("Non-ASCII {} at byte 0x{:X}".format(what, self.offset)) from None

    def section(self, name):
        got = self.read(len(name), name + " tag")
        if got != name.encode("ascii"):
            raise InvalidP3D("Expected {!r} at byte 0x{:X}, found {!r}".format(
                name, self.offset - len(name), got))
        # ORIGINAL Redline P3Ds store the byte length of this section after
        # its four-byte size field. Wurunduk-derived add-ons conventionally
        # write the placeholder 1337 instead. Both are parseable, but only
        # measured source lengths are native-stock-conformant.
        length = self.unpack("<I", name + " section length")[0]
        return length, self.offset

    def check_section_size(self, name, marker, start, warnings):
        actual = self.offset - start
        if marker == actual:
            return
        if marker == 1337:
            self.placeholder_sections.append(name)
            return
        # Numerous genuine Workshop-authored P3Ds retain stale source section
        # lengths after texture/geometry edits. Wurunduk reads their tags and
        # structures correctly because it does not trust these lengths.
        # Expose the mismatch, but do not call parseable authored mods corrupt
        # without an actual game-loader result.
        warnings.append("{} section declares {} bytes but contains {} bytes; source-size mismatch, in-game tolerance unproven".format(
            name, marker, actual))


def _finite(values, label):
    if not all(math.isfinite(value) for value in values):
        raise InvalidP3D("Non-finite " + label)


def _extend_vertex_bounds(bounds, point):
    """Update a native XYZ AABB; includes intentionally unused helper vertices."""
    if bounds is None:
        return [list(point), list(point)]
    for axis in range(3):
        bounds[0][axis] = min(bounds[0][axis], point[axis])
        bounds[1][axis] = max(bounds[1][axis], point[axis])
    return bounds


def _vertex_bounds_report(bounds):
    if bounds is None:
        return None
    lo, hi = bounds
    return {"min": list(lo), "max": list(hi),
            "extent": [hi[axis] - lo[axis] for axis in range(3)]}


def validate_bytes(data, label="<memory>"):
    """Return a deterministic diagnostic report. Invalid input cannot raise.

    Definite structural defects are errors; unusual but possible authoring
    choices are warnings, never invented 'game physics limits'.
    """
    report = {
        "source": str(label), "bytes": len(data) if isinstance(data, bytes) else None,
        "sha256": hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else None,
        "ok": False, "errors": [], "warnings": [], "textures": [],
        "meshes": [], "lights": 0, "triangles": 0, "vertices": 0,
        "header_dimensions_xyz": None,
        "positioned_vertex_bounds_xyz": None,
        "coll_flagged_vertex_bounds_xyz": None,
    }
    try:
        if not isinstance(data, bytes):
            raise InvalidP3D("Expected binary bytes, not {}".format(type(data).__name__))
        if len(data) > MAX_FILE_BYTES:
            raise InvalidP3D("Input exceeds read-only 64 MiB per-file safety limit")
        r = Reader(data)
        magic = r.read(4, "P3D2 signature")
        if magic != b"P3D\x02":
            raise InvalidP3D("Unsupported P3D version/signature {!r}; only native P3D2 is validated".format(magic))
        dimensions = r.unpack("<3f", "P3D model dimensions")
        _finite(dimensions, "model dimensions")
        report["dimensions"] = list(dimensions)
        # Header is stored length, height, depth; published XYZ uses X,Y,Z.
        report["header_dimensions_xyz"] = [dimensions[0], dimensions[2], dimensions[1]]
        all_vertex_bounds = None
        coll_vertex_bounds = None
        tex_size, tex_start = r.section("TEX")
        texture_count = r.unpack("<B", "texture count")[0]
        textures = [r.text("texture filename") for _ in range(texture_count)]
        r.check_section_size("TEX", tex_size, tex_start, report["warnings"])
        if len(set(name.casefold() for name in textures)) != len(textures):
            report["warnings"].append("Duplicate case-insensitive native texture names")
        report["textures"] = textures
        light_size, light_start = r.section("LIGHTS")
        nlights = r.unpack("<H", "light count")[0]
        if nlights > MAX_LIGHTS:
            raise InvalidP3D("Excessive light count {}".format(nlights))
        report["lights"] = nlights
        for i in range(nlights):
            r.text("light {} name".format(i))
            numbers = r.unpack("<4fi3B", "light {} data".format(i))
            _finite(numbers[:4], "light {} transform/range".format(i))
        r.check_section_size("LIGHTS", light_size, light_start, report["warnings"])
        mesh_size, mesh_start = r.section("MESHES")
        nmeshes = r.unpack("<H", "mesh count")[0]
        if nmeshes > MAX_MESHES:
            raise InvalidP3D("Excessive mesh count {}".format(nmeshes))
        names = set()
        for mi in range(nmeshes):
            submesh_size, submesh_start = r.section("SUBMESH")
            name = r.text("mesh {} name".format(mi))
            if name.casefold() in names:
                report["warnings"].append("Repeated mesh name {!r}".format(name))
            names.add(name.casefold())
            flags, *transform = r.unpack("<I6f", "mesh {} flags/transform".format(mi))
            _finite(transform, "mesh {} transform".format(mi))
            infos = [r.unpack("<7H", "mesh {} material counts".format(mi))
                     for _ in range(texture_count)]
            nverts = r.unpack("<H", "mesh {} vertex count".format(mi))[0]
            mesh_local_bounds = None
            mesh_positioned_bounds = None
            # Native P3D stores X,Z,Y, as independently confirmed by the
            # Wurunduk reader. Its mesh.pos offset also stores X,Z,Y.
            # Include all vertices, including intentional invisible helper
            # corners used by large authored main/maincoll models.
            position_xyz = (transform[0], transform[2], transform[1])
            for vi in range(nverts):
                raw = r.unpack("<3f", "mesh {} vertex {}".format(mi, vi))
                _finite(raw, "mesh {} vertex {}".format(mi, vi))
                local = (raw[0], raw[2], raw[1])
                positioned = tuple(local[axis] + position_xyz[axis] for axis in range(3))
                mesh_local_bounds = _extend_vertex_bounds(mesh_local_bounds, local)
                mesh_positioned_bounds = _extend_vertex_bounds(mesh_positioned_bounds, positioned)
                all_vertex_bounds = _extend_vertex_bounds(all_vertex_bounds, positioned)
                if flags & 8:
                    coll_vertex_bounds = _extend_vertex_bounds(coll_vertex_bounds, positioned)
            npolys = r.unpack("<H", "mesh {} triangle count".format(mi))[0]
            nonfinite_uv_components = 0
            for fi in range(npolys):
                triangle = r.unpack("<H2fH2fH2f", "mesh {} triangle {}".format(mi, fi))
                for ci in (0, 3, 6):
                    if triangle[ci] >= nverts:
                        raise InvalidP3D("Mesh {!r} triangle {} references nonexistent vertex {}/{}".format(
                            name, fi, triangle[ci], nverts))
                nonfinite_uv_components += sum(not math.isfinite(value) for value in
                                               (triangle[1], triangle[2], triangle[4],
                                                triangle[5], triangle[7], triangle[8]))
            # Native material counts must partition the triangle array.
            # Wurunduk's reader trusts these offsets; bad offsets may crash it.
            expected_start = 0
            for ti, info in enumerate(infos):
                start = info[0]
                count = sum(info[1:])
                if start != expected_start:
                    raise InvalidP3D("Mesh {!r} texture {!r} starts at triangle {}, expected {}".format(
                        name, textures[ti], start, expected_start))
                if count > npolys - expected_start:
                    raise InvalidP3D("Mesh {!r} texture {!r} triangle counts exceed {} faces".format(
                        name, textures[ti], npolys))
                expected_start += count
            if texture_count and expected_start != npolys:
                raise InvalidP3D("Mesh {!r} material ranges cover {}/{} triangles".format(
                    name, expected_start, npolys))
            if not texture_count and npolys:
                report["warnings"].append("Mesh {!r}: faces but zero texture entries".format(name))
            if nonfinite_uv_components:
                # Four real NaN UV components occur in read-only original
                # dat006.cpk/rwtostr.p3d; they are not a malformed layout.
                # Warn rather than mislabel stock engine assets as unreadable.
                report["warnings"].append("Mesh {!r}: {} non-finite UV values; retain source data, verify texturing if edited".format(
                    name, nonfinite_uv_components))
            r.check_section_size("SUBMESH {!r}".format(name), submesh_size,
                                 submesh_start, report["warnings"])
            report["meshes"].append({
                "name": name, "flags": flags, "collidable_flag": bool(flags & 8),
                "vertices": nverts, "triangles": npolys,
                "nonfinite_uv_values": nonfinite_uv_components,
                "position_xyz_native": [transform[0], transform[2], transform[1]],
                "bounds_xyz_native": [transform[3], transform[5], transform[4]],
                "local_vertex_bounds_xyz": _vertex_bounds_report(mesh_local_bounds),
                "positioned_vertex_bounds_xyz": _vertex_bounds_report(mesh_positioned_bounds),
            })
            report["vertices"] += nverts
            report["triangles"] += npolys
        r.check_section_size("MESHES", mesh_size, mesh_start, report["warnings"])
        report["positioned_vertex_bounds_xyz"] = _vertex_bounds_report(all_vertex_bounds)
        report["coll_flagged_vertex_bounds_xyz"] = _vertex_bounds_report(coll_vertex_bounds)
        user_size, user_start = r.section("USER")
        user_bytes = r.unpack("<i", "user data length")[0]
        if user_bytes < 0:
            raise InvalidP3D("Negative USER data length {}".format(user_bytes))
        r.read(user_bytes, "USER data")
        r.check_section_size("USER", user_size, user_start, report["warnings"])
        report["user_data_bytes"] = user_bytes
        # Stock Redline P3Ds use BOTH exact byte-length sections and Wurunduk
        # placeholder 1337 sections. Report their count, without declaring a
        # genuine shipped asset invalid or requiring a speculative rewrite.
        report["placeholder_size_sections"] = len(r.placeholder_sections)
        report["consumed_bytes"] = r.offset
        if r.offset < len(data):
            report["warnings"].append("{} trailing bytes after USER payload".format(len(data) - r.offset))
        if nmeshes == 0:
            report["warnings"].append("No meshes: model may be light-only or empty")
        report["ok"] = True
    except (InvalidP3D, struct.error, OverflowError, ValueError) as exc:
        report["errors"].append(str(exc))
    return report


def validate_path(path):
    """Never writes the source, never follows unbounded inputs into memory."""
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        return {"source": str(path), "ok": False,
                "errors": ["Input exceeds read-only 64 MiB per-file safety limit"],
                "warnings": []}
    with path.open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    return validate_bytes(data, label=str(path))


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_P3D_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_P3D_MEMBERS = 4096


def validate_archive(path):
    """Inspect P3Ds in a ZIP-format CPK or Workshop legacy BIN, in memory.

    Never extracts, modifies or publishes original game resources. Inspecting
    source bytes is limited by archive size, member count and expanded bytes.
    Other archive members remain completely untouched.
    """
    path = Path(path)
    result = {"source": str(path), "ok": False, "total": 0, "valid": 0,
              "invalid": 0, "warning_models": 0, "errors": [], "assets": []}
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise InvalidP3D("Archive exceeds 512 MiB read-only input cap")
        with zipfile.ZipFile(str(path), "r") as archive:
            entries = [info for info in archive.infolist()
                       if not info.is_dir() and info.filename.lower().endswith(".p3d")]
            if not entries:
                raise InvalidP3D("No .p3d members in ZIP-format package")
            if len(entries) > MAX_ARCHIVE_P3D_MEMBERS:
                raise InvalidP3D("Too many P3D members for bounded audit")
            names = [info.filename.casefold() for info in entries]
            if len(names) != len(set(names)):
                raise InvalidP3D("Ambiguous duplicate P3D member paths")
            if any(info.file_size > MAX_FILE_BYTES or info.file_size < 0 for info in entries):
                raise InvalidP3D("One or more P3D members exceed 64 MiB per-model limit")
            if sum(info.file_size for info in entries) > MAX_ARCHIVE_P3D_BYTES:
                raise InvalidP3D("P3D member uncompressed total exceeds 128 MiB cap")
            result["total"] = len(entries)
            for info in sorted(entries, key=lambda item: item.filename.casefold()):
                member = info.filename
                if any(ord(ch) < 32 for ch in member):
                    report = {"source": member, "ok": False,
                              "warnings": [], "errors": ["Unsafe control character in member name"]}
                else:
                    try:
                        with archive.open(info, "r") as source:
                            content = source.read(info.file_size + 1)
                        if len(content) != info.file_size:
                            raise InvalidP3D("ZIP uncompressed member size mismatch")
                        report = validate_bytes(content, label=member)
                    except (OSError, EOFError, RuntimeError, ValueError,
                            zipfile.BadZipFile) as exc:
                        report = {"source": member, "ok": False,
                                  "warnings": [], "errors": ["Unreadable P3D member: " + str(exc)]}
                result["assets"].append(report)
                result["valid" if report["ok"] else "invalid"] += 1
                result["warning_models"] += int(bool(report["warnings"]))
            result["ok"] = (result["valid"] == result["total"])
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        result["errors"].append(str(exc))
    return result


MAX_DIRECTORY_P3D_FILES = 4096
MAX_DIRECTORY_P3D_BYTES = 256 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 20000
MAX_DIRECTORY_DEPTH = 12


def validate_directory(path):
    """Read-only recursive audit of a loose P3D output tree.

    Scan ALL entries first, then inspect P3Ds only if the complete intended
    input set fits the resource budget. Symlinks/reparse-point escapes are
    refused instead of silently following someone else's data. Unlike
    --archive, unrelated textures/CFLs are not opened or validated.
    """
    path = Path(path)
    report = {"source": str(path), "ok": False, "total": 0, "valid": 0,
              "invalid": 0, "warning_models": 0, "total_p3d_bytes": 0,
              "errors": [], "assets": []}
    try:
        if path.is_symlink() or not path.is_dir():
            raise InvalidP3D("Directory missing or symbolic link: " + str(path))
        root = path.resolve(strict=True)
        root_real = os.path.normcase(os.path.realpath(str(root)))
        pending = [(root, 0)]
        files = []
        seen = set()
        entries_seen = 0
        while pending:
            folder, depth = pending.pop()
            if depth > MAX_DIRECTORY_DEPTH:
                raise InvalidP3D("Directory nesting exceeds 12 levels")
            entries = []
            with os.scandir(str(folder)) as listing:
                for entry in listing:
                    entries_seen += 1
                    if entries_seen > MAX_DIRECTORY_ENTRIES:
                        raise InvalidP3D("Directory tree exceeds 20000 entries")
                    entries.append(entry)
            entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
            for entry in entries:
                item = Path(entry.path)
                if entry.is_symlink():
                    raise InvalidP3D("Symbolic link in audit tree: " + str(item))
                real = os.path.normcase(os.path.realpath(str(item)))
                try:
                    if os.path.commonpath((root_real, real)) != root_real:
                        raise InvalidP3D("Path escapes selected directory: " + str(item))
                except ValueError:
                    raise InvalidP3D("Path crosses a filesystem boundary: " + str(item))
                if entry.is_dir(follow_symlinks=False):
                    pending.append((item, depth + 1))
                elif entry.is_file(follow_symlinks=False) and item.suffix.casefold() == ".p3d":
                    relative = item.relative_to(root).as_posix()
                    if relative.casefold() in seen:
                        raise InvalidP3D("Ambiguous case-insensitive P3D path: " + relative)
                    seen.add(relative.casefold())
                    size = entry.stat(follow_symlinks=False).st_size
                    if size < 0 or size > MAX_FILE_BYTES:
                        raise InvalidP3D("P3D exceeds 64 MiB per-model safety cap: " + relative)
                    report["total_p3d_bytes"] += size
                    if report["total_p3d_bytes"] > MAX_DIRECTORY_P3D_BYTES:
                        raise InvalidP3D("Loose P3D total exceeds 256 MiB directory safety cap")
                    files.append((relative, item, real))
                    if len(files) > MAX_DIRECTORY_P3D_FILES:
                        raise InvalidP3D("Loose P3D count exceeds 4096-file directory cap")
        if not files:
            raise InvalidP3D("No loose .p3d files found in selected directory")
        files.sort(key=lambda item: (item[0].casefold(), item[0]))
        report["total"] = len(files)
        for relative, item, scanned_real in files:
            try:
                # Refuse links/reparse redirection introduced after the scan.
                current_real = os.path.normcase(os.path.realpath(str(item)))
                if item.is_symlink() or current_real != scanned_real:
                    raise InvalidP3D("Unsafe redirected P3D source")
                if os.path.commonpath((root_real, current_real)) != root_real:
                    raise InvalidP3D("P3D source escaped the selected directory")
                result = validate_path(item)
            except (OSError, InvalidP3D, ValueError) as exc:
                result = {"source": relative, "ok": False,
                          "errors": ["Cannot read selected P3D: " + str(exc)], "warnings": []}
            result["source"] = relative
            report["assets"].append(result)
            report["valid" if result["ok"] else "invalid"] += 1
            report["warning_models"] += int(bool(result["warnings"]))
        report["ok"] = report["invalid"] == 0
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
    return report


MAX_TILE_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_TILE_MANIFEST_FILES = MAX_DIRECTORY_P3D_FILES


def _tile_manifest_unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InvalidP3D("Duplicate JSON manifest field: " + str(key))
        obj[key] = value
    return obj


def _tile_manifest_finite(values, count, label):
    if (not isinstance(values, list) or len(values) != count or
            any(type(value) not in (float, int) or not math.isfinite(value)
                for value in values)):
        raise InvalidP3D("Invalid finite {} coordinates".format(label))


def validate_tile_manifest(filename):
    """Check every declared Tile Splitter v1 P3D against bytes AND native model.

    This is a read-only authoring/packaging gate. The authored world-XY anchor
    exists in JSON only, not the P3D; validate its shape without pretending to
    independently recover it from the model or prove in-game placement.
    """
    path = Path(filename)
    report = {"source": str(path), "ok": False, "total": 0, "valid": 0,
              "invalid": 0, "errors": [], "assets": [], "total_p3d_bytes": 0,
              "manifest_sha256": None}
    try:
        if path.is_symlink() or not path.is_file() or path.parent.is_symlink():
            raise InvalidP3D("Missing or symbolic-link manifest")
        root = path.parent.resolve(strict=True)
        root_real = os.path.normcase(os.path.realpath(str(root)))
        if path.stat().st_size > MAX_TILE_MANIFEST_BYTES:
            raise InvalidP3D("Tile manifest exceeds 8 MiB safety limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_TILE_MANIFEST_BYTES + 1)
        if len(raw) > MAX_TILE_MANIFEST_BYTES:
            raise InvalidP3D("Tile manifest exceeds 8 MiB safety limit")
        report["manifest_sha256"] = hashlib.sha256(raw).hexdigest()

        def bad_constant(value):
            raise InvalidP3D("Non-finite JSON constant " + value)
        content = json.loads(raw.decode("utf-8"),
                             object_pairs_hook=_tile_manifest_unique_pairs,
                             parse_constant=bad_constant)
        if not isinstance(content, dict) or content.get("schema") != "micas-crashday-tile-export/v1":
            raise InvalidP3D("Unsupported or missing Tile Splitter export manifest schema")
        entries = content.get("p3d_files")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_TILE_MANIFEST_FILES:
            raise InvalidP3D("Manifest P3D entries must number 1 to 4096")
        if type(content.get("p3d_count")) is not int or content["p3d_count"] != len(entries):
            raise InvalidP3D("Manifest P3D count does not match listed files")
        expected_total = content.get("total_p3d_bytes")
        if type(expected_total) is not int or expected_total < 0 or expected_total > MAX_DIRECTORY_P3D_BYTES:
            raise InvalidP3D("Invalid or excessive manifest P3D byte total")
        expected_files = set()
        expected_keys = set()
        expected_origins = set()
        preflight = []
        total = 0
        import re
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise InvalidP3D("Manifest entry {} is not an object".format(i))
            name = entry.get("file")
            key = entry.get("key")
            if (not isinstance(name, str) or
                    not re.fullmatch(r"[A-Za-z0-9_-]{1,96}\.p3d", name)):
                raise InvalidP3D("Unsafe or non-canonical P3D basename in entry {}".format(i))
            if (not isinstance(key, str) or
                    not re.fullmatch(r"x[pm]\d{4}_y[pm]\d{4}", key) or
                    not name.lower().endswith(key.lower() + ".p3d")):
                raise InvalidP3D("Invalid tile key/filename association: " + name)
            if name.casefold() in expected_files or key in expected_keys:
                raise InvalidP3D("Duplicate case-insensitive filename or tile key: " + name)
            expected_files.add(name.casefold())
            expected_keys.add(key)
            _tile_manifest_finite(entry.get("tile_origin_world_xy"), 2, "tile origin")
            _tile_manifest_finite(entry.get("main_world_center"), 3, "main source centre")
            floor = entry.get("floor_z")
            if floor is not None and (type(floor) not in (float, int) or not math.isfinite(floor)):
                raise InvalidP3D("Non-finite floor Z in " + name)
            origin = tuple(entry["tile_origin_world_xy"])
            if origin in expected_origins:
                raise InvalidP3D("Duplicate authored tile origin in " + name)
            expected_origins.add(origin)
            size = entry.get("bytes")
            digest = entry.get("sha256")
            if (type(size) is not int or not 0 < size <= MAX_FILE_BYTES or
                    not isinstance(digest, str) or
                    not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise InvalidP3D("Invalid byte size/hash in " + name)
            total += size
            if total > MAX_DIRECTORY_P3D_BYTES:
                raise InvalidP3D("Tile manifest exceeds 256 MiB input budget")
            textures = entry.get("textures")
            meshes = entry.get("meshes")
            if (not isinstance(textures, list) or
                    any(not isinstance(t, str) or
                        not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", t) for t in textures)):
                raise InvalidP3D("Invalid native texture audit in " + name)
            if not isinstance(meshes, list) or not 1 <= len(meshes) <= MAX_MESHES:
                raise InvalidP3D("Invalid native mesh audit in " + name)
            for mesh in meshes:
                if (not isinstance(mesh, dict) or
                        not isinstance(mesh.get("mesh"), str) or
                        type(mesh.get("flags")) is not int or
                        type(mesh.get("vertices")) is not int or
                        type(mesh.get("triangles")) is not int):
                    raise InvalidP3D("Invalid native mesh summary in " + name)
            candidate = root / name
            real = os.path.normcase(os.path.realpath(str(candidate)))
            if (candidate.is_symlink() or
                    os.path.commonpath((root_real, real)) != root_real):
                raise InvalidP3D("Redirected/escaping tile P3D path: " + name)
            preflight.append((entry, candidate, real))
        if total != expected_total:
            raise InvalidP3D("Manifest aggregate P3D byte count mismatch")
        report["total"] = len(preflight)
        report["total_p3d_bytes"] = total
        for entry, candidate, original_real in preflight:
            name = entry["file"]
            errors = []
            try:
                if (candidate.is_symlink() or not candidate.is_file() or
                        os.path.normcase(os.path.realpath(str(candidate))) != original_real):
                    raise InvalidP3D("Missing or redirected P3D")
                if candidate.stat().st_size != entry["bytes"]:
                    raise InvalidP3D("File length differs from manifest")
                with candidate.open("rb") as stream:
                    data = stream.read(entry["bytes"] + 1)
                if len(data) != entry["bytes"]:
                    raise InvalidP3D("Read length differs from manifest")
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise InvalidP3D("SHA-256 differs from manifest")
                native = validate_bytes(data, label=name)
                if not native["ok"]:
                    raise InvalidP3D("Invalid native P3D: " + "; ".join(native["errors"]))
                if native["textures"] != [t + ".tga" for t in entry["textures"]]:
                    raise InvalidP3D("Native texture list differs from manifest")
                actual_meshes = [
                    {"mesh": mesh["name"], "flags": mesh["flags"],
                     "vertices": mesh["vertices"], "triangles": mesh["triangles"]}
                    for mesh in native["meshes"]]
                if actual_meshes != entry["meshes"]:
                    raise InvalidP3D("Native mesh flags/names/geometry counts differ from manifest")
            except (OSError, InvalidP3D, ValueError) as exc:
                errors.append(str(exc))
            report["assets"].append({"file": name, "ok": not errors, "errors": errors})
            report["valid" if not errors else "invalid"] += 1
        report["ok"] = report["invalid"] == 0
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        report["errors"].append(str(exc))
    return report


def _print_bounds(report, indent="  "):
    """Optional concise human-readable bounds without changing PASS semantics."""
    header = report.get("header_dimensions_xyz")
    if header is None:
        return
    print("{}Header nominal XYZ dimensions: {}".format(indent, header))
    for title, key in (("All positioned mesh vertices", "positioned_vertex_bounds_xyz"),
                       ("COLL-bit meshes only", "coll_flagged_vertex_bounds_xyz")):
        bounds = report.get(key)
        if bounds is None:
            print("{}{}: none".format(indent, title))
        else:
            print("{}{}: min={} max={} extent={}".format(
                indent, title, bounds["min"], bounds["max"], bounds["extent"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("p3d", nargs="*", help="Read-only loose P3D2 file(s)")
    parser.add_argument("--archive", action="append", default=[], metavar="CPK_OR_BIN",
                        help="Inspect native P3Ds inside ZIP-format Crashday CPK or Workshop legacy BIN, without extraction")
    parser.add_argument("--directory", action="append", default=[], metavar="P3D_FOLDER",
                        help="Recursively validate loose P3Ds in a selected authoring-output tree (no writes)")
    parser.add_argument("--tile-manifest", action="append", default=[], metavar="TILES_MANIFEST_JSON",
                        help="Verify Tile Splitter v1 JSON manifest against exact native P3D bytes, structure and audit")
    parser.add_argument("--json", action="store_true", help="Machine-readable structural report")
    parser.add_argument("--bounds", action="store_true",
                        help="Print measured full XYZ vertex bounds (including COLL-bit subset); no CFL size limit")
    args = parser.parse_args(argv)
    if not args.p3d and not args.archive and not args.directory and not args.tile_manifest:
        parser.error("Provide .p3d files, --archive, --directory, or --tile-manifest")
    reports = []
    archives = []
    directories = []
    for filename in args.p3d:
        try:
            result = validate_path(filename)
        except OSError as exc:
            result = {"source": filename, "ok": False,
                      "errors": ["Cannot read file: " + str(exc)], "warnings": []}
        reports.append(result)
    for filename in args.archive:
        archives.append(validate_archive(filename))
    for folder in args.directory:
        directories.append(validate_directory(folder))
    manifests = [validate_tile_manifest(item) for item in args.tile_manifest]
    if args.json:
        print(json.dumps({"files": reports, "archives": archives, "directories": directories,
                          "tile_manifests": manifests}, indent=2, sort_keys=True))
    else:
        for report in reports:
            print("{}: {} ({} vertices, {} triangles, {} mesh(es), {} warning(s))".format(
                report["source"], "PASS" if report["ok"] else "INVALID",
                report.get("vertices", 0), report.get("triangles", 0),
                len(report.get("meshes", [])), len(report["warnings"])))
            if args.bounds:
                _print_bounds(report)
            for message in report["errors"]:
                print("  ERROR: " + message)
            for message in report["warnings"]:
                print("  WARN: " + message)
        for result in archives:
            print("{}: {} ({} P3Ds, {} valid, {} invalid, {} with warnings)".format(
                result["source"], "PASS" if result["ok"] else "INVALID",
                result["total"], result["valid"], result["invalid"], result["warning_models"]))
            for msg in result["errors"]:
                print("  ERROR: " + msg)
            if args.bounds:
                for asset in result["assets"]:
                    print("  BOUNDS {}".format(asset["source"]))
                    _print_bounds(asset, indent="    ")
            problems = [r for r in result["assets"] if not r["ok"] or r["warnings"]]
            for report in problems[:20]:
                print("  {}: {}".format(report["source"],
                      "; ".join(report["errors"] + report["warnings"])))
            if len(problems) > 20:
                print("  ... {} additional affected models (use --json for details)".format(len(problems)-20))
        for result in directories:
            print("{}: {} ({} loose P3Ds, {} valid, {} invalid, {} with warnings)".format(
                result["source"], "PASS" if result["ok"] else "INVALID",
                result["total"], result["valid"], result["invalid"], result["warning_models"]))
            for message in result["errors"]:
                print("  ERROR: " + message)
            if args.bounds:
                for asset in result["assets"]:
                    print("  BOUNDS {}".format(asset["source"]))
                    _print_bounds(asset, indent="    ")
            problems = [r for r in result["assets"] if not r["ok"] or r["warnings"]]
            for asset in problems[:20]:
                print("  {}: {}".format(asset["source"],
                      "; ".join(asset["errors"] + asset["warnings"])))
            if len(problems) > 20:
                print("  ... {} additional affected models (use --json for details)".format(len(problems)-20))
    if not args.json:
        for result in manifests:
            print("{}: {} ({} declared P3Ds, {} verified, {} invalid)".format(
                result["source"], "PASS" if result["ok"] else "INVALID",
                result["total"], result["valid"], result["invalid"]))
            for msg in result["errors"]:
                print("  ERROR: " + msg)
            for asset in result["assets"]:
                for msg in asset["errors"]:
                    print("  {}: {}".format(asset["file"], msg))
    return 0 if (all(report["ok"] for report in reports)
                 and all(archive["ok"] for archive in archives)
                 and all(folder["ok"] for folder in directories)
                 and all(result["ok"] for result in manifests)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
