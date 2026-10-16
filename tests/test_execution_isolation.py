"""Real-process isolation and interruption regression tests."""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from mimo_harness.execution import ExecutionContext, clean_environment, run_process, validate_execution
from mimo_harness.cli import main
from run_mimo_codeharness_v02 import BuildTesterV02, run_harness


def running(pid):
    if os.name == "nt":
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = w.DWORD()
        api.GetExitCodeProcess(handle, ctypes.byref(code))
        api.CloseHandle(handle)
        return code.value == 259
    try:
        if sys.platform == "linux":
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            if state == "Z":
                return False
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False


def wait_for(path, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return
        time.sleep(0.05)
    pytest.fail(f"Child did not start: {path}")


@pytest.mark.parametrize("exit_early", [False, True])
def test_process_tree_cleanup_on_timeout_and_normal_exit(tmp_path, exit_early):
    pid_path = tmp_path / "child.pid"
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"Path({str(pid_path)!r}).write_text(str(p.pid))\n"
        + ("" if exit_early else "time.sleep(60)\n"), encoding="utf-8")
    outcome = run_process([sys.executable, str(script)], tmp_path, timeout=3)
    assert outcome.status == ("completed" if exit_early else "timeout")
    assert pid_path.is_file()
    pid = int(pid_path.read_text())
    end = time.monotonic() + 5
    while running(pid) and time.monotonic() < end:
        time.sleep(0.05)
    assert not running(pid), "A descendant survived managed process cleanup"


