"""One source-only packager for independent Mica's Crashday Tools modules.

Frozen builds reproduce the approved archive hash or fail without writing.
Candidate builds permit reviewed local source edits, keep license/upstream bytes
pinned, and never use a published filename. Neither mode publishes or installs.
"""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
PATH_PART = re.compile(r"[A-Za-z0-9_.-]+\Z")
MAX_MEMBER = 2 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
PRIVATE = re.compile(rb"(?i)(gh[pousr]_[a-z0-9]{25,}|github_pat_[a-z0-9_]{40,}|sk-(?:proj-)?[a-z0-9_-]{24,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|[a-z]:[\\/]+(?:users[\\/]+mica|projects[\\/]+crashdayblendertrackimporter|games[\\/]+steamapps)|micas-crashday-tools-incubator)")

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def safe_rel(value):
    require(isinstance(value, str) and bool(value), "Missing path")
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value and
            all(part not in (".", "..") and PATH_PART.fullmatch(part) and not part.endswith(".")
                for part in p.parts) and value != ".", "Unsafe path: "+value)
    return p

def nonredirected(path):
    # Reject symlinks and Windows reparse points before resolving a path.
    for p in [path] + list(path.parents):
        try:
            info = p.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and
                not (getattr(info, "st_file_attributes", 0) &
                     getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)),
                "Redirected path: "+str(p))

def source_bytes(path):
    nonredirected(path)
    require(path.is_file() and path.stat().st_size <= MAX_MEMBER, "Missing or oversized member: "+str(path.name))
    return path.read_bytes()

def load_specs(root=ROOT):
    root = Path(root).absolute()
    catalog = json.loads(source_bytes(root/"docs/catalog.json"))
    layouts = json.loads(source_bytes(root/"scripts/package_layouts.json"))
    require(catalog["schema"] == "micas-crashday-tools-catalog/v1" and
            layouts["schema"] == "micas-crashday-package-layouts/v1", "Package metadata schema")
    mods = {m["id"]:m for m in catalog["modules"]}
    require(len(mods)==len(catalog["modules"]), "Duplicate catalog ID")
    result = {}
    for layout in layouts["modules"]:
        mid = layout["id"]
        require(ID.fullmatch(mid) and mid in mods and mid not in result, "Duplicate/unknown package layout")
        m = mods[mid]
        require(layout["tag"] == m["tag"] and m["asset"] == "micas-crashday-tools-"+m["tag"]+".zip"
                and m["tag"].endswith("-v"+m["version"]) and HEX.fullmatch(m["sha256"]), "Release identity")
        require(m["public_dir"] in ("addons/"+mid,"utilities/"+mid), "Invalid module root")
        stamp = layout["stamp"]
        require(isinstance(stamp,list) and len(stamp)==6 and all(type(x) is int for x in stamp)
                and 1980<=stamp[0]<=2107 and 1<=stamp[1]<=12 and 1<=stamp[2]<=31
                and stamp[3:]==[0,0,0], "Invalid archive stamp")
        sources, names, prefixes = set(), set(), set()
        for row in layout["members"]:
            require(set(row)=={"source","archive","sha256","line_endings","locked_in_candidate"}, "Member fields")
            source, archive = safe_rel(row["source"]), safe_rel(row["archive"])
            require(len(archive.parts)>=2 and source.name == archive.name and
                    (source.suffix == ".py" or source.name in {"README.md","CHANGELOG.md","NOTICE.md",
                        "LICENSE","LICENSE_WURUNDUK_P3D.txt","SOURCE_MANIFEST.json"}), "Unexpected member: "+str(source))
            require(source.as_posix().casefold() not in sources and archive.as_posix().casefold() not in names,
                    "Duplicate case-insensitive member")
            require(HEX.fullmatch(row["sha256"]) and row["line_endings"] in ("exact","crlf")
                    and type(row["locked_in_candidate"]) is bool, "Invalid member lock")
            if source.name in {"LICENSE","LICENSE_WURUNDUK_P3D.txt","wurunduk_p3d.py"}:
                require(row["locked_in_candidate"], "License/upstream lock missing")
            prefixes.add(archive.parts[0]);sources.add(source.as_posix().casefold());names.add(archive.as_posix().casefold())
        require(len(prefixes)==1, "Multiple package roots")
        prefix = next(iter(prefixes))
        require(all((prefix+"/"+f).casefold() in names for f in ("README.md","CHANGELOG.md","LICENSE","NOTICE.md")),
                "Missing required distribution notices")
        result[mid] = (m,layout,prefix)
    require(set(result)==set(mods), "Missing package layout")
    return result

