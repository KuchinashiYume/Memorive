"""DeepSeek adapter (OpenAI-compatible chat/completions).

Supports provider-neutral metadata preflight plus long-running streamed chat
requests.  Thinking content is observed for accounting but never retained in
the normalized raw response.
"""
from __future__ import annotations

from copy import deepcopy
from http.client import IncompleteRead
import json
import math
import os
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from . import register
from .base import ProviderAdapter
from ..capabilities import registry_capabilities
from ..errors import ApiKeyMissing


_THINKING_TYPES = frozenset({"enabled", "disabled"})
_REASONING_EFFORTS = frozenset(registry_capabilities("deepseek", "deepseek-v4-pro")["capabilities"]["reasoning_efforts"])
_RESPONSE_FORMATS = frozenset({"text", "json_object"})


def _positive_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"DeepSeek {label} must be a positive integer")
    return value


def _max_tokens(slot, payload: dict) -> int:
    explicit = payload.get("max_tokens")
    configured = getattr(slot, "max_tokens", None)
    value = explicit if explicit is not None else configured if configured is not None else 16
    return _positive_int(value, "max_tokens")


def _thinking(payload: dict) -> dict | None:
    value = payload.get("thinking")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"type"}:
        raise ValueError("DeepSeek thinking must be {'type': 'enabled'|'disabled'}")
    if value.get("type") not in _THINKING_TYPES:
        raise ValueError("DeepSeek thinking.type must be enabled or disabled")
    return dict(value)


def _reasoning_effort(payload: dict) -> str | None:
    value = payload.get("reasoning_effort")
    if value is not None and value not in _REASONING_EFFORTS:
        raise ValueError("DeepSeek reasoning_effort is not declared in the capability registry")
    return value


def _response_format(payload: dict) -> dict | None:
    value = payload.get("response_format")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"type"}:
        raise ValueError("DeepSeek response_format must contain only type")
    if value.get("type") not in _RESPONSE_FORMATS:
        raise ValueError("DeepSeek response_format.type must be text or json_object")
    return dict(value)


def _stream_enabled(payload: dict) -> bool:
    value = payload.get("stream", False)
    if not isinstance(value, bool):
        raise ValueError("DeepSeek stream must be bool")
    return value


def _temperature(payload: dict) -> float | None:
    value = payload.get("temperature")
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 2
    ):
        raise ValueError("DeepSeek temperature must be a finite number from 0 to 2")
    return float(value)


