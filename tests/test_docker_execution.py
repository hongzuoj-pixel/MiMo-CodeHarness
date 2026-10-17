"""Real Docker acceptance; enabled explicitly on the Linux CI Docker worker."""
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from mimo_harness.checks import run_check
from mimo_harness.execution import ExecutionContext
from mimo_harness.cli import default_project_config, main
from run_mimo_codeharness_v02 import BuildTesterV02

IMAGE = os.getenv("MIMO_DOCKER_TEST_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Requires configured real Linux Docker acceptance worker")


def policy(**kw):
    return {"mode": "docker", "image": IMAGE, "memory_mb": 512, "pids_limit": 128, **kw}


def assert_removed(names):
    assert names
    for name in names:
        assert subprocess.run(["docker", "inspect", name], capture_output=True).returncode != 0


def test_docker_resources_network_credentials_and_evidence(tmp_path, monkeypatch):
    root = tmp_path / "project with spaces"
    root.mkdir()
    monkeypatch.setenv("MIMO_API_KEY", "fake-test-key")
    (root / "test_boundary.py").write_text("""import os, socket, subprocess, sys
from pathlib import Path
import pytest

def test_nonroot_and_secret_boundary():
    assert os.getuid() != 0
    assert 'MIMO_API_KEY' not in os.environ
    assert not Path('/var/run/docker.sock').exists()
    with pytest.raises(OSError):
        Path('/etc/mimo-write-probe').write_text('must not write')
    assert os.statvfs('/').f_flag & os.ST_RDONLY
    status = Path('/proc/self/status').read_text()
    assert int(status.split('CapEff:')[1].split()[0], 16) == 0
    assert int(status.split('NoNewPrivs:')[1].split()[0]) == 1

def test_network_disabled():
    with socket.socket() as sock:
        sock.settimeout(1)
        with pytest.raises(OSError):
            sock.connect(('1.1.1.1', 53))

def test_resources_and_background_child():
    assert int(Path('/sys/fs/cgroup/memory.max').read_text()) <= 512 * 1024 * 1024
    assert int(Path('/sys/fs/cgroup/pids.max').read_text()) <= 128
    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
""", encoding="utf-8")
    summary = BuildTesterV02(root, tmp_path / "logs", checks=[{"id": "boundary", "adapter": "pytest", "args": ["-q"]}],
                            execution=policy()).run_all(True)
    assert summary["all_critical_ok"], json.dumps(summary)
    assert len(summary["test_cases"]) == 3
    assert summary["execution_environment"]["image_id"].startswith("sha256:")
    assert Path(summary["results"][-1]["junit_xml"]).is_file()
    assert_removed(summary["execution_environment"]["container_names"])


