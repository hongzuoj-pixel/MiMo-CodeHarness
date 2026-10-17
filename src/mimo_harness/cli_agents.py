"""Versioned external CLI proposals, isolated processes and durable invocation limits."""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from mimo_harness.execution import ExecutionContext, run_process, validate_execution
from mimo_harness.manifests import RunLock, atomic_json, digest, read_object, safe_file
from run_mimo_codeharness_v02 import ModelRunRecord, copy_worktree


def _number(value, name, maximum, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, int if integer else (int, float))
            or not 0 < value <= maximum or not math.isfinite(value)):
        raise ValueError(f"Agent {name} must be positive and at most {maximum}.")
    return value


def load_agent_config(path: Path) -> dict:
    cfg = read_object(path)
    allowed = {"schema_version", "argv", "cwd", "timeout_seconds", "max_output_bytes", "execution", "budget"}
    if type(cfg.get("schema_version")) is not int or cfg["schema_version"] != 1 or set(cfg) - allowed:
        raise ValueError("Agent configuration requires schema_version 1 and known fields only.")
    argv = cfg.get("argv")
    if not isinstance(argv, list) or not 1 <= len(argv) <= 128 or any(not isinstance(a, str) or not a or len(a) > 65536 or "\0" in a for a in argv):
        raise ValueError("Agent argv must be a non-empty array of literal arguments.")
    if "{request}" not in argv:
        raise ValueError("Agent argv must include a whole {request} argument.")
    if argv[0] in {"{request}", "{workspace}"}:
        raise ValueError("Agent argv[0] must be an executable or {python}.")
    for arg in argv:
        if arg.startswith("{") and arg.endswith("}") and arg[1:-1].isidentifier() and arg not in {"{python}", "{request}", "{workspace}"}:
            raise ValueError(f"Unknown Agent placeholder: {arg}")
    cwd = cfg.get("cwd", ".")
    from pathlib import PureWindowsPath
    if not isinstance(cwd, str) or not cwd or Path(cwd).is_absolute() or PureWindowsPath(cwd).drive or ".." in cwd.replace("\\", "/").split("/"):
        raise ValueError("Agent cwd must be a relative directory inside its workspace.")
    execution = validate_execution(cfg.get("execution"))
    if execution["prepare"] or execution["mode"] == "venv":
        raise ValueError("Agents require local mode or a reviewed Docker image, without prepare steps.")
    budget = cfg.get("budget", {"max_invocations": 3})
    if not isinstance(budget, dict) or set(budget) - {"max_invocations", "max_reserved_seconds"}:
        raise ValueError("Agent budget supports max_invocations and max_reserved_seconds only.")
    budget = {"max_invocations": 3, **budget}
    _number(budget["max_invocations"], "max_invocations", 1000, integer=True)
    if "max_reserved_seconds" in budget:
        _number(budget["max_reserved_seconds"], "max_reserved_seconds", 3600000)
    return {**cfg, "cwd": cwd, "execution": execution, "budget": budget,
            "timeout_seconds": _number(cfg.get("timeout_seconds", 180), "timeout_seconds", 3600),
            "max_output_bytes": _number(cfg.get("max_output_bytes", 262144), "max_output_bytes", 1048576, integer=True)}


def preflight_agent(config, project):
    cwd = (project / config["cwd"]).resolve()
    cwd.relative_to(project.resolve())
    if not cwd.is_dir():
        raise ValueError("Agent cwd does not exist inside the project.")
    missing = [key for key in config["execution"]["env_allowlist"] if not os.environ.get(key)]
    if missing:
        raise ValueError("Missing Agent environment variables: " + ", ".join(missing))
    if config["execution"]["mode"] == "local":
        executable = sys.executable if config["argv"][0] == "{python}" else config["argv"][0]
        if not shutil.which(executable, path=os.environ.get("PATH", "")) and not (cwd / executable).is_file():
            raise ValueError("Agent executable is unavailable; install it or select a reviewed Docker image.")


def ledger_document(policy, entries):
    return {"schema_version": 1, "policy": policy, "entries": entries,
            "summary": {"invocations_reserved": len(entries),
                        "seconds_reserved": sum(e["reserved_seconds"] for e in entries),
                        "usage_source": "unavailable"}}


