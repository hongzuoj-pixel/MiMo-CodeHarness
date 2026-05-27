"""Utilities for connecting real open-source HarmonyOS repositories.

This module is intentionally independent from the core demo pipeline. It can
clone or update the GitCode repositories recommended by the course notice, and
it can also register an already-downloaded local HarmonyOS project as input.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

RECOMMENDED_REPOS: Dict[str, str] = {
    "sample_in_harmonyos": "https://gitcode.com/HarmonyOS_Samples/sample_in_harmonyos.git",
    "guide_snippets": "https://gitcode.com/HarmonyOS_Samples/guide-snippets.git",
}

@dataclass
class RepoStatus:
    name: str
    url: str
    local_path: str
    available: bool
    action: str
    message: str

class RepoManager:
    def __init__(self, root: str | Path = "external_repos"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def clone_or_update(self, name: str, url: Optional[str] = None) -> RepoStatus:
        url = url or RECOMMENDED_REPOS[name]
        local = self.root / name
        try:
            if local.exists() and (local / ".git").exists():
                result = subprocess.run(
                    ["git", "-C", str(local), "pull", "--ff-only"],
                    text=True,
                    capture_output=True,
                    timeout=180,
                )
                ok = result.returncode == 0
                msg = (result.stdout or result.stderr).strip()[:1000]
                return RepoStatus(name, url, str(local), ok, "pull", msg)
            if local.exists() and any(local.iterdir()):
                return RepoStatus(name, url, str(local), True, "reuse_local", "Directory exists and is non-empty; reused as local project.")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(local)],
                text=True,
                capture_output=True,
                timeout=300,
            )
            ok = result.returncode == 0
            msg = (result.stdout or result.stderr).strip()[:1000]
            return RepoStatus(name, url, str(local), ok, "clone", msg)
        except Exception as exc:
            return RepoStatus(name, url, str(local), False, "error", f"{type(exc).__name__}: {exc}")

    def clone_all(self) -> List[RepoStatus]:
        return [self.clone_or_update(name, url) for name, url in RECOMMENDED_REPOS.items()]

    def write_status(self, statuses: List[RepoStatus], output_path: str | Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps([asdict(s) for s in statuses], ensure_ascii=False, indent=2), encoding="utf-8")

def main() -> None:
    parser = argparse.ArgumentParser(description="Clone/update recommended HarmonyOS open-source repositories.")
    parser.add_argument("--repo", choices=list(RECOMMENDED_REPOS) + ["all"], default="all")
    parser.add_argument("--root", default="external_repos")
    parser.add_argument("--out", default="outputs/repo_status.json")
    args = parser.parse_args()
    manager = RepoManager(args.root)
    if args.repo == "all":
        statuses = manager.clone_all()
    else:
        statuses = [manager.clone_or_update(args.repo)]
    manager.write_status(statuses, args.out)
    print(json.dumps([asdict(s) for s in statuses], ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
