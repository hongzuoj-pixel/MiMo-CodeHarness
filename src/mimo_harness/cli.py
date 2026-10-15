"""User-facing CLI for installing, diagnosing, running, and reporting the harness."""
from __future__ import annotations

import argparse
import io
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
from run_mimo_codeharness_v02 import (
    BuildTesterV02,
    ModelRunRecord,
    apply_patches,
    copy_worktree,
    ensure_dir,
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
    }


def default_model_config(mode: str = "offline") -> Dict[str, Any]:
    if mode == "api":
        return {
            "default_timeout_seconds": 180,
            "default_max_tokens": 4000,
            "default_temperature": 0.2,
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
                }
            ],
        }
    return {
        "default_timeout_seconds": 60,
        "default_max_tokens": 2000,
        "default_temperature": 0.2,
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

    model_path = _resolve(base, str(cfg.get("model_config", "")))
    model_cfg: Optional[Dict[str, Any]] = None
    try:
        model_cfg = _read_json(model_path)
        enabled = [m for m in model_cfg.get("models", []) if m.get("enabled", True)]
        if not enabled:
            raise ValueError("Model configuration contains no enabled models.")
        rows.append(
            Diagnostic("model_config", "ok", f"{model_path} ({len(enabled)} enabled)")
        )
    except (ValueError, AttributeError) as exc:
        rows.append(Diagnostic("model_config", "error", str(exc)))

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
    try:
        _write_json_new(config_path, project_cfg, args.force)
        _write_json_new(model_path, default_model_config(args.mode), args.force)
    except FileExistsError as exc:
        print(f"[ERROR] {exc} Use --force only when replacement is intentional.", file=sys.stderr)
        return EXIT_CODES[ERROR]

    print(f"[OK] Created {config_path}")
    print(f"[OK] Created {model_path}")
    if args.mode == "offline":
        print("[MODE] OFFLINE MOCK — no API key is required and no model API will be called.")
    else:
        print("[NEXT] Set MIMO_API_KEY in the environment, then run: mimo doctor")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    rows, _ = collect_diagnostics(config_path)
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
    if (output_root / name).exists():
        print(
            f"[ERROR] Run directory already exists: {output_root / name}. Choose a new --name.",
            file=sys.stderr,
        )
        return EXIT_CODES[ERROR]

    print(
        "[MODE] API execution — configured providers will be called."
        if execute
        else "[MODE] OFFLINE MOCK — no model API will be called."
    )
    try:
        log = run_harness(
            project=project,
            name=name,
            config_path=model_config,
            limit_tasks=args.limit_tasks or int(cfg.get("limit_tasks", 6)),
            max_files=args.max_files or int(cfg.get("max_files", 800)),
            execute=execute,
            apply_patch_flag=apply_patches_flag,
            run_build_tests=run_tests,
            output_root=output_root,
        )
    except Exception as exc:
        print(f"[ERROR] Harness execution failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CODES[ERROR]

    verification = log.get("verification_summary", {})
    print(f"[RESULT] {verification.get('overall_verdict', ERROR)}")
    print(f"[OUTPUT] {log.get('output_dir', '')}")
    print(f"[SUMMARY] {log.get('key_outputs', {}).get('verification_summary', '')}")
    return int(verification.get("exit_code", EXIT_CODES[ERROR]))


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
        copy_worktree(project, destination)
        return "WORKTREE"

    top = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        text=True,
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
        capture_output=True,
    )
    if resolved.returncode != 0:
        raise ValueError(f"Git base ref does not resolve to a commit: {base_ref}")
    archive = subprocess.run(
        ["git", "-C", str(project), "archive", "--format=tar", base_ref],
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
    run_dir = output_root / (args.name or _run_name("verify"))
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
        patch_text = patch_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print("[ERROR] Patch must be a UTF-8 unified diff.", file=sys.stderr)
        return EXIT_CODES[ERROR]

    ensure_dir(run_dir)
    try:
        resolved_base = _materialize_baseline(project, args.base, baseline_dir)
        baseline_summary = BuildTesterV02(
            baseline_dir, run_dir / "baseline"
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
            [record], baseline_dir, run_dir, actually_apply=True
        )[0]
        candidate_summary = None
        if patch_result.applied:
            candidate_summary = BuildTesterV02(
                Path(patch_result.worktree), run_dir / "candidate"
            ).run_all(run_external=args.run_tests)
        task = SimpleNamespace(task_id="PATCH", task_type=args.task_type)
        result = classify_candidate(task, patch_result, baseline_summary, candidate_summary)
        verification = summarize_verifications([result])
    except Exception as exc:
        print(f"[ERROR] Verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CODES[ERROR]

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


def _latest_run(output_root: Path) -> Optional[Path]:
    candidates = [p for p in output_root.iterdir() if p.is_dir()] if output_root.is_dir() else []
    candidates = [p for p in candidates if (p / "run_log.json").is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


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
    for key in ("PASS", "FAIL", "INSUFFICIENT_EVIDENCE", "ERROR", "NOT_APPLICABLE"):
        lines.append(f"| {key} | {counts.get(key, 0)} |")
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

    report = sub.add_parser("report", help="Generate a concise report for a completed run.")
    report.add_argument("--run", help="Run directory; defaults to the latest completed run")
    report.set_defaults(handler=command_report)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())

