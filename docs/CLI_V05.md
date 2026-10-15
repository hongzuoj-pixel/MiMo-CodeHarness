# Installable CLI v0.5

The `mimo` command is the supported user entry point for the v0.5 development line. It wraps the existing v0.4 verification core without removing the legacy Python scripts.

## Installation

```bash
python -m pip install -e .
mimo --version
```

The repository has no runtime dependency beyond Python 3.10+ and Git. Target repositories may require their own build and test dependencies.

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
| `mimo report` | Generate a concise Markdown summary for a completed run | No |

## Direct patch verification

```bash
mimo verify \
  --base HEAD \
  --patch fix.patch \
  --task-type bug_fix \
  --run-tests
```

The command archives the selected Git commit into an isolated baseline, creates a separate candidate, applies the patch inside that candidate, and compares detected tests. `--base WORKTREE` is available when intentionally verifying the current filesystem state.

The original checkout is never the patch target. Every run gets a new directory under `.mimo/runs/`; an existing directory is rejected instead of overwritten.

## Exit codes

The CLI preserves the v0.4 automation contract:

| Code | Meaning |
|---:|---|
| `0` | `PASS`, `NOT_APPLICABLE`, or a successful administrative command |
| `2` | Verification `FAIL` |
| `3` | `INSUFFICIENT_EVIDENCE` |
| `4` | Configuration, environment, or execution `ERROR` |

Static checks alone cannot produce a verified pass. `--run-tests` executes detected repository tests locally; use a container or disposable worker for untrusted code.
