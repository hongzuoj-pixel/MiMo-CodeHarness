"""Validation script for MiMo-CodeHarness v0.2 outputs.

It checks that the new v0.2 pipeline can run in offline dry-run mode, produces
all expected artifacts, and that critical JSON/CSV/HTML outputs are present.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def assert_exists(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise AssertionError(f"Missing or empty output: {path}")


def main() -> None:
    cmd = [
        sys.executable,
        str(ROOT / "src" / "run_mimo_codeharness_v02.py"),
        "--project", str(ROOT / "demo_harmony_project"),
        "--name", "validation_v02",
        "--config", str(ROOT / "config" / "api_models_mimo.json"),
        "--limit-tasks", "6",
        "--apply-patches",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    start = proc.stdout.find("{")
    data = json.loads(proc.stdout[start:])
    out_dir = Path(data["output_dir"])
    for name in [
        "repo_summary.json",
        "dependencies.csv",
        "tasks.json",
        "model_outputs.jsonl",
        "patch_results.csv",
        "build_test_logs/build_test_summary.json",
        "evaluation_scores.csv",
        "evaluation_summary.json",
        "token_usage.csv",
        "dashboard.html",
        "TECHNICAL_REPORT.md",
        "PAPER_DRAFT.md",
        "run_log.json",
    ]:
        assert_exists(out_dir / name)
    summary = json.loads((out_dir / "evaluation_summary.json").read_text(encoding="utf-8"))
    build = json.loads((out_dir / "build_test_logs" / "build_test_summary.json").read_text(encoding="utf-8"))
    if summary.get("run_count", 0) < 1:
        raise AssertionError("No model runs were scored.")
    if not build.get("all_critical_ok"):
        raise AssertionError("Critical build/test checks did not pass in offline validation.")
    print(json.dumps({
        "validated": True,
        "output_dir": str(out_dir),
        "run_count": summary.get("run_count"),
        "overall_avg_score": summary.get("overall_avg_score"),
        "total_tokens_est": summary.get("total_tokens_est"),
        "build_all_critical_ok": build.get("all_critical_ok"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
