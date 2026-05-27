# MiMo-CodeHarness v0.2 技术报告

## 1. 项目定位

MiMo-CodeHarness v0.2 是一个面向真实代码仓库的多 Agent 自动评测平台。它将原来的 HarmonyOS 文件依赖分析与多模型评分原型升级为更通用的 repository-level evaluation harness，可用于 IoT/嵌入式、Python 工具、HarmonyOS/ArkTS、前端、科研代码等仓库。

## 2. 已实现功能

- 多语言仓库扫描：C/C++/Embedded, Python, Docs
- 跨文件依赖分析：共提取 86 条依赖边。
- 多 Agent 任务流：Repository Scanner → Dependency Reasoner → Task Generator → Model Runner → Patch Applicator → Build/Test Runner → Evaluator → Report Agent。
- 真实 patch 支持：可从模型输出中提取 unified diff，并在隔离 worktree 中执行 `git apply --check` 和可选真实应用。
- build/test 支持：已接入 Python py_compile、JSON parse、C brace scan，以及 pytest/npm/go/cargo 的自动检测执行入口。
- 可视化 dashboard：输出 `dashboard.html`。
- token 记录：记录 input/output/total token 估算，并兼容真实 API usage 字段。

## 3. 本次运行摘要

- 项目路径：`<STM32_REPO>`
- 文件数：16
- 总行数：810
- 生成任务数：6
- 总 token 估算：41520
- 平均评分：78.76
- Build/Test 关键检查：通过

## 4. 论文雏形方向

可扩展为本科科研/课程论文主题：**A Multi-Agent Evaluation Harness for Repository-Level Code Understanding in IoT and Embedded Software Projects**。

核心研究问题：现有代码评测偏算法题与短代码片段，缺少面向真实 IoT/嵌入式/工具型仓库的评测闭环。本项目提供一种可复现 harness：自动扫描仓库、生成任务、调用模型、检查 patch、运行 build/test，并从正确性、依赖理解、工程质量和 token 成本进行综合评估。

## 5. 下一步

1. 填入小米 MiMo API Key，运行真实模型调用；
2. 接入你的 STM32 RFID、SlideNotes、HarmonyOS 与 DreamZero-Libero 作为 case studies；
3. 增加人工标注小样本，校准 LLM Judge 与规则评分；
4. 增加真实 patch 后的单元测试和构建命令；
5. 形成可投递老师的项目说明与暑期研究计划。
