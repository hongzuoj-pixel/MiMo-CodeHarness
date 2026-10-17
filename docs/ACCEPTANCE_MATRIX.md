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
| Python/pytest/platform compatibility | Python 3.10/3.12/3.13; pytest 8/9; Linux/Windows | All jobs pass; JUnit retained even on failure |

Run ordinary checks with `python -m pytest tests -q --junitxml=results.xml`. Docker tests require a real non-root Linux Docker worker and `MIMO_DOCKER_TEST_IMAGE`; they are explicitly skipped elsewhere, never counted as passed. POSIX SIGINT integration is skipped on Windows, whose Job cleanup is exercised by timeout/normal-exit tests. The separate Docker CI job is mandatory before claiming this backend is validated.

## Final stable-release gates still pending

- Generalized cross-run caches/migration remain deferred; current recovery reuses only completed model responses within a validated run lineage. Network preparation and external services require pinned inputs and fresh execution.
- Signed/remote attestation, richer evidence migration and organization-level enforced workflow policies are outside the current unsigned bundle format. The opt-in PR template still requires maintainer-configured required checks/review protection.
- Unified provider/Agent contract, limited repair loop, retry/budget/truncation/usage tests; live paid-provider validation only when credentials and budget are available.
- Certified Node/GenericCommand support and additional real defect cases; Keil/firmware/hardware behavior remain separately scoped.
- Wheel/sdist release process, upgrade/compatibility notes, maintainer-selected license, and at least three independent new-user walkthroughs.
- One final clean checkout/install run across the supported matrix, all failure-injection scenarios, evidence audit and scope review after the last functional change.

Passing an intermediate batch is not the final completeness sign-off. Unavailable environments, skipped tests and external/manual gates must remain visible in the release record.
