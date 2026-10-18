"""Pinned, independently sourced defect acceptance; reference patches are not AI results."""
from __future__ import annotations

import difflib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from mimo_harness.manifests import atomic_json, digest, file_digest, source_inventory
from run_mimo_codeharness_v02 import COPY_EXCLUDE_DIRS

CASES = {
    "boltons-write": {
        "repository": "https://github.com/mahmoud/boltons.git",
        "upstream_base": "961dcff3f42e73b245aef65e377fe82763b257bb",
        "upstream_fix": "4e5faa3d7e4008d89e0d8bf1ea87b6d9a061a16d",
        "license_url": "https://github.com/mahmoud/boltons/blob/961dcff3f42e73b245aef65e377fe82763b257bb/LICENSE",
        "source_file": "boltons/ioutils.py",
        "old": "        self.buffer.write(s.encode('utf-8'))\n        self._tell = current_pos + len(s)\n",
        "fixed": "        self.buffer.write(s.encode('utf-8'))\n        self._tell = current_pos + len(s)\n        return len(s)\n",
        "regressed": "        self.buffer.write(s.upper().encode('utf-8'))\n        self._tell = current_pos + len(s)\n        return len(s)\n",
        "target": "test_write_reports_count",
        "task": "Fix SpooledStringIO.write to return the number of characters written, as the text stream API requires. Preserve stored Unicode text, readback and rolled-stream behavior. Keep acceptance tests unchanged.",
        "tests": '''from boltons.ioutils import SpooledStringIO

def test_write_reports_count():
    with SpooledStringIO() as stream:
        assert stream.write("abc") == 3

def test_unicode_round_trip():
    with SpooledStringIO() as stream:
        stream.write("a\\u2603b")
        assert stream.getvalue() == "a\\u2603b"

def test_rolled_round_trip():
    with SpooledStringIO() as stream:
        stream.rollover()
        stream.write("mixed Case")
        assert stream.getvalue() == "mixed Case"
''',
    },
    "more-itertools-bucket": {
        "repository": "https://github.com/more-itertools/more-itertools.git",
        "upstream_base": "1ea82a711c69f590054987b5cb194157f8ce8ac4",
        "upstream_fix": "67fcf640477c78605580f5ee703a54c73c1e7a8a",
        "license_url": "https://github.com/more-itertools/more-itertools/blob/1ea82a711c69f590054987b5cb194157f8ce8ac4/LICENSE",
        "source_file": "more_itertools/more.py",
        "old": "        self._validator = validator or (lambda x: True)\n",
        "fixed": "        self._validator = (lambda x: True) if validator is None else validator\n",
        "regressed": "        self._validator = lambda x: False\n",
        "target": "test_falsey_validator_is_honoured",
        "task": "Fix bucket to honour a callable validator even when bool(validator) is False. Preserve the default validator and valid-key iteration. Keep acceptance tests unchanged.",
        "tests": '''from more_itertools import bucket

class AllowedKeys(set):
    def __call__(self, key):
        return key in self

def test_falsey_validator_is_honoured():
    allowed = AllowedKeys()
    assert not allowed
    groups = bucket([10, 20, 11, 21], key=lambda x: x, validator=allowed)
    assert list(groups[10]) == []
    assert 10 not in groups

def test_default_validator_keeps_values():
    groups = bucket([1, 2, 1], key=lambda x: x)
    assert list(groups[1]) == [1, 1]
    assert list(groups[2]) == [2]

def test_nonempty_validator_keeps_permitted_values():
    groups = bucket([1, 2, 1], key=lambda x: x, validator=AllowedKeys([1]))
    assert list(groups[1]) == [1, 1]
    assert list(groups[2]) == []
''',
    },
}

