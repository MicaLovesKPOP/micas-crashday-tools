"""Opt-in, owner-authored native .tex *preset* staging for private worlds.

No shader is inferred from source image appearance, filenames, geometry, or
Blender materials. The owner must supply an explicit UTF-8 preset policy tied
to the exact source TGA manifest SHA-256. Native token forms were checked
against original read-only Redline .tex examples (debris1, diner2, tree, b1);
engine behaviour / look and authored material appropriateness are UNTESTED.

Python 3.7+, stdlib only. No game/Steam writes, no proprietary template copy.
"""
import hashlib
import json
from pathlib import Path

from .source_tga_stage import NATIVE_ID, SHA256

POLICY_SCHEMA = "micas-crashday-private-tex-presets/v1"
MAX_POLICY_BYTES = 128 * 1024
# Canonical token sequences with known native precedents. The ordinary
# rgba-diffuse preset uses 'has_alpha' even if source TGA alpha is fully opaque.
# Do not infer alpha semantics from a 32-bit TGA or silently assign materials.
# Preserve the native on-disk line framing, not only the logical tokens.
# The original game's .tex files use CRLF, inline material/shader comments and
# trailing blank lines. A compact LF-only version passed our source parser
# but the original game rejected it before rendering (2026-11-22).
# These are short functional shader directives, not shipped texture assets.
PRESETS = {
    "rgba_diffuse": (
        b"has_alpha\r\n# has mipmaps\r\n"
        b"default\t\t\t # ingame material type\r\n"
        b"diffuse     \t\t# shader type\r\n\r\n\r\n\r\n"
    ),
    "alpha_test": (
        b"has_alpha\r\n# has mipmaps\r\n"
        b"default\t\t # ingame material type\r\n"
        b"alphatest      # shader type\r\n\r\n\r\n\r\n"
    ),
    "foliage_double_sided": (
        b"has_alpha\r\n# has mipmaps\r\n"
        b"tree\t\t\t # ingame material type\r\n"
        b"alphatestdoubleside      # shader type\r\n\r\n\r\n\r\n"
    ),
    "standard_no_mips": (
        b"has_alpha\r\ndisable_mipmapping\r\n"
        b"STANDARD     # shader file is not executed any further. Standard material is used instead\r\n"
        b"\r\n\r\n"
    ),
}


class SourceTEXError(ValueError):
    pass


def render_preset(name):
    if not isinstance(name, str) or name not in PRESETS:
        raise SourceTEXError("Unknown or unsafe native TEX preset: {!r}".format(name))
    return PRESETS[name]


