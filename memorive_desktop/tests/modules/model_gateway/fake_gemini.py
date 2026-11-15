"""Offline Gemini CLI stream-json fixture for GEMINI-ADAPTER tests."""

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
requested_model = option("--model", "unknown-model")

if mode == "sleep":
    time.sleep(30)
if mode == "ansi":
    sys.stdout.buffer.write(b"\x1b[31m")

init = {"type": "init", "session_id": "fixture-session"}
if mode != "no_model":
    init["model"] = "gemini-relabeled-model" if mode == "label_mismatch" else requested_model
emit(init)

if mode == "tool":
    emit({"type": "tool_use", "tool_name": "run_shell_command", "parameters": {}})
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
    emit({"type": "message", "role": "assistant", "content": content, "delta": False})

if mode == "partial":
    sys.exit(0)
if mode == "nonzero":
    sys.exit(8)

stats = {"input_tokens": 13, "output_tokens": 7, "total_tokens": 20, "tool_calls": 0}
if mode == "token_conflict":
    stats["total_tokens"] = 99
emit(
    {
        "type": "result",
        "status": "error" if mode == "result_error" else "success",
        "stats": stats,
    }
)
