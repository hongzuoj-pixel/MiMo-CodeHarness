"""User-facing CLI for installing, diagnosing, running, and reporting the harness."""
from __future__ import annotations

import argparse
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from mimo_harness import __version__
from mimo_harness.checks import validate_checks
from mimo_harness.execution import validate_execution
from mimo_harness.providers import validate_model_config
from mimo_harness.manifests import (
    MANIFEST_NAME, RecoverySource, RunLock, RunManifest, atomic_json, source_inventory,
)
from run_mimo_codeharness_v02 import (
    BuildTesterV02,
    ModelRunRecord,
    COPY_EXCLUDE_DIRS,
    apply_patches,
    copy_worktree,
    run_harness,
    write_json,
)
from verification_v04 import ERROR, EXIT_CODES, classify_candidate, summarize_verifications

CONFIG_FILENAME = "mimo-harness.json"
STATE_DIRNAME = ".mimo"
MODEL_CONFIG_FILENAME = "models.json"
CONFIG_SCHEMA_VERSION = 1


@dataclass
class Diagnostic:
    name: str
    status: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def _json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise ValueError(f"Could not read JSON configuration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a JSON object: {path}")
    return data


def _write_json_new(path: Path, data: Mapping[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dump(data) + "\n", encoding="utf-8")


def _resolve(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _config_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def load_project_config(path: Path) -> Dict[str, Any]:
    data = _read_json(path)
    schema = data.get("schema_version")
    if schema != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version {schema!r}; expected {CONFIG_SCHEMA_VERSION}."
        )
    mode = str(data.get("mode", "")).lower()
    if mode not in {"offline", "api"}:
        raise ValueError("Configuration 'mode' must be 'offline' or 'api'.")
    for name in ("project", "output_dir", "model_config"):
        if not isinstance(data.get(name), str) or not str(data[name]).strip():
            raise ValueError(f"Configuration '{name}' must be a non-empty string.")
    for name in ("limit_tasks", "max_files"):
        value = data.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"Configuration '{name}' must be a positive integer.")
    for name in ("apply_patches", "run_build_tests"):
        if not isinstance(data.get(name), bool):
            raise ValueError(f"Configuration '{name}' must be true or false.")
    validate_checks(data.get("checks"))
    data["execution"] = validate_execution(data.get("execution"))
    return data


def default_project_config(mode: str = "offline") -> Dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "project": ".",
        "output_dir": ".mimo/runs",
        "mode": mode,
        "model_config": f"{STATE_DIRNAME}/{MODEL_CONFIG_FILENAME}",
        "limit_tasks": 6,
        "max_files": 800,
        "apply_patches": True,
        "run_build_tests": False,
        "execution": {"mode": "local", "env_allowlist": [], "prepare": []},
    }


def default_model_config(mode: str = "offline") -> Dict[str, Any]:
    if mode == "api":
        return {
            "default_timeout_seconds": 180,
            "default_max_tokens": 4000,
            "default_temperature": 0.2,
            "runtime": {"max_attempts": 1},
            "budget": {"max_requests": 100},
            "models": [
                {
                    "name": "Xiaomi-MiMo-Pro",
                    "provider_type": "openai_compatible",
                    "model": "mimo-v2.5-pro",
                    "base_url": "https://api.xiaomimimo.com/v1",
                    "api_key_env": "MIMO_API_KEY",
                    "enabled": True,
                    "max_tokens": 4000,
                    "temperature": 0.2,
                    "timeout_seconds": 240,
                    "auth_header": "api-key",
                    "max_tokens_field": "max_completion_tokens",
                }
            ],
        }
    return {
        "default_timeout_seconds": 60,
        "default_max_tokens": 2000,
        "default_temperature": 0.2,
        "runtime": {"max_attempts": 1},
        "budget": {"max_requests": 100},
        "models": [
            {
                "name": "LocalMock",
                "provider_type": "mock",
                "model": "dry-run-code-agent",
                "enabled": True,
            }
        ],
    }


