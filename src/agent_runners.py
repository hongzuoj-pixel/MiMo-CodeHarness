"""Extensible AgentRunner framework for multi-Agent Code Agent evaluation.

The default runner is MockAgentRunner so the project is always reproducible.
DeepSeekRunner is implemented as an optional real API adapter. CLIAgentRunner
is a safe generic adapter for OpenCode, Claude Code, CodeBuddy CLI and Qoder CLI.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional

@dataclass
class AgentRunResult:
    task_id: str
    agent_name: str
    model_name: str
    status: str
    prompt_chars: int
    response_chars: int
    time_seconds: float
    output_path: str
    error: str = ""

class BaseAgentRunner:
    def __init__(self, agent_name: str, model_name: str = "unknown"):
        self.agent_name = agent_name
        self.model_name = model_name

    def build_prompt(self, task: Dict) -> str:
        return f"""
You are a Code Agent being evaluated on a HarmonyOS ArkTS benchmark task.

Task ID: {task.get('task_id')}
Scenario: {task.get('scenario_type')}
Task type: {task.get('task_type')}
Difficulty: {task.get('difficulty')}
Description: {task.get('description')}
Related files: {task.get('related_files')}
Expected behavior: {task.get('expected_behavior')}
Round prompts: {task.get('round_prompts')}
Scoring points: {task.get('scoring_points')}

Please output:
1. Requirement understanding
2. Files to inspect or modify
3. Step-by-step modification plan
4. Test and compile checks
5. Risks and final summary
""".strip()

    def run_task(self, task: Dict, output_dir: str | Path) -> AgentRunResult:
        raise NotImplementedError

class MockAgentRunner(BaseAgentRunner):
    def run_task(self, task: Dict, output_dir: str | Path) -> AgentRunResult:
        start = time.perf_counter()
        prompt = self.build_prompt(task)
        response = {
            "mode": "mock",
            "task_id": task.get("task_id"),
            "agent": self.agent_name,
            "model": self.model_name,
            "summary": "Mock response used for reproducible offline evaluation.",
            "located_files": task.get("related_files", [])[:5],
            "round_decisions": [
                "Understand requirement and dependency chain.",
                "Modify related ArkTS files consistently.",
                "Run compile/test checks and produce final summary."
            ],
        }
        out_dir = Path(output_dir); out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{task.get('task_id')}_{self.agent_name}.json"
        out_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        return AgentRunResult(task.get("task_id"), self.agent_name, self.model_name, "ok", len(prompt), len(json.dumps(response, ensure_ascii=False)), round(time.perf_counter()-start,4), str(out_path))

class DeepSeekRunner(BaseAgentRunner):
    def __init__(self, model_name: str = "deepseek-chat", api_key_env: str = "DEEPSEEK_API_KEY"):
        super().__init__("DeepSeek", model_name)
        self.api_key_env = api_key_env

    def run_task(self, task: Dict, output_dir: str | Path) -> AgentRunResult:
        start = time.perf_counter()
        prompt = self.build_prompt(task)
        api_key = os.getenv(self.api_key_env)
        out_dir = Path(output_dir); out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{task.get('task_id')}_DeepSeek.json"
        if not api_key:
            error = f"Missing environment variable {self.api_key_env}."
            out_path.write_text(json.dumps({"error": error, "prompt_preview": prompt[:500]}, ensure_ascii=False, indent=2), encoding="utf-8")
            return AgentRunResult(task.get("task_id"), self.agent_name, self.model_name, "skipped", len(prompt), 0, round(time.perf_counter()-start,4), str(out_path), error)
        try:
            from openai import OpenAI  # optional dependency
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            resp = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are a rigorous software engineering Code Agent."},
                    {"role": "user", "content": prompt},
                ],
                stream=False,
            )
            content = resp.choices[0].message.content
            out_path.write_text(json.dumps({"task_id": task.get("task_id"), "agent": self.agent_name, "model": self.model_name, "response": content}, ensure_ascii=False, indent=2), encoding="utf-8")
            return AgentRunResult(task.get("task_id"), self.agent_name, self.model_name, "ok", len(prompt), len(content or ""), round(time.perf_counter()-start,4), str(out_path))
        except Exception as exc:
            out_path.write_text(json.dumps({"error": str(exc), "prompt_preview": prompt[:500]}, ensure_ascii=False, indent=2), encoding="utf-8")
            return AgentRunResult(task.get("task_id"), self.agent_name, self.model_name, "error", len(prompt), 0, round(time.perf_counter()-start,4), str(out_path), str(exc))

class CLIAgentRunner(BaseAgentRunner):
    def __init__(self, agent_name: str, command_template: str, model_name: str = "cli-default", timeout_seconds: int = 600):
        super().__init__(agent_name, model_name)
        self.command_template = command_template
        self.timeout_seconds = timeout_seconds

    def run_task(self, task: Dict, output_dir: str | Path) -> AgentRunResult:
        start = time.perf_counter()
        prompt = self.build_prompt(task)
        out_dir = Path(output_dir); out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{task.get('task_id')}_{self.agent_name.replace(' ', '_')}.txt"
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".prompt.txt") as f:
            f.write(prompt)
            prompt_file = f.name
        cmd = self.command_template.format(prompt_file=prompt_file)
        try:
            completed = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=self.timeout_seconds)
            output = completed.stdout + "\n" + completed.stderr
            out_path.write_text(output, encoding="utf-8")
            status = "ok" if completed.returncode == 0 else "error"
            error = "" if completed.returncode == 0 else f"returncode={completed.returncode}"
            return AgentRunResult(task.get("task_id"), self.agent_name, self.model_name, status, len(prompt), len(output), round(time.perf_counter()-start,4), str(out_path), error)
        except Exception as exc:
            out_path.write_text(str(exc), encoding="utf-8")
            return AgentRunResult(task.get("task_id"), self.agent_name, self.model_name, "error", len(prompt), 0, round(time.perf_counter()-start,4), str(out_path), str(exc))
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

def result_to_dict(result: AgentRunResult) -> Dict:
    return asdict(result)
