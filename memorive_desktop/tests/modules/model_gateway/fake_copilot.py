"""Offline GitHub Copilot CLI JSONL fixture. No network or GitHub access."""

from __future__ import annotations

import json
import sys
import time


def option(name: str, default: str | None = None) -> str | None:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return default


def option_equals(name: str, default: str | None = None) -> str | None:
    prefix = name + "="
    for item in sys.argv:
        if item.startswith(prefix):
            return item[len(prefix):]
    return default


def emit(value: dict) -> None:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


try:
    request = json.loads((option("-p", "{}") or "{}").encode("utf-8").decode("utf-8"))
except Exception:
    request = {"mode": "invalid_prompt", "value": ""}

mode = request.get("mode", "valid")
value = request.get("value", "离线 Ω")
model = option_equals("--model", "unknown-model")

if mode == "sleep":
    time.sleep(30)
if mode == "empty":
    sys.exit(0)
if mode == "ansi":
    sys.stdout.buffer.write(b"\x1b[31m")

start = {"type": "session.start", "session_id": "offline-fixture"}
if mode != "no_model":
    start["model"] = "copilot-relabeled-model" if mode == "label_mismatch" else model
emit(start)

if mode == "mixed_diagnostic":
    emit({"type": "diagnostic", "level": "warning", "code": "OFFLINE_FIXTURE_WARNING"})
if mode == "invalid_json_line":
    sys.stdout.write("not-json\n")
    sys.stdout.flush()

special_events = {
    "auth_pending": ("auth.error", "AUTH_PENDING"),
    "auth_denied": ("auth.error", "AUTH_DENIED"),
    "entitlement_absent": ("entitlement.error", "ENTITLEMENT_ABSENT"),
    "org_policy_denied": ("organization_policy.error", "ORGANIZATION_POLICY_DENIED"),
    "model_unavailable": ("model.error", "MODEL_UNAVAILABLE"),
    "permission_denied": ("permission.error", "TOOL_PERMISSION_DENIED"),
    "rate_limit": ("rate_limit.error", "RATE_LIMIT"),
    "quota": ("quota.error", "QUOTA_EXCEEDED"),
    "provider_error": ("provider.error", "PROVIDER_ERROR"),
}

if mode == "tool":
    emit({"type": "tool.request", "tool": "shell", "arguments": {}})
elif mode == "network":
    emit({"type": "network.request", "url_kind": "external"})
elif mode == "github_mutation":
    emit({"type": "github.mutation", "operation": "create_issue"})
elif mode == "unknown_event":
    emit({"type": "future.event", "value": 1})
elif mode in special_events:
    kind, code = special_events[mode]
    emit({"type": kind, "code": code})
else:
    if mode == "invalid_payload_json":
        content = "not-json"
    else:
        payload = {
            "status": 3 if mode == "schema_invalid" else "ok",
            "value": value + "；正文自报模型=totally-different-model",
        }
        content = json.dumps(payload, ensure_ascii=False)
    emit({"type": "assistant.message", "data": {"content": content}})

if mode == "partial":
    sys.exit(0)
if mode == "nonzero":
    sys.exit(8)

usage = {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16}
if mode == "token_conflict":
    usage["total_tokens"] = 99
status = "error" if mode in special_events or mode == "result_error" else "success"
terminal = {"type": "session.end", "status": status, "usage": usage}
emit(terminal)
if mode == "duplicate_terminal":
    emit(terminal)
