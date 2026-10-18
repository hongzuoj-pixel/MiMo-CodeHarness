"""Canonical case/JUnit evidence shared by native Node and declared JUnit checks."""
from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath
from xml.etree import ElementTree as ET

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def validate_node_version(version):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version.strip())
    parts = tuple(map(int, match.groups())) if match else ()
    if not parts or not ((parts[0] == 22 and parts[1] >= 23) or (parts[0] == 24 and parts[1] >= 19)):
        raise ValueError("NODE_UNSUPPORTED_VERSION: use Node 22.23+ or 24.19+")
    return version.strip()


def read_bytes(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("TEST_EVIDENCE_MISSING_OR_OVERSIZED")
    return path.read_bytes()


def case_file(value):
    if (not isinstance(value, str) or not value or "\\" in value or Path(value).is_absolute()
            or PureWindowsPath(value).drive or ".." in value.split("/")):
        raise ValueError("TEST_FILE_OUTSIDE_WORKSPACE")
    return value


def validate_record(data):
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or not isinstance(data.get("cases"), dict) or not isinstance(data.get("collected"), list)):
        raise ValueError("INVALID_CASE_EVIDENCE")
    ids = data["collected"]
    if len(ids) != len(set(ids)) or set(ids) != set(data["cases"]):
        raise ValueError("DUPLICATE_OR_MISSING_TEST_IDS")
    for key, case in data["cases"].items():
        if (not isinstance(key, str) or not key or not isinstance(case, dict) or case.get("nodeid") != key
                or case.get("status") not in {"passed", "failed", "error", "skipped", "xfail", "xpass"}
                or type(case.get("assertion_failure")) is not bool):
            raise ValueError("INVALID_CASE_EVIDENCE")
        case_file(case.get("file"))
    return data


def write_junit(data, output):
    root = ET.Element("testsuite", tests=str(len(data["cases"])))
    for nodeid, case in data["cases"].items():
        row = ET.SubElement(root, "testcase", name=nodeid, file=case["file"])
        tag = {"failed": "failure", "error": "error", "skipped": "skipped", "xfail": "skipped", "xpass": "skipped"}.get(case["status"])
        if tag:
            ET.SubElement(row, tag, type="AssertionError" if case["assertion_failure"] else case["status"]).text = case.get("message", "")
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def parse_junit(payload):
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise ValueError("INVALID_JUNIT_DOCUMENT")
    try:
        text = payload.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("INVALID_JUNIT_DOCUMENT")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError) as exc:
        raise ValueError("INVALID_JUNIT_DOCUMENT") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("INVALID_JUNIT_DOCUMENT")
    cases = {}
    for row in root.iter("testcase"):
        file = case_file(row.get("file"))
        name, cls = row.get("name"), row.get("classname", "")
        if not name:
            raise ValueError("JUNIT_TEST_NAME_MISSING")
        nodeid = "::".join(filter(None, [file, cls, name]))
        outcomes = [child for child in row if child.tag in {"failure", "error", "skipped"}]
        if len(outcomes) > 1 or nodeid in cases:
            raise ValueError("JUNIT_DUPLICATE_OR_CONTRADICTORY_CASE")
        tag = outcomes[0].tag if outcomes else None
        status = {"failure": "failed", "error": "error", "skipped": "skipped"}.get(tag, "passed")
        assertion = tag == "failure" and outcomes[0].get("type") in {"AssertionError", "ERR_ASSERTION", "AssertionFailure"}
        cases[nodeid] = {"nodeid": nodeid, "file": file, "status": status, "assertion_failure": assertion,
                         "phases": {"call": status}, "message": "".join(outcomes[0].itertext())[:8192] if outcomes else ""}
    for suite in root.iter():
        if suite.tag not in {"testsuite", "testsuites"}:
            continue
        rows = list(suite.iter("testcase"))
        expected = {"tests": len(rows), "failures": sum(r.find("failure") is not None for r in rows),
                    "errors": sum(r.find("error") is not None for r in rows), "skipped": sum(r.find("skipped") is not None for r in rows)}
        if any(key in suite.attrib and suite.attrib[key] != str(count) for key, count in expected.items()):
            raise ValueError("JUNIT_TOTALS_DISAGREE")
    return {"schema_version": 1, "adapter": "junit", "complete": True, "cases": cases, "collected": list(cases),
            "collection_errors": [], "collection_skips": []}


def verify_junit(data, payload):
    """Verify exact case identity/outcome, not just aggregate counts."""
    parsed = parse_junit(payload)
    if data.get("adapter") == "junit":
        expected = {key: {k: case[k] for k in ("file", "status", "assertion_failure")} for key, case in data["cases"].items()}
        actual = {key: {k: case[k] for k in ("file", "status", "assertion_failure")} for key, case in parsed["cases"].items()}
        if expected != actual:
            raise ValueError("JUNIT_CASES_DISAGREE")
    else:
        # The harness's Node JUnit writer stores the canonical ID in name.
        root = ET.fromstring(payload)
        rows = list(root.iter("testcase"))
        if len(rows) != len(data["cases"]):
            raise ValueError("JUNIT_CASES_DISAGREE")
        for row, (key, case) in zip(rows, data["cases"].items()):
            tag = "failure" if case["status"] == "failed" else "error" if case["status"] == "error" else "skipped" if case["status"] in {"skipped", "xfail", "xpass"} else None
            if row.get("name") != key or row.get("file") != case["file"] or [c.tag for c in row] != ([tag] if tag else []):
                raise ValueError("JUNIT_CASES_DISAGREE")


def load_node(path):
    data = validate_record(json.loads(read_bytes(path).decode("utf-8")))
    if data.get("runner_error"):
        raise ValueError(data["runner_error"])
    if data.get("adapter") != "node" or data.get("complete") is not True:
        raise ValueError("NODE_INCOMPLETE_STREAM")
    return data
