"""Synthetic source/package smoke for World Asset Preflight; no game files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "utilities" / "world-asset-preflight"
BUILDER = ROOT / "scripts" / "build_world_asset_preflight.py"
CLI = MODULE / "validate_world_assets.py"

class WorldAssetPreflightSmoke(unittest.TestCase):
    def test_reproducible_zip_and_strict_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_a, out_b = root / "a", root / "b"
            out_a.mkdir()
            out_b.mkdir()
            for d in (out_a, out_b):
                subprocess.check_call([sys.executable, "-B", str(BUILDER), "--output-dir", str(d)])
            za, zb = next(out_a.glob("*.zip")), next(out_b.glob("*.zip"))
            self.assertEqual(hashlib.sha256(za.read_bytes()).digest(), hashlib.sha256(zb.read_bytes()).digest())
            with zipfile.ZipFile(za) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(len(z.namelist()), 6)
                self.assertEqual(z.read("world_asset_preflight/asset_library.py"), (MODULE/"asset_library.py").read_bytes())
                self.assertEqual(z.read("world_asset_preflight/validate_world_assets.py"), CLI.read_bytes())
                z.extractall(root/"extracted")
            extracted = root/"extracted"/"world_asset_preflight"/CLI.name
            archive = root/"synthetic.cpk"
            cfl = "\r\n".join(["Crashday-FieldObject-File", "-", "road.p3d", "1 1", "GROUND_FLAT", "0", "NONE", "0", "1", "0", "0", "default", "V1 STOP", "---- VEGETATION ----", "NO_VEGETATION"]) + "\r\n"
            for name, expected in (("content/models/road.p3d",0),("content/models/road.txt",2)):
                with zipfile.ZipFile(archive,"w") as z:
                    z.writestr("content/tiles/road.cfl",cfl)
                    z.writestr(name,b"presence-only synthetic fixture")
                before = archive.read_bytes()
                run = subprocess.run([sys.executable,"-B",str(extracted),"--archive",str(archive),"--require-self-contained","--native-cfl-framing","--json"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertEqual(run.returncode,expected,run.stderr.decode())
                self.assertEqual(json.loads(run.stdout)["ok"],expected==0)
                self.assertEqual(archive.read_bytes(),before)
            duplicate = subprocess.run([sys.executable,"-B",str(BUILDER),"--output-dir",str(out_a)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertNotEqual(duplicate.returncode,0)
            self.assertEqual(za.read_bytes(),zb.read_bytes())

if __name__ == "__main__":
    unittest.main()
