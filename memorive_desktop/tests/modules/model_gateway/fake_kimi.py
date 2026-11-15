"""Offline Kimi CLI stream-json fixture for KIMI-ADAPTER tests."""

from __future__ import annotations

import json
import sys
import time


def option(name: str, default: str | None = None) -> str | None:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return default


def emit(value: dict) -> None:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(rendered + b"\n")
    sys.stdout.buffer.flush()


try:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
except Exception:
    request = {"mode": "invalid_prompt", "value": ""}

mode = request.get("mode", "valid")
value = request.get("value", "离线 Ω")
if mode == "sleep":
    time.sleep(30)
if mode == "ansi":
    sys.stdout.buffer.write(b"\x1b[31m")
if mode == "partial":
    sys.stdout.buffer.write(b'{"role":"assistant","content":')
    sys.stdout.buffer.flush()
    sys.exit(0)

if mode == "tool":
    emit({"role": "tool", "content": "forbidden"})
else:
    if mode == "invalid_json":
        content = "not-json"
    elif mode == "schema_invalid":
        content = json.dumps({"status": 3, "value": value}, ensure_ascii=False)
    else:
        content = json.dumps(
            {
                "status": "ok",
                "value": value + "；正文自报模型=totally-different-model",
            },
            ensure_ascii=False,
        )
    emit({"role": "assistant", "content": content})

if mode == "nonzero":
    sys.exit(8)
