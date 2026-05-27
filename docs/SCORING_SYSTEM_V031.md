# MiMo-CodeHarness v0.3.1 Scoring/Review Patch

## What changed

v0.3.1 does not claim that automatic scores are absolute truth. It improves the evaluation layer in three ways:

1. **Robust worktree copy for real repositories**
   - Binary/media files such as `.png`, `.jpg`, `.mp4`, `.pdf` are skipped during isolated patch worktree copying.
   - This prevents Windows path/copy errors caused by screenshots and other assets that are irrelevant to code patch verification.

2. **LLM Judge support**
   - `src/run_llm_judge_v03.py` reads an existing run directory and asks a judge model to score each task output using a strict JSON-only rubric.
   - Output: `llm_judge_reviews.json`.

3. **Human review / rubric support**
   - `src/generate_human_review_template_v03.py` creates `human_review_template.csv` for manual scoring.
   - `src/rescore_existing_run_v03.py` re-scores an existing run with `--llm-review`, `--human-review`, and/or `--human-rubric`, without re-running model tasks.

## Recommended workflow

```powershell
python src\run_mimo_codeharness_v02.py --project "C:/path/to/repo" --name repo_v031 --config config\api_models_mimo.json --limit-tasks 3 --execute --apply-patches
python src\run_llm_judge_v03.py --run-dir outputs\mimo_codeharness_v02\repo_v031 --limit 3
python src\generate_human_review_template_v03.py --run-dir outputs\mimo_codeharness_v02\repo_v031
python src\rescore_existing_run_v03.py --run-dir outputs\mimo_codeharness_v02\repo_v031 --llm-review outputs\mimo_codeharness_v02\repo_v031\llm_judge_reviews.json --replace
```

## Important limitation

For STM32/Keil projects, current build/test evidence is still a lightweight safety check unless a real Keil/ARM build command is integrated. Report it as **static/safety build evidence**, not as full firmware compilation.
