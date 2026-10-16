"""Real Docker acceptance; enabled explicitly on the Linux CI Docker worker."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from mimo_harness.checks import run_check
from mimo_harness.execution import ExecutionContext
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