CASES["ms-invalid-number"] = {
    "adapter": "node", "repository": "https://github.com/vercel/ms.git",
    "upstream_base": "d95e17fd75a93708c8a3daf5bb07bf649cd5931c",
    "upstream_fix": "d1add60365fe2340b750b1f7a254b83bea34e52d",
    "license_url": "https://github.com/vercel/ms/blob/d95e17fd75a93708c8a3daf5bb07bf649cd5931c/license.md",
    "source_file": "index.js",
    "old": r"  var match = /^((?:\d+)?-?(?:\d+)?\.?\d+) *",
    "fixed": r"  var match = /^(-?(?:\d+)?\.?\d+) *",
    "regressed": r"  var match = /^((?:\d+)?\.?\d+) *",
    "target": "rejects an internal minus sign",
    "task": "Fix ms parsing so malformed numbers such as 10-.5 are rejected. Preserve negative decimal durations, unit conversion and case-insensitive units. Keep acceptance tests unchanged.",
    "tests": '''const {test} = require('node:test');
const assert = require('node:assert/strict');
const ms = require('../index.js');
test('rejects an internal minus sign', () => assert.equal(ms('10-.5'), undefined));
test('preserves negative decimals', () => assert.equal(ms('-10.5s'), -10500));
test('preserves unit conversion', () => assert.equal(ms('2 HOURS'), 7200000));
''',
}

# A user-owned command can emit the documented JUnit contract without importing
# harness internals. This reference runner executes real stdlib unittest cases.
UNITTEST_JUNIT_RUNNER = '''import inspect, sys, unittest
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree
root=Element('testsuite')
class Result(unittest.TextTestResult):
    def save(self,test,status,message=''):
        file=Path(inspect.getsourcefile(type(test))).resolve().relative_to(Path.cwd()).as_posix()
        row=SubElement(root,'testcase',file=file,classname=type(test).__name__,name=test._testMethodName)
        if status:
            SubElement(row,status,type='AssertionError' if status=='failure' else status).text=message
    def addSuccess(self,test):
        super().addSuccess(test); self.save(test,None)
    def addFailure(self,test,err):
        super().addFailure(test,err); self.save(test,'failure',self._exc_info_to_string(err,test))
    def addError(self,test,err):
        super().addError(test,err); self.save(test,'error',self._exc_info_to_string(err,test))
    def addSkip(self,test,reason):
        super().addSkip(test,reason); self.save(test,'skipped',reason)
sys.path.insert(0,str(Path.cwd()))
suite=unittest.defaultTestLoader.discover('.mimo_acceptance',pattern='test_regression.py')
result=unittest.TextTestRunner(resultclass=Result,verbosity=2).run(suite)
root.set('tests',str(result.testsRun))
ElementTree(root).write(sys.argv[1],encoding='utf-8',xml_declaration=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''

CASES["boltons-junit"] = {
    **CASES["boltons-write"], "adapter": "junit", "target": "Streams::test_write_reports_count",
    "tests": '''import unittest
from boltons.ioutils import SpooledStringIO
class Streams(unittest.TestCase):
    def test_write_reports_count(self):
        with SpooledStringIO() as stream:
            self.assertEqual(stream.write('abc'),3)
    def test_unicode_round_trip(self):
        with SpooledStringIO() as stream:
            stream.write('a\\u2603b')
            self.assertEqual(stream.getvalue(),'a\\u2603b')
    def test_rolled_round_trip(self):
        with SpooledStringIO() as stream:
            stream.rollover(); stream.write('mixed Case')
            self.assertEqual(stream.getvalue(),'mixed Case')
