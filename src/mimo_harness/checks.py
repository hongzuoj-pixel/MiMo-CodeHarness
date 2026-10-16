"""Versioned project checks and per-test executable evidence."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def validate_checks(checks: Any) -> list[dict] | None:
    if checks is None:
        return None  # legacy automatic detection, recorded explicitly
    if not isinstance(checks, list):
        raise ValueError("checks must be an array; omit it for legacy detection.")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Each check must be an object.")
        extra = set(check) - {"id", "adapter", "argv", "args", "cwd", "timeout_seconds", "category", "target_tests"}
        if extra:
            raise ValueError(f"Unknown check fields: {sorted(extra)}")
        name = check.get("id", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in seen:
            raise ValueError("Check IDs must be unique letters, numbers, underscores or hyphens.")
        seen.add(name)
        adapter = check.get("adapter")
        if adapter not in {"pytest", "command"}:
            raise ValueError(f"Check {name}: adapter must be pytest or command.")
        key = "args" if adapter == "pytest" else "argv"
        args = check.get(key, [] if key == "args" else None)
        if not isinstance(args, list) or any(not isinstance(a, str) or not a for a in args):
            raise ValueError(f"Check {name}: {key} must be an array of non-empty strings.")
        if key == "argv" and not args:
            raise ValueError(f"Check {name}: argv cannot be empty.")
        if (adapter == "pytest" and "argv" in check) or (adapter == "command" and "args" in check):
            raise ValueError(f"Check {name}: use args for pytest and argv for command.")
        cwd = check.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
            raise ValueError(f"Check {name}: cwd must stay inside the isolated project.")
        timeout = check.get("timeout_seconds", 180)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
            raise ValueError(f"Check {name}: timeout_seconds must be > 0 and <= 86400.")
        if check.get("category", "test") not in {"test", "build", "smoke"}:
            raise ValueError(f"Check {name}: unsupported category.")
        if adapter == "pytest" and check.get("category", "test") != "test":
            raise ValueError(f"Check {name}: pytest category must be test.")
        targets = check.get("target_tests", [])
        if not isinstance(targets, list) or any(not isinstance(t, str) or not t for t in targets):
            raise ValueError(f"Check {name}: target_tests must be an array of node IDs.")
        if targets and adapter != "pytest":
            raise ValueError(f"Check {name}: target_tests requires the pytest adapter.")
    return checks


def protected_files(root: Path) -> dict[str, str]:
    """Fingerprint baseline acceptance inputs before either test command executes."""
    hashes = {}
    excluded = {".git", ".mimo", ".venv", "venv", "env", "__pycache__", "node_modules", "outputs", "build", "dist"}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if set(relative.parts) & excluded or not path.is_file():
            continue
        if (path.name in {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
                or path.name.startswith("test_") or path.name.endswith("_test.py")
                or "tests" in relative.parts):
            if path.is_symlink():
                raise ValueError(f"Acceptance input is a symlink: {relative}")
            hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def run_check(root: Path, logs: Path, spec: dict, enabled: bool) -> dict:
    name = spec["id"]
    adapter = spec["adapter"]
    logs.mkdir(parents=True, exist_ok=True)
    evidence_path = (logs / f"{name}_tests.json").resolve()
    junit_path = (logs / f"{name}_junit.xml").resolve()
    cwd = (root / spec.get("cwd", ".")).resolve()
    cwd.relative_to(root.resolve())
    args = spec.get("args", ["-q"])
    case_adapter = adapter in {"pytest", "unittest"}
    argv = (
        [sys.executable, str(Path(__file__).with_name("pytest_worker.py")), str(evidence_path),
         json.dumps([*args, f"--junitxml={junit_path}"])]
        if case_adapter else
        [sys.executable if part == "{python}" else part for part in spec["argv"]]
    )
    if adapter == "unittest":
        argv.extend(["unittest", str(junit_path)])
    # Include custom-named collected Python tests in the pre-execution fingerprint.
    python_files = {
        path.resolve(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    } if case_adapter and enabled else {}
    row = {
        "name": name, "adapter": adapter, "category": spec.get("category", "test"),
        "argv": argv, "command": " ".join(argv), "cwd": str(cwd),
        "timeout_seconds": spec.get("timeout_seconds", 180), "returncode": None,
        "status": "skipped", "time_seconds": 0.0, "note": "Disabled by execution policy.",
        "stdout_log": str(logs / f"{name}_stdout.log"),
        "stderr_log": str(logs / f"{name}_stderr.log"),
        "test_cases": [], "collected": [], "collection_errors": [],
        "collection_skips": [],
        "reason_codes": [], "target_tests": [f"{name}::{t}" for t in spec.get("target_tests", [])],
    }
    stdout = stderr = ""
    start = time.perf_counter()
    if enabled:
        evidence_path.unlink(missing_ok=True)
        junit_path.unlink(missing_ok=True)
        try:
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=row["timeout_seconds"])
            stdout, stderr = proc.stdout, proc.stderr
            row.update(returncode=proc.returncode, status="ok" if proc.returncode == 0 else "failed",
                       note="Executed configured check with shell=False.")
            if case_adapter:
                if not evidence_path.is_file():
                    row.update(status="error", reason_codes=["PYTEST_EVIDENCE_MISSING"])
                else:
                    data = json.loads(evidence_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("cases"), dict):
                        raise ValueError("Invalid pytest evidence schema")
                    row["test_cases"] = [dict(case, nodeid=f"{name}::{nodeid}") for nodeid, case in data["cases"].items()]
                    row["collected"] = [f"{name}::{nodeid}" for nodeid in data.get("collected", [])]
                    row["collection_errors"] = data.get("collection_errors", [])
                    row["collection_skips"] = [dict(item, nodeid=f"{name}::{item['nodeid']}")
                                                for item in data.get("collection_skips", [])]
                    row["pytest_version"] = data.get("pytest_version")
                    row["protected_files"] = {}
                    for case in data["cases"].values():
                        source = Path(case.get("file", "")).resolve()
                        if source in python_files:
                            row["protected_files"][source.relative_to(root.resolve()).as_posix()] = python_files[source]
                    row["test_evidence"] = str(evidence_path)
                    row["junit_xml"] = str(junit_path)
                    if data.get("runner_error"):
                        row.update(status="error", reason_codes=[data["runner_error"]])
                    elif data.get("collection_errors"):
                        row.update(status="error", reason_codes=["PYTEST_COLLECTION_ERROR"])
                    elif len(row["collected"]) != len(set(row["collected"])):
                        row.update(status="error", reason_codes=["DUPLICATE_TEST_IDS"])
                    elif proc.returncode == 5:
                        row.update(status="insufficient", reason_codes=["NO_TESTS_COLLECTED"])
                    elif proc.returncode not in (0, 1):
                        row.update(status="error", reason_codes=["PYTEST_EXECUTION_ERROR"])
                    elif any(c["status"] == "error" for c in row["test_cases"]):
                        row.update(status="error", reason_codes=["PYTEST_CASE_ERROR"])
                    elif not row["collected"] or not any(c["status"] in {"passed", "failed"} for c in row["test_cases"]):
                        row.update(status="insufficient", reason_codes=["NO_BEHAVIOR_TEST_EXECUTED"])
                    elif proc.returncode == 1 and not any(c["status"] == "failed" for c in row["test_cases"]):
                        row.update(status="error", reason_codes=["PYTEST_INCONSISTENT_RESULT"])
                    elif data.get("exit_code") != proc.returncode:
                        row.update(status="error", reason_codes=["PYTEST_INCONSISTENT_RESULT"])
                    elif proc.returncode == 0 and any(c["status"] == "failed" for c in row["test_cases"]):
                        row.update(status="error", reason_codes=["PYTEST_INCONSISTENT_RESULT"])
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            stderr = "Check exceeded configured timeout."
            row.update(status="error", reason_codes=["CHECK_TIMEOUT"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            stderr = f"{type(exc).__name__}: {exc}"
            row.update(status="error", reason_codes=["CHECK_EXECUTION_ERROR"])
    Path(row["stdout_log"]).write_text(stdout, encoding="utf-8")
    Path(row["stderr_log"]).write_text(stderr, encoding="utf-8")
    row["time_seconds"] = round(time.perf_counter() - start, 4)
    return row
