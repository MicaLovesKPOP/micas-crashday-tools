"""Public Exporter v0.1.0 source, license and ZIP smoke tests."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/"scripts/build_crashday_exporter.py"
spec=importlib.util.spec_from_file_location("build_exporter_010",str(BUILD))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class ExporterReleaseTests(unittest.TestCase):
    def test_pinned_source_and_license(self):
        files=mod.collect()
        self.assertEqual(len(files),12)
        self.assertIn(b'"author": "MicaLovesKPOP"',files["crashday_track_importer/__init__.py"])
        self.assertEqual(files["crashday_track_importer/LICENSE"],
                         files["crashday_track_importer/LICENSE_WURUNDUK_P3D.txt"])
    def test_determinism_and_layout(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            p1,h1=mod.build(a)
            p2,h2=mod.build(b)
            self.assertEqual(h1,h2)
            self.assertEqual(p1.read_bytes(),p2.read_bytes())
            with zipfile.ZipFile(str(p1)) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(len(z.namelist()),12)
                self.assertTrue(all(x.startswith("crashday_track_importer/") for x in z.namelist()))
                self.assertFalse(any(x.lower().endswith((".p3d",".trk",".cfl",".cpk",".tga",".dds",".blend",".exe")) for x in z.namelist()))
    def test_exclusive_output(self):
        with tempfile.TemporaryDirectory() as a:
            mod.build(a)
            with self.assertRaises(FileExistsError):mod.build(a)
if __name__=="__main__":unittest.main()
