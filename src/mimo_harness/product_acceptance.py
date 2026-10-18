"""Explicit live-trial inputs and source-bound product acceptance records.

Human independence, provider origin and account spending caps are attestations;
the harness checks artifacts and never invents those external observations.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from mimo_harness.cli_agents import load_agent_config, preflight_agent
from mimo_harness.bundles import BundleReader
from mimo_harness.evidence import inspect_evidence
from mimo_harness.manifests import atomic_json, digest, engine_identity, file_digest, read_object, safe_file

STEPS = ("install", "configure", "run", "diagnose_failure", "recover", "export_and_gate")
TRIAL_CASES = ("boltons-write", "ms-invalid-number")
REQUIRED_JOBS = {"docker-acceptance", "real-python-case", "evidence-gate-contract",
    "agent-and-real-cases (ubuntu-latest)", "agent-and-real-cases (windows-latest)",
    "cli-install (ubuntu-latest, 3.12)", "cli-install (windows-latest, 3.12)",
    "unit-and-smoke (3.10, 8.4.2)", "unit-and-smoke (3.12, 8.4.2)",
    "unit-and-smoke (3.13, 8.4.2)", "unit-and-smoke (3.12, 9.0.2)",
    "structured-cases (ubuntu-latest, 22)", "structured-cases (ubuntu-latest, 24)",
    "structured-cases (windows-latest, 24)"}
RECORD_ERRORS = (ValueError, OSError, TypeError, KeyError, AttributeError, EOFError, zipfile.BadZipFile, zlib.error)


def command_trial_init(args):
    from mimo_harness.cli import _write_json_new
    agent = Path(args.agent_config).expanduser().resolve()
    config = load_agent_config(agent)
    if config.get("adapter") != "codex":
        raise ValueError("The initial live-trial workflow requires the pinned Codex adapter.")
    plan = {"schema_version": 1, "mode": "live_model_trial", "engine_sha256": engine_identity(),
            "agent_config": str(agent), "agent_config_sha256": file_digest(agent), "case_ids": list(TRIAL_CASES),
            "spending_allowance": None, "currency": "USD", "spending_cap_location": "", "operator": "",
            "note": "Fill the allowance/operator and set an actual provider/account spending cap. Two cases, one reserved CLI invocation per case. Opaque CLI requests/cost are not capped by the harness."}
    _write_json_new(Path(args.output).expanduser().resolve(), plan, False)
    print(f"[OK] Live trial plan: {args.output}\n[NEXT] Fill its allowance/operator/cap location, set credentials, then trial-run. No model was called.")
    return 0


def validate_trial_plan(path):
    plan = read_object(path)
    amount = plan.get("spending_allowance")
    if (type(plan.get("schema_version")) is not int or plan["schema_version"] != 1 or plan.get("mode") != "live_model_trial"
            or plan.get("case_ids") != list(TRIAL_CASES) or plan.get("engine_sha256") != engine_identity()):
        raise ValueError("Live trial plan/source changed; generate and review a new plan.")
    if (isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0
            or plan.get("currency") != "USD" or not isinstance(plan.get("operator"), str) or not plan["operator"].strip()
            or not isinstance(plan.get("spending_cap_location"), str) or not plan["spending_cap_location"].strip()):
        raise ValueError("Live trial needs an explicit positive USD allowance, operator and actual account/provider cap location. No Agent was launched.")
    if not isinstance(plan.get("agent_config"), str):
        raise ValueError("Live trial Agent configuration path missing.")
    agent = Path(plan["agent_config"])
    if file_digest(agent) != plan.get("agent_config_sha256"):
        raise ValueError("Live trial Agent configuration changed.")
    config = load_agent_config(agent)
    if config.get("adapter") != "codex" or config.get("cwd") != ".":
        raise ValueError("Live trial requires the pinned Codex adapter at the repository root.")
    if config.get("base_url") and urlsplit(config["base_url"]).hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("A loopback protocol server cannot be recorded as a live-model trial.")
    preflight_agent(config, Path.cwd())
    return plan, config


def command_trial_run(args):
    from mimo_harness.real_cases import command_case_init, validate_case
    from mimo_harness.repair import command_repair
    from mimo_harness.cli import command_export, command_gate
    plan, config = validate_trial_plan(Path(args.plan).expanduser().resolve())
    root = Path(args.output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "mode": "live_model_trial", "status": "RUNNING", "plan": plan,
              "origin_evidence": "operator_declared_remote_provider; CLI completion and independent tests are recorded",
              "cost_source": "unavailable; allowance/account cap is operator-attested", "cases": []}
    record_path = root / "trial.json"
    atomic_json(record_path, report)
    try:
        for case_id in TRIAL_CASES:
            case_root = root / case_id
            command_case_init(SimpleNamespace(case=case_id, output=str(case_root)))
            case = validate_case(case_root)
            profile = case_root / "trial-agent.json"
            atomic_json(profile, {**config, "budget": {"max_invocations": 1, "max_reserved_seconds": config["timeout_seconds"]}})
            code = command_repair(SimpleNamespace(config=str(case_root / "mimo-harness.json"), project=None,
                agent_config=str(profile), task_file=str(case_root / "task.txt"), base=case["acceptance_base"], max_rounds=1, name="live"))
            run = case_root / "runs/live"
            bundle = case_root / "live-evidence.zip"
            command_export(SimpleNamespace(run=str(run), output=str(bundle)))
            gate = command_gate(SimpleNamespace(run=None, bundle=str(bundle), expect_base=case["acceptance_base"], expect_patch=None, json=True))
            history = read_object(run / "repair_history.json")
            usage_path = run / "agent_runs/repair-001/logs/codex_metadata.json"
            usage = read_object(usage_path) if usage_path.is_file() else {"usage_source": "unavailable", "usage": None}
            report["cases"].append({"case_id": case_id, "source": case, "exit_code": code, "gate_exit": gate,
                "bundle": bundle.relative_to(root).as_posix(), "bundle_sha256": file_digest(bundle),
                "agent": history.get("agent"), "ledger": read_object(run / "agent_ledger.json")["summary"],
                "limited_profile_sha256": file_digest(profile),
                "usage_source": usage["usage_source"], "usage": usage["usage"]})
            atomic_json(record_path, report)
        report["status"] = "PASS" if all(r["exit_code"] == r["gate_exit"] == 0 for r in report["cases"]) else "FAIL"
    except KeyboardInterrupt:
        report["status"] = "CANCELLED"
        raise
    except (ValueError, OSError, KeyError, RuntimeError):
        report["status"] = "ERROR"
        raise
    finally:
        atomic_json(record_path, report)
    print(f"[TRIAL] {report['status']}\n[RECORD] {record_path}")
    return 0 if report["status"] == "PASS" else 2


def command_walkthrough_init(args):
    root = Path(args.output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    atomic_json(root / "feedback.json", {"schema_version": 1, "mode": "independent_user", "tester_id": "",
        "independence_confirmed": False, "observed_at": "", "environment": "", "steps": {step: False for step in STEPS},
        "issues": [], "notes": "", "evidence_bundle": "", "engine_sha256": engine_identity()})
    guide = """# 独立新用户试用

