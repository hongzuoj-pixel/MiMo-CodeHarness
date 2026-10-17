"""Actual verification → strict gate → portable export, including failure paths."""
import hashlib
import json
import os
import shutil
import stat
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

from mimo_harness.cli import main
from mimo_harness.manifests import RunLock, file_digest
from mimo_harness.bundles import BundleReader
from mimo_harness.offline_report import render_report
import run_mimo_codeharness_v02 as harness


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def setup_project(tmp_path, monkeypatch, git=False, stable=False):
    root = tmp_path / "project with spaces"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    assertion = "VALUE in (1, 2)" if stable else "VALUE == 2"
    (root / "test_value.py").write_text(f"from value import VALUE\ndef test_value(): assert {assertion}\n", encoding="utf-8")
    if git:
        import subprocess
        for argv in (["init", "-q"], ["add", "."], ["-c", "user.email=tests@example.invalid", "-c", "user.name=Test", "commit", "-qm", "base"]):
            subprocess.run(["git", *argv], check=True)
    assert main(["init"]) == 0
    cfg = read(root / "mimo-harness.json")
    cfg["checks"] = [{"id": "unit", "adapter": "pytest", "args": ["-q"],
                      "target_tests": [] if stable else ["test_value.py::test_value"]}]
    write(root / "mimo-harness.json", cfg)
    patch = tmp_path / "fix.patch"
    patch.write_text("--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n", encoding="utf-8")
    return root, patch


def verify(root, patch, *, name="checked", base="WORKTREE", tests=True, stable=False, expected=0):
    argv = ["verify", "--base", base, "--patch", str(patch), "--task-type", "code_refactor" if stable else "bug_fix", "--name", name]
    assert main(argv + (["--run-tests"] if tests else [])) == expected
    return root / ".mimo/runs" / name


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.scripts = [], 0

    def handle_starttag(self, tag, attrs):
        self.scripts += tag == "script"
        self.links.extend(value for key, value in attrs if key == "href")


@pytest.mark.parametrize("base", ["WORKTREE", "HEAD"])
def test_real_gate_bundle_relocates_without_source_config_or_environment(tmp_path, monkeypatch, capsys, base):
    root, patch = setup_project(tmp_path, monkeypatch, git=True)
    run = verify(root, patch, base=base)
    original = (run / "run_manifest.json").read_bytes()
    manifest = read(run / "run_manifest.json")
    assert main(["gate", "--run", str(run), "--expect-base", manifest["inputs"]["resolved_base"], "--expect-patch", str(patch)]) == 0
    bundle = tmp_path / "handoff.zip"
    assert main(["export", "--run", str(run), "--output", str(bundle)]) == 0
    assert (run / "run_manifest.json").read_bytes() == original
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert any(name.endswith(".xml") for name in names)
        assert any(name.endswith("_stdout.log") for name in names)
        assert not any("runtime/" in name or "checkpoints/" in name or "model_outputs" in name or name.endswith(".py") for name in names)
        page = archive.read("index.html").decode()
        parser = Links()
        parser.feed(page)
        assert not parser.scripts
        assert all(href.startswith("#") or unquote(href) in names for href in parser.links)
        assert "APPROVED" in page
    monkeypatch.chdir(tmp_path)
    moved = tmp_path / "another machine"
    moved.mkdir()
    destination = moved / "evidence.zip"
    shutil.move(str(bundle), destination)
    shutil.move(str(root), tmp_path / "source unavailable")
    monkeypatch.setenv("MIMO_MODEL", "irrelevant-current-provider")
    capsys.readouterr()
    assert main(["gate", "--bundle", str(destination), "--json"]) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["allowed"] and decision["fingerprint"] == manifest["fingerprint"]
    assert main(["gate", "--bundle", str(destination), "--expect-base", "wrong-base"]) == 4
    patch.write_text(patch.read_text().replace("+VALUE = 2", "+VALUE = 3"))
    assert main(["gate", "--bundle", str(destination), "--expect-patch", str(patch)]) == 4


