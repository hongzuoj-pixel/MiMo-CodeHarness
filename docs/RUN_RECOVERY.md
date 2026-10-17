# Run identities and recovery (v0.6.0)

The supported CLI records a versioned `run_manifest.json` before model calls or project commands. Source inputs are captured once under `inputs/project/`; baseline and candidate checks use separate copies. Every completed model response is checkpointed immediately, without waiting for the entire run.

## Continue after interruption

```bash
mimo run --name first-run --run-tests
# If interrupted, continue into a new directory:
mimo resume --run .mimo/runs/first-run --name continued-run
mimo report --run .mimo/runs/continued-run
```

The same `resume` command supports direct patch verification:

```bash
mimo verify --base HEAD --patch fix.patch --run-tests --name patch-check
mimo resume --run .mimo/runs/patch-check --name patch-recheck
```

`resume` uses the original configuration path and effective options, including command-line overrides. API runs still require their configured credentials. A new name is generated if `--name` is omitted. The source run remains unchanged; the new manifest records its parent run ID/directory and reused candidate IDs. Completed runs can also be resumed to execute their checks again.

## What is reused

| Item | Recovery behavior |
|---|---|
| Completed model response | Reused only after file integrity, exact prompt/provider configuration, engine and run input checks |
| Failed/incomplete model call | Retried; a missing/uncommitted response is never invented |
| Baseline/candidate worktree | Recreated from the validated source input |
| Dependency environment | Prepared fresh; existing venvs/containers are not reused |
| Build/test result, verdict, score/report | Recomputed from this attempt's execution |

`token_usage.csv` marks `reused_from_checkpoint`; inherited token estimates describe the saved response, not a new paid invocation. If a provider completes a request just before the process dies but the response was not checkpointed, retry can call the provider again. This is not an exactly-once billing protocol.

The run lock is released by the OS even after abrupt process termination. A concurrent resume is rejected while another process owns the source run's lock. A leftover `.run.lock` file is normal and should not be deleted to bypass an active run.

## Identity and invalidation

The fingerprint includes:

- all copied source files, test/config/dependency inputs, binary and large fixtures, relative symlink targets and executable modes; scan limits do not limit input hashing;
- project/model configuration file hashes, normalized policy and effective options;
- selected Git commit or WORKTREE identity, saved patch bytes;
- harness source digest, Python/platform identity, installed host package versions, Git/explicit command executable hashes;
- the cleaned command environment and model URL/name overrides (salted digest only), and resolved Docker image ID.

Changing source/tests, policy, model settings, allowlisted environment values, harness implementation, interpreter/packages or Docker image invalidates recovery with exit `4`. A moving `HEAD` is a different selected commit even if its tree is identical; start a new verification, optionally specifying the original commit SHA. A changed original patch is rejected; if an external patch was removed, its integrity-checked saved copy can still be used.

The input snapshot, model checkpoints and sealed final/partial evidence files are checked before reuse. Absolute/traversing/symlink evidence paths, missing files, damaged JSON and unsupported schema versions are rejected. Input symlinks that escape the captured project are rejected. Input collection checks that WORKTREE did not change during copying.

Environment values and API credentials are not copied into the manifest; their allowed non-credential execution environment contributes to its salted identity. Project files are copied according to the existing worktree copy policy, so a source file remains a source input regardless of its name. Model credentials come from the process environment, not a saved authentication header.

## Boundaries

- Recovery currently targets the same project/configuration locations and host environment; relocation, cross-platform/upgrade migration and legacy runs without a manifest require a new run. Legacy reports remain readable.
- This is a per-run model-response checkpoint, not a global cache of passing verdicts. Manifest hashes detect accidental changes, not an attacker rewriting both data and hashes.
- Network downloads, undeclared absolute-path dependencies and external services can change behavior. Pin preparation inputs for reproducibility; all project checks run again precisely because a fingerprint alone cannot prove the current behavior.
- Ctrl-C records `CANCELLED`/exit `130`; runtime failures record `ERROR`/exit `4`. Abrupt process death can leave `RUNNING`, which can be recovered after its OS lock is released. Partial command logs from abrupt death are diagnostic evidence and are never reused as completed results.
- If result files exist but the attempt was never sealed, `report` displays `INCOMPLETE` rather than a final passing verdict.
- The legacy `python src/run_mimo_codeharness_v02.py` entry point remains compatible; managed manifests/recovery are provided by the public CLI.

## Acceptance

`tests/test_run_recovery.py` runs real baseline/candidate pytest checks, stale-PASS rejection, individual model interruption/failure recovery, abrupt CLI process death, real inter-process locking, changed binary/test/config/provider/environment/engine inputs, patch/ref changes and damaged evidence. POSIX SIGINT and symlink integration have explicit Windows skips; abrupt death, locks and model recovery also run on Windows. The dedicated Docker CI job additionally checks fresh containers and rejection after an image tag is rebound to a different image.
