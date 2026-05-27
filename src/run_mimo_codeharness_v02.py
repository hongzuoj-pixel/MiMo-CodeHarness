"""MiMo-CodeHarness v0.2

A repository-level multi-agent evaluation harness for real-world code projects.

Core capabilities:
1. multi-language repository scan and dependency extraction;
2. repository-level benchmark task generation;
3. OpenAI-compatible MiMo/GPT/DeepSeek style model execution, with dry-run fallback;
4. safe patch extraction/application inside an isolated copied worktree;
5. build/test or syntax-check execution;
6. token usage logging, scoring, HTML dashboard, technical report and paper draft.

The script is intentionally stdlib-only so it can run on Windows/Linux/macOS without
extra installation. Real API execution is enabled by --execute and environment keys.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import html
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scoring_v03 import score_outputs_v03
except Exception:  # keep legacy harness runnable if copied without scoring_v03.py
    score_outputs_v03 = None

ROOT = Path(__file__).resolve().parents[1]

LANGUAGE_SUFFIXES: Dict[str, List[str]] = {
    "HarmonyOS/ArkTS": [".ets"],
    "TypeScript/JavaScript": [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
    "Python": [".py", ".ipynb"],
    "C/C++/Embedded": [".c", ".h", ".cpp", ".cc", ".cxx", ".hpp"],
    "Java/Kotlin": [".java", ".kt", ".kts"],
    "Go": [".go"],
    "Rust": [".rs"],
    "Shell": [".sh", ".bash", ".zsh", ".ps1", ".bat"],
    "Config/Data": [".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".properties"],
    "Docs": [".md", ".rst", ".txt"],
    "Web/UI": [".html", ".css", ".scss", ".vue"],
}
SUPPORTED_SUFFIXES = {suffix for values in LANGUAGE_SUFFIXES.values() for suffix in values}
EXCLUDE_DIRS = {
    ".git", ".idea", ".vscode", "node_modules", "oh_modules", "build", "dist", "out",
    "coverage", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv", "env",
    "target", ".gradle", ".hvigor", "hvigor", "checkpoints", "datasets", "data/raw",
}
MAX_SNIPPET_CHARS = 2800


@dataclass
class FileRecord:
    path: str
    suffix: str
    language: str
    size_bytes: int
    line_count: int
    role: str


@dataclass
class DependencyEdge:
    source_file: str
    relation_type: str
    target: str
    target_file: str
    line: int
    evidence: str
    strength: str


@dataclass
class HarnessTask:
    task_id: str
    task_type: str
    difficulty: str
    title: str
    description: str
    related_files: List[str]
    expected_output: str
    scoring_points: List[str]


@dataclass
class ModelRunRecord:
    task_id: str
    provider_name: str
    model: str
    status: str
    dry_run: bool
    time_seconds: float
    input_tokens_est: int
    output_tokens_est: int
    total_tokens_est: int
    content: str
    error: str
    raw: Dict[str, Any]


@dataclass
class PatchResult:
    task_id: str
    provider_name: str
    status: str
    patch_file: str
    worktree: str
    returncode: Optional[int]
    changed_files: str
    stdout: str
    stderr: str
    applied: bool
    note: str


@dataclass
class CheckResult:
    name: str
    status: str
    command: str
    returncode: Optional[int]
    time_seconds: float
    stdout_log: str
    stderr_log: str
    note: str


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text_safe(path: Path, max_chars: Optional[int] = None) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            text = path.read_text(encoding=enc, errors="strict")
            return text[:max_chars] if max_chars else text
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text[:max_chars] if max_chars else text
    except OSError:
        return ""


def relpath(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def language_for_suffix(suffix: str) -> str:
    for lang, suffixes in LANGUAGE_SUFFIXES.items():
        if suffix in suffixes:
            return lang
    return "Other"


def infer_role(rel: str, suffix: str) -> str:
    lower = rel.lower()
    name = Path(rel).name.lower()
    if "readme" in name or suffix in {".md", ".rst"}:
        return "documentation"
    if "test" in lower or "spec" in lower:
        return "test"
    if name in {"package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "hvigorfile.ts"}:
        return "build_config"
    if any(x in lower for x in ["config", "setting", "profile", "manifest"]):
        return "config"
    if any(x in lower for x in ["src/", "entry/", "app/", "main/", "groot/"]):
        return "source"
    return "source" if suffix in SUPPORTED_SUFFIXES else "other"


def is_excluded(path: Path, root: Path) -> bool:
    try:
        parts = set(path.relative_to(root).parts)
    except ValueError:
        parts = set(path.parts)
    return bool(parts & EXCLUDE_DIRS)


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def estimate_tokens(text: str) -> int:
    # Conservative mixed Chinese/English approximation. This is for logging only;
    # real API usage is also read from API response when available.
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, int(ascii_chars / 4 + non_ascii / 1.6))


class RepositoryScanner:
    def __init__(self, project_root: Path, max_files: int = 800, max_file_size_kb: int = 512):
        self.project_root = project_root.resolve()
        self.max_files = max_files
        self.max_file_size_kb = max_file_size_kb
        self.files: List[FileRecord] = []
        self.snippets: Dict[str, str] = {}

    def scan(self) -> List[FileRecord]:
        files: List[FileRecord] = []
        for path in self.project_root.rglob("*"):
            if len(files) >= self.max_files:
                break
            if is_excluded(path, self.project_root) or not path.is_file():
                continue
            suffix = path.suffix.lower()
            important = path.name.lower() in {"readme.md", "requirements.txt", "package.json", "pyproject.toml", "go.mod", "cargo.toml"}
            if suffix not in SUPPORTED_SUFFIXES and not important:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > self.max_file_size_kb * 1024:
                continue
            rel = relpath(path, self.project_root)
            text = read_text_safe(path, max_chars=MAX_SNIPPET_CHARS)
            files.append(FileRecord(
                path=rel,
                suffix=suffix,
                language=language_for_suffix(suffix),
                size_bytes=size,
                line_count=text.count("\n") + 1 if text else 0,
                role=infer_role(rel, suffix),
            ))
            if len(self.snippets) < 80 and text:
                self.snippets[rel] = text
        self.files = sorted(files, key=lambda r: r.path)
        return self.files

    def summary(self) -> Dict[str, Any]:
        lang_counts = Counter(f.language for f in self.files)
        role_counts = Counter(f.role for f in self.files)
        suffix_counts = Counter(f.suffix or "<no_suffix>" for f in self.files)
        return {
            "project_root": str(self.project_root),
            "file_count": len(self.files),
            "language_counts": dict(lang_counts.most_common()),
            "role_counts": dict(role_counts.most_common()),
            "suffix_counts": dict(suffix_counts.most_common()),
            "total_lines": sum(f.line_count for f in self.files),
            "total_size_bytes": sum(f.size_bytes for f in self.files),
            "sample_files": [f.path for f in self.files[:30]],
        }


class DependencyReasoner:
    IMPORT_PATTERNS = [
        ("python_import", re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import\s+([\w\*,\s]+)|import\s+([\w\.,\s]+))", re.MULTILINE)),
        ("js_import", re.compile(r"^\s*import\s+(?:.+?\s+from\s+)?[\"']([^\"']+)[\"']", re.MULTILINE)),
        ("js_require", re.compile(r"require\([\"']([^\"']+)[\"']\)")),
        ("c_include", re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]", re.MULTILINE)),
        ("java_import", re.compile(r"^\s*import\s+([\w\.\*]+)\s*;", re.MULTILINE)),
        ("rust_use", re.compile(r"^\s*use\s+([^;]+);", re.MULTILINE)),
        ("go_import", re.compile(r"^\s*import\s+(?:\((.*?)\)|\"([^\"]+)\")", re.MULTILINE | re.DOTALL)),
        ("markdown_link", re.compile(r"\[[^\]]+\]\(([^)]+)\)")),
    ]
    CALL_PATTERN = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
    DEF_PATTERNS = [
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?(?:class|struct|interface)\s+([A-Za-z_]\w*)", re.MULTILINE),
        re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.MULTILINE),
        re.compile(r"^\s*(?:static\s+)?(?:void|int|char|float|double|bool|uint\w*_t|HAL_StatusTypeDef)\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE),
    ]
    RESERVED = {"if", "for", "while", "switch", "return", "function", "class", "print", "len", "sizeof"}

    def __init__(self, project_root: Path, files: List[FileRecord], snippets: Dict[str, str]):
        self.project_root = project_root.resolve()
        self.files = files
        self.snippets = snippets
        self.symbol_to_file: Dict[str, str] = {}
        self.edges: List[DependencyEdge] = []

    def _index_symbols(self) -> None:
        self.symbol_to_file.clear()
        for rel, text in self.snippets.items():
            for pattern in self.DEF_PATTERNS:
                for m in pattern.finditer(text):
                    name = m.group(1)
                    self.symbol_to_file.setdefault(name, rel)

    def _resolve_local_target(self, source_rel: str, target: str) -> str:
        if not target or target.startswith(("http://", "https://", "#")):
            return target
        # Relative module path resolution.
        src_dir = (self.project_root / source_rel).parent
        candidates = []
        raw = target.split("#", 1)[0]
        if raw.startswith(".") or raw.startswith("/"):
            base = (src_dir / raw).resolve() if raw.startswith(".") else (self.project_root / raw.lstrip("/")).resolve()
            candidates.extend([base])
            for suffix in SUPPORTED_SUFFIXES:
                candidates.append(base.with_suffix(suffix))
                candidates.append(base / f"index{suffix}")
        else:
            # Try direct file name/header match for C includes and markdown links.
            for record in self.files:
                if Path(record.path).name == raw or record.path.endswith(raw):
                    return record.path
        for c in candidates:
            try:
                if c.exists() and c.is_file():
                    return relpath(c, self.project_root)
            except OSError:
                pass
        return target

    def analyze(self) -> List[DependencyEdge]:
        self._index_symbols()
        edges: List[DependencyEdge] = []
        seen = set()
        for record in self.files:
            rel = record.path
            text = self.snippets.get(rel) or read_text_safe(self.project_root / rel, max_chars=MAX_SNIPPET_CHARS)
            if not text:
                continue
            for relation_type, pattern in self.IMPORT_PATTERNS:
                for m in pattern.finditer(text):
                    target = ""
                    if relation_type == "python_import":
                        target = m.group(1) or m.group(3) or ""
                    elif relation_type == "go_import" and m.group(1):
                        imports = re.findall(r"\"([^\"]+)\"", m.group(1))
                        for imp in imports:
                            self._add_edge(edges, seen, rel, relation_type, imp, m.start(), m.group(0))
                        continue
                    else:
                        target = next((g for g in m.groups() if g), "")
                    self._add_edge(edges, seen, rel, relation_type, target, m.start(), m.group(0))
            # Lightweight cross-file call edges.
            for m in self.CALL_PATTERN.finditer(text):
                name = m.group(1)
                if name in self.RESERVED:
                    continue
                target_file = self.symbol_to_file.get(name)
                if target_file and target_file != rel:
                    self._add_edge(edges, seen, rel, "symbol_call", name, m.start(), m.group(0), target_file=target_file)
        self.edges = edges
        return edges

    def _line_of(self, text: str, index: int) -> int:
        return text.count("\n", 0, index) + 1

    def _add_edge(self, edges: List[DependencyEdge], seen: set, rel: str, relation_type: str, target: str, index: int, evidence: str, target_file: Optional[str] = None) -> None:
        text = self.snippets.get(rel, "")
        line = self._line_of(text, index) if text else 1
        target_file = target_file or self._resolve_local_target(rel, target)
        strength = "strong" if target_file and target_file != target and target_file != rel else "medium" if relation_type.endswith("import") else "weak"
        key = (rel, relation_type, target, target_file, line)
        if key in seen:
            return
        seen.add(key)
        edges.append(DependencyEdge(rel, relation_type, target, target_file, line, evidence[:200], strength))

    def summary(self) -> Dict[str, Any]:
        return {
            "dependency_count": len(self.edges),
            "relation_counts": dict(Counter(e.relation_type for e in self.edges).most_common()),
            "strong_edges": sum(1 for e in self.edges if e.strength == "strong"),
            "sample_edges": [asdict(e) for e in self.edges[:20]],
        }


class TaskGeneratorV02:
    def __init__(self, files: List[FileRecord], edges: List[DependencyEdge], snippets: Dict[str, str]):
        self.files = files
        self.edges = edges
        self.snippets = snippets

    def _top_files(self, n: int = 5) -> List[str]:
        score = Counter()
        for f in self.files:
            base = 2 if f.role in {"source", "build_config", "test"} else 1
            score[f.path] += base
        for e in self.edges:
            score[e.source_file] += 2
            if e.target_file and "/" in e.target_file:
                score[e.target_file] += 1
        return [p for p, _ in score.most_common(n)] or [f.path for f in self.files[:n]]

    def generate(self, limit: int = 8) -> List[HarnessTask]:
        top = self._top_files(8)
        dependency_samples = sorted({e.source_file for e in self.edges[:20]})[:5]
        tasks = [
            HarnessTask(
                "T001", "repo_understanding", "easy", "Repository-level architecture understanding",
                "总结该仓库的主要目标、核心模块、入口文件、配置文件和潜在工程风险。回答必须引用真实文件路径。",
                top[:6], "结构化项目理解报告", ["项目目标", "核心文件", "模块职责", "风险点", "后续改进"],
            ),
            HarnessTask(
                "T002", "dependency_explanation", "medium", "Cross-file dependency reasoning",
                "基于依赖边和相关文件，解释该仓库的跨文件依赖链，指出哪些文件最可能影响构建或运行。",
                dependency_samples or top[:5], "依赖链解释与影响范围分析", ["跨文件关系", "影响范围", "关键依赖", "证据路径"],
            ),
            HarnessTask(
                "T003", "bug_localization", "medium", "Potential bug localization",
                "假设项目出现运行失败或功能异常，请根据仓库结构定位最值得优先检查的 3 个文件，并解释原因。",
                top[:5], "Bug 定位分析", ["定位理由", "相关文件", "排查顺序", "测试建议"],
            ),
            HarnessTask(
                "T004", "test_generation", "medium", "Test plan generation",
                "为该仓库设计一组最小可执行测试方案，包括静态检查、单元测试、构建检查和人工验证点。",
                top[:5], "测试方案或测试代码草案", ["测试目标", "测试步骤", "预期结果", "边界条件"],
            ),
            HarnessTask(
                "T005", "code_refactor", "hard", "Patch-style maintainability improvement",
                "输出一个安全、最小、可回滚的 unified diff patch，用于增加仓库说明或改进错误处理。不要修改业务核心逻辑。",
                top[:3], "unified diff patch + 修改解释", ["patch 格式", "最小修改", "可回滚", "风险说明"],
            ),
            HarnessTask(
                "T006", "risk_analysis", "medium", "Engineering risk analysis",
                "从工程化角度评估该仓库的可维护性、可测试性、依赖风险、部署风险和 token 成本。",
                top[:5], "风险分析报告", ["可维护性", "可测试性", "依赖风险", "成本", "缓解方案"],
            ),
        ]
        if any(f.language == "C/C++/Embedded" for f in self.files):
            tasks.append(HarnessTask(
                "T007", "embedded_iot_review", "hard", "Embedded/IoT control-flow review",
                "该仓库包含 C/C++/嵌入式代码。请分析外设初始化、通信协议、状态机或错误处理链路。",
                [f.path for f in self.files if f.language == "C/C++/Embedded"][:6],
                "嵌入式控制逻辑审查", ["外设/协议", "状态流", "错误处理", "实时性风险"],
            ))
        if any(f.language == "Python" for f in self.files):
            tasks.append(HarnessTask(
                "T008", "python_tooling_review", "medium", "Python tooling workflow review",
                "该仓库包含 Python 工具链。请分析 CLI/GUI/数据处理入口、异常处理和可测试性。",
                [f.path for f in self.files if f.language == "Python"][:6],
                "Python 工具链审查", ["入口函数", "数据流", "异常处理", "测试建议"],
            ))
        return tasks[:limit]


def load_model_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"models": [{"name": "LocalMock", "provider_type": "mock", "model": "dry-run-code-agent", "enabled": True}], "default_max_tokens": 2000}
    text = path.read_text(encoding="utf-8")
    for key, value in os.environ.items():
        text = text.replace("${" + key + "}", value)
    return json.loads(text)


def build_prompt(task: HarnessTask, repo_summary: Dict[str, Any], dep_summary: Dict[str, Any], snippets: Dict[str, str]) -> str:
    related_snippets = []
    for file in task.related_files[:6]:
        text = snippets.get(file, "")
        if text:
            related_snippets.append(f"### {file}\n```text\n{text[:1800]}\n```")
    return f"""
