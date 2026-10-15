from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_mimo_codeharness_v02 import (
    BuildTesterV02,
    copy_worktree,
    list_changed_files,
    load_model_config,
)
from scoring_v03 import score_build_test
from verification_v04 import (
    FAIL,
    INSUFFICIENT_EVIDENCE,
    NOT_APPLICABLE,
    PASS,
    classify_candidate,
    summarize_verifications,
)


def summary(test_status: str | None = None) -> dict:
    results = [
        {
            "name": "python_syntax",
            "status": "ok",
            "category": "static",
        }
    ]
    executed = 0
    failed = 0
    if test_status is not None:
        results.append(
            {
                "name": "pytest",
                "status": test_status,
                "category": "test",
            }
        )
        executed = 1
        failed = int(test_status in {"failed", "error"})
    return {
        "all_critical_ok": failed == 0,
        "failed_count": failed,
        "warning_count": 0,
        "skipped_count": 0,
        "run_external": True,
        "external_executed_count": executed,
        "results": results,
    }


class WorkspaceTests(unittest.TestCase):
    def test_copy_preserves_binary_and_source_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            payload = bytes(range(256))
            (source / "fixture.png").write_bytes(payload)
            (source / "main.py").write_text("print('source')\n", encoding="utf-8")

            copy_worktree(source, target)
            (target / "main.py").write_text("print('candidate')\n", encoding="utf-8")

            self.assertEqual((target / "fixture.png").read_bytes(), payload)
            self.assertEqual(
                (source / "main.py").read_text(encoding="utf-8"),
                "print('source')\n",
            )

    def test_change_detection_covers_add_modify_delete_and_rename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "rename_me.txt").write_text("same", encoding="utf-8")
            (source / "modify.txt").write_text("before", encoding="utf-8")
            (source / "delete.txt").write_text("delete", encoding="utf-8")
            copy_worktree(source, target)

            (target / "rename_me.txt").rename(target / "renamed.txt")
            (target / "modify.txt").write_text("after", encoding="utf-8")
            (target / "delete.txt").unlink()
            (target / "added.bin").write_bytes(b"new")

            changes = set(list_changed_files(source, target))
            self.assertIn("R rename_me.txt -> renamed.txt", changes)
            self.assertIn("M modify.txt", changes)
            self.assertIn("D delete.txt", changes)
            self.assertIn("A added.bin", changes)


class VerificationTests(unittest.TestCase):
    def task(self, task_type: str = "code_refactor") -> SimpleNamespace:
        return SimpleNamespace(task_id="T005", task_type=task_type)

    def patch(self, candidate_id: str, status: str = "applied", applied: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            candidate_id=candidate_id,
            task_id="T005",
            provider_name="MiMo",
            status=status,
            applied=applied,
            changed_files="M src/main.py",
        )

    def test_static_only_is_insufficient_and_never_scores_100(self) -> None:
        result = classify_candidate(
            self.task(),
            self.patch("candidate-static"),
            summary(None),
            summary(None),
        )
        score, meta = score_build_test(summary(None))
        self.assertEqual(result.verdict, INSUFFICIENT_EVIDENCE)
        self.assertEqual(score, 65.0)
        self.assertEqual(meta["external_executed_count"], 0)

    def test_candidates_are_classified_independently(self) -> None:
        baseline = summary("ok")
        passed = classify_candidate(
            self.task(),
            self.patch("candidate-pass"),
            baseline,
            summary("ok"),
        )
        failed = classify_candidate(
            self.task(),
            self.patch("candidate-fail"),
            baseline,
            summary("failed"),
        )
        self.assertEqual(passed.verdict, PASS)
        self.assertEqual(failed.verdict, FAIL)
        aggregate = summarize_verifications([passed, failed])
        self.assertEqual(aggregate["overall_verdict"], FAIL)
        self.assertEqual(aggregate["counts"][PASS], 1)
        self.assertEqual(aggregate["counts"][FAIL], 1)

    def test_applied_noop_is_a_failure(self) -> None:
        patch = self.patch("candidate-noop")
        patch.changed_files = ""
        result = classify_candidate(
            self.task(),
            patch,
            summary("ok"),
            summary("ok"),
        )
        self.assertEqual(result.verdict, FAIL)
        self.assertIn("NO_CHANGED_FILES", result.reason_codes)

    def test_missing_required_patch_is_not_a_pass(self) -> None:
        result = classify_candidate(
            self.task(),
            self.patch("candidate-none", status="no_patch", applied=False),
            summary("ok"),
            None,
        )
        self.assertEqual(result.verdict, INSUFFICIENT_EVIDENCE)

    def test_analysis_without_patch_is_not_applicable(self) -> None:
        result = classify_candidate(
            self.task("repo_understanding"),
            self.patch("candidate-analysis", status="no_patch", applied=False),
            summary("ok"),
            None,
        )
        self.assertEqual(result.verdict, NOT_APPLICABLE)

    def test_fail_to_pass_is_required_for_bug_fix(self) -> None:
        no_fix = classify_candidate(
            self.task("bug_fix"),
            self.patch("candidate-no-fix"),
            summary("ok"),
            summary("ok"),
        )
        fixed = classify_candidate(
            self.task("bug_fix"),
            self.patch("candidate-fixed"),
            summary("failed"),
            summary("ok"),
        )
        self.assertEqual(no_fix.verdict, INSUFFICIENT_EVIDENCE)
        self.assertEqual(fixed.verdict, PASS)


class GuardrailTests(unittest.TestCase):
    def test_missing_model_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.json"
            with self.assertRaises(FileNotFoundError):
                load_model_config(missing)

    def test_ast_compile_does_not_create_pycache(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("value = 1\n", encoding="utf-8")
            tester = BuildTesterV02(root, root / "logs")
            result = tester.run_all(run_external=True)
            self.assertEqual(result["external_executed_count"], 0)
            self.assertFalse((root / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
