"""Anthropic adapter(Messages API,x-api-key)。

⚠ Anthropic = Messages API,与 OpenAI 兼容那套不同(openai.py 只当结构参照、别照抄):
  POST https://api.anthropic.com/v1/messages
  头 x-api-key: <key> + anthropic-version: 2023-06-01(非 Bearer)
  体 {model, max_tokens(必填), messages:[{role,content}]}(M4 纯 user、无 system 顶层字段)
  回 content[](块列表)→ 取第一个 type=="text" 的 .text;usage={input_tokens,output_tokens}
⚠ 境外 API:调用前须 TUN(出口非大陆);region_preflight 在 M4 发调用前拦大陆出口。
⚠ key 只按 api_key_env 名从环境变量取(M4 用 PROS_ANTHROPIC_KEY——非 ANTHROPIC_API_KEY,
  后者被 Claude Code 用 OAuth 剥离、CC 子进程读不到);绝不硬编码 / 打印。
⚠ Opus 4.8 / Sonnet 5 的 adaptive thinking 与 effort 由 slot/payload 显式下发：
  thinking={type:"adaptive"}；effort 在 output_config.effort（不是 thinking 内）。
  输出上限涵盖 thinking + 最终文本；B 实验的 xhigh 用 analysis 槽 64k。
归一化 usage → OpenAI 风格 {prompt_tokens,completion_tokens,completion_tokens_details,total_tokens}，
其中 reasoning_tokens 承接 Anthropic output_tokens_details.thinking_tokens（output 仍为包含 thinking 的计费总量）。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from http.client import IncompleteRead
from copy import deepcopy

from . import register
from .base import ProviderAdapter
from ..errors import ApiKeyMissing

_ANTHROPIC_VERSION = "2023-06-01"
_THINKING_TYPES = frozenset({"adaptive", "enabled", "disabled"})
_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_CACHE_TTLS = frozenset({"5m", "1h"})
_UNSUPPORTED_OUTPUT_SCHEMA_KEYWORDS = frozenset(
    {
        "$id",
        "$schema",
        "format",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)


class AnthropicHTTPError(RuntimeError):
    """Sanitized provider rejection with the actionable Anthropic message."""


def _sanitized_http_error(exc: HTTPError) -> AnthropicHTTPError:
    error_type = "unknown_error"
    message = str(exc.reason or "request rejected")
    request_id = None
    try:
        raw = exc.read(65536).decode("utf-8", errors="replace")
        value = json.loads(raw)
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, dict):
                error_type = str(error.get("type") or error_type)
                message = str(error.get("message") or message)
            request_id = value.get("request_id")
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    message = " ".join(message.split())[:1200]
    suffix = f"; request_id={request_id}" if request_id else ""
    return AnthropicHTTPError(
        f"Anthropic HTTP {exc.code}; type={error_type}; message={message}{suffix}"
    )


def _anthropic_output_schema(schema: dict) -> dict:
    """Return the provider-supported schema subset without mutating local gold."""
    if not isinstance(schema, dict):
        raise ValueError("Anthropic output_config.format.schema must be an object")

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key: visit(item)
            for key, item in value.items()
            if key not in _UNSUPPORTED_OUTPUT_SCHEMA_KEYWORDS
        }

    return visit(deepcopy(schema))


def _positive_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Anthropic {label} 须为正整数")
    return value


def _thinking(slot, payload: dict) -> dict | None:
    """调用参数优先，其次读 slot；仅允许官方 thinking type，避免把 effort 错塞进 thinking。"""
    raw = payload.get("thinking")
    if raw is None:
        configured = getattr(slot, "thinking_type", None)
        raw = {"type": configured} if configured is not None else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Anthropic thinking 须为对象")
    out = dict(raw)
    thinking_type = out.get("type")
    if thinking_type not in _THINKING_TYPES:
        raise ValueError(f"Anthropic thinking.type 非法:{thinking_type!r}")
    return out


def _output_config(slot, payload: dict) -> dict | None:
    """合并 output_config；调用方显式 output_config/effort 优先于槽配置。"""
    raw = payload.get("output_config")
    if raw is None:
        out = {}
    elif isinstance(raw, dict):
        out = deepcopy(raw)
    else:
        raise ValueError("Anthropic output_config 须为对象")

    effort = out.get("effort", payload.get("effort", getattr(slot, "effort", None)))
    if effort is not None:
        if effort not in _EFFORTS:
            raise ValueError(f"Anthropic effort 非法:{effort!r}")
        out["effort"] = effort
    output_format = out.get("format")
    if output_format is not None:
        if not isinstance(output_format, dict):
            raise ValueError("Anthropic output_config.format must be an object")
        if output_format.get("type") == "json_schema":
            if "schema" not in output_format:
                raise ValueError("Anthropic json_schema format requires schema")
            output_format["schema"] = _anthropic_output_schema(output_format["schema"])
    return out or None


def _max_tokens(slot, payload: dict) -> int:
    """An explicit per-call cap may lower the slot default; missing values use the configured safe cap."""
    explicit = payload.get("max_tokens")
    configured = getattr(slot, "max_tokens", None)
    return _positive_int(explicit if explicit is not None else configured if configured is not None else 8192, "max_tokens")


def _temperature(payload: dict) -> float | None:
    """显式采样温度才透传；缺省不下发，以兼容不再支持 temperature 的新模型。"""
    raw = payload.get("temperature")
    if raw is None:
        return None
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
        or not 0 <= raw <= 1
    ):
        raise ValueError("Anthropic temperature 须为 0.0–1.0 的有限数值")
    return float(raw)


def _stream_enabled(payload: dict) -> bool:
    value = payload.get("stream", False)
    if not isinstance(value, bool):
        raise ValueError("Anthropic stream 须为 bool")
    return value


def _read_sse_response(
    resp, *, deadline_monotonic: float | None = None
) -> tuple[dict, dict]:
    """Assemble one complete Messages SSE response without retaining thinking text."""
    message: dict | None = None
    blocks: dict[int, dict] = {}
    open_blocks: set[int] = set()
    usage: dict = {}
    complete = False
    event_count = 0
    last_event_type = None

    def text_so_far() -> str:
        return "".join(
            str(block.get("text") or "")
            for _index, block in sorted(blocks.items())
            if block.get("type") == "text"
        )

    for raw_line in resp:
        if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
            raise TimeoutError("ANTHROPIC_STREAM_WALL_CLOCK_DEADLINE_EXCEEDED")
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        event = json.loads(payload)
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        event_count += 1
        last_event_type = event_type
        if complete:
            raise ValueError("Anthropic SSE event arrived after message_stop")
        if event_type == "error":
            error = event.get("error") or {}
            raise RuntimeError(f"Anthropic stream error: {error.get('type') or 'unknown'}")
        if event_type == "message_start":
            if message is not None or blocks:
                raise ValueError("Anthropic message_start duplicated or out of order")
            raw_message = event.get("message")
            if not isinstance(raw_message, dict):
                raise ValueError("Anthropic message_start.message 须为对象")
            message = deepcopy(raw_message)
            usage.update(message.get("usage") or {})
            message["content"] = []
        elif event_type == "content_block_start":
            if message is None:
                raise ValueError("Anthropic content_block_start arrived before message_start")
            index = event.get("index")
            block = event.get("content_block")
            if isinstance(index, bool) or not isinstance(index, int) or not isinstance(block, dict):
                raise ValueError("Anthropic content_block_start index/block invalid")
            if index in blocks:
                raise ValueError("Anthropic content_block_start duplicated index")
            blocks[index] = dict(block)
            open_blocks.add(index)
            if blocks[index].get("type") == "thinking":
                blocks[index]["thinking"] = ""
        elif event_type == "content_block_delta":
            if message is None:
                raise ValueError("Anthropic content_block_delta arrived before message_start")
            index = event.get("index")
            delta = event.get("delta")
            block = blocks.get(index)
            if index not in open_blocks or not isinstance(block, dict) or not isinstance(delta, dict):
                raise ValueError("Anthropic content_block_delta arrived without an open block")
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                block["text"] = str(block.get("text") or "") + str(delta.get("text") or "")
            elif delta_type == "input_json_delta":
                block["partial_json"] = str(block.get("partial_json") or "") + str(
                    delta.get("partial_json") or ""
                )
        elif event_type == "content_block_stop":
            if message is None:
                raise ValueError("Anthropic content_block_stop arrived before message_start")
            index = event.get("index")
            if index not in open_blocks:
                raise ValueError("Anthropic content_block_stop arrived without an open block")
            open_blocks.remove(index)
        elif event_type == "message_delta":
            if message is None:
                raise ValueError("Anthropic message_delta arrived before message_start")
            if open_blocks:
                raise IncompleteRead(text_so_far().encode("utf-8"), None)
            delta = event.get("delta") or {}
            if isinstance(delta, dict):
                for key in ("stop_reason", "stop_sequence"):
                    if key in delta:
                        message[key] = delta[key]
            delta_usage = event.get("usage") or {}
            if isinstance(delta_usage, dict):
                usage.update(delta_usage)
        elif event_type == "message_stop":
            if message is None:
                raise ValueError("Anthropic message_stop arrived before message_start")
            if open_blocks:
                raise IncompleteRead(text_so_far().encode("utf-8"), None)
            complete = True

    if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
        raise TimeoutError("ANTHROPIC_STREAM_WALL_CLOCK_DEADLINE_EXCEEDED")
    if message is None or not complete or open_blocks:
        raise IncompleteRead(text_so_far().encode("utf-8"), None)
    message["content"] = [block for _index, block in sorted(blocks.items())]
    message["usage"] = usage
    return message, {
        "stream": True,
        "complete": True,
        "event_count": event_count,
        "last_event_type": last_event_type,
    }


def _usage_count(value) -> int | float:
    """provider usage 缺失/畸形时如实归零，避免把布尔/NaN 送进 M11 记账契约。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return 0
    return value


