# External CLI Agents and finite repair

`mimo repair` turns a defect description into a bounded sequence of proposals and independent per-case acceptance. It uses the same patch verification rules as `mimo verify --task-type bug_fix`: every target must reproduce an assertion failure on the baseline, then pass without regressions or changed acceptance files. Pytest, native Node and the documented JUnit subset are supported. The original project is preserved. Review/apply the final `input.patch` yourself after a PASS.

## Run the complete free example

From a repository checkout, install `python -m pip install ".[test]"`, then run:

```bash
mimo --config examples/cli_agent/mimo-harness.json doctor --agent-config examples/cli_agent/agent.json
mimo --config examples/cli_agent/mimo-harness.json repair --agent-config examples/cli_agent/agent.json --task-file examples/cli_agent/task.txt --base WORKTREE --max-rounds 2 --name addition
mimo --config examples/cli_agent/mimo-harness.json report --run examples/cli_agent/.mimo/runs/addition
mimo export --run examples/cli_agent/.mimo/runs/addition --output addition-evidence.zip
mimo gate --bundle addition-evidence.zip
```

The included `demo_agent.py` is deterministic and makes no model requests. Round 1 fails, round 2 passes, and both retain JSON/JUnit/log evidence. It demonstrates the executable integration protocol; it is not an AI repair model. Use a new `--name` for another run. The example files live in the source repository; an installed wheel provides the commands.

## Connect a real Agent

Codex users can start with the built-in [pinned CLI adapter and `agent-init`](CODEX_AGENT.md). The steps below are for the generic JSON adapter; both share the same independent verifier, limits and recovery.

1. Run `mimo init` in your project. Review `checks` in `mimo-harness.json`; configure explicit pytest `target_tests`, e.g. `"test_calc.py::test_add"`. These IDs exclude the harness's check prefix. Confirm the defect reproduces in the selected `--base` (default `HEAD`; `WORKTREE` includes local edits).
2. Write a reviewed adapter around your Agent CLI. It receives the request-file path and emits exactly the JSON response below. Most vendor CLIs need a small adapter; vendor flags/event formats are not assumed interchangeable.
3. Save the Agent configuration, set named credentials in your shell, and run `doctor --agent-config agent.json`. This checks configuration, local executable and environment presence, without invoking an Agent. Baseline test reproduction and Docker image/setup are checked by `repair`.
4. Run `mimo repair --agent-config agent.json --task-file issue.txt --max-rounds 3 --name fix-001`. This explicitly invokes the configured program even if the ordinary `run` mode is `offline`. The default offline setting controls HTTP/mock `mimo run`, not this separate command.

```json
{
  "schema_version": 1,
  "argv": ["{python}", "my_agent_adapter.py", "{request}"],
  "cwd": ".",
  "timeout_seconds": 180,
  "max_output_bytes": 262144,
  "execution": {"mode": "local", "env_allowlist": ["MY_AGENT_API_KEY"]},
  "budget": {"max_invocations": 3, "max_reserved_seconds": 540}
}
```

`argv` is an argument array, executed without a shell. Only whole arguments `{python}`, `{request}`, `{workspace}` are substituted once; literal JSON/code braces are preserved. `cwd` must remain inside the workspace. Other relative file arguments are interpreted from `cwd`. Scripts inside the selected baseline are recommended. Existing absolute file arguments are hashed for recovery; Docker arguments must use container-visible paths. Installation commands are not accepted in Agent execution policy: use an already installed local CLI or a reviewed Docker image. `venv` Agent mode is not currently supported.

Local mode runs trusted programs with host user permissions. A copy and clean environment are not a security sandbox. For untrusted Agent execution use the supported non-root Linux Docker backend, with a prebuilt image, resource limits and an explicit network policy as described in [EXECUTION_ISOLATION.md](EXECUTION_ISOLATION.md). Set Agent `network: "bridge"` only when it needs a remote provider; acceptance can remain `network: "none"`. No automatic local fallback exists.

Agent credentials belong only in its `execution.env_allowlist`. Agent and acceptance allowlists must be disjoint. Unlisted ambient secrets and home/Python settings are removed; credential values are redacted from Agent logs and rejected in proposals. The Agent still controls its own processes and can read its allowed credentials, so review the program and provider policy before granting them.

## Request / response version 1

The request JSON has `schema_version`, `task`, `round`, `source_sha256`, `patch_basis: "original_baseline"`, `previous_patch`, `feedback` and `instructions`. Each invocation gets a fresh copy of the original snapshot. `previous_patch` is the prior cumulative proposal, not already applied. `feedback` contains bounded failed-case messages, stable node IDs, the prior verification result and check/patch errors. The private request is available through `{request}`; the copy through `{workspace}` or the working directory.

Return on stdout, then exit zero:

```json
{"schema_version":1,"status":"completed","patch":"diff --git a/calc.py b/calc.py\n..."}
```

The patch is a UTF-8 unified diff **against the original baseline**, including all accumulated changes. This strict response has exactly these fields; put adapter progress on stderr. Workspace edits do not become submitted changes. A fresh verifier applies only this declared patch to another original copy. Nonzero exit, timeout, excessive stdout/stderr, malformed JSON, incomplete output, tool-call objects and empty/non-diff proposals are errors. Returning `completed` does not establish PASS.

Per-stream retained output is capped by `max_output_bytes` (1 MiB maximum); the process tree/container is terminated on overflow, timeout, cancellation and normal completion. Setup has its own bounded execution steps; `timeout_seconds` bounds the Agent command itself. Acceptance checks use their configured timeouts independently.

## Limits and recovery

The loop stops at the first independent PASS, terminal execution/protocol error, exhausted shared allowance, or `--max-rounds` (1–100). `repair_history.json` records every completed round; only the last round determines the final outcome. Intermediate FAIL evidence stays available. Exit codes: PASS `0`, failed acceptance `2`, insufficient evidence `3`, execution/accounting error `4`, cancellation `130`.

Before dispatch the harness reserves one invocation and its complete command timeout. Failed, interrupted and uncertain reservations are never refunded. These are invocation/time limits: an opaque external CLI may make multiple internal model calls. Tokens, provider requests and monetary usage are unavailable, not zero. Set the actual model spending cap in your Agent/provider.

```bash
mimo resume --run .mimo/runs/fix-001 --name fix-001-recovered
```

Recovery preserves the original run, validates the task/source/configuration/engine/environment, may reuse completed proposals for an identical request, and recreates baseline/candidate tests. Failed/incomplete proposals can be retried only within the shared allowance. Changed diagnostic input blocks incompatible reuse. A previous PASS is never a cached test result.

Preserve `.agent-budgets/` beside run directories: recovery branches and older ancestors share this locked, durable coordinator. Missing/corrupted accounting blocks further dispatch. A new independent `repair` starts a deliberately new budget. Portable reports use sealed ledger snapshots and need no live coordinator. `gate --expect-patch final.patch` binds a repair to the last proposal's exact bytes; `--expect-base` binds its resolved Git base.

`report` shows rounds and accounting; `export` includes all round patches and independent test evidence, while excluding private task/request/Agent logs, checkpoints and source workspaces. Test logs can contain source excerpts and project data; review evidence before sharing it. Hashes detect inconsistency, not an adversary who can rewrite every unsigned artifact.

The generic protocol has local/real-Docker acceptance cases. The optional Codex adapter has separate actual-binary/local-server CI; live-model repair and tool behavior require their own credentials, spending allowance and observed validation. Neither protocol tests nor invocation counts establish billing accuracy. HTTP final-text providers remain available through `mimo run`; that path does not silently execute tool calls.