def policy_descriptors(tex_manifest_path, verified_tgas, policy_path):
    """Return SHA-verified explicit preset records; never invent TEX choices.

    An optional null default means only listed overrides are authored; all
    other textures remain visibly uncovered. Overrides cannot reference art
    missing from the source TGA manifest. Reject stale or aliased policies.
    """
    policy = Path(policy_path)
    source = Path(tex_manifest_path).resolve(strict=True)
    if (policy.is_symlink() or not policy.is_file() or
            policy.resolve(strict=True).parent != source.parent or
            policy.stat().st_size > MAX_POLICY_BYTES):
        raise SourceTEXError("TEX policy must be a bounded, regular file beside source TGA manifest")
    raw = policy.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SourceTEXError("Malformed UTF-8 TEX policy") from exc
    if (not isinstance(data, dict) or set(data) !=
            {"schema", "source_tga_manifest_sha256", "default_preset", "overrides"} or
            data["schema"] != POLICY_SCHEMA or
            data["source_tga_manifest_sha256"] != verified_tgas["manifest_sha256"]):
        raise SourceTEXError("Unknown or stale TEX policy/schema/manifest digest")
    default, overrides = data["default_preset"], data["overrides"]
    if default is not None:
        render_preset(default)
    if not isinstance(overrides, dict) or len(overrides) > 8192:
        raise SourceTEXError("Invalid TEX per-texture overrides")
    available = {item["texture"] for item in verified_tgas["records"]}
    for name, preset in overrides.items():
        if (not isinstance(name, str) or not NATIVE_ID.fullmatch(name) or
                name not in available):
            raise SourceTEXError("TEX override not backed by an original P3D/TGA: " + str(name))
        render_preset(preset)
    result = []
    for name in sorted(available):
        preset = overrides.get(name, default)
        if preset is None:
            continue
        # Every whitelisted stock-grounded preset explicitly says has_alpha.
        # Never apply one to a legal but 24-bit TGA with no alpha channel.
        image = source.parent / "textures" / (name + ".tga")
        if image.is_symlink() or not image.is_file():
            raise SourceTEXError("Selected TEX preset lacks a real source image")
        with image.open("rb") as stream:
            header = stream.read(18)
        if len(header) != 18 or header[16] != 32:
            raise SourceTEXError(
                "Selected native TEX alpha-bearing preset requires 32-bit source TGA: " + name)
        body = render_preset(preset)
        result.append({"texture": name, "file": name + ".tex", "preset": preset,
                       "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    return {"records": result, "missing": sorted(available - {x["texture"] for x in result}),
            "policy_sha256": hashlib.sha256(raw).hexdigest(),
            "source_tga_manifest_sha256": verified_tgas["manifest_sha256"]}


def verify_staged_tex(stage_root, plan):
    """Check exact generated bytes and model-TGA affinity before a new draft."""
    records = plan.get("native_tex_descriptors")
    keys = ("native_tex_policy_sha256", "native_tex_source_manifest_sha256",
            "unresolved_native_tex_refs")
    if records is None:
        if any(key in plan for key in keys):
            raise SourceTEXError("Partial native TEX stage provenance")
        return []
    if (not isinstance(records, list) or len(records) > 8192 or
            not all(isinstance(plan.get(k), (str, list)) for k in keys)):
        raise SourceTEXError("Malformed native TEX stage provenance")
    sha = plan["native_tex_policy_sha256"]
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise SourceTEXError("Invalid native TEX policy SHA-256")
    if plan["native_tex_source_manifest_sha256"] != plan.get("source_texture_manifest_sha256"):
        raise SourceTEXError("Native TEX source manifest digest differs from TGA stage")
    refs = plan.get("source_native_texture_refs")
    if not isinstance(refs, list):
        raise SourceTEXError("Native TEX stage lacks source artwork references")
    source_images = {row["texture"] for row in plan.get("source_textures", [])
                     if isinstance(row, dict) and isinstance(row.get("texture"), str)}
    uncovered = plan["unresolved_native_tex_refs"]
    if (not isinstance(uncovered, list) or len(uncovered) != len(set(uncovered)) or
            any(not isinstance(k, str) or k not in refs for k in uncovered)):
        raise SourceTEXError("Invalid native TEX unresolved references")
    folder = Path(stage_root) / "content" / "textures"
    if records and (folder.is_symlink() or not folder.is_dir()):
        raise SourceTEXError("Missing/redirected staged native TEX directory")
    names = set()
    verified = []
    for record in records:
        if not isinstance(record, dict):
            raise SourceTEXError("Malformed staged TEX record")
        name, preset = record.get("texture"), record.get("preset")
        if (not isinstance(name, str) or not NATIVE_ID.fullmatch(name) or
                name in names or name not in source_images or
                record.get("file") != name + ".tex"):
            raise SourceTEXError("Invalid/duplicate staged native TEX identity")
        body = render_preset(preset)
        if (record.get("bytes") != len(body) or type(record.get("bytes")) is not int or
                record.get("sha256") != hashlib.sha256(body).hexdigest()):
            raise SourceTEXError("Staged TEX metadata differs from explicit native preset")
        path = folder / record["file"]
        if (path.is_symlink() or not path.is_file() or
                path.resolve(strict=True).parent != folder.resolve(strict=True) or
                path.stat().st_size != len(body) or path.read_bytes() != body):
            raise SourceTEXError("Staged native TEX bytes were modified: " + name)
        names.add(name)
        verified.append(("textures", record["file"], body, record["sha256"]))
    if (set(uncovered) != set(refs) - names or
            names.intersection(uncovered) or
            not names.issubset(source_images)):
        raise SourceTEXError("Native TEX resolved/unresolved source refs disagree")
    return verified


def create_explicit_policy(tile_manifest, texture_manifest, output_path, chosen_default):
    """Create a new author-editable JSON preset map; NEVER choose a shader."""
    from .source_tga_stage import verify_source_tga_manifest
    if chosen_default != "none":
        render_preset(chosen_default)
    verified = verify_source_tga_manifest(tile_manifest, texture_manifest)
    output = Path(output_path)
    source_dir = Path(texture_manifest).resolve(strict=True).parent
    if (output.is_symlink() or output.exists() or
            output.parent.resolve(strict=True) != source_dir or
            output.name != "tile_tex_policy.json"):
        raise SourceTEXError("TEX policy must be NEW tile_tex_policy.json beside original TGA manifest")
    policy = {"schema": POLICY_SCHEMA,
              "source_tga_manifest_sha256": verified["manifest_sha256"],
              "default_preset": None if chosen_default == "none" else chosen_default,
              "overrides": {}}
    raw = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with output.open("xb") as stream:
        stream.write(raw)
    return {"policy_path": str(output), "sha256": hashlib.sha256(raw).hexdigest(),
            "source_image_count": len(verified["records"]),
            "source_images_missing": len(verified["unresolved"]),
            "explicit_default_preset": chosen_default,
            "engine_material_validation": "NOT TESTED"}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-manifest", required=True)
    parser.add_argument("--texture-manifest", required=True)
    parser.add_argument("--make-policy", action="store_true", required=True,
                        help="Create a new author-editable policy; never infer source materials")
    parser.add_argument("--default-preset", required=True,
                        choices=["none"] + sorted(PRESETS),
                        help="EXPLICIT chosen default; none means per-image choices only")
    arguments = parser.parse_args(argv)
    target = Path(arguments.texture_manifest).parent / "tile_tex_policy.json"
    try:
        print(json.dumps(create_explicit_policy(
            arguments.tile_manifest, arguments.texture_manifest, target,
            arguments.default_preset), sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print("INVALID OR EXISTING PRIVATE TEX POLICY: " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
