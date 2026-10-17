"""Real subprocess proposals, independent acceptance and recovery budget contracts."""
from __future__ import annotations

import difflib
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimo_harness.cli import default_project_config, main
from mimo_harness.cli_agents import AgentBudget, load_agent_config, validate_agent_ledger
from mimo_harness.evidence import RunReader, inspect_evidence
from mimo_harness.execution import run_process
from mimo_harness.manifests import atomic_json


ORIGINAL = "def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n"


def diff(before, after, name="calc.py"):
    return f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile=f"a/{name}", tofile=f"b/{name}"))


def case(tmp_path, *, patches=None, behavior="normal", budget=3, timeout=3, secret=False):
    project = tmp_path / "project"
    project.mkdir()
    (project / "calc.py").write_text(ORIGINAL, encoding="utf-8")
    tests = "from calc import add, mul\nimport os\ndef test_add():\n    assert add(3, 2) == 5\ndef test_mul():\n    assert mul(3, 2) == 6\n    assert 'REPAIR_TEST_KEY' not in os.environ\n"
    (project / "test_calc.py").write_text(tests, encoding="utf-8")
    patches = patches or [diff(ORIGINAL, ORIGINAL.replace("a - b", "a")), diff(ORIGINAL, ORIGINAL.replace("a - b", "a + b"))]
    script = f'''import json, os, sys, time
from pathlib import Path
r = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert r["schema_version"] == 1 and r["patch_basis"] == "original_baseline"
assert "fix addition" in r["task"]
assert r["feedback"]["cases"]
assert bool(r["previous_patch"]) == (r["round"] > 1)
if {secret!r}:
    assert os.environ["REPAIR_TEST_KEY"]
    print(os.environ["REPAIR_TEST_KEY"], file=sys.stderr)
behavior = {behavior!r}
if behavior == "timeout":
    time.sleep(60)
if behavior == "overflow":
    print("x" * 1000000)
elif behavior == "invalid_utf8":
    patch = {patches!r}[0]
    payload = json.dumps({{"schema_version": 1, "status": "completed", "patch": patch + "#marker"}}).encode("utf-8")
    sys.stdout.buffer.write(payload.replace(b"#marker", b"#\\xff"))
elif behavior == "invalid":
    print("not json")
elif behavior == "exit":
    sys.exit(7)
elif behavior == "incomplete":
    print(json.dumps({{"schema_version": 1, "status": "truncated", "patch": "diff --git a/calc.py b/calc.py"}}))
elif behavior == "tools":
    print(json.dumps({{"schema_version": 1, "status": "completed", "tool_calls": []}}))
else:
    # Edits to the proposer workspace must never become acceptance inputs.
    Path("calc.py").write_text("raise RuntimeError('agent workspace must be discarded')\\n", encoding="utf-8")
    patches = {patches!r}
    patch = patches[min(r["round"] - 1, len(patches) - 1)]
    print(json.dumps({{"schema_version": 1, "status": "completed", "patch": patch}}))
'''
    (project / "adapter.py").write_text(script, encoding="utf-8")
    config = default_project_config()
    config.update(project=str(project), output_dir=str(tmp_path / "runs"),
                  checks=[{"id": "unit", "adapter": "pytest", "args": ["-q", "test_calc.py"],
                           "target_tests": ["test_calc.py::test_add"]}])
    config_path = tmp_path / "mimo-harness.json"
    atomic_json(config_path, config)
    agent_path = tmp_path / "agent.json"
    atomic_json(agent_path, {"schema_version": 1, "argv": ["{python}", "adapter.py", "{request}"],
                            "timeout_seconds": timeout, "max_output_bytes": 16384,
                            "execution": {"mode": "local", "env_allowlist": ["REPAIR_TEST_KEY"] if secret else []},
                            "budget": {"max_invocations": budget}})
    task_path = tmp_path / "task.txt"
    task_path.write_text("fix addition; keep multiplication working", encoding="utf-8")
    return SimpleNamespace(project=project, config=config_path, agent=agent_path, task=task_path,
                           root=tmp_path, tests=tests)


def repair(c, name="first", rounds=2):
    return main(["--config", str(c.config), "repair", "--agent-config", str(c.agent), "--task-file", str(c.task),
                 "--base", "WORKTREE", "--max-rounds", str(rounds), "--name", name])


def read(c, name="first", file="repair_history.json"):
    return json.loads((c.root / "runs" / name / file).read_text(encoding="utf-8"))


