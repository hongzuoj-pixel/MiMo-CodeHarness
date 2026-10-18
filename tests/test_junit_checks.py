"""Actual generic commands plus strict JUnit ingestion and artifact consistency."""
import json
import sys
from pathlib import Path

import pytest

from mimo_harness.checks import run_check, validate_checks
from mimo_harness.structured_cases import parse_junit, verify_junit


@pytest.mark.parametrize("behavior,status", [("pass", "ok"), ("assert", "failed"), ("error", "error"),
                                          ("skip", "insufficient"), ("empty", "insufficient"),
                                          ("missing", "error"), ("inconsistent", "error")])
def test_real_generic_command_junit_contract(tmp_path, behavior, status):
    project = tmp_path / "project"; project.mkdir()
    payload = '<testsuite tests="1"><testcase file="test_source.py" classname="C" name="behavior">{}</testcase></testsuite>'
    inner = {"assert": '<failure type="AssertionError">wrong value</failure>', "inconsistent": '<failure type="AssertionError"/>',
             "error": '<error>bad setup</error>', "skip": '<skipped/>'}.get(behavior, '')
    xml = '<testsuite tests="0"/>' if behavior == "empty" else payload.format(inner)
    (project / "test_source.py").write_text("# declared acceptance input\n")
    program = project / "runner.py"
    program.write_text("from pathlib import Path\nimport sys\n" + ("" if behavior == "missing" else f"Path(sys.argv[1]).write_text({xml!r},encoding='utf-8')\n") + f"raise SystemExit({1 if behavior in {'assert','error'} else 0})\n")
    spec = {"id": "generic", "adapter": "junit", "argv": ["{python}", "runner.py", "{junit}"],
            "acceptance_files": ["test_source.py", "runner.py"]}
    validate_checks([spec])
    row = run_check(project, tmp_path / "logs", spec, True)
    assert row["status"] == status, row
    if behavior == "assert": assert row["test_cases"][0]["assertion_failure"] is True


@pytest.mark.parametrize("xml", [b'<testsuite tests="2"><testcase file="x.py" name="a"/></testsuite>',
    b'<testsuite><testcase file="x.py" name="a"/><testcase file="x.py" name="a"/></testsuite>',
    b'<testsuite><testcase file="../x.py" name="a"/></testsuite>',
    b'<testsuite><testcase file="x.py" name="a"><failure/><error/></testcase></testsuite>',
    b'<!DOCTYPE foo [<!ENTITY x "bad">]><testsuite/>', '<testsuite/>'.encode('utf-16'), b'not xml'])
def test_malformed_or_ambiguous_junit_fails_closed(xml):
    with pytest.raises(ValueError): parse_junit(xml)


def test_junit_same_count_but_changed_test_identity_cannot_pass_proof():
    xml = b'<testsuite tests="1"><testcase file="x.py" name="target"/></testsuite>'
    data = parse_junit(xml)
    verify_junit(data, xml)
    with pytest.raises(ValueError, match="DISAGREE"):
        verify_junit(data, xml.replace(b'target', b'forged'))


def test_untyped_junit_failure_cannot_reproduce_a_target_assertion():
    data = parse_junit(b'<testsuite><testcase file="x.py" name="target"><failure/></testcase></testsuite>')
    assert data['cases']['x.py::target']['assertion_failure'] is False
