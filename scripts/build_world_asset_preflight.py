"""Build the standalone MIT World Asset Preflight v0.2 release (Python 3.7+)."""
import argparse
import hashlib
import io
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "utilities" / "world-asset-preflight"
FILES = ("README.md", "validate_world_assets.py", "asset_library.py",
         "LICENSE", "NOTICE.md", "CHANGELOG.md")
NAME = "micas-crashday-tools-world-asset-preflight-v0.2.zip"

def build(destination):
    destination = Path(destination)
    if not destination.is_dir() or destination.is_symlink():
        raise ValueError("Use an existing, non-redirected output folder")
    target = destination / NAME
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in FILES:
            source = MODULE / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Missing or unsafe module member: " + name)
            data = source.read_bytes()
            if name.endswith(".py"):
                compile(data, name, "exec")
            info = zipfile.ZipInfo("world_asset_preflight/" + name, (2024, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    data = payload.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.testzip() is not None or set(z.namelist()) != {"world_asset_preflight/" + n for n in FILES}:
            raise ValueError("Archive failed integrity/allowlist verification")
    with target.open("xb") as f:
        f.write(data)
    digest = hashlib.sha256(data).hexdigest()
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise RuntimeError("ZIP readback mismatch")
    print("ZIP:", target, "\nSHA256:", digest)
    return target, digest

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", required=True)
    build(p.parse_args().output_dir)
