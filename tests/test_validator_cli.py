"""Portable, model-free smoke checks for the public standalone P3D Validator."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "utilities" / "p3d-validator" / "validate_p3d.py"


class ValidatorSmoke(unittest.TestCase):
    def test_help(self):
        result = subprocess.run([sys.executable, str(VALIDATOR), "--help"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--archive", result.stdout)
        self.assertIn("--directory", result.stdout)

    def test_rejects_non_p3d(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "invalid.p3d"
            path.write_bytes(b"not a valid P3D")
            result = subprocess.run([sys.executable, str(VALIDATOR), str(path)],
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
