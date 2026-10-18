"""CLI contract failures plus the actual pinned Codex binary on a local server."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mimo_harness.cli import main
from mimo_harness.cli_agents import load_agent_config
from mimo_harness.codex_adapter import CODEX_VERSION, parse_events, resolve_codex_binary
from mimo_harness.manifests import atomic_json
from test_cli_repair import ORIGINAL, case, diff, read, repair


def stream(response, terminal="turn.completed"):
    return ("\n".join(json.dumps(v) for v in [
        {"type": "thread.started", "thread_id": "contract"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(response)}},
        {"type": terminal, "usage": {"input_tokens": 0, "output_tokens": 20}},
    ]) + "\n").encode()


def codex_profile(c, argv, **extra):
    cfg = {"schema_version": 1, "adapter": "codex", "argv": argv, "cli_version": CODEX_VERSION,
           "model": "mimo-contract-test", "timeout_seconds": 30, "max_output_bytes": 1048576,
           "execution": {"mode": "local", "env_allowlist": ["CODEX_API_KEY"]},
           "budget": {"max_invocations": 2}, **extra}
    atomic_json(c.agent, cfg)
    return cfg


def test_codex_adapter_real_subprocess_and_untrusted_project_config(tmp_path, monkeypatch):
    c = case(tmp_path)
    monkeypatch.setenv("CODEX_API_KEY", "fake-contract-credential")
    (c.project / ".codex").mkdir()
    (c.project / ".codex/config.toml").write_text('model_provider="hostile"\n')
    patches = [diff(ORIGINAL, ORIGINAL.replace("a - b", "a")), diff(ORIGINAL, ORIGINAL.replace("a - b", "a + b"))]
    program = c.project / "adapter.py"
    program.write_text(f'''import json,sys
from pathlib import Path
if sys.argv[-1] == "--version":
    print("codex-cli {CODEX_VERSION}"); sys.exit(0)
assert "--json" in sys.argv and "--ignore-user-config" in sys.argv and "--ignore-rules" in sys.argv
assert sys.argv[sys.argv.index("--sandbox")+1] == "read-only"
assert not Path(".codex").exists()
request=json.loads(next(Path.cwd().glob(".mimo-agent-*.json")).read_text(encoding="utf-8"))
patches={patches!r}
response={{"schema_version":1,"status":"completed","patch":patches[request["round"]-1]}}
for event in [{{"type":"thread.started"}},{{"type":"turn.started"}},
 {{"type":"item.completed","item":{{"type":"agent_message","text":json.dumps(response)}}}},
 {{"type":"turn.completed","usage":{{"input_tokens":0,"output_tokens":20}}}}]:
 print(json.dumps(event))
''', encoding="utf-8")
    codex_profile(c, [sys.executable, str(program)])
    assert repair(c) == 0
    assert read(c)["stop_reason"] == "VERIFIED_PASS"
    assert read(c)["agent"]["model"] == "mimo-contract-test"
    assert read(c)["agent"]["cli_version"] == CODEX_VERSION
    assert (c.project / ".codex/config.toml").read_text() == 'model_provider="hostile"\n'
    assert main(["gate", "--run", str(tmp_path / "runs/first")]) == 0
    assert main(["resume", "--run", str(tmp_path / "runs/first"), "--name", "resumed"]) == 0
    assert read(c, "resumed", "agent_ledger.json")["summary"]["invocations_reserved"] == 2


@pytest.mark.parametrize("payload,code", [
    (b"\xff", "INVALID_EVENTS"),
    (b'{}\n', "INVALID_EVENTS"),
    (b'{"type":"turn.started"}\n', "INCOMPLETE_RESPONSE"),
    (stream({}, "turn.failed"), "TURN_FAILED"),
    (b'{"type":"turn.started"}\n{"type":"turn.completed"}\n', "INCOMPLETE_RESPONSE"),
    (stream({}) + b'{"type":"turn.started"}\n', "EVENTS_AFTER_COMPLETION"),
    (b'{"type":"turn.started"}\n{"type":"turn.started"}\n', "MULTIPLE_TURNS"),
    (b'{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}\n', "INVALID_EVENTS"),
])
def test_codex_completion_cannot_be_inferred_from_partial_or_error_events(payload, code):
    with pytest.raises(ValueError, match=code):
        parse_events(payload)


def test_zero_usage_is_preserved_as_cli_reported():
    response = {"schema_version": 1, "status": "completed", "patch": "--- a/x\n+++ b/x\n"}
    result, usage = parse_events(stream(response))
    assert result == response and usage["input_tokens"] == 0


@pytest.mark.parametrize("field,value", [("cli_version", "latest"), ("model", ""), ("model", "bad\nmodel"),
    ("argv", ["codex", "--dangerously-bypass-approvals-and-sandbox"]),
    ("base_url", "http://example.com/v1"), ("base_url", "https://user:password@example.com"),
    ("execution", {"mode": "local", "env_allowlist": ["CODEX_HOME", "CODEX_API_KEY"]})])
def test_codex_configuration_is_explicit_and_transport_bounded(tmp_path, field, value):
    c = case(tmp_path)
    cfg = codex_profile(c, [sys.executable, "fake.py"])
    cfg[field] = value
    atomic_json(c.agent, cfg)
    with pytest.raises(ValueError):
        load_agent_config(c.agent)


def test_agent_init_profile_is_explicit_and_never_overwrites(tmp_path, monkeypatch):
    import mimo_harness.codex_adapter as adapter
    binary = str(tmp_path / "codex-native")
    monkeypatch.setattr(adapter, "resolve_codex_binary", lambda value: binary)
    monkeypatch.setattr(adapter, "probe_codex", lambda argv: f"codex-cli {CODEX_VERSION}")
    output = tmp_path / "codex-agent.json"
    args = ["agent-init", "--model", "chosen-model", "--output", str(output)]
    assert main(args) == 0
    cfg = load_agent_config(output)
    assert cfg["argv"] == [binary] and cfg["model"] == "chosen-model"
    assert cfg["execution"]["env_allowlist"] == ["CODEX_API_KEY"]
    assert cfg["budget"] == {"max_invocations": 3, "max_reserved_seconds": 540}
    saved = output.read_bytes()
    assert main(args) == 4 and output.read_bytes() == saved


def test_npm_package_resolves_to_native_binary(tmp_path):
    import platform
    system = {"linux": "linux", "darwin": "darwin", "windows": "win32"}[platform.system().lower()]
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine().lower()]
    package = tmp_path / "node_modules/@openai/codex"
    launcher = package / "bin/codex.js"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("// npm launcher\n")
    binary = package.parent / f"codex-{system}-{arch}/vendor/test-triple/bin" / ("codex.exe" if os.name == "nt" else "codex")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native-package-bytes")
    assert resolve_codex_binary(str(launcher)) == str(binary.resolve())


class ResponsesServer:
    def __init__(self, patches, status=200):
        self.requests, self.patches, self.status = [], patches, status
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                if owner.status != 200:
                    self.send_response(owner.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"injected local failure","type":"server_error"}}')
                    return
                patch = owner.patches[min(len(owner.requests) - 1, len(owner.patches) - 1)]
                output = json.dumps({"schema_version": 1, "status": "completed", "patch": patch})
                item = {"id": "msg_contract", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": output, "annotations": []}]}
                response = {"id": "resp_contract", "object": "response", "created_at": 1, "status": "completed",
                            "model": "mimo-contract-test", "output": [item],
                            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}
                events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                          {"type": "response.output_item.added", "output_index": 0,
                           "item": {**item, "status": "in_progress", "content": []}},
                          {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0,
                           "content_index": 0, "delta": output},
                          {"type": "response.output_item.done", "output_index": 0, "item": item},
                          {"type": "response.completed", "response": response}]
                payload = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.mark.skipif(not os.getenv("MIMO_CODEX_BINARY"), reason="Requires the actual pinned Codex CLI; dedicated CI installs it")
@pytest.mark.parametrize("status", [200, 503])
def test_actual_codex_binary_local_responses_contract(tmp_path, monkeypatch, status):
    c = case(tmp_path)
    monkeypatch.setenv("CODEX_API_KEY", "fake-local-contract-key")
    binary = resolve_codex_binary(os.environ["MIMO_CODEX_BINARY"])
    patches = [diff(ORIGINAL, ORIGINAL.replace("a - b", "a")), diff(ORIGINAL, ORIGINAL.replace("a - b", "a + b"))]
    with ResponsesServer(patches, status=status) as server:
        codex_profile(c, [binary], base_url=server.url)
        code = repair(c)
        stderr = c.root / "runs/first/agent_runs/repair-001/logs/codex_stderr.log"
        assert code == (0 if status == 200 else 4), (server.requests, stderr.read_text(encoding="utf-8")[-4000:])
        assert len(server.requests) == (2 if status == 200 else 1)
        assert all(r["authorization"] == "Bearer fake-local-contract-key" for r in server.requests)
        assert all(r["body"]["model"] == "mimo-contract-test" for r in server.requests)
        if status == 200:
            assert read(c)["stop_reason"] == "VERIFIED_PASS"
            bundle = tmp_path / "codex-contract.zip"
            assert main(["export", "--run", str(tmp_path / "runs/first"), "--output", str(bundle)]) == 0
            assert main(["gate", "--bundle", str(bundle)]) == 0
        else:
            assert read(c, file="agent_ledger.json")["summary"]["invocations_reserved"] == 1
