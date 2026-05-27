#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small MiMo/OpenAI-compatible API connection test for MiMo-CodeHarness v0.2.

Usage:
  Windows PowerShell:
    $env:MIMO_API_KEY="your_key"
    $env:MIMO_BASE_URL="https://api.xiaomimimo.com/v1"
    $env:MIMO_MODEL="mimo-v2.5-pro"
    python src/test_mimo_api_connection.py

  macOS/Linux:
    export MIMO_API_KEY="your_key"
    export MIMO_BASE_URL="https://api.xiaomimimo.com/v1"
    export MIMO_MODEL="mimo-v2.5-pro"
    python src/test_mimo_api_connection.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def normalize_openai_chat_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url


def main() -> int:
    api_key = os.getenv("MIMO_API_KEY", "").strip()
    base_url = os.getenv("MIMO_BASE_URL", "").strip()
    model = os.getenv("MIMO_MODEL", "mimo-v2.5-pro").strip()

    if not api_key:
        print("[ERROR] Missing MIMO_API_KEY. Please set it first.")
        return 2
    if not base_url:
        print("[ERROR] Missing MIMO_BASE_URL. Example: https://api.xiaomimimo.com/v1")
        return 2

    endpoint = normalize_openai_chat_url(base_url)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise API connectivity tester."},
            {"role": "user", "content": "请只回复：MiMo API connected"},
        ],
        "temperature": 0.0,
        "max_completion_tokens": 32,
    }
    headers = {
        # Xiaomi MiMo official docs use the custom `api-key` header, not `Authorization: Bearer`.
        "api-key": api_key,
        "Content-Type": "application/json",
    }

    print("[INFO] Testing MiMo/OpenAI-compatible API...")
    print(f"[INFO] endpoint = {endpoint}")
    print(f"[INFO] model    = {model}")
    start = time.perf_counter()
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - start
        data = json.loads(raw_text)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        print(f"[ERROR] HTTP {exc.code}: {detail}")
        return 1
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    print("[OK] API connected successfully.")
    print(f"[OK] response = {content}")
    print(f"[OK] usage    = {json.dumps(usage, ensure_ascii=False)}")
    print(f"[OK] elapsed  = {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
