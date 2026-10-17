"""Versioned run identities and conservative, integrity-checked recovery.

Only completed model responses can be reused. Test results and dependency
environments are deliberately never cached across attempts.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from mimo_harness import __version__
from mimo_harness.execution import clean_environment

SCHEMA_VERSION = 1
MANIFEST_NAME = "run_manifest.json"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Missing or damaged recovery evidence: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Recovery evidence must be an object: {path}")
    return value


def safe_file(root: Path, relative: str) -> Path:
    """Evidence references must be relative regular files with no symlink parts."""
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe recovery evidence path: {relative}")
    target = root / path
    if any(part.is_symlink() for part in [target, *target.parents] if part != root.parent):
        raise ValueError(f"Symlink in recovery evidence path: {relative}")
    if not target.is_file():
        raise ValueError(f"Missing recovery evidence: {relative}")
    target.resolve().relative_to(root.resolve())
    return target


def source_inventory(root: Path, excluded=()) -> dict:
    """Hash all copied inputs, including binary/large files and executable modes."""
    root = root.resolve()
    rows = {}

    def walk(directory):
        for path in sorted(directory.iterdir()):
            if path.name in excluded:
                continue
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                try:
                    path.resolve().relative_to(root)
                except (ValueError, RuntimeError) as exc:
                    raise ValueError(f"Input symlink escapes the project: {relative}") from exc
                rows[relative] = {"type": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                rows[relative] = {"type": "directory"}
                walk(path)
            elif stat.S_ISREG(info.st_mode):
                rows[relative] = {"type": "file", "mode": stat.S_IMODE(info.st_mode),
                                  "sha256": file_digest(path), "size": info.st_size}
            else:
                raise ValueError(f"Unsupported input file type: {relative}")

    walk(root)
    return rows


class RunLock:
    """An OS-released lock also permits recovery after an ungraceful process exit."""
    def __init__(self, root: Path):
        self.root = root
        self.handle = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".run.lock"
        if path.is_symlink():
            raise ValueError("Run lock cannot be a symlink.")
        self.handle = path.open("a+b")
        if path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise ValueError("Run is still active or already being resumed; try after it stops.") from exc
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def environment_identity(execution: dict, checks, salt: str, runtime_home: Path) -> dict:
    # Environment values contribute to identity but are never written to evidence.
    env = clean_environment(runtime_home, execution.get("env_allowlist", []))
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
        env.pop(key, None)
    # These overrides affect actual model calls, independently of models.json.
    env.update({key: os.getenv(key, "") for key in ("MIMO_MODEL", "MIMO_BASE_URL")})
    packages = sorted({(d.metadata.get("Name", "").lower(), d.version)
                       for d in importlib.metadata.distributions()})
    tools = {"git"}
    for step in [*(checks or []), *execution.get("prepare", [])]:
        argv = step.get("argv", [])
        if argv and argv[0] != "{python}":
            tools.add(argv[0])
    binaries = {}
    for name in sorted(tools):
        path = shutil.which(name)
        binaries[name] = {"path": path, "sha256": file_digest(Path(path)) if path else None}
    image_id = None
    if execution["mode"] == "docker":
        try:
            result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", execution["image"]],
                                    capture_output=True, text=True, timeout=30, env=env)
            image_id = result.stdout.strip() if result.returncode == 0 else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            image_id = "unavailable"
    return {"platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version, "executable": sys.executable,
            "packages_sha256": digest(packages), "tools": binaries, "image_id": image_id,
            "environment_sha256": digest([salt, env])}


def engine_identity() -> str:
    # A wheel lives in site-packages beside unrelated packages; a checkout may
    # contain legacy scripts that are not distributed. Hash the same executable
    # components in both layouts, using module names instead of install paths.
    code = {"mimo_harness/" + p.name: file_digest(p)
            for p in sorted(Path(__file__).parent.glob("*.py"))}
    for name in ("run_mimo_codeharness_v02", "scoring_v03", "verification_v04"):
        spec = importlib.util.find_spec(name)
        code[name + ".py"] = file_digest(Path(spec.origin)) if spec and spec.origin else None
    return digest(code)


class RecoverySource:
    def __init__(self, root: Path):
        self.root = root.resolve()
        path = safe_file(self.root, MANIFEST_NAME)
        self.data = read_object(path)
        if self.data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported or missing run manifest version; start a new run.")
        if self.data.get("kind") not in {"run", "verify"}:
            raise ValueError("Unsupported recovery run kind.")
        for name in ("inputs", "origin", "options", "model_checkpoints", "artifacts"):
            if not isinstance(self.data.get(name), dict):
                raise ValueError(f"Malformed recovery manifest field: {name}")
        if not isinstance(self.data.get("environment_salt"), str) or not isinstance(self.data.get("run_id"), str):
            raise ValueError("Malformed recovery identity.")
        if digest(self.data.get("inputs")) != self.data.get("fingerprint"):
            raise ValueError("Damaged run fingerprint.")
        if self.data["options"] != self.data["inputs"].get("options"):
            raise ValueError("Run options disagree with the input fingerprint.")
        saved = read_object(safe_file(self.root, "inputs/source_manifest.json"))
        if digest(saved) != self.data["inputs"].get("source_sha256"):
            raise ValueError("Damaged source inventory.")
        snapshot = self.root / "inputs/project"
        if snapshot.is_symlink() or snapshot.parent.is_symlink() or not snapshot.is_dir():
            raise ValueError("Missing or unsafe source snapshot.")
        if source_inventory(snapshot) != saved:
            raise ValueError("Saved source snapshot changed; start a new run.")
        for relative, expected in self.data.get("artifacts", {}).items():
            if file_digest(safe_file(self.root, relative)) != expected:
                raise ValueError(f"Recovery evidence changed: {relative}")
        self.provider_ledger = None
        ledger = self.data.get("provider_ledger")
        if ledger is not None:
            if not isinstance(ledger, dict) or ledger.get("path") != "provider_ledger.json":
                raise ValueError("Invalid provider budget ledger reference.")
            path = safe_file(self.root, ledger["path"])
            if file_digest(path) != ledger.get("sha256"):
                raise ValueError("Provider budget ledger changed; start a new run.")
            self.provider_ledger = read_object(path)
            from mimo_harness.model_runtime import validate_ledger
            validate_ledger(self.provider_ledger)
        self.agent_ledger = None
        if self.data.get("agent_ledger") is not None:
            reference = self.data["agent_ledger"]
            if not isinstance(reference, dict) or reference.get("path") != "agent_ledger.json":
                raise ValueError("Invalid Agent ledger reference.")
            path = safe_file(self.root, reference["path"])
            if file_digest(path) != reference.get("sha256"):
                raise ValueError("Agent ledger changed.")
            from mimo_harness.cli_agents import validate_agent_ledger
            self.agent_ledger = validate_agent_ledger(read_object(path))
        if self.data["options"].get("workflow") == "repair":
            expected = self.data["options"].get("task_sha256")
            if file_digest(safe_file(self.root, "inputs/task.txt")) != expected:
                raise ValueError("Saved repair task changed.")
            original = Path(self.data["origin"]["task_file"])
            if original.exists() and file_digest(original) != expected:
                raise ValueError("Original repair task changed; start a new run.")
        self.records = {}
        for key, checkpoint in self.data.get("model_checkpoints", {}).items():
            if not isinstance(checkpoint, dict) or any(not isinstance(checkpoint.get(k), str)
                                                      for k in ("path", "sha256", "signature")):
                raise ValueError(f"Malformed model checkpoint: {key}")
            path = safe_file(self.root, checkpoint["path"])
            if file_digest(path) != checkpoint["sha256"]:
                raise ValueError(f"Model checkpoint changed: {key}")
            row = read_object(path)
            if row.get("candidate_id") != key:
                raise ValueError(f"Invalid model checkpoint identity: {key}")
            from run_mimo_codeharness_v02 import ModelRunRecord
            try:
                ModelRunRecord(**row)
            except TypeError as exc:
                raise ValueError(f"Malformed model response: {key}") from exc
            if row["status"] not in {"completed", "failed"} or not isinstance(row["content"], str):
                raise ValueError(f"Malformed model response status/content: {key}")
            self.records[key] = (checkpoint["signature"], row)
        if self.data["kind"] == "verify":
            if file_digest(safe_file(self.root, "input.patch")) != self.data["inputs"].get("patch_sha256"):
                raise ValueError("Saved patch changed.")
            original = Path(self.data["origin"]["patch"])
            if original.exists() and file_digest(original) != self.data["inputs"]["patch_sha256"]:
                raise ValueError("Original patch changed; start a new verification.")


class RunManifest:
    def __init__(self, root: Path, kind: str, origin: dict, options: dict,
                 policy: dict, resolved_base=None, previous: RecoverySource | None = None):
        self.root, self.previous = root, previous
        salt = previous.data["environment_salt"] if previous else uuid.uuid4().hex
        source = source_inventory(root / "inputs/project")
        atomic_json(root / "inputs/source_manifest.json", source)
        model_path = origin.get("model_config")
        inputs = {"source_sha256": digest(source), "resolved_base": resolved_base,
                  "config_sha256": file_digest(Path(origin["config"])),
                  "model_config_sha256": file_digest(Path(model_path)) if model_path else None,
                  "patch_sha256": file_digest(root / "input.patch") if kind == "verify" else None,
                  "policy_sha256": digest(policy), "options": options,
                  "engine_sha256": engine_identity(),
                  "environment": environment_identity(policy["execution"], policy.get("checks"), salt,
                                                       root / "identity-home")}
        if origin.get("agent_config"):
            from mimo_harness.cli_agents import load_agent_config
            agent = load_agent_config(Path(origin["agent_config"]))
            files = {}
            for arg in agent["argv"]:
                if arg.startswith("-") or arg in {"{python}", "{request}", "{workspace}"}:
                    continue
                path = Path(arg)
                if not path.is_absolute():
                    path = root / "inputs/project" / agent["cwd"] / path
                try:
                    if path.is_file():
                        files[arg] = file_digest(path)
                except OSError:
                    # An inline program/JSON argument is not necessarily a path.
                    pass
            inputs["agent"] = {"config_sha256": file_digest(Path(origin["agent_config"])), "argv_files": files,
                "environment": environment_identity(agent["execution"], [{"argv": agent["argv"]}], salt,
                                                      root / "identity-home-agent")}
        if previous and inputs != previous.data["inputs"]:
            changed = sorted(key for key in inputs if inputs[key] != previous.data["inputs"].get(key))
            raise ValueError("Recovery inputs changed (" + ", ".join(changed) + "); start a new run.")
        self.data = {"schema_version": SCHEMA_VERSION, "harness_version": __version__,
                     "run_id": uuid.uuid4().hex, "kind": kind, "status": "RUNNING",
                     "created_at": time.time(), "origin": origin, "options": options,
                     "environment_salt": salt, "inputs": inputs, "fingerprint": digest(inputs),
                     "resumed_from": {"run_id": previous.data["run_id"], "directory": str(previous.root)} if previous else None,
                     "model_checkpoints": {}, "artifacts": {}, "reused_candidates": []}
        self.data["provider_budget_id"] = previous.data.get("provider_budget_id", previous.data["run_id"]) if previous else self.data["run_id"]
        self.save()

    def save(self):
        atomic_json(self.root / MANIFEST_NAME, self.data)

    def save_provider_ledger(self, ledger):
        path = self.root / "provider_ledger.json"
        atomic_json(path, ledger)
        self.data["provider_ledger"] = {"path": path.name, "sha256": file_digest(path)}
        self.save()

    def save_agent_ledger(self, ledger):
        path = self.root / "agent_ledger.json"
        atomic_json(path, ledger)
        self.data["agent_ledger"] = {"path": path.name, "sha256": file_digest(path)}
        self.save()

    def reuse(self, candidate_id: str, signature: str):
        if not self.previous or candidate_id not in self.previous.records:
            return None
        expected, row = self.previous.records[candidate_id]
        if expected != signature:
            raise ValueError(f"Model prompt/config changed: {candidate_id}; start a new run.")
        if row.get("status") != "completed":
            return None
        self.data["reused_candidates"].append(candidate_id)
        self.record(candidate_id, signature, row, reused=True)
        return row

    def record(self, candidate_id: str, signature: str, row: dict, reused=False):
        relative = f"checkpoints/models/{candidate_id}.json"
        path = self.root / relative
        atomic_json(path, row)
        self.data["model_checkpoints"][candidate_id] = {"path": relative, "sha256": file_digest(path),
                                                         "signature": signature, "reused": reused}
        self.save()

    def finish(self, status: str, exit_code: int):
        artifacts = {}
        ignored = {"inputs", "worktrees", "identity-home", "identity-home-agent", "checkpoints", "agent_runs"}
        for directory, dirs, files in os.walk(self.root):
            parent = Path(directory)
            dirs[:] = [name for name in dirs if name != "runtime" and
                       not (parent / name).is_symlink() and not (parent == self.root and name in ignored)]
            for name in files:
                path = parent / name
                if path.is_symlink() or name in {MANIFEST_NAME, ".run.lock", "MIMO_RUN_SUMMARY.md"}:
                    continue
                artifacts[path.relative_to(self.root).as_posix()] = file_digest(path)
        self.data.update(status=status, exit_code=exit_code, finished_at=time.time(), artifacts=artifacts)
        self.save()