@pytest.mark.parametrize("outcome,code", [("FAIL", 2), ("INSUFFICIENT_EVIDENCE", 3), ("NOT_APPLICABLE", 3), ("ERROR", 4), ("CANCELLED", 4), ("INCOMPLETE", 4)])
def test_failure_and_incomplete_evidence_can_export_but_never_approve(tmp_path, monkeypatch, outcome, code):
    root, patch = setup_project(tmp_path, monkeypatch)
    if outcome == "NOT_APPLICABLE":
        assert main(["run", "--name", "checked", "--limit-tasks", "1"]) == 0
        run = root / ".mimo/runs/checked"
    elif outcome == "CANCELLED":
        def interrupt(*args, **kw):
            raise KeyboardInterrupt
        monkeypatch.setattr(harness, "call_model", interrupt)
        assert main(["run", "--name", "checked", "--limit-tasks", "1"]) == 130
        run = root / ".mimo/runs/checked"
    elif outcome == "ERROR":
        (root / "test_value.py").write_text("import mimo_missing_dependency\n")
        run = verify(root, patch, expected=4)
    elif outcome == "INSUFFICIENT_EVIDENCE":
        run = verify(root, patch, tests=False, expected=3)
    else:
        if outcome == "FAIL":
            patch.write_text(patch.read_text().replace("+VALUE = 2", "+VALUE = 3"))
        run = verify(root, patch, expected=2 if outcome == "FAIL" else 0)
        if outcome == "INCOMPLETE":
            m = read(run / "run_manifest.json")
            m["status"] = "RUNNING"
            write(run / "run_manifest.json", m)
    assert main(["gate", "--run", str(run)]) == code
    output = tmp_path / "failure.zip"
    assert main(["export", "--run", str(run), "--output", str(output)]) == 0
    assert main(["gate", "--bundle", str(output)]) == code
    with zipfile.ZipFile(output) as archive:
        report = json.loads(archive.read("report.json"))
        assert report["gate"]["verdict"] == outcome
        assert not report["gate"]["allowed"]
        assert b"BLOCKED" in archive.read("index.html")


def reseal(run, relative):
    m = read(run / "run_manifest.json")
    m["artifacts"][relative] = file_digest(run / relative)
    write(run / "run_manifest.json", m)


@pytest.mark.parametrize("change", ["summary", "recorder", "junit", "junit-skip", "missing-log", "snapshot", "input-inventory"])
def test_gate_rejects_corruption_and_cross_checks_even_resealed_summaries(tmp_path, monkeypatch, change):
    root, patch = setup_project(tmp_path, monkeypatch)
    run = verify(root, patch)
    if change == "summary":
        path = run / "verification_summary.json"
        data = read(path)
        data["counts"]["PASS"] += 1
        write(path, data)
        reseal(run, "verification_summary.json")
        log = read(run / "run_log.json")
        log["verification_summary"] = data
        write(run / "run_log.json", log)
        reseal(run, "run_log.json")
    elif change == "recorder":
        relative = "candidate/unit_tests.json"
        data = read(run / relative)
        next(iter(data["cases"].values()))["status"] = "failed"
        write(run / relative, data)
        reseal(run, relative)
    elif change == "junit":
        (run / "candidate/unit_junit.xml").write_text("<testsuites/>")
        reseal(run, "candidate/unit_junit.xml")
    elif change == "junit-skip":
        (run / "candidate/unit_junit.xml").write_text('<testsuite><testcase name="test_value"><skipped/></testcase></testsuite>')
        reseal(run, "candidate/unit_junit.xml")
    elif change == "missing-log":
        (run / "candidate/unit_stdout.log").unlink()
    elif change == "snapshot":
        (run / "inputs/project/value.py").write_text("VALUE = 100\n")
    else:
        write(run / "inputs/source_manifest.json", {})
    assert main(["gate", "--run", str(run)]) == 4
    assert main(["export", "--run", str(run), "--output", str(tmp_path / "bad.zip")]) == 4
    assert not (tmp_path / "bad.zip").exists()


