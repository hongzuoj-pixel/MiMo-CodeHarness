# MiMo-CodeHarness：面向真实代码仓库的多 Agent 自动评测 Harness

> 一个基于小米 MiMo Pro API 的仓库级 CodeAgent 评测系统。  
> 它可以扫描真实代码仓库，分析跨文件依赖，自动生成评测任务，调用模型执行任务，检查 patch/build 信号，记录 token 消耗，并生成 dashboard 和技术报告。

[English README](README.md)

---

## 1. 这个项目解决什么问题？

很多大模型代码能力评测只关注算法题、单函数补全或短代码片段。但真实工程开发远不止“写出一段代码”，它通常需要：

- 理解真实项目的目录结构；
- 找到相关文件和模块；
- 分析跨文件依赖关系；
- 生成仓库级代码理解/修复/测试任务；
- 检查模型输出是否基于真实文件；
- 记录 token 消耗和模型运行结果；
- 生成可复现的评分和报告。

**MiMo-CodeHarness** 的目标就是把大模型代码能力评测从“单题问答”推进到“真实代码仓库级 Agent 评测”。

这个项目特别适合展示 **AI + 软件工程 + 物联网/嵌入式/工具类项目** 的结合。当前版本使用小米 **MiMo Pro API** 作为主要模型后端，并在两个真实项目上完成了最终 clean 版评测：

1. STM32 RFID 门禁系统；
2. SlideNotes Python GUI 文档导出工具。

---

## 2. 什么是 Agent Harness？

这个项目不是普通的 API Demo。

普通 API Demo 通常是：

```text
用户输入 prompt -> 模型回答
```

MiMo-CodeHarness 的流程是：

```text
真实代码仓库
-> 仓库扫描
-> 依赖分析
-> 任务生成
-> 模型执行
-> patch 检查
-> build/test 检查
-> 证据分层评分
-> dashboard / 技术报告 / token 日志
```

也就是说，它不仅会调用模型，还会**管理一整套 Agent 实验流程**：给任务、跑模型、检查结果、记录日志、打分、生成报告。

这就是它比普通“LLM API 调用脚本”更有价值的地方。

---

## 3. 核心功能

### 仓库扫描

- 统计文件数量、语言类型、文件后缀、源码/文档角色；
- 抽取代表性文件；
- 支持 C/C++/嵌入式 C、Python、JavaScript/TypeScript、ArkTS、Markdown、JSON、Shell 等类型。

### 依赖分析

- 提取 C include、Python import、Markdown 链接、符号调用等关系；
- 输出依赖关系表和依赖摘要；
- 帮助模型理解真实工程中的跨文件关系。

### 任务生成

系统会自动生成仓库级任务，例如：

- 项目架构理解；
- 跨文件依赖解释；
- 潜在 bug 定位；
- 测试方案生成；
- patch-style 可维护性改进；
- 工程风险分析。

### 模型执行

- 使用小米 MiMo Pro API；
- 通过环境变量读取 API Key；
- 不在源码中保存密钥；
- 记录模型输出、耗时和 token 消耗。

### Patch 与 build/test 检查

- 对模型输出中的 patch-style 内容做检查；
- 在隔离 worktree 中进行 patch 检查，避免破坏原仓库；
- 执行轻量级 build/test 信号，如 Python 语法检查、JSON 解析、C/C++ 括号扫描等。

注意：对于 STM32 项目，当前 build/test 不是完整 Keil 固件编译，而是轻量级静态安全检查。这个限制在报告中会明确说明。

### 描述性评分与硬验证

描述性评分衡量真实文件引用、rubric 覆盖、回答完整度和工程信号。v0.4 另外为每个候选输出独立的硬判定：`PASS`、`FAIL`、`INSUFFICIENT_EVIDENCE`、`ERROR` 或 `NOT_APPLICABLE`。

高分不能覆盖失败或未经验证的补丁。系统先验证未修改仓库的基线，再把每个候选放入独立、完整的工作区运行，并比较前后测试结果。只有静态检查而没有行为测试时，结果必须是 `INSUFFICIENT_EVIDENCE`。详细规则见 [v0.4 验证说明](docs/VERIFICATION_V04.md)。

LLM Judge 和人工 review 仍然只是可选证据，不能替代可执行验证。

---

## 4. Final Clean 评测结果

下面是当前推荐在 README、简历、项目报告和展示中引用的最终 clean 版结果。

| Case Study | 项目类型 | 文件数 | 依赖边数 | 任务数 | 平均分 | 估算 Token | 状态 |
|---|---:|---:|---:|---:|---:|---:|---|
| STM32 RFID Access Control | 嵌入式 C / 物联网 | 16 | 86 | 6 | 78.76 | 41,520 | 历史结果 / 未硬验证 |
| SlideNotes GUI Tool | Python GUI / 文档导出工具 | 91 | 505 | 6 | 66.71 | 历史结果 / 未硬验证 |

