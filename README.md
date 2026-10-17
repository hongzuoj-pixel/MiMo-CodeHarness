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

### Evidence-aware scoring and hard verification

The descriptive score measures repository grounding, rubric coverage, answer completeness, patch evidence, and build/test signals. v0.4 now reports an independent hard verdict for every candidate: `PASS`, `FAIL`, `INSUFFICIENT_EVIDENCE`, `ERROR`, or `NOT_APPLICABLE`.

A high score cannot override a failed or unverified patch. Each applied candidate is tested in its own faithful workspace and compared with an untouched baseline. Static-only checks no longer count as behavior-test success. See [Verification Pipeline v0.4](docs/VERIFICATION_V04.md).

LLM Judge and human review remain optional evidence and do not replace executable verification.

---

## 4. Final Clean Case Study Results

The following results are from final clean runs. They are the recommended results to cite in README, reports, and presentations.

| Case Study | Repository Type | Files | Dependency Edges | Tasks | Avg. Score | Estimated Tokens | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| STM32 RFID Access Control | Embedded C / IoT | 16 | 86 | 6 | 78.76 | 41,520 | Legacy / unverified |
| SlideNotes GUI Tool | Python GUI / Document Export Tool | 91 | 505 | 6 | 66.71 | Legacy / unverified |

These historical v0.3.1 scores predate baseline-vs-candidate verification and must not be cited as verified patch success.

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
git clone https://github.com/hongzuoj-pixel/MiMo-CodeHarness.git
cd MiMo-CodeHarness
```

### 6.2 Install the CLI

Python 3.10+ is required. The harness itself is stdlib-only.

```bash
python -m pip install -e ".[test]"
mimo --version
```

For Python behavior verification, install the test extra with `python -m pip install -e ".[test]"` and install your project's own test dependencies in the same interpreter.

The legacy `python src/...` entry points remain available, but new users should use the `mimo` CLI.

### 6.3 Initialize and diagnose a project

Start in explicit offline mode. This creates `mimo-harness.json` and `.mimo/models.json`; it never calls a model API.

```bash
mimo init --mode offline
mimo doctor
mimo run --name first-run --limit-tasks 1
mimo report
```

Generated run artifacts are written under `.mimo/runs/`. Existing run directories and configuration files are not overwritten implicitly.

### 6.4 Verify an existing patch without an API key

`verify` materializes the selected Git baseline and applies the patch only to an isolated candidate workspace. The source checkout is not modified.

```bash
git diff > fix.patch
mimo verify --base HEAD --patch fix.patch --run-tests
mimo report
```

Omit `--run-tests` for static checks only. That intentionally yields `INSUFFICIENT_EVIDENCE`, not a false pass. For `--task-type bug_fix`, configure explicit `target_tests`: each target must fail a baseline assertion and pass in the candidate. See [project checks and pytest evidence](docs/PYTEST_EVIDENCE.md) for a complete configuration example.

Pytest results now include stable case IDs, execution phases, and JUnit XML. Missing tests, new skip/xfail, changed baseline acceptance inputs, collection errors, and empty suites cannot produce a verified pass. Existing baseline skips are listed explicitly and do not count as executed acceptance tests.

### Execution environments

The default `local` backend uses the current interpreter with a clean environment and per-run home/temp directories. Use `execution.mode: "venv"` for fresh baseline/candidate dependency environments, or the Linux Docker backend for resource and network controls. Preparation commands are explicit, logs are streamed, and managed descendants are cleaned up on timeout, cancellation and normal exit. An interrupted CLI run retains evidence and returns `130`.

See [execution isolation and configuration](docs/EXECUTION_ISOLATION.md) and the [end-to-end acceptance matrix](docs/ACCEPTANCE_MATRIX.md). Docker is never silently replaced with local execution.

### Continue an interrupted run

```bash
mimo resume --run .mimo/runs/first-run --name continued-run
mimo report --run .mimo/runs/continued-run
```

CLI runs save versioned input fingerprints and individual model checkpoints. Recovery preserves the original run, validates source/config/environment and evidence integrity, reuses completed model responses, and rebuilds/retests in fresh workspaces. Changed inputs return an error instead of reusing an old verdict. See [run identities and recovery](docs/RUN_RECOVERY.md).

### Export evidence and enforce a CI gate

```bash
mimo export --run .mimo/runs/first-run --output evidence.zip
mimo gate --bundle evidence.zip --json
```

Extract the ZIP and open `index.html` for an offline report with portable JSON/JUnit/log links. The strict gate recomputes the verdict and only returns success for an intact `PASS`; a successful export or `NOT_APPLICABLE` is not approval. See [evidence bundles, exit codes and the opt-in PR workflow](docs/EVIDENCE_AND_CI.md). Required merge checks must be enabled by the repository maintainer.

### 6.5 Configure Xiaomi MiMo API

Do not write your API key into source code or commit it to GitHub.

PowerShell example:

```powershell
$env:MIMO_API_KEY='YOUR_MIMO_API_KEY'
$env:MIMO_BASE_URL='https://api.xiaomimimo.com/v1'
$env:MIMO_MODEL='mimo-v2.5-pro'
```

Create API-mode configuration in a project that has not been initialized yet:

```powershell
mimo init --mode api
mimo doctor
mimo run --execute --apply-patches --run-tests
```

`doctor` fails closed when API mode has no real provider or its required key is missing. It never prints the secret value. To replace an existing generated configuration, review it first and then use `mimo init --mode api --force` deliberately.

The legacy API connectivity check is still available:

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
├── pyproject.toml
├── mimo-harness.json          # generated by `mimo init`
├── .mimo/
│   ├── models.json            # generated model configuration, never a secret
│   └── runs/                  # generated and gitignored
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

## 9. Current Limitations

This project now has an installable beta CLI and evidence-backed verification semantics, but it is not yet a fully sandboxed industrial benchmark service.

Current limitations:

- A candidate receives `INSUFFICIENT_EVIDENCE` when no behavior test is detected or executed.
- The embedded STM32 case still does not perform full Keil firmware compilation; static checks alone are never reported as verified success.
- Local and venv execution run trusted project code with host permissions. Docker execution is currently supported on non-root Linux Docker workers; Windows Docker/WSL is not certified.
- The package is installable from the repository but is not yet published to PyPI.
- LLM Judge and human review are not used in the final clean scores.
- Scores should be interpreted as evidence-aware automatic evaluation results, not absolute ground truth.
- More repositories and more repeated trials are needed for stronger benchmark-level conclusions.

---

## 10. Roadmap

Planned improvements after the installable CLI:

- Add project-specific build/test adapters, such as Keil/STM32 build checks and Python unit tests.
- Add more real repositories, including HarmonyOS, robotics, and backend projects.
- Add optional calibrated LLM Judge with stricter JSON schema and cross-model judging.
- Add manual review protocol for small-sample validation.
- Add resumable run manifests and input-aware caches; broaden certified container platforms.
- Build a web dashboard for comparing multiple repositories and multiple models.
- Convert the current technical report into a more complete undergraduate research report or workshop-style paper draft.

---


## 11. Safety Notes

- Do not commit API keys.
- Use environment variables for all model credentials.
- Review generated patches before applying them to production code.
- Treat automatic scores as descriptive evidence, not final truth.
- Use the hard verification verdict and process exit code as the automation gate.
