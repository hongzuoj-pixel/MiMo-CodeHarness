# Reproduce two real Python defects

v0.11 also provides `ms-invalid-number` through native Node and `boltons-junit` through a generic JUnit-emitting command. See [Node/JUnit setup and evidence](STRUCTURED_CHECKS.md). The original two pytest cases remain unchanged.

These cases let a new user exercise the entire verifier without an API key, a model call or a custom adapter. GitHub access is needed to prepare the pinned source. The checks use Python/pytest already installed in the invoking environment; the selected cases have no additional third-party runtime dependencies.

From the harness checkout:

```bash
python -m pip install ".[test]"
mimo case-init --case boltons-write --output .mimo/cases/boltons
mimo case-check --directory .mimo/cases/boltons --name first
mimo case-init --case more-itertools-bucket --output .mimo/cases/more-itertools
mimo case-check --directory .mimo/cases/more-itertools --name first
```

Both output directories must be new. To repeat acceptance on an unchanged prepared case, choose a new `--name`. The commands preserve upstream source and reject changed prepared inputs. Failed preparation leaves its directory for diagnosis; choose another output directory after correcting network/Git issues.

## Source and defect identity

| Case | Pinned upstream baseline | Reference fix | Independent acceptance |
|---|---|---|---|
| `boltons-write` | [mahmoud/boltons · 961dcff](https://github.com/mahmoud/boltons/commit/961dcff3f42e73b245aef65e377fe82763b257bb) | [4e5faa3](https://github.com/mahmoud/boltons/commit/4e5faa3d7e4008d89e0d8bf1ea87b6d9a061a16d) | `SpooledStringIO.write` returns a character count; Unicode and rolled-stream readback remain correct |
| `more-itertools-bucket` | [more-itertools/more-itertools · 1ea82a7](https://github.com/more-itertools/more-itertools/commit/1ea82a711c69f590054987b5cb194157f8ce8ac4) | [67fcf64](https://github.com/more-itertools/more-itertools/commit/67fcf640477c78605580f5ee703a54c73c1e7a8a) | `bucket` honours a falsey callable validator; default/permitted-key behavior is preserved |

`case-init` checks out the exact upstream baseline and creates one local acceptance commit adding only `.mimo_acceptance/test_regression.py` and `pytest.ini`. The original upstream commit remains its parent. This allows ordinary `verify`/`repair` to use a fixed Git base including the independently authored acceptance tests. It does not alter or push to the upstream repository.

`case.json` records both upstream SHAs, the local acceptance base, source inventory hash, generated input hashes, provenance and license link. Reference and intentionally regressing patches live outside the project so an Agent's workspace does not receive them. `task.txt` describes the defect without the reference patch.

The minimal reference changes follow those upstream fixes. Upstream code remains under each project's license: [boltons LICENSE](https://github.com/mahmoud/boltons/blob/961dcff3f42e73b245aef65e377fe82763b257bb/LICENSE) and [more-itertools LICENSE](https://github.com/more-itertools/more-itertools/blob/1ea82a711c69f590054987b5cb194157f8ce8ac4/LICENSE). [Third-party notices](THIRD_PARTY_NOTICES.md) are also included in source/wheel distributions. Preserve them when redistributing upstream code or patches. The regression mutations are authored negative controls, not upstream changes or AI-produced repairs.

## What acceptance proves

`case-check` runs these public workflow operations:

1. Validate pinned source, test/configuration/patch hashes and Git ancestry.
2. Reproduce the original target assertion, accept the reference fix with no missing/newly failing acceptance tests, then export and gate the portable bundle.
3. Resume the reference run with fresh baseline/candidate tests and gate that result.
4. Reject an intentional mutation that fixes the target but breaks previously passing behavior, then export it and confirm its portable gate returns FAIL.
5. Confirm prepared source/acceptance inputs remain unchanged and write `results/NAME/acceptance.json`.

The intentional regression prints a blocked FAIL and returns verification code `2`; the enclosing `case-check` returns `0` only when the positive and negative controls both behave as required. Configuration/execution/corruption failures return `4`. `acceptance.json` states `mode: "upstream_reference_controls"` and `model_called: false`, and includes bundle hashes. These controls validate the verifier, not an AI model's success rate or every upstream behavior. Linux/Windows CI retains both cases' records and portable ZIP evidence.

Open an exported ZIP's `report.html` after extracting it, or run `mimo gate --bundle PATH.zip --json`. Run `mimo --config CASE/mimo-harness.json report --run CASE/runs/NAME-reference` for a concise text report. Portable evidence omits private Agent requests/credentials and source workspaces; review test logs for project data before sharing.

## Use an actual Agent

Use `mimo --config CASE/mimo-harness.json repair --agent-config AGENT.json --task-file CASE/task.txt --name NEW_NAME`. The prepared immutable base and target tests are already configured. `repair` explicitly invokes the chosen Agent. Configure credentials and actual spending limits first; see the ready-to-use [Codex adapter](CODEX_AGENT.md) or [generic JSON Agent protocol](CLI_AGENTS.md).
