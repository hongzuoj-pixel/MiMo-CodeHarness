# Model providers and bounded execution

The main `mimo run` workflow, `universal_model_runner.call_model` and `DeepSeekRunner` share a stdlib-only HTTP runtime. `mimo doctor` validates the configuration locally; it does not call a paid endpoint. Start with `mimo init --mode offline` to test installation, or `mimo init --mode api` and review `.mimo/models.json` to use an API.

## MiMo and OpenAI-compatible providers

Existing `config/api_models_mimo.json` continues to work. MiMo keeps the `api-key` header and `max_completion_tokens` request field. Other compatible services default to bearer authentication and `max_tokens`; both are configurable:

```json
{
  "default_timeout_seconds": 180,
  "default_max_tokens": 4000,
  "default_temperature": 0.2,
  "runtime": {"max_attempts": 1, "backoff_seconds": 0.5, "max_retry_delay_seconds": 30},
  "budget": {"max_requests": 12, "max_reserved_tokens": 200000},
  "models": [{
    "name": "Reviewed provider",
    "provider_type": "openai_compatible",
    "model": "YOUR_DEPLOYED_MODEL",
    "base_url": "https://YOUR_GATEWAY/v1",
    "api_key_env": "CODE_MODEL_KEY",
    "auth_header": "authorization_bearer",
    "max_tokens_field": "max_tokens",
    "enabled": true
  }]
}
```

Set `CODE_MODEL_KEY` in your environment, switch the project configuration to `"mode": "api"`, then run `mimo doctor` and `mimo run --name api-first --limit-tasks 1`. A `/v1` URL gains `/chat/completions`; a full endpoint is used as given. `MIMO_MODEL` and the fallback `MIMO_BASE_URL` apply only to MiMo configurations (`MIMO_API_KEY` or a Xiaomi MiMo host); they do not silently override another provider.

Endpoints use HTTPS. Loopback HTTP is supported for local gateways and contract tests. URL credentials, query strings and redirects are rejected. Credentials are read using `api_key_env`, passed to the HTTP worker in memory, redacted from successful response data, and never written into request logs or exception tracebacks.

## Other response formats

| provider_type | Endpoint/request | Response contract |
|---|---|---|
| `mock` | No network or credential needed | Explicit mock output and estimated usage |
| `openai_compatible` | Chat messages; configurable auth/token field | Text at `choices[0].message.content`, explicit `finish_reason: stop`, optional `usage` |
| `anthropic` | Full `/v1/messages` URL, `x-api-key`, version `2023-06-01`, `max_tokens` | Text blocks, `stop_reason: end_turn` or `stop_sequence`, optional input/output/cache usage |
| `generic_http` | Full URL, reviewed headers/body template | Explicit text/finish/usage key paths; supported successful finish values `stop`, `end_turn`, `stop_sequence` |

Example generic adapter fields inside a model object:

```json
{
  "name": "Private gateway",
  "provider_type": "generic_http",
  "model": "YOUR_DEPLOYED_MODEL",
  "base_url": "https://YOUR_GATEWAY/complete",
  "api_key_env": "CODE_MODEL_KEY",
  "headers": {"Authorization": "Bearer ${API_KEY}"},
  "body_template": {"model": "${MODEL}", "prompt": "${PROMPT}", "max_tokens": "${MAX_TOKENS}"},
  "response_path": ["answer"],
  "finish_reason_path": ["finish_reason"],
  "usage_path": ["usage"]
}
```

Whole-value `${MAX_TOKENS}` and `${TEMPERATURE}` placeholders become JSON numbers. Expansion is a single pass over template text: inserted prompts retain literal `${MODEL}`, `${MAX_TOKENS}` and similar code unchanged, including inside prefixed/suffixed prompt fields. Prompt and maximum-token placeholders are required; credentials belong in headers. Header values containing credentials use `${API_KEY}`; arbitrary environment substitution in configuration JSON is no longer performed. This avoids expanding secret values into saved model signatures or breaking JSON quoting.

