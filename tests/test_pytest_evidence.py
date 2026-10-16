"""Real subprocess integration tests: no simulated pytest results."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimo_harness.checks import run_check, validate_checks
from mimo_harness.cli import main
from run_mimo_codeharness_v02 import BuildTesterV02, copy_worktree
from verification_v04 import classify_candidate

TESTS = """from calc import target, stable

def test_target():
    assert target() == 2

def test_stable():
    assert stable() == 10
"""


def create_project(root, code="def target(): return 1\ndef stable(): return 10\n"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(code, encoding="utf-8")
    (root / "test_calc.py").write_text(TESTS, encoding="utf-8")


def checks(**updates):
    return [dict(id="acceptance", adapter="pytest", args=["-q"],
                 target_tests=["test_calc.py::test_target"], **updates)]


def evaluate(base, candidate, logs, task="bug_fix", policy=None):
    policy = policy if policy is not None else checks()
    baseline = BuildTesterV02(base, logs / "baseline", checks=policy).run_all(True)
    after = BuildTesterV02(candidate, logs / "candidate", checks=policy).run_all(True)
    patch = SimpleNamespace(candidate_id="test", task_id="T", provider_name="local",
                            status="applied", applied=True, changed_files="M calc.py")
    result = classify_candidate(SimpleNamespace(task_type=task), patch, baseline, after)
    return result, baseline, after


@pytest.mark.parametrize("change,verdict,reason", [
    ("fix", "PASS", "BEHAVIOR_TESTS_PASS"),
    ("unfixed", "FAIL", "TARGET_NOT_FIXED"),
    ("swap_failure", "FAIL", "REGRESSION_DETECTED"),
    ("delete_test", "INSUFFICIENT_EVIDENCE", "ACCEPTANCE_INPUT_CHANGED"),
    ("skip_target", "INSUFFICIENT_EVIDENCE", "ACCEPTANCE_INPUT_CHANGED"),
    ("weaken_test", "INSUFFICIENT_EVIDENCE", "ACCEPTANCE_INPUT_CHANGED"),
    ("collect_error", "ERROR", "CANDIDATE_PYTEST_COLLECTION_ERROR"),
    ("setup_error", "ERROR", "CANDIDATE_PYTEST_CASE_ERROR"),
    ("no_targets", "INSUFFICIENT_EVIDENCE", "TARGET_TESTS_NOT_CONFIGURED"),
    ("module_skip", "INSUFFICIENT_EVIDENCE", "NEW_COLLECTION_SKIP"),
])
def test_actual_pytest_verdicts(tmp_path, change, verdict, reason):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base)
    copy_worktree(base, candidate)
    (candidate / "calc.py").write_text("def target(): return 2\ndef stable(): return 10\n", encoding="utf-8")
    policy = checks()
    if change == "unfixed":
        (candidate / "calc.py").write_text("def target(): return 3\ndef stable(): return 10\n", encoding="utf-8")
    elif change == "swap_failure":
        (candidate / "calc.py").write_text("def target(): return 2\ndef stable(): return 0\n", encoding="utf-8")
    elif change == "delete_test":
        (candidate / "test_calc.py").write_text("def test_stable(): assert True\n", encoding="utf-8")
    elif change == "skip_target":
        (candidate / "test_calc.py").write_text("import pytest\n" + TESTS.replace("def test_target", "@pytest.mark.skip\ndef test_target"), encoding="utf-8")
    elif change == "weaken_test":
        (candidate / "test_calc.py").write_text(TESTS.replace("== 2", "in (1, 2)"), encoding="utf-8")
    elif change == "collect_error":
        (candidate / "calc.py").write_text("raise ImportError('missing dependency')\n", encoding="utf-8")
    elif change == "setup_error":
        (candidate / "conftest.py").write_text("import pytest\n@pytest.fixture(autouse=True)\ndef broken():\n    raise RuntimeError('setup failure')\n", encoding="utf-8")
    elif change == "no_targets":
        policy[0].pop("target_tests")
    elif change == "module_skip":
        (candidate / "test_optional.py").write_text("import pytest\npytest.skip('unavailable', allow_module_level=True)\n", encoding="utf-8")
    result, before, after = evaluate(base, candidate, tmp_path / "logs", policy=policy)
    assert result.verdict == verdict, result.to_dict()
    assert reason in result.reason_codes
    assert before["results"][-1]["returncode"] == 1
    assert Path(after["results"][-1]["junit_xml"]).is_file()
    if change == "swap_failure":
        assert result.fixed_tests == ["acceptance::test_calc.py::test_target"]
        assert result.regressed_tests == ["acceptance::test_calc.py::test_stable"]


@pytest.mark.parametrize("scenario,verdict", [
    ("zero", "INSUFFICIENT_EVIDENCE"), ("all_skip", "INSUFFICIENT_EVIDENCE"),
    ("baseline_collect_error", "ERROR"), ("teardown", "ERROR"),
    ("usage", "ERROR"), ("failfast", "INSUFFICIENT_EVIDENCE"),
])
def test_non_evidence_never_passes(tmp_path, scenario, verdict):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base)
    policy = checks()
    if scenario == "zero":
        (base / "test_calc.py").write_text("", encoding="utf-8")
    elif scenario == "all_skip":
        (base / "test_calc.py").write_text("import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n", encoding="utf-8")
    elif scenario == "baseline_collect_error":
        (base / "calc.py").write_text("raise ImportError('not a defect reproduction')\n", encoding="utf-8")
    elif scenario == "teardown":
        (base / "conftest.py").write_text("import pytest\n@pytest.fixture(autouse=True)\ndef broken():\n    yield\n    raise RuntimeError('teardown failure')\n", encoding="utf-8")
    elif scenario == "usage":
        policy[0]["args"] = ["--unknown-mimo-option"]
    elif scenario == "failfast":
        policy[0]["args"] = ["-q", "-x"]
    copy_worktree(base, candidate)
    result, before, after = evaluate(base, candidate, tmp_path / "logs", policy=policy)
    assert result.verdict == verdict, result.to_dict()
    if scenario == "zero":
        assert before["results"][-1]["returncode"] == 5
    if scenario == "baseline_collect_error":
        assert result.fixed_tests == []


@pytest.mark.parametrize("behavior", ["skip", "xfail", "missing", "parameter_missing"])
def test_runtime_skip_and_collection_loss_without_test_file_change(tmp_path, behavior):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base, "SKIP = False\n")
    text = """import pytest
