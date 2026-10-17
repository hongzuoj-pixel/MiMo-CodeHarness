"""Finite CLI-Agent repair proposals with independent, fixed-baseline acceptance."""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from mimo_harness.cli_agents import AgentBudget, load_agent_config, preflight_agent, propose
from mimo_harness.manifests import RunManifest, atomic_json, file_digest
from run_mimo_codeharness_v02 import BuildTesterV02, apply_patches, copy_worktree
from verification_v04 import classify_candidate, summarize_verifications


def feedback(summary, run_dir, result=None):
    """Stable, bounded case diagnostics; no runtime paths in recovery signatures."""
    cases = []
    for case in sorted(summary.get("test_cases", []), key=lambda c: c["nodeid"]):
        if case["status"] == "passed":
            continue
        message = str(case.get("message", ""))
        for old, replacement in ((summary.get("project_root", ""), "<workspace>"), (str(run_dir), "<evidence>")):
            if old:
                message = message.replace(old, replacement).replace(old.replace("\\", "/"), replacement)
        cases.append({k: case.get(k) for k in ("nodeid", "status", "assertion_failure", "phases")})
        cases[-1]["message"] = message[:4096]
        if len(cases) == 16:
            break
    return {"cases": cases, "verification": result or {},
            "check_errors": [{"name": r["name"], "status": r["status"], "reason_codes": r.get("reason_codes", [])}
                             for r in summary.get("results", []) if r["status"] in {"error", "failed", "insufficient"}]}