这些 v0.3.1 历史分数生成于“基线—候选”验证机制之前，不能再表述为补丁已验证通过。

推荐展示目录：

```text
outputs/mimo_codeharness_v02/final_stm32_v031_clean__stm32_rfid/
outputs/mimo_codeharness_v02/final_slidenotes_v031_clean__slidenotes/
```

每个目录中包含：

```text
dashboard.html
TECHNICAL_REPORT.md
PAPER_DRAFT.md
token_usage.csv
evaluation_scores.csv
model_outputs.jsonl
repo_summary.json
dependency_summary.json
tasks.json
```

---

## 5. 如何查看结果

打开 STM32 dashboard：

```powershell
ii "outputs\mimo_codeharness_v02\final_stm32_v031_clean__stm32_rfid\dashboard.html"
```

打开 SlideNotes dashboard：

```powershell
ii "outputs\mimo_codeharness_v02\final_slidenotes_v031_clean__slidenotes\dashboard.html"
```

查看技术报告：

```text
TECHNICAL_REPORT.md
```

查看 token 消耗：

```text
token_usage.csv
```

查看评分表：

```text
evaluation_scores.csv
```

---

## 6. 快速开始

### 6.1 克隆仓库

```bash
git clone https://github.com/hongzuoj-pixel/MiMo-CodeHarness.git
cd MiMo-CodeHarness
```

### 6.2 安装统一 CLI

要求 Python 3.10+；Harness 本身仅使用标准库。

```bash
python -m pip install -e ".[test]"
mimo --version
```

验证 Python 行为测试时，使用 `python -m pip install -e ".[test]"` 安装测试扩展，并在同一解释器中安装目标项目自身的测试依赖。

旧的 `python src/...` 入口仍然保留，但新用户推荐统一使用 `mimo`。

### 6.3 初始化并检查项目

先使用明确的离线模式。下面命令会创建 `mimo-harness.json` 和 `.mimo/models.json`，不会调用模型 API。

```bash
mimo init --mode offline
mimo doctor
mimo run --name first-run --limit-tasks 1
mimo report
```

运行产物写入 `.mimo/runs/`。CLI 不会静默覆盖已经存在的配置或同名运行目录。

### 6.4 不使用 API Key，直接验证现有补丁

`verify` 会从指定 Git 基线生成隔离副本，只在候选工作区应用补丁，不修改源仓库。

```bash
git diff > fix.patch
mimo verify --base HEAD --patch fix.patch --run-tests
mimo report
```

不加 `--run-tests` 时只执行静态检查，结果会明确为 `INSUFFICIENT_EVIDENCE`。使用 `--task-type bug_fix` 时，需要在配置中指定 `target_tests`：每个目标用例必须在基线中因断言失败、在候选中通过。完整配置见[项目检查与 pytest 证据](docs/PYTEST_EVIDENCE.md)。

现在 pytest 会记录稳定用例 ID、执行阶段和 JUnit XML。用例消失、新增 skip/xfail、原有验收文件被修改、收集错误、空测试集均不能获得验证通过。原本就跳过且保持跳过的用例会单独列出，不计作已执行验收。

### 选择运行环境

默认 `local` 模式使用当前解释器，但会清理环境变量，并为每次检查准备独立的用户与临时目录。设置 `execution.mode: "venv"` 可为基线、每个候选分别创建全新依赖环境；Linux Docker 模式提供资源和网络限制。依赖准备命令需要明确配置，日志实时写入磁盘；超时、中断和正常结束时均会清理受管理的子进程。CLI 中断会保存已有证据并返回 `130`。

配置见[执行隔离说明](docs/EXECUTION_ISOLATION.md)，完整流程验收见[验收矩阵](docs/ACCEPTANCE_MATRIX.md)。请求 Docker 但环境不可用时会明确失败，不会自动改用本机执行。

### 恢复中断的运行

```bash
mimo resume --run .mimo/runs/first-run --name continued-run
mimo report --run .mimo/runs/continued-run
```

CLI 会保存版本化输入指纹，并在每次模型调用完成后写入检查点。恢复会保留原运行，检查源码、配置、环境与证据完整性，复用已完成的模型输出，再用全新工作区和依赖环境重跑构建与测试。输入改变时会明确报错。详细边界见[运行指纹与恢复](docs/RUN_RECOVERY.md)。

### 6.5 设置 MiMo API

不要把 API Key 写进代码，也不要上传到 GitHub。

PowerShell 示例：

```powershell
$env:MIMO_API_KEY='YOUR_MIMO_API_KEY'
$env:MIMO_BASE_URL='https://api.xiaomimimo.com/v1'
$env:MIMO_MODEL='mimo-v2.5-pro'
```

对于尚未初始化的项目，可直接生成 API 模式配置：

