"""A structured chat request answered by a command-line tool (LiveModelValidationRunner._send_cli).

The tool is a stand-in process that returns fixed output: Codex's JSONL events, or the stream output of
Claude Code. These tests check what the route makes of each run: the answer and usage of a good reply,
and the failed result (reason and counts) when the run ends badly or the reply names another model.
Nothing leaves the computer.
"""
from __future__ import annotations

import json

import pytest

from memorive.models.validation.runner import LiveModelValidationRunner
from memorive.models.validation.transport import ProcessResult

SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"],
          "additionalProperties": False}
ANSWER = json.dumps({"value": "fine"})


def codex_events(text, *, finish="turn.completed", model=None):
    last = {"type": finish, "usage": {"input_tokens": 120, "cached_input_tokens": 0, "output_tokens": 30}}
    if model:
        last["model"] = model
    events = [{"type": "thread.started", "thread_id": "11111111-1111-4111-8111-111111111111"},
              {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}}, last]
    return "\n".join(json.dumps(e) for e in events).encode()


def claude_stream(text, *, model="claude-haiku-5-5"):
    lines = [{"type": "system", "subtype": "init", "model": model},
             {"type": "result", "subtype": "success", "result": text, "usage": {"input_tokens": 100, "output_tokens": 20}}]
    return "\n".join(json.dumps(e) for e in lines).encode()


class Tool:
    def __init__(self, result):
        self.result, self.calls = result, []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def ask(tmp_path, adapter, model, result):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"cli-fixture")
    tool = Tool(result)
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _ref: b"unused",
        preferences_loader=lambda: {"proxy_mode": "NONE", "proxy_address": "", "request_timeout_seconds": 30},
        scratch_root=tmp_path / "chat", process_transport=tool, executable_resolver=lambda _value: str(executable))
    reply = runner.execute_structured_chat(
        profile_kind="CLI", service={"adapter_id": adapter, "executable": adapter},
        model={"model_name": model, "max_output_tokens_mode": "PROVIDER_DEFAULT"},
        prompt="Return one object with a value.", response_schema=SCHEMA, purpose="research_chat")
    return reply, tool


def counts(reply):
    names = ("external_network_calls", "provider_calls", "external_model_calls", "external_process_launches")
    return tuple(reply[name] for name in names)


@pytest.mark.parametrize("adapter, model, stdout, route", [
    ("codex_cli", "gpt-5.6-sol", codex_events(ANSWER), "CODEX_CLI_SUBSCRIPTION"),
    ("claude_code", "claude-haiku-5-5", claude_stream(ANSWER), "CLAUDE_CODE_SUBSCRIPTION"),
])
def test_a_tool_that_answers_gives_the_answer_and_its_usage(tmp_path, adapter, model, stdout, route):
    reply, tool = ask(tmp_path, adapter, model, ProcessResult(True, 0, stdout, b"", stdout_total_bytes=len(stdout)))
    assert (reply["status"], reply["response"]) == ("PASS", {"value": "fine"})
    receipt = reply["execution_receipt"]
    assert (receipt["route"], receipt["returned_model"]) == (route, model)
    assert receipt["token_usage"]["prompt_tokens"] > 0 and receipt["token_usage"]["completion_tokens"] > 0
    assert len(tool.calls) == 1 and model in tool.calls[0]["argv"]


@pytest.mark.parametrize("result, reason", [
    (ProcessResult(True, None, b"", b"", timed_out=True), "CLI_STRUCTURED_CHAT_TIMEOUT"),
    (ProcessResult(True, 0, b"{", b"", output_truncated=True), "CLI_OUTPUT_LIMIT_EXCEEDED"),
    (ProcessResult(True, 0, codex_events(ANSWER, finish="turn.failed"), b""), "CODEX_JSONL_FINISH_NOT_SUCCESS"),
    (ProcessResult(True, 0, codex_events(ANSWER, model="gpt-4.1"), b""), "CLI_RETURNED_MODEL_MISMATCH"),
])
def test_a_codex_run_that_ends_badly_fails_after_one_launch(tmp_path, result, reason):
    reply, _tool = ask(tmp_path, "codex_cli", "gpt-5.6-sol", result)
    assert (reply["status"], reply["reason"]) == ("FAILED", reason)
    assert counts(reply) == (0, 0, 1, 1)


def test_a_tool_that_does_not_start_counts_no_model_call(tmp_path):
    reply, _tool = ask(tmp_path, "claude_code", "claude-haiku-5-5", ProcessResult(False, None, b"", b""))
    assert (reply["status"], reply["reason"]) == ("FAILED", "CLI_PROCESS_START_FAILED")
    assert counts(reply) == (0, 0, 0, 0)


def test_a_reply_that_names_another_model_fails(tmp_path):
    stdout = claude_stream(ANSWER, model="claude-opus-5")
    reply, _tool = ask(tmp_path, "claude_code", "claude-haiku-5-5", ProcessResult(True, 0, stdout, b""))
    assert (reply["reason"], reply["execution_receipt"]["returned_model"]) == ("CLI_RETURNED_MODEL_MISMATCH", "claude-opus-5")