def collect_diagnostics(
    config_path: Path,
    mode_override: Optional[str] = None,
    project_override: Optional[str] = None,
) -> tuple[List[Diagnostic], Optional[Dict[str, Any]]]:
    rows: List[Diagnostic] = []
    version_ok = sys.version_info >= (3, 10)
    rows.append(
        Diagnostic(
            "python",
            "ok" if version_ok else "error",
            f"Python {sys.version.split()[0]} (requires >= 3.10)",
        )
    )

    git_path = shutil.which("git")
    rows.append(
        Diagnostic(
            "git",
            "ok" if git_path else "error",
            git_path or "git was not found on PATH",
        )
    )

    try:
        cfg = load_project_config(config_path)
        rows.append(Diagnostic("config", "ok", str(config_path)))
    except ValueError as exc:
        rows.append(Diagnostic("config", "error", str(exc)))
        return rows, None

    base = config_path.parent
    project = _resolve(base, project_override or str(cfg.get("project", ".")))
    if project.is_dir():
        rows.append(Diagnostic("project", "ok", str(project)))
    else:
        rows.append(Diagnostic("project", "error", f"Directory does not exist: {project}"))

    output_dir = _resolve(base, str(cfg.get("output_dir", ".mimo/runs")))
    probe_parent = output_dir
    while not probe_parent.exists() and probe_parent != probe_parent.parent:
        probe_parent = probe_parent.parent
    writable = probe_parent.is_dir() and os.access(probe_parent, os.W_OK)
    rows.append(
        Diagnostic(
            "output",
            "ok" if writable else "error",
            f"{output_dir} (nearest existing parent: {probe_parent})",
        )
    )
    if project.is_dir() and not _output_location_is_safe(project, output_dir):
        rows.append(
            Diagnostic(
                "output_isolation",
                "error",
                "When output_dir is inside the project, it must be under .mimo/ or outputs/.",
            )
        )
    for check in cfg.get("checks") or []:
        check_cwd = (project / check.get("cwd", ".")).resolve()
        try:
            check_cwd.relative_to(project)
            if not check_cwd.is_dir():
                raise ValueError("directory does not exist")
        except ValueError as exc:
            rows.append(Diagnostic(f"check:{check['id']}", "error", f"Invalid cwd: {check_cwd}: {exc}"))
        if check["adapter"] == "pytest" and cfg.get("execution", {}).get("mode", "local") == "local" and importlib.util.find_spec("pytest") is None:
            rows.append(Diagnostic(f"check:{check['id']}", "error",
                                   "pytest is not installed in this interpreter. Install the project's test dependencies or pip install pytest."))
        if check["adapter"] == "node" and cfg["execution"]["mode"] != "docker":
            try:
                import tempfile
                from mimo_harness.execution import clean_environment, run_process
                from mimo_harness.structured_cases import validate_node_version
                with tempfile.TemporaryDirectory(prefix="mimo-node-doctor-") as temporary:
                    runtime = Path(temporary)
                    probe = run_process(["node", "--version"], runtime, timeout=10, env=clean_environment(runtime / "home"))
                if probe.status != "completed" or probe.returncode != 0:
                    raise ValueError("Node is unavailable; install Node 22.23+ or 24.19+.")
                rows.append(Diagnostic(f"node:{check['id']}", "ok", validate_node_version(probe.stdout)))
            except (ValueError, OSError) as exc:
                rows.append(Diagnostic(f"node:{check['id']}", "error", str(exc)))
    execution = cfg["execution"]
    rows.append(Diagnostic("execution", "ok", execution["mode"] + " (clean environment; allowlist names only)"))
    if execution["mode"] == "docker":
        available = sys.platform == "linux" and bool(shutil.which("docker")) and os.getuid() != 0
        rows.append(Diagnostic("docker", "ok" if available else "error",
                               "Linux Docker client available; image/daemon checked during preparation." if available else
                               "Docker mode requires Docker on a non-root Linux host; no local fallback."))

    model_path = _resolve(base, str(cfg.get("model_config", "")))
    model_cfg: Optional[Dict[str, Any]] = None
    try:
        model_cfg = _read_json(model_path)
        validate_model_config(model_cfg)
        enabled = [m for m in model_cfg.get("models", []) if m.get("enabled", True)]
        if not enabled:
            raise ValueError("Model configuration contains no enabled models.")
        rows.append(
            Diagnostic("model_config", "ok", f"{model_path} ({len(enabled)} enabled)")
        )
    except (ValueError, AttributeError) as exc:
        rows.append(Diagnostic("model_config", "error", str(exc)))
        model_cfg = None

    mode = mode_override or str(cfg.get("mode", "offline")).lower()
    rows.append(
        Diagnostic(
            "mode",
            "ok",
            "OFFLINE MOCK (no API call)" if mode == "offline" else "API execution",
        )
    )
    if mode == "api" and model_cfg:
        api_models = [
            m
            for m in model_cfg.get("models", [])
            if m.get("enabled", True) and m.get("provider_type") != "mock"
        ]
        if not api_models:
            rows.append(
                Diagnostic(
                    "api_provider",
                    "error",
                    "API mode requires at least one enabled non-mock provider.",
                )
            )
        for model in api_models:
            env_name = str(model.get("api_key_env") or "MIMO_API_KEY")
            present = bool(os.getenv(env_name))
            rows.append(
                Diagnostic(
                    f"secret:{env_name}",
                    "ok" if present else "error",
                    "set" if present else f"Missing environment variable: {env_name}",
                )
            )

    if project.is_dir():
        proc = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
        ) if git_path else None
        in_git = bool(proc and proc.returncode == 0 and proc.stdout.strip() == "true")
        rows.append(
            Diagnostic(
                "repository",
                "ok" if in_git else "warning",
                "Git worktree detected" if in_git else "Not a Git worktree; run works, but verify --base HEAD does not.",
            )
        )
    return rows, cfg


def _print_diagnostics(rows: Sequence[Diagnostic]) -> None:
    labels = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
    for row in rows:
        print(f"[{labels.get(row.status, row.status.upper())}] {row.name}: {row.message}")


def _has_errors(rows: Iterable[Diagnostic]) -> bool:
    return any(row.status == "error" for row in rows)


def _output_location_is_safe(project: Path, output_dir: Path) -> bool:
    try:
        relative = output_dir.resolve().relative_to(project.resolve())
    except ValueError:
        return True
    return bool(relative.parts) and relative.parts[0] in {STATE_DIRNAME, "outputs"}


