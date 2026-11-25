"""Validated native 1x1 checkpoint routes for private offline Crashday authoring.

Separate, reusable Blender-independent input contract. Does not edit staged
CFLs in place, change original user/game files, or infer game-mode bitmasks.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct


SCHEMA = "micas-crashday-checkpoint-route/v1"
_CFL_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\.cfl\Z")
MAX_CHECKPOINTS = 1024


class RouteError(ValueError):
    pass


def _check_bounds(raw):
    if (not isinstance(raw, list) or len(raw) != 4 or
            any(type(x) not in (float, int) or not math.isfinite(x)
                or not -10.0 <= x <= 10.0 for x in raw)):
        raise RouteError("Checkpoint box must have four finite local 20m coordinates")
    left, top, right, bottom = raw
    if not left < right or not bottom < top:
        raise RouteError("Checkpoint box must satisfy left < right and bottom < top")
    return [float(x) for x in raw]


def validated_route(source):
    if not isinstance(source, dict) or source.get("schema") != SCHEMA:
        raise RouteError("Checkpoint route schema mismatch")
    entries = source.get("checkpoints")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CHECKPOINTS:
        raise RouteError("Checkpoint route requires 1..{} ordered entries".format(MAX_CHECKPOINTS))
    known = set()
    normalized = []
    for n, item in enumerate(entries, 1):
        if not isinstance(item, dict) or set(item) - {"cfl", "rotation", "box"}:
            raise RouteError("Malformed checkpoint entry {}".format(n))
        cfl, rotation = item.get("cfl"), item.get("rotation")
        if not isinstance(cfl, str) or not _CFL_NAME.fullmatch(cfl):
            raise RouteError("Checkpoint {} has unsafe CFL filename".format(n))
        if cfl.casefold() in known:
            raise RouteError("Checkpoint uses the same 1x1 tile twice: " + cfl)
        known.add(cfl.casefold())
        if type(rotation) is not int or rotation not in (0, 1, 2, 3):
            raise RouteError("Checkpoint {} rotation must be one of 0, 1, 2, 3".format(n))
        box = _check_bounds(item["box"]) if "box" in item else None
        normalized.append({"cfl": cfl, "rotation": rotation, "box": box})
    return normalized


def read_route(path):
    path = Path(path)
    if (not path.is_file() or path.is_symlink() or
            not 1 <= path.stat().st_size <= 65536):
        raise RouteError("Unsafe or missing checkpoint route JSON (64 KiB maximum)")
    return validated_route(json.loads(path.read_text(encoding="utf-8")))


def native_route_layout(plan, fields, tiles, width, height, route):
    """Return rotated copy of native 1x1 tile entries and ORDERED CP indices."""
    named = {piece["cfl"].casefold(): piece for piece in plan["pieces"]}
    field_ids = {name.casefold(): i for i, name in enumerate(fields)}
    result = list(tiles)
    indices = []
    for entry in route:
        name = entry["cfl"].casefold()
        piece = named.get(name)
        if piece is None or name not in field_ids:
            raise RouteError("Checkpoint must name an authored staged 1x1 tile: " + entry["cfl"])
        if piece["logical_cell_footprint"] != [1, 1]:
            raise RouteError("Checkpoints require one native 1x1 (20x20m) tile: " + entry["cfl"])
        # A centered 1x1 logical anchor may belong to a much larger physical
        # region (e.g. a 60x20m remainder). The checkpoint must be carved
        # into a genuine physical 20m square before route authoring.
        if (plan.get("macro_layout") or "physical_cell_dimensions" in piece) and (
                piece.get("physical_cell_dimensions") != [1, 1]):
            raise RouteError("Checkpoint must be carved into a physical 20x20m tile: " + entry["cfl"])
        x, y = piece["logical_cell_origin"]
        index = (height - y - 1) * width + x
        fid, _rotation, mirror, variant = result[index]
        if fid != field_ids[name] or mirror or variant:
            raise RouteError("Checkpoint tile table does not match source: " + entry["cfl"])
        result[index] = (fid, entry["rotation"], mirror, variant)
        indices.append(index)
    return result, indices


def _number(x):
    return "{:g}".format(float(x))


def patch_cfl_assets(assets, route):
    """Return a COPY of authored native CRLF CFLs with real CP flag and box."""
    selected = {entry["cfl"].casefold(): entry for entry in route}
    found = set()
    changed = []
    receipts = []
    for folder, name, raw, digest in assets:
        if folder != "tiles" or name.casefold() not in selected:
            changed.append((folder, name, raw, digest))
            continue
        entry = selected[name.casefold()]
        if (b"\r\n" not in raw or b"\n" in raw.replace(b"\r\n", b"") or
                b"\r" in raw.replace(b"\r\n", b"")):
            raise RouteError("Checkpoint CFL must use native CRLF framing: " + name)
        rows = raw.split(b"\r\n")
        if (len(rows) < 14 or rows[0] != b"Crashday-FieldObject-File" or
                rows[3] != b"1 1" or rows[5] != b"0" or
                rows[9] not in (b"0", b"1")):
            raise RouteError("Checkpoint source is not an authored 1x1 Autolist=0 CFL: " + name)
        if rows[9] == b"0":
            if entry["box"] is None:
                raise RouteError("Specify checkpoint box bounds for new checkpoint tile: " + name)
            if rows[10] not in (b"0", b"1"):
                raise RouteError("CFL expected native free-roam record before checkpoint insertion")
            rows[9] = b"1"
            rows.insert(10, (" ".join(_number(x) for x in entry["box"])).encode("ascii"))
        else:
            if entry["box"] is not None:
                rows[10] = (" ".join(_number(x) for x in entry["box"])).encode("ascii")
            else:
                try:
                    _check_bounds([float(x) for x in rows[10].decode("ascii").split()])
                except (ValueError, UnicodeError) as exc:
                    raise RouteError("Native checkpoint box is unreadable: " + name) from exc
        authored = b"\r\n".join(rows)
        new_digest = hashlib.sha256(authored).hexdigest()
        changed.append((folder, name, authored, new_digest))
        receipts.append({"cfl": name, "original_sha256": digest,
                         "authored_sha256": new_digest})
        found.add(name.casefold())
    if found != set(selected):
        raise RouteError("Checkpoint references missing staged authored CFL assets")
    return changed, receipts


def counterrotate_model(raw, turn, name="<memory>"):
    """Counteract authored native tile yaw without rotating source world XYZ.

    Native track turn is clockwise in Blender XY; P3D stores X,Z,Y.
    Rotate all local vertices, mesh offsets and light positions oppositely.
    Swap X/Y dimensions on odd turns; preserve height, topology, UV bytes,
    flags, section lengths and original source files. Unknown USER/trailing
    payloads are refused rather than guessing their transform semantics.
    This is reader-grounded offline geometry, not engine physics proof.
    """
    if type(turn) is not int or turn not in (0, 1, 2, 3):
        raise RouteError("Invalid checkpoint turn")
    if not turn:
        return raw
    from validate_p3d import Reader, validate_bytes
    report = validate_bytes(raw, name)
    if not report["ok"] or report["user_data_bytes"] or report["consumed_bytes"] != len(raw):
        raise RouteError("Checkpoint rotation requires valid P3D2 without opaque USER/trailing data: " + name)
    out = bytearray(raw)
    r = Reader(raw)

    def point():
        offset = r.offset
        x, z, y = r.unpack("<3f", "checkpoint position")
        x, y = ((-y, x), (-x, -y), (y, -x))[turn-1]
        struct.pack_into("<3f", out, offset, x, z, y)

    def dimensions():
        offset = r.offset
        x, z, y = r.unpack("<3f", "checkpoint dimensions")
        if turn % 2:
            struct.pack_into("<3f", out, offset, y, z, x)

    r.read(4, "signature")
    dimensions()
    r.section("TEX")
    ntex = r.unpack("<B", "texture count")[0]
    for _ in range(ntex):
        r.text("texture")
    r.section("LIGHTS")
    for _ in range(r.unpack("<H", "light count")[0]):
        r.text("light name")
        point()
        r.read(11, "light range/color/flags")
    r.section("MESHES")
    for _ in range(r.unpack("<H", "mesh count")[0]):
        r.section("SUBMESH")
        r.text("mesh name")
        r.read(4, "mesh flags")
        point()
        dimensions()
        r.read(ntex * 14, "material ranges")
        for _ in range(r.unpack("<H", "vertex count")[0]):
            point()
        nfaces = r.unpack("<H", "face count")[0]
        r.read(nfaces * 30, "face data")
    r.section("USER")
    if r.unpack("<i", "user length")[0] != 0 or r.offset != len(raw):
        raise RouteError("Unexpected checkpoint P3D trailing data")
    result = bytes(out)
    check = validate_bytes(result, name)
    if not check["ok"] or len(result) != len(raw):
        raise RouteError("Counterrotated checkpoint model failed structural validation: " + name)
    return result


def counterrotate_checkpoint_assets(plan, assets, route):
    """Derive private model variants only for rotated authored checkpoint tiles."""
    by_cfl = {p["cfl"].casefold(): p for p in plan["pieces"]}
    targets = {by_cfl[e["cfl"].casefold()]["p3d"]: e["rotation"]
               for e in route if e["rotation"]}
    result, receipts = [], []
    for folder, name, raw, digest in assets:
        if folder != "models" or name not in targets:
            result.append((folder, name, raw, digest))
            continue
        turn = targets[name]
        adjusted = counterrotate_model(raw, turn, name)
        new_hash = hashlib.sha256(adjusted).hexdigest()
        result.append((folder, name, adjusted, new_hash))
        receipts.append({"p3d": name, "native_turn": turn,
                         "input_sha256": digest, "output_sha256": new_hash})
    if len(receipts) != len(targets):
        raise RouteError("Missing authored checkpoint model for geometry preservation")
    return result, receipts
