"""Deterministic, free protocol example. Replace with your reviewed Agent adapter."""
import difflib
import json
import sys
from pathlib import Path


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if request["schema_version"] != 1 or request["patch_basis"] != "original_baseline":
        raise ValueError("Unsupported request")
    before = Path("calc.py").read_text(encoding="utf-8")
    # Deliberately demonstrate failed first attempt followed by independent PASS.
    after = before.replace("a - b", "a" if request["round"] == 1 else "a + b")
    patch = "diff --git a/calc.py b/calc.py\n" + "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile="a/calc.py", tofile="b/calc.py"))
    print(json.dumps({"schema_version": 1, "status": "completed", "patch": patch}))


if __name__ == "__main__":
    main()