def validate_agent_ledger(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Invalid Agent invocation ledger.")
    policy, entries = value.get("policy"), value.get("entries")
    if not isinstance(policy, dict) or not isinstance(entries, list):
        raise ValueError("Malformed Agent invocation ledger.")
    _number(policy.get("max_invocations"), "max_invocations", 1000, integer=True)
    if set(policy) - {"max_invocations", "max_reserved_seconds"}:
        raise ValueError("Unknown Agent budget policy.")
    if "max_reserved_seconds" in policy:
        _number(policy["max_reserved_seconds"], "max_reserved_seconds", 3600000)
    ids = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"id", "candidate_id", "reserved_seconds", "status"}
                or not isinstance(entry.get("id"), str) or entry["id"] in ids
                or not isinstance(entry.get("candidate_id"), str)
                or entry.get("status") not in {"RESERVED", "completed", "failed", "cancelled"}):
            raise ValueError("Malformed Agent reservation.")
        _number(entry.get("reserved_seconds"), "reserved_seconds", 3600)
        ids.add(entry["id"])
    expected = ledger_document(policy, entries)
    if value != expected or len(entries) > policy["max_invocations"] or (
            expected["summary"]["seconds_reserved"] > policy.get("max_reserved_seconds", math.inf)):
        raise ValueError("Agent accounting totals or limits disagree.")
    return value


class AgentBudget:
    """Reserve before launch; all recovery branches share a locked coordinator."""
    def __init__(self, manifest, policy):
        self.manifest, self.policy = manifest, policy
        self.control = manifest.root.parent / ".agent-budgets" / manifest.data["provider_budget_id"]
        if self.control.is_symlink() or self.control.parent.is_symlink():
            raise ValueError("Unsafe Agent budget coordinator.")
        previous = getattr(manifest.previous, "agent_ledger", None)
        self.entries = previous["entries"] if previous else []
        self.required = manifest.previous is not None
        with RunLock(self.control):
            self._refresh()
            self._save()

    def _refresh(self):
        path = self.control / "ledger.json"
        if path.exists():
            saved = read_object(safe_file(self.control, "ledger.json"))
            payload = {k: v for k, v in saved.items() if k != "sha256"}
            if saved.get("sha256") != digest(payload) or payload.get("fingerprint") != self.manifest.data["fingerprint"]:
                raise ValueError("Damaged Agent budget coordinator; start a new run.")
            ledger = validate_agent_ledger(payload.get("ledger"))
            entries = ledger["entries"]
            if ledger["policy"] != self.policy or len(entries) < len(self.entries):
                raise ValueError("Agent budget history was lost or changed.")
            for old, new in zip(self.entries, entries):
                if any(old.get(k) != new.get(k) for k in ("id", "candidate_id", "reserved_seconds")):
                    raise ValueError("Agent budget reservation changed.")
            self.entries = entries
        elif self.required or self.entries:
            raise ValueError("Missing Agent budget coordinator; restore it before recovery.")

    def _save(self):
        ledger = ledger_document(self.policy, self.entries)
        validate_agent_ledger(ledger)
        payload = {"fingerprint": self.manifest.data["fingerprint"], "ledger": ledger}
        atomic_json(self.control / "ledger.json", {**payload, "sha256": digest(payload)})
        self.manifest.save_agent_ledger(ledger)

    def reserve(self, candidate_id, timeout):
        with RunLock(self.control):
            self._refresh()
            if len(self.entries) >= self.policy["max_invocations"] or sum(e["reserved_seconds"] for e in self.entries) + timeout > self.policy.get("max_reserved_seconds", math.inf):
                raise ValueError("AGENT_BUDGET_EXHAUSTED: invocation/time allowance reached; no Agent launched.")
            reservation = {"id": uuid.uuid4().hex, "candidate_id": candidate_id,
                           "reserved_seconds": timeout, "status": "RESERVED"}
            self.entries.append(reservation)
            self._save()
            return reservation["id"]

    def finish(self, reservation_id, status):
        with RunLock(self.control):
            self._refresh()
            next(e for e in self.entries if e["id"] == reservation_id)["status"] = status
            self._save()


