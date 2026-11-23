"""Build deterministic GPL-2.0-only Exporter 0.1.0 ZIP (Python 3.7+)."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"addons/crashday-exporter"
MODULE=SOURCE/"crashday_track_importer"
MODULE_HASHES={
"__init__.py":"6c9719638092b0e322f35a35ce542560aaf5f2aa7c06c3a55423d13d041e7b2c",
"asset_library.py":"bb1f6cff620f295530fe935b540dde913533a3ceed8c2d898be36f20a1df5b3e",
"blender_scene.py":"6f3b78decfc6a4a6637ad522d5654e680307da9047ac6b5193d71d5819356d99",
"geometry.py":"143c7987975c9b416e226618854b08472f921820cc51c70534df8b719f81ec1c",
"import_diagnostics.py":"7225217c8d12caaf663f5393db4696dd97e65d8fec15c40e04076c4194d39ff0",
"trk_probe.py":"5b662247947a2b174845486ccd3b0fd0422c5e9c2f5cf38a2a770bf8b891da08",
"wurunduk_p3d.py":"ba3bffa53305b82ba7256215b8b3bf624a05e9374436403f514549277ae13206",
"LICENSE_WURUNDUK_P3D.txt":"f3d0f98935410a5196f0df27c4928a86a1822e6b9f49f9312fefb08b35e1e07c",
}
DOCS=("LICENSE","NOTICE.md","README.md","CHANGELOG.md")
RELEASE="micas-crashday-tools-exporter-v0.1.0.zip"
def collect():
    source={}
    for name,expected in MODULE_HASHES.items():
        path=MODULE/name
        if not path.is_file() or path.is_symlink():raise ValueError("Missing/unsafe code: "+name)
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected:raise ValueError("Unqualified source: "+name)
        source["crashday_track_importer/"+name]=data
    for name in DOCS:
        path=SOURCE/name
        if not path.is_file() or path.is_symlink() or path.stat().st_size>100000:raise ValueError("Missing/unsafe docs: "+name)
        source["crashday_track_importer/"+name]=path.read_bytes()
    lic=source["crashday_track_importer/LICENSE"]
    upstream=source["crashday_track_importer/LICENSE_WURUNDUK_P3D.txt"]
    notice=source["crashday_track_importer/NOTICE.md"]
    if lic!=upstream or b"GNU GENERAL PUBLIC LICENSE" not in lic or b"Version 2, June 1991" not in lic:raise ValueError("GPLv2 license mismatch")
    if b"GPL-2.0-only" not in notice or b"MicaLovesKPOP" not in notice or b"Wurunduk" not in notice:raise ValueError("Missing attribution")
    if b"pending" in notice.lower() or b"proposed" in notice.lower():raise ValueError("Provisional notice")
    for name,data in source.items():
        for token in (b"gho_",b"github_pat_",b"BEGIN PRIVATE KEY",b"D:\\Projects\\",b"I:\\Games\\",b"C:\\Users\\Mica\\"):
            if token in data:raise ValueError("Private data: "+name)
        if name.endswith(".py"):compile(data,name,"exec")
    return source
def build(output_dir):
    output_dir=Path(output_dir).resolve()
    if not output_dir.is_dir() or output_dir.is_symlink():raise ValueError("Create safe output directory first")
    path=output_dir/RELEASE
    if path.exists() or path.is_symlink():raise FileExistsError("Refuse output overwrite")
    members=collect()
    with zipfile.ZipFile(str(path),"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(members.items()):
            info=zipfile.ZipInfo(name,(2026,11,23,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.create_system=3
            info.external_attr=0o100644<<16
            z.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(str(path)) as z:
        if z.testzip() is not None or set(z.namelist())!=set(members):raise ValueError("ZIP verification")
        for name,data in members.items():
            if z.read(name)!=data:raise ValueError("ZIP member mismatch: "+name)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    print("RELEASE",str(path))
    print("SHA256",digest)
    print("MEMBERS",len(members))
    return path,digest
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output-dir",required=True)
    build(p.parse_args().output_dir)
