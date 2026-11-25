"""Public download catalog integrity and documentation drift checks."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_catalog", ROOT/"scripts/render_catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)

class CatalogTests(unittest.TestCase):
    def test_generated_pages_and_links(self):
        c = catalog.read_catalog()
        self.assertTrue({"tile-splitter", "world-builder", "crashday-exporter", "p3d-validator", "world-asset-preflight"}.issubset({m["id"] for m in c["modules"]}))
        for path, content in catalog.outputs().items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)
            for m in c["modules"]:
                self.assertIn(catalog.download(m), content)
    def test_documentation_relative_links_resolve(self):
        import re
        for path in [ROOT/"README.md", ROOT/"CONTRIBUTING.md"] + list((ROOT/"docs").rglob("*.md")):
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if target.startswith(("https://", "http://", "#")):
                    continue
                self.assertTrue((path.parent/target.split("#",1)[0]).exists(), (str(path),target))

    def test_independent_upgrade_does_not_invalidate_known_pair(self):
        original = catalog.read_catalog()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = copy.deepcopy(original)
            for m in value["modules"]:
                d = root/m["public_dir"]
                d.mkdir(parents=True)
                for name in ("README.md","CHANGELOG.md","LICENSE","NOTICE.md"):
                    (d/name).touch()
                if m["id"] == "tile-splitter":
                    m.update(version="0.4.8",tag="tile-splitter-v0.4.8",asset="micas-crashday-tools-tile-splitter-v0.4.8.zip")
            (root/"docs").mkdir()
            (root/"docs/catalog.json").write_text(json.dumps(value), encoding="utf-8")
            result = catalog.read_catalog(root)
            self.assertIn("tile-splitter-v0.4.7/", catalog.render_downloads(result))
            self.assertIn("tile-splitter-v0.4.8/", catalog.render_downloads(result))

    def test_bad_mapping_is_rejected(self):
        original = catalog.read_catalog()
        for change in ("duplicate","unsafe-path","wrong-pair","bad-hash"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                value = copy.deepcopy(original)
                for m in value["modules"]:
                    d = root/m["public_dir"]
                    d.mkdir(parents=True)
                    for name in ("README.md","CHANGELOG.md","LICENSE","NOTICE.md"):
                        (d/name).touch()
                if change == "duplicate":
                    value["modules"].append(value["modules"][0])
                elif change == "unsafe-path":
                    value["modules"][0]["public_dir"] = "../private"
                elif change == "wrong-pair":
                    value["tested_map_creation_pair"]["modules"][0]["version"] = "0.0.0"
                else:
                    value["modules"][0]["sha256"] = "bad"
                (root/"docs").mkdir()
                (root/"docs/catalog.json").write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    catalog.read_catalog(root)

if __name__ == "__main__":
    unittest.main()
