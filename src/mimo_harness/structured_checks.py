"""Execute structured non-Python checks under the existing isolation policy."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from mimo_harness.execution import CheckCancelled, ExecutionContext
from mimo_harness.manifests import atomic_json
from mimo_harness.structured_cases import load_node, parse_junit, read_bytes, validate_record, verify_junit


def acceptance_hashes(root, patterns):
    hashes = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if set(path.relative_to(root).parts) & {".git", ".mimo", ".venv", "venv", "env", "node_modules", "outputs", "build", "dist", "__pycache__"}:
                continue
            if path.is_symlink():
                raise ValueError("ACCEPTANCE_SYMLINK")
            path.resolve().relative_to(root.resolve())
            if path.is_file():
                hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def run_structured_check(root, logs, spec, enabled, context=None):
    name, adapter = spec["id"], spec["adapter"]
    context = context or ExecutionContext(root, logs)
    logs.mkdir(parents=True, exist_ok=True)
    evidence, junit = (logs / f"{name}_tests.json").resolve(), (logs / f"{name}_junit.xml").resolve()
    cwd = (root / spec.get("cwd", ".")).resolve()
    cwd.relative_to(root.resolve())
    if adapter == "node":
        argv = [context.python, context.path(Path(__file__).with_name("node_worker.py")), context.path(evidence),
                json.dumps(spec["args"]), context.path(root), context.path(junit)]
    else:
        argv = [{"{python}": context.python, "{junit}": context.path(junit)}.get(part, part) for part in spec["argv"]]
    row = {"name": name, "adapter": adapter, "category": "test", "argv": argv, "command": " ".join(argv),
           "cwd": str(cwd), "timeout_seconds": spec.get("timeout_seconds", 180), "returncode": None,
           "status": "skipped", "time_seconds": 0, "note": "Disabled by execution policy.",
           "stdout_log": str(logs / f"{name}_stdout.log"), "stderr_log": str(logs / f"{name}_stderr.log"),
           "test_cases": [], "collected": [], "collection_errors": [], "collection_skips": [], "reason_codes": [],
           "target_tests": [f"{name}::{t}" for t in spec.get("target_tests", [])], "execution_environment": context.metadata()}
    start = time.monotonic()
    try:
        if enabled:
            evidence.unlink(missing_ok=True)
            junit.unlink(missing_ok=True)
            patterns = ["**/*.js", "**/*.mjs", "**/*.cjs"] if adapter == "node" else spec["acceptance_files"]
            original = acceptance_hashes(root, patterns)
            declared = acceptance_hashes(root, spec.get("acceptance_files", []))
            row["protected_files"] = dict(declared)
            if adapter == "junit" and not original:
                raise ValueError("JUNIT_ACCEPTANCE_FILES_MISSING")
            proc = context.run(argv, cwd, row["timeout_seconds"], Path(row["stdout_log"]), Path(row["stderr_log"]))
            row.update(returncode=proc.returncode, status="ok" if proc.returncode == 0 else "failed", note="Executed structured case check.")
            if proc.status != "completed":
                raise ValueError({"cancelled": "CHECK_CANCELLED", "timeout": "CHECK_TIMEOUT"}.get(proc.status, "CHECK_EXECUTION_ERROR"))
            if adapter == "node":
                data = load_node(evidence)
            else:
                data = parse_junit(read_bytes(junit))
                data["exit_code"] = proc.returncode
                data["cases"] = dict(sorted(data["cases"].items()))
                atomic_json(evidence, data)
            validate_record(data)
            verify_junit(data, read_bytes(junit))
            for case in data["cases"].values():
                file = case["file"]
                if file not in original:
                    raise ValueError("TEST_FILE_NOT_IN_ACCEPTANCE_SNAPSHOT")
                row["protected_files"][file] = original[file]
            row.update(test_evidence=str(evidence), junit_xml=str(junit), runner_version=data.get("runner_version"),
                       test_cases=[dict(case, nodeid=f"{name}::{key}") for key, case in data["cases"].items()],
                       collected=[f"{name}::{key}" for key in data["collected"]], collection_errors=data.get("collection_errors", []),
                       collection_skips=[dict(item, nodeid=f"{name}::{item['nodeid']}") for item in data.get("collection_skips", [])])
            if data.get("runner_error"):
                raise ValueError(data["runner_error"])
            if data.get("collection_errors"):
                raise ValueError(adapter.upper() + "_COLLECTION_ERROR")
            if any(c["status"] == "error" for c in data["cases"].values()):
                raise ValueError(adapter.upper() + "_CASE_ERROR")
            if data.get("exit_code") != proc.returncode or proc.returncode not in {0, 1}:
                raise ValueError(adapter.upper() + "_INCONSISTENT_RESULT")
            failed = any(c["status"] == "failed" for c in data["cases"].values())
            if (proc.returncode == 0 and failed) or (proc.returncode == 1 and not failed):
                raise ValueError(adapter.upper() + "_INCONSISTENT_RESULT")
            if not any(c["status"] in {"passed", "failed"} for c in data["cases"].values()):
                row.update(status="insufficient", reason_codes=["NO_BEHAVIOR_TEST_EXECUTED"])
    except KeyboardInterrupt:
        row.update(status="cancelled", reason_codes=["CHECK_CANCELLED"])
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        reason = str(exc)
        row.update(status="cancelled" if reason == "CHECK_CANCELLED" else "error", reason_codes=[reason], note=reason)
    finally:
        for field in ("stdout_log", "stderr_log"):
            path = Path(row[field])
            if path.is_symlink():
                row.update(status="error", reason_codes=["UNSAFE_CHECK_LOG_PATH"])
            elif not path.exists():
                with path.open("x", encoding="utf-8"):
                    pass
        row["time_seconds"] = round(time.monotonic() - start, 4)
        row["execution_environment"] = context.metadata()
        atomic_json(logs / f"{name}_check.json", row)
    if row["status"] == "cancelled":
        raise CheckCancelled(row)
    return row
