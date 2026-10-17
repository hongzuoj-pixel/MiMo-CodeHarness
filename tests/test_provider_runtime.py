"""Real HTTP, installed CLI and crash/recovery contracts; no paid API calls."""
import datetime
import email.utils
import json
import os
import signal
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mimo_harness.cli import main
from mimo_harness.model_runtime import ModelRuntime, retry_delay
from mimo_harness.providers import ProviderError, resolve_model, validate_model_config

SECRET = "provider-contract-private-key"
PATCH = "```diff\n--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n```"


def chat(content="Completed answer", reason="stop", usage=None):
    return {"choices": [{"message": {"content": content}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2} if usage is None else usage}


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", SECRET)
    monkeypatch.delenv("MIMO_MODEL", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    servers = []
    def start(actions):
        state = {"actions": deque(actions), "requests": [], "started": threading.Event()}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
                state["started"].set()
                action = state["actions"].popleft() if state["actions"] else {"status": 500}
                if action.get("wait"):
                    time.sleep(action["wait"])
                payload = action.get("raw")
                if payload is None:
                    payload = json.dumps(action.get("body", chat())).encode()
                self.send_response(action.get("status", 200))
                for key, value in action.get("headers", {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    if action.get("trickle"):
                        for byte in payload:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(.02)
                    else:
                        self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}", state
    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def model(url, kind="openai_compatible", **options):
    result = {"name": "Contract", "provider_type": kind, "model": "reviewed-model",
              "api_key_env": "TEST_PROVIDER_KEY", "base_url": url + "/v1",
              "max_tokens": 64, "timeout_seconds": 5}
    if kind == "anthropic":
        result["base_url"] = url + "/v1/messages"
    if kind == "generic_http":
        result.update(base_url=url + "/complete", headers={"Authorization": "Bearer ${API_KEY}"},
                      body_template={"model": "${MODEL}", "input": "${PROMPT}", "limit": "${MAX_TOKENS}"},
                      response_path=["answer"], finish_reason_path=["finish"], usage_path=["usage"])
    result.update(options)
    return result


def config(m, **values):
    return {"models": [m], **values}


@pytest.mark.parametrize("kind", ["openai_compatible", "mimo", "anthropic", "generic_http"])
def test_real_provider_wire_formats_usage_and_legacy_unpacking(gateway, kind):
    body = chat()
    if kind == "anthropic":
        body = {"content": [{"type": "text", "text": "Completed answer"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 2, "cache_read_input_tokens": 3}}
    elif kind == "generic_http":
        body = {"answer": "Completed answer", "finish": "stop", "usage": {"input_tokens": 0, "output_tokens": 2}}
    url, state = gateway([{"body": body}])
    m = model(url, "openai_compatible" if kind == "mimo" else kind)
    if kind == "mimo":
        m.update(auth_header="api-key", max_tokens_field="max_completion_tokens")
    runtime = ModelRuntime(config(m))
    result = runtime.complete(m, "真实 prompt")
    content, raw, i, o, total = result
    assert content == "Completed answer"
    assert result.usage_source == "provider" and i == (3 if kind == "anthropic" else 0)
    assert (o, total) == (2, i + 2)
    req = state["requests"][0]
    if kind == "anthropic":
        assert req["headers"]["X-Api-Key"] == SECRET
        assert req["body"]["max_tokens"] == 64
        assert req["path"] == "/v1/messages"
    else:
        key = "Api-Key" if kind == "mimo" else "Authorization"
        assert req["headers"][key] == (SECRET if kind == "mimo" else "Bearer " + SECRET)
        assert req["body"]["limit" if kind == "generic_http" else "max_completion_tokens" if kind == "mimo" else "max_tokens"] == 64
    assert runtime.ledger.summary()["reserved_requests"] == 1
    assert runtime.ledger.summary()["reserved_estimated_cost_usd"] is None
    assert runtime.ledger.summary()["requests_without_pricing"] == 1


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503, 529])
def test_transient_retry_is_bounded_and_every_attempt_is_reserved(gateway, status):
    url, state = gateway([{"status": status, "headers": {"Retry-After": "0"}}, {}])
    m = model(url)
    runtime = ModelRuntime(config(m, runtime={"max_attempts": 2, "backoff_seconds": 0}))
    result = runtime.complete(m, "prompt")
    assert len(state["requests"]) == 2
    assert state["requests"][0]["body"] == state["requests"][1]["body"]
    assert [e["status"] for e in result.attempts] == ["HTTP_ERROR", "COMPLETED"]
    assert result.attempts[0]["http_status"] == status
    assert runtime.ledger.summary()["reserved_requests"] == 2
    assert runtime.ledger.summary()["requests_without_provider_usage"] == 1


@pytest.mark.parametrize("status", [302, 400, 401, 403, 404, 422])
def test_permanent_errors_and_redirects_never_retry_or_expose_error_bodies(gateway, status):
    url, state = gateway([{"status": status, "body": {"secret": SECRET}, "headers": {"Location": "/other"}}])
    m = model(url)
    runtime = ModelRuntime(config(m, runtime={"max_attempts": 3, "backoff_seconds": 0}))
    with pytest.raises(ProviderError) as error:
        runtime.complete(m, "prompt")
    assert error.value.code == "HTTP_ERROR"
    assert len(state["requests"]) == 1
    assert SECRET not in str(error.value) + json.dumps(runtime.ledger.entries)


def test_default_never_retries_and_retry_after_obeys_total_deadline(gateway):
    url, state = gateway([{"status": 503}, {}])
    m = model(url)
    with pytest.raises(ProviderError):
        ModelRuntime(config(m)).complete(m, "prompt")
    assert len(state["requests"]) == 1
    url, state = gateway([{"status": 429, "headers": {"Retry-After": "3"}}])
    m = model(url, timeout_seconds=1.5)
    with pytest.raises(ProviderError, match="deadline"):
        ModelRuntime(config(m, runtime={"max_attempts": 3})).complete(m, "prompt")
    assert len(state["requests"]) == 1
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=10)
    assert 8 < retry_delay(email.utils.format_datetime(future), 0) <= 10
    assert retry_delay("not-a-date", .25) == .25


@pytest.mark.parametrize("body,code", [
    (chat(PATCH, "length"), "TRUNCATED"),
    (chat(None, "length"), "TRUNCATED"),
    (chat(""), "EMPTY_RESPONSE"),
    ({"choices": []}, "INVALID_RESPONSE"),
    (chat({"not": "text"}), "INVALID_RESPONSE"),
    (chat(usage={"prompt_tokens": -1, "completion_tokens": 2}), "INVALID_USAGE"),
    (chat(usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 1}), "INVALID_USAGE"),
    (chat(usage={"prompt_tokens": 0, "completion_tokens": 65}), "OUTPUT_LIMIT_EXCEEDED"),
    ({"choices": [{"message": {"content": None, "tool_calls": [{}]}, "finish_reason": "tool_calls"}]}, "UNSUPPORTED_TOOL_CALL"),
    ({"choices": [{"message": {"content": "", "refusal": "no"}, "finish_reason": "stop"}]}, "REFUSED"),
    (chat("answer", "content_filter"), "INCOMPLETE_RESPONSE"),
])
def test_incomplete_or_invalid_output_never_becomes_a_completed_response(gateway, body, code):
    url, state = gateway([{"body": body}])
    m = model(url)
    runtime = ModelRuntime(config(m, runtime={"max_attempts": 3}))
    with pytest.raises(ProviderError) as error:
        runtime.complete(m, "prompt")
    assert error.value.code == code
    assert len(state["requests"]) == 1
    assert runtime.ledger.entries[0]["status"] == code
    if code == "TRUNCATED":
        assert error.value.completion.content == (body["choices"][0]["message"]["content"] or "")
        assert error.value.completion.usage_source == "provider"


