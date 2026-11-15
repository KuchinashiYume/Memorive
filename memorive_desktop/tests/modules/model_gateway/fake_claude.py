"""Offline Claude Code stream-json fixture for CLAUDE-ADAPTER tests."""

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

reported_model = "relay-remapped-backend" if mode == "relay_label_mismatch" else requested_model
if mode == "ansi":
    sys.stdout.buffer.write(b"\x1b[31m")

init = {
    "type": "system",
    "subtype": "init",
    "session_id": "fixture-session",
    "tools": [],
}
if mode != "no_model":
    init["model"] = reported_model
emit(init)

if mode == "tool":
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {}}]
            },
        }
    )
else:
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "自报模型=totally-different-model（仅正文，不是身份数据）",
                    }
                ]
            },
        }
    )

if mode == "partial":
    sys.exit(0)
if mode == "nonzero":
    sys.exit(9)

structured = {"status": "ok", "value": value}
if mode == "schema_invalid":
    structured = {"status": 3, "value": value}
terminal = {
    "type": "result",
    "subtype": "success" if mode != "result_error" else "error_during_execution",
    "is_error": mode == "result_error",
    "structured_output": structured,
    "usage": {"input_tokens": 17, "output_tokens": 5},
    "total_cost_usd": 0.0001,
}
if mode != "no_model":
    terminal["model"] = reported_model
emit(terminal)
