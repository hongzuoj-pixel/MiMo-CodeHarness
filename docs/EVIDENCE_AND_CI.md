# Portable evidence and strict CI gates (v0.7.0)

## Export and inspect a completed run

```bash
mimo verify --base HEAD --patch fix.patch --run-tests --name reviewed
mimo export --run .mimo/runs/reviewed --output reviewed-evidence.zip
mimo gate --bundle reviewed-evidence.zip --json
```

Extract the ZIP and open `index.html` to read it offline. No web server, JavaScript, external fonts, model API, project checkout or original configuration is required. `report.json` contains the same structured conclusion and relative evidence links. Original JSON/JUnit/log files retain their recorded provenance paths as informational text; report links resolve inside the extracted bundle.

Export is an administrative operation: successful export returns `0` even for a failed verification. **Use `gate`, not `export`, as the CI acceptance step.** The destination must be outside the original run and must not already exist. Original run files are not rewritten.

| Gate code | Meaning |
|---:|---|
| `0` | Only an intact, recomputed `PASS` |
| `2` | Verification `FAIL` |
| `3` | `INSUFFICIENT_EVIDENCE` or `NOT_APPLICABLE` |
| `4` | Runtime error, cancellation, unsealed/incomplete run, invalid/missing evidence, or expected input mismatch |

`mimo gate --run PATH` checks a live run's saved evidence under its OS lock. The archive form reads ZIP entries without extracting or executing project code. Neither form calls a model or reruns tests: it audits recorded execution evidence. Resume/new verification is required to obtain new execution evidence.

To bind acceptance to the intended change rather than a previous valid result:

```bash
mimo gate --bundle reviewed-evidence.zip --expect-base FULL_BASE_COMMIT_SHA --expect-patch fix.patch --json
```

The expected patch is compared byte-for-byte through SHA-256. A different Git base or patch blocks the gate. The CI runner always supplies both expected inputs.

## What the gate verifies

- Run/input inventory versions, source fingerprint and sealed artifact hashes; a local run also checks the captured input snapshot.
- Manifest completion status, verification summary and run-log consistency.
- Recomputed per-candidate classifications and aggregate counts using the existing baseline/candidate verifier. A saved summary that merely says PASS is insufficient.
- For PASS, recorder JSON versus summarized test IDs/results/exit codes, JUnit case/failure/error consistency, required logs and behavior execution. An exit-zero generic command cannot substitute for per-test evidence.
- Pytest IDs/conftest lookup are rooted in the copied workspace. A pytest configuration selected from outside that workspace is an error, not an undeclared acceptance input. Nested plain projects can add an explicit `pytest.ini` containing `[pytest]` to prevent inheriting a parent repository's settings.
- Bundle inventory, original run seal, portable report JSON and the versioned canonical offline HTML. Unknown bundle/renderer versions fail closed.

Bundle schema and renderer version are currently `1`; future changes to the canonical renderer must preserve the version-1 reader/renderer or introduce a new explicitly supported format version. Hashes detect accidental changes and inconsistent evidence, not an attacker rewriting every unsigned record. This is not a digital signature, proof of all business behavior, or a general-purpose source backup.

## Export contents and limits

Included: provenance manifests and source-file inventory (hashes, not source contents); patch-verification records; per-case JSON, JUnit and command logs; input patch and token-usage CSV where available. Excluded: source snapshots/worktrees, venv/container contents, full model responses and authentication configuration.

Logs and patches can contain project data printed or supplied by the project. Review the bundle before sharing; export does not silently redact evidence and invalidate its original hashes. Relative links make the report portable; they do not remove informational host paths from original raw records.

Archives are bounded to 10,000 entries, 32 MiB per file and 256 MiB total uncompressed data. Oversized exports fail without publishing a partial ZIP. Duplicate/case-colliding names, path traversal, absolute/Windows drive paths, control characters, device names, symlinks, encrypted entries and damaged data are rejected. Legacy runs without manifests remain reportable but cannot be promoted into verified bundles. Unsealed attempts can export diagnostic bundles only, and are always blocked.

## Optional GitHub pull-request workflow

This repository does **not** silently change branch protection or force users to adopt a workflow. The complete template lives in `examples/ci/`.

| Source file | Target in the consuming project |
|---|---|
| `examples/ci/mimo-patch-gate.yml` | `.github/workflows/mimo-patch-gate.yml` |
| `examples/ci/verify_pr.py` | `.github/scripts/mimo_verify_pr.py` |
| `examples/ci/Dockerfile` | `.github/mimo/Dockerfile` |
| `examples/ci/mimo-harness.json` | Merge reviewed checks/execution policy into `mimo-harness.json` |

1. Review and commit these files on the trusted base branch. Merge with existing configuration/workflows; do not overwrite unrelated files. The example image includes Python/pytest only: add the project's pinned test dependencies to the trusted Dockerfile.
2. Set repository Actions variable `MIMO_HARNESS_REF` to a reviewed full 40-character MiMo-CodeHarness commit containing v0.7+ functionality. No mutable branch or PR-selected harness version is accepted by the template.
3. Run a test PR. The workflow checks out the base policy and candidate Git objects separately, builds the image from the base, and invokes the base's runner script. The runner verifies the host checkout/policy match the base, creates a binary-capable diff, and executes project checks only in the configured non-root Linux Docker backend with network disabled and no environment allowlist.
4. After the trusted setup has landed and the check is working, configure branch protection/rulesets to require this workflow's `verify-patch` check. Protect workflow/runner/policy/image changes with maintainer review (for example CODEOWNERS and review rules). A pull_request workflow is not itself a protected policy: a PR can propose workflow changes, so review/ruleset configuration is part of the deployment boundary.

The runner verifies against the event's base/head commits and passes expected base/patch hashes to the bundle gate. It preserves evidence for PASS/FAIL/insufficient/error runs; the artifact step uses `always()` and omits source worktrees/dependency environments. The template defaults to regression-preserving `code_refactor` verification. For defect-specific acceptance, review the task type and configure explicit target tests. Changes to protected baseline acceptance inputs require independent review and are not automatically approved.

The template uses `pull_request`, read-only contents permission, no persisted checkout credentials and no model/API secret. It deliberately does not use `pull_request_target` to execute candidate code. Fork approval, repository permissions and required-check settings remain the maintainer's responsibility. See [GitHub's security guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target), [artifact retention](https://docs.github.com/en/actions/tutorials/store-and-share-data) and [protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

## Automated acceptance

`tests/test_evidence_bundles.py` exercises real PASS/FAIL/error/incomplete runs, relocation without source/config/API, JSON/JUnit/report cross-checks, input binding, malicious/corrupted archives, locks, size bounds, non-overwrite behavior, stdlib unittest, full agent-run evidence and HTML escaping. The Linux/Windows installed CLI job exports and gates a NOT_APPLICABLE run to confirm it cannot pass.

`evidence-gate-contract` generates downloadable **fixture** PASS/FAIL bundles and checks actual CLI exit codes. It opens both reports offline in Chrome at 320/768/1024/1440px, checks keyboard skip navigation, heading/table semantics, links, console/network errors and horizontal overflow, and retains screenshots plus JSON metrics. That job tests the gate implementation; its expected-FAIL fixture is not approval of a user patch. The dedicated Docker job additionally runs the exact PR runner against good and regressing commits, confirms both retain bundles, and checks the original base checkout remains unchanged.