def command_init(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    base = config_path.parent
    project = _resolve(base, args.project)
    if not project.is_dir():
        print(f"[ERROR] Project directory does not exist: {project}", file=sys.stderr)
        return EXIT_CODES[ERROR]

    project_cfg = default_project_config(args.mode)
    project_cfg["project"] = os.path.relpath(project, base).replace(os.sep, "/")
    model_path = base / STATE_DIRNAME / MODEL_CONFIG_FILENAME
    existing = [path for path in (config_path, model_path) if path.exists()]
    if existing and not args.force:
        joined = ", ".join(str(path) for path in existing)
        print(
            f"[ERROR] Refusing to overwrite existing file(s): {joined}. "
            "Use --force only when replacement is intentional.",
            file=sys.stderr,
        )
        return EXIT_CODES[ERROR]
    # Save suggestions for review once; subsequent runs reuse the stored policy.
    detected = BuildTesterV02(project, base / STATE_DIRNAME / "init-logs").detect_external_commands()
    if not any(name == "unittest" for name, _ in detected):
        project_cfg["checks"] = [
            {"id": name, "adapter": "pytest", "args": ["-q"], "cwd": ".", "timeout_seconds": 180}
            if name == "pytest" else {"id": name, "adapter": "command", "argv": argv, "cwd": ".", "timeout_seconds": 180}
            for name, argv in detected
        ]
    try:
        _write_json_new(config_path, project_cfg, args.force)
        _write_json_new(model_path, default_model_config(args.mode), args.force)
    except FileExistsError as exc:
        print(f"[ERROR] {exc} Use --force only when replacement is intentional.", file=sys.stderr)
        return EXIT_CODES[ERROR]

    print(f"[OK] Created {config_path}")
    print(f"[OK] Created {model_path}")
    print("[NEXT] Review project checks in the configuration before enabling --run-tests.")
    if args.mode == "offline":
        print("[MODE] OFFLINE MOCK — no API key is required and no model API will be called.")
    else:
        print("[NEXT] Set MIMO_API_KEY in the environment, then run: mimo doctor")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    rows, cfg = collect_diagnostics(config_path)
    if getattr(args, "agent_config", None) and cfg is not None:
        try:
            from mimo_harness.cli_agents import load_agent_config, preflight_agent
            agent = load_agent_config(_resolve(Path.cwd(), args.agent_config))
            preflight_agent(agent, _resolve(config_path.parent, cfg["project"]))
            if {k.upper() for k in agent["execution"]["env_allowlist"]} & {k.upper() for k in cfg["execution"]["env_allowlist"]}:
                raise ValueError("Agent and acceptance env_allowlist must be disjoint.")
            if not any(c.get("target_tests") for c in (cfg.get("checks") or []) if c["adapter"] in {"pytest", "node", "junit"}):
                raise ValueError("Repair requires explicit target_tests in a structured case check.")
            rows.append(Diagnostic("cli_agent", "OK", "Agent configuration/credentials available; no Agent invoked. Docker availability is checked during setup."))
        except (ValueError, OSError) as exc:
            rows.append(Diagnostic("cli_agent", "ERROR", str(exc)))
    if args.json:
        print(_json_dump({"healthy": not _has_errors(rows), "checks": [r.to_dict() for r in rows]}))
    else:
        _print_diagnostics(rows)
        print("Doctor result: " + ("READY" if not _has_errors(rows) else "NOT READY"))
    return 0 if not _has_errors(rows) else EXIT_CODES[ERROR]


def _bool_override(positive: bool, negative: bool, default: bool) -> bool:
    if positive:
        return True
    if negative:
        return False
    return default


def _run_name(prefix: str) -> str:
    return (
        f"{prefix}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
        f"-{time.time_ns() % 1_000_000:06d}"
    )


def _run_directory(output_root: Path, name: str) -> Path:
    if not name or name in {".", ".."} or any(c in name for c in '/\\:') or Path(name).name != name:
        raise ValueError("Run name must be a single directory name, without path separators.")
    return output_root / name


def _snapshot_worktree(project: Path, destination: Path) -> None:
    before = source_inventory(project, COPY_EXCLUDE_DIRS)
    copy_worktree(project, destination)
    if before != source_inventory(destination) or before != source_inventory(project, COPY_EXCLUDE_DIRS):
        raise ValueError("Project changed while capturing inputs; retry after edits stop.")


def _execute_attempt(run_dir: Path, project: Path, operation) -> int:
    # mkdir is the reservation: two invocations can never share one output tree.
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir()
    except FileExistsError:
        print(f"[ERROR] Run directory already exists: {run_dir}. Choose a new --name.", file=sys.stderr)
        return EXIT_CODES[ERROR]
    with RunLock(run_dir):
        state = {}
        try:
            code = operation(state)
        except KeyboardInterrupt:
            code = _record_cancelled(run_dir, project)
        except Exception as exc:
            print(f"[ERROR] Execution failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            reason = str(exc).split(":", 1)[0]
            if reason not in {"AGENT_BUDGET_EXHAUSTED", "BASELINE_TARGET_NOT_REPRODUCED"}:
                reason = "EXECUTION_ERROR"
            summary = {"overall_verdict": ERROR, "exit_code": 4, "counts": {ERROR: 1},
                       "run_errors": [{"stage": "execution", "check": "runtime", "reason_codes": [reason]}]}
            atomic_json(run_dir / "verification_summary.json", summary)
            atomic_json(run_dir / "run_log.json", {"mode": "error", "project": str(project),
                        "output_dir": str(run_dir), "verification_summary": summary,
                        "key_outputs": {"verification_summary": str(run_dir / "verification_summary.json")}})
            code = EXIT_CODES[ERROR]
        manifest = state.get("manifest")
        if manifest is not None:
            manifest.finish("CANCELLED" if code == 130 else "ERROR" if code == 4 else "COMPLETED", code)
        return code


def command_run(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    try:
        initial_cfg = load_project_config(config_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CODES[ERROR]
    mode = "api" if args.execute else "offline" if args.offline else str(initial_cfg["mode"])
    rows, cfg = collect_diagnostics(
        config_path, mode_override=mode, project_override=args.project
    )
    if cfg is None or _has_errors(rows):
        _print_diagnostics(rows)
        print("[ERROR] Preflight failed. Fix the checks above before running.", file=sys.stderr)
        return EXIT_CODES[ERROR]

    base = config_path.parent
    project = _resolve(base, args.project or str(cfg.get("project", ".")))
    output_root = _resolve(base, str(cfg.get("output_dir", ".mimo/runs")))
    model_config = _resolve(base, str(cfg.get("model_config", "")))
    execute = mode == "api"
    apply_patches_flag = _bool_override(
        args.apply_patches, args.no_apply_patches, bool(cfg.get("apply_patches", True))
    )
    run_tests = _bool_override(
        args.run_tests, args.no_run_tests, bool(cfg.get("run_build_tests", False))
    )
    name = args.name or _run_name("run")
    run_dir = _run_directory(output_root, name)

    print(
        "[MODE] API execution — configured providers will be called."
        if execute
        else "[MODE] OFFLINE MOCK — no model API will be called."
    )
    def operate(state):
        snapshot = run_dir / "inputs/project"
        _snapshot_worktree(project, snapshot)
        options = {"execute": execute, "apply_patches": apply_patches_flag, "run_tests": run_tests,
                   "limit_tasks": args.limit_tasks or int(cfg["limit_tasks"]),
                   "max_files": args.max_files or int(cfg["max_files"])}
        manifest = RunManifest(run_dir, "run", {"project": str(project), "config": str(config_path),
                               "model_config": str(model_config)}, options, cfg,
                               previous=getattr(args, "recovery_source", None))
        state["manifest"] = manifest
        log = run_harness(
            project=snapshot,
            name=name,
            config_path=model_config,
            limit_tasks=args.limit_tasks or int(cfg.get("limit_tasks", 6)),
            max_files=args.max_files or int(cfg.get("max_files", 800)),
            execute=execute,
            apply_patch_flag=apply_patches_flag,
            run_build_tests=run_tests,
            output_root=output_root,
            checks=cfg.get("checks"),
            execution=cfg.get("execution"),
            checkpoint=manifest,
            display_project=project,
        )
        log["project"] = str(project)
        log["key_outputs"]["run_manifest"] = str(run_dir / MANIFEST_NAME)
        log["reused_candidates"] = manifest.data["reused_candidates"]
        atomic_json(run_dir / "run_log.json", log)
        verification = log.get("verification_summary", {})
        print(f"[RESULT] {verification.get('overall_verdict', ERROR)}")
        for row in log.get("provider_summary", {}).get("models", []):
            if row.get("status") == "failed":
                print(f"[MODEL ERROR] {row['candidate_id']}: {row['error']}")
        print(f"[OUTPUT] {log.get('output_dir', '')}")
        print(f"[SUMMARY] {log.get('key_outputs', {}).get('verification_summary', '')}")
        return int(verification.get("exit_code", EXIT_CODES[ERROR]))

    return _execute_attempt(run_dir, project, operate)


def _safe_extract_tar(payload: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Unsafe path in git archive: {member.name}") from exc
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                try:
                    link_target.relative_to(root)
                except ValueError as exc:
                    raise ValueError(
                        f"Unsafe link target in git archive: {member.name} -> {member.linkname}"
                    ) from exc
        archive.extractall(destination)


def _materialize_baseline(project: Path, base_ref: str, destination: Path) -> str:
    if destination.exists():
        shutil.rmtree(destination)
    if base_ref.upper() == "WORKTREE":
        _snapshot_worktree(project, destination)
        return "WORKTREE"

    top = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if top.returncode != 0:
        raise ValueError(f"Project is not a Git worktree: {project}")
    if Path(top.stdout.strip()).resolve() != project.resolve():
        raise ValueError(
            "For --base Git refs, --project must point to the repository root."
        )
    resolved = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if resolved.returncode != 0:
        raise ValueError(f"Git base ref does not resolve to a commit: {base_ref}")
    archive = subprocess.run(
        ["git", "-C", str(project), "archive", "--format=tar", resolved.stdout.strip()],
        capture_output=True,
    )
    if archive.returncode != 0:
        raise ValueError(archive.stderr.decode("utf-8", errors="replace").strip())
    _safe_extract_tar(archive.stdout, destination)
    return resolved.stdout.strip()


def command_verify(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    try:
        cfg = load_project_config(config_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CODES[ERROR]

    base = config_path.parent
    project = _resolve(base, args.project or str(cfg.get("project", ".")))
    patch_path = _resolve(Path.cwd(), args.patch)
    output_root = _resolve(base, str(cfg.get("output_dir", ".mimo/runs")))
    run_dir = _run_directory(output_root, args.name or _run_name("verify"))
    baseline_dir = run_dir / "worktrees" / "_baseline"

    if not project.is_dir():
        print(f"[ERROR] Project directory does not exist: {project}", file=sys.stderr)
        return EXIT_CODES[ERROR]
    if not patch_path.is_file():
        print(f"[ERROR] Patch file does not exist: {patch_path}", file=sys.stderr)
        return EXIT_CODES[ERROR]
    if not shutil.which("git"):
        print("[ERROR] git was not found on PATH.", file=sys.stderr)
        return EXIT_CODES[ERROR]
    if not _output_location_is_safe(project, output_root):
        print(
            "[ERROR] When output_dir is inside the project, it must be under .mimo/ or outputs/.",
            file=sys.stderr,
        )
        return EXIT_CODES[ERROR]
    if run_dir.exists():
        print(
            f"[ERROR] Run directory already exists: {run_dir}. Choose a new --name.",
            file=sys.stderr,
        )
        return EXIT_CODES[ERROR]

    try:
        patch_bytes = patch_path.read_bytes()
        # Keep original bytes for identity; normalize only the text used by git
        # so Windows write_text cannot turn CRLF input into CR-CR-LF output.
        patch_text = patch_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        print("[ERROR] Patch must be a UTF-8 unified diff.", file=sys.stderr)
        return EXIT_CODES[ERROR]

    def operate(state):
        snapshot = run_dir / "inputs/project"
        resolved_base = _materialize_baseline(project, args.base, snapshot)
        (run_dir / "input.patch").write_bytes(patch_bytes)
        previous = getattr(args, "recovery_source", None)
        options = {"base": args.base, "task_type": args.task_type, "run_tests": args.run_tests}
        origin = {"project": str(project), "config": str(config_path),
                  "patch": previous.data["origin"]["patch"] if previous else str(patch_path)}
        manifest = RunManifest(run_dir, "verify", origin, options, cfg, resolved_base, previous)
        state["manifest"] = manifest
        write_json(run_dir / "verification_policy.json", {"checks": cfg.get("checks"), "execution": cfg.get("execution"), "task_type": args.task_type})
        copy_worktree(snapshot, baseline_dir)
        baseline_summary = BuildTesterV02(
            baseline_dir, run_dir / "baseline", checks=cfg.get("checks"), execution=cfg.get("execution")
        ).run_all(run_external=args.run_tests)
        record = ModelRunRecord(
            task_id="PATCH",
            provider_name="local-patch",
            model="none",
            status="completed",
            dry_run=True,
            time_seconds=0.0,
            input_tokens_est=0,
            output_tokens_est=0,
            total_tokens_est=0,
            content=f"```diff\n{patch_text}\n```",
            error="",
            raw={},
            candidate_id="local-patch",
        )
        patch_result = apply_patches(
            [record], snapshot, run_dir, actually_apply=True
        )[0]
        candidate_summary = None
        if patch_result.applied:
            candidate_summary = BuildTesterV02(
                Path(patch_result.worktree), run_dir / "candidate", checks=cfg.get("checks"), execution=cfg.get("execution")
            ).run_all(run_external=args.run_tests)
        task = SimpleNamespace(task_id="PATCH", task_type=args.task_type)
        result = classify_candidate(task, patch_result, baseline_summary, candidate_summary)
        verification = summarize_verifications([result])
        write_json(run_dir / "patch_result.json", asdict(patch_result))
        write_json(run_dir / "baseline_summary.json", baseline_summary)
        if candidate_summary is not None:
            write_json(run_dir / "candidate_summary.json", candidate_summary)
        write_json(run_dir / "verification_result.json", result.to_dict())
        write_json(run_dir / "verification_summary.json", verification)
        run_log = {
            "mode": "direct_patch_verification",
            "project": str(project),
            "patch": str(patch_path),
            "base": args.base,
            "resolved_base": resolved_base,
            "task_type": args.task_type,
            "run_build_tests": args.run_tests,
            "output_dir": str(run_dir),
            "verification_summary": verification,
            "key_outputs": {
                "verification_summary": str(run_dir / "verification_summary.json"),
                "verification_result": str(run_dir / "verification_result.json"),
                "baseline_summary": str(run_dir / "baseline_summary.json"),
                "candidate_summary": str(run_dir / "candidate_summary.json") if candidate_summary else "",
                "run_manifest": str(run_dir / MANIFEST_NAME),
            },
        }
        write_json(run_dir / "run_log.json", run_log)

        print(f"[RESULT] {result.verdict}")
        print(f"[REASONS] {', '.join(result.reason_codes)}")
        print(f"[CHANGED] {', '.join(result.changed_files) or 'none'}")
        print(f"[OUTPUT] {run_dir}")
        if not args.run_tests:
            print("[NOTE] Behavior tests were not executed. Use --run-tests for executable evidence.")
        return int(verification["exit_code"])

    return _execute_attempt(run_dir, project, operate)


def _latest_run(output_root: Path) -> Optional[Path]:
    candidates = [p for p in output_root.iterdir() if p.is_dir()] if output_root.is_dir() else []
    candidates = [p for p in candidates if (p / "run_log.json").is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _record_cancelled(run_dir: Path, project: Path) -> int:
    summary = {"overall_verdict": "CANCELLED", "exit_code": 130, "counts": {"CANCELLED": 1}}
    write_json(run_dir / "verification_summary.json", summary)
    write_json(run_dir / "run_log.json", {"mode": "interrupted", "project": str(project),
               "output_dir": str(run_dir), "verification_summary": summary,
               "key_outputs": {"verification_summary": str(run_dir / "verification_summary.json")}})
    print(f"[RESULT] CANCELLED — processes cleaned up; existing evidence: {run_dir}", file=sys.stderr)
    return 130


def command_resume(args: argparse.Namespace) -> int:
    root = _resolve(Path.cwd(), args.run)
    if not root.is_dir():
        raise ValueError(f"Recovery run directory does not exist: {root}")
    with RunLock(root):
        try:
            source = RecoverySource(root)
            origin, options = source.data["origin"], source.data["options"]
            if args.config != CONFIG_FILENAME and _config_path(args.config) != Path(origin["config"]):
                raise ValueError("Recovery uses the original configuration path; start a new run to change it.")
            common = dict(config=origin["config"], project=origin["project"],
                          name=args.name or _run_name("resume"), recovery_source=source)
            print(f"[RESUME] {root} — completed model responses may be reused; all checks run fresh.")
            if options.get("workflow") == "repair":
                from mimo_harness.repair import command_repair
                return command_repair(SimpleNamespace(**common, agent_config=origin["agent_config"],
                    task_file=str(root / "inputs/task.txt"), base=options["base"], max_rounds=options["max_rounds"]))
            if source.data["kind"] == "verify":
                return command_verify(SimpleNamespace(**common, patch=str(root / "input.patch"),
                    base=options["base"], task_type=options["task_type"], run_tests=options["run_tests"]))
            return command_run(SimpleNamespace(**common, execute=options["execute"], offline=not options["execute"],
                apply_patches=options["apply_patches"], no_apply_patches=not options["apply_patches"],
                run_tests=options["run_tests"], no_run_tests=not options["run_tests"],
                limit_tasks=options["limit_tasks"], max_files=options["max_files"]))
        except (KeyError, TypeError) as exc:
            raise ValueError("Malformed recovery manifest; start a new run.") from exc


def command_report(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    try:
        cfg = load_project_config(config_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CODES[ERROR]
    output_root = _resolve(config_path.parent, str(cfg.get("output_dir", ".mimo/runs")))
    run_dir = _resolve(Path.cwd(), args.run) if args.run else _latest_run(output_root)
    if run_dir is None or not (run_dir / "run_log.json").is_file():
        print("[ERROR] No completed run with run_log.json was found.", file=sys.stderr)
        return EXIT_CODES[ERROR]

    log = _read_json(run_dir / "run_log.json")
    verification = log.get("verification_summary", {})
    manifest_path = run_dir / MANIFEST_NAME
    manifest = _read_json(manifest_path) if manifest_path.is_file() else None
    if manifest is not None and manifest.get("status") == "RUNNING":
        # A crash can occur after writing results but before sealing the attempt.
        verification = {"overall_verdict": "INCOMPLETE", "exit_code": 4, "counts": {}}
    verdict = verification.get("overall_verdict", "UNKNOWN")
    counts = verification.get("counts", {})
    lines = [
        "# MiMo-CodeHarness Run Summary",
        "",
        f"- Run directory: `{run_dir}`",
        f"- Mode: `{log.get('mode', 'unknown')}`",
        f"- Project: `{log.get('project', '')}`",
        f"- Verdict: **{verdict}**",
        f"- Exit code: `{verification.get('exit_code', '')}`",
        "",
        "## Verdict counts",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for key in ("PASS", "FAIL", "INSUFFICIENT_EVIDENCE", "ERROR", "NOT_APPLICABLE", "CANCELLED"):
        lines.append(f"| {key} | {counts.get(key, 0)} |")
    if manifest is not None:
        lines.extend(["", "## Run identity and recovery", "",
                      f"- Input fingerprint: `{manifest.get('fingerprint', '')}`",
                      f"- Manifest status: `{manifest.get('status', '')}`",
                      f"- Reused completed model responses: {len(manifest.get('reused_candidates', []))}",
                      "- Build/test verdicts and dependency environments are recreated for every attempt."])
        if manifest.get("resumed_from"):
            lines.append(f"- Resumed from: `{manifest['resumed_from']['directory']}`")
    if verification.get("run_errors"):
        lines.extend(["", "## Run errors", ""])
        for error in verification["run_errors"]:
            lines.append(f"- {error['stage']}/{error['check']}: {', '.join(error['reason_codes'])}")
    provider_budget = (log.get("provider_summary") or {}).get("budget")
    if provider_budget is None and (run_dir / "provider_ledger.json").is_file():
        provider_budget = _read_json(run_dir / "provider_ledger.json").get("summary")
    if provider_budget:
        budget = provider_budget
        cost = budget['reserved_estimated_cost_usd']
        if cost is None:
            cost = f"Unknown ({budget['requests_without_pricing']} unpriced request(s)); known-price reservations: {budget['known_price_reservations_usd']}"
        lines.extend(["", "## Model requests and budget", "",
                      f"- Reserved requests across recovery lineage: {budget['reserved_requests']}",
                      f"- Reserved tokens: {budget['reserved_tokens']}",
                      f"- Reserved estimated cost (configured prices, USD): {cost}",
                      f"- Requests without provider-reported usage: {budget['requests_without_provider_usage']}",
                      "- Reservations include failed/interrupted attempts; estimated cost is not a provider invoice."])
        models = (log.get("provider_summary") or {}).get("models", [])
        if models:
            lines.extend(["", "| Candidate | State | Finish | Usage | Attempts | Error code |",
                          "|---|---|---|---|---:|---|"])
            for row in models:
                lines.append(f"| {row['candidate_id']} | {row['status']} | {row['finish_reason']} | {row['usage_source']} | {len(row['attempts'])} | {row['error_code']} |")
    if (run_dir / "repair_history.json").is_file():
        history = _read_json(run_dir / "repair_history.json")
        lines.extend(["", "## Repair rounds", "", f"- Stop reason: `{history['stop_reason']}`",
                      "- Each candidate was tested against the original baseline; prior failures remain in the evidence.",
                      "", "| Round | Agent | Verdict | Reasons |", "|---:|---|---|---|"])
        for row in history["rounds"]:
            result = row["verification"]
            lines.append(f"| {row['round']} | {row['agent_status']} | {result['verdict']} | {', '.join(result['reason_codes'])} |")
    if (run_dir / "agent_ledger.json").is_file():
        accounting = _read_json(run_dir / "agent_ledger.json")["summary"]
        lines.extend(["", "## External Agent budget", "",
                      f"- Invocations reserved across recovery branches: {accounting['invocations_reserved']}",
                      f"- Command timeout seconds reserved: {accounting['seconds_reserved']}",
                      "- Internal model requests, tokens and monetary charges are unavailable; configure spending limits in your Agent/provider."])
    lines.extend(["", "## Evidence files", ""])
    for key, value in (log.get("key_outputs") or {}).items():
        if value:
            lines.append(f"- {key}: `{value}`")
    report_path = run_dir / "MIMO_RUN_SUMMARY.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[RESULT] {verdict}")
    print(f"[REPORT] {report_path}")
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def command_export(args: argparse.Namespace) -> int:
    from mimo_harness.bundles import create_bundle
    from mimo_harness.evidence import RunReader
    root = _resolve(Path.cwd(), args.run)
    if not root.is_dir():
        raise ValueError(f"Run directory does not exist: {root}")
    output = _resolve(Path.cwd(), args.output)
    if output == root or root in output.parents:
        raise ValueError("Export outside the original run directory to preserve its evidence.")
    try:
        with RunLock(root):
            decision = create_bundle(RunReader(root), output)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed run evidence; no export was published.") from exc
    print(f"[EXPORT] {output}")
    print(f"[GATE] {'APPROVED' if decision['allowed'] else 'BLOCKED'} — {decision['verdict']}")
    print("[NOTE] Export success is not gate approval. Use mimo gate for an automation exit code.")
    return 0


def command_gate(args: argparse.Namespace) -> int:
    import zipfile
    import zlib
    from mimo_harness.bundles import BundleReader
    from mimo_harness.evidence import RunReader, inspect_evidence
    from mimo_harness.manifests import file_digest
    try:
        expected = dict(expect_base=args.expect_base,
                        expect_patch_sha256=file_digest(_resolve(Path.cwd(), args.expect_patch)) if args.expect_patch else None)
        if args.bundle:
            with BundleReader(_resolve(Path.cwd(), args.bundle)) as reader:
                decision = inspect_evidence(reader, **expected)["gate"]
        else:
            root = _resolve(Path.cwd(), args.run)
            if not root.is_dir():
                raise ValueError(f"Run directory does not exist: {root}")
            with RunLock(root):
                decision = inspect_evidence(RunReader(root), **expected)["gate"]
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError,
            EOFError, zlib.error, zipfile.BadZipFile) as exc:
        decision = {"schema_version": 1, "allowed": False, "verdict": "ERROR", "exit_code": 4,
                    "reason_codes": ["EVIDENCE_INVALID"], "note": str(exc)}
    if args.json:
        print(_json_dump(decision))
    else:
        print(f"[GATE] {'APPROVED' if decision['allowed'] else 'BLOCKED'} — {decision['verdict']}")
        print(f"[REASONS] {', '.join(decision['reason_codes'])}")
        if decision.get("note"):
            print(f"[DETAIL] {decision['note']}")
    return int(decision["exit_code"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimo",
        description="Installable CLI for evidence-backed repository and patch verification.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        default=CONFIG_FILENAME,
        help=f"Project configuration path (default: {CONFIG_FILENAME})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create an explicit project and model configuration.")
    init.add_argument("--project", default=".", help="Repository/project directory")
    init.add_argument("--mode", choices=("offline", "api"), default="offline")
    init.add_argument("--force", action="store_true", help="Replace generated config files")
    init.set_defaults(handler=command_init)

    doctor = sub.add_parser("doctor", help="Check the environment without running the harness.")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics")
    doctor.add_argument("--agent-config", help="Also preflight an external CLI Agent without invoking it")
    doctor.set_defaults(handler=command_doctor)

    run = sub.add_parser("run", help="Run repository scanning, agents, verification, and reports.")
    run.add_argument("--project", help="Override the configured project directory")
    run.add_argument("--name", help="Stable output run name")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Call configured model APIs")
    mode.add_argument("--offline", action="store_true", help="Force explicit offline/mock mode")
    patches = run.add_mutually_exclusive_group()
    patches.add_argument("--apply-patches", action="store_true")
    patches.add_argument("--no-apply-patches", action="store_true")
    tests = run.add_mutually_exclusive_group()
    tests.add_argument("--run-tests", action="store_true", help="Execute detected project tests")
    tests.add_argument("--no-run-tests", action="store_true")
    run.add_argument("--limit-tasks", type=_positive_int, help="Override task limit")
    run.add_argument("--max-files", type=_positive_int, help="Override scan limit")
    run.set_defaults(handler=command_run)

    verify = sub.add_parser("verify", help="Verify one UTF-8 unified diff against an isolated baseline.")
    verify.add_argument("--project", help="Override the configured project directory")
    verify.add_argument("--patch", required=True, help="Unified diff file")
    verify.add_argument("--base", default="HEAD", help="Git commit/ref, or WORKTREE")
    verify.add_argument("--name", help="Stable output run name")
    verify.add_argument(
        "--task-type",
        choices=("code_refactor", "bug_fix", "test_generation", "patch_generation"),
        default="code_refactor",
    )
    verify.add_argument("--run-tests", action="store_true", help="Execute detected project tests")
    verify.set_defaults(handler=command_verify)

    from mimo_harness.repair import command_repair
    repair = sub.add_parser("repair", help="Run a bounded external CLI Agent repair loop with fixed per-case acceptance.")
    repair.add_argument("--project", help="Override the configured project directory")
    repair.add_argument("--agent-config", required=True, help="Versioned Agent argv/execution/budget JSON")
    repair.add_argument("--task-file", required=True, help="UTF-8 defect description (at most 64 KiB)")
    repair.add_argument("--base", default="HEAD", help="Git commit/ref, or WORKTREE")
    repair.add_argument("--max-rounds", type=_positive_int, default=3, help="Maximum proposal rounds (1–100)")
    repair.add_argument("--name", help="Stable output run name")
    repair.set_defaults(handler=command_repair)

    from mimo_harness.codex_adapter import command_agent_init
    agent_init = sub.add_parser("agent-init", help="Create a pinned, ready-to-use Codex Agent profile without a model call.")
    agent_init.add_argument("--model", required=True, help="Explicit model identifier to use for real execution")
    agent_init.add_argument("--codex", default="codex", help="Installed Codex executable or npm shim")
    agent_init.add_argument("--output", default=".mimo/codex-agent.json", help="New Agent profile; existing files are preserved")
    agent_init.set_defaults(handler=command_agent_init)

    from mimo_harness.real_cases import CASES, command_case_init, command_case_check
    case_init = sub.add_parser("case-init", help="Prepare a pinned real upstream defect and fixed independent acceptance.")
    case_init.add_argument("--case", choices=sorted(CASES), required=True)
    case_init.add_argument("--output", required=True, help="New case directory; existing directories are preserved")
    case_init.set_defaults(handler=command_case_init)
    case_check = sub.add_parser("case-check", help="Verify upstream reference/regression controls, recovery and portable gates.")
    case_check.add_argument("--directory", required=True, help="Prepared case directory")
    case_check.add_argument("--name", help="New acceptance attempt name")
    case_check.set_defaults(handler=command_case_check)

    from mimo_harness.product_acceptance import command_trial_init, command_trial_run, command_walkthrough_init, command_readiness
    trial_init = sub.add_parser("trial-init", help="Prepare a live-model trial plan without calling a model.")
    trial_init.add_argument("--agent-config", required=True)
    trial_init.add_argument("--output", default=".mimo/live-trial-plan.json")
    trial_init.set_defaults(handler=command_trial_init)
    trial_run = sub.add_parser("trial-run", help="Execute a reviewed live trial with explicit allowance and provider cap declaration.")
    trial_run.add_argument("--plan", required=True)
    trial_run.add_argument("--output", required=True, help="New trial directory; existing evidence is preserved")
    trial_run.set_defaults(handler=command_trial_run)
    walkthrough = sub.add_parser("walkthrough-init", help="Create an independent-user guide and blank feedback record.")
    walkthrough.add_argument("--output", required=True)
    walkthrough.set_defaults(handler=command_walkthrough_init)
    readiness = sub.add_parser("readiness", help="Check source-bound product evidence; missing external gates remain pending.")
    readiness.add_argument("--repository", default=".")
    readiness.add_argument("--trial", help="Completed trial.json")
    readiness.add_argument("--feedback", action="append", help="Completed independent-user feedback.json; repeat for each tester")
    readiness.add_argument("--license-id", help="Maintainer-selected license identifier; requires a repository LICENSE file")
    readiness.add_argument("--ci-record", help="Source-bound final CI acceptance JSON")
    readiness.set_defaults(handler=command_readiness)

    resume = sub.add_parser("resume", help="Recover into a new run; validate inputs and rerun all checks.")
    resume.add_argument("--run", required=True, help="Original run directory with run_manifest.json")
    resume.add_argument("--name", help="New output run name; the original is preserved")
    resume.set_defaults(handler=command_resume)

    report = sub.add_parser("report", help="Generate a concise report for a completed run.")
    report.add_argument("--run", help="Run directory; defaults to the latest completed run")
    report.set_defaults(handler=command_report)

    export = sub.add_parser("export", help="Export a hash-indexed offline HTML/JSON/JUnit evidence ZIP.")
    export.add_argument("--run", required=True, help="Run directory")
    export.add_argument("--output", required=True, help="New ZIP path outside the original run")
    export.set_defaults(handler=command_export)

    gate = sub.add_parser("gate", help="Strict automation gate: only an intact, recomputed PASS succeeds.")
    location = gate.add_mutually_exclusive_group(required=True)
    location.add_argument("--run", help="Run directory")
    location.add_argument("--bundle", help="Portable evidence ZIP (no extraction or project execution)")
    gate.add_argument("--expect-base", help="Required resolved Git base SHA")
    gate.add_argument("--expect-patch", help="Patch file whose exact bytes must match the recorded input")
    gate.add_argument("--json", action="store_true", help="Emit a machine-readable gate decision")
    gate.set_defaults(handler=command_gate)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("[RESULT] CANCELLED", file=sys.stderr)
        return 130
    except (ValueError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CODES[ERROR]


if __name__ == "__main__":
    raise SystemExit(main())

