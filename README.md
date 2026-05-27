# MiMo-CodeHarness: Multi-Agent Evaluation Harness for Real-World Code Repositories

> A repository-level CodeAgent evaluation harness powered by Xiaomi MiMo Pro API.  
> It scans real code repositories, reasons over cross-file dependencies, generates coding tasks, runs model-based agents, checks patches/build signals, records token usage, and produces dashboards and technical reports.

[中文说明](README_CN.md)

---

## 1. Why this project?

Most LLM coding evaluations focus on isolated algorithm problems or short code snippets. However, real engineering work requires much more than writing one function:

- understanding a real repository structure;
- locating relevant files across modules;
- reasoning about cross-file dependencies;
- generating repository-level tasks;
- applying or checking patch-style modifications;
- running lightweight build/test checks;
- recording token usage and evaluation evidence;
- producing reproducible reports.

**MiMo-CodeHarness** was built to evaluate these repository-level CodeAgent capabilities in a more engineering-oriented workflow.

This project is especially designed for **AI + Software Engineering + IoT/Embedded/Tooling scenarios**. It uses Xiaomi **MiMo Pro API** as the main model backend and applies the harness to real repositories such as an STM32 RFID access control project and a Python SlideNotes GUI tool.

---

## 2. What is an Agent Harness?

This project is not just a single LLM API demo.

A simple API demo usually looks like:

```text
Prompt -> LLM response
```

MiMo-CodeHarness runs a structured evaluation pipeline:

```text
Real Repository
-> Repository Scanner
-> Dependency Reasoner
-> Task Generator
-> Model Runner
-> Patch Checker
-> Build/Test Checker
-> Evidence-aware Scorer
-> Dashboard / Technical Report / Token Log
```

In other words, this project works as a **harness**: it provides the task environment, execution pipeline, evidence collection, scoring logic, and reproducible report generation for CodeAgent evaluation.

---

## 3. Core Features

### Repository scanning

- Counts files, languages, suffixes, and source/documentation roles.
- Extracts representative files from the target repository.
- Supports multiple code types including C/C++/embedded C, Python, JavaScript/TypeScript, ArkTS, Markdown, JSON, and shell scripts.

### Dependency reasoning

- Extracts cross-file relations such as C includes, Python imports, Markdown links, and symbol-level references.
- Produces dependency summaries and structured dependency files.

### Task generation

Generates repository-level tasks such as:

- repository architecture understanding;
- cross-file dependency explanation;
- bug localization;
- test plan generation;
- patch-style maintainability improvement;
- engineering risk analysis.

### Model execution

- Uses Xiaomi MiMo Pro API through environment variables.
- Records model outputs, response time, and token usage.
- API keys are never stored in source code.

### Patch and build/test checks

- Extracts patch-style outputs when available.
- Applies patch checks in isolated worktrees.
- Runs lightweight build/test signals such as Python syntax check, JSON parsing, and C/C++ brace scan.
- For embedded projects, the current build/test check is **not a full Keil firmware compilation**; it is a lightweight static safety check.

### Evidence-aware scoring

The final clean results use the v0.3/v0.3.1 evidence-aware automatic scorer. The score is based on repository grounding, task-rubric coverage, answer completeness, patch evidence, and build/test evidence.

LLM Judge and human review scripts were explored experimentally, but they are **not used as the final reported scoring basis** in the clean results, because model-as-judge can be biased and needs further calibration.

---

## 4. Final Clean Case Study Results

The following results are from final clean runs. They are the recommended results to cite in README, reports, and presentations.

| Case Study | Repository Type | Files | Dependency Edges | Tasks | Avg. Score | Estimated Tokens | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| STM32 RFID Access Control | Embedded C / IoT | 16 | 86 | 6 | 78.76 | 41,520 | OK |
| SlideNotes GUI Tool | Python GUI / Document Export Tool | 91 | 505 | 6 | 66.71 | 38,675 | OK |

Recommended result directories:

```text
outputs/mimo_codeharness_v02/final_stm32_v031_clean__stm32_rfid/
outputs/mimo_codeharness_v02/final_slidenotes_v031_clean__slidenotes/
```

Each final result directory contains:

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

## 5. Example Outputs

### Dashboard

Each case study produces an HTML dashboard showing:

