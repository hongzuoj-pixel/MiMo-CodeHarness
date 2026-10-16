# Project checks and pytest evidence (v0.5.1)

This change adds optional `checks` to the existing schema-version-1 `mimo-harness.json`. The CLI reads the policy before creating workspaces and passes the same policy to baseline and candidate. A candidate's edited configuration does not replace it.

## Install and configure

```bash
python -m pip install -e ".[test]"
mimo init --mode offline
```

Install the target project's own dependencies in this same interpreter. `init` saves suggested checks for review when it detects pytest or other commands; it does not execute them. Older configurations without `checks` retain recorded legacy detection. `checks: []` explicitly disables external checks and cannot establish behavior correctness.

Add or replace the `checks` array in your generated configuration:

```json
{
  "checks": [
    {
      "id": "build",
      "adapter": "command",
      "category": "build",
      "argv": ["{python}", "-m", "compileall", "-q", "."],
      "cwd": ".",
      "timeout_seconds": 60
    },
    {
      "id": "acceptance",
      "adapter": "pytest",
      "args": ["-q", "tests"],
      "cwd": ".",
      "timeout_seconds": 180,
      "target_tests": ["tests/test_calc.py::test_target"]
    }
  ]
}
```

Use actual paths and node IDs from your project. All checks are required; there is no silent optional-check bypass. IDs must be unique. `cwd` must stay inside the isolated project. Commands use argument arrays with `shell=False`; `{python}` selects the harness interpreter. Arbitrary shell syntax is not interpreted. The generic command adapter supports build/smoke checks; command exit status alone cannot establish per-test regression correctness.

```bash
mimo doctor
mimo verify --base HEAD --patch fix.patch --task-type bug_fix --run-tests
mimo report
```

The normal change/refactor mode does not require an existing defect. Bug-fix mode requires every configured target to fail an assertion in the baseline and pass in the candidate. Unchanged failing targets produce `FAIL`; targets that never reproduced or were not configured produce `INSUFFICIENT_EVIDENCE`.

## Evidence and decisions

- Case IDs use `check-id::pytest-node-id`, including parametrized case identifiers. Setup, call and teardown are recorded separately.
- Pytest assertion failures are behavior failures. Import/collection, setup, teardown and runner errors produce `ERROR`; they cannot be used as fail-to-pass defect proof.
- Pytest exit code 5 (no collected tests) produces insufficient evidence. Unknown/non-test-failure exit codes produce `ERROR`. Missing pytest has an explicit diagnostic.
- Baseline passing cases that fail in the candidate are regressions even if the total failure count is unchanged.
- Lost collected IDs, fail-fast leaving collected tests unexecuted, and newly skipped/xfail/xpass cases produce insufficient evidence.
- Existing baseline skips that remain unchanged are listed as excluded execution scope. An all-skipped suite cannot pass.
- Existing test files, pytest configuration, conftest files, test-directory resources, and collected custom-named Python tests are fingerprinted. Changed/deleted baseline acceptance inputs require independent review and cannot silently earn `PASS`. New tests do not replace old acceptance cases.
- Detected stdlib unittest suites also record individual IDs, skips, errors, and zero-test outcomes without a pytest dependency. Explicit configurable target selection is currently provided by the pytest adapter.

Summaries include `test_cases`, `collected_tests`, `target_tests`, `protected_files`, policy source and the original command return code. Verification results list `fixed_tests`, `regressed_tests`, `missing_tests`, `skipped_tests`, and `changed_acceptance_files`. Each pytest check saves stdout/stderr, raw structured evidence and JUnit XML beside the baseline/candidate logs. Existing CLI exit codes remain 0/2/3/4.

## Scope

The default local backend shares the interpreter's dependencies and is not a sandbox. v0.5.2 adds independent venvs, managed process cleanup, and a Linux Docker backend; see [execution isolation](EXECUTION_ISOLATION.md). Test hooks/project code are trusted in local mode. Acceptance hashes are a conservative change check, not tamper-proof attestation or proof of business completeness.

The regression suite invokes real pytest subprocesses for repair success, unchanged defects, same-count regressions, deletion, weakening, skips/xfail, parametrized case loss, collection/setup/teardown errors, empty suites, fail-fast, missing pytest, timeouts, and HEAD-based CLI verification in a path with spaces and Chinese characters. CI runs the suite on Python 3.10/3.12/3.13, Linux/Windows, and pytest 8.4.2/9.0.2, and retains the pinned SlideNote behavior gate.

Pytest references: [exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html), [report output and JUnit XML](https://docs.pytest.org/en/stable/how-to/output.html).