def _cache_control(value, *, label: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Anthropic {label} 须为对象")
    control = dict(value)
    if control.get("type") != "ephemeral":
        raise ValueError(f"Anthropic {label}.type 仅允许 'ephemeral'")
    ttl = control.get("ttl")
    if ttl is not None and ttl not in _CACHE_TTLS:
        raise ValueError(f"Anthropic {label}.ttl 仅允许 5m 或 1h")
    if set(control) - {"type", "ttl"}:
        raise ValueError(f"Anthropic {label} 含未知字段")
    return control


def _system(payload: dict):
    value = payload.get("system")
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Anthropic system 须为字符串或内容块列表")
    breakpoints = 0
    blocks = []
    for index, block in enumerate(value):
        if not isinstance(block, dict):
            raise ValueError(f"Anthropic system[{index}] 须为对象")
        item = dict(block)
        if "cache_control" in item:
            item["cache_control"] = _cache_control(item.get("cache_control"), label=f"system[{index}].cache_control")
            breakpoints += 1
        blocks.append(item)
    if breakpoints > 4:
        raise ValueError("Anthropic 显式 cache breakpoint 最多 4 个")
    return blocks


@register("anthropic")
class AnthropicAdapter(ProviderAdapter):
    def probe_model_capabilities(self, slot, *, timeout: int = 30) -> dict:
        key = os.environ.get(slot.api_key_env or "")
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        parsed = urlsplit(slot.endpoint)
        path = parsed.path.rstrip("/")
        if path.endswith("/messages"):
            path = path[: -len("/messages")]
        endpoint = urlunsplit(
            (parsed.scheme, parsed.netloc, f"{path}/models/{quote(slot.model_id, safe='')}", "", "")
        )
        req = urllib.request.Request(endpoint, method="GET")
        req.add_header("x-api-key", key)
        req.add_header("anthropic-version", _ANTHROPIC_VERSION)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            headers = getattr(resp, "headers", None)
            request_id = headers.get("request-id") if headers is not None else None

        max_output_tokens = _positive_int(data.get("max_tokens"), "Models API max_tokens")
        max_input_tokens = data.get("max_input_tokens")
        if max_input_tokens is not None:
            max_input_tokens = _positive_int(max_input_tokens, "Models API max_input_tokens")
        caps = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
        effort = caps.get("effort") if isinstance(caps.get("effort"), dict) else {}
        xhigh = effort.get("xhigh") if isinstance(effort.get("xhigh"), dict) else {}
        thinking = caps.get("thinking") if isinstance(caps.get("thinking"), dict) else {}
        thinking_types = thinking.get("types") if isinstance(thinking.get("types"), dict) else {}
        adaptive = (
            thinking_types.get("adaptive")
            if isinstance(thinking_types.get("adaptive"), dict)
            else {}
        )
        structured = (
            caps.get("structured_outputs")
            if isinstance(caps.get("structured_outputs"), dict)
            else {}
        )
        return {
            "provider": "anthropic",
            "requested_model_id": slot.model_id,
            "resolved_model_id": data.get("id") or slot.model_id,
            "available": True,
            "max_output_tokens": max_output_tokens,
            "max_input_tokens": max_input_tokens,
            "capability_source": "provider_api",
            "capabilities": {
                "effort_xhigh": xhigh.get("supported") is True,
                "adaptive_thinking": adaptive.get("supported") is True,
                "structured_outputs": structured.get("supported") is True,
                "streaming": True,
            },
            "request_id": request_id,
        }

    def invoke(self, slot, payload: dict) -> dict:
        key = os.environ.get(slot.api_key_env or "")       # 按配置里的"环境变量名"取 key
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        thinking = _thinking(slot, payload)
        output_config = _output_config(slot, payload)
        max_tokens = _max_tokens(slot, payload)
        temperature = _temperature(payload)
        system = _system(payload)
        cache_control = _cache_control(payload.get("cache_control"), label="cache_control")
        stream = _stream_enabled(payload)
        body_data = {
            "model": slot.model_id,
            "max_tokens": max_tokens,                         # Anthropic 必填；包含 thinking + 最终文本
            "messages": payload["messages"],
        }
        if system is not None:
            body_data["system"] = system
        if cache_control is not None:
            body_data["cache_control"] = cache_control
        if thinking is not None:
            body_data["thinking"] = thinking
        if output_config is not None:
            body_data["output_config"] = output_config
        if temperature is not None:
            body_data["temperature"] = temperature
        if stream:
            body_data["stream"] = True
        body = json.dumps(body_data).encode("utf-8")

        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("x-api-key", key)                     # Anthropic: x-api-key(非 Bearer)
        req.add_header("anthropic-version", _ANTHROPIC_VERSION)
        req.add_header("Content-Type", "application/json")

        request_timeout = float(payload.get("timeout", 180))
        if request_timeout <= 0:
            raise ValueError("Anthropic timeout 须为正数")
        wall_clock_seconds = float(
            payload.get("wall_clock_deadline_seconds", request_timeout)
        )
        idle_timeout_seconds = min(
            wall_clock_seconds,
            float(payload.get("stream_idle_timeout_seconds", request_timeout)),
        )
        if wall_clock_seconds <= 0 or idle_timeout_seconds <= 0:
            raise ValueError("Anthropic stream timeout 须为正数")
        socket_timeout = idle_timeout_seconds if stream else request_timeout
        started = time.monotonic()

        try:
            response_context = urllib.request.urlopen(req, timeout=socket_timeout)
        except HTTPError as exc:
            raise _sanitized_http_error(exc) from exc

        with response_context as resp:
            if stream:
                data, transport = _read_sse_response(
                    resp, deadline_monotonic=started + wall_clock_seconds
                )
                transport.update(
                    {
                        "wall_clock_deadline_seconds": wall_clock_seconds,
                        "idle_timeout_seconds": idle_timeout_seconds,
                    }
                )
            else:
                data = json.loads(resp.read().decode("utf-8"))
                transport = {"stream": False, "complete": True}
            headers = getattr(resp, "headers", None)
            request_id = headers.get("request-id") if headers is not None else None

        # content 是块列表;取第一个 type=="text" 的 .text(M4 无 thinking/工具,content[0] 即 text)
        text = next((b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text"), "")
        u = data.get("usage") or {}
        details = u.get("output_tokens_details") or {}
        it = _usage_count(u.get("input_tokens", 0))
        ot = _usage_count(u.get("output_tokens", 0))
        cache_creation_tokens = _usage_count(u.get("cache_creation_input_tokens", 0))
        cache_read_tokens = _usage_count(u.get("cache_read_input_tokens", 0))
        thinking_tokens = _usage_count(details.get("thinking_tokens", 0) if isinstance(details, dict) else 0)
        return {
            "task_type": slot.task_type,
            "provider": "anthropic",
            "model": data.get("model"),
            "text": text,
            # output_tokens 含思考、是计费总量；reasoning 只作可观测拆分，绝不叠加进 total/cost。
            "usage": {"prompt_tokens": it, "completion_tokens": ot,
                      "cache_creation_input_tokens": cache_creation_tokens,
                      "cache_read_input_tokens": cache_read_tokens,
                      "completion_tokens_details": {"reasoning_tokens": thinking_tokens},
                      "total_tokens": it + ot},
            "thinking": thinking,
            "effort": (output_config or {}).get("effort"),
            "max_tokens": max_tokens,
            "request_id": request_id,
            "response_id": data.get("id"),
            "stop_reason": data.get("stop_reason"),
            "cache_control": cache_control,
            "transport": transport,
            "raw": data,
        }
