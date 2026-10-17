"""Bounded calls, conservative reservations and resume-aware provider accounting."""
from __future__ import annotations

import copy
import email.utils
import json
import os
import random
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

from mimo_harness.manifests import RunLock, atomic_json, digest, read_object, safe_file
from mimo_harness.providers import (
    Completion, ProviderError, decimal_value, normalize_response, number, resolve_model, validate_model_config,
)

MAX_REQUEST_BYTES = 2 * 1024 * 1024
SYSTEM_PROMPT = "You are a rigorous repository-level code agent evaluator. Prefer precise file paths and safe patches."


def ledger_summary(entries, policy):
    if not isinstance(policy, dict) or set(policy) - {"max_requests", "max_reserved_tokens", "max_estimated_cost_usd"}:
        raise ValueError("Invalid provider ledger policy.")
    number(policy.get("max_requests"), "ledger max_requests", 1, 100000, integer=True)
    if "max_reserved_tokens" in policy:
        number(policy["max_reserved_tokens"], "ledger token budget", 1, integer=True)
    if "max_estimated_cost_usd" in policy:
        decimal_value(policy["max_estimated_cost_usd"], "ledger cost budget")
    if not isinstance(entries, list) or len(entries) > policy["max_requests"]:
        raise ValueError("Provider ledger exceeds its request budget.")
    identities = set()
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Invalid provider reservation.")
        identity = row.get("reservation_id")
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity) or identity in identities:
            raise ValueError("Invalid or duplicate provider reservation identity.")
        identities.add(identity)
        number(row.get("reserved_tokens"), "reserved_tokens", 1, integer=True)
        number(row.get("attempt"), "attempt", 1, 5, integer=True)
        if "reserved_estimated_cost_usd" not in row:
            raise ValueError("Missing provider pricing provenance.")
        if row["reserved_estimated_cost_usd"] is not None:
            decimal_value(row["reserved_estimated_cost_usd"], "reserved estimated cost")
        if not isinstance(row.get("status"), str) or not isinstance(row.get("candidate_id"), str):
            raise ValueError("Invalid provider reservation status/identity.")
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if key in row:
                number(row[key], key, 0, integer=True)
        if "usage_source" in row and row["usage_source"] not in {"provider", "estimated"}:
            raise ValueError("Invalid provider usage provenance.")
    unpriced = sum(e["reserved_estimated_cost_usd"] is None for e in entries)
    known_cost = sum((Decimal(e["reserved_estimated_cost_usd"]) for e in entries if e["reserved_estimated_cost_usd"] is not None), Decimal(0))
    summary = {"policy": policy, "reserved_requests": len(entries),
               "reserved_tokens": sum(e["reserved_tokens"] for e in entries),
               "reserved_estimated_cost_usd": None if unpriced else str(known_cost),
               "known_price_reservations_usd": str(known_cost), "requests_without_pricing": unpriced,
               "provider_input_tokens": sum(e.get("input_tokens", 0) for e in entries if e.get("usage_source") == "provider"),
               "provider_output_tokens": sum(e.get("output_tokens", 0) for e in entries if e.get("usage_source") == "provider"),
               "requests_without_provider_usage": sum(e.get("usage_source") != "provider" for e in entries)}
    if "max_reserved_tokens" in policy and summary["reserved_tokens"] > policy["max_reserved_tokens"]:
        raise ValueError("Provider ledger exceeds its reserved token budget.")
    if "max_estimated_cost_usd" in policy and (unpriced or known_cost > decimal_value(policy["max_estimated_cost_usd"], "cost budget")):
        raise ValueError("Provider ledger exceeds its reserved cost budget.")
    return summary


def validate_ledger(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("summary"), dict):
        raise ValueError("Unsupported provider budget ledger.")
    expected = ledger_summary(data.get("entries"), data["summary"].get("policy"))
    if data["summary"] != expected:
        raise ValueError("Provider ledger totals disagree with reservations.")


def normalize_chat_url(url):
    url = url.strip().rstrip("/")
    return url + "/chat/completions" if url.endswith("/v1") else url


