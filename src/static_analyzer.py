"""Scalable static analyzer for HarmonyOS ArkTS/TypeScript/Cangjie-like projects.

This version is designed as a 20K+ file ready foundation:
- skips heavy generated folders such as oh_modules, node_modules, build, .git;
- supports max_files and max_file_size guards for large repositories;
- writes deterministic CSV/JSON outputs;
- records performance statistics for scalability reporting;
- keeps the lightweight regex implementation explainable for course reporting.

It is still a research prototype rather than a full ArkTS compiler parser. A
Tree-sitter/AST analyzer can later replace this module behind the same API.
"""
from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

SOURCE_SUFFIXES = {".ets", ".ts", ".cj"}
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".idea", ".vscode", "node_modules", "oh_modules", "build", "dist",
    ".hvigor", "hvigor", "coverage", "out", "tmp", "temp", "__pycache__"
}
RESERVED_CALLS = {
    "if", "for", "while", "switch", "return", "function", "console", "Promise",
    "String", "Number", "Boolean", "Array", "new", "catch", "trim", "test", "length",
    "push", "map", "filter", "reduce", "setTimeout", "Date", "RegExp", "Math",
    "describe", "it", "expect", "assert", "let", "const", "var", "async", "await"
}

@dataclass
class CodeEntity:
    file: str
    entity_type: str
    name: str
    exported: bool
    line: int

@dataclass
class DependencyRecord:
    source_file: str
    source_entity: str
    source_line: int
    relation_type: str
    target_name: str
    target_file: str
    dependency_strength: str
    evidence: str

