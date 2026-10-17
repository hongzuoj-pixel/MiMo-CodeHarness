"""Generate inspectable PASS/FAIL demo bundles while asserting public CLI exits."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    output = Path(parser.parse_args().output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "fixture"
    project.mkdir()
    (project / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "test_value.py").write_text("from value import VALUE\ndef test_value(): assert VALUE == 2\n", encoding="utf-8")
    cli = [sys.executable, "-m", "mimo_harness"]
    subprocess.run([*cli, "init"], cwd=project, check=True)
    cfg = json.loads((project / "mimo-harness.json").read_text())
    cfg["checks"] = [{"id": "acceptance", "adapter": "pytest", "args": ["-q"],
                      "target_tests": ["test_value.py::test_value"]}]
    (project / "mimo-harness.json").write_text(json.dumps(cfg), encoding="utf-8")
    for name, value, expected in (("PASS", 2, 0), ("FAIL", 3, 2)):
        case = output / name
        case.mkdir()
        patch = case / "input.patch"
        patch.write_text(f"--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = {value}\n", encoding="utf-8")
        result = subprocess.run([*cli, "verify", "--base", "WORKTREE", "--patch", str(patch),
            "--task-type", "bug_fix", "--run-tests", "--name", name], cwd=project)
        assert result.returncode == expected, (name, result.returncode)
        run = project / ".mimo/runs" / name
        bundle = case / "evidence.zip"
        subprocess.run([*cli, "export", "--run", str(run), "--output", str(bundle)], cwd=project, check=True)
        gate = subprocess.run([*cli, "gate", "--bundle", str(bundle), "--expect-patch", str(patch), "--json"],
                              capture_output=True, text=True)
        (case / "gate.json").write_text(gate.stdout, encoding="utf-8")
        assert gate.returncode == expected, gate.stdout + gate.stderr
        assert json.loads(gate.stdout)["allowed"] is (expected == 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
