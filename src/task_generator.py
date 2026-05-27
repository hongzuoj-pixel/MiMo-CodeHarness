"""Final benchmark task generator for HarmonyOS Code Agent evaluation.

The generator reads dependency records produced by static_analyzer.py and
constructs cross-file, multi-round benchmark tasks. It covers three realistic
complex-development scenarios required by the assignment:
1) new feature development, 2) Android-to-HarmonyOS migration, 3) bug fixing.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List

AGENT_CANDIDATES = ["OpenCode", "Claude Code", "CodeBuddy CLI", "Qoder CLI"]
MODEL_CANDIDATES = ["DeepSeek v4", "MiniMax-M2.7", "kimi-k2.6", "GLM-5.1", "Qwen3.6"]

@dataclass
class BenchmarkTask:
    task_id: str
    scenario_type: str
    task_type: str
    difficulty: str
    description: str
    related_files: List[str]
    expected_behavior: str
    precondition: str
    round_prompts: List[str]
    key_nodes: List[str]
    scoring_points: Dict[str, int]
    objective_checks: List[str]
    candidate_agents: List[str]
    candidate_models: List[str]

TASK_TEMPLATES = {
    "validateUsername": {
        "scenario_type": "代码问题修复",
        "task_type": "bug_fix",
        "description": "修复用户名校验逻辑，使空字符串、纯空格和长度小于 3 的用户名均无法通过。",
        "expected": "合法用户名返回 true；空字符串、纯空格和短用户名返回 false。",
        "files": ["utils/Validator.ets", "pages/LoginPage.ets", "tests/Validator.test.ets"],
    },
    "validatePassword": {
        "scenario_type": "代码问题修复",
        "task_type": "bug_fix",
        "description": "增强密码校验逻辑，要求密码长度不少于 6 位，并能在登录流程中正确拦截弱密码。",
        "expected": "弱密码无法登录；合法密码可以继续进入 UserService.login。",
        "files": ["utils/Validator.ets", "services/UserService.ets", "pages/LoginPage.ets"],
    },
    "validatePhone": {
        "scenario_type": "新特性开发",
        "task_type": "feature_development",
        "description": "为手机号绑定流程补充中国大陆手机号格式校验，并保证页面层能正确处理失败提示。",
        "expected": "非法手机号返回 false；合法 11 位手机号允许进入绑定服务。",
        "files": ["utils/Validator.ets", "pages/LoginPage.ets", "services/UserService.ets"],
    },
    "login": {
        "scenario_type": "代码问题修复",
        "task_type": "cross_file_bug_fix",
        "description": "修改登录流程，使页面层、校验工具和服务层之间保持一致的失败处理逻辑。",
        "expected": "输入非法时不调用服务层；输入合法时调用 UserService.login。",
        "files": ["pages/LoginPage.ets", "services/UserService.ets", "utils/Validator.ets"],
    },
    "bindPhone": {
        "scenario_type": "新特性开发",
        "task_type": "cross_file_feature",
        "description": "完善手机号绑定功能，要求 LoginPage.bindPhone 与 UserService.bindPhone 的规则保持一致。",
        "expected": "页面校验和服务层校验结果一致，不出现页面通过但服务层失败的情况。",
        "files": ["pages/LoginPage.ets", "services/UserService.ets", "utils/Validator.ets"],
    },
    "getProfile": {
        "scenario_type": "测试补全",
        "task_type": "test_generation",
        "description": "为主页数据加载链路补充测试，验证 HomePage.loadHomeData 能调用 UserService.getProfile。",
        "expected": "测试能够覆盖 HomePage、UserService、Logger、UserModel 的跨文件调用链路。",
        "files": ["pages/HomePage.ets", "services/UserService.ets", "models/UserModel.ets", "utils/Logger.ets"],
    },
    "showToast": {
        "scenario_type": "代码问题修复",
        "task_type": "behavior_check",
        "description": "检查页面提示信息是否经过统一 ToastUtil 输出，避免页面层直接返回原始错误字符串。",
        "expected": "登录、绑定手机号和首页加载均应返回统一格式的提示结果。",
        "files": ["utils/ToastUtil.ets", "pages/LoginPage.ets", "pages/HomePage.ets"],
    },
    "explainValidation": {
        "scenario_type": "新特性开发",
        "task_type": "cross_file_feature",
        "description": "统一校验失败说明，要求 Validator、UserService 与页面层使用相同错误信息来源。",
        "expected": "用户名、密码、手机号错误信息均由 explainValidation 和 ToastUtil 共同生成。",
        "files": ["utils/Validator.ets", "utils/ToastUtil.ets", "pages/LoginPage.ets", "services/UserService.ets"],
    },
}

MIGRATION_TASK = {
    "scenario_type": "应用迁移",
    "task_type": "android_to_harmony_migration",
    "description": "将 Android LoginActivity 中的用户名、密码校验与登录入口迁移为 HarmonyOS ArkTS 页面逻辑，并保持页面层、服务层、工具层的一致性。",
    "expected": "迁移后的 LoginPage.ets 能复用 Validator.ets 和 UserService.ets；非法输入被页面层拦截，合法输入进入服务层。",
    "files": [
        "demo_android_project/app/src/main/java/com/example/LoginActivity.java",
        "pages/LoginPage.ets",
        "services/UserService.ets",
        "utils/Validator.ets",
    ],
}


def _difficulty(related_files: List[str]) -> str:
    count = len(set(related_files))
    if count >= 4:
        return "hard"
    if count >= 2:
        return "medium"
    return "easy"


def _round_prompts(desc: str) -> List[str]:
    return [
        f"Round 1 - 需求澄清与文件定位：请说明任务目标，列出需要读取/修改的文件，并解释依赖路径。任务：{desc}",
        "Round 2 - 方案设计与代码修改：请给出跨文件修改方案，说明函数、模块和接口之间如何保持一致。",
        "Round 3 - 编译测试与反馈修复：请根据编译/测试反馈修复问题，并输出最终修改摘要与风险点。",
    ]


def _build_task(idx: int, scenario_type: str, task_type: str, desc: str, expected: str, related_files: List[str]) -> BenchmarkTask:
    return BenchmarkTask(
        task_id=f"T{idx:03d}",
        scenario_type=scenario_type,
        task_type=task_type,
        difficulty=_difficulty(related_files),
        description=desc,
        related_files=sorted(set(related_files)),
        expected_behavior=expected,
        precondition="已提供 HarmonyOS 6.0/ArkTS 工程；应用迁移任务额外提供 Android 示例工程；允许 Code Agent 读取文件并给出修改。",
        round_prompts=_round_prompts(desc),
        key_nodes=["需求理解", "相关文件定位", "依赖路径解释", "核心代码修改", "跨文件一致性", "编译检查", "测试检查", "最终摘要"],
        scoring_points={
            "requirement_understanding": 15,
            "file_location": 15,
            "dependency_reasoning": 10,
            "code_modification": 20,
            "cross_file_consistency": 15,
            "compile_success": 10,
            "test_success": 10,
            "efficiency": 5,
        },
        objective_checks=[
            "静态依赖文件是否命中",
            "跨文件接口是否保持一致",
            "修改后语法/编译是否通过",
            "测试用例是否通过",
            "输出摘要是否覆盖关键节点",
        ],
        candidate_agents=AGENT_CANDIDATES,
        candidate_models=MODEL_CANDIDATES,
    )


def _generic_task_from_dependency(idx: int, row: Dict[str, str]) -> BenchmarkTask:
    source = row.get("source_file", "unknown")
    target = row.get("target_file", "unknown")
    symbol = row.get("target_name", "unknown_symbol")
    relation = row.get("relation_type", "unknown")
    related = [f for f in [source, target] if f and f != "unknown"]
    scenario = "代码问题修复" if relation == "call" else "新特性开发"
    task_type = "generic_cross_file_dependency_task"
    desc = (
        f"基于真实工程依赖关系，检查并改进 {source} 对 {symbol} 的{relation}依赖，"
        f"确保相关模块在需求变更或缺陷修复时保持接口一致。"
    )
    expected = f"{source} 与 {target} 之间的依赖关系保持一致；修改后不破坏相关调用链。"
    return _build_task(idx, scenario, task_type, desc, expected, related)


def generate_tasks(dependency_csv: str | Path, output_json: str | Path) -> List[BenchmarkTask]:
    rows = list(csv.DictReader(Path(dependency_csv).open("r", encoding="utf-8-sig")))
    by_target: Dict[str, set] = {}
    for row in rows:
        target = row["target_name"]
        by_target.setdefault(target, set()).add(row["source_file"])
        if row["target_file"] != "unknown":
            by_target[target].add(row["target_file"])

    tasks: List[BenchmarkTask] = []
    idx = 1
    for target in [t for t in TASK_TEMPLATES if t in by_target]:
        template = TASK_TEMPLATES[target]
        related_files = sorted(set(by_target[target]) | set(template["files"]))
        tasks.append(_build_task(idx, template["scenario_type"], template["task_type"], template["description"], template["expected"], related_files))
        idx += 1

    # For large real repositories, template symbols may not appear. Add generic high-value
    # cross-file tasks from strong dependencies so the method works on most HarmonyOS repos.
    used_pairs = {(tuple(t.related_files), t.task_type) for t in tasks}
    for row in rows:
        if len(tasks) >= 200:  # large-repo safety guard; enough for benchmark sampling
            break
        if row.get("dependency_strength") != "strong":
            continue
        pair = tuple(sorted([x for x in [row.get("source_file"), row.get("target_file")] if x and x != "unknown"]))
        key = (pair, "generic_cross_file_dependency_task")
        if not pair or key in used_pairs:
            continue
        tasks.append(_generic_task_from_dependency(idx, row))
        used_pairs.add(key)
        idx += 1

    # Add one migration scenario to show how the method extends to Android-to-HarmonyOS migration.
    tasks.append(_build_task(idx, MIGRATION_TASK["scenario_type"], MIGRATION_TASK["task_type"], MIGRATION_TASK["description"], MIGRATION_TASK["expected"], MIGRATION_TASK["files"]))

    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2), encoding="utf-8")
    return tasks


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(f"Generated {len(generate_tasks(root/'outputs'/'dependencies.csv', root/'outputs'/'benchmark_tasks.json'))} tasks")
