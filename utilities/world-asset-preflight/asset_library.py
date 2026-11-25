"""Read-only, case-insensitive Crashday assets from CPK ZIPs and loose files.

Game content is indexed without bulk extraction and never modified.
Only referenced archive DDS/TGA textures may be staged in a separate
bounded cache. Original game and mod assets remain read-only.
Precedence: explicit user-selected mod > vanilla dat*.cpk > loose game
data/content > unambiguous mod_testing or Steam Workshop fallbacks.
Different copies with the same logical filename require owner selection;
bit-identical copies may be used automatically (SHA-256 verified).
Game-runtime precedence remains unverified.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
import hashlib
import os
import re
import math
import zipfile
import zlib


class AssetError(ValueError):
    pass


@dataclass(frozen=True)
class AssetRef:
    logical_name: str
    source: Path
    member: str | None = None

    def read_bytes(self, max_bytes: int = 256 * 1024 * 1024) -> bytes:
        """Read an individual asset with an actual decompressed-byte limit.

        Workshop archives are untrusted input. ZIP's declared uncompressed
        size is checked first, but is not trusted as the sole protection:
        bound the stream even if metadata understates its eventual output.
        No bulk extraction or changes to the original package.
        """
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self.member is None:
            if self.source.stat().st_size > max_bytes:
                raise AssetError("Asset exceeds safe read limit: " + self.logical_name)
            with self.source.open("rb") as stream:
                return self._read_bounded(stream, max_bytes)
        try:
            with zipfile.ZipFile(self.source) as archive:
                info = archive.getinfo(self.member)
                if info.file_size > max_bytes:
                    raise AssetError("Asset exceeds safe read limit: " + self.logical_name)
                with archive.open(info) as stream:
                    return self._read_bounded(stream, max_bytes)
        except (OSError, KeyError, RuntimeError, EOFError, NotImplementedError,
                zipfile.BadZipFile, zlib.error) as exc:
            # A corrupt or unsupported subscribed package must become a
            # per-asset import diagnostic, not crash the whole track import.
            # Never silently substitute another mod's same-named asset.
            raise AssetError("Unreadable ZIP asset {} in {} ({}): {}: {}".format(
                self.logical_name, self.source, self.member,
                type(exc).__name__, exc)) from exc

    def _read_bounded(self, stream, max_bytes):
        chunks = []
        count = 0
        while True:
            # Read no more than one byte past the ceiling; never decompress a
            # whole archive member into memory before enforcing the limit.
            data = stream.read(min(1024 * 1024, max_bytes - count + 1))
            if not data:
                return b"".join(chunks)
            count += len(data)
            if count > max_bytes:
                raise AssetError("Asset exceeds safe read limit: " + self.logical_name)
            chunks.append(data)

    def local_texture_path(self, cache_dir: Path, max_bytes: int = 64 * 1024 * 1024) -> Path:
        """Return a real DDS/TGA path; materialize only a requested CPK image.

        Native P3D/CFL sources remain READ ONLY. The cache holds only images
        actually requested by a scene, with content-addressed names so a
        modified CPK cannot silently reuse stale pixels. Never bulk extract.
        """
        if self.member is None:
            # Loose textures bypass ZIP decompression, but must not bypass
            # the same per-texture input cap before Blender decodes/loads them.
            # Keep the user's original file in place and never copy it.
            if self.source.stat().st_size > max_bytes:
                raise AssetError("Texture exceeds safe read limit: " + self.logical_name)
            return self.source
        key = canonical_key(self.logical_name)
        if not key.startswith("content/textures/") or not key.endswith((".dds", ".tga")):
            raise AssetError("Only packed image textures may be materialized: " + key)
        # Both declared ZIP size and the actual decompression stream are
        # bounded; archive.read() alone could inflate an untrusted ZIP bomb.
        pixels = self.read_bytes(max_bytes=max_bytes)
        digest = hashlib.sha256(pixels).hexdigest()
        filename = digest[:24] + "_" + PurePosixPath(key).name
        target_dir = Path(cache_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        if target.is_file() and target.stat().st_size == len(pixels):
            if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                return target
        staging = target_dir / (filename + "." + str(os.getpid()) + ".partial")
        try:
            staging.write_bytes(pixels)
            os.replace(str(staging), str(target))
        finally:
            if staging.exists():
                staging.unlink()
        return target


@dataclass(frozen=True)
class TileDefinition:
    tile_file: str
    model_file: str
    width: int
    depth: int
    ground_type: str
    bend_rules: str
    model: AssetRef | None
    # Text-only vegetation metadata; resolved even if P3D greenery is not rendered.
    vegetation: tuple["VegetationArticle", ...] = ()
    vegetation_alias: str | None = None
    source_ref: AssetRef | None = None
    # The actual CFL defining the vegetation articles (may be an inherited alias).
    vegetation_source_ref: AssetRef | None = None


@dataclass(frozen=True)
class VegetationArticle:
    """A probabilistic placement zone from a CFL's vegetation section.

    The source uses 0..(20*width) X and negative Y, measured from the
    upper-left of the unrotated tile, unlike centered P3D mesh coordinates.
    Height 'auto' is represented by None; its game-engine raycast is unknown.
    """
    veg_file: str
    probability: float
    x: float
    y: float
    width: float
    depth: float
    height: float | None


@dataclass(frozen=True)
class VegetationDefinition:
    veg_file: str
    model_file: str
    min_scale: float
    max_scale: float
    rotation: str
    height_offset: float
    render_color: str
    min_copies: int
    max_copies: int
    orient_to_ground: bool | None
    model: AssetRef | None
    source_ref: AssetRef | None = None


@dataclass(frozen=True)
class AmbienceDefinition:
    """Source .amb lighting fields, not a claim of matching game rendering."""
    file_name: str
    sky_texture_set: str
    sun_rgb: tuple[int, int, int]
    sun_max_strength: float
    sun_exponent: float
    sky_rgb: tuple[int, int, int]
    sunlit_object_rgb: tuple[int, int, int]
    ambient_min_rgb: tuple[int, int, int]
    specular_rgb: tuple[int, int, int]
    vegetation_dark_rgb: tuple[int, int, int]
    vegetation_light_rgb: tuple[int, int, int]
    sun_direction: tuple[float, float, float]
    sun_azimuth_degrees: float
    screen_gamma: tuple[float, float, float, float]
    apply_gamma_to_sky: bool
    texture_filter_rgb: tuple[int, int, int]


@dataclass(frozen=True)
class TextureShaderInfo:
    """Authored and resolved .tex shader tokens; not Blender light calculations."""
    tex_file: str
    has_alpha: bool
    material_type: str
    authored_shader: str
    effective_shader: str
    shader_source: str
    parameters: tuple[str, ...]
    trailing_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class DynamicDefinition:
    """CDO model references, independent of the placed TRK transforms."""
    cdo_file: str
    low_model_file: str
    high_model_file: str
    material: str
    mass: float
    center_of_mass: tuple[float, float, float]
    model: AssetRef | None
    using_low_fallback: bool = False
    source_ref: AssetRef | None = None


def canonical_key(path: str) -> str:
    """Canonicalize member paths with deliberate rejection of traversal."""
    original = path.replace("\\", "/").strip()
    if not original or original.startswith("/") or re.match(r"^[A-Za-z]:", original):
        raise AssetError(f"Invalid absolute or empty asset path: {path!r}")
    parts = original.split("/")
    if any(p == ".." for p in parts):
        raise AssetError(f"Asset path traversal rejected: {path!r}")
    return "/".join(p.lower() for p in parts if p not in ("", "."))


def clean_cfl_lines(data: bytes) -> list[str]:
    # Stock CFL files contain German cp1252 comments; primary format data is ASCII.
    lines = data.decode("cp1252", errors="replace").splitlines()
    # Python 3.7 compatibility (Blender 2.80); avoid 3.8 assignment expressions.
    return [text for text in (line.split("#", 1)[0].strip() for line in lines) if text]


def parse_cfl(data: bytes, tile_file: str, model: AssetRef | None = None) -> TileDefinition:
    lines = clean_cfl_lines(data)
    if len(lines) < 13 or lines[0] != "Crashday-FieldObject-File":
        raise AssetError(f"Missing valid CFL header in {tile_file}")
    model_name = lines[2].strip()
    dimensions = lines[3].split()
    if len(dimensions) != 2:
        raise AssetError(f"Invalid footprint in {tile_file}: {lines[3]!r}")
    try:
        width, depth = map(int, dimensions)
    except ValueError as ex:
        raise AssetError(f"Invalid footprint in {tile_file}: {lines[3]!r}") from ex
    if not 1 <= width <= 16 or not 1 <= depth <= 16:
        raise AssetError(f"Unsafe footprint in {tile_file}: {width}x{depth}")
    if model_name.upper() in ("NONE", "-"):
        model_name = ""
    # Checkpoint CFLs can insert an additional flattening parameter before
    # the restriction tokens. FlyX TileManager.cs handles this with an offset.
    # Search the bounded restriction slot rather than assuming line 12.
    bend_candidates = [line for line in lines[12:15]
                       if line.split() and line.split()[-1].upper() == "STOP"]
    if len(bend_candidates) != 1:
        raise AssetError(f"Expected one CFL bend-rule STOP line in {tile_file}, got {bend_candidates}")
    raw_rules = bend_candidates[0].upper().split()
    legal = {"V1", "V2", "H1", "H2", "STOP"}
    if any(token not in legal for token in raw_rules):
        raise AssetError(f"Unsupported CFL bend-rule token in {tile_file}: {raw_rules}")
    # Stock files sometimes list V2/H2 for 1x1 footprints: these are
    # harmless inactive flags, not an invalid tile (e.g. arenaend.cfl).
    vegetation, vegetation_alias = parse_cfl_vegetation(
        lines[bend_candidates_index(lines, bend_candidates[0])+1:], tile_file)
    return TileDefinition(
        tile_file=tile_file, model_file=model_name,
        width=width, depth=depth, ground_type=lines[4],
        bend_rules=" ".join(dict.fromkeys(raw_rules)), model=model,
        vegetation=vegetation, vegetation_alias=vegetation_alias
    )


def bend_candidates_index(lines: list[str], candidate: str) -> int:
    """Identify the restriction line in the same bounded checkpoint-safe slot."""
    return next(i for i in range(12, min(15, len(lines))) if lines[i] == candidate)


def parse_cfl_vegetation(lines: list[str], cfl_name: str):
    """Parse stock CFL vegetation records without inventing their spawn count.

    Each article has exactly six lines: a bounded 'Next: N' marker, VEG
    filename, probability, (x,y) center, (width,depth) rectangle and
    float/auto height. The leading article count sets the true record count.
    Native Workshop examples show labels starting at 3, 4 and repeating 1..4;
    they are headings, not reliable sequential or unique article IDs.
    Preserve ALL articles in file order. SAME_VEGETATION_AS inheritance is
    resolved at the library level with cycle detection.
    """
    if not lines:
        return (), None  # Tiny synthetic/legacy CFLs may omit this section.
    if lines[0].strip(" -").upper() != "VEGETATION":
        raise AssetError("Unexpected CFL vegetation section in {}".format(cfl_name))
    records = lines[1:]
    if records == ["NO_VEGETATION"]:
        return (), None
    if len(records) == 2 and records[0] == "SAME_VEGETATION_AS":
        return (), records[1]
    if not records:
        raise AssetError("Missing vegetation records in {}".format(cfl_name))
    try:
        count = int(records[0])
    except ValueError as e:
        raise AssetError("Invalid vegetation article count in {}".format(cfl_name)) from e
    if count < 0 or count > 64 or len(records) != 1 + count * 6:
        raise AssetError("Incorrect vegetation article count in {}".format(cfl_name))
    result = []
    for i in range(count):
        chunk = records[1+i*6:1+(i+1)*6]
        if re.fullmatch(r"Next: [1-9][0-9]{0,3}", chunk[0]) is None:
            raise AssetError("Invalid vegetation article heading in {}".format(cfl_name))
        try:
            filename = canonical_key(chunk[1])
            chance = float(chunk[2])
            x, y = (float(v) for v in chunk[3].split())
            width, depth = (float(v) for v in chunk[4].split())
            height = None if chunk[5].lower() == "auto" else float(chunk[5])
        except (ValueError, AssetError) as e:
            raise AssetError("Invalid CFL vegetation article {} in {}".format(i+1, cfl_name)) from e
        numbers = (chance, x, y, width, depth) + (() if height is None else (height,))
        if (not all(math.isfinite(n) for n in numbers)
                or not (0 <= chance <= 1) or width < 0 or depth < 0
                or not filename.endswith(".veg")):
            raise AssetError("Unsafe CFL vegetation article {} in {}".format(i+1, cfl_name))
        result.append(VegetationArticle(filename, chance, x, y, width, depth, height))
    return tuple(result), None


def parse_veg(data: bytes, veg_file: str, model: AssetRef | None = None):
    """Read supported .veg visual parameters; preserve unknown trailing fields.

    Owner's SDK .veg table identifies lines 10/11 as per-tile min/max
    copy counts, and line 16 as ground orientation. Remaining LOD/2D fields
    are not interpreted. The P3D model is sourced read-only.
    """
    lines = clean_cfl_lines(data)
    if len(lines) < 11:
        raise AssetError("Incomplete VEG file {}".format(veg_file))
    try:
        model_name = canonical_key(lines[0])
        lo, hi = float(lines[1]), float(lines[2])
        height_offset = float(lines[4])
        copies_min = int(lines[9])
        copies_max = int(lines[10])
        rotation = lines[3].lower()
        if rotation not in ("random", "random90deg"):
            if not math.isfinite(float(rotation)):
                raise ValueError("Nonfinite rotation")
        orient_to_ground = None if len(lines) < 16 else bool(int(lines[15]))
    except (AssetError, ValueError) as e:
        raise AssetError("Invalid VEG model or parameters {}".format(veg_file)) from e
    if (not all(math.isfinite(n) for n in (lo, hi, height_offset))
            or lo <= 0 or hi < lo or not model_name.endswith(".p3d")
            or copies_min < 0 or copies_max < copies_min or copies_max > 10000):
        raise AssetError("Unsafe VEG scale/offset in {}".format(veg_file))
    return VegetationDefinition(veg_file, model_name, lo, hi, rotation,
                                height_offset, lines[8].lower(),
                                copies_min, copies_max, orient_to_ground, model)


def parse_ambience(data: bytes, file_name: str) -> AmbienceDefinition:
    """Parse bounded, source-authored native lighting tokens from an AMB.

    Positional field meanings: owner-provided SDK config *.amb tab (gid
    2080193163), cross-checked against installed Redline day/even sources.
    This is source metadata, NOT a 1:1 Blender shader/lighting conversion.
    """
    lines = clean_cfl_lines(data)
    if len(lines) < 17 or lines[0] != "PropsFX-Ambience-File":
        raise AssetError("Missing valid AMB header/lighting fields in " + file_name)

    def rgb(field):
        values = field.split()
        if len(values) != 3:
            raise ValueError("AMB RGB requires 3 channels")
        channels = tuple(int(value) for value in values)
        if any(c < 0 or c > 255 for c in channels):
            raise ValueError("AMB RGB outside 0..255")
        return channels

    try:
        sun = rgb(lines[3])
        maximum, exponent = float(lines[4]), float(lines[5])
        sky, sunlit_objects, minimum, specular = (rgb(lines[i]) for i in range(6, 10))
        vegetation_dark, vegetation_light = rgb(lines[10]), rgb(lines[11])
        direction = tuple(float(value) for value in lines[12].split())
        azimuth = float(lines[13])
        gamma = tuple(float(value) for value in lines[14].split())
        gamma_sky_token = lines[15]
        texture_filter = rgb(lines[16])
        if gamma_sky_token not in ("0", "1"):
            raise ValueError("Invalid sky gamma switch")
    except (ValueError, TypeError) as exc:
        raise AssetError("Invalid AMB lighting values in " + file_name) from exc
    if (len(gamma) != 4 or not all(math.isfinite(x) and 0 < x <= 10 for x in gamma)
            or len(direction) != 3 or not all(math.isfinite(x) for x in direction)
            or (maximum > 0 and sum(x*x for x in direction) < 1e-12)
            or not math.isfinite(azimuth)
            or not all(math.isfinite(x) for x in (maximum, exponent))
            or maximum < 0 or maximum > 1 or exponent < 0 or exponent > 100):
        raise AssetError("Unsafe AMB lighting values in " + file_name)
    return AmbienceDefinition(file_name, lines[2], sun, maximum, exponent,
                              sky, sunlit_objects, minimum, specular, vegetation_dark,
                              vegetation_light, direction, azimuth, gamma,
                              gamma_sky_token == "1", texture_filter)


def parse_tex_shader(data: bytes, tex_file: str) -> TextureShaderInfo:
    """Read shader tokens without treating commented-out has_alpha as active.

    A normal .tex has an optional active has_alpha marker, a material class,
    and a shader token. STANDARD material files may intentionally end early.
    Parameters stay textual: stock templates contain nonstandard numeric text.
    """
    fields = clean_cfl_lines(data)
    has_alpha = bool(fields and fields[0].lower() == "has_alpha")
    if has_alpha:
        fields = fields[1:]
    if not fields or (len(fields) < 2 and fields[0].lower() != "standard"):
        raise AssetError("Missing .tex material or shader in " + tex_file)
    material_type = fields[0].lower()
    authored = fields[1].lower() if len(fields) > 1 else "standard"
    # Parameter counts from Crashday Center's .tex reference, cross-checked
    # with original stock _ground/_metal templates. _ground.tex contains an
    # EXTRA old grass/diffuse clause after its five active specularvertex
    # settings; do not silently treat those two tokens as specular numbers.
    lengths = {"specularvertex": 5, "diffuseenvmap": 3, "chrome": 1,
               "use_shaderparams_from": 1, "diffuse": 0,
               "alphatest": 0, "alphatestdoubleside": 0,
               "alphadoubleside": 0, "standard": 0}
    values = tuple(fields[2:])
    count = lengths.get(authored)
    if count is not None and len(values) < count:
        raise AssetError("Incomplete .tex shader parameters in " + tex_file)
    params = values if count is None else values[:count]
    trailing = () if count is None else values[count:]
    return TextureShaderInfo(tex_file, has_alpha, material_type, authored,
                             authored, tex_file, params, trailing)


def parse_tex_alpha_mode(data: bytes):
    """Conservatively recognize Crashday's explicit alpha-test shaders.

    A .tex 'has_alpha' flag ALONE does not mean transparency: many track
    textures use the alpha channel for specular reflectance. Only alpha-test
    shaders qualify for Blender foliage/mesh opacity preview. Also allow the
    *native tree material* with diffuse shader and explicit has_alpha: original
    gras/waste foliage textures have 69-74% nearly transparent DXT5 texels,
    otherwise rendered as opaque rectangular grass/waste cards. This is a
    source-backed Blender cutout approximation, NOT proof of exact game shader.
    Keep default/ground/specular and shader aliases opaque; don't interpret
    every has_alpha channel as transparency.
    """
    fields = clean_cfl_lines(data)
    if len(fields) < 3 or fields[0].lower() != "has_alpha":
        return None
    shader = fields[2].split()[0].lower()
    if shader == "alphatest":
        return "CLIP"
    if shader == "alphatestdoubleside":
        return "CLIP_DOUBLE_SIDED"
    # Original gras.tex and waste.tex are 'has_alpha / tree / diffuse'.
    # Their actual DXT5 textures are mostly transparent; treating these
    # native foliage cards as opaque is a measurable Blender import defect.
    # Keep this visual cutout fallback restricted to native tree materials.
    if shader == "diffuse" and fields[1].split()[0].lower() == "tree":
        return "CLIP_DOUBLE_SIDED"
    return None


def parse_cdo(data: bytes, cdo_file: str, model: AssetRef | None = None) -> DynamicDefinition:
    """Parse the seven data lines of Crashday's dynamic-object definition.

    The first model reference is the low/detail fallback; the next is the
    high-detail P3D. The installed game's barrier2.cdo has an anomalous
    '1Crashday' header and is intentionally rejected, not silently fixed.
    """
    lines = clean_cfl_lines(data)
    if len(lines) != 7 or lines[0] != "Crashday-DynamicObject-File":
        raise AssetError("Invalid CDO header or field count in {}".format(cdo_file))
    low, high = lines[1], lines[2]
    if not low or not high:
        raise AssetError("Missing CDO model field in {}".format(cdo_file))
    try:
        mass = float(lines[4])
        center = tuple(float(x) for x in lines[6].split())
    except ValueError as ex:
        raise AssetError("Invalid CDO mass or center of gravity in {}".format(cdo_file)) from ex
    if len(center) != 3:
        raise AssetError("Expected three CDO center-of-gravity values in {}".format(cdo_file))
    if not all(math.isfinite(x) for x in (mass,) + center):
        raise AssetError("Non-finite CDO physical parameter in {}".format(cdo_file))
    return DynamicDefinition(cdo_file, low, high, lines[3].upper(), mass, center, model)


def candidate_model_keys(model_name: str) -> tuple[str, ...]:
    if not model_name:
        return ()
    canonical = canonical_key(model_name)
    filename = PurePosixPath(canonical).name
    if "/" in canonical:
        return (canonical, f"content/{canonical}") if not canonical.startswith("content/") else (canonical,)
    return (f"content/models/{filename}", f"content/tiles/{filename}")


def suggest_installed_mods_for_missing_assets(game, candidate_paths, excluded_content_roots=()):
    """Offer possible mod roots for explicitly named missing assets, never load them.

    The caller supplies {display_name: candidate logical content paths}; only
    direct files under content/tiles, content/models or content/dynamics are
    inspected. A single bounded directory pass answers all requested kinds.
    Do not infer mod activation order, merge mods, or read candidate contents.
    """
    try:
        requested = {}
        folders = set()
        for label, candidates in candidate_paths.items():
            allowed = set()
            for candidate in candidates:
                try:
                    parts = canonical_key(str(candidate)).split("/")
                except AssetError:
                    continue
                if (len(parts) == 3 and parts[0] == "content"
                        and parts[1] in ("tiles", "models", "dynamics")
                        and parts[2] and "." in parts[2]):
                    allowed.add((parts[1], parts[2]))
                    folders.add(parts[1])
            if allowed:
                requested[str(label)] = allowed
        if not requested:
            return {}
        base = Path(game) / "user" / "mod_testing"
        if not base.is_dir():
            return {}
        excluded = {os.path.normcase(os.path.abspath(str(path))).lower()
                    for path in excluded_content_roots}
        matches = {name: [] for name in requested}
        for mod in sorted(base.iterdir(), key=lambda path: path.name.lower()):
            if not mod.is_dir() or mod.is_symlink() or mod.name == "_nah":
                continue
            content = mod / "content"
            if os.path.normcase(os.path.abspath(str(content))).lower() in excluded:
                continue
            found = {}
            for folder_name in folders:
                folder = content / folder_name
                if not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    found[folder_name] = {p.name.lower() for p in folder.iterdir()
                                          if p.is_file() and not p.is_symlink()}
                except OSError:
                    continue
            for name, alternatives in requested.items():
                if any(filename in found.get(folder_name, ())
                       for folder_name, filename in alternatives):
                    matches[name].append(str(mod))
        return {name: paths for name, paths in matches.items() if paths}
    except OSError:
        # Optional diagnostics must never cause a working track import to fail.
        return {}


def suggest_installed_mods_for_missing_cfl(game, missing_cfl_names, excluded_content_roots=()):
    """Suggest CFL filenames in other installed mods without loading any mod."""
    requested = {}
    for name in dict.fromkeys(str(item) for item in missing_cfl_names):
        try:
            basename = PurePosixPath(canonical_key(name)).name
        except AssetError:
            continue
        if basename.endswith(".cfl"):
            requested[name] = ("content/tiles/" + basename,)
    return suggest_installed_mods_for_missing_assets(
        game, requested, excluded_content_roots=excluded_content_roots)


def discover_workshop_root(game):
    """Find this game's Steam Workshop subscriptions beside steamapps/common.

    Crashday: Redline Edition's Steam app ID is 508980. Workshop item
    directories typically contain ZIP-format *_legacy.bin archives, not
    unpacked content folders. This function never modifies Steam files.
    """
    game = Path(game)
    if game.parent.name.lower() != "common":
        return None
    root = game.parent.parent / "workshop" / "content" / "508980"
    return root if root.is_dir() else None


class AssetLibrary:
    """Builds an in-memory index of member names, not copied file contents."""

    def __init__(self, game: str | Path, *, archives: Iterable[Path] | None = None,
                 texture_cache_dir: Path | None = None,
                 mod_content_roots: Iterable[Path] | None = None):
        self.game = Path(game)
        self.texture_cache_dir = Path(texture_cache_dir) if texture_cache_dir is not None else None
        self.sources: dict[str, AssetRef] = {}
        self.collisions: dict[str, list[str]] = {}
        self.mod_content_paths: list[Path] = []
        # Workshop subscriptions are the default optional mod source. Keep
        # them lower priority than stock and explicit overrides; never let
        # duplicate names from different subscriptions be chosen arbitrarily.
        self.workshop_root = discover_workshop_root(self.game)
        self.mod_testing_root = self.game / "user" / "mod_testing"
        self._fallback_indexed = False
        self._fallback_candidates: dict[str, list[AssetRef]] = {}
        self.fallback_ambiguities: dict[str, list[str]] = {}
        # Only unsafe aliases inside a particular archive are a contextual
        # blocker. A global different-mod ambiguity must not poison a valid
        # definition + model pair after an earlier unqualified get().
        self._fallback_archive_alias_roots: dict[str, set[str]] = {}
        self.fallback_identical: dict[str, list[str]] = {}
        # Definition files from one mod must not silently borrow geometry
        # found only in an unrelated mod. Report the candidate roots instead.
        self.fallback_dependency_conflicts: dict[str, dict] = {}
        # Texture and .tex dependencies are distinct from CFL/CDO/VEG P3D
        # model dependencies and must never be reported as model failures.
        self.fallback_texture_conflicts: dict[str, dict] = {}
        # Inherited vegetation CFLs may come from a different byte-identical
        # mod copy than the parent; preserve their actual selected source.
        self.vegetation_alias_sources: dict[str, dict] = {}
        self.fallback_resolved: set[str] = set()
        self._fallback_choice: dict[str, AssetRef] = {}
        # Definition-scoped P3D selections are deliberately not global get()
        # choices: different mods may legitimately use different same-named
        # models, and an unqualified lookup must stay ambiguous.
        self._dependency_source_reasons: dict[AssetRef, str] = {}
        data = self.game / "data"
        packages = sorted(archives if archives is not None else data.glob("dat*.cpk"),
                          key=lambda p: p.name.lower())
        for package in packages:
            with zipfile.ZipFile(package) as z:
                for member in z.namelist():
                    if member.endswith("/"):
                        continue
                    try:
                        key = canonical_key(member)
                    except AssetError:
                        continue
                    if key.startswith("content/"):
                        self._register(key, AssetRef(key, package, member))
        # Owner rule: VANILLA CPK beats loose game data by default.
        # A deliberately selected mod remains a separate explicit override.
        loose = data / "content"
        if loose.is_dir():
            self._register_content_folder(loose, override=False)

        # EXPLICIT override is a loose mod/content directory OR one Steam
        # Workshop item folder containing exactly one ZIP-format legacy.bin.
        # A full Workshop collection is intentionally not a single override.
        for supplied in (mod_content_roots or ()):
            root = Path(supplied)
            content = root if root.name.lower() == "content" else root / "content"
            if content.is_dir():
                self._register_content_folder(content)
                self.mod_content_paths.append(content)
                continue
            packages = (sorted(p for p in root.iterdir()
                               if p.is_file() and not p.is_symlink()
                               and p.name.lower().endswith("_legacy.bin")
                               and zipfile.is_zipfile(p))
                        if root.is_dir() and not root.is_symlink() else [])
            if len(packages) != 1:
                raise AssetError(
                    "Custom mod folder must contain content/ or ONE ZIP-format "
                    "*_legacy.bin Workshop item: {}".format(root))
            registered = self._register_archive_content(packages[0])
            if registered == 0:
                raise AssetError("Workshop item has no content assets: {}".format(root))
            self.mod_content_paths.append(root)

    @staticmethod
    def _archive_aliases_identical(archive, infos, max_bytes=256 * 1024 * 1024):
        """Compare colliding ZIP entries by their actual bounded bytes.

        CRC/declared sizes alone do not prove equality. ZipInfo handles are
        essential: getinfo(name) otherwise returns the LAST entry when the
        archive contains the same literal name more than once.
        """
        digests = set()
        try:
            for info in infos:
                if info.file_size > max_bytes:
                    return False
                digest = hashlib.sha256()
                total = 0
                with archive.open(info) as stream:
                    while True:
                        chunk = stream.read(min(1024 * 1024, max_bytes - total + 1))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            return False
                        digest.update(chunk)
                digests.add(digest.digest())
                if len(digests) > 1:
                    return False
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, EOFError):
            return False
        return bool(digests)

    @staticmethod
    def _archive_refs(package, duplicate_keys=None):
        """Index Workshop/mod ZIP members without guessing between differences.

        Multiple paths that normalize to the same key are safe ONLY when
        their actual uncompressed SHA-256 hashes match. Compare just these
        rare aliases on demand; other entries need no decompression. For a
        repeated literal member name, a matched last-entry lookup is safe
        because ALL entries were verified byte-identical. Differing or
        unreadable aliases fail closed, while other assets remain available
        for automatic fallback. Source archives remain read-only.
        """
        refs = {}
        first_info = {}
        alias_groups = {}
        with zipfile.ZipFile(package) as archive:
            for info in archive.infolist():
                member = info.filename
                if member.endswith("/"):
                    continue
                try:
                    key = canonical_key(member)
                except AssetError:
                    continue
                if not key.startswith("content/"):
                    continue
                if key in refs:
                    alias_groups.setdefault(key, [first_info[key]]).append(info)
                else:
                    refs[key] = AssetRef(key, package, member)
                    first_info[key] = info
            conflicting = {key for key, infos in alias_groups.items()
                           if not AssetLibrary._archive_aliases_identical(archive, infos)}
        if duplicate_keys is None and conflicting:
            raise AssetError("Selected mod ZIP has duplicate logical asset paths with "
                             "different or unverifiable content: {} ({})".format(
                                 package, ", ".join(sorted(conflicting)[:8])))
        if duplicate_keys is not None:
            duplicate_keys.update(conflicting)
        return [(key, ref) for key, ref in refs.items() if key not in conflicting]

    def _register_archive_content(self, package):
        # _archive_refs preflights the whole archive before any selected
        # override is registered; partial or arbitrary duplicate wins forbidden.
        refs = self._archive_refs(package)
        for key, ref in refs:
            self._register(key, ref)
        return len(refs)

    def _index_fallback_mods(self):
        """Discover active mod_testing roots and Workshop ZIP members lazily.

        Only direct mod_testing/<mod>/content is indexed; nested folders
        such as mod_testing/_nah/<mod> are NOT implicitly activated.
        Their intent is unverified, and individual mods can be selected
        explicitly without merging an entire nested group.
        """
        if self._fallback_indexed:
            return
        self._fallback_indexed = True
        if self.mod_testing_root.is_dir():
            # Mod Testing can also hold directly staged *.cpk packages.
            # Index their content members only, read-only, as fallback data.
            for package in sorted(self.mod_testing_root.glob("*.cpk")):
                if not package.is_file() or package.is_symlink():
                    continue
                try:
                    duplicate_keys = set()
                    for key, ref in self._archive_refs(package, duplicate_keys):
                        self._fallback_candidates.setdefault(key, []).append(ref)
                    for key in duplicate_keys:
                        self.fallback_ambiguities.setdefault(key, []).append(
                            str(package) + " (duplicate archive paths)")
                        self._fallback_archive_alias_roots.setdefault(key, set()).add(
                            self._source_mod_root(AssetRef(key, package, key)))
                except (OSError, zipfile.BadZipFile):
                    continue
            for mod in sorted(self.mod_testing_root.iterdir(),
                              key=lambda p: p.name.lower()):
                if not mod.is_dir() or mod.is_symlink() or mod.name == "_nah":
                    continue
                content = mod / "content"
                if not content.is_dir() or content.is_symlink():
                    continue
                for path in sorted(content.rglob("*"), key=lambda p: str(p).lower()):
                    if path.is_file() and not path.is_symlink():
                        try:
                            key = canonical_key(
                                "content/" + path.relative_to(content).as_posix())
                        except AssetError:
                            continue
                        self._fallback_candidates.setdefault(key, []).append(
                            AssetRef(key, path))
        if self.workshop_root is None:
            return
        for item in sorted(self.workshop_root.iterdir(), key=lambda p: p.name):
            if not item.is_dir() or item.is_symlink() or not item.name.isdigit():
                continue
            for package in sorted(item.glob("*_legacy.bin")):
                if not package.is_file() or package.is_symlink():
                    continue
                try:
                    duplicate_keys = set()
                    for key, ref in self._archive_refs(package, duplicate_keys):
                        self._fallback_candidates.setdefault(key, []).append(ref)
                    for key in duplicate_keys:
                        self.fallback_ambiguities.setdefault(key, []).append(
                            str(package) + " (duplicate archive paths)")
                        self._fallback_archive_alias_roots.setdefault(key, set()).add(
                            self._source_mod_root(AssetRef(key, package, key)))
                except (OSError, zipfile.BadZipFile):
                    continue

    @staticmethod
    def _source_mod_root(ref):
        if ref.member is not None:
            # Directly staged mod_testing/*.cpk are independent packages, not
            # one giant mod merely because they share the same parent folder.
            # A Workshop *_legacy.bin is grouped by its selectable item root.
            if ref.source.suffix.lower() == ".cpk":
                return str(ref.source)
            return str(ref.source.parent)
        for parent in ref.source.parents:
            if parent.name.lower() == "content":
                return str(parent.parent)
        return str(ref.source.parent)

    @staticmethod
    def _sha256_identical_check(ref, max_bytes=256 * 1024 * 1024):
        """Hash original BYTES, not ZIP CRC, names, sizes or metadata.

        Only used on duplicate logical filenames; the bound prevents
        unexpected massive archive decompression during automatic selection.
        """
        def consume(stream):
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    return digest.hexdigest()
                size += len(chunk)
                if size > max_bytes:
                    raise AssetError("Mod asset exceeds automatic hash size limit")
                digest.update(chunk)

        if ref.member is None:
            if ref.source.stat().st_size > max_bytes:
                raise AssetError("Mod asset exceeds automatic hash size limit")
            with ref.source.open("rb") as stream:
                return consume(stream)
        with zipfile.ZipFile(ref.source) as archive:
            info = archive.getinfo(ref.member)
            if info.file_size > max_bytes:
                raise AssetError("Mod asset exceeds automatic hash size limit")
            with archive.open(info) as stream:
                return consume(stream)

    def _register_content_folder(self, content: Path, override=True):
        for path in sorted(content.rglob("*"), key=lambda p: str(p).lower()):
            if path.is_file() and not path.is_symlink():
                key = canonical_key("content/" + path.relative_to(content).as_posix())
                self._register(key, AssetRef(key, path), override=override)

    def _register(self, key: str, ref: AssetRef, override=True):
        old = self.sources.get(key)
        if old:
            self.collisions.setdefault(key, [str(old.source)]).append(str(ref.source))
        if override or old is None:
            self.sources[key] = ref

    def get(self, logical: str) -> AssetRef | None:
        key = canonical_key(logical)
        stock_or_override = self.sources.get(key)
        if stock_or_override is not None:
            return stock_or_override
        if key in self._fallback_choice:
            return self._fallback_choice[key]
        self._index_fallback_mods()
        # An in-archive duplicate is ambiguous even when another package
        # offers a unique copy: none can establish which content is intended.
        # Include ALL other available candidate roots in the owner's choices.
        if key in self.fallback_ambiguities:
            other_roots = [self._source_mod_root(ref)
                           for ref in self._fallback_candidates.get(key, ())]
            self.fallback_ambiguities[key] = sorted(set(
                self.fallback_ambiguities[key] + other_roots))
            return None
        candidates = self._fallback_candidates.get(key, ())
        if len(candidates) == 1:
            self.fallback_resolved.add(key)
            self._fallback_choice[key] = candidates[0]
            return candidates[0]
        if len(candidates) > 1:
            roots = sorted(set(self._source_mod_root(ref) for ref in candidates))
            try:
                hashes = {self._sha256_identical_check(ref) for ref in candidates}
            except (AssetError, OSError, KeyError, RuntimeError, zipfile.BadZipFile):
                hashes = set()
            if len(hashes) == 1:
                self.fallback_identical[key] = roots
                self.fallback_resolved.add(key)
                self._fallback_choice[key] = candidates[0]
                return candidates[0]
            # Different content, unreadable content or oversized copies:
            # never choose a package arbitrarily for the user's geometry.
            self.fallback_ambiguities[key] = roots
        return None

    def describe_source(self, ref: AssetRef) -> dict:
        """Explain the actual asset chosen, including archive member.

        This records tool provenance, not undocumented Crashday runtime mod
        precedence. An unresolved fallback is never labeled as selected.
        """
        key = canonical_key(ref.logical_name)
        if ref in self._dependency_source_reasons:
            reason = self._dependency_source_reasons[ref]
        elif self._fallback_choice.get(key) == ref:
            reason = ("SHA-256-identical mod fallback" if key in self.fallback_identical
                      else "unique mod fallback")
        elif any(root == ref.source or root in ref.source.parents
                 for root in self.mod_content_paths):
            reason = "explicit mod override"
        elif ref.member is not None and ref.source.parent == self.game / "data":
            reason = "vanilla game CPK"
        elif self.game / "data" / "content" in ref.source.parents:
            reason = "loose game content"
        else:
            reason = "selected asset (source category unverified)"
        return {"reason": reason, "package_or_file": str(ref.source),
                "archive_member": ref.member or ""}

    def _model_for_definition(self, model_name: str, parent: AssetRef):
        """Keep mod-owned CFL/CDO/VEG geometry in its own source family.

        Stock or explicitly selected assets still take precedence. Otherwise
        resolve models in the same mod family, even if another mod has a
        conflicting same-named P3D. A byte-identical definition in a second
        package can legitimately supply its missing model; verify duplicate
        model bytes before choosing. Unrelated-only models remain blocked.
        """
        keys = candidate_model_keys(model_name)
        parent_key = canonical_key(parent.logical_name)
        # A source-scoped VEG or inherited CFL may be selected directly from
        # a mod family without entering the global name-only choice cache.
        # Treat it as fallback for its P3D model dependencies nonetheless.
        parent_is_fallback = (self._fallback_choice.get(parent_key) == parent or
                              parent in self._fallback_candidates.get(parent_key, ()))
        parent_is_override = any(root == parent.source or root in parent.source.parents
                                 for root in self.mod_content_paths)
        if not (parent_is_fallback or parent_is_override):
            return next((ref for key in keys for ref in [self.get(key)]
                         if ref is not None), None)
        for key in keys:
            if key in self.sources:
                return self.sources[key]
        self._index_fallback_mods()
        own_root = self._source_mod_root(parent)
        # An identical definition copied into another mod is an equally valid
        # context for its model: the first sorted copy may be incomplete. Only
        # use these additional roots after get(parent) has actually verified
        # all duplicate definition bytes by bounded SHA-256.
        compatible_roots = {own_root}
        if parent_is_fallback and parent_key in self.fallback_identical:
            compatible_roots.update(self.fallback_identical[parent_key])
        for key in keys:
            related = [ref for ref in self._fallback_candidates.get(key, ())
                       if self._source_mod_root(ref) in compatible_roots]
            if not related:
                continue
            # An unsafe alias in an ELIGIBLE mod family is a blocker. Global
            # same-name differences in unrelated mods (or a prior get()) are
            # not: definition-scoped source selection must be order-independent.
            if self._fallback_archive_alias_roots.get(key, set()) & compatible_roots:
                return None
            if len(related) > 1:
                try:
                    hashes = {self._sha256_identical_check(ref) for ref in related}
                except (AssetError, OSError, KeyError, RuntimeError,
                        ValueError, zipfile.BadZipFile):
                    hashes = set()
                if len(hashes) != 1:
                    self.fallback_ambiguities[key] = sorted(set(
                        self._source_mod_root(ref)
                        for ref in self._fallback_candidates.get(key, ())))
                    return None
            chosen = related[0]
            self._dependency_source_reasons[chosen] = (
                "same-mod definition dependency"
                if self._source_mod_root(chosen) == own_root
                else "SHA-256-identical definition dependency")
            return chosen
        foreign = sorted(set(self._source_mod_root(ref) for key in keys
                             for ref in self._fallback_candidates.get(key, ())))
        if foreign:
            self.fallback_dependency_conflicts[parent.logical_name + " -> " + model_name] = {
                "definition": parent.logical_name,
                "definition_source": own_root,
                "other_model_sources": foreign,
            }
        return None

    def get_for_model(self, logical: str, model: AssetRef | None,
                      record_conflict=True) -> AssetRef | None:
        """Resolve a P3D material dependency without mixing unrelated mods.

        Vanilla/explicit sources retain precedence. For fallback-only textures
        referenced by a mod P3D, require the same mod family; a globally unique
        filename in an unrelated Workshop item is NOT sufficient evidence.
        This is also used for .tex shader parameters and inherited templates.
        """
        key = canonical_key(logical)
        if key in self.sources:
            return self.sources[key]
        if model is None:
            return self.get(key)
        # Stock model assets retain the normal global fallback behavior.
        if (model.member is not None and model.source.parent == self.game / "data"
                or model.member is None and
                self.game / "data" / "content" in model.source.parents):
            return self.get(key)
        self._index_fallback_mods()
        own_root = self._source_mod_root(model)
        related = [ref for ref in self._fallback_candidates.get(key, ())
                   if self._source_mod_root(ref) == own_root]
        if own_root in self._fallback_archive_alias_roots.get(key, set()):
            if record_conflict:
                self.fallback_texture_conflicts[model.logical_name + " -> " + key] = {
                    "definition": model.logical_name, "definition_source": own_root,
                    "other_model_sources": [own_root + " (unsafe archive aliases)"],
                }
            return None
        if related:
            if len(related) > 1:
                try:
                    hashes = {self._sha256_identical_check(ref) for ref in related}
                except (AssetError, OSError, KeyError, RuntimeError,
                        ValueError, zipfile.BadZipFile):
                    hashes = set()
                if len(hashes) != 1:
                    if record_conflict:
                        self.fallback_texture_conflicts[model.logical_name + " -> " + key] = {
                            "definition": model.logical_name, "definition_source": own_root,
                            "other_model_sources": [own_root + " (conflicting copies)"],
                        }
                    return None
            chosen = related[0]
            self._dependency_source_reasons[chosen] = "same-mod P3D texture dependency"
            return chosen
        foreign = sorted(set(self._source_mod_root(ref)
                             for ref in self._fallback_candidates.get(key, ())))
        if foreign and record_conflict:
            self.fallback_texture_conflicts[model.logical_name + " -> " + key] = {
                "definition": model.logical_name, "definition_source": own_root,
                "other_model_sources": foreign,
            }
        return None

    def texture_image_for_model(self, texture_name: str, model: AssetRef | None):
        """Prefer stock/selected textures, then same-mod images across extensions.

        Avoid recording a false error for a foreign .tga when the same mod has
        a valid .dds (or vice versa). Only report source conflicts if BOTH
        possible image extensions remain unresolved.
        """
        stem = (str(texture_name).rsplit(".", 1)[0]
                if str(texture_name).lower().endswith((".dds", ".tga"))
                else str(texture_name))
        candidates = ["content/textures/{}.tga".format(stem),
                      "content/textures/{}.dds".format(stem)]
        for path in candidates:
            ref = self.sources.get(canonical_key(path))
            if ref is not None:
                return ref
        for path in candidates:
            ref = self.get_for_model(path, model, record_conflict=False)
            if ref is not None:
                return ref
        for path in candidates:
            self.get_for_model(path, model, record_conflict=True)
        return None

    def _inherited_vegetation_asset(self, key, parent):
        """Resolve CFL vegetation inheritance in the parent's source family.

        A fallback CFL may share its filename with unrelated mods. Its
        SAME_VEGETATION_AS target is a dependency, not an unqualified global
        lookup. Vanilla assets and explicit overrides retain precedence.
        """
        if key in self.sources:
            return self.sources[key]
        self._index_fallback_mods()
        parent_key = canonical_key(parent.logical_name)
        selected_parent = any(root == parent.source or root in parent.source.parents
                              for root in self.mod_content_paths)
        fallback_parent = parent in self._fallback_candidates.get(parent_key, ())
        if not (selected_parent or fallback_parent):
            # Stock definitions continue to use ordinary vanilla-first,
            # unambiguous mod fallback rules for their optional aliases.
            return self.require(key)
        own_root = self._source_mod_root(parent)
        compatible = {own_root}
        # Only an automatically selected and verified byte-identical parent
        # may safely inherit a missing alias from its equivalent source copy.
        if fallback_parent and not selected_parent:
            compatible.update(self.fallback_identical.get(parent_key, ()))
        unsafe = self._fallback_archive_alias_roots.get(key, set()) & compatible
        if unsafe:
            raise AssetError("Unsafe vegetation CFL alias in matching mod: {} -> {} ({})".format(
                parent_key, key, " ; ".join(sorted(unsafe))))
        related = [ref for ref in self._fallback_candidates.get(key, ())
                   if self._source_mod_root(ref) in compatible]
        if related:
            if len(related) > 1:
                try:
                    hashes = {self._sha256_identical_check(ref) for ref in related}
                except (AssetError, OSError, KeyError, RuntimeError, ValueError,
                        zipfile.BadZipFile, EOFError):
                    hashes = set()
                if len(hashes) != 1:
                    raise AssetError(
                        "Ambiguous vegetation CFL dependency: {} -> {} (eligible mod sources: {})".format(
                            parent_key, key, " ; ".join(sorted(compatible))))
            chosen = related[0]
            self.fallback_resolved.add(key)
            self._dependency_source_reasons[chosen] = (
                "same-mod vegetation CFL dependency"
                if self._source_mod_root(chosen) == own_root
                else "SHA-256-identical parent vegetation CFL dependency")
            return chosen
        foreign = sorted(set(self._source_mod_root(ref)
                             for ref in self._fallback_candidates.get(key, ())))
        if foreign:
            raise AssetError(
                "Blocked cross-mod vegetation CFL inheritance: {} -> {} "
                "(parent source: {}; unrelated candidates: {})".format(
                    parent_key, key, own_root, " ; ".join(foreign)))
        raise AssetError("Missing asset: {} (vegetation CFL inherited by {})".format(
            key, parent_key))

    def require(self, logical: str) -> AssetRef:
        ref = self.get(logical)
        if ref is None:
            key = canonical_key(logical)
            if key in self.fallback_ambiguities:
                raise AssetError("Missing asset: {} (ambiguous mod sources: {}; "
                                 "select the correct mod folder)".format(
                                     logical, " ; ".join(self.fallback_ambiguities[key])))
            raise AssetError(f"Missing asset: {logical}")
        return ref

    def tile(self, cfl_name: str, _visited=None, _source_ref=None) -> TileDefinition:
        cfl_key = canonical_key(cfl_name)
        if "/" not in cfl_key:
            cfl_key = "content/tiles/" + cfl_key
        visited = set() if _visited is None else set(_visited)
        if cfl_key in visited:
            raise AssetError("CFL vegetation inheritance cycle: " + cfl_key)
        visited.add(cfl_key)
        asset = _source_ref if _source_ref is not None else self.require(cfl_key)
        parsed = parse_cfl(asset.read_bytes(), cfl_key)
        model = self._model_for_definition(parsed.model_file, asset)
        vegetation = parsed.vegetation
        vegetation_source = asset
        if parsed.vegetation_alias:
            alias_key = canonical_key(parsed.vegetation_alias)
            if "/" not in alias_key:
                alias_key = "content/tiles/" + alias_key
            alias_source = self._inherited_vegetation_asset(alias_key, asset)
            self.vegetation_alias_sources[cfl_key + " -> " + alias_key] = (
                self.describe_source(alias_source))
            inherited = self.tile(alias_key, visited, alias_source)
            vegetation = inherited.vegetation
            vegetation_source = inherited.vegetation_source_ref or alias_source
        return TileDefinition(
            parsed.tile_file, parsed.model_file, parsed.width, parsed.depth,
            parsed.ground_type, parsed.bend_rules, model, vegetation,
            parsed.vegetation_alias, asset, vegetation_source
        )

    def vegetation(self, veg_name: str, parent_source: AssetRef | None = None) -> VegetationDefinition:
        """Resolve VEG and its P3D within the actual originating CFL family.

        With no parent, retain the original unqualified standalone lookup.
        Stock/explicit sources still win, and unrelated-mod fallback is
        blocked if a CFL parent was provided.
        """
        key = canonical_key(veg_name)
        if "/" not in key:
            key = "content/vegetat/" + key
        veg_source = (self._inherited_vegetation_asset(key, parent_source)
                      if parent_source is not None else self.require(key))
        parsed = parse_veg(veg_source.read_bytes(), key)
        model = self._model_for_definition(parsed.model_file, veg_source)
        return VegetationDefinition(parsed.veg_file, parsed.model_file,
                                    parsed.min_scale, parsed.max_scale,
                                    parsed.rotation, parsed.height_offset,
                                    parsed.render_color, parsed.min_copies,
                                    parsed.max_copies, parsed.orient_to_ground,
                                    model, veg_source)

    def ambience(self, ambience_name: str) -> AmbienceDefinition:
        """Resolve an original track's selected AMB directly from game assets."""
        key = canonical_key(ambience_name)
        if "/" not in key:
            key = "content/ambience/" + key
        return parse_ambience(self.require(key).read_bytes(), key)

    def texture_shader_info(self, texture_name: str, model: AssetRef | None = None):
        """Resolve native .tex template inheritance without changing rendering.

        The original texture's alpha flag and material class remain its own;
        use_shaderparams_from inherits ONLY the referenced shader parameters.
        Unknown source .tex returns None. Broken/cyclic templates raise an
        explicit AssetError instead of inventing a shader or opacity.
        """
        name = canonical_key(texture_name)
        if name.endswith((".dds", ".tga", ".tex")):
            name = name.rsplit(".", 1)[0]
        if not name.startswith("content/textures/"):
            name = "content/textures/" + name
        return self._resolve_tex_shader(name + ".tex", set(), model)

    def _resolve_tex_shader(self, key, visited, model=None):
        key = canonical_key(key)
        if key in visited:
            raise AssetError("Cyclic .tex shader template: " + key)
        if len(visited) >= 16:
            raise AssetError("Excessive .tex shader template depth: " + key)
        ref = self.get_for_model(key, model)
        if ref is None:
            if not visited:
                return None
            raise AssetError("Missing referenced .tex shader template: " + key)
        raw = parse_tex_shader(ref.read_bytes(), key)
        if raw.authored_shader != "use_shaderparams_from":
            return raw
        if not raw.parameters:
            raise AssetError("Missing .tex template target: " + key)
        target = canonical_key(raw.parameters[0])
        if not target.endswith(".tex"):
            raise AssetError("Invalid .tex template extension in " + key)
        if not target.startswith("content/textures/"):
            target = "content/textures/" + target
        resolved = self._resolve_tex_shader(target, visited | {key}, model)
        return TextureShaderInfo(key, raw.has_alpha, raw.material_type,
                                 raw.authored_shader, resolved.effective_shader,
                                 resolved.shader_source, resolved.parameters,
                                 resolved.trailing_tokens)

    def texture_alpha_mode(self, texture_name: str, model: AssetRef | None = None):
        """Recognize alpha-tested textures from installed read-only .tex files.

        Return None on absent or non-cutout shaders, especially alpha-as-
        specular cases. The format is parsed without loading images or
        modifying the installed game. No recursive alias guessing.
        """
        name = canonical_key(texture_name)
        if name.lower().endswith((".dds", ".tga")):
            name = name.rsplit(".", 1)[0]
        if not name.startswith("content/textures/"):
            name = "content/textures/" + name
        ref = self.get_for_model(name + ".tex", model)
        return parse_tex_alpha_mode(ref.read_bytes()) if ref is not None else None

    def dynamic(self, cdo_name: str) -> DynamicDefinition:
        """Resolve a dynamic CDO and its preferred high-detail P3D, read-only."""
        cdo_key = canonical_key(cdo_name)
        if "/" not in cdo_key:
            cdo_key = "content/dynamics/" + cdo_key
        cdo_source = self.require(cdo_key)
        parsed = parse_cdo(cdo_source.read_bytes(), cdo_key)
        high = self._model_for_definition(parsed.high_model_file, cdo_source)
        low = None
        if high is None:
            low = self._model_for_definition(parsed.low_model_file, cdo_source)
        return DynamicDefinition(parsed.cdo_file, parsed.low_model_file,
                                 parsed.high_model_file, parsed.material, parsed.mass,
                                 parsed.center_of_mass, high or low,
                                 high is None and low is not None, cdo_source)

    def report_tiles(self, names: Iterable[str]) -> dict:
        missing_cfl, missing_p3d, malformed, resolved = [], [], [], []
        for name in sorted(set(names), key=str.lower):
            try:
                definition = self.tile(name)
            except AssetError as error:
                if str(error).startswith("Missing asset"):
                    missing_cfl.append(name)
                else:
                    malformed.append({"file": name, "error": str(error)})
                continue
            if definition.model is None and definition.model_file:
                missing_p3d.append({"file": name, "model": definition.model_file})
            else:
                resolved.append({
                    "file": name, "model": definition.model_file,
                    "size": [definition.width, definition.depth],
                    "bend": definition.bend_rules,
                    "source": str(definition.model.source) if definition.model else None,
                })
        return {
            "resolved": resolved,
            "missing_cfl": missing_cfl,
            "missing_p3d": missing_p3d,
            "malformed_cfl": malformed,
            "content_assets_indexed": len(self.sources),
            "overlapping_asset_names": len(self.collisions),
        }
