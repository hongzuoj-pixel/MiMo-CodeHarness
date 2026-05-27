"""Real build/test command runner for HarmonyOS benchmark evaluation.

This module is intentionally optional. By default the project uses offline
metrics for reproducible course demonstration. When a real DevEco/HarmonyOS
build environment is available, enable real_commands in config/industrial_config.json
or pass --enable-real-commands to industrial_pipeline.py.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional

@dataclass
class CommandResult:
    name: str
    command: str
    enabled: bool
    status: str
    returncode: Optional[int]
    time_seconds: float
    stdout_log: str
    stderr_log: str
    error: str = ""

class BuildTestRunner:
    def __init__(self, project_root: str | Path, output_dir: str | Path, timeout_seconds: int = 1800):
        self.project_root = Path(project_root).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def run_command(self, name: str, command: str, enabled: bool = False) -> CommandResult:
        start = time.perf_counter()
        stdout_path = self.output_dir / f"{name}_stdout.log"
        stderr_path = self.output_dir / f"{name}_stderr.log"
        if not enabled:
            msg = "Command not executed because real command mode is disabled."
            stdout_path.write_text(msg, encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            return CommandResult(name, command, False, "skipped", None, round(time.perf_counter()-start, 4), str(stdout_path), str(stderr_path), msg)
        try:
            completed = subprocess.run(
                shlex.split(command),
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            stdout_path.write_text(completed.stdout or "", encoding="utf-8", errors="ignore")
            stderr_path.write_text(completed.stderr or "", encoding="utf-8", errors="ignore")
            status = "ok" if completed.returncode == 0 else "failed"
            return CommandResult(name, command, True, status, completed.returncode, round(time.perf_counter()-start, 4), str(stdout_path), str(stderr_path))
        except Exception as exc:
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(str(exc), encoding="utf-8")
            return CommandResult(name, command, True, "error", None, round(time.perf_counter()-start, 4), str(stdout_path), str(stderr_path), str(exc))

    def run_all(self, commands: Dict, enabled: bool = False) -> Dict:
        timeout = int(commands.get("timeout_seconds", self.timeout_seconds))
        self.timeout_seconds = timeout
        results = []
        for name in ("install", "build", "test"):
            cmd = commands.get(name, "")
            if not cmd:
                continue
            results.append(asdict(self.run_command(name, cmd, enabled=enabled)))
        summary = {
            "real_command_enabled": enabled,
            "project_root": str(self.project_root),
            "results": results,
            "all_ok": bool(results) and all(r["status"] == "ok" for r in results) if enabled else False,
            "note": "Skipped results are expected unless DevEco/HarmonyOS build environment is configured."
        }
        (self.output_dir / "build_test_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
