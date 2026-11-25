"""Offline, read-only Crashday CFL/VEG/P3D dependency preflight.

Checks a *single* self-contained authoring content tree or ZIP-family package.
Default missing references are clearly UNRESOLVED (may be supplied by vanilla or
another explicit mod). --require-self-contained makes them errors. Neither mode
claims actual game engine compatibility. Python 3.7+, stdlib + local parsers.
"""
import argparse
import json
import os
from pathlib import Path
import zipfile

from asset_library import parse_cfl, parse_veg, AssetError

MAX_ARCHIVE_SIZE = 512 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 20000
MAX_DEPTH = 12
MAX_FILES = 12000
MAX_SOURCE_BYTES = 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 128 * 1024 * 1024


class UnsafeSource(ValueError):
    pass


def native_cfl_framing_issue(raw):
    """Conservative opt-in native-game line framing gate.

    All 269 original dat006.cpk CFL records inspected use CRLF. The
    Crashday Track Editor rejects some LF-only authoring fixtures, although
    a tolerant Python parser accepts them. This catches that mismatch
    without asserting that line endings explain every native engine error.
    """
    if b"\r\n" not in raw:
        return "No native CRLF records (LF-only/bare-CR CFL); regenerate using CRLF"
    if (raw.count(b"\n") != raw.count(b"\r\n") or
            b"\r" in raw.replace(b"\r\n", b"")):
        return "Mixed LF/CRLF/bare-CR records; regenerate using CRLF throughout"
    # Native stock CFLs do not all end in a final newline; 34/269 in
    # dat006.cpk have valid CRLF records but NO terminal delimiter.
    return None


def key_for(name):
    """Canonical safe archive-relative path, with case-insensitive lookup."""
    if not isinstance(name, str) or not name or any(ord(ch) < 32 for ch in name):
        raise UnsafeSource("Unsafe name")
    if "\\" in name or name.startswith("/") or ":" in name:
        raise UnsafeSource("Unsafe path prefix/separator")
    parts = name.split("/")
    if any(segment in ("", ".", "..") for segment in parts):
        raise UnsafeSource("Unsafe path component")
    return "/".join(parts).casefold()


def normalize_content_path(name, subdir):
    name = key_for(name)
    if not name.startswith("content/"):
        if "/" in name:
            raise UnsafeSource("Relative asset reference contains unsupported directory: " + name)
        name = "content/" + subdir + "/" + name
    # A definition outside the supported parser scope must not count as
    # resolved just because a file with that name exists. This is a tool
    # scope check, not a claim about arbitrary native engine paths.
    suffix = {"tiles": ".cfl", "vegetat": ".veg"}.get(subdir)
    if suffix and (not name.startswith("content/" + subdir + "/")
                   or not name.endswith(suffix)):
        raise UnsafeSource("Unsupported definition path for " + subdir + ": " + name)
    if subdir == "models" and (not name.startswith("content/models/")
                               or not name.endswith(".p3d")):
        raise UnsafeSource("Unsupported P3D model path: " + name)
    return name


