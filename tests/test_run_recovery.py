"""Recovery acceptance through the public CLI, real tests and real OS locks."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mimo_harness.cli import main
from mimo_harness.manifests import file_digest, source_inventory
import run_mimo_codeharness_v02 as harness


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def project(tmp_path, monkeypatch, git=False):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "test_value.py").write_text("from value import VALUE\ndef test_value(): assert VALUE == 2\n", encoding="utf-8")
    if git:
        for argv in (["init", "-q"], ["config", "user.email", "tests@example.invalid"],
                     ["config", "user.name", "Test"], ["add", "."], ["commit", "-qm", "baseline"]):
            subprocess.run(["git", *argv], cwd=root, check=True)
    assert main(["init"]) == 0
    cfg_path = root / "mimo-harness.json"
    cfg = read(cfg_path)
    cfg["checks"] = [{"id": "unit", "adapter": "pytest", "args": ["-q"],
                      "target_tests": ["test_value.py::test_value"]}]
    cfg["execution"]["env_allowlist"] = ["MIMO_ALLOWED"]
    write(cfg_path, cfg)
    patch = tmp_path / "fix.patch"
    patch.write_text("--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n", encoding="utf-8")
    return root, patch


def verify(root, patch, base="WORKTREE", name="first"):
    assert main(["verify", "--patch", str(patch), "--base", base, "--task-type", "bug_fix",
                 "--run-tests", "--name", name]) == 0
    return root / ".mimo/runs" / name


@pytest.mark.parametrize("base", ["WORKTREE", "HEAD"])
def test_resume_reexecutes_real_tests_and_cannot_reuse_a_stale_pass(tmp_path, monkeypatch, base):
    root, patch = project(tmp_path, monkeypatch, git=True)
    flag = tmp_path / "external-failure"
    source = root / "test_value.py"
    source.write_text("from value import VALUE\nfrom pathlib import Path\ndef test_value():\n"
                      f"    assert VALUE == 2\n    assert not Path({str(flag)!r}).exists()\n", encoding="utf-8")
    subprocess.run(["git", "add", "test_value.py"], check=True)
    subprocess.run(["git", "commit", "-qm", "acceptance"], check=True)
    first = verify(root, patch, base)
    original = (first / "run_manifest.json").read_bytes()
    assert read(first / "run_manifest.json")["status"] == "COMPLETED"
    assert main(["report", "--run", str(first)]) == 0
    flag.touch()
    # The external patch can be removed: the saved immutable input remains usable.
    patch.unlink()
    assert main(["resume", "--run", str(first), "--name", "second"]) == 2
    second = root / ".mimo/runs/second"
    assert read(second / "verification_summary.json")["overall_verdict"] == "FAIL"
    manifest = read(second / "run_manifest.json")
    assert manifest["resumed_from"]["run_id"] == read(first / "run_manifest.json")["run_id"]
    assert manifest["fingerprint"] == read(first / "run_manifest.json")["fingerprint"]
    assert "second" in read(second / "candidate_summary.json")["project_root"]
    assert (first / "run_manifest.json").read_bytes() == original
    assert (root / "value.py").read_text() == "VALUE = 1\n"


def test_interrupted_models_are_checkpointed_individually_and_failed_calls_retry(tmp_path, monkeypatch):
    root, _ = project(tmp_path, monkeypatch)
    real = harness.call_model
    calls = []

    def interrupted(task, *args, **kw):
        calls.append(task.task_id)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(task, *args, **kw)

    monkeypatch.setattr(harness, "call_model", interrupted)
    assert main(["run", "--name", "first", "--limit-tasks", "3", "--no-run-tests"]) == 130
    first = root / ".mimo/runs/first"
    saved = read(first / "run_manifest.json")
    assert saved["status"] == "CANCELLED"
    assert len(saved["model_checkpoints"]) == 1
    first_id = calls[0]

    def retry(task, *args, **kw):
        calls.append(task.task_id)
        if len(calls) == 3:
            raise RuntimeError("simulated provider failure")
        return real(task, *args, **kw)

    monkeypatch.setattr(harness, "call_model", retry)
    main(["resume", "--run", str(first), "--name", "second"])
    second = root / ".mimo/runs/second"
    second_manifest = read(second / "run_manifest.json")
    assert len(second_manifest["reused_candidates"]) == 1
    assert calls.count(first_id) == 1
    failed = [key for key, item in second_manifest["model_checkpoints"].items()
              if read(second / item["path"])["status"] == "failed"]
    assert len(failed) == 1
    monkeypatch.setattr(harness, "call_model", lambda task, *a, **kw: (calls.append(task.task_id) or real(task, *a, **kw)))
    main(["resume", "--run", str(second), "--name", "third"])
    third = read(root / ".mimo/runs/third/run_manifest.json")
    assert failed[0] not in third["reused_candidates"]
    assert calls.count(first_id) == 1
    assert all(read(root / ".mimo/runs/third" / cp["path"])["status"] == "completed"
               for cp in third["model_checkpoints"].values())


@pytest.mark.parametrize("change", ["binary", "tests", "policy", "models", "environment", "provider", "engine"])
def test_resume_invalidates_changed_inputs_before_any_model_call(tmp_path, monkeypatch, capsys, change):
    root, _ = project(tmp_path, monkeypatch)
    (root / "large.bin").write_bytes(b"\x00" * 600_000)
    monkeypatch.setenv("MIMO_ALLOWED", "private-value-one")
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 0
    first = root / ".mimo/runs/first"
    if change == "binary":
        (root / "large.bin").write_bytes(b"\x00" * 599_999 + b"\x01")
    elif change == "tests":
        (root / "test_value.py").write_text("def test_removed_acceptance(): assert True\n")
    elif change == "policy":
        cfg = read(root / "mimo-harness.json")
        cfg["checks"][0]["timeout_seconds"] = 10
        write(root / "mimo-harness.json", cfg)
    elif change == "models":
        cfg = read(root / ".mimo/models.json")
        cfg["models"][0]["model"] = "different-model"
        write(root / ".mimo/models.json", cfg)
    elif change == "environment":
        monkeypatch.setenv("MIMO_ALLOWED", "private-value-two")
    elif change == "provider":
        monkeypatch.setenv("MIMO_MODEL", "different-model")
    else:
        monkeypatch.setattr("mimo_harness.manifests.engine_identity", lambda: "changed-engine")
    monkeypatch.setattr(harness, "call_model", lambda *a, **kw: pytest.fail("must reject before calling a model"))
    assert main(["resume", "--run", str(first), "--name", "second"]) == 4
    output = capsys.readouterr()
    assert "Recovery inputs changed" in output.err
    assert "private-value" not in (first / "run_manifest.json").read_text()


@pytest.mark.parametrize("change", ["snapshot", "checkpoint", "artifact", "schema", "traversal"])
def test_resume_rejects_damaged_or_unsafe_evidence(tmp_path, monkeypatch, change):
    root, _ = project(tmp_path, monkeypatch)
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 0
    first = root / ".mimo/runs/first"
    path = first / "run_manifest.json"
    manifest = read(path)
    checkpoint = next(iter(manifest["model_checkpoints"].values()))
    if change == "snapshot":
        (first / "inputs/project/value.py").write_text("VALUE = 999\n")
    elif change == "checkpoint":
        (first / checkpoint["path"]).write_text("{}")
    elif change == "artifact":
        (first / "verification_summary.json").write_text("{}")
    elif change == "schema":
        manifest["schema_version"] = 999
        write(path, manifest)
    else:
        checkpoint["path"] = "../../outside.json"
        write(path, manifest)
    assert main(["resume", "--run", str(first), "--name", "second"]) == 4
    assert not (root / ".mimo/runs/second").exists()


def test_patch_and_moving_git_ref_invalidate_recovery(tmp_path, monkeypatch, capsys):
    root, patch = project(tmp_path, monkeypatch, git=True)
    first = verify(root, patch, "HEAD")
    patch.write_text(patch.read_text().replace("+VALUE = 2", "+VALUE = 3"))
    assert main(["resume", "--run", str(first)]) == 4
    assert "Original patch changed" in capsys.readouterr().err
    patch.write_bytes((first / "input.patch").read_bytes())
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "move HEAD"], check=True)
    assert main(["resume", "--run", str(first)]) == 4
    assert "resolved_base" in capsys.readouterr().err


def test_real_os_lock_blocks_concurrent_resume_and_releases_after_exit(tmp_path, monkeypatch, capsys):
    root, _ = project(tmp_path, monkeypatch)
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 0
    first = root / ".mimo/runs/first"
    code = "from pathlib import Path; from mimo_harness.manifests import RunLock; import sys\nwith RunLock(Path(sys.argv[1])):\n print('READY',flush=True)\n sys.stdin.readline()\n"
    child = subprocess.Popen([sys.executable, "-c", code, str(first)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "READY"
        assert main(["resume", "--run", str(first), "--name", "second"]) == 4
        assert "still active" in capsys.readouterr().err
    finally:
        child.terminate()
        child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()
    assert main(["resume", "--run", str(first), "--name", "second"]) == 0


def test_abrupt_process_death_keeps_completed_model_checkpoints(tmp_path, monkeypatch):
    root, _ = project(tmp_path, monkeypatch)
    marker = tmp_path / "model-started"
    script = """import time