def request_data(model, prompt, secret):
    kind = model["provider_type"]
    headers = {"Content-Type": "application/json"}
    if kind == "anthropic":
        headers.update({"x-api-key": secret, "anthropic-version": model.get("anthropic_version", "2023-06-01")})
        body = {"model": model["model"], "max_tokens": model["max_tokens"],
                "temperature": model["temperature"], "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}]}
        url = model["base_url"]
    elif kind == "generic_http":
        replacements = {"${MODEL}": model["model"], "${PROMPT}": prompt,
                        "${MAX_TOKENS}": model["max_tokens"], "${TEMPERATURE}": model["temperature"]}
        def replace(value):
            if isinstance(value, str):
                if value in replacements:
                    return replacements[value]
                for key, item in replacements.items():
                    value = value.replace(key, str(item))
            elif isinstance(value, dict):
                return {k: replace(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace(v) for v in value]
            return value
        body = replace(model["body_template"])
        headers.update({key: value.replace("${API_KEY}", secret) for key, value in model.get("headers", {}).items()})
        url = model["base_url"]
    else:
        headers.update({"api-key": secret} if model["auth_header"] == "api-key" else {"Authorization": "Bearer " + secret})
        body = {"model": model["model"], model["max_tokens_field"]: model["max_tokens"],
                "temperature": model["temperature"], "stream": False,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
        url = normalize_chat_url(model["base_url"])
    body = json.dumps(body, ensure_ascii=False, allow_nan=False)
    if len(body.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ProviderError("REQUEST_TOO_LARGE", "Model input exceeds the 2 MiB request limit.")
    return url, headers, body


def redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {redact(k, secret): redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    return value


def http_attempt(url, headers, body, timeout, maximum):
    # Only TLS/proxy and OS runtime settings enter this worker's environment.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    payload = json.dumps({"url": url, "headers": headers, "body": body,
                          "timeout": timeout, "max_response_bytes": maximum}, ensure_ascii=False)
    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("provider_worker.py"))],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, start_new_session=os.name != "nt")
    try:
        try:
            output, _ = proc.communicate(payload.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": "TIMEOUT"}
        if proc.returncode == 124:
            return {"ok": False, "code": "TIMEOUT"}
        if proc.returncode or len(output) > maximum * 6 + 1024:
            return {"ok": False, "code": "WORKER_ERROR"}
        try:
            return json.loads(output)
        except (ValueError, RecursionError):
            return {"ok": False, "code": "WORKER_ERROR"}
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


class BudgetLedger:
    def __init__(self, config, checkpoint=None, path=None):
        self.policy = {"max_requests": 100, **config.get("budget", {})}
        self.checkpoint, self.path = checkpoint, path
        previous = checkpoint.previous if checkpoint else None
        self.entries = []
        if previous and previous.data.get("provider_ledger"):
            self.entries = copy.deepcopy(previous.provider_ledger["entries"])
        self.control = None
        if checkpoint:
            identity = checkpoint.data["provider_budget_id"]
            if not isinstance(identity, str) or len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
                raise ValueError("Invalid provider budget identity.")
            self.control = checkpoint.root.parent / ".provider-budgets" / identity
            if self.control.is_symlink() or self.control.parent.is_symlink():
                raise ValueError("Provider budget coordinator cannot be a symlink.")
        with self.lock():
            self.refresh(required=bool(previous))
            self.save()

    def lock(self):
        return RunLock(self.control) if self.control else nullcontext()

    def refresh(self, required=False):
        if not self.control:
            return
        path = self.control / "ledger.json"
        if not path.exists() and not required:
            return
        data = read_object(safe_file(self.control, "ledger.json"))
        checksum = data.pop("sha256", None)
        if data.get("schema_version") != 1 or digest(data) != checksum or data.get("fingerprint") != self.checkpoint.data["fingerprint"] or data.get("policy") != self.policy:
            raise ValueError("Provider budget coordinator changed; start a new run.")
        entries = data.get("entries")
        ledger_summary(entries, self.policy)
        if not isinstance(entries, list) or len(entries) < len(self.entries):
            raise ValueError("Provider budget coordinator lost reservations.")
        for old, new in zip(self.entries, entries):
            for key in ("reservation_id", "reserved_tokens", "reserved_estimated_cost_usd"):
                if old.get(key) != new.get(key):
                    raise ValueError("Provider budget reservation changed.")
        self.entries = entries

    def summary(self):
        return ledger_summary(self.entries, self.policy)

    def save(self):
        data = {"schema_version": 1, "entries": self.entries, "summary": self.summary()}
        if self.checkpoint:
            shared = {"schema_version": 1, "entries": self.entries, "policy": self.policy,
                      "fingerprint": self.checkpoint.data["fingerprint"]}
            atomic_json(self.control / "ledger.json", {**shared, "sha256": digest(shared)})
            self.checkpoint.save_provider_ledger(data)
        elif self.path:
            atomic_json(self.path, data)

    def reserve(self, model, body, candidate_id, attempt):
        with self.lock():
            self.refresh(required=bool(self.control))
            return self._reserve(model, body, candidate_id, attempt)

    def _reserve(self, model, body, candidate_id, attempt):
        # Never refund a failed, interrupted or unknown-charge request. Reserve the
        # full UTF-8 input bytes plus configured maximum output for every attempt.
        input_bound = len(body.encode("utf-8"))
        tokens = input_bound + model["max_tokens"]
        pricing = model.get("pricing")
        cost = None if pricing is None else (Decimal(input_bound) * decimal_value(pricing["input_per_million_usd"], "input pricing") +
                Decimal(model["max_tokens"]) * decimal_value(pricing["output_per_million_usd"], "output pricing")) / 1000000
        totals = self.summary()
        blocked = len(self.entries) >= self.policy["max_requests"]
        blocked |= "max_reserved_tokens" in self.policy and totals["reserved_tokens"] + tokens > self.policy["max_reserved_tokens"]
        blocked |= "max_estimated_cost_usd" in self.policy and Decimal(totals["reserved_estimated_cost_usd"]) + cost > decimal_value(self.policy["max_estimated_cost_usd"], "cost budget")
        if blocked:
            self.save()
            raise ProviderError("BUDGET_EXCEEDED", "Provider budget exhausted before sending another request.")
        row = {"reservation_id": uuid.uuid4().hex, "candidate_id": candidate_id, "attempt": attempt, "provider_type": model["provider_type"],
               "status": "RESERVED", "reserved_tokens": tokens, "reserved_estimated_cost_usd": str(cost) if cost is not None else None}
        self.entries.append(row)
        self.save()  # Durable before dispatch; an abrupt exit still consumes this reservation.
        return row

    def finish(self, row, code, elapsed, completion=None, http_status=None):
        with self.lock():
            self.refresh(required=bool(self.control))
            saved = next(e for e in self.entries if e["reservation_id"] == row["reservation_id"])
            saved.update(status=code, elapsed_seconds=round(elapsed, 4))
            if http_status is not None:
                saved["http_status"] = http_status
            if completion:
                saved.update(input_tokens=completion.input_tokens, output_tokens=completion.output_tokens,
                             total_tokens=completion.total_tokens, usage_source=completion.usage_source,
                             finish_reason=completion.finish_reason)
            row.update(saved)
            self.save()


def retry_delay(value, fallback):
    if value:
        try:
            result = float(value)
        except (ValueError, TypeError):
            try:
                result = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                return fallback
        if result >= 0 and result < float("inf"):
            return result
    return fallback


class ModelRuntime:
    def __init__(self, config=None, checkpoint=None, ledger_path=None):
        self.config = config or {}
        if config is not None:
            validate_model_config(config)
        self.ledger = BudgetLedger(self.config, checkpoint, ledger_path)

    def complete(self, model_cfg, prompt, *, offline=False, mock_content="Mock response", candidate_id="standalone"):
        model = resolve_model(model_cfg, self.config)
        if offline or model["provider_type"] == "mock":
            i, o = max(1, len(prompt) // 4), max(1, len(mock_content) // 4)
            return Completion(mock_content, {"mock": True}, i, o, i + o, "estimated", "mock", "mock", model.get("model", "mock"))
        secret = os.getenv(model["api_key_env"], "")
        if not secret:
            raise ProviderError("MISSING_CREDENTIAL", "Missing API key environment variable: " + model["api_key_env"])
        url, headers, body = request_data(model, prompt, secret)
        runtime = model["runtime"]
        deadline = time.monotonic() + model["timeout_seconds"]
        attempts = []
        for attempt in range(1, runtime["max_attempts"] + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("TIMEOUT", "Model call exceeded its total deadline.", attempts=attempts)
            try:
                row = self.ledger.reserve(model, body, candidate_id, attempt)
            except ProviderError as exc:
                exc.attempts = attempts
                raise
            started = time.monotonic()
            completion = None
            try:
                wire = http_attempt(url, headers, body, remaining, runtime["max_response_bytes"])
                code = wire.get("code", "HTTP_ERROR")
                if wire.get("ok"):
                    try:
                        completion = normalize_response(redact(wire["raw"], secret), model, prompt)
                        code = "COMPLETED"
                    except ProviderError as exc:
                        completion, code = exc.completion, exc.code
                status = wire.get("http_status")
                self.ledger.finish(row, code, time.monotonic() - started, completion, status)
            except BaseException:
                # Includes cancellation; no untrusted exception text is persisted.
                self.ledger.finish(row, "INTERRUPTED", time.monotonic() - started)
                raise
            event = {key: row[key] for key in ("attempt", "status", "elapsed_seconds", "http_status") if key in row}
            attempts.append(event)
            if code == "COMPLETED":
                completion.attempts = attempts
                return completion
            retryable = code in {"CONNECTION_ERROR", "TIMEOUT"} or (code == "HTTP_ERROR" and (status in {408, 409, 429} or status is not None and 500 <= status < 600))
            if not retryable or attempt == runtime["max_attempts"]:
                raise ProviderError(code, "Provider call failed: " + code + (f" (HTTP {status})" if status else ""), attempts=attempts, completion=completion)
            delay = retry_delay(wire.get("retry_after"), runtime["backoff_seconds"] * 2 ** (attempt - 1) * random.uniform(.5, 1))
            if delay > runtime["max_retry_delay_seconds"]:
                raise ProviderError("RETRY_DELAY_LIMIT", "Provider Retry-After exceeds the configured retry delay limit.", attempts=attempts)
            if delay >= deadline - time.monotonic():
                raise ProviderError("TIMEOUT", "Retry delay would exceed the total model deadline.", attempts=attempts)
            time.sleep(delay)
        raise AssertionError("unreachable")
