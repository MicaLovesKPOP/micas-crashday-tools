"""Cross-module packaging regression tests; no original game assets."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
import io

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_tool", ROOT/"scripts/package_tool.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class PackageToolTests(unittest.TestCase):
    def test_all_published_archives_exact(self):
        for mid,(m,layout,_) in tool.load_specs().items():
            with self.subTest(module=mid):
                data = tool.package_bytes(mid)
                self.assertEqual(tool.sha(data), m["sha256"])
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    self.assertIsNone(z.testzip())
                    self.assertEqual(z.namelist(), [r["archive"] for r in layout["members"]])
                    for r in layout["members"]:
                        self.assertEqual(tool.sha(z.read(r["archive"])),r["sha256"])

    def fixture(self, folder, mid):
        root = Path(folder)
        (root/"docs").mkdir()
        (root/"scripts").mkdir()
        shutil.copyfile(ROOT/"docs/catalog.json", root/"docs/catalog.json")
        shutil.copyfile(ROOT/"scripts/package_layouts.json",root/"scripts/package_layouts.json")
        m,_,_ = tool.load_specs()[mid]
        target = root/m["public_dir"]
        shutil.copytree(ROOT/m["public_dir"],target,ignore=shutil.ignore_patterns("__pycache__"))
        return root,target

    def test_validator_document_line_endings_are_deterministic(self):
        with tempfile.TemporaryDirectory() as folder:
            root,target=self.fixture(folder,"p3d-validator")
            for name in ("README.md","NOTICE.md","CHANGELOG.md"):
                p=target/name
                p.write_bytes(p.read_bytes().replace(b"\r\n",b"\n"))
            data=tool.package_bytes("p3d-validator",root)
            self.assertEqual(tool.sha(data),tool.load_specs()["p3d-validator"][0]["sha256"])

    def test_candidate_does_not_require_old_hash_or_impersonate_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root,target=self.fixture(folder,"tile-splitter")
            p=target/"crashday_tile_splitter/p3d_export.py"
            p.write_bytes(p.read_bytes()+b"\n# synthetic local candidate change\n")
            out=root/"output"
            out.mkdir()
            with self.assertRaisesRegex(ValueError,"Frozen member hash"):
                tool.build("tile-splitter",out,root)
            self.assertEqual(list(out.iterdir()),[])
            a,digest=tool.build("tile-splitter",out,root,candidate=True)
            self.assertIn("-candidate-",a.name)
            self.assertEqual(a.read_bytes(),tool.package_bytes("tile-splitter",root,candidate=True))
            with zipfile.ZipFile(a) as z:
                manifest=json.loads(z.read("crashday_tile_splitter/SOURCE_MANIFEST.json"))
                self.assertEqual(manifest["qualification"],"unqualified-development-candidate")
                self.assertEqual(manifest["files"]["crashday_tile_splitter/p3d_export.py"],tool.sha(p.read_bytes()))
            with self.assertRaises(FileExistsError):
                tool.build("tile-splitter",out,root,candidate=True)
            self.assertEqual(tool.sha(a.read_bytes()),digest)

    def test_candidate_preserves_license_and_upstream(self):
        for rel in ("LICENSE","crashday_track_importer/wurunduk_p3d.py"):
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as folder:
                root,target=self.fixture(folder,"crashday-exporter")
                p=target/rel
                p.write_bytes(p.read_bytes()+b"\n# unapproved change\n")
                with self.assertRaisesRegex(ValueError,"Frozen member hash"):
                    tool.package_bytes("crashday-exporter",root,candidate=True)

    def test_extra_source_and_unsafe_recipe_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,target=self.fixture(folder,"world-builder")
            extra=target/"unreviewed.py"
            extra.write_text("pass")
            with self.assertRaisesRegex(ValueError,"Source layout drift"):
                tool.package_bytes("world-builder",root,candidate=True)
            extra.unlink()
            layout=root/"scripts/package_layouts.json"
            d=json.loads(layout.read_text(encoding="utf-8"))
            d["modules"][0]["members"][0]["archive"]="../outside.md"
            layout.write_text(json.dumps(d),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"Unsafe path"):
                tool.load_specs(root)


if __name__=="__main__":
    unittest.main()
