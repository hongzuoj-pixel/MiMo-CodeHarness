"""Trusted-base PR runner; candidate code is executed only by the Docker backend."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from mimo_harness.cli import load_project_config


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--image", default="mimo-pr:reviewed")
    args = parser.parse_args(argv)
    project, output = Path(args.project).resolve(), Path(args.output).resolve()
    try:
        if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (args.base, args.head)):
            raise ValueError("Base/head must be full reviewed/event commit SHAs.")
        if project == output or project in output.parents:
            raise ValueError("CI output must be outside the baseline project.")
        if subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() != args.base:
            raise ValueError("The host checkout must be the trusted base commit, not the PR head.")
        config_path = project / "mimo-harness.json"
        committed_policy = subprocess.run(["git", "-C", str(project), "show", f"{args.base}:mimo-harness.json"],
                                          capture_output=True, check=True).stdout
        if config_path.read_bytes() != committed_policy:
            raise ValueError("Acceptance policy must match the trusted base commit exactly.")
        cfg = load_project_config(config_path)
        execution = cfg["execution"]
        if (execution["mode"] != "docker" or execution.get("image") != args.image
                or execution.get("network", "none") != "none"
                or execution.get("prepare_network", "none") != "none" or execution.get("env_allowlist")):
            raise ValueError("CI policy requires the reviewed image, Docker, no network and an empty environment allowlist.")
        if not cfg.get("checks") or not any(c["adapter"] == "pytest" for c in cfg["checks"]):
            raise ValueError("Configure reviewed pytest acceptance checks in the base policy.")
        output.mkdir(parents=True, exist_ok=False)
        patch = output / "input.patch"
        with patch.open("wb") as stream:
            subprocess.run(["git", "-C", str(project), "diff", "--binary", "--full-index", args.base, args.head],
                           stdout=stream, check=True)
        if patch.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("PR diff exceeds the reviewed CI input limit.")
        from mimo_harness.manifests import file_digest
        (output / "ci_context.json").write_text(json.dumps({"base": args.base, "head": args.head,
            "patch_sha256": file_digest(patch)}, indent=2), encoding="utf-8")
        cfg.update(project=str(project), output_dir=str(output / "runs"),
                   model_config=str((project / cfg["model_config"]).resolve()))
        derived = output / "policy.json"
        derived.write_text(json.dumps(cfg), encoding="utf-8")
        prefix = [sys.executable, "-m", "mimo_harness"]
        verified = subprocess.run([*prefix, "--config", str(derived), "verify", "--project", str(project),
            "--base", args.base, "--patch", str(patch), "--run-tests", "--name", "patch"], cwd=output).returncode
        run = output / "runs/patch"
        bundle = output / "evidence.zip"
        exported = subprocess.run([*prefix, "export", "--run", str(run), "--output", str(bundle)], cwd=output).returncode
        gate = subprocess.run([*prefix, "gate", "--bundle", str(bundle), "--expect-base", args.base,
            "--expect-patch", str(patch), "--json"], cwd=output, capture_output=True, text=True)
        (output / "gate.json").write_text(gate.stdout, encoding="utf-8")
        if gate.stderr:
            print(gate.stderr, file=sys.stderr)
        print(gate.stdout)
        # Export is administrative success; it must never mask failed verification.
        if exported != 0 or (gate.returncode == 0 and verified != 0):
            return 4
        return gate.returncode if gate.returncode in {0, 2, 3, 4} else 4
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"[CI ERROR] {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