def generated_sources(module, root, specs):
    """Return the explicit license-compatible generated members for one module."""
    layouts = json.loads(source_bytes(Path(root).absolute()/"scripts/package_layouts.json"))
    targets = {}
    seen_components = set()
    seen_targets = set()
    for component in layouts.get("generated_copies", []):
        cid = component["component"]
        canonical = safe_rel(component["canonical"])
        require(ID.fullmatch(cid) and cid not in seen_components and canonical.suffix==".py",
                "Invalid shared component")
        seen_components.add(cid)
        license_id = component["license"]
        # A component either has its own documented license or belongs to a
        # released module. Only the explicit compatible license flow is used.
        if canonical.parts[0]=="shared":
            require(canonical.parent.as_posix()=="shared/"+cid and license_id=="GPL-2.0-or-later",
                    "Unexpected shared source license/path")
            directory=Path(root).absolute()/canonical.parent
            notice=source_bytes(directory/"NOTICE.md")
            license_bytes=source_bytes(directory/"LICENSE")
            splitter,split_layout,_=specs["tile-splitter"]
            license_pin=next(row["sha256"] for row in split_layout["members"] if row["source"]=="LICENSE")
            require(license_id.encode() in notice and b"MicaLovesKPOP" in notice
                    and sha(license_bytes)==license_pin, "Shared license/provenance mismatch")
        else:
            owners=[mid for mid,(m,_,_) in specs.items()
                    if canonical.as_posix().startswith(m["public_dir"]+"/")]
            require(len(owners)==1 and specs[owners[0]][0]["license"]==license_id,
                    "Canonical component license mismatch")
        for dest in component["targets"]:
            mid=dest["module"]
            path=safe_rel(dest["source"]).as_posix()
            transform=dest["transform"]
            require(mid in specs and (mid,path) not in seen_targets, "Duplicate/unknown shared target")
            seen_targets.add((mid,path))
            m,layout,_=specs[mid]
            require(any(row["source"]==path and not row["locked_in_candidate"] for row in layout["members"]),
                    "Unknown or locked generated target")
            require(license_id==m["license"] or
                    (license_id=="GPL-2.0-or-later" and m["license"]=="GPL-3.0-or-later"),
                    "Incompatible generated component license")
            require(canonical.as_posix()!=m["public_dir"]+"/"+path and
                    transform in ("identity","route-validator-relative-v1"), "Unsafe shared transform")
            if mid==module:
                raw=source_bytes(Path(root).absolute()/canonical)
                require(not PRIVATE.search(raw), "Private-looking shared source")
                if transform=="route-validator-relative-v1":
                    before=b"    from validate_p3d import Reader, validate_bytes"
                    after=b"    from .validate_p3d import Reader, validate_bytes"
                    require(raw.count(before)==1, "Checkpoint route namespace contract changed")
                    raw=raw.replace(before,after,1)
                    for node in ast.walk(ast.parse(raw)):
                        if isinstance(node,ast.ImportFrom):
                            require(not (node.level==0 and node.module=="validate_p3d"), "Unhandled validator import")
                        elif isinstance(node,ast.Import):
                            require(all(alias.name!="validate_p3d" for alias in node.names), "Unhandled validator import")
                targets[path]=raw
    require(not any(canonical==specs[mid][0]["public_dir"]+"/"+path
                    for canonical in (c["canonical"] for c in layouts.get("generated_copies",[]))
                    for mid,path in seen_targets), "Generated source chain is unsupported")
    return targets


