# Node.js and generic JUnit acceptance

v0.11 adds `node` and `junit` case adapters. Both use fixed test/configuration inputs, stable test IDs, assertion/error classification and portable evidence. A command's zero exit alone remains insufficient. Existing pytest/unittest checks and unstructured `command` checks retain their behavior.

## Try real defects

Install the harness with `python -m pip install ".[test]"`. Install Node 22.23+ or 24.19+ for the Node case; Node 22 and 24 are certified in dedicated CI, including Windows on 24. Then:

```bash
mimo case-init --case ms-invalid-number --output .mimo/cases/ms
mimo --config .mimo/cases/ms/mimo-harness.json doctor
mimo case-check --directory .mimo/cases/ms --name first
mimo case-init --case boltons-junit --output .mimo/cases/generic
mimo case-check --directory .mimo/cases/generic --name first
```

The `ms` case pins [d95e17f](https://github.com/vercel/ms/commit/d95e17fd75a93708c8a3daf5bb07bf649cd5931c) before the upstream [d1add60 fix](https://github.com/vercel/ms/commit/d1add60365fe2340b750b1f7a254b83bea34e52d). Its target rejects malformed `10-.5`; regression checks preserve negative decimals and unit conversion. The negative control rejects valid negative values. The generic case runs the existing boltons defect through an independently executed stdlib unittest command that emits JUnit. It is another adapter certification, not another independent repository.

Both reference fixes must PASS, intentional regressions must FAIL, recovery must run fresh tests, and exported positive/negative gates must agree. These reference controls make no model requests. See [case provenance](REAL_DEFECT_CASES.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

## Native Node configuration

After `mimo init`, put an explicit check in `mimo-harness.json`:

```json
{"id":"unit","adapter":"node","args":["test/**/*.test.cjs"],
 "target_tests":["test/example.test.cjs::suite > fixes the defect"],"timeout_seconds":180}
```

`args` contains relative test-file paths/globs with forward slashes. Reporter, isolation and execution flags are supplied by the adapter; arbitrary Node flags/watch modes are rejected. Files under dependency/output directories are excluded. Dependencies must be prepared explicitly using pinned lockfiles and the normal [execution policy](EXECUTION_ISOLATION.md); no automatic npm installation occurs.

The installed custom `node:test` reporter records native events and requires a completed global summary. IDs use `relative file::suite > test`; duplicate identities are errors. File-only success and empty suites do not count as behavior tests. Native `ERR_ASSERTION` failures can reproduce a defect; import/setup/runtime/cancellation failures cannot. Skip/TODO and missing-test changes cannot obtain a false PASS. Entry files run serially so native parent IDs remain unambiguous. Tests with duplicate names/ambiguous ancestry must be renamed or split; Jest/Vitest are not native `node:test` and need a compatible JUnit command instead.

Collected test files, conventional test directories/files, package manifests/lockfiles and optional explicit `acceptance_files` are hashed before execution. Changing them requires independent review. Fixes in production modules remain patchable. Local/venv execution binds the Node executable bytes for recovery; Docker binds its image ID. Node's version is checked by doctor and every managed invocation. The reporter's bytes are part of the harness engine identity and are included in wheels.

## Generic command with JUnit

```json
{"id":"integration","adapter":"junit",
 "argv":["{python}","tests/run_acceptance.py","{junit}"],
 "acceptance_files":["tests/**/*.py"],
 "target_tests":["tests/test_value.py::ValueTests::test_target"]}
```

The trusted command must write a new UTF-8 JUnit document to the whole `{junit}` output argument, then exit `0` for pass/skip-only or `1` for test failures. `argv` executes without a shell; `{python}` selects the isolated Python. Existing report files are removed before execution. `acceptance_files` declares all acceptance code/helpers; each reported source file must be included in the pre-execution snapshot.

Each `<testcase>` needs a nonempty `name` and a project-relative `file` using forward slashes; `classname` is optional. The stable ID is `file::classname::name` (empty classname omitted). `<failure type="AssertionError">`, `ERR_ASSERTION` or `AssertionFailure` can reproduce a target assertion; untyped failures cannot. `<error>` is an execution error and `<skipped>` is a skip. Contradictory/duplicate cases, inconsistent declared totals, missing/oversized/malformed reports, DTD/entities and exit/report disagreement are errors. Reports are limited to 8 MiB. This is a documented subset of JUnit, not certification of every framework's XML dialect.

The harness records canonical JSON and keeps the original XML. Offline gates recheck exact test identities and outcomes against XML and the fixed baseline. A changed case name with unchanged aggregate counts is rejected. As with other unsigned evidence, hashes do not authenticate a hostile party that rewrites every artifact. Use reviewed runners and isolated environments for untrusted projects.

Both adapters support `verify`, finite `repair`, fresh `resume`, `report`, `export` and `gate`. No changed tests, newly skipped/missing tests, unresolved failures or runtime errors can provide an automatic bug-fix PASS. Broader framework/language claims require their own real acceptance cases.

API reference: [Node 24 test reporters and TestsStream events](https://nodejs.org/docs/latest-v24.x/api/test.html).
