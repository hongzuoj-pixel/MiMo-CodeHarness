# Product completion plan

Goal: a new user can install the harness, connect a supported provider or supply an Agent's patch, run independent acceptance in an isolated environment, understand a blocked result, recover an interruption, and hand off inspectable evidence. A passing configured test suite does not establish universal program correctness.

## Plan review

The verification foundation is implemented and tested, but the earlier two-item description (provider integration, then final tests) was too broad to be a stable-release plan. It omitted provider wire compatibility, response completion status, retry charges, recovery forks, explicit support certification, packaging, licensing and independent usability review. Feature-batch CI and final product acceptance are separate milestones.

Priorities are ordered by user dependency, with a concrete exit condition:

| Priority | Work | Completion condition |
|---|---|---|
| P0 · implemented | Isolated baseline/candidate verification, stable Python case evidence, recovery, portable reports, PASS-only gates | Existing Linux/Windows/Python/pytest/Docker/browser gates remain green |
| P1 · implemented | Unified HTTP/mock provider contract, bounded deadlines/retries, explicit usage/completion states, persistent request/token/cost reservations | Real local HTTP fault injection; API → pytest → export/gate; interruption and branching recovery cannot reset budgets; old configs and offline execution work |
| P2a · implemented | Generic external CLI Agent protocol and limited pytest bug-fix repair loop | Explicit argv, separate Agent/acceptance workspaces and credentials, process cleanup and bounded output; each round preserves its patch and independent test evidence; unchanged baseline acceptance; shared invocation/time budget, finite rounds, recovery and portable strict gate |
| P2b · adapter implemented; live trial pending | Ready-to-use vendor adapter, then live model validation | Pinned Codex CLI adapter and config generator; actual-binary/local-server CI. A real model still requires available credentials, an explicit spending allowance and a separately recorded outcome |
| P3a · implemented | Two independent real Python defect cases | boltons and more-itertools pinned source/fix commits; reproduced target assertions, reference acceptance, regression rejection, fresh recovery and portable evidence via `case-init`/`case-check` |
| P3b | Certified Node/GenericCommand checks | At least one independent bug-fix and regression case per claimed adapter; structured case evidence rather than command exit alone; documented dependency/setup boundaries |
| P4 | New-user workflow and actionable diagnosis | Complete install/configure/run/fail/recover/export walkthrough; at least three independent new users can follow the guide, with observed problems addressed and their results recorded |
| P5 | Release and final completeness sign-off | Clean wheel/sdist installation, upgrade/support notes, maintainer-selected license, supported matrix and failure-injection acceptance rerun after the last functional change |

P2–P4 may be scoped into smaller batches, but a capability is not advertised as certified before its exit condition is met. Existing `mimo verify` already accepts patches produced by external tools without needing a tool-specific Agent integration.

## Delivery sequence and status rules

The v0.10 delivery adds one ready-to-use Agent adapter, two pinned real Python defect cases, user commands/configuration, automated failure cases and inspectable acceptance records. This removes the requirement to write an adapter before trying the workflow. It does not establish model repair quality from a deterministic reply. See [Codex setup](CODEX_AGENT.md) and [real defect acceptance](REAL_DEFECT_CASES.md).

1. **Adapter and real-defect acceptance:** validate the Codex binary, response completion, fixed-baseline verification, failure handling and recoverable budgets. Local protocol replies and upstream reference patches are labelled explicitly. A live model run records the exact CLI/model, source/test inputs, observed outcome and available usage separately.
2. **Language coverage:** establish the same per-case evidence for Node before advertising support; add other languages only with their own executable acceptance.
3. **First-use validation:** complete the installation/configuration/failure/recovery/export guide and observe at least three independent new users. Automated tests and simulated users do not count as those people.
4. **Stable release:** choose the license with the maintainer; document supported versions, upgrades and recovery limits; build clean wheel/sdist artifacts; rerun the full supported matrix and failure scenarios on the final functional tree; publish the resulting acceptance record.

Every update distinguishes **implemented**, **automated protocol verified**, **real defect verified**, **live model verified**, **independent user accepted** and **release accepted**. A passing intermediate CI batch never changes later statuses automatically. Credential access, an explicit model spending allowance, real users and the maintainer's license decision are external acceptance inputs; all implementation and reproducible preparation proceed before requesting any of them.

## P1 decisions and acceptance

- Keep existing MiMo configurations and the original five-value Python `call_model` unpacking interface. Route the main harness, universal runner and optional DeepSeek API wrapper through one implementation.
- Support `mock`, OpenAI-compatible chat, Anthropic Messages and explicitly mapped generic HTTP text completions. Unknown providers/configuration, empty/malformed/truncated/refused output and unsupported tool calls fail visibly; incomplete text is never applied as a successful patch.
- Default to one network attempt. Opt-in retries apply only to transient transport/HTTP errors and obey an overall deadline and bounded Retry-After/backoff. Each retry is another reservation.
- Preserve provider-reported zero counts, distinguish actual from estimated usage, and retain usage for truncated responses. Configured prices are user inputs, not fetched prices or billing guarantees.
- Reserve requests, conservative input bytes plus maximum output, and configured estimated cost before dispatch. Never refund failed/interrupted/unknown-charge attempts. Resume shares a durable coordinator across branches of the original run; missing/damaged accounting blocks more calls. Starting a deliberately new run creates a new budget.
- Keep immutable per-attempt accounting snapshots and source/test evidence. The coordinator lives beside run directories; it must be preserved for recovery. Portable offline gates use the sealed snapshots and need no live coordinator/provider/configuration.
- Apply a real wall-clock deadline even to trickling responses, bound request/response sizes, reap HTTP workers on timeout/Ctrl-C, prevent credential-bearing redirects and exclude credential values from recorded diagnostics.
- Validate all of this without a paid provider, then separately record any live-provider trial with its model, credentials availability, explicit spending allowance and observed results. Local protocol tests cannot substitute for a live-provider claim.

## Final release record

P2 is deliberately split at its verifiable integration boundary. The generic CLI protocol is exercised by real local processes and dedicated Docker acceptance; the pinned Codex CLI has actual-binary/local-server Linux/Windows gates. The deterministic example, protocol replies and two upstream reference controls do not claim live model repair quality. No opaque CLI's internal model requests, tokens or invoice can be inferred from invocation count. See [CLI_AGENTS.md](CLI_AGENTS.md). Node adapters, broader defect diversity and independent new-user trials remain necessary before stable-release sign-off.

Use [ACCEPTANCE_MATRIX.md](ACCEPTANCE_MATRIX.md) as the executable inventory. The final record must identify the exact commit/tree, supported environments, passed/failed/skipped checks, install artifacts, real defect cases, usability evidence and any remaining exclusions. Keil firmware compilation and hardware behavior require their own toolchains and acceptance; they are not inferred from lightweight embedded scans.

General caches, signed/remote attestation and organization-wide policy administration remain later extensions. Required GitHub checks and review rules are enabled by repository maintainers. These are explicit boundaries, not hidden prerequisites for running the local verifier.