from calc import SKIP
@pytest.mark.skipif(SKIP, reason='conditional')
def test_target(): assert True
def test_stable(): assert True
"""
    if behavior == "xfail":
        text = text.replace("skipif", "xfail").replace("def test_target(): assert True", "def test_target(): assert not SKIP")
    elif behavior == "missing":
        text = "from calc import SKIP\nif not SKIP:\n    def test_target(): assert True\ndef test_stable(): assert True\n"
    elif behavior == "parameter_missing":
        text = "import pytest\nfrom calc import SKIP\n@pytest.mark.parametrize('x', [1] if SKIP else [1, 2])\ndef test_target(x): assert x\n"
    (base / "test_calc.py").write_text(text, encoding="utf-8")
    copy_worktree(base, candidate)
    (candidate / "calc.py").write_text("SKIP = True\n", encoding="utf-8")
    result, _, _ = evaluate(base, candidate, tmp_path / "logs", task="code_refactor")
    assert result.verdict == "INSUFFICIENT_EVIDENCE", result.to_dict()
    assert not result.changed_acceptance_files
    if "missing" in behavior:
        assert result.missing_tests
    else:
        assert result.skipped_tests


def test_configured_command_working_directory_timeout_and_no_shell(tmp_path):
    root = tmp_path / "project with spaces"
    (root / "sub").mkdir(parents=True)
    row = run_check(root, tmp_path / "logs", {
        "id": "build", "adapter": "command", "category": "build", "cwd": "sub",
        "argv": ["{python}", "-c", "from pathlib import Path; print(Path.cwd().name)"]}, True)
    assert row["status"] == "ok"
    assert Path(row["stdout_log"]).read_text().strip() == "sub"
    timed = run_check(root, tmp_path / "logs", {
        "id": "timeout", "adapter": "command", "argv": ["{python}", "-c", "import time; time.sleep(5)"],
        "timeout_seconds": 0.1}, True)
    assert timed["reason_codes"] == ["CHECK_TIMEOUT"]
    missing = run_check(root, tmp_path / "logs", {
        "id": "missing", "adapter": "command", "argv": ["mimo-nonexistent-executable"]}, True)
    assert missing["status"] == "error"


@pytest.mark.parametrize("bad", [
    [{"id": "x", "adapter": "pytest", "args": "-q"}],
    [{"id": "../x", "adapter": "pytest"}],
    [{"id": "x", "adapter": "command", "argv": []}],
    [{"id": "x", "adapter": "pytest", "cwd": "../escape"}],
    [{"id": "x", "adapter": "pytest", "timeout_seconds": False}],
    [{"id": "x", "adapter": "pytest", "timeout_seconds": float("nan")}],
    [{"id": "x", "adapter": "pytest", "shell": True}],
    [{"id": "x", "adapter": "pytest"}, {"id": "x", "adapter": "pytest"}],
])
def test_invalid_config_fails_before_execution(bad):
    with pytest.raises(ValueError):
        validate_checks(bad)


def test_cli_real_bug_fix_against_git_head(tmp_path, monkeypatch):
    root = tmp_path / "中文 project"
    create_project(root)
    for argv in (["git", "init", "-q"], ["git", "add", "."],
                 ["git", "-c", "user.email=tests@example.invalid", "-c", "user.name=Test", "commit", "-qm", "baseline"]):
        subprocess.run(argv, cwd=root, check=True)
    monkeypatch.chdir(root)
    assert main(["init"]) == 0
    config = json.loads(Path("mimo-harness.json").read_text())
    config["checks"] = checks()
    Path("mimo-harness.json").write_text(json.dumps(config), encoding="utf-8")
    Path("fix.patch").write_text("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-def target(): return 1\n+def target(): return 2\n def stable(): return 10\n", encoding="utf-8")
    assert main(["verify", "--patch", "fix.patch", "--task-type", "bug_fix", "--run-tests", "--name", "real-fix"]) == 0
    result = json.loads(Path(".mimo/runs/real-fix/verification_result.json").read_text())
    assert result["fixed_tests"] == ["acceptance::test_calc.py::test_target"]
    assert (root / "calc.py").read_text().startswith("def target(): return 1")


def test_custom_test_filename_is_protected(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base)
    (base / "test_calc.py").rename(base / "acceptance.py")
    copy_worktree(base, candidate)
    (candidate / "acceptance.py").write_text(TESTS.replace("== 2", "== 1"), encoding="utf-8")
    policy = [{"id": "custom", "adapter": "pytest", "args": ["acceptance.py", "-q"]}]
    result, _, _ = evaluate(base, candidate, tmp_path / "logs", task="code_refactor", policy=policy)
    assert result.verdict == "INSUFFICIENT_EVIDENCE"
    assert result.changed_acceptance_files == ["acceptance.py"]


def test_unchanged_baseline_skip_is_reported_but_not_a_new_regression(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base)
    (base / "test_calc.py").write_text(TESTS + "\nimport pytest\n@pytest.mark.skip(reason='platform')\ndef test_platform(): pass\n", encoding="utf-8")
    copy_worktree(base, candidate)
    (candidate / "calc.py").write_text("def target(): return 2\ndef stable(): return 10\n", encoding="utf-8")
    result, _, _ = evaluate(base, candidate, tmp_path / "logs")
    assert result.verdict == "PASS", result.to_dict()
    assert result.skipped_tests == ["acceptance::test_calc.py::test_platform"]


def test_successful_unstructured_command_is_not_per_case_evidence(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base)
    copy_worktree(base, candidate)
    policy = [{"id": "generic", "adapter": "command", "argv": ["{python}", "-c", "print('ok')"]}]
    result, _, _ = evaluate(base, candidate, tmp_path / "logs", task="code_refactor", policy=policy)
    assert result.verdict == "INSUFFICIENT_EVIDENCE"
    assert result.reason_codes == ["UNSTRUCTURED_TEST_COMMAND"]


def test_missing_pytest_produces_explicit_error_evidence(tmp_path):
    import mimo_harness.pytest_worker as worker
    report = tmp_path / "missing.json"
    proc = subprocess.run([sys.executable, "-S", worker.__file__, str(report), "[]"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 4
    assert json.loads(report.read_text())["runner_error"] == "PYTEST_NOT_INSTALLED"


def test_pytest_timeout_keeps_logs_and_error(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "test_slow.py").write_text("import time\ndef test_slow(): time.sleep(5)\n", encoding="utf-8")
    row = run_check(root, tmp_path / "logs", {"id": "slow", "adapter": "pytest",
                                             "timeout_seconds": 0.2}, True)
    assert row["status"] == "error"
    assert row["reason_codes"] == ["CHECK_TIMEOUT"]
    assert Path(row["stderr_log"]).is_file()


def test_empty_unittest_suite_cannot_pass(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create_project(base)
    (base / "test_calc.py").write_text("import unittest\n", encoding="utf-8")
    copy_worktree(base, candidate)
    before = BuildTesterV02(base, tmp_path / "logs/base").run_all(True)
    after = BuildTesterV02(candidate, tmp_path / "logs/after").run_all(True)
    patch = SimpleNamespace(status="applied", applied=True, changed_files="M calc.py")
    result = classify_candidate(SimpleNamespace(task_type="code_refactor"), patch, before, after)
    assert result.verdict == "INSUFFICIENT_EVIDENCE"
    assert before["results"][-1]["returncode"] == 5
