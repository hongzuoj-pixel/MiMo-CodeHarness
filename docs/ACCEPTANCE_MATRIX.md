# End-to-end acceptance and release gates

The final product is accepted by complete workflows and failure scenarios, not by feature presence or a descriptive score. This matrix records the automated gates for the current development line and the remaining stable-release gates.

## Current automated gates

| Workflow/scenario | Automated evidence | Required result |
|---|---|---|
| Fresh package install → init → doctor → run → report | Linux/Windows installed CLI job | No internal script knowledge or API key required for offline path |
| Real HEAD-based defect repair with fixed acceptance | `test_pytest_evidence.py` | Target assertion fails → passes; source unchanged |
| Unfixed target, same-count new regression | Real pytest subprocess fixtures | FAIL and exact failed/fixed/regressed IDs |
| Zero tests, deleted/weakened tests, skip/xfail, collection/setup/teardown errors | Real pytest/unittest fixtures | No false PASS; classified reason and logs |
| Candidate preparation with offline dependency wheel | `test_execution_isolation.py` CLI fixture | Independent baseline/candidate venvs; source unchanged; report readable |
| Dependency contamination | Two real venvs and two Docker environments | Install in first is not visible in second |
| Preparation failure | Actual failing prepare command | Checks do not start; error/preparation evidence retained |
| Parent timeout and parent normal exit with background child | Actual spawned processes on Linux/Windows | Managed descendants terminated |
| Ctrl-C during checks and during preparation | Actual POSIX CLI SIGINT | Exit 130; child terminated; partial report readable |
| Credentials/config boundary | Actual child environment and Docker tests | No ambient model/cloud keys or parent Python path |
| Large command output | Actual 2 MB output | Full disk log; bounded diagnostic tail |
| Docker resource/network/root boundary | Dedicated Linux Docker CI job | Non-root, read-only root, no socket/ambient key, bounded memory/PIDs, network disabled |
| Docker timeout and normal teardown | Actual containers inspected after command | Container removal confirmed |
| Missing Docker/runtime/preparation | Failure fixtures | Explicit ERROR; no local fallback |
| Interrupted models and abrupt process death → resume | `test_run_recovery.py` public CLI and actual processes | Completed model outputs reused; incomplete/failed calls retried; original evidence retained |
| Previous PASS → changed external behavior → resume | Actual baseline/candidate pytest on WORKTREE and HEAD | Fresh execution yields FAIL; no cached verdict |
| Binary/test/config/provider/environment/engine/patch/ref changes | Input invalidation fixtures | ERROR before model reuse or project commands |
| Damaged snapshots/checkpoints/evidence and concurrent recovery | File corruption fixtures and actual OS locks | No unsafe reuse or overwrite |
| Docker recovery and mutable image tag | Dedicated Docker CLI recovery test | New containers per attempt; changed image ID rejected |
| Verify → export → relocate → offline gate | `test_evidence_bundles.py`, Linux/Windows installed CLI | HTML/JSON/JUnit/log links work without original source/configuration |
| Forged summary, recorder/JUnit/report disagreement, invalid ZIP paths/data | Corruption and malicious archive fixtures | ERROR; never a false approval |
| FAIL/insufficient/analysis-only/error/cancelled/unsealed export | Actual execution and interruption fixtures | Diagnostic artifacts retained; strict gate blocks |
| Trusted-base PR runner with good/regressing commits | Real Docker CI runner test | PASS returns 0; regression returns 2; both retain evidence; base unchanged |
| Real external repository regression | Fixed SlideNote commit gate | Structured case evidence and clean source |
| MiMo-compatible/OpenAI-compatible/Anthropic/generic provider formats | Real local HTTP server + subprocess workers | Correct auth/request fields, explicit completion status and actual/estimated usage including zero |
| Limits, rate limiting, redirects and incomplete provider output | Real HTTP 408/409/429/5xx, permanent errors, truncation/tool/invalid/empty replies, trickled bodies | Finite retries/deadline; every attempt reserved; no partial output applied; worker cleanup |
| API → real pytest → report → bundle/gate → resume | Local HTTP provider through public CLI | PASS from fresh project tests; portable accounting; completed response reused without a new request |
| Failed/inflight/branching recovery and provider budget corruption/loss | Actual CLI processes, abrupt parent death, repeated/parallel recovery | Reservations preserved across branches; no extra call after cap or invalid accounting |
| External CLI → two-round independent pytest repair → report/export/gate | `test_cli_repair.py`, complete free example | First FAIL retained, final PASS recomputed, all patches/case evidence portable, original source unchanged |
| Agent timeout/output overflow/nonzero/invalid/incomplete/tool-only replies | Actual CLI subprocess fault injection | ERROR, bounded retained logs, process-tree cleanup, invocation reservation retained |
| Agent credentials and acceptance tampering | Actual separate child environments | Credentials absent from tests/export; leaked credential patch rejected; changed acceptance cannot PASS |
| Repair resume, stale PASS, older-ancestor/sibling budgets and damaged inputs/accounting | Public CLI recovery and durable ledger/lock fixtures | Fresh tests; completed proposals reused only for matching inputs; no budget reset/refund or false approval |
| External CLI repair in real Docker | Dedicated Docker two-round workflow | Separate containers/credentials, pinned image identity, cleanup verified, portable PASS gate |
| Pinned Codex CLI → local Responses → two-round pytest repair | Actual npm native binary on Linux/Windows; fake local credentials | Explicit model/version and completion; reference PASS, portable gate, injected HTTP failure; no paid model claim |
| Two independent pinned real Python defects | `case-init`/`case-check` for boltons and more-itertools on Linux/Windows | Target assertion reproduced; reference accepted, new regression rejected, fresh recovery, portable positive/negative gates, unchanged source |
| Python/pytest/platform compatibility | Python 3.10/3.12/3.13; pytest 8/9; Linux/Windows | All jobs pass; JUnit retained even on failure |
| Native Node per-case acceptance and repair | Actual Node 22/24 on Linux and 24 on Windows, plus real Docker | Stable parent/test IDs, assertion/error classification, skip/TODO/missing/empty handling, finite two-round repair, fresh recovery, strict portable proof |
| Generic command with typed JUnit evidence | Actual commands and pinned boltons unittest/JUnit control | Predeclared acceptance files; typed assertions, exact identity/outcomes, XML/exit/totals consistency; malformed/missing/stale reports cannot PASS |
| Real Node upstream defect | Pinned ms source/fix and independent tests | Reject malformed numeric input, preserve negative decimals/units; reference PASS and intentional regression FAIL, fresh recovery and portable gate |
| Live trial preparation and evidence | `test_product_acceptance.py` with explicitly synthetic fixture declarations | No dispatch without allowance/credentials; changed inputs and loopback labels blocked; reference ZIP cannot be relabelled as model repair |
| Independent-user preparation | Blank guide/feedback, duplicate/stale/unfinished record tests | Templates count as zero users; separate identities and run evidence required; independence remains an actual external observation |
| Exact-engine user kit installation and recovery | `test_user_kit.py`, actual built-wheel `ci_user_kit.py` on Linux/Windows | Reject changed/missing/foreign inputs; private install in a Chinese path with spaces; recover missing pip/CLI files without losing feedback; real boltons controls, manual recovery and relocated evidence gate; rehearsal counts as zero human trials |
| Final source-bound automation | `release-automation`, exact Git commit/tree plus all 14 prerequisite job results | Automatic evidence stays pending without the real model, three independent users and maintainer license; no automatic publication |

