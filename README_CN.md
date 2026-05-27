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

### 证据分层评分

最终 clean 版结果使用 v0.3/v0.3.1 证据分层自动评分系统。评分依据包括：

- 是否引用真实文件和模块；
- 是否覆盖任务 rubric；
- 回答是否完整清晰；
- patch 检查结果；
- build/test 检查结果。

LLM Judge 和人工 review 脚本曾经作为实验功能探索过，但**不作为当前 final clean 结果的最终评分依据**，因为同模型自评可能存在偏宽松问题，需要后续进一步校准。

---

## 4. Final Clean 评测结果

下面是当前推荐在 README、简历、项目报告和展示中引用的最终 clean 版结果。

| Case Study | 项目类型 | 文件数 | 依赖边数 | 任务数 | 平均分 | 估算 Token | 状态 |
|---|---:|---:|---:|---:|---:|---:|---|
| STM32 RFID Access Control | 嵌入式 C / 物联网 | 16 | 86 | 6 | 78.76 | 41,520 | OK |
| SlideNotes GUI Tool | Python GUI / 文档导出工具 | 91 | 505 | 6 | 66.71 | 38,675 | OK |

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

### 6.2 Python 环境

推荐 Python 3.10+。

```bash
python --version
```

当前版本主要使用 Python 标准库。

### 6.3 设置 MiMo API

不要把 API Key 写进代码，也不要上传到 GitHub。

PowerShell 示例：

```powershell
$env:MIMO_API_KEY='YOUR_MIMO_API_KEY'
$env:MIMO_BASE_URL='https://api.xiaomimimo.com/v1'
$env:MIMO_MODEL='mimo-v2.5-pro'
```

测试 API：

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
├── config/
│   ├── api_models_mimo.json
│   ├── case_stm32_clean.local.json
│   └── case_slidenotes_clean.local.json
├── src/
│   ├── run_real_repo_case_studies.py
│   ├── run_mimo_codeharness_v02.py
│   ├── test_mimo_api_connection.py
│   ├── validate_mimo_codeharness_v02.py
│   └── scoring_v03.py
├── docs/
│   ├── SCORING_SYSTEM_V03.md
│   └── SCORING_SYSTEM_V031.md
├── outputs/
│   └── mimo_codeharness_v02/
│       ├── final_stm32_v031_clean__stm32_rfid/
│       └── final_slidenotes_v031_clean__slidenotes/
├── README.md
└── README_CN.md
```

---

## 9. 当前限制

这个项目目前是工程原型，不是完整工业级 benchmark。

当前限制包括：

- build/test 阶段是轻量级检查，不等于完整项目编译；
- STM32 项目暂未接入 Keil/ARM 工具链真实固件编译；
- final clean 分数不使用 LLM Judge 和人工 review；
- 自动分数应理解为“证据分层评测结果”，不是绝对真值；
- 未来需要更多仓库和更多轮重复实验来增强 benchmark 结论。

---

## 10. 后续计划

后续可以继续升级：

- 接入项目专属 build/test adapter，如 STM32/Keil 编译检查；
- 增加 HarmonyOS、机器人、后端等更多真实仓库；
- 增加可校准的跨模型 LLM Judge；
- 增加小样本人工 review 协议；
- 做一个可视化对比 dashboard；
- 将技术报告扩展成本科科研报告或 workshop-style paper draft。

---


## 11. 安全说明

- 不要提交 API Key；
- 不要把 `.env` 上传 GitHub；
- 所有密钥通过环境变量设置；
- 自动生成 patch 不能直接用于生产环境，必须人工确认；
- 自动评分只能作为评测证据，不应被解释为绝对真值。
