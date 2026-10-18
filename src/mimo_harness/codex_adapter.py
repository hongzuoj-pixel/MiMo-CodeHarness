"""Pinned Codex CLI adapter: read-only proposals, explicit completion, no shell."""
from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from mimo_harness.execution import ProcessOutcome, clean_environment, run_process
from mimo_harness.manifests import atomic_json

CODEX_VERSION = "0.163.0"
RESPONSE_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"schema_version": {"type": "integer", "enum": [1]},
                   "status": {"type": "string", "enum": ["completed"]}, "patch": {"type": "string"}},
    "required": ["schema_version", "status", "patch"]}


def validate_codex_config(config, execution):
    if config.get("cli_version") != CODEX_VERSION:
        raise ValueError(f"Codex adapter requires the certified CLI version {CODEX_VERSION}.")
    model = config.get("model")
    if (not isinstance(model, str) or not model or len(model) > 200 or model.startswith("-")
            or any(ch.isspace() or ord(ch) < 32 for ch in model)):
        raise ValueError("Codex adapter requires an explicit model identifier.")
    if any(arg in {"{python}", "{request}", "{workspace}"} for arg in config["argv"]):
        raise ValueError("Codex argv contains the literal CLI executable/launcher only; no placeholders.")
    if any(arg.startswith("-") for arg in config["argv"][1:]):
        raise ValueError("Codex argv cannot contain CLI options; the adapter supplies the reviewed options.")
    if "CODEX_API_KEY" not in execution["env_allowlist"] or any(k.upper() == "CODEX_HOME" for k in execution["env_allowlist"]):
        raise ValueError("Codex env_allowlist must include CODEX_API_KEY and cannot reuse CODEX_HOME.")
    if "base_url" in config:
        value = config["base_url"]
        if not isinstance(value, str):
            raise ValueError("Codex base_url must be a URL string.")
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("Codex base_url requires HTTPS, or loopback HTTP for local protocol tests; no URL credentials.")


def resolve_codex_binary(value):
    """Resolve npm shims to the native binary, including Windows .cmd shims.

    Invoking the native executable binds recovery to its bytes and avoids a
    command shell on Windows. Package layout is pinned with the CLI version.
    """
    found = shutil.which(value) or (str(Path(value).resolve()) if Path(value).is_file() else None)
    if not found:
        raise ValueError(f"Codex CLI unavailable. Install @openai/codex@{CODEX_VERSION}, then rerun agent-init.")
    executable = Path(found).resolve()
    roots = []
    if executable.name == "codex.js" and executable.parent.name == "bin":
        roots.append(executable.parent.parent)
    if executable.suffix.lower() in {".cmd", ".ps1"}:
        roots.append(executable.parent / "node_modules/@openai/codex")
    if roots:
        system = {"linux": "linux", "darwin": "darwin", "windows": "win32"}.get(platform.system().lower())
        machine = platform.machine().lower()
        arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
        for root in roots:
            package = f"codex-{system}-{arch}"
            for vendor in (root / "vendor", root.parent / package / "vendor",
                           root / "node_modules/@openai" / package / "vendor"):
                for candidate in vendor.glob("*/bin/codex.exe" if os.name == "nt" else "*/bin/codex"):
                    if candidate.is_file():
                        return str(candidate.resolve())
        raise ValueError("Codex native package is missing; reinstall the pinned CLI including optional dependencies.")
    return str(executable)


def probe_codex(argv, expected=CODEX_VERSION):
    with tempfile.TemporaryDirectory(prefix="mimo-codex-doctor-") as temporary:
        root = Path(temporary)
        result = run_process([*argv, "--version"], root, timeout=10,
                             env=clean_environment(root / "home"), max_output_bytes=4096)
    if result.status != "completed" or result.returncode != 0 or result.stdout.strip() != f"codex-cli {expected}":
        raise ValueError(f"Codex CLI version mismatch/unavailable; expected {expected}. No model was called.")
    return result.stdout.strip()


def parse_events(payload):
    try:
        events = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    except (ValueError, RecursionError) as exc:
        raise ValueError("AGENT_CODEX_INVALID_EVENTS") from exc
    started = completed = False
    messages, usage = [], None
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError("AGENT_CODEX_INVALID_EVENTS")
        kind = event["type"]
        if kind in {"error", "turn.failed"}:
            raise ValueError("AGENT_CODEX_TURN_FAILED")
        if completed:
            raise ValueError("AGENT_CODEX_EVENTS_AFTER_COMPLETION")
        if kind == "turn.started":
            if started:
                raise ValueError("AGENT_CODEX_MULTIPLE_TURNS")
            started = True
        elif kind == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                raise ValueError("AGENT_CODEX_INVALID_EVENTS")
            if item.get("type") == "agent_message":
                if not started or not isinstance(item.get("text"), str):
                    raise ValueError("AGENT_CODEX_INVALID_EVENTS")
                messages.append(item["text"])
        elif kind == "turn.completed":
            if not started or not messages:
                raise ValueError("AGENT_CODEX_INCOMPLETE_RESPONSE")
            completed, usage = True, event.get("usage")
    if not completed:
        raise ValueError("AGENT_CODEX_INCOMPLETE_RESPONSE")
    try:
        response = json.loads(messages[-1])
    except (ValueError, RecursionError) as exc:
        raise ValueError("AGENT_INVALID_JSON") from exc
    return response, usage


