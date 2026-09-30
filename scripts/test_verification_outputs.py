import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VerificationOutputTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.simulation = self.root / "simulation"
        self.simulation.mkdir()
        shutil.copy2(ROOT / "simulation/verify_outputs.py", self.simulation)
        for name, pattern in (
            ("output", "*.csv"), ("output", "scenario_and_settings.json"),
            ("output", "figure_axes_audit.json"), ("data", "N22E114.hgt"),
            ("reference", "sos_pattern.py"),
        ):
            destination = self.simulation / name
            destination.mkdir(exist_ok=True)
            for source in (ROOT / "simulation" / name).glob(pattern):
                shutil.copy2(source, destination)
        for source in (ROOT / "simulation").glob("*.m"):
            shutil.copy2(source, self.simulation)
        shutil.copytree(ROOT / "simulation/verification", self.simulation / "verification")
        self.archived = self.simulation / "verification/independent_verification.json"

    def run_verifier(self, *args):
        return subprocess.run(
            [sys.executable, str(self.simulation / "verify_outputs.py"), *map(str, args)],
            cwd=self.root, capture_output=True, text=True,
        )

    def test_default_run_preserves_archived_report_and_creates_no_report(self):
        before = (self.archived.read_bytes(), self.archived.stat().st_mtime_ns)
        files_before = set(self.simulation.glob("**/*.json"))
        completed = self.run_verifier()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["all_passed"])
        self.assertEqual(before, (self.archived.read_bytes(), self.archived.stat().st_mtime_ns))
        self.assertEqual(files_before, set(self.simulation.glob("**/*.json")))

    def test_explicit_output_records_checks_without_changing_archive(self):
        output = self.root / "new-results/checks.json"
        before = hashlib.sha256(self.archived.read_bytes()).hexdigest()
        completed = self.run_verifier("--output", output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(output.read_text())
        self.assertTrue(report["all_passed"])
        self.assertEqual(report["check_count"], 53)
        self.assertIn("completed_at", report)
        self.assertEqual(before, hashlib.sha256(self.archived.read_bytes()).hexdigest())

    def test_existing_output_is_rejected_and_preserved(self):
        output = self.root / "existing.json"
        output.write_text('{"original": true}\n')
        completed = self.run_verifier("--output", output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("already exists", completed.stderr)
        self.assertEqual(output.read_text(), '{"original": true}\n')


if __name__ == "__main__":
    unittest.main()
