"""Hard verification semantics for MiMo-CodeHarness v0.4.

This module keeps descriptive quality scores separate from executable evidence.
A high analysis score can never turn an unverified patch into a verified pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Sequence

PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ERROR = "ERROR"
NOT_APPLICABLE = "NOT_APPLICABLE"

PATCH_REQUIRED_TYPES = {
    "bug_fix",
    "code_refactor",
    "test_generation",
    "patch_generation",
}
BUG_FIX_TYPES = {"bug_fix"}

EXIT_CODES = {
    PASS: 0,
    NOT_APPLICABLE: 0,
    FAIL: 2,
    INSUFFICIENT_EVIDENCE: 3,
    ERROR: 4,
}


@dataclass
class VerificationResult:
    candidate_id: str
    task_id: str
    provider_name: str
    execution_status: str
    verdict: str
    reason_codes: List[str]
    changed_files: List[str]
    baseline_failed_tests: List[str]
    candidate_failed_tests: List[str]
    fixed_tests: List[str]
    regressed_tests: List[str]
    external_tests_executed: int
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def task_expects_patch(task_type: str) -> bool:
    return str(task_type or "").lower() in PATCH_REQUIRED_TYPES


def _test_statuses(summary: Mapping[str, Any]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for result in summary.get("results", []) or []:
        category = str(result.get("category", ""))
        name = str(result.get("name", ""))
        if category == "test" and name:
            statuses[name] = str(result.get("status", ""))
    return statuses


def classify_candidate(
    task: Any,
    patch_result: Any,
    baseline_summary: Mapping[str, Any],
    candidate_summary: Mapping[str, Any] | None,
) -> VerificationResult:
    """Classify one candidate using baseline-vs-candidate executable evidence."""
    task_type = str(_get(task, "task_type", ""))
    patch_status = str(_get(patch_result, "status", ""))
    applied = bool(_get(patch_result, "applied", False))
    changed = [
        item for item in str(_get(patch_result, "changed_files", "")).split(";") if item
    ]
    candidate_id = str(_get(patch_result, "candidate_id", ""))
    task_id = str(_get(patch_result, "task_id", _get(task, "task_id", "")))
    provider = str(_get(patch_result, "provider_name", ""))

    common = dict(
        candidate_id=candidate_id,
        task_id=task_id,
        provider_name=provider,
        changed_files=changed,
        baseline_failed_tests=[],
        candidate_failed_tests=[],
        fixed_tests=[],
        regressed_tests=[],
        external_tests_executed=0,
    )

    if patch_status == "no_patch":
        if task_expects_patch(task_type):
            return VerificationResult(
                execution_status="not_run",
                verdict=INSUFFICIENT_EVIDENCE,
                reason_codes=["PATCH_REQUIRED_BUT_MISSING"],
                note="The task requires a patch, but no usable unified diff was found.",
                **common,
            )
        return VerificationResult(
            execution_status="not_applicable",
            verdict=NOT_APPLICABLE,
            reason_codes=["TASK_DOES_NOT_REQUIRE_PATCH"],
            note="This analysis task does not require executable patch verification.",
            **common,
        )

    if patch_status == "check_failed":
        return VerificationResult(
            execution_status="failed",
            verdict=FAIL,
            reason_codes=["PATCH_NOT_APPLICABLE"],
            note="git apply --check rejected the candidate patch.",
            **common,
        )

    if patch_status in {"apply_failed", "error"}:
        return VerificationResult(
            execution_status="error",
            verdict=ERROR,
            reason_codes=["PATCH_EXECUTION_ERROR"],
            note="The candidate could not be prepared for verification.",
            **common,
        )

    if not applied:
        return VerificationResult(
            execution_status="not_run",
            verdict=INSUFFICIENT_EVIDENCE,
            reason_codes=["PATCH_NOT_APPLIED"],
            note="The patch was checked but not applied, so candidate tests were not run.",
            **common,
        )

    if not changed:
        return VerificationResult(
            execution_status="completed",
            verdict=FAIL,
            reason_codes=["NO_CHANGED_FILES"],
            note="The applied candidate produced no detectable content or mode change.",
            **common,
        )

    if not candidate_summary:
        return VerificationResult(
            execution_status="error",
            verdict=ERROR,
            reason_codes=["CANDIDATE_SUMMARY_MISSING"],
            note="The applied candidate has no build/test summary.",
            **common,
        )

    baseline_tests = _test_statuses(baseline_summary)
    candidate_tests = _test_statuses(candidate_summary)
    baseline_failed = sorted(
        name for name, status in baseline_tests.items() if status in {"failed", "error"}
    )
    candidate_failed = sorted(
        name for name, status in candidate_tests.items() if status in {"failed", "error"}
    )
    fixed = sorted(name for name in baseline_failed if candidate_tests.get(name) == "ok")
    regressed = sorted(
        name
        for name, status in candidate_tests.items()
        if status in {"failed", "error"}
        and baseline_tests.get(name) not in {"failed", "error"}
    )
    executed = int(candidate_summary.get("external_executed_count", 0) or 0)
    common.update(
        baseline_failed_tests=baseline_failed,
        candidate_failed_tests=candidate_failed,
        fixed_tests=fixed,
        regressed_tests=regressed,
        external_tests_executed=executed,
    )

    if int(candidate_summary.get("failed_count", 0) or 0) and not candidate_failed:
        return VerificationResult(
            execution_status="completed",
            verdict=FAIL,
            reason_codes=["STATIC_CHECK_FAILED"],
            note="The candidate introduced or retained a failing syntax/configuration check.",
            **common,
        )

    if regressed:
        return VerificationResult(
            execution_status="completed",
            verdict=FAIL,
            reason_codes=["REGRESSION_DETECTED"],
            note="At least one behavior test regressed relative to the baseline.",
            **common,
        )

    if executed == 0:
        return VerificationResult(
            execution_status="completed",
            verdict=INSUFFICIENT_EVIDENCE,
            reason_codes=["NO_BEHAVIOR_TEST_EXECUTED"],
            note="Static checks are useful signals but cannot establish behavior correctness.",
            **common,
        )

    if candidate_failed:
        return VerificationResult(
            execution_status="completed",
            verdict=INSUFFICIENT_EVIDENCE,
            reason_codes=["PREEXISTING_FAILURES_UNRESOLVED"],
            note="Behavior tests still fail, but no new regression was identified.",
            **common,
        )

    if task_type in BUG_FIX_TYPES and not fixed:
        return VerificationResult(
            execution_status="completed",
            verdict=INSUFFICIENT_EVIDENCE,
            reason_codes=["NO_FAIL_TO_PASS_EVIDENCE"],
            note="Bug-fix verification requires at least one baseline failure to pass.",
            **common,
        )

    return VerificationResult(
        execution_status="completed",
        verdict=PASS,
        reason_codes=["BEHAVIOR_TESTS_PASS", "NO_REGRESSION"],
        note="The applied candidate passed executed behavior tests without regression.",
        **common,
    )


def summarize_verifications(results: Sequence[VerificationResult]) -> Dict[str, Any]:
    counts = {name: 0 for name in (PASS, FAIL, INSUFFICIENT_EVIDENCE, ERROR, NOT_APPLICABLE)}
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    applicable = [r for r in results if r.verdict != NOT_APPLICABLE]
    if any(r.verdict == ERROR for r in applicable):
        overall = ERROR
    elif any(r.verdict == FAIL for r in applicable):
        overall = FAIL
    elif any(r.verdict == INSUFFICIENT_EVIDENCE for r in applicable):
        overall = INSUFFICIENT_EVIDENCE
    elif applicable and all(r.verdict == PASS for r in applicable):
        overall = PASS
    else:
        overall = NOT_APPLICABLE
    return {
        "overall_verdict": overall,
        "exit_code": EXIT_CODES[overall],
        "candidate_count": len(results),
        "applicable_candidate_count": len(applicable),
        "counts": counts,
        "verified_pass_rate": (
            round(counts[PASS] / len(applicable), 4) if applicable else None
        ),
    }
