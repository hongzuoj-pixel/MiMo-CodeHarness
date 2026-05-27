"""MiMo-CodeHarness scoring v0.3

A stricter, research-oriented scoring module for repository-level CodeAgent evaluation.

Design goals
------------
This module deliberately avoids pretending that a single heuristic number is a
perfect ground truth.  It separates evidence sources:

1. repo grounding: did the answer cite real related files/modules?
2. task rubric coverage: did it cover the task-specific scoring points?
3. answer completeness: is the reasoning structured and actionable?
4. patch verification: if the task asks for a patch, is a patch present/applicable/applied?
5. build/test evidence: did safe checks or real build/test pass?
6. LLM judge: optional structured external judge score loaded from JSON/CSV or produced by a judge model.
7. human review: optional human rubric score for small-sample calibration.

The final score is a weighted, evidence-aware score. Missing optional evidence is
not silently treated as success; the row includes `evidence_level` and a note.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


PATCH_REQUIRED_TYPES = {
    "bug_fix",
    "bug_localization",
    "code_refactor",
    "test_generation",
    "patch_generation",
}

DEFAULT_WEIGHTS: Dict[str, float] = {
    "repo_grounding": 0.20,
    "task_rubric": 0.22,
    "answer_completeness": 0.14,
    "patch_verification": 0.14,
    "build_test": 0.10,
    "llm_judge": 0.15,
    "human_review": 0.05,
}

# Light bilingual alias table. This is intentionally small and auditable.
ALIASES: Dict[str, List[str]] = {
    "测试": ["测试", "test", "pytest", "unit", "build", "compile", "验证", "check"],
    "验证": ["验证", "test", "build", "compile", "check", "确认"],
    "依赖": ["依赖", "dependency", "import", "include", "调用", "模块", "链路", "graph"],
    "调用": ["调用", "call", "invoke", "import", "include", "flow", "chain"],
    "文件": ["文件", "file", "path", "路径", "module", "模块"],
    "风险": ["风险", "risk", "边界", "rollback", "回滚", "安全", "privacy", "failure"],
    "成本": ["成本", "token", "latency", "耗时", "费用", "cost"],
    "可维护性": ["可维护", "maintainability", "结构", "模块化", "重构", "refactor"],
    "可测试性": ["可测试", "testability", "测试", "mock", "fixture"],
    "错误处理": ["错误处理", "异常", "exception", "error", "fail", "失败", "边界"],
    "入口函数": ["入口", "entry", "main", "cli", "gui", "app"],
    "数据流": ["数据流", "data flow", "pipeline", "input", "output", "转换", "process"],
    "状态流": ["状态", "state", "flow", "状态机", "control", "流程"],
    "外设/协议": ["spi", "uart", "i2c", "can", "外设", "协议", "mcu", "stm32", "rc522"],
    "实时性风险": ["实时", "latency", "delay", "blocking", "interrupt", "中断", "超时"],
    "patch 格式": ["diff --git", "@@", "---", "+++", "```diff", "patch"],
    "最小修改": ["最小", "minimal", "small", "localized", "不影响", "safe"],
    "可回滚": ["回滚", "rollback", "revert", "可恢复", "isolated"],
}


def normalize_text(text: str) -> str:
    return (text or "").replace("\\", "/").lower()


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def safe_round(x: float) -> float:
    return round(float(x), 2)


def split_terms(text: str) -> List[str]:
    # English-like terms + contiguous CJK phrases are enough for a transparent heuristic.
    lower = normalize_text(text)
    terms = re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]*|[\u4e00-\u9fff]{2,}", lower)
    return [t.strip(" ./-_") for t in terms if len(t.strip(" ./-_")) >= 2]


def term_present(point: str, answer: str) -> bool:
    ans = normalize_text(answer)
    point_norm = normalize_text(point)
    if point_norm and point_norm in ans:
        return True
    for alias_key, aliases in ALIASES.items():
        if alias_key in point or normalize_text(alias_key) in point_norm:
            if any(normalize_text(a) in ans for a in aliases):
                return True
    terms = [t for t in split_terms(point) if len(t) >= 3]
    if not terms:
        return False
    hits = sum(1 for t in terms if t in ans)
    return hits >= max(1, math.ceil(len(terms) * 0.45))


def score_repo_grounding(task: Any, answer: str) -> Tuple[float, Dict[str, Any]]:
    ans = normalize_text(answer)
    related_files = list(getattr(task, "related_files", []) or [])
    exact_hits: List[str] = []
    basename_hits: List[str] = []
    for f in related_files:
        f_norm = normalize_text(f)
        base = normalize_text(Path(f).name)
        if f_norm and f_norm in ans:
            exact_hits.append(f)
        elif base and base in ans:
            basename_hits.append(f)
    # Also reward code-reference style evidence, but cap it.
    code_ref_hits = len(re.findall(r"`[^`]{3,80}`|\b[A-Za-z_][A-Za-z0-9_]+\(\)|\b[A-Za-z_][A-Za-z0-9_]+\.[A-Za-z0-9_]+", answer or ""))
    if not related_files:
        file_score = 45.0 if code_ref_hits else 30.0
    else:
        denom = max(1, min(len(related_files), 6))
        file_score = min(70.0, (len(exact_hits) * 18.0 + len(basename_hits) * 10.0) / denom * 3.0)
    code_score = min(30.0, code_ref_hits * 5.0)
    score = clamp(file_score + code_score)
    return score, {
        "related_file_count": len(related_files),
        "exact_file_hits": exact_hits,
        "basename_file_hits": basename_hits,
        "code_ref_hits": code_ref_hits,
        "grounding_note": "Grounding requires concrete file/path/module evidence; generic good writing alone should not score high.",
    }


def score_task_rubric(task: Any, answer: str, human_rubric: Optional[Mapping[str, Any]] = None) -> Tuple[float, Dict[str, Any]]:
    points = list(getattr(task, "scoring_points", []) or [])
    extra_must = []
    extra_nice = []
    if human_rubric:
        extra_must = list(human_rubric.get("must_include", []) or [])
        extra_nice = list(human_rubric.get("nice_to_have", []) or [])
    all_required = points + extra_must
    hit_required = [p for p in all_required if term_present(str(p), answer)]
    hit_nice = [p for p in extra_nice if term_present(str(p), answer)]
    if not all_required:
        base = 55.0
    else:
        base = 100.0 * len(hit_required) / max(1, len(all_required))
    nice_bonus = min(8.0, 4.0 * len(hit_nice))
    score = clamp(base + nice_bonus)
    return score, {
        "rubric_total": len(all_required),
        "rubric_hits": len(hit_required),
        "rubric_hit_items": hit_required,
        "nice_hits": len(hit_nice),
        "nice_hit_items": hit_nice,
    }


def score_answer_completeness(answer: str) -> Tuple[float, Dict[str, Any]]:
    text = answer or ""
    lower = normalize_text(text)
    sections = {
        "understanding": ["需求理解", "理解", "purpose", "目标"],
        "files": ["相关文件", "文件定位", "file", "path"],
        "dependency": ["依赖", "调用链", "dependency", "import", "include"],
        "solution": ["解决方案", "patch", "建议", "方案", "改进"],
        "test": ["测试", "验证", "build", "compile", "test"],
        "risk": ["风险", "边界", "回滚", "安全", "risk"],
    }
    hits = {name: any(normalize_text(k) in lower for k in kws) for name, kws in sections.items()}
    length_score = 0.0
    n = len(text)
    if n >= 1200:
        length_score = 25.0
    elif n >= 700:
        length_score = 20.0
    elif n >= 350:
        length_score = 14.0
    elif n >= 120:
        length_score = 8.0
    numbered_or_bullets = len(re.findall(r"(^|\n)\s*(\d+\.|[-*])\s+", text))
    structure_score = min(20.0, numbered_or_bullets * 4.0)
    section_score = 55.0 * sum(1 for v in hits.values() if v) / len(sections)
    score = clamp(length_score + structure_score + section_score)
    return score, {
        "length_chars": n,
        "section_hits": hits,
        "bullet_or_numbered_items": numbered_or_bullets,
    }


def task_expects_patch(task: Any, human_rubric: Optional[Mapping[str, Any]] = None) -> bool:
    task_type = str(getattr(task, "task_type", "")).lower()
    expected = normalize_text(str(getattr(task, "expected_output", "")))
    description = normalize_text(str(getattr(task, "description", "")))
    if human_rubric and "expected_patch" in human_rubric:
        return bool(human_rubric.get("expected_patch"))
    return task_type in PATCH_REQUIRED_TYPES or "diff" in expected or "patch" in expected or "patch" in description or "unified diff" in description


def score_patch_verification(task: Any, answer: str, patch_statuses: List[str], human_rubric: Optional[Mapping[str, Any]] = None) -> Tuple[float, Dict[str, Any]]:
    expects = task_expects_patch(task, human_rubric)
    has_diff_text = "diff --git" in answer or "```diff" in answer or "@@" in answer
    statuses = set(patch_statuses or [])
    if expects:
        if "applied" in statuses:
            score = 100.0
            note = "Expected patch; patch applied in isolated worktree."
        elif "checked" in statuses:
            score = 86.0
            note = "Expected patch; patch passed git apply --check."
        elif has_diff_text:
            score = 55.0
            note = "Expected patch; diff-like text present but not verified."
        else:
            score = 20.0
            note = "Expected patch; no usable patch evidence found."
    else:
        if "apply_failed" in statuses or "check_failed" in statuses:
            score = 60.0
            note = "Patch not required; failed optional patch should not dominate score but is a warning."
        elif patch_statuses:
            score = 88.0
            note = "Patch not required; optional patch evidence exists."
        else:
            score = 82.0
            note = "Patch not required for this task. Neutral-high score assigned."
    return clamp(score), {"patch_expected": expects, "patch_statuses": patch_statuses, "has_diff_text": has_diff_text, "patch_note": note}


def score_build_test(build_summary: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
    failed = int(build_summary.get("failed_count", 0) or 0)
    warnings = int(build_summary.get("warning_count", 0) or 0)
    skipped = int(build_summary.get("skipped_count", 0) or 0)
    all_ok = bool(build_summary.get("all_critical_ok"))
    run_external = bool(build_summary.get("run_external"))
    if all_ok and run_external and skipped == 0:
        score = 100.0
        note = "Real external build/test and safe checks passed."
    elif all_ok:
        score = 86.0 if skipped else 92.0
        note = "Critical safe checks passed; external build/test may be skipped."
    elif failed:
        score = 30.0
        note = "Critical build/test checks failed or errored."
    else:
        score = 70.0
        note = "Warnings present; needs manual review."
    if warnings:
        score = max(0.0, score - min(12.0, warnings * 4.0))
    return clamp(score), {"build_note": note, "failed_count": failed, "warning_count": warnings, "skipped_count": skipped, "run_external": run_external}


def load_review_scores(path: Optional[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Load optional LLM judge or human review scores.

    Accepted JSON format:
    [
      {"task_id":"T001", "provider_name":"MiMo", "score":82, "note":"..."}
    ]

    Accepted CSV columns: task_id, provider_name, score, note
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    rows: List[Dict[str, Any]] = []
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("reviews", [])
    else:
        with p.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows:
        task_id = str(r.get("task_id", "")).strip()
        provider = str(r.get("provider_name", r.get("provider", ""))).strip()
        if not task_id or not provider:
            continue

        # v0.3.2: invalid/malformed LLM judge outputs are treated as MISSING
        # evidence, not as a real zero score. A genuine zero is accepted only
        # when the judge returned valid parse_ok/status information and a
        # numeric score.
        judge_status = str(r.get("judge_status", "ok")).strip().lower()
        major_issue = str(r.get("major_issue", "")).strip().lower()
        parse_ok_value = r.get("parse_ok", True)
        if isinstance(parse_ok_value, str):
            parse_ok = parse_ok_value.strip().lower() not in {"false", "0", "no"}
        else:
            parse_ok = bool(parse_ok_value)
        raw_score = r.get("score", r.get("judge_score", ""))
        if raw_score is None or str(raw_score).strip() == "":
            continue
        if judge_status in {"invalid_json", "error"} or judge_status.startswith("http_"):
            continue
        if major_issue in {"invalid_json", "invalid_score", "http_error", "exception"}:
            continue
        if not parse_ok:
            continue
        try:
            score = float(raw_score)
        except Exception:
            continue
        out[(task_id, provider)] = {"score": clamp(score), "note": str(r.get("note", r.get("reason", "")))}
    return out


def weighted_available_score(component_scores: Mapping[str, Optional[float]], weights: Mapping[str, float]) -> Tuple[float, float, List[str]]:
    total_weight = 0.0
    weighted = 0.0
    missing: List[str] = []
    for name, w in weights.items():
        v = component_scores.get(name)
        if v is None:
            missing.append(name)
            continue
        weighted += float(v) * float(w)
        total_weight += float(w)
    if total_weight <= 0:
        return 0.0, 0.0, missing
    return clamp(weighted / total_weight), round(total_weight, 3), missing


def evidence_level(has_llm: bool, has_human: bool, has_patch: bool, build_score: float) -> str:
    if has_llm and has_human and build_score >= 80 and has_patch:
        return "A"
    if (has_llm or has_human) and build_score >= 70:
        return "B"
    if build_score >= 70:
        return "C"
    return "D"


def score_outputs_v03(
    tasks: List[Any],
    runs: List[Any],
    patch_results: List[Any],
    build_summary: Mapping[str, Any],
    llm_review_path: Optional[str] = None,
    human_review_path: Optional[str] = None,
    human_rubric_path: Optional[str] = None,
    weights: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    task_map = {getattr(t, "task_id", ""): t for t in tasks}
    patch_map: Dict[Tuple[str, str], List[Any]] = defaultdict(list)
    for p in patch_results:
        patch_map[(getattr(p, "task_id", ""), getattr(p, "provider_name", ""))].append(p)

    llm_scores = load_review_scores(llm_review_path)
    human_scores = load_review_scores(human_review_path)
    human_rubrics: Dict[str, Any] = {}
    if human_rubric_path and Path(human_rubric_path).exists():
        data = json.loads(Path(human_rubric_path).read_text(encoding="utf-8"))
        human_rubrics = data.get("tasks", data if isinstance(data, dict) else {})

    build_score, build_meta = score_build_test(build_summary)
    used_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        used_weights.update({k: float(v) for k, v in weights.items()})

    rows: List[Dict[str, Any]] = []
    for r in runs:
        task_id = getattr(r, "task_id", "")
        provider = getattr(r, "provider_name", "")
        task = task_map.get(task_id)
        answer = getattr(r, "content", "") or ""
        status = getattr(r, "status", "")
        rubric = human_rubrics.get(task_id, {}) if task_id else {}
        patch_items = patch_map.get((task_id, provider), [])
        patch_statuses = [getattr(p, "status", "") for p in patch_items]

        repo_score, repo_meta = score_repo_grounding(task, answer) if task else (0.0, {})
        rubric_score, rubric_meta = score_task_rubric(task, answer, rubric) if task else (0.0, {})
        completeness_score, completeness_meta = score_answer_completeness(answer)
        patch_score, patch_meta = score_patch_verification(task, answer, patch_statuses, rubric) if task else (0.0, {})

        llm_review = llm_scores.get((task_id, provider))
        human_review = human_scores.get((task_id, provider))
        llm_score = float(llm_review["score"]) if llm_review else None
        human_score = float(human_review["score"]) if human_review else None

        components: Dict[str, Optional[float]] = {
            "repo_grounding": repo_score,
            "task_rubric": rubric_score,
            "answer_completeness": completeness_score,
            "patch_verification": patch_score,
            "build_test": build_score,
            "llm_judge": llm_score,
            "human_review": human_score,
        }
        final, effective_weight, missing = weighted_available_score(components, used_weights)
        if status != "completed":
            final = 0.0
        has_patch_evidence = bool(patch_statuses) or patch_meta.get("has_diff_text")
        level = evidence_level(llm_score is not None, human_score is not None, bool(has_patch_evidence), build_score)

        rows.append({
            "task_id": task_id,
            "task_type": getattr(task, "task_type", "unknown") if task else "unknown",
            "provider_name": provider,
            "model": getattr(r, "model", ""),
            "status": status,
            "dry_run": getattr(r, "dry_run", False),
            "final_score": safe_round(final),
            "total_score": safe_round(final),  # backward-compatible column name
            "pass": status == "completed" and final >= 70,
            "evidence_level": level,
            "effective_weight": effective_weight,
            "missing_evidence": ";".join(missing),
            "repo_grounding_score": safe_round(repo_score),
            "task_rubric_score": safe_round(rubric_score),
            "answer_completeness_score": safe_round(completeness_score),
            "patch_verification_score": safe_round(patch_score),
            "build_test_score": safe_round(build_score),
            "llm_judge_score": "" if llm_score is None else safe_round(llm_score),
            "human_review_score": "" if human_score is None else safe_round(human_score),
            "llm_judge_note": "" if not llm_review else llm_review.get("note", ""),
            "human_review_note": "" if not human_review else human_review.get("note", ""),
            "file_hits_exact": len(repo_meta.get("exact_file_hits", [])),
            "file_hits_basename": len(repo_meta.get("basename_file_hits", [])),
            "related_file_count": repo_meta.get("related_file_count", 0),
            "rubric_hits": rubric_meta.get("rubric_hits", 0),
            "rubric_total": rubric_meta.get("rubric_total", 0),
            "rubric_hit_items": "; ".join(map(str, rubric_meta.get("rubric_hit_items", []))),
            "patch_expected": patch_meta.get("patch_expected", False),
            "patch_status": ";".join(patch_statuses),
            "patch_note": patch_meta.get("patch_note", ""),
            "build_note": build_meta.get("build_note", ""),
            "input_tokens_est": getattr(r, "input_tokens_est", 0),
            "output_tokens_est": getattr(r, "output_tokens_est", 0),
            "total_tokens_est": getattr(r, "total_tokens_est", 0),
            "time_seconds": getattr(r, "time_seconds", 0),
            "scoring_note": (
                "v0.3 evidence-aware score. Missing optional LLM/human evidence is shown in missing_evidence; "
                "scores should be reported with evidence_level, not as absolute truth."
            ),
            "error": getattr(r, "error", ""),
        })
    return rows


def build_llm_judge_prompt(task: Any, run: Any, patch_statuses: List[str], build_summary: Mapping[str, Any]) -> str:
    """Return a strict judge prompt. The caller may send it to any judge model.

    The judge must return JSON only, e.g. {"score": 82, "note": "..."}.
    """
    return f"""
你是代码智能体评测的严格评审。请基于真实任务要求评估模型回答，不要因为格式漂亮就给高分。

【任务】
- task_id: {getattr(task, 'task_id', '')}
- task_type: {getattr(task, 'task_type', '')}
- description: {getattr(task, 'description', '')}
- related_files: {json.dumps(getattr(task, 'related_files', []), ensure_ascii=False)}
- scoring_points: {json.dumps(getattr(task, 'scoring_points', []), ensure_ascii=False)}

【Patch 状态】{patch_statuses}
【Build/Test 摘要】{json.dumps(dict(build_summary), ensure_ascii=False)[:3000]}

【模型回答】
{getattr(run, 'content', '')[:8000]}

请只输出 JSON：
{{"score": 0-100的整数, "note": "一句话说明扣分/加分原因", "grounded": true/false, "major_issue": "none 或主要问题"}}
""".strip()
