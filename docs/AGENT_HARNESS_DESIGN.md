# Agent Harness Design

This document explains where the Agent Harness is implemented in MiMo-CodeHarness v0.2.

## 1. Definition

In this project, an Agent Harness is not a single prompt and not a chatbot. It is an execution framework that provides:

1. a real repository environment;
2. task generation from repository structure and dependencies;
3. model execution through MiMo Pro / OpenAI-compatible APIs;
4. patch extraction and isolated application checks;
5. build/test verification;
6. scoring, token accounting, and reproducible reports.

## 2. Implementation Map

| Stage | Implementation | Output |
|---|---|---|
| Repository Scanner | `RepositoryScanner.scan()` | `repo_inventory.json`, `repo_summary.json` |
| Dependency Reasoner | `DependencyReasoner.analyze()` | `dependencies.csv`, `dependency_summary.json` |
| Task Generator | `TaskGeneratorV02.generate()` | `tasks.json` |
| Model Runner | `run_models()`, `call_model()` | `model_outputs.jsonl`, `model_runs.csv` |
| Patch Applicator | `extract_diff_block()`, `apply_patches()` | `patch_results.csv` |
| Build/Test Runner | `BuildTesterV02.run_all()` | `build_test_logs/` |
| Evaluator | `score_outputs()`, `summarize_scores()` | `evaluation_scores.csv`, `evaluation_summary.json` |
| Report Agent | `generate_dashboard()`, `generate_reports()` | `dashboard.html`, `TECHNICAL_REPORT.md`, `PAPER_DRAFT.md` |

## 3. Why this is a Harness

A normal LLM demo usually does this:

```text
User prompt → Model answer
```

MiMo-CodeHarness does this:

```text
Repository → Scan → Dependency graph → Task set → Model run → Patch check → Build/test → Score → Report → Token log
```

The framework controls the whole experiment, records every step, and makes results reproducible. That is the key difference between a simple API demo and an Agent Evaluation Harness.

## 4. Current Limitations

The v0.2 baseline already provides an offline runnable harness and API-ready execution path. However, the following items still require real case execution:

- running MiMo Pro on real GitHub repositories;
- checking model-generated patches against real project tests;
- comparing MiMo Pro with other models under the same tasks;
- manually reviewing a subset of results for evaluator calibration.