你是 MiMo-CodeHarness v0.2 中的代码智能体，正在完成真实代码仓库级评测任务。

【仓库概况】
{json.dumps(repo_summary, ensure_ascii=False, indent=2)[:6000]}

【依赖概况】
{json.dumps(dep_summary, ensure_ascii=False, indent=2)[:6000]}

【任务】
- task_id: {task.task_id}
- task_type: {task.task_type}
- difficulty: {task.difficulty}
- title: {task.title}
- description: {task.description}
- related_files: {json.dumps(task.related_files, ensure_ascii=False)}
- expected_output: {task.expected_output}
- scoring_points: {json.dumps(task.scoring_points, ensure_ascii=False)}

【相关文件片段】
{chr(10).join(related_snippets) if related_snippets else '无可读取片段，请基于仓库摘要回答。'}

请按以下结构回答：
1. 需求理解
2. 相关文件定位
3. 依赖/调用链分析
4. 解决方案或 patch 建议
5. build/test 验证方案
6. 风险与补充信息

如果任务要求 patch，请优先输出一个小而安全的 ```diff 代码块。不要编造不存在的大段源码。
""".strip()


def normalize_openai_chat_url(base_url: str) -> str:
    """Accept either a base URL ending with /v1 or a full /chat/completions URL."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url


def http_post_json(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def extract_usage(raw: Dict[str, Any], prompt: str, content: str) -> Tuple[int, int, int]:
    usage = raw.get("usage") or {}
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or estimate_tokens(prompt)
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or estimate_tokens(content)
    total_tokens = usage.get("total_tokens") or int(input_tokens) + int(output_tokens)
    return int(input_tokens), int(output_tokens), int(total_tokens)


def mock_model_response(task: HarnessTask, repo_summary: Dict[str, Any], snippets: Dict[str, str]) -> str:
    files = task.related_files[:4]
    file_lines = "\n".join(f"- {p}" for p in files) or "- 暂无关键文件"
    if task.task_type == "code_refactor":
        return f"""
1. 需求理解
本任务要求在不触碰核心业务逻辑的前提下，输出一个可回滚的小 patch，用于增加 CodeHarness 评测说明。

2. 相关文件定位
{file_lines}

3. 依赖/调用链分析
本次 patch 只新增说明文件，不影响现有依赖链和 build/test 流程。

4. 解决方案或 patch 建议
```diff
diff --git a/CODEHARNESS_AGENT_NOTE.md b/CODEHARNESS_AGENT_NOTE.md
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/CODEHARNESS_AGENT_NOTE.md
@@ -0,0 +1,5 @@
+# CodeHarness Agent Note
+
+This file was generated inside an isolated worktree by MiMo-CodeHarness v0.2.
+It demonstrates safe patch extraction, patch check, and patch application.
+The original repository files are not modified.
```

5. build/test 验证方案
执行 patch check，再运行 Python py_compile / JSON parse / C syntax scan 等安全检查。

6. 风险与补充信息
该 patch 只新增 Markdown 文件，风险低；若接入真实模型，需要人工复核 patch 内容后再合并。
""".strip()
    return f"""
1. 需求理解
这是一个仓库级代码智能体评测任务，需要基于真实文件结构完成项目理解、依赖分析和验证建议。

2. 相关文件定位
{file_lines}

3. 依赖/调用链分析
仓库共扫描到 {repo_summary.get('file_count', 0)} 个文件，语言分布为 {repo_summary.get('language_counts', {})}。应优先关注 source、build_config、test 三类文件。

4. 解决方案或 patch 建议
建议先生成项目摘要、关键依赖图和最小任务集，再逐步扩展到真实 patch 应用与 build/test 结果评分。

5. build/test 验证方案
运行静态语法检查、配置文件解析、可选构建命令和测试命令，并记录日志路径。

6. 风险与补充信息
主要风险是模型上下文截断、依赖解析不完整、真实构建环境缺失。需要保留 token 日志和失败案例以便复现实验。
""".strip()


def call_model(task: HarnessTask, model_cfg: Dict[str, Any], prompt: str, execute: bool, repo_summary: Dict[str, Any], snippets: Dict[str, str]) -> Tuple[str, Dict[str, Any], int, int, int]:
    provider_type = model_cfg.get("provider_type", "openai_compatible")
    if (not execute) or provider_type == "mock":
        content = mock_model_response(task, repo_summary, snippets)
        return content, {"mock": True}, estimate_tokens(prompt), estimate_tokens(content), estimate_tokens(prompt) + estimate_tokens(content)
    api_key_env = model_cfg.get("api_key_env", "MIMO_API_KEY")
    api_key = os.getenv(api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {api_key_env}")
    base_url = model_cfg.get("base_url") or os.getenv("MIMO_BASE_URL", "")
    if not base_url:
        raise RuntimeError("Missing base_url. Set config base_url or MIMO_BASE_URL.")
    base_url = normalize_openai_chat_url(base_url)
    model = os.getenv("MIMO_MODEL") or model_cfg.get("model", "mimo-v2.5-pro")
    timeout = int(model_cfg.get("timeout_seconds", 180))
    max_tokens = int(model_cfg.get("max_tokens", 4000))
    temperature = float(model_cfg.get("temperature", 0.2))
    headers = {
        # Xiaomi MiMo official docs use the custom `api-key` header.
        # This avoids HTTP 401 invalid_key errors caused by using Authorization: Bearer.
        "api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a rigorous repository-level code agent evaluator. Prefer precise file paths and safe patches."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    raw = http_post_json(base_url, headers, body, timeout)
    content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
    input_tokens, output_tokens, total_tokens = extract_usage(raw, prompt, content)
    return content, raw, input_tokens, output_tokens, total_tokens


def run_models(tasks: List[HarnessTask], config: Dict[str, Any], execute: bool, repo_summary: Dict[str, Any], dep_summary: Dict[str, Any], snippets: Dict[str, str]) -> List[ModelRunRecord]:
    models = [m for m in config.get("models", []) if m.get("enabled", True)] or [{"name": "LocalMock", "provider_type": "mock", "model": "dry-run-code-agent"}]
    rows: List[ModelRunRecord] = []
    for task in tasks:
        prompt = build_prompt(task, repo_summary, dep_summary, snippets)
        for model_cfg in models:
            start = time.perf_counter()
            status = "completed"
            error = ""
            content = ""
            raw: Dict[str, Any] = {}
            in_tok = out_tok = total_tok = 0
            try:
                content, raw, in_tok, out_tok, total_tok = call_model(task, model_cfg, prompt, execute, repo_summary, snippets)
            except Exception as exc:
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
                raw = {"traceback": traceback.format_exc(limit=4)}
            rows.append(ModelRunRecord(
                task_id=task.task_id,
                provider_name=str(model_cfg.get("name", "unknown")),
                model=str(model_cfg.get("model", "unknown")),
                status=status,
                dry_run=not execute or model_cfg.get("provider_type") == "mock",
                time_seconds=round(time.perf_counter() - start, 4),
                input_tokens_est=in_tok,
                output_tokens_est=out_tok,
                total_tokens_est=total_tok,
                content=content,
                error=error,
                raw=raw,
            ))
    return rows


def extract_diff_block(content: str) -> str:
    fence = re.search(r"```(?:diff|patch)\s*\n(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip() + "\n"
    idx = content.find("diff --git ")
    if idx >= 0:
        return content[idx:].strip() + "\n"
    return ""


MEDIA_OR_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".mp4", ".mov", ".avi", ".wmv", ".mp3", ".wav",
    ".zip", ".rar", ".7z", ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".uvguix", ".uvopt", ".uvoptx", ".bak",
}