@pytest.mark.parametrize("action,code", [({"raw": b"{bad"}, "INVALID_JSON"),
                                          ({"raw": b"x" * 2048}, "RESPONSE_TOO_LARGE")])
def test_bounded_wire_response(gateway, action, code):
    url, state = gateway([action])
    m = model(url, runtime={"max_response_bytes": 1024})
    with pytest.raises(ProviderError) as error:
        ModelRuntime(config(m)).complete(m, "prompt")
    assert error.value.code == code and len(state["requests"]) == 1


def test_real_trickling_response_cannot_extend_total_deadline_and_worker_is_reaped(gateway, monkeypatch):
    url, state = gateway([{"trickle": True}])
    import mimo_harness.model_runtime as runtime_module
    real = subprocess.Popen
    workers = []
    def tracked(*args, **kwargs):
        worker = real(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(runtime_module.subprocess, "Popen", tracked)
    m = model(url, timeout_seconds=1)
    started = time.monotonic()
    with pytest.raises(ProviderError) as error:
        ModelRuntime(config(m)).complete(m, "prompt")
    assert error.value.code == "TIMEOUT"
    assert time.monotonic() - started < 3
    assert len(state["requests"]) == 1
    assert workers and all(p.poll() is not None for p in workers)


def test_http_worker_watchdog_exits_even_without_parent_deadline_enforcement(gateway):
    import mimo_harness.provider_worker as worker
    url, state = gateway([{"trickle": True}])
    payload = {"url": url + "/v1/chat/completions", "headers": {"Content-Type": "application/json"},
               "body": "{}", "timeout": 1, "max_response_bytes": 1024}
    result = subprocess.run([sys.executable, worker.__file__], input=json.dumps(payload).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    assert result.returncode == 124 and len(state["requests"]) == 1


@pytest.mark.parametrize("budget", [{"max_reserved_tokens": 1}, {"max_estimated_cost_usd": "0.000001"}])
def test_token_and_configured_cost_reservations_block_before_network(gateway, budget):
    url, state = gateway([])
    m = model(url, pricing={"input_per_million_usd": "1", "output_per_million_usd": "1"})
    runtime = ModelRuntime(config(m, budget=budget))
    with pytest.raises(ProviderError) as error:
        runtime.complete(m, "prompt")
    assert error.value.code == "BUDGET_EXCEEDED" and not state["requests"]
    assert not runtime.ledger.entries


def test_retry_cannot_bypass_request_budget(gateway):
    url, state = gateway([{"status": 429, "headers": {"Retry-After": "0"}}, {}])
    m = model(url)
    runtime = ModelRuntime(config(m, runtime={"max_attempts": 3}, budget={"max_requests": 1}))
    with pytest.raises(ProviderError) as error:
        runtime.complete(m, "prompt")
    assert error.value.code == "BUDGET_EXCEEDED"
    assert len(state["requests"]) == len(error.value.attempts) == 1


def test_decimal_cost_cap_keeps_full_reservations_after_success(gateway):
    from mimo_harness.model_runtime import request_data
    url, state = gateway([{}, {}, {}])
    m = model(url, pricing={"input_per_million_usd": "0.1", "output_per_million_usd": "0.2"})
    _, _, body = request_data(resolve_model(m), "prompt", SECRET)
    expected = (Decimal(len(body.encode())) * Decimal("0.1") + 64 * Decimal("0.2")) / 1000000
    runtime = ModelRuntime(config(m, budget={"max_estimated_cost_usd": str(expected * 2)}))
    runtime.complete(m, "prompt")
    runtime.complete(m, "prompt")
    with pytest.raises(ProviderError) as error:
        runtime.complete(m, "prompt")
    assert error.value.code == "BUDGET_EXCEEDED" and len(state["requests"]) == 2
    assert Decimal(runtime.ledger.summary()["reserved_estimated_cost_usd"]) == expected * 2
    assert runtime.ledger.summary()["requests_without_pricing"] == 0


def test_missing_usage_is_explicitly_estimated_and_large_requests_do_not_send(gateway):
    url, state = gateway([{"body": chat(usage={"total_tokens": None})}])
    m = model(url)
    runtime = ModelRuntime(config(m))
    response = runtime.complete(m, "prompt")
    assert response.usage_source == "estimated"
    assert runtime.ledger.summary()["requests_without_provider_usage"] == 1
    with pytest.raises(ProviderError) as error:
        runtime.complete(m, "x" * (2 * 1024 * 1024))
    assert error.value.code == "REQUEST_TOO_LARGE" and len(state["requests"]) == 1


def test_server_retry_delay_limit_does_not_trigger_an_early_retry(gateway):
    url, state = gateway([{"status": 429, "headers": {"Retry-After": "30"}}])
    m = model(url, runtime={"max_attempts": 3, "max_retry_delay_seconds": 1})
    with pytest.raises(ProviderError) as error:
        ModelRuntime(config(m)).complete(m, "prompt")
    assert error.value.code == "RETRY_DELAY_LIMIT" and len(state["requests"]) == 1


def project(tmp_path, monkeypatch, cfg):
    root = tmp_path / "project"
    root.mkdir()
    (root / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "test_value.py").write_text("from value import VALUE\ndef test_value(): assert VALUE == 2\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    monkeypatch.chdir(root)
    assert main(["init", "--mode", "api"]) == 0
    (root / ".mimo/models.json").write_text(json.dumps(cfg), encoding="utf-8")
    return root


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def record(run):
    return json.loads((run / "model_outputs.jsonl").read_text(encoding="utf-8").splitlines()[0])


def cli(*args):
    result = subprocess.run([sys.executable, "-m", "mimo_harness", *args],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert "Traceback" not in result.stderr, result.stderr
    return result.returncode


def test_cli_api_to_real_pytest_gate_bundle_and_resume_without_another_request(tmp_path, monkeypatch, gateway):
    url, state = gateway([{"body": chat(PATCH + "\n" + SECRET)}])
    m = model(url)
    root = project(tmp_path, monkeypatch, config(m, budget={"max_requests": 1}))
    before = (root / "value.py").read_bytes()
    assert cli("doctor") == 0
    assert cli("run", "--name", "first", "--limit-tasks", "1", "--run-tests") == 0
    run = root / ".mimo/runs/first"
    row = record(run)
    assert row["usage_source"] == "provider" and row["input_tokens_est"] == 0
    assert row["finish_reason"] == "stop" and len(row["attempts"]) == 1
    assert cli("gate", "--run", str(run)) == 0
    assert cli("resume", "--run", str(run), "--name", "second") == 0
    assert len(state["requests"]) == 1
    assert read(root / ".mimo/runs/second/provider_ledger.json")["summary"]["reserved_requests"] == 1
    assert read(root / ".mimo/runs/second/run_manifest.json")["reused_candidates"]
    assert cli("report", "--run", str(run)) == 0
    assert "Reserved requests" in (run / "MIMO_RUN_SUMMARY.md").read_text()
    output = tmp_path / "evidence.zip"
    assert cli("export", "--run", str(run), "--output", str(output)) == 0
    with zipfile.ZipFile(output) as archive:
        assert "evidence/provider_summary.json" in archive.namelist()
        assert all(SECRET.encode() not in archive.read(n) for n in archive.namelist())
    assert all(SECRET.encode() not in p.read_bytes() for p in run.rglob("*") if p.is_file())
    assert (root / "value.py").read_bytes() == before
    monkeypatch.chdir(tmp_path)
    root.rename(tmp_path / "moved-project")
    monkeypatch.delenv("TEST_PROVIDER_KEY")
    assert cli("gate", "--bundle", str(output)) == 0


def test_truncated_valid_diff_is_retained_but_never_applied(tmp_path, monkeypatch, gateway):
    url, state = gateway([{"body": chat(PATCH, "length")}])
    root = project(tmp_path, monkeypatch, config(model(url)))
    assert main(["run", "--name", "truncated", "--limit-tasks", "1"]) == 4
    run = root / ".mimo/runs/truncated"
    row = record(run)
    assert row["error_code"] == "TRUNCATED" and row["content"] == PATCH
    assert row["status"] == "failed" and row["usage_source"] == "provider"
    assert not list((run / "patches").glob("*.patch"))
    assert main(["gate", "--run", str(run)]) == 4


def test_failed_calls_consume_budget_across_repeated_resume(tmp_path, monkeypatch, gateway):
    url, state = gateway([{"status": 503}, {"status": 503}, {}])
    root = project(tmp_path, monkeypatch, config(model(url), budget={"max_requests": 2}))
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 4
    first = root / ".mimo/runs/first"
    assert record(first)["usage_source"] == "unavailable"
    assert main(["resume", "--run", str(first), "--name", "second"]) == 4
    second = root / ".mimo/runs/second"
    assert main(["resume", "--run", str(second), "--name", "third"]) == 4
    third = root / ".mimo/runs/third"
    assert len(state["requests"]) == 2
    assert record(third)["error_code"] == "BUDGET_EXCEEDED"
    assert read(third / "provider_ledger.json")["summary"]["reserved_requests"] == 2
    # Resuming an older sibling must also see requests made by its descendants.
    assert main(["resume", "--run", str(first), "--name", "fork"]) == 4
    assert record(root / ".mimo/runs/fork")["error_code"] == "BUDGET_EXCEEDED"
    assert len(state["requests"]) == 2
    original = (third / "provider_ledger.json").read_bytes()
    (third / "provider_ledger.json").write_bytes(original + b" ")
    assert main(["resume", "--run", str(third), "--name", "damaged"]) == 4
    assert len(state["requests"]) == 2


def test_abrupt_parent_death_preserves_inflight_reservation(tmp_path, monkeypatch, gateway):
    url, state = gateway([{"wait": 1.5}])
    root = project(tmp_path, monkeypatch, config(model(url, timeout_seconds=2), budget={"max_requests": 1}))
    with (tmp_path / "console.log").open("wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "mimo_harness", "run", "--name", "first", "--limit-tasks", "1"],
                                stdout=log, stderr=log)
        try:
            assert state["started"].wait(timeout=15)
        finally:
            proc.kill()
            proc.wait(timeout=5)
    run = root / ".mimo/runs/first"
    assert read(run / "provider_ledger.json")["entries"][0]["status"] == "RESERVED"
    assert main(["resume", "--run", str(run), "--name", "second"]) == 4
    assert record(root / ".mimo/runs/second")["error_code"] == "BUDGET_EXCEEDED"
    assert len(state["requests"]) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT CLI acceptance; deadline cleanup is tested on Windows.")
def test_ctrl_c_during_http_cleans_worker_and_keeps_budget_evidence(tmp_path, monkeypatch, gateway):
    url, state = gateway([{"wait": 1.5}])
    root = project(tmp_path, monkeypatch, config(model(url)))
    with (tmp_path / "console.log").open("wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "mimo_harness", "run", "--name", "first", "--limit-tasks", "1"],
                                stdout=log, stderr=log)
        try:
            assert state["started"].wait(timeout=15)
            proc.send_signal(signal.SIGINT)
            assert proc.wait(timeout=5) == 130
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    run = root / ".mimo/runs/first"
    assert read(run / "run_manifest.json")["status"] == "CANCELLED"
    assert read(run / "provider_ledger.json")["entries"][0]["status"] == "INTERRUPTED"
    assert main(["report", "--run", str(run)]) == 0
    assert "Reserved requests" in (run / "MIMO_RUN_SUMMARY.md").read_text()
    assert main(["export", "--run", str(run), "--output", str(tmp_path / "cancelled.zip")]) == 0
    assert main(["gate", "--bundle", str(tmp_path / "cancelled.zip")]) == 4


@pytest.mark.parametrize("damage", ["missing", "checksum", "schema", "negative"])
def test_missing_or_damaged_shared_budget_blocks_old_run_resume(tmp_path, monkeypatch, gateway, damage):
    from mimo_harness.manifests import digest
    url, state = gateway([{"status": 503}, {}])
    root = project(tmp_path, monkeypatch, config(model(url)))
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 4
    run = root / ".mimo/runs/first"
    identity = read(run / "run_manifest.json")["provider_budget_id"]
    coordinator = root / ".mimo/runs/.provider-budgets" / identity / "ledger.json"
    if damage == "missing":
        coordinator.rename(coordinator.with_suffix(".moved"))
    else:
        data = read(coordinator)
        if damage == "checksum":
            data["entries"][0]["reserved_tokens"] += 1
        else:
            if damage == "schema":
                data["schema_version"] = 99
            else:
                data["entries"][0]["reserved_tokens"] = -1
            data.pop("sha256")
            data["sha256"] = digest(data)
        coordinator.write_text(json.dumps(data), encoding="utf-8")
    assert main(["resume", "--run", str(run), "--name", "second"]) == 4
    assert len(state["requests"]) == 1


def test_different_recovery_branches_share_one_concurrent_request_cap(tmp_path, monkeypatch, gateway):
    url, state = gateway([{"status": 503}, {"status": 503}, {"wait": .3}])
    root = project(tmp_path, monkeypatch, config(model(url), budget={"max_requests": 3}))
    assert main(["run", "--name", "first", "--limit-tasks", "1"]) == 4
    first = root / ".mimo/runs/first"
    assert main(["resume", "--run", str(first), "--name", "second"]) == 4
    second = root / ".mimo/runs/second"
    children = []
    with (tmp_path / "branches.log").open("wb") as log:
        try:
            for source, name in ((first, "fork-a"), (second, "fork-b")):
                children.append(subprocess.Popen([sys.executable, "-m", "mimo_harness", "resume", "--run", str(source), "--name", name],
                                                 stdout=log, stderr=log))
            codes = [p.wait(timeout=15) for p in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
    assert sorted(codes) == [0, 4]
    assert len(state["requests"]) == 3
    assert main(["resume", "--run", str(first), "--name", "fork-c"]) == 4
    assert record(root / ".mimo/runs/fork-c")["error_code"] == "BUDGET_EXCEEDED"
    assert len(state["requests"]) == 3


@pytest.mark.parametrize("change", [
    {"runtime": {"max_attempts": True}}, {"runtime": {"max_attempts": 6}},
    {"runtime": {"misspelled": 2}}, {"budget": {"max_requests": 0}},
    {"budget": {"max_estimated_cost_usd": "1"}},
    {"models": [{"provider_type": "unknown"}]}, {"models": ["bad"]},
    {"models": [{"provider_type": []}]}, {"models": [model("https://gateway.example", auth_header=[])]},
    {"models": [model("https://gateway.example", pricing="bad")]},
    {"models": [{"provider_type": "openai_compatible", "base_url": "https://key:secret@example.com/v1"}]},
    {"models": [{"provider_type": "openai_compatible", "base_url": "http://example.com/v1"}]},
])
def test_doctor_rejects_bad_runtime_or_provider_before_network(tmp_path, monkeypatch, change):
    monkeypatch.setenv("TEST_PROVIDER_KEY", SECRET)
    root = project(tmp_path, monkeypatch, {"models": [model("https://gateway.example")], **change})
    assert main(["doctor", "--json"]) == 4
    assert main(["run", "--name", "bad"]) == 4
    assert not (root / ".mimo/runs/bad").exists()


def test_offline_skips_all_network_and_mimo_overrides_do_not_change_other_models(gateway, monkeypatch):
    url, state = gateway([])
    m = model(url)
    monkeypatch.delenv("TEST_PROVIDER_KEY")
    monkeypatch.setenv("MIMO_MODEL", "mimo-only")
    result = ModelRuntime(config(m)).complete(m, "prompt", offline=True)
    assert not state["requests"] and result.raw["mock"] and result.usage_source == "estimated"
    assert resolve_model(m)["model"] == "reviewed-model"
    assert resolve_model({**m, "api_key_env": "MIMO_API_KEY"})["model"] == "mimo-only"


def test_legacy_universal_entry_uses_the_same_wire_contract(gateway):
    import universal_model_runner as universal
    url, state = gateway([{}])
    m = model(url)
    result = universal.call_model({"task_id": "T1"}, m, config(m), dry_run=False)
    assert result["usage_source"] == "provider" and result["input_tokens"] == 0
    assert state["requests"][0]["headers"]["Authorization"] == "Bearer " + SECRET


def test_generic_template_expands_once_and_preserves_literals_inside_prompt(gateway):
    url, state = gateway([{"body": {"answer": "done", "finish": "stop", "usage": {"input_tokens": 0, "output_tokens": 0}}}])
    m = model(url, "generic_http")
    m["body_template"]["input"] = "prefix ${PROMPT}"
    prompt = "code=${MAX_TOKENS}; model=${MODEL}; temperature=${TEMPERATURE}"
    ModelRuntime(config(m)).complete(m, prompt)
    sent = state["requests"][0]["body"]
    assert sent["input"] == "prefix " + prompt
    assert sent["limit"] == 64
