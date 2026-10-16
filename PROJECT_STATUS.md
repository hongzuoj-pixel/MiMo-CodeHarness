# Project Status

Current development line: MiMo-CodeHarness v0.5.1 installable CLI with per-test Python evidence.

Evidence-backed capabilities:

- isolated baseline-versus-candidate verification;
- hard verdicts and stable exit codes;
- installable `mimo` / `mimo-harness` console entry points;
- explicit `init`, `doctor`, `run`, `verify`, and `report` workflows;
- direct patch verification without a model API key;
- fail-closed API diagnostics and non-overwriting run directories.
- project-level pytest/build/smoke check policies with explicit argv, cwd and timeout;
- pytest and detected unittest case IDs, phases, JUnit and zero-test detection;
- configured bug-fix target assertions, regression IDs, lost/skipped test detection and baseline acceptance fingerprints.

Historical v0.3.1 case-study scores remain in the repository for provenance, but they predate hard verification and are labeled unverified. Embedded checks are still lightweight and do not represent a full Keil firmware build.
