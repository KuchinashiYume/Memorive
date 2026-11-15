from __future__ import annotations

import json
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from model_gateway.providers import anthropic


class _Headers:
    def get(self, key, default=None):
        return "req_test" if key.lower() == "request-id" else default


class _FakeResponse:
    headers = _Headers()

    def __init__(self) -> None:
        self._body = json.dumps(
            {
                "id": "msg_test",
                "model": "claude-sonnet-5",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "{}"}],
                "usage": {
                    "input_tokens": 23,
                    "output_tokens": 7,
                    "cache_creation_input_tokens": 1000,
                    "cache_read_input_tokens": 500,
                    "output_tokens_details": {"thinking_tokens": 4},
                },
            }
        ).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


class AnthropicCacheContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SimpleNamespace(
            task_type="verify",
            model_id="claude-sonnet-5",
            endpoint="https://example.invalid/v1/messages",
            api_key_env="MEMORIVE_ANTHROPIC_KEY",
            thinking_type="adaptive",
            effort="low",
            max_tokens=16384,
        )

    def _invoke(self, payload: dict):
        captured = {}

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

    def test_system_blocks_and_explicit_cache_breakpoint_are_preserved(self) -> None:
        system = [
            {"type": "text", "text": "stable contract", "cache_control": {"type": "ephemeral", "ttl": "5m"}},
            {"type": "text", "text": "role schema"},
        ]
        _result, captured = self._invoke({"system": system, "messages": [{"role": "user", "content": "dynamic"}], "timeout": 2})
        self.assertEqual(captured["body"]["system"], system)
        self.assertEqual(captured["body"]["thinking"], {"type": "adaptive"})
        self.assertEqual(captured["body"]["output_config"], {"effort": "low"})

    def test_top_level_automatic_cache_control_is_preserved(self) -> None:
        _result, captured = self._invoke(
            {
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
                "messages": [{"role": "user", "content": "dynamic"}],
            }
        )
        self.assertEqual(captured["body"]["cache_control"], {"type": "ephemeral", "ttl": "1h"})

    def test_invalid_cache_ttl_is_rejected_before_request(self) -> None:
        with self.assertRaises(ValueError):
            self._invoke({"cache_control": {"type": "ephemeral", "ttl": "2h"}, "messages": [{"role": "user", "content": "x"}]})

    def test_cache_usage_is_normalized_without_changing_total_tokens(self) -> None:
        result, _captured = self._invoke({"messages": [{"role": "user", "content": "x"}]})
        usage = result["usage"]
        self.assertEqual(usage["prompt_tokens"], 23)
        self.assertEqual(usage["completion_tokens"], 7)
        self.assertEqual(usage["total_tokens"], 30)
        self.assertEqual(usage["cache_creation_input_tokens"], 1000)
        self.assertEqual(usage["cache_read_input_tokens"], 500)
        self.assertEqual(usage["completion_tokens_details"]["reasoning_tokens"], 4)

    def test_request_id_and_stop_reason_are_retained(self) -> None:
        result, _captured = self._invoke({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(result["request_id"], "req_test")
        self.assertEqual(result["response_id"], "msg_test")
        self.assertEqual(result["stop_reason"], "end_turn")

    def test_explicit_lower_max_tokens_overrides_slot_default(self) -> None:
        result, captured = self._invoke({"messages": [{"role": "user", "content": "x"}], "max_tokens": 2048})
        self.assertEqual(captured["body"]["max_tokens"], 2048)
        self.assertEqual(result["max_tokens"], 2048)

    def test_structured_output_schema_is_sanitized_without_mutating_caller(self) -> None:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test",
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                }
            },
        }
        original = json.loads(json.dumps(schema))
        _result, captured = self._invoke(
            {
                "messages": [{"role": "user", "content": "x"}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            }
        )
        sent = captured["body"]["output_config"]["format"]["schema"]
        self.assertNotIn("$schema", sent)
        self.assertNotIn("$id", sent)
        self.assertNotIn("minItems", sent["properties"]["items"])
        self.assertNotIn("minLength", sent["properties"]["items"]["items"])
        self.assertEqual(schema, original)

    def test_structured_output_requires_schema(self) -> None:
        with self.assertRaises(ValueError):
            self._invoke(
                {
                    "messages": [{"role": "user", "content": "x"}],
                    "output_config": {"format": {"type": "json_schema"}},
                }
            )

    def test_http_400_preserves_sanitized_provider_message(self) -> None:
        body = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Schema is too complex for compilation.",
                },
                "request_id": "req_schema_test",
            }
        ).encode("utf-8")
        error = HTTPError(
            self.slot.endpoint,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(body),
        )
        with (
            patch.dict(os.environ, {"MEMORIVE_ANTHROPIC_KEY": "offline-test-only"}),
            patch.object(anthropic.urllib.request, "urlopen", side_effect=error),
            self.assertRaisesRegex(
                anthropic.AnthropicHTTPError,
                "Schema is too complex for compilation",
            ) as captured,
        ):
            anthropic.AnthropicAdapter().invoke(
                self.slot,
                {"messages": [{"role": "user", "content": "x"}]},
            )
        self.assertIn("req_schema_test", str(captured.exception))
        self.assertNotIn("offline-test-only", str(captured.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