class HarmonyStaticAnalyzer:
    def __init__(
        self,
        project_root: str | Path,
        source_suffixes: Optional[Sequence[str]] = None,
        exclude_dirs: Optional[Sequence[str]] = None,
        max_files: Optional[int] = None,
        max_file_size_kb: int = 1024,
    ):
        self.project_root = Path(project_root).resolve()
        self.source_suffixes = set(source_suffixes or SOURCE_SUFFIXES)
        self.exclude_dirs = set(exclude_dirs or DEFAULT_EXCLUDE_DIRS)
        self.max_files = max_files
        self.max_file_size_kb = max_file_size_kb
        self.files: List[Path] = []
        self.entities: List[CodeEntity] = []
        self.dependencies: List[DependencyRecord] = []
        self.symbol_to_file: Dict[str, str] = {}
        self.symbol_to_files: Dict[str, List[str]] = defaultdict(list)
        self.performance: Dict[str, float | int | str] = {}

    def _is_excluded(self, path: Path) -> bool:
        parts = set(path.relative_to(self.project_root).parts) if path.is_relative_to(self.project_root) else set(path.parts)
        return bool(parts & self.exclude_dirs)

    def scan_files(self) -> List[Path]:
        start = time.perf_counter()
        files: List[Path] = []
        for p in self.project_root.rglob("*"):
            if self._is_excluded(p):
                continue
            if not p.is_file() or p.suffix not in self.source_suffixes:
                continue
            try:
                if p.stat().st_size > self.max_file_size_kb * 1024:
                    continue
            except OSError:
                continue
            files.append(p)
            if self.max_files and len(files) >= self.max_files:
                break
        self.files = sorted(files)
        self.performance["scan_seconds"] = round(time.perf_counter() - start, 4)
        self.performance["scanned_file_count"] = len(self.files)
        self.performance["max_files"] = self.max_files or "unlimited"
        self.performance["excluded_dirs"] = ",".join(sorted(self.exclude_dirs))
        return self.files

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()

    def _read(self, path: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="ignore")

    def _line_of(self, text: str, index: int) -> int:
        return text.count("\n", 0, index) + 1

    def _resolve_import_file(self, current: Path, raw_module: str) -> str:
        if not raw_module.startswith("."):
            return raw_module
        base = (current.parent / raw_module).resolve()
        candidates = [base]
        for suffix in self.source_suffixes:
            candidates.append(base.with_suffix(suffix))
        for suffix in self.source_suffixes:
            candidates.append(base / f"index{suffix}")
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return self._rel(candidate)
        return raw_module

    def extract_entities(self) -> List[CodeEntity]:
        start = time.perf_counter()
        self.entities.clear()
        patterns = [
            ("function", re.compile(r"(export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")),
            ("class", re.compile(r"(export\s+)?class\s+(\w+)\s*")),
            ("struct", re.compile(r"(export\s+)?struct\s+(\w+)\s*")),
            ("arrow_function", re.compile(r"(export\s+)?const\s+(\w+)\s*=\s*(?:\([^)]*\)|\w+)\s*(?::\s*[\w<>\[\]\|]+)?\s*=>")),
            ("method", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]\|]+)?\s*\{", re.MULTILINE)),
        ]
        for path in self.files or self.scan_files():
            text = self._read(path)
            rel = self._rel(path)
            seen = set()
            for entity_type, pattern in patterns:
                for match in pattern.finditer(text):
                    name = match.group(2) if entity_type != "method" else match.group(1)
                    exported = bool(match.group(1)) if entity_type != "method" else False
                    if name in RESERVED_CALLS or name in seen:
                        continue
                    seen.add(name)
                    self.entities.append(CodeEntity(rel, entity_type, name, exported, self._line_of(text, match.start())))
        self.symbol_to_files.clear()
        for entity in self.entities:
            self.symbol_to_files[entity.name].append(entity.file)
        self.symbol_to_file = {name: files[0] for name, files in self.symbol_to_files.items()}
        self.performance["entity_extract_seconds"] = round(time.perf_counter() - start, 4)
        self.performance["entity_count"] = len(self.entities)
        return self.entities

    def _entity_for_line(self, rel_file: str, line: int) -> CodeEntity | None:
        candidates = [e for e in self.entities if e.file == rel_file and e.line <= line]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.line)

    def _strength(self, relation_type: str, source_file: str, target_file: str) -> str:
        if relation_type == "call" and target_file != "unknown" and target_file != source_file:
            return "strong"
        if relation_type == "import":
            return "medium"
        return "weak"

    def extract_dependencies(self) -> List[DependencyRecord]:
        start = time.perf_counter()
        if not self.entities:
            self.extract_entities()
        self.dependencies.clear()
        import_pattern = re.compile(r"import\s+\{([^}]+)\}\s+from\s+[\"']([^\"']+)[\"']")
        default_import_pattern = re.compile(r"import\s+(\w+)\s+from\s+[\"']([^\"']+)[\"']")
        call_pattern = re.compile(r"(?<!function\s)(?<!class\s)\b([A-Za-z_]\w*)\s*\(")
        seen_records = set()
        entity_by_file: Dict[str, List[CodeEntity]] = defaultdict(list)
        for e in self.entities:
            entity_by_file[e.file].append(e)
        for rel in entity_by_file:
            entity_by_file[rel].sort(key=lambda e: e.line)

        for path in self.files:
            text = self._read(path)
            rel = self._rel(path)
            # named imports: import { A, B as C } from './x'
            for match in import_pattern.finditer(text):
                line = self._line_of(text, match.start())
                raw_module = match.group(2)
                resolved_module = self._resolve_import_file(path, raw_module)
                names = [part.strip().split(" as ")[0].strip() for part in match.group(1).split(",")]
                for name in names:
                    target_file = self.symbol_to_file.get(name, resolved_module)
                    key = (rel, line, "import", name, target_file)
                    if key not in seen_records:
                        seen_records.add(key)
                        self.dependencies.append(DependencyRecord(rel, "<file>", line, "import", name, target_file, self._strength("import", rel, target_file), match.group(0)))
            # default imports
            for match in default_import_pattern.finditer(text):
                line = self._line_of(text, match.start())
                name = match.group(1)
                raw_module = match.group(2)
                target_file = self._resolve_import_file(path, raw_module)
                key = (rel, line, "import", name, target_file)
                if key not in seen_records:
                    seen_records.add(key)
                    self.dependencies.append(DependencyRecord(rel, "<file>", line, "import", name, target_file, self._strength("import", rel, target_file), match.group(0)))
            for match in call_pattern.finditer(text):
                call = match.group(1)
                if call in RESERVED_CALLS:
                    continue
                target_file = self.symbol_to_file.get(call, "unknown")
                if target_file == "unknown" or target_file == rel:
                    continue
                line = self._line_of(text, match.start())
                candidates = [e for e in entity_by_file.get(rel, []) if e.line <= line]
                entity = max(candidates, key=lambda e: e.line) if candidates else None
                source_entity = entity.name if entity else "<file>"
                key = (rel, source_entity, line, "call", call, target_file)
                if key not in seen_records:
                    seen_records.add(key)
                    self.dependencies.append(DependencyRecord(rel, source_entity, line, "call", call, target_file, self._strength("call", rel, target_file), f"{call}(...)") )
        self.performance["dependency_extract_seconds"] = round(time.perf_counter() - start, 4)
        self.performance["dependency_count"] = len(self.dependencies)
        return self.dependencies

    def summarize(self) -> Dict[str, int | float | str]:
        relation_types = {record.relation_type for record in self.dependencies}
        cross_file = [r for r in self.dependencies if r.target_file != "unknown" and r.target_file != r.source_file]
        strong = [r for r in self.dependencies if r.dependency_strength == "strong"]
        suffix_counts = Counter(p.suffix for p in self.files)
        return {
            "source_file_count": len(self.files),
            "entity_count": len(self.entities),
            "dependency_count": len(self.dependencies),
            "cross_file_dependency_count": len(cross_file),
            "strong_dependency_count": len(strong),
            "relation_type_count": len(relation_types),
            "suffix_counts": dict(suffix_counts),
            "scalability_mode": "20k_ready_foundation",
            **self.performance,
        }

    def export_csv(self, output_path: str | Path) -> None:
        output_path = Path(output_path); output_path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["source_file", "source_entity", "source_line", "relation_type", "target_name", "target_file", "dependency_strength", "evidence"]
        with output_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            for record in self.dependencies:
                writer.writerow(asdict(record))

    def export_summary_json(self, output_path: str | Path) -> None:
        output_path = Path(output_path); output_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "summary": self.summarize(),
            "entities": [asdict(e) for e in self.entities[:2000]],
            "entity_export_note": "For large repositories only the first 2000 entities are embedded in JSON; full dependencies are in CSV.",
        }
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def analyze_project(
    project_root: str | Path,
    output_csv: str | Path,
    output_summary: str | Path | None = None,
    max_files: Optional[int] = None,
    exclude_dirs: Optional[Sequence[str]] = None,
    source_suffixes: Optional[Sequence[str]] = None,
    max_file_size_kb: int = 1024,
):
    analyzer = HarmonyStaticAnalyzer(
        project_root,
        max_files=max_files,
        exclude_dirs=exclude_dirs,
        source_suffixes=source_suffixes,
        max_file_size_kb=max_file_size_kb,
    )
    total_start = time.perf_counter()
    analyzer.scan_files(); analyzer.extract_entities(); analyzer.extract_dependencies(); analyzer.export_csv(output_csv)
    analyzer.performance["total_seconds"] = round(time.perf_counter() - total_start, 4)
    if output_summary: analyzer.export_summary_json(output_summary)
    return analyzer

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    analyzer = analyze_project(root / "demo_harmony_project", root / "outputs" / "dependencies.csv", root / "outputs" / "analysis_summary.json")
    print(json.dumps(analyzer.summarize(), ensure_ascii=False, indent=2))