请由未参与实现的使用者独立操作，并如实填写 feedback.json。模板不计为已完成试用。

1. 安装 Python 3.10+、Git，在仓库根目录执行 `python -m pip install ".[test]"` 和 `mimo --version`。
2. 执行 `mimo case-init --case boltons-write --output .mimo/user-case`，查看生成的配置与目标测试。
3. 执行 `mimo --config .mimo/user-case/mimo-harness.json doctor`。
4. 执行 `mimo case-check --directory .mimo/user-case --name first`。阅读参考 PASS、故意回归 FAIL 和恢复重跑的证据；整个对照验收应成功。
5. 再用相同名称运行一次，确认拒绝覆盖；记录提示是否足以解决问题。换一个新名称可重新验收。
6. 将 `results/first/reference.zip` 复制到其他目录，执行 `mimo gate --bundle ZIP路径 --json`；解压后打开 report.html。
7. 填写匿名 tester_id、观察时间、环境、各步骤是否完成、遇到的问题。只在实际独立试用后设置 independence_confirmed=true。issues 中每个问题记录 description 和 resolved。
8. evidence_bundle 填写你自己运行产生的参考 ZIP 路径（相对 feedback.json 所在目录，或绝对路径）。不同试用者需要各自的运行证据。

记录卡住的步骤同样有价值。未解决的问题保持 resolved=false。请勿放入密钥或个人敏感信息。
"""
    (root / "GUIDE_CN.md").write_text(guide, encoding="utf-8", newline="\n")
    print(f"[OK] Walkthrough and blank feedback: {root}\n[NOTE] No participant or successful trial has been recorded.")
    return 0


def bundle_proof(path, live_row=None):
    with BundleReader(path) as reader:
        decision = inspect_evidence(reader)["gate"]
        if decision["exit_code"] != 0:
            raise ValueError("Acceptance bundle did not PASS.")
        if reader.manifest["inputs"]["engine_sha256"] != engine_identity():
            raise ValueError("Acceptance bundle used a different harness engine.")
        if live_row is not None:
            from mimo_harness.codex_adapter import CODEX_VERSION
            from mimo_harness.real_cases import CASES
            manifest = reader.manifest
            if manifest["options"].get("workflow") != "repair":
                raise ValueError("Reference controls are not live-model repair evidence.")
            history = json.loads(reader.read("evidence/repair_history.json"))
            ledger = json.loads(reader.read("evidence/agent_ledger.json"))
            agent = history.get("agent", {})
            source = live_row["source"]; spec = CASES[live_row["case_id"]]
            inventory = json.loads(reader.read("provenance/source_manifest.json"))
            content_sha = digest({name: {key: value for key, value in row.items() if key != "mode"}
                                  for name, row in inventory.items()})
            checks = {"adapter": agent.get("adapter") == "codex", "cli_version": agent.get("cli_version") == CODEX_VERSION,
                      "agent_identity": agent == live_row.get("agent"), "ledger": ledger["summary"] == live_row.get("ledger"),
                      "invocations": ledger["summary"]["invocations_reserved"] == 1,
                      "upstream_base": source["upstream_base"] == spec["upstream_base"],
                      "upstream_fix": source["upstream_fix"] == spec["upstream_fix"],
                      "acceptance_base": manifest["inputs"]["resolved_base"] == source["acceptance_base"],
                      "source_content": content_sha == source.get("source_content_sha256"),
                      "agent_profile": manifest["inputs"]["agent"]["config_sha256"] == live_row.get("limited_profile_sha256")}
            if not all(checks.values()):
                raise ValueError("Live trial repair evidence mismatch: " + ", ".join(key for key, ok in checks.items() if not ok))
        return decision["run_id"]


def command_readiness(args):
    repository = Path(args.repository).expanduser().resolve()
    pending, errors = [], []
    accepted = {"trial": False, "independent_users": 0, "license": False, "ci": False}
    if args.trial:
        try:
            path = Path(args.trial).expanduser().resolve(); trial = read_object(path)
            if (type(trial.get("schema_version")) is not int or trial["schema_version"] != 1
                    or trial.get("mode") != "live_model_trial" or trial.get("status") != "PASS"
                    or trial.get("plan", {}).get("engine_sha256") != engine_identity()
                    or [r["case_id"] for r in trial.get("cases", [])] != list(TRIAL_CASES)):
                raise ValueError("Live model acceptance is incomplete or uses a different engine.")
            for row in trial["cases"]:
                bundle = safe_file(path.parent, row["bundle"])
                if file_digest(bundle) != row["bundle_sha256"]: raise ValueError("Trial bundle changed.")
                bundle_proof(bundle, row)
            accepted["trial"] = True
        except RECORD_ERRORS as exc: errors.append("trial: " + str(exc))
    else: pending.append("LIVE_MODEL_TRIAL")
    users, runs = set(), set()
    for value in args.feedback or []:
        try:
            path = Path(value).expanduser().resolve(); feedback = read_object(path)
            user = feedback.get("tester_id")
            user = user.strip().casefold() if isinstance(user, str) else user
            if (type(feedback.get("schema_version")) is not int or feedback["schema_version"] != 1
                    or feedback.get("mode") != "independent_user" or feedback.get("independence_confirmed") is not True
                    or not isinstance(user, str) or not user.strip() or user in users
                    or not feedback.get("observed_at") or not feedback.get("environment")
                    or feedback.get("engine_sha256") != engine_identity()
                    or feedback.get("steps") != {step: True for step in STEPS}
                    or any(type(v) is not bool for v in feedback["steps"].values())
                    or not isinstance(feedback.get("issues"), list)
                    or any(not isinstance(i, dict) or i.get("resolved") is not True or not i.get("description") for i in feedback["issues"])):
                raise ValueError("Feedback incomplete, duplicate, stale, or has unresolved issues.")
            bundle = Path(feedback["evidence_bundle"])
            if not bundle.is_absolute(): bundle = path.parent / bundle
            run = bundle_proof(bundle)
            if run in runs: raise ValueError("Independent walkthroughs reused the same run evidence.")
            users.add(user); runs.add(run)
        except RECORD_ERRORS as exc: errors.append("feedback: " + str(exc))
    accepted["independent_users"] = len(users)
    if len(users) < 3: pending.append("THREE_INDEPENDENT_USER_WALKTHROUGHS")
    license_path = repository / "LICENSE"
    if args.license_id and license_path.is_file() and license_path.stat().st_size:
        accepted["license"] = {"maintainer_selected_id": args.license_id, "sha256": file_digest(license_path)}
    else: pending.append("MAINTAINER_LICENSE_SELECTION_AND_LICENSE_FILE")
    if args.ci_record:
        try:
            ci = read_object(Path(args.ci_record))
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True).strip()
            jobs = ci.get("jobs", [])
            if (type(ci.get("schema_version")) is not int or ci["schema_version"] != 1
                    or ci.get("commit") != commit or ci.get("tree") != tree or not isinstance(jobs, list) or not jobs
                    or any(j.get("conclusion") != "success" for j in jobs)
                    or {j.get("name") for j in jobs} != REQUIRED_JOBS
                    or subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repository)):
                raise ValueError("CI evidence is incomplete, failed or does not bind the clean current commit/tree.")
            accepted["ci"] = True
        except (*RECORD_ERRORS, subprocess.SubprocessError) as exc: errors.append("ci: " + str(exc))
    else: pending.append("FINAL_SOURCE_BOUND_CI_RECORD")
    report = {"schema_version": 1, "status": "ERROR" if errors else "PENDING" if pending else "READY_FOR_MAINTAINER_REVIEW",
              "accepted": accepted, "pending": pending, "errors": errors,
              "attestation_boundary": "Provider origin, account cap, user independence and license choice are declared by their operators; artifact integrity and source binding are checked."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 4 if errors else 3 if pending else 0
