"""Optional Anthropic SSE transport for long xhigh analysis calls."""
from __future__ import annotations

import http.client
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from memorive.models.gateway.providers import anthropic


class _Headers:
    def get(self, key, default=None):
        return "req_stream" if key.lower() == "request-id" else default


class _FakeStreamResponse:
    headers = _Headers()

    def __init__(self, events: list[dict]) -> None:
        self.events = events

    def __iter__(self):
        for event in self.events:
            yield f"event: {event['type']}\n".encode("utf-8")
            yield ("data: " + json.dumps(event) + "\n").encode("utf-8")
            yield b"\n"

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


def _events(*, complete: bool = True) -> list[dict]:
    items = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_stream",
                "model": "claude-sonnet-5",
                "content": [],
                "stop_reason": None,
                "usage": {
                    "input_tokens": 101,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 7,
                    "cache_read_input_tokens": 3,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "private reasoning"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "{\"answer_complete\":"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "true}"},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {
                "output_tokens": 55,
                "output_tokens_details": {"thinking_tokens": 34},
            },
        },
    ]
    if complete:
        items.append({"type": "message_stop"})
    return items


class AnthropicStreamingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SimpleNamespace(
            task_type="analysis",
            model_id="claude-sonnet-5",
            endpoint="https://example.invalid/v1/messages",
            api_key_env="MEMORIVE_ANTHROPIC_KEY",
            thinking_type="adaptive",
            effort="xhigh",
            max_tokens=65536,
        )

    def _invoke(self, events: list[dict], payload: dict | None = None):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeStreamResponse(events)

        request = {
            "messages": [{"role": "user", "content": "long analysis"}],
            "stream": True,
            "timeout": 600,
            **(payload or {}),
        }
        with (
            patch.dict(os.environ, {"MEMORIVE_ANTHROPIC_KEY": "offline-test-only"}),
            patch.object(anthropic.urllib.request, "urlopen", fake_urlopen),
        ):
            result = anthropic.AnthropicAdapter().invoke(self.slot, request)
        return result, captured

    def test_stream_request_assembles_complete_text_usage_and_ids(self) -> None:
        result, captured = self._invoke(
            _events(),
            {
                "wall_clock_deadline_seconds": 600,
                "stream_idle_timeout_seconds": 90,
            },
        )

        self.assertTrue(captured["body"]["stream"])
        self.assertEqual(captured["timeout"], 90)
        self.assertEqual(result["text"], '{"answer_complete":true}')
        self.assertEqual(result["request_id"], "req_stream")
        self.assertEqual(result["response_id"], "msg_stream")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["usage"]["prompt_tokens"], 101)
        self.assertEqual(result["usage"]["completion_tokens"], 55)
        self.assertEqual(result["usage"]["completion_tokens_details"]["reasoning_tokens"], 34)
        self.assertTrue(result["transport"]["stream"])
        self.assertTrue(result["transport"]["complete"])
        self.assertEqual(result["transport"]["wall_clock_deadline_seconds"], 600)
        self.assertEqual(result["transport"]["idle_timeout_seconds"], 90)

    def test_stream_wall_clock_deadline_is_enforced(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "WALL_CLOCK_DEADLINE"):
            anthropic._read_sse_response(
                _FakeStreamResponse(_events()), deadline_monotonic=-1
            )

    def test_stream_without_message_stop_fails_as_transient_incomplete_read(self) -> None:
        with self.assertRaises(http.client.IncompleteRead):
            self._invoke(_events(complete=False))

    def test_message_stop_before_message_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "message_stop.*before message_start"):
            self._invoke([{"type": "message_stop"}])

    def test_delta_without_open_block_is_rejected(self) -> None:
        events = _events()
        events.pop(1)
        with self.assertRaisesRegex(ValueError, "without an open block"):
            self._invoke(events)

    def test_event_after_message_stop_is_rejected(self) -> None:
        events = _events() + [{"type": "message_delta", "delta": {}}]
        with self.assertRaisesRegex(ValueError, "after message_stop"):
            self._invoke(events)

    def test_unclosed_content_block_is_transient_incomplete_read(self) -> None:
        events = [event for event in _events() if event != {"type": "content_block_stop", "index": 1}]
        with self.assertRaises(http.client.IncompleteRead):
            self._invoke(events)

    def test_stream_flag_must_be_boolean(self) -> None:
        with self.assertRaisesRegex(ValueError, "stream"):
            self._invoke(_events(), {"stream": "yes"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
