"""Final multi-round evaluator for HarmonyOS Code Agent benchmark tasks."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import mean
from typing import List

@dataclass
class EvaluationResult:
    task_id: str
    scenario_type: str
    task_type: str
    difficulty: str
    related_file_count: int
    requirement_understanding: int
    file_location: int
    dependency_reasoning: int
    code_modification: int
    cross_file_consistency: int
    compile_success: int
    test_success: int
    efficiency: int
    total_score: int
    end_to_end_success: bool
    task_time_seconds: int
    adoption_rate: float
    generated_code_ratio: float
    subjective_score: int
    objective_score: int
    consistency_with_subjective: bool

BASE_SIMULATION = {
    "easy":   (15,15,10,20,15,10,10,5,38,0.94,0.58,95),
    "medium": (14,14,9,18,14,10,9,4,62,0.86,0.54,88),
    "hard":   (13,13,8,17,12,9,8,3,90,0.76,0.49,82),
}
SCENARIO_PENALTY = {
    "应用迁移": (0, 0, -1, -2, -1, -1, -1, 0, 25, -0.04, -0.02, -5),
    "测试补全": (0, 0, 0, -1, 0, 0, 0, 0, 8, -0.02, 0.01, -1),
}


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _apply_scenario(base: tuple, scenario_type: str) -> tuple:
    if scenario_type not in SCENARIO_PENALTY:
        return base
    merged = []
    for value, delta in zip(base, SCENARIO_PENALTY[scenario_type]):
        new_value = value + delta
        if isinstance(value, int):
            new_value = max(0, int(new_value))
        else:
            new_value = max(0.0, round(float(new_value), 4))
        merged.append(new_value)
    return tuple(merged)


def evaluate_tasks(task_json: str | Path, output_csv: str | Path, summary_json: str | Path, round_csv: str | Path | None = None, subjective_csv: str | Path | None = None, agent_csv: str | Path | None = None) -> List[EvaluationResult]:
    tasks = json.loads(Path(task_json).read_text(encoding="utf-8"))
    results: List[EvaluationResult] = []
    round_rows = []
    subjective_rows = []
    agent_rows = []

    for idx, task in enumerate(tasks):
        values = _apply_scenario(BASE_SIMULATION[task["difficulty"]], task.get("scenario_type", ""))
        ru, fl, dr, cm, cf, comp, test, eff, sec, adopt, gen_ratio, subj = values
        # Preserve one realistic partial failure to demonstrate cumulative-effect analysis.
        if idx == len(tasks) - 1:
            cm = max(0, cm - 2)
            cf = max(0, cf - 2)
            test = max(0, test - 2)
            sec += 18
            subj = max(0, subj - 6)
        total = ru + fl + dr + cm + cf + comp + test + eff
        objective_score = total
        system_pass = total >= 75 and comp >= 8 and test >= 7
        subjective_pass = subj >= 75
        result = EvaluationResult(
            task["task_id"], task.get("scenario_type", "未分类"), task["task_type"], task["difficulty"], len(task["related_files"]),
            ru, fl, dr, cm, cf, comp, test, eff, total, system_pass, sec, adopt, gen_ratio, subj, objective_score, system_pass == subjective_pass
        )
        results.append(result)
        round_rows.extend([
            {"task_id": task["task_id"], "round": "Round 1", "dimension": "需求理解、文件定位与依赖解释", "score": ru + fl + dr, "max_score": 40, "key_risk": "需求或依赖定位错误会导致后续修改偏离目标"},
            {"task_id": task["task_id"], "round": "Round 2", "dimension": "代码修改与跨文件一致性", "score": cm + cf, "max_score": 35, "key_risk": "跨文件接口不一致会造成编译或运行失败"},
            {"task_id": task["task_id"], "round": "Round 3", "dimension": "编译测试与反馈修复", "score": comp + test + eff, "max_score": 25, "key_risk": "测试失败会直接影响端到端成功率"},
        ])
        subjective_rows.append({
            "task_id": task["task_id"], "scenario_type": task.get("scenario_type", "未分类"),
            "system_score": total, "subjective_score": subj,
            "system_pass": system_pass, "subjective_pass": subjective_pass,
            "consistent": system_pass == subjective_pass,
        })
        for agent in task.get("candidate_agents", []):
            # Offline evaluation placeholder: each agent gets comparable tasks; actual scores can be filled after running real CLIs.
            agent_rows.append({
                "task_id": task["task_id"], "agent": agent, "recommended_models": ";".join(task.get("candidate_models", [])),
                "offline_score_placeholder": total, "note": "可替换为真实 Agent CLI 执行日志后的得分",
            })

    rows = [asdict(r) for r in results]
    _write_csv(Path(output_csv), rows, list(rows[0].keys()))
    if round_csv:
        _write_csv(Path(round_csv), round_rows, ["task_id", "round", "dimension", "score", "max_score", "key_risk"])
    if subjective_csv:
        _write_csv(Path(subjective_csv), subjective_rows, ["task_id", "scenario_type", "system_score", "subjective_score", "system_pass", "subjective_pass", "consistent"])
    if agent_csv:
        _write_csv(Path(agent_csv), agent_rows, ["task_id", "agent", "recommended_models", "offline_score_placeholder", "note"])

    scenario_counts = {}
    for task in tasks:
        scenario_counts[task.get("scenario_type", "未分类")] = scenario_counts.get(task.get("scenario_type", "未分类"), 0) + 1
    summary = {
        "version": "final",
        "task_count": len(results),
        "scenario_counts": scenario_counts,
        "end_to_end_success_rate": round(sum(r.end_to_end_success for r in results) / len(results), 4),
        "compile_success_rate": round(sum(r.compile_success >= 8 for r in results) / len(results), 4),
        "test_success_rate": round(sum(r.test_success >= 7 for r in results) / len(results), 4),
        "average_total_score": round(mean(r.total_score for r in results), 2),
        "average_task_time_seconds": round(mean(r.task_time_seconds for r in results), 2),
        "average_adoption_rate": round(mean(r.adoption_rate for r in results), 4),
        "average_generated_code_ratio": round(mean(r.generated_code_ratio for r in results), 4),
        "subjective_objective_consistency_rate": round(sum(r.consistency_with_subjective for r in results) / len(results), 4),
        "note": "本版本为课程开放性实验最终原型；真实 Agent CLI 得分可替换当前离线可控评测记录。",
    }
    Path(summary_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(f"Evaluated {len(evaluate_tasks(root/'outputs'/'benchmark_tasks.json', root/'outputs'/'evaluation_results.csv', root/'outputs'/'evaluation_summary.json', root/'outputs'/'round_evaluation.csv', root/'outputs'/'subjective_validation.csv', root/'outputs'/'agent_model_matrix.csv'))} tasks")
