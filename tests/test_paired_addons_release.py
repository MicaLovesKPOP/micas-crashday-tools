"""Release source and ZIP smoke tests; no Blender/game required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("paired_builder", ROOT/"scripts/build_paired_addons.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class TestPairedAddonRelease(unittest.TestCase):
    def test_reproducible_sources_and_packages(self):
        for module in builder.SPECS:
            with self.subTest(module=module), tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
                members = builder.collect(module)
                p, digest = builder.build(module, a)
                q, digest2 = builder.build(module, b)
                self.assertEqual(digest, digest2)
                self.assertEqual(p.read_bytes(), q.read_bytes())
                with zipfile.ZipFile(p) as z:
                    self.assertIsNone(z.testzip())
                    self.assertEqual({name:z.read(name) for name in z.namelist()}, members)
                with self.assertRaises(FileExistsError):
                    builder.build(module, a)

if __name__ == "__main__":
    unittest.main()
