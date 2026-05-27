"""Universal multi-model API runner for HarmonyOS Code Agent benchmark tasks.

This module is designed for classroom-safe and production-style extension:
- No API key is stored in source code.
- OpenAI-compatible providers can be added by editing config/api_models.json.
- Non-compatible providers can be integrated through anthropic or generic_http adapters.
- Default mode can run dry-run/mock tests without network or secrets.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "api_models.json"
DEFAULT_TASKS = ROOT / "outputs" / "benchmark_tasks.json"
DEFAULT_JSONL = ROOT / "outputs" / "multi_model_api_outputs.jsonl"
DEFAULT_CSV = ROOT / "outputs" / "multi_model_api_matrix.csv"


@dataclass
class ModelRunResult:
    task_id: str
    provider_name: str
    provider_type: str
    model: str
    status: str
    dry_run: bool
    time_seconds: float
    response_chars: int
    output_file: str
    error: str = ""


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_jsonl(path: str | Path, records: List[Dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_csv(path: str | Path, rows: List[Dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def build_prompt(task: Dict[str, Any]) -> str:
    round_prompts = task.get("round_prompts") or task.get("rounds") or []
    scoring_points = task.get("scoring_points") or {}
    objective_checks = task.get("objective_checks") or []
    related_files = task.get("related_files") or []
    return f"""
你是一个 Code Agent，正在接受 HarmonyOS / ArkTS 工程复杂任务评测。
请根据任务信息输出结构化回答，不要编造未给出的文件内容。

【任务编号】{task.get('task_id')}
【任务类型】{task.get('task_type') or task.get('scenario_type')}
【难度】{task.get('difficulty')}
【任务描述】{task.get('description')}
【相关文件】{json.dumps(related_files, ensure_ascii=False)}
【期望行为】{task.get('expected_behavior')}
【多轮交互提示】{json.dumps(round_prompts, ensure_ascii=False)}
【评分点】{json.dumps(scoring_points, ensure_ascii=False)}
【客观检查项】{json.dumps(objective_checks, ensure_ascii=False)}

