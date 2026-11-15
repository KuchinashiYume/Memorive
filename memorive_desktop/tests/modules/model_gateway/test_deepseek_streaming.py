"""DeepSeek long-response transport and thinking parameter contract."""
from __future__ import annotations

from http.client import IncompleteRead
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from model_gateway.providers.deepseek import DeepSeekAdapter, _read_sse_response


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _StreamResponse:
    headers = _Headers({"x-request-id": "req_deepseek_stream"})

    def __init__(self, events: list[dict], *, done: bool = True) -> None:
        self.lines: list[bytes] = []
        for event in events:
            self.lines.append(("data: " + json.dumps(event) + "\n").encode("utf-8"))
        if done:
            self.lines.append(b"data: [DONE]\n")

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _events() -> list[dict]:
    return [
        {
            "id": "chatcmpl-1",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "reasoning_content": "private reasoning"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "model": "deepseek-v4-flash",
            "choices": [
                {"index": 0, "delta": {"content": "{\"ok\":"}, "finish_reason": None}
            ],
        },
        {
            "id": "chatcmpl-1",
            "model": "deepseek-v4-flash",
            "choices": [
                {"index": 0, "delta": {"content": "true}"}, "finish_reason": "stop"}
            ],
        },
        {
            "id": "chatcmpl-1",
            "model": "deepseek-v4-flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 43000,
                "completion_tokens": 2500,
                "total_tokens": 45500,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 43000,
                "completion_tokens_details": {"reasoning_tokens": 2100},
            },
        },
    ]


class DeepSeekStreamingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SimpleNamespace(
            task_type="distill",
            model_id="deepseek-v4-flash",
            endpoint="https://api.deepseek.com/chat/completions",
            api_key_env="DEEPSEEK_API_KEY",
            max_tokens=None,
        )

    def _invoke(self, response, payload_overrides=None):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return response

        payload = {
            "messages": [{"role": "user", "content": "Return JSON."}],
            "max_tokens": 393216,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "response_format": {"type": "json_object"},
            "stream": True,
            "timeout": 41,
        }
        payload.update(payload_overrides or {})
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret"}, clear=False),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            result = DeepSeekAdapter().invoke(self.slot, payload)
        return result, captured

    def test_stream_request_preserves_parameters_and_assembles_final_content(self) -> None:
        result, captured = self._invoke(_StreamResponse(_events()))

        self.assertEqual(captured["timeout"], 41)
        self.assertEqual(captured["body"]["max_tokens"], 393216)
        self.assertEqual(captured["body"]["thinking"], {"type": "enabled"})
        self.assertEqual(captured["body"]["reasoning_effort"], "high")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertTrue(captured["body"]["stream"])
        self.assertEqual(captured["body"]["stream_options"], {"include_usage": True})
        self.assertEqual(result["text"], '{"ok":true}')
        self.assertEqual(result["model"], "deepseek-v4-flash")
        self.assertEqual(result["stop_reason"], "stop")
        self.assertEqual(result["request_id"], "req_deepseek_stream")
        self.assertEqual(result["response_id"], "chatcmpl-1")
        self.assertEqual(result["usage"]["completion_tokens_details"]["reasoning_tokens"], 2100)
        self.assertTrue(result["transport"]["stream"])
        self.assertTrue(result["transport"]["complete"])
        self.assertTrue(result["transport"]["reasoning_content_observed"])
        self.assertNotIn("private reasoning", json.dumps(result["raw"]))

    def test_stream_without_done_marker_is_transient_incomplete_read(self) -> None:
        with self.assertRaises(IncompleteRead):
            self._invoke(_StreamResponse(_events(), done=False))

    def test_stream_wall_clock_deadline_is_enforced_even_when_chunks_continue(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "wall-clock deadline"):
            _read_sse_response(
                _StreamResponse(_events()), deadline_monotonic=-1
            )

    def test_invalid_thinking_and_reasoning_effort_are_rejected_before_network(self) -> None:
        cases = (
            ({"thinking": {"type": "adaptive"}}, "thinking"),
            ({"reasoning_effort": "xhigh"}, "reasoning_effort"),
            ({"stream": "yes"}, "stream"),
        )
        for override, pattern in cases:
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, pattern):
                self._invoke(_StreamResponse(_events()), override)


if __name__ == "__main__":
    unittest.main(verbosity=2)