''',
}


def _git(argv, cwd, *, env=None):
    try:
        result = subprocess.run(["git", *argv], cwd=cwd, env=env, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Case Git operation timed out; retry in a new output directory.") from exc
    if result.returncode:
        raise ValueError("Case Git operation failed: " + result.stderr.decode("utf-8", errors="replace")[-2000:])
    return result.stdout.decode("utf-8").strip()


def patch_for(spec, before, replacement):
    if before.count(spec["old"]) != 1:
        raise ValueError("Pinned source fragment does not match the case; no patch generated.")
    after = before.replace(spec["old"], replacement, 1)
    name = spec["source_file"]
    return f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile="a/" + name, tofile="b/" + name))


def command_case_init(args):
    from mimo_harness.cli import default_project_config, default_model_config
    spec = CASES[args.case]
    root = Path(args.output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    project = root / "project"
    print(f"[CASE] {args.case}: fetching pinned upstream source", flush=True)
    _git(["-c", "core.autocrlf=false", "clone", "--no-checkout", "--filter=blob:none", spec["repository"], str(project)], root)
    # This owned clone must keep the same bytes in checkout and Git archives,
    # including when the operator's global Windows configuration requests CRLF.
    _git(["config", "--local", "core.autocrlf", "false"], project)
    _git(["config", "--local", "core.eol", "lf"], project)
    _git(["-c", "core.autocrlf=false", "checkout", "--detach", spec["upstream_base"]], project)
    if _git(["rev-parse", "HEAD"], project) != spec["upstream_base"]:
        raise ValueError("Unexpected upstream base; no acceptance prepared.")
    before = (project / spec["source_file"]).read_text(encoding="utf-8")
    for name, replacement in (("reference", spec["fixed"]), ("regression", spec["regressed"])):
        (root / f"{name}.patch").write_text(patch_for(spec, before, replacement), encoding="utf-8", newline="\n")
    acceptance = project / ".mimo_acceptance"
    acceptance.mkdir()
    adapter = spec.get("adapter", "pytest")
    test_name = "test_regression.cjs" if adapter == "node" else "test_regression.py"
    (acceptance / test_name).write_text(spec["tests"], encoding="utf-8", newline="\n")
    if adapter == "pytest":
        (acceptance / "pytest.ini").write_text("[pytest]\n", encoding="utf-8", newline="\n")
    if adapter == "junit":
        (acceptance / "run_junit.py").write_text(UNITTEST_JUNIT_RUNNER, encoding="utf-8", newline="\n")
    # A local acceptance commit preserves the exact upstream parent and allows
    # the public verify/repair commands to use an immutable Git base by default.
    _git(["add", "--", ".mimo_acceptance"], project)
    hooks = root / "empty-hooks"
    hooks.mkdir()
    env = dict(os.environ, GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")
    _git(["-c", "user.name=MiMoHarness acceptance", "-c", "user.email=acceptance@example.invalid",
          "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(hooks),
          "commit", "--no-verify", "-m", "Add fixed independent defect acceptance"], project, env=env)
    resolved = _git(["rev-parse", "HEAD"], project)
    if _git(["rev-parse", "HEAD^"], project) != spec["upstream_base"]:
        raise ValueError("Acceptance commit does not preserve upstream ancestry.")
    cfg = default_project_config()
    check = {"id": "case", "adapter": adapter, "target_tests": [".mimo_acceptance/" + test_name + "::" + spec["target"]]}
    if adapter == "junit":
        check.update(argv=["{python}", ".mimo_acceptance/run_junit.py", "{junit}"], acceptance_files=[".mimo_acceptance/*.py"])
    else:
        check["args"] = [".mimo_acceptance/" + test_name] if adapter == "node" else ["-q", "-c", ".mimo_acceptance/pytest.ini", ".mimo_acceptance/test_regression.py"]
    cfg.update(project="project", output_dir="runs", model_config="models.json", run_build_tests=True, checks=[check])
    atomic_json(root / "mimo-harness.json", cfg)
    atomic_json(root / "models.json", default_model_config())
    (root / "task.txt").write_text(spec["task"] + "\n", encoding="utf-8", newline="\n")
    tracked = ["mimo-harness.json", "models.json", "task.txt", "reference.patch", "regression.patch"]
    inventory = source_inventory(project, COPY_EXCLUDE_DIRS)
    record = {"schema_version": 1, "case_id": args.case, "mode": "upstream_reference_controls",
              "repository": spec["repository"], "upstream_base": spec["upstream_base"], "upstream_fix": spec["upstream_fix"],
              "license_url": spec["license_url"], "acceptance_base": resolved,
              "source_sha256": digest(inventory),
              "source_content_sha256": digest({name: {key: value for key, value in row.items() if key != "mode"}
                                               for name, row in inventory.items()}),
              "files": {name: file_digest(root / name) for name in tracked},
              "reference_note": "Minimal reference change follows the pinned upstream fix. Regression patch is an intentional negative control, not an upstream change or an AI result."}
    atomic_json(root / "case.json", record)
    print(f"[OK] Prepared {args.case}: {root}\n[BASE] {resolved}\n[NEXT] mimo case-check --directory {root}")
    return 0


def validate_case(root):
    from mimo_harness.manifests import read_object, safe_file
    record = read_object(safe_file(root, "case.json"))
    case_id = record.get("case_id")
    spec = CASES.get(case_id) if isinstance(case_id, str) else None
    if (type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or record.get("mode") != "upstream_reference_controls" or not spec
            or any(record.get(key) != spec[key] for key in ("repository", "upstream_base", "upstream_fix", "license_url"))):
        raise ValueError("Case provenance changed or is unsupported.")
    if (not isinstance(record.get("files"), dict)
            or set(record["files"]) != {"mimo-harness.json", "models.json", "task.txt", "reference.patch", "regression.patch"}):
        raise ValueError("Case file inventory changed.")
    for name, sha in record["files"].items():
        if file_digest(safe_file(root, name)) != sha:
            raise ValueError("Case acceptance input changed: " + name)
    project = root / "project"
    if digest(source_inventory(project, COPY_EXCLUDE_DIRS)) != record.get("source_sha256"):
        raise ValueError("Pinned case source/acceptance changed; prepare a new case.")
    if _git(["rev-parse", "HEAD"], project) != record.get("acceptance_base") or _git(["rev-parse", "HEAD^"], project) != spec["upstream_base"]:
        raise ValueError("Case Git base changed.")
    return record


def command_case_check(args):
    from mimo_harness.cli import command_verify, command_resume, command_export, command_gate, _run_directory, _run_name
    root = Path(args.directory).expanduser().resolve()
    record = validate_case(root)
    name = args.name or _run_name("acceptance")
    output = _run_directory(root / "results", name)
    output.mkdir(parents=True, exist_ok=False)
    runs = []
    for kind, expected in (("reference", 0), ("regression", 2)):
        run_name = f"{name}-{kind}"
        run = root / "runs" / run_name
        patch = root / f"{kind}.patch"
        actual = command_verify(SimpleNamespace(config=str(root / "mimo-harness.json"), project=None,
                               patch=str(patch), base=record["acceptance_base"], name=run_name,
                               task_type="bug_fix", run_tests=True))
        if actual != expected:
            raise ValueError(f"Case {kind} exit {actual}, expected {expected}; retained evidence: {run}")
        bundle = output / f"{kind}.zip"
        command_export(SimpleNamespace(run=str(run), output=str(bundle)))
        gate = command_gate(SimpleNamespace(run=None, bundle=str(bundle), expect_base=record["acceptance_base"],
                                          expect_patch=str(patch), json=True))
        if gate != expected:
            raise ValueError("Portable case gate disagrees with expected control result.")
        runs.append({"control": kind, "expected_exit": expected, "exit_code": actual,
                     "bundle": bundle.name, "bundle_sha256": file_digest(bundle), "run_directory": str(run)})
        if kind == "reference":
            resumed_name = name + "-resumed"
            code = command_resume(SimpleNamespace(config=str(root / "mimo-harness.json"), run=str(run), name=resumed_name))
            if code != 0:
                raise ValueError("Reference recovery failed; no cached PASS accepted.")
            resumed = root / "runs" / resumed_name
            if command_gate(SimpleNamespace(run=str(resumed), bundle=None, expect_base=record["acceptance_base"],
                                           expect_patch=str(patch), json=True)) != 0:
                raise ValueError("Fresh reference recovery gate failed.")
    validate_case(root)
    report = {"schema_version": 1, "status": "PASS", "mode": "upstream_reference_controls", "model_called": False,
              "case": record, "controls": runs, "recovery": "fresh baseline/candidate checks passed"}
    atomic_json(output / "acceptance.json", report)
    print(f"[ACCEPTANCE] PASS — reference accepted, regression rejected, recovery rerun\n[RECORD] {output / 'acceptance.json'}")
    return 0