def command_repair(args):
    # Lazy import keeps the public CLI and workflow modules independently usable.
    from mimo_harness.cli import (_config_path, _resolve, load_project_config, _run_directory,
                                  _run_name, _output_location_is_safe, _execute_attempt, _materialize_baseline)
    config_path = _config_path(args.config)
    cfg = load_project_config(config_path)
    agent_path = _resolve(Path.cwd(), args.agent_config)
    agent = load_agent_config(agent_path)
    if not 1 <= args.max_rounds <= 100:
        raise ValueError("max_rounds must be between 1 and 100.")
    checks = cfg.get("checks") or []
    if not any(c.get("target_tests") for c in checks if c["adapter"] == "pytest"):
        raise ValueError("Repair requires an explicit pytest check with target_tests that reproduce the defect.")
    if {k.upper() for k in agent["execution"]["env_allowlist"]} & {k.upper() for k in cfg["execution"]["env_allowlist"]}:
        raise ValueError("Agent and acceptance env_allowlist must be disjoint; keep Agent credentials out of tests.")
    project = _resolve(config_path.parent, args.project or cfg["project"])
    output_root = _resolve(config_path.parent, cfg["output_dir"])
    if not project.is_dir() or not shutil.which("git") or not _output_location_is_safe(project, output_root):
        raise ValueError("Repair requires a project directory, Git and a safe output_dir (.mimo/ or outputs/ inside the project).")
    preflight_agent(agent, project)
    task_path = _resolve(Path.cwd(), args.task_file)
    task_bytes = task_path.read_bytes()
    if not 0 < len(task_bytes) <= 65536 or not task_bytes.decode("utf-8").strip():
        raise ValueError("Repair task must be non-empty UTF-8 text, at most 64 KiB.")
    task = task_bytes.decode("utf-8")
    root = _run_directory(output_root, args.name or _run_name("repair"))

    def operate(state):
        previous = getattr(args, "recovery_source", None)
        snapshot = root / "inputs/project"
        resolved = _materialize_baseline(project, args.base, snapshot)
        (root / "inputs/task.txt").write_bytes(task_bytes)
        options = {"workflow": "repair", "base": args.base, "max_rounds": args.max_rounds,
                   "task_sha256": hashlib.sha256(task_bytes).hexdigest()}
        origin = {"project": str(project), "config": str(config_path), "agent_config": str(agent_path),
                  "task_file": previous.data["origin"]["task_file"] if previous else str(task_path)}
        manifest = RunManifest(root, "run", origin, options, cfg, resolved, previous)
        state["manifest"] = manifest
        budget = AgentBudget(manifest, agent["budget"])
        baseline_root = copy_worktree(snapshot, root / "worktrees/_baseline")
        baseline = BuildTesterV02(baseline_root, root / "baseline", checks=checks,
                                  execution=cfg["execution"]).run_all(run_external=True)
        atomic_json(root / "baseline_summary.json", baseline)
        targets = baseline.get("target_tests", [])
        cases = {c["nodeid"]: c for c in baseline.get("test_cases", [])}
        if (not targets or any(cases.get(t, {}).get("status") != "failed" or not cases.get(t, {}).get("assertion_failure") for t in targets)
                or any(r["status"] in {"error", "insufficient"} for r in baseline["results"])
                or any(r["status"] == "failed" for r in baseline["results"] if r["category"] != "test")):
            raise ValueError("BASELINE_TARGET_NOT_REPRODUCED: fix collection/setup/static errors and reproduce every target assertion before invoking an Agent.")
        history = {"schema_version": 1, "max_rounds": args.max_rounds, "rounds": [],
                   "stop_reason": "RUNNING", "selected_round": None, "final_patch_sha256": None}
        atomic_json(root / "repair_history.json", history)
        previous_patch, prior_feedback = "", feedback(baseline, root)
        for number in range(1, args.max_rounds + 1):
            print(f"[REPAIR] Round {number}/{args.max_rounds}", flush=True)
            request = {"schema_version": 1, "task": task, "round": number,
                       "source_sha256": manifest.data["inputs"]["source_sha256"],
                       "patch_basis": "original_baseline", "previous_patch": previous_patch,
                       "feedback": prior_feedback,
                       "instructions": "Return JSON schema_version=1, status=completed, patch=<cumulative UTF-8 unified diff against original_baseline>. Preserve acceptance tests/configuration. Workspace edits are not submitted. No prose or tool calls on stdout."}
            record = propose(agent, snapshot, root, request, manifest, budget)
            round_root = root / "verification" / f"round-{number:03d}"
            patch = apply_patches([record], snapshot, root, actually_apply=True)[0]
            candidate = None
            if patch.applied:
                candidate = BuildTesterV02(Path(patch.worktree), round_root / "candidate", checks=checks,
                                           execution=cfg["execution"]).run_all(run_external=True)
            result = classify_candidate(SimpleNamespace(task_id="REPAIR", task_type="bug_fix"), patch, baseline, candidate)
            row = {"round": number, "candidate_id": record.candidate_id, "agent_status": record.status,
                   "agent_error": record.error_code, "reservation_id": record.attempts[0]["reservation_id"],
                   "patch_sha256": file_digest(Path(patch.patch_file)) if patch.patch_file else None,
                   "patch_result": asdict(patch), "candidate_summary": candidate, "verification": result.to_dict()}
            history["rounds"].append(row)
            history["selected_round"] = number
            if patch.patch_file:
                previous_patch = Path(patch.patch_file).read_text(encoding="utf-8")
                shutil.copyfile(patch.patch_file, root / "input.patch")
            else:
                (root / "input.patch").unlink(missing_ok=True)
            history["final_patch_sha256"] = row["patch_sha256"]
            atomic_json(root / "repair_history.json", history)
            atomic_json(round_root / "verification_result.json", result.to_dict())
            print(f"[ROUND RESULT] {result.verdict}: {', '.join(result.reason_codes)}", flush=True)
            if result.verdict == "PASS":
                history["stop_reason"] = "VERIFIED_PASS"
                break
            if record.status != "completed" or result.verdict == "ERROR":
                history["stop_reason"] = record.error_code or "VERIFICATION_ERROR"
                break
            prior_feedback = feedback(candidate or baseline, root, result.to_dict())
            if not patch.applied:
                prior_feedback["patch_error"] = (patch.stderr or patch.note)[:4096].replace(str(root), "<evidence>")
        if history["stop_reason"] == "RUNNING":
            history["stop_reason"] = "ROUND_LIMIT_REACHED"
        atomic_json(root / "repair_history.json", history)
        verification = summarize_verifications([result])
        atomic_json(root / "verification_result.json", result.to_dict())
        atomic_json(root / "verification_summary.json", verification)
        atomic_json(root / "run_log.json", {"mode": "cli_agent_repair", "project": str(project),
                    "output_dir": str(root), "resolved_base": resolved,
                    "verification_summary": verification, "stop_reason": history["stop_reason"],
                    "round_count": len(history["rounds"]), "reused_candidates": manifest.data["reused_candidates"],
                    "key_outputs": {name: str(root / name) for name in
                                    ("repair_history.json", "verification_summary.json", "input.patch", "agent_ledger.json")}})
        print(f"[RESULT] {result.verdict}; {history['stop_reason']}\n[OUTPUT] {root}")
        return verification["exit_code"]

    def guarded(state):
        try:
            return operate(state)
        except (Exception, KeyboardInterrupt) as exc:
            path = root / "repair_history.json"
            if path.is_file():
                from mimo_harness.manifests import read_object
                history = read_object(path)
                code = str(exc).split(":", 1)[0]
                history["stop_reason"] = ("CANCELLED" if isinstance(exc, KeyboardInterrupt) else
                                          code if code == "AGENT_BUDGET_EXHAUSTED" else "EXECUTION_ERROR")
                atomic_json(path, history)
            raise

    return _execute_attempt(root, project, guarded)
