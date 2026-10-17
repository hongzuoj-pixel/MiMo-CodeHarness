"""One bounded HTTP request; credentials enter through stdin and never logs."""
import json
import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(data):
    # This deadline also terminates the worker if its parent dies abruptly.
    timer = threading.Timer(data["timeout"], lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    try:
        req = urllib.request.Request(data["url"], data=data["body"].encode("utf-8"),
                                     headers=data["headers"], method="POST")
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=data["timeout"]) as response:
            raw = response.read(data["max_response_bytes"] + 1)
        if len(raw) > data["max_response_bytes"]:
            return {"ok": False, "code": "RESPONSE_TOO_LARGE"}
        try:
            return {"ok": True, "raw": json.loads(raw.decode("utf-8"))}
        except (ValueError, UnicodeError, RecursionError):
            return {"ok": False, "code": "INVALID_JSON"}
    except urllib.error.HTTPError as exc:
        # Error bodies and URLs can contain credentials; keep only safe metadata.
        result = {"ok": False, "code": "HTTP_ERROR", "http_status": exc.code,
                  "retry_after": exc.headers.get("Retry-After", "")[:128]}
        exc.close()
        return result
    except (TimeoutError, socket.timeout):
        return {"ok": False, "code": "TIMEOUT"}
    except urllib.error.URLError as exc:
        return {"ok": False, "code": "TLS_ERROR" if isinstance(exc.reason, ssl.SSLError) else "CONNECTION_ERROR"}
    except (OSError, ValueError):
        return {"ok": False, "code": "CONNECTION_ERROR"}
    finally:
        timer.cancel()


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
        result = request(payload)
    except Exception:
        result = {"ok": False, "code": "WORKER_ERROR"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