def test_two_round_repair_preserves_failed_evidence_and_original(tmp_path, monkeypatch):
    c = case(tmp_path, secret=True)
    monkeypatch.setenv("REPAIR_TEST_KEY", "credential-canary-for-cli-agent")
    assert repair(c) == 0
    h = read(c)
    assert [r["verification"]["verdict"] for r in h["rounds"]] == ["FAIL", "PASS"]
    assert h["selected_round"] == 2 and h["stop_reason"] == "VERIFIED_PASS"
    assert (c.project / "calc.py").read_text() == ORIGINAL
    assert (c.project / "test_calc.py").read_text() == c.tests
    run = c.root / "runs/first"
    assert "credential-canary" not in (run / "agent_runs/repair-001/logs/stderr.log").read_text()
    assert main(["gate", "--run", str(run), "--expect-base", "WORKTREE", "--expect-patch", str(run / "input.patch")]) == 0
    bundle = tmp_path / "evidence.zip"
    assert main(["export", "--run", str(run), "--output", str(bundle)]) == 0
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        assert any("round-001" in n and n.endswith("_tests.json") for n in names)
        assert not any("agent_runs" in n or "worktrees" in n or n.endswith("task.txt") for n in names)
        assert all(b"credential-canary" not in z.read(n) for n in names)
    assert main(["gate", "--bundle", str(bundle)]) == 0


def test_resume_reuses_proposals_but_not_tests(tmp_path):
    c = case(tmp_path, budget=2)
    assert repair(c) == 0
    old = c.root / "runs/first"
    before = (old / "run_manifest.json").read_bytes()
    assert main(["resume", "--run", str(old), "--name", "second"]) == 0
    m = read(c, "second", "run_manifest.json")
    assert m["reused_candidates"] == ["repair-001", "repair-002"]
    assert read(c, "second", "agent_ledger.json")["summary"]["invocations_reserved"] == 2
    assert (old / "run_manifest.json").read_bytes() == before
    assert main(["gate", "--run", str(c.root / "runs/second")]) == 0


def test_resume_previous_pass_runs_fresh_and_detects_external_regression(tmp_path):
    c = case(tmp_path, budget=2)
    flag = tmp_path / "external-state.txt"
    flag.write_text("1")
    (c.project / "test_calc.py").write_text(c.tests + f"\ndef test_external():\n    from pathlib import Path\n    if add(3, 2) == 5:\n        assert Path({str(flag)!r}).read_text() == '1'\n", encoding="utf-8")
    assert repair(c) == 0
    flag.write_text("0")
    assert main(["resume", "--run", str(c.root / "runs/first"), "--name", "second"]) == 2
    assert read(c, "second", "run_manifest.json")["reused_candidates"] == ["repair-001", "repair-002"]
    assert read(c, "second", "agent_ledger.json")["summary"]["invocations_reserved"] == 2
    assert "REGRESSION_DETECTED" in read(c, "second")["rounds"][-1]["verification"]["reason_codes"]
    assert main(["gate", "--run", str(c.root / "runs/second")]) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT; Windows Job cleanup covered by timeout and normal exit")
