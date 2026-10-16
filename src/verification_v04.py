"""Hard verification semantics for MiMo-CodeHarness v0.4.

This module keeps descriptive quality scores separate from executable evidence.
A high analysis score can never turn an unverified patch into a verified pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    missing_tests: List[str] = field(default_factory=list)
    skipped_tests: List[str] = field(default_factory=list)
    changed_acceptance_files: List[str] = field(default_factory=list)
    skipped_collections: List[str] = field(default_factory=list)

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

    if baseline_summary.get("evidence_schema_version") or candidate_summary.get("evidence_schema_version"):
        return _classify_test_cases(task_type, common, baseline_summary, candidate_summary)

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


def _classify_test_cases(task_type, common, baseline, candidate):
    """Strict, per-case comparison for structured checks; command success is insufficient."""
    def result(verdict, reason, note):
        return VerificationResult(
            execution_status="error" if verdict == ERROR else "completed",
            verdict=verdict, reason_codes=[reason], note=note, **common,
        )

    before_rows = baseline.get("test_cases", [])
    after_rows = candidate.get("test_cases", [])
    before = {r["nodeid"]: r for r in before_rows}
    after = {r["nodeid"]: r for r in after_rows}
    common.update(
        baseline_failed_tests=sorted(k for k, r in before.items() if r["status"] == "failed"),
        candidate_failed_tests=sorted(k for k, r in after.items() if r["status"] == "failed"),
        fixed_tests=sorted(k for k, r in before.items() if r["status"] == "failed" and after.get(k, {}).get("status") == "passed"),
        regressed_tests=sorted(k for k, r in after.items() if r["status"] == "failed" and before.get(k, {}).get("status") != "failed"),
        external_tests_executed=int(candidate.get("external_executed_count", 0)),
        missing_tests=sorted(set(baseline.get("collected_tests", [])) - set(candidate.get("collected_tests", []))),
        skipped_tests=sorted(k for k, r in after.items() if r["status"] in {"skipped", "xfail", "xpass"}),
        changed_acceptance_files=sorted(k for k, digest in baseline.get("protected_files", {}).items()
                                        if candidate.get("protected_files", {}).get(k) != digest),
        skipped_collections=sorted(item["nodeid"] for item in candidate.get("collection_skips", [])),
    )
    if len(before) != len(before_rows) or len(after) != len(after_rows):
        return result(ERROR, "DUPLICATE_TEST_IDS", "Duplicate test IDs cannot support a reliable comparison.")
    base_env, candidate_env = baseline.get("execution_environment", {}), candidate.get("execution_environment", {})
    if base_env.get("mode") != candidate_env.get("mode") or base_env.get("image_id") != candidate_env.get("image_id"):
        return result(INSUFFICIENT_EVIDENCE, "EXECUTION_ENVIRONMENT_CHANGED", "Execution backend or resolved Docker image changed between baseline and candidate.")
    for label, summary in (("BASELINE", baseline), ("CANDIDATE", candidate)):
        errors = [r for r in summary.get("results", []) if r["status"] == "error"]
        if errors:
            reason = next(iter(errors[0].get("reason_codes", [])), "CHECK_ERROR")
            return result(ERROR, f"{label}_{reason}", "Environment, collection, setup, teardown or runner error; not a reproduced defect.")
    if any(r["status"] == "failed" and r["category"] != "test" for r in candidate.get("results", [])):
        return result(FAIL, "REQUIRED_CHECK_FAILED", "A required static/build/smoke check failed.")
    if common["regressed_tests"]:
        return result(FAIL, "REGRESSION_DETECTED", "New assertion failures were identified by stable test ID.")
    if common["changed_acceptance_files"]:
        return result(INSUFFICIENT_EVIDENCE, "ACCEPTANCE_INPUT_CHANGED", "Baseline tests or test configuration changed; independent acceptance review is required.")
    if common["missing_tests"]:
        return result(INSUFFICIENT_EVIDENCE, "BASELINE_TESTS_MISSING", "Baseline test IDs disappeared from candidate collection.")
    if set(common["skipped_collections"]) - {item["nodeid"] for item in baseline.get("collection_skips", [])}:
        return result(INSUFFICIENT_EVIDENCE, "NEW_COLLECTION_SKIP", "A new module was skipped during collection.")
    for label, summary, cases in (("BASELINE", baseline, before), ("CANDIDATE", candidate, after)):
        if any(r["status"] in {"skipped", "insufficient"} for r in summary.get("results", []) if r["category"] != "static"):
            return result(INSUFFICIENT_EVIDENCE, f"{label}_CHECK_INCOMPLETE", "A required check was disabled, empty, or incomplete.")
        if any(r.get("adapter") == "command" and r["category"] == "test" for r in summary.get("results", [])):
            return result(INSUFFICIENT_EVIDENCE, "UNSTRUCTURED_TEST_COMMAND", "Exit codes alone do not provide per-test regression evidence.")
        if not cases or not any(r["status"] in {"passed", "failed"} for r in cases.values()):
            return result(INSUFFICIENT_EVIDENCE, "NO_BEHAVIOR_TEST_EXECUTED", "No comparable behavior tests executed.")
        if set(summary.get("collected_tests", [])) - set(cases):
            return result(INSUFFICIENT_EVIDENCE, f"{label}_TESTS_NOT_RUN", "Collected tests did not all produce results (for example, fail-fast).")
    lost_execution = [k for k, r in after.items()
                      if r["status"] not in {"passed", "failed"}
                      and (before.get(k, {}).get("status") != r["status"] or r["status"] == "xpass")]
    if lost_execution:
        return result(INSUFFICIENT_EVIDENCE, "TESTS_SKIPPED_OR_XFAILED", "Previously executed or new tests were skipped/xfail/xpass.")
    if task_type in BUG_FIX_TYPES:
        targets = baseline.get("target_tests", [])
        if not targets:
            return result(INSUFFICIENT_EVIDENCE, "TARGET_TESTS_NOT_CONFIGURED", "Bug-fix verification requires explicit target_tests in the pytest check.")
        if any(k not in before or before[k]["status"] != "failed" or not before[k].get("assertion_failure") for k in targets):
            return result(INSUFFICIENT_EVIDENCE, "TARGET_NOT_REPRODUCED", "Each target must fail a baseline behavior assertion.")
        if any(after.get(k, {}).get("status") != "passed" for k in targets):
            return result(FAIL, "TARGET_NOT_FIXED", "At least one configured defect target still fails.")
    if common["candidate_failed_tests"]:
        return result(INSUFFICIENT_EVIDENCE, "PREEXISTING_FAILURES_UNRESOLVED", "Existing failures remain; no full acceptance pass.")
    if any(r["status"] == "failed" for r in baseline.get("results", []) if r["category"] != "test"):
        return result(INSUFFICIENT_EVIDENCE, "BASELINE_CHECK_FAILED", "Baseline static/build/smoke failed; comparability is incomplete.")
    return result(PASS, "BEHAVIOR_TESTS_PASS", "Configured acceptance inputs were preserved and per-test evidence passed without regression.")


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
