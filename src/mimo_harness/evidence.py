"""Portable evidence semantics shared by live-run and archive gates."""
from __future__ import annotations

import json
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

from mimo_harness.manifests import digest, file_digest, read_object, safe_file, source_inventory
from verification_v04 import classify_candidate, summarize_verifications

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000
ROOT_FILES = {
    "run_log.json", "verification_summary.json", "verification_result.json", "verification_results.json",
    "baseline_summary.json", "candidate_summary.json", "candidate_build_summaries.json",
    "patch_result.json", "patch_results.json", "tasks.json", "input.patch", "token_usage.csv",
}
LOG_DIRS = {"baseline", "candidate", "build_test_logs", "verification"}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def portable_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or any(ord(c) < 32 for c in name):
        raise ValueError("Unsafe evidence filename.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in name.split("/")):
        raise ValueError(f"Unsafe evidence filename: {name}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(part != part.rstrip(" .") or part.split(".")[0].upper() in reserved for part in path.parts):
        raise ValueError("Evidence filename is not portable to Windows.")
    return name


def object_from_bytes(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Damaged JSON evidence.") from exc


class RunReader:
    """Read a locked run without consulting its current source/configuration paths."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        path = safe_file(self.root, "run_manifest.json")
        self.manifest = read_object(path)
        m = self.manifest
        if m.get("schema_version") != 1 or m.get("kind") not in {"run", "verify"}:
            raise ValueError("A supported run manifest is required; legacy reports remain readable.")
        if not isinstance(m.get("inputs"), dict) or digest(m["inputs"]) != m.get("fingerprint"):
            raise ValueError("Damaged run fingerprint.")
        if m.get("status") not in {"RUNNING", "COMPLETED", "ERROR", "CANCELLED"}:
            raise ValueError("Unsupported run state.")
        source = read_object(safe_file(self.root, "inputs/source_manifest.json"))
        if digest(source) != m["inputs"].get("source_sha256"):
            raise ValueError("Damaged source inventory.")
        snapshot = self.root / "inputs/project"
        if snapshot.is_symlink() or source_inventory(snapshot) != source:
            raise ValueError("Captured source snapshot changed.")
        artifacts = m.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("Missing evidence seal.")
        for name, sha in artifacts.items():
            portable_name(name)
            if file_digest(safe_file(self.root, name)) != sha:
                raise ValueError(f"Evidence changed: {name}")
        self.paths = {"provenance/run_manifest.json": path,
                      "provenance/source_manifest.json": self.root / "inputs/source_manifest.json"}
        # Unsealed attempts are diagnostic only. Never read files while active;
        # the caller holds RunLock, which is released after abrupt process death.
        names = artifacts if m["status"] != "RUNNING" else self._partial_files()
        for name in names:
            p = PurePosixPath(name)
            wanted = name in ROOT_FILES or (p.parts[0] in LOG_DIRS and "runtime" not in p.parts
                                          and p.suffix in {".json", ".xml", ".log", ".txt"})
            if wanted:
                self.paths["evidence/" + portable_name(name)] = safe_file(self.root, name)
        self.names = set(self.paths)
        self.expected = {name: (artifacts[name[len("evidence/"):]]
                               if name.startswith("evidence/") and m["status"] != "RUNNING" else file_digest(path))
                         for name, path in self.paths.items()}
        if m["kind"] == "verify" and self.expected.get("evidence/input.patch") != m["inputs"].get("patch_sha256"):
            raise ValueError("Captured patch does not match its input identity.")

    def _partial_files(self):
        import os
        names = []
        for directory, dirs, files in os.walk(self.root):
            parent = Path(directory)
            dirs[:] = [n for n in dirs if not (parent / n).is_symlink() and n != "runtime"
                       and not (parent == self.root and n not in LOG_DIRS)]
            names.extend((parent / n).relative_to(self.root).as_posix() for n in files)
        return names

    def read(self, name: str) -> bytes:
        if name not in self.paths:
            raise ValueError(f"Missing portable evidence: {name}")
        path = self.paths[name]
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"Evidence exceeds the per-file size limit: {name}")
        payload = path.read_bytes()
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("Evidence grew while reading.")
        if hashlib.sha256(payload).hexdigest() != self.expected[name]:
            raise ValueError("Evidence changed during inspection.")
        return payload


def original_relative(path: str, run_directory: str) -> str:
    cls = PureWindowsPath if "\\" in run_directory or (len(run_directory) > 1 and run_directory[1] == ":") else PurePosixPath
    try:
        relative = cls(path).relative_to(cls(run_directory)).as_posix()
    except (ValueError, TypeError) as exc:
        raise ValueError("Evidence reference escapes the recorded run.") from exc
    return portable_name(relative)


def _load(reader, name):
    full = "evidence/" + name
    if full not in reader.names:
        raise ValueError(f"Required evidence missing: {name}")
    return object_from_bytes(reader.read(full))


def _proof(reader, summary, original_root):
    """Cross-check summarized case evidence against recorder JSON and JUnit."""
    if summary.get("evidence_schema_version") != 1:
        raise ValueError("A PASS gate requires structured per-test evidence.")
    cases, collected = [], []
    links = []
    for row in summary.get("results", []):
        for field in ("stdout_log", "stderr_log"):
            if row.get(field):
                name = "evidence/" + original_relative(row[field], original_root)
                if name not in reader.names:
                    raise ValueError("Required command log is missing.")
                links.append(name)
        if row.get("adapter") not in {"pytest", "unittest"}:
            continue
        evidence_name = "evidence/" + original_relative(row.get("test_evidence", ""), original_root)
        junit_name = "evidence/" + original_relative(row.get("junit_xml", ""), original_root)
        raw = object_from_bytes(reader.read(evidence_name))
        if raw.get("schema_version") != 1 or not isinstance(raw.get("cases"), dict):
            raise ValueError("Invalid test recorder schema.")
        expected = [dict(case, nodeid=f"{row['name']}::{key}") for key, case in raw["cases"].items()]
        ids = [f"{row['name']}::{key}" for key in raw.get("collected", [])]
        if expected != row.get("test_cases") or ids != row.get("collected") or raw.get("exit_code") != row.get("returncode"):
            raise ValueError("Test recorder and check summary disagree.")
        xml = reader.read(junit_name)
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("DTD/entity declarations are not allowed in JUnit evidence.")
        try:
            tests = list(ET.fromstring(xml).iter("testcase"))
        except ET.ParseError as exc:
            raise ValueError("Damaged JUnit XML.") from exc
        if len(tests) < len(expected):
            raise ValueError("JUnit omits recorded test cases.")
        if sum(t.find("failure") is not None for t in tests) != sum(c["status"] == "failed" for c in expected):
            raise ValueError("JUnit and recorded assertion failures disagree.")
        if any(t.find("error") is not None for t in tests):
            raise ValueError("JUnit contains execution errors.")
        skipped = sum(c["status"] in {"skipped", "xfail"} for c in expected) + len(raw.get("collection_skips", []))
        if sum(t.find("skipped") is not None for t in tests) != skipped:
            raise ValueError("JUnit and recorded skip/xfail outcomes disagree.")
        cases.extend(expected)
        collected.extend(ids)
        links.extend((evidence_name, junit_name))
    if cases != summary.get("test_cases") or collected != summary.get("collected_tests"):
        raise ValueError("Aggregate test cases disagree with individual checks.")
    if not any(c["status"] in {"passed", "failed"} for c in cases):
        raise ValueError("No behavior tests executed.")
    return sorted(set(links))


def inspect_evidence(reader, *, expect_base=None, expect_patch_sha256=None) -> dict:
    m = reader.manifest
    decision = {"schema_version": 1, "allowed": False, "verdict": "ERROR", "exit_code": 4,
                "reason_codes": [], "run_id": m.get("run_id"), "fingerprint": m.get("fingerprint")}
    report = {"schema_version": 1, "run": {k: m.get(k) for k in
              ("run_id", "kind", "status", "harness_version", "fingerprint", "created_at")},
              "gate": decision, "summary": {}, "candidates": [], "stages": []}
    if m["status"] == "RUNNING":
        decision.update(verdict="INCOMPLETE", reason_codes=["RUN_NOT_SEALED"])
        return report
    summary = _load(reader, "verification_summary.json")
    log = _load(reader, "run_log.json")
    if summary != log.get("verification_summary") or summary.get("exit_code") != m.get("exit_code"):
        raise ValueError("Manifest, run log and verification summary disagree.")
    report["summary"] = summary
    if expect_base is not None and m["inputs"].get("resolved_base") != expect_base:
        decision["reason_codes"] = ["EXPECTED_BASE_MISMATCH"]
        return report
    if expect_patch_sha256 is not None and m["inputs"].get("patch_sha256") != expect_patch_sha256:
        decision["reason_codes"] = ["EXPECTED_PATCH_MISMATCH"]
        return report
    verdict = summary.get("overall_verdict")
    if m["status"] in {"ERROR", "CANCELLED"}:
        if (m["status"] == "ERROR" and (verdict != "ERROR" or m.get("exit_code") != 4)) or (
                m["status"] == "CANCELLED" and (verdict != "CANCELLED" or m.get("exit_code") != 130)):
            raise ValueError("Run completion state disagrees with its verdict.")
        decision.update(verdict=verdict, reason_codes=["RUN_" + m["status"]])
        return report
    if m["kind"] == "verify":
        baseline = _load(reader, "baseline_summary.json")
        patch = _load(reader, "patch_result.json")
        candidate = _load(reader, "candidate_summary.json") if patch.get("applied") else None
        task = SimpleNamespace(task_id="PATCH", task_type=m["options"]["task_type"])
        results = [classify_candidate(task, patch, baseline, candidate)]
        saved = [_load(reader, "verification_result.json")]
        stages = [("baseline", baseline)] + ([("candidate", candidate)] if candidate else [])
    else:
        tasks = {row["task_id"]: row for row in _load(reader, "tasks.json")}
        baseline = _load(reader, "build_test_logs/build_test_summary.json")
        patches = _load(reader, "patch_results.json")
        builds = _load(reader, "candidate_build_summaries.json")
        results = [classify_candidate(tasks.get(p["task_id"]), p, baseline, builds.get(p["candidate_id"])) for p in patches]
        saved = _load(reader, "verification_results.json")
        stages = [("baseline", baseline)] + [(key, value) for key, value in builds.items()]
    if [r.to_dict() for r in results] != saved:
        raise ValueError("Recorded verdicts disagree with recomputed baseline/candidate evidence.")
    expected = summarize_verifications(results)
    errors = [r for r in baseline.get("results", []) if r.get("status") == "error"]
    if errors and m["kind"] == "run":
        expected.update(overall_verdict="ERROR", exit_code=4,
                        run_errors=[{"stage": "baseline", "check": r["name"],
                                     "reason_codes": r.get("reason_codes", ["BASELINE_CHECK_ERROR"]),
                                     "note": r.get("note", "")} for r in errors])
    if expected != summary:
        raise ValueError("Verification totals disagree with recomputed results.")
    report["candidates"] = saved
    original_root = log["output_dir"]
    for label, stage in stages:
        item = {"name": label, "executed_checks": stage.get("external_executed_count", 0),
                "cases": [{k: c.get(k) for k in ("nodeid", "status", "assertion_failure")}
                          for c in stage.get("test_cases", [])], "links": []}
        if verdict == "PASS":
            item["links"] = _proof(reader, stage, original_root)
        else:
            for row in stage.get("results", []):
                for field in ("stdout_log", "stderr_log", "test_evidence", "junit_xml"):
                    if row.get(field):
                        name = "evidence/" + original_relative(row[field], original_root)
                        if name in reader.names:
                            item["links"].append(name)
            item["links"] = sorted(set(item["links"]))
        report["stages"].append(item)
    if verdict == "PASS":
        decision.update(allowed=True, verdict="PASS", exit_code=0, reason_codes=["VERIFIED_PASS"])
    elif verdict == "FAIL":
        decision.update(verdict="FAIL", exit_code=2, reason_codes=["VERIFICATION_FAILED"])
    elif verdict in {"INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"}:
        decision.update(verdict=verdict, exit_code=3, reason_codes=["NO_COMPLETE_ACCEPTANCE_PASS"])
    else:
        raise ValueError("Unsupported final verdict.")
    return report
