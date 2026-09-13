from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StandaloneFormatBaselineTests(unittest.TestCase):
    def test_exported_v2_validates_without_old_project_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "standalone-v2"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_distribution.py"),
                 "--output", str(package)],
                check=True, capture_output=True, text=True,
            )
            command = [sys.executable, str(package / "scripts/validate_v1_compatibility.py")]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("35 baseline files verified", result.stdout)

            schema = package / "references/output-schema.md"
            original = schema.read_bytes()
            schema.write_bytes(original + b"\nUnexpected format change\n")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unauthorized inherited-file change: references/output-schema.md", result.stdout)

            schema.write_bytes(original)
            schema.unlink()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing inherited file: references/output-schema.md", result.stdout)


if __name__ == "__main__":
    unittest.main()