def copy_worktree(project_root: Path, dst: Path) -> Path:
    """Copy a source tree for isolated patch checking.

    v0.3.1 intentionally skips large/binary media files. They are not needed for
    patch application, static build checks, or code scoring, and on Windows they
    often cause path-length/copy failures in nested worktree directories.
    """
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)

    def ignore(dirpath: str, names: List[str]) -> set:
        ignored = set()
        for n in names:
            if n in EXCLUDE_DIRS or n in {"outputs", "figures", "screenshots", ".git"}:
                ignored.add(n)
                continue
            suffix = Path(n).suffix.lower()
            if suffix in MEDIA_OR_BINARY_SUFFIXES:
                ignored.add(n)
        return ignored

    try:
        shutil.copytree(project_root, dst, ignore=ignore)
    except shutil.Error as exc:
        # Best effort fallback: keep the harness running and copy source-like files only.
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True, exist_ok=True)
        source_suffixes = {".py", ".c", ".h", ".cpp", ".hpp", ".cc", ".java", ".kt", ".js", ".ts", ".ets", ".json", ".yaml", ".yml", ".md", ".txt", ".sh", ".bat", ".ps1", ".xml", ".html", ".css"}
        for src in project_root.rglob("*"):
            if not src.is_file() or is_excluded(src, project_root):
                continue
            if src.suffix.lower() not in source_suffixes:
                continue
            rel = src.relative_to(project_root)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, target)
            except OSError:
                continue
    return dst


