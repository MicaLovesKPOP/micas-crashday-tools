"""Rebuild the exact paired Blender add-on releases from public source (Python 3.7+)."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    "tile-splitter": ("crashday_tile_splitter", "0.4.7", "GPL-2.0-or-later", 5),
    "world-builder": ("crashday_world_builder", "0.2.0", "GPL-3.0-or-later", 19),
}
DOCS = {"LICENSE", "NOTICE.md", "README.md", "CHANGELOG.md"}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def collect(module):
    prefix, version, license_id, count = SPECS[module]
    root = ROOT / "addons" / module
    manifest_path = root / "SOURCE_MANIFEST.json"
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if (manifest["module"], manifest["version"], manifest["license"]) != (module, version, license_id):
        raise ValueError("Unexpected release metadata")
    expected = manifest["files"]
    if len(expected) != count + len(DOCS):
        raise ValueError("Unexpected source member count")
    members = {}
    paths = set()
    for name, digest in expected.items():
        path = PurePosixPath(name)
        if (str(path) != name or path.parts[0] != prefix or len(path.parts)<2 or
                ".." in path.parts or "\\" in name or ":" in name or name.startswith("/")):
            raise ValueError("Unsafe source member " + name)
        if path.suffix == ".py":
            local = root.joinpath(*path.parts)
        elif len(path.parts) == 2 and path.name in DOCS:
            local = root / path.name
        else:
            raise ValueError("Unexpected member " + name)
        if not local.is_file() or any(p.is_symlink() for p in [local]+list(local.parents) if p == root or root in p.parents):
            raise ValueError("Missing or redirected source " + name)
        data = local.read_bytes()
        if sha(data) != digest:
            raise ValueError("Source identity mismatch " + name)
        if path.suffix == ".py":
            ast.parse(data, filename=name)
        paths.add(local)
        members[name] = data
    actual = {p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if actual != paths | {manifest_path}:
        raise ValueError("Unexpected source tree contents")
    if not all(prefix + "/" + doc in members for doc in DOCS):
        raise ValueError("Incomplete license/documentation")
    notice = members[prefix + "/NOTICE.md"]
    if license_id.encode() not in notice or b"MicaLovesKPOP" not in notice:
        raise ValueError("Incorrect notice")
    members[prefix + "/SOURCE_MANIFEST.json"] = raw
    return members

def build(module, output_dir):
    _, version, _, _ = SPECS[module]
    output = Path(output_dir)
    if not output.is_dir() or output.is_symlink():
        raise ValueError("Choose an existing safe output directory")
    dest = output / ("micas-crashday-tools-" + module + "-v" + version + ".zip")
    members = collect(module)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, (2026,11,25,0,0,0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    raw = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None or set(z.namelist()) != set(members):
            raise ValueError("Archive integrity failure")
        if any(z.read(name) != data or not stat.S_ISREG(z.getinfo(name).external_attr >> 16)
               for name, data in members.items()):
            raise ValueError("Archive identity failure")
    with dest.open("xb") as f:
        f.write(raw)
    print(module, dest.name, sha(raw), len(raw), len(members), flush=True)
    return dest, sha(raw)

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--module", choices=sorted(SPECS), required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()
    build(args.module, args.output_dir)
