"""Provider wire formats and a single, explicit completion contract."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

PROVIDERS = {"mock", "openai_compatible", "anthropic", "generic_http"}
RUNTIME_DEFAULTS = {"max_attempts": 1, "backoff_seconds": .5,
                    "max_retry_delay_seconds": 30, "max_response_bytes": 4 * 1024 * 1024}


def number(value, name, minimum=0, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if value < minimum or (maximum is not None and value > maximum) or (integer and not isinstance(value, int)):
        raise ValueError(f"{name} is outside its supported range.")
    return value


def decimal_value(value, name):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal("1e12") or isinstance(value, bool):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a nonnegative decimal.") from None


def resolve_model(model, config=None):
    config = config or {}
    if not isinstance(model, dict):
        raise ValueError("Each model must be an object.")
    result = dict(model)
    kind = result.setdefault("provider_type", "openai_compatible")
    if not isinstance(kind, str) or kind not in PROVIDERS:
        raise ValueError("Unsupported provider_type; use mock, openai_compatible, anthropic or generic_http.")
    for key, default in (("timeout_seconds", 180), ("max_tokens", 4000), ("temperature", .2)):
        result.setdefault(key, config.get("default_" + key, default))
    number(result["timeout_seconds"], "timeout_seconds", .05, 3600)
    number(result["max_tokens"], "max_tokens", 1, 1000000, integer=True)
    number(result["temperature"], "temperature", 0, 2)
    runtime = config.get("runtime", {})
    local_runtime = model.get("runtime", {})
    if not isinstance(runtime, dict) or not isinstance(local_runtime, dict):
        raise ValueError("runtime must be an object.")
    result["runtime"] = {**RUNTIME_DEFAULTS, **runtime, **local_runtime}
    if set(result["runtime"]) - set(RUNTIME_DEFAULTS):
        raise ValueError("Unknown runtime option.")
    r = result["runtime"]
    number(r["max_attempts"], "max_attempts", 1, 5, integer=True)
    number(r["backoff_seconds"], "backoff_seconds", 0, 30)
    number(r["max_retry_delay_seconds"], "max_retry_delay_seconds", 0, 60)
    number(r["max_response_bytes"], "max_response_bytes", 1024, 8 * 1024 * 1024, integer=True)
    if kind == "mock":
        return result
    env = result.setdefault("api_key_env", "MIMO_API_KEY")
    if not isinstance(env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
        raise ValueError("api_key_env must name an environment variable.")
    # Retain the original MiMo overrides without changing unrelated providers.
    try:
        host = urlsplit(str(result.get("base_url", ""))).hostname or ""
    except ValueError:
        host = ""
    mimo = env == "MIMO_API_KEY" or host == "xiaomimimo.com" or host.endswith(".xiaomimimo.com")
    if mimo:
        result["base_url"] = result.get("base_url") or os.getenv("MIMO_BASE_URL", "")
        result["model"] = os.getenv("MIMO_MODEL") or result.get("model", "mimo-v2.5-pro")
    url = result.get("base_url", "")
    if not isinstance(url, str):
        raise ValueError("base_url must be an HTTP(S) URL.")
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {"https", "http"} and parts.hostname and parts.port != 0
        valid = valid and len(url) <= 2048 and not any(c.isspace() or ord(c) < 32 for c in url)
        valid = valid and not (parts.username or parts.password or parts.query or parts.fragment)
        # Local HTTP is useful for private gateways and deterministic acceptance.
        valid = valid and (parts.scheme == "https" or parts.hostname in {"localhost", "127.0.0.1", "::1"})
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("base_url requires HTTPS (or loopback HTTP), without credentials/query/fragment.")
    if not isinstance(result.get("model"), str) or not result["model"].strip():
        raise ValueError("model must be a nonempty string.")
    result.setdefault("auth_header", "api-key" if mimo else "authorization_bearer")
    result.setdefault("max_tokens_field", "max_completion_tokens" if mimo else "max_tokens")
    if not isinstance(result["auth_header"], str) or result["auth_header"] not in {"authorization_bearer", "api-key"}:
        raise ValueError("auth_header must be authorization_bearer or api-key.")
    if not isinstance(result["max_tokens_field"], str) or result["max_tokens_field"] not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Unsupported max_tokens_field.")
    if kind == "anthropic" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(result.get("anthropic_version", "2023-06-01"))):
        raise ValueError("anthropic_version must be a YYYY-MM-DD version.")
    if kind == "generic_http":
        if not isinstance(result.get("body_template"), dict) or not isinstance(result.get("headers", {}), dict):
            raise ValueError("generic_http needs object body_template and headers.")
        for key in ("response_path", "finish_reason_path", "usage_path"):
            path = result.get(key, {"response_path": ["choices", 0, "message", "content"],
                                   "finish_reason_path": ["choices", 0, "finish_reason"], "usage_path": ["usage"]}[key])
            if not isinstance(path, list) or not path or any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in path):
                raise ValueError(f"{key} must be a nonempty JSON key/index list.")
            result[key] = path
        for key, value in result.get("headers", {}).items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9-]+", key) or not isinstance(value, str) or "\r" in value or "\n" in value:
                raise ValueError("Invalid generic_http header.")
            if any(word in key.lower() for word in ("authorization", "api-key", "token", "secret")) and "${API_KEY}" not in value:
                raise ValueError("Credential headers must use ${API_KEY}, resolved only in memory.")
            if key.lower() in {"host", "content-length", "transfer-encoding", "connection"}:
                raise ValueError("Transport headers are managed by the HTTP worker.")
        template = str(result["body_template"])
        if "${PROMPT}" not in template or "${MAX_TOKENS}" not in template:
            raise ValueError("generic_http body_template must include ${PROMPT} and ${MAX_TOKENS}.")
    if "pricing" in result:
        pricing = result["pricing"]
        if not isinstance(pricing, dict) or set(pricing) != {"input_per_million_usd", "output_per_million_usd"}:
            raise ValueError("pricing needs explicit input_per_million_usd and output_per_million_usd.")
        for key, value in pricing.items():
            decimal_value(value, key)
    return result


def validate_model_config(config):
    if not isinstance(config, dict) or not isinstance(config.get("models"), list):
        raise ValueError("Model configuration must contain a models list.")
    if any(not isinstance(m, dict) or not isinstance(m.get("enabled", True), bool) for m in config["models"]):
        raise ValueError("Each model must be an object with a boolean enabled flag.")
    models = [resolve_model(m, config) for m in config["models"] if m.get("enabled", True)]
    if not models:
        raise ValueError("Model configuration contains no enabled models.")
    budget = config.get("budget", {})
    if not isinstance(budget, dict) or set(budget) - {"max_requests", "max_reserved_tokens", "max_estimated_cost_usd"}:
        raise ValueError("Unknown or invalid budget option.")
    number(budget.get("max_requests", 100), "max_requests", 1, 100000, integer=True)
    if "max_reserved_tokens" in budget:
        number(budget["max_reserved_tokens"], "max_reserved_tokens", 1, integer=True)
    if "max_estimated_cost_usd" in budget:
        decimal_value(budget["max_estimated_cost_usd"], "max_estimated_cost_usd")
        for model in models:
            if model["provider_type"] != "mock":
                pricing = model.get("pricing", {})
                if not isinstance(pricing, dict) or set(pricing) != {"input_per_million_usd", "output_per_million_usd"}:
                    raise ValueError("A cost budget requires explicit input/output pricing for each provider.")
                for key, value in pricing.items():
                    decimal_value(value, key)
    return models


@dataclass
class Completion:
    content: str
    raw: dict
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_source: str = "estimated"
    finish_reason: str = "unknown"
    provider_type: str = "mock"
    model: str = "unknown"
    attempts: list = field(default_factory=list)

    def __iter__(self):
        # Keep the original public five-value call_model interface compatible.
        return iter((self.content, self.raw, self.input_tokens, self.output_tokens, self.total_tokens))


class ProviderError(RuntimeError):
    def __init__(self, code, message, *, attempts=None, completion=None):
        super().__init__(message)
        self.code, self.attempts, self.completion = code, attempts or [], completion


def extract_path(data, path, default=None):
    try:
        for key in path:
            data = data[key]
        return data
    except (KeyError, IndexError, TypeError):
        return default


def normalize_response(raw, model, prompt):
    if not isinstance(raw, dict):
        raise ProviderError("INVALID_RESPONSE", "Provider response must be a JSON object.")
    kind = model["provider_type"]
    if kind == "anthropic":
        blocks = raw.get("content", [])
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
            raise ProviderError("INVALID_RESPONSE", "Invalid provider content blocks.")
        content = "\n".join(b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str))
        reason, usage = raw.get("stop_reason"), raw.get("usage")
        tools = any(b.get("type") == "tool_use" for b in blocks)
    elif kind == "generic_http":
        content = extract_path(raw, model["response_path"])
        reason = extract_path(raw, model["finish_reason_path"])
        usage = extract_path(raw, model["usage_path"])
        tools = False
    else:
        choice = extract_path(raw, ["choices", 0], {})
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            message = {}
        content, reason, usage = message.get("content"), choice.get("finish_reason") if isinstance(choice, dict) else None, raw.get("usage")
        tools = bool(message.get("tool_calls") or message.get("function_call"))
        if message.get("refusal"):
            raise ProviderError("REFUSED", "Provider refused the request.")
    if content is None and (tools or reason in {"length", "max_tokens", "model_context_window_exceeded"}):
        content = ""
    if not isinstance(content, str) or not isinstance(reason, str):
        raise ProviderError("INVALID_RESPONSE", "Provider response needs text and an explicit finish reason.")
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        raise ProviderError("INVALID_USAGE", "Provider usage must be an object.")
    i = usage.get("prompt_tokens", usage.get("input_tokens"))
    o = usage.get("completion_tokens", usage.get("output_tokens"))
    for value in (i, o, usage.get("total_tokens"), usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens")):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ProviderError("INVALID_USAGE", "Provider usage counts must be nonnegative integers.")
    source = "provider" if i is not None and o is not None else "estimated"
    i = max(1, len(prompt) // 4) if i is None else i
    i += (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    o = max(1, len(content) // 4) if o is None else o
    total = usage.get("total_tokens")
    total = i + o if total is None else total
    if total < i + o:
        raise ProviderError("INVALID_USAGE", "Provider total usage is inconsistent.")
    known_reasons = {"stop", "end_turn", "stop_sequence", "tool_calls", "function_call", "tool_use",
                     "length", "max_tokens", "model_context_window_exceeded", "content_filter", "pause_turn", "refusal"}
    result = Completion(content, raw, i, o, total, source, reason if reason in known_reasons else "unsupported", kind, model["model"])
    if source == "provider" and o > model["max_tokens"]:
        raise ProviderError("OUTPUT_LIMIT_EXCEEDED", "Provider usage exceeds the configured output limit.", completion=result)
    if tools or reason in {"tool_calls", "function_call", "tool_use"}:
        raise ProviderError("UNSUPPORTED_TOOL_CALL", "Tool execution requires a separate Agent runner.", completion=result)
    if reason in {"length", "max_tokens", "model_context_window_exceeded"}:
        raise ProviderError("TRUNCATED", "Provider stopped at an output/context limit; partial output is not applied.", completion=result)
    if reason not in {"stop", "end_turn", "stop_sequence"}:
        raise ProviderError("INCOMPLETE_RESPONSE", "Provider did not report a supported successful finish reason.", completion=result)
    if not content.strip():
        raise ProviderError("EMPTY_RESPONSE", "Provider returned no text.", completion=result)
    return result