def collect(module, root=ROOT, candidate=False):
    specs=load_specs(root)
    m, layout, prefix = specs[module]
    generated=generated_sources(module,root,specs)
    folder = Path(root).absolute()/m["public_dir"]
    nonredirected(folder)
    expected = {r["source"] for r in layout["members"]}
    actual = set()
    for current, dirs, files in os.walk(folder, followlinks=False):
        for d in dirs:
            nonredirected(Path(current)/d)
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for file in files:
            p = Path(current)/file
            nonredirected(p)
            actual.add(p.relative_to(folder).as_posix())
    require(actual == expected, "Source layout drift (update the explicit recipe): "+str(sorted(actual^expected)))
    members = {}
    total = 0
    for row in layout["members"]:
        name = row["archive"]
        raw = source_bytes(folder/row["source"])
        if row["source"] in generated:
            canonical=generated[row["source"]]
            # Frozen distribution copies must agree exactly. A candidate may
            # generate from an edited canonical source while the checked-in
            # distribution copy remains at its last approved digest.
            require(raw==canonical or (candidate and sha(raw)==row["sha256"]),
                    "Generated copy drift; edit its canonical source: "+name)
            raw=canonical
        if row["line_endings"] == "crlf":
            raw = raw.replace(b"\r\n",b"\n").replace(b"\n",b"\r\n")
        if not candidate or row["locked_in_candidate"]:
            require(sha(raw)==row["sha256"], "Frozen member hash mismatch: "+name)
        require(not PRIVATE.search(raw), "Private-looking content in "+name)
        if name.endswith(".py"):
            ast.parse(raw, filename=name)
        members[name] = raw
        total += len(raw)
    require(total <= MAX_TOTAL, "Package too large")
    lic = members[prefix+"/LICENSE"]
    notice = members[prefix+"/NOTICE.md"]
    require(m["license"].encode("ascii") in notice and b"MicaLovesKPOP" in notice, "Attribution/license mismatch")
    if prefix+"/LICENSE_WURUNDUK_P3D.txt" in members:
        require(lic == members[prefix+"/LICENSE_WURUNDUK_P3D.txt"] and b"Wurunduk" in notice,
                "Bundled upstream license mismatch")
    manifest_name = prefix+"/SOURCE_MANIFEST.json"
    if manifest_name in members:
        manifest = json.loads(members[manifest_name])
        require(manifest["module"]==module and manifest["license"]==m["license"]
                and manifest["schema"]=="micas-crashday-release-source/v1", "Package source manifest identity")
        expected_hashes = {name:sha(raw) for name,raw in members.items() if name!=manifest_name}
        if candidate:
            manifest["files"] = expected_hashes
            manifest["qualification"] = "unqualified-development-candidate"
            members[manifest_name] = (json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
        else:
            require(manifest["version"]==m["version"] and manifest["files"]==expected_hashes,
                    "Frozen source manifest mismatch")
    return members

def serialize(members, stamp):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        # Dict insertion order intentionally preserves each historical archive's member order.
        for name, raw in members.items():
            info = zipfile.ZipInfo(name, tuple(stamp))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info,raw,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    payload = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        require(z.testzip() is None and z.namelist()==list(members), "ZIP structure/CRC failure")
        require(all(stat.S_ISREG(z.getinfo(name).external_attr>>16) and z.read(name)==raw
                    for name,raw in members.items()), "ZIP source identity failure")
    return payload

def package_bytes(module, root=ROOT, candidate=False):
    m,layout,_ = load_specs(root)[module]
    data = serialize(collect(module,root,candidate),layout["stamp"])
    if not candidate:
        require(sha(data)==m["sha256"],
                "ZIP digest differs from published release; use the qualified Python/zlib environment, not a substituted archive")
    return data

def build(module, output_dir, root=ROOT, candidate=False):
    m,_,_ = load_specs(root)[module]
    output = Path(output_dir).absolute()
    nonredirected(output)
    require(output.is_dir(), "Choose an existing non-redirected output directory")
    data = package_bytes(module,root,candidate)
    digest = sha(data)
    filename = ("micas-crashday-tools-"+module+"-candidate-"+digest[:16]+".zip") if candidate else m["asset"]
    dest = output/filename
    # Exclusive creation, no overwrite. All verification precedes writing.
    with dest.open("xb") as f:
        f.write(data)
    require(sha(dest.read_bytes())==digest,"Output readback mismatch")
    print("UNQUALIFIED CANDIDATE" if candidate else "VERIFIED FROZEN RELEASE",dest.name,digest,len(data),flush=True)
    return dest,digest

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--module",choices=sorted(load_specs()),required=True)
    p.add_argument("--output-dir",required=True)
    p.add_argument("--candidate",action="store_true",help="Build local unqualified edits with a distinct filename; never publish")
    a = p.parse_args(argv)
    build(a.module,a.output_dir,candidate=a.candidate)

if __name__=="__main__":
    main()