These adapters accept final text responses. Tool/function calls, unsupported termination reasons, malformed/empty replies and output/context truncation are failed model runs, with diagnostic reason codes. Partial text/known usage may be retained locally but no patch from it is applied. The legacy `CLIAgentRunner` is not a certified isolated Agent integration; use `mimo verify --patch PATH` for independently produced patches until P2 is accepted.

## Deadlines, retries and usage

`timeout_seconds` is a total wall-clock limit for one logical completion, including attempts and backoff. Each HTTP request runs in a bounded worker process; a trickling body cannot extend this limit. Timeout/Ctrl-C terminates and reaps the worker; its own watchdog also bounds it after abrupt parent death. Defaults: 2 MiB maximum request body and 4 MiB response; `runtime.max_response_bytes` may be configured from 1 KiB to 8 MiB.

`runtime.max_attempts` defaults to **1** and supports 1–5. Opt-in retries cover connection/timeouts, HTTP 408/409/429 and 5xx. Authentication/configuration/response errors are not retried. Retry-After (seconds or HTTP date) is honored within `max_retry_delay_seconds` and the total deadline; an excessive delay blocks the retry. Otherwise exponential backoff has jitter. A retry can be billed even if a previous response was lost.

`provider_summary.json`, `provider_ledger.json`, `token_usage.csv` and the CLI report show completion/error reasons, attempts, usage provenance and budget reservations. Legacy `*_tokens_est` column names remain compatible; `usage_source: provider` identifies reported counts, including zero and Anthropic cache input counts. Missing usage is explicitly estimated. Error bodies and raw HTTP headers are not retained. Full model responses remain local and are excluded from exported bundles.

A failed request without a usable response records `usage_source: unavailable`; its zero placeholder token columns are not reported as measured or estimated consumption. Its full reservation remains in the budget ledger.

## Budgets and recovery

| Budget option | Meaning |
|---|---|
| `max_requests` | Hard cap on dispatched request attempts across this recovery lineage; default 100 |
| `max_reserved_tokens` | Cap on cumulative reservations: each request body UTF-8 byte count plus configured maximum output tokens |
| `max_estimated_cost_usd` | Cap on those reservations priced using explicit per-model input/output prices |

Cost budgets require every enabled real provider to have `pricing: {"input_per_million_usd": "...", "output_per_million_usd": "..."}`. Use the reviewed prices for your deployment. No prices are inferred or hardcoded. Decimal arithmetic avoids binary rounding around limits. This is a conservative planning estimate, not a tokenizer certification or a guaranteed invoice ceiling; gateways, provider overhead, pricing changes or ignored output limits can differ from the reservation. An over-limit response is failed rather than accepted.

Without configured pricing, the total reserved cost is `null`/Unknown, with a count of unpriced requests and a separate known-price subtotal. Missing prices are never presented as a free request or a zero-cost total.

Reservations are durably written **before** sending; failed/interrupted/unknown-charge requests are never refunded. Completed model responses can be reused without another request, but builds/tests still run fresh. A shared, locked `.provider-budgets/<lineage-id>/ledger.json` beside the run directories ensures repeated resumes from an older run see consumption by its descendants. Each run retains its own sealed snapshot, so old evidence is not rewritten. Preserve this coordinator when moving backups used for recovery; missing/damaged state fails closed. Starting a new run explicitly creates a new budget; these limits are not a global provider-account quota.

Offline mocks do not consume network reservations. Portable evidence contains accounting snapshots and can be gated without live source, provider credentials or the shared coordinator. Unsigned snapshots provide consistency checks, not external billing attestation.

## Protocol references and validation scope

Wire formats are checked against [Xiaomi's chat API](https://mimo.mi.com/docs/en-US/api/chat) and [Anthropic's Messages documentation](https://platform.claude.com/docs/en/api/messages). Retry/error conventions are informed by the [official Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) and [its SDK documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python). No SDK runtime dependency is required.

Automated tests use actual local HTTP servers and subprocesses for success, limiting/retry, authentication errors, redirects, malformed/empty/truncated/tool responses, unknown usage, byte/deadline limits, interruption, abrupt death, lineage budgets and an API-to-real-pytest-to-offline-gate workflow. A live paid-provider deployment test requires available credentials and an explicit spending allowance and is recorded separately.
