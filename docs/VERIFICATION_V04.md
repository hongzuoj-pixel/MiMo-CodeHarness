# Verification Pipeline v0.4

MiMo-CodeHarness v0.4 separates two questions that were previously mixed:

1. **How good is the model response?** — a descriptive score from 0 to 100.
2. **Did the proposed code change work?** — a hard, evidence-backed verdict.

A high descriptive score never overrides a failed or missing verification result.

## Verdicts and exit codes

| Verdict | Exit code | Meaning |
|---|---:|---|
| `PASS` | 0 | The applied candidate passed executed behavior tests without regression. |
| `NOT_APPLICABLE` | 0 | The task is analytical and does not require a patch. |
| `FAIL` | 2 | The patch is invalid, a static check failed, or a new regression was detected. |
| `INSUFFICIENT_EVIDENCE` | 3 | The patch was not executed, no behavior test ran, or bug-fix evidence lacks a fail-to-pass transition. |
| `ERROR` | 4 | The candidate environment or verification process failed. |

Batch execution returns the most severe exit code observed across prepared cases.

## Verification flow

For each run, the harness:

1. scans and tests the untouched repository to produce a baseline;
2. creates one isolated copied workspace per candidate;
3. applies exactly one candidate patch to that workspace;
4. records added, modified, deleted, and renamed files using SHA-256 snapshots;
5. runs safe static checks and detected behavior tests in that candidate workspace;
6. compares the candidate test states with the baseline;
7. writes a hard verdict independently of the descriptive score.

Binary files and test fixtures are preserved in copied workspaces. A copy error is fatal; there is no source-only fallback.

## Evidence rules

- Static syntax, JSON, and brace checks are signals, not behavior proof.
- Setting `--run-build-tests` does not count as execution when no external command was detected.
- A bug-fix `PASS` requires at least one baseline failing test to pass in the candidate.
- A new candidate failure that was not present in the baseline is a regression and yields `FAIL`.
- A patch that only passes `git apply --check` yields `INSUFFICIENT_EVIDENCE`; it has not been executed.
- Analysis-only tasks may be `NOT_APPLICABLE` and still pass their descriptive quality threshold.

## Outputs

Every run writes:

- `build_test_logs/build_test_summary.json` — untouched baseline;
- `candidate_build_summaries.json` — summaries keyed by stable candidate ID;
- `verification/<candidate-id>/` — candidate check logs;
- `verification_results.json` and `.csv` — candidate-level evidence;
- `verification_summary.json` — overall verdict, counts, pass rate, and exit code;
- `evaluation_scores.csv` — descriptive score plus `analysis_pass`, `verified_pass`, and `verification_verdict`.

## Recommended command

```bash
python src/run_mimo_codeharness_v02.py \
  --project /path/to/repository \
  --name my-run \
  --config config/api_models_mimo.json \
  --execute \
  --apply-patches \
  --run-build-tests
```

Review `verification_summary.json` first. Only use the descriptive score to compare candidates with the same verification status.

## CI security boundary

The included workflow uses the `pull_request` event and read-only `contents` permission. It does not use `pull_request_target`, repository secrets, or a privileged token to execute untrusted candidate code. Production deployments should add OS/container sandboxing, CPU and memory limits, network isolation, and per-language dependency caching before enabling arbitrary third-party repositories.
