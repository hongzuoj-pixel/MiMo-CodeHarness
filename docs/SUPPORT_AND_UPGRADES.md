# Supported scope and upgrades

v0.12 remains a beta. Automated release evidence and a stable release sign-off are separate. The package has no mandatory third-party Python runtime dependencies; configured projects/runners need their own explicitly prepared dependencies.

| Component | Automated coverage | Boundary |
|---|---|---|
| Harness | Python 3.10/3.12/3.13; pytest 8.4.2/9.0.2; Linux/Windows | Python 3.11 is within the declared range but is not a separate matrix job |
| pytest/unittest | Real assertions, runtime/collection/teardown/skip/missing evidence; fixed acceptance | Static success or empty suites cannot PASS |
| Native Node | Node 22.23+ and 24.19+; Linux on 22/24, Windows on 24 | node:test only; no arbitrary runner flags; native test/suite names must be unambiguous |
| Generic JUnit | Real command, UTF-8 subset with file/name/type identity; positive/negative boltons controls | Not every JUnit dialect; untyped failure cannot reproduce an assertion target |
| Local / venv | Clean child environments, fresh Python venvs, process cleanup | Host user permissions; not a security sandbox; venv does not install Node |
| Linux Docker | Actual non-root isolated baseline/candidate/Agent tests; Node/JUnit included | Prebuilt image, exact resolved image ID, explicit network/resources; no local fallback |
| Codex | CLI 0.163.0 native-binary protocol on Linux/Windows | Explicit model/key; live model/tool quality and billing require observed trials |
| Portable evidence | Relocation, strict gate, HTML/JSON/JUnit/log linkage; exact Node/JUnit identity | Unsigned integrity evidence, not hostile-party authentication |
| Independent user kit | One exact wheel, portable offline Python dependencies; actual Python 3.12 installation/recovery on Linux/Windows | Python/Git must already be installed; fetching pinned upstream cases needs GitHub; automated rehearsal is not a human trial |
| Embedded/other languages | Existing scans and explicit commands | No claim of full Keil firmware build, hardware validity or certified Go/Rust/Jest/Vitest acceptance |

## Install and upgrade

From the selected checkout/tag: `python -m pip install --upgrade ".[test]"`. For an offline install use the matching wheel and predownloaded test/project dependencies. Both wheel and source distributions are built and installed in CI; the Node reporter and third-party notices are packaged. Releases are not automatically published to PyPI.

Keep the previous checkout/tag and all run directories before upgrading. Do not delete `.agent-budgets/` or `.provider-budgets/` beside runs. Existing configurations remain valid; new adapters and commands are opt-in. A deliberately new run creates a deliberately new budget.

Recovery requires the exact source/test/configuration, task, engine, executable/environment and Docker identity that produced the original run. A version upgrade, changed reporter or changed Node binary can invalidate recovery. Restore the matching old environment to resume an old run, or start a new run; do not edit fingerprints/accounting to force reuse. Previously completed portable bundles remain available for inspection, subject to their evidence format and verifier compatibility.

The current version does not migrate cached tests or silently reuse old PASS. Resume recreates baseline/candidate checks; completed proposals are reused only when inputs match. Native Node executable bytes are included in local recovery identity; Docker runtime bytes are bound through image identity.

## Final release record

Retain the source-bound CI JSON, JUnit results, built distributions, real defect ZIP/JSON records, real-model trial record and three independent-user records. Inspect skipped checks by their environment: Docker and actual-Codex tests are opt-in elsewhere but mandatory in their dedicated jobs; POSIX-only process/signal tests are skipped on Windows and Windows cleanup is tested separately.

The maintainer chooses the project license and verifies redistribution rights. [Third-party notices](THIRD_PARTY_NOTICES.md) cover the included minimal upstream reference changes. Final manual inputs and automated `readiness` behavior are described in [the product acceptance guide](PRODUCT_ACCEPTANCE_CN.md).