def _index_archive(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_ARCHIVE_SIZE:
        raise UnsafeSource("Archive symlink or exceeds 512 MiB")
    with zipfile.ZipFile(str(path)) as z:
        files = {}
        entries = z.infolist()
        if len(entries) > MAX_DIRECTORY_ENTRIES:
            raise UnsafeSource("Archive exceeds 20,000 entries")
        source_total = 0
        for entry in entries:
            if entry.is_dir():
                continue
            k = key_for(entry.filename)
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise UnsafeSource("Symlink primary ZIP member: " + k)
            if k in files:
                raise UnsafeSource("Duplicate case-insensitive archive member: " + k)
            if entry.file_size < 0 or entry.file_size > MAX_SOURCE_BYTES and k.endswith((".cfl", ".veg")):
                raise UnsafeSource("Oversized CFL/VEG member: " + k)
            files[k] = entry
            if len(files) > MAX_FILES:
                raise UnsafeSource("Too many archive files")
            if k.endswith((".cfl", ".veg")):
                source_total += entry.file_size
                if source_total > MAX_TOTAL_SOURCE_BYTES:
                    raise UnsafeSource("Archive CFL/VEG budget exceeded")
        if not any(k.startswith("content/tiles/") and k.endswith(".cfl") for k in files):
            raise UnsafeSource("No content/tiles/*.cfl definitions in archive")
        payload = {}
        for k, entry in files.items():
            if k.endswith((".cfl", ".veg")):
                with z.open(entry, "r") as source:
                    raw = source.read(MAX_SOURCE_BYTES + 1)
                if len(raw) != entry.file_size or len(raw) > MAX_SOURCE_BYTES:
                    raise UnsafeSource("CFL/VEG archive member size mismatch: " + k)
                payload[k] = raw
        return payload, set(files)


def _index_directory(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise UnsafeSource("Content directory missing or symlink")
    root = path.resolve(strict=True)
    if root.name.casefold() != "content":
        raise UnsafeSource("Select the content directory itself, not its parent")
    root_real = os.path.normcase(os.path.realpath(str(root)))
    pending = [(root, 0)]
    names = {}
    nentries = 0
    source_total = 0
    while pending:
        parent, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise UnsafeSource("Content directory exceeds 12 levels")
        with os.scandir(str(parent)) as it:
            entries = list(it)
        entries.sort(key=lambda e: (e.name.casefold(), e.name))
        for entry in entries:
            nentries += 1
            if nentries > MAX_DIRECTORY_ENTRIES:
                raise UnsafeSource("Content directory exceeds 20,000 entries")
            if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                raise UnsafeSource("Symlink or Windows reparse point: " + entry.name)
            real = os.path.normcase(os.path.realpath(entry.path))
            try:
                if os.path.commonpath((root_real, real)) != root_real:
                    raise UnsafeSource("Path escapes content root")
            except ValueError:
                raise UnsafeSource("Source crosses filesystem root")
            item = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                pending.append((item, depth + 1))
            elif entry.is_file(follow_symlinks=False):
                rel = item.relative_to(root).as_posix()
                k = key_for("content/" + rel)
                if k in names:
                    raise UnsafeSource("Duplicate case-insensitive path: " + k)
                if len(names) >= MAX_FILES:
                    raise UnsafeSource("Too many content files")
                size = entry.stat(follow_symlinks=False).st_size
                if size < 0:
                    raise UnsafeSource("Invalid source file size")
                if k.endswith((".cfl", ".veg")):
                    if size > MAX_SOURCE_BYTES:
                        raise UnsafeSource("Oversized CFL/VEG: " + k)
                    source_total += size
                    if source_total > MAX_TOTAL_SOURCE_BYTES:
                        raise UnsafeSource("CFL/VEG directory budget exceeded")
                names[k] = item
    if not any(k.startswith("content/tiles/") and k.endswith(".cfl") for k in names):
        raise UnsafeSource("No content/tiles/*.cfl in selected root")
    payload = {}
    for k, item in names.items():
        if k.endswith((".cfl", ".veg")):
            if item.is_symlink() or os.path.commonpath((
                    root_real, os.path.normcase(os.path.realpath(str(item))))) != root_real:
                raise UnsafeSource("Source redirected during scan")
            with item.open("rb") as f:
                raw = f.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                raise UnsafeSource("CFL/VEG grew after scan: " + k)
            payload[k] = raw
    return payload, set(names)


def _index_reference_archives(paths):
    """Bounded *read-only* filename inventory for explicitly selected ZIP CPKs.

    Reference sources need not contain CFLs (native VEG data often lives in
    separate stock archives). Do not automatically discover/merge Workshop mods.
    """
    if len(paths) > 12:
        raise UnsafeSource("At most 12 explicit reference archives may be selected")
    seen_paths = set()
    lookup = {}
    for supplied in paths:
        path = Path(supplied)
        if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400:
            raise UnsafeSource("Reference archive is a symlink/reparse point")
        resolved = path.resolve(strict=True)
        if resolved in seen_paths:
            raise UnsafeSource("Duplicate reference archive path")
        seen_paths.add(resolved)
        if path.stat().st_size > MAX_ARCHIVE_SIZE:
            raise UnsafeSource("Reference archive exceeds 512 MiB")
        with zipfile.ZipFile(str(path)) as z:
            entries = z.infolist()
            if len(entries) > MAX_DIRECTORY_ENTRIES:
                raise UnsafeSource("Reference archive exceeds 20,000 entries")
            keys = set()
            nfiles = 0
            for info in entries:
                if info.is_dir():
                    continue
                key = key_for(info.filename)
                if key in keys:
                    raise UnsafeSource("Duplicate case-insensitive reference member: " + key)
                keys.add(key)
                nfiles += 1
                if nfiles > MAX_FILES:
                    raise UnsafeSource("Reference archive exceeds 12,000 files")
                # A symlink is not a usable source data file.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UnsafeSource("Symlink reference ZIP member: " + key)
                lookup.setdefault(key, []).append((str(path), info))
    return lookup


def _reference_digest(path, info, budget):
    """Read actual uncompressed bytes, bounded, not CRC/name-only equivalence."""
    if info.file_size > 64 * 1024 * 1024 or info.file_size < 0:
        raise UnsafeSource("Ambiguous referenced asset is too large to hash safely")
    if budget[0] + info.file_size > 256 * 1024 * 1024:
        raise UnsafeSource("Reference collision-hash budget exceeded")
    import hashlib
    h = hashlib.sha256()
    total = 0
    with zipfile.ZipFile(str(path)) as z:
        current = z.getinfo(info.filename)
        if (current.file_size, current.CRC) != (info.file_size, info.CRC):
            raise UnsafeSource("Reference archive changed during lookup")
        with z.open(current) as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > info.file_size:
                    raise UnsafeSource("Reference member exceeded declared size")
                h.update(chunk)
    if total != info.file_size:
        raise UnsafeSource("Incomplete reference asset")
    budget[0] += total
    return h.hexdigest()


def _read_external_text(path, info, source_budget):
    """Read a *selected* external CFL/VEG using bounded uncompressed bytes.

    ZIP metadata is not proof of bytes. This is not extraction and never
    reads arbitrary P3Ds/textures just to resolve an external vegetation graph.
    """
    if info.file_size < 0 or info.file_size > MAX_SOURCE_BYTES:
        raise UnsafeSource("Oversized referenced CFL/VEG: " + info.filename)
    if source_budget[0] + info.file_size > MAX_TOTAL_SOURCE_BYTES:
        raise UnsafeSource("Referenced CFL/VEG aggregate source budget exceeded")
    with zipfile.ZipFile(str(path), "r") as archive:
        current = archive.getinfo(info.filename)
        if (current.file_size, current.CRC) != (info.file_size, info.CRC):
            raise UnsafeSource("Reference source changed during lookup")
        with archive.open(current) as stream:
            payload = stream.read(MAX_SOURCE_BYTES + 1)
    if len(payload) != info.file_size or len(payload) > MAX_SOURCE_BYTES:
        raise UnsafeSource("Referenced CFL/VEG uncompressed length mismatch")
    source_budget[0] += len(payload)
    return payload


def _expand_external_native_definitions(links, parsed_cfl, names, refs,
                                        native_cfl_framing=False):
    """Expand only actually referenced external CFL/VEG, never entire mods.

    All selected conflicting candidates must have identical decompressed bytes
    before one is chosen. Primary names always win. Output includes a bounded
    provenance list, so a PASS can be audited without guessing which mod won.
    """
    pending = sorted(set(target for dependencies in links.values()
                         for _relation, target in dependencies
                         if target not in names and target in refs and
                         target.endswith((".cfl", ".veg"))))
    read_budget = [0]
    hash_budget = [0]
    seen = set()
    chosen = []
    while pending:
        target = pending.pop(0)
        if target in seen or target in names:
            continue
        seen.add(target)
        matches = refs.get(target)
        if not matches:
            continue
        if len(matches) > 1:
            hashes = []
            for path, member in matches:
                if member.file_size > MAX_SOURCE_BYTES:
                    raise UnsafeSource("Ambiguous oversized reference: " + target)
                hashes.append(_reference_digest(path, member, hash_budget))
            if len(set(hashes)) != 1:
                raise UnsafeSource("Conflicting external source definition: " + target)
        path, member = sorted(matches, key=lambda r: r[0])[0]
        payload = _read_external_text(path, member, read_budget)
        if target.startswith("content/tiles/") and target.endswith(".cfl"):
            if native_cfl_framing:
                issue = native_cfl_framing_issue(payload)
                if issue:
                    raise UnsafeSource("Native CFL framing {} in {}: {}".format(
                        target, path, issue))
            record = parse_cfl(payload, target)
            parsed_cfl[target] = record
            deps = []
            if record.model_file:
                deps.append(("model", normalize_content_path(record.model_file, "models")))
            if record.vegetation_alias:
                deps.append(("vegetation_alias", normalize_content_path(record.vegetation_alias, "tiles")))
            for article in record.vegetation:
                deps.append(("vegetation", normalize_content_path(article.veg_file, "vegetat")))
        elif target.startswith("content/vegetat/") and target.endswith(".veg"):
            record = parse_veg(payload, target)
            deps = [("vegetation_model", normalize_content_path(record.model_file, "models"))]
        else:
            raise UnsafeSource("Unexpected external definition type: " + target)
        links[target] = deps
        chosen.append({"asset": target, "reference_archive": path})
        for _rel, child in deps:
            if (child not in links and child not in names and child in refs
                    and child.endswith((".cfl", ".veg")) and child not in seen):
                pending.append(child)
        if len(seen) > MAX_FILES:
            raise UnsafeSource("Too many external native definitions")
        pending.sort()
    return chosen


def validate_source(kind, path, strict=False, reference_archives=(),
                    require_resolved_direct=False, require_resolved_transitive=False,
                    native_cfl_framing=False):
    """Return deterministic useful facts; never writes any file or external game path."""
    result = {"source": str(path), "kind": kind, "ok": False, "strict": bool(strict),
              "cfl_count": 0, "veg_count": 0, "p3d_count": 0, "dependencies_checked": 0,
              "resolved": 0, "resolved_external": 0, "external_matches": [],
              "ambiguous": [], "ambiguous_instances": 0,
              "reference_archives": [str(p) for p in reference_archives],
              "require_resolved_direct": bool(require_resolved_direct),
              "require_resolved_transitive": bool(require_resolved_transitive),
              "native_cfl_framing": bool(native_cfl_framing),
              "external_definitions_parsed": [],
              "unresolved": [], "unresolved_instances": 0,
              "errors": [], "warnings": []}
    try:
        if kind == "archive":
            sources, names = _index_archive(path)
        elif kind == "content":
            sources, names = _index_directory(path)
        else:
            raise UnsafeSource("Unknown source type")
        refs = _index_reference_archives(reference_archives)
        result["p3d_count"] = sum(k.startswith("content/models/") and k.endswith(".p3d") for k in names)
        links = {}
        parsed_cfl = {}
        for k in sorted(sources):
            if not k.startswith("content/tiles/") or not k.endswith(".cfl"):
                continue
            try:
                if native_cfl_framing:
                    issue = native_cfl_framing_issue(sources[k])
                    if issue:
                        result["errors"].append(
                            "Native CFL framing {}: {}".format(k, issue))
                record = parse_cfl(sources[k], k)
                parsed_cfl[k] = record
                result["cfl_count"] += 1
                deps = []
                if record.model_file:
                    deps.append(("model", normalize_content_path(record.model_file, "models")))
                if record.vegetation_alias:
                    deps.append(("vegetation_alias", normalize_content_path(record.vegetation_alias, "tiles")))
                for article in record.vegetation:
                    deps.append(("vegetation", normalize_content_path(article.veg_file, "vegetat")))
                links[k] = deps
            except (AssetError, UnsafeSource, UnicodeError, ValueError) as exc:
                result["errors"].append("Invalid CFL {}: {}".format(k, exc))
        for k in sorted(sources):
            if not k.startswith("content/vegetat/") or not k.endswith(".veg"):
                continue
            try:
                record = parse_veg(sources[k], k)
                result["veg_count"] += 1
                links[k] = [("vegetation_model", normalize_content_path(record.model_file, "models"))]
            except (AssetError, UnsafeSource, UnicodeError, ValueError) as exc:
                result["errors"].append("Invalid VEG {}: {}".format(k, exc))
        if require_resolved_transitive:
            result["external_definitions_parsed"] = _expand_external_native_definitions(
                links, parsed_cfl, names, refs, native_cfl_framing)
        result["dependencies_checked"] = sum(len(edges) for edges in links.values())
        aliases = {}
        missing_counts = {}
        ambiguous_counts = {}
        external_counts = {}
        digests = {}
        budget = [0]
        for parent, deps in sorted(links.items()):
            for relation, target in deps:
                if target in names:
                    # Primary source always wins, as in the main Exporter.
                    result["resolved"] += 1
                elif target in refs:
                    candidates = refs[target]
                    if len(candidates) > 1:
                        fingerprints = []
                        for ref_path, info in candidates:
                            cache_key = (ref_path, target)
                            if cache_key not in digests:
                                try:
                                    digests[cache_key] = _reference_digest(ref_path, info, budget)
                                except (UnsafeSource, OSError, RuntimeError, zipfile.BadZipFile) as error:
                                    digests[cache_key] = None
                            fingerprints.append(digests[cache_key])
                        safe = (all(value is not None for value in fingerprints)
                                and len(set(fingerprints)) == 1)
                    else:
                        safe = True
                    k = (parent, relation, target)
                    if safe:
                        result["resolved"] += 1
                        result["resolved_external"] += 1
                        # Same actual bytes across packages are interchangeable.
                        canonical_ref = sorted(x[0] for x in candidates)[0]
                        refkey = k + (canonical_ref,)
                        external_counts[refkey] = external_counts.get(refkey, 0) + 1
                    else:
                        ambiguous_counts[k] = ambiguous_counts.get(k, 0) + 1
                else:
                    k = (parent, relation, target)
                    missing_counts[k] = missing_counts.get(k, 0) + 1
                if relation == "vegetation_alias" and target in parsed_cfl:
                    aliases[parent] = target
        result["external_matches"] = [{"from": a, "relation": b, "asset": c,
                                       "reference_archive": d, "occurrences": n}
                                      for (a, b, c, d), n in sorted(external_counts.items())]
        result["ambiguous"] = [{"from": a, "relation": b, "asset": c,
                                "reference_archives": sorted(x[0] for x in refs[c]),
                                "occurrences": n}
                               for (a, b, c), n in sorted(ambiguous_counts.items())]
        result["ambiguous_instances"] = sum(ambiguous_counts.values())
        if result["ambiguous"]:
            result["errors"].append(
                "{} ambiguous referenced asset(s): conflicting or unverifiable bytes; choose one explicit source rather than merging".format(
                    len(result["ambiguous"])))
        result["unresolved"] = [{"from": a, "relation": b, "missing": c,
                                 "occurrences": count}
                                for (a, b, c), count in sorted(missing_counts.items())]
        result["unresolved_instances"] = sum(missing_counts.values())
        # Local inherited CFL graph cycles are unambiguously invalid.
        for start in sorted(aliases):
            seen = set()
            current = start
            while current in aliases:
                if current in seen:
                    result["errors"].append("Cyclic local CFL vegetation inheritance starting at " + start)
                    break
                seen.add(current)
                current = aliases[current]
        if result["unresolved"]:
            result["warnings"].append("{} unique / {} occurrences of dependencies absent from selected source and explicit references (may exist in unselected packages)".format(
                len(result["unresolved"]), result["unresolved_instances"]))
        if strict and (result["unresolved"] or result["resolved_external"]):
            result["errors"].append(
                "Not self-contained: {} unresolved direct dependency occurrences outside the primary source (including external reference matches)".format(
                    result["unresolved_instances"] + result["resolved_external"]))
        elif (require_resolved_direct or require_resolved_transitive) and result["unresolved"]:
            mode = ("--require-resolved-transitive" if require_resolved_transitive
                    else "--require-resolved-direct")
            result["errors"].append(
                "{} unresolved distinct dependencies with {}".format(
                    len(result["unresolved"]), mode))
        result["ok"] = not result["errors"]
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        result["errors"].append(str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive", metavar="CPK_OR_BIN", help="Read-only ZIP-format content archive")
    group.add_argument("--content-root", metavar="CONTENT_FOLDER", help="Read-only loose mod/content directory")
    parser.add_argument("--reference-archive", action="append", default=[], metavar="CPK_OR_BIN",
                        help="Explicit read-only stock/mod reference ZIP; repeatable, hash-checks conflicting names")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--require-self-contained", action="store_true",
                        help="Require all direct references within the PRIMARY source even if external references are provided")
    checks.add_argument("--require-resolved-direct", action="store_true",
                        help="Require all direct references in primary or explicitly selected reference archives (not recursive)")
    checks.add_argument("--require-resolved-transitive", action="store_true",
                        help="Also parse referenced external CFL/VEG definitions and require their full dependency closure")
    parser.add_argument("--native-cfl-framing", action="store_true",
                        help="Require native CRLF line framing for CFLs; use before loading authored mods in Crashday")
    parser.add_argument("--json", action="store_true", help="Emit full machine-readable report (includes private paths)")
    args = parser.parse_args(argv)
    report = validate_source("archive" if args.archive else "content", args.archive or args.content_root,
                             strict=args.require_self_contained,
                             reference_archives=args.reference_archive,
                             require_resolved_direct=args.require_resolved_direct,
                             require_resolved_transitive=args.require_resolved_transitive,
                             native_cfl_framing=args.native_cfl_framing)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("{}: {} ({} CFL, {} VEG, {} P3D, {}/{} refs resolved, {} unresolved)".format(
            report["source"], "PASS" if report["ok"] else "INVALID",
            report["cfl_count"], report["veg_count"], report["p3d_count"],
            report["resolved"], report["dependencies_checked"], len(report["unresolved"])))
        if report["reference_archives"]:
            print("  References: {} explicit archives; {} externally present; {} ambiguous".format(
                len(report["reference_archives"]), report["resolved_external"], len(report["ambiguous"])))
        for msg in report["errors"]:
            print("  ERROR: " + msg)
        for msg in report["warnings"]:
            print("  WARN: " + msg)
        for entry in report["ambiguous"][:10]:
            print("  AMBIGUOUS: {} -> {} ({} source packages)".format(
                entry["from"], entry["asset"], len(entry["reference_archives"])))
        for missing in report["unresolved"][:20]:
            print("  UNRESOLVED: {} -> {} ({}; {}x)".format(
                missing["from"], missing["missing"], missing["relation"], missing["occurrences"]))
        if len(report["unresolved"]) > 20:
            print("  ... {} more (use --json)".format(len(report["unresolved"])-20))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
