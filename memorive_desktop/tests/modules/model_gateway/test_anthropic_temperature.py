"""Anthropic Messages API temperature passthrough contract (offline)."""
from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from memorive.models.gateway.providers import anthropic


class _FakeResponse:
    def __init__(self) -> None:
        self._body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": "{}"}],
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


class AnthropicTemperatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SimpleNamespace(
            task_type="verify",
            model_id="claude-haiku-4-5-20251001",
            endpoint="https://example.invalid/v1/messages",
            api_key_env="MEMORIVE_ANTHROPIC_KEY",
            thinking_type=None,
            effort=None,
            max_tokens=None,
        )
        self.payload = {
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 32,
            "timeout": 2,
        }

    def _invoke(self, **overrides):
        captured = {}
        payload = dict(self.payload)
        payload.update(overrides)

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse()

        with (
            patch.dict(os.environ, {"MEMORIVE_ANTHROPIC_KEY": "offline-test-only"}),
            patch.object(anthropic.urllib.request, "urlopen", fake_urlopen),
        ):
            result = anthropic.AnthropicAdapter().invoke(self.slot, payload)
        return result, captured

    def test_explicit_zero_temperature_is_sent(self) -> None:
        _result, captured = self._invoke(temperature=0.0)
        self.assertEqual(captured["body"]["temperature"], 0.0)
        self.assertEqual(captured["timeout"], 2)

    def test_omitted_temperature_is_not_sent(self) -> None:
        _result, captured = self._invoke()
        self.assertNotIn("temperature", captured["body"])

    def test_invalid_temperature_is_rejected_before_request(self) -> None:
        for value in (True, "0", -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self._invoke(temperature=value)


if __name__ == "__main__":
    unittest.main()
