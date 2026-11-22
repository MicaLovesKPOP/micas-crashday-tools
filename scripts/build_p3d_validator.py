"""Build deterministic self-contained P3D Validator v0.4.0 ZIP, Python 3.7+."""
from pathlib import Path
import argparse, hashlib, zipfile
ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/"utilities"/"p3d-validator"
FILES=("validate_p3d.py","README.md","CHANGELOG.md","LICENSE","NOTICE.md")
def build(destination):
    destination=Path(destination).resolve()
    if not destination.is_dir():
        raise ValueError("Create a private output directory first")
    zip_path=destination/"micas-crashday-tools-p3d-validator-v0.4.0.zip"
    if zip_path.exists():raise FileExistsError("Refusing to overwrite existing output")
    source={}
    for name in FILES:
        p=MODULE/name
        if not p.is_file() or p.is_symlink() or p.stat().st_size>150000:
            raise ValueError("Unsafe or missing input: "+name)
        data=p.read_bytes()
        for banned in (b"gho_",b"github_pat_",b"BEGIN PRIVATE KEY",b"D:\\Projects\\",b"I:\\Games\\"):
            if banned in data:raise ValueError("Private data in source: "+name)
        source["p3d_validator/"+name]=data
    assert b"Version 3, 29 June 2007" in source["p3d_validator/LICENSE"]
    assert b"GPL-3.0-or-later" in source["p3d_validator/NOTICE.md"]
    compile(source["p3d_validator/validate_p3d.py"],"validate_p3d.py","exec")
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(source.items()):
            member=zipfile.ZipInfo(name,(2026,11,22,0,0,0))
            member.compress_type=zipfile.ZIP_DEFLATED
            member.create_system=3
            member.external_attr=0o100644<<16
            z.writestr(member,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(zip_path) as z:
        assert z.testzip() is None and set(z.namelist())==set(source)
        for name,data in source.items():assert z.read(name)==data
    digest=hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print("ZIP:",zip_path,"\nSHA256:",digest,"\nMEMBERS:",len(source))
    return zip_path,digest
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output-dir",required=True)
    build(p.parse_args().output_dir)
