"""Offline public-command acceptance plus corruption checks for pinned cases."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimo_harness.cli import main
from mimo_harness.manifests import atomic_json
from mimo_harness.real_cases import CASES, _git, patch_for, validate_case
from test_cli_repair import ORIGINAL


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(["init", "--quiet"], upstream)
    (upstream / "calc.py").write_text(ORIGINAL, encoding="utf-8", newline="\n")
    _git(["add", "calc.py"], upstream)
    _git(["-c", "user.name=Case Test", "-c", "user.email=test@example.invalid",
          "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Known defect"], upstream)
    base = _git(["rev-parse", "HEAD"], upstream)
    spec = {"repository": str(upstream), "upstream_base": base, "upstream_fix": "1" * 40,
            "license_url": "https://example.invalid/LICENSE", "source_file": "calc.py",
            "old": ORIGINAL, "fixed": ORIGINAL.replace("a - b", "a + b"),
            "regressed": ORIGINAL.replace("a - b", "a + b").replace("a * b", "0"),
            "target": "test_add", "task": "Fix addition; preserve multiplication.",
            "tests": "from calc import add, mul\n\ndef test_add():\n    assert add(3, 2) == 5\n\ndef test_mul():\n    assert mul(3, 2) == 6\n"}
    monkeypatch.setitem(CASES, "local-fixture", spec)
    root = tmp_path / "prepared"
    assert main(["case-init", "--case", "local-fixture", "--output", str(root)]) == 0
    return SimpleNamespace(root=root, spec=spec)


def test_pinned_case_public_workflow_and_no_overwrite(prepared):
    c = prepared
    record = validate_case(c.root)
    assert record["upstream_base"] == c.spec["upstream_base"]
    assert main(["case-check", "--directory", str(c.root), "--name", "first"]) == 0
    report_path = c.root / "results/first/acceptance.json"
    original = report_path.read_bytes()
    report = json.loads(original)
    assert report["status"] == "PASS" and report["model_called"] is False
    assert report["mode"] == "upstream_reference_controls"
    assert [r["exit_code"] for r in report["controls"]] == [0, 2]
    for result in report["controls"]:
        bundle = report_path.parent / result["bundle"]
        assert bundle.is_file()
        assert main(["gate", "--bundle", str(bundle)]) == result["exit_code"]
    assert (c.root / "project/calc.py").read_text() == ORIGINAL
    assert main(["case-check", "--directory", str(c.root), "--name", "first"]) == 4
    assert main(["case-init", "--case", "local-fixture", "--output", str(c.root)]) == 4
    assert report_path.read_bytes() == original


@pytest.mark.parametrize("target", ["project/calc.py", "project/.mimo_acceptance/test_regression.py",
                                   "reference.patch", "mimo-harness.json"])
def test_modified_source_acceptance_or_controls_are_rejected(prepared, target):
    with (prepared.root / target).open("a", encoding="utf-8") as stream:
        stream.write("\n# changed input\n")
    assert main(["case-check", "--directory", str(prepared.root), "--name", "changed"]) == 4
    assert not (prepared.root / "results/changed").exists()


@pytest.mark.parametrize("field,value", [("files", None), ("files", []), ("case_id", []),
                                      ("mode", "live_model"), ("schema_version", True),
                                      ("upstream_fix", "0" * 40)])
def test_malformed_or_forged_case_metadata_fails_visibly(prepared, field, value):
    path = prepared.root / "case.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    atomic_json(path, record)
    assert main(["case-check", "--directory", str(prepared.root)]) == 4


def test_reference_generation_requires_unique_upstream_fragment():
    spec = CASES["boltons-write"]
    for source in ("unrelated source", spec["old"] * 2):
        with pytest.raises(ValueError, match="Pinned source fragment"):
            patch_for(spec, source, spec["fixed"])
