# Installable CLI v0.5

The `mimo` command is the supported user entry point introduced in v0.5. It wraps the existing verification core without removing the legacy Python scripts. v0.6 adds versioned input identities and conservative [run recovery](RUN_RECOVERY.md).

## Installation

```bash
python -m pip install -e .
mimo --version
```

The repository has no runtime dependency beyond Python 3.10+ and Git. Target repositories may require their own build and test dependencies.

Install `python -m pip install -e ".[test]"` for pytest verification. As of v0.5.1, project checks can be configured once and pytest comparisons use individual test IDs. See [project checks](PYTEST_EVIDENCE.md). Stdlib unittest detection remains available without pytest.

## Configuration lifecycle

```bash
mimo init --mode offline
mimo doctor
```

`init` creates:

- `mimo-harness.json`: project, output, mode, and execution policy;
- `.mimo/models.json`: model provider configuration without secret values.

Neither file is overwritten unless `--force` is explicitly supplied. Offline and API modes are explicit: API mode fails diagnostics when it has no enabled non-mock provider or a required environment variable is missing.

## Commands

| Command | Purpose | API required |
|---|---|---:|
| `mimo init` | Create explicit configuration | No |
| `mimo doctor` | Validate Python, Git, paths, model config, and API prerequisites | No |
| `mimo run` | Run scanning, task generation, agents, candidate verification, and reports | Only in API mode |
| `mimo verify` | Verify one local unified diff against an isolated baseline | No |
| `mimo resume --run PATH` | Validate inputs, recover completed model responses, rerun all checks in a new attempt | Original API mode only |
| `mimo report` | Generate a concise Markdown summary for a completed run | No |
| `mimo export --run PATH --output ZIP` | Export a portable, hash-indexed offline evidence package | No |
| `mimo gate --run PATH` / `--bundle ZIP` | Recompute acceptance from saved evidence; only intact PASS returns zero | No |

## Direct patch verification

```bash
mimo verify \
  --base HEAD \
  --patch fix.patch \
  --task-type bug_fix \
  --run-tests
```

The command archives the selected Git commit into an isolated baseline, creates a separate candidate, applies the patch inside that candidate, and compares detected tests. `--base WORKTREE` is available when intentionally verifying the current filesystem state.

For `--task-type bug_fix`, first configure `target_tests` in the pytest check. Omitting the targets now yields `INSUFFICIENT_EVIDENCE`; an environment/import error does not count as reproducing the defect.

The original checkout is never the patch target. Every run gets a new directory under `.mimo/runs/`; an existing directory is rejected instead of overwritten.

## Exit codes

The CLI preserves the v0.4 automation contract:

| Code | Meaning |
|---:|---|
| `0` | `PASS`, `NOT_APPLICABLE`, or a successful administrative command |
| `2` | Verification `FAIL` |
| `3` | `INSUFFICIENT_EVIDENCE` |
| `4` | Configuration, environment, or execution `ERROR` |
| `130` | Interrupted run; partial evidence retained |

Static checks alone cannot produce a verified pass. `--run-tests` executes detected repository tests locally; use a container or disposable worker for untrusted code.

The administrative export code is not an acceptance decision. The strict v0.7 gate maps NOT_APPLICABLE to `3` and cancellation/incomplete evidence to `4`; see [portable evidence and CI](EVIDENCE_AND_CI.md).
