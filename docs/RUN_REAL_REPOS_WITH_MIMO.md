# Run Real Repositories with MiMo Pro

This guide is for generating the real evidence package for MiMo-CodeHarness v0.2:

- STM32 / IoT repository result
- SlideNotes / Python GUI repository result
- HarmonyOS repository result
- optional DreamZero-Libero high-difficulty research-code result
- token usage logs
- dashboards
- technical reports
- paper-draft style summaries

## 1. Set MiMo API variables

PowerShell:

```powershell
$env:MIMO_API_KEY="YOUR_MIMO_API_KEY"
$env:MIMO_BASE_URL="YOUR_OPENAI_COMPATIBLE_BASE_URL"
$env:MIMO_MODEL="YOUR_MIMO_MODEL_NAME"
```

Example model names and endpoint values must follow your Xiaomi MiMo console. Do not commit API keys.

## 2. Test the API first

```powershell
python src/test_mimo_api_connection.py
```

Expected result:

```text
[OK] API connected successfully.
```

If this step fails, do not run the full harness yet.

## 3. Prepare repository case config

Copy the example config:

```powershell
copy config\real_repo_cases.example.json config\real_repo_cases.local.json
```

Then edit `config/real_repo_cases.local.json`:

- replace `YOUR_USERNAME` with your GitHub username;
- or replace `path` with the local path of each cloned repository;
- keep `limit_tasks` small for the first real run, such as 3 or 4.

Recommended first real cases:

```text
STM32 RFID       -> embedded C / IoT case
SlideNotes       -> Python GUI / software-tool case
HarmonyOS repo   -> ArkTS / dependency-analysis case
DreamZero-Libero -> optional robotics/research-code case, analysis only
```

## 4. Run a safe dry-run batch first

```powershell
python src/run_real_repo_case_studies.py --cases config/real_repo_cases.local.json --suite-name dryrun_case_studies --clone-missing --limit-cases 3
```

This checks paths, cloning, scanning, dashboards, reports, and batch summary generation without consuming real tokens.

## 5. Run the real MiMo API batch

Start small:

```powershell
python src/run_real_repo_case_studies.py --cases config/real_repo_cases.local.json --suite-name mimo_real_case_studies_v1 --config config/api_models_mimo.json --execute --clone-missing --apply-patches --limit-cases 3
```

Only add external build/test commands after the first run succeeds:

```powershell
python src/run_real_repo_case_studies.py --cases config/real_repo_cases.local.json --suite-name mimo_real_case_studies_v2 --config config/api_models_mimo.json --execute --clone-missing --apply-patches --run-build-tests --limit-cases 3
```

## 6. Check outputs

Open:

```text
outputs/mimo_codeharness_v02/mimo_real_case_studies_v1/REAL_REPO_CASE_STUDY_INDEX.md
outputs/mimo_codeharness_v02/mimo_real_case_studies_v1/case_study_summary.csv
```

Each individual case also has:

```text
outputs/mimo_codeharness_v02/<suite_name>__<case_name>/dashboard.html
outputs/mimo_codeharness_v02/<suite_name>__<case_name>/TECHNICAL_REPORT.md
outputs/mimo_codeharness_v02/<suite_name>__<case_name>/PAPER_DRAFT.md
outputs/mimo_codeharness_v02/<suite_name>__<case_name>/token_usage.csv
outputs/mimo_codeharness_v02/<suite_name>__<case_name>/evaluation_scores.csv
```

## 7. Evidence package for GitHub / teacher / resume

Use these as proof:

1. `REAL_REPO_CASE_STUDY_INDEX.md`
2. each `dashboard.html`
3. each `TECHNICAL_REPORT.md`
4. `case_study_summary.csv`
5. `token_usage.csv`
6. screenshots of dashboard and MiMo token usage

Suggested narrative:

> I built MiMo-CodeHarness, a repository-level multi-agent evaluation harness. It scans real repositories, analyzes dependencies, generates tasks, runs MiMo Pro agents, checks patches/builds, scores outputs, records token usage, and generates reproducible reports. I applied it to embedded C, Python GUI, HarmonyOS, and robotics/research-code repositories.
