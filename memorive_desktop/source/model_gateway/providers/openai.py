"""OpenAI adapter(chat/completions,Bearer)。

Verification 第 3 步:判断类 GPT 异源校核(verify_judgment 槽)用。OpenAI 官方 chat/completions,
与 deepseek adapter 同款(DeepSeek 即"OpenAI 兼容")。⚠ 境外 API:调用前须 TUN(出口非大陆);
区域预检门(region_preflight)在测前 D0/D4 拦大陆出口,live 调用由 TUN 保证。
纪律:key 只按 api_key_env 名从环境变量取、绝不硬编码 / 打印。
"""
from __future__ import annotations

import json
import os
import urllib.request

from . import register
from .base import ProviderAdapter
from ..errors import ApiKeyMissing


@register("openai")
class OpenAIAdapter(ProviderAdapter):
    def invoke(self, slot, payload: dict) -> dict:
        key = os.environ.get(slot.api_key_env or "")       # 按配置"环境变量名"取 key
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