- repository file count;
- dependency edge count;
- estimated token usage;
- task count;
- average score;
- build/test status;
- task-level score table.

Open a dashboard locally:

```powershell
ii "outputs\mimo_codeharness_v02\final_stm32_v031_clean__stm32_rfid\dashboard.html"
```

or:

```powershell
ii "outputs\mimo_codeharness_v02\final_slidenotes_v031_clean__slidenotes\dashboard.html"
```

### Technical report

Each case study also generates a technical report:

```text
TECHNICAL_REPORT.md
```

The report summarizes repository structure, dependency analysis, model evaluation results, token usage, and engineering observations.

---

## 6. Quick Start

### 6.1 Clone this repository

```bash
git clone https://github.com/YOUR_USERNAME/MiMo-CodeHarness.git
cd MiMo-CodeHarness
```

### 6.2 Install requirements

The current version mainly uses Python standard libraries. If your local environment needs additional packages, install them according to your project configuration.

```bash
python --version
```

Python 3.10+ is recommended.

### 6.3 Configure Xiaomi MiMo API

Do not write your API key into source code or commit it to GitHub.

PowerShell example:

```powershell
$env:MIMO_API_KEY='YOUR_MIMO_API_KEY'
$env:MIMO_BASE_URL='https://api.xiaomimimo.com/v1'
$env:MIMO_MODEL='mimo-v2.5-pro'
```

Test the API connection:

```powershell
python src\test_mimo_api_connection.py
```

Expected result:

```text
[OK] API connected successfully.
```

---

## 7. Run Final Clean Case Studies

### 7.1 STM32 RFID case

```powershell
python src\run_real_repo_case_studies.py --cases config\case_stm32_clean.local.json --suite-name final_stm32_v031_clean --config config\api_models_mimo.json --execute --clone-missing --apply-patches --limit-cases 1
```

Check result:

```powershell
Get-Content outputs\mimo_codeharness_v02\final_stm32_v031_clean\case_study_summary.csv
Test-Path "outputs\mimo_codeharness_v02\final_stm32_v031_clean__stm32_rfid\dashboard.html"
```

### 7.2 SlideNotes case

```powershell
python src\run_real_repo_case_studies.py --cases config\case_slidenotes_clean.local.json --suite-name final_slidenotes_v031_clean --config config\api_models_mimo.json --execute --clone-missing --apply-patches --limit-cases 1
```

Check result:

```powershell
Get-Content outputs\mimo_codeharness_v02\final_slidenotes_v031_clean\case_study_summary.csv
Test-Path "outputs\mimo_codeharness_v02\final_slidenotes_v031_clean__slidenotes\dashboard.html"
```

---

## 8. Repository Structure

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

## 9. Current Limitations

This project is an engineering prototype, not a fully industrial benchmark yet.

Current limitations:

- The build/test stage is lightweight and does not replace full project-specific compilation.
- The embedded STM32 case does not perform full Keil firmware compilation yet.
- LLM Judge and human review are not used in the final clean scores.
- Scores should be interpreted as evidence-aware automatic evaluation results, not absolute ground truth.
- More repositories and more repeated trials are needed for stronger benchmark-level conclusions.

---

## 10. Roadmap

Planned improvements:

- Add project-specific build/test adapters, such as Keil/STM32 build checks and Python unit tests.
- Add more real repositories, including HarmonyOS, robotics, and backend projects.
- Add optional calibrated LLM Judge with stricter JSON schema and cross-model judging.
- Add manual review protocol for small-sample validation.
- Build a web dashboard for comparing multiple repositories and multiple models.
- Convert the current technical report into a more complete undergraduate research report or workshop-style paper draft.

---

## 11. Why this project matters

For researchers and supervisors, this project shows an early but concrete attempt to evaluate CodeAgent behavior on real engineering repositories.

For engineers, it demonstrates a practical workflow for repository-level code understanding, task generation, model execution, token tracking, and reproducible report generation.

For career development, it shows hands-on experience with:

- LLM API integration;
- Agent workflow design;
- repository-level code analysis;
- automated evaluation;
- IoT/embedded software case study;
- Python tooling and engineering documentation.

---

## 12. Safety Notes

- Do not commit API keys.
- Use environment variables for all model credentials.
- Review generated patches before applying them to production code.
- Treat automatic scores as evaluation evidence, not final truth.
