"""Managed native node:test invocation; standalone inside the read-only runner mount."""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from .structured_cases import load_node, validate_node_version, write_junit
else:
    from structured_cases import load_node, validate_node_version, write_junit


def main():
    output, args, root, junit = Path(sys.argv[1]), json.loads(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    try:
        version = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        validate_node_version(version)
        files = []
        for arg in args:
            for match in sorted(glob.glob(arg, recursive=True)):
                path = Path(match)
                if set(path.parts) & {".git", ".mimo", ".venv", "node_modules", "outputs", "build", "dist"}:
                    continue
                if path.is_symlink():
                    raise ValueError("NODE_TEST_SYMLINK")
                path.resolve().relative_to(root.resolve())
                if path.is_file() and str(path.resolve()) not in files:
                    files.append(str(path.resolve()))
        if not files:
            data = {"schema_version": 1, "adapter": "node", "complete": True, "cases": {}, "collected": [],
                    "collection_errors": [], "collection_skips": [], "runner_version": version, "exit_code": 0}
            output.write_text(json.dumps(data), encoding="utf-8")
            write_junit(data, junit)
            return 0
        env = dict(os.environ, MIMO_NODE_EVIDENCE=str(output), MIMO_NODE_ROOT=str(root.resolve()))
        reporter = Path(__file__).with_name("node_reporter.mjs").resolve().as_uri()
        command = ["node", "--test", "--test-concurrency=1", "--test-reporter=" + reporter, *files]
        code = subprocess.call(command, env=env)
        data = load_node(output)
        data["exit_code"] = code
        output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        write_junit(data, junit)
        return code
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
