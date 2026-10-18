"""Actual Node processes, portable proof and fixed-baseline repair."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimo_harness.checks import run_check, validate_checks
from mimo_harness.cli import main
from mimo_harness.manifests import atomic_json
from run_mimo_codeharness_v02 import BuildTesterV02, copy_worktree
from test_cli_repair import case, diff, read, repair
from verification_v04 import classify_candidate

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable; required native Node CI runs these checks")
SOURCE = "exports.target=()=>1; exports.stable=()=>10; exports.skip=false; exports.include=true;\n"
TESTS = """const {test}=require('node:test');
const assert=require('node:assert/strict');
const calc=require('./calc.cjs');
if(calc.include) test('target',{skip:calc.skip},()=>assert.equal(calc.target(),2));
test('stable',()=>assert.equal(calc.stable(),10));
"""


def policy():
    return [{"id": "native", "adapter": "node", "args": ["accept.cjs"], "target_tests": ["accept.cjs::target"]}]


def create(root):
    root.mkdir()
    (root / "calc.cjs").write_text(SOURCE, encoding="utf-8")
    (root / "accept.cjs").write_text(TESTS, encoding="utf-8")


@pytest.mark.parametrize("scenario,verdict,reason", [
    ("fix", "PASS", "BEHAVIOR_TESTS_PASS"), ("unfixed", "FAIL", "TARGET_NOT_FIXED"),
    ("regress", "FAIL", "REGRESSION_DETECTED"), ("skip", "INSUFFICIENT_EVIDENCE", "TESTS_SKIPPED_OR_XFAILED"),
    ("missing", "INSUFFICIENT_EVIDENCE", "BASELINE_TESTS_MISSING"),
    ("weaken", "INSUFFICIENT_EVIDENCE", "ACCEPTANCE_INPUT_CHANGED"),
    ("package", "INSUFFICIENT_EVIDENCE", "ACCEPTANCE_INPUT_CHANGED"),
    ("import", "ERROR", "CANDIDATE_NODE_COLLECTION_ERROR"),
    ("runtime", "ERROR", "CANDIDATE_NODE_CASE_ERROR"),
])
def test_actual_node_case_outcomes(tmp_path, scenario, verdict, reason):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    create(base)
    (base / "package.json").write_text('{"private":true}')
    copy_worktree(base, candidate)
    code = SOURCE.replace("()=>1;", "()=>2;")
    if scenario == "unfixed": code = SOURCE
    if scenario == "regress": code = code.replace("()=>10;", "()=>0;")
    if scenario == "skip": code = code.replace("skip=false", "skip=true")
    if scenario == "missing": code = code.replace("include=true", "include=false")
    if scenario == "import": code = "throw new Error('missing dependency');\n"
    if scenario == "runtime": code = code.replace("()=>2;", "()=>{throw new TypeError('runtime failure')};")
    (candidate / "calc.cjs").write_text(code, encoding="utf-8")
    if scenario == "weaken": (candidate / "accept.cjs").write_text(TESTS.replace("calc.target(),2", "true,true"))
    if scenario == "package": (candidate / "package.json").write_text('{"private":true,"name":"changed"}')
    before = BuildTesterV02(base, tmp_path / "logs/base", checks=policy()).run_all(True)
    after = BuildTesterV02(candidate, tmp_path / "logs/after", checks=policy()).run_all(True)
    patch = SimpleNamespace(candidate_id="test", task_id="T", provider_name="local", status="applied", applied=True, changed_files="M calc.cjs")
    result = classify_candidate(SimpleNamespace(task_type="bug_fix"), patch, before, after)
    assert result.verdict == verdict and reason in result.reason_codes, result.to_dict()
    assert before["test_cases"][0]["assertion_failure"] is True


@pytest.mark.parametrize("body", ["", "const {test}=require('node:test');test.skip('skip',()=>{});",
                                  "const {describe}=require('node:test');describe('empty',()=>{});"])
def test_empty_files_suites_and_all_skips_never_count_as_behavior(tmp_path, body):
    project = tmp_path / "project"; project.mkdir()
    (project / "empty.cjs").write_text(body)
    row = run_check(project, tmp_path / "logs", {"id": "empty", "adapter": "node", "args": ["empty.cjs"]}, True)
    assert row["status"] == "insufficient", row


def test_suite_and_nested_test_identity_is_stable(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    (project / "nested.cjs").write_text("const {test,describe,it}=require('node:test');describe('suite',()=>it('leaf',()=>{}));test('parent',async t=>{await t.test('child',()=>{});});")
    row = run_check(project, tmp_path / "logs", {"id": "nested", "adapter": "node", "args": ["nested.cjs"]}, True)
    assert row["status"] == "ok", row
    assert row["collected"] == ["nested::nested.cjs::suite > leaf", "nested::nested.cjs::parent > child", "nested::nested.cjs::parent"]


@pytest.mark.parametrize("body", ["const {test}=require('node:test');test('same',()=>{});test('same',()=>{});",
                                  "const {test}=require('node:test');test('broken',()=>new Promise(()=>{}));"])
def test_duplicate_identity_and_cancelled_tests_are_errors(tmp_path, body):
    project = tmp_path / "project"; project.mkdir()
    (project / "broken.cjs").write_text(body)
    row = run_check(project, tmp_path / "logs", {"id": "broken", "adapter": "node", "args": ["broken.cjs"], "timeout_seconds": 3}, True)
    assert row["status"] == "error", row


def test_node_two_round_repair_resume_and_portable_gate(tmp_path, monkeypatch):
    patches = [diff(SOURCE, SOURCE.replace("()=>1;", "()=>0;"), "calc.cjs"), diff(SOURCE, SOURCE.replace("()=>1;", "()=>2;"), "calc.cjs")]
    c = case(tmp_path, patches=patches, budget=2)
    (c.project / "calc.cjs").write_text(SOURCE)
    (c.project / "accept.cjs").write_text(TESTS)
    cfg = json.loads(c.config.read_text()); cfg["checks"] = policy(); atomic_json(c.config, cfg)
    assert repair(c) == 0
    assert [r["verification"]["verdict"] for r in read(c)["rounds"]] == ["FAIL", "PASS"]
    assert (c.project / "calc.cjs").read_text() == SOURCE
    assert main(["resume", "--run", str(tmp_path / "runs/first"), "--name", "resumed"]) == 0
    bundle = tmp_path / "evidence.zip"
    assert main(["export", "--run", str(tmp_path / "runs/resumed"), "--output", str(bundle)]) == 0
    assert main(["gate", "--bundle", str(bundle)]) == 0
    import mimo_harness.manifests as manifests
    original = manifests.file_digest
    monkeypatch.setattr(manifests, "file_digest", lambda path: "0" * 64 if Path(path).name.lower() in {"node", "node.exe"} else original(path))
    assert main(["resume", "--run", str(tmp_path / "runs/first"), "--name", "changed-node"]) == 4


def test_native_node_timeout_is_explicit_and_does_not_accept_partial_results(tmp_path):
    project=tmp_path/'project';project.mkdir()
    (project/'slow.cjs').write_text("const {test}=require('node:test');test('pending',async()=>new Promise(()=>{setInterval(()=>{},1000)}));")
    row=run_check(project,tmp_path/'logs',{'id':'slow','adapter':'node','args':['slow.cjs'],'timeout_seconds':1},True)
    assert row['status']=='error' and row['reason_codes']==['CHECK_TIMEOUT']


def test_node_junit_identity_is_rechecked_after_resealing(tmp_path):
    from mimo_harness.evidence import RunReader, inspect_evidence
    from mimo_harness.manifests import file_digest
    c=case(tmp_path,patches=[diff(SOURCE,SOURCE.replace('()=>1;','()=>2;'),'calc.cjs')])
    (c.project/'calc.cjs').write_text(SOURCE);(c.project/'accept.cjs').write_text(TESTS)
    cfg=json.loads(c.config.read_text());cfg['checks']=policy();atomic_json(c.config,cfg)
    assert repair(c,rounds=1)==0
    root=tmp_path/'runs/first';history=read(c)
    xml=Path(history['rounds'][0]['candidate_summary']['results'][-1]['junit_xml'])
    xml.write_bytes(xml.read_bytes().replace(b'accept.cjs::target',b'accept.cjs::forged'))
    manifest=read(c,file='run_manifest.json');manifest['artifacts'][xml.relative_to(root).as_posix()]=file_digest(xml)
    atomic_json(root/'run_manifest.json',manifest)
    with pytest.raises(ValueError):inspect_evidence(RunReader(root))


@pytest.mark.parametrize("args", [["--eval", "1"], ["--test-reporter=dot"], ["../outside.js"], ["C:/outside.js"], []])
def test_node_rejects_unreviewed_options_and_escaping_paths(args):
    with pytest.raises(ValueError): validate_checks([{"id": "node", "adapter": "node", "args": args}])