def run_cmd(cmd: List[str], cwd: Path, timeout: int = 60) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def apply_patches(records: List[ModelRunRecord], source_project: Path, out_dir: Path, actually_apply: bool = False) -> List[PatchResult]:
    patch_dir = ensure_dir(out_dir / "patches")
    worktree_root = ensure_dir(out_dir / "worktrees")
    results: List[PatchResult] = []
    for idx, record in enumerate(records, start=1):
        patch = extract_diff_block(record.content)
        patch_file = patch_dir / f"{record.task_id}_{record.provider_name}_{idx}.patch"
        if not patch:
            results.append(PatchResult(record.task_id, record.provider_name, "no_patch", "", "", None, "", "", "", False, "No diff block found in model output."))
            continue
        patch_file.write_text(patch, encoding="utf-8")
        worktree = copy_worktree(source_project, worktree_root / f"{record.task_id}_{record.provider_name}_{idx}")
        try:
            check_code, check_out, check_err = run_cmd(["git", "apply", "--check", str(patch_file)], worktree)
            if check_code != 0:
                results.append(PatchResult(record.task_id, record.provider_name, "check_failed", str(patch_file), str(worktree), check_code, "", check_out, check_err, False, "git apply --check failed."))
                continue
            if actually_apply:
                apply_code, apply_out, apply_err = run_cmd(["git", "apply", str(patch_file)], worktree)
                changed = list_changed_files(worktree)
                results.append(PatchResult(record.task_id, record.provider_name, "applied" if apply_code == 0 else "apply_failed", str(patch_file), str(worktree), apply_code, ";".join(changed), apply_out, apply_err, apply_code == 0, "Patch applied in isolated copied worktree."))
            else:
                results.append(PatchResult(record.task_id, record.provider_name, "checked", str(patch_file), str(worktree), check_code, "", check_out, check_err, False, "Patch is syntactically applicable. Use --apply-patches to apply inside copied worktree."))
        except Exception as exc:
            results.append(PatchResult(record.task_id, record.provider_name, "error", str(patch_file), str(worktree), None, "", "", str(exc), False, "Patch application raised exception."))
    return results


