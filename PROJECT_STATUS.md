# Project Status

Current development line: **MiMo-CodeHarness v0.12.0 beta**, with independent verification, bounded external-Agent repair and reproducible independent-user kits. The development batch is implemented; stable-release acceptance remains open.

## Current delivery and acceptance

| Area | Implemented and automated | External acceptance still needed |
|---|---|---|
| Verification foundation / providers | Baseline-versus-candidate tests, explicit verdicts, budgets, recovery, portable evidence and strict gates | Configured test coverage does not establish universal correctness |
| External CLI Agent | Finite repair loop; pinned Codex adapter; actual-binary/local-server Linux/Windows checks | A recorded live-model run with model credentials and an explicit spending allowance |
| Real defect controls / runners | boltons and more-itertools Python defects; native Node ms case; generic JUnit boltons variant; reference/regression controls and recovery | Broader repositories/frameworks require their own executable acceptance |
| First-use delivery | Exact-engine wheel, offline dependencies, Chinese guide, interrupted installation recovery, three blank feedback slots | Three actual independent users must complete the workflow and resolve observed issues |
| Release preparation | Wheel/sdist, support/upgrade notes, exact-source CI record, `readiness` checks | Maintainer license choice, completed model/user evidence and final manual review; no stable GitHub Release or PyPI publication yet |

Latest automation is available in [GitHub Actions](https://github.com/hongzuoj-pixel/MiMo-CodeHarness/actions/workflows/verification.yml). Use its exact commit/tree and retained artifacts when reviewing a run. See the [acceptance matrix](docs/ACCEPTANCE_MATRIX.md), [independent-user kit guide](docs/INDEPENDENT_USER_KIT_CN.md) and [final acceptance guide](docs/PRODUCT_ACCEPTANCE_CN.md).

## Implemented capabilities

- isolated baseline-versus-candidate verification;
- hard verdicts and stable exit codes;
- installable `mimo` / `mimo-harness` console entry points;
- explicit `init`, `doctor`, `run`, `verify`, `resume`, `report`, `export`, and `gate` workflows;
- direct patch verification without a model API key;
- fail-closed API diagnostics and non-overwriting run directories.
- project-level pytest/build/smoke check policies with explicit argv, cwd and timeout;
- pytest and detected unittest case IDs, phases, JUnit and zero-test detection;
- configured bug-fix target assertions, regression IDs, lost/skipped test detection and baseline acceptance fingerprints.
- fresh per-workspace venvs, explicit dependency preparation and installed-package logs;
- clean command environment, isolated home/temp, streamed logs, managed process-tree cleanup;
- Linux Docker backend with resolved image IDs, network/resource controls and explicit no-fallback behavior;
- interruption exit 130 and preserved partial evidence; [acceptance matrix](docs/ACCEPTANCE_MATRIX.md).
- versioned input/environment/engine fingerprints, immutable input snapshots and atomic per-model checkpoints;
- recovery into new attempts with OS locks and evidence integrity validation; all builds/tests execute fresh; [recovery boundaries](docs/RUN_RECOVERY.md).
- bounded, hash-indexed offline evidence ZIPs with HTML/JSON/JUnit/logs and relocation checks;
- strict recomputed PASS-only gates, expected base/patch binding and an opt-in trusted-base Docker PR runner; [deployment boundaries](docs/EVIDENCE_AND_CI.md).
- shared mock/OpenAI-compatible/Anthropic/generic HTTP text completion adapters, explicit completion/usage states and bounded request workers;
- opt-in transient retries with total deadlines, conservative request/token/configured-cost reservations and locked accounting across recovery branches; [provider contract](docs/MODEL_PROVIDERS.md);
- external CLI `repair` with separate Agent/test credentials, cumulative patches, bounded rounds/invocations/time and durable recovery accounting; [Agent protocol](docs/CLI_AGENTS.md);
- pinned Codex configuration and event adapter with actual-binary/local-server controls; [Codex setup](docs/CODEX_AGENT.md);
- pinned real Python and Node defect controls with independent acceptance, intentional regression rejection, fresh recovery and portable ZIP gates; [case provenance](docs/REAL_DEFECT_CASES.md);
- native Node and typed generic JUnit case identities/outcomes, acceptance protection and strict portable evidence; [structured checks](docs/STRUCTURED_CHECKS.md);
- `walkthrough-pack` with exact-engine wheel checks, portable dependencies, private-environment installation/repair and preserved user feedback;
- `trial-init`/`trial-run`, `walkthrough-init` and `readiness` for preparing and checking actual model/user/release records. Empty templates and automated rehearsals record zero completed human trials.

## Remaining scope and limitations

The [reviewed product plan](docs/PRODUCT_PLAN.md) separates implemented features from live-model, independent-user and final release acceptance. Paid live-provider trials require available credentials and a spending allowance; local protocol/fault tests are not labeled live service validation. Credentials belong in the execution environment, never in feedback or source control. The maintainer still needs to choose the project license.

Reusable project PR integration, a local web interface, generalized cross-run caches, broader platform/framework certification and signed remote attestation remain later extensions. The existing opt-in trusted-base PR template still requires maintainer-configured repository checks and review rules.

Historical v0.3.1 case-study scores remain in the repository for provenance, but they predate hard verification and are labeled unverified. Embedded checks are still lightweight and do not represent a full Keil firmware build.