def _without_reasoning_content(value):
    """Recursively remove private chain-of-thought fields before evidence use."""
    if isinstance(value, list):
        return [_without_reasoning_content(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _without_reasoning_content(item)
        for key, item in value.items()
        if key != "reasoning_content"
    }


def _read_sse_response(
    resp, *, deadline_monotonic: float | None = None
) -> tuple[dict, dict]:
    content: list[str] = []
    usage: dict = {}
    response_id = None
    model = None
    finish_reason = None
    done = False
    event_count = 0
    reasoning_content_observed = False

    for raw_line in resp:
        if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
            raise TimeoutError("DeepSeek stream wall-clock deadline exceeded")
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            break
        if not payload:
            continue
        chunk = json.loads(payload)
        if not isinstance(chunk, dict):
            continue
        event_count += 1
        chunk_id = chunk.get("id")
        chunk_model = chunk.get("model")
        if response_id is not None and chunk_id not in (None, response_id):
            raise ValueError("DeepSeek stream response id changed between chunks")
        if model is not None and chunk_model not in (None, model):
            raise ValueError("DeepSeek stream model changed between chunks")
        response_id = response_id or chunk_id
        model = model or chunk_model
        if isinstance(chunk.get("usage"), dict):
            usage = dict(chunk["usage"])
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if delta.get("reasoning_content"):
                reasoning_content_observed = True
            if delta.get("content") is not None:
                content.append(str(delta.get("content")))
            if choice.get("finish_reason") is not None:
                finish_reason = choice.get("finish_reason")

    if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
        raise TimeoutError("DeepSeek stream wall-clock deadline exceeded")

    text = "".join(content)
    if not done or finish_reason is None:
        raise IncompleteRead(text.encode("utf-8"), None)
    data = {
        "id": response_id,
        "model": model,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    return data, {
        "stream": True,
        "complete": True,
        "event_count": event_count,
        "reasoning_content_observed": reasoning_content_observed,
        "reasoning_content_recorded": False,
    }


@register("deepseek")
class DeepSeekAdapter(ProviderAdapter):
    def probe_model_capabilities(self, slot, *, timeout: int = 30) -> dict:
        key = os.environ.get(slot.api_key_env or "")
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        parsed = urlsplit(slot.endpoint)
        endpoint = urlunsplit((parsed.scheme, parsed.netloc, "/models", "", ""))
        req = urllib.request.Request(endpoint, method="GET")
        req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            headers = getattr(resp, "headers", None)
            request_id = None
            if headers is not None:
                request_id = headers.get("x-request-id") or headers.get("request-id")

        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise ValueError("DeepSeek /models response missing data list")
        match = next(
            (item for item in models if isinstance(item, dict) and item.get("id") == slot.model_id),
            None,
        )
        if match is None:
            raise ValueError(f"DeepSeek live API did not return requested model {slot.model_id}")
        frozen = registry_capabilities("deepseek", slot.model_id)
        return {
            "provider": "deepseek",
            "requested_model_id": slot.model_id,
            "resolved_model_id": match.get("id"),
            "available": True,
            "max_output_tokens": frozen["max_output_tokens"],
            "max_input_tokens": frozen["max_input_tokens"],
            "capability_source": "provider_api_plus_frozen_registry",
            "registry_source_url": frozen.get("source_url"),
            "registry_verified_at": frozen.get("verified_at"),
            "capabilities": frozen["capabilities"],
            "request_id": request_id,
        }

    def invoke(self, slot, payload: dict) -> dict:
        key = os.environ.get(slot.api_key_env or "")       # 按配置里的"环境变量名"取 key
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        thinking = _thinking(payload)
        reasoning_effort = _reasoning_effort(payload)
        response_format = _response_format(payload)
        stream = _stream_enabled(payload)
        temperature = _temperature(payload)
        max_tokens = _max_tokens(slot, payload)
        body_data = {
            "model": slot.model_id,
            "messages": payload["messages"],
            "max_tokens": max_tokens,
        }
        if thinking is not None:
            body_data["thinking"] = thinking
        if reasoning_effort is not None:
            body_data["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            body_data["response_format"] = response_format
        if temperature is not None:
            body_data["temperature"] = temperature
        if stream:
            body_data["stream"] = True
            body_data["stream_options"] = {"include_usage": True}
        body = json.dumps(body_data).encode("utf-8")

        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")   # DeepSeek = OpenAI 兼容:Bearer
        req.add_header("Content-Type", "application/json")

        wall_clock_seconds = float(
            payload.get("wall_clock_deadline_seconds", payload.get("timeout", 180))
        )
        idle_timeout_seconds = min(
            wall_clock_seconds,
            float(payload.get("stream_idle_timeout_seconds", 120)),
        )
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=idle_timeout_seconds) as resp:
            if stream:
                data, transport = _read_sse_response(
                    resp, deadline_monotonic=started + wall_clock_seconds
                )
                transport["wall_clock_deadline_seconds"] = wall_clock_seconds
                transport["idle_timeout_seconds"] = idle_timeout_seconds
            else:
                data = json.loads(resp.read().decode("utf-8"))
                transport = {
                    "stream": False,
                    "complete": True,
                    "reasoning_content_observed": any(
                        isinstance(choice, dict)
                        and isinstance(choice.get("message"), dict)
                        and bool(choice["message"].get("reasoning_content"))
                        for choice in (data.get("choices") or [])
                    ),
                    "reasoning_content_recorded": False,
                }
            headers = getattr(resp, "headers", None)
            request_id = None
            if headers is not None:
                request_id = headers.get("x-request-id") or headers.get("request-id")

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("DeepSeek response missing choices[0]")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

        return {
            "task_type": slot.task_type,
            "provider": "deepseek",
            "model": data.get("model"),
            "text": message.get("content") or "",
            "usage": usage,
            "thinking": thinking,
            "reasoning_effort": reasoning_effort,
            "max_tokens": max_tokens,
            "response_format": response_format,
            "request_id": request_id,
            "response_id": data.get("id"),
            "stop_reason": choice.get("finish_reason"),
            "transport": transport,
            "raw": _without_reasoning_content(deepcopy(data)),
        }