def list_changed_files(worktree: Path) -> List[str]:
    changed: List[str] = []
    for p in worktree.rglob("*"):
        if p.is_file() and not is_excluded(p, worktree):
            rel = relpath(p, worktree)
            if rel == "CODEHARNESS_AGENT_NOTE.md":
                changed.append(rel)
    return changed


class BuildTesterV02:
    def __init__(self, project_root: Path, out_dir: Path):
        self.project_root = project_root.resolve()
        self.out_dir = ensure_dir(out_dir)

    def _log(self, name: str, stdout: str, stderr: str) -> Tuple[str, str]:
        stdout_path = self.out_dir / f"{name}_stdout.log"
        stderr_path = self.out_dir / f"{name}_stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8", errors="ignore")
        stderr_path.write_text(stderr, encoding="utf-8", errors="ignore")
        return str(stdout_path), str(stderr_path)

    def run_safe_checks(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        start = time.perf_counter()
        py_errors = []
        for py in self.project_root.rglob("*.py"):
            if is_excluded(py, self.project_root):
                continue
            try:
                py_compile.compile(str(py), doraise=True)
            except Exception as exc:
                py_errors.append(f"{relpath(py, self.project_root)}: {exc}")
        out, err = self._log("python_py_compile", "\n".join(py_errors), "")
        results.append(CheckResult("python_py_compile", "ok" if not py_errors else "failed", "py_compile all .py", 0 if not py_errors else 1, round(time.perf_counter()-start, 4), out, err, "Stdlib Python syntax check."))

        start = time.perf_counter()
        json_errors = []
        for js in list(self.project_root.rglob("*.json"))[:200]:
            if is_excluded(js, self.project_root):
                continue
            try:
                json.loads(read_text_safe(js))
            except Exception as exc:
                json_errors.append(f"{relpath(js, self.project_root)}: {exc}")
        out, err = self._log("json_parse", "\n".join(json_errors), "")
        results.append(CheckResult("json_parse", "ok" if not json_errors else "failed", "json.loads all .json", 0 if not json_errors else 1, round(time.perf_counter()-start, 4), out, err, "JSON config parse check."))

        start = time.perf_counter()
        c_warnings = []
        for cfile in list(self.project_root.rglob("*.c"))[:80] + list(self.project_root.rglob("*.h"))[:80]:
            if is_excluded(cfile, self.project_root):
                continue
            text = read_text_safe(cfile)
            if text.count("{") != text.count("}"):
                c_warnings.append(f"{relpath(cfile, self.project_root)}: brace count mismatch")
        out, err = self._log("c_brace_scan", "\n".join(c_warnings), "")
        results.append(CheckResult("c_brace_scan", "ok" if not c_warnings else "warning", "brace scan for .c/.h", 0 if not c_warnings else 2, round(time.perf_counter()-start, 4), out, err, "Lightweight embedded C safety scan; not a compiler."))
        return results

    def detect_external_commands(self) -> List[Tuple[str, List[str]]]:
        cmds: List[Tuple[str, List[str]]] = []
        if (self.project_root / "package.json").exists():
            cmds.append(("npm_test", ["npm", "test", "--", "--watch=false"]))
        if (self.project_root / "pyproject.toml").exists() or (self.project_root / "pytest.ini").exists() or any(self.project_root.rglob("test_*.py")):
            cmds.append(("pytest", [sys.executable, "-m", "pytest", "-q"]))
        if (self.project_root / "go.mod").exists():
            cmds.append(("go_test", ["go", "test", "./..."]))
        if (self.project_root / "Cargo.toml").exists():
            cmds.append(("cargo_test", ["cargo", "test", "--quiet"]))
        return cmds

    def run_external_commands(self, enabled: bool = False, timeout: int = 180) -> List[CheckResult]:
        results: List[CheckResult] = []
        for name, cmd in self.detect_external_commands():
            start = time.perf_counter()
            if not enabled:
                out, err = self._log(name, "External command detected but not executed. Use --run-build-tests to enable.", "")
                results.append(CheckResult(name, "skipped", " ".join(cmd), None, round(time.perf_counter()-start, 4), out, err, "Skipped by default for safe offline execution."))
                continue
            try:
                code, stdout, stderr = run_cmd(cmd, self.project_root, timeout=timeout)
                out, err = self._log(name, stdout, stderr)
                results.append(CheckResult(name, "ok" if code == 0 else "failed", " ".join(cmd), code, round(time.perf_counter()-start, 4), out, err, "Real external build/test command."))
            except Exception as exc:
                out, err = self._log(name, "", str(exc))
                results.append(CheckResult(name, "error", " ".join(cmd), None, round(time.perf_counter()-start, 4), out, err, "External command raised exception."))
        return results

    def run_all(self, run_external: bool = False) -> Dict[str, Any]:
        results = self.run_safe_checks() + self.run_external_commands(enabled=run_external)
        summary = {
            "project_root": str(self.project_root),
            "run_external": run_external,
            "check_count": len(results),
            "ok_count": sum(1 for r in results if r.status == "ok"),
            "failed_count": sum(1 for r in results if r.status in {"failed", "error"}),
            "warning_count": sum(1 for r in results if r.status == "warning"),
            "skipped_count": sum(1 for r in results if r.status == "skipped"),
            "all_critical_ok": all(r.status not in {"failed", "error"} for r in results),
            "results": [asdict(r) for r in results],
        }
        write_json(self.out_dir / "build_test_summary.json", summary)
        return summary


def score_outputs(tasks: List[HarnessTask], runs: List[ModelRunRecord], patch_results: List[PatchResult], build_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    task_map = {t.task_id: t for t in tasks}
    patch_map = defaultdict(list)
    for p in patch_results:
        patch_map[(p.task_id, p.provider_name)].append(p)
    rows: List[Dict[str, Any]] = []
    keywords = {
        "understanding": ["需求", "理解", "目标", "architecture", "purpose"],
        "files": ["文件", "路径", "related", "file", "入口"],
        "deps": ["依赖", "调用", "import", "include", "链", "模块"],
        "tests": ["测试", "验证", "build", "test", "compile", "py_compile"],
        "risk": ["风险", "边界", "回滚", "成本", "token", "privacy", "安全"],
    }
    for r in runs:
        task = task_map.get(r.task_id)
        text = r.content.lower()
        score = 0
        dims = {}
        for dim, kws in keywords.items():
            hit = any(k.lower() in text for k in kws)
            dims[dim] = 12 if hit else 4
            score += dims[dim]
        file_hits = 0
        if task:
            for f in task.related_files:
                if f.lower() in text or Path(f).name.lower() in text:
                    file_hits += 1
        file_score = min(20, file_hits * 6)
        score += file_score
        patch_statuses = [p.status for p in patch_map.get((r.task_id, r.provider_name), [])]
        patch_score = 10 if "applied" in patch_statuses else 6 if "checked" in patch_statuses else 2 if patch_statuses else 0
        build_score = 10 if build_summary.get("all_critical_ok") else 4
        score += patch_score + build_score
        if r.status != "completed":
            score = 0
        rows.append({
            "task_id": r.task_id,
            "task_type": task.task_type if task else "unknown",
            "provider_name": r.provider_name,
            "model": r.model,
            "status": r.status,
            "dry_run": r.dry_run,
            "file_hits": file_hits,
            "file_score": file_score,
            "patch_status": ";".join(patch_statuses),
            "patch_score": patch_score,
            "build_score": build_score,
            "total_score": min(score, 100),
            "pass": r.status == "completed" and score >= 70,
            "input_tokens_est": r.input_tokens_est,
            "output_tokens_est": r.output_tokens_est,
            "total_tokens_est": r.total_tokens_est,
            "time_seconds": r.time_seconds,
            **{f"dim_{k}": v for k, v in dims.items()},
            "error": r.error,
        })
    return rows


def summarize_scores(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_provider: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_provider[str(row["provider_name"])].append(row)
    providers = []
    for provider, items in by_provider.items():
        providers.append({
            "provider_name": provider,
            "run_count": len(items),
            "avg_score": round(sum(float(i["total_score"]) for i in items) / max(len(items), 1), 2),
            "pass_rate": round(sum(1 for i in items if i["pass"]) / max(len(items), 1), 4),
            "total_tokens_est": sum(int(i["total_tokens_est"]) for i in items),
            "avg_time_seconds": round(sum(float(i["time_seconds"]) for i in items) / max(len(items), 1), 3),
        })
    return {
        "run_count": len(rows),
        "provider_count": len(by_provider),
        "providers": sorted(providers, key=lambda x: x["avg_score"], reverse=True),
        "total_tokens_est": sum(int(i["total_tokens_est"]) for i in rows),
        "overall_avg_score": round(sum(float(i["total_score"]) for i in rows) / max(len(rows), 1), 2),
    }


def generate_dashboard(out_dir: Path, repo_summary: Dict[str, Any], dep_summary: Dict[str, Any], tasks: List[HarnessTask], score_summary: Dict[str, Any], build_summary: Dict[str, Any]) -> Path:
    provider_rows = "".join(
        f"<tr><td>{html.escape(p['provider_name'])}</td><td>{p['run_count']}</td><td>{p['avg_score']}</td><td>{p['pass_rate']}</td><td>{p['total_tokens_est']}</td></tr>"
        for p in score_summary.get("providers", [])
    )
    lang_cards = "".join(
        f"<div class='pill'><span>{html.escape(k)}</span><b>{v}</b></div>" for k, v in repo_summary.get("language_counts", {}).items()
    )
    task_cards = "".join(
        f"<div class='task'><b>{html.escape(t.task_id)} · {html.escape(t.task_type)}</b><p>{html.escape(t.description)}</p></div>" for t in tasks
    )
    html_text = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiMo-CodeHarness v0.2 Dashboard</title>
<style>
:root{{--bg:#f7f8fb;--ink:#0f172a;--muted:#667085;--card:rgba(255,255,255,.82);--line:#e5e7eb;--orange:#ff6900;--blue:#1677ff;}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 10%,#ffe4cf,transparent 30%),radial-gradient(circle at 90% 20%,#cfe7ff,transparent 32%),var(--bg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC',Arial,sans-serif;color:var(--ink)}}
header{{position:sticky;top:0;background:rgba(255,255,255,.72);backdrop-filter:blur(18px);border-bottom:1px solid var(--line);padding:18px 48px;display:flex;justify-content:space-between;align-items:center}}
.logo{{display:flex;gap:14px;align-items:center;font-weight:800}}.badge{{background:#111827;color:#fff;border-radius:14px;padding:10px 13px}} main{{padding:42px 6vw}} .hero{{display:grid;grid-template-columns:1.1fr .9fr;gap:28px;align-items:stretch}} 
.card{{background:var(--card);border:1px solid rgba(255,255,255,.8);box-shadow:0 20px 70px rgba(15,23,42,.08);border-radius:34px;padding:32px}} h1{{font-size:58px;line-height:1.02;margin:0 0 18px}} h2{{font-size:28px;margin:0 0 18px}} .muted{{color:var(--muted);line-height:1.8;font-size:18px}} .metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:25px}} .metric{{border-radius:24px;background:#fff;padding:20px;border:1px solid var(--line)}} .metric b{{font-size:36px}} .metric span{{display:block;color:var(--muted)}}
.pills{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}}.pill{{display:flex;justify-content:space-between;align-items:center;background:#fff;border:1px solid var(--line);border-radius:20px;padding:16px 18px}}.pill b{{color:var(--orange)}}
section{{margin-top:28px}} table{{width:100%;border-collapse:separate;border-spacing:0 10px}} td,th{{text-align:left;padding:14px 18px;background:#fff}} th{{color:var(--muted)}} tr td:first-child,tr th:first-child{{border-radius:16px 0 0 16px}} tr td:last-child,tr th:last-child{{border-radius:0 16px 16px 0}} .task{{background:#fff;border:1px solid var(--line);border-radius:22px;padding:18px;margin:12px 0}} .ok{{color:#16a34a}} .warn{{color:#f97316}} code{{background:#111827;color:#d1fae5;border-radius:10px;padding:3px 7px}} @media(max-width:900px){{.hero{{grid-template-columns:1fr}} h1{{font-size:42px}}}}
</style></head><body>
<header><div class="logo"><div class="badge">AI</div><div>MiMo-CodeHarness v0.2</div></div><div>Real Repo · Patch · Build/Test · Dashboard</div></header>
<main>
<div class="hero"><div class="card"><h1>真实代码仓库多 Agent 自动评测 Harness</h1><p class="muted">支持仓库扫描、跨语言依赖分析、任务生成、MiMo/OpenAI-compatible 模型执行、patch 应用、build/test 检查、评分和报告生成。</p><div class="metrics"><div class="metric"><span>文件数</span><b>{repo_summary.get('file_count',0)}</b></div><div class="metric"><span>依赖边</span><b>{dep_summary.get('dependency_count',0)}</b></div><div class="metric"><span>预计 Tokens</span><b>{score_summary.get('total_tokens_est',0)}</b></div></div></div>
<div class="card"><h2>运行状态</h2><p class="muted">Build/Test: <b class="{'ok' if build_summary.get('all_critical_ok') else 'warn'}">{'OK' if build_summary.get('all_critical_ok') else 'Needs Review'}</b></p><p class="muted">平均评分：<b>{score_summary.get('overall_avg_score',0)}</b> / 100</p><p class="muted">任务数量：<b>{len(tasks)}</b></p><p class="muted">输出目录：<code>{html.escape(str(out_dir))}</code></p></div></div>
<section class="card"><h2>语言分布</h2><div class="pills">{lang_cards}</div></section>
<section class="card"><h2>模型评分矩阵</h2><table><tr><th>Provider</th><th>Runs</th><th>Avg Score</th><th>Pass Rate</th><th>Tokens Est.</th></tr>{provider_rows}</table></section>
<section class="card"><h2>生成任务</h2>{task_cards}</section>
<section class="card"><h2>关键文件</h2><p class="muted">{html.escape(', '.join(repo_summary.get('sample_files', [])[:20]))}</p></section>
</main></body></html>"""
    path = out_dir / "dashboard.html"
    path.write_text(html_text, encoding="utf-8")
    return path


def generate_reports(out_dir: Path, project_root: Path, repo_summary: Dict[str, Any], dep_summary: Dict[str, Any], tasks: List[HarnessTask], score_summary: Dict[str, Any], build_summary: Dict[str, Any]) -> Tuple[Path, Path]:
    tech = f"""# MiMo-CodeHarness v0.2 技术报告

## 1. 项目定位

MiMo-CodeHarness v0.2 是一个面向真实代码仓库的多 Agent 自动评测平台。它将原来的 HarmonyOS 文件依赖分析与多模型评分原型升级为更通用的 repository-level evaluation harness，可用于 IoT/嵌入式、Python 工具、HarmonyOS/ArkTS、前端、科研代码等仓库。

## 2. 已实现功能

- 多语言仓库扫描：{', '.join(repo_summary.get('language_counts', {}).keys())}
- 跨文件依赖分析：共提取 {dep_summary.get('dependency_count', 0)} 条依赖边。
- 多 Agent 任务流：Repository Scanner → Dependency Reasoner → Task Generator → Model Runner → Patch Applicator → Build/Test Runner → Evaluator → Report Agent。
- 真实 patch 支持：可从模型输出中提取 unified diff，并在隔离 worktree 中执行 `git apply --check` 和可选真实应用。
- build/test 支持：已接入 Python py_compile、JSON parse、C brace scan，以及 pytest/npm/go/cargo 的自动检测执行入口。
- 可视化 dashboard：输出 `dashboard.html`。
- token 记录：记录 input/output/total token 估算，并兼容真实 API usage 字段。

## 3. 本次运行摘要

- 项目路径：`{project_root}`
- 文件数：{repo_summary.get('file_count', 0)}
- 总行数：{repo_summary.get('total_lines', 0)}
- 生成任务数：{len(tasks)}
- 总 token 估算：{score_summary.get('total_tokens_est', 0)}
- 平均评分：{score_summary.get('overall_avg_score', 0)}
- Build/Test 关键检查：{'通过' if build_summary.get('all_critical_ok') else '需要复核'}

## 4. 论文雏形方向

可扩展为本科科研/课程论文主题：**A Multi-Agent Evaluation Harness for Repository-Level Code Understanding in IoT and Embedded Software Projects**。

核心研究问题：现有代码评测偏算法题与短代码片段，缺少面向真实 IoT/嵌入式/工具型仓库的评测闭环。本项目提供一种可复现 harness：自动扫描仓库、生成任务、调用模型、检查 patch、运行 build/test，并从正确性、依赖理解、工程质量和 token 成本进行综合评估。

## 5. 下一步

1. 填入小米 MiMo API Key，运行真实模型调用；
2. 接入你的 STM32 RFID、SlideNotes、HarmonyOS 与 DreamZero-Libero 作为 case studies；
3. 增加人工标注小样本，校准 LLM Judge 与规则评分；
4. 增加真实 patch 后的单元测试和构建命令；
5. 形成可投递老师的项目说明与暑期研究计划。
"""
    paper = f"""# Paper Draft: MiMo-CodeHarness

## Title
MiMo-CodeHarness: A Multi-Agent Evaluation Harness for Repository-Level Code Understanding in IoT and Embedded Software Projects

## Abstract
Large language models are increasingly used as coding agents, but many evaluations still focus on algorithmic questions or short code snippets. This draft proposes MiMo-CodeHarness, a lightweight multi-agent harness for evaluating repository-level code understanding on real-world projects. The system scans a repository, extracts multi-language dependency evidence, generates benchmark tasks, runs OpenAI-compatible models such as Xiaomi MiMo Pro, applies patch-style outputs inside isolated worktrees, executes build/test checks, and produces reproducible score reports with token usage logs. The current prototype supports HarmonyOS/ArkTS, Python, C/C++ embedded projects, JavaScript/TypeScript, configuration files, and documentation. Future experiments will use STM32 RFID, SlideNotes, HarmonyOS, and robotics research repositories as case studies.

## Proposed Contributions
1. A reproducible multi-agent harness for repository-level code evaluation.
2. A task-generation pipeline covering dependency reasoning, bug localization, test generation, patch proposal, and risk analysis.
3. A combined scoring method using model output quality, patch applicability, build/test status, and token cost.
4. Case studies across IoT/embedded software and AI research repositories.

## Experimental Plan
- Models: MiMo Pro, DeepSeek Coder, GPT-compatible baselines.
- Repositories: STM32 RFID Access Control, SlideNotes GUI, HarmonyOS demo, DreamZero-Libero light analysis.
- Metrics: correctness, dependency understanding, patch applicability, build/test pass rate, maintainability score, token cost, latency.

## Current Prototype Status
This v0.2 implementation has completed the offline harness pipeline and is ready for real MiMo API execution after setting environment variables.
"""
    tech_path = out_dir / "TECHNICAL_REPORT.md"
    paper_path = out_dir / "PAPER_DRAFT.md"
    tech_path.write_text(tech, encoding="utf-8")
    paper_path.write_text(paper, encoding="utf-8")
    return tech_path, paper_path


def run_harness(project: Path, name: str, config_path: Path, limit_tasks: int, max_files: int, execute: bool, apply_patch_flag: bool, run_build_tests: bool, llm_review_path: str = "", human_review_path: str = "", human_rubric_path: str = "") -> Dict[str, Any]:
    project = project.resolve()
    out_dir = ensure_dir(ROOT / "outputs" / "mimo_codeharness_v02" / name)
    start = time.perf_counter()

    scanner = RepositoryScanner(project, max_files=max_files)
    files = scanner.scan()
    repo_summary = scanner.summary()
    reasoner = DependencyReasoner(project, files, scanner.snippets)
    edges = reasoner.analyze()
    dep_summary = reasoner.summary()
    tasks = TaskGeneratorV02(files, edges, scanner.snippets).generate(limit=limit_tasks)
    config = load_model_config(config_path)
    model_runs = run_models(tasks, config, execute, repo_summary, dep_summary, scanner.snippets)
    patch_results = apply_patches(model_runs, project, out_dir, actually_apply=apply_patch_flag)

    # Build/test is run on the first actually patched worktree if available; otherwise on original project.
    build_target = project
    applied = [p for p in patch_results if p.applied and p.worktree]
    if applied:
        build_target = Path(applied[0].worktree)
    build_summary = BuildTesterV02(build_target, out_dir / "build_test_logs").run_all(run_external=run_build_tests)
    
    if score_outputs_v03 is not None:
        score_rows = score_outputs_v03(
            tasks, model_runs, patch_results, build_summary,
            llm_review_path=llm_review_path or None,
            human_review_path=human_review_path or None,
            human_rubric_path=human_rubric_path or None,
        )
        scoring_version = "v0.3.1 evidence-aware + review-ready"
    else:
        score_rows = score_outputs(tasks, model_runs, patch_results, build_summary)
        scoring_version = "legacy v0.2"
    score_summary = summarize_scores(score_rows)

    write_json(out_dir / "repo_inventory.json", [asdict(f) for f in files])
    write_json(out_dir / "repo_summary.json", repo_summary)
    write_json(out_dir / "dependencies.json", [asdict(e) for e in edges])
    write_csv(out_dir / "dependencies.csv", [asdict(e) for e in edges])
    write_json(out_dir / "dependency_summary.json", dep_summary)
    write_json(out_dir / "tasks.json", [asdict(t) for t in tasks])
    write_jsonl(out_dir / "model_outputs.jsonl", [asdict(r) for r in model_runs])
    write_csv(out_dir / "model_runs.csv", [asdict(r) for r in model_runs])
    write_json(out_dir / "patch_results.json", [asdict(p) for p in patch_results])
    write_csv(out_dir / "patch_results.csv", [asdict(p) for p in patch_results])
    write_csv(out_dir / "evaluation_scores.csv", score_rows)
    write_json(out_dir / "evaluation_summary.json", score_summary)
    token_rows = [{
        "task_id": r.task_id,
        "provider_name": r.provider_name,
        "model": r.model,
        "input_tokens_est": r.input_tokens_est,
        "output_tokens_est": r.output_tokens_est,
        "total_tokens_est": r.total_tokens_est,
        "dry_run": r.dry_run,
        "status": r.status,
    } for r in model_runs]
    write_csv(out_dir / "token_usage.csv", token_rows)
    dashboard = generate_dashboard(out_dir, repo_summary, dep_summary, tasks, score_summary, build_summary)
    tech_report, paper_draft = generate_reports(out_dir, project, repo_summary, dep_summary, tasks, score_summary, build_summary)

    run_log = {
        "mode": "mimo_codeharness_v02",
        "project": str(project),
        "name": name,
        "output_dir": str(out_dir),
        "execute_real_api": execute,
        "apply_patches": apply_patch_flag,
        "run_build_tests": run_build_tests,
        "elapsed_seconds": round(time.perf_counter() - start, 4),
        "repo_summary": repo_summary,
        "dependency_summary": dep_summary,
        "task_count": len(tasks),
        "model_run_count": len(model_runs),
        "patch_result_count": len(patch_results),
        "build_summary": build_summary,
        "score_summary": score_summary,
        "scoring_version": scoring_version,
        "scoring_inputs": {
            "llm_review_path": llm_review_path,
            "human_review_path": human_review_path,
            "human_rubric_path": human_rubric_path,
        },
        "key_outputs": {
            "dashboard": str(dashboard),
            "technical_report": str(tech_report),
            "paper_draft": str(paper_draft),
            "token_usage": str(out_dir / "token_usage.csv"),
            "evaluation_scores": str(out_dir / "evaluation_scores.csv"),
            "patch_results": str(out_dir / "patch_results.csv"),
        },
    }
    write_json(out_dir / "run_log.json", run_log)
    return run_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MiMo-CodeHarness v0.2 on a real repository.")
    parser.add_argument("--project", default=str(ROOT / "demo_harmony_project"), help="Repository/project path")
    parser.add_argument("--name", default="demo_harmony_v02", help="Output run name")
    parser.add_argument("--config", default=str(ROOT / "config" / "api_models_mimo.json"), help="Model config JSON")
    parser.add_argument("--limit-tasks", type=int, default=6, help="Task limit")
    parser.add_argument("--max-files", type=int, default=800, help="Max scanned files")
    parser.add_argument("--execute", action="store_true", help="Call real model APIs. Without this flag, dry-run/mock is used.")
    parser.add_argument("--apply-patches", action="store_true", help="Apply extracted patches inside copied worktrees after git apply --check.")
    parser.add_argument("--run-build-tests", action="store_true", help="Run detected external build/test commands. Safe syntax checks always run.")
    parser.add_argument("--llm-review", default="", help="Optional JSON/CSV LLM judge scores for scoring v0.3")
    parser.add_argument("--human-review", default="", help="Optional JSON/CSV human review scores for scoring v0.3")
    parser.add_argument("--human-rubric", default="", help="Optional JSON human rubric file for scoring v0.3")
    args = parser.parse_args()
    log = run_harness(
        project=Path(args.project),
        name=args.name,
        config_path=Path(args.config),
        limit_tasks=args.limit_tasks,
        max_files=args.max_files,
        execute=args.execute,
        apply_patch_flag=args.apply_patches,
        run_build_tests=args.run_build_tests,
        llm_review_path=args.llm_review,
        human_review_path=args.human_review,
        human_rubric_path=args.human_rubric,
    )
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