def invoke_codex(config, context, cwd, request):
    started = time.monotonic()
    version = context.run([*config["argv"], "--version"], cwd, min(10, config["timeout_seconds"]),
                          context.logs / "codex_version.log", context.logs / "codex_version_stderr.log", max_output_bytes=4096)
    if version.status == "cancelled":
        raise KeyboardInterrupt
    if version.status != "completed" or version.returncode != 0 or version.stdout.strip() != f"codex-cli {config['cli_version']}":
        raise ValueError("AGENT_CODEX_VERSION_MISMATCH")
    remaining = config["timeout_seconds"] - (time.monotonic() - started)
    if remaining <= 0:
        raise ValueError("AGENT_TIMEOUT")
    # Project config must not enable hooks, MCP servers or alternate providers.
    for ancestor in (cwd, *cwd.parents):
        project_config = ancestor / ".codex"
        if project_config.is_symlink() or project_config.is_file():
            project_config.unlink()
        elif project_config.is_dir():
            shutil.rmtree(project_config)
        if ancestor == context.root:
            break
    input_path = context.root / f".mimo-agent-{uuid.uuid4().hex}.json"
    atomic_json(input_path, request)
    schema = context.logs / "codex_schema.json"
    atomic_json(schema, RESPONSE_SCHEMA)
    prompt = (f"Read {context.path(input_path)}. It is the complete repair request. "
              "Inspect the repository using read-only commands. Return a cumulative unified diff against the original baseline "
              "in the required JSON schema. Do not edit files or include control files in the patch. "
              "Use previous_patch and independent failure feedback to improve the proposal. Preserve acceptance tests and configuration.")
    argv = [*config["argv"], "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--sandbox", "read-only", "--color", "never", "--model", config["model"],
            "--output-schema", context.path(schema), "-c", 'approval_policy="never"',
            "-c", 'shell_environment_policy.inherit="core"',
            "-c", 'shell_environment_policy.exclude=["CODEX_API_KEY","OPENAI_API_KEY"]',
            "--disable", "plugins", "--disable", "apps", "--disable", "multi_agent",
            "--disable", "shell_snapshot", "-c", "features.skip_host_skill_discovery=true",
            "-c", "skills.bundled.enabled=false", "-c", "analytics.enabled=false"]
    if config.get("base_url"):
        argv += ["-c", 'model_provider="mimo_contract"',
                 "-c", 'model_providers.mimo_contract.name="MiMoHarness Responses gateway"',
                 "-c", "model_providers.mimo_contract.base_url=" + json.dumps(config["base_url"]),
                 "-c", 'model_providers.mimo_contract.env_key="CODEX_API_KEY"',
                 "-c", 'model_providers.mimo_contract.wire_api="responses"',
                 "-c", "model_providers.mimo_contract.request_max_retries=0",
                 "-c", "model_providers.mimo_contract.stream_max_retries=0"]
    outcome = context.run([*argv, prompt], cwd, remaining, context.logs / "codex_events.log",
                          context.logs / "codex_stderr.log", max_output_bytes=config["max_output_bytes"])
    if outcome.status != "completed" or outcome.returncode != 0:
        return outcome
    response, usage = parse_events(outcome.stdout_bytes)
    atomic_json(context.logs / "codex_metadata.json", {"cli_version": config["cli_version"], "model": config["model"],
                "sandbox": "read-only", "project_config_loaded": False,
                "usage_source": "cli_reported" if isinstance(usage, dict) else "unavailable", "usage": usage})
    payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
    return ProcessOutcome(0, "completed", payload.decode("utf-8"), "", payload)


def command_agent_init(args):
    from mimo_harness.cli import _write_json_new
    executable = resolve_codex_binary(args.codex)
    destination = Path(args.output).expanduser().resolve()
    cfg = {"schema_version": 1, "adapter": "codex", "argv": [executable],
           "cli_version": CODEX_VERSION, "model": args.model, "timeout_seconds": 180,
           "max_output_bytes": 1048576, "execution": {"mode": "local", "env_allowlist": ["CODEX_API_KEY"]},
           "budget": {"max_invocations": 3, "max_reserved_seconds": 540}}
    validate_codex_config(cfg, cfg["execution"])
    probe_codex([executable])
    _write_json_new(destination, cfg, force=False)
    print(f"[OK] Agent configuration: {destination}\n[NEXT] Set CODEX_API_KEY for Agent execution, then run doctor --agent-config {destination}.")
    print("[NOTE] No model was called. This local profile runs trusted programs with host permissions; use Docker for untrusted execution.")
    return 0