def test_clean_environment_keeps_credentials_and_user_config_out(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "fake-test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-test-secret")
    monkeypatch.setenv("PYTHONPATH", "/must-not-leak")
    monkeypatch.setenv("PROJECT_PUBLIC_FLAG", "expected")
    env = clean_environment(tmp_path / "home", ["PROJECT_PUBLIC_FLAG"])
    outcome = run_process([sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"], tmp_path, env=env)
    recorded = json.loads(outcome.stdout)
    assert "MIMO_API_KEY" not in recorded
    assert "AWS_SECRET_ACCESS_KEY" not in recorded
    assert "PYTHONPATH" not in recorded
    assert recorded["PROJECT_PUBLIC_FLAG"] == "expected"
    assert recorded["HOME"] == str(tmp_path / "home")
    assert recorded["TMPDIR"] == str(tmp_path / "home/tmp")


def test_large_output_is_streamed_and_tail_is_bounded(tmp_path):
    output = tmp_path / "stdout.log"
    outcome = run_process([sys.executable, "-c", "print('x' * 2000000)"], tmp_path, stdout_path=output)
    assert outcome.returncode == 0
    assert output.stat().st_size >= 2000000
    assert len(outcome.stdout) <= 1024 * 1024


def test_separate_venvs_do_not_share_installed_modules(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    ctx1 = ExecutionContext(first, tmp_path / "logs1", {"mode": "venv"})
    ctx2 = ExecutionContext(second, tmp_path / "logs2", {"mode": "venv"})
    ctx1.setup()
    ctx2.setup()
    install = "import sysconfig; from pathlib import Path; (Path(sysconfig.get_paths()['purelib'])/'mimo_isolation_probe.py').write_text('VALUE=42')"
    assert ctx1.run([ctx1.python, "-c", install], first, 30, tmp_path / "install.out", tmp_path / "install.err").returncode == 0
    assert ctx1.run([ctx1.python, "-c", "import mimo_isolation_probe"], first, 30, tmp_path / "first.out", tmp_path / "first.err").returncode == 0
    assert ctx2.run([ctx2.python, "-c", "import mimo_isolation_probe"], second, 30, tmp_path / "second.out", tmp_path / "second.err").returncode != 0
    assert ctx1.python != ctx2.python
    assert "include-system-site-packages = false" in Path(ctx1.python).parent.parent.joinpath("pyvenv.cfg").read_text().lower()
    assert (tmp_path / "logs1/installed-packages_stdout.log").is_file()
    with pytest.raises(ValueError, match="Refusing to reuse"):
        ctx1.setup()


def make_dependency_wheel(path):
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("mimo_fixture_dep/__init__.py", "VALUE = 42\n")
        wheel.writestr("mimo_fixture_dep-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: mimo-fixture-dep\nVersion: 1.0\n")
        wheel.writestr("mimo_fixture_dep-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: mimo-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr("mimo_fixture_dep-1.0.dist-info/RECORD", "")


def test_venv_cli_installs_offline_dependency_and_preserves_source(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "test_value.py").write_text(
        "import unittest\nfrom value import VALUE\nfrom mimo_fixture_dep import VALUE as DEP\n"
        "class Tests(unittest.TestCase):\n    def test_value(self):\n        self.assertIn(VALUE, (1,2))\n        self.assertEqual(DEP,42)\n", encoding="utf-8")
    wheel_name = "mimo_fixture_dep-1.0-py3-none-any.whl"
    make_dependency_wheel(project / wheel_name)
    monkeypatch.chdir(project)
    assert main(["init"]) == 0
    config = json.loads(Path("mimo-harness.json").read_text())
    config["execution"] = {"mode": "venv", "prepare": [
        {"argv": ["{python}", "-m", "pip", "install", "--no-index", "--no-deps", wheel_name]}]}
    Path("mimo-harness.json").write_text(json.dumps(config), encoding="utf-8")
    Path("fix.patch").write_text("--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n", encoding="utf-8")
    assert main(["verify", "--base", "WORKTREE", "--patch", "fix.patch", "--run-tests", "--name", "isolated"]) == 0
    root = project / ".mimo/runs/isolated"
    baseline = json.loads((root / "baseline_summary.json").read_text())
    candidate = json.loads((root / "candidate_summary.json").read_text())
    assert baseline["execution_environment"]["mode"] == "venv"
    assert baseline["execution_environment"]["python"] != candidate["execution_environment"]["python"]
    assert (project / "value.py").read_text() == "VALUE = 1\n"
    assert main(["report"]) == 0


def test_preparation_failure_stops_checks_and_retains_evidence(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "test_never.py").write_text("def test_never(): assert False\n", encoding="utf-8")
    summary = BuildTesterV02(root, tmp_path / "logs", checks=[{"id": "tests", "adapter": "pytest"}],
                            execution={"mode": "venv", "prepare": [{"argv": ["{python}", "-c", "raise SystemExit(1)"]}]}).run_all(True)
    assert summary["external_executed_count"] == 0
    assert summary["results"][-1]["reason_codes"] == ["ENVIRONMENT_PREPARATION_ERROR"]
    assert (tmp_path / "logs/preparation.json").is_file()
    assert not (tmp_path / "logs/tests_tests.json").exists()


@pytest.mark.parametrize("bad", [
    {"mode": "unknown"}, {"mode": "local", "prepare": [{"argv": ["pip", "install", "x"]}]},
    {"mode": "venv", "env_allowlist": ["PYTHONPATH"]}, {"mode": "docker"},
    {"mode": "docker", "image": "x", "network": "host"},
    {"mode": "docker", "image": "x", "pids_limit": 0},
    {"mode": "venv", "prepare": [{"argv": ["x"], "cwd": "../outside"}]},
    {"mode": "venv", "reuse": True},
])
def test_invalid_execution_policy_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_execution(bad)


def test_requested_docker_never_silently_falls_back(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    import mimo_harness.execution as execution
    monkeypatch.setattr(execution.shutil, "which", lambda _: None)
    summary = BuildTesterV02(root, tmp_path / "logs", checks=[],
                            execution={"mode": "docker", "image": "missing-image"}).run_all(True)
    assert summary["execution_environment"]["mode"] == "docker"
    assert summary["results"][-1]["status"] == "error"
    assert summary["external_executed_count"] == 0


def test_analysis_only_run_cannot_hide_runtime_failure(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [{"name": "mock", "provider_type": "mock", "enabled": True}]}), encoding="utf-8")
    import mimo_harness.execution as execution
    monkeypatch.setattr(execution.shutil, "which", lambda _: None)
    log = run_harness(root, "runtime-failure", config, 1, 100, False, False, True,
                      output_root=tmp_path / "runs", checks=[],
                      execution={"mode": "docker", "image": "missing-image"})
    assert log["verification_summary"]["overall_verdict"] == "ERROR"
    assert log["verification_summary"]["exit_code"] == 4
    assert log["verification_summary"]["run_errors"][0]["stage"] == "baseline"


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT integration; Windows Job cleanup tested by real timeout and normal exit")
@pytest.mark.parametrize("stage", ["check", "prepare"])
def test_ctrl_c_stops_children_and_keeps_cli_evidence(tmp_path, monkeypatch, stage):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    assert main(["init"]) == 0
    ready = tmp_path / "ready.pid"
    config = json.loads(Path("mimo-harness.json").read_text())
    config["checks"] = [{"id": "slow", "adapter": "command", "argv": ["{python}", "-c",
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(ready)!r}).write_text(str(p.pid)); time.sleep(60)"]}]
    if stage == "prepare":
        config["execution"] = {"mode": "venv", "prepare": [{"argv": config["checks"][0]["argv"]}]}
    Path("mimo-harness.json").write_text(json.dumps(config), encoding="utf-8")
    Path("value.txt").write_text("before\n", encoding="utf-8")
    Path("fix.patch").write_text("--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-before\n+after\n", encoding="utf-8")
    # Use the actual console process so SIGINT traverses the public CLI path.
    with (tmp_path / "console.log").open("wb") as output:
        process = subprocess.Popen([sys.executable, "-m", "mimo_harness", "verify", "--base", "WORKTREE",
                                    "--patch", "fix.patch", "--run-tests", "--name", "cancelled"],
                                   stdout=output, stderr=output)
        try:
            wait_for(ready)
            child_pid = int(ready.read_text())
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=15) == 130
            assert not running(child_pid)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    run = root / ".mimo/runs/cancelled"
    assert json.loads((run / "verification_summary.json").read_text())["overall_verdict"] == "CANCELLED"
    if stage == "check":
        assert json.loads((run / "baseline/slow_check.json").read_text())["status"] == "cancelled"
    else:
        assert json.loads((run / "baseline/preparation.json").read_text())[-1]["status"] == "cancelled"
    assert main(["report"]) == 0
