"""Offline Codex CLI fixture used only by CODEX-ADAPTER adapter tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


def option(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


mode = os.environ.get("MEMORIVE_FAKE_CODEX_MODE", "valid")
model = option("--model")
final_path = Path(option("--output-last-message"))
prompt = sys.stdin.buffer.read().decode("utf-8")

if mode == "timeout":
    time.sleep(30)
    raise SystemExit(0)

returned_model = "gpt-5.6-terra" if mode == "mismatch-model" else model
thread = {"type": "thread.started", "thread_id": "thread_fixture_001"}
if mode != "missing-model":
    thread["model"] = returned_model

events = [
    thread,
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"type": "agent_message", "response_id": "resp_fixture_001"},
    },
]

if mode == "tool-event":
    events.insert(
        2,
        {
            "type": "item.completed",
            "item": {"type": "view_image", "path": "synthetic.png"},
        },
    )

if mode == "partial-jsonl":
    sys.stdout.buffer.write(b'{"type":"thread.started"}\n{"type":')
    raise SystemExit(0)
if mode == "ansi":
    sys.stdout.buffer.write(b"\x1b[31m" + json.dumps(thread).encode("utf-8") + b"\x1b[0m\n")
    raise SystemExit(0)
if mode == "no-terminal":
    for event in events:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    raise SystemExit(0)
if mode == "nonzero":
    events.append({"type": "turn.failed", "error": {"code": "fixture_failure"}})
    for event in events:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    raise SystemExit(3)
if mode == "error-then-turn-failed":
    events.extend(
        [
            {"type": "error", "message": "fixture provider rejection"},
            {"type": "turn.failed", "error": {"message": "fixture provider rejection"}},
        ]
    )
    for event in events:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    raise SystemExit(1)

payload = {"status": "ok", "message": f"收到：{prompt}"}
if mode == "invalid-schema":
    payload = {"status": "wrong", "message": 7}
final_path.parent.mkdir(parents=True, exist_ok=True)
final_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

usage = {"input_tokens": 100, "output_tokens": 40, "reasoning_output_tokens": 10}
if mode == "missing-usage":
    usage = None
terminal = {"type": "turn.completed"}
if usage is not None:
    terminal["usage"] = usage
events.append(terminal)
for event in events:
    print(json.dumps(event, ensure_ascii=False), flush=True)