请按以下格式回答：
1. 需求理解
2. 相关文件定位
3. 修改方案
4. 测试与验证建议
5. 风险点与需要用户补充的信息
""".strip()


def _http_post_json(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _extract_path(data: Any, path: List[Any]) -> Any:
    current = data
    for key in path:
        current = current[key]
    return current


def _mock_response(task: Dict[str, Any], model_cfg: Dict[str, Any]) -> Dict[str, Any]:
    content = (
        f"Mock response for {task.get('task_id')}: locate related files, "
        f"modify {task.get('task_type')}, run compile/test checks, and summarize risks."
    )
    return {
        "provider": model_cfg.get("name", "MockAgent"),
        "model": model_cfg.get("model", "mock-code-agent"),
        "content": content,
        "raw": {"mock": True, "task_id": task.get("task_id")},
    }


def _normalize_openai_chat_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url



def call_model(task: Dict[str, Any], model_cfg: Dict[str, Any], global_cfg: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    provider_type = model_cfg.get("provider_type", "openai_compatible")
    prompt = build_prompt(task)
    timeout = int(model_cfg.get("timeout_seconds", global_cfg.get("default_timeout_seconds", 120)))
    max_tokens = int(model_cfg.get("max_tokens", global_cfg.get("default_max_tokens", 2000)))
    temperature = float(model_cfg.get("temperature", global_cfg.get("default_temperature", 0.2)))

    if dry_run or provider_type == "mock":
        return _mock_response(task, model_cfg)

    api_key_env = model_cfg.get("api_key_env")
    api_key = os.getenv(api_key_env or "")
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {api_key_env}")

    model = model_cfg.get("model")
    base_url = model_cfg.get("base_url")
    if not base_url:
        raise RuntimeError("Missing base_url in model config")

    if provider_type == "openai_compatible":
        auth_header = model_cfg.get("auth_header", "authorization_bearer")
        if auth_header == "api-key":
            headers = {"api-key": api_key, "Content-Type": "application/json"}
        else:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        token_field = model_cfg.get("max_tokens_field", "max_tokens")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a rigorous code-agent evaluation assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            token_field: max_tokens,
        }
        raw = _http_post_json(_normalize_openai_chat_url(base_url), headers, body, timeout)
        content = raw["choices"][0]["message"]["content"]
        return {"provider": model_cfg.get("name"), "model": model, "content": content, "raw": raw}

    if provider_type == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": model_cfg.get("anthropic_version", "2023-06-01"),
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = _http_post_json(base_url, headers, body, timeout)
        parts = raw.get("content", [])
        content = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict))
        return {"provider": model_cfg.get("name"), "model": model, "content": content, "raw": raw}

    if provider_type == "generic_http":
        headers_template = model_cfg.get("headers", {})
        headers = {
            k: str(v).replace("${API_KEY}", api_key)
            for k, v in headers_template.items()
        }
        body_template = model_cfg.get("body_template", {})
        replacements = {
            "${MODEL}": str(model),
            "${PROMPT}": prompt,
            "${TEMPERATURE}": str(temperature),
            "${MAX_TOKENS}": str(max_tokens),
        }

        def replace_value(value: Any) -> Any:
            if isinstance(value, str):
                for old, new in replacements.items():
                    value = value.replace(old, new)
                return value
            if isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace_value(v) for v in value]
            return value

        body = replace_value(body_template)
        raw = _http_post_json(base_url, headers, body, timeout)
        path = model_cfg.get("response_path", ["choices", 0, "message", "content"])
        content = _extract_path(raw, path)
        return {"provider": model_cfg.get("name"), "model": model, "content": str(content), "raw": raw}

    raise RuntimeError(f"Unsupported provider_type: {provider_type}")


def run_matrix(config_path: str | Path, task_path: str | Path, out_jsonl: str | Path, out_csv: str | Path, limit_tasks: Optional[int], execute: bool, only_enabled: bool = True) -> List[ModelRunResult]:
    config = _load_json(config_path)
    tasks = _load_json(task_path)
    if limit_tasks is not None:
        tasks = tasks[:limit_tasks]
    models = config.get("models", [])
    if only_enabled:
        models = [m for m in models if m.get("enabled", False)]

    all_records: List[Dict[str, Any]] = []
    results: List[ModelRunResult] = []
    dry_run = not execute

    for task in tasks:
        for model_cfg in models:
            started = time.perf_counter()
            status = "completed"
            error = ""
            content = ""
            raw: Dict[str, Any] = {}
            try:
                response = call_model(task, model_cfg, config, dry_run=dry_run)
                content = response.get("content", "")
                raw = response.get("raw", {})
            except Exception as exc:  # keep matrix robust; one failed provider cannot break all tests
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
                raw = {"traceback": traceback.format_exc(limit=3)}
            elapsed = round(time.perf_counter() - started, 4)
            record = {
                "task_id": task.get("task_id"),
                "provider_name": model_cfg.get("name"),
                "provider_type": model_cfg.get("provider_type"),
                "model": model_cfg.get("model"),
                "status": status,
                "dry_run": dry_run,
                "time_seconds": elapsed,
                "response_chars": len(content),
                "content": content,
                "error": error,
                "raw": raw,
            }
            all_records.append(record)
            results.append(ModelRunResult(
                task_id=str(task.get("task_id")),
                provider_name=str(model_cfg.get("name")),
                provider_type=str(model_cfg.get("provider_type")),
                model=str(model_cfg.get("model")),
                status=status,
                dry_run=dry_run,
                time_seconds=elapsed,
                response_chars=len(content),
                output_file=str(out_jsonl),
                error=error,
            ))

    _write_jsonl(out_jsonl, all_records)
    _write_csv(out_csv, [asdict(r) for r in results])
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run benchmark tasks against configurable LLM API providers.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to api_models.json")
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS), help="Path to benchmark_tasks.json")
    parser.add_argument("--out-jsonl", default=str(DEFAULT_JSONL), help="Output JSONL file")
    parser.add_argument("--out-csv", default=str(DEFAULT_CSV), help="Output CSV summary")
    parser.add_argument("--limit", type=int, default=2, help="Limit number of tasks for quick tests")
    parser.add_argument("--execute", action="store_true", help="Actually call external APIs. Without this flag, dry-run/mock mode is used.")
    parser.add_argument("--include-disabled", action="store_true", help="Also run disabled model entries")
    args = parser.parse_args()

    results = run_matrix(
        config_path=args.config,
        task_path=args.tasks,
        out_jsonl=args.out_jsonl,
        out_csv=args.out_csv,
        limit_tasks=args.limit,
        execute=args.execute,
        only_enabled=not args.include_disabled,
    )
    summary = {
        "config": args.config,
        "tasks": args.tasks,
        "out_jsonl": args.out_jsonl,
        "out_csv": args.out_csv,
        "execute": args.execute,
        "run_count": len(results),
        "completed_count": sum(r.status == "completed" for r in results),
        "failed_count": sum(r.status == "failed" for r in results),
        "providers": sorted({r.provider_name for r in results}),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