from pathlib import Path
import run_mimo_codeharness_v02 as h
from mimo_harness.cli import main
real = h.call_model
def slow(task, *args, **kw):
    if task.task_id != 'T001':
        Path(MARKER).touch()
        time.sleep(60)
    return real(task, *args, **kw)
h.call_model = slow
raise SystemExit(main(['run', '--name', 'first', '--limit-tasks', '2', '--no-run-tests']))
""".replace("MARKER", repr(str(marker)))
    with (tmp_path / "console.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-c", script], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                assert process.poll() is None
                time.sleep(.05)
            assert marker.exists()
        finally:
            process.kill()
            process.wait(timeout=10)
    first = root / ".mimo/runs/first"
    assert read(first / "run_manifest.json")["status"] == "RUNNING"
    assert main(["resume", "--run", str(first), "--name", "second"]) == 0
    second = read(root / ".mimo/runs/second/run_manifest.json")
    assert len(second["reused_candidates"]) == 1
    assert second["status"] == "COMPLETED"


def test_legacy_runs_and_unsafe_or_existing_names_do_not_overwrite(tmp_path, monkeypatch):
    root, _ = project(tmp_path, monkeypatch)
    legacy = root / ".mimo/runs/legacy"
    legacy.mkdir(parents=True)
    write(legacy / "run_log.json", {"verification_summary": {"overall_verdict": "PASS"}})
    assert main(["report", "--run", str(legacy)]) == 0
    assert main(["resume", "--run", str(legacy)]) == 4
    assert main(["run", "--name", "../../escape"]) == 4
    assert not (root / ".mimo/escape").exists()
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 0
    first = root / ".mimo/runs/first"
    original = file_digest(first / "run_manifest.json")
    assert main(["resume", "--run", str(first), "--name", "first"]) == 4
    assert file_digest(first / "run_manifest.json") == original


def test_unsealed_result_is_reported_as_incomplete(tmp_path, monkeypatch, capsys):
    root, _ = project(tmp_path, monkeypatch)
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 0
    first = root / ".mimo/runs/first"
    # Simulate abrupt exit between writing run_log and sealing the manifest.
    path = first / "run_manifest.json"
    manifest = read(path)
    manifest["status"] = "RUNNING"
    write(path, manifest)
    log = read(first / "run_log.json")
    log["verification_summary"] = {"overall_verdict": "PASS", "exit_code": 0, "counts": {"PASS": 1}}
    write(first / "run_log.json", log)
    capsys.readouterr()
    assert main(["report", "--run", str(first)]) == 0
    assert "[RESULT] INCOMPLETE" in capsys.readouterr().out
    report = (first / "MIMO_RUN_SUMMARY.md").read_text()
    assert "**INCOMPLETE**" in report
    assert "| PASS | 0 |" in report


@pytest.mark.skipif(os.name == "nt", reason="POSIX real SIGINT integration; individual model cancellation and OS lock run on Windows")
def test_real_cli_interruption_then_resume_in_fresh_workspaces(tmp_path, monkeypatch):
    root, patch = project(tmp_path, monkeypatch)
    marker = tmp_path / "started"
    test = root / "test_value.py"
    test.write_text("from pathlib import Path\nimport time\nfrom value import VALUE\n"
                    f"marker=Path({str(marker)!r})\n"
                    "def test_value():\n"
                    "    if not marker.exists():\n"
                    "        marker.touch()\n        time.sleep(60)\n"
                    "    assert VALUE == 2\n", encoding="utf-8")
    with (tmp_path / "console.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "mimo_harness", "verify", "--base", "WORKTREE",
            "--patch", str(patch), "--task-type", "bug_fix", "--run-tests", "--name", "first"], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                assert process.poll() is None
                time.sleep(.05)
            assert marker.exists()
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=15) == 130
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    first = root / ".mimo/runs/first"
    assert read(first / "run_manifest.json")["status"] == "CANCELLED"
    assert main(["resume", "--run", str(first), "--name", "second"]) == 0
    assert read(root / ".mimo/runs/second/run_manifest.json")["status"] == "COMPLETED"
    assert read(first / "baseline/unit_check.json")["status"] == "cancelled"


@pytest.mark.skipif(os.name == "nt", reason="Symlink modes require privileges on Windows")
def test_input_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "outside").write_text("private")
    (root / "leak").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink escapes"):
        source_inventory(root)