def test_agent_ctrl_c_cleans_children_and_reserves_budget(tmp_path):
    from test_execution_isolation import running, wait_for
    c = case(tmp_path, timeout=60)
    ready = tmp_path / "ready.pid"
    script = c.project / "adapter.py"
    script.write_text("import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                      f"Path({str(ready)!r}).write_text(str(p.pid))\ntime.sleep(60)\n", encoding="utf-8")
    with (tmp_path / "console.log").open("wb") as console:
        proc = subprocess.Popen([sys.executable, "-m", "mimo_harness", "--config", str(c.config), "repair",
                                 "--agent-config", str(c.agent), "--task-file", str(c.task), "--base", "WORKTREE", "--name", "first"],
                                stdout=console, stderr=console, cwd=tmp_path)
        try:
            wait_for(ready)
            proc.send_signal(signal.SIGINT)
            assert proc.wait(timeout=15) == 130
            assert not running(int(ready.read_text()))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    assert read(c, file="run_manifest.json")["status"] == "CANCELLED"
    assert read(c, file="agent_ledger.json")["entries"][0]["status"] == "cancelled"
    assert main(["gate", "--run", str(c.root / "runs/first")]) == 4


@pytest.mark.parametrize("timeout", [2, 5])
def test_bounded_process_reaps_background_children_on_timeout_and_normal_exit(tmp_path, timeout):
    from test_execution_isolation import running
    ready = tmp_path / "ready.pid"
    code = "import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
    code += f"Path({str(ready)!r}).write_text(str(p.pid)); " + ("time.sleep(60)" if timeout < 3 else "print('done')")
    outcome = run_process([sys.executable, "-c", code], tmp_path, timeout=timeout, max_output_bytes=4096)
    assert outcome.status == ("timeout" if timeout < 3 else "completed")
    assert ready.is_file()
    deadline = time.monotonic() + 3
    while running(int(ready.read_text())) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not running(int(ready.read_text()))


def test_agent_credential_in_proposal_is_rejected_and_redacted(tmp_path, monkeypatch):
    c = case(tmp_path, secret=True)
    secret = "not-for-generated-code-credential"
    monkeypatch.setenv("REPAIR_TEST_KEY", secret)
    script = c.project / "adapter.py"
    script.write_text("import json,os\nprint(json.dumps({'schema_version':1,'status':'completed','patch':"
                      "'--- a/calc.py\\n+++ b/calc.py\\n@@ -1 +1 @@\\n-x\\n+'+os.environ['REPAIR_TEST_KEY']+'\\n'}))\n", encoding="utf-8")
    assert repair(c) == 4
    assert read(c)["stop_reason"] == "AGENT_CREDENTIAL_IN_PATCH"
    assert secret not in (c.root / "runs/first/agent_runs/repair-001/logs/stdout.log").read_text()


def test_missing_agent_credentials_and_overlapping_test_environment_block_preflight(tmp_path, monkeypatch):
    c = case(tmp_path, secret=True)
    monkeypatch.delenv("REPAIR_TEST_KEY", raising=False)
    assert repair(c) == 4
    assert not (c.root / "runs").exists()
    monkeypatch.setenv("REPAIR_TEST_KEY", "canary")
    config = json.loads(c.config.read_text())
    config["execution"]["env_allowlist"] = ["REPAIR_TEST_KEY"]
    atomic_json(c.config, config)
    assert repair(c) == 4
    assert not (c.root / "runs").exists()


@pytest.mark.parametrize("behavior,error", [("timeout", "AGENT_TIMEOUT"), ("overflow", "AGENT_OUTPUT_LIMIT"),
    ("invalid", "AGENT_INVALID_JSON"), ("invalid_utf8", "AGENT_INVALID_JSON"), ("exit", "AGENT_EXIT_ERROR"),
    ("incomplete", "AGENT_INCOMPLETE_RESPONSE"), ("tools", "AGENT_INCOMPLETE_RESPONSE")])
def test_process_and_protocol_failures_cannot_pass(tmp_path, behavior, error):
    c = case(tmp_path, behavior=behavior, timeout=0.2 if behavior == "timeout" else 3)
    assert repair(c) == 4
    h = read(c)
    assert len(h["rounds"]) == 1 and h["stop_reason"] == error
    assert read(c, file="agent_ledger.json")["summary"]["invocations_reserved"] == 1
    assert main(["gate", "--run", str(c.root / "runs/first")]) == 4
    assert (c.project / "calc.py").read_text() == ORIGINAL


def test_round_limit_regression_and_acceptance_tampering(tmp_path):
    regressed = ORIGINAL.replace("a - b", "a + b").replace("a * b", "0")
    c = case(tmp_path, patches=[diff(ORIGINAL, regressed)])
    assert repair(c, rounds=2) == 2
    h = read(c)
    assert h["stop_reason"] == "ROUND_LIMIT_REACHED"
    assert "REGRESSION_DETECTED" in h["rounds"][-1]["verification"]["reason_codes"]
    d = tmp_path / "tamper"
    d.mkdir()
    x = case(d)
    script = x.project / "adapter.py"
    script.write_text(script.read_text().replace(repr([diff(ORIGINAL, ORIGINAL.replace("a - b", "a")), diff(ORIGINAL, ORIGINAL.replace("a - b", "a + b"))]),
        repr([diff(x.tests, x.tests.replace("== 5", "== 1"), "test_calc.py")])))
    assert repair(x, rounds=1) == 3
    assert "ACCEPTANCE_INPUT_CHANGED" in read(x)["rounds"][0]["verification"]["reason_codes"]


def test_no_reproducible_target_does_not_invoke_agent(tmp_path):
    c = case(tmp_path)
    (c.project / "calc.py").write_text(ORIGINAL.replace("a - b", "a + b"))
    assert repair(c) == 4
    assert read(c, file="agent_ledger.json")["summary"]["invocations_reserved"] == 0
    assert not (c.root / "runs/first/agent_runs").exists()


def test_budget_exhaustion_and_old_ancestor_resume_cannot_reset(tmp_path):
    c = case(tmp_path, budget=1, behavior="invalid")
    assert repair(c) == 4
    old = c.root / "runs/first"
    for name in ("branch-one", "branch-two"):
        assert main(["resume", "--run", str(old), "--name", name]) == 4
        assert read(c, name, "agent_ledger.json")["summary"]["invocations_reserved"] == 1
        assert not (c.root / "runs" / name / "agent_runs").exists()


def test_budget_exhaustion_after_a_valid_failed_round_retains_diagnosis(tmp_path):
    c = case(tmp_path, budget=1)
    assert repair(c) == 4
    h = read(c)
    assert h["stop_reason"] == "AGENT_BUDGET_EXHAUSTED" and len(h["rounds"]) == 1
    assert read(c, file="verification_summary.json")["run_errors"][0]["reason_codes"] == ["AGENT_BUDGET_EXHAUSTED"]
    assert main(["gate", "--run", str(c.root / "runs/first")]) == 4


@pytest.mark.parametrize("changed", ["task", "config", "ledger", "missing_coordinator"])
def test_recovery_rejects_changed_inputs_and_accounting(tmp_path, changed):
    c = case(tmp_path)
    assert repair(c) == 0
    if changed == "task":
        c.task.write_text("different defect")
    elif changed == "config":
        cfg = json.loads(c.agent.read_text())
        cfg["timeout_seconds"] = 5
        atomic_json(c.agent, cfg)
    elif changed == "ledger":
        atomic_json(c.root / "runs/first/agent_ledger.json", {})
    else:
        next((c.root / "runs/.agent-budgets").glob("*/ledger.json")).unlink()
    assert main(["resume", "--run", str(c.root / "runs/first"), "--name", "second"]) == 4


@pytest.mark.parametrize("corruption", ["selection", "round_verdict", "patch_identity", "reservation", "options"])
def test_gate_recomputes_history_after_reseal(tmp_path, corruption):
    c = case(tmp_path)
    assert repair(c) == 0
    root = c.root / "runs/first"
    h = read(c)
    if corruption == "selection":
        h["selected_round"] = 1
    elif corruption == "round_verdict":
        h["rounds"][0]["verification"]["verdict"] = "PASS"
    elif corruption == "patch_identity":
        h["rounds"][0]["patch_sha256"] = "0" * 64
    elif corruption == "reservation":
        h["rounds"][1]["reservation_id"] = "nonexistent"
    atomic_json(root / "repair_history.json", h)
    m = read(c, file="run_manifest.json")
    from mimo_harness.manifests import file_digest
    m["artifacts"]["repair_history.json"] = file_digest(root / "repair_history.json")
    if corruption == "options":
        m["options"]["max_rounds"] = 100
    atomic_json(root / "run_manifest.json", m)
    with pytest.raises(ValueError):
        inspect_evidence(RunReader(root))


@pytest.mark.parametrize("field,value", [("argv", "python adapter.py"), ("argv", ["python", "{unknown}", "{request}"]),
    ("cwd", "../outside"), ("timeout_seconds", float("nan")), ("max_output_bytes", True),
    ("execution", {"mode": "venv"}), ("budget", {"max_invocations": 0}), ("budget", {"max_tokens": 1000})])
def test_strict_agent_configuration(tmp_path, field, value):
    c = case(tmp_path)
    cfg = json.loads(c.agent.read_text())
    cfg[field] = value
    atomic_json(c.agent, cfg)
    with pytest.raises(ValueError):
        load_agent_config(c.agent)


def test_output_collection_is_bounded_even_for_fast_exit(tmp_path):
    out, err = tmp_path / "out.log", tmp_path / "err.log"
    result = run_process([sys.executable, "-c", "import os; os.write(1,b'x'*1000000); os.write(2,b'y'*1000000)"],
                         tmp_path, timeout=5, stdout_path=out, stderr_path=err, max_output_bytes=4096)
    assert result.status == "output_limit"
    assert out.stat().st_size <= 4096 and err.stat().st_size <= 4096


class LedgerManifest:
    def __init__(self, root, previous=None):
        self.root, self.previous = root, previous
        self.data = {"provider_budget_id": "lineage", "fingerprint": "same-inputs"}
        self.ledger = None

    def save_agent_ledger(self, ledger):
        self.ledger = json.loads(json.dumps(ledger))


def test_sibling_reservations_use_shared_coordinator_and_keep_snapshots(tmp_path):
    policy = {"max_invocations": 2, "max_reserved_seconds": 4}
    a = LedgerManifest(tmp_path / "first")
    ba = AgentBudget(a, policy)
    first = ba.reserve("repair-001", 2)
    ba.finish(first, "failed")
    original = a.ledger
    prior = SimpleNamespace(agent_ledger=original)
    b = LedgerManifest(tmp_path / "second", prior)
    c = LedgerManifest(tmp_path / "third", prior)
    bb, bc = AgentBudget(b, policy), AgentBudget(c, policy)
    bb.reserve("repair-001", 2)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        bc.reserve("repair-001", 2)
    assert a.ledger == original and len(original["entries"]) == 1
    validate_agent_ledger(b.ledger)


def test_command_time_reservations_are_not_refunded(tmp_path):
    m = LedgerManifest(tmp_path / "first")
    b = AgentBudget(m, {"max_invocations": 3, "max_reserved_seconds": 5})
    reservation = b.reserve("repair-001", 3)
    b.finish(reservation, "cancelled")
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        b.reserve("repair-002", 3)
