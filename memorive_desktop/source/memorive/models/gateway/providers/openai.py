"""The OpenAI adapter (chat/completions, Bearer), used for the second model in card review
judgments (the verify_judgment slot). The same form as the DeepSeek adapter, which is OpenAI
compatible. It is an overseas API: region_preflight stops calls from a mainland China exit.
The key is read only from the environment variable named by api_key_env, never written or printed.
"""
from __future__ import annotations

import json
import os
import urllib.request

from memorive.models.gateway.providers import register
from memorive.models.gateway.providers.base import ProviderAdapter
from memorive.models.gateway.errors import ApiKeyMissing


@register("openai")
class OpenAIAdapter(ProviderAdapter):
    def invoke(self, slot, payload: dict) -> dict:
        key = os.environ.get(slot.api_key_env or "")       # the variable named in the configuration
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        body = json.dumps(
            {
                "model": slot.model_id,
                "messages": payload["messages"],
                "max_tokens": payload.get("max_tokens", 16),
            }
        ).encode("utf-8")

        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")   # OpenAI:Bearer
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=payload.get("timeout", 60)) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        return {
            "task_type": slot.task_type,
            "provider": "openai",
            "model": data.get("model"),
            "text": data["choices"][0]["message"]["content"],
            "usage": data.get("usage"),
            "raw": data,
        }
