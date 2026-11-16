"""Provider-neutral model capability preflight and Anthropic implementation."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from memorive.models.gateway.providers.anthropic import AnthropicAdapter
from memorive.models.gateway.providers.deepseek import DeepSeekAdapter
from memorive.models.gateway.router import Router


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _Response:
    headers = _Headers({"request-id": "req_capability"})

    def __init__(self, value: dict) -> None:
        self._body = json.dumps(value).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ProviderCapabilityPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SimpleNamespace(
            task_type="verify",
            enabled=True,
            provider="anthropic",
            model_id="claude-sonnet-5",
            endpoint="https://api.anthropic.com/v1/messages",
            api_key_env="MEMORIVE_ANTHROPIC_KEY",
        )

    def test_anthropic_probe_normalizes_provider_model_capabilities(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["api_key"] = req.get_header("X-api-key")
            return _Response(
                {
                    "id": "claude-sonnet-5",
                    "type": "model",
                    "max_tokens": 131072,
                    "max_input_tokens": 1000000,
                    "capabilities": {
                        "effort": {"supported": True, "xhigh": {"supported": True}},
                        "thinking": {
                            "supported": True,
                            "types": {"adaptive": {"supported": True}},
                        },
                        "structured_outputs": {"supported": True},
                    },
                }
            )

        with (
            patch.dict(os.environ, {"MEMORIVE_ANTHROPIC_KEY": "secret"}, clear=False),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            result = AnthropicAdapter().probe_model_capabilities(self.slot, timeout=17)

        self.assertEqual(captured["url"], "https://api.anthropic.com/v1/models/claude-sonnet-5")
        self.assertEqual(captured["timeout"], 17)
        self.assertEqual(captured["api_key"], "secret")
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["requested_model_id"], "claude-sonnet-5")
        self.assertEqual(result["resolved_model_id"], "claude-sonnet-5")
        self.assertEqual(result["max_output_tokens"], 131072)
        self.assertEqual(result["max_input_tokens"], 1000000)
        self.assertTrue(result["capabilities"]["effort_xhigh"])
        self.assertTrue(result["capabilities"]["adaptive_thinking"])
        self.assertTrue(result["capabilities"]["structured_outputs"])
        self.assertEqual(result["request_id"], "req_capability")
        self.assertNotIn("raw", result)

    def test_anthropic_probe_rejects_missing_positive_output_ceiling(self) -> None:
        with (
            patch.dict(os.environ, {"MEMORIVE_ANTHROPIC_KEY": "secret"}, clear=False),
            patch(
                "urllib.request.urlopen",
                return_value=_Response(
                    {"id": "claude-sonnet-5", "max_tokens": 0, "capabilities": {}}
                ),
            ),
            self.assertRaisesRegex(ValueError, "max_tokens"),
        ):
            AnthropicAdapter().probe_model_capabilities(self.slot)

    def test_router_delegates_preflight_without_provider_branching(self) -> None:
        config = SimpleNamespace(slot=lambda _task_type: self.slot)
        adapter = SimpleNamespace(
            probe_model_capabilities=lambda slot, timeout: {
                "provider": slot.provider,
                "requested_model_id": slot.model_id,
                "max_output_tokens": 999,
                "timeout": timeout,
            }
        )
        with patch("memorive.models.gateway.router.providers.get_adapter", return_value=adapter) as get_adapter:
            result = Router(config).preflight_model("verify", timeout=23)

        get_adapter.assert_called_once_with("anthropic")
        self.assertEqual(result["max_output_tokens"], 999)
        self.assertEqual(result["timeout"], 23)


    def test_deepseek_probe_rejects_model_not_returned_by_live_api(self) -> None:
        slot = SimpleNamespace(
            task_type="distill",
            enabled=True,
            provider="deepseek",
            model_id="deepseek-v4-pro",
            endpoint="https://api.deepseek.com/chat/completions",
            api_key_env="DEEPSEEK_API_KEY",
        )
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret"}, clear=False),
            patch("urllib.request.urlopen", return_value=_Response({"object": "list", "data": []})),
            self.assertRaisesRegex(ValueError, "deepseek-v4-pro"),
        ):
            DeepSeekAdapter().probe_model_capabilities(slot)


if __name__ == "__main__":
    unittest.main(verbosity=2)
