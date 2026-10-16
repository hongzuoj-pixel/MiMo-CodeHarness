from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mimo_harness.cli import main


class CliTests(unittest.TestCase):
    def invoke(self, args: list[str], cwd: Path) -> tuple[int, str, str]:
        previous = Path.cwd()
        stdout = StringIO()
        stderr = StringIO()
        try:
            os.chdir(cwd)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(args)
        finally:
            os.chdir(previous)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_init_and_doctor_are_explicit_and_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            code, stdout, _ = self.invoke(["init"], root)
            self.assertEqual(code, 0)
            self.assertIn("OFFLINE MOCK", stdout)

            cfg = json.loads((root / "mimo-harness.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["mode"], "offline")
            model_cfg = json.loads((root / ".mimo/models.json").read_text(encoding="utf-8"))
            self.assertEqual(model_cfg["models"][0]["provider_type"], "mock")

            original = (root / "mimo-harness.json").read_bytes()
            second_code, _, second_stderr = self.invoke(["init"], root)
            self.assertEqual(second_code, 4)
            self.assertIn("Refusing to overwrite", second_stderr)
            self.assertEqual((root / "mimo-harness.json").read_bytes(), original)

            doctor_code, doctor_stdout, _ = self.invoke(["doctor"], root)
            self.assertEqual(doctor_code, 0)
            self.assertIn("Doctor result: READY", doctor_stdout)

    def test_api_mode_fails_closed_without_real_provider_or_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.invoke(["init"], root)
            cfg_path = root / "mimo-harness.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["mode"] = "api"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

            code, stdout, _ = self.invoke(["doctor"], root)
            self.assertEqual(code, 4)
            self.assertIn("API mode requires at least one enabled non-mock provider", stdout)

    def test_output_inside_project_must_use_excluded_artifact_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.invoke(["init"], root)
            cfg_path = root / "mimo-harness.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["output_dir"] = "artifacts/runs"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

            code, stdout, _ = self.invoke(["doctor"], root)
            self.assertEqual(code, 4)
            self.assertIn("must be under .mimo/ or outputs/", stdout)

    def test_offline_run_uses_configured_output_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("value = 1\n", encoding="utf-8")
            self.invoke(["init"], root)

            code, stdout, stderr = self.invoke(
                ["run", "--name", "smoke", "--limit-tasks", "1"], root
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("OFFLINE MOCK", stdout)
            self.assertTrue((root / ".mimo/runs/smoke/run_log.json").is_file())

            second, _, second_stderr = self.invoke(
                ["run", "--name", "smoke", "--limit-tasks", "1"], root
            )
            self.assertEqual(second, 4)
            self.assertIn("already exists", second_stderr)

    def test_verify_head_applies_only_inside_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "MiMo Tests"], cwd=root, check=True
            )
            source = root / "value.txt"
            source.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "value.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "baseline", "--quiet"], cwd=root, check=True)
            self.invoke(["init"], root)
            patch = root / "change.patch"
            patch.write_text(
                """diff --git a/value.txt b/value.txt
--- a/value.txt
+++ b/value.txt
@@ -1 +1 @@
-before
+after
""",
                encoding="utf-8",
            )

            code, stdout, stderr = self.invoke(
                ["verify", "--patch", str(patch), "--base", "HEAD", "--name", "patch-check"],
                root,
            )
            self.assertEqual(code, 3, stderr)
            self.assertIn("INSUFFICIENT_EVIDENCE", stdout)
            self.assertEqual(source.read_text(encoding="utf-8"), "before\n")
            candidate = root / ".mimo/runs/patch-check/worktrees/local-patch/value.txt"
            self.assertEqual(candidate.read_text(encoding="utf-8"), "after\n")
            summary = json.loads(
                (root / ".mimo/runs/patch-check/verification_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["exit_code"], 3)

            report_code, report_stdout, _ = self.invoke(["report"], root)
            self.assertEqual(report_code, 0)
            self.assertIn("MIMO_RUN_SUMMARY.md", report_stdout)

    def test_verify_runs_stdlib_unittest_and_can_reach_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "MiMo Tests"], cwd=root, check=True
            )
            source = root / "value.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "test_value.py").write_text(
                """import unittest
from value import VALUE

class ValueTests(unittest.TestCase):
    def test_supported_value(self):
        self.assertIn(VALUE, (1, 2))
""",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "value.py", "test_value.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "baseline", "--quiet"], cwd=root, check=True)
            self.invoke(["init"], root)
            patch = root / "change.patch"
            patch.write_text(
                """diff --git a/value.py b/value.py
--- a/value.py
+++ b/value.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
""",
                encoding="utf-8",
            )

            code, stdout, stderr = self.invoke(
                [
                    "verify", "--patch", str(patch), "--base", "HEAD",
                    "--name", "behavior-check", "--run-tests",
                ],
                root,
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("[RESULT] PASS", stdout)
            self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 1\n")
            result = json.loads(
                (root / ".mimo/runs/behavior-check/verification_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["external_tests_executed"], 1)


if __name__ == "__main__":
    unittest.main()