def rewrite_bundle(path, transform):
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    transform(files)
    index = json.loads(files["bundle_manifest.json"])
    index["files"] = {name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                      for name, value in files.items() if name != "bundle_manifest.json"}
    files["bundle_manifest.json"] = json.dumps(index).encode()
    replacement = path.with_name("changed.zip")
    with zipfile.ZipFile(replacement, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return replacement


@pytest.mark.parametrize("change", ["report", "html", "missing-case", "evidence-bytes"])
def test_bundle_recomputes_results_after_inventory_is_rewritten(tmp_path, monkeypatch, change):
    root, patch = setup_project(tmp_path, monkeypatch)
    run = verify(root, patch)
    bundle = tmp_path / "good.zip"
    assert main(["export", "--run", str(run), "--output", str(bundle)]) == 0
    def mutate(files):
        if change == "report":
            report = json.loads(files["report.json"])
            report["gate"]["exit_code"] = 2
            files["report.json"] = json.dumps(report).encode()
        elif change == "html":
            files["index.html"] = b"<h1>APPROVED</h1>"
        elif change == "missing-case":
            del files["evidence/candidate/unit_tests.json"]
        else:
            files["evidence/verification_result.json"] = b"{}"
    changed = rewrite_bundle(bundle, mutate)
    assert main(["gate", "--bundle", str(changed)]) == 4


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/drive", "evidence\\escape", "same/../path"])
def test_unsafe_zip_paths_are_rejected_without_extraction(tmp_path, capsys, name):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"payload")
    assert main(["gate", "--bundle", str(path), "--json"]) == 4
    decision = json.loads(capsys.readouterr().out)
    assert not decision["allowed"]
    assert not (tmp_path.parent / "outside").exists()


@pytest.mark.parametrize("kind", ["duplicate", "case-collision", "symlink", "directory", "unknown-schema", "non-object"])
def test_unsupported_archive_structures_fail_closed(tmp_path, kind):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        if kind == "duplicate":
            archive.writestr("same", b"one")
            with pytest.warns(UserWarning):
                archive.writestr("same", b"two")
        elif kind == "case-collision":
            archive.writestr("Same", b"one")
            archive.writestr("same", b"two")
        elif kind == "symlink":
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../../outside")
        elif kind == "directory":
            archive.writestr("folder/", b"")
        elif kind == "unknown-schema":
            archive.writestr("bundle_manifest.json", b'{"schema_version":999}')
        else:
            archive.writestr("bundle_manifest.json", b'[]')
    assert main(["gate", "--bundle", str(path)]) == 4


def test_legacy_bad_zip_and_unstructured_exit_zero_cannot_approve(tmp_path, monkeypatch, capsys):
    root, patch = setup_project(tmp_path, monkeypatch)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    write(legacy / "run_log.json", {"verification_summary": {"overall_verdict": "PASS"}})
    assert main(["gate", "--run", str(legacy)]) == 4
    malformed = tmp_path / "broken.zip"
    malformed.write_bytes(b"not an archive")
    capsys.readouterr()
    assert main(["gate", "--bundle", str(malformed), "--json"]) == 4
    assert not json.loads(capsys.readouterr().out)["allowed"]
    cfg = read(root / "mimo-harness.json")
    cfg["checks"] = [{"id": "not-proof", "adapter": "command", "argv": ["{python}", "-c", "pass"]}]
    write(root / "mimo-harness.json", cfg)
    run = verify(root, patch, stable=True, expected=3)
    assert main(["gate", "--run", str(run)]) == 3
    bundle = tmp_path / "unstructured.zip"
    assert main(["export", "--run", str(run), "--output", str(bundle)]) == 0
    assert main(["gate", "--bundle", str(bundle)]) == 3


