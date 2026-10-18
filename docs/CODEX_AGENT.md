# Codex CLI repair adapter

The optional `codex` adapter removes the need to write a JSON wrapper for Codex. The harness supplies a read-only proposal request, translates completed CLI JSONL events, then applies only the returned diff in a separate verifier. A model answer is accepted only after fixed-baseline pytest acceptance. The original checkout is preserved.

## Install and configure

Install Python 3.10+, Git and a supported Node/npm runtime, then from the harness checkout:

```bash
python -m pip install ".[test]"
npm install --global @openai/codex@0.163.0
mimo agent-init --model YOUR_MODEL_ID --output .mimo/codex-agent.json
```

Replace `YOUR_MODEL_ID` with an identifier available to your API account. There is no implicit model choice or subscription-login reuse. `agent-init` resolves the npm launcher to the native executable, checks the exact CLI version, and writes a new configuration without overwriting an existing file. It makes no model request. The generated path is machine-specific; regenerate the profile on another machine.

Set `CODEX_API_KEY` in the shell or your secret manager, never in configuration or a committed file. Review the account/provider spending limit before a live run. Invocation/time allowances cannot cap the opaque CLI's internal requests or invoice.

## Run on a prepared real defect

Start with a fresh case from [REAL_DEFECT_CASES.md](REAL_DEFECT_CASES.md). Its immutable acceptance commit and pytest target are already configured:

```bash
mimo case-init --case boltons-write --output .mimo/boltons
mimo --config .mimo/boltons/mimo-harness.json doctor --agent-config .mimo/codex-agent.json
mimo --config .mimo/boltons/mimo-harness.json repair --agent-config .mimo/codex-agent.json --task-file .mimo/boltons/task.txt --max-rounds 3 --name codex-001
mimo --config .mimo/boltons/mimo-harness.json report --run .mimo/boltons/runs/codex-001
mimo export --run .mimo/boltons/runs/codex-001 --output codex-001.zip
mimo gate --bundle codex-001.zip
```

`repair` explicitly invokes the configured program even if ordinary `run` mode is offline. `doctor` checks configuration, executable/version and named environment presence, without a model request. A failed target must reproduce as an assertion before the first Agent invocation. Review the final `input.patch` before applying it yourself; the harness never installs it into the original project.

For your own project, run `mimo init`, configure explicit pytest `checks` and `target_tests`, and write an issue/task text file. See the full [repair and recovery protocol](CLI_AGENTS.md) and [pytest evidence guide](PYTEST_EVIDENCE.md).

## Profile and execution boundary

The generated schema version 1 profile has `adapter: "codex"`, a native executable `argv`, `cli_version: "0.163.0"`, your explicit `model`, and a local execution allowlist containing only `CODEX_API_KEY`. Defaults reserve at most three invocations of 180 seconds each. These limits persist across recovery branches. Other CLI versions fail preflight instead of assuming compatibility. Do not add CLI flags to `argv`; the adapter supplies the reviewed flags.

The adapter uses `exec --json --ephemeral --ignore-user-config --ignore-rules --sandbox read-only`, a required output schema and `approval_policy="never"`. Plugins, apps, subagents, shell snapshots, bundled/host skill discovery and analytics are disabled. Project `.codex` configuration on the working-directory ancestry is removed only from the private Agent copy. Model and CLI version are retained in `repair_history.json`; CLI-reported usage, when available, is private diagnostic metadata rather than a billing ledger. The public invocation ledger continues to report token/cost usage as unavailable.

The native CLI still runs with host user permissions in this generated local profile. Its read-only command policy is an additional tool boundary, not a security certification for an untrusted executable. The generic Agent Docker backend remains separately available; this Codex profile's actual-binary certification covers local Linux/Windows protocol execution, not Codex-in-Docker tools or every model tool path.

An optional `base_url` selects a compatible Responses gateway; HTTPS is required except loopback HTTP for local tests. URL credentials/query/fragment are rejected. Gateway profiles disable configurable request/stream retries. The default OpenAI provider may retry internally; the harness bounds the complete CLI deadline and invocation reservations, not internal HTTP attempts. Credentials are excluded from model-generated shell environments and kept separate from acceptance tests.

## Diagnosis and validation

| Symptom | Action |
|---|---|
| CLI missing/version mismatch | Install the exact pinned package; regenerate the profile after updating the executable |
| Missing `CODEX_API_KEY` | Set it in the invoking environment; acceptance tests must not allowlist it |
| `BASELINE_TARGET_NOT_REPRODUCED` | Fix import/setup/collection problems and confirm every target fails an assertion on the selected base |
| `AGENT_TIMEOUT`, `AGENT_EXIT_ERROR`, output limit | Inspect private `agent_runs/repair-NNN/logs/`; requests/events can contain project data |
| Incomplete/invalid/failed Codex events | No patch is submitted; retain the error and inspect the CLI/provider response |
| Independent FAIL | Inspect stable failed/fixed/regressed IDs; another round may use failure feedback within the shared limits |
| Recovery input/version changed | Restore the exact inputs/runtime for recovery, or start a deliberately new run |

`mimo resume --run PATH --name NEW_NAME` recreates tests in fresh workspaces. It may reuse a completed proposal only when all bound inputs match. Preserve `.agent-budgets/` beside the run directories.

Dedicated Linux/Windows CI installs the actual pinned native binary and points it at a local Responses server with a fake key. It exercises two rounds, strict completion, real pytest, export/gate and injected HTTP failure. Ordinary tests also reject incomplete/malformed event streams and mismatched configuration. This protocol acceptance makes no paid model calls and does not measure model repair quality. A live-model trial remains a separately recorded acceptance input with credentials, an explicit spending allowance and observed results.

References: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive/), [CLI reference](https://developers.openai.com/codex/cli/reference/), [pinned upstream implementation](https://github.com/openai/codex/tree/rust-v0.163.0).