```powershell
mimo init --mode api
mimo doctor
mimo run --execute --apply-patches --run-tests
```

API 模式如果没有启用真实 provider，或者缺少所需环境变量，`doctor` 会直接失败，并且不会输出密钥内容。如果需要替换已有生成配置，应先人工检查，再明确使用 `mimo init --mode api --force`。

旧版 API 连通性检查仍然保留：

```powershell
python src\test_mimo_api_connection.py
```

看到类似下面结果说明 API 正常：

```text
[OK] API connected successfully.
```

---

## 7. 运行 final clean case studies

### 7.1 STM32 RFID

```powershell
python src\run_real_repo_case_studies.py --cases config\case_stm32_clean.local.json --suite-name final_stm32_v031_clean --config config\api_models_mimo.json --execute --clone-missing --apply-patches --limit-cases 1
```

检查：

```powershell
Get-Content outputs\mimo_codeharness_v02\final_stm32_v031_clean\case_study_summary.csv
Test-Path "outputs\mimo_codeharness_v02\final_stm32_v031_clean__stm32_rfid\dashboard.html"
```

### 7.2 SlideNotes

```powershell
python src\run_real_repo_case_studies.py --cases config\case_slidenotes_clean.local.json --suite-name final_slidenotes_v031_clean --config config\api_models_mimo.json --execute --clone-missing --apply-patches --limit-cases 1
```

检查：

```powershell
Get-Content outputs\mimo_codeharness_v02\final_slidenotes_v031_clean\case_study_summary.csv
Test-Path "outputs\mimo_codeharness_v02\final_slidenotes_v031_clean__slidenotes\dashboard.html"
```

---

## 8. 项目结构

```text
MiMo-CodeHarness/
├── pyproject.toml
├── mimo-harness.json          # `mimo init` 生成
├── .mimo/
│   ├── models.json            # 模型配置，不保存密钥
│   └── runs/                  # 运行产物，已加入 gitignore
├── config/
│   ├── api_models_mimo.json
│   ├── case_stm32_clean.local.json
│   └── case_slidenotes_clean.local.json
├── src/
│   ├── mimo_harness/
│   │   ├── cli.py
│   │   └── __main__.py
│   ├── run_real_repo_case_studies.py
│   ├── run_mimo_codeharness_v02.py
│   ├── test_mimo_api_connection.py
│   ├── validate_mimo_codeharness_v02.py
│   ├── scoring_v03.py
│   └── verification_v04.py
├── docs/
│   ├── SCORING_SYSTEM_V03.md
│   ├── SCORING_SYSTEM_V031.md
│   ├── VERIFICATION_V04.md
│   └── CLI_V05.md
├── tests/
│   ├── test_verification_v04.py
│   └── test_cli_v05.py
├── .github/workflows/
│   └── verification.yml
├── outputs/
│   └── mimo_codeharness_v02/
│       ├── final_stm32_v031_clean__stm32_rfid/
│       └── final_slidenotes_v031_clean__slidenotes/
├── README.md
└── README_CN.md
```

---

## 9. 当前限制

当前项目已经具备可安装的 beta CLI 和证据化硬验证，但还不是带完整沙箱的工业级 benchmark 服务。

当前限制包括：

- 没有检测并执行行为测试时，候选结果为 `INSUFFICIENT_EVIDENCE`；
- STM32 项目暂未接入 Keil/ARM 工具链真实固件编译，静态检查不会再被报告为验证成功；
- local/venv 模式以当前用户权限执行可信项目代码；Docker 当前认证范围是非 root Linux worker，Windows Docker/WSL 尚未认证；
- 当前支持从仓库安装，但还没有发布到 PyPI；
- final clean 分数不使用 LLM Judge 和人工 review；
- 自动分数应理解为“证据分层评测结果”，不是绝对真值；
- 未来需要更多仓库和更多轮重复实验来增强 benchmark 结论。

---

## 10. 后续计划

完成统一 CLI 后，后续继续升级：

- 接入项目专属 build/test adapter，如 STM32/Keil 编译检查；
- 增加 HarmonyOS、机器人、后端等更多真实仓库；
- 增加可校准的跨模型 LLM Judge；
- 增加小样本人工 review 协议；
- 增加可恢复的运行 manifest、按输入识别的缓存，以及更多容器平台认证；
- 做一个可视化对比 dashboard；
- 将技术报告扩展成本科科研报告或 workshop-style paper draft。

---


## 11. 安全说明

- 不要提交 API Key；
- 不要把 `.env` 上传 GitHub；
- 所有密钥通过环境变量设置；
- 自动生成 patch 不能直接用于生产环境，必须人工确认；
- 自动评分只能作为描述性证据，不应被解释为绝对真值；
- 自动化流程应以硬验证 verdict 和进程退出码作为门禁。