def test_docker_timeout_removes_container(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    ctx = ExecutionContext(root, tmp_path / "logs", policy())
    ctx.setup()
    row = run_check(root, tmp_path / "logs", {
        "id": "slow", "adapter": "command", "argv": ["{python}", "-c", "import time; time.sleep(60)"],
        "timeout_seconds": 2}, True, context=ctx)
    assert row["reason_codes"] == ["CHECK_TIMEOUT"]
    assert_removed(ctx.containers)


def test_docker_dependency_layers_are_separate(tmp_path):
    roots = [tmp_path / name for name in ("base", "candidate")]
    for root in roots:
        root.mkdir()
    contexts = [ExecutionContext(root, tmp_path / f"logs{i}", policy()) for i, root in enumerate(roots)]
    for ctx in contexts:
        ctx.setup()
    install = "import sysconfig; from pathlib import Path; (Path(sysconfig.get_paths()['purelib'])/'mimo_container_probe.py').write_text('VALUE=1')"
    for i, argv in enumerate(([contexts[0].python, "-c", install], [contexts[1].python, "-c", "import mimo_container_probe"])):
        outcome = contexts[i].run(argv, roots[i], 30, tmp_path / f"cmd{i}.out", tmp_path / f"cmd{i}.err")
        assert (outcome.returncode == 0) == (i == 0)
    for ctx in contexts:
        assert_removed(ctx.containers)


def test_cli_recovery_uses_fresh_containers_and_rejects_retagged_image(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "test_value.py").write_text("from value import VALUE\ndef test_value(): assert VALUE == 2\n", encoding="utf-8")
    patch = tmp_path / "fix.patch"
    patch.write_text("--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n", encoding="utf-8")
    alias = "mimo-resume-" + uuid.uuid4().hex
    cfg = default_project_config()
    cfg["execution"] = policy(image=alias)
    cfg["checks"] = [{"id": "unit", "adapter": "pytest", "target_tests": ["test_value.py::test_value"]}]
    (root / "mimo-harness.json").write_text(json.dumps(cfg), encoding="utf-8")
    subprocess.run(["docker", "tag", IMAGE, alias], check=True)
    try:
        assert main(["verify", "--patch", str(patch), "--base", "WORKTREE", "--task-type", "bug_fix",
                     "--run-tests", "--name", "first"]) == 0
        first = root / ".mimo/runs/first"
        bundle = tmp_path / "docker-evidence.zip"
        assert main(["export", "--run", str(first), "--output", str(bundle)]) == 0
        assert main(["gate", "--bundle", str(bundle)]) == 0
        assert main(["resume", "--run", str(first), "--name", "second"]) == 0
        second = root / ".mimo/runs/second"
        manifests = [json.loads((p / "run_manifest.json").read_text()) for p in (first, second)]
        assert manifests[0]["fingerprint"] == manifests[1]["fingerprint"]
        names = []
        for attempt in (first, second):
            for name in ("baseline", "candidate"):
                summary = json.loads((attempt / f"{name}_summary.json").read_text())
                names.extend(summary["execution_environment"]["container_names"])
        assert len(set(names)) == len(names)
        assert_removed(names)
        build = tmp_path / "retag"
        build.mkdir()
        (build / "Dockerfile").write_text(f"FROM {IMAGE}\nLABEL mimo.recovery=new-image\n", encoding="utf-8")
        subprocess.run(["docker", "build", "-t", alias, str(build)], check=True, capture_output=True)
        assert main(["resume", "--run", str(first), "--name", "retagged"]) == 4
        assert not (root / ".mimo/runs/retagged/baseline").exists()
        assert main(["gate", "--bundle", str(bundle)]) == 0  # historical evidence needs no live daemon/image lookup
    finally:
        subprocess.run(["docker", "image", "rm", alias], capture_output=True)


def test_ci_runner_enforces_real_patch_gate_and_keeps_failed_evidence(tmp_path):
    root = tmp_path / "baseline"
    root.mkdir()
    (root / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "test_value.py").write_text("from value import VALUE\ndef test_value(): assert VALUE in (1, 2)\n", encoding="utf-8")
    cfg = default_project_config()
    cfg["execution"] = policy()
    cfg["checks"] = [{"id": "unit", "adapter": "pytest"}]
    (root / "mimo-harness.json").write_text(json.dumps(cfg), encoding="utf-8")
    def git(*argv):
        return subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (root / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
    git("add", "value.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "good")
    good = git("rev-parse", "HEAD")
    (root / "value.py").write_text("VALUE = 3\n", encoding="utf-8")
    git("add", "value.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "bad")
    bad = git("rev-parse", "HEAD")
    git("checkout", "-q", base)
    script = Path(__file__).resolve().parents[1] / "examples/ci/verify_pr.py"
    import sys
    for head, expected, name in ((good, 0, "good"), (bad, 2, "bad")):
        output = tmp_path / name
        completed = subprocess.run([sys.executable, str(script), "--project", str(root),
            "--base", base, "--head", head, "--image", IMAGE, "--output", str(output)], capture_output=True, text=True)
        assert completed.returncode == expected, completed.stdout + completed.stderr
        assert (output / "evidence.zip").is_file()
        assert json.loads((output / "gate.json").read_text())["allowed"] is (expected == 0)
        assert (root / "value.py").read_text() == "VALUE = 1\n"
