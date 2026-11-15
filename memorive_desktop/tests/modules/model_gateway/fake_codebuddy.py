"""Offline CodeBuddy headless fixture. It never opens a network connection."""

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
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


try:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
except Exception:
    request = {"mode": "invalid_prompt", "value": ""}

mode = request.get("mode", "valid")
value = request.get("value", "离线 Ω")
requested_model = option("--model", "unknown-model")
output_format = option("--output-format", "stream-json")

if mode == "sleep":
    time.sleep(30)
if mode == "empty":
    sys.exit(0)
if mode == "ansi":
    sys.stdout.buffer.write(b"\x1b[31m")

payload = {
    "status": 3 if mode == "schema_invalid" else "ok",
    "value": value + "；正文自报模型=totally-different-model",
}

if output_format == "json":
    if mode == "invalid_json":
        sys.stdout.write("not-json")
        sys.exit(0)
    result = {
        "type": "result",
        "subtype": "error" if mode == "result_error" else "success",
        "structured_output": payload,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    if mode != "no_model":
        result["model"] = "codebuddy-relabeled-model" if mode == "label_mismatch" else requested_model
    emit(result)
    sys.exit(0)

init = {"type": "system", "subtype": "init", "session_id": "offline-fixture"}
if mode != "no_model":
    init["model"] = "codebuddy-relabeled-model" if mode == "label_mismatch" else requested_model
emit(init)
emit({"type": "user", "message": {"role": "user", "content": "fixture-input"}})

if mode == "tool":
    emit({"type": "tool_use", "name": "run_shell_command", "input": {}})
elif mode == "unknown_event":
    emit({"type": "future_event", "value": 1})
else:
    content = "not-json" if mode == "invalid_json" else json.dumps(payload, ensure_ascii=False)
    message_content = (
        [{"type": "text", "text": content}]
        if mode == "content_blocks"
        else content
    )
    emit({"type": "assistant", "message": {"role": "assistant", "content": message_content}})

if mode == "partial":
    sys.exit(0)
if mode == "nonzero":
    sys.exit(8)

usage = {"input_tokens": 13, "output_tokens": 7, "total_tokens": 20}
if mode == "token_conflict":
    usage["total_tokens"] = 99
terminal = {"type": "result", "subtype": "error" if mode == "result_error" else "success", "usage": usage}
emit(terminal)
if mode == "duplicate_terminal":
    emit(terminal)
