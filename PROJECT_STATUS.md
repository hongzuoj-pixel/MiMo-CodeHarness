# Project Status

Current development line: MiMo-CodeHarness v0.8.0, unified providers and bounded model execution.

Evidence-backed capabilities:

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
- opt-in transient retries with total deadlines, conservative request/token/configured-cost reservations and locked accounting across recovery branches; [provider contract](docs/MODEL_PROVIDERS.md).

The [reviewed product plan](docs/PRODUCT_PLAN.md) separates these implemented foundations from the remaining Agent-loop, adapter certification, usability and final release gates. Paid live-provider trials require available credentials and a spending allowance; local protocol/fault tests are not labeled live service validation.

Historical v0.3.1 case-study scores remain in the repository for provenance, but they predate hard verification and are labeled unverified. Embedded checks are still lightweight and do not represent a full Keil firmware build.