def propose(config, snapshot: Path, root: Path, request: dict, manifest, budget) -> ModelRunRecord:
    candidate_id = f"repair-{request['round']:03d}"
    signature = digest(request)
    reused = manifest.reuse(candidate_id, signature)
    if reused:
        return ModelRunRecord(**reused)
    reservation = budget.reserve(candidate_id, config["timeout_seconds"])
    private = root / "agent_runs" / candidate_id
    workspace = copy_worktree(snapshot, private / "workspace")
    logs = private / "logs"
    context = ExecutionContext(workspace, logs, config["execution"])
    secrets = [os.environ[k] for k in config["execution"]["env_allowlist"] if os.environ.get(k)]
    def redact(text):
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text
    started, status, patch, error = time.monotonic(), "failed", "", "AGENT_EXECUTION_ERROR"
    try:
        # Create a Git boundary before untrusted Agent edits. Never run host Git
        # commands against this workspace after launching the Agent.
        boundary = run_process(["git", "init", "--quiet"], workspace, env=context.env, timeout=30)
        if boundary.status != "completed" or boundary.returncode:
            raise ValueError("AGENT_WORKSPACE_ERROR")
        context.setup()
        request_path = logs / "request.json"
        atomic_json(request_path, request)
        replacements = {"{python}": context.python, "{request}": context.path(request_path),
                        "{workspace}": context.path(workspace)}
        argv = [replacements.get(a, a) for a in config["argv"]]
        cwd = (workspace / config["cwd"]).resolve()
        cwd.relative_to(workspace)
        outcome = context.run(argv, cwd, config["timeout_seconds"], logs / "stdout.log", logs / "stderr.log",
                              max_output_bytes=config["max_output_bytes"])
        if outcome.status == "cancelled":
            raise KeyboardInterrupt
        if outcome.status != "completed" or outcome.returncode != 0:
            raise ValueError("AGENT_" + (outcome.status.upper() if outcome.status != "completed" else "EXIT_ERROR"))
        try:
            response = json.loads(outcome.stdout)
        except (ValueError, RecursionError) as exc:
            raise ValueError("AGENT_INVALID_JSON") from exc
        if (not isinstance(response, dict) or type(response.get("schema_version")) is not int or response["schema_version"] != 1
                or response.get("status") != "completed" or set(response) - {"schema_version", "status", "patch"}
                or not isinstance(response.get("patch"), str) or not response["patch"].strip()):
            raise ValueError("AGENT_INCOMPLETE_RESPONSE")
        patch = response["patch"].replace("\r\n", "\n").replace("\r", "\n")
        if not patch.lstrip().startswith(("diff --git ", "--- ")) or "```" in patch:
            raise ValueError("AGENT_INVALID_PATCH")
        if any(secret in patch for secret in secrets):
            raise ValueError("AGENT_CREDENTIAL_IN_PATCH")
        # Only the declared proposal is accepted. All workspace edits are discarded.
        status, error = "completed", ""
    except KeyboardInterrupt:
        budget.finish(reservation, "cancelled")
        raise
    except Exception as exc:
        # Diagnostics use fixed codes; process output and credential-bearing paths
        # are private. Never serialize arbitrary adapter exception text.
        error = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("AGENT_") else "AGENT_EXECUTION_ERROR"
    finally:
        atomic_json(logs / "execution_environment.json", context.metadata())
        for path in logs.glob("*.log"):
            if not path.is_symlink():
                path.write_text(redact(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    record = ModelRunRecord("REPAIR", "cli-agent", "external-cli", status, False,
                            time.monotonic() - started, 0, 0, 0,
                            f"```diff\n{patch}\n```" if status == "completed" else "", error, {},
                            candidate_id=candidate_id, provider_type="cli_agent", usage_source="unavailable",
                            finish_reason="completed" if status == "completed" else "error", error_code=error,
                            attempts=[{"reservation_id": reservation, "status": status}])
    budget.finish(reservation, status)
    manifest.record(candidate_id, signature, asdict(record))
    return record
