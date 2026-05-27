"""Score model/agent outputs for HarmonyOS Code Agent benchmark tasks.

This scorer is intentionally lightweight and reproducible for classroom evaluation:
- It does not require external APIs.
- It reads the real benchmark tasks and the model output JSONL.
- It scores whether an Agent response covers key evaluation dimensions:
  requirement understanding, file location, dependency awareness, modification plan,
  test plan, and risk/clarification awareness.

For strict industrial evaluation, this module can be replaced or extended with:
- real code patch application,
- DevEco/hvigor build logs,
- test execution logs,
- human review labels.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_OUTPUTS = Path("outputs/multi_model_api_outputs.jsonl")
DEFAULT_TASKS = Path("outputs/benchmark_tasks.json")
DEFAULT_SCORE_CSV = Path("outputs/model_score_matrix.csv")
DEFAULT_SCORE_JSON = Path("outputs/model_score_summary.json")


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    path = Path(path)
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_csv(path: str | Path, rows: List[Dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def contains_any(text: str, words: List[str]) -> bool:
    lower = text.lower()
    return any(w.lower() in lower for w in words)


def score_record(record: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    content = str(record.get("content") or "")
    status = record.get("status", "")
    related_files = [str(x) for x in task.get("related_files", [])]
    file_hits = sum(1 for f in related_files if f and (f in content or Path(f).name in content))
    file_hit_ratio = file_hits / max(len(related_files), 1)

    # Basic dimensions. Scores intentionally sum to 100.
    requirement_understanding = 15 if contains_any(content, ["需求", "理解", "goal", "objective", "任务"]) else 8
    file_location = int(round(20 * min(file_hit_ratio, 1.0)))
    dependency_awareness = 15 if contains_any(content, ["依赖", "调用", "import", "call", "跨文件", "模块"]) else 6
    modification_plan = 25 if contains_any(content, ["修改", "方案", "patch", "实现", "fix", "change"]) else 10
    test_plan = 15 if contains_any(content, ["测试", "验证", "compile", "build", "unit test", "用例"]) else 6
    risk_awareness = 10 if contains_any(content, ["风险", "补充", "clarify", "注意", "fallback", "边界"]) else 4

    if status != "completed":
        requirement_understanding = file_location = dependency_awareness = modification_plan = test_plan = risk_awareness = 0

    total = requirement_understanding + file_location + dependency_awareness + modification_plan + test_plan + risk_awareness
    return {
        "task_id": record.get("task_id"),
        "provider_name": record.get("provider_name"),
        "model": record.get("model"),
        "status": status,
        "dry_run": record.get("dry_run"),
        "response_chars": record.get("response_chars"),
        "time_seconds": record.get("time_seconds"),
        "related_file_count": len(related_files),
        "related_file_hits": file_hits,
        "requirement_understanding": requirement_understanding,
        "file_location": file_location,
        "dependency_awareness": dependency_awareness,
        "modification_plan": modification_plan,
        "test_plan": test_plan,
        "risk_awareness": risk_awareness,
        "total_score": total,
        "pass": total >= 70 and status == "completed",
        "error": record.get("error", ""),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_provider: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_provider.setdefault(str(row.get("provider_name")), []).append(row)

    provider_summary = []
    for provider, items in sorted(by_provider.items()):
        count = len(items)
        avg_score = round(sum(float(i.get("total_score", 0)) for i in items) / max(count, 1), 2)
        pass_rate = round(sum(1 for i in items if i.get("pass")) / max(count, 1), 4)
        completed_rate = round(sum(1 for i in items if i.get("status") == "completed") / max(count, 1), 4)
        avg_time = round(sum(float(i.get("time_seconds") or 0) for i in items) / max(count, 1), 4)
        provider_summary.append({
            "provider_name": provider,
            "run_count": count,
            "average_score": avg_score,
            "pass_rate": pass_rate,
            "completed_rate": completed_rate,
            "average_time_seconds": avg_time,
        })

    return {
        "run_count": len(rows),
        "provider_count": len(by_provider),
        "provider_summary": provider_summary,
        "scoring_note": (
            "This is a reproducible heuristic score for comparing Agent outputs. "
            "For strict industrial use, combine it with real patch application, build/test logs, and human labels."
        ),
    }


def score_outputs(outputs_jsonl: str | Path, tasks_json: str | Path, out_csv: str | Path, out_json: str | Path) -> Dict[str, Any]:
    records = load_jsonl(outputs_jsonl)
    tasks = load_json(tasks_json)
    task_map = {str(t.get("task_id")): t for t in tasks}

    rows: List[Dict[str, Any]] = []
    for record in records:
        task_id = str(record.get("task_id"))
        task = task_map.get(task_id, {})
        rows.append(score_record(record, task))

    write_csv(out_csv, rows)
    summary = summarize(rows)
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Score model outputs against HarmonyOS benchmark tasks.")
    parser.add_argument("--outputs", default=str(DEFAULT_OUTPUTS), help="Model outputs JSONL")
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS), help="Benchmark tasks JSON")
    parser.add_argument("--out-csv", default=str(DEFAULT_SCORE_CSV), help="Scored model matrix CSV")
    parser.add_argument("--out-json", default=str(DEFAULT_SCORE_JSON), help="Scored model summary JSON")
    args = parser.parse_args()

    summary = score_outputs(args.outputs, args.tasks, args.out_csv, args.out_json)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