def test_export_limits_lock_and_non_overwrite(tmp_path, monkeypatch):
    root, patch = setup_project(tmp_path, monkeypatch)
    run = verify(root, patch)
    output = tmp_path / "exists.zip"
    output.write_bytes(b"keep original")
    assert main(["export", "--run", str(run), "--output", str(output)]) == 4
    assert output.read_bytes() == b"keep original"
    assert main(["export", "--run", str(run), "--output", str(run / "inside.zip")]) == 4
    with RunLock(run):
        assert main(["gate", "--run", str(run)]) == 4
        assert main(["export", "--run", str(run), "--output", str(tmp_path / "locked.zip")]) == 4
    monkeypatch.setattr("mimo_harness.bundles.MAX_TOTAL_BYTES", 10)
    assert main(["export", "--run", str(run), "--output", str(tmp_path / "large.zip")]) == 4
    assert not (tmp_path / "large.zip").exists()
    assert not list(tmp_path.glob(".mimo-export-*"))


def test_full_agent_run_exports_without_raw_model_responses(tmp_path, monkeypatch):
    root, patch = setup_project(tmp_path, monkeypatch, stable=True)
    real = harness.call_model
    def response(task, *args, **kw):
        if task.task_type in {"bug_fix", "code_refactor", "test_generation", "patch_generation"}:
            return "```diff\n" + patch.read_text() + "```", {"private-provider-payload": True}, 1, 1, 2
        return real(task, *args, **kw)
    monkeypatch.setattr(harness, "call_model", response)
    assert main(["run", "--name", "agents", "--limit-tasks", "5", "--run-tests"]) == 0
    run = root / ".mimo/runs/agents"
    assert main(["gate", "--run", str(run)]) == 0
    output = tmp_path / "agents.zip"
    assert main(["export", "--run", str(run), "--output", str(output)]) == 0
    assert main(["gate", "--bundle", str(output)]) == 0
    with zipfile.ZipFile(output) as archive:
        assert all(b"private-provider-payload" not in archive.read(n) for n in archive.namelist())


def test_unittest_proof_and_html_injection_handling(tmp_path, monkeypatch):
    root, patch = setup_project(tmp_path, monkeypatch, stable=True)
    (root / "test_value.py").write_text("import unittest\nfrom value import VALUE\nclass ValueTest(unittest.TestCase):\n    def test_supported(self): self.assertIn(VALUE, (1, 2))\n")
    cfg = read(root / "mimo-harness.json")
    cfg.pop("checks", None)  # stdlib unittest uses the supported auto-detection path
    write(root / "mimo-harness.json", cfg)
    run = verify(root, patch, stable=True)
    assert main(["gate", "--run", str(run)]) == 0
    output = tmp_path / "unittest.zip"
    assert main(["export", "--run", str(run), "--output", str(output)]) == 0
    with BundleReader(output) as reader:
        from mimo_harness.bundles import make_report
        report = make_report(reader)
    report["run"]["run_id"] = '<script>alert("x")</script>'
    page = render_report(report)
    assert "<script>" not in page and "&lt;script&gt;" in page
    report["files"][0]["href"] = "javascript:alert(1)"
    with pytest.raises(ValueError):
        render_report(report)


def test_preexisting_module_skip_is_preserved_in_junit_cross_check(tmp_path, monkeypatch):
    root, patch = setup_project(tmp_path, monkeypatch, stable=True)
    (root / "test_optional.py").write_text("import pytest\npytest.skip('optional module', allow_module_level=True)\n")
    run = verify(root, patch, stable=True)
    assert main(["gate", "--run", str(run)]) == 0
    bundle = tmp_path / "with-skips.zip"
    assert main(["export", "--run", str(run), "--output", str(bundle)]) == 0
    assert main(["gate", "--bundle", str(bundle)]) == 0
