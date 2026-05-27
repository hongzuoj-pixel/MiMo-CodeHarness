#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline validation tests for MiMo-CodeHarness scoring v0.3.

These tests do not prove the scoring system is perfect. They prove several
important invariants:
1. grounded, rubric-complete answers score higher than generic answers;
2. patch-required tasks penalize missing patches;
3. LLM/human evidence changes evidence_level;
4. failed model runs score zero;
5. no external dependencies are required.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scoring_v03 import score_outputs_v03


@dataclass
class Task:
    task_id: str
    task_type: str
    related_files: list
    scoring_points: list
    expected_output: str = ""
    description: str = ""


@dataclass
class Run:
    task_id: str
    provider_name: str
    model: str
    status: str
    dry_run: bool
    time_seconds: float
    input_tokens_est: int
    output_tokens_est: int
    total_tokens_est: int
    content: str
    error: str = ""


@dataclass
class Patch:
    task_id: str
    provider_name: str
    status: str


def main() -> None:
    build_ok = {"all_critical_ok": True, "failed_count": 0, "warning_count": 0, "skipped_count": 0, "run_external": False}
    tasks = [
        Task("T001", "repo_understanding", ["src/main.py", "README.md"], ["项目目标", "核心文件", "依赖关系"], description="Explain repo."),
        Task("T005", "code_refactor", ["src/main.py"], ["patch 格式", "最小修改", "可回滚", "风险说明"], expected_output="unified diff patch", description="Output a patch."),
    ]
    good = Run("T001", "MiMo", "mimo", "completed", False, 1.0, 100, 100, 200,
               "1. 需求理解：项目目标是 CLI 工具。2. 相关文件定位：src/main.py 和 README.md。3. 依赖关系：main.py import config。4. 测试：pytest。5. 风险：路径边界。")
    generic = Run("T001", "MiMo", "mimo", "completed", False, 1.0, 100, 50, 150,
                  "这个项目很好，建议优化结构，增加测试，注意风险。")
    patch_good = Run("T005", "MiMo", "mimo", "completed", False, 1.0, 100, 200, 300,
                     "src/main.py 最小修改，可回滚，风险低。```diff\ndiff --git a/src/main.py b/src/main.py\n--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('x')\n+print('ok')\n```")
    patch_missing = Run("T005", "MiMo", "mimo", "completed", False, 1.0, 100, 70, 170,
                        "建议重构 src/main.py，注意风险，但这里不给 patch。")
    failed = Run("T001", "MiMo", "mimo", "error", False, 0.2, 0, 0, 0, "", "boom")

    rows = score_outputs_v03(tasks, [good, generic, patch_good, patch_missing, failed], [Patch("T005", "MiMo", "checked")], build_ok)
    by_content = rows
    good_score = rows[0]["final_score"]
    generic_score = rows[1]["final_score"]
    patch_good_score = rows[2]["final_score"]
    patch_missing_score = rows[3]["final_score"]
    failed_score = rows[4]["final_score"]
    assert good_score > generic_score, (good_score, generic_score)
    assert patch_good_score > patch_missing_score, (patch_good_score, patch_missing_score)
    assert failed_score == 0, failed_score
    assert rows[0]["evidence_level"] in {"C", "B", "A"}
    assert "llm_judge" in rows[0]["missing_evidence"]

    with tempfile.TemporaryDirectory() as td:
        judge_path = Path(td) / "judge.json"
        judge_path.write_text(json.dumps([{"task_id":"T001", "provider_name":"MiMo", "score":88, "note":"Grounded.", "parse_ok": True, "judge_status": "ok"}], ensure_ascii=False), encoding="utf-8")
        rows2 = score_outputs_v03(tasks, [good], [], build_ok, llm_review_path=str(judge_path))
        assert rows2[0]["llm_judge_score"] == 88
        assert rows2[0]["evidence_level"] in {"B", "A"}

        invalid_judge_path = Path(td) / "invalid_judge.json"
        invalid_judge_path.write_text(json.dumps([{"task_id":"T001", "provider_name":"MiMo", "score":"", "note":"invalid", "parse_ok": False, "judge_status": "invalid_json", "major_issue": "invalid_json"}], ensure_ascii=False), encoding="utf-8")
        rows3 = score_outputs_v03(tasks, [good], [], build_ok, llm_review_path=str(invalid_judge_path))
        assert rows3[0]["llm_judge_score"] == ""
        assert "llm_judge" in rows3[0]["missing_evidence"]

    print(json.dumps({
        "validated": True,
        "test_count": 6,
        "good_score": good_score,
        "generic_score": generic_score,
        "patch_good_score": patch_good_score,
        "patch_missing_score": patch_missing_score,
        "failed_score": failed_score,
        "note": "These are invariant tests, not a proof that scoring is perfect."
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
