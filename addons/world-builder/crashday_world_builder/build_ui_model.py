"""Safe Blender-to-private-offline World Builder bridge (Python 3.7 stdlib).

An embedded, self-contained backend is mandatory for actual execution. The UI
never guesses locations of a developer checkout, installed game, or mods.
No .cat generation or native game launch. Permission encoding is statically verified.
"""
from contextlib import contextmanager
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile

BACKEND_FILES = (
    "build_route_world.py", "author_trk_offline.py", "checkpoint_routes.py",
    "stage_tile_cfls.py", "macro_native_layout.py", "validate_p3d.py", "source_tga_stage.py",
    "source_tex_stage.py", "source_tile_preview_stage.py",
    "native_tile_preview.py",
)
_MAX_ROUTE = 65536

# Verified native editor checkbox / TRK byte mapping (2026-11-24 evidence).
GAME_MODES = (
    ("allow_wrecking", "Wrecking Match", 0x01),
    ("allow_stunt", "Stunt Show", 0x02),
    ("allow_bomb_run", "Bomb Run", 0x04),
    ("allow_lap_race", "Race (Laps / Knockout)", 0x08),
    ("allow_hold_flag", "Hold the Flag", 0x10),
    ("allow_pass_bomb", "Pass the Bomb", 0x20),
    ("allow_point_to_point", "Race (Point-to-Point)", 0x40),
)


def game_mode_mask(settings):
    # New worlds keep the old writer's high bit. Future importers can restore
    # the existing track's actual high bit; it has no exposed semantic toggle.
    mask = 0x80 if settings.game_mode_unknown_bit else 0
    for name, _label, bit in GAME_MODES:
        if getattr(settings, name):
            mask |= bit
    return mask


class BuildUIError(ValueError):
    pass


@contextmanager
def bundled_backend(folder):
    """Load a pinned local backend; never fall back to another project checkout."""
    root = Path(folder)
    if not root.is_dir() or root.is_symlink():
        raise BuildUIError("Offline World Builder backend is not installed in this add-on")
    root = root.resolve(strict=True)
    # Private sibling modules live under this add-on's own package. Neither
    # sys.path nor another add-on's top-level imports need to be modified.
    if root != (Path(__file__).resolve().parent / "offline_backend"):
        raise BuildUIError("Offline World Builder backend is outside this add-on")
    if not __package__:
        raise BuildUIError("World Builder must be loaded as a Blender add-on package")
    prefix = __package__ + ".offline_backend"
    for name in ("__init__.py",) + BACKEND_FILES:
        file = root / name
        if not file.is_file() or file.is_symlink() or file.resolve().parent != root:
            raise BuildUIError("Missing or redirected bundled backend file: " + name)
        module_name = prefix if name == "__init__.py" else prefix + "." + name[:-3]
        module = sys.modules.get(module_name)
        if module is not None:
            origin = getattr(module, "__file__", None)
            if origin is None or Path(origin).resolve() != file:
                raise BuildUIError("Conflicting private backend module: " + module_name)
    backend = importlib.import_module(prefix + ".build_route_world")
    if Path(backend.__file__).resolve() != root / "build_route_world.py":
        raise BuildUIError("Offline World Builder resolved outside the add-on")
    yield backend


def private_validate_or_build(tile_manifest, route_payload, output_root, label,
                              backend, write=False, texture_manifest=None,
                              tex_policy=None, tile_preview_manifest=None,
                              scenery_profile="highlands", game_mode_mask=255,
                              expected_source_manifest_sha256=None, checkpoint_mode="AUTHORED",
                              macro_layout=False, void_cfl=None, void_p3d=None):
    """One verified route preflight and optionally a complete PRIVATE world build.

    The scene's route is serialized into an exclusively owned temporary file in
    the explicitly selected safe output directory, not next to the source P3Ds.
    It is removed after the backend returns. The full native build remains an
    immutable NEW directory; no replacement/overwrite mode exists.
    """
    if not isinstance(output_root, str) or not output_root.strip():
        raise BuildUIError("Choose an existing private output directory")
    if not Path(output_root).is_absolute():
        raise BuildUIError("Choose an absolute private output directory")
    root = backend.native._private_output_root(output_root)
    backend._label(label)
    if checkpoint_mode not in ("AUTHORED", "NONE", "STOCK"):
        raise BuildUIError("Unknown checkpoint route mode")
    if checkpoint_mode == "AUTHORED":
        if not isinstance(route_payload, bytes) or not 1 <= len(route_payload) <= _MAX_ROUTE:
            raise BuildUIError("Canonical checkpoint route must be 1..65536 bytes")
    elif route_payload is not None:
        raise BuildUIError("A checkpoint route cannot be discarded by choosing another mode")
    opts = dict(texture_manifest=texture_manifest,
                tex_policy=tex_policy,
                tile_preview_manifest=tile_preview_manifest,
                scenery_profile=scenery_profile, game_mode_mask=game_mode_mask,
                checkpoint_mode=checkpoint_mode, macro_layout=macro_layout,
                void_cfl=void_cfl, void_p3d=void_p3d)
    temporary = None
    owned_identity = None
    owned_digest = hashlib.sha256(route_payload).hexdigest() if route_payload is not None else None
    try:
        if route_payload is not None:
            fd, name = tempfile.mkstemp(prefix=".micas_world_route_", suffix=".json", dir=str(root))
            temporary = Path(name)
            owned_identity = temporary.stat().st_ino
            with os.fdopen(fd, "wb") as stream:
                stream.write(route_payload)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != owned_digest:
                raise BuildUIError("Temporary route changed during private preflight")
        preview = backend.inspect_route_world(tile_manifest, temporary, **opts)
        if preview["route_sha256"] != owned_digest:
            raise BuildUIError("Checkpoint route changed during Blender preflight; retry")
        if (expected_source_manifest_sha256 is not None and
                preview["source_manifest_sha256"] != expected_source_manifest_sha256):
            raise BuildUIError("Exported tile manifest changed; refresh it before building")
        if not write:
            return {"status": "PRIVATE OFFLINE VALIDATED; GAME COLLISION NOT VERIFIED",
                    "preview": preview}
        target, receipt = backend.build_route_world(
            tile_manifest, temporary, label, workspace=str(root),
            expected_preflight=preview, **opts)
        return {"status": "PRIVATE OFFLINE BUILT; GAME COLLISION NOT VERIFIED",
                "private_directory": str(target),
                "preview": preview,
                "receipt": receipt}
    finally:
        if temporary is not None:
            try:
                if (temporary.is_file() and not temporary.is_symlink()
                        and temporary.stat().st_ino == owned_identity
                        and hashlib.sha256(temporary.read_bytes()).hexdigest() == owned_digest):
                    temporary.unlink()
            except OSError:
                # Never remove a replaced or rewritten file owned by another task.
                pass