Run ordinary checks with `python -m pytest tests -q --junitxml=results.xml`. Docker tests require a real non-root Linux Docker worker and `MIMO_DOCKER_TEST_IMAGE`; they are explicitly skipped elsewhere, never counted as passed. POSIX SIGINT integration is skipped on Windows, whose Job cleanup is exercised by timeout/normal-exit tests. The separate Docker CI job is mandatory before claiming this backend is validated.

The two actual-Codex tests require `MIMO_CODEX_BINARY` and the exact pinned executable; ordinary runs skip them explicitly. The `agent-and-real-cases` jobs install it and must pass both tests plus both real-repository workflows. These jobs retain JUnit and case ZIP/JSON evidence. Reference patches and local protocol replies are controls, not live AI results.

## Final stable-release gates still pending

- Generalized cross-run caches/migration remain deferred; current recovery reuses only completed model responses within a validated run lineage. Network preparation and external services require pinned inputs and fresh execution.
- Signed/remote attestation, richer evidence migration and organization-level enforced workflow policies are outside the current unsigned bundle format. The opt-in PR template still requires maintainer-configured required checks/review protection.
- Generic CLI Agent repair, the pinned Codex event adapter and two real Python defect controls have automated gates. Live paid-provider/model repair validation still requires credentials and an explicit spending allowance. Opaque external Agent tokens/costs remain unavailable; invocation/time caps are not billing caps.
- Broader language/framework and defect diversity beyond the documented native Node/JUnit subset; Keil/firmware/hardware behavior remain separately scoped.
- Maintainer-selected license and at least three independent new-user walkthroughs. Wheel/sdist checks, upgrade/support notes, exact-wheel user kits, offline install/setup recovery, trial/feedback generation and source-bound CI collection are implemented; generated templates and automated installer rehearsals are not completed external evidence.
- One final clean checkout/install run across the supported matrix, all failure-injection scenarios, evidence audit and scope review after the last functional change.

Passing an intermediate batch is not the final completeness sign-off. Unavailable environments, skipped tests and external/manual gates must remain visible in the release record.

See [the reviewed product completion plan](PRODUCT_PLAN.md) for priority, dependency and exit conditions.
